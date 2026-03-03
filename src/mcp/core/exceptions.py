"""
MCP异常模块
定义所有MCP相关的异常类
"""

from typing import Any, Dict, Optional
from datetime import datetime, timezone


class MCPError(Exception):
    """MCP基础异常类"""
    
    error_code: str = "MCP_ERROR"
    http_status: int = 500
    
    def __init__(
        self, 
        message: str, 
        details: Optional[Dict[str, Any]] = None,
        suggestion: Optional[str] = None,
        cause: Optional[Exception] = None
    ):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.suggestion = suggestion
        self.cause = cause
        self.timestamp = datetime.now(timezone.utc).isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        result = {
            "code": self.error_code,
            "message": self.message,
            "timestamp": self.timestamp,
        }
        
        if self.details:
            result["details"] = self.details
        
        if self.suggestion:
            result["suggestion"] = self.suggestion
        
        if self.cause:
            result["cause"] = str(self.cause)
        
        return result
    
    def __str__(self) -> str:
        return f"[{self.error_code}] {self.message}"


class FileNotFoundError(MCPError):
    """文件或目录不存在"""
    
    error_code = "FILE_NOT_FOUND"
    http_status = 404
    
    def __init__(self, path: str, **kwargs):
        super().__init__(
            message=f"文件或目录不存在: {path}",
            details={"path": path},
            suggestion="请检查路径是否正确",
            **kwargs
        )


class PermissionDeniedError(MCPError):
    """权限不足"""
    
    error_code = "PERMISSION_DENIED"
    http_status = 403
    
    def __init__(self, path: str, operation: str = "access", **kwargs):
        super().__init__(
            message=f"权限不足，无法{operation}: {path}",
            details={"path": path, "operation": operation},
            suggestion="请检查文件权限或以管理员身份运行",
            **kwargs
        )


class PathOutsideWorkspaceError(MCPError):
    """路径超出工作区范围"""
    
    error_code = "PATH_OUTSIDE_WORKSPACE"
    http_status = 403
    
    def __init__(self, path: str, workspace: str, **kwargs):
        super().__init__(
            message=f"路径超出工作区范围: {path}",
            details={"path": path, "workspace": workspace},
            suggestion="所有操作必须在工作区内进行",
            **kwargs
        )


class ResourceLimitExceededError(MCPError):
    """超出资源限制"""
    
    error_code = "RESOURCE_LIMIT_EXCEEDED"
    http_status = 413
    
    def __init__(
        self, 
        resource_type: str, 
        current_value: Any, 
        limit_value: Any, 
        **kwargs
    ):
        super().__init__(
            message=f"超出{resource_type}限制: 当前 {current_value}, 限制 {limit_value}",
            details={
                "resource_type": resource_type,
                "current_value": current_value,
                "limit_value": limit_value
            },
            suggestion="请减少操作规模或调整配置限制",
            **kwargs
        )


class SizeLimitExceededError(ResourceLimitExceededError):
    """文件大小超出限制"""
    
    error_code = "SIZE_LIMIT_EXCEEDED"
    
    def __init__(self, path: str, size: int, limit: int, **kwargs):
        super().__init__(
            resource_type="文件大小",
            current_value=f"{size / (1024*1024):.2f}MB",
            limit_value=f"{limit / (1024*1024):.2f}MB",
            **kwargs
        )
        self.details["path"] = path


class TimeoutError(MCPError):
    """操作超时"""
    
    error_code = "TIMEOUT"
    http_status = 408
    
    def __init__(self, operation: str, timeout_ms: int, **kwargs):
        super().__init__(
            message=f"操作超时: {operation} (超时时间: {timeout_ms}ms)",
            details={"operation": operation, "timeout_ms": timeout_ms},
            suggestion="请增加超时时间或优化操作",
            **kwargs
        )


class InvalidParameterError(MCPError):
    """参数无效"""
    
    error_code = "INVALID_PARAMETER"
    http_status = 400
    
    def __init__(self, parameter: str, value: Any, reason: str, **kwargs):
        super().__init__(
            message=f"无效参数 '{parameter}': {reason}",
            details={"parameter": parameter, "value": str(value), "reason": reason},
            suggestion="请检查参数格式和取值范围",
            **kwargs
        )


class ConcurrentModificationError(MCPError):
    """并发修改冲突"""
    
    error_code = "CONCURRENT_MODIFICATION"
    http_status = 409
    
    def __init__(self, path: str, **kwargs):
        super().__init__(
            message=f"并发修改冲突: {path}",
            details={"path": path},
            suggestion="请稍后重试或等待其他操作完成",
            **kwargs
        )


class TaskNotFoundError(MCPError):
    """任务不存在"""
    
    error_code = "TASK_NOT_FOUND"
    http_status = 404
    
    def __init__(self, task_id: str, **kwargs):
        super().__init__(
            message=f"任务不存在: {task_id}",
            details={"task_id": task_id},
            suggestion="请检查任务ID是否正确",
            **kwargs
        )


class TaskAlreadyRunningError(MCPError):
    """任务已在运行"""
    
    error_code = "TASK_ALREADY_RUNNING"
    http_status = 409
    
    def __init__(self, task_id: str, **kwargs):
        super().__init__(
            message=f"任务已在运行: {task_id}",
            details={"task_id": task_id},
            suggestion="请等待任务完成或停止后重试",
            **kwargs
        )


class TaskFailedError(MCPError):
    """任务执行失败"""
    
    error_code = "TASK_FAILED"
    http_status = 500
    
    def __init__(self, task_id: str, exit_code: int, stderr: str = "", **kwargs):
        super().__init__(
            message=f"任务执行失败: {task_id} (退出码: {exit_code})",
            details={"task_id": task_id, "exit_code": exit_code, "stderr": stderr},
            suggestion="请检查命令和参数是否正确",
            **kwargs
        )


class SystemError(MCPError):
    """系统级错误"""
    
    error_code = "SYSTEM_ERROR"
    http_status = 500
    
    def __init__(self, message: str, **kwargs):
        super().__init__(
            message=f"系统错误: {message}",
            suggestion="请联系系统管理员",
            **kwargs
        )


class BlockedPathError(MCPError):
    """访问被阻止的路径"""
    
    error_code = "BLOCKED_PATH"
    http_status = 403
    
    def __init__(self, path: str, **kwargs):
        super().__init__(
            message=f"禁止访问系统保护路径: {path}",
            details={"path": path},
            suggestion="此路径受系统保护，无法访问",
            **kwargs
        )


class BlockedCommandError(MCPError):
    """执行被阻止的命令"""
    
    error_code = "BLOCKED_COMMAND"
    http_status = 403
    
    def __init__(self, command: str, **kwargs):
        super().__init__(
            message=f"禁止执行危险命令",
            details={"command": command[:100]},  # 只显示前100个字符
            suggestion="此命令被安全策略禁止",
            **kwargs
        )


class EncodingError(MCPError):
    """编码错误"""
    
    error_code = "ENCODING_ERROR"
    http_status = 400
    
    def __init__(self, path: str, encoding: str, **kwargs):
        super().__init__(
            message=f"文件编码错误: {path}",
            details={"path": path, "encoding": encoding},
            suggestion="请检查文件编码或指定正确的编码格式",
            **kwargs
        )


class PatchApplyError(MCPError):
    """补丁应用失败"""
    
    error_code = "PATCH_APPLY_ERROR"
    http_status = 400
    
    def __init__(self, path: str, reason: str, **kwargs):
        super().__init__(
            message=f"补丁应用失败: {path}",
            details={"path": path, "reason": reason},
            suggestion="请检查补丁格式和内容是否正确",
            **kwargs
        )


class SymlinkError(MCPError):
    """符号链接错误"""
    
    error_code = "SYMLINK_ERROR"
    http_status = 403
    
    def __init__(self, path: str, target: str, **kwargs):
        super().__init__(
            message=f"符号链接指向工作区外: {path}",
            details={"path": path, "target": target},
            suggestion="不允许跟随指向工作区外的符号链接",
            **kwargs
        )


class MaxConcurrentTasksError(MCPError):
    """达到最大并发任务数"""
    
    error_code = "MAX_CONCURRENT_TASKS"
    http_status = 429
    
    def __init__(self, max_tasks: int, **kwargs):
        super().__init__(
            message=f"已达到最大并发任务数限制: {max_tasks}",
            details={"max_tasks": max_tasks},
            suggestion="请等待部分任务完成后重试",
            **kwargs
        )


def error_from_code(code: str, **kwargs) -> MCPError:
    """根据错误码创建异常"""
    error_map = {
        "FILE_NOT_FOUND": FileNotFoundError,
        "PERMISSION_DENIED": PermissionDeniedError,
        "PATH_OUTSIDE_WORKSPACE": PathOutsideWorkspaceError,
        "RESOURCE_LIMIT_EXCEEDED": ResourceLimitExceededError,
        "SIZE_LIMIT_EXCEEDED": SizeLimitExceededError,
        "TIMEOUT": TimeoutError,
        "INVALID_PARAMETER": InvalidParameterError,
        "CONCURRENT_MODIFICATION": ConcurrentModificationError,
        "TASK_NOT_FOUND": TaskNotFoundError,
        "TASK_ALREADY_RUNNING": TaskAlreadyRunningError,
        "TASK_FAILED": TaskFailedError,
        "SYSTEM_ERROR": SystemError,
        "BLOCKED_PATH": BlockedPathError,
        "BLOCKED_COMMAND": BlockedCommandError,
        "ENCODING_ERROR": EncodingError,
        "PATCH_APPLY_ERROR": PatchApplyError,
        "SYMLINK_ERROR": SymlinkError,
        "MAX_CONCURRENT_TASKS": MaxConcurrentTasksError,
    }
    
    error_class = error_map.get(code, MCPError)
    return error_class(**kwargs)
