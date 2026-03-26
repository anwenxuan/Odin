"""
agent/task_manager/queue.py — Task Queue

基于优先级的持久化任务队列。

使用 SQLite 作为底层存储，支持：
- 优先级排序（FIFO + Priority）
- Task 持久化（重启后不丢失）
- 批量入队/出队
- 任务取消与删除
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.task_manager.state import Task, TaskState, TaskPriority

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Queue Entry
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class QueueEntry:
    """队列条目。"""
    task: Task
    priority: int                    # 数字越小优先级越高
    enqueued_at: str
    position: int = 0               # 在同优先级内的顺序

    def to_tuple(self) -> tuple[str, str, int, str, int]:
        return (
            self.task.id,
            self.task.to_json(),
            self.priority,
            self.enqueued_at,
            self.position,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Task Queue
# ─────────────────────────────────────────────────────────────────────────────


class TaskQueue:
    """
    持久化优先级任务队列。

    底层使用 SQLite，按 priority ASC, enqueued_at ASC 排序。
    同一优先级的任务按入队时间 FIFO。

    使用方式：
        queue = TaskQueue(".odin/tasks.db")
        task = Task.create("分析 SQL 注入", inputs={"repo": "my-repo"})
        queue.enqueue(task, priority=TaskPriority.HIGH)

        while True:
            entry = queue.dequeue()
            if entry is None:
                break
            process(entry.task)
    """

    def __init__(
        self,
        db_path: str | Path = ".odin/task_queue.db",
        max_size: int = 10000,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_size = max_size
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ── 数据库初始化 ─────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        """初始化数据库 schema。"""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_queue (
                task_id     TEXT PRIMARY KEY,
                task_json   TEXT NOT NULL,
                priority    INTEGER NOT NULL,
                enqueued_at TEXT NOT NULL,
                position    INTEGER NOT NULL,
                cancelled   INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_priority_enqueued
            ON task_queue (priority ASC, enqueued_at ASC)
            WHERE cancelled = 0
        """)
        conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    # ── 入队 ─────────────────────────────────────────────────────────────────

    def enqueue(
        self,
        task: Task,
        priority: int | TaskPriority | None = None,
    ) -> QueueEntry:
        """
        将 Task 入队。

        Args:
            task    : 要入队的 Task
            priority: 优先级（int 或 TaskPriority）

        Returns:
            QueueEntry
        """
        task.enqueue()
        priority_val = int(priority.value if isinstance(priority, TaskPriority) else (priority or TaskPriority.NORMAL.value))
        enqueued_at = datetime.now(timezone.utc).isoformat()

        # 获取当前位置（同一优先级的下一个位置）
        conn = self._get_conn()
        cur = conn.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM task_queue WHERE priority = ? AND cancelled = 0",
            (priority_val,),
        )
        position = cur.fetchone()[0]

        entry = QueueEntry(
            task=task,
            priority=priority_val,
            enqueued_at=enqueued_at,
            position=position,
        )

        conn.execute(
            """
            INSERT OR REPLACE INTO task_queue
            (task_id, task_json, priority, enqueued_at, position, cancelled)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (task.id, task.to_json(), priority_val, enqueued_at, position),
        )
        conn.commit()

        logger.info(
            "[TaskQueue] Enqueued task=%s priority=%d position=%d",
            task.id,
            priority_val,
            position,
        )
        return entry

    def enqueue_batch(self, entries: list[QueueEntry]) -> int:
        """批量入队。"""
        conn = self._get_conn()
        conn.executemany(
            """
            INSERT OR REPLACE INTO task_queue
            (task_id, task_json, priority, enqueued_at, position, cancelled)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            [e.to_tuple() for e in entries],
        )
        conn.commit()
        logger.info("[TaskQueue] Batch enqueued %d tasks", len(entries))
        return len(entries)

    # ── 出队 ─────────────────────────────────────────────────────────────────

    def dequeue(self, worker_id: str | None = None) -> QueueEntry | None:
        """
        取出最高优先级、最早入队的 Task。

        Args:
            worker_id: 可选的 Worker ID（用于记录）

        Returns:
            QueueEntry 或 None（队列为空）
        """
        conn = self._get_conn()
        row = conn.execute(
            """
            SELECT task_id, task_json, priority, enqueued_at, position
            FROM task_queue
            WHERE cancelled = 0
            ORDER BY priority ASC, enqueued_at ASC
            LIMIT 1
            """,
        ).fetchone()

        if row is None:
            return None

        task = Task.from_dict(json.loads(row["task_json"]))
        conn.execute(
            "DELETE FROM task_queue WHERE task_id = ?",
            (row["task_id"],)
        )
        conn.commit()

        logger.info(
            "[TaskQueue] Dequeued task=%s priority=%d",
            task.id,
            row["priority"],
        )
        return QueueEntry(
            task=task,
            priority=row["priority"],
            enqueued_at=row["enqueued_at"],
            position=row["position"],
        )

    def peek(self, n: int = 1) -> list[QueueEntry]:
        """查看队列前 N 个条目（不删除）。"""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT task_id, task_json, priority, enqueued_at, position
            FROM task_queue
            WHERE cancelled = 0
            ORDER BY priority ASC, enqueued_at ASC
            LIMIT ?
            """,
            (n,),
        ).fetchall()
        entries = []
        for row in rows:
            task = Task.from_dict(json.loads(row["task_json"]))
            entries.append(QueueEntry(
                task=task,
                priority=row["priority"],
                enqueued_at=row["enqueued_at"],
                position=row["position"],
            ))
        return entries

    # ── 队列操作 ─────────────────────────────────────────────────────────────

    def cancel(self, task_id: str) -> bool:
        """标记 Task 为已取消（软删除）。"""
        conn = self._get_conn()
        conn.execute(
            "UPDATE task_queue SET cancelled = 1 WHERE task_id = ?",
            (task_id,),
        )
        conn.commit()
        logger.info("[TaskQueue] Cancelled task=%s", task_id)
        return conn.total_changes > 0

    def remove(self, task_id: str) -> bool:
        """彻底从队列中删除 Task。"""
        conn = self._get_conn()
        conn.execute("DELETE FROM task_queue WHERE task_id = ?", (task_id,))
        conn.commit()
        return conn.total_changes > 0

    def requeue(self, task: Task) -> QueueEntry:
        """将 Task 重新入队（通常用于失败重试）。"""
        return self.enqueue(task)

    # ── 队列状态 ─────────────────────────────────────────────────────────────

    def size(self) -> int:
        """队列中的 Task 数量。"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COUNT(*) FROM task_queue WHERE cancelled = 0"
        ).fetchone()
        return row[0] if row else 0

    def is_empty(self) -> bool:
        return self.size() == 0

    def size_by_priority(self) -> dict[int, int]:
        """各优先级的 Task 数量。"""
        conn = self._get_conn()
        rows = conn.execute(
            """
            SELECT priority, COUNT(*) as cnt
            FROM task_queue
            WHERE cancelled = 0
            GROUP BY priority
            ORDER BY priority ASC
            """
        ).fetchall()
        return {row["priority"]: row["cnt"] for row in rows}

    def clear(self) -> int:
        """清空队列（慎用）。"""
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM task_queue WHERE cancelled = 0")
        conn.commit()
        count = cur.rowcount
        logger.warning("[TaskQueue] Cleared %d tasks", count)
        return count

    def __len__(self) -> int:
        return self.size()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
