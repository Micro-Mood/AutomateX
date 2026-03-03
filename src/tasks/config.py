# -*- coding: utf-8 -*-
"""
AutomateX 配置模块
==================

从统一配置系统加载配置，提供向后兼容的接口。
"""

import logging
import os
import sys
from pathlib import Path
from typing import Optional

# 导入统一配置
from src.config import config

# ============== 项目路径 ==============
PROJECT_ROOT = config.project_root


def get_default_working_directory() -> Path:
    """获取默认工作目录"""
    return config.get_working_directory()


def set_default_working_directory(path: str | Path) -> None:
    """设置默认工作目录"""
    p = Path(path)
    if not p.exists():
        raise ValueError(f"路径不存在: {path}")
    if not p.is_dir():
        raise ValueError(f"不是目录: {path}")
    config.user.default_working_directory = str(p)
    config.save_user_config()


def resolve_working_directory(working_dir: Optional[str] = None) -> Path:
    """解析工作目录，如果未指定则使用默认值"""
    if working_dir:
        return Path(working_dir)
    return get_default_working_directory()


# ============== MCP 配置 ==============
def get_mcp_host() -> str:
    return config.sys.mcp.host


def get_mcp_port() -> int:
    return config.sys.mcp.port


DEFAULT_MCP_HOST = "127.0.0.1"  # 向后兼容
DEFAULT_MCP_PORT = 8080


# ============== 处理器配置 ==============
def get_max_iterations() -> int:
    return config.get_max_iterations()


DEFAULT_MAX_ITERATIONS = 50
DEFAULT_MODEL_NAME = "deepseek"


# ============== 日志配置 ==============
_LOGGING_CONFIGURED = False

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LOG_LEVEL = logging.INFO


def get_log_level() -> int:
    """获取日志级别"""
    env_level = os.environ.get("AUTOMATEX_LOG_LEVEL", "").upper()
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }
    if env_level in level_map:
        return level_map[env_level]
    
    # 从配置读取
    config_level = config.sys.log_level.upper()
    return level_map.get(config_level, DEFAULT_LOG_LEVEL)


def setup_logging(level: Optional[int] = None, force: bool = False) -> None:
    """配置统一的日志系统"""
    global _LOGGING_CONFIGURED
    
    if _LOGGING_CONFIGURED and not force:
        return
    
    log_level = level if level is not None else get_log_level()
    
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(log_level)
    
    formatter = logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT)
    console_handler.setFormatter(formatter)
    
    root_logger.addHandler(console_handler)
    
    for module_name in ["src.tasks", "src.mcp", "ws_manager"]:
        module_logger = logging.getLogger(module_name)
        module_logger.setLevel(log_level)
    
    _LOGGING_CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """获取日志器"""
    setup_logging()
    return logging.getLogger(name)


def safe_print(message: str, **kwargs) -> None:
    """安全打印，处理 Windows 控制台编码问题"""
    try:
        print(message, **kwargs)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        safe_message = message.encode(encoding, errors="replace").decode(encoding, errors="replace")
        print(safe_message, **kwargs)
