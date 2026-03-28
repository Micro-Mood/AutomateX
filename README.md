# AutomateX

<div align="center">

<h3>🚀 Intelligent Task Automation Engine for Windows</h3>

**Drive your PC with natural language — let AI handle the repetitive work**

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows-blue.svg)](https://www.microsoft.com/windows)

[**中文文档**](README_CN.md)

</div>

---

## ✨ Why AutomateX?

| Traditional AI Agent | AutomateX V3 Engine |
|---------------------|---------------------|
| Sends all tool descriptions every call | Two-phase on-demand injection, **saves 90% tokens** |
| Single tool format | Compact description format, **saves another 80%** |
| AI directly executes system commands | MCP security isolation, **prevents dangerous ops** |
| Unbounded context growth | FIFO sliding window, **stable long conversations** |

## 🖼️ Screenshots

**📊 Dashboard** — Task statistics & quick actions

<img src="docs/images/dashboard.png" width="800"/>

**📋 Task Detail** — Execution results & real-time feedback

<img src="docs/images/task-detail.png" width="800"/>

**💬 Chat History** — AI interaction & TODO tracking

<img src="docs/images/task-history.png" width="800"/>

## 🌟 Key Features

| Feature | Description |
|---------|-------------|
| 🤖 **Multi-Model Support** | Kimi, DeepSeek, Qwen, GPT — any OpenAI-compatible API |
| ⚡ **Cost Efficient** | V3 two-phase architecture + compact format, **90%+ token reduction** |
| 🔒 **Security Isolation** | MCP Server runs in a separate process, dangerous commands auto-blocked |
| 🖥️ **Ready to Use** | Electron desktop app, no command line needed |
| 📝 **Customizable** | Externalized prompts, easily tweak AI behavior |
| 🔄 **Smart Context** | FIFO window management, supports ultra-long task conversations |

## 📖 Documentation

| Document | Description |
|----------|-------------|
| [Getting Started](docs/getting-started.md) | Installation, configuration, first run |
| [API Reference](docs/api-reference.md) | REST API, WebSocket, Python API reference |
| [Architecture](docs/architecture.md) | V3 engine, MCP Server, security mechanisms |
| [Deployment](docs/deployment.md) | Electron packaging, production deployment, troubleshooting |

## Quick Start

### 1. Install Dependencies

```bash
# Create virtual environment
python -m venv .venv
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure API Key

**Option A (Recommended):** Configure API Key, Base URL, and Model directly through the desktop app's settings panel.

**Option B:** Edit `src/config/user_config.json`:

```json
{
  "api": {
    "api_key": "your-api-key-here",
    "base_url": "https://api.moonshot.cn/",
    "model": "kimi-k2-0905-preview"
  }
}
```

Supports Kimi, DeepSeek, Qwen, and other OpenAI-compatible APIs.

### 3. Launch

**Desktop App (Recommended):**

```bash
cd src/web
npm install
npm start
```

**CLI:**

```bash
python -m src.tasks.main "Create a folder named test"
```

**Python API:**

```python
from src.tasks import AutomateX

ax = AutomateX(model="deepseek")
task = ax.run("List all files in the current directory")
```

## Project Structure

```
AutomateX/
├── src/
│   ├── config/              # Unified configuration system
│   │   ├── loader.py        # ConfigManager singleton loader
│   │   ├── sys_config.json  # System config (MCP, logging, security)
│   │   └── user_config.json # User preferences (incl. API key)
│   ├── tasks/               # Task engine core
│   │   ├── engine.py        # V3 two-phase tool-calling engine
│   │   ├── tools.py         # Tool definitions & registry
│   │   ├── context.py       # FIFO context management
│   │   ├── mcp_client.py    # MCP client (with local fallback)
│   │   ├── api.py           # AutomateX high-level API
│   │   ├── models.py        # Data models & state machine
│   │   ├── store.py         # Task persistence (atomic JSON writes)
│   │   ├── scheduler.py     # Task scheduler
│   │   ├── main.py          # CLI entry point
│   │   ├── config.py        # Config compatibility layer
│   │   ├── chat/            # AI interface (OpenAI-compatible API)
│   │   ├── prompt/          # Prompt templates
│   │   ├── messages/        # Task message history (runtime)
│   │   └── examples/        # Usage examples
│   ├── mcp/                 # MCP Server (tool execution service)
│   │   ├── server.py        # JSON-RPC 2.0 server
│   │   ├── cli.py           # CLI tool (Click + Rich)
│   │   ├── sdk.py           # Python client SDK
│   │   ├── core/            # Core modules
│   │   │   ├── security.py  # Path safety & command filtering
│   │   │   ├── cache.py     # Multi-layer caching
│   │   │   ├── config.py    # Pydantic config models
│   │   │   └── exceptions.py # Unified exception system
│   │   └── modules/         # Tool modules
│   │       ├── read/        # File read, directory listing
│   │       ├── search/      # Filename & content search
│   │       ├── edit/        # File CRUD & content editing
│   │       └── execute/     # Process management & command execution
│   └── web/                 # Electron desktop app
│       ├── index.html       # Frontend (SPA)
│       ├── main.js          # Electron main process
│       ├── preload.js       # Preload security bridge
│       ├── server.py        # FastAPI backend
│       ├── ws_manager.py    # WebSocket connection manager
│       ├── assets/          # Static assets (icons, etc.)
│       ├── scripts/         # Build scripts
│       └── build/           # Embedded Python runtime
├── docs/                    # Documentation
├── requirements.txt         # Python dependencies
└── README.md                # This file
```

## Architecture

### V3 Two-Phase Tool Calling

```
┌─────────────────────────────────────────────────────────────┐
│                     Task Engine V3                           │
├─────────────────────────────────────────────────────────────┤
│  Phase 1: SELECT                                            │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│  │ User Task   │ -> │ AI selects  │ -> │ Tool List   │     │
│  │ (brief)     │    │ tools(names)│    │ ["read_file"]│    │
│  └─────────────┘    └─────────────┘    └─────────────┘     │
├─────────────────────────────────────────────────────────────┤
│  Phase 2: PARAMS                                            │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│  │ Tool specs  │ -> │ AI fills    │ -> │ Execute     │     │
│  │ (compact)   │    │ params      │    │ MCP/Local   │     │
│  └─────────────┘    └─────────────┘    └─────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

**Advantages:**
- Phase 1 only sends tool name list — no detailed descriptions
- Phase 2 only injects specs for selected tools
- Context uses FIFO sliding window — no unbounded growth

### MCP Server

The MCP (Model Context Protocol) Server provides a security-isolated tool execution environment:

- **JSON-RPC 2.0** protocol, supporting TCP and stdio modes
- **Path safety**: workspace scope validation + system path blocking + symlink detection
- **Command filtering**: 15+ dangerous pattern regex checks (recursive delete, format, registry ops, etc.)
- **Multi-layer caching**: file metadata (60s) / directory listing (30s) / search results (5min) with independent TTLs
- **Isolated process**, fully separated from the main application
- **Python SDK** (`sdk.py`) with full async client wrapper
- **Rich CLI** (`cli.py`) for interactive debugging and service management

## Available Tools

| Category | Tool | Description |
|----------|------|-------------|
| Read | `read_file` | Read file contents |
| Read | `list_dir` | List directory contents |
| Read | `exists` | Check if a path exists |
| Search | `search_files` | Search by filename |
| Search | `search_content` | Search within file contents |
| Edit | `write_file` | Write to a file |
| Edit | `create_dir` | Create a directory |
| Edit | `delete` | Delete a file or directory |
| Edit | `move` | Move / rename |
| Edit | `copy` | Copy a file |
| Execute | `run` | Run a command |
| Control | `done` | Mark task as completed |
| Control | `fail` | Mark task as failed |
| Control | `ask` | Ask the user |

## Custom Prompts

Edit `src/tasks/prompt/select.md` to customize AI behavior. Supported variables:

- `{cwd}` — Current working directory
- `{tool_list}` — Available tool list

## Configuration

Configuration files are in the `src/config/` directory, using a unified config management system:

**User config** `user_config.json` (includes API settings):
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

**System config** `sys_config.json`:
```json
{
  "mcp": { "server": { "host": "localhost", "port": 8080 } },
  "tasks": { "engine": { "backend": "v3" } },
  "logging": { "level": "INFO" }
}
```

Access all config via `ConfigManager`:
```python
from src.config import config

api_key = config.user.api_key
model = config.user.model
config.save_user_config()
```

## API Reference

### AutomateX Class

```python
from src.tasks.api import AutomateX

# Initialize (AI model auto-loaded from user_config.json)
ax = AutomateX(
    working_directory="./",     # Working directory
    use_mcp=True,               # Use MCP Server
    show_reasoning=False        # Show reasoning process
)

# Run a task
task = ax.run("task description")

# Interactive run (prompts for input from console when needed)
task = ax.run_interactive("task description")

# Create a task (without executing)
task = ax.create_task("task description")

# Continue a task (with optional user input)
task = ax.continue_task("task_id", user_input="answer")

# List tasks
tasks = ax.list_tasks()

# Get task status
task = ax.get_task("task_id")

# Retry a failed task
ax.retry_task("task_id")

# Cancel a task
ax.cancel_task("task_id")

# Clean up old tasks
ax.cleanup(days=30)

# Get statistics
stats = ax.get_statistics()
```

### Quick Functions

```python
from src.tasks.api import quick_run, interactive_run

# Quick run
task = quick_run("Create test.txt")

# Interactive run
task = interactive_run("Organize current directory")
```

## Development

### MCP Server

```bash
# Start MCP Server (TCP mode)
python -m src.mcp --port 8080

# Interactive testing via CLI
python -m src.mcp interactive --host 127.0.0.1 --port 8080
```

### Web UI

```bash
cd src/web
npm install
npm run dev        # Dev mode (DevTools enabled)
npm start          # Production start
npm run build:win  # Package Windows installer
```

### Run Examples

```bash
python -m src.tasks.examples.examples
```

### Dependencies

- **Python**: >= 3.10, see `requirements.txt`
- **Node.js**: >= 18.0.0, for the Electron desktop app
- **AI API**: Kimi / DeepSeek / Qwen or any OpenAI-compatible API

## License

MIT License

## Contributing

Issues and Pull Requests are welcome!
