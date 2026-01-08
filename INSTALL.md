# PCA 安装和使用指南

## 安装

### 方式1: 从本地路径安装（开发测试）

如果 PCA 插件在本地开发，可以直接从路径安装：

```bash
# 从源码目录安装
cd /path/to/orbit/pca
pip install .

# 开发模式安装（修改代码后无需重新安装）
cd /path/to/orbit/pca
pip install -e .

# 从其他项目直接安装
pip install /path/to/orbit/pca
```

### 方式2: 从 Git 仓库安装

如果 PCA 代码在 Git 仓库中：

```bash
# 从 Git 仓库安装
pip install git+https://github.com/your-org/orbit.git#subdirectory=pca

# 或指定分支/标签
pip install git+https://github.com/your-org/orbit.git@main#subdirectory=pca
```

### 方式3: 从 Wheel 文件安装

```bash
# 先构建 wheel 包（在 pca 目录下）
cd /path/to/orbit/pca
pip install build wheel
python -m build --wheel

# 然后安装 wheel 文件
pip install dist/python_coverage_agent-0.1.0-py3-none-any.whl
```

### 方式4: 从 PyPI 安装（如果已发布）

```bash
pip install python-coverage-agent
```

### 方式5: 使用 requirements.txt

在被测项目的 `requirements.txt` 中添加：

```txt
# 从本地路径
-e /path/to/orbit/pca

# 或从 Git 仓库
git+https://github.com/your-org/orbit.git#subdirectory=pca

# 或从 PyPI（如果已发布）
python-coverage-agent>=0.1.0
```

然后安装：
```bash
pip install -r requirements.txt
```

> **详细部署方案请参考**: [DEPLOYMENT.md](DEPLOYMENT.md)

安装成功后，会在 `site-packages` 中自动生成：
- `platform_coverage_agent.pth` - Python路径钩子
- `sitecustomize.py` - Python启动钩子

## 配置

### 环境变量

在运行Python应用前，可以设置以下环境变量（均为可选）：

```bash
# 可选：RabbitMQ连接URL（默认: amqp://coverage:coverage123@localhost:5672/）
export PCA_RABBITMQ_URL="amqp://user:pass@localhost:5672/"

# 可选：采集间隔（秒，默认60）
export PCA_FLUSH_INTERVAL=30

# 可选：是否启用PCA（默认启用）
export PCA_ENABLED=1
```

**注意**: 如果不设置环境变量，PCA会使用默认值：
- `PCA_RABBITMQ_URL`: `amqp://coverage:coverage123@localhost:5672/`
- `PCA_FLUSH_INTERVAL`: `60` 秒

### 验证安装

```bash
# 检查PCA状态
pca status

# 测试agent
pca test
```

## 使用

### 基本使用

1. 设置环境变量（见上方）
2. 正常运行Python应用：

```bash
python your_app.py
```

PCA会自动：
- 在Python启动时自动加载
- 定时采集覆盖率数据
- 检测覆盖率变化
- 上报到RabbitMQ

### 测试示例

```bash
# 设置RabbitMQ URL
export PCA_RABBITMQ_URL="amqp://coverage:coverage123@localhost:5672/"

# 运行测试脚本
python test_example.py
```

## 工作原理

1. **启动钩子**: Python解释器启动时自动执行 `sitecustomize.py`
2. **初始化Agent**: 创建 `CoverageAgent` 实例
3. **启动定时器**: 每隔 `flush_interval` 秒执行一次采集
4. **采集流程**:
   - `cov.stop()` - 停止覆盖率收集
   - 生成 coverage data
   - 提取 executed_lines
   - 行 → 区间压缩
   - 计算 fingerprint（区间级hash）
   - 对比上一次 fingerprint
   - 如果变化 → 上报到MQ
   - 更新 fingerprint
   - `cov.start()` - 继续覆盖率收集

## 故障排查

### PCA未启动

检查：
1. `PCA_ENABLED` 环境变量是否为 `1`
2. `sitecustomize.py` 是否存在于 `site-packages`
3. 查看日志输出

### 未上报到MQ

检查：
1. `PCA_RABBITMQ_URL` 是否正确设置
2. RabbitMQ服务是否运行
3. 网络连接是否正常
4. 查看日志中的错误信息

### 覆盖率未变化

这是正常的！PCA使用增量上报机制，只有当覆盖率发生变化时才会上报。

## 卸载

```bash
pip uninstall python-coverage-agent
```

注意：卸载后需要手动删除：
- `site-packages/platform_coverage_agent.pth`
- `site-packages/sitecustomize.py`

