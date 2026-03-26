"""
agent/verification/ — Verification System

Verification System 负责验证 AI 结论的正确性。

目录结构：
    verification/
        __init__.py    — 公共接口
        engine.py      — 验证引擎
        rules.py       — 内置验证规则
"""

from agent.verification.engine import (
    VerificationEngine,
    VerificationRule,
    VerificationResult,
    RuleResult,
    VerificationStatus,
)
from agent.verification.rules import (
    EvidencePresenceRule,
    CodeSyntaxRule,
    UnitTestRule,
    ConfidenceThresholdRule,
    CrossReferenceRule,
    register_builtin_rules,
)

__all__ = [
    "VerificationEngine",
    "VerificationRule",
    "VerificationResult",
    "RuleResult",
    "VerificationStatus",
    "EvidencePresenceRule",
    "CodeSyntaxRule",
    "UnitTestRule",
    "ConfidenceThresholdRule",
    "CrossReferenceRule",
    "register_builtin_rules",
]
