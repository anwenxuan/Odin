"""
Evidence Store Module

管理所有 MinimumEvidenceUnit（MEU）的注册、查询与校验。
每个 Skill 输出的 evidence_ref 必须在 EvidenceStore 中有对应条目。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from memory.models import (
    CallRelation,
    Confidence,
    EvidenceType,
    MinimumEvidenceUnit,
    confidence_level,
)


class EvidenceStore:
    """
    MEU 存储与检索系统。

    核心职责：
    1. 注册 MEU（put）
    2. 校验 evidence_ref 是否存在（has / validate）
    3. 按维度查询（by_repo, by_file, by_symbol, by_tag）
    4. 持久化（JSON Lines 格式）
    """

    def __init__(self, persist_path: Path | str | None = None):
        self._meus: dict[str, MinimumEvidenceUnit] = {}
        self._by_repo: dict[str, set[str]] = {}      # repo -> set of meu_id
        self._by_file: dict[str, set[str]] = {}      # file_path -> set of meu_id
        self._by_symbol: dict[str, set[str]] = {}    # symbol -> set of meu_id
        self._by_tag: dict[str, set[str]] = {}       # tag -> set of meu_id
        self._by_extracted_by: dict[str, set[str]] = {}  # skill_id -> set of meu_id
        self._persist_path = Path(persist_path) if persist_path else None
        if self._persist_path:
            self._persist_path.mkdir(parents=True, exist_ok=True)
            self._load_persisted()

    # ── 注册 ────────────────────────────────────────────────────────────────

    def put(self, meu: MinimumEvidenceUnit) -> MinimumEvidenceUnit:
        """注册一个 MEU（自动去重）。"""
        if meu.meu_id in self._meus:
            # 合并 snippet（保留更长的）
            existing = self._meus[meu.meu_id]
            if len(meu.snippet) > len(existing.snippet):
                self._meus[meu.meu_id] = meu
        else:
            self._meus[meu.meu_id] = meu

        self._index(meu)

        if self._persist_path:
            self._persist_meu(meu)

        return self._meus[meu.meu_id]

    def _index(self, meu: MinimumEvidenceUnit) -> None:
        """更新所有倒排索引。"""
        # by_repo
        if meu.repo:
            self._by_repo.setdefault(meu.repo, set()).add(meu.meu_id)
        # by_file
        if meu.file_path:
            self._by_file.setdefault(meu.file_path, set()).add(meu.meu_id)
        # by_symbol
        if meu.symbol:
            self._by_symbol.setdefault(meu.symbol, set()).add(meu.meu_id)
        # by_tag
        for tag in meu.tags:
            self._by_tag.setdefault(tag, set()).add(meu.meu_id)
        # by_extracted_by
        if meu.extracted_by:
            self._by_extracted_by.setdefault(meu.extracted_by, set()).add(meu.meu_id)

    # ── 校验 ────────────────────────────────────────────────────────────────

    def has(self, evidence_ref: str) -> bool:
        """检查 evidence_ref 是否存在（支持 MEU ID 或 file_path::symbol:line 格式）。"""
        if evidence_ref in self._meus:
            return True
        # 格式：file_path::symbol:line
        return any(
            meu.location == evidence_ref
            for meu in self._meus.values()
        )

    def validate(self, evidence_refs: list[str]) -> list[str]:
        """
        校验 evidence_refs 列表。
        Returns:
            缺失的 ref 列表（空=全部有效）
        """
        return [ref for ref in evidence_refs if not self.has(ref)]

    # ── 查询 ────────────────────────────────────────────────────────────────

    def get(self, meu_id: str) -> MinimumEvidenceUnit | None:
        return self._meus.get(meu_id)

    def get_by_location(self, file_path: str, symbol: str = "", line: int | None = None) -> list[MinimumEvidenceUnit]:
        """按代码位置查询 MEU。"""
        candidates = self._meus.values()
        if file_path:
            candidates = [m for m in candidates if file_path in m.file_path]
        if symbol:
            candidates = [m for m in candidates if symbol in m.symbol]
        if line:
            candidates = [
                m for m in candidates
                if m.line_start and m.line_end
                and m.line_start <= line <= m.line_end
            ]
        return list(candidates)

    def by_repo(self, repo: str) -> list[MinimumEvidenceUnit]:
        return [self._meus[mid] for mid in self._by_repo.get(repo, []) if mid in self._meus]

    def by_file(self, file_path: str) -> list[MinimumEvidenceUnit]:
        return [self._meus[mid] for mid in self._by_file.get(file_path, []) if mid in self._meus]

    def by_symbol(self, symbol: str) -> list[MinimumEvidenceUnit]:
        return [self._meus[mid] for mid in self._by_symbol.get(symbol, []) if mid in self._meus]

    def by_tag(self, tag: str) -> list[MinimumEvidenceUnit]:
        return [self._meus[mid] for mid in self._by_tag.get(tag, []) if mid in self._meus]

    def by_extracted_by(self, skill_id: str) -> list[MinimumEvidenceUnit]:
        return [self._meus[mid] for mid in self._by_extracted_by.get(skill_id, []) if mid in self._meus]

    def search(self, query: str) -> list[MinimumEvidenceUnit]:
        """全文搜索：匹配 file_path、symbol、snippet。"""
        q = query.lower()
        return [
            m for m in self._meus.values()
            if q in m.file_path.lower()
            or q in m.symbol.lower()
            or q in m.snippet.lower()
        ]

    def list_all(self) -> list[MinimumEvidenceUnit]:
        return list(self._meus.values())

    # ── 批量操作 ─────────────────────────────────────────────────────────────

    def bulk_put(self, meus: list[MinimumEvidenceUnit]) -> list[MinimumEvidenceUnit]:
        """批量注册 MEU。"""
        return [self.put(meu) for meu in meus]

    def merge(self, other: "EvidenceStore") -> int:
        """合并另一个 EvidenceStore，返回新增 MEU 数量。"""
        before = len(self._meus)
        for meu in other.list_all():
            self.put(meu)
        return len(self._meus) - before

    # ── 报告生成 ─────────────────────────────────────────────────────────────

    def build_evidence_index(
        self,
        repo: str | None = None,
        min_confidence: float = 0.0,
    ) -> list[dict[str, Any]]:
        """
        构建 Evidence Index，用于研究报告。
        可按仓库过滤，按置信度排序。
        """
        results = list(self._meus.values())
        if repo:
            results = [m for m in results if m.repo == repo]
        if min_confidence > 0:
            results = [m for m in results if m.confidence >= min_confidence]
        results.sort(key=lambda m: m.confidence, reverse=True)
        return [
            {
                "meu_id": m.meu_id,
                "location": m.location,
                "symbol": m.symbol,
                "evidence_type": m.evidence_type.value
                              if isinstance(m.evidence_type, EvidenceType)
                              else m.evidence_type,
                "confidence": m.confidence,
                "confidence_level": confidence_level(m.confidence).value,
                "extracted_by": m.extracted_by,
                "snippet_preview": m.snippet[:200] if m.snippet else "",
                "relation": m.relation.to_dict() if m.relation else None,
                "tags": m.tags,
            }
            for m in results
        ]

    # ── 持久化 ─────────────────────────────────────────────────────────────

    def _persist_meu(self, meu: MinimumEvidenceUnit) -> None:
        if not self._persist_path:
            return
        import json
        path = self._persist_path / f"{meu.meu_id}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(meu.to_dict(), ensure_ascii=False) + "\n")

    def _load_persisted(self) -> None:
        """从 JSON Lines 文件恢复所有 MEU。"""
        import json
        if not self._persist_path or not self._persist_path.is_dir():
            return
        for fpath in self._persist_path.glob("*.jsonl"):
            try:
                with fpath.open(encoding="utf-8") as fh:
                    line = fh.readline()
                    if line:
                        data = json.loads(line)
                        meu = MinimumEvidenceUnit.from_dict(data)
                        self._meus[meu.meu_id] = meu
                        self._index(meu)
            except Exception:
                pass

    # ── 统计 ────────────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        return {
            "total_meus": len(self._meus),
            "by_repo": {k: len(v) for k, v in self._by_repo.items()},
            "by_extracted_by": {k: len(v) for k, v in self._by_extracted_by.items()},
            "by_evidence_type": self._count_by_type(),
            "avg_confidence": (
                sum(m.confidence for m in self._meus.values()) / len(self._meus)
                if self._meus else 0.0
            ),
        }

    def _count_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self._meus.values():
            key = m.evidence_type.value if isinstance(m.evidence_type, EvidenceType) else str(m.evidence_type)
            counts[key] = counts.get(key, 0) + 1
        return counts
