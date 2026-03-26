"""
Tools Module — 让程序长出「手」，供 Agent 调用以操作代码库。

目录结构：
    tools/
        base.py          — Tool 接口定义（Protocol / dataclass）
        executor.py       — ToolExecutor：工具注册、调度、结果返回
        registry.py       — 工具注册表装饰器
        builtin/
            read_file.py  — 读取文件内容
            list_dir.py   — 列出目录结构
            search_code.py — Grep/正则搜索
            run_shell.py  — 执行 shell 命令
            git_ops.py    — Git 操作（clone/diff/log）
"""

from tools.base import Tool, ToolResult, ToolContext
from tools.executor import ToolExecutor
from tools.registry import tool, get_tool_registry
from tools.router import ToolRouter, ToolCategory, ToolMetadata, ToolIntent

__all__ = [
    "Tool",
    "ToolResult",
    "ToolContext",
    "ToolExecutor",
    "tool",
    "get_tool_registry",
    "ToolRouter",
    "ToolCategory",
    "ToolMetadata",
    "ToolIntent",
]
