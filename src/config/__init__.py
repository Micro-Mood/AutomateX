# -*- coding: utf-8 -*-
"""
AutomateX 统一配置模块
======================

集中管理所有配置，提供统一的配置加载和访问接口。

使用方式::

    from src.config import config
    
    # 获取 API 配置
    api_key = config.user.api_key
    model = config.user.model
    
    # 获取用户配置
    model = config.user.model
    
    # 获取系统配置
    mcp_port = config.sys.mcp.server_port
"""

from .loader import ConfigManager, config

__all__ = [
    "ConfigManager",
    "config",
]
