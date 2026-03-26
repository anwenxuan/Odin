"""
tools/router.py — Tool Router

Tool Router 负责从自然语言请求或 LLM 意图中智能选择合适的工具。

核心职责：
1. 意图识别   ：将用户请求解析为工具调用意图
2. 工具匹配   ：基于语义相似度选择最合适的工具
3. 参数提取   ：从请求中提取工具参数
4. 危险检查   ：评估工具危险级别，决定是否需要沙箱
5. 工具编排   ：对于复杂任务，编排多个工具调用序列
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from agent.llm_adapter import LLMAdapter

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Tool Categories
# ─────────────────────────────────────────────────────────────────────────────


class ToolCategory(str, Enum):
    """工具分类。"""
    FILE = "file"
    SEARCH = "search"
    SHELL = "shell"
    GIT = "git"
    ANALYSIS = "analysis"
    HTTP = "http"
    CODE = "code"
    SANDBOX = "sandbox"
    VERIFICATION = "verification"
    UNKNOWN = "unknown"


@dataclass
class ToolIntent:
    """工具调用意图。"""
    tool_id: str
    tool_name: str
    confidence: float                    # 0.0 - 1.0
    category: ToolCategory
    params: dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""                  # 为什么选择这个工具
    danger_level: str = "safe"           # safe / caution / dangerous


# ─────────────────────────────────────────────────────────────────────────────
# Tool Registry (extended metadata)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ToolMetadata:
    """
    工具元数据（扩展版）。

    包含工具路由所需的所有元信息。
    """
    id: str
    name: str
    description: str
    category: ToolCategory
    parameters: dict[str, Any]            # JSON Schema
    returns: dict[str, Any]
    sandbox_required: bool = False
    danger_level: str = "safe"            # safe / caution / dangerous
    tags: list[str] = field(default_factory=list)
    rate_limit: int = 60                  # 每分钟调用次数
    version: str = "1.0.0"
    examples: list[str] = field(default_factory=list)
    score_keywords: list[str] = field(default_factory=list)  # 关键词评分

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def keyword_score(self, query: str) -> float:
        """基于关键词匹配计算相关性分数。"""
        query_lower = query.lower()
        score = 0.0
        matched = 0
        total = len(self.score_keywords)
        for kw in self.score_keywords:
            if kw.lower() in query_lower:
                score += 1.0
                matched += 1
        return score / max(total, 1)


# ─────────────────────────────────────────────────────────────────────────────
# Tool Router
# ─────────────────────────────────────────────────────────────────────────────


class ToolRouter:
    """
    工具路由器。

    支持两种路由模式：
    1. Direct Mode（直接模式）：已知工具名，直接调用
    2. Intent Mode（意图模式）：从自然语言请求中推断工具

    使用方式：
        router = ToolRouter(llm_adapter, registry)
        intent = await router.route_intent(
            "读取 src/main.py 文件的 1-20 行",
            available_tools=["read_file", "search_code", "run_shell"],
        )
        result = await router.execute_intent(intent, context)
    """

    SYSTEM_PROMPT = """You are a tool routing assistant. Given a user request and available tools, you must:
1. Identify the most appropriate tool for the request
2. Extract the correct parameters from the request
3. Assess the danger level (safe/caution/dangerous)
4. Provide reasoning for your choice

Return a JSON object with:
- "tool_id": the exact tool identifier
- "tool_name": the tool name
- "confidence": 0.0-1.0 confidence score
- "category": one of file/search/shell/git/analysis/http/code/sandbox/verification/unknown
- "params": extracted parameters for the tool
- "reasoning": why this tool was chosen
- "danger_level": safe/caution/dangerous

Available tools:
{tool_descriptions}

Request: "{query}"

Return ONLY the JSON object."""

    def __init__(
        self,
        llm_adapter: LLMAdapter,
        tool_metadata: dict[str, ToolMetadata] | None = None,
    ):
        self.llm = llm_adapter
        self._metadata: dict[str, ToolMetadata] = tool_metadata or {}

        # 内置工具注册表
        self._register_builtin_metadata()

    def _register_builtin_metadata(self) -> None:
        """注册内置工具的元数据。"""
        builtins = [
            ToolMetadata(
                id="read_file",
                name="read_file",
                description="Read the content of a file. Supports line range selection.",
                category=ToolCategory.FILE,
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path to read"},
                        "line_start": {"type": "integer", "description": "Start line (1-indexed)"},
                        "line_end": {"type": "integer", "description": "End line (inclusive)"},
                    },
                    "required": ["path"],
                },
                returns={},
                tags=["file", "read", "content"],
                score_keywords=["read", "file", "content", "show", "view", "display"],
            ),
            ToolMetadata(
                id="search_code",
                name="search_code",
                description="Search for code patterns using regex across the codebase.",
                category=ToolCategory.SEARCH,
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Regex pattern"},
                        "file_filter": {"type": "string", "description": "File glob filter (e.g., *.py)"},
                        "context_lines": {"type": "integer", "description": "Context lines before/after"},
                    },
                    "required": ["pattern"],
                },
                returns={},
                tags=["search", "grep", "regex", "find"],
                score_keywords=["search", "grep", "find", "pattern", "regex", "locate"],
            ),
            ToolMetadata(
                id="run_shell",
                name="run_shell",
                description="Execute a shell command in the repository.",
                category=ToolCategory.SHELL,
                parameters={
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Shell command to execute"},
                        "cwd": {"type": "string", "description": "Working directory"},
                    },
                    "required": ["command"],
                },
                returns={},
                sandbox_required=True,
                danger_level="caution",
                tags=["shell", "exec", "command", "run"],
                score_keywords=["run", "execute", "shell", "command", "bash", "script"],
            ),
            ToolMetadata(
                id="list_dir",
                name="list_dir",
                description="List directory contents as a tree view.",
                category=ToolCategory.FILE,
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path"},
                        "depth": {"type": "integer", "description": "Max tree depth"},
                    },
                },
                returns={},
                tags=["ls", "dir", "tree", "list"],
                score_keywords=["list", "dir", "ls", "tree", "directory", "files"],
            ),
            ToolMetadata(
                id="git_log",
                name="git_log",
                description="Get git commit history.",
                category=ToolCategory.GIT,
                parameters={
                    "type": "object",
                    "properties": {
                        "repo_path": {"type": "string"},
                        "n": {"type": "integer", "description": "Number of commits"},
                        "file": {"type": "string", "description": "Filter by file"},
                    },
                },
                returns={},
                tags=["git", "log", "history", "commit"],
                score_keywords=["git", "log", "commit", "history", "version"],
            ),
            ToolMetadata(
                id="git_diff",
                name="git_diff",
                description="Get git diff between commits or branches.",
                category=ToolCategory.GIT,
                parameters={
                    "type": "object",
                    "properties": {
                        "repo_path": {"type": "string"},
                        "from_ref": {"type": "string", "description": "From commit/branch"},
                        "to_ref": {"type": "string", "description": "To commit/branch"},
                    },
                },
                returns={},
                tags=["git", "diff", "change"],
                score_keywords=["diff", "git diff", "changes", "patch"],
            ),
            ToolMetadata(
                id="detect_lang",
                name="detect_lang",
                description="Detect programming languages and tech stack used in the codebase.",
                category=ToolCategory.ANALYSIS,
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Root path to scan"},
                    },
                },
                returns={},
                tags=["detect", "language", "stack"],
                score_keywords=["detect", "language", "tech", "stack", "framework", "what"],
            ),
        ]
        for m in builtins:
            self._metadata[m.id] = m

    def register(self, metadata: ToolMetadata) -> None:
        """注册工具元数据。"""
        self._metadata[metadata.id] = metadata

    def get(self, tool_id: str) -> ToolMetadata | None:
        return self._metadata.get(tool_id)

    def list_by_category(self, category: ToolCategory) -> list[ToolMetadata]:
        return [m for m in self._metadata.values() if m.category == category]

    def list_all(self) -> list[ToolMetadata]:
        return list(self._metadata.values())

    # ── Intent Routing ──────────────────────────────────────────────────────

    async def route_intent(
        self,
        query: str,
        available_tools: list[str] | None = None,
        context: str = "",
    ) -> ToolIntent | None:
        """
        从自然语言请求推断工具调用意图。

        Args:
            query         : 用户请求
            available_tools: 可用工具 ID 列表（None = 所有已注册工具）
            context       : 额外上下文

        Returns:
            ToolIntent 或 None
        """
        tools = available_tools or list(self._metadata.keys())
        tool_descs = []
        for tid in tools:
            meta = self._metadata.get(tid)
            if meta:
                tool_descs.append(f"- {meta.name}: {meta.description}")

        prompt = self.SYSTEM_PROMPT.format(
            tool_descriptions="\n".join(tool_descs) or "No tools available",
            query=query,
        )

        messages = [
            {"role": "system", "content": "You are a JSON-only tool routing assistant."},
            {"role": "user", "content": prompt},
        ]

        try:
            response = self.llm.chat(messages=messages, temperature=0.0, max_tokens=512)
            return self._parse_intent_response(response.content, query)
        except Exception:
            logger.exception("Intent routing failed")
            return None

    def route_direct(
        self,
        tool_id: str,
        params: dict[str, Any] | None = None,
    ) -> ToolIntent | None:
        """
        直接路由到指定工具（不需要 LLM）。

        Args:
            tool_id: 工具 ID
            params : 工具参数

        Returns:
            ToolIntent 或 None（工具不存在）
        """
        meta = self._metadata.get(tool_id)
        if not meta:
            return None

        return ToolIntent(
            tool_id=meta.id,
            tool_name=meta.name,
            confidence=1.0,
            category=meta.category,
            params=params or {},
            reasoning=f"Direct routing to {tool_id}",
            danger_level=meta.danger_level,
        )

    # ── Keyword Fallback Routing ───────────────────────────────────────────

    def route_by_keywords(self, query: str) -> ToolIntent | None:
        """
        基于关键词的快速路由（不需要 LLM）。

        用于简单请求的快速匹配。
        """
        scores: dict[str, float] = {}
        for tid, meta in self._metadata.items():
            score = meta.keyword_score(query)
            if score > 0:
                scores[tid] = score

        if not scores:
            return None

        best_id = max(scores, key=lambda k: scores[k])
        if scores[best_id] < 0.1:
            return None

        return self.route_direct(best_id)

    # ── Tool Call Sequences ────────────────────────────────────────────────

    async def plan_tool_sequence(
        self,
        task: str,
        context: str = "",
    ) -> list[ToolIntent]:
        """
        为复杂任务规划工具调用序列。

        Args:
            task   : 任务描述
            context: 当前上下文

        Returns:
            工具调用序列
        """
        prompt = f"""Given the following task, plan the sequence of tool calls needed to complete it.

Task: {task}
Context: {context}

Available tools:
{json.dumps([m.to_openai_schema()["function"] for m in self._metadata.values()], indent=2)}

Return a JSON array of tool calls with:
- "tool_id": tool identifier
- "params": parameters for each call
- "reasoning": why this tool is needed at this step

Example:
[
  {{"tool_id": "detect_lang", "params": {{}}, "reasoning": "First detect the tech stack"}},
  {{"tool_id": "list_dir", "params": {{"path": "."}}, "reasoning": "Explore project structure"}}
]"""

        messages = [
            {"role": "system", "content": "You are a tool planning assistant. Return JSON array only."},
            {"role": "user", "content": prompt},
        ]

        try:
            response = self.llm.chat(messages=messages, temperature=0.0, max_tokens=2048)
            return self._parse_sequence_response(response.content)
        except Exception:
            logger.exception("Tool sequence planning failed")
            return []

    # ── Internal Parsing ───────────────────────────────────────────────────

    def _parse_intent_response(
        self,
        raw: str,
        query: str,
    ) -> ToolIntent | None:
        """解析 LLM 返回的意图响应。"""
        raw = raw.strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            import re
            m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(1).strip())
                except json.JSONDecodeError:
                    data = {}
            else:
                data = {}

        tool_id = data.get("tool_id", "")
        if not tool_id:
            tool_id = data.get("tool_name", "")

        meta = self._metadata.get(tool_id)
        if not meta:
            return None

        category_str = data.get("category", "unknown")
        try:
            category = ToolCategory(category_str)
        except ValueError:
            category = ToolCategory.UNKNOWN

        return ToolIntent(
            tool_id=tool_id,
            tool_name=meta.name,
            confidence=float(data.get("confidence", 0.5)),
            category=category,
            params=data.get("params", {}),
            reasoning=data.get("reasoning", ""),
            danger_level=data.get("danger_level", meta.danger_level),
        )

    def _parse_sequence_response(self, raw: str) -> list[ToolIntent]:
        """解析工具序列响应。"""
        raw = raw.strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            import re
            m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(1).strip())
                except json.JSONDecodeError:
                    return []
            else:
                return []

        if not isinstance(data, list):
            return []

        intents: list[ToolIntent] = []
        for item in data:
            tool_id = item.get("tool_id", "")
            meta = self._metadata.get(tool_id)
            if meta:
                intents.append(ToolIntent(
                    tool_id=tool_id,
                    tool_name=meta.name,
                    confidence=float(item.get("confidence", 0.8)),
                    category=meta.category,
                    params=item.get("params", {}),
                    reasoning=item.get("reasoning", ""),
                    danger_level=meta.danger_level,
                ))
        return intents

    # ── Sandbox Decision ───────────────────────────────────────────────────

    def requires_sandbox(self, intent: ToolIntent) -> bool:
        """判断意图是否需要沙箱执行。"""
        meta = self._metadata.get(intent.tool_id)
        if meta:
            return meta.sandbox_required
        return intent.danger_level in {"caution", "dangerous"}
