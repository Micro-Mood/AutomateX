"""
任务数据模型定义
================

定义任务、状态、命令结果等核心数据结构。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskStatus(Enum):
    """任务状态枚举"""
    WAITING = "waiting"           # 等待执行
    RUNNING = "running"           # 运行中
    NEED_INPUT = "need_input"     # 等待用户输入
    COMPLETED = "completed"       # 已完成
    FAILED = "failed"             # 执行失败
    CANCELLED = "cancelled"       # 已取消
    PAUSED = "paused"             # 已暂停


# 有效的状态转换映射
VALID_STATUS_TRANSITIONS: Dict[TaskStatus, List[TaskStatus]] = {
    TaskStatus.WAITING: [TaskStatus.RUNNING, TaskStatus.CANCELLED],
    TaskStatus.RUNNING: [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.NEED_INPUT, TaskStatus.PAUSED, TaskStatus.CANCELLED],
    TaskStatus.NEED_INPUT: [TaskStatus.RUNNING, TaskStatus.PAUSED, TaskStatus.CANCELLED, TaskStatus.FAILED],
    TaskStatus.PAUSED: [TaskStatus.RUNNING, TaskStatus.CANCELLED, TaskStatus.WAITING],
    TaskStatus.COMPLETED: [TaskStatus.RUNNING, TaskStatus.WAITING],  # 允许重新执行或重置已完成的任务
    TaskStatus.FAILED: [TaskStatus.RUNNING, TaskStatus.WAITING],  # 允许重试失败的任务
    TaskStatus.CANCELLED: [TaskStatus.WAITING, TaskStatus.RUNNING],  # 允许重置或重新执行取消的任务
}


@dataclass
class CommandResult:
    """命令执行结果"""
    command: str                          # 执行的命令
    return_code: int                      # 返回码
    stdout: str                           # 标准输出
    stderr: str                           # 标准错误
    execution_time: float                 # 执行时间（秒）
    success: bool                         # 是否成功
    timeout: bool = False                 # 是否超时
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    raw_stdout_b64: Optional[str] = None  # 可选：原始 stdout 的 base64 编码（便于前端按原始字节重解码）
    raw_stderr_b64: Optional[str] = None  # 可选：原始 stderr 的 base64 编码

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "return_code": self.return_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "execution_time": self.execution_time,
            "success": self.success,
            "timeout": self.timeout,
            "timestamp": self.timestamp,
            "raw_stdout_b64": self.raw_stdout_b64,
            "raw_stderr_b64": self.raw_stderr_b64,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CommandResult:
        return cls(
            command=data["command"],
            return_code=data["return_code"],
            stdout=data["stdout"],
            stderr=data["stderr"],
            execution_time=data["execution_time"],
            success=data["success"],
            timeout=data.get("timeout", False),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
            raw_stdout_b64=data.get("raw_stdout_b64"),
            raw_stderr_b64=data.get("raw_stderr_b64"),
        )


@dataclass
class FileOperation:
    """文件操作记录"""
    operation_type: str      # create, write, append, delete, read
    path: str                # 文件路径
    success: bool            # 是否成功
    message: str             # 操作结果消息
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation_type": self.operation_type,
            "path": self.path,
            "success": self.success,
            "message": self.message,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FileOperation:
        return cls(
            operation_type=data["operation_type"],
            path=data["path"],
            success=data["success"],
            message=data["message"],
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )


@dataclass
class ToolCallRecord:
    """工具调用记录"""
    id: str                                      # 调用ID
    tool: str                                    # 工具名称
    args: Dict[str, Any]                         # 调用参数
    success: bool = True                         # 是否成功
    result: Optional[Dict[str, Any]] = None      # 执行结果
    error: Optional[str] = None                  # 错误信息
    duration_ms: float = 0.0                     # 执行耗时（毫秒）
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tool": self.tool,
            "args": self.args,
            "success": self.success,
            "result": self.result,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ToolCallRecord:
        return cls(
            id=data["id"],
            tool=data["tool"],
            args=data.get("args", {}),
            success=data.get("success", True),
            result=data.get("result"),
            error=data.get("error"),
            duration_ms=data.get("duration_ms", 0.0),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )


@dataclass
class Message:
    """对话消息"""
    role: str                                    # user, assistant, tool, system
    content: str                                 # 消息内容
    message_type: str = "text"                   # text, tool_call, tool_result, thinking
    tool_call_id: Optional[str] = None           # 工具调用ID（role=tool 时使用）
    tool_calls: List[ToolCallRecord] = field(default_factory=list)  # 工具调用列表（role=assistant 时）
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "message_type": self.message_type,
            "tool_call_id": self.tool_call_id,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Message:
        tool_calls = [
            ToolCallRecord.from_dict(tc) for tc in data.get("tool_calls", [])
        ]
        return cls(
            role=data["role"],
            content=data["content"],
            message_type=data.get("message_type", "text"),
            tool_call_id=data.get("tool_call_id"),
            tool_calls=tool_calls,
            timestamp=data.get("timestamp", datetime.now().isoformat()),
            metadata=data.get("metadata", {}),
        )

    def to_api_format(self) -> Dict[str, Any]:
        """转换为 API 调用格式"""
        msg: Dict[str, Any] = {
            "role": self.role,
            "content": self.content
        }
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        return msg


@dataclass
class TodoItem:
    """TODO 清单项"""
    id: str                                          # 唯一ID
    content: str                                     # 内容描述
    completed: bool = False                          # 是否已完成
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "completed": self.completed,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TodoItem:
        return cls(
            id=data["id"],
            content=data["content"],
            completed=data.get("completed", False),
            created_at=data.get("created_at", datetime.now().isoformat()),
        )


@dataclass
class NeedInputInfo:
    """用户输入请求信息"""
    required: bool = False
    question: str = ""
    options: List[str] = field(default_factory=list)
    user_response: Optional[str] = None
    responded_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "required": self.required,
            "question": self.question,
            "options": self.options,
            "user_response": self.user_response,
            "responded_at": self.responded_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> NeedInputInfo:
        return cls(
            required=data.get("required", False),
            question=data.get("question", ""),
            options=data.get("options", []),
            user_response=data.get("user_response"),
            responded_at=data.get("responded_at"),
        )


@dataclass
class Task:
    """任务实体"""
    id: str                                           # 任务唯一ID
    description: str                                  # 任务描述（用户原始输入）
    status: TaskStatus = TaskStatus.WAITING           # 任务状态
    progress: int = 0                                 # 进度百分比 0-100
    
    # 时间戳
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    
    # 执行相关
    current_step: str = ""                            # 当前步骤说明
    next_step: str = ""                               # 下一步计划
    retry_count: int = 0                              # 重试次数
    max_retries: int = 3                              # 最大重试次数
    
    # 用户输入
    need_input: NeedInputInfo = field(default_factory=NeedInputInfo)
    
    # 执行结果
    command_results: List[CommandResult] = field(default_factory=list)
    file_operations: List[FileOperation] = field(default_factory=list)
    
    # 错误信息
    error_message: Optional[str] = None
    
    # AI思考过程
    last_thinking: str = ""
    
    # 工作目录
    working_directory: Optional[str] = None
    
    # Token 用量统计
    token_usage: Dict[str, int] = field(default_factory=lambda: {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    })

    # TODO 清单
    todo_items: List[TodoItem] = field(default_factory=list)

    @staticmethod
    def generate_id() -> str:
        """生成唯一任务ID"""
        return f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    def update_status(self, new_status: TaskStatus, force: bool = False) -> None:
        """
        更新任务状态
        
        Args:
            new_status: 新的任务状态
            force: 是否强制更新（跳过状态转换验证）
            
        Raises:
            ValueError: 如果状态转换无效
        """
        # 验证状态转换
        if not force and self.status != new_status:
            valid_targets = VALID_STATUS_TRANSITIONS.get(self.status, [])
            if new_status not in valid_targets:
                raise ValueError(
                    f"无效的状态转换: {self.status.value} -> {new_status.value}, "
                    f"有效目标: {[s.value for s in valid_targets]}"
                )
        
        self.status = new_status
        self.updated_at = datetime.now().isoformat()
        
        if new_status == TaskStatus.RUNNING and not self.started_at:
            self.started_at = datetime.now().isoformat()
        elif new_status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            self.completed_at = datetime.now().isoformat()

    def add_command_result(self, result: CommandResult) -> None:
        """添加命令执行结果"""
        self.command_results.append(result)
        self.updated_at = datetime.now().isoformat()

    def add_file_operation(self, operation: FileOperation) -> None:
        """添加文件操作记录"""
        self.file_operations.append(operation)
        self.updated_at = datetime.now().isoformat()

    def set_user_input(self, response: str) -> None:
        """设置用户输入"""
        self.need_input.user_response = response
        self.need_input.responded_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status.value,
            "progress": self.progress,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "current_step": self.current_step,
            "next_step": self.next_step,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "need_input": self.need_input.to_dict(),
            "command_results": [r.to_dict() for r in self.command_results],
            "file_operations": [o.to_dict() for o in self.file_operations],
            "error_message": self.error_message,
            "last_thinking": self.last_thinking,
            "working_directory": self.working_directory,
            "token_usage": self.token_usage,
            "todo_items": [t.to_dict() for t in self.todo_items],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Task:
        """从字典创建任务"""
        task = cls(
            id=data["id"],
            description=data["description"],
            status=TaskStatus(data["status"]),
            progress=data.get("progress", 0),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            current_step=data.get("current_step", ""),
            next_step=data.get("next_step", ""),
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 3),
            need_input=NeedInputInfo.from_dict(data.get("need_input", {})),
            error_message=data.get("error_message"),
            last_thinking=data.get("last_thinking", ""),
            working_directory=data.get("working_directory"),
            token_usage=data.get("token_usage", {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }),
        )
        
        # 恢复命令结果
        for r in data.get("command_results", []):
            task.command_results.append(CommandResult.from_dict(r))
        
        # 恢复文件操作
        for o in data.get("file_operations", []):
            task.file_operations.append(FileOperation.from_dict(o))
        
        # 恢复 TODO 清单
        for t in data.get("todo_items", []):
            task.todo_items.append(TodoItem.from_dict(t))
        
        # 向后兼容：旧任务的 description 含 [追加需求] 标记时自动迁移为 TODO 列表
        if not task.todo_items and "[追加需求]" in task.description:
            parts = task.description.split("\n\n[追加需求]\n")
            main_desc = parts[0].strip()
            if main_desc:
                task.todo_items.append(TodoItem(
                    id=uuid.uuid4().hex[:8],
                    content=main_desc,
                    completed=task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED),
                    created_at=task.created_at,
                ))
            for ap in parts[1:]:
                ap = ap.strip()
                if ap:
                    task.todo_items.append(TodoItem(
                        id=uuid.uuid4().hex[:8],
                        content=ap,
                        completed=False,
                        created_at=task.updated_at,
                    ))
            # 清理 description：只保留主描述
            task.description = main_desc
        
        return task

    def __repr__(self) -> str:
        return f"Task(id={self.id}, status={self.status.value}, progress={self.progress}%)"
