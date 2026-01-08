"""
安装钩子 - 在安装时生成 .pth 和 sitecustomize.py
"""
import os
import sys
import site
from pathlib import Path


def get_site_packages_dir():
    """获取site-packages目录"""
    # 获取所有site-packages目录
    site_packages = site.getsitepackages()
    if site_packages:
        return site_packages[0]
    
    # 如果getsitepackages()返回空，尝试从sys.path获取
    for path in sys.path:
        if 'site-packages' in path:
            return path
    
    # 最后尝试从distutils获取
    try:
        from distutils.sysconfig import get_python_lib
        return get_python_lib()
    except ImportError:
        # Python 3.12+ 移除了distutils
        # 使用site-packages的默认位置
        import site
        return site.getsitepackages()[0] if site.getsitepackages() else None


def install_hooks():
    """安装.pth和sitecustomize.py钩子"""
    site_packages = get_site_packages_dir()
    if not site_packages:
        raise RuntimeError("Cannot find site-packages directory")
    
    site_packages_path = Path(site_packages)
    
    # 1. 创建 .pth 文件
    pth_file = site_packages_path / "platform_coverage_agent.pth"
    pyca_package_path = Path(__file__).parent.parent
    
    # 写入.pth文件，内容是pyca包的路径
    with open(pth_file, 'w') as f:
        f.write(str(pyca_package_path) + '\n')
    
    print(f"Created .pth file: {pth_file}")
    
    # 2. 创建 sitecustomize.py 文件
    sitecustomize_file = site_packages_path / "sitecustomize.py"
    
    # 读取模板
    sitecustomize_template = Path(__file__).parent / "sitecustomize.py"
    if sitecustomize_template.exists():
        # 复制sitecustomize.py到site-packages
        import shutil
        shutil.copy2(sitecustomize_template, sitecustomize_file)
        print(f"Created sitecustomize.py: {sitecustomize_file}")
    else:
        # 如果模板不存在，创建一个简单的版本
        sitecustomize_content = '''"""
sitecustomize.py - Python sitecustomize钩子
这个文件会被Python解释器自动加载，用于启动PCA agent
"""
import os
import sys
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [PYCA] - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def _is_pip_install_context():
    """
    检测是否在pip install过程中
    返回True表示在安装过程中，应该跳过启动agent
    """
    # 检查sys.argv[0]是否包含setup.py或pip
    if len(sys.argv) > 0:
        script_name = sys.argv[0].lower()
        if 'setup.py' in script_name or 'pip' in script_name:
            return True
    
    # 检查环境变量（pip会设置这些变量）
    if os.getenv('PIP_INSTALL') or os.getenv('PIP_REQ_TRACKER'):
        return True
    
    # 检查是否在setup.py的执行上下文中
    # 通过检查调用栈来判断
    import traceback
    stack = traceback.extract_stack()
    for frame in stack:
        filename = frame.filename.lower()
        if 'setup.py' in filename or 'pip' in filename:
            return True
    
    return False

# 检查是否启用PYCA（支持PCA_*向后兼容）
PYCA_ENABLED = os.getenv('PYCA_ENABLED') or os.getenv('PCA_ENABLED', '1')
PYCA_ENABLED = PYCA_ENABLED.lower() in ('1', 'true', 'yes', 'on')

# 检查是否在pip install过程中
if _is_pip_install_context():
    logger.debug("[PYCA] Detected pip install context, skipping agent startup")
    PYCA_ENABLED = False

if PYCA_ENABLED:
    try:
        # 导入agent模块
        from pyca.agent import CoverageAgent
        
        # 创建并启动agent
        agent = CoverageAgent()
        agent.start()
        
        logger.info("[PYCA] Coverage agent started via sitecustomize")
    except Exception as e:
        logger.error(f"[PYCA] Failed to start coverage agent: {e}", exc_info=True)
else:
    logger.debug("[PYCA] Coverage agent disabled (PYCA_ENABLED=0 or PCA_ENABLED=0)")
'''
        with open(sitecustomize_file, 'w') as f:
            f.write(sitecustomize_content)
        print(f"Created sitecustomize.py: {sitecustomize_file}")
    
    print("Installation hooks installed successfully!")


if __name__ == '__main__':
    install_hooks()

