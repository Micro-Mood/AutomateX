"""
Windows智能任务自动化引擎 - Tasks模块
=======================================

本模块实现任务管理、调度、执行的核心功能。

快速开始::

    from src.tasks import AutomateX, quick_run
    
    # 方式1: 使用快捷函数
    task = quick_run("创建一个test文件夹")
    
    # 方式2: 使用API类
    ax = AutomateX()
    task = ax.run("列出当前目录的文件")
    
    # 方式3: 交互式运行
    ax.run_interactive("帮我整理文件")
"""

from .models import Task, TaskStatus, CommandResult, FileOperation, ToolCallRecord, Message
from .store import TaskStore
from .scheduler import TaskScheduler
from .api import AutomateX, quick_run, interactive_run

# V3 核心模块
from .tools import Tool, ToolParam, TOOLS, get_tool, get_all_names, get_compact_desc
from .context import Context, Phase, Message as ContextMessage
from .mcp_client import MCPClient, MCPResult
from .engine import TaskEngine, EngineConfig

__all__ = [
    # 主接口
    "AutomateX",
    "quick_run",
    "interactive_run",
    # 数据模型
    "Task",
    "TaskStatus",
    "CommandResult",
    "FileOperation",
    "ToolCallRecord",
    "Message",
    # 存储
    "TaskStore",
    # 调度
    "TaskScheduler",
    # V3 引擎
    "TaskEngine",
    "EngineConfig",
    "Context",
    "Phase",
    "MCPClient",
    "MCPResult",
    "Tool",
    "ToolParam",
    "TOOLS",
    "get_tool",
    "get_all_names",
    "get_compact_desc",
]
