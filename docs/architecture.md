# 系统架构

本文档描述 AutomateX 的内部架构、核心模块和设计决策。

---

## 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                      Electron 桌面应用                           │
│  ┌────────────┐              ┌────────────┐                     │
│  │ index.html │◄── IPC ────►│  main.js   │                     │
│  │ (前端 UI)  │              │ (主进程)    │                     │
│  └─────┬──────┘              └────────────┘                     │
│        │ WebSocket + REST                                       │
└────────┼────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│                   FastAPI 后端 (server.py)                       │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │ REST API     │  │ WebSocket Manager│  │ Engine Manager   │  │
│  │ /api/tasks/* │  │ 订阅/广播/心跳    │  │ 线程安全引擎管理  │  │
│  └──────┬───────┘  └────────┬─────────┘  └────────┬─────────┘  │
│         └──────────────────┬┘                      │            │
│                            ▼                       │            │
│  ┌──────────────────────────────────────┐          │            │
│  │         TaskStore (store.json)       │◄─────────┘            │
│  │  任务持久化 / 消息历史 / 原子写入      │                       │
│  └──────────────────────────────────────┘                       │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    TaskEngine V3 (engine.py)                     │
│                                                                 │
│   ┌─────────┐      ┌─────────┐      ┌─────────┐               │
│   │ SELECT  │ ───► │ PARAMS  │ ───► │  EXEC   │ ──► 循环      │
│   │ 选工具  │      │ 填参数   │      │ 执行    │               │
│   └─────────┘      └─────────┘      └────┬────┘               │
│        ▲                                  │                     │
│        │          ┌─────────┐             │                     │
│        └──────────│ RESULT  │◄────────────┘                     │
│                   │ 写结果  │                                    │
│                   └─────────┘                                   │
│                                                                 │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│   │ Context  │  │  Tools   │  │  Chat    │  │  Prompt  │      │
│   │ FIFO窗口 │  │ 注册表   │  │ AI API   │  │ 模板     │      │
│   └──────────┘  └──────────┘  └──────────┘  └──────────┘      │
└────────────────────────────┬────────────────────────────────────┘
                             │
               ┌─────────────┴─────────────┐
               ▼                           ▼
┌──────────────────────┐     ┌──────────────────────┐
│     MCP Client       │     │    Local Fallback    │
│  (TCP JSON-RPC 2.0)  │     │   (直接本地执行)     │
└──────────┬───────────┘     └──────────────────────┘
           │
           ▼
┌──────────────────────────────────────────────────┐
│              MCP Server (server.py)              │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌──────────┐  │
│  │  read  │ │ search │ │  edit  │ │ execute  │  │
│  └────────┘ └────────┘ └────────┘ └──────────┘  │
│                                                  │
│  ┌────────────────────────────────────────────┐  │
│  │  Core: Security | Cache | Config | Error  │  │
│  └────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

---

## V3 两阶段工具调用引擎

### 设计动机

传统方式：每次 AI 调用都携带全部工具的详细描述 → Token 消耗巨大。

V3 方案：拆分为两个阶段，**按需注入**工具描述。

### 阶段详解

#### Phase 1: SELECT

- 输入：用户任务描述 + 工具**名称列表**（不含参数说明）
- AI 输出：`{"select": ["read_file", "write_file"]}`
- Token 消耗：极低（工具列表仅几十个 token）

#### Phase 2: PARAMS

- 输入：被选中工具的**精简描述**（紧凑格式） + 历史上下文
- AI 输出：`{"call": "read_file", "path": "/some/file.txt"}`
- 引擎执行工具，将结果写回上下文

#### 循环

AI 可在一次任务中进行多轮 SELECT → PARAMS → EXEC 循环，直到调用 `done`（完成）或 `fail`（失败）。

### 工具描述紧凑格式

```
read_file|读取文件内容|path:str:文件路径,encoding:str:编码:false:utf-8
```

格式：`名称|描述|参数名:类型:说明:是否必填:默认值`

相比 JSON Schema 格式，Token 消耗降低约 80%。

### 上下文管理（FIFO）

```python
Context(max_size=20)  # 最大 20 条消息
```

- 超出时淘汰最早的 user/assistant 消息
- System 消息（提示词）**永远保留**
- 分阶段管理：`SELECT` → `PARAMS` → `EXEC` → `RESULT`

### 任务状态机

```
            ┌──────────────────────────────────────┐
            │                                      ▼
WAITING ──► RUNNING ──► COMPLETED          CANCELLED
               │                              ▲
               ├──► NEED_INPUT ──► RUNNING    │
               │                              │
               ├──► PAUSED ──► RUNNING ───────┤
               │                              │
               └──► FAILED ──► WAITING (retry)│
                       │                      │
                       └──────────────────────┘
```

---

## MCP Server

### 协议

JSON-RPC 2.0 over TCP（默认端口 8080），也支持 stdio 模式。

### 请求格式

```json
{
  "jsonrpc": "2.0",
  "method": "read_file",
  "params": {"path": "/some/file.txt", "encoding": "utf-8"},
  "id": 1
}
```

### 响应格式

```json
{
  "jsonrpc": "2.0",
  "result": {"content": "文件内容..."},
  "id": 1
}
```

### 工具模块

| 模块 | 方法 | 说明 |
|------|------|------|
| **read** | `read_file` | 读取文件内容（支持编码检测） |
| | `list_directory` | 列出目录内容 |
| | `get_file_info` | 获取文件元数据 |
| | `file_exists` | 检查路径是否存在 |
| **search** | `search_files` | 按文件名模式搜索 |
| | `search_content` | 在文件内容中搜索（正则支持） |
| | `search_symbols` | 搜索代码符号 |
| **edit** | `write_file` | 写入文件 |
| | `create_directory` | 创建目录 |
| | `delete` | 删除文件/目录 |
| | `move` | 移动/重命名 |
| | `copy` | 复制文件 |
| | `replace_range` | 替换文件中的文本范围 |
| | `insert_text` | 在指定位置插入文本 |
| | `delete_range` | 删除文本范围 |
| | `patch_file` | 批量补丁操作 |
| **execute** | `run_command` | 执行命令（一次性） |
| | `create_task` | 创建后台进程 |
| | `start_task` | 启动后台进程 |
| | `wait_task` | 等待进程完成 |
| | `kill_task` | 终止进程 |
| | `get_stdout` / `get_stderr` | 获取进程输出 |

### Local Fallback

当 MCP Server 不可用时，引擎会自动降级到本地执行模式，支持以下基础操作：

- `local_run_command` — 执行命令
- `local_read_file` — 读取文件
- `local_write_file` — 写入文件
- `local_list_dir` — 列出目录
- `local_exists` — 检查路径

---

## 安全机制

### 路径安全（Security）

| 检查 | 说明 |
|------|------|
| 工作区范围 | 所有路径操作限制在工作区目录内 |
| 系统路径阻止 | 阻止访问 `C:\Windows`、`C:\Program Files` 等 |
| 符号链接检测 | 防止通过符号链接绕过路径限制 |
| 路径遍历防护 | 过滤 `..` 遍历攻击 |
| 文件名清理 | 移除特殊字符，防止注入 |

### 命令安全

15+ 危险模式正则检测：

- 递归删除（`rd /s`、`rm -rf`）
- 磁盘格式化（`format`）
- 注册表操作（`reg delete`）
- PowerShell 危险命令（`Invoke-Expression`、`Set-ExecutionPolicy`）
- 系统关键操作（`shutdown`、`taskkill /f`）
- 环境变量修改（`setx`）

### 网络安全

- API 仅监听 `127.0.0.1`（不对外暴露）
- CORS 白名单限制来源
- WebSocket 可选 Token 认证
- API Key 本地存储，不通过网络传输

---

## 缓存策略

MCP Server 使用基于 `cachetools` 的多层缓存：

| 缓存类型 | TTL | 容量 | 用途 |
|----------|-----|------|------|
| 文件元数据 | 60s | 500 | `get_file_info` 结果 |
| 目录列表 | 30s | 200 | `list_directory` 结果 |
| 搜索结果 | 5min | 100 | `search_*` 结果 |
| 任务状态 | 10s | 50 | 进程状态 |
| 通用 LRU | — | 1000 | 其他数据 |

缓存在文件写入/删除/移动等操作后自动失效。

---

## 统一配置系统

```
ConfigManager (单例)
  ├── sys_config.json  → SysConfig
  │     ├── mcp (host, port)
  │     ├── security (blocked_paths, blocked_commands)
  │     ├── logging (level)
  │     └── backend (host, port)
  │
  └── user_config.json → UserConfig
        ├── api (api_key, base_url, model)
        ├── workspace (default_working_directory)
        ├── ui (auto_scroll)
        └── task (max_iterations)
```

访问方式：
```python
from src.config import config

config.user.api_key      # 读取
config.user.model = "x"  # 修改
config.save_user_config() # 保存
config.reload()           # 重新加载
```

---

## WebSocket 连接管理

### 架构

```
WebSocketManager
  ├── 连接池 (_connections: Dict[str, WebSocketConnection])
  ├── 订阅表 (_task_subscriptions: Dict[str, Set[str]])
  ├── 心跳检测（定时清理死连接）
  └── 批量广播（asyncio.gather）
```

### 消息流

1. 前端通过 `subscribe` 订阅特定任务
2. 引擎通过回调函数（`on_thinking`/`on_tool_start`/`on_tool_end`）产生事件
3. 回调通过 `asyncio.run_coroutine_threadsafe` 投递到主事件循环
4. `WebSocketManager` 查找订阅该任务的连接，批量广播

### 线程模型

```
主线程 (asyncio event loop)
  ├── FastAPI 请求处理
  ├── WebSocket 收发
  └── 广播协程执行

任务线程 (threading.Thread)
  ├── TaskEngine.run()
  ├── AI API 调用
  └── MCP / Local 工具执行
       └── 通过 run_coroutine_threadsafe 投递 WS 广播
```

> Windows 下任务线程使用 `SelectorEventLoop`（而非默认的 ProactorEventLoop），避免管道写入竞争 bug。
