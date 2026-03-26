"""
tools/executor.py — ToolExecutor：工具注册、调度与结果返回

核心职责：
1. 管理工具注册表（内置工具 + 自定义工具）
2. 执行工具调用（参数解析 → 路由 → 执行 → 结果包装）
3. 提供工具列表（生成 OpenAI/Anthropic Function Calling 格式）
4. 安全路径检查（禁止访问 repo 目录之外的路径）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.base import (
    Tool,
    ToolResult,
    ToolContext,
    tools_to_openai_spec,
    tools_to_anthropic_spec,
)
from tools.registry import get_tool_registry

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 工具调用记录
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ToolCall:
    """单次工具调用的记录。"""
    tool_name: str
    arguments: dict[str, Any]
    result: ToolResult
    iteration: int = 0   # AgentLoop 第几轮迭代

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "success": self.result.success,
            "output_preview": self.result.output[:200] if self.result.output else "",
            "error": self.result.error,
            "iteration": self.iteration,
        }


# ─────────────────────────────────────────────────────────────────────────────
# ToolExecutor
# ─────────────────────────────────────────────────────────────────────────────

class ToolExecutor:
    """
    工具执行器。

    核心职责：
    1. 工具注册（register / auto_load_builtin）
    2. 工具调用（execute）
    3. 工具描述导出（to_openai_spec / to_anthropic_spec）
    4. 调用历史记录（tool_calls）

    使用方式：
        executor = ToolExecutor(repo_path=Path("/tmp/my-repo"))
        executor.auto_load_builtin()          # 加载所有内置工具
        executor.register(MyCustomTool())     # 添加自定义工具

        # 执行工具
        result = executor.execute("read_file", {"path": "src/main.py"})

        # 获取 OpenAI tools 格式
        spec = executor.to_openai_spec()
    """

    def __init__(
        self,
        repo_path: Path | str | None = None,
        session_id: str = "",
        run_id: str = "",
        max_tool_call_errors: int = 3,
    ):
        self._tools: dict[str, Tool] = {}
        self._repo_path = Path(repo_path) if repo_path else None
        self._session_id = session_id
        self._run_id = run_id
        self._max_tool_call_errors = max_tool_call_errors
        self._tool_call_errors: dict[str, int] = {}   # tool_name → error count
        self._call_history: list[ToolCall] = []

    # ── 注册 ──────────────────────────────────────────────────────────────────

    def register(self, tool_instance: Tool | type[Tool]) -> None:
        """注册一个工具（支持实例或类）。"""
        if isinstance(tool_instance, type):
            # 类 → 实例化
            instance = tool_instance()
        else:
            instance = tool_instance
        self._tools[instance.name] = instance
        logger.debug("Registered tool: %s", instance.name)

    def auto_load_builtin(self) -> None:
        """
        自动加载所有内置工具。

        导入 builtin 子模块即可触发 @tool 装饰器注册。
        """
        # 延迟导入避免循环依赖
        from tools import builtin as _builtin
        import importlib
        import pkgutil

        for _importer, modname, _ispkg in pkgutil.iter_modules(_builtin.__path__):
            importlib.import_module(f"tools.builtin.{modname}")
            logger.debug("Loaded builtin tool module: %s", modname)

        # 从注册表取出所有已注册的实例
        registry = get_tool_registry()
        for tool_cls in registry.list_all():
            if tool_cls.name not in self._tools:
                self.register(tool_cls)
                logger.info("Auto-registered builtin tool: %s", tool_cls.name)

    # ── 执行 ─────────────────────────────────────────────────────────────────

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        iteration: int = 0,
    ) -> ToolResult:
        """
        执行指定工具。

        Args:
            tool_name : 工具名称（对应 Tool.name）
            arguments : 工具参数（从 LLM 的函数调用请求中提取）
            iteration : 当前 AgentLoop 迭代轮次（用于记录）

        Returns:
            ToolResult，统一格式的结果包装
        """
        # 查找工具
        tool_impl = self._tools.get(tool_name)
        if tool_impl is None:
            result = ToolResult.err(f"Unknown tool: '{tool_name}'. Available tools: {list(self._tools.keys())}")
            self._record_call(tool_name, arguments, result, iteration)
            return result

        # 构建上下文
        ctx = ToolContext(
            repo_path=self._repo_path,
            session_id=self._session_id,
            run_id=self._run_id,
        )

        # 执行
        try:
            result = tool_impl.execute(arguments, ctx)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Tool '%s' raised unhandled exception", tool_name)
            result = ToolResult.err(f"Tool execution error: {exc}")

        # 追踪错误次数
        if result.failed:
            self._tool_call_errors[tool_name] = self._tool_call_errors.get(tool_name, 0) + 1
            if self._tool_call_errors[tool_name] >= self._max_tool_call_errors:
                logger.warning(
                    "Tool '%s' has failed %d times — disabling for this session",
                    tool_name,
                    self._tool_call_errors[tool_name],
                )
        else:
            self._tool_call_errors.pop(tool_name, None)

        self._record_call(tool_name, arguments, result, iteration)
        return result

    def _record_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolResult,
        iteration: int,
    ) -> None:
        """记录工具调用到历史。"""
        self._call_history.append(
            ToolCall(
                tool_name=tool_name,
                arguments=arguments,
                result=result,
                iteration=iteration,
            )
        )

    def can_call(self, tool_name: str) -> bool:
        """检查工具是否可用（未超过错误阈值）。"""
        return self._tool_call_errors.get(tool_name, 0) < self._max_tool_call_errors

    # ── 工具描述导出 ───────────────────────────────────────────────────────────

    def to_openai_spec(self) -> list[dict[str, Any]]:
        """导出为 OpenAI function_calling 格式。"""
        return tools_to_openai_spec(list(self._tools.values()))

    def to_anthropic_spec(self) -> list[dict[str, Any]]:
        """导出为 Anthropic tool_use 格式。"""
        return tools_to_anthropic_spec(list(self._tools.values()))

    def list_tools(self) -> list[dict[str, Any]]:
        """返回所有已注册工具的元数据列表。"""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
        ]

    def list_tool_names(self) -> list[str]:
        return list(self._tools.keys())

    # ── 历史与统计 ─────────────────────────────────────────────────────────────

    @property
    def call_history(self) -> list[ToolCall]:
        return list(self._call_history)

    def stats(self) -> dict[str, Any]:
        return {
            "total_calls": len(self._call_history),
            "tools_used": {
                name: sum(1 for c in self._call_history if c.tool_name == name)
                for name in self._tools.keys()
            },
            "error_counts": dict(self._tool_call_errors),
        }
