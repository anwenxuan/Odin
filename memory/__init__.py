"""
AI Code Research System - Memory Package

Memory System: 存储研究中间产物、证据索引、结论与引用关系
"""
from memory.models import (
    EvidenceType,
    Confidence,
    ArtifactKind,
    CallRelation,
    MinimumEvidenceUnit,
    ResearchArtifact,
    Conclusion,
    EvidenceLink,
    confidence_level,
)
from memory.evidence_store import EvidenceStore
from memory.memory_store import MemoryStore
from memory.working import WorkingMemory, StepSummary, MemoryContext

__version__ = "0.2.0"

__all__ = [
    # models
    "EvidenceType",
    "Confidence",
    "ArtifactKind",
    "CallRelation",
    "MinimumEvidenceUnit",
    "ResearchArtifact",
    "Conclusion",
    "EvidenceLink",
    "confidence_level",
    # stores
    "EvidenceStore",
    "MemoryStore",
    # working memory
    "WorkingMemory",
    "StepSummary",
    "MemoryContext",
]
