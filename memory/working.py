"""
memory/working.py — WorkingMemory

三层 Memory 架构中的第一层：WorkingMemory（工作内存）。

WorkingMemory 是当前任务执行期间的高速缓存：
- 存储最近 N 步的执行记录（recent_steps）
- 临时 Evidence 缓存
- 当前 Plan 和 pending observations
- LLM 上下文窗口

特点：
- 全内存操作，速度最快
- 通过 to_session_memory() 定期持久化到 SessionMemory
- 通过 context_for_llm() 注入到 LLM prompt
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Step Summary
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class StepSummary:
    """
    步骤摘要 — 用于 WorkingMemory 的滚动窗口。

    是 StepRecord 的轻量版（不含完整 ToolCall），只保留关键信息。
    """
    step: int
    description: str
    observation: str
    evidence_refs: list[str]
    duration_ms: int
    status: str
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "description": self.description,
            "observation": self.observation[:200],
            "evidence_refs": self.evidence_refs,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error": self.error,
        }


# ─────────────────────────────────────────────────────────────────────────────
# WorkingMemory
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class WorkingMemory:
    """
    WorkingMemory — 当前任务的工作内存。

    位于三层 Memory 架构的第一层（最顶层），特点：
    - 全内存操作，零持久化开销
    - 固定大小的滚动窗口（recent_steps）
    - 定期持久化到 SessionMemory

    使用方式：
        memory = WorkingMemory(task_id="task-001", window_size=50)
        memory.add_step(...)
        memory.add_evidence(...)
        context = memory.get_context_for_llm()
    """

    def __init__(
        self,
        task_id: str,
        window_size: int = 50,
    ):
        self.task_id = task_id
        self.window_size = window_size

        # 最近步骤（固定大小滚动窗口）
        self.recent_steps: deque[StepSummary] = deque(maxlen=window_size)

        # 临时 Evidence 缓存（待持久化）
        self.temp_evidence: list[dict[str, Any]] = []

        # 当前执行计划
        self.current_plan: list[dict[str, Any]] = []

        # 待处理的观察结果
        self.pending_observations: list[str] = []

        # LLM 上下文窗口（最近消息摘要）
        self.context_window: deque[str] = deque(maxlen=10)

        # 元数据
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.last_persist_at: str | None = None
        self.step_count: int = 0

    # ── Step 管理 ─────────────────────────────────────────────────────────

    def add_step(self, record: StepSummary | dict[str, Any]) -> None:
        """添加步骤记录。"""
        if isinstance(record, dict):
            record = StepSummary(**record)
        self.recent_steps.append(record)
        self.step_count = len(self.recent_steps)

        # 更新上下文窗口
        self.context_window.append(
            f"[Step {record.step}] {record.description}: {record.status}"
        )

        logger.debug(
            "[WorkingMemory %s] Added step %d — total %d steps",
            self.task_id,
            record.step,
            self.step_count,
        )

    # ── Evidence 管理 ─────────────────────────────────────────────────────

    def add_evidence(self, evidence: dict[str, Any]) -> None:
        """添加临时 Evidence。"""
        self.temp_evidence.append(evidence)

    def add_evidence_batch(self, evidence_list: list[dict[str, Any]]) -> None:
        """批量添加 Evidence。"""
        self.temp_evidence.extend(evidence_list)

    def get_pending_evidence(self) -> list[dict[str, Any]]:
        """获取所有待持久化的 Evidence。"""
        return list(self.temp_evidence)

    def clear_pending_evidence(self) -> None:
        """清除已持久化的 Evidence。"""
        self.temp_evidence.clear()

    # ── Plan 管理 ─────────────────────────────────────────────────────────

    def set_plan(self, plan: list[dict[str, Any]]) -> None:
        """设置当前 Plan。"""
        self.current_plan = list(plan)

    def update_plan_status(
        self,
        action_id: str,
        status: str,
        result: Any = None,
    ) -> None:
        """更新 Plan 中某个 Action 的状态。"""
        for action in self.current_plan:
            if action.get("id") == action_id:
                action["status"] = status
                if result is not None:
                    action["result"] = result
                break

    # ── Context 生成 ───────────────────────────────────────────────────────

    def get_context_for_llm(self) -> str:
        """
        生成 LLM 可读的上下文摘要。

        包含：
        - 最近步骤摘要
        - 当前计划进度
        - Evidence 数量
        - 待处理观察
        """
        recent = "\n".join(
            f"  Step {s.step}: {s.description} → {s.status} "
            f"({s.duration_ms}ms) [refs: {len(s.evidence_refs)}]"
            for s in list(self.recent_steps)[-5:]
        )

        plan_progress = "\n".join(
            f"  - [{a.get('status', '?')}] {a.get('id', '?')}: {a.get('description', '')[:60]}"
            for a in self.current_plan[:5]
        )

        return (
            f"## Working Memory Context\n"
            f"Task: {self.task_id}\n"
            f"Steps completed: {self.step_count}\n\n"
            f"### Recent Steps\n{recent or '  (none)'}\n\n"
            f"### Current Plan\n{plan_progress or '  (no plan)'}\n\n"
            f"### Evidence\n"
            f"  Pending to persist: {len(self.temp_evidence)}\n\n"
            f"### Pending Observations\n"
            + ("\n".join(f"  - {obs}" for obs in self.pending_observations) or "  (none)")
        )

    def get_context_dict(self) -> dict[str, Any]:
        """生成用于注入的 dict 格式上下文。"""
        return {
            "task_id": self.task_id,
            "step_count": self.step_count,
            "recent_steps": [s.to_dict() for s in list(self.recent_steps)[-5:]],
            "current_plan": self.current_plan[:5],
            "evidence_count": len(self.temp_evidence),
            "pending_observations": self.pending_observations,
        }

    # ── 持久化 ───────────────────────────────────────────────────────────

    def to_session_memory_data(self) -> dict[str, Any]:
        """
        导出为 SessionMemory 可存储的格式。
        调用后应配合 clear_pending_evidence()。
        """
        return {
            "task_id": self.task_id,
            "steps": [s.to_dict() for s in self.recent_steps],
            "evidence": list(self.temp_evidence),
            "plan": list(self.current_plan),
            "step_count": self.step_count,
            "created_at": self.created_at,
            "last_persist_at": datetime.now(timezone.utc).isoformat(),
        }

    def load_from_session_data(self, data: dict[str, Any]) -> None:
        """从 SessionMemory 数据恢复。"""
        self.recent_steps = deque(
            [StepSummary(**s) for s in data.get("steps", [])],
            maxlen=self.window_size,
        )
        self.temp_evidence = list(data.get("evidence", []))
        self.current_plan = list(data.get("plan", []))
        self.step_count = data.get("step_count", 0)
        self.last_persist_at = data.get("last_persist_at")

    # ── 快照 ─────────────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """生成完整快照（用于 Checkpoint）。"""
        return {
            "task_id": self.task_id,
            "window_size": self.window_size,
            "recent_steps": [s.to_dict() for s in self.recent_steps],
            "temp_evidence": list(self.temp_evidence),
            "current_plan": list(self.current_plan),
            "pending_observations": list(self.pending_observations),
            "context_window": list(self.context_window),
            "step_count": self.step_count,
            "created_at": self.created_at,
            "last_persist_at": self.last_persist_at,
        }

    @classmethod
    def from_snapshot(
        cls,
        data: dict[str, Any],
        window_size: int = 50,
    ) -> "WorkingMemory":
        """从快照恢复 WorkingMemory。"""
        task_id = data.get("task_id", "unknown")
        memory = cls(task_id=task_id, window_size=window_size)

        memory.recent_steps = deque(
            [StepSummary(**s) for s in data.get("recent_steps", [])],
            maxlen=window_size,
        )
        memory.temp_evidence = list(data.get("temp_evidence", []))
        memory.current_plan = list(data.get("current_plan", []))
        memory.pending_observations = list(data.get("pending_observations", []))
        memory.context_window = deque(
            data.get("context_window", []),
            maxlen=10,
        )
        memory.step_count = data.get("step_count", 0)
        memory.last_persist_at = data.get("last_persist_at")
        return memory

    # ── 统计 ─────────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "window_size": self.window_size,
            "step_count": self.step_count,
            "pending_evidence": len(self.temp_evidence),
            "plan_items": len(self.current_plan),
            "pending_observations": len(self.pending_observations),
        }


# ─────────────────────────────────────────────────────────────────────────────
# MemoryContext — 跨组件传递的 Memory 引用
# ─────────────────────────────────────────────────────────────────────────────


class MemoryContext:
    """
    跨组件传递的 Memory 引用包装器。

    AgentRuntime、ToolExecutor、Observer 等共享同一个 MemoryContext，
    以便在同一任务内共享状态。
    """

    def __init__(
        self,
        task_id: str,
        working: WorkingMemory | None = None,
    ):
        self.task_id = task_id
        self.working = working or WorkingMemory(task_id=task_id)
        self._sessions: dict[str, Any] = {}   # session_id → session data
        self._longterm_refs: dict[str, Any] = {}  # key → ref

    def get_working(self) -> WorkingMemory:
        return self.working

    def store_session(self, key: str, value: Any) -> None:
        self._sessions[key] = value

    def get_session(self, key: str, default: Any = None) -> Any:
        return self._sessions.get(key, default)

    def store_longterm(self, key: str, value: Any) -> None:
        self._longterm_refs[key] = value

    def get_longterm(self, key: str) -> Any | None:
        return self._longterm_refs.get(key)
