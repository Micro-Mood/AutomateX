"""
执行模块处理器
实现任务管理、进程控制、输入输出接口
"""

import os
import sys
import signal
import asyncio
import subprocess
import uuid
import locale
import base64
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
import psutil
import structlog

from src.mcp.core.config import MCPConfig, get_config
from src.mcp.core.security import SecurityManager, ResourceTracker
from src.mcp.core.cache import CacheManager, get_cache_manager
from src.mcp.core.exceptions import (
    MCPError,
    TaskNotFoundError,
    TaskAlreadyRunningError,
    TaskFailedError,
    MaxConcurrentTasksError,
    BlockedCommandError,
    InvalidParameterError,
    TimeoutError as MCPTimeoutError,
)

logger = structlog.get_logger(__name__)

# 命令输出大小限制（防止内存耗尽）
MAX_OUTPUT_SIZE_BYTES = 10 * 1024 * 1024  # 10MB
MAX_RAW_BYTES_SIZE = 10 * 1024 * 1024  # 10MB


# Windows 控制台默认编码检测
def get_console_encoding() -> str:
    """
    获取 Windows 控制台的默认编码
    
    Windows 中文环境下通常是 cp936 (GBK)
    其他语言环境可能是 cp1252 等
    """
    if sys.platform == 'win32':
        try:
            import ctypes
            # 获取控制台代码页
            cp = ctypes.windll.kernel32.GetConsoleOutputCP()
            return f'cp{cp}'
        except Exception:
            pass
        # 回退到系统默认编码
        return locale.getpreferredencoding(False) or 'cp936'
    return 'utf-8'


def decode_output(data: bytes) -> Tuple[str, str]:
    """
    智能解码命令输出
    
    Args:
        data: 原始字节数据
    
    Returns:
        (decoded_text, raw_base64): 解码后的文本和原始 base64 编码
    """
    if not data:
        return '', ''
    
    raw_b64 = base64.b64encode(data).decode('ascii')
    
    # Windows 优先尝试控制台编码
    if sys.platform == 'win32':
        console_encoding = get_console_encoding()
        try:
            decoded = data.decode(console_encoding)
            return decoded, raw_b64
        except (UnicodeDecodeError, LookupError):
            pass
        
        # 尝试 GBK
        try:
            decoded = data.decode('gbk')
            return decoded, raw_b64
        except UnicodeDecodeError:
            pass
    
    # 尝试 UTF-8
    try:
        decoded = data.decode('utf-8')
        return decoded, raw_b64
    except UnicodeDecodeError:
        pass
    
    # 最后使用替换模式
    return data.decode('utf-8', errors='replace'), raw_b64


class TaskState(Enum):
    """任务状态枚举"""
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"
    KILLED = "killed"


class TaskPriority(Enum):
    """任务优先级枚举"""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    REALTIME = "realtime"


@dataclass
class TaskSpec:
    """任务规范"""
    command: str
    args: List[str] = field(default_factory=list)
    cwd: Optional[str] = None
    env: Optional[Dict[str, str]] = None
    shell: bool = True
    timeout: Optional[int] = None
    stdin: Optional[str] = None
    detached: bool = False
    priority: TaskPriority = TaskPriority.NORMAL


@dataclass
class Task:
    """任务实例"""
    task_id: str
    spec: TaskSpec
    state: TaskState = TaskState.CREATED
    process: Optional[asyncio.subprocess.Process] = None
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    signal: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    stdout_buffer: str = ""
    stderr_buffer: str = ""
    combined_output: str = ""
    # 原始字节的 base64 编码，用于前端备用解码
    stdout_raw_b64: str = ""
    stderr_raw_b64: str = ""
    attached_streams: Set[str] = field(default_factory=set)
    
    @property
    def is_active(self) -> bool:
        """任务是否活跃"""
        return self.state in (TaskState.CREATED, TaskState.RUNNING)
    
    @property
    def duration_ms(self) -> Optional[float]:
        """任务持续时间(毫秒)"""
        if self.started_at is None:
            return None
        
        end_time = self.completed_at or datetime.now(timezone.utc)
        return (end_time - self.started_at).total_seconds() * 1000
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "task_id": self.task_id,
            "state": self.state.value,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "signal": self.signal,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": self.duration_ms,
            "command": self.spec.command,
            "args": self.spec.args,
            "cwd": self.spec.cwd,
            # 原始字节 base64，用于前端备用解码
            "stdout_raw_b64": self.stdout_raw_b64 if self.stdout_raw_b64 else None,
            "stderr_raw_b64": self.stderr_raw_b64 if self.stderr_raw_b64 else None,
        }


class ExecuteHandler:
    """执行模块处理器"""
    
    def __init__(
        self,
        config: Optional[MCPConfig] = None,
        security: Optional[SecurityManager] = None,
        cache: Optional[CacheManager] = None
    ):
        self.config = config or get_config()
        self.security = security or SecurityManager(self.config)
        self.cache = cache or get_cache_manager()
        self.resource_tracker = ResourceTracker(self.config)
        
        # 任务存储
        self._tasks: Dict[str, Task] = {}
        self._lock = asyncio.Lock()
        
        # 输出读取任务
        self._output_readers: Dict[str, asyncio.Task] = {}
    
    def _generate_task_id(self) -> str:
        """生成唯一任务ID"""
        return f"task_{uuid.uuid4().hex[:12]}"
    
    async def create_task(
        self,
        command: str,
        args: Optional[List[str]] = None,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        shell: bool = True,
        timeout: Optional[int] = None,
        stdin: Optional[str] = None,
        detached: bool = False,
        priority: str = "normal"
    ) -> Dict[str, Any]:
        """
        创建任务
        
        Args:
            command: 要执行的命令
            args: 命令行参数
            cwd: 工作目录
            env: 环境变量
            shell: 是否在shell中执行
            timeout: 超时时间(毫秒)
            stdin: 标准输入初始内容
            detached: 是否分离模式
            priority: 进程优先级
            
        Returns:
            任务创建结果
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # 验证命令安全性
            full_command = command
            if args:
                full_command = f"{command} {' '.join(args)}"
            
            self.security.validate_command(full_command)
            
            # 验证工作目录
            if cwd:
                validated_cwd = self.security.validate_path(cwd)
                if not validated_cwd.is_dir():
                    raise InvalidParameterError(
                        parameter="cwd",
                        value=cwd,
                        reason="工作目录不存在或不是目录"
                    )
                cwd = str(validated_cwd)
            else:
                cwd = str(self.security.workspace_path)
            
            # 解析优先级
            try:
                task_priority = TaskPriority(priority.lower())
            except ValueError:
                task_priority = TaskPriority.NORMAL
            
            # 创建任务规范
            spec = TaskSpec(
                command=command,
                args=args or [],
                cwd=cwd,
                env=env,
                shell=shell,
                timeout=timeout,
                stdin=stdin,
                detached=detached,
                priority=task_priority,
            )
            
            # 生成任务ID
            task_id = self._generate_task_id()
            
            # 创建任务实例
            task = Task(
                task_id=task_id,
                spec=spec,
                state=TaskState.CREATED,
                created_at=start_time,
            )
            
            # 存储任务
            async with self._lock:
                self._tasks[task_id] = task
            
            # 审计日志
            self.security.log_audit(
                "create_task",
                details={"task_id": task_id, "command": command}
            )
            
            return {
                "status": "success",
                "data": {
                    "task_id": task_id,
                    "spec": {
                        "command": command,
                        "args": args or [],
                        "cwd": cwd,
                        "created_at": start_time.isoformat(),
                    }
                },
                "metadata": {
                    "operation": "create_task",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": 0,
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("创建任务失败", command=command, error=str(e))
            raise MCPError(f"创建任务失败: {str(e)}", cause=e)
    
    async def start_task(self, task_id: str) -> Dict[str, Any]:
        """
        启动任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            任务启动结果
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            async with self._lock:
                # 检查任务是否存在
                task = self._tasks.get(task_id)
                if not task:
                    raise TaskNotFoundError(task_id=task_id)
                
                # 检查任务状态
                if task.state == TaskState.RUNNING:
                    raise TaskAlreadyRunningError(task_id=task_id)
                
                if task.state not in (TaskState.CREATED,):
                    raise InvalidParameterError(
                        parameter="task_id",
                        value=task_id,
                        reason=f"任务状态 {task.state.value} 不允许启动"
                    )
                
                # 检查并发任务数
                await self.resource_tracker.register_task(task_id)
            
            try:
                # 构建命令
                if task.spec.shell:
                    if task.spec.args:
                        cmd = f"{task.spec.command} {' '.join(task.spec.args)}"
                    else:
                        cmd = task.spec.command
                    
                    # Windows使用cmd.exe
                    process = await asyncio.create_subprocess_shell(
                        cmd,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=task.spec.cwd,
                        env={**os.environ, **(task.spec.env or {})},
                    )
                else:
                    args = [task.spec.command] + task.spec.args
                    process = await asyncio.create_subprocess_exec(
                        *args,
                        stdin=asyncio.subprocess.PIPE,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=task.spec.cwd,
                        env={**os.environ, **(task.spec.env or {})},
                    )
                
                # 更新任务状态
                async with self._lock:
                    task.process = process
                    task.pid = process.pid
                    task.state = TaskState.RUNNING
                    task.started_at = datetime.now(timezone.utc)
                
                # 写入初始stdin
                if task.spec.stdin:
                    process.stdin.write(task.spec.stdin.encode('utf-8'))
                    await process.stdin.drain()
                
                # 启动输出读取任务
                self._start_output_readers(task)
                
                # 如果设置了超时，启动超时监控
                if task.spec.timeout:
                    asyncio.create_task(self._timeout_monitor(task))
                
                # 审计日志
                self.security.log_audit(
                    "start_task",
                    details={"task_id": task_id, "pid": process.pid}
                )
                
                return {
                    "status": "success",
                    "data": {
                        "task_id": task_id,
                        "pid": process.pid,
                        "state": TaskState.RUNNING.value,
                        "started_at": task.started_at.isoformat(),
                    },
                    "metadata": {
                        "operation": "start_task",
                        "timestamp": start_time.isoformat(),
                        "duration_ms": (datetime.now(timezone.utc) - start_time).total_seconds() * 1000,
                    }
                }
                
            except Exception as e:
                await self.resource_tracker.unregister_task(task_id)
                raise
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("启动任务失败", task_id=task_id, error=str(e))
            raise MCPError(f"启动任务失败: {str(e)}", cause=e)
    
    def _start_output_readers(self, task: Task) -> None:
        """启动输出读取协程"""
        # 用于累积原始字节
        stdout_raw_bytes = bytearray()
        stderr_raw_bytes = bytearray()
        # 跟踪是否已达到输出限制
        stdout_truncated = False
        stderr_truncated = False
        
        async def read_stdout():
            nonlocal stdout_raw_bytes, stdout_truncated
            try:
                while task.process and task.state == TaskState.RUNNING:
                    try:
                        data = await asyncio.wait_for(
                            task.process.stdout.read(4096),
                            timeout=0.1
                        )
                        if data:
                            # 检查是否达到输出限制
                            if len(stdout_raw_bytes) >= MAX_RAW_BYTES_SIZE:
                                if not stdout_truncated:
                                    stdout_truncated = True
                                    logger.warning(
                                        "stdout输出已达到大小限制，后续输出将被丢弃",
                                        task_id=task.task_id,
                                        limit_bytes=MAX_RAW_BYTES_SIZE
                                    )
                                    task.stdout_buffer += "\n[输出已截断：超过10MB限制]"
                                continue
                            
                            # 累积原始字节（限制大小）
                            remaining = MAX_RAW_BYTES_SIZE - len(stdout_raw_bytes)
                            stdout_raw_bytes.extend(data[:remaining])
                            
                            # 使用智能解码
                            text, _ = decode_output(data[:remaining])
                            # 限制 buffer 大小
                            if len(task.stdout_buffer) < MAX_OUTPUT_SIZE_BYTES:
                                task.stdout_buffer += text
                                task.combined_output += text
                        elif task.process.returncode is not None:
                            break
                    except asyncio.TimeoutError:
                        continue
                # 任务结束时保存完整的 base64
                if stdout_raw_bytes:
                    task.stdout_raw_b64 = base64.b64encode(bytes(stdout_raw_bytes)).decode('ascii')
            except Exception as e:
                logger.debug("stdout读取异常", task_id=task.task_id, error=str(e))
        
        async def read_stderr():
            nonlocal stderr_raw_bytes, stderr_truncated
            try:
                while task.process and task.state == TaskState.RUNNING:
                    try:
                        data = await asyncio.wait_for(
                            task.process.stderr.read(4096),
                            timeout=0.1
                        )
                        if data:
                            # 检查是否达到输出限制
                            if len(stderr_raw_bytes) >= MAX_RAW_BYTES_SIZE:
                                if not stderr_truncated:
                                    stderr_truncated = True
                                    logger.warning(
                                        "stderr输出已达到大小限制，后续输出将被丢弃",
                                        task_id=task.task_id,
                                        limit_bytes=MAX_RAW_BYTES_SIZE
                                    )
                                    task.stderr_buffer += "\n[输出已截断：超过10MB限制]"
                                continue
                            
                            # 累积原始字节（限制大小）
                            remaining = MAX_RAW_BYTES_SIZE - len(stderr_raw_bytes)
                            stderr_raw_bytes.extend(data[:remaining])
                            
                            # 使用智能解码
                            text, _ = decode_output(data[:remaining])
                            # 限制 buffer 大小
                            if len(task.stderr_buffer) < MAX_OUTPUT_SIZE_BYTES:
                                task.stderr_buffer += text
                                task.combined_output += text
                        elif task.process.returncode is not None:
                            break
                    except asyncio.TimeoutError:
                        continue
                # 任务结束时保存完整的 base64
                if stderr_raw_bytes:
                    task.stderr_raw_b64 = base64.b64encode(bytes(stderr_raw_bytes)).decode('ascii')
            except Exception as e:
                logger.debug("stderr读取异常", task_id=task.task_id, error=str(e))
        
        self._output_readers[f"{task.task_id}_stdout"] = asyncio.create_task(read_stdout())
        self._output_readers[f"{task.task_id}_stderr"] = asyncio.create_task(read_stderr())
    
    async def _timeout_monitor(self, task: Task) -> None:
        """超时监控"""
        if not task.spec.timeout:
            return
        
        await asyncio.sleep(task.spec.timeout / 1000)
        
        if task.state == TaskState.RUNNING:
            logger.warning("任务超时", task_id=task.task_id, timeout_ms=task.spec.timeout)
            await self.kill_task(task.task_id)
    
    async def stop_task(
        self,
        task_id: str,
        signal_name: str = "CTRL_C",
        timeout: int = 5000
    ) -> Dict[str, Any]:
        """
        优雅停止任务
        
        Args:
            task_id: 任务ID
            signal_name: 终止信号 (CTRL_C, CTRL_BREAK)
            timeout: 等待超时(毫秒)
            
        Returns:
            任务停止结果
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            async with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    raise TaskNotFoundError(task_id=task_id)
                
                if task.state != TaskState.RUNNING:
                    raise InvalidParameterError(
                        parameter="task_id",
                        value=task_id,
                        reason=f"任务状态 {task.state.value} 不是运行中"
                    )
            
            # 发送终止信号
            if task.process:
                try:
                    if signal_name == "CTRL_C":
                        # Windows下发送CTRL_C
                        task.process.send_signal(signal.CTRL_C_EVENT)
                    elif signal_name == "CTRL_BREAK":
                        task.process.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        task.process.terminate()
                except Exception:
                    task.process.terminate()
                
                # 等待进程退出
                try:
                    await asyncio.wait_for(
                        task.process.wait(),
                        timeout=timeout / 1000
                    )
                except asyncio.TimeoutError:
                    # 超时则强制终止
                    task.process.kill()
                    await task.process.wait()
            
            # 更新任务状态
            async with self._lock:
                task.state = TaskState.STOPPED
                task.completed_at = datetime.now(timezone.utc)
                task.exit_code = task.process.returncode if task.process else None
                task.signal = signal_name
            
            await self.resource_tracker.unregister_task(task_id)
            
            # 审计日志
            self.security.log_audit(
                "stop_task",
                details={"task_id": task_id, "signal": signal_name}
            )
            
            return {
                "status": "success",
                "data": task.to_dict(),
                "metadata": {
                    "operation": "stop_task",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": (datetime.now(timezone.utc) - start_time).total_seconds() * 1000,
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("停止任务失败", task_id=task_id, error=str(e))
            raise MCPError(f"停止任务失败: {str(e)}", cause=e)
    
    async def kill_task(self, task_id: str) -> Dict[str, Any]:
        """
        强制终止任务
        
        Args:
            task_id: 任务ID
            
        Returns:
            任务终止结果
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            async with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    raise TaskNotFoundError(task_id=task_id)
            
            # 强制终止进程
            if task.process:
                try:
                    task.process.kill()
                    await task.process.wait()
                except Exception:
                    pass
            
            # 更新任务状态
            async with self._lock:
                task.state = TaskState.KILLED
                task.completed_at = datetime.now(timezone.utc)
                task.exit_code = -9
            
            await self.resource_tracker.unregister_task(task_id)
            
            # 审计日志
            self.security.log_audit(
                "kill_task",
                details={"task_id": task_id}
            )
            
            return {
                "status": "success",
                "data": task.to_dict(),
                "metadata": {
                    "operation": "kill_task",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": (datetime.now(timezone.utc) - start_time).total_seconds() * 1000,
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("终止任务失败", task_id=task_id, error=str(e))
            raise MCPError(f"终止任务失败: {str(e)}", cause=e)
    
    async def get_task(self, task_id: str) -> Dict[str, Any]:
        """
        获取任务状态
        
        Args:
            task_id: 任务ID
            
        Returns:
            任务状态信息
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            async with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    raise TaskNotFoundError(task_id=task_id)
            
            # 检查进程状态更新
            if task.process and task.state == TaskState.RUNNING:
                if task.process.returncode is not None:
                    async with self._lock:
                        task.exit_code = task.process.returncode
                        task.state = TaskState.COMPLETED if task.exit_code == 0 else TaskState.FAILED
                        task.completed_at = datetime.now(timezone.utc)
                    
                    await self.resource_tracker.unregister_task(task_id)
            
            # 获取资源使用情况
            cpu_time = None
            memory_usage = None
            
            if task.pid and task.state == TaskState.RUNNING:
                try:
                    proc = psutil.Process(task.pid)
                    cpu_time = proc.cpu_times().user * 1000  # 转换为毫秒
                    memory_usage = proc.memory_info().rss
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            
            result_data = task.to_dict()
            result_data.update({
                "cpu_time": cpu_time,
                "memory_usage": memory_usage,
            })
            
            return {
                "status": "success",
                "data": result_data,
                "metadata": {
                    "operation": "get_task",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": 0,
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("获取任务状态失败", task_id=task_id, error=str(e))
            raise MCPError(f"获取任务状态失败: {str(e)}", cause=e)
    
    async def list_tasks(
        self,
        filter: str = "all",
        limit: int = 50
    ) -> Dict[str, Any]:
        """
        列出所有任务
        
        Args:
            filter: 过滤条件 (all, active, completed, failed)
            limit: 最大返回数
            
        Returns:
            任务列表
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            async with self._lock:
                tasks = list(self._tasks.values())
            
            # 应用过滤
            if filter == "active":
                tasks = [t for t in tasks if t.is_active]
            elif filter == "completed":
                tasks = [t for t in tasks if t.state == TaskState.COMPLETED]
            elif filter == "failed":
                tasks = [t for t in tasks if t.state == TaskState.FAILED]
            
            # 按创建时间倒序
            tasks.sort(key=lambda t: t.created_at, reverse=True)
            
            # 限制数量
            tasks = tasks[:limit]
            
            return {
                "status": "success",
                "data": {
                    "tasks": [t.to_dict() for t in tasks],
                    "total": len(self._tasks),
                    "filtered": len(tasks),
                    "active_count": self.resource_tracker.active_task_count,
                },
                "metadata": {
                    "operation": "list_tasks",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": 0,
                }
            }
            
        except Exception as e:
            logger.error("列出任务失败", error=str(e))
            raise MCPError(f"列出任务失败: {str(e)}", cause=e)
    
    async def write_stdin(
        self,
        task_id: str,
        data: str,
        encoding: str = "utf-8",
        eof: bool = False
    ) -> Dict[str, Any]:
        """
        写入标准输入
        
        Args:
            task_id: 任务ID
            data: 要写入的数据
            encoding: 数据编码
            eof: 是否发送EOF
            
        Returns:
            写入结果
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            async with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    raise TaskNotFoundError(task_id=task_id)
                
                if task.state != TaskState.RUNNING:
                    raise InvalidParameterError(
                        parameter="task_id",
                        value=task_id,
                        reason="任务不在运行状态"
                    )
            
            if task.process and task.process.stdin:
                data_bytes = data.encode(encoding)
                task.process.stdin.write(data_bytes)
                await task.process.stdin.drain()
                
                if eof:
                    task.process.stdin.close()
            
            return {
                "status": "success",
                "data": {
                    "task_id": task_id,
                    "bytes_written": len(data.encode(encoding)),
                    "eof_sent": eof,
                },
                "metadata": {
                    "operation": "write_stdin",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": 0,
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("写入stdin失败", task_id=task_id, error=str(e))
            raise MCPError(f"写入stdin失败: {str(e)}", cause=e)
    
    async def stream_stdout(
        self,
        task_id: str,
        max_bytes: int = 8192,
        timeout: int = 1000,
        encoding: str = "utf-8"
    ) -> Dict[str, Any]:
        """
        流式读取标准输出
        
        Args:
            task_id: 任务ID
            max_bytes: 最大读取字节数
            timeout: 读取超时(毫秒)
            encoding: 输出编码
            
        Returns:
            输出内容
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            async with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    raise TaskNotFoundError(task_id=task_id)
            
            # 从缓冲区读取
            output = task.stdout_buffer[:max_bytes]
            task.stdout_buffer = task.stdout_buffer[len(output):]
            
            # 检查是否EOF
            eof = (
                task.state != TaskState.RUNNING and 
                len(task.stdout_buffer) == 0
            )
            
            return {
                "status": "success",
                "data": {
                    "task_id": task_id,
                    "output": output,
                    "bytes_read": len(output.encode(encoding)),
                    "eof": eof,
                },
                "metadata": {
                    "operation": "stream_stdout",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": 0,
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("读取stdout失败", task_id=task_id, error=str(e))
            raise MCPError(f"读取stdout失败: {str(e)}", cause=e)
    
    async def stream_stderr(
        self,
        task_id: str,
        max_bytes: int = 8192,
        timeout: int = 1000,
        encoding: str = "utf-8"
    ) -> Dict[str, Any]:
        """
        流式读取标准错误
        
        Args:
            task_id: 任务ID
            max_bytes: 最大读取字节数
            timeout: 读取超时(毫秒)
            encoding: 输出编码
            
        Returns:
            错误输出内容
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            async with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    raise TaskNotFoundError(task_id=task_id)
            
            # 从缓冲区读取
            output = task.stderr_buffer[:max_bytes]
            task.stderr_buffer = task.stderr_buffer[len(output):]
            
            # 检查是否EOF
            eof = (
                task.state != TaskState.RUNNING and 
                len(task.stderr_buffer) == 0
            )
            
            return {
                "status": "success",
                "data": {
                    "task_id": task_id,
                    "output": output,
                    "bytes_read": len(output.encode(encoding)),
                    "eof": eof,
                },
                "metadata": {
                    "operation": "stream_stderr",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": 0,
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("读取stderr失败", task_id=task_id, error=str(e))
            raise MCPError(f"读取stderr失败: {str(e)}", cause=e)
    
    async def wait_task(
        self,
        task_id: str,
        timeout: int = 30000
    ) -> Dict[str, Any]:
        """
        等待任务完成
        
        Args:
            task_id: 任务ID
            timeout: 等待超时(毫秒)，默认30秒，0表示无限等待
            
        Returns:
            任务最终结果
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            async with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    raise TaskNotFoundError(task_id=task_id)
            
            # 等待进程完成
            if task.process and task.state == TaskState.RUNNING:
                try:
                    if timeout > 0:
                        await asyncio.wait_for(
                            task.process.wait(),
                            timeout=timeout / 1000
                        )
                    else:
                        await task.process.wait()
                except asyncio.TimeoutError:
                    raise MCPTimeoutError(operation="wait_task", timeout_ms=timeout)
            
            # 更新任务状态
            async with self._lock:
                if task.process:
                    task.exit_code = task.process.returncode
                    task.state = TaskState.COMPLETED if task.exit_code == 0 else TaskState.FAILED
                    task.completed_at = datetime.now(timezone.utc)
            
            await self.resource_tracker.unregister_task(task_id)
            
            result_data = task.to_dict()
            result_data.update({
                "stdout": task.stdout_buffer,
                "stderr": task.stderr_buffer,
                "combined_output": task.combined_output,
            })
            
            return {
                "status": "success",
                "data": result_data,
                "metadata": {
                    "operation": "wait_task",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": (datetime.now(timezone.utc) - start_time).total_seconds() * 1000,
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("等待任务失败", task_id=task_id, error=str(e))
            raise MCPError(f"等待任务失败: {str(e)}", cause=e)
    
    async def attach_task(
        self,
        task_id: str,
        stream_stdout: bool = True,
        stream_stderr: bool = True,
        buffer_size: int = 4096
    ) -> Dict[str, Any]:
        """
        附加到任务
        
        Args:
            task_id: 任务ID
            stream_stdout: 是否流式接收stdout
            stream_stderr: 是否流式接收stderr
            buffer_size: 缓冲区大小
            
        Returns:
            附加结果
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            async with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    raise TaskNotFoundError(task_id=task_id)
                
                if stream_stdout:
                    task.attached_streams.add("stdout")
                if stream_stderr:
                    task.attached_streams.add("stderr")
            
            return {
                "status": "success",
                "data": {
                    "task_id": task_id,
                    "attached": True,
                    "streams": list(task.attached_streams),
                },
                "metadata": {
                    "operation": "attach_task",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": 0,
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("附加任务失败", task_id=task_id, error=str(e))
            raise MCPError(f"附加任务失败: {str(e)}", cause=e)
    
    async def detach_task(self, task_id: str) -> Dict[str, Any]:
        """
        从任务分离
        
        Args:
            task_id: 任务ID
            
        Returns:
            分离结果
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            async with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    raise TaskNotFoundError(task_id=task_id)
                
                task.attached_streams.clear()
            
            return {
                "status": "success",
                "data": {
                    "task_id": task_id,
                    "detached": True,
                },
                "metadata": {
                    "operation": "detach_task",
                    "timestamp": start_time.isoformat(),
                    "duration_ms": 0,
                }
            }
            
        except MCPError:
            raise
        except Exception as e:
            logger.error("分离任务失败", task_id=task_id, error=str(e))
            raise MCPError(f"分离任务失败: {str(e)}", cause=e)
    
    async def cleanup(self) -> None:
        """清理所有任务"""
        async with self._lock:
            for task_id, task in list(self._tasks.items()):
                if task.state == TaskState.RUNNING and task.process:
                    try:
                        task.process.kill()
                        await task.process.wait()
                    except Exception:
                        pass
                
                await self.resource_tracker.unregister_task(task_id)
            
            self._tasks.clear()
        
        # 取消所有输出读取任务
        for reader_task in self._output_readers.values():
            reader_task.cancel()
        
        self._output_readers.clear()


# 模块级便捷函数
_handler: Optional[ExecuteHandler] = None


def get_handler() -> ExecuteHandler:
    """获取处理器实例"""
    global _handler
    if _handler is None:
        _handler = ExecuteHandler()
    return _handler


def reset_handler() -> None:
    """重置处理器实例（用于运行时配置更新）"""
    global _handler
    _handler = None


# 任务生命周期
async def create_task(
    command: str,
    args: Optional[List[str]] = None,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    shell: bool = True,
    timeout: Optional[int] = None,
    stdin: Optional[str] = None,
    detached: bool = False,
    priority: str = "normal"
) -> Dict[str, Any]:
    return await get_handler().create_task(
        command, args, cwd, env, shell, timeout, stdin, detached, priority
    )


async def start_task(task_id: str) -> Dict[str, Any]:
    return await get_handler().start_task(task_id)


async def stop_task(task_id: str, signal_name: str = "CTRL_C", timeout: int = 5000) -> Dict[str, Any]:
    return await get_handler().stop_task(task_id, signal_name, timeout)


async def kill_task(task_id: str) -> Dict[str, Any]:
    return await get_handler().kill_task(task_id)


async def get_task(task_id: str) -> Dict[str, Any]:
    return await get_handler().get_task(task_id)


async def list_tasks(filter: str = "all", limit: int = 50) -> Dict[str, Any]:
    return await get_handler().list_tasks(filter, limit)


# 输入输出
async def write_stdin(task_id: str, data: str, encoding: str = "utf-8", eof: bool = False) -> Dict[str, Any]:
    return await get_handler().write_stdin(task_id, data, encoding, eof)


async def stream_stdout(task_id: str, max_bytes: int = 8192, timeout: int = 1000, encoding: str = "utf-8") -> Dict[str, Any]:
    return await get_handler().stream_stdout(task_id, max_bytes, timeout, encoding)


async def stream_stderr(task_id: str, max_bytes: int = 8192, timeout: int = 1000, encoding: str = "utf-8") -> Dict[str, Any]:
    return await get_handler().stream_stderr(task_id, max_bytes, timeout, encoding)


# 任务控制
async def wait_task(task_id: str, timeout: int = 30000) -> Dict[str, Any]:
    return await get_handler().wait_task(task_id, timeout)


async def attach_task(task_id: str, stream_stdout: bool = True, stream_stderr: bool = True, buffer_size: int = 4096) -> Dict[str, Any]:
    return await get_handler().attach_task(task_id, stream_stdout, stream_stderr, buffer_size)


async def detach_task(task_id: str) -> Dict[str, Any]:
    return await get_handler().detach_task(task_id)


# 便捷函数：同步执行命令
async def run_command(
    command: str,
    cwd: Optional[str] = None,
    timeout: int = 30000,
    env: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    同步执行命令并等待完成（便捷函数）
    
    组合 create_task + start_task + wait_task 为单一调用。
    
    Args:
        command: 要执行的命令
        cwd: 工作目录
        timeout: 超时时间（毫秒），默认 30 秒
        env: 环境变量
        
    Returns:
        包含 stdout、stderr、exit_code 的响应
    """
    # 创建任务
    create_result = await create_task(
        command=command,
        cwd=cwd,
        timeout=timeout,
        env=env,
        shell=True
    )
    
    if create_result.get("status") != "success":
        return create_result
    
    task_id = create_result["data"]["task_id"]
    
    try:
        # 启动任务
        start_result = await start_task(task_id)
        if start_result.get("status") != "success":
            return start_result
        
        # 等待任务完成
        wait_result = await wait_task(task_id, timeout)
        return wait_result
        
    except Exception as e:
        # 发生异常时尝试终止任务
        try:
            await kill_task(task_id)
        except:
            pass
        return {
            "status": "error",
            "error": {
                "code": "COMMAND_EXECUTION_ERROR",
                "message": str(e)
            }
        }
