"""
tools/base.py — Tool 接口定义

核心抽象：
- Tool       : 所有工具必须实现的 Protocol
- ToolResult : 工具执行结果的统一包装
- ToolContext: 工具执行时的运行时上下文（repo_path / session_id 等）
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Protocol, runtime_checkable
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# ToolResult — 工具执行结果的统一包装
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ToolResult:
    """
    工具执行结果的统一返回格式。

    无论工具内部成功或失败，都返回 ToolResult，
    而不是抛出异常（异常由 ToolExecutor 统一处理）。
    """
    success: bool
    output: str = ""                    # 人类可读的输出文本
    error: str | None = None            # 错误信息（success=False 时）
    metadata: dict[str, Any] = field(default_factory=dict)  # 扩展元数据

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def failed(self) -> bool:
        return not self.success

    @classmethod
    def ok(cls, output: str, **metadata: Any) -> "ToolResult":
        return cls(success=True, output=output, metadata=metadata)

    @classmethod
    def err(cls, error: str, **metadata: Any) -> "ToolResult":
        return cls(success=False, output="", error=error, metadata=metadata)


# ─────────────────────────────────────────────────────────────────────────────
# ToolContext — 工具执行时的运行时上下文
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ToolContext:
    """
    工具执行时的运行时上下文。

    由 ToolExecutor 在每次调用时构建并传入工具。
    """
    repo_path: Path | None = None       # 仓库根目录
    session_id: str = ""                # 本次研究会话 ID
    run_id: str = ""                    # 当前 WorkflowRun ID
    skill_id: str = ""                  # 当前执行中的 Skill ID
    custom: dict[str, Any] = field(default_factory=dict)  # 扩展字段

    def resolve_path(self, rel_path: str) -> Path | None:
        """将相对路径解析为绝对路径。"""
        if self.repo_path is None:
            return None
        p = (self.repo_path / rel_path).resolve()
        # 安全检查：不允许路径逃逸到 repo 外部
        try:
            p.relative_to(self.repo_path.resolve())
        except ValueError:
            return None
        return p

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_path": str(self.repo_path) if self.repo_path else None,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "skill_id": self.skill_id,
            **self.custom,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Tool — 工具接口（Protocol）
# ─────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class Tool(Protocol):
    """
    所有工具必须实现的接口。

    实现类应添加 @tool 装饰器以自动注册到 ToolExecutor。

    示例：
        @tool
        class ReadFileTool:
            name = "read_file"
            description = "读取文件内容..."
            input_schema = {...}

            def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
                ...
    """

    # ── 元数据 ────────────────────────────────────────────────────────────────

    name: str          # 工具唯一标识，LLM 用此名称发起调用
    description: str   # LLM 可读的工具描述，说明用途和用法
    input_schema: dict[str, Any]   # JSON Schema，描述参数结构

    # ── 执行方法 ──────────────────────────────────────────────────────────────

    def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """
        执行工具逻辑。

        Args:
            args: LLM 传入的参数（已通过 input_schema 校验）
            ctx : 运行时上下文

        Returns:
            ToolResult，统一格式的结果包装
        """
        ...


# ─────────────────────────────────────────────────────────────────────────────
# 内置工具列表 — 生成 OpenAI/Anthropic Function Calling 格式
# ─────────────────────────────────────────────────────────────────────────────

def tools_to_openai_spec(tools: list[type[Tool]]) -> list[dict[str, Any]]:
    """将 Tool 类列表转换为 OpenAI function_calling 格式。"""
    spec = []
    for t in tools:
        spec.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        })
    return spec


def tools_to_anthropic_spec(tools: list[type[Tool]]) -> list[dict[str, Any]]:
    """将 Tool 类列表转换为 Anthropic tool_use 格式。"""
    spec = []
    for t in tools:
        spec.append({
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        })
    return spec
