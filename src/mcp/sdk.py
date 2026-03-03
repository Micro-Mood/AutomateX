"""
MCP Python客户端SDK
提供便捷的Python API来调用MCP服务
"""

import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass
import httpx
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class MCPResponse:
    """MCP响应对象"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    
    @classmethod
    def from_dict(cls, response: Dict[str, Any]) -> "MCPResponse":
        if "result" in response:
            result = response["result"]
            return cls(
                success=result.get("status") == "success",
                data=result.get("data"),
                error=result.get("error")
            )
        elif "error" in response:
            return cls(
                success=False,
                error=response["error"]
            )
        else:
            return cls(success=False, error={"message": "无效的响应格式"})


class MCPClient:
    """
    MCP客户端
    
    使用示例:
    ```python
    async with MCPClient("localhost", 8080) as client:
        # 读取文件
        result = await client.read_file("path/to/file.txt")
        print(result.data["content"])
        
        # 搜索文件
        result = await client.search_files("*.py", ".")
        for file in result.data["results"]:
            print(file["name"])
        
        # 执行命令
        result = await client.run_command("dir")
        print(result.data["stdout"])
    ```
    """
    
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        timeout: float = 30.0
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._request_id = 0
        self._lock = asyncio.Lock()
    
    async def connect(self) -> None:
        """连接到MCP服务器"""
        self._reader, self._writer = await asyncio.open_connection(
            self.host, self.port
        )
        logger.info("已连接到MCP服务器", host=self.host, port=self.port)
    
    async def disconnect(self) -> None:
        """断开连接"""
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
            self._writer = None
            self._reader = None
        logger.info("已断开MCP服务器连接")
    
    async def __aenter__(self) -> "MCPClient":
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
    
    async def _call(self, method: str, params: Optional[Dict[str, Any]] = None) -> MCPResponse:
        """调用RPC方法"""
        if not self._writer or not self._reader:
            raise RuntimeError("未连接到服务器")
        
        async with self._lock:
            self._request_id += 1
            request = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
                "id": self._request_id
            }
            
            # 发送请求
            request_data = json.dumps(request, ensure_ascii=False) + "\n"
            self._writer.write(request_data.encode('utf-8'))
            await self._writer.drain()
            
            # 读取响应
            response_data = await asyncio.wait_for(
                self._reader.readline(),
                timeout=self.timeout
            )
            
            response = json.loads(response_data.decode('utf-8'))
            return MCPResponse.from_dict(response)
    
    # ==================== 读取模块 ====================
    
    async def read_file(
        self,
        path: str,
        encoding: str = "utf-8",
        range: Optional[Tuple[int, int]] = None,
        max_size: int = 1048576
    ) -> MCPResponse:
        """读取文件内容"""
        params = {
            "path": path,
            "encoding": encoding,
            "max_size": max_size
        }
        if range:
            params["range"] = list(range)
        
        return await self._call("read_file", params)
    
    async def list_directory(
        self,
        path: str,
        limit: int = 100,
        offset: int = 0,
        pattern: Optional[str] = None,
        recursive: bool = False,
        include_hidden: bool = False
    ) -> MCPResponse:
        """列出目录内容"""
        return await self._call("list_directory", {
            "path": path,
            "limit": limit,
            "offset": offset,
            "pattern": pattern,
            "recursive": recursive,
            "include_hidden": include_hidden
        })
    
    async def stat_path(self, path: str, follow_symlinks: bool = True) -> MCPResponse:
        """获取路径状态"""
        return await self._call("stat_path", {
            "path": path,
            "follow_symlinks": follow_symlinks
        })
    
    async def exists(self, path: str) -> MCPResponse:
        """检查路径是否存在"""
        return await self._call("exists", {"path": path})
    
    # ==================== 搜索模块 ====================
    
    async def search_files(
        self,
        pattern: str,
        root_dir: str,
        max_results: int = 100,
        recursive: bool = True,
        file_types: Optional[List[str]] = None
    ) -> MCPResponse:
        """搜索文件"""
        return await self._call("search_files", {
            "pattern": pattern,
            "root_dir": root_dir,
            "max_results": max_results,
            "recursive": recursive,
            "file_types": file_types
        })
    
    async def search_content(
        self,
        query: str,
        root_dir: str,
        file_pattern: Optional[str] = None,
        max_files: int = 50,
        case_sensitive: bool = False,
        is_regex: bool = False
    ) -> MCPResponse:
        """搜索文件内容"""
        return await self._call("search_content", {
            "query": query,
            "root_dir": root_dir,
            "file_pattern": file_pattern,
            "max_files": max_files,
            "case_sensitive": case_sensitive,
            "is_regex": is_regex
        })
    
    async def search_symbol(
        self,
        symbol: str,
        root_dir: str,
        symbol_type: Optional[str] = None,
        language: Optional[str] = None
    ) -> MCPResponse:
        """搜索代码符号"""
        return await self._call("search_symbol", {
            "symbol": symbol,
            "root_dir": root_dir,
            "symbol_type": symbol_type,
            "language": language
        })
    
    # ==================== 编辑模块 ====================
    
    async def create_directory(
        self,
        path: str,
        recursive: bool = True
    ) -> MCPResponse:
        """创建目录"""
        return await self._call("create_directory", {
            "path": path,
            "recursive": recursive
        })
    
    async def delete_directory(
        self,
        path: str,
        recursive: bool = False,
        force: bool = False
    ) -> MCPResponse:
        """删除目录"""
        return await self._call("delete_directory", {
            "path": path,
            "recursive": recursive,
            "force": force
        })
    
    async def move_directory(
        self,
        source: str,
        destination: str,
        overwrite: bool = False
    ) -> MCPResponse:
        """移动目录"""
        return await self._call("move_directory", {
            "source": source,
            "destination": destination,
            "overwrite": overwrite
        })
    
    async def create_file(
        self,
        path: str,
        content: str = "",
        encoding: str = "utf-8",
        overwrite: bool = False
    ) -> MCPResponse:
        """创建文件"""
        return await self._call("create_file", {
            "path": path,
            "content": content,
            "encoding": encoding,
            "overwrite": overwrite
        })
    
    async def write_file(
        self,
        path: str,
        content: str,
        encoding: str = "utf-8"
    ) -> MCPResponse:
        """写入文件"""
        return await self._call("write_file", {
            "path": path,
            "content": content,
            "encoding": encoding
        })
    
    async def delete_file(
        self,
        path: str,
        backup: bool = False
    ) -> MCPResponse:
        """删除文件"""
        return await self._call("delete_file", {
            "path": path,
            "backup": backup
        })
    
    async def move_file(
        self,
        source: str,
        destination: str,
        overwrite: bool = False
    ) -> MCPResponse:
        """移动文件"""
        return await self._call("move_file", {
            "source": source,
            "destination": destination,
            "overwrite": overwrite
        })
    
    async def copy_file(
        self,
        source: str,
        destination: str,
        overwrite: bool = False
    ) -> MCPResponse:
        """复制文件"""
        return await self._call("copy_file", {
            "source": source,
            "destination": destination,
            "overwrite": overwrite
        })
    
    async def replace_range(
        self,
        path: str,
        range: Tuple[int, int],
        new_text: str,
        encoding: str = "utf-8"
    ) -> MCPResponse:
        """替换文本范围"""
        return await self._call("replace_range", {
            "path": path,
            "range": list(range),
            "new_text": new_text,
            "encoding": encoding
        })
    
    async def insert_text(
        self,
        path: str,
        position: int,
        text: str,
        encoding: str = "utf-8"
    ) -> MCPResponse:
        """插入文本"""
        return await self._call("insert_text", {
            "path": path,
            "position": position,
            "text": text,
            "encoding": encoding
        })
    
    async def apply_patch(
        self,
        path: str,
        patch: str,
        dry_run: bool = False
    ) -> MCPResponse:
        """应用补丁"""
        return await self._call("apply_patch", {
            "path": path,
            "patch": patch,
            "dry_run": dry_run
        })
    
    # ==================== 执行模块 ====================
    
    async def create_task(
        self,
        command: str,
        args: Optional[List[str]] = None,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        shell: bool = True,
        timeout: Optional[int] = None
    ) -> MCPResponse:
        """创建任务"""
        return await self._call("create_task", {
            "command": command,
            "args": args,
            "cwd": cwd,
            "env": env,
            "shell": shell,
            "timeout": timeout
        })
    
    async def start_task(self, task_id: str) -> MCPResponse:
        """启动任务"""
        return await self._call("start_task", {"task_id": task_id})
    
    async def stop_task(self, task_id: str, timeout: int = 5000) -> MCPResponse:
        """停止任务"""
        return await self._call("stop_task", {"task_id": task_id, "timeout": timeout})
    
    async def kill_task(self, task_id: str) -> MCPResponse:
        """终止任务"""
        return await self._call("kill_task", {"task_id": task_id})
    
    async def get_task(self, task_id: str) -> MCPResponse:
        """获取任务状态"""
        return await self._call("get_task", {"task_id": task_id})
    
    async def list_tasks(self, filter: str = "all") -> MCPResponse:
        """列出任务"""
        return await self._call("list_tasks", {"filter": filter})
    
    async def write_stdin(
        self,
        task_id: str,
        data: str,
        eof: bool = False
    ) -> MCPResponse:
        """写入stdin"""
        return await self._call("write_stdin", {
            "task_id": task_id,
            "data": data,
            "eof": eof
        })
    
    async def stream_stdout(
        self,
        task_id: str,
        max_bytes: int = 8192
    ) -> MCPResponse:
        """读取stdout"""
        return await self._call("stream_stdout", {
            "task_id": task_id,
            "max_bytes": max_bytes
        })
    
    async def stream_stderr(
        self,
        task_id: str,
        max_bytes: int = 8192
    ) -> MCPResponse:
        """读取stderr"""
        return await self._call("stream_stderr", {
            "task_id": task_id,
            "max_bytes": max_bytes
        })
    
    async def wait_task(self, task_id: str, timeout: int = 0) -> MCPResponse:
        """等待任务完成"""
        return await self._call("wait_task", {
            "task_id": task_id,
            "timeout": timeout
        })
    
    # ==================== 便捷方法 ====================
    
    async def run_command(
        self,
        command: str,
        cwd: Optional[str] = None,
        timeout: Optional[int] = None
    ) -> MCPResponse:
        """
        运行命令并等待完成
        
        Args:
            command: 要执行的命令
            cwd: 工作目录
            timeout: 超时时间(毫秒)
            
        Returns:
            包含stdout, stderr, exit_code的响应
        """
        # 创建任务
        create_result = await self.create_task(command, cwd=cwd, timeout=timeout)
        if not create_result.success:
            return create_result
        
        task_id = create_result.data["task_id"]
        
        # 启动任务
        start_result = await self.start_task(task_id)
        if not start_result.success:
            return start_result
        
        # 等待完成
        return await self.wait_task(task_id, timeout or 0)
    
    # ==================== 系统方法 ====================
    
    async def ping(self) -> MCPResponse:
        """健康检查"""
        return await self._call("ping")
    
    async def get_version(self) -> MCPResponse:
        """获取版本"""
        return await self._call("get_version")
    
    async def get_methods(self) -> MCPResponse:
        """获取可用方法"""
        return await self._call("get_methods")

    async def set_workspace(
        self,
        root_path: str,
        persist: bool = True,
        config_path: Optional[str] = None,
        reset_cache: bool = True
    ) -> MCPResponse:
        """运行中设置工作区根目录"""
        params: Dict[str, Any] = {
            "root_path": root_path,
            "persist": persist,
            "reset_cache": reset_cache,
        }
        if config_path:
            params["config_path"] = config_path
        return await self._call("set_workspace", params)
    
    async def get_stats(self) -> MCPResponse:
        """获取统计信息"""
        return await self._call("get_stats")
    
    async def clear_cache(self) -> MCPResponse:
        """清空缓存"""
        return await self._call("clear_cache")


class SyncMCPClient:
    """
    同步MCP客户端
    
    使用示例:
    ```python
    with SyncMCPClient("localhost", 8080) as client:
        result = client.read_file("path/to/file.txt")
        print(result.data["content"])
    ```
    """
    
    def __init__(self, host: str = "127.0.0.1", port: int = 8080, timeout: float = 30.0):
        self._client = MCPClient(host, port, timeout)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
    
    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            try:
                self._loop = asyncio.get_event_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        return self._loop
    
    def connect(self) -> None:
        self._get_loop().run_until_complete(self._client.connect())
    
    def disconnect(self) -> None:
        self._get_loop().run_until_complete(self._client.disconnect())
    
    def __enter__(self) -> "SyncMCPClient":
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()
    
    def _run(self, coro) -> MCPResponse:
        return self._get_loop().run_until_complete(coro)
    
    # 同步包装方法
    def read_file(self, path: str, **kwargs) -> MCPResponse:
        return self._run(self._client.read_file(path, **kwargs))
    
    def list_directory(self, path: str, **kwargs) -> MCPResponse:
        return self._run(self._client.list_directory(path, **kwargs))
    
    def stat_path(self, path: str, **kwargs) -> MCPResponse:
        return self._run(self._client.stat_path(path, **kwargs))
    
    def exists(self, path: str) -> MCPResponse:
        return self._run(self._client.exists(path))
    
    def search_files(self, pattern: str, root_dir: str, **kwargs) -> MCPResponse:
        return self._run(self._client.search_files(pattern, root_dir, **kwargs))
    
    def search_content(self, query: str, root_dir: str, **kwargs) -> MCPResponse:
        return self._run(self._client.search_content(query, root_dir, **kwargs))
    
    def create_file(self, path: str, **kwargs) -> MCPResponse:
        return self._run(self._client.create_file(path, **kwargs))
    
    def write_file(self, path: str, content: str, **kwargs) -> MCPResponse:
        return self._run(self._client.write_file(path, content, **kwargs))
    
    def delete_file(self, path: str, **kwargs) -> MCPResponse:
        return self._run(self._client.delete_file(path, **kwargs))
    
    def run_command(self, command: str, **kwargs) -> MCPResponse:
        return self._run(self._client.run_command(command, **kwargs))
    
    def ping(self) -> MCPResponse:
        return self._run(self._client.ping())

    def set_workspace(
        self,
        root_path: str,
        persist: bool = True,
        config_path: Optional[str] = None,
        reset_cache: bool = True
    ) -> MCPResponse:
        return self._run(self._client.set_workspace(
            root_path,
            persist=persist,
            config_path=config_path,
            reset_cache=reset_cache
        ))
