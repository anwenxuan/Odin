"""
agent/llm_adapter.py — LLM 适配器

统一 LLM 调用接口，同时支持 OpenAI / Anthropic / Ollama / Mock 四种后端。

核心接口：LLMAdapter（抽象基类）+ 具体实现
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from agent.messages import AIMessage, Message

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# LLM Response — LLM 响应的统一包装
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LLMResponse:
    """
    LLM API 响应的统一包装。
    不管是 OpenAI / Anthropic / Ollama，返回格式统一为：
    - content: str         （纯文本内容，可能为空）
    - tool_calls: list     （如果有函数调用）
    - raw: dict           （原始 API 响应）
    - usage: dict         （token 用量）
    - model: str          （实际使用的模型）
    """
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# LLMAdapter — 抽象基类
# ─────────────────────────────────────────────────────────────────────────────

class LLMAdapter(ABC):
    """
    LLM 调用适配器抽象基类。

    实现此接口即可接入任意 LLM 提供商。
    """

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        """
        发送对话请求到 LLM。

        Args:
            messages   : 对话历史（OpenAI 格式）
            tools     : 工具描述列表（OpenAI tools 格式），None 表示不使用工具
            model     : 模型名，覆盖默认配置
            temperature: 温度参数
            max_tokens: 最大 token 数

        Returns:
            LLMResponse，统一包装的响应
        """
        ...

    @abstractmethod
    def supports_tools(self) -> bool:
        """该适配器是否支持 Function Calling / Tool Use。"""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """提供商名称（如 'openai'、'anthropic'）。"""
        ...


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI Adapter
# ─────────────────────────────────────────────────────────────────────────────

class OpenAIAdapter(LLMAdapter):
    """OpenAI GPT 系列（gpt-4o / gpt-4o-mini 等）。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str = "gpt-4o-mini",
        default_temperature: float = 0.0,
        default_max_tokens: int = 4096,
    ):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url
        self.default_model = default_model
        self.default_temperature = default_temperature
        self.default_max_tokens = default_max_tokens
        self._client: Any = None

    @property
    def provider_name(self) -> str:
        return "openai"

    def supports_tools(self) -> bool:
        return True

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from openai import OpenAI as _OpenAI
            except ImportError:
                raise ImportError(
                    "openai package not installed. Run: pip install openai"
                )
            kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = _OpenAI(**kwargs)
        return self._client

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        client = self._get_client()
        req: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.default_temperature,
            "max_tokens": max_tokens or self.default_max_tokens,
        }
        if tools:
            req["tools"] = tools
            req["tool_choice"] = "auto"

        response = client.chat.completions.create(**req)
        raw = json.loads(response.model_dump_json())

        # 解析 tool_calls
        tool_calls = []
        choice = raw.get("choices", [{}])[0]
        delta = choice.get("message", {})

        # OpenAI 的 tool_calls 在 message.tool_calls 中
        raw_tool_calls = delta.get("tool_calls") or []
        for tc in raw_tool_calls:
            tool_calls.append({
                "id": tc.get("id", ""),
                "type": tc.get("type", "function"),
                "function": {
                    "name": tc.get("function", {}).get("name", ""),
                    "arguments": tc.get("function", {}).get("arguments", ""),
                },
            })

        return LLMResponse(
            content=delta.get("content") or "",
            tool_calls=tool_calls,
            raw=raw,
            usage=raw.get("usage", {}),
            model=raw.get("model", model or self.default_model),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Anthropic Adapter
# ─────────────────────────────────────────────────────────────────────────────

class AnthropicAdapter(LLMAdapter):
    """Anthropic Claude 系列（claude-sonnet-4 / claude-3-5-sonnet 等）。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str = "claude-sonnet-4-20250514",
        default_max_tokens: int = 4096,
    ):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.base_url = base_url
        self.default_model = default_model
        self.default_max_tokens = default_max_tokens
        self._client: Any = None

    @property
    def provider_name(self) -> str:
        return "anthropic"

    def supports_tools(self) -> bool:
        return True

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic as _Anthropic
            except ImportError:
                raise ImportError(
                    "anthropic package not installed. Run: pip install anthropic"
                )
            kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = _Anthropic.Anthropic(**kwargs)
        return self._client

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        client = self._get_client()

        # Anthropic 的 messages 格式与 OpenAI 兼容，但 system 消息需要单独处理
        system_parts: list[str] = []
        chat_messages: list[dict[str, Any]] = []

        for msg in messages:
            if msg.get("role") == "system":
                system_parts.append(msg.get("content", ""))
            else:
                chat_messages.append(msg)

        req: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": chat_messages,
            "max_tokens": max_tokens or self.default_max_tokens,
        }
        if system_parts:
            req["system"] = "\n".join(system_parts)
        if temperature != 0.0:
            req["temperature"] = temperature
        if tools:
            req["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"].get("parameters", {}),
                }
                for t in tools
            ]

        response = client.messages.create(**req)
        raw = json.loads(response.model_dump_json())

        # Anthropic 响应
        content_blocks = raw.get("content", [])
        text_parts: list[str] = []
        tool_use_blocks: list[dict[str, Any]] = []

        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_use_blocks.append({
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {})),
                    },
                })

        return LLMResponse(
            content="\n".join(text_parts),
            tool_calls=tool_use_blocks,
            raw=raw,
            usage={
                "input_tokens": raw.get("usage", {}).get("input_tokens", 0),
                "output_tokens": raw.get("usage", {}).get("output_tokens", 0),
            },
            model=raw.get("model", model or self.default_model),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Mock Adapter — 用于测试
# ─────────────────────────────────────────────────────────────────────────────

class MockAdapter(LLMAdapter):
    """
    Mock LLM 适配器，用于测试和开发。

    返回预设的响应，或按简单规则生成响应。
    """

    def __init__(
        self,
        response_template: str = '{"result": "mock"}',
        tool_calls_enabled: bool = True,
    ):
        self.response_template = response_template
        self.tool_calls_enabled = tool_calls_enabled
        self.call_history: list[dict[str, Any]] = []

    @property
    def provider_name(self) -> str:
        return "mock"

    def supports_tools(self) -> bool:
        return True

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        self.call_history.append({
            "messages": messages,
            "tools": tools,
            "model": model,
        })

        # 检查最后一轮用户消息中是否有工具调用历史
        # 如果有，说明这是 LLM 收到工具结果后的再次调用
        has_tool_results = any(
            m.get("role") == "tool" for m in messages
        )

        if has_tool_results:
            # 工具结果回来了 → 生成最终 JSON 输出
            return LLMResponse(
                content=self.response_template,
                tool_calls=[],
                usage={"input_tokens": 100, "output_tokens": 50},
                model="mock",
            )

        # 第一轮 → 检查是否有工具可用
        if tools and self.tool_calls_enabled:
            # 模拟 LLM 请求第一个工具
            tool = tools[0]
            tool_name = tool.get("function", {}).get("name", "")
            if tool_name == "detect_lang":
                args = {}
            elif tool_name == "list_dir":
                args = {"dir": ""}
            elif tool_name == "read_file":
                args = {"path": "README.md"}
            elif tool_name == "search_code":
                args = {"pattern": "def.*\\(.*\\):"}
            elif tool_name == "git_log":
                args = {"n": 5}
            else:
                args = {}

            return LLMResponse(
                content="",
                tool_calls=[{
                    "id": f"call_mock_{tool_name}",
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(args),
                    },
                }],
                usage={"input_tokens": 50, "output_tokens": 30},
                model="mock",
            )

        return LLMResponse(
            content="Mock response — no tools available",
            tool_calls=[],
            usage={"input_tokens": 10, "output_tokens": 5},
            model="mock",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Adapter Factory
# ─────────────────────────────────────────────────────────────────────────────

def create_adapter(
    provider: str,
    **kwargs: Any,
) -> LLMAdapter:
    """
    根据 provider 名称创建对应的 LLM 适配器。

    Args:
        provider : openai | anthropic | ollama | mock
        **kwargs : 传给具体 adapter 的参数

    Returns:
        LLMAdapter 实例
    """
    provider = provider.lower().strip()

    if provider == "openai":
        return OpenAIAdapter(**kwargs)
    elif provider in ("anthropic", "claude"):
        return AnthropicAdapter(**kwargs)
    elif provider == "mock":
        return MockAdapter(**kwargs)
    elif provider == "ollama":
        # Ollama 使用 OpenAI-compatible API
        return OpenAIAdapter(
            base_url=kwargs.get("base_url", "http://localhost:11434/v1"),
            api_key=kwargs.get("api_key", "ollama"),
            default_model=kwargs.get("model", "llama3"),
            **kwargs,
        )
    else:
        raise ValueError(
            f"Unknown LLM provider: '{provider}'. "
            f"Supported: openai, anthropic, ollama, mock"
        )
