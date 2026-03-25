"""
Memory Store Module

存储研究中间产物（Artifacts）和结论（Conclusions）。
支持按 run_id、skill_id、artifact_id 等维度查询。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from memory.models import (
    ArtifactKind,
    Conclusion,
    EvidenceLink,
    ResearchArtifact,
)


# ─────────────────────────────────────────────────────────────────────────────
# MemoryStore
# ─────────────────────────────────────────────────────────────────────────────

class MemoryStore:
    """
    研究记忆存储。

    管理：
    - ResearchArtifacts : 每个 Skill 输出的快照
    - Conclusions       : 带 evidence 引用的结论
    - EvidenceLinks     : 结论到 MEU 的链接关系

    支持两种后端：
    - 内存（默认，进程内）
    - 文件持久化（JSON Lines）
    """

    def __init__(self, persist_path: Path | str | None = None):
        """
        Args:
            persist_path: 若提供，则每次写入时同步持久化到该目录。
        """
        self._artifacts: dict[str, ResearchArtifact] = {}
        self._conclusions: dict[str, Conclusion] = {}
        self._evidence_links: dict[str, EvidenceLink] = {}
        self._persist_path = Path(persist_path) if persist_path else None
        if self._persist_path:
            self._persist_path.mkdir(parents=True, exist_ok=True)
            self._load_persisted()

    # ── Artifact 操作 ────────────────────────────────────────────────────────

    def put_artifact(
        self,
        run_id: str,
        skill_id: str,
        skill_version: str,
        kind: str | ArtifactKind,
        content: dict[str, Any],
        summary: str = "",
        tags: list[str] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> ResearchArtifact:
        """创建并存储一个 Artifact。"""
        artifact_id = f"ART-{uuid.uuid4().hex[:12]}"
        if isinstance(kind, str):
            kind = ArtifactKind(kind)
        artifact = ResearchArtifact(
            artifact_id=artifact_id,
            run_id=run_id,
            skill_id=skill_id,
            skill_version=skill_version,
            kind=kind,
            content=content,
            summary=summary,
            tags=tags or [],
            evidence_refs=evidence_refs or [],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._artifacts[artifact_id] = artifact

        # 同步持久化
        if self._persist_path:
            self._persist_artifact(artifact)

        return artifact

    def get_artifact(self, artifact_id: str) -> ResearchArtifact | None:
        return self._artifacts.get(artifact_id)

    def list_artifacts(
        self,
        run_id: str | None = None,
        skill_id: str | None = None,
        kind: str | ArtifactKind | None = None,
    ) -> list[ResearchArtifact]:
        results = list(self._artifacts.values())
        if run_id:
            results = [a for a in results if a.run_id == run_id]
        if skill_id:
            results = [a for a in results if a.skill_id == skill_id]
        if kind:
            k = kind.value if isinstance(kind, ArtifactKind) else kind
            results = [a for a in results if a.kind == k]
        return results

    # ── Conclusion 操作 ─────────────────────────────────────────────────────

    def put_conclusion(
        self,
        conclusion: Conclusion,
    ) -> Conclusion:
        """存储一个结论（自动去重）。"""
        # 用 claim 文本作为去重 key
        key = f"{conclusion.category}:{conclusion.claim}"
        if key in self._conclusions:
            existing = self._conclusions[key]
            # 合并 evidence_refs
            existing.evidence_refs = list(set(existing.evidence_refs + conclusion.evidence_refs))
            return existing
        self._conclusions[key] = conclusion
        return conclusion

    def list_conclusions(
        self,
        category: str | None = None,
        min_confidence: float | None = None,
    ) -> list[Conclusion]:
        results = list(self._conclusions.values())
        if category:
            results = [c for c in results if c.category == category]
        if min_confidence is not None:
            results = [c for c in results if c.confidence >= min_confidence]
        return results

    # ── Evidence Link 操作 ───────────────────────────────────────────────────

    def put_evidence_link(
        self,
        conclusion_artifact_id: str,
        conclusion_claim: str,
        evidence_ref: str,
        evidence_meu_id: str | None = None,
    ) -> EvidenceLink:
        """存储结论到 MEU 的链接。"""
        link_id = f"ELINK-{uuid.uuid4().hex[:12]}"
        link = EvidenceLink(
            link_id=link_id,
            conclusion_artifact_id=conclusion_artifact_id,
            conclusion_claim=conclusion_claim[:200],
            evidence_ref=evidence_ref,
            evidence_meu_id=evidence_meu_id,
            is_valid=True,
            validated_at=datetime.now(timezone.utc).isoformat(),
        )
        self._evidence_links[link_id] = link
        return link

    def get_evidence_links(self, artifact_id: str) -> list[EvidenceLink]:
        return [
            link for link in self._evidence_links.values()
            if link.conclusion_artifact_id == artifact_id
        ]

    # ── 持久化 ──────────────────────────────────────────────────────────────

    def _persist_artifact(self, artifact: ResearchArtifact) -> None:
        import json
        path = self._persist_path / f"{artifact.artifact_id}.json"
        with path.open("w", encoding="utf-8") as fh:
            json.dump(artifact.to_dict(), fh, ensure_ascii=False, indent=2)

    def _load_persisted(self) -> None:
        """从持久化目录恢复所有 Artifact。"""
        import json
        if not self._persist_path or not self._persist_path.is_dir():
            return
        for fpath in self._persist_path.glob("*.json"):
            try:
                with fpath.open(encoding="utf-8") as fh:
                    data = json.load(fh)
                artifact = ResearchArtifact(
                    artifact_id=data["artifact_id"],
                    run_id=data.get("run_id", ""),
                    skill_id=data.get("skill_id", ""),
                    skill_version=data.get("skill_version", ""),
                    kind=ArtifactKind(data.get("kind", "report")),
                    content=data.get("content", {}),
                    summary=data.get("summary", ""),
                    tags=list(data.get("tags", [])),
                    evidence_refs=list(data.get("evidence_refs", [])),
                    created_at=data.get("created_at", ""),
                )
                self._artifacts[artifact.artifact_id] = artifact
            except Exception:
                pass

    # ── 统计 ────────────────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        return {
            "total_artifacts": len(self._artifacts),
            "total_conclusions": len(self._conclusions),
            "total_evidence_links": len(self._evidence_links),
            "by_skill": self._count_by("skill_id", self._artifacts),
            "by_kind": self._count_by_kind(),
        }

    def _count_by(self, field_name: str, items: dict) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in items.values():
            key = getattr(item, field_name, "?")
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _count_by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for artifact in self._artifacts.values():
            k = artifact.kind.value if isinstance(artifact.kind, ArtifactKind) else str(artifact.kind)
            counts[k] = counts.get(k, 0) + 1
        return counts
