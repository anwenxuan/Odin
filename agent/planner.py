"""
agent/planner.py — Task Planner

Planner 负责将复杂任务自动拆解为可执行的 Action 序列。
基于 LLM 实现，支持：
- 任务理解与分解
- Action 序列生成
- 依赖关系分析
- 优先级排序
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.runtime import RuntimeConfig

from agent.llm_adapter import LLMAdapter

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Action & Plan Types
# ─────────────────────────────────────────────────────────────────────────────


class ActionType(str, Enum):
    """Action 类型枚举。"""
    READ_FILE = "read_file"
    SEARCH_CODE = "search_code"
    RUN_SHELL = "run_shell"
    GIT_OPERATION = "git_operation"
    ANALYZE = "analyze"
    SYNTHESIZE = "synthesize"
    REPORT = "report"
    VERIFY = "verify"
    PLAN = "plan"
    UNKNOWN = "unknown"


class ActionPriority(int, Enum):
    """Action 优先级（数字越小优先级越高）。"""
    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4


@dataclass
class Action:
    """
    单个可执行 Action。

    代表 Planner 生成的一个步骤，包含：
    - type      : Action 类型
    - description: 自然语言描述
    - params    : 执行参数
    - depends_on: 依赖的其他 Action ID
    - priority  : 执行优先级
    """
    id: str
    type: ActionType
    description: str
    params: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    priority: ActionPriority = ActionPriority.MEDIUM
    status: str = "pending"          # pending | running | completed | failed
    result: Any = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "description": self.description,
            "params": self.params,
            "depends_on": self.depends_on,
            "priority": self.priority.value,
            "status": self.status,
            "result": self.result,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Action":
        at = ActionType(data.get("type", ActionType.UNKNOWN))
        try:
            priority = ActionPriority(data.get("priority", ActionPriority.MEDIUM.value))
        except ValueError:
            priority = ActionPriority.MEDIUM
        return cls(
            id=data["id"],
            type=at,
            description=data.get("description", ""),
            params=data.get("params", {}),
            depends_on=data.get("depends_on", []),
            priority=priority,
            status=data.get("status", "pending"),
            result=data.get("result"),
            error=data.get("error"),
        )


@dataclass
class TaskDecomposition:
    """
    任务分解结果。

    包含：Action 序列、分解摘要、原始任务描述。
    """
    task_description: str
    actions: list[Action] = field(default_factory=list)
    summary: str = ""
    reasoning: str = ""
    estimated_steps: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def get_ready_actions(self) -> list[Action]:
        """获取所有依赖已满足、可以执行的 Action。"""
        completed_ids = {a.id for a in self.actions if a.status == "completed"}
        ready = []
        for action in self.actions:
            if action.status != "pending":
                continue
            deps_met = all(dep_id in completed_ids for dep_id in action.depends_on)
            if deps_met:
                ready.append(action)
        return sorted(ready, key=lambda a: a.priority.value)

    def all_completed(self) -> bool:
        """检查是否所有 Action 都已完成。"""
        return all(a.status == "completed" for a in self.actions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_description": self.task_description,
            "actions": [a.to_dict() for a in self.actions],
            "summary": self.summary,
            "reasoning": self.reasoning,
            "estimated_steps": self.estimated_steps,
            "created_at": self.created_at,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Planner
# ─────────────────────────────────────────────────────────────────────────────


class Planner:
    """
    任务 Planner。

    使用 LLM 将复杂任务分解为有序的 Action 序列。
    支持 Action 依赖关系分析和优先级排序。

    使用方式：
        planner = Planner(llm_adapter)
        decomposition = await planner.decompose(
            task="分析这个代码仓库中的 SQL 注入漏洞",
            context="repo: python/django, 100 files",
            tools=["read_file", "search_code", "run_shell"],
        )
    """

    SYSTEM_PROMPT = """You are an expert AI task planner. Your job is to break down complex software engineering tasks into a sequence of precise, executable actions.

For each task, you must:
1. Understand the user's goal
2. Break it down into atomic, ordered actions
3. Identify dependencies between actions
4. Set appropriate priorities

Available action types:
- read_file: Read source code files to gather evidence
- search_code: Search for patterns in code using regex
- run_shell: Execute shell commands (git, grep, find, etc.)
- git_operation: Perform git operations (clone, log, diff)
- analyze: Analyze gathered information and draw conclusions
- synthesize: Combine multiple findings into structured output
- report: Generate final report
- verify: Verify findings with additional checks

Rules:
- Every action must be specific and actionable
- Actions that depend on others must list those dependencies
- Set priority based on importance (CRITICAL=1, HIGH=2, MEDIUM=3, LOW=4)
- Aim for 3-10 actions for most tasks
- Prioritize evidence gathering before analysis
"""

    USER_TEMPLATE = """## Task
{task}

## Context
{context}

## Available Tools
{tools}

## Output Format
Return a JSON object with:
- "summary": Brief description of the decomposition approach
- "reasoning": Why these actions are needed in this order
- "estimated_steps": Total number of actions
- "actions": Array of action objects with:
  - "id": Unique action ID (e.g., "action-1")
  - "type": One of the action types above
  - "description": Clear description of what to do
  - "params": Parameters for the action (empty if none)
  - "depends_on": Array of action IDs this depends on (empty if none)
  - "priority": 1-4 (1=CRITICAL, 2=HIGH, 3=MEDIUM, 4=LOW)

Return ONLY the JSON object, no additional text."""

    def __init__(
        self,
        llm_adapter: LLMAdapter,
        config: "RuntimeConfig | None" = None,
    ):
        self.llm = llm_adapter
        if config is None:
            from agent.runtime import RuntimeConfig
            config = RuntimeConfig()
        self.config = config

    async def decompose(
        self,
        task: str,
        context: str = "",
        tools: list[str] | None = None,
    ) -> TaskDecomposition:
        """
        将任务分解为 Action 序列。

        Args:
            task    : 任务描述
            context : 当前上下文（已有信息、代码结构等）
            tools   : 可用工具列表

        Returns:
            TaskDecomposition，包含 Action 序列
        """
        tools_str = ", ".join(tools) if tools else "all available tools"

        user_prompt = self.USER_TEMPLATE.format(
            task=task,
            context=context or "(no additional context provided)",
            tools=tools_str,
        )

        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        try:
            response = self.llm.chat(
                messages=messages,
                tools=None,
                model=self.config.planner_model,
                temperature=0.0,
                max_tokens=4096,
            )
            return self._parse_decomposition(task, response.content)
        except Exception as exc:
            logger.exception("Planner LLM call failed")
            return TaskDecomposition(
                task_description=task,
                summary="Planning failed, using default sequential approach",
                reasoning=str(exc),
                estimated_steps=1,
            )

    def _parse_decomposition(
        self,
        task_description: str,
        raw_response: str,
    ) -> TaskDecomposition:
        """解析 LLM 响应，构造 TaskDecomposition。"""
        actions: list[Action] = []
        summary = ""
        reasoning = ""
        estimated_steps = 0

        # 尝试解析 JSON
        raw = raw_response.strip()
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

        summary = data.get("summary", "")
        reasoning = data.get("reasoning", "")
        estimated_steps = int(data.get("estimated_steps", 0))

        for action_data in data.get("actions", []):
            try:
                action = Action.from_dict(action_data)
                actions.append(action)
            except Exception:
                logger.warning("Failed to parse action: %s", action_data)

        logger.info(
            "Planner decomposed task into %d actions: %s",
            len(actions),
            summary[:80],
        )

        return TaskDecomposition(
            task_description=task_description,
            actions=actions,
            summary=summary,
            reasoning=reasoning,
            estimated_steps=estimated_steps,
        )

    async def refine_plan(
        self,
        decomposition: TaskDecomposition,
        feedback: str,
    ) -> TaskDecomposition:
        """
        基于反馈重新规划。

        Args:
            decomposition: 当前分解结果
            feedback     : 反馈信息（如某个 Action 失败）

        Returns:
            更新后的 TaskDecomposition
        """
        prompt = f"""## Current Plan
{json.dumps(decomposition.to_dict(), ensure_ascii=False, indent=2)}

## Feedback
{feedback}

## Task
Revise the plan based on the feedback. You can:
- Remove failed or unnecessary actions
- Add new actions to address the issue
- Reorder actions based on new understanding
- Adjust dependencies

Return a new JSON object with the same format as before."""

        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            response = self.llm.chat(messages=messages, temperature=0.0, max_tokens=4096)
            new_plan = self._parse_decomposition(
                decomposition.task_description,
                response.content,
            )
            # 保留已完成 Action 的状态
            completed_ids = {
                a.id for a in decomposition.actions if a.status == "completed"
            }
            for action in new_plan.actions:
                if action.id in completed_ids:
                    action.status = "completed"
            return new_plan
        except Exception as exc:
            logger.warning("Plan refinement failed: %s", exc)
            return decomposition
