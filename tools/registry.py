"""
tools/registry.py — 工具注册表与装饰器

提供一个 @tool 装饰器，自动将工具类注册到全局注册表。
在 ToolExecutor 初始化时统一加载所有内置工具。

使用方式：
    from tools.registry import tool, get_tool_registry

    @tool
    class MyTool:
        name = "my_tool"
        description = "..."
        input_schema = {...}
        def execute(self, args, ctx): ...

    # 获取已注册的所有工具
    registry = get_tool_registry()
"""

from __future__ import annotations

from typing import Any, ClassVar

from tools.base import Tool, ToolResult, ToolContext


# ─────────────────────────────────────────────────────────────────────────────
# 全局注册表
# ─────────────────────────────────────────────────────────────────────────────

class _ToolRegistry:
    """全局工具注册表。"""

    def __init__(self) -> None:
        self._tools: dict[str, type[Tool]] = {}

    def register(self, tool_cls: type[Tool]) -> type[Tool]:
        """注册一个工具类。"""
        self._tools[tool_cls.name] = tool_cls
        return tool_cls

    def get(self, name: str) -> type[Tool] | None:
        return self._tools.get(name)

    def list_all(self) -> list[type[Tool]]:
        return list(self._tools.values())

    def list_names(self) -> list[str]:
        return list(self._tools.keys())


# 全局单例
_global_registry = _ToolRegistry()


def get_tool_registry() -> _ToolRegistry:
    """获取全局工具注册表。"""
    return _global_registry


# ─────────────────────────────────────────────────────────────────────────────
# @tool 装饰器
# ─────────────────────────────────────────────────────────────────────────────

def tool(cls: type[Tool]) -> type[Tool]:
    """
    工具类装饰器，自动注册到全局注册表。

    用法：
        @tool
        class ReadFileTool:
            name = "read_file"
            description = "..."
            input_schema = {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "..."},
                },
                "required": ["path"],
            }

            def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
                ...
    """
    _global_registry.register(cls)
    return cls
