# PCA 部署和安装指南

本文档说明如何让被测项目安装和使用 `python-coverage-agent` 插件。

## 安装方式

### 方式1: 从本地路径安装（推荐用于开发测试）

如果 PCA 插件在本地开发，可以直接从路径安装：

```bash
# 方式1.1: 从源码目录安装
cd /path/to/orbit/pca
pip install .

# 方式1.2: 从源码目录安装（开发模式，修改代码后无需重新安装）
cd /path/to/orbit/pca
pip install -e .

# 方式1.3: 从其他项目直接安装
pip install /path/to/orbit/pca
```

### 方式2: 从 Git 仓库安装

如果 PCA 代码在 Git 仓库中：

```bash
# 从 Git 仓库安装
pip install git+https://github.com/your-org/orbit.git#subdirectory=pca

# 或指定分支/标签
pip install git+https://github.com/your-org/orbit.git@main#subdirectory=pca

# 或使用 SSH
pip install git+ssh://git@github.com/your-org/orbit.git#subdirectory=pca
```

### 方式3: 打包成 Wheel 文件分发

#### 3.1 构建 Wheel 包

```bash
cd /path/to/orbit/pca

# 安装构建工具
pip install build wheel

# 构建 wheel 包
python -m build --wheel

# 构建结果在 dist/ 目录
# dist/python_coverage_agent-0.1.0-py3-none-any.whl
```

#### 3.2 从 Wheel 文件安装

```bash
# 方式3.2.1: 从本地 wheel 文件安装
pip install dist/python_coverage_agent-0.1.0-py3-none-any.whl

# 方式3.2.2: 分发 wheel 文件给其他项目
# 将 wheel 文件复制到被测项目，然后安装
pip install python_coverage_agent-0.1.0-py3-none-any.whl

# 方式3.2.3: 从 HTTP 服务器安装
# 将 wheel 文件放到 HTTP 服务器上
pip install http://your-server.com/packages/python_coverage_agent-0.1.0-py3-none-any.whl
```

### 方式4: 发布到 PyPI（公开或私有）

#### 4.1 发布到公开 PyPI

```bash
cd /path/to/orbit/pca

# 安装发布工具
pip install build twine

# 构建分发包
python -m build

# 上传到 PyPI（需要先注册账号）
twine upload dist/*
```

安装：
```bash
pip install python-coverage-agent
```

#### 4.2 发布到私有 PyPI 服务器

```bash
# 配置私有 PyPI 服务器
# 在 ~/.pypirc 或 pip.conf 中配置

# 上传到私有 PyPI
twine upload --repository private-pypi dist/*

# 从私有 PyPI 安装
pip install --index-url https://your-private-pypi.com/simple python-coverage-agent
```

### 方式5: 使用 requirements.txt

在被测项目的 `requirements.txt` 中添加：

```txt
# 方式5.1: 从本地路径
-e /path/to/orbit/pca

# 方式5.2: 从 Git 仓库
git+https://github.com/your-org/orbit.git#subdirectory=pca

# 方式5.3: 从 Wheel 文件
python_coverage_agent @ file:///path/to/python_coverage_agent-0.1.0-py3-none-any.whl

# 方式5.4: 从 PyPI（如果已发布）
python-coverage-agent>=0.1.0
```

然后安装：
```bash
pip install -r requirements.txt
```

## 验证安装

安装后，验证是否安装成功：

```bash
# 检查包是否安装
pip show python-coverage-agent

# 检查 CLI 工具
pca status

# 检查钩子文件是否生成
python -c "import site; print(site.getsitepackages())"
# 检查 site-packages 目录中是否有：
# - platform_coverage_agent.pth
# - sitecustomize.py
```

## 常见问题

### Q1: 安装后没有生成 .pth 和 sitecustomize.py

**原因**: 安装钩子可能失败

**解决**:
1. 检查安装日志，查看是否有错误
2. 手动运行安装钩子：
   ```bash
   python -c "from pca.install_hooks import install_hooks; install_hooks()"
   ```

### Q2: 使用 `pip install -e .` 后修改代码不生效

**原因**: 某些文件可能被缓存

**解决**:
```bash
# 重新安装
pip install -e . --force-reinstall --no-cache-dir
```

### Q3: 多个 Python 环境

**原因**: 不同环境需要分别安装

**解决**:
```bash
# 为每个环境分别安装
python3.8 -m pip install /path/to/orbit/pca
python3.9 -m pip install /path/to/orbit/pca
python3.10 -m pip install /path/to/orbit/pca
```

### Q4: 虚拟环境中安装

**解决**:
```bash
# 激活虚拟环境后安装
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate  # Windows

pip install /path/to/orbit/pca
```

## 推荐方案

根据使用场景选择：

1. **开发测试**: 使用方式1（本地路径安装，开发模式 `-e`）
2. **CI/CD**: 使用方式2（Git 仓库）或方式3（Wheel 文件）
3. **生产环境**: 使用方式4（私有 PyPI）或方式3（Wheel 文件分发）
4. **多项目共享**: 使用方式4（PyPI）或方式3（Wheel 文件 + HTTP 服务器）

