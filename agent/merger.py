"""
agent/merger.py — 多 Agent 结果合并

支持多个 Agent 并行执行后，将结果合并为统一的报告上下文。

典型场景：
- Agent1 负责架构分析 → 输出 architecture_report
- Agent2 负责调用链追踪 → 输出 call_graph
- Agent3 负责安全扫描 → 输出 security_findings
- ↓ 合并 ↓
- Agent4 负责总结生成 → 输出最终报告
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from memory.evidence_store import EvidenceStore
from memory.memory_store import MemoryStore
from memory.models import Conclusion

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """单个 Agent 的执行结果。"""
    agent_name: str          # Agent 标识
    skill_id: str            # 对应的 Skill ID
    status: str              # succeeded | failed
    output: dict[str, Any]   # Agent 输出
    evidence_refs: list[str] = field(default_factory=list)
    duration_ms: int = 0
    error: str | None = None


@dataclass
class MergedContext:
    """
    合并后的研究上下文。

    包含：
    - 所有 Agent 的输出聚合
    - 提取的 MEU evidence_refs
    - 生成的高置信度结论
    - 报告草稿
    """
    run_id: str
    agent_results: list[AgentResult] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    conclusions: list[Conclusion] = field(default_factory=list)
    merged_findings: list[dict[str, Any]] = field(default_factory=list)
    report_draft: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def add_result(self, result: AgentResult) -> None:
        self.agent_results.append(result)
        self._merge_findings(result)

    def _merge_findings(self, result: AgentResult) -> None:
        """将 Agent 输出中的 findings 合并到 merged_findings。"""
        output = result.output
        if not isinstance(output, dict):
            return

        findings: list[dict[str, Any]] = []
        for key in ["findings", "modules", "hypotheses", "attack_surfaces",
                    "sinks", "vulnerabilities", "entries"]:
            if key in output and isinstance(output[key], list):
                findings.extend(output[key])

        for finding in findings:
            if not isinstance(finding, dict):
                continue
            # 去重：如果 finding 已存在（相同 file_path + symbol），跳过
            duplicate = False
            for existing in self.merged_findings:
                if (existing.get("file_path") == finding.get("file_path")
                        and existing.get("symbol") == finding.get("symbol")
                        and existing.get("type") == finding.get("type")):
                    # 合并 evidence_refs
                    existing_refs = set(existing.get("evidence_refs", []))
                    new_refs = set(finding.get("evidence_refs", []))
                    existing_refs.update(new_refs)
                    existing["evidence_refs"] = list(existing_refs)
                    duplicate = True
                    break
            if not duplicate:
                self.merged_findings.append(finding)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "agent_results": [
                {
                    "agent_name": r.agent_name,
                    "skill_id": r.skill_id,
                    "status": r.status,
                    "output": r.output,
                    "evidence_refs": r.evidence_refs,
                    "duration_ms": r.duration_ms,
                    "error": r.error,
                }
                for r in self.agent_results
            ],
            "evidence_refs": self.evidence_refs,
            "merged_findings_count": len(self.merged_findings),
            "conclusions_count": len(self.conclusions),
            "report_draft_preview": self.report_draft[:200] if self.report_draft else "",
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


class AgentResultMerger:
    """
    多 Agent 结果合并器。

    负责：
    1. 接收多个 Agent 的执行结果
    2. 去重合并 findings
    3. 收集 evidence_refs
    4. 生成结论（从 findings 聚合）
    5. 生成报告草稿
    """

    def __init__(
        self,
        run_id: str,
        evidence_store: EvidenceStore | None = None,
        memory_store: MemoryStore | None = None,
    ):
        self.context = MergedContext(run_id=run_id)
        self.evidence_store = evidence_store
        self.memory_store = memory_store

    def add_result(self, result: AgentResult) -> None:
        """添加一个 Agent 的执行结果。"""
        self.context.add_result(result)

        # 收集 evidence_refs
        if result.evidence_refs:
            existing = set(self.context.evidence_refs)
            existing.update(result.evidence_refs)
            self.context.evidence_refs = list(existing)

        # 存入 EvidenceStore
        if self.evidence_store and result.evidence_refs:
            for ref in result.evidence_refs:
                if not self.evidence_store.has(ref):
                    logger.debug("Merger: 引用了未存储的 evidence_ref: %s", ref)

        logger.info(
            "[Merger] Added result from '%s' — status=%s, findings=%d",
            result.agent_name,
            result.status,
            len(result.output) if isinstance(result.output, dict) else 0,
        )

    def generate_conclusions(self) -> list[Conclusion]:
        """
        从 merged_findings 生成高层次的结论。

        分类聚合：
        - architecture: 架构相关发现
        - security: 安全相关发现
        - data: 数据结构相关发现
        - behavior: 行为相关发现
        """
        from memory.models import Conclusion

        categories: dict[str, list[dict]] = {}
        for finding in self.context.merged_findings:
            cat = finding.get("category", finding.get("type", "general"))
            categories.setdefault(cat, []).append(finding)

        conclusions = []
        for category, findings in categories.items():
            if not findings:
                continue

            # 按置信度排序
            sorted_findings = sorted(
                findings,
                key=lambda f: f.get("confidence", 0.5),
                reverse=True,
            )
            top = sorted_findings[0]

            # 收集 evidence_refs
            refs: list[str] = []
            for f in sorted_findings:
                refs.extend(f.get("evidence_refs", []))

            conclusion = Conclusion(
                claim=f"{category}: Found {len(findings)} {category} related findings",
                category=category,
                confidence=sum(f.get("confidence", 0.5) for f in sorted_findings) / len(sorted_findings),
                evidence_refs=list(set(refs)),
                tags=[category, "merged"],
                uncertainty_note=(
                    "Low confidence" if len(sorted_findings) < 3 else ""
                ),
            )
            conclusions.append(conclusion)

        self.context.conclusions = conclusions

        # 存入 MemoryStore
        if self.memory_store:
            for conc in conclusions:
                self.memory_store.put_conclusion(conc)

        return conclusions

    def generate_report_draft(self) -> str:
        """
        基于合并的 findings 生成报告草稿。

        这是最终报告的中间版本，可交给专门的 report_generation Skill 做润色。
        """
        lines = [
            "# Research Summary",
            "",
            f"**Run ID**: {self.context.run_id}",
            f"**Agents**: {len(self.context.agent_results)}",
            f"**Findings**: {len(self.context.merged_findings)}",
            f"**Evidence Refs**: {len(self.context.evidence_refs)}",
            "",
            "## Findings by Category",
            "",
        ]

        categories: dict[str, list[dict]] = {}
        for finding in self.context.merged_findings:
            cat = finding.get("category", finding.get("type", "other"))
            categories.setdefault(cat, []).append(finding)

        for cat, findings in sorted(categories.items()):
            lines.append(f"### {cat.title()} ({len(findings)})")
            for f in findings[:5]:  # 每类最多显示 5 个
                title = f.get("title", f.get("name", f.get("symbol", "Unknown")))
                conf = f.get("confidence", 0.5)
                refs = f.get("evidence_refs", [])
                lines.append(
                    f"- **{title}** (conf={conf:.1f})"
                    + (f" refs={len(refs)}" if refs else "")
                )
            if len(findings) > 5:
                lines.append(f"  _... and {len(findings) - 5} more_")
            lines.append("")

        if self.context.conclusions:
            lines.extend([
                "## Conclusions",
                "",
            ])
            for conc in self.context.conclusions:
                icon = "🔴" if conc.confidence >= 0.7 else "🟡" if conc.confidence >= 0.5 else "⚪"
                lines.append(
                    f"{icon} [{conc.category}] {conc.claim} "
                    f"(confidence={conc.confidence:.2f})"
                )
            lines.append("")

        self.context.report_draft = "\n".join(lines)
        return self.context.report_draft

    def finalize(self) -> MergedContext:
        """完成合并流程：生成结论和报告草稿。"""
        self.generate_conclusions()
        self.generate_report_draft()
        logger.info(
            "[Merger] Finalized — %d findings, %d conclusions, %d refs",
            len(self.context.merged_findings),
            len(self.context.conclusions),
            len(self.context.evidence_refs),
        )
        return self.context
