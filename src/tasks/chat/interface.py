# -*- coding: utf-8 -*-
"""
OpenAI 风格 API 接口
====================

通用 OpenAI 风格 API 接口，支持 Qwen、DeepSeek、Kimi。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Union

import requests

JsonDict = Dict[str, Any]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class APIConfig:
    api_key: str
    base_url: str
    model: str


class OpenAIChatAPI:
    """
    通用 OpenAI 风格 API 接口，支持 Qwen、DeepSeek、Kimi。
    """

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        # 最近一次 API 调用的 token 用量
        self.last_usage: Optional[Dict[str, int]] = None

    def chat(
        self,
        messages: Iterable[Mapping[str, str]],
        temperature: float = 1.0,
        timeout: int = 180,
        max_retries: int = 3,
        stream: bool = True,
        show_reasoning: bool = True,
        on_stream: Optional[Callable[[str, str], None]] = None,
        tools: Optional[List[Mapping[str, Any]]] = None,
        tool_choice: Optional[str] = None,
    ) -> Union[str, Dict[str, Any]]:
        """
        发送聊天消息，返回回复。

        messages 示例：[{"role": "user", "content": "..."}]
        stream: 是否使用流式输出（默认True）
        show_reasoning: 是否在输出中包含思考过程（默认True）
        on_stream: 流式回调函数 (content_so_far, reasoning_so_far) -> None
        
        Returns:
            str: 普通回复内容
            dict: 包含 tool_calls 的响应，格式为 {"content": str, "tool_calls": list}
        """
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            "stream": stream,
        }
        original_stream = stream
        fallback_without_tools = False

        if tools:
            payload["tools"] = list(tools)
            if tool_choice:
                payload["tool_choice"] = tool_choice
            payload["stream"] = False
            stream = False

        last_error = None
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    url, headers=headers, json=payload, timeout=timeout, stream=stream
                )
                if response.status_code == 400 and tools and not fallback_without_tools:
                    logger.warning(
                        "工具调用请求被拒绝(400)，尝试不带 tools 的降级请求: %s",
                        response.text[:500],
                    )
                    payload.pop("tools", None)
                    payload.pop("tool_choice", None)
                    payload["stream"] = original_stream
                    stream = original_stream
                    fallback_without_tools = True
                    continue
                response.raise_for_status()

                if stream:
                    return self._handle_stream_response(
                        response, show_reasoning, on_stream
                    )
                else:
                    return self._handle_json_response(response)

            except requests.exceptions.Timeout as e:
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(min(2**attempt, 10))
                    continue
            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep(min(2**attempt, 10))
                    continue

        if last_error:
            raise last_error
        raise RuntimeError("API 请求失败，所有重试均失败")

    def _handle_stream_response(
        self,
        response,
        show_reasoning: bool,
        on_stream: Optional[Callable[[str, str], None]],
    ) -> str:
        """处理流式响应"""
        full_content = ""
        reasoning_content = ""
        thinking = False
        self.last_usage = None

        for line in response.iter_lines():
            if not line:
                continue
            line_str = line.decode("utf-8")
            if line_str.startswith("data: "):
                data_str = line_str[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue

                    # 捕获流式响应中的 usage（部分 API 在最后一个 chunk 返回）
                    usage = chunk.get("usage")
                    if usage:
                        self.last_usage = {
                            "prompt_tokens": usage.get("prompt_tokens", 0),
                            "completion_tokens": usage.get("completion_tokens", 0),
                            "total_tokens": usage.get("total_tokens", 0),
                        }

                    choice = choices[0]
                    delta = choice.get("delta", {})

                    # 处理思考内容
                    if "reasoning_content" in delta:
                        reasoning_part = delta.get("reasoning_content", "")
                        if reasoning_part:
                            if not thinking:
                                thinking = True
                                if show_reasoning:
                                    print(
                                        "\n=============开始思考=============",
                                        flush=True,
                                    )
                            reasoning_content += reasoning_part
                            if show_reasoning:
                                print(reasoning_part, end="", flush=True)
                            if on_stream:
                                on_stream(full_content, reasoning_content)

                    # 处理回复内容
                    content = delta.get("content", "")
                    if content:
                        if thinking:
                            thinking = False
                            if show_reasoning:
                                print(
                                    "\n=============思考结束=============\n",
                                    flush=True,
                                )
                        full_content += content
                        if show_reasoning:
                            print(content, end="", flush=True)
                        if on_stream:
                            on_stream(
                                full_content,
                                (
                                    reasoning_content
                                    + "\n\n[正在生成回复...]\n"
                                    + full_content
                                    if reasoning_content
                                    else full_content
                                ),
                            )

                except json.JSONDecodeError:
                    continue

        if show_reasoning:
            print()
        return full_content

    def _handle_json_response(self, response) -> Union[str, Dict[str, Any]]:
        """处理 JSON 响应"""
        data: JsonDict = response.json()
        # 捕获 token 用量
        usage = data.get("usage")
        if usage:
            self.last_usage = {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
        else:
            self.last_usage = None
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("API 返回了空的 choices")
        message = choices[0].get("message", {})
        content = message.get("content", "")
        tool_calls = message.get("tool_calls")
        if tool_calls:
            return {
                "content": content,
                "tool_calls": tool_calls,
            }
        return content


def get_api(model_name: str = None) -> OpenAIChatAPI:
    """
    获取 AI API 实例
    
    model_name 参数已废弃，保留仅为兼容旧调用，实际从 user_config 读取。
    """
    from src.config import config
    
    api_key = config.user.api_key
    base_url = config.user.base_url
    model = config.user.model
    
    if not api_key or not base_url or not model:
        raise ValueError("API 未配置，请在设置中填写 API Key、Base URL 和 Model。")
    if any(ord(ch) > 255 for ch in api_key):
        raise ValueError("API Key 含有非 ASCII 字符，请在设置中填写真实的 Key。")
    if "API Key" in api_key:
        raise ValueError("API Key 未配置，请在设置中填写真实的 Key。")
    return OpenAIChatAPI(api_key, base_url, model)
