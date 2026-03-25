"""
Memory Query Module

提供高级查询接口，封装常见查询模式。
"""
from __future__ import annotations

from typing import Any

from memory.evidence_store import EvidenceStore
from memory.memory_store import MemoryStore


class MemoryQuery:
    """
    封装 Memory/Evidence Store 的高级查询方法。

    Usage:
        query = MemoryQuery(memory_store, evidence_store)
        high_confidence_hypotheses = query.by_confidence(min=0.7)
    """

    def __init__(
        self,
        memory_store: MemoryStore,
        evidence_store: EvidenceStore,
    ):
        self._memory = memory_store
        self._evidence = evidence_store

    # ── Evidence 查询 ────────────────────────────────────────────────────────

    def evidence_for_conclusion(self, claim_text: str) -> list[Any]:
        """查找某结论引用的所有 MEU。"""
        # 遍历所有 link，找 claim 匹配的
        import json, re
        pattern = re.compile(re.escape(claim_text[:80]), re.IGNORECASE)
        meu_ids: set[str] = set()
        for link in _get_links(self._memory):
            if pattern.search(link.conclusion_claim):
                if link.evidence_meu_id:
                    meu_ids.add(link.evidence_meu_id)
                elif link.evidence_ref:
                    meu_ids.add(link.evidence_ref)

        return [self._evidence.get(mid) for mid in meu_ids if self._evidence.get(mid)]

    def evidence_in_file(self, file_path: str) -> list[Any]:
        """查找某文件相关的所有 MEU。"""
        return self._evidence.by_file(file_path)

    def evidence_by_tag(self, tag: str) -> list[Any]:
        """按 tag 过滤 MEU。"""
        return self._evidence.by_tag(tag)

    # ── Artifact 查询 ───────────────────────────────────────────────────────

    def artifacts_by_run(self, run_id: str) -> list[Any]:
        """获取某次 Workflow 运行的所有 Artifact。"""
        return self._memory.list_artifacts(run_id=run_id)

    def artifacts_by_skill(self, skill_id: str) -> list[Any]:
        """获取某 Skill 的所有历史输出。"""
        return self._memory.list_artifacts(skill_id=skill_id)

    # ── 综合查询 ────────────────────────────────────────────────────────────

    def full_evidence_index(self, repo: str | None = None) -> list[dict[str, Any]]:
        """
        生成完整 Evidence Index（含 MEU 详情）。

        用于研究报告 Evidence 章节。
        """
        return self._evidence.build_evidence_index(repo=repo)

    def high_confidence_findings(
        self,
        min_confidence: float = 0.7,
        category: str | None = None,
    ) -> list[Any]:
        """获取高置信度结论。"""
        conclusions = self._memory.list_conclusions(
            category=category,
            min_confidence=min_confidence,
        )
        return conclusions

    def vulnerable_paths(
        self,
        repo: str,
    ) -> list[dict[str, Any]]:
        """
        查找与漏洞相关的证据链。

        聚合：含 vulnerability / injection / overflow 等 tag 的 MEU，
        及其关联的 call_relation。
        """
        suspect_tags = {
            "vulnerability", "injection", "overflow",
            "rce", "sqli", "xss", "idor", "ssrf",
            "deserialize", "path_traversal",
        }
        results: list[dict[str, Any]] = []
        for tag in suspect_tags:
            meus = self._evidence.by_tag(tag)
            for meu in meus:
                if repo and meu.repo != repo:
                    continue
                results.append({
                    "meu_id": meu.meu_id,
                    "location": meu.location,
                    "symbol": meu.symbol,
                    "tag": tag,
                    "confidence": meu.confidence,
                    "relation": meu.relation.to_dict() if meu.relation else None,
                    "snippet": meu.snippet[:300],
                })
        return sorted(results, key=lambda r: r["confidence"], reverse=True)


def _get_links(memory_store: MemoryStore):
    """透传 MemoryStore._evidence_links（避免暴露内部）。"""
    return getattr(memory_store, "_evidence_links", {}).values()
