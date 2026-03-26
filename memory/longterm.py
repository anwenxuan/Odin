"""
memory/longterm.py — LongTermMemory & RAG Integration

三层 Memory 架构中的第三层：LongTermMemory（长期记忆）。

LongTermMemory 负责：
1. 持久化历史任务摘要（completed_task_summaries）
2. 存储已验证结论（proven_conclusions）
3. 可复用知识检索（RAG，基于 SQLite FTS5）
4. 跨任务上下文注入

与现有模块的关系：
- 复用 rag/store.py 的 SQLite FTS5 引擎
- 复用 memory/evidence_store.py 的 MEU 存储
- 复用 memory/memory_store.py 的 Artifacts 存储
- 为 AgentRuntime 提供"历史经验"检索能力
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rag.store import RAGStore, SearchResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Knowledge Types
# ─────────────────────────────────────────────────────────────────────────────


class KnowledgeType(str):
    TASK_SUMMARY = "task_summary"
    PROVEN_CONCLUSION = "proven_conclusion"
    REUSABLE_PATTERN = "reusable_pattern"
    VULNERABILITY_PATTERN = "vulnerability_pattern"
    ARCHITECTURE_PATTERN = "architecture_pattern"


@dataclass
class TaskSummary:
    """
    历史任务摘要。

    每个完成的任务持久化后生成此摘要，供未来相似任务参考。
    """
    summary_id: str
    task_type: str                          # vulnerability_research / code_analysis 等
    repo_language: str = ""                  # 代码语言
    repo_size: str = ""                     # small / medium / large
    duration_seconds: float = 0.0
    steps_completed: int = 0
    conclusions_count: int = 0
    evidence_count: int = 0
    key_findings: list[str] = field(default_factory=list)  # 关键发现摘要
    tools_used: list[str] = field(default_factory=list)
    verification_passed: bool = False
    score: float = 0.0                      # 任务评分（A/B/C/D/F → 1.0/0.8/0.6/0.4/0.2）
    tags: list[str] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskSummary":
        return cls(**data)

    def relevance_score(self, query: str) -> float:
        """计算与查询的相关性分数（简单关键词匹配）。"""
        text = " ".join([
            self.task_type,
            self.repo_language,
            " ".join(self.key_findings),
            " ".join(self.tags),
        ]).lower()
        query_words = query.lower().split()
        if not query_words:
            return 0.0
        matched = sum(1 for w in query_words if w in text)
        return matched / len(query_words)


@dataclass
class ProvenConclusion:
    """
    已验证结论。

    经过 Verification System 验证的结论，标记为可信赖。
    可跨任务复用，作为新任务的先验知识。
    """
    conclusion_id: str
    claim: str
    category: str                           # security / architecture / data / behavior
    repo_pattern: str = ""                  # 匹配的仓库模式（如 django / express / 所有）
    evidence_summary: str = ""               # 证据摘要（不含完整代码）
    confidence: float = 1.0
    verified_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    tags: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)  # 局限性说明

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProvenConclusion":
        return cls(**data)


@dataclass
class KnowledgeEntry:
    """
    通用知识条目。

    用于 RAG 检索的原子单位。
    """
    entry_id: str
    knowledge_type: KnowledgeType
    title: str                              # 简短标题
    content: str                           # 完整内容（可含代码片段）
    summary: str = ""                      # 摘要（用于快速预览）
    repo_pattern: str = ""                  # 适用的仓库模式
    tags: list[str] = field(default_factory=list)
    task_refs: list[str] = field(default_factory=list)  # 关联的 Task IDs
    usage_count: int = 0                   # 被复用次数
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_used_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KnowledgeEntry":
        return cls(
            entry_id=data["entry_id"],
            knowledge_type=data["knowledge_type"],
            title=data["title"],
            content=data["content"],
            summary=data.get("summary", ""),
            repo_pattern=data.get("repo_pattern", ""),
            tags=list(data.get("tags", [])),
            task_refs=list(data.get("task_refs", [])),
            usage_count=int(data.get("usage_count", 0)),
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
            last_used_at=data.get("last_used_at"),
        )


# ─────────────────────────────────────────────────────────────────────────────
# LongTermMemory
# ─────────────────────────────────────────────────────────────────────────────


class LongTermMemory:
    """
    长期记忆系统。

    位于三层 Memory 架构的第三层（最底层），特点：
    - 持久化存储（SQLite）
    - RAG 检索（基于 SQLite FTS5）
    - 跨任务知识复用
    - 经验积累与学习

    使用方式：
        ltm = LongTermMemory(".odin/longterm")
        ltm.store_task_summary(summary)
        context = ltm.retrieve_for_task(task_query="SQL injection Django")
    """

    def __init__(
        self,
        persist_dir: Path | str | None = ".odin/longterm",
        rag_store: RAGStore | None = None,
    ):
        self._persist_dir = Path(persist_dir) if persist_dir else None
        if self._persist_dir:
            self._persist_dir.mkdir(parents=True, exist_ok=True)

        # RAG Store（复用现有）
        self._rag = rag_store or RAGStore(self._persist_dir / "rag" if self._persist_dir else None)

        # SQLite 连接（存储结构化知识）
        self._conn: sqlite3.Connection | None = None
        self._init_db()

        # 内存缓存（最近使用的条目）
        self._recent_entries: deque[KnowledgeEntry] = deque(maxlen=100)

    # ── 数据库初始化 ──────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        """初始化 SQLite 数据库。"""
        if not self._persist_dir:
            return
        db_path = self._persist_dir / "longterm.db"
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")

        # Task Summaries 表
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS task_summaries (
                summary_id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                repo_language TEXT DEFAULT '',
                repo_size TEXT DEFAULT '',
                duration_seconds REAL DEFAULT 0.0,
                steps_completed INTEGER DEFAULT 0,
                conclusions_count INTEGER DEFAULT 0,
                evidence_count INTEGER DEFAULT 0,
                key_findings_json TEXT DEFAULT '[]',
                tools_used_json TEXT DEFAULT '[]',
                verification_passed INTEGER DEFAULT 0,
                score REAL DEFAULT 0.0,
                tags_json TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                content TEXT NOT NULL
            )
        """)

        # Proven Conclusions 表
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS proven_conclusions (
                conclusion_id TEXT PRIMARY KEY,
                claim TEXT NOT NULL,
                category TEXT DEFAULT '',
                repo_pattern TEXT DEFAULT '',
                evidence_summary TEXT DEFAULT '',
                confidence REAL DEFAULT 1.0,
                verified_at TEXT NOT NULL,
                tags_json TEXT DEFAULT '[]',
                caveats_json TEXT DEFAULT '[]'
            )
        """)

        # Knowledge Entries 表
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_entries (
                entry_id TEXT PRIMARY KEY,
                knowledge_type TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                summary TEXT DEFAULT '',
                repo_pattern TEXT DEFAULT '',
                tags_json TEXT DEFAULT '[]',
                task_refs_json TEXT DEFAULT '[]',
                usage_count INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                last_used_at TEXT
            )
        """)

        # 全文索引
        try:
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
                    title, content, summary, tags_json,
                    entry_id UNINDEXED
                )
            """)
        except sqlite3.OperationalError:
            pass

        self._conn.commit()

    # ── Task Summary 操作 ───────────────────────────────────────────────────

    def store_task_summary(self, summary: TaskSummary) -> None:
        """
        存储任务摘要。

        将完成的任务摘要持久化，供未来相似任务检索参考。
        """
        if not self._conn:
            return

        import json
        content = json.dumps(summary.to_dict(), ensure_ascii=False)

        self._conn.execute("""
            INSERT OR REPLACE INTO task_summaries
            (summary_id, task_type, repo_language, repo_size, duration_seconds,
             steps_completed, conclusions_count, evidence_count, key_findings_json,
             tools_used_json, verification_passed, score, tags_json, created_at, content)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            summary.summary_id,
            summary.task_type,
            summary.repo_language,
            summary.repo_size,
            summary.duration_seconds,
            summary.steps_completed,
            summary.conclusions_count,
            summary.evidence_count,
            json.dumps(summary.key_findings, ensure_ascii=False),
            json.dumps(summary.tools_used, ensure_ascii=False),
            int(summary.verification_passed),
            summary.score,
            json.dumps(summary.tags, ensure_ascii=False),
            summary.created_at,
            content,
        ))
        self._conn.commit()

        # 同时索引到 RAG
        self._rag.index_report(
            run_id=summary.summary_id,
            report_text=content,
            summary=" | ".join(summary.key_findings[:3]) if summary.key_findings else summary.task_type,
            metadata={"type": "task_summary", "task_type": summary.task_type, "score": summary.score},
        )

        logger.info("[LongTermMemory] Stored task summary: %s", summary.summary_id)

    def retrieve_similar_tasks(
        self,
        query: str,
        task_type: str | None = None,
        top_k: int = 5,
    ) -> list[TaskSummary]:
        """
        检索相似的历史任务。

        Args:
            query       : 检索查询（关键词）
            task_type   : 可选，限定任务类型
            top_k       : 返回数量

        Returns:
            按相关性排序的 TaskSummary 列表
        """
        if not self._conn:
            return []

        import json

        # 使用 RAG 检索
        rag_results = self._rag.search(query, top_k=top_k * 2)
        if not rag_results:
            return []

        summaries: list[TaskSummary] = []
        seen_ids: set[str] = set()

        for r in rag_results:
            if r.report_id in seen_ids:
                continue
            seen_ids.add(r.report_id)

            row = self._conn.execute(
                "SELECT content FROM task_summaries WHERE summary_id = ?",
                (r.report_id,),
            ).fetchone()

            if row:
                try:
                    data = json.loads(row["content"])
                    if task_type and data.get("task_type") != task_type:
                        continue
                    summary = TaskSummary.from_dict(data)
                    summaries.append(summary)
                    if len(summaries) >= top_k:
                        break
                except Exception:
                    pass

        return summaries

    def get_recent_summaries(self, limit: int = 20) -> list[TaskSummary]:
        """获取最近的任务摘要。"""
        if not self._conn:
            return []
        import json
        rows = self._conn.execute(
            "SELECT content FROM task_summaries ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        summaries = []
        for row in rows:
            try:
                summaries.append(TaskSummary.from_dict(json.loads(row["content"])))
            except Exception:
                pass
        return summaries

    # ── Proven Conclusion 操作 ──────────────────────────────────────────────

    def store_proven_conclusion(self, conclusion: ProvenConclusion) -> None:
        """存储已验证结论。"""
        if not self._conn:
            return

        import json
        self._conn.execute("""
            INSERT OR REPLACE INTO proven_conclusions
            (conclusion_id, claim, category, repo_pattern, evidence_summary,
             confidence, verified_at, tags_json, caveats_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            conclusion.conclusion_id,
            conclusion.claim,
            conclusion.category,
            conclusion.repo_pattern,
            conclusion.evidence_summary,
            conclusion.confidence,
            conclusion.verified_at,
            json.dumps(conclusion.tags, ensure_ascii=False),
            json.dumps(conclusion.caveats, ensure_ascii=False),
        ))
        self._conn.commit()

        # 索引到 RAG
        self._rag.index_report(
            run_id=conclusion.conclusion_id,
            report_text=f"{conclusion.claim}\n\n{conclusion.evidence_summary}",
            summary=conclusion.claim[:100],
            metadata={"type": "proven_conclusion", "category": conclusion.category},
        )

        logger.info("[LongTermMemory] Stored proven conclusion: %s", conclusion.conclusion_id)

    def retrieve_proven_conclusions(
        self,
        query: str,
        category: str | None = None,
        repo_pattern: str | None = None,
        top_k: int = 10,
    ) -> list[ProvenConclusion]:
        """
        检索已验证结论。

        用于新任务中引用历史验证结论作为先验知识。
        """
        if not self._conn:
            return []

        import json
        results = self._rag.search(query, top_k=top_k * 2)
        if not results:
            return []

        conclusions: list[ProvenConclusion] = []
        seen_ids: set[str] = set()

        for r in results:
            if r.report_id in seen_ids:
                continue
            seen_ids.add(r.report_id)

            row = self._conn.execute(
                "SELECT * FROM proven_conclusions WHERE conclusion_id = ?",
                (r.report_id,),
            ).fetchone()

            if row:
                try:
                    data = {
                        "conclusion_id": row["conclusion_id"],
                        "claim": row["claim"],
                        "category": row["category"],
                        "repo_pattern": row["repo_pattern"],
                        "evidence_summary": row["evidence_summary"],
                        "confidence": row["confidence"],
                        "verified_at": row["verified_at"],
                        "tags": json.loads(row["tags_json"]),
                        "caveats": json.loads(row["caveats_json"]),
                    }
                    if category and data["category"] != category:
                        continue
                    if repo_pattern and data["repo_pattern"] not in (repo_pattern, "*"):
                        continue
                    conclusions.append(ProvenConclusion.from_dict(data))
                    if len(conclusions) >= top_k:
                        break
                except Exception:
                    pass

        return conclusions

    # ── Knowledge Entry 操作 ───────────────────────────────────────────────

    def store_knowledge(self, entry: KnowledgeEntry) -> None:
        """存储可复用知识条目。"""
        if not self._conn:
            return

        import json
        self._conn.execute("""
            INSERT OR REPLACE INTO knowledge_entries
            (entry_id, knowledge_type, title, content, summary, repo_pattern,
             tags_json, task_refs_json, usage_count, created_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry.entry_id,
            str(entry.knowledge_type),
            entry.title,
            entry.content,
            entry.summary,
            entry.repo_pattern,
            json.dumps(entry.tags, ensure_ascii=False),
            json.dumps(entry.task_refs, ensure_ascii=False),
            entry.usage_count,
            entry.created_at,
            entry.last_used_at,
        ))
        self._conn.commit()

        # FTS5 索引
        try:
            self._conn.execute(
                "INSERT INTO knowledge_fts (title, content, summary, tags_json, entry_id) VALUES (?, ?, ?, ?, ?)",
                (entry.title, entry.content, entry.summary,
                 json.dumps(entry.tags, ensure_ascii=False), entry.entry_id),
            )
            self._conn.commit()
        except sqlite3.OperationalError:
            pass

        # RAG 索引
        self._rag.index_report(
            run_id=entry.entry_id,
            report_text=f"{entry.title}\n\n{entry.content}",
            summary=entry.summary,
            metadata={
                "type": "knowledge",
                "knowledge_type": str(entry.knowledge_type),
                "repo_pattern": entry.repo_pattern,
            },
        )

        logger.info("[LongTermMemory] Stored knowledge: %s (%s)", entry.entry_id, entry.knowledge_type)

    def retrieve_knowledge(
        self,
        query: str,
        knowledge_type: KnowledgeType | None = None,
        repo_pattern: str | None = None,
        top_k: int = 5,
    ) -> list[KnowledgeEntry]:
        """
        检索可复用知识。

        用于 Agent 执行时注入历史经验。
        """
        if not self._conn:
            return []

        import json
        results = self._rag.search(query, top_k=top_k * 2)
        if not results:
            return []

        entries: list[KnowledgeEntry] = []
        seen_ids: set[str] = set()

        for r in results:
            if r.report_id in seen_ids:
                continue
            seen_ids.add(r.report_id)

            row = self._conn.execute(
                "SELECT * FROM knowledge_entries WHERE entry_id = ?",
                (r.report_id,),
            ).fetchone()

            if row:
                try:
                    data = {
                        "entry_id": row["entry_id"],
                        "knowledge_type": row["knowledge_type"],
                        "title": row["title"],
                        "content": row["content"],
                        "summary": row["summary"],
                        "repo_pattern": row["repo_pattern"],
                        "tags": json.loads(row["tags_json"]),
                        "task_refs": json.loads(row["task_refs_json"]),
                        "usage_count": row["usage_count"],
                        "created_at": row["created_at"],
                        "last_used_at": row["last_used_at"],
                    }
                    if knowledge_type and data["knowledge_type"] != str(knowledge_type):
                        continue
                    if repo_pattern and data["repo_pattern"] not in (repo_pattern, "*"):
                        continue
                    entry = KnowledgeEntry.from_dict(data)
                    entries.append(entry)
                    if len(entries) >= top_k:
                        break
                except Exception:
                    pass

        return entries

    def increment_usage(self, entry_id: str) -> None:
        """增加知识条目使用计数。"""
        if not self._conn:
            return
        self._conn.execute(
            """UPDATE knowledge_entries
               SET usage_count = usage_count + 1,
                   last_used_at = ?
               WHERE entry_id = ?""",
            (datetime.now(timezone.utc).isoformat(), entry_id),
        )
        self._conn.commit()

    # ── RAG 检索入口 ───────────────────────────────────────────────────────

    def retrieve_for_task(
        self,
        task_query: str,
        task_type: str | None = None,
        repo_language: str | None = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """
        综合检索入口：为新任务准备历史上下文。

        整合检索结果，生成可注入到 prompt 的上下文字符串。

        Returns:
            {
                "similar_tasks": [...],
                "proven_conclusions": [...],
                "knowledge_entries": [...],
                "context_text": "..."   # 可直接注入 prompt
            }
        """
        similar_tasks = self.retrieve_similar_tasks(
            task_query, task_type=task_type, top_k=top_k
        )
        proven_conclusions = self.retrieve_proven_conclusions(
            task_query, repo_pattern=repo_language, top_k=top_k
        )
        knowledge_entries = self.retrieve_knowledge(
            task_query, repo_pattern=repo_language, top_k=top_k
        )

        context_parts = ["## Historical Context from LongTermMemory\n"]

        if similar_tasks:
            context_parts.append("### Similar Past Tasks")
            for t in similar_tasks[:3]:
                findings = " | ".join(t.key_findings[:3]) if t.key_findings else "no findings"
                context_parts.append(
                    f"- [{t.task_type}] score={t.score:.1f} "
                    f"steps={t.steps_completed} evidence={t.evidence_count}: {findings}"
                )
            context_parts.append("")

        if proven_conclusions:
            context_parts.append("### Proven Conclusions (High Confidence)")
            for c in proven_conclusions[:3]:
                caveats = f" (caveats: {', '.join(c.caveats)})" if c.caveats else ""
                context_parts.append(f"- {c.claim}{caveats}")
            context_parts.append("")

        if knowledge_entries:
            context_parts.append("### Reusable Knowledge")
            for e in knowledge_entries[:3]:
                context_parts.append(f"- **{e.title}**: {e.summary or e.content[:100]}")
            context_parts.append("")

        return {
            "similar_tasks": [t.to_dict() for t in similar_tasks],
            "proven_conclusions": [c.to_dict() for c in proven_conclusions],
            "knowledge_entries": [e.to_dict() for e in knowledge_entries],
            "context_text": "\n".join(context_parts),
        }

    def get_context_for_llm(self, task_query: str, **kwargs: Any) -> str:
        """
        生成可直接注入到 LLM prompt 的上下文。

        便捷方法，等价于 retrieve_for_task(...)[context_text]。
        """
        result = self.retrieve_for_task(task_query, **kwargs)
        return result.get("context_text", "")

    # ── 统计 ───────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """获取存储统计。"""
        if not self._conn:
            return {}
        import json
        c1 = self._conn.execute("SELECT COUNT(*) as n FROM task_summaries").fetchone()
        c2 = self._conn.execute("SELECT COUNT(*) as n FROM proven_conclusions").fetchone()
        c3 = self._conn.execute("SELECT COUNT(*) as n FROM knowledge_entries").fetchone()
        return {
            "task_summaries": c1["n"] if c1 else 0,
            "proven_conclusions": c2["n"] if c2 else 0,
            "knowledge_entries": c3["n"] if c3 else 0,
            "rag_stats": self._rag.stats(),
        }

    def close(self) -> None:
        """关闭数据库连接。"""
        if self._conn:
            self._conn.close()
            self._conn = None
        self._rag.close()
