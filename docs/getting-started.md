# 快速入门

本文档帮助你在 5 分钟内运行 AutomateX。

## 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10 / 11 |
| Python | >= 3.10 |
| Node.js | >= 18.0.0（桌面应用需要） |
| AI API | 需要一个 OpenAI 兼容 API 的 Key（Kimi / DeepSeek / Qwen 等） |

## 安装步骤

### 1. 克隆项目

```bash
git clone <repo-url>
cd rabit
```

### 2. 安装 Python 依赖

```bash
# 推荐使用虚拟环境
python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
```

### 3. 配置 AI API Key

**方式一（推荐）：** 启动桌面应用后在设置界面配置。

**方式二：** 直接编辑 `src/config/user_config.json`：

```json
{
  "api": {
    "api_key": "sk-your-api-key-here",
    "base_url": "https://api.moonshot.cn/",
    "model": "kimi-k2-0905-preview"
  }
}
```

#### 支持的 AI 服务

| 服务 | Base URL | 推荐模型 |
|------|----------|----------|
| Kimi | `https://api.moonshot.cn/` | `kimi-k2-0905-preview` |
| DeepSeek | `https://api.deepseek.com/` | `deepseek-reasoner` |
| Qwen | `https://dashscope.aliyuncs.com/` | `qwen2.5-0.5b` |
| 其他 OpenAI 兼容 | 自定义 | 自定义 |

> ⚠️ `user_config.json` 包含敏感信息，已添加到 `.gitignore`，不会被提交到版本控制。

### 4. 启动应用

#### 桌面应用（推荐）

```bash
cd src/web
npm install
npm start
```

应用会自动启动后端 API 服务（端口 8000）和 Electron 窗口。

#### 命令行模式

```bash
# 直接运行任务
python -m src.tasks.main "创建一个名为test的文件夹"

# 交互式模式
python -m src.tasks.main --interactive "帮我整理当前目录"

# 查看任务列表
python -m src.tasks.main --list
```

#### Python API

```python
from src.tasks import AutomateX

ax = AutomateX()
task = ax.run("列出当前目录的文件")
print(task.status)
```

## 首次运行检查

1. **API 连通性**：确保网络可以访问你配置的 AI 服务 Base URL
2. **端口占用**：后端 API 默认使用 `8000` 端口，MCP Server 使用 `8080` 端口
3. **权限**：部分文件操作需要管理员权限

## 配置文件说明

| 文件 | 作用 | 是否提交到 Git |
|------|------|---------------|
| `src/config/sys_config.json` | 系统配置（MCP 地址、日志级别、安全策略） | ✅ 是 |
| `src/config/user_config.json` | 用户配置（API Key、模型、工作目录） | ❌ 否 |

### 系统配置 `sys_config.json`

```json
{
  "mcp": {
    "server": { "host": "127.0.0.1", "port": 8080 }
  },
  "tasks": {
    "engine": { "backend": "v3" }
  },
  "logging": { "level": "INFO" }
}
```

### 用户配置 `user_config.json`

```json
{
  "workspace": {
    "default_working_directory": ""
  },
  "ui": {
    "auto_scroll": true
  },
  "task": {
    "max_iterations": 50
  },
  "api": {
    "api_key": "your-api-key",
    "base_url": "https://api.moonshot.cn/",
    "model": "kimi-k2-0905-preview"
  }
}
```

## 下一步

- 查看 [API 接口文档](api-reference.md) 了解如何与后端交互
- 查看 [系统架构](architecture.md) 了解内部工作原理
- 查看 [部署指南](deployment.md) 了解如何打包发布
