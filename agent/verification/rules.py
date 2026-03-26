"""
agent/verification/rules.py — Built-in Verification Rules

内置验证规则：
1. EvidencePresenceRule   — 证据存在性验证
2. CodeSyntaxRule         — 代码语法验证
3. UnitTestRule           — 单元测试验证
4. ConfidenceThresholdRule — 置信度阈值验证
5. CrossReferenceRule     — 交叉引用验证
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from typing import Any

from pathlib import Path

from agent.verification.engine import (
    VerificationRule,
    RuleResult,
    VerificationStatus,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Evidence Presence Rule
# ─────────────────────────────────────────────────────────────────────────────


class EvidencePresenceRule(VerificationRule):
    """
    Evidence 存在性验证。

    验证每个结论是否至少引用了一个有效的 MEU evidence_ref。
    """

    @property
    def name(self) -> str:
        return "evidence_presence"

    @property
    def description(self) -> str:
        return "验证每个结论是否引用了有效的 Evidence"

    def applies_to(self, target: dict[str, Any]) -> bool:
        return "claim" in target or "evidence_refs" in target

    async def verify(
        self,
        target: dict[str, Any],
        evidence: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> RuleResult:
        evidence_refs = target.get("evidence_refs", [])

        if not evidence_refs:
            return RuleResult(
                rule_name=self.name,
                status=VerificationStatus.FAILED,
                message="Conclusion has no evidence_refs. Every conclusion must reference at least one MEU.",
            )

        # 验证每个 evidence_ref 是否有效
        valid_refs: list[str] = []
        invalid_refs: list[str] = []
        for ref in evidence_refs:
            if self._ref_exists(ref, evidence):
                valid_refs.append(ref)
            else:
                invalid_refs.append(ref)

        if not valid_refs:
            return RuleResult(
                rule_name=self.name,
                status=VerificationStatus.FAILED,
                message=f"No valid evidence refs found. Invalid: {invalid_refs}",
                details={"valid_refs": valid_refs, "invalid_refs": invalid_refs},
            )

        return RuleResult(
            rule_name=self.name,
            status=VerificationStatus.PASSED,
            message=f"{len(valid_refs)} valid evidence refs found",
            details={"valid_refs": valid_refs},
        )

    def _ref_exists(self, ref: str, evidence: list[dict[str, Any]]) -> bool:
        for e in evidence:
            if e.get("meu_id") == ref or ref in e.get("tags", []):
                return True
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Code Syntax Rule
# ─────────────────────────────────────────────────────────────────────────────


class CodeSyntaxRule(VerificationRule):
    """
    代码语法验证。

    验证代码片段的语法正确性。
    支持：Python、JavaScript/TypeScript、Go、Rust
    """

    LANGUAGE_CHECKERS: dict[str, str] = {
        "python": "python3 -m py_compile",
        "js": "node --check",
        "javascript": "node --check",
        "typescript": "npx tsc --noEmit",
        "go": "go build -o /dev/null",
        "rust": "rustc --edition 2021 --emit=metadata",
    }

    @property
    def name(self) -> str:
        return "code_syntax"

    @property
    def description(self) -> str:
        return "验证代码片段的语法正确性"

    def applies_to(self, target: dict[str, Any]) -> bool:
        return target.get("type") == "code_snippet" or "snippet" in target

    async def verify(
        self,
        target: dict[str, Any],
        evidence: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> RuleResult:
        snippet = target.get("snippet", "")
        language = target.get("language", "")

        if not snippet:
            return RuleResult(
                rule_name=self.name,
                status=VerificationStatus.SKIPPED,
                message="No code snippet to verify",
            )

        checker_cmd = self.LANGUAGE_CHECKERS.get(language.lower())
        if not checker_cmd:
            return RuleResult(
                rule_name=self.name,
                status=VerificationStatus.SKIPPED,
                message=f"No syntax checker for language: {language}",
            )

        # 写临时文件
        import tempfile
        suffix_map = {"python": ".py", "js": ".js", "javascript": ".js",
                       "typescript": ".ts", "go": ".go", "rust": ".rs"}
        suffix = suffix_map.get(language.lower(), ".txt")
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="w") as f:
            f.write(snippet)
            temp_path = f.name

        try:
            cmd_parts = checker_cmd.split()
            cmd_parts.append(temp_path)

            proc = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                return RuleResult(
                    rule_name=self.name,
                    status=VerificationStatus.PASSED,
                    message=f"Syntax OK for {language}",
                    details={"language": language},
                )
            else:
                return RuleResult(
                    rule_name=self.name,
                    status=VerificationStatus.FAILED,
                    message=f"Syntax error: {stderr.decode()[:200]}",
                    details={"language": language, "stderr": stderr.decode()[:200]},
                )
        except Exception as exc:
            return RuleResult(
                rule_name=self.name,
                status=VerificationStatus.ERROR,
                message=f"Failed to run syntax checker: {exc}",
            )
        finally:
            import os
            try:
                os.unlink(temp_path)
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Unit Test Rule
# ─────────────────────────────────────────────────────────────────────────────


class UnitTestRule(VerificationRule):
    """
    单元测试验证。

    运行代码仓库中的相关单元测试，验证修改没有引入回归。
    """

    @property
    def name(self) -> str:
        return "unit_test"

    @property
    def description(self) -> str:
        return "运行单元测试验证代码正确性"

    def applies_to(self, target: dict[str, Any]) -> bool:
        return target.get("type") == "code_change" or "file_path" in target

    async def verify(
        self,
        target: dict[str, Any],
        evidence: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> RuleResult:
        repo_path = context.get("repo_path")
        if not repo_path:
            return RuleResult(
                rule_name=self.name,
                status=VerificationStatus.SKIPPED,
                message="No repo_path in context — skipping unit test",
            )

        file_path = target.get("file_path", "")
        test_cmd = self._find_test_command(Path(repo_path), file_path)
        if not test_cmd:
            return RuleResult(
                rule_name=self.name,
                status=VerificationStatus.SKIPPED,
                message="No test command found for this file",
            )

        try:
            cmd_parts = test_cmd.split()
            proc = await asyncio.create_subprocess_exec(
                *cmd_parts,
                cwd=str(repo_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                return RuleResult(
                    rule_name=self.name,
                    status=VerificationStatus.PASSED,
                    message="All unit tests passed",
                    details={"cmd": test_cmd, "stdout": stdout.decode()[:500]},
                )
            else:
                return RuleResult(
                    rule_name=self.name,
                    status=VerificationStatus.FAILED,
                    message=f"Unit tests failed: {stderr.decode()[:200]}",
                    details={"cmd": test_cmd, "returncode": proc.returncode},
                )
        except Exception as exc:
            return RuleResult(
                rule_name=self.name,
                status=VerificationStatus.ERROR,
                message=f"Failed to run tests: {exc}",
            )

    def _find_test_command(self, repo_path: Path, file_path: str) -> str | None:
        if (repo_path / "pytest.ini").exists() or (repo_path / "pyproject.toml").exists():
            if file_path.endswith(".py"):
                return f"python3 -m pytest {file_path} -v --tb=short"
        if (repo_path / "package.json").exists():
            if file_path.endswith((".js", ".ts", ".jsx", ".tsx")):
                return f"npm test -- {file_path} --passWithNoTests"
        if (repo_path / "Cargo.toml").exists():
            if file_path.endswith(".rs"):
                return f"cargo test {Path(file_path).stem} --quiet"
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Confidence Threshold Rule
# ─────────────────────────────────────────────────────────────────────────────


class ConfidenceThresholdRule(VerificationRule):
    """
    置信度阈值验证。

    验证结论的置信度是否达到阈值，并检查是否有 uncertainty_note。
    """

    def __init__(self, min_confidence: float = 0.5):
        self.min_confidence = min_confidence

    @property
    def name(self) -> str:
        return "confidence_threshold"

    @property
    def description(self) -> str:
        return f"验证置信度 >= {self.min_confidence}"

    def applies_to(self, target: dict[str, Any]) -> bool:
        return "confidence" in target or "claim" in target

    async def verify(
        self,
        target: dict[str, Any],
        evidence: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> RuleResult:
        confidence = float(target.get("confidence", 0.0))
        uncertainty_note = target.get("uncertainty_note", "")

        if confidence < self.min_confidence:
            if not uncertainty_note:
                return RuleResult(
                    rule_name=self.name,
                    status=VerificationStatus.FAILED,
                    message=f"Confidence {confidence} < {self.min_confidence} without uncertainty_note",
                    details={"confidence": confidence, "threshold": self.min_confidence},
                )
            else:
                return RuleResult(
                    rule_name=self.name,
                    status=VerificationStatus.PASSED,
                    message=f"Confidence low ({confidence}) but uncertainty_note provided",
                    details={"confidence": confidence},
                )

        return RuleResult(
            rule_name=self.name,
            status=VerificationStatus.PASSED,
            message=f"Confidence {confidence} meets threshold",
            details={"confidence": confidence},
        )


# ─────────────────────────────────────────────────────────────────────────────
# Cross Reference Rule
# ─────────────────────────────────────────────────────────────────────────────


class CrossReferenceRule(VerificationRule):
    """
    交叉引用验证。

    验证多个结论之间是否存在矛盾，以及证据的一致性。
    """

    @property
    def name(self) -> str:
        return "cross_reference"

    @property
    def description(self) -> str:
        return "验证多个结论之间的一致性"

    def applies_to(self, target: dict[str, Any]) -> bool:
        return "conclusions" in target

    async def verify(
        self,
        target: dict[str, Any],
        evidence: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> RuleResult:
        conclusions = target.get("conclusions", [])

        if len(conclusions) < 2:
            return RuleResult(
                rule_name=self.name,
                status=VerificationStatus.SKIPPED,
                message="Need at least 2 conclusions for cross-reference",
            )

        conflicts: list[dict[str, str]] = []
        for i, c1 in enumerate(conclusions):
            for c2 in conclusions[i + 1:]:
                if self._are_contradictory(c1, c2):
                    conflicts.append({
                        "c1": c1.get("claim", "")[:50],
                        "c2": c2.get("claim", "")[:50],
                    })

        if conflicts:
            return RuleResult(
                rule_name=self.name,
                status=VerificationStatus.FAILED,
                message=f"Found {len(conflicts)} contradictory conclusions",
                details={"conflicts": conflicts},
            )

        return RuleResult(
            rule_name=self.name,
            status=VerificationStatus.PASSED,
            message=f"All {len(conclusions)} conclusions are consistent",
        )

    def _are_contradictory(self, c1: dict[str, Any], c2: dict[str, Any]) -> bool:
        claim1 = c1.get("claim", "").lower()
        claim2 = c2.get("claim", "").lower()

        contradictions = [
            ("always", "never"),
            ("safe", "vulnerable"),
            ("correct", "incorrect"),
            ("found", "not found"),
            ("exists", "not exist"),
        ]

        for pos, neg in contradictions:
            if pos in claim1 and neg in claim2:
                return True
            if neg in claim1 and pos in claim2:
                return True

        return False


# ─────────────────────────────────────────────────────────────────────────────
# Built-in Rule Registration Helper
# ─────────────────────────────────────────────────────────────────────────────


def register_builtin_rules(engine: Any) -> None:
    """将所有内置验证规则注册到 VerificationEngine。"""
    engine.register(EvidencePresenceRule())
    engine.register(CodeSyntaxRule())
    engine.register(UnitTestRule())
    engine.register(ConfidenceThresholdRule(min_confidence=0.5))
    engine.register(CrossReferenceRule())
