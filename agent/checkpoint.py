"""
agent/checkpoint.py — Checkpoint Manager

Checkpoint 用于在长时间运行的 Agent 任务中定期保存执行状态，
支持从任意 Checkpoint 恢复继续执行，防止任务中断导致的工作丢失。

Checkpoint 包含：
- 当前 Agent RuntimeState
- WorkingMemory 的完整快照
- 已收集的 MEU / Evidence
- 工具调用历史
- 当前 Plan

存储格式：JSON Lines（append-only，保留完整历史）
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.runtime import RuntimeState

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Checkpoint record
# ─────────────────────────────────────────────────────────────────────────────


class CheckpointRecord:
    """
    单个 Checkpoint 记录。

    包含：版本号、创建时间、完整状态快照、关联的 evidence_refs。
    """

    VERSION = 1

    def __init__(
        self,
        task_id: str,
        step: int,
        state_snapshot: dict[str, Any],
        evidence_refs: list[str],
        tool_history: list[dict[str, Any]],
        memory_snapshot: dict[str, Any],
        plan_snapshot: list[dict[str, Any]],
        checkpoint_id: str | None = None,
    ):
        self.checkpoint_id = (
            checkpoint_id
            or f"ckpt-{task_id}-{step}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        )
        self.task_id = task_id
        self.step = step
        self.state_snapshot = state_snapshot
        self.evidence_refs = list(evidence_refs)
        self.tool_history = list(tool_history)
        self.memory_snapshot = memory_snapshot
        self.plan_snapshot = list(plan_snapshot)
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.version = self.VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "task_id": self.task_id,
            "step": self.step,
            "version": self.version,
            "timestamp": self.timestamp,
            "state_snapshot": self.state_snapshot,
            "evidence_refs": self.evidence_refs,
            "tool_history": self.tool_history,
            "memory_snapshot": self.memory_snapshot,
            "plan_snapshot": self.plan_snapshot,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CheckpointRecord":
        return cls(
            checkpoint_id=data["checkpoint_id"],
            task_id=data["task_id"],
            step=data["step"],
            state_snapshot=data["state_snapshot"],
            evidence_refs=data["evidence_refs"],
            tool_history=data["tool_history"],
            memory_snapshot=data["memory_snapshot"],
            plan_snapshot=data["plan_snapshot"],
        )


# ─────────────────────────────────────────────────────────────────────────────
# Checkpoint Manager
# ─────────────────────────────────────────────────────────────────────────────


class CheckpointManager:
    """
    Checkpoint 管理器。

    职责：
    1. save()    — 在指定 step 保存完整状态快照
    2. restore() — 从 checkpoint_id 恢复 RuntimeState
    3. list()    — 列出某个 task 的所有 checkpoint
    4. latest()  — 获取最新 checkpoint
    5. prune()   — 删除旧 checkpoint（保留最近 N 个）

    存储结构（每个 task 一个目录）：
        checkpoints/
        └── {task_id}/
            ├── index.jsonl     ← 所有 checkpoint 的索引
            └── ckpt-{step}.json ← 每个 checkpoint 的完整数据
    """

    def __init__(self, storage_dir: str | Path = ".odin/checkpoints"):
        self.storage_dir = Path(storage_dir)

    # ── 核心操作 ──────────────────────────────────────────────────────────────

    def save(
        self,
        state: "RuntimeState",
        memory_snapshot: dict[str, Any],
        plan_snapshot: list[dict[str, Any]],
        auto: bool = True,
    ) -> str:
        """
        保存当前执行状态为 Checkpoint。

        Args:
            state          : 当前 RuntimeState
            memory_snapshot: WorkingMemory 的序列化快照
            plan_snapshot  : 当前 Plan 的序列化列表
            auto           : 是否为自动保存（用于区分手动/自动 checkpoint）

        Returns:
            新建 Checkpoint 的 checkpoint_id
        """
        task_dir = self._task_dir(state.task_id)
        task_dir.mkdir(parents=True, exist_ok=True)

        record = CheckpointRecord(
            task_id=state.task_id,
            step=state.step,
            state_snapshot=self._serialize_state(state),
            evidence_refs=[e.meu_id for e in state.evidence],
            tool_history=[
                tc.to_dict() if hasattr(tc, "to_dict") else tc
                for tc in state.tool_history
            ],
            memory_snapshot=memory_snapshot,
            plan_snapshot=plan_snapshot,
        )

        # 写 checkpoint 文件
        ckpt_path = task_dir / f"{record.checkpoint_id}.json"
        with open(ckpt_path, "w", encoding="utf-8") as f:
            json.dump(record.to_dict(), f, ensure_ascii=False, indent=2)

        # 更新索引（append-only）
        index_path = task_dir / "index.jsonl"
        with open(index_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

        logger.info(
            "[%s] Checkpoint saved: step=%d id=%s auto=%s",
            state.task_id,
            record.step,
            record.checkpoint_id,
            auto,
        )
        return record.checkpoint_id

    def restore(self, task_id: str, checkpoint_id: str) -> CheckpointRecord:
        """
        从指定 Checkpoint 恢复。

        Args:
            task_id     : Task ID
            checkpoint_id: Checkpoint ID

        Returns:
            CheckpointRecord，包含完整恢复所需的数据

        Raises:
            FileNotFoundError: Checkpoint 不存在
        """
        ckpt_path = self._task_dir(task_id) / f"{checkpoint_id}.json"
        if not ckpt_path.exists():
            raise FileNotFoundError(
                f"Checkpoint not found: {ckpt_path}"
            )
        with open(ckpt_path, encoding="utf-8") as f:
            data = json.load(f)
        record = CheckpointRecord.from_dict(data)
        logger.info(
            "[%s] Checkpoint restored: step=%d id=%s",
            task_id,
            record.step,
            checkpoint_id,
        )
        return record

    def latest(self, task_id: str) -> CheckpointRecord | None:
        """获取某个 task 最新的 Checkpoint。"""
        index_path = self._task_dir(task_id) / "index.jsonl"
        if not index_path.exists():
            return None
        last_line: str | None = None
        with open(index_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last_line = line
        if not last_line:
            return None
        return CheckpointRecord.from_dict(json.loads(last_line))

    def list(self, task_id: str) -> list[CheckpointRecord]:
        """列出某个 task 的所有 Checkpoint（按时间正序）。"""
        index_path = self._task_dir(task_id) / "index.jsonl"
        if not index_path.exists():
            return []
        records: list[CheckpointRecord] = []
        with open(index_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(CheckpointRecord.from_dict(json.loads(line)))
        return records

    def prune(self, task_id: str, keep_last: int = 5) -> list[str]:
        """
        清理旧 Checkpoint，保留最近 N 个。

        Returns:
            被删除的 checkpoint_id 列表
        """
        all_records = self.list(task_id)
        if len(all_records) <= keep_last:
            return []

        to_delete = all_records[:-keep_last]
        task_dir = self._task_dir(task_id)
        deleted: list[str] = []

        for record in to_delete:
            ckpt_path = task_dir / f"{record.checkpoint_id}.json"
            if ckpt_path.exists():
                ckpt_path.unlink()
                deleted.append(record.checkpoint_id)

        # 重写索引
        index_path = task_dir / "index.jsonl"
        remaining = all_records[-keep_last:]
        with open(index_path, "w", encoding="utf-8") as f:
            for record in remaining:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

        logger.info(
            "[%s] Pruned %d checkpoints, kept %d",
            task_id,
            len(deleted),
            keep_last,
        )
        return deleted

    def restore_state_from_record(
        self,
        record: CheckpointRecord,
    ) -> dict[str, Any]:
        """
        从 CheckpointRecord 重建可用的 state_dict。
        供 RuntimeState.from_checkpoint() 使用。
        """
        return {
            "task_id": record.task_id,
            "step": record.step,
            "tool_history": record.tool_history,
            "evidence_refs": record.evidence_refs,
            "memory_snapshot": record.memory_snapshot,
            "plan_snapshot": record.plan_snapshot,
            "state_snapshot": record.state_snapshot,
        }

    # ── 内部工具 ───────────────────────────────────────────────────────────────

    def _task_dir(self, task_id: str) -> Path:
        return self.storage_dir / task_id

    def _serialize_state(self, state: "RuntimeState") -> dict[str, Any]:
        """将 RuntimeState 序列化为可存储的 dict。"""
        if hasattr(state, "to_dict"):
            return state.to_dict()
        result: dict[str, Any] = {}
        for key, value in asdict(state).items():
            if is_dataclass(value) and not isinstance(value, type):
                result[key] = asdict(value)
            elif isinstance(value, list) and value and is_dataclass(value[0]):
                result[key] = [asdict(v) for v in value]
            else:
                result[key] = value
        return result


# ─────────────────────────────────────────────────────────────────────────────
# Checkpoint-aware Loop Result
# ─────────────────────────────────────────────────────────────────────────────


def save_step_record(
    task_id: str,
    step: int,
    action: str,
    tool_calls: list[dict[str, Any]],
    observation: str,
    evidence_refs: list[str],
    duration_ms: int,
    output_dir: str | Path = ".odin/logs",
) -> Path:
    """
    快速保存单个 step 的记录到 JSONL 文件。
    用于高频日志（不经过 Checkpoint Manager）。
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    step_file = output_dir / f"{task_id}.steps.jsonl"
    record = {
        "task_id": task_id,
        "step": step,
        "action": action,
        "tool_calls": tool_calls,
        "observation": observation,
        "evidence_refs": evidence_refs,
        "duration_ms": duration_ms,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(step_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return step_file
