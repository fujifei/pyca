# PCA 快速开始指南

## 场景1: 本地开发测试

### 步骤1: 安装 PCA

```bash
# 在 PCA 项目目录下
cd /path/to/orbit/pca

# 开发模式安装（推荐，修改代码后无需重新安装）
pip install -e .
```

### 步骤2: 配置环境变量（可选）

```bash
# 使用默认值（amqp://coverage:coverage123@localhost:5672/）
# 或自定义
export PCA_RABBITMQ_URL="amqp://user:pass@localhost:5672/"
export PCA_FLUSH_INTERVAL=30
```

### 步骤3: 运行被测应用

```bash
python your_app.py
```

PCA 会自动启动并开始采集覆盖率。

## 场景2: 被测项目集成

### 方式A: 使用 requirements.txt

在被测项目的 `requirements.txt` 中添加：

```txt
# 从本地路径安装
-e /path/to/orbit/pca
```

然后安装：
```bash
pip install -r requirements.txt
```

### 方式B: 从 Git 仓库安装

在被测项目的 `requirements.txt` 中添加：

```txt
# 从 Git 仓库安装
git+https://github.com/your-org/orbit.git#subdirectory=pca
```

### 方式C: 直接安装

```bash
# 从本地路径
pip install /path/to/orbit/pca

# 从 Git 仓库
pip install git+https://github.com/your-org/orbit.git#subdirectory=pca
```

## 场景3: CI/CD 集成

### GitHub Actions 示例

```yaml
- name: Install PCA
  run: |
    pip install git+https://github.com/your-org/orbit.git#subdirectory=pca

- name: Set PCA environment
  run: |
    export PCA_RABBITMQ_URL="amqp://coverage:coverage123@localhost:5672/"
    export PCA_FLUSH_INTERVAL=60

- name: Run tests
  run: |
    python -m pytest tests/
```

### Jenkins 示例

```groovy
stage('Install PCA') {
    sh '''
        pip install git+https://github.com/your-org/orbit.git#subdirectory=pca
    '''
}

stage('Run Tests') {
    sh '''
        export PCA_RABBITMQ_URL="amqp://coverage:coverage123@localhost:5672/"
        python -m pytest tests/
    '''
}
```

## 验证安装

```bash
# 检查包是否安装
pip show python-coverage-agent

# 检查 CLI 工具
pca status

# 检查钩子文件
python -c "import site; import os; sp = site.getsitepackages()[0]; print(f'PTH: {os.path.join(sp, \"platform_coverage_agent.pth\")}'); print(f'Sitecustomize: {os.path.join(sp, \"sitecustomize.py\")}')"
```

## 常见问题

### Q: 安装后 PCA 没有启动？

**检查**:
1. 查看 Python 启动日志，是否有 PCA 相关日志
2. 检查 `PCA_ENABLED` 环境变量是否为 `1`
3. 检查 `sitecustomize.py` 是否存在于 site-packages

### Q: 如何禁用 PCA？

```bash
export PCA_ENABLED=0
python your_app.py
```

### Q: 如何查看 PCA 日志？

PCA 使用 Python 标准 logging，日志会输出到标准输出/错误。

## 下一步

- 查看 [INSTALL.md](INSTALL.md) 了解详细安装方式
- 查看 [DEPLOYMENT.md](DEPLOYMENT.md) 了解部署方案
- 查看 [README.md](README.md) 了解完整功能

