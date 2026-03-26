"""
rag/store.py — 基于 SQLite FTS5 的轻量级 RAG 存储

不依赖 ChromaDB / FAISS 等外部向量库，直接使用 SQLite FTS5 全文搜索。
支持：
- 报告分块存储
- 全文检索
- 相似报告上下文注入
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """搜索结果。"""
    report_id: str
    chunk_id: str
    chunk_text: str
    chunk_index: int
    repo_url: str
    workflow_id: str
    created_at: str
    score: float


class RAGStore:
    """
    基于 SQLite FTS5 的轻量级 RAG 存储。

    使用 SQLite FTS5（全文搜索）替代向量数据库，适合中小规模（数千份报告）。
    如需更强语义搜索，可接入 OpenAI Embeddings + ChromaDB。
    """

    DEFAULT_CHUNK_SIZE = 500
    DEFAULT_CHUNK_OVERLAP = 50
    DEFAULT_TOP_K = 5

    def __init__(
        self,
        persist_dir: Path | str | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._persist_dir = Path(persist_dir) if persist_dir else None
        if self._persist_dir:
            self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._fts_available = False
        self._init_db()

    def _init_db(self) -> None:
        """初始化数据库表。"""
        db_path = self._persist_dir / "rag.db" if self._persist_dir else None
        self._conn = sqlite3.connect(
            str(db_path) if db_path else ":memory:",
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")

        # reports 表
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                report_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                repo_url TEXT DEFAULT '',
                workflow_id TEXT DEFAULT '',
                report_text TEXT NOT NULL,
                summary TEXT DEFAULT '',
                finding_count INTEGER DEFAULT 0,
                vuln_count INTEGER DEFAULT 0,
                language TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                metadata_json TEXT DEFAULT '{}'
            )
        """)

        # chunks 表
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                chunk_hash TEXT NOT NULL,
                metadata_json TEXT DEFAULT '{}',
                FOREIGN KEY (report_id) REFERENCES reports(report_id)
            )
        """)

        # FTS5 虚拟表
        try:
            self._conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    text,
                    chunk_id UNINDEXED,
                    report_id UNINDEXED
                )
            """)
            self._fts_available = True
            logger.info("[RAG] FTS5 可用")
        except sqlite3.OperationalError as exc:
            logger.warning("[RAG] FTS5 不可用: %s，降级为关键词搜索", exc)
            self._fts_available = False

        self._conn.commit()

    def index_report(
        self,
        run_id: str,
        report_text: str,
        repo_url: str = "",
        workflow_id: str = "",
        summary: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """将报告存入 RAG Store（分块 + FTS5 索引）。"""
        if self._conn is None:
            raise RuntimeError("RAGStore 未初始化")

        report_id = f"report_{uuid.uuid4().hex[:12]}"
        metadata = metadata or {}

        vuln_count = report_text.lower().count("vulnerability")
        finding_count = report_text.count("##") + report_text.count("###")

        self._conn.execute(
            """INSERT INTO reports
               (report_id, run_id, repo_url, workflow_id, report_text, summary,
                finding_count, vuln_count, language, created_at, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                report_id, run_id, repo_url, workflow_id,
                report_text, summary, finding_count, vuln_count,
                metadata.get("language", ""),
                datetime.now(timezone.utc).isoformat(),
                json.dumps(metadata, ensure_ascii=False),
            ),
        )

        chunks = self._chunk_text(report_text)
        for i, chunk_text in enumerate(chunks):
            chunk_id = f"chunk_{report_id}_{i}"
            chunk_hash = hashlib.sha256(chunk_text.encode()).hexdigest()[:16]

            self._conn.execute(
                """INSERT INTO chunks
                   (chunk_id, report_id, chunk_index, text, chunk_hash, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (chunk_id, report_id, i, chunk_text, chunk_hash,
                 json.dumps(metadata, ensure_ascii=False)),
            )

            if self._fts_available:
                try:
                    self._conn.execute(
                        "INSERT INTO chunks_fts (text, chunk_id, report_id) VALUES (?, ?, ?)",
                        (chunk_text, chunk_id, report_id),
                    )
                except sqlite3.OperationalError:
                    self._fts_available = False

        self._conn.commit()
        logger.info("[RAG] Indexed report %s with %d chunks", report_id, len(chunks))
        return report_id

    def search(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        repo_url: str | None = None,
        workflow_id: str | None = None,
    ) -> list[SearchResult]:
        """全文检索相似报告片段。"""
        if self._conn is None:
            return []

        filter_params: list[Any] = []
        filter_clauses: list[str] = []
        if repo_url:
            filter_clauses.append("r.repo_url = ?")
            filter_params.append(repo_url)
        if workflow_id:
            filter_clauses.append("r.workflow_id = ?")
            filter_params.append(workflow_id)
        where_extra = " AND " + " AND ".join(filter_clauses) if filter_clauses else ""

        keywords = self._extract_keywords(query)

        if self._fts_available and keywords:
            results = self._fts_search(keywords, top_k, where_extra, filter_params)
        else:
            results = self._keyword_search(keywords, top_k, where_extra, filter_params)

        return results

    def _fts_search(
        self,
        keywords: list[str],
        top_k: int,
        where_extra: str,
        filter_params: list[Any],
    ) -> list[SearchResult]:
        fts_query = " OR ".join(f'"{kw}"' for kw in keywords) if keywords else "*"
        sql = f"""
            SELECT c.chunk_id, c.report_id, c.text, c.chunk_index,
                   r.repo_url, r.workflow_id, r.created_at,
                   bm25(chunks_fts) as score
            FROM chunks_fts f
            JOIN chunks c ON f.chunk_id = c.chunk_id
            JOIN reports r ON c.report_id = r.report_id
            WHERE chunks_fts MATCH ?
            {where_extra}
            ORDER BY score
            LIMIT ?
        """
        cursor = self._conn.execute(sql, [fts_query] + filter_params + [top_k])
        return self._rows_to_results(cursor.fetchall())

    def _keyword_search(
        self,
        keywords: list[str],
        top_k: int,
        where_extra: str,
        filter_params: list[Any],
    ) -> list[SearchResult]:
        if not keywords:
            return []
        # 简单评分：命中的关键词越多越好
        score_expr = " + ".join(
            f"(INSTR(LOWER(c.text), LOWER(?)) > 0)" for _ in keywords
        )
        sql = f"""
            SELECT c.chunk_id, c.report_id, c.text, c.chunk_index,
                   r.repo_url, r.workflow_id, r.created_at,
                   ({score_expr}) as score
            FROM chunks c
            JOIN reports r ON c.report_id = r.report_id
            WHERE {where_extra or '1=1'}
            ORDER BY score DESC
            LIMIT ?
        """
        cursor = self._conn.execute(sql, keywords * 2 + filter_params + [top_k])
        return self._rows_to_results(cursor.fetchall())

    def _rows_to_results(self, rows: list) -> list[SearchResult]:
        out: list[SearchResult] = []
        for row in rows:
            out.append(SearchResult(
                report_id=row["report_id"],
                chunk_id=row["chunk_id"],
                chunk_text=row["text"],
                chunk_index=row["chunk_index"],
                repo_url=row["repo_url"] or "",
                workflow_id=row["workflow_id"] or "",
                created_at=row["created_at"] or "",
                score=abs(float(row["score"])) if row["score"] else 0.0,
            ))
        return out

    def get_context_for_prompt(
        self,
        query: str,
        max_chars: int = 3000,
        **kwargs: Any,
    ) -> str:
        """生成可注入到 prompt 的上下文字符串。"""
        results = self.search(query, **kwargs)
        if not results:
            return ""

        parts = [
            "## Relevant Context from Previous Analyses",
            "(Build upon these findings. Do not repeat work already done.)",
            "",
        ]

        total = 0
        for r in results:
            text = r.chunk_text
            if total + len(text) > max_chars:
                text = text[: max_chars - total]
            parts.extend([
                f"---",
                f"[From {r.repo_url} / {r.workflow_id} | score={r.score:.2f}]",
                text[:800],
                "",
            ])
            total += len(text) + 100
            if total > max_chars:
                break

        parts.append("---")
        return "\n".join(parts)

    def _chunk_text(self, text: str) -> list[str]:
        """将文本分块，尽量在句子边界截断。"""
        if len(text) <= self.chunk_size:
            return [text] if text.strip() else []

        chunks: list[str] = []
        start = 0

        while start < len(text):
            end = start + self.chunk_size
            chunk = text[start:end]

            if end < len(text):
                # 找最近的句子边界
                split = max(
                    chunk.rfind(". "),
                    chunk.rfind(".\n"),
                    chunk.rfind("\n\n"),
                    chunk.rfind("\n"),
                )
                if split > start + self.chunk_size // 3:
                    chunk = chunk[: split + 1]
                    end = start + split + 1

            chunk = chunk.strip()
            if chunk:
                chunks.append(chunk)

            start = end - self.chunk_overlap

        return chunks

    def _extract_keywords(self, query: str) -> list[str]:
        """提取有意义的关键词（长度>=3，非停用词）。"""
        words = re.findall(r"\w+", query.lower())
        stop = {
            "a", "an", "the", "is", "are", "was", "be", "been",
            "have", "has", "had", "do", "does", "did", "will",
            "would", "should", "could", "may", "can", "to", "of",
            "in", "for", "on", "with", "at", "by", "from", "as",
            "and", "or", "but", "not", "this", "that", "i", "you",
            "we", "they", "what", "which", "who", "how", "why",
            "where", "when", "all", "some", "any", "each", "find",
            "look", "show", "use", "here", "there", "there", "into",
        }
        return [w for w in words if len(w) >= 3 and w not in stop]

    def stats(self) -> dict[str, Any]:
        if self._conn is None:
            return {}
        c1 = self._conn.execute("SELECT COUNT(*) as n FROM reports").fetchone()
        c2 = self._conn.execute("SELECT COUNT(*) as n FROM chunks").fetchone()
        return {
            "total_reports": c1["n"] if c1 else 0,
            "total_chunks": c2["n"] if c2 else 0,
            "fts_available": self._fts_available,
        }

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
