"""
agent/messages.py — Agent 消息模型

统一的消息类型：
- HumanMessage  : 用户输入（Skill task prompt）
- SystemMessage  : 系统级指令（工具描述、Evidence 规则）
- AIMessage      : LLM 输出（可能是纯文本，也可能是 ToolCall）
- ToolMessage    : 工具执行结果（追加到对话历史）
- ToolCall       : LLM 请求的工具调用（嵌入在 AIMessage 中）
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Message types
# ─────────────────────────────────────────────────────────────────────────────

class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolCall:
    """
    嵌入在 AIMessage 中的工具调用请求。
    格式与 OpenAI function_calling 一致。
    """
    id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:12]}")
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCall":
        fn = data.get("function", {})
        return cls(
            id=str(data.get("id", "")),
            name=fn.get("name", ""),
            arguments=json.loads(fn.get("arguments", "{}"))
                      if isinstance(fn.get("arguments"), str)
                      else fn.get("arguments", {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass
class Message:
    """
    统一消息基类。
    所有消息类型共享 role / content / timestamp。
    """
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""    # ToolMessage 必须填此字段
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "role": self.role.value,
            "content": self.content,
        }
        if self.tool_calls:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


@dataclass
class HumanMessage(Message):
    """用户输入消息（Skill task prompt）。"""
    def __init__(self, content: str, **kwargs: Any):
        super().__init__(role=Role.USER, content=content, **kwargs)


@dataclass
class SystemMessage(Message):
    """系统级指令消息。"""
    def __init__(self, content: str, **kwargs: Any):
        super().__init__(role=Role.SYSTEM, content=content, **kwargs)


@dataclass
class AIMessage(Message):
    """LLM 输出消息。"""
    def __init__(self, content: str = "", tool_calls: list[ToolCall] | None = None, **kwargs: Any):
        super().__init__(
            role=Role.ASSISTANT,
            content=content,
            tool_calls=tool_calls or [],
            **kwargs,
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AIMessage":
        tcs = [ToolCall.from_dict(tc) for tc in d.get("tool_calls", [])]
        return cls(
            content=d.get("content", ""),
            tool_calls=tcs,
            metadata=d.get("metadata", {}),
        )


@dataclass
class ToolMessage(Message):
    """
    工具执行结果消息。

    ToolMessage 追加到对话历史，告诉 LLM 工具返回了什么。
    """
    def __init__(
        self,
        tool_call_id: str,
        content: str,
        tool_name: str = "",
        success: bool = True,
        **kwargs: Any,
    ):
        super().__init__(
            role=Role.TOOL,
            content=content,
            tool_call_id=tool_call_id,
            metadata={"tool_name": tool_name, "success": success, **kwargs},
        )

    @property
    def tool_name(self) -> str:
        return self.metadata.get("tool_name", "")

    @property
    def success(self) -> bool:
        return self.metadata.get("success", True)
