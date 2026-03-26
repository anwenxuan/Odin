"""
agent/verification/engine.py — Verification Engine

验证引擎负责验证 AI 结论的正确性。

支持多种验证方式：
- Rule Verification   ：基于规则的验证
- Unit Test Verification：执行单元测试验证
- Execution Verification：实际运行代码验证
- Cross-reference     ：交叉引用验证
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Verification Types
# ─────────────────────────────────────────────────────────────────────────────


class VerificationStatus(str, Enum):
    """验证状态。"""
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class RuleResult:
    """单条验证规则的结果。"""
    rule_name: str
    status: VerificationStatus
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_name": self.rule_name,
            "status": self.status.value,
            "message": self.message,
            "details": self.details,
            "duration_ms": self.duration_ms,
            "timestamp": self.timestamp,
        }


@dataclass
class VerificationResult:
    """验证结果 — 多个 RuleResult 的聚合。"""
    target_id: str                    # 要验证的对象 ID（conclusion_id / file_path 等）
    passed: bool
    overall_status: VerificationStatus
    rule_results: list[RuleResult] = field(default_factory=list)
    confidence_delta: float = 0.0     # 验证后置信度变化
    recommendations: list[str] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "passed": self.passed,
            "overall_status": self.overall_status.value,
            "rule_results": [r.to_dict() for r in self.rule_results],
            "confidence_delta": self.confidence_delta,
            "recommendations": self.recommendations,
            "timestamp": self.timestamp,
        }

    def summary(self) -> str:
        passed = sum(1 for r in self.rule_results if r.status == VerificationStatus.PASSED)
        failed = sum(1 for r in self.rule_results if r.status == VerificationStatus.FAILED)
        return (
            f"VerificationResult({self.target_id}): "
            f"{passed} passed, {failed} failed, "
            f"delta={self.confidence_delta:+.2f}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# VerificationRule — 验证规则抽象
# ─────────────────────────────────────────────────────────────────────────────


class VerificationRule(ABC):
    """
    验证规则抽象基类。

    实现此接口即可定义自定义验证规则。

    示例：
        class MyRule(VerificationRule):
            @property
            def name(self) -> str:
                return "my_custom_rule"

            def applies_to(self, target: Any) -> bool:
                return target.get("type") == "my_type"

            async def verify(self, target: Any, context: dict) -> RuleResult:
                # 执行验证
                return RuleResult(rule_name=self.name, status=VerificationStatus.PASSED)
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """规则名称。"""
        ...

    @property
    def description(self) -> str:
        """规则描述。"""
        return ""

    @abstractmethod
    def applies_to(self, target: dict[str, Any]) -> bool:
        """
        判断此规则是否适用于目标对象。

        Args:
            target: 要验证的目标对象（如 Conclusion / 代码修改等）

        Returns:
            True if this rule should be applied
        """
        ...

    @abstractmethod
    async def verify(
        self,
        target: dict[str, Any],
        evidence: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> RuleResult:
        """
        执行验证。

        Args:
            target  : 要验证的目标对象
            evidence: 关联的 Evidence 列表
            context : 验证上下文（代码库路径、沙箱等）

        Returns:
            RuleResult
        """
        ...


# ─────────────────────────────────────────────────────────────────────────────
# Verification Engine
# ─────────────────────────────────────────────────────────────────────────────


class VerificationEngine:
    """
    验证引擎。

    核心职责：
    1. 管理验证规则注册
    2. 对目标执行所有适用的验证规则
    3. 聚合结果，计算置信度变化
    4. 提供验证建议

    使用方式：
        engine = VerificationEngine()
        engine.register(MyCustomRule())

        result = await engine.verify_conclusion(
            conclusion={"id": "c-001", "claim": "..."},
            evidence=[...],
            context={"repo_path": "/repo"},
        )
    """

    def __init__(self, strict: bool = True):
        self.strict = strict
        self.rules: list[VerificationRule] = []
        self._history: list[VerificationResult] = []

    def register(self, rule: VerificationRule) -> None:
        """注册验证规则。"""
        self.rules.append(rule)
        logger.info("[VerificationEngine] Registered rule: %s", rule.name)

    def unregister(self, rule_name: str) -> None:
        """注销验证规则。"""
        self.rules = [r for r in self.rules if r.name != rule_name]

    def list_rules(self) -> list[str]:
        """列出所有已注册规则。"""
        return [r.name for r in self.rules]

    # ── 验证入口 ─────────────────────────────────────────────────────────

    async def verify_conclusion(
        self,
        conclusion: dict[str, Any],
        evidence: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> VerificationResult:
        """
        验证结论的正确性。

        Args:
            conclusion: 要验证的结论（Conclusion dict）
            evidence : 关联的 Evidence 列表
            context : 验证上下文

        Returns:
            VerificationResult
        """
        target_id = conclusion.get("id", "unknown")
        rule_results: list[RuleResult] = []
        total_delta: float = 0.0

        applicable_rules = [r for r in self.rules if r.applies_to(conclusion)]

        if not applicable_rules:
            return VerificationResult(
                target_id=target_id,
                passed=True,
                overall_status=VerificationStatus.SKIPPED,
                rule_results=[],
                confidence_delta=0.0,
                recommendations=["No verification rules applied — conclusion accepted by default"],
            )

        for rule in applicable_rules:
            import time
            t0 = time.monotonic()
            try:
                result = await rule.verify(conclusion, evidence, context)
                result.duration_ms = int((time.monotonic() - t0) * 1000)
            except Exception as exc:
                logger.exception("[VerificationEngine] Rule %s raised", rule.name)
                result = RuleResult(
                    rule_name=rule.name,
                    status=VerificationStatus.ERROR,
                    message=str(exc),
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
            rule_results.append(result)

            # 累计置信度变化
            delta = self._compute_delta(result)
            total_delta += delta

        passed = all(r.status == VerificationStatus.PASSED for r in rule_results)
        if self.strict:
            passed = passed and len(rule_results) > 0

        overall_status = (
            VerificationStatus.PASSED if passed
            else VerificationStatus.FAILED
        )

        verification = VerificationResult(
            target_id=target_id,
            passed=passed,
            overall_status=overall_status,
            rule_results=rule_results,
            confidence_delta=total_delta,
            recommendations=self._generate_recommendations(rule_results),
        )

        self._history.append(verification)
        logger.info("[VerificationEngine] %s", verification.summary())

        return verification

    async def verify_code_change(
        self,
        file_path: str,
        old_code: str,
        new_code: str,
        context: dict[str, Any],
    ) -> VerificationResult:
        """
        验证代码修改的正确性。

        执行步骤：
        1. 语法检查（Python: py_compile / JS: eslint）
        2. 单元测试运行（如有）
        3. 回归测试
        """
        rule_results: list[RuleResult] = []
        target_id = f"code_change:{file_path}"

        import time
        for rule in self.rules:
            if not rule.applies_to({"type": "code_change", "file_path": file_path}):
                continue

            t0 = time.monotonic()
            try:
                result = await rule.verify(
                    {"type": "code_change", "file_path": file_path,
                     "old_code": old_code, "new_code": new_code},
                    [],
                    context,
                )
                result.duration_ms = int((time.monotonic() - t0) * 1000)
            except Exception as exc:
                logger.exception("Code change verification rule %s failed", rule.name)
                result = RuleResult(
                    rule_name=rule.name,
                    status=VerificationStatus.ERROR,
                    message=str(exc),
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
            rule_results.append(result)

        passed = all(r.status == VerificationStatus.PASSED for r in rule_results)
        return VerificationResult(
            target_id=target_id,
            passed=passed,
            overall_status=VerificationStatus.PASSED if passed else VerificationStatus.FAILED,
            rule_results=rule_results,
            confidence_delta=0.0,
        )

    # ── 内部工具 ─────────────────────────────────────────────────────────

    def _compute_delta(self, result: RuleResult) -> float:
        """根据 RuleResult 计算置信度变化。"""
        if result.status == VerificationStatus.PASSED:
            return 0.0
        if result.status == VerificationStatus.FAILED:
            return -0.1
        if result.status == VerificationStatus.SKIPPED:
            return 0.0
        return -0.05  # ERROR

    def _generate_recommendations(self, results: list[RuleResult]) -> list[str]:
        """生成验证失败后的改进建议。"""
        recommendations: list[str] = []
        for result in results:
            if result.status == VerificationStatus.FAILED:
                if "evidence" in result.message.lower():
                    recommendations.append(
                        f"Rule '{result.rule_name}' failed: add more evidence references"
                    )
                elif "confidence" in result.message.lower():
                    recommendations.append(
                        f"Rule '{result.rule_name}' failed: lower confidence or add caveats"
                    )
                else:
                    recommendations.append(
                        f"Rule '{result.rule_name}' failed: review {result.message}"
                    )
        return recommendations

    def get_history(self) -> list[VerificationResult]:
        """获取验证历史。"""
        return list(self._history)

    def get_stats(self) -> dict[str, Any]:
        """获取验证统计。"""
        return {
            "total_verifications": len(self._history),
            "passed": sum(1 for r in self._history if r.passed),
            "failed": sum(1 for r in self._history if not r.passed),
            "rules_registered": len(self.rules),
        }
