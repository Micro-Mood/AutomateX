# API 接口文档

AutomateX 提供三种接口方式：**REST API**（HTTP）、**WebSocket**（实时推送）、**Python API**（直接集成）。

后端服务地址默认为 `http://127.0.0.1:8000`。

---

## 目录

- [通用说明](#通用说明)
- [REST API](#rest-api)
  - [健康检查](#健康检查)
  - [任务 CRUD](#任务-crud)
  - [任务执行控制](#任务执行控制)
  - [TODO 管理](#todo-管理)
  - [统计信息](#统计信息)
  - [系统配置](#系统配置)
- [WebSocket](#websocket)
- [Python API](#python-api)
- [错误处理](#错误处理)
- [注意事项](#注意事项)

---

## 通用说明

### Base URL

```
http://127.0.0.1:8000
```

### 认证

目前 REST API 无需认证（仅限本地访问）。WebSocket 支持可选的 Token 认证：

```
ws://127.0.0.1:8000/ws?token=YOUR_TOKEN
```

Token 通过环境变量 `AUTOMATEX_WS_TOKEN` 设置。未设置时不校验。

### CORS 策略

仅允许以下来源：
- `http://localhost:*`
- `http://127.0.0.1:*`
- `file://`（Electron 应用）

### 请求/响应格式

- 请求体：`Content-Type: application/json`
- 响应体：JSON 格式
- 时间格式：ISO 8601（`2026-02-09T12:00:00.000000`）

### 任务状态枚举

| 状态 | 值 | 说明 |
|------|-----|------|
| 等待执行 | `waiting` | 任务已创建，等待启动 |
| 运行中 | `running` | 任务正在执行 |
| 等待输入 | `need_input` | AI 向用户提问，等待回复 |
| 已完成 | `completed` | 任务执行成功 |
| 已失败 | `failed` | 任务执行出错 |
| 已取消 | `cancelled` | 用户主动取消 |
| 已暂停 | `paused` | 任务已暂停（可恢复） |

---

## REST API

### 健康检查

#### `GET /`

返回 API 基本信息。

**响应：**
```json
{
  "name": "AutomateX API",
  "version": "3.0.0",
  "status": "running",
  "engine": "TaskEngine V3"
}
```

#### `GET /api/health`

健康检查。

**响应：**
```json
{
  "status": "healthy",
  "timestamp": "2026-02-09T12:00:00.000000"
}
```

---

### 任务 CRUD

#### `GET /api/tasks` — 获取任务列表

**查询参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `status` | string | — | 按状态筛选（`waiting`/`running`/`completed`/`failed`/`cancelled`/`paused`/`need_input`） |
| `search` | string | — | 按描述关键词搜索（不区分大小写） |
| `sort_by` | string | `created_at` | 排序字段（`created_at`/`updated_at`/`status`） |
| `sort_order` | string | `desc` | 排序方向（`asc`/`desc`） |
| `limit` | int | `50` | 每页数量（1-200） |
| `offset` | int | `0` | 偏移量 |

**响应：**
```json
{
  "tasks": [
    {
      "id": "abc12345",
      "description": "创建一个test文件夹",
      "status": "completed",
      "progress": 100,
      "created_at": "2026-02-09T12:00:00",
      "updated_at": "2026-02-09T12:01:00",
      "working_directory": "C:\\Users\\TP\\Desktop",
      "current_step": "任务完成",
      "error_message": null,
      "token_usage": {"prompt_tokens": 500, "completion_tokens": 120}
    }
  ],
  "total": 42,
  "limit": 50,
  "offset": 0
}
```

#### `POST /api/tasks` — 创建任务

**请求体：**
```json
{
  "description": "创建一个名为test的文件夹",
  "working_directory": "C:\\Users\\TP\\Desktop",
  "todo_items": ["创建文件夹", "写入README"]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `description` | string | 否* | 任务描述 |
| `working_directory` | string | 否 | 工作目录（默认使用配置中的目录） |
| `todo_items` | string[] | 否 | TODO 清单，第一项作为默认描述 |

> *`description` 和 `todo_items` 至少提供一个。

**响应：**
```json
{
  "success": true,
  "task": { "id": "abc12345", "status": "waiting", ... }
}
```

#### `GET /api/tasks/{task_id}` — 获取任务详情

**响应：**
```json
{
  "task": {
    "id": "abc12345",
    "description": "...",
    "status": "running",
    "progress": 45,
    "current_step": "正在读取文件...",
    "next_step": "分析内容",
    "token_usage": {},
    "todo_items": [],
    "need_input": { "question": null, "options": null, "user_response": null }
  }
}
```

#### `PUT /api/tasks/{task_id}` — 更新任务

仅允许更新 `waiting` 状态的任务。

**请求体：**
```json
{
  "description": "新的任务描述",
  "working_directory": "C:\\new\\path"
}
```

#### `PUT /api/tasks/{task_id}/description` — 更新任务描述

特殊行为：如果任务已结束（`completed`/`failed`/`cancelled`），会自动重置状态并重新执行。

**请求体：**
```json
{
  "description": "修改后的任务描述"
}
```

**响应：**
```json
{
  "success": true,
  "task": { ... },
  "message": "任务描述已更新，任务已重新开始执行",
  "restarted": true
}
```

#### `DELETE /api/tasks/{task_id}` — 删除任务

如果任务正在执行，会先自动停止。

---

### 任务执行控制

#### `POST /api/tasks/{task_id}/run` — 启动任务

**请求体（可选）：**
```json
{
  "auto_mode": true
}
```

**前置条件：**
- 任务状态必须是 `waiting`、`paused` 或 `failed`
- 不能有同 ID 的任务正在运行

**错误码：**
| HTTP 状态 | 说明 |
|-----------|------|
| 400 | 任务已在执行中 / 已完成 / 状态不允许 |
| 404 | 任务不存在 |
| 409 | 任务正在启动或停止中（竞态保护） |

#### `POST /api/tasks/{task_id}/stop` — 停止任务

将运行中的任务暂停。状态变为 `paused`。

**前置条件：** 任务状态为 `running` 或 `need_input`

#### `POST /api/tasks/{task_id}/cancel` — 取消任务

永久取消任务。状态变为 `cancelled`。

#### `POST /api/tasks/{task_id}/input` — 提交用户输入

当任务状态为 `need_input` 时，提交用户的回答。

**请求体：**
```json
{
  "input_text": "用户的回答内容"
}
```

提交后任务自动继续执行。

#### `POST /api/tasks/{task_id}/append` — 追加任务

对已结束的任务追加新的需求描述，任务会自动重置并重新开始。

**请求体：**
```json
{
  "additional_description": "还需要添加一个配置文件"
}
```

**前置条件：** 任务状态为 `completed`、`failed` 或 `cancelled`

#### `POST /api/tasks/{task_id}/retry` — 重试失败任务

重置失败任务的状态并重新开始执行。

**前置条件：** 任务状态为 `failed`

#### `GET /api/tasks/{task_id}/history` — 获取执行历史

返回任务的完整执行记录，包括消息历史、命令结果、文件操作等。

**响应：**
```json
{
  "task_id": "abc12345",
  "description": "...",
  "status": "completed",
  "progress": 100,
  "messages": [
    {"role": "user", "content": "创建test文件夹", "type": "user_input", "timestamp": "..."},
    {"role": "assistant", "content": "正在创建...", "type": "ai_response", "timestamp": "..."},
    {"role": "tool", "content": "{\"success\": true}", "type": "tool_result", "timestamp": "..."}
  ],
  "command_results": [
    {"command": "mkdir test", "return_code": 0, "stdout": "", "stderr": "", "success": true}
  ],
  "file_operations": [],
  "last_thinking": "AI的最后思考内容",
  "current_step": "任务完成",
  "token_usage": {"prompt_tokens": 500, "completion_tokens": 120},
  "todo_items": []
}
```

---

### TODO 管理

任务支持附带 TODO 清单，由 AI 在执行过程中自动更新进度。

#### `GET /api/tasks/{task_id}/todos` — 获取 TODO 列表

#### `POST /api/tasks/{task_id}/todos` — 添加 TODO 项

**请求体：**
```json
{
  "content": "需要完成的事项"
}
```

> 特殊行为：如果任务已结束且有未完成的 TODO，添加后会自动重启任务。

#### `PUT /api/tasks/{task_id}/todos/{todo_id}` — 更新 TODO 内容

**请求体：**
```json
{
  "content": "修改后的内容"
}
```

#### `DELETE /api/tasks/{task_id}/todos/{todo_id}` — 删除 TODO 项

---

### 统计信息

#### `GET /api/stats` — 获取全局统计

**响应：**
```json
{
  "total": 42,
  "status_counts": {
    "waiting": 2,
    "running": 1,
    "need_input": 0,
    "completed": 35,
    "failed": 3,
    "cancelled": 1,
    "paused": 0
  },
  "today_completed": 5,
  "active_engines": 1
}
```

---

### 系统配置

#### `GET /api/config` — 获取配置

**响应：**
```json
{
  "config": {
    "workspace": { "default_working_directory": "C:\\Users\\TP" },
    "ui": { "auto_scroll": true },
    "task": { "max_iterations": 50 },
    "api": {
      "api_key": "sk-...",
      "base_url": "https://api.moonshot.cn/",
      "model": "kimi-k2-0905-preview"
    }
  }
}
```

#### `PUT /api/config` — 更新配置

**请求体（部分更新）：**
```json
{
  "api": {
    "api_key": "sk-new-key",
    "model": "deepseek-reasoner"
  }
}
```

> ⚠️ **有任务运行时无法修改配置**，返回 `409 Conflict`。

#### `GET /api/settings/locked` — 检查配置是否被锁定

**响应：**
```json
{
  "locked": false,
  "active_tasks": 0
}
```

---

## WebSocket

### 连接

```
ws://127.0.0.1:8000/ws?token=YOUR_TOKEN
```

Token 可选，通过环境变量 `AUTOMATEX_WS_TOKEN` 配置。

### 连接限制

| 配置 | 值 |
|------|-----|
| 最大总连接数 | 100 |
| 每客户端最大连接数 | 5 |
| 每连接最大订阅数 | 20 |
| 心跳间隔 | 30 秒 |
| 心跳超时 | 300 秒（5 分钟无心跳视为死连接） |

### 客户端发送消息格式

#### 心跳

```json
{"type": "ping"}
```

服务端响应 pong。

#### 订阅任务更新

```json
{"type": "subscribe", "task_id": "abc12345"}
```

#### 取消订阅

```json
{"type": "unsubscribe", "task_id": "abc12345"}
```

### 服务端推送消息类型

#### 任务状态更新

```json
{
  "type": "task_status",
  "task_id": "abc12345",
  "data": { "id": "abc12345", "status": "running", "progress": 45, ... }
}
```

#### AI 思考内容（流式）

```json
{
  "type": "ai_thinking",
  "task_id": "abc12345",
  "content": "AI正在分析任务...",
  "partial": true
}
```

`partial: true` 表示内容还在流式输出中。

#### 工具开始执行

```json
{
  "type": "tool_start",
  "task_id": "abc12345",
  "tool": "read_file",
  "args": {"path": "test.txt"},
  "call_id": "call_001"
}
```

#### 工具执行完成

```json
{
  "type": "tool_end",
  "task_id": "abc12345",
  "tool": "read_file",
  "result": {"content": "文件内容..."},
  "call_id": "call_001",
  "duration_ms": 150
}
```

### 典型使用流程

```javascript
const ws = new WebSocket('ws://127.0.0.1:8000/ws');

ws.onopen = () => {
  // 订阅某个任务
  ws.send(JSON.stringify({ type: 'subscribe', task_id: 'abc12345' }));
};

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  
  switch (msg.type) {
    case 'task_status':
      console.log('任务状态:', msg.data.status);
      break;
    case 'ai_thinking':
      console.log('AI思考:', msg.content);
      break;
    case 'tool_start':
      console.log(`工具 ${msg.tool} 开始执行`);
      break;
    case 'tool_end':
      console.log(`工具 ${msg.tool} 完成，耗时 ${msg.duration_ms}ms`);
      break;
  }
};

// 保持心跳
setInterval(() => {
  ws.send(JSON.stringify({ type: 'ping' }));
}, 25000);
```

---

## Python API

### AutomateX 类

```python
from src.tasks import AutomateX

# 创建实例（AI 模型自动从 user_config.json 读取）
ax = AutomateX(
    working_directory="C:\\workspace",  # 可选，默认用当前目录
    use_mcp=True,                       # 是否使用 MCP Server
    show_reasoning=False                # 是否打印 AI 思考过程
)

# 运行任务
task = ax.run("创建test文件夹")

# 交互式运行（遇到 ask 时从控制台获取输入）
task = ax.run_interactive("整理当前目录")

# 仅创建任务（不执行）
task = ax.create_task("待执行的任务")

# 继续执行需要输入的任务
task = ax.continue_task("task_id", user_input="回答内容")

# 任务管理
tasks = ax.list_tasks(status="completed")  # 按状态筛选
task = ax.get_task("task_id")              # 获取详情
ax.cancel_task("task_id")                  # 取消
ax.retry_task("task_id")                   # 重试
ax.delete_task("task_id")                  # 删除
ax.cleanup(days=30)                        # 清理旧任务
stats = ax.get_statistics()                # 统计信息

# 自定义输出回调
ax.set_output_callback(lambda msg: print(f"[LOG] {msg}"))
```

### 快捷函数

```python
from src.tasks import quick_run, interactive_run

# 最简方式
task = quick_run("创建test文件夹")

# 交互式
task = interactive_run("帮我整理文件")
```

### Task 对象属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `id` | str | 任务唯一 ID |
| `description` | str | 任务描述 |
| `status` | TaskStatus | 当前状态 |
| `progress` | int | 进度百分比 (0-100) |
| `working_directory` | str | 工作目录 |
| `current_step` | str | 当前步骤描述 |
| `next_step` | str | 下一步计划 |
| `error_message` | str | 错误信息（失败时） |
| `command_results` | List[CommandResult] | 执行过的命令列表 |
| `file_operations` | List[FileOperation] | 文件操作记录 |
| `token_usage` | dict | Token 用量统计 |
| `todo_items` | List[TodoItem] | TODO 清单 |
| `need_input` | NeedInput | 用户输入请求（问题 + 选项） |
| `created_at` | str | 创建时间 |
| `completed_at` | str | 完成时间 |
| `retry_count` | int | 重试次数 |

---

## 错误处理

### HTTP 状态码

| 状态码 | 说明 |
|--------|------|
| 200 | 成功 |
| 400 | 请求参数错误 / 状态不允许 |
| 404 | 资源不存在 |
| 409 | 冲突（任务正在启动/停止中，或有任务运行时修改配置） |
| 500 | 服务器内部错误 |

### 错误响应格式

```json
{
  "detail": "错误描述信息"
}
```

### 常见错误场景

| 场景 | 状态码 | detail |
|------|--------|--------|
| 启动已完成的任务 | 400 | "任务已完成，无需重复执行。如需重新执行，请使用追加或重试功能" |
| 停止非运行任务 | 400 | "任务状态 waiting 无法停止" |
| 运行时改配置 | 409 | "有任务正在运行，无法修改设置" |
| API Key 未配置 | 任务标记 failed | "API 未配置，请在设置中填写 API Key、Base URL 和 Model。" |

---

## 注意事项

### 并发安全

- 同一任务 ID **不能同时运行多个引擎**实例，`EngineManager` 保证线程安全
- 任务启动/停止操作有竞态保护（`is_starting` / `is_stopping` 检查）
- 配置修改在有任务运行时被锁定

### 性能考量

- 任务执行在 **后台线程** 中运行，不阻塞 API 响应
- WebSocket 广播使用 **asyncio.gather** 批量发送
- WebSocket 回调采用 **fire-and-forget** 模式，不阻塞引擎执行
- Windows 下子线程使用 `SelectorEventLoop`（避免 ProactorEventLoop 管道写入 bug）

### 安全注意

- API 仅监听 `127.0.0.1`，不对外暴露
- CORS 限制仅允许本地来源
- WebSocket 支持可选 Token 认证
- MCP Server 有完整的路径验证和命令过滤
- API Key 存储在本地 `user_config.json`，不通过网络传输

### 工作目录

- 每个任务有独立的工作目录
- 默认使用 `user_config.json` 中配置的目录
- 未配置时使用用户主目录
- 优先级：任务指定 > 用户配置 > 环境变量 > 用户主目录

### Token 消耗优化

V3 两阶段工具调用大幅降低了 Token 消耗（约 90%）：
- 第一阶段仅传工具名称列表，不传参数说明
- 第二阶段仅注入被选中工具的精简描述
- FIFO 上下文窗口限制历史消息数量（默认 20 条）
