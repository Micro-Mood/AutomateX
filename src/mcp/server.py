"""
MCP JSON-RPC 服务器
实现JSON-RPC 2.0协议的MCP服务器
"""

import asyncio
import inspect
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union
import structlog

from src.mcp.core.config import MCPConfig, get_config, set_config
from src.mcp.core.security import SecurityManager
from src.mcp.core.cache import CacheManager, get_cache_manager, reset_cache_manager
from src.mcp.core.exceptions import MCPError, InvalidParameterError

# 导入所有模块处理器
from src.mcp.modules import read, search, edit, execute

logger = structlog.get_logger(__name__)


class JSONRPCError(Exception):
    """JSON-RPC错误"""
    
    def __init__(self, code: int, message: str, data: Optional[Any] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "code": self.code,
            "message": self.message,
        }
        if self.data is not None:
            result["data"] = self.data
        return result


# 标准JSON-RPC 2.0错误码
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# 自定义错误码 (-32000 to -32099)
MCP_ERROR_CODE = -32000


class MCPServer:
    """MCP JSON-RPC服务器"""
    
    def __init__(self, config: Optional[MCPConfig] = None):
        self.config = config or get_config()
        set_config(self.config)
        
        self.security = SecurityManager(self.config)
        self.cache = get_cache_manager()
        
        # 方法注册表
        self._methods: Dict[str, Callable] = {}
        self._register_methods()
        
        # 服务器状态
        self._running = False
        self._server = None
        
        logger.info(
            "MCP服务器初始化完成",
            workspace=self.config.workspace.root_path,
            host=self.config.server.host,
            port=self.config.server.port
        )
    
    def _register_methods(self) -> None:
        """注册所有RPC方法"""
        # 读取模块
        self._methods["read_file"] = read.read_file
        self._methods["list_directory"] = read.list_directory
        self._methods["stat_path"] = read.stat_path
        self._methods["exists"] = read.exists
        
        # 搜索模块
        self._methods["search_files"] = search.search_files
        self._methods["search_content"] = search.search_content
        self._methods["search_symbol"] = search.search_symbol
        
        # 编辑模块
        self._methods["create_directory"] = edit.create_directory
        self._methods["delete_directory"] = edit.delete_directory
        self._methods["move_directory"] = edit.move_directory
        self._methods["create_file"] = edit.create_file
        self._methods["write_file"] = edit.write_file
        self._methods["delete_file"] = edit.delete_file
        self._methods["move_file"] = edit.move_file
        self._methods["copy_file"] = edit.copy_file
        self._methods["replace_range"] = edit.replace_range
        self._methods["insert_text"] = edit.insert_text
        self._methods["delete_range"] = edit.delete_range
        self._methods["apply_patch"] = edit.apply_patch
        
        # 执行模块
        self._methods["run_command"] = execute.run_command
        self._methods["create_task"] = execute.create_task
        self._methods["start_task"] = execute.start_task
        self._methods["stop_task"] = execute.stop_task
        self._methods["kill_task"] = execute.kill_task
        self._methods["get_task"] = execute.get_task
        self._methods["list_tasks"] = execute.list_tasks
        self._methods["write_stdin"] = execute.write_stdin
        self._methods["stream_stdout"] = execute.stream_stdout
        self._methods["stream_stderr"] = execute.stream_stderr
        self._methods["wait_task"] = execute.wait_task
        self._methods["attach_task"] = execute.attach_task
        self._methods["detach_task"] = execute.detach_task
        
        # 系统方法
        self._methods["ping"] = self._ping
        self._methods["get_version"] = self._get_version
        self._methods["get_methods"] = self._get_methods
        self._methods["get_config"] = self._get_config
        self._methods["set_workspace"] = self._set_workspace
        self._methods["get_stats"] = self._get_stats
        self._methods["clear_cache"] = self._clear_cache
    
    async def _ping(self) -> Dict[str, Any]:
        """健康检查"""
        return {
            "status": "success",
            "data": {
                "pong": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }
    
    async def _get_version(self) -> Dict[str, Any]:
        """获取版本信息"""
        from src.mcp import __version__
        return {
            "status": "success",
            "data": {
                "version": __version__,
                "protocol": "1.0",
                "python": "3.9+",
            }
        }
    
    async def _get_methods(self) -> Dict[str, Any]:
        """获取可用方法列表"""
        return {
            "status": "success",
            "data": {
                "methods": list(self._methods.keys()),
                "count": len(self._methods),
            }
        }
    
    async def _get_config(self) -> Dict[str, Any]:
        """获取当前配置"""
        return {
            "status": "success",
            "data": {
                "workspace": self.config.workspace.model_dump(),
                "performance": self.config.performance.model_dump(),
                "server": self.config.server.model_dump(),
            }
        }

    async def _set_workspace(
        self,
        root_path: str,
        persist: bool = True,
        config_path: Optional[str] = None,
        reset_cache: bool = True
    ) -> Dict[str, Any]:
        """运行中设置工作区根目录"""
        if not root_path or not isinstance(root_path, str):
            raise InvalidParameterError(
                parameter="root_path",
                value=root_path,
                reason="必须提供有效的路径字符串"
            )

        # 构造新配置并校验（WorkspaceConfig 会验证路径存在）
        config_data = self.config.model_dump()
        config_data.setdefault("workspace", {})["root_path"] = root_path

        try:
            new_config = MCPConfig(**config_data)
        except Exception as e:
            raise InvalidParameterError(
                parameter="root_path",
                value=root_path,
                reason=str(e)
            )

        # 更新全局与本实例配置
        set_config(new_config)
        self.config = new_config
        self.security = SecurityManager(self.config)

        # 重置模块处理器以应用新配置
        try:
            read.reset_handler()
            search.reset_handler()
            edit.reset_handler()
            execute.reset_handler()
        except Exception:
            # 若重置失败，不阻断主流程
            logger.warning("重置模块处理器失败", traceback=traceback.format_exc())

        if reset_cache:
            reset_cache_manager()

        # 持久化配置（默认写入 config/mcp.json）
        if persist:
            target_path = Path(config_path) if config_path else Path("config/mcp.json")
            new_config.to_file(str(target_path))

        return {
            "status": "success",
            "data": {
                "workspace": new_config.workspace.model_dump(),
                "persisted": persist,
                "config_path": str(Path(config_path) if config_path else Path("config/mcp.json"))
            }
        }
    
    async def _get_stats(self) -> Dict[str, Any]:
        """获取服务器统计"""
        return {
            "status": "success",
            "data": {
                "cache": self.cache.get_stats(),
                "uptime": "N/A",  # 可以添加启动时间跟踪
            }
        }
    
    async def _clear_cache(self) -> Dict[str, Any]:
        """清空缓存"""
        self.cache.clear_all()
        return {
            "status": "success",
            "data": {
                "cleared": True,
            }
        }
    
    async def handle_request(self, request_data: Union[str, bytes]) -> str:
        """
        处理JSON-RPC请求
        
        Args:
            request_data: JSON-RPC请求数据
            
        Returns:
            JSON-RPC响应字符串
        """
        try:
            # 解析请求
            if isinstance(request_data, bytes):
                request_data = request_data.decode('utf-8')
            
            try:
                request = json.loads(request_data)
            except json.JSONDecodeError as e:
                return self._error_response(
                    None,
                    JSONRPCError(PARSE_ERROR, f"解析错误: {str(e)}")
                )
            
            # 处理批量请求
            if isinstance(request, list):
                if not request:
                    return self._error_response(
                        None,
                        JSONRPCError(INVALID_REQUEST, "空的批量请求")
                    )
                
                responses = await asyncio.gather(
                    *[self._handle_single_request(req) for req in request],
                    return_exceptions=True
                )
                
                results = []
                for resp in responses:
                    if isinstance(resp, Exception):
                        results.append(self._error_response(
                            None,
                            JSONRPCError(INTERNAL_ERROR, str(resp))
                        ))
                    else:
                        results.append(resp)
                
                return json.dumps([json.loads(r) for r in results], ensure_ascii=False)
            
            # 处理单个请求
            return await self._handle_single_request(request)
            
        except Exception as e:
            logger.error("请求处理异常", error=str(e), traceback=traceback.format_exc())
            return self._error_response(
                None,
                JSONRPCError(INTERNAL_ERROR, f"内部错误: {str(e)}")
            )
    
    async def _handle_single_request(self, request: Dict[str, Any]) -> str:
        """处理单个JSON-RPC请求"""
        # 验证请求格式
        if not isinstance(request, dict):
            return self._error_response(
                None,
                JSONRPCError(INVALID_REQUEST, "请求必须是对象")
            )
        
        # 检查jsonrpc版本
        if request.get("jsonrpc") != "2.0":
            return self._error_response(
                request.get("id"),
                JSONRPCError(INVALID_REQUEST, "必须指定 jsonrpc: '2.0'")
            )
        
        # 获取方法名
        method = request.get("method")
        if not method or not isinstance(method, str):
            return self._error_response(
                request.get("id"),
                JSONRPCError(INVALID_REQUEST, "缺少或无效的method字段")
            )
        
        # 获取参数
        params = request.get("params", {})
        if not isinstance(params, (dict, list)):
            return self._error_response(
                request.get("id"),
                JSONRPCError(INVALID_PARAMS, "params必须是对象或数组")
            )
        
        # 获取请求ID
        request_id = request.get("id")
        
        # 调用方法
        try:
            result = await self._invoke_method(method, params)
            return self._success_response(request_id, result)
            
        except JSONRPCError as e:
            return self._error_response(request_id, e)
        except MCPError as e:
            return self._error_response(
                request_id,
                JSONRPCError(MCP_ERROR_CODE, e.message, e.to_dict())
            )
        except Exception as e:
            logger.error("方法调用异常", method=method, error=str(e))
            return self._error_response(
                request_id,
                JSONRPCError(INTERNAL_ERROR, f"内部错误: {str(e)}")
            )
    
    async def _invoke_method(self, method: str, params: Union[Dict, List]) -> Any:
        """调用注册的方法"""
        if method not in self._methods:
            raise JSONRPCError(METHOD_NOT_FOUND, f"方法不存在: {method}")
        
        handler = self._methods[method]
        
        try:
            if isinstance(params, dict):
                # 根据函数签名自动转换参数类型（AI 经常把数字传成字符串）
                params = self._coerce_params(handler, params)
                result = await handler(**params)
            elif isinstance(params, list):
                result = await handler(*params)
            else:
                result = await handler()
            
            return result
            
        except TypeError as e:
            raise JSONRPCError(INVALID_PARAMS, f"参数错误: {str(e)}")
    
    @staticmethod
    def _coerce_params(handler: Callable, params: Dict[str, Any]) -> Dict[str, Any]:
        """根据函数签名自动转换参数类型"""
        try:
            sig = inspect.signature(handler)
        except (ValueError, TypeError):
            return params
        
        coerced = {}
        for key, value in params.items():
            if key not in sig.parameters or value is None:
                coerced[key] = value
                continue
            
            param = sig.parameters[key]
            annotation = param.annotation
            if annotation is inspect.Parameter.empty:
                coerced[key] = value
                continue
            
            # 处理 Optional/Union 类型，取第一个非 None 类型
            origin = getattr(annotation, '__origin__', None)
            if origin is Union:
                type_args = [a for a in annotation.__args__ if a is not type(None)]
                annotation = type_args[0] if type_args else annotation
            
            # 字符串→数字的自动转换
            if isinstance(value, str) and annotation in (int, float):
                try:
                    coerced[key] = annotation(value)
                    continue
                except (ValueError, TypeError):
                    pass
            
            # bool 字符串转换
            if isinstance(value, str) and annotation is bool:
                coerced[key] = value.lower() in ('true', '1', 'yes')
                continue
            
            coerced[key] = value
        
        return coerced
    
    def _success_response(self, request_id: Any, result: Any) -> str:
        """构建成功响应"""
        response = {
            "jsonrpc": "2.0",
            "result": result,
        }
        if request_id is not None:
            response["id"] = request_id
        
        return json.dumps(response, ensure_ascii=False)
    
    def _error_response(self, request_id: Any, error: JSONRPCError) -> str:
        """构建错误响应"""
        response = {
            "jsonrpc": "2.0",
            "error": error.to_dict(),
        }
        if request_id is not None:
            response["id"] = request_id
        
        return json.dumps(response, ensure_ascii=False)
    
    async def start(self) -> None:
        """启动服务器"""
        if self._running:
            logger.warning("服务器已在运行")
            return
        
        self._running = True
        
        # 启动TCP服务器
        self._server = await asyncio.start_server(
            self._handle_connection,
            self.config.server.host,
            self.config.server.port,
        )
        
        logger.info(
            "MCP服务器已启动",
            host=self.config.server.host,
            port=self.config.server.port
        )
        
        async with self._server:
            await self._server.serve_forever()
    
    async def _handle_connection(
        self, 
        reader: asyncio.StreamReader, 
        writer: asyncio.StreamWriter
    ) -> None:
        """处理客户端连接"""
        addr = writer.get_extra_info('peername')
        logger.debug("新连接", addr=addr)
        
        try:
            while True:
                # 读取请求（按行分隔）
                try:
                    data = await asyncio.wait_for(
                        reader.readline(),
                        timeout=300  # 5分钟超时
                    )
                except asyncio.TimeoutError:
                    break
                
                if not data:
                    break
                
                # 处理请求
                response = await self.handle_request(data)
                
                # 发送响应
                writer.write((response + '\n').encode('utf-8'))
                await writer.drain()
                
        except Exception as e:
            logger.error("连接处理异常", addr=addr, error=str(e))
        finally:
            writer.close()
            await writer.wait_closed()
            logger.debug("连接关闭", addr=addr)
    
    async def stop(self) -> None:
        """停止服务器"""
        if not self._running:
            return
        
        self._running = False
        
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        
        # 清理执行模块
        from src.mcp.modules.execute.handlers import get_handler as get_execute_handler
        await get_execute_handler().cleanup()
        
        logger.info("MCP服务器已停止")


class StdioServer:
    """标准输入输出服务器（用于MCP协议）"""
    
    def __init__(self, config: Optional[MCPConfig] = None):
        self.server = MCPServer(config)
    
    async def run(self) -> None:
        """运行stdio服务器"""
        import sys
        
        logger.info("MCP Stdio服务器已启动")
        
        try:
            while True:
                # 从stdin读取一行
                line = await asyncio.get_event_loop().run_in_executor(
                    None,
                    sys.stdin.readline
                )
                
                if not line:
                    break
                
                line = line.strip()
                if not line:
                    continue
                
                # 处理请求
                response = await self.server.handle_request(line)
                
                # 输出到stdout
                print(response, flush=True)
                
        except KeyboardInterrupt:
            pass
        finally:
            await self.server.stop()
            logger.info("MCP Stdio服务器已停止")


async def run_tcp_server(config: Optional[MCPConfig] = None) -> None:
    """运行TCP服务器"""
    server = MCPServer(config)
    
    try:
        await server.start()
    except KeyboardInterrupt:
        pass
    finally:
        await server.stop()


async def run_stdio_server(config: Optional[MCPConfig] = None) -> None:
    """运行Stdio服务器"""
    server = StdioServer(config)
    await server.run()


def run_server(mode: str = "tcp", config_path: Optional[str] = None) -> None:
    """
    运行MCP服务器
    
    Args:
        mode: 运行模式 ("tcp" 或 "stdio")
        config_path: 配置文件路径
    """
    from src.mcp.core.config import MCPConfig
    
    # 加载配置
    config = MCPConfig.load(config_path)
    
    # 配置日志
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer() if config.logging.log_format == "json" 
            else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    
    # 运行服务器
    if mode == "stdio":
        asyncio.run(run_stdio_server(config))
    else:
        asyncio.run(run_tcp_server(config))


if __name__ == "__main__":
    import sys
    
    mode = sys.argv[1] if len(sys.argv) > 1 else "tcp"
    config_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    run_server(mode, config_path)
