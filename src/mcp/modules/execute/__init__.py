"""
MCP执行模块
实现任务管理、进程控制、输入输出等功能
"""

from src.mcp.modules.execute.handlers import (
    # 便捷函数
    run_command,
    # 任务生命周期
    create_task,
    start_task,
    stop_task,
    kill_task,
    get_task,
    list_tasks,
    # 输入输出
    write_stdin,
    stream_stdout,
    stream_stderr,
    # 任务控制
    wait_task,
    attach_task,
    detach_task,
    reset_handler,
)

__all__ = [
    "run_command",
    "create_task",
    "start_task",
    "stop_task",
    "kill_task",
    "get_task",
    "list_tasks",
    "write_stdin",
    "stream_stdout",
    "stream_stderr",
    "wait_task",
    "attach_task",
    "detach_task",
    "reset_handler",
]
