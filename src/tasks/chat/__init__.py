# -*- coding: utf-8 -*-
"""
Chat 模块
=========

OpenAI 风格 API 接口封装，支持 Qwen、DeepSeek、Kimi 等模型。
"""

from .interface import OpenAIChatAPI, get_api, APIConfig

__all__ = [
    "OpenAIChatAPI",
    "get_api",
    "APIConfig",
]
