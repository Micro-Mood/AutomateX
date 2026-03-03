"""
MCP 客户端
==========

简化的 MCP Server 通信客户端
"""

import asyncio
import json
import sys
from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class MCPResult:
    """MCP 调用结果"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# ==================== 本地安全检查 ====================

def _validate_command_locally(cmd: str) -> Optional[str]:
    """
    本地命令安全检查（MCP 不可用时的 fallback 安全策略）
    
    复用 src/mcp/core/security.py 的检查逻辑。
    
    Returns:
        None 如果命令安全，否则返回错误信息
    """
    import re
    
    cmd_lower = cmd.lower().strip()
    
    # 阻止的命令关键字
    blocked_commands = [
        "format", "diskpart", "bcdedit", "shutdown", "restart",
        "reg delete", "reg add", "netsh", "sfc", "dism",
    ]
    for blocked in blocked_commands:
        if blocked in cmd_lower:
            return f"命令被阻止（安全策略）: 包含危险关键字 '{blocked}'"
    
    # 危险模式检测（与 security.py 保持一致）
    dangerous_patterns = [
        r'&&\s*(?:del|rd|rmdir|format|diskpart|rm|dd|mkfs)',
        r'\|\s*(?:del|rd|rm|dd|format)',
        r';\s*(?:del|rd|rm|dd|format|mkfs)',
        r'>\s*(?:con|nul|prn|aux|com\d|lpt\d|/dev/)',
        r'`[^`]+`',
        r'\$\([^)]+\)',
        r'\$\{[^}]+\}',
        r'\bdel\s+/[sfq]',
        r'\brd\s+/[sq]',
        r'\brm\s+-[rf]+',
        r'\brm\s+--no-preserve-root',
        r'\bformat\s+[a-z]:',
        r'\bdiskpart',
        r'\bdd\s+.*of=',
        r'\bmkfs\.',
        r'\bnc\s+-[el]',
        r'\bcurl\s+.*\|\s*(?:bash|sh|powershell)',
        r'\bwget\s+.*\|\s*(?:bash|sh)',
        r'invoke-expression',
        r'\biex\s*\(',
        r'-encodedcommand',
        r'downloadstring',
        r'\breg\s+delete',
        r'\breg\s+add\s+.*\/f',
    ]
    
    for pattern in dangerous_patterns:
        if re.search(pattern, cmd_lower, re.IGNORECASE):
            return f"命令被阻止（安全策略）: 匹配危险模式 '{pattern}'"
    
    return None


class MCPClient:
    """MCP 客户端"""
    
    def __init__(self, host: str = "127.0.0.1", port: int = 8080):
        self.host = host
        self.port = port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._req_id = 0
        self._connected = False
    
    @property
    def connected(self) -> bool:
        """是否已连接"""
        return self._connected and self._writer is not None
    
    async def connect(self) -> bool:
        """连接到 MCP Server"""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=5.0
            )
            self._connected = True
            return True
        except Exception:
            self._connected = False
            return False
    
    async def disconnect(self):
        """断开连接"""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._connected = False
        self._writer = None
        self._reader = None
    
    async def __aenter__(self):
        await self.connect()
        return self
    
    async def __aexit__(self, *args):
        await self.disconnect()
    
    async def call(self, method: str, params: Dict[str, Any], timeout: Optional[float] = None) -> MCPResult:
        """调用 MCP 方法
        
        Args:
            method: MCP 方法名
            params: 方法参数
            timeout: 读取超时(秒)，None 则根据方法自动推断
        """
        if not self._writer or not self._reader:
            return MCPResult(False, error="未连接")
        
        self._req_id += 1
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": self._req_id
        }
        
        # 动态推断读超时
        if timeout is None:
            if method == "wait_task":
                # wait_task: 取参数中的 timeout（毫秒→秒）+ 10s 缓冲
                wait_ms = params.get("timeout", 30000)
                if wait_ms <= 0:
                    timeout = 120  # 无限等待时最多等 2 分钟
                else:
                    timeout = wait_ms / 1000 + 10
            elif method == "run_command":
                cmd_ms = params.get("timeout", 30000)
                timeout = cmd_ms / 1000 + 10
            else:
                timeout = 60  # 默认 60 秒
        
        try:
            # 发送请求
            data = json.dumps(request, ensure_ascii=False) + "\n"
            self._writer.write(data.encode())
            await self._writer.drain()
            
            # 读取响应
            line = await asyncio.wait_for(self._reader.readline(), timeout=timeout)
            if not line:
                return MCPResult(False, error="连接已关闭")
            
            response = json.loads(line.decode())
            
            if "error" in response:
                return MCPResult(False, error=response["error"].get("message", "未知错误"))
            
            result = response.get("result", {})
            if isinstance(result, dict):
                if result.get("status") == "success":
                    return MCPResult(True, data=result.get("data"))
                elif result.get("status") == "error":
                    return MCPResult(False, error=result.get("error", {}).get("message", "失败"))
                else:
                    # 直接返回 result
                    return MCPResult(True, data=result)
            else:
                return MCPResult(True, data={"value": result})
        
        except asyncio.TimeoutError:
            return MCPResult(False, error="请求超时")
        except json.JSONDecodeError as e:
            return MCPResult(False, error=f"JSON 解析错误: {e}")
        except Exception as e:
            return MCPResult(False, error=str(e))


# ==================== 本地 Fallback ====================

async def local_run_command(cmd: str, cwd: Optional[str] = None) -> MCPResult:
    """本地执行命令（MCP 不可用时的 fallback）
    
    注意：此函数会执行本地安全检查，与 MCP Server 的安全策略保持一致。
    """
    # P0-3: 在 fallback 路径中执行安全检查
    security_error = _validate_command_locally(cmd)
    if security_error:
        return MCPResult(False, error=security_error)
    
    try:
        if sys.platform == "win32":
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd
            )
        else:
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd
            )
        
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        
        # 解码
        def decode(b: bytes) -> str:
            for enc in ["utf-8", "gbk", "latin-1"]:
                try:
                    return b.decode(enc)
                except Exception:
                    continue
            return b.decode("utf-8", errors="replace")
        
        return MCPResult(
            success=process.returncode == 0,
            data={
                "stdout": decode(stdout),
                "stderr": decode(stderr),
                "code": process.returncode
            },
            error=None if process.returncode == 0 else f"退出码: {process.returncode}"
        )
    
    except asyncio.TimeoutError:
        return MCPResult(False, error="命令执行超时")
    except Exception as e:
        return MCPResult(False, error=str(e))


async def local_read_file(path: str) -> MCPResult:
    """本地读取文件"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return MCPResult(True, data={"content": content})
    except FileNotFoundError:
        return MCPResult(False, error=f"文件不存在: {path}")
    except Exception as e:
        return MCPResult(False, error=str(e))


async def local_write_file(path: str, content: str) -> MCPResult:
    """本地写入文件"""
    try:
        import os
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return MCPResult(True, data={"path": path, "bytes": len(content)})
    except Exception as e:
        return MCPResult(False, error=str(e))


async def local_list_dir(path: str, pattern: str = "*") -> MCPResult:
    """本地列目录"""
    try:
        import os
        import fnmatch
        
        if not os.path.isdir(path):
            return MCPResult(False, error=f"目录不存在: {path}")
        
        entries = []
        for name in os.listdir(path):
            if fnmatch.fnmatch(name, pattern):
                full_path = os.path.join(path, name)
                is_dir = os.path.isdir(full_path)
                entries.append({
                    "name": name,
                    "type": "directory" if is_dir else "file"
                })
        
        return MCPResult(True, data={"entries": entries})
    except Exception as e:
        return MCPResult(False, error=str(e))


async def local_exists(path: str) -> MCPResult:
    """本地检查文件/目录是否存在"""
    import os
    exists = os.path.exists(path)
    is_file = os.path.isfile(path) if exists else False
    is_dir = os.path.isdir(path) if exists else False
    return MCPResult(True, data={
        "exists": exists,
        "is_file": is_file,
        "is_directory": is_dir
    })
