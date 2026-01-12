"""
Setup script for PYCA (Python Coverage Agent)
"""
from setuptools import setup, find_packages
from setuptools.command.install import install
from pathlib import Path
import sys
import site

# 读取README
readme_file = Path(__file__).parent / "README.md"
long_description = readme_file.read_text(encoding='utf-8') if readme_file.exists() else ""


class PostInstallHook(install):
    """安装后钩子，用于生成.pth和sitecustomize.py"""
    
    def run(self):
        # 先执行标准安装
        install.run(self)
        
        # 安装钩子
        self.install_hooks()
    
    def install_hooks(self):
        """安装.pth和sitecustomize.py钩子"""
        try:
            # 获取site-packages目录
            if self.user:
                # 用户安装模式
                site_packages = site.getusersitepackages()
            else:
                # 系统安装模式
                site_packages = self.install_lib
            
            site_packages_path = Path(site_packages)
            
            if not site_packages_path.exists():
                print(f"[PYCA] Warning: site-packages directory does not exist: {site_packages_path}")
                return
            
            # 1. 创建 .pth 文件
            pth_file = site_packages_path / "platform_coverage_agent.pth"
            
            # 获取pyca包的安装路径
            # 在安装后，pyca包应该在site_packages/pyca
            pyca_package_path = site_packages_path / "pyca"
            
            # 写入.pth文件，内容是pyca包的路径
            with open(pth_file, 'w') as f:
                f.write(str(pyca_package_path) + '\n')
            
            print(f"[PYCA] Created .pth file: {pth_file}")
            
            # 2. 创建 sitecustomize.py 文件
            sitecustomize_file = site_packages_path / "sitecustomize.py"
            
            # 读取模板
            sitecustomize_template = Path(__file__).parent / "pyca" / "sitecustomize.py"
            if sitecustomize_template.exists():
                # 复制sitecustomize.py到site-packages
                import shutil
                shutil.copy2(sitecustomize_template, sitecustomize_file)
                print(f"[PYCA] Created sitecustomize.py: {sitecustomize_file}")
            else:
                # 如果模板不存在，创建一个简单的版本
                sitecustomize_content = '''"""
sitecustomize.py - Python sitecustomize钩子
这个文件会被Python解释器自动加载，用于启动PYCA agent
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
                print(f"[PYCA] Created sitecustomize.py: {sitecustomize_file}")
            
            print("[PYCA] Installation hooks installed successfully!")
        
        except Exception as e:
            print(f"[PYCA] Warning: Failed to install hooks: {e}")
            import traceback
            traceback.print_exc()


setup(
    name="python-coverage-agent",
    version="0.1.0",
    description="PYCA (Python Coverage Agent) - 业务无侵入的Python覆盖率上报插件",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="PCA Team",
    author_email="",
    url="https://github.com/your-org/pca",
    packages=find_packages(),
    install_requires=[
        "coverage>=7.0.0",
        "pika>=1.3.0",
        "python-dotenv>=1.0.0",
    ],
    python_requires=">=3.7",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    entry_points={
        "console_scripts": [
            "pyca=pyca.cli:main",
        ],
    },
    cmdclass={
        'install': PostInstallHook,
    },
    include_package_data=True,
    zip_safe=False,
)

