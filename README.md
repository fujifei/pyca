# PYCA (Python Coverage Agent)

业务无侵入的Python覆盖率上报插件

## 概述

PYCA (Python Coverage Agent) 是一个零侵入的Python代码覆盖率采集和上报工具。它通过Python的钩子机制自动启动，无需修改业务代码，即可实现覆盖率数据的自动采集和上报。

## 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        Python 应用进程                            │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              Python 解释器启动                             │  │
│  │                                                           │  │
│  │  1. 加载 site-packages/sitecustomize.py                   │  │
│  │     └─> 自动导入 pyca.agent.CoverageAgent                │  │
│  │                                                           │  │
│  │  2. 初始化 CoverageAgent                                  │  │
│  │     ├─> 启动 coverage.Coverage()                         │  │
│  │     ├─> 配置定时器 (默认60秒)                              │  │
│  │     └─> 加载上次的 fingerprint                            │  │
│  │                                                           │  │
│  └──────────────────────────────────────────────────────────┘  │
│                           │                                      │
│                           │ 定时触发 (flush_interval)            │
│                           ▼                                      │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │           覆盖率采集流程 (CoverageAgent)                     │  │
│  │                                                           │  │
│  │  1. cov.stop() - 停止覆盖率收集                            │  │
│  │  2. 获取覆盖率数据 (coverage.get_data())                  │  │
│  │  3. 提取已执行的行 (executed_lines)                       │  │
│  │  4. 行号压缩为区间 (compress_to_ranges)                   │  │
│  │  5. 计算 fingerprint (SHA256 hash)                        │  │
│  │  6. 对比上次 fingerprint                                  │  │
│  │  7. 如果变化 → 格式化数据 → 上报到MQ                      │  │
│  │  8. 更新 fingerprint                                      │  │
│  │  9. cov.start() - 继续覆盖率收集                           │  │
│  └──────────────────────────────────────────────────────────┘  │
│                           │                                      │
│                           │ 覆盖率变化时                          │
│                           ▼                                      │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │              数据格式化与上报                                │  │
│  │                                                           │  │
│  │  ┌─────────────────────────────────────────────────┐   │  │
│  │  │  覆盖率数据格式 (类似goc格式)                      │   │  │
│  │  │  mode: count                                     │   │  │
│  │  │  file.py:10.0,15.0 6 1                           │   │  │
│  │  │  file.py:20.0,20.0 1 0                           │   │  │
│  │  └─────────────────────────────────────────────────┘   │  │
│  │                                                           │  │
│  │  ┌─────────────────────────────────────────────────┐   │  │
│  │  │  上报消息 (JSON格式)                               │   │  │
│  │  │  {                                                │   │  │
│  │  │    "repo": "git@github.com:owner/repo.git",      │   │  │
│  │  │    "repo_id": "12345678",                        │   │  │
│  │  │    "branch": "main",                             │   │  │
│  │  │    "commit": "abc123...",                        │   │  │
│  │  │    "coverage": {                                  │   │  │
│  │  │      "format": "pyca",                           │   │  │
│  │  │      "raw": "mode: count\n..."                   │   │  │
│  │  │    }                                              │   │  │
│  │  │  }                                                │   │  │
│  │  └─────────────────────────────────────────────────┘   │  │
│  └──────────────────────────────────────────────────────────┘  │
│                           │                                      │
│                           │ 发布到 RabbitMQ                       │
│                           ▼                                      │
└─────────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    RabbitMQ Message Queue                        │
│                                                                   │
│  Exchange: coverage_exchange                                     │
│  Routing Key: coverage.report                                   │
└─────────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│              Coverage Platform (Consumer)                        │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  1. 接收覆盖率报告                                         │  │
│  │  2. 解析覆盖率数据                                         │  │
│  │  3. 存储到数据库                                          │  │
│  │  4. 提供API查询接口                                       │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## 核心组件

### 1. sitecustomize.py
- **位置**: `site-packages/sitecustomize.py`
- **作用**: Python解释器启动时自动加载的钩子文件
- **功能**: 检测是否启用PYCA，如果启用则自动启动CoverageAgent

### 2. platform_coverage_agent.pth
- **位置**: `site-packages/platform_coverage_agent.pth`
- **作用**: Python路径钩子文件，确保pyca包在sys.path中
- **内容**: pyca包的安装路径

### 3. CoverageAgent
- **位置**: `pyca/agent.py`
- **功能**:
  - 初始化coverage.Coverage对象
  - 定时采集覆盖率数据
  - 计算fingerprint（增量检测）
  - 格式化覆盖率数据
  - 上报到RabbitMQ

### 4. Fingerprint算法
- **目的**: 实现增量上报，只在覆盖率变化时上报
- **算法**:
  1. 将已执行的行号压缩为区间（如：10-15行）
  2. 对每个文件的区间列表计算SHA256 hash
  3. 格式：`filename:start-end,start-end;filename:start-end,...`
  4. 对比上次的fingerprint，如果不同则上报

## 功能特性

### 1. 零侵入性
- 使用 `.pth` + `sitecustomize` 双钩子方案
- 无需修改业务代码
- 安装后自动生效

### 2. 增量上报
- 使用"已覆盖行指纹（Incremental Coverage）"算法
- 只在覆盖率变化时上报
- 减少网络传输和服务器负载

### 3. 定时采集
- 自动定时采集覆盖率数据（默认60秒）
- 可配置采集间隔
- 支持启动时立即上报

### 4. MQ上报
- 支持上报到RabbitMQ
- 协议兼容goc格式
- 支持持久化消息

### 5. Git信息自动获取
- 自动获取Git仓库信息（repo, branch, commit）
- 支持GitHub API获取repo_id
- 支持CI环境信息（GitHub Actions, GitLab CI, Jenkins等）

## 安装

### 快速安装

```bash
# 方式1: 从本地路径安装（开发测试推荐）
pip install /path/to/pyca

# 方式2: 从 Git 仓库安装
pip install git+https://github.com/fujifei/pyca.git

# 方式3: 从 PyPI 安装（如果已发布）
pip install python-coverage-agent
```

### 安装验证

安装成功后，会在 `site-packages` 中自动生成：
- `platform_coverage_agent.pth` - Python路径钩子
- `sitecustomize.py` - Python启动钩子

验证安装：
```bash
pip show python-coverage-agent
pyca status
```

> **详细安装方式请参考**: [INSTALL.md](INSTALL.md) 和 [DEPLOYMENT.md](DEPLOYMENT.md)

## 配置

### 环境变量

- `PYCA_ENABLED`: 是否启用PYCA（默认: 1，支持`PCA_ENABLED`向后兼容）
- `PYCA_RABBITMQ_URL`: RabbitMQ连接URL（默认: `amqp://coverage:coverage123@localhost:5672/`，支持`PCA_RABBITMQ_URL`向后兼容）
- `PYCA_FLUSH_INTERVAL`: 采集间隔（秒，默认: 60，支持`PCA_FLUSH_INTERVAL`向后兼容）
- `GITHUB_TOKEN` 或 `PYCA_GITHUB_TOKEN`: GitHub Personal Access Token（可选，用于获取 repo_id，提升 API rate limit）

### GitHub Token 配置（可选）

PYCA 会自动从 GitHub API 获取仓库的 `repo_id`。为了提升 API rate limit（从 60次/小时 提升到 5000次/小时），可以配置 GitHub Token：

#### 1. 创建 GitHub Personal Access Token

1. 访问 [GitHub Settings > Developer settings > Personal access tokens > Tokens (classic)](https://github.com/settings/tokens)
2. 点击 "Generate new token (classic)"
3. 设置 token 名称和过期时间
4. 选择权限（至少需要 `public_repo` 权限来读取公开仓库信息）
5. 点击 "Generate token"
6. **重要**: 复制生成的 token（只显示一次）

#### 2. 设置环境变量

PYCA 支持两种方式配置 GitHub Token：

**方式1: 使用 .env 文件（推荐）**

在项目根目录创建 `.env` 文件（PYCA 会自动查找并加载）：

```bash
# 在项目根目录创建 .env 文件
cat > .env << EOF
# GitHub Token 配置（用于获取 repo_id，提升 API rate limit）
GITHUB_TOKEN=your_github_personal_access_token
# 或者使用 PYCA 专用变量名：
# PYCA_GITHUB_TOKEN=your_github_personal_access_token
EOF
```

PYCA 会在启动时自动加载项目根目录下的 `.env` 文件。如果项目根目录没有 `.env` 文件，会尝试从当前工作目录加载。

**方式2: 使用环境变量**

```bash
# 方式1: 使用 GITHUB_TOKEN（标准环境变量）
export GITHUB_TOKEN=your_github_personal_access_token

# 方式2: 使用 PYCA_GITHUB_TOKEN（PYCA 专用）
export PYCA_GITHUB_TOKEN=your_github_personal_access_token
```

**优先级说明**：
- 已存在的环境变量优先级最高（不会被 .env 文件覆盖）
- .env 文件中的配置会补充未设置的环境变量
- 支持的环境变量名称（按优先级）：`GITHUB_TOKEN` > `PYCA_GITHUB_TOKEN` > `PCA_GITHUB_TOKEN`

#### 3. 验证

设置 token 后，PYCA 会在首次获取 repo_id 时使用认证请求，并缓存结果。后续上报将使用缓存的 repo_id，不会重复调用 API。

**注意**: 
- Token 是可选的，不设置也能正常工作（但可能遇到 rate limit）
- repo_id 会被缓存到 `~/.pyca_repo_id_cache`，避免重复调用 API
- 如果遇到 rate limit 错误，设置 token 可以解决问题

### 示例

```bash
# 使用默认配置（RabbitMQ: amqp://coverage:coverage123@localhost:5672/, 间隔: 60秒）
python your_app.py

# 方式1: 使用 .env 文件配置（推荐）
# 在项目根目录创建 .env 文件，包含：
# GITHUB_TOKEN=your_github_token
# PYCA_RABBITMQ_URL=amqp://user:pass@localhost:5672/
# PYCA_FLUSH_INTERVAL=30
python your_app.py

# 方式2: 使用环境变量配置
export PYCA_RABBITMQ_URL="amqp://user:pass@localhost:5672/"
export PYCA_FLUSH_INTERVAL=30
export GITHUB_TOKEN=your_github_token
python your_app.py
```

## 工作原理

### 启动流程

1. **Python解释器启动**: 自动加载 `sitecustomize.py`
2. **初始化Agent**: 创建 `CoverageAgent` 实例并启动
3. **启动时上报**: 立即上报一次覆盖率（不检查变化）

### 定时采集流程

每隔 `flush_interval` 秒执行一次采集流程：

1. `cov.stop()` - 停止覆盖率收集
2. 生成 coverage data
3. 提取 executed_lines
4. 行 → 区间压缩
5. 计算 fingerprint（区间级hash）
6. 对比上一次 fingerprint
7. 如果变化 → 上报到MQ
8. 更新 fingerprint
9. `cov.start()` - 继续覆盖率收集

### Fingerprint算法

- Python覆盖率是行级的，但最终转换成区间级
- 连续的覆盖行为一个区间（如：10-15行）
- 对每个文件的区间列表计算SHA256 hash
- 格式：`filename:start-end,start-end;filename:start-end,...`

### 上报协议

上报消息格式参考goc协议：

```json
{
  "repo": "git@github.com:owner/repo.git",
  "repo_id": "12345678",
  "branch": "main",
  "commit": "abc123...",
  "ci": {
    "provider": "github",
    "pipeline_id": "123",
    "job_id": "test"
  },
  "coverage": {
    "format": "pyca",
    "raw": "mode: count\nfile.py:10.0,15.0 6 1\n..."
  },
  "timestamp": 1234567890
}
```

覆盖率原始数据格式（类似goc）：
```
mode: count
file.py:10.0,15.0 6 1
file.py:20.0,20.0 1 1
```

格式说明：
- `file.py:start_line.col,end_line.col statements count`
- `statements`: 区间内的语句数（end-start+1）
- `count`: 执行次数（1表示已覆盖，0表示未覆盖）

## CLI工具

```bash
# 查看状态
pyca status

# 测试agent
pyca test
```

## 数据流

```
Python应用运行
    │
    ├─> Coverage收集代码执行情况
    │
    ├─> 定时器触发 (每60秒)
    │
    ├─> 停止收集 → 获取数据 → 计算fingerprint
    │
    ├─> 对比上次fingerprint
    │
    ├─> 如果变化 → 格式化数据 → 上报到RabbitMQ
    │
    └─> 继续收集
```

## 文件结构

```
pyca/
├── pyca/                    # 主包目录
│   ├── __init__.py          # 包初始化
│   ├── agent.py             # 覆盖率采集和上报核心模块
│   ├── cli.py               # CLI工具
│   ├── install_hooks.py     # 安装钩子工具
│   └── sitecustomize.py     # Python启动钩子模板
├── setup.py                 # 安装脚本
├── pyproject.toml           # 项目配置
├── README.md               # 本文档
├── INSTALL.md              # 安装指南
├── DEPLOYMENT.md           # 部署指南
└── QUICKSTART.md           # 快速开始
```

## 开发

```bash
# 安装开发版本
pip install -e .

# 运行测试
python -m pytest tests/
```

## 向后兼容性

PYCA 保持对旧版本 `PCA_*` 环境变量的向后兼容：
- `PCA_ENABLED` → `PYCA_ENABLED`
- `PCA_RABBITMQ_URL` → `PYCA_RABBITMQ_URL`
- `PCA_FLUSH_INTERVAL` → `PYCA_FLUSH_INTERVAL`
- `PCA_GITHUB_TOKEN` → `PYCA_GITHUB_TOKEN`

覆盖率格式 `pca` 也保持兼容，但新版本使用 `pyca` 格式。

## 常见问题

### Q: 安装后 PYCA 没有启动？

**检查**:
1. 查看 Python 启动日志，是否有 PYCA 相关日志
2. 检查 `PYCA_ENABLED` 环境变量是否为 `1`
3. 检查 `sitecustomize.py` 是否存在于 site-packages

### Q: 如何禁用 PYCA？

```bash
export PYCA_ENABLED=0
python your_app.py
```

### Q: 如何查看 PYCA 日志？

PYCA 使用 Python 标准 logging，日志会输出到标准输出/错误。

### Q: Fingerprint文件在哪里？

默认位置：`~/.pyca_fingerprint`

### Q: 如何清理缓存？

```bash
rm ~/.pyca_fingerprint
rm ~/.pyca_repo_id_cache
```

## 许可证

MIT License
