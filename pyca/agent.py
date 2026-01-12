"""
PYCA Agent - 覆盖率采集和上报核心模块
"""
import os
import sys
import time
import json
import hashlib
import logging
import threading
import ast
from typing import Dict, List, Set, Optional, Tuple
from pathlib import Path
import coverage
from coverage.exceptions import NoSource
import pika
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class CoverageAgent:
    """覆盖率采集代理"""
    
    def __init__(self, config: Optional[Dict] = None):
        """
        初始化覆盖率代理
        
        Args:
            config: 配置字典，包含以下可选键：
                - rabbitmq_url: RabbitMQ连接URL (默认从环境变量PYCA_RABBITMQ_URL获取，如果未设置则使用默认值)
                - flush_interval: 采集间隔（秒，默认60）
                - fingerprint_file: fingerprint存储文件路径（默认~/.pyca_fingerprint）
                - path_mapping: 路径映射字典，格式 {host_path: container_path}，用于容器环境路径转换
        """
        self.config = config or {}
        # PYCA_RABBITMQ_URL: 优先使用config，其次环境变量（支持PCA_*向后兼容），最后使用默认值（使用相同网段时使用rabbitmq:5672）
        self.rabbitmq_url = (
            self.config.get('rabbitmq_url') or 
            os.getenv('PYCA_RABBITMQ_URL') or 
            os.getenv('PCA_RABBITMQ_URL') or 
            'amqp://coverage:coverage123@rabbitmq:5672/'
        )
        # 记录实际使用的RabbitMQ URL（用于调试）
        logger.info(f"[PYCA] RabbitMQ URL configured: {self.rabbitmq_url}")
        # 解析并验证URL
        parsed = urlparse(self.rabbitmq_url)
        logger.info(f"[PYCA] Parsed RabbitMQ hostname: {parsed.hostname}, port: {parsed.port}")
        # PYCA_FLUSH_INTERVAL: 优先使用config，其次环境变量（支持PCA_*向后兼容），最后使用默认值60
        flush_interval_str = (
            self.config.get('flush_interval') or 
            os.getenv('PYCA_FLUSH_INTERVAL') or 
            os.getenv('PCA_FLUSH_INTERVAL') or 
            '60'
        )
        self.flush_interval = int(flush_interval_str)
        self.fingerprint_file = Path(
            self.config.get('fingerprint_file') or 
            os.path.expanduser('~/.pyca_fingerprint')
        )
        
        # 初始化coverage
        # 配置coverage，确保能正确收集数据
        # 不指定source，让coverage自动跟踪所有导入的模块
        self.cov = coverage.Coverage(
            auto_data=True,  # 自动保存数据
            data_suffix=None,  # 不使用文件后缀
            branch=False,  # 不收集分支覆盖率（只收集行覆盖率）
            # 不设置source，让coverage跟踪所有代码
            # 注意：coverage 会自动跟踪所有导入的 Python 模块
        )
        self.cov.start()
        logger.info("[PYCA] Coverage collection started (will track all imported Python modules)")
        
        # 上次的fingerprint
        self.last_fingerprint = self._load_fingerprint()
        
        # 定时器
        self.timer = None
        self.running = False
        
        # Git信息缓存
        self._git_info = None
        
        # Repo ID 缓存文件路径（格式：{repo_url: repo_id}）
        self.repo_id_cache_file = Path(
            self.config.get('repo_id_cache_file') or 
            os.path.expanduser('~/.pyca_repo_id_cache')
        )
        self._repo_id_cache = self._load_repo_id_cache()
        
        # 路径映射配置（用于容器环境，将宿主机路径转换为容器内路径）
        # 支持格式：环境变量 PYCA_PATH_MAPPING 或 PCA_PATH_MAPPING
        # 格式：host_path1:container_path1;host_path2:container_path2
        # 或通过 config 传入字典：{"host_path": "container_path"}
        path_mapping_str = (
            self.config.get('path_mapping') or
            os.getenv('PYCA_PATH_MAPPING') or
            os.getenv('PCA_PATH_MAPPING')
        )
        self.path_mapping = {}
        if path_mapping_str:
            if isinstance(path_mapping_str, dict):
                # 如果直接传入字典
                self.path_mapping = path_mapping_str
            else:
                # 从环境变量解析：host_path1:container_path1;host_path2:container_path2
                for mapping in path_mapping_str.split(';'):
                    mapping = mapping.strip()
                    if ':' in mapping:
                        host_path, container_path = mapping.split(':', 1)
                        self.path_mapping[host_path.strip()] = container_path.strip()
            if self.path_mapping:
                logger.info(f"[PYCA] Path mapping configured: {len(self.path_mapping)} mappings")
                for host_path, container_path in list(self.path_mapping.items())[:3]:
                    logger.debug(f"[PYCA]   {host_path} -> {container_path}")
        
        logger.info(f"[PYCA] Agent initialized, flush_interval={self.flush_interval}s")
    
    def _map_path(self, filename: str) -> str:
        """
        将宿主机路径转换为容器内路径（如果配置了路径映射）
        
        Args:
            filename: 原始文件路径
            
        Returns:
            转换后的文件路径，如果未配置映射或无法匹配则返回原路径
        """
        if not self.path_mapping:
            return filename
        
        # 按路径长度从长到短排序，优先匹配更具体的路径
        sorted_mappings = sorted(self.path_mapping.items(), key=lambda x: len(x[0]), reverse=True)
        
        for host_path, container_path in sorted_mappings:
            # 确保路径以 / 结尾或完全匹配
            if filename.startswith(host_path):
                # 替换路径前缀
                mapped_path = filename.replace(host_path, container_path, 1)
                # 检查转换后的路径是否存在
                if os.path.exists(mapped_path):
                    logger.debug(f"[PYCA] Mapped path: {filename} -> {mapped_path}")
                    return mapped_path
                else:
                    logger.debug(f"[PYCA] Mapped path not found: {filename} -> {mapped_path} (file does not exist)")
        
        return filename
    
    def _parse_python_statements(self, filepath: str) -> Set[int]:
        """
        解析 Python 文件，获取所有可执行语句的行号
        
        Args:
            filepath: Python 文件路径
            
        Returns:
            可执行语句的行号集合
        """
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                source = f.read()
            
            tree = ast.parse(source, filename=filepath)
            statements = set()
            
            for node in ast.walk(tree):
                if hasattr(node, 'lineno') and node.lineno:
                    # 记录所有有行号的节点（函数、类、语句等）
                    statements.add(node.lineno)
                    # 对于多行节点，也记录结束行
                    if hasattr(node, 'end_lineno') and node.end_lineno:
                        for line in range(node.lineno, node.end_lineno + 1):
                            statements.add(line)
            
            return statements
        except Exception as e:
            logger.warning(f"[PYCA] Failed to parse Python file {filepath}: {e}")
            return set()
    
    def _get_project_root(self) -> Optional[str]:
        """
        获取项目根目录（Git 仓库根目录或当前工作目录）
        
        Returns:
            项目根目录路径，如果找不到则返回 None
        """
        # 优先查找 Git 仓库根目录
        cwd = os.getcwd()
        git_dir = self._find_git_dir(cwd)
        if git_dir:
            repo_root = os.path.dirname(git_dir)
            return os.path.abspath(repo_root)
        
        # 如果没有 Git 仓库，使用当前工作目录
        return os.path.abspath(cwd)
    
    def _to_relative_path(self, filepath: str, project_root: Optional[str] = None) -> str:
        """
        将绝对路径转换为相对于项目根目录的相对路径
        
        Args:
            filepath: 文件路径（绝对或相对）
            project_root: 项目根目录，如果为 None 则自动检测
            
        Returns:
            相对路径，如果无法转换则返回原路径
        """
        if not os.path.isabs(filepath):
            # 已经是相对路径，直接返回
            return filepath
        
        if project_root is None:
            project_root = self._get_project_root()
        
        if not project_root:
            # 无法确定项目根目录，返回原路径
            return filepath
        
        try:
            # 转换为绝对路径
            abs_filepath = os.path.abspath(filepath)
            abs_project_root = os.path.abspath(project_root)
            
            # 检查文件是否在项目根目录下
            if abs_filepath.startswith(abs_project_root):
                # 计算相对路径
                relative_path = os.path.relpath(abs_filepath, abs_project_root)
                # 统一使用正斜杠（跨平台兼容）
                relative_path = relative_path.replace(os.sep, '/')
                return relative_path
            else:
                # 文件不在项目根目录下，返回原路径
                logger.debug(f"[PYCA] File {filepath} is not under project root {project_root}, keeping absolute path")
                return filepath
        except Exception as e:
            logger.warning(f"[PYCA] Failed to convert path {filepath} to relative: {e}")
            return filepath
    
    def start(self):
        """启动定时采集"""
        if self.running:
            logger.warning("[PYCA] Agent already running")
            return
        
        self.running = True
        # 启动时立即上报一次覆盖率（不检查变化）
        self._report_on_startup()
        # 启动定时器
        self._start_timer()
        logger.info("[PYCA] Agent started")
    
    def stop(self):
        """停止采集"""
        self.running = False
        if self.timer:
            self.timer.cancel()
        if self.cov:
            self.cov.stop()
        logger.info("[PYCA] Agent stopped")
    
    def _start_timer(self):
        """启动定时器"""
        if not self.running:
            return
        self.timer = threading.Timer(self.flush_interval, self._timer_callback)
        self.timer.daemon = True
        self.timer.start()
    
    def _timer_callback(self):
        """定时器回调"""
        try:
            self._flush_coverage()
        except Exception as e:
            logger.error(f"[PYCA] Error in timer callback: {e}", exc_info=True)
        finally:
            if self.running:
                self._start_timer()
    
    def _report_on_startup(self):
        """启动时上报覆盖率（不检查变化）
        
        注意：此方法捕获所有异常，确保上报失败不会影响被测服务的启动
        """
        logger.info("[PYCA] Reporting coverage on startup...")
        
        # 延迟一小段时间，确保coverage能收集到初始数据
        # 但即使没有数据，也要上报（至少上报所有可执行的行，count=0）
        import time
        time.sleep(2)  # 等待0.5秒，让coverage有机会收集数据
        
        # a. cov.stop()
        self.cov.stop()
        
        try:
            try:
                # b. 生成 coverage data
                coverage_data = self._get_coverage_data()
                
                # 检查是否有数据
                total_lines = sum(len(lines) for lines in coverage_data.values())
                logger.info(f"[PYCA] Coverage data collected: {len(coverage_data)} files, {total_lines} total lines")
                
                # c. 提取 executed_lines（用于更新fingerprint）
                executed_lines = self._extract_executed_lines(coverage_data)
                
                # d. 行 → 区间压缩
                ranges = self._compress_to_ranges(executed_lines)
                
                # e. 计算 fingerprint
                fingerprint = self._calculate_fingerprint(ranges)
                
                # f. 直接上报（不检查变化，即使数据为空也要上报）
                logger.info("[PYCA] Reporting coverage on startup (no change check)")
                self._report_coverage(coverage_data)  # 内部已捕获异常，不会抛出
                
                # g. 更新 fingerprint
                self.last_fingerprint = fingerprint
                self._save_fingerprint(fingerprint)
            except Exception as e:
                # 额外保护：即使 _report_coverage 内部有未捕获的异常，也不会影响服务启动
                logger.error(f"[PYCA] Error in startup coverage report: {e}", exc_info=True)
                logger.warning("[PYCA] Startup coverage report failed, but continuing service startup (non-blocking)")
        finally:
            # h. cov.start() - 确保覆盖率收集继续运行
            try:
                self.cov.start()
            except Exception as e:
                logger.error(f"[PYCA] Failed to restart coverage collection: {e}", exc_info=True)
        
        logger.info("[PYCA] Startup coverage report completed")
    
    def _flush_coverage(self):
        """采集并检查覆盖率"""
        logger.info("[PYCA] Starting coverage flush...")
        
        # a. cov.stop() - 停止覆盖率收集
        self.cov.stop()
        logger.debug("[PYCA] Coverage collection stopped")
        
        try:
            # 确保数据已保存到磁盘
            self.cov.save()
            logger.debug("[PYCA] Coverage data saved to disk")
            
            # 重新加载数据以确保获取最新数据
            # 注意：在stop()之后，get_data()应该返回最新的数据
            # 但为了确保数据完整性，我们重新获取一次
            data = self.cov.get_data()
            logger.debug(f"[PYCA] Coverage data retrieved, measured files: {len(data.measured_files())}")
            
            # b. 生成 coverage data
            coverage_data = self._get_coverage_data()
            
            # 检查是否有数据
            total_lines = sum(len(lines) for lines in coverage_data.values())
            logger.info(f"[PYCA] Coverage data collected: {len(coverage_data)} files, {total_lines} total lines")
            
            # c. 提取 executed_lines
            executed_lines = self._extract_executed_lines(coverage_data)
            
            # 记录已执行的文件和行数
            total_executed_lines = sum(len(lines) for lines in executed_lines.values())
            logger.info(f"[PYCA] Executed lines: {len(executed_lines)} files, {total_executed_lines} total executed lines")
            
            # 如果执行行数为0但有覆盖率数据，记录警告
            if total_executed_lines == 0 and total_lines > 0:
                logger.warning(f"[PYCA] WARNING: Found {total_lines} total lines but 0 executed lines - code may not have been executed or coverage data is incomplete")
                # 即使没有执行行，也尝试上报（至少上报可执行的行，count=0）
                # 这样可以确保系统知道哪些代码是可执行的，即使还没有被执行
            
            # d. 行 → 区间压缩
            ranges = self._compress_to_ranges(executed_lines)
            
            # e. 计算 fingerprint
            fingerprint = self._calculate_fingerprint(ranges)
            
            # f. 对比上一次
            # 如果执行行数为0，但这是首次运行（last_fingerprint为None），应该上报
            # 或者如果执行行数为0但覆盖率数据不为空，也应该上报（至少上报可执行的行）
            should_report = False
            if fingerprint != self.last_fingerprint:
                # 指纹变化，需要上报
                should_report = True
                logger.info(f"[PYCA] Coverage changed, reporting... (old fingerprint: {self.last_fingerprint[:16] if self.last_fingerprint else 'None'}..., new: {fingerprint[:16]}...)")
            elif self.last_fingerprint is None:
                # 首次运行，即使指纹为空也要上报
                should_report = True
                logger.info("[PYCA] First run detected, reporting coverage to initialize")
            elif total_executed_lines == 0 and total_lines > 0:
                # 执行行数为0但有覆盖率数据，可能是代码还未执行
                # 为了确保系统知道哪些代码是可执行的，我们也应该上报一次
                # 但为了避免重复上报，我们检查是否已经上报过空指纹
                empty_fingerprint = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # 空字符串的SHA256
                if fingerprint == empty_fingerprint and self.last_fingerprint == empty_fingerprint:
                    # 这是空数据的hash，如果上次也是这个，说明已经上报过了
                    logger.info(f"[PYCA] Coverage unchanged (no executed lines), skipping report (fingerprint: {fingerprint[:16]}...)")
                else:
                    # 首次遇到这种情况，上报一次
                    should_report = True
                    logger.info("[PYCA] No executed lines but coverage data exists, reporting to register executable lines")
            else:
                logger.info(f"[PYCA] Coverage unchanged, skipping report (fingerprint matches: {fingerprint[:16] if fingerprint else 'None'}...)")
            
            if should_report:
                # g. 上报（内部已捕获异常，不会抛出）
                try:
                    self._report_coverage(coverage_data)
                    # h. 更新 fingerprint（只有上报成功才更新）
                    self.last_fingerprint = fingerprint
                    self._save_fingerprint(fingerprint)
                except Exception as e:
                    # 额外保护：即使 _report_coverage 内部有未捕获的异常，也不会影响服务运行
                    logger.error(f"[PYCA] Error reporting coverage in flush: {e}", exc_info=True)
                    logger.warning("[PYCA] Coverage report failed, but continuing service execution (non-blocking)")
        finally:
            # i. cov.start() - 继续覆盖率收集
            self.cov.start()
            logger.debug("[PYCA] Coverage collection restarted")
        
        logger.info("[PYCA] Coverage flush completed")
    
    def _get_coverage_data(self) -> Dict:
        """
        获取覆盖率数据
        返回格式: {filename: {line_number: count, ...}}
        count: 执行次数，0表示未执行但可执行
        """
        # 获取覆盖率数据（在stop()之后调用，应该返回最新数据）
        data = self.cov.get_data()
        
        coverage_data = {}
        measured_files = data.measured_files()
        logger.info(f"[PYCA] Found {len(measured_files)} measured files")
        
        # 检查是否有已执行的文件
        files_with_executed_lines = []
        for filename in measured_files:
            lines = data.lines(filename)
            if lines:
                files_with_executed_lines.append(filename)
        logger.info(f"[PYCA] Files with executed lines: {len(files_with_executed_lines)}/{len(measured_files)}")
        
        if not measured_files:
            logger.warning("[PYCA] No measured files found, coverage may not be collecting data")
            # 尝试获取所有已执行的文件
            all_files = list(data.measured_files())
            if all_files:
                logger.info(f"[PYCA] But found {len(all_files)} files in coverage data")
            else:
                logger.warning("[PYCA] Coverage data is empty - no code has been executed yet")
        
        for filename in measured_files:
            try:
                # 使用 Coverage 对象的 analysis 方法获取分析结果
                # 不同版本的 coverage 库返回的元组格式可能不同：
                # 格式1: (statements, excluded, missing, missing_branch, excluded_branch) - 标准格式
                # 格式2: (filename, statements, missing, missing_str) - 某些版本的格式
                # 注意：coverage.analysis() 需要使用 coverage 数据中记录的文件名
                analysis_result = self.cov.analysis(filename)
                
                # 检测返回格式：如果第一个元素是字符串（文件名），说明是格式2
                if len(analysis_result) >= 3 and isinstance(analysis_result[0], str):
                    # 格式2: (filename, statements, missing, ...)
                    logger.info(f"[PYCA] Detected analysis format 2: (filename, statements, missing, ...)")
                    statements = analysis_result[1]  # 所有可执行的行号列表
                    missing = analysis_result[2]     # 未执行的行号列表
                    excluded = set()  # 格式2 中没有 excluded 信息，使用空集合
                    logger.info(f"[PYCA]   statements type: {type(statements)}, missing type: {type(missing)}")
                elif len(analysis_result) >= 3:
                    # 格式1: (statements, excluded, missing, ...)
                    logger.info(f"[PYCA] Detected analysis format 1: (statements, excluded, missing, ...)")
                    statements = analysis_result[0]  # 所有可执行的行号集合
                    excluded = analysis_result[1]    # 被排除的行号集合
                    missing = analysis_result[2]     # 未执行的行号集合
                else:
                    # 如果返回值格式不符合预期，回退到简单方法
                    logger.error(f"[PYCA] ERROR: Unexpected analysis result format: {len(analysis_result)} values")
                    logger.error(f"[PYCA]   analysis_result: {analysis_result}")
                    raise ValueError(f"Unexpected analysis result format: {len(analysis_result)} values")
                
                # 验证 statements 的类型
                if not isinstance(statements, (set, list, tuple)):
                    logger.error(f"[PYCA] ERROR: statements is not a collection! Type: {type(statements)}, value: {statements}")
                    logger.error(f"[PYCA]   This is a critical bug - statements should be a set/list of line numbers")
                    logger.error(f"[PYCA]   analysis_result: {analysis_result}")
                    # 如果 statements 是字符串（文件名），说明 analysis 返回格式错误
                    if isinstance(statements, str):
                        logger.error(f"[PYCA]   statements is a string (filename?), this indicates analysis() returned wrong format")
                        # 尝试从 analysis_result 中提取正确的数据
                        if len(analysis_result) >= 2 and isinstance(analysis_result[1], (list, tuple, set)):
                            logger.warning(f"[PYCA]   Attempting to extract statements from analysis_result[1]")
                            statements = analysis_result[1]
                        else:
                            raise ValueError(f"analysis() returned invalid format: statements is a string '{statements[:50]}...'")
                    else:
                        continue
                
                # 确保 statements 是集合类型
                if isinstance(statements, (list, tuple)):
                    statements = set(statements)
                elif not isinstance(statements, set):
                    # 如果不是集合、列表或元组，尝试转换
                    try:
                        statements = set(statements)
                    except TypeError:
                        logger.error(f"[PYCA] ERROR: Cannot convert statements to set. Type: {type(statements)}, value: {statements}")
                        continue
                
                # 确保 excluded 是集合类型
                if isinstance(excluded, (list, tuple)):
                    excluded = set(excluded)
                elif not isinstance(excluded, set):
                    excluded = set()
                
                # 确保 missing 是集合类型（用于日志）
                if isinstance(missing, (list, tuple)):
                    missing_set = set(missing)
                elif isinstance(missing, set):
                    missing_set = missing
                else:
                    missing_set = set()
                
                # 验证 statements 中的元素都是整数
                if statements and not all(isinstance(s, int) for s in list(statements)[:10]):
                    sample_statements = list(statements)[:10]
                    logger.error(f"[PYCA] ERROR: statements contains non-integer values! Sample: {sample_statements}")
                    logger.error(f"[PYCA]   Types: {[type(s) for s in sample_statements]}")
                    logger.error(f"[PYCA]   This indicates analysis() returned wrong format")
                    # 过滤掉非整数元素
                    statements = {s for s in statements if isinstance(s, int)}
                    logger.warning(f"[PYCA]   Filtered statements to {len(statements)} integer line numbers")
                
                # 获取已执行的行
                executed_lines = set(data.lines(filename))
                
                # 添加调试日志
                logger.info(f"[PYCA] Analyzing {filename}: {len(statements)} statements, {len(executed_lines)} executed lines, {len(missing_set)} missing lines")
                if len(executed_lines) == 0 and len(statements) > 0:
                    logger.warning(f"[PYCA] WARNING: File {filename} has {len(statements)} statements but 0 executed lines - code may not have been executed")
                
                # 构建覆盖率数据：{line_number: count}
                # statements 包含所有可执行的行（包括已执行和未执行的）
                file_coverage = {}
                for line_num in statements:
                    # 排除被排除的行
                    if line_num in excluded:
                        continue
                    # 如果行已执行，count = 1
                    # 如果行未执行，count = 0
                    if line_num in executed_lines:
                        file_coverage[line_num] = 1
                    else:
                        file_coverage[line_num] = 0
                
                if file_coverage:
                    # 确保 file_coverage 的结构正确：{line_number: count}
                    # 添加验证和日志
                    sample_keys = list(file_coverage.keys())[:5]
                    logger.info(f"[PYCA] File {filename}: file_coverage sample keys: {sample_keys}, types: {[type(k) for k in sample_keys]}")
                    
                    # 验证：所有键应该是整数
                    invalid_keys = [k for k in file_coverage.keys() if not isinstance(k, int)]
                    if invalid_keys:
                        logger.error(f"[PYCA] ERROR: file_coverage has non-integer keys! First few: {invalid_keys[:10]}")
                        logger.error(f"[PYCA]   This indicates a bug in data construction. Expected line numbers (int), got: {[type(k) for k in invalid_keys[:5]]}")
                        # 修复：只保留整数键
                        file_coverage = {k: v for k, v in file_coverage.items() if isinstance(k, int)}
                        logger.warning(f"[PYCA]   Fixed: removed {len(invalid_keys)} invalid keys, kept {len(file_coverage)} valid keys")
                    
                    coverage_data[filename] = file_coverage
                    executed_count = sum(1 for count in file_coverage.values() if count > 0)
                    logger.info(f"[PYCA] File {filename}: {len(statements)} statements, {executed_count} executed, {len(file_coverage) - executed_count} not executed")
                else:
                    logger.info(f"[PYCA] File {filename}: no coverage data (all lines excluded or no statements)")
            except NoSource as e:
                # 源文件不存在（常见于容器环境，源文件不在容器内）
                logger.info(f"[PYCA] Source file not available for {filename}: {e}")
                
                # 尝试路径映射：如果配置了路径映射，尝试用映射后的路径读取源文件
                mapped_filename = self._map_path(filename)
                if mapped_filename != filename and os.path.exists(mapped_filename):
                    # 映射后的文件存在，尝试解析获取所有可执行语句
                    try:
                        logger.debug(f"[PYCA] Trying mapped path: {mapped_filename}")
                        # 使用 AST 解析 Python 文件获取所有可执行语句
                        statements = self._parse_python_statements(mapped_filename)
                        
                        if statements:
                            # 从原始 coverage 数据获取已执行的行
                            executed_lines = set(data.lines(filename))
                            
                            # 构建覆盖率数据
                            file_coverage = {}
                            for line_num in statements:
                                file_coverage[line_num] = 1 if line_num in executed_lines else 0
                            
                            coverage_data[filename] = file_coverage
                            executed_count = sum(1 for count in file_coverage.values() if count > 0)
                            logger.debug(f"[PYCA] Mapped path analysis: {filename} -> {mapped_filename}: {len(statements)} statements, {executed_count} executed")
                        else:
                            # 如果解析失败，回退到只记录已执行的行
                            lines = data.lines(filename)
                            if lines:
                                coverage_data[filename] = {line: 1 for line in lines}
                                logger.debug(f"[PYCA] Fallback: File {filename}: {len(lines)} executed lines (from data.lines)")
                    except Exception as map_error:
                        logger.debug(f"[PYCA] Mapped path analysis failed: {map_error}")
                        # 回退到只记录已执行的行
                        lines = data.lines(filename)
                        if lines:
                            coverage_data[filename] = {line: 1 for line in lines}
                            logger.debug(f"[PYCA] Fallback: File {filename}: {len(lines)} executed lines (from data.lines)")
                        else:
                            logger.debug(f"[PYCA] Fallback: File {filename}: no executed lines found")
                else:
                    # 没有路径映射或映射后的文件也不存在，回退到只记录已执行的行
                    lines = data.lines(filename)
                    if lines:
                        coverage_data[filename] = {line: 1 for line in lines}
                        logger.debug(f"[PYCA] Fallback: File {filename}: {len(lines)} executed lines (from data.lines)")
                    else:
                        logger.debug(f"[PYCA] Fallback: File {filename}: no executed lines found")
            except Exception as e:
                logger.error(f"[PYCA] Failed to analyze {filename}: {e}", exc_info=True)
                # 如果分析失败，回退到只记录已执行的行
                lines = data.lines(filename)
                if lines:
                    coverage_data[filename] = {line: 1 for line in lines}
                    logger.info(f"[PYCA] Fallback: File {filename}: {len(lines)} executed lines (from data.lines)")
                else:
                    logger.warning(f"[PYCA] Fallback: File {filename}: no executed lines found")
        
        # 将所有绝对路径转换为相对路径
        project_root = self._get_project_root()
        if project_root:
            logger.debug(f"[PYCA] Converting absolute paths to relative paths (project root: {project_root})")
            normalized_coverage_data = {}
            for filename, line_coverage in coverage_data.items():
                relative_filename = self._to_relative_path(filename, project_root)
                if relative_filename != filename:
                    logger.debug(f"[PYCA] Converted path: {filename} -> {relative_filename}")
                normalized_coverage_data[relative_filename] = line_coverage
            coverage_data = normalized_coverage_data
        else:
            logger.debug(f"[PYCA] Could not determine project root, keeping original paths")
        
        # 验证返回的数据结构
        logger.debug(f"[PYCA] _get_coverage_data returning {len(coverage_data)} files")
        for filename, line_coverage in list(coverage_data.items())[:3]:
            if not isinstance(line_coverage, dict):
                logger.error(f"[PYCA] ERROR: Invalid coverage_data structure! filename={filename}, line_coverage type={type(line_coverage)}")
            else:
                sample_keys = list(line_coverage.keys())[:5]
                if sample_keys and not all(isinstance(k, int) for k in sample_keys):
                    logger.error(f"[PYCA] ERROR: Invalid keys in coverage_data! filename={filename}, sample keys={sample_keys}, types={[type(k) for k in sample_keys]}")
        
        return coverage_data
    
    def _extract_executed_lines(self, coverage_data: Dict) -> Dict[str, Set[int]]:
        """
        提取已执行的行（用于fingerprint计算）
        
        Args:
            coverage_data: 覆盖率数据字典 {filename: {line_number: count, ...}}
        
        Returns:
            {filename: set(executed_lines)} - 只包含count > 0的行
        """
        executed_lines = {}
        for filename, line_coverage in coverage_data.items():
            # 只提取count > 0的行（已执行的行）
            executed = {line for line, count in line_coverage.items() if count > 0}
            if executed:
                executed_lines[filename] = executed
        return executed_lines
    
    def _compress_to_ranges(self, executed_lines: Dict[str, Set[int]]) -> Dict[str, List[Tuple[int, int]]]:
        """
        将行号压缩为区间
        
        Args:
            executed_lines: {filename: set(executed_lines)}
        
        Returns:
            {filename: [(start_line, end_line), ...]}
        """
        ranges = {}
        for filename, lines in executed_lines.items():
            if not lines:
                continue
            
            sorted_lines = sorted(lines)
            file_ranges = []
            start = sorted_lines[0]
            end = sorted_lines[0]
            
            for line in sorted_lines[1:]:
                if line == end + 1:
                    # 连续，扩展区间
                    end = line
                else:
                    # 不连续，保存当前区间，开始新区间
                    file_ranges.append((start, end))
                    start = line
                    end = line
            
            # 保存最后一个区间
            file_ranges.append((start, end))
            ranges[filename] = file_ranges
        
        return ranges
    
    def _calculate_fingerprint(self, ranges: Dict[str, List[Tuple[int, int]]]) -> str:
        """
        计算区间级hash fingerprint
        
        Args:
            ranges: {filename: [(start_line, end_line), ...]}
        
        Returns:
            fingerprint字符串
        """
        # 构建用于hash的字符串
        # 格式: filename:start-end,start-end;filename:start-end,...
        parts = []
        for filename in sorted(ranges.keys()):
            range_strs = [f"{start}-{end}" for start, end in sorted(ranges[filename])]
            parts.append(f"{filename}:{','.join(range_strs)}")
        
        content = ";".join(parts)
        
        # 计算hash
        hash_obj = hashlib.sha256(content.encode('utf-8'))
        return hash_obj.hexdigest()
    
    def _load_fingerprint(self) -> Optional[str]:
        """加载上次的fingerprint"""
        if self.fingerprint_file.exists():
            try:
                with open(self.fingerprint_file, 'r') as f:
                    return f.read().strip()
            except Exception as e:
                logger.warning(f"[PYCA] Failed to load fingerprint: {e}")
        return None
    
    def _save_fingerprint(self, fingerprint: str):
        """保存fingerprint"""
        try:
            self.fingerprint_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.fingerprint_file, 'w') as f:
                f.write(fingerprint)
        except Exception as e:
            logger.error(f"[PYCA] Failed to save fingerprint: {e}")
    
    def _report_coverage(self, coverage_data: Dict):
        """上报覆盖率到MQ
        
        注意：此方法捕获所有异常，确保上报失败不会影响被测服务的正常运行
        """
        if not self.rabbitmq_url:
            logger.warning("[PYCA] RabbitMQ URL not configured, skipping report")
            return
        
        try:
            # 获取Git信息
            git_info = self._get_git_info()
            
            # 生成覆盖率原始数据（类似goc格式）
            # 添加调试日志：检查coverage_data的内容
            logger.info(f"[PYCA] Coverage data before formatting: {len(coverage_data)} files")
            if coverage_data:
                for filename, line_coverage in list(coverage_data.items())[:3]:  # 只打印前3个文件作为示例
                    # 检查 line_coverage 的类型
                    if not isinstance(line_coverage, dict):
                        logger.error(f"[PYCA] ERROR: line_coverage is not a dict! Type: {type(line_coverage)}, value: {line_coverage}")
                        continue
                    
                    sample_items = list(line_coverage.items())[:5] if line_coverage else []
                    logger.info(f"[PYCA]   Sample file {filename}: {len(line_coverage)} lines, sample items (first 5): {sample_items}")
                    # 检查数据类型
                    if sample_items:
                        first_key, first_value = sample_items[0]
                        logger.info(f"[PYCA]     First item - key type: {type(first_key)}, value type: {type(first_value)}, key: {first_key}, value: {first_value}")
                        
                        # 如果键不是整数，说明数据结构有问题
                        if not isinstance(first_key, int):
                            logger.error(f"[PYCA]     ERROR: Key is not an integer! This indicates coverage_data structure is wrong.")
                            logger.error(f"[PYCA]     Expected: {{filename: {{line_number: count, ...}}}}")
                            logger.error(f"[PYCA]     But got: key type {type(first_key)}, which suggests the data structure was corrupted.")
                            
                            # 尝试修复：如果 filename 看起来像是被拆分了
                            if isinstance(filename, str) and len(filename) == 1:
                                logger.error(f"[PYCA]     WARNING: filename is a single character '{filename}', this suggests the dict was iterated incorrectly!")
            else:
                logger.error("[PYCA] ERROR: coverage_data is empty!")
            
            coverage_raw = self._format_coverage_raw(coverage_data)
            
            # 构建上报消息（参考goc协议）
            report = {
                "repo": git_info.get("repo", ""),
                "repo_id": git_info.get("repo_id", ""),
                "branch": git_info.get("branch", ""),
                "commit": git_info.get("commit", ""),
                "ci": git_info.get("ci", {}),
                "coverage": {
                    "format": "pyca",  # Python Coverage Agent
                    "raw": coverage_raw
                },
                "timestamp": int(time.time())
            }
            
            # 打印完整的覆盖率报告详情
            logger.info("[PYCA] ========== Coverage Report Details ==========")
            logger.info(f"[PYCA] Repo: {report.get('repo', 'N/A')}")
            logger.info(f"[PYCA] Repo ID: {report.get('repo_id', 'N/A')}")
            logger.info(f"[PYCA] Branch: {report.get('branch', 'N/A')}")
            logger.info(f"[PYCA] Commit: {report.get('commit', 'N/A')}")
            logger.info(f"[PYCA] CI: {json.dumps(report.get('ci', {}), indent=2)}")
            logger.info(f"[PYCA] Timestamp: {report.get('timestamp', 'N/A')}")
            logger.info(f"[PYCA] Coverage Data Files: {len(coverage_data)}")
            
            # 打印每个文件的覆盖率详情
            for filename, line_coverage in coverage_data.items():
                executed_count = sum(1 for count in line_coverage.values() if count > 0)
                total_count = len(line_coverage)
                logger.info(f"[PYCA]   File: {filename}")
                logger.info(f"[PYCA]     Total lines: {total_count}, Executed lines: {executed_count}, Coverage: {executed_count/total_count*100:.2f}%" if total_count > 0 else f"[PYCA]     Total lines: {total_count}, Executed lines: {executed_count}")
            
            # 打印覆盖率原始数据（前1000个字符，避免日志过长）
            coverage_raw_preview = coverage_raw[:1000] if len(coverage_raw) > 1000 else coverage_raw
            logger.info(f"[PYCA] Coverage Raw Data (preview, {len(coverage_raw)} chars total):")
            logger.info(f"[PYCA] {coverage_raw_preview}")
            if len(coverage_raw) > 1000:
                logger.info(f"[PYCA] ... (truncated, {len(coverage_raw) - 1000} more chars)")
            
            # 打印完整的JSON报告（格式化）
            report_json = json.dumps(report, indent=2, ensure_ascii=False)
            logger.info(f"[PYCA] Full Report JSON:")
            logger.info(f"[PYCA] {report_json}")
            logger.info("[PYCA] ============================================")
            
            # 上报到MQ（内部会捕获异常，不会抛出）
            self._publish_to_mq(report)
            
        except Exception as e:
            # 捕获所有异常，确保上报失败不会影响被测服务
            logger.error(f"[PYCA] Failed to report coverage: {e}", exc_info=True)
            # 不重新抛出异常，确保被测服务继续正常运行
    
    def _format_coverage_raw(self, coverage_data: Dict) -> str:
        """
        格式化覆盖率数据为原始字符串（类似goc格式）
        
        Python覆盖率格式: file.py:start_line.end_col,end_line.end_col statements count
        其中statements是区间内的语句数（这里用end-start+1近似），count是执行次数
        
        Args:
            coverage_data: {filename: {line_number: count, ...}}
        """
        lines = ["mode: count"]
        
        total_files_processed = 0
        total_lines_processed = 0
        total_lines_skipped = 0
        
        logger.info(f"[PYCA] Formatting coverage raw data for {len(coverage_data)} files")
        
        for filename in sorted(coverage_data.keys()):
            line_coverage = coverage_data[filename]
            
            # 验证 line_coverage 的类型和结构
            if not isinstance(line_coverage, dict):
                logger.error(f"[PYCA] ERROR: line_coverage for {filename} is not a dict! Type: {type(line_coverage)}, value: {line_coverage}")
                continue
            
            if not line_coverage:
                logger.debug(f"[PYCA] Skipping empty file: {filename}")
                continue
            
            # 检查数据结构是否正确
            sample_items = list(line_coverage.items())[:5]
            if sample_items:
                first_key, first_value = sample_items[0]
                # 如果键不是整数，说明数据结构有问题
                if not isinstance(first_key, int):
                    logger.error(f"[PYCA] ERROR: Invalid data structure for {filename}!")
                    logger.error(f"[PYCA]   Expected: {{line_number (int): count (int), ...}}")
                    logger.error(f"[PYCA]   But got: key type {type(first_key)}, key value: {first_key}")
                    logger.error(f"[PYCA]   Sample items: {sample_items[:10]}")
                    
                    # 如果键是单个字符，说明文件名被错误地拆分了
                    if isinstance(first_key, str) and len(first_key) == 1:
                        logger.error(f"[PYCA]   WARNING: Keys are single characters, suggesting filename was incorrectly iterated!")
                        logger.error(f"[PYCA]   This is a critical bug - coverage_data structure is corrupted!")
                        # 跳过这个文件，因为数据无法修复
                        continue
            
            # 将所有行按count分组：已执行（count>0）和未执行（count=0）
            # 确保行号是整数类型，过滤掉非数字的键
            executed_lines = []
            not_executed_lines = []
            file_lines_skipped = 0
            
            # 添加调试：打印前几个item的示例
            logger.info(f"[PYCA] Processing {filename}: {len(line_coverage)} lines, sample items: {sample_items}")
            
            for line, count in line_coverage.items():
                try:
                    # 处理行号：可能是整数或字符串
                    if isinstance(line, int):
                        line_num = line
                    elif isinstance(line, str):
                        line_num = int(line)
                    else:
                        logger.warning(f"[PYCA] Unexpected line type: {type(line)} for line {line} in file {filename}")
                        file_lines_skipped += 1
                        total_lines_skipped += 1
                        continue
                    
                    # 处理count：确保是数字类型
                    if isinstance(count, (int, float)):
                        count_value = int(count)
                    elif isinstance(count, str):
                        try:
                            count_value = int(count)
                        except ValueError:
                            logger.warning(f"[PYCA] Invalid count value: {count} (type: {type(count)}) for line {line_num} in file {filename}")
                            file_lines_skipped += 1
                            total_lines_skipped += 1
                            continue
                    else:
                        logger.warning(f"[PYCA] Unexpected count type: {type(count)} for line {line_num} in file {filename}")
                        file_lines_skipped += 1
                        total_lines_skipped += 1
                        continue
                    
                    total_lines_processed += 1
                    if count_value > 0:
                        executed_lines.append(line_num)
                    else:
                        not_executed_lines.append(line_num)
                        
                except (ValueError, TypeError) as e:
                    # 跳过非数字的键（可能是数据格式问题）
                    logger.warning(f"[PYCA] Skipping invalid line number: {line} (type: {type(line)}, error: {e}) in file {filename}")
                    file_lines_skipped += 1
                    total_lines_skipped += 1
                    continue
            
            if file_lines_skipped > 0:
                logger.warning(f"[PYCA] Skipped {file_lines_skipped} lines in file {filename} due to format issues")
            
            executed_lines = sorted(executed_lines)
            not_executed_lines = sorted(not_executed_lines)
            
            logger.info(f"[PYCA] Formatting {filename}: {len(executed_lines)} executed, {len(not_executed_lines)} not executed, {file_lines_skipped} skipped in this file")
            
            # 如果处理后的行数为0，说明所有行都被跳过了，记录警告并尝试强制处理
            if len(executed_lines) == 0 and len(not_executed_lines) == 0 and len(line_coverage) > 0:
                logger.error(f"[PYCA] ERROR: All {len(line_coverage)} lines in {filename} were skipped! This should not happen.")
                logger.error(f"[PYCA]   Sample items from line_coverage: {list(line_coverage.items())[:5]}")
                # 尝试直接处理，不进行类型转换
                for line, count in list(line_coverage.items())[:5]:
                    logger.error(f"[PYCA]     Raw item - line: {line} (type: {type(line)}), count: {count} (type: {type(count)})")
                
                # 强制处理：直接使用原始数据，假设所有行都未执行
                logger.warning(f"[PYCA] Attempting fallback: treating all lines as not executed")
                fallback_lines = []
                for line, count in line_coverage.items():
                    try:
                        if isinstance(line, int):
                            line_num = line
                        else:
                            line_num = int(str(line))
                        fallback_lines.append(line_num)
                    except:
                        continue
                
                if fallback_lines:
                    not_executed_lines = sorted(fallback_lines)
                    logger.info(f"[PYCA] Fallback: Using {len(not_executed_lines)} lines as not executed")
            
            # 处理已执行的行（压缩为区间）
            if executed_lines:
                executed_ranges = self._compress_lines_to_ranges(executed_lines)
                logger.info(f"[PYCA]   Executed ranges for {filename}: {executed_ranges}")
                for start, end in executed_ranges:
                    statements = end - start + 1
                    count = 1  # 已覆盖
                    # 格式: file.py:start_line.end_col,end_line.end_col statements count
                    coverage_line = f"{filename}:{start}.0,{end}.0 {statements} {count}"
                    lines.append(coverage_line)
                    logger.debug(f"[PYCA]   Added executed line: {coverage_line}")
            
            # 处理未执行的行（压缩为区间）
            # 注意：即使所有行都未执行，也要生成覆盖率数据（count=0）
            if not_executed_lines:
                not_executed_ranges = self._compress_lines_to_ranges(not_executed_lines)
                logger.info(f"[PYCA]   Not executed ranges for {filename}: {len(not_executed_ranges)} ranges, first few: {not_executed_ranges[:3]}")
                for start, end in not_executed_ranges:
                    statements = end - start + 1
                    count = 0  # 未覆盖
                    # 格式: file.py:start_line.end_col,end_line.end_col statements count
                    coverage_line = f"{filename}:{start}.0,{end}.0 {statements} {count}"
                    lines.append(coverage_line)
                    logger.debug(f"[PYCA]   Added not executed line: {coverage_line}")
                total_files_processed += 1
                logger.info(f"[PYCA]   Successfully processed {filename}: added {len(not_executed_ranges)} coverage lines for not executed lines")
            elif executed_lines:
                # 如果有执行的行，也要计数
                total_files_processed += 1
                logger.info(f"[PYCA]   Successfully processed {filename}: added {len(executed_ranges)} coverage lines for executed lines")
            else:
                # 如果既没有执行的行，也没有未执行的行，说明所有行都被跳过了
                # 这不应该发生，但为了安全，我们记录警告
                logger.error(f"[PYCA]   ERROR: No coverage lines generated for {filename}!")
                logger.error(f"[PYCA]     Executed lines: {len(executed_lines)}, Not executed lines: {len(not_executed_lines)}")
                logger.error(f"[PYCA]     Original line_coverage size: {len(line_coverage)}")
                logger.error(f"[PYCA]     Lines skipped: {file_lines_skipped}")
                # 如果原始数据不为空，但处理后为空，说明数据格式有问题
                if len(line_coverage) > 0:
                    logger.error(f"[PYCA]     This indicates a data format issue - all lines were skipped during processing")
        
        logger.info(f"[PYCA] Formatted coverage raw: {total_files_processed} files processed, {total_lines_processed} lines processed, {total_lines_skipped} lines skipped, {len(lines)-1} coverage lines generated")
        
        result = "\n".join(lines)
        logger.info(f"[PYCA] Coverage raw result length: {len(result)} chars, {len(lines)} lines (including header)")
        
        if len(result) <= len("mode: count") or len(lines) <= 1:
            logger.error(f"[PYCA] ERROR: Coverage raw data is empty or only contains header!")
            logger.error(f"[PYCA]   Files in coverage_data: {len(coverage_data)}")
            logger.error(f"[PYCA]   Total lines processed: {total_lines_processed}")
            logger.error(f"[PYCA]   Total lines skipped: {total_lines_skipped}")
            logger.error(f"[PYCA]   Coverage lines generated: {len(lines)-1}")
            logger.error(f"[PYCA]   Result: {result[:200]}")  # 打印前200个字符
        
        return result
    
    def _compress_lines_to_ranges(self, lines: List[int]) -> List[Tuple[int, int]]:
        """
        将行号列表压缩为区间列表
        
        Args:
            lines: 排序后的行号列表
        
        Returns:
            [(start_line, end_line), ...]
        """
        if not lines:
            return []
        
        # 确保所有元素都是整数
        lines = [int(line) for line in lines]
        lines = sorted(lines)  # 确保排序
        
        ranges = []
        start = int(lines[0])
        end = int(lines[0])
        
        for line in lines[1:]:
            line = int(line)
            if line == end + 1:
                # 连续，扩展区间
                end = line
            else:
                # 不连续，保存当前区间，开始新区间
                ranges.append((start, end))
                start = line
                end = line
        
        # 保存最后一个区间
        ranges.append((start, end))
        return ranges
    
    def _get_git_info(self) -> Dict:
        """获取Git信息"""
        if self._git_info is not None:
            return self._git_info
        
        git_info = {
            "repo": "",
            "repo_id": "",
            "branch": "",
            "commit": "",
            "ci": {}
        }
        
        try:
            import subprocess
            
            # 获取当前工作目录
            cwd = os.getcwd()
            
            # 查找.git目录
            git_dir = self._find_git_dir(cwd)
            if not git_dir:
                logger.warning("[PYCA] .git directory not found")
                return git_info
            
            repo_root = os.path.dirname(git_dir)
            
            # 获取remote origin URL
            try:
                result = subprocess.run(
                    ['git', 'config', '--get', 'remote.origin.url'],
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    git_info["repo"] = result.stdout.strip()
                    logger.info(f"[PYCA] Retrieved git repo URL: {git_info['repo']}")
                else:
                    logger.warning(f"[PYCA] Failed to get git remote URL: returncode={result.returncode}, stderr={result.stderr.strip()}")
            except Exception as e:
                logger.warning(f"[PYCA] Failed to get git remote: {e}")
            
            # 获取branch
            try:
                result = subprocess.run(
                    ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    git_info["branch"] = result.stdout.strip()
            except Exception as e:
                logger.debug(f"[PYCA] Failed to get git branch: {e}")
            
            # 获取commit
            try:
                result = subprocess.run(
                    ['git', 'rev-parse', 'HEAD'],
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    git_info["commit"] = result.stdout.strip()
            except Exception as e:
                logger.debug(f"[PYCA] Failed to get git commit: {e}")
            
            # 获取CI信息
            git_info["ci"] = self._get_ci_info()
            
            # 尝试获取repo_id（从GitHub API）
            if git_info["repo"]:
                logger.info(f"[PYCA] Attempting to get repo_id for repo: {git_info['repo']}")
                repo_id = self._get_github_repo_id(git_info["repo"])
                if repo_id:
                    git_info["repo_id"] = repo_id
                    logger.info(f"[PYCA] Successfully set repo_id: {repo_id}")
                else:
                    logger.warning(f"[PYCA] Failed to get repo_id for repo: {git_info['repo']}")
            else:
                logger.warning("[PYCA] Repo URL is empty, cannot get repo_id")
            
        except Exception as e:
            logger.warning(f"[PYCA] Failed to get git info: {e}")
        
        self._git_info = git_info
        return git_info
    
    def _find_git_dir(self, start_dir: str) -> Optional[str]:
        """查找.git目录"""
        dir_path = Path(start_dir)
        while dir_path != dir_path.parent:
            git_path = dir_path / ".git"
            if git_path.exists():
                return str(git_path)
            dir_path = dir_path.parent
        return None
    
    def _get_ci_info(self) -> Dict:
        """获取CI信息"""
        ci_info = {}
        
        # GitHub Actions
        if os.getenv("GITHUB_RUN_ID"):
            ci_info["provider"] = "github"
            ci_info["pipeline_id"] = os.getenv("GITHUB_RUN_ID", "")
            ci_info["job_id"] = os.getenv("GITHUB_JOB", "")
        # GitLab CI
        elif os.getenv("CI_PIPELINE_ID"):
            ci_info["provider"] = "gitlab"
            ci_info["pipeline_id"] = os.getenv("CI_PIPELINE_ID", "")
            ci_info["job_id"] = os.getenv("CI_JOB_ID", "")
        # Jenkins
        elif os.getenv("BUILD_NUMBER"):
            ci_info["provider"] = "jenkins"
            ci_info["pipeline_id"] = os.getenv("BUILD_NUMBER", "")
            ci_info["job_id"] = os.getenv("JOB_NAME", "")
        # CircleCI
        elif os.getenv("CIRCLE_BUILD_NUM"):
            ci_info["provider"] = "circleci"
            ci_info["pipeline_id"] = os.getenv("CIRCLE_BUILD_NUM", "")
            ci_info["job_id"] = os.getenv("CIRCLE_JOB", "")
        
        return ci_info
    
    def _load_repo_id_cache(self) -> Dict[str, str]:
        """加载 repo_id 缓存"""
        cache = {}
        if self.repo_id_cache_file.exists():
            try:
                with open(self.repo_id_cache_file, 'r') as f:
                    cache = json.load(f)
                logger.debug(f"[PYCA] Loaded {len(cache)} repo_id entries from cache")
            except Exception as e:
                logger.warning(f"[PYCA] Failed to load repo_id cache: {e}")
        return cache
    
    def _save_repo_id_cache(self, repo_url: str, repo_id: str):
        """保存 repo_id 到缓存"""
        try:
            self._repo_id_cache[repo_url] = repo_id
            self.repo_id_cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.repo_id_cache_file, 'w') as f:
                json.dump(self._repo_id_cache, f, indent=2)
            logger.debug(f"[PYCA] Cached repo_id for {repo_url}: {repo_id}")
        except Exception as e:
            logger.warning(f"[PYCA] Failed to save repo_id cache: {e}")
    
    def _get_github_repo_id(self, repo_url: str) -> Optional[str]:
        """从GitHub API获取repo_id（带缓存）"""
        if not repo_url:
            logger.warning("[PYCA] Repo URL is empty, cannot get repo_id")
            return None
        
        # 先检查缓存
        if repo_url in self._repo_id_cache:
            cached_repo_id = self._repo_id_cache[repo_url]
            logger.info(f"[PYCA] Using cached repo_id for {repo_url}: {cached_repo_id}")
            return cached_repo_id
        
        # 缓存未命中，调用 API
        try:
            import re
            import urllib.request
            
            logger.info(f"[PYCA] Attempting to get GitHub repo ID for: {repo_url}")
            
            # 解析repo URL
            # 支持格式: https://github.com/owner/repo, git@github.com:owner/repo
            patterns = [
                re.compile(r'(?i)^https?://github\.com/([^/]+)/([^/]+)/?$'),
                re.compile(r'(?i)^git@github\.com:([^/]+)/([^/]+)/?$'),
                re.compile(r'(?i)^git://github\.com/([^/]+)/([^/]+)/?$')
            ]
            
            original_repo_url = repo_url
            repo_url_clean = repo_url.rstrip('.git').rstrip('/')
            owner, repo = None, None
            
            logger.debug(f"[PYCA] Parsing repo URL: original='{original_repo_url}', cleaned='{repo_url_clean}'")
            
            for i, pattern in enumerate(patterns):
                match = pattern.match(repo_url_clean)
                if match:
                    owner, repo = match.groups()
                    logger.info(f"[PYCA] Matched pattern {i+1}: owner={owner}, repo={repo}")
                    break
            
            if not owner or not repo:
                logger.warning(f"[PYCA] Failed to parse repo URL: {original_repo_url}")
                logger.warning(f"[PYCA]   After cleaning: {repo_url_clean}")
                logger.warning(f"[PYCA]   Tried patterns: https://github.com/owner/repo, git@github.com:owner/repo, git://github.com/owner/repo")
                # 尝试手动解析常见格式
                if 'github.com' in repo_url_clean:
                    parts = repo_url_clean.split('github.com')[-1].strip('/').split('/')
                    if len(parts) >= 2:
                        owner, repo = parts[0], parts[1]
                        logger.info(f"[PYCA] Manually parsed: owner={owner}, repo={repo}")
                    else:
                        logger.warning(f"[PYCA] Could not extract owner/repo from URL parts: {parts}")
                        return None
                else:
                    return None
            
            # 调用GitHub API
            api_url = f"https://api.github.com/repos/{owner}/{repo}"
            logger.info(f"[PYCA] Calling GitHub API: {api_url}")
            req = urllib.request.Request(api_url)
            req.add_header('User-Agent', 'pyca-agent')
            
            # 支持 GitHub token 认证（从环境变量获取，支持PYCA_*和PCA_*向后兼容）
            github_token = os.getenv('GITHUB_TOKEN') or os.getenv('PYCA_GITHUB_TOKEN') or os.getenv('PCA_GITHUB_TOKEN')
            if github_token:
                req.add_header('Authorization', f'token {github_token}')
                logger.debug("[PYCA] Using GitHub token for authentication (higher rate limit)")
            else:
                logger.debug("[PYCA] No GitHub token found, using unauthenticated request (lower rate limit)")
            
            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    if response.status == 200:
                        data = json.loads(response.read().decode('utf-8'))
                        repo_id = str(data.get('id', ''))
                        if repo_id:
                            logger.info(f"[PYCA] Successfully retrieved repo_id: {repo_id} for {owner}/{repo}")
                            # 保存到缓存
                            self._save_repo_id_cache(repo_url, repo_id)
                            return repo_id
                        else:
                            logger.warning(f"[PYCA] GitHub API response does not contain 'id' field. Response keys: {list(data.keys())[:10]}")
                    else:
                        logger.warning(f"[PYCA] GitHub API returned status {response.status} for {api_url}")
                        # 尝试读取响应体以获取更多信息
                        try:
                            response_body = response.read().decode('utf-8')
                            logger.debug(f"[PYCA] Response body: {response_body[:200]}")
                        except:
                            pass
            except urllib.error.HTTPError as e:
                error_body = ""
                try:
                    if hasattr(e, 'read'):
                        error_body = e.read().decode('utf-8')
                except:
                    pass
                
                logger.warning(f"[PYCA] HTTP error getting GitHub repo ID: {e.code} - {e.reason}")
                
                if e.code == 404:
                    logger.warning(f"[PYCA] Repository not found on GitHub: {owner}/{repo}")
                    logger.warning(f"[PYCA]   Please check if the repo URL is correct: {original_repo_url}")
                elif e.code == 403:
                    if 'rate limit' in error_body.lower():
                        logger.warning(f"[PYCA] GitHub API rate limit exceeded for unauthenticated requests")
                        logger.warning(f"[PYCA]   To increase rate limit, set GITHUB_TOKEN or PYCA_GITHUB_TOKEN environment variable")
                        logger.warning(f"[PYCA]   Example: export GITHUB_TOKEN=your_github_token")
                        logger.warning(f"[PYCA]   See: https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token")
                    else:
                        logger.warning(f"[PYCA] GitHub API access denied (403). Response: {error_body[:200]}")
                elif e.code == 401:
                    logger.warning(f"[PYCA] GitHub API authentication failed (401)")
                    logger.warning(f"[PYCA]   Please check if GITHUB_TOKEN or PYCA_GITHUB_TOKEN is valid")
                else:
                    logger.warning(f"[PYCA] Unexpected HTTP error: {e.code}")
                    if error_body:
                        logger.debug(f"[PYCA] Error response: {error_body[:200]}")
            except urllib.error.URLError as e:
                logger.warning(f"[PYCA] URL error getting GitHub repo ID: {e.reason}")
                logger.warning(f"[PYCA]   This might be a network issue or GitHub API is unreachable")
        
        except Exception as e:
            logger.error(f"[PYCA] Failed to get GitHub repo ID: {e}", exc_info=True)
        
        return None
    
    def _publish_to_mq(self, report: Dict):
        """发布消息到RabbitMQ
        
        注意：此方法捕获所有异常并记录日志，不会抛出异常，确保上报失败不会影响被测服务
        """
        connection = None
        try:
            # 解析RabbitMQ URL
            parsed = urlparse(self.rabbitmq_url)
            
            # 提取认证信息
            username = parsed.username or 'guest'
            password = parsed.password or 'guest'
            host = parsed.hostname or 'localhost'
            port = parsed.port or 5672
            vhost = parsed.path.lstrip('/') or '/'
            
            # 添加调试日志
            logger.info(f"[PYCA] Connecting to RabbitMQ: host={host}, port={port}, vhost={vhost}, username={username}")
            logger.debug(f"[PYCA] Full RabbitMQ URL: {self.rabbitmq_url}")
            logger.debug(f"[PYCA] Parsed URL components: hostname={parsed.hostname}, port={parsed.port}, path={parsed.path}")
            
            # 连接RabbitMQ
            # 验证hostname不为空，避免回退到localhost
            if not host or host == 'localhost':
                error_msg = f"[PYCA] ERROR: Invalid RabbitMQ hostname '{host}' from URL '{self.rabbitmq_url}'. Please check your configuration."
                logger.error(error_msg)
                # 不抛出异常，只记录日志
                return
            
            # 验证hostname是否可以解析（避免DNS解析失败导致pika回退到localhost）
            try:
                import socket
                resolved = socket.gethostbyname(host)
                logger.info(f"[PYCA] DNS resolution for '{host}': {resolved}")
            except socket.gaierror as e:
                error_msg = f"[PYCA] ERROR: Cannot resolve hostname '{host}' from URL '{self.rabbitmq_url}'. DNS error: {e}"
                logger.error(error_msg)
                # 不抛出异常，只记录日志
                return
            
            credentials = pika.PlainCredentials(username, password)
            parameters = pika.ConnectionParameters(
                host=host,
                port=port,
                virtual_host=vhost,
                credentials=credentials,
                socket_timeout=10,  # 设置socket超时
                connection_attempts=1,  # 只尝试一次，避免自动重试
                retry_delay=0,  # 不延迟重试
            )
            
            logger.info(f"[PYCA] Pika connection parameters: host={parameters.host}, port={parameters.port}, vhost={parameters.virtual_host}")
            logger.info(f"[PYCA] Attempting to connect to RabbitMQ at {host}:{port}...")
            connection = pika.BlockingConnection(parameters)
            channel = connection.channel()
            
            # 声明exchange（如果不存在）
            channel.exchange_declare(
                exchange='coverage_exchange',
                exchange_type='topic',
                durable=True
            )
            
            # 发布消息
            message_body = json.dumps(report)
            channel.basic_publish(
                exchange='coverage_exchange',
                routing_key='coverage.report',
                body=message_body,
                properties=pika.BasicProperties(
                    content_type='application/json',
                    delivery_mode=2  # 持久化
                )
            )
            
            connection.close()
            connection = None  # 标记已关闭，避免在finally中重复关闭
            
            logger.info(f"[PYCA] Coverage report published successfully: repo={report.get('repo')}, "
                       f"branch={report.get('branch')}, commit={report.get('commit')}")
        
        except Exception as e:
            # 捕获所有异常，记录日志但不抛出，确保不影响被测服务
            logger.error(f"[PYCA] Failed to publish to MQ: {e}", exc_info=True)
            logger.warning("[PYCA] Coverage report failed, but continuing service execution (non-blocking)")
        finally:
            # 确保连接被正确关闭
            if connection is not None and not connection.is_closed:
                try:
                    connection.close()
                except Exception as e:
                    logger.debug(f"[PYCA] Error closing connection: {e}")

