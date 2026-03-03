# -*- coding: utf-8 -*-
"""
配置加载器
==========

统一加载和管理所有配置文件，提供类型化的配置访问接口。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

# 配置目录
CONFIG_DIR = Path(__file__).parent
PROJECT_ROOT = CONFIG_DIR.parent.parent


@dataclass
class MCPConfig:
    """MCP Server 配置"""
    host: str = "127.0.0.1"
    port: int = 8080
    workspace_root: str = "./workspace"
    blocked_paths: list = field(default_factory=list)
    blocked_commands: list = field(default_factory=list)
    max_file_size_mb: int = 10
    max_concurrent_tasks: int = 10
    cache_ttl_seconds: int = 60
    log_level: str = "INFO"


@dataclass
class TasksConfig:
    """Tasks 引擎配置"""
    max_iterations: int = 50
    context_max_history: int = 20
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000


@dataclass
class UserConfig:
    """用户配置"""
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    default_working_directory: str = ""
    auto_scroll: bool = True
    max_iterations: int = 50


@dataclass
class SysConfig:
    """系统配置"""
    mcp: MCPConfig = field(default_factory=MCPConfig)
    tasks: TasksConfig = field(default_factory=TasksConfig)
    log_level: str = "INFO"


class ConfigManager:
    """
    统一配置管理器
    
    加载并管理所有配置文件，提供类型化访问接口。
    """
    
    _instance: Optional["ConfigManager"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        
        # 配置文件路径
        self._user_config_path = CONFIG_DIR / "user_config.json"
        self._sys_config_path = CONFIG_DIR / "sys_config.json"
        
        # 加载配置
        self._load_all()
    
    def _load_json(self, path: Path) -> Dict[str, Any]:
        """加载 JSON 配置文件"""
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    
    def _save_json(self, path: Path, data: Dict[str, Any]) -> None:
        """保存 JSON 配置文件"""
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def _load_all(self) -> None:
        """加载所有配置"""
        # 加载用户配置
        user_data = self._load_json(self._user_config_path)
        api_data = user_data.get("api", {})
        self.user = UserConfig(
            api_key=api_data.get("api_key", ""),
            base_url=api_data.get("base_url", ""),
            model=api_data.get("model", ""),
            default_working_directory=user_data.get("workspace", {}).get("default_working_directory", ""),
            auto_scroll=user_data.get("ui", {}).get("auto_scroll", True),
            max_iterations=user_data.get("task", {}).get("max_iterations", 50),
        )
        
        # 加载系统配置
        sys_data = self._load_json(self._sys_config_path)
        mcp_data = sys_data.get("mcp", {})
        tasks_data = sys_data.get("tasks", {})
        
        self.sys = SysConfig(
            mcp=MCPConfig(
                host=mcp_data.get("server", {}).get("host", "127.0.0.1"),
                port=mcp_data.get("server", {}).get("port", 8080),
                workspace_root=mcp_data.get("workspace", {}).get("root_path", "./workspace"),
                blocked_paths=mcp_data.get("security", {}).get("blocked_paths", []),
                blocked_commands=mcp_data.get("security", {}).get("blocked_commands", []),
                max_file_size_mb=mcp_data.get("security", {}).get("max_file_size_mb", 10),
                max_concurrent_tasks=mcp_data.get("performance", {}).get("max_concurrent_tasks", 10),
                cache_ttl_seconds=mcp_data.get("performance", {}).get("cache_ttl_seconds", 60),
                log_level=mcp_data.get("logging", {}).get("level", "INFO"),
            ),
            tasks=TasksConfig(
                max_iterations=tasks_data.get("engine", {}).get("max_iterations", 50),
                context_max_history=tasks_data.get("engine", {}).get("context_max_history", 20),
                backend_host=tasks_data.get("backend", {}).get("host", "127.0.0.1"),
                backend_port=tasks_data.get("backend", {}).get("port", 8000),
            ),
            log_level=sys_data.get("logging", {}).get("level", "INFO"),
        )
        
    def reload(self) -> None:
        """重新加载所有配置"""
        self._load_all()

    def save_user_config(self) -> None:
        """保存用户配置"""
        data = {
            "workspace": {
                "default_working_directory": self.user.default_working_directory,
            },
            "ui": {
                "auto_scroll": self.user.auto_scroll,
            },
            "task": {
                "max_iterations": self.user.max_iterations,
            },
            "api": {
                "api_key": self.user.api_key,
                "base_url": self.user.base_url,
                "model": self.user.model,
            },
        }
        self._save_json(self._user_config_path, data)
    
    def get_working_directory(self) -> Path:
        """
        获取工作目录
        
        优先级: 环境变量 > 用户配置 > 用户主目录
        """
        # 环境变量
        env_dir = os.environ.get("AUTOMATEX_WORKING_DIR")
        if env_dir:
            p = Path(env_dir)
            if p.exists() and p.is_dir():
                return p
        
        # 用户配置
        if self.user.default_working_directory:
            p = Path(self.user.default_working_directory)
            if p.exists() and p.is_dir():
                return p
        
        # 默认
        return Path.home()
    
    def get_max_iterations(self) -> int:
        """获取最大迭代次数（用户配置优先）"""
        if self.user.max_iterations > 0:
            return self.user.max_iterations
        return self.sys.tasks.max_iterations
    
    @property
    def project_root(self) -> Path:
        """项目根目录"""
        return PROJECT_ROOT


# 全局配置实例
config = ConfigManager()
