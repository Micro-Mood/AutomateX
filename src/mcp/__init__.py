"""
Windows MCP (Model Context Protocol) Server
专为Windows环境设计的任务自动化协议服务器

提供四大核心功能模块:
- read: 文件内容读取、目录列表浏览、路径状态查询
- search: 文件名称搜索、文件内容搜索、符号搜索
- edit: 目录操作、文件操作、内容编辑
- execute: 任务管理、输入输出、进程控制
"""

__version__ = "1.0.0"
__author__ = "MCP Team"

# 延迟导入，避免 runpy RuntimeWarning:
#   'src.mcp.server' found in sys.modules after import of package 'src.mcp',
#   but prior to execution of 'src.mcp.server'
# 直接导入 server 会导致在 python -m src.mcp.server 时产生循环导入警告

__all__ = [
    "MCPConfig",
    "MCPError", 
    "MCPServer",
    "__version__",
]


def __getattr__(name: str):
    """按需延迟导入，避免包初始化时的循环导入"""
    if name == "MCPConfig":
        from src.mcp.core.config import MCPConfig
        return MCPConfig
    if name == "MCPError":
        from src.mcp.core.exceptions import MCPError
        return MCPError
    if name == "MCPServer":
        from src.mcp.server import MCPServer
        return MCPServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
