"""
MCP安全模块
实现路径安全检查、权限验证、资源限制等安全功能
"""

import os
import re
import hashlib
from pathlib import Path
from typing import List, Optional, Set, Tuple
from datetime import datetime, timezone
import asyncio
import structlog

from src.mcp.core.config import MCPConfig, get_config
from src.mcp.core.exceptions import (
    BlockedCommandError,
    BlockedPathError,
    PathOutsideWorkspaceError,
    PermissionDeniedError,
    SymlinkError,
)

logger = structlog.get_logger(__name__)


class SecurityManager:
    """安全管理器"""
    
    def __init__(self, config: Optional[MCPConfig] = None):
        self.config = config or get_config()
        self._workspace_path: Optional[Path] = None
        self._file_locks: dict[str, asyncio.Lock] = {}
        self._lock_manager = asyncio.Lock()
    
    @property
    def workspace_path(self) -> Path:
        """获取工作区路径"""
        if self._workspace_path is None:
            self._workspace_path = Path(self.config.workspace.root_path).resolve()
        return self._workspace_path
    
    def validate_path(self, path: str, follow_symlinks: bool = True) -> Path:
        """
        验证路径安全性
        
        Args:
            path: 要验证的路径
            follow_symlinks: 是否跟随符号链接
            
        Returns:
            验证后的规范化路径
            
        Raises:
            BlockedPathError: 路径被阻止
            PathOutsideWorkspaceError: 路径超出工作区
            SymlinkError: 符号链接指向工作区外
        """
        # 规范化路径
        try:
            target_path = Path(path)
            
            # 如果是相对路径，基于工作区解析
            if not target_path.is_absolute():
                target_path = self.workspace_path / target_path
            
            # 解析符号链接
            if follow_symlinks:
                resolved_path = target_path.resolve()
            else:
                # 不解析符号链接，但仍然规范化路径
                resolved_path = Path(os.path.abspath(target_path))
                
                # 如果是符号链接，检查目标
                if target_path.is_symlink():
                    link_target = target_path.resolve()
                    if not self._is_within_workspace(link_target):
                        raise SymlinkError(
                            path=str(target_path),
                            target=str(link_target)
                        )
            
        except Exception as e:
            if isinstance(e, (BlockedPathError, PathOutsideWorkspaceError, SymlinkError)):
                raise
            logger.error("路径解析失败", path=path, error=str(e))
            raise PathOutsideWorkspaceError(path=path, workspace=str(self.workspace_path))
        
        # 检查是否在工作区内
        if not self._is_within_workspace(resolved_path):
            raise PathOutsideWorkspaceError(
                path=str(resolved_path),
                workspace=str(self.workspace_path)
            )
        
        # 检查是否是被阻止的系统路径
        if self._is_blocked_path(resolved_path):
            raise BlockedPathError(path=str(resolved_path))
        
        return resolved_path
    
    def _is_within_workspace(self, path: Path) -> bool:
        """检查路径是否在工作区内"""
        try:
            path.relative_to(self.workspace_path)
            return True
        except ValueError:
            return False
    
    def _is_blocked_path(self, path: Path) -> bool:
        """检查路径是否被阻止"""
        path_str = str(path).lower()
        for blocked in self.config.security.blocked_paths:
            if path_str.startswith(blocked.lower()):
                return True
        return False
    
    def validate_command(self, command: str) -> None:
        """
        验证命令安全性
        
        Args:
            command: 要验证的命令
            
        Raises:
            BlockedCommandError: 命令被阻止
        """
        command_lower = command.lower()
        
        for blocked in self.config.security.blocked_commands:
            if blocked.lower() in command_lower:
                logger.warning("阻止危险命令", command=command[:100])
                raise BlockedCommandError(command=command)
        
        # 增强的危险模式检测
        dangerous_patterns = [
            # 命令链接和管道到危险命令
            r'&&\s*(?:del|rd|rmdir|format|diskpart|rm|dd|mkfs)',
            r'\|\s*(?:del|rd|rm|dd|format)',
            r';\s*(?:del|rd|rm|dd|format|mkfs)',
            
            # 设备重定向
            r'>\s*(?:con|nul|prn|aux|com\d|lpt\d|/dev/)',
            
            # Shell 元字符（可能用于注入）
            r'`[^`]+`',  # 反引号命令替换
            r'\$\([^)]+\)',  # $() 命令替换
            r'\$\{[^}]+\}',  # ${} 变量扩展
            
            # 危险的递归删除
            r'\bdel\s+/[sfq]',
            r'\brd\s+/[sq]',
            r'\brm\s+-[rf]+',
            r'\brm\s+--no-preserve-root',
            
            # 格式化和磁盘操作
            r'\bformat\s+[a-z]:',
            r'\bdiskpart',
            r'\bdd\s+.*of=',
            r'\bmkfs\.',
            
            # 网络相关危险命令
            r'\bnc\s+-[el]',  # netcat 监听
            r'\bcurl\s+.*\|\s*(?:bash|sh|powershell)',  # 管道执行
            r'\bwget\s+.*\|\s*(?:bash|sh)',
            
            # PowerShell 危险操作
            r'invoke-expression',
            r'\biex\s*\(',
            r'-encodedcommand',
            r'downloadstring',
            
            # 注册表危险操作
            r'\breg\s+delete',
            r'\breg\s+add\s+.*\/f',
            
            # 环境变量展开（可能危险）
            r'%[a-zA-Z]+%',
        ]
        
        for pattern in dangerous_patterns:
            if re.search(pattern, command_lower, re.IGNORECASE):
                logger.warning("检测到可疑命令模式", command=command[:100], pattern=pattern)
                raise BlockedCommandError(command=command)
    
    def check_file_permission(self, path: Path, operation: str = "read") -> None:
        """
        检查文件权限
        
        Args:
            path: 文件路径
            operation: 操作类型 (read, write, execute)
            
        Raises:
            PermissionDeniedError: 权限不足
        """
        try:
            if not path.exists():
                return  # 文件不存在时由其他检查处理
            
            if operation == "read":
                if not os.access(path, os.R_OK):
                    raise PermissionDeniedError(path=str(path), operation="读取")
            
            elif operation == "write":
                if path.exists():
                    if not os.access(path, os.W_OK):
                        raise PermissionDeniedError(path=str(path), operation="写入")
                else:
                    # 检查父目录写权限
                    parent = path.parent
                    if parent.exists() and not os.access(parent, os.W_OK):
                        raise PermissionDeniedError(path=str(path), operation="创建")
            
            elif operation == "execute":
                if not os.access(path, os.X_OK):
                    raise PermissionDeniedError(path=str(path), operation="执行")
            
            elif operation == "delete":
                if not os.access(path.parent, os.W_OK):
                    raise PermissionDeniedError(path=str(path), operation="删除")
        
        except PermissionDeniedError:
            raise
        except Exception as e:
            logger.error("权限检查失败", path=str(path), operation=operation, error=str(e))
            raise PermissionDeniedError(path=str(path), operation=operation)
    
    async def acquire_file_lock(self, path: str) -> asyncio.Lock:
        """
        获取文件锁
        
        Args:
            path: 文件路径
            
        Returns:
            文件锁对象
        """
        async with self._lock_manager:
            normalized_path = str(Path(path).resolve())
            if normalized_path not in self._file_locks:
                self._file_locks[normalized_path] = asyncio.Lock()
            return self._file_locks[normalized_path]
    
    async def release_file_lock(self, path: str) -> None:
        """释放文件锁（清理不再使用的锁）"""
        async with self._lock_manager:
            normalized_path = str(Path(path).resolve())
            if normalized_path in self._file_locks:
                lock = self._file_locks[normalized_path]
                if not lock.locked():
                    del self._file_locks[normalized_path]
    
    def compute_checksum(self, content: bytes, algorithm: str = "md5") -> str:
        """
        计算内容校验和
        
        Args:
            content: 文件内容
            algorithm: 哈希算法 (md5, sha1, sha256)
            
        Returns:
            十六进制校验和字符串
        """
        if algorithm == "md5":
            return hashlib.md5(content).hexdigest()
        elif algorithm == "sha1":
            return hashlib.sha1(content).hexdigest()
        elif algorithm == "sha256":
            return hashlib.sha256(content).hexdigest()
        else:
            raise ValueError(f"不支持的哈希算法: {algorithm}")
    
    def sanitize_filename(self, filename: str) -> str:
        """
        清理文件名，移除危险字符
        
        Args:
            filename: 原始文件名
            
        Returns:
            安全的文件名
        """
        # Windows禁止的字符
        forbidden_chars = r'<>:"/\|?*'
        # 控制字符
        forbidden_chars += ''.join(chr(i) for i in range(32))
        
        result = filename
        for char in forbidden_chars:
            result = result.replace(char, '_')
        
        # 移除末尾的点和空格
        result = result.rstrip('. ')
        
        # 检查保留名称
        reserved_names = [
            'CON', 'PRN', 'AUX', 'NUL',
            'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
            'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9',
        ]
        
        name_without_ext = Path(result).stem.upper()
        if name_without_ext in reserved_names:
            result = f"_{result}"
        
        return result or "unnamed"
    
    def is_extension_allowed(self, path: Path) -> bool:
        """检查文件扩展名是否允许"""
        if not self.config.workspace.allowed_extensions:
            return True  # 未配置则允许所有
        
        ext = path.suffix.lower()
        return ext in [e.lower() for e in self.config.workspace.allowed_extensions]
    
    def validate_encoding(self, encoding: str) -> str:
        """验证编码格式"""
        allowed_encodings = [
            'utf-8', 'utf-16', 'utf-16-le', 'utf-16-be',
            'gbk', 'gb2312', 'gb18030', 'big5',
            'ascii', 'latin-1', 'iso-8859-1',
            'shift-jis', 'euc-jp', 'euc-kr',
        ]
        
        encoding_lower = encoding.lower().replace('_', '-')
        
        if encoding_lower not in allowed_encodings:
            # 尝试Python编码名称规范化
            try:
                import codecs
                codecs.lookup(encoding)
                return encoding
            except LookupError:
                raise ValueError(f"不支持的编码格式: {encoding}")
        
        return encoding
    
    def log_audit(
        self, 
        operation: str, 
        path: Optional[str] = None,
        details: Optional[dict] = None,
        success: bool = True
    ) -> None:
        """
        记录审计日志
        
        Args:
            operation: 操作类型
            path: 相关路径
            details: 额外详情
            success: 是否成功
        """
        if not self.config.logging.audit_enabled:
            return
        
        log_data = {
            "operation": operation,
            "success": success,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        if path:
            log_data["path"] = path
        
        if details:
            log_data.update(details)
        
        if success:
            logger.info("审计日志", **log_data)
        else:
            logger.warning("审计日志(失败)", **log_data)


class RateLimiter:
    """速率限制器"""
    
    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: List[float] = []
        self._lock = asyncio.Lock()
    
    async def acquire(self) -> bool:
        """尝试获取请求配额"""
        async with self._lock:
            now = datetime.now(timezone.utc).timestamp()
            
            # 清理过期请求
            self.requests = [
                t for t in self.requests 
                if now - t < self.window_seconds
            ]
            
            if len(self.requests) >= self.max_requests:
                return False
            
            self.requests.append(now)
            return True
    
    async def wait_and_acquire(self) -> None:
        """等待直到可以获取配额"""
        while not await self.acquire():
            await asyncio.sleep(0.1)


class ResourceTracker:
    """资源跟踪器"""
    
    def __init__(self, config: Optional[MCPConfig] = None):
        self.config = config or get_config()
        self._active_tasks: Set[str] = set()
        self._memory_usage: int = 0
        self._lock = asyncio.Lock()
    
    async def register_task(self, task_id: str) -> None:
        """注册新任务"""
        async with self._lock:
            if len(self._active_tasks) >= self.config.performance.max_concurrent_tasks:
                from src.mcp.core.exceptions import MaxConcurrentTasksError
                raise MaxConcurrentTasksError(
                    max_tasks=self.config.performance.max_concurrent_tasks
                )
            self._active_tasks.add(task_id)
    
    async def unregister_task(self, task_id: str) -> None:
        """注销任务"""
        async with self._lock:
            self._active_tasks.discard(task_id)
    
    @property
    def active_task_count(self) -> int:
        """活动任务数"""
        return len(self._active_tasks)
    
    async def track_memory(self, size: int) -> None:
        """跟踪内存使用"""
        async with self._lock:
            max_bytes = self.config.performance.max_memory_mb * 1024 * 1024
            if self._memory_usage + size > max_bytes:
                from src.mcp.core.exceptions import ResourceLimitExceededError
                raise ResourceLimitExceededError(
                    resource_type="内存",
                    current_value=f"{(self._memory_usage + size) / (1024*1024):.2f}MB",
                    limit_value=f"{self.config.performance.max_memory_mb}MB"
                )
            self._memory_usage += size
    
    async def release_memory(self, size: int) -> None:
        """释放内存跟踪"""
        async with self._lock:
            self._memory_usage = max(0, self._memory_usage - size)
