# 配置系统

AutomateX 的统一配置管理模块，采用单例模式提供全局配置访问。

## 目录结构

| 文件 | 说明 |
|------|------|
| `__init__.py` | 导出 `config` 单例对象 |
| `loader.py` | `ConfigManager` 配置加载器 |
| `sys_config.json` | 系统配置（MCP 地址、安全策略、性能参数） |
| `user_config.json` | 用户配置（API Key、模型、工作目录、UI 偏好） |

## 配置层级

```
┌─────────────────┐     ┌─────────────────┐
│ sys_config.json │     │ user_config.json│
│ (系统级/只读)    │     │ (用户级/可写)    │
└────────┬────────┘     └────────┬────────┘
         │                       │
         └───────┬───────────────┘
                 ↓
        ┌────────────────┐
        │ ConfigManager  │
        │  (单例模式)     │
        └────────┬───────┘
                 │
    ┌────────────┼────────────┐
    ↓            ↓            ↓
config.sys   config.user   config.mcp
```

## 数据类

| 类 | 属性 |
|----|------|
| `SysConfig` | MCP 服务器地址、安全策略、后端地址 |
| `UserConfig` | API Key、Base URL、模型、工作目录、UI 设置、任务参数 |
| `MCPConfig` | host、port |
| `APIConfig` | api_key、base_url、model |

## 使用方式

```python
from src.config import config

# 读取配置
api_key = config.user.api_key
model = config.user.model
mcp_host = config.sys.mcp_host

# 获取工作目录（优先级：环境变量 > 用户配置 > 用户主目录）
cwd = config.get_working_directory()

# 修改并保存用户配置
config.user.api_key = "new-key"
config.save_user_config()

# 重新加载配置
config.reload()
```

## 配置文件示例

### `sys_config.json`

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

### `user_config.json`

```json
{
  "workspace": { "default_working_directory": "" },
  "ui": { "auto_scroll": true },
  "task": { "max_iterations": 50 },
  "api": {
    "api_key": "your-api-key",
    "base_url": "https://api.moonshot.cn/",
    "model": "kimi-k2-0905-preview"
  }
}
```

## 注意事项

- `user_config.json` 包含 API Key，已添加到 `.gitignore`
- `sys_config.json` 为系统级配置，通常不需要用户修改
- 桌面应用可通过设置界面修改用户配置
