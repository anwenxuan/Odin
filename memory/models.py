"""
Memory Models Module

定义 Memory System 中所有数据模型：
- MinimumEvidenceUnit (MEU)        : 最小证据单元
- ResearchArtifact                  : 研究中间产物
- Conclusion                        : 带证据引用的结论
- EvidenceLink                      : 证据链接关系
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class EvidenceType(str, Enum):
    """证据来源类型。"""
    CODE_SNIPPET = "code_snippet"     # 源代码片段
    CONFIG = "config"                 # 配置文件
    DEPENDENCY = "dependency"          # 依赖声明
    SCHEMA = "schema"                  # 数据结构定义
    API_SPEC = "api_spec"             # API 规范
    DOC = "doc"                       # 文档
    INFERRED = "inferred"             # 推断结论（置信度受限）


class Confidence(str, Enum):
    """结论置信度级别。"""
    HIGH = "high"       # >= 0.8
    MEDIUM = "medium"  # 0.5-0.8
    LOW = "low"        # 0.4-0.5
    UNCERTAIN = "uncertain"  # < 0.4


class ArtifactKind(str, Enum):
    """Artifact 类型。"""
    MODULE_MAP = "module_map"
    ENTRY_POINTS = "entry_points"
    CALL_GRAPH = "call_graph"
    DATA_STRUCTURES = "data_structures"
    AUTH_FLOWS = "auth_flows"
    INPUT_FLOWS = "input_flows"
    ATTACK_SURFACE = "attack_surface"
    VULN_HYPOTHESIS = "vuln_hypothesis"
    POC = "poc"
    REPORT = "report"


# ─────────────────────────────────────────────────────────────────────────────
# MinimumEvidenceUnit (MEU)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CallRelation:
    """
    调用关系证据。
    用于描述两个代码实体之间的关联。
    """
    type: str                              # call_relation | data_flow | inheritance | composition
    caller: str | None = None              # 调用方符号
    callee: str | None = None              # 被调用方符号
    direction: str = "caller_to_callee"     # caller_to_callee | bidirectional
    guard: str | None = None               # 中间是否有鉴权 guard
    taint_level: str | None = None         # 无 | low | medium | high
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "caller": self.caller,
            "callee": self.callee,
            "direction": self.direction,
            "guard": self.guard,
            "taint_level": self.taint_level,
            "description": self.description,
        }


@dataclass
class MinimumEvidenceUnit:
    """
    最小证据单元（MEU）。

    这是系统中证据的原子单位——每个结论必须至少引用一个 MEU。
    MEU 必须基于真实读取的代码，不可推断。

    字段设计遵循 CWE/SARIF 标准，兼顾可追溯性和分析友好性。
    """
    meu_id: str                       # 全局唯一标识：MEU-{uuid12}
    repo: str = ""                    # 仓库名：owner/repo
    commit: str = ""                  # Git commit SHA

    # 代码位置
    file_path: str = ""               # 相对于仓库根的路径
    symbol: str = ""                  # 符号名（函数/类/变量等）
    line_start: int | None = None     # 起始行（1-indexed）
    line_end: int | None = None       # 结束行（包含）
    snippet: str = ""                 # 源代码片段（原始文本）

    # 语义标注
    evidence_type: EvidenceType = EvidenceType.CODE_SNIPPET
    language: str = ""                # 编程语言（python/js/go 等）
    framework: str = ""               # 框架标识（django/express/gin 等）

    # 调用关系（可选）
    relation: CallRelation | None = None

    # 元数据
    extracted_by: str = ""            # 提取该证据的 Skill ID
    confidence: float = 1.0           # 0.0-1.0，MEU 自身置信度
    tags: list[str] = field(default_factory=list)   # 自由标签
    custom: dict[str, Any] = field(default_factory=dict)  # 扩展字段

    # 时间戳
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（用于 JSON 存储）。"""
        result = {
            "meu_id": self.meu_id,
            "repo": self.repo,
            "commit": self.commit,
            "file_path": self.file_path,
            "symbol": self.symbol,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "snippet": self.snippet,
            "evidence_type": self.evidence_type.value
                           if isinstance(self.evidence_type, EvidenceType)
                           else self.evidence_type,
            "language": self.language,
            "framework": self.framework,
            "relation": self.relation.to_dict() if self.relation else None,
            "extracted_by": self.extracted_by,
            "confidence": self.confidence,
            "tags": list(self.tags),
            "custom": dict(self.custom),
            "timestamp": self.timestamp,
        }
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MinimumEvidenceUnit":
        """从 dict 反序列化。"""
        rel = data.get("relation")
        call_rel = None
        if isinstance(rel, dict):
            call_rel = CallRelation(
                type=rel.get("type", ""),
                caller=rel.get("caller"),
                callee=rel.get("callee"),
                direction=rel.get("direction", "caller_to_callee"),
                guard=rel.get("guard"),
                taint_level=rel.get("taint_level"),
                description=rel.get("description"),
            )
        ev_type = data.get("evidence_type", EvidenceType.CODE_SNIPPET)
        if isinstance(ev_type, str):
            try:
                ev_type = EvidenceType(ev_type)
            except ValueError:
                ev_type = EvidenceType.CODE_SNIPPET
        return cls(
            meu_id=data["meu_id"],
            repo=data.get("repo", ""),
            commit=data.get("commit", ""),
            file_path=data.get("file_path", ""),
            symbol=data.get("symbol", ""),
            line_start=data.get("line_start"),
            line_end=data.get("line_end"),
            snippet=data.get("snippet", ""),
            evidence_type=ev_type,
            language=data.get("language", ""),
            framework=data.get("framework", ""),
            relation=call_rel,
            extracted_by=data.get("extracted_by", ""),
            confidence=float(data.get("confidence", 1.0)),
            tags=list(data.get("tags", [])),
            custom=dict(data.get("custom", {})),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
        )

    @property
    def location(self) -> str:
        """人类可读的代码位置描述。"""
        parts = [self.file_path]
        if self.symbol:
            parts.append(f"::{self.symbol}")
        if self.line_start is not None:
            if self.line_end and self.line_end != self.line_start:
                parts.append(f":{self.line_start}-{self.line_end}")
            else:
                parts.append(f":{self.line_start}")
        return "".join(parts)

    @property
    def evidence_ref(self) -> str:
        """生成 evidence_ref 字符串（可在 Skill output 中引用）。"""
        return f"{self.meu_id}"


# ─────────────────────────────────────────────────────────────────────────────
# ResearchArtifact
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ResearchArtifact:
    """
    研究中间产物。
    对应一个 Skill 的一次执行输出。
    """
    artifact_id: str
    run_id: str                         # 所属 WorkflowRun ID
    skill_id: str
    skill_version: str
    kind: ArtifactKind
    content: dict[str, Any]             # Skill 原始输出 JSON
    summary: str = ""                   # 一句话摘要
    tags: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)  # 引用的 MEU IDs
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "run_id": self.run_id,
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "kind": self.kind.value if isinstance(self.kind, ArtifactKind) else self.kind,
            "content": self.content,
            "summary": self.summary,
            "tags": list(self.tags),
            "evidence_refs": list(self.evidence_refs),
            "created_at": self.created_at,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Conclusion
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Conclusion:
    """
    带证据引用的结论。
    每个 Conclusion 必须至少引用一个 MEU evidence_ref。
    """
    claim: str                           # 结论内容
    category: str = ""                   # 分类：architecture | security | data | behavior
    confidence: float = 0.5              # 0.0-1.0
    evidence_refs: list[str] = field(default_factory=list)  # 必须非空
    tags: list[str] = field(default_factory=list)
    uncertainty_note: str = ""            # 置信度低时的说明

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "category": self.category,
            "confidence": self.confidence,
            "evidence_refs": list(self.evidence_refs),
            "tags": list(self.tags),
            "uncertainty_note": self.uncertainty_note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Conclusion":
        return cls(
            claim=data["claim"],
            category=data.get("category", ""),
            confidence=float(data.get("confidence", 0.5)),
            evidence_refs=list(data.get("evidence_refs", [])),
            tags=list(data.get("tags", [])),
            uncertainty_note=data.get("uncertainty_note", ""),
        )

    def validate(self) -> list[str]:
        """验证结论合法性。"""
        errors: list[str] = []
        if not self.claim.strip():
            errors.append("Conclusion claim cannot be empty.")
        if not self.evidence_refs:
            errors.append(
                f"Conclusion '{self.claim[:50]}...' has no evidence_refs. "
                "Every conclusion must reference at least one MEU."
            )
        if not (0.0 <= self.confidence <= 1.0):
            errors.append(f"Confidence must be in [0,1], got {self.confidence}.")
        if self.confidence < 0.4 and not self.uncertainty_note.strip():
            errors.append(
                f"Confidence={self.confidence} < 0.4 requires uncertainty_note."
            )
        return errors


# ─────────────────────────────────────────────────────────────────────────────
# EvidenceLink
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EvidenceLink:
    """
    证据链接：结论到 MEU 的映射关系。
    用于追踪"哪个结论引用了哪个证据"。
    """
    link_id: str
    conclusion_artifact_id: str         # 包含该结论的 Artifact ID
    conclusion_claim: str                # 结论文本（去重用）
    evidence_ref: str                    # 引用的 MEU ID 或 evidence_ref 字符串
    evidence_meu_id: str | None = None   # 解析后的 MEU ID（若可解析）
    is_valid: bool = True                # 该链接是否仍然有效
    validated_at: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Confidence helpers
# ─────────────────────────────────────────────────────────────────────────────

def confidence_level(score: float) -> Confidence:
    """根据分数返回置信度级别。"""
    if score >= 0.8:
        return Confidence.HIGH
    elif score >= 0.5:
        return Confidence.MEDIUM
    elif score >= 0.4:
        return Confidence.LOW
    return Confidence.UNCERTAIN
