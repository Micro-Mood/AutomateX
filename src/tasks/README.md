# AutomateX 任务引擎

本目录包含 AutomateX V3 任务引擎的核心实现，负责 AI 驱动的任务编排与执行。

## 目录结构

| 文件/目录 | 说明 |
|-----------|------|
| `engine.py` | V3 两阶段工具调用引擎核心（972 行） |
| `tools.py` | 工具定义和注册，紧凑格式优化 Token 用量 |
| `context.py` | FIFO 上下文管理，滑动窗口控制历史长度 |
| `mcp_client.py` | MCP Server 客户端 + 本地 fallback 执行 |
| `api.py` | `AutomateX` 高层 API（`run` / `create_task` / `continue_task` 等） |
| `main.py` | CLI 入口，支持 `--interactive` / `--list` / `--continue` 等参数 |
| `models.py` | 数据模型：`Task`、`TaskStatus` 状态机、`CommandResult` |
| `store.py` | 任务持久化（JSON 原子写入 + 崩溃恢复） |
| `scheduler.py` | 任务调度器：队列管理、定时任务、暂停/恢复 |
| `config.py` | 配置兼容层（委托到 `src.config.ConfigManager`） |
| `chat/` | AI 接口封装（OpenAI 兼容 API，支持流式响应 + Token 追踪） |
| `prompt/` | 提示词模板（`select.md`） |
| `messages/` | 任务消息历史（每个任务独立 JSON 文件，运行时生成） |
| `examples/` | 使用示例（7 种典型场景） |

## V3 架构

### 两阶段工具调用

```
Phase 1: SELECT                          Phase 2: PARAMS
┌──────────────┐    ┌──────────────┐     ┌──────────────┐    ┌──────────────┐
│ 用户任务描述  │ -> │ AI 选择工具   │     │ 注入工具说明  │ -> │ AI 填写参数   │
│              │    │ (仅返回名称)  │     │ (精简格式)   │    │ 执行工具调用  │
└──────────────┘    └──────┬───────┘     └──────────────┘    └──────┬───────┘
                           │                                        │
                           └──── 工具名称列表 ─────────────────────→ │
                                                                    ↓
                                                            ┌──────────────┐
                                                            │ MCP / Local  │
                                                            │ 执行并返回   │
                                                            └──────────────┘
```

### 执行流程

1. **SELECT** — AI 分析任务描述，返回需要使用的工具名称列表
2. **PARAMS** — 引擎注入被选工具的精简说明，AI 填写参数并生成调用
3. **EXEC** — 引擎通过 MCP Client（或 Local fallback）执行工具
4. **RESULT** — 将执行结果写回上下文，进入下一轮循环

### FIFO 上下文

- 最大保留 20 条历史消息
- 超出时删除最早的消息
- System 消息始终保留
- 上下文阶段枚举：`SELECT` / `PARAMS` / `EXEC` / `RESULT`

### 任务状态机

```
WAITING ──→ RUNNING ──→ COMPLETED
   │           │
   │           ├──→ NEED_INPUT ──→ RUNNING
   │           │
   │           ├──→ PAUSED ──→ RUNNING
   │           │
   │           └──→ FAILED ──→ WAITING (retry)
   │
   └──→ CANCELLED
```

## 工具分类

| 类别 | 工具 |
|------|------|
| read | `read_file`, `list_dir`, `exists` |
| search | `search_files`, `search_content` |
| edit | `write_file`, `create_dir`, `delete`, `move`, `copy` |
| exec | `run` |
| ctrl | `done`, `fail`, `ask` |

## 使用示例

### Python API

```python
from src.tasks import AutomateX

ax = AutomateX()
task = ax.run("列出当前目录的文件")
print(task.current_step)
```

### 命令行

```bash
python -m src.tasks.main "创建 test 文件夹"
python -m src.tasks.main --list
python -m src.tasks.main --interactive "帮我整理文件"
```

## 扩展工具

在 `tools.py` 中注册新工具：

```python
register("new_tool", "工具描述", [
    ToolParam("param1", "str", "参数说明"),
    ToolParam("param2", "int", "可选参数", False, "0"),
], "category")
```

然后在 `engine.py` 的 `_exec_tool` 方法中添加执行逻辑。
