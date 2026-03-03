"""
MCP配置模块
支持环境变量和配置文件
"""

import os
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field
from pydantic import BaseModel, Field, field_validator
import structlog

logger = structlog.get_logger(__name__)


class WorkspaceConfig(BaseModel):
    """工作区配置"""
    root_path: str = Field(default=".", description="工作区根目录")
    allowed_extensions: List[str] = Field(
        default_factory=lambda: [".txt", ".py", ".js", ".ts", ".json", ".md", ".yaml", ".yml", 
                                  ".xml", ".html", ".css", ".java", ".cpp", ".c", ".h", ".hpp",
                                  ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".bat", ".ps1",
                                  ".sql", ".ini", ".cfg", ".conf", ".toml", ".env", ".log"],
        description="允许的文件扩展名"
    )
    max_depth: int = Field(default=10, ge=1, le=50, description="最大递归深度")
    
    @field_validator('root_path')
    @classmethod
    def validate_root_path(cls, v: str) -> str:
        """验证并规范化工作区路径"""
        path = Path(v).resolve()
        if not path.exists():
            raise ValueError(f"工作区路径不存在: {v}")
        return str(path)


class SecurityConfig(BaseModel):
    """安全配置"""
    blocked_paths: List[str] = Field(
        default_factory=lambda: [
            "C:\\Windows",
            "C:\\Program Files",
            "C:\\Program Files (x86)",
            "C:\\ProgramData",
            "C:\\System Volume Information",
            "C:\\$Recycle.Bin",
            "C:\\Recovery",
            "C:\\Boot",
        ],
        description="禁止访问的路径列表"
    )
    blocked_commands: List[str] = Field(
        default_factory=lambda: [
            "format", "diskpart", "del /s", "rd /s", "rmdir /s",
            "reg delete", "reg add", "bcdedit", "shutdown", "taskkill /f",
            "net user", "net localgroup", "runas", "powershell -ep bypass",
        ],
        description="禁止执行的危险命令"
    )
    max_file_size_mb: int = Field(default=100, ge=1, le=1000, description="最大文件大小(MB)")
    require_authentication: bool = Field(default=False, description="是否需要认证")
    allowed_shells: List[str] = Field(
        default_factory=lambda: ["cmd.exe", "powershell.exe", "pwsh.exe"],
        description="允许的Shell程序"
    )


class PerformanceConfig(BaseModel):
    """性能配置"""
    max_concurrent_tasks: int = Field(default=10, ge=1, le=100, description="最大并发任务数")
    cache_ttl_seconds: int = Field(default=60, ge=0, le=3600, description="缓存TTL(秒)")
    stream_buffer_size_kb: int = Field(default=4, ge=1, le=64, description="流缓冲区大小(KB)")
    max_list_items: int = Field(default=1000, ge=100, le=10000, description="列表最大条目数")
    max_search_results: int = Field(default=1000, ge=100, le=10000, description="搜索最大结果数")
    max_search_files: int = Field(default=10000, ge=100, le=100000, description="搜索最大扫描文件数")
    default_timeout_ms: int = Field(default=30000, ge=1000, le=300000, description="默认超时时间(毫秒)")
    max_memory_mb: int = Field(default=512, ge=64, le=4096, description="最大内存使用(MB)")


class LoggingConfig(BaseModel):
    """日志配置"""
    level: str = Field(default="INFO", description="日志级别")
    audit_enabled: bool = Field(default=True, description="是否启用审计日志")
    log_file: Optional[str] = Field(default=None, description="日志文件路径")
    log_format: str = Field(default="json", description="日志格式: json 或 text")
    max_log_size_mb: int = Field(default=100, ge=1, le=1000, description="最大日志文件大小(MB)")
    backup_count: int = Field(default=5, ge=0, le=100, description="日志备份数量")


class ServerConfig(BaseModel):
    """服务器配置"""
    host: str = Field(default="127.0.0.1", description="监听地址")
    port: int = Field(default=8080, ge=1024, le=65535, description="监听端口")
    unix_socket: Optional[str] = Field(default=None, description="Unix套接字路径")
    ssl_cert: Optional[str] = Field(default=None, description="SSL证书路径")
    ssl_key: Optional[str] = Field(default=None, description="SSL密钥路径")
    cors_origins: List[str] = Field(default_factory=lambda: ["*"], description="CORS允许的来源")


class MCPConfig(BaseModel):
    """MCP主配置类"""
    workspace: WorkspaceConfig = Field(default_factory=WorkspaceConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    performance: PerformanceConfig = Field(default_factory=PerformanceConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    
    @classmethod
    def from_file(cls, config_path: str) -> "MCPConfig":
        """从JSON配置文件加载配置"""
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        return cls(**data)
    
    @classmethod
    def from_env(cls) -> "MCPConfig":
        """从环境变量加载配置"""
        config_data: Dict[str, Any] = {}
        
        # 工作区配置
        if os.getenv("MCP_WORKSPACE_ROOT"):
            config_data.setdefault("workspace", {})["root_path"] = os.getenv("MCP_WORKSPACE_ROOT")
        
        if os.getenv("MCP_ALLOWED_EXTENSIONS"):
            config_data.setdefault("workspace", {})["allowed_extensions"] = \
                os.getenv("MCP_ALLOWED_EXTENSIONS", "").split(",")
        
        # 安全配置
        if os.getenv("MCP_MAX_FILE_SIZE_MB"):
            config_data.setdefault("security", {})["max_file_size_mb"] = \
                int(os.getenv("MCP_MAX_FILE_SIZE_MB", "100"))
        
        if os.getenv("MCP_REQUIRE_AUTH"):
            config_data.setdefault("security", {})["require_authentication"] = \
                os.getenv("MCP_REQUIRE_AUTH", "").lower() == "true"
        
        # 性能配置
        if os.getenv("MCP_MAX_CONCURRENT_TASKS"):
            config_data.setdefault("performance", {})["max_concurrent_tasks"] = \
                int(os.getenv("MCP_MAX_CONCURRENT_TASKS", "10"))
        
        if os.getenv("MCP_CACHE_TTL"):
            config_data.setdefault("performance", {})["cache_ttl_seconds"] = \
                int(os.getenv("MCP_CACHE_TTL", "60"))
        
        # 日志配置
        if os.getenv("MCP_LOG_LEVEL"):
            config_data.setdefault("logging", {})["level"] = os.getenv("MCP_LOG_LEVEL")
        
        if os.getenv("MCP_LOG_FILE"):
            config_data.setdefault("logging", {})["log_file"] = os.getenv("MCP_LOG_FILE")
        
        # 服务器配置
        if os.getenv("MCP_HOST"):
            config_data.setdefault("server", {})["host"] = os.getenv("MCP_HOST")
        
        if os.getenv("MCP_PORT"):
            config_data.setdefault("server", {})["port"] = int(os.getenv("MCP_PORT", "8080"))
        
        return cls(**config_data)
    
    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "MCPConfig":
        """加载配置，优先级: 配置文件 > 环境变量 > 默认值"""
        # 首先尝试从配置文件加载
        if config_path and Path(config_path).exists():
            logger.info("从配置文件加载配置", path=config_path)
            return cls.from_file(config_path)
        
        # 检查默认配置文件路径
        default_paths = [
            Path("mcp.json"),
            Path("config/mcp.json"),
            Path.home() / ".mcp" / "config.json",
        ]
        
        for path in default_paths:
            if path.exists():
                logger.info("从默认配置文件加载配置", path=str(path))
                return cls.from_file(str(path))
        
        # 从环境变量加载
        logger.info("从环境变量加载配置")
        return cls.from_env()
    
    def to_file(self, config_path: str) -> None:
        """保存配置到文件"""
        path = Path(config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.model_dump(), f, indent=2, ensure_ascii=False)
        
        logger.info("配置已保存", path=config_path)
    
    def get_max_file_size_bytes(self) -> int:
        """获取最大文件大小(字节)"""
        return self.security.max_file_size_mb * 1024 * 1024
    
    def get_stream_buffer_size_bytes(self) -> int:
        """获取流缓冲区大小(字节)"""
        return self.performance.stream_buffer_size_kb * 1024
    
    def is_path_blocked(self, path: str) -> bool:
        """检查路径是否被阻止"""
        path_lower = path.lower()
        for blocked in self.security.blocked_paths:
            if path_lower.startswith(blocked.lower()):
                return True
        return False
    
    def is_command_blocked(self, command: str) -> bool:
        """检查命令是否被阻止"""
        command_lower = command.lower()
        for blocked in self.security.blocked_commands:
            if blocked.lower() in command_lower:
                return True
        return False


# 全局配置实例
_config: Optional[MCPConfig] = None


def get_config() -> MCPConfig:
    """获取全局配置实例"""
    global _config
    if _config is None:
        _config = MCPConfig.load()
    return _config


def set_config(config: MCPConfig) -> None:
    """设置全局配置实例"""
    global _config
    _config = config


def reset_config() -> None:
    """重置全局配置实例"""
    global _config
    _config = None
