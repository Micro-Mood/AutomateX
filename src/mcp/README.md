# MCP Server

Model Context Protocol 服务器，为 AutomateX 提供安全隔离的工具执行环境。

## 目录结构

| 文件/目录 | 说明 |
|-----------|------|
| `server.py` | JSON-RPC 2.0 服务器，注册 32+ RPC 方法，支持 TCP / stdio 模式 |
| `cli.py` | 命令行工具（Click + Rich），提供 `start` / `stop` / `interactive` 等子命令 |
| `sdk.py` | Python 客户端 SDK，`MCPClient` 异步上下文管理器 |
| `__main__.py` | 模块入口（`python -m src.mcp`） |
| `core/` | 核心模块：安全、缓存、配置、异常 |
| `modules/` | 四大工具模块：read / search / edit / execute |

## 核心模块 (`core/`)

| 文件 | 说明 |
|------|------|
| `security.py` | 路径安全验证（工作区范围 + 符号链接检测）、命令安全过滤（15+ 危险模式正则） |
| `cache.py` | 多层缓存策略（基于 cachetools），文件元数据 60s / 目录列表 30s / 搜索结果 5min |
| `config.py` | Pydantic 强类型配置模型（`ServerConfig` / `SecurityConfig` 等） |
| `exceptions.py` | 统一异常体系（`MCPError` 基类 + 丰富子类，携带 error_code / details） |

## 工具模块 (`modules/`)

| 模块 | 功能 | RPC 方法 |
|------|------|----------|
| `read/` | 文件读取、目录列表 | `read_file`, `list_directory`, `get_file_info`, `file_exists` |
| `search/` | 文件名搜索、内容搜索 | `search_files`, `search_content`, `search_symbols` |
| `edit/` | 文件 CRUD & 内容编辑 | `write_file`, `create_directory`, `delete`, `move`, `copy`, `replace_range`, `insert_text`, `delete_range`, `patch_file` 等 |
| `execute/` | 进程管理 & 命令执行 | `run_command`, `create_task`, `start_task`, `wait_task`, `kill_task`, `get_stdout`, `get_stderr` 等 |

## 快速使用

### 启动服务器

```bash
# TCP 模式（默认端口 8080）
python -m src.mcp --port 8080

# stdio 模式
python -m src.mcp --stdio
```

### CLI 交互式测试

```bash
python -m src.mcp interactive --host 127.0.0.1 --port 8080
```

### Python SDK

```python
from src.mcp.sdk import MCPClient

async with MCPClient("127.0.0.1", 8080) as client:
    # 读取文件
    content = await client.read_file("/path/to/file.txt")

    # 搜索内容
    results = await client.search_content("keyword", "/search/dir")

    # 执行命令
    result = await client.run_command("dir /b", working_dir="C:\\")
```

## 安全机制

- **路径验证**：限制在工作区目录内，阻止系统路径访问，检测符号链接绕过
- **命令过滤**：正则检测递归删除、磁盘格式化、注册表操作、PowerShell 注入等
- **文件锁**：并发写入保护
- **文件名清理**：防止路径遍历攻击
- **校验和计算**：支持文件完整性验证
