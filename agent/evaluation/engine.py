"""
agent/evaluation/engine.py — Evaluation Engine

评估 Agent 系统表现的核心引擎。

评估维度：
- 任务完成率（Completion Rate）
- 幻觉率（Hallucination Rate）
- 工具调用成功率（Tool Call Success Rate）
- 证据覆盖率（Evidence Coverage）
- 验证通过率（Verification Pass Rate）
- Token 效率（Token Efficiency）
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation Types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class EvaluationMetrics:
    """
    单次评估的量化指标。
    """
    # 任务完成
    task_id: str = ""
    completion_rate: float = 0.0          # 0.0-1.0
    task_status: str = ""               # completed / failed / timeout

    # 幻觉率
    hallucination_rate: float = 0.0     # 无证据支撑的结论占比
    hallucinations: list[str] = field(default_factory=list)  # 幻觉结论 ID

    # 工具调用
    tool_call_success_rate: float = 0.0  # 成功工具调用占比
    tool_call_count: int = 0
    tool_call_errors: int = 0

    # Evidence
    evidence_coverage: float = 0.0      # 结论的证据覆盖率
    evidence_refs_found: int = 0
    evidence_refs_total: int = 0

    # 验证
    verification_pass_rate: float = 0.0  # 验证通过率
    verification_failed: int = 0
    verification_total: int = 0

    # 性能
    avg_steps: float = 0.0
    total_steps: int = 0
    total_tokens: int = 0
    duration_seconds: float = 0.0
    tokens_per_step: float = 0.0

    # 置信度
    avg_confidence: float = 0.0
    high_confidence_ratio: float = 0.0   # 高置信度结论占比

    # 综合评分
    overall_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def score_letter(self) -> str:
        """根据综合评分返回字母等级。"""
        if self.overall_score >= 0.9:
            return "A"
        elif self.overall_score >= 0.8:
            return "B"
        elif self.overall_score >= 0.7:
            return "C"
        elif self.overall_score >= 0.6:
            return "D"
        return "F"


@dataclass
class EvaluationReport:
    """
    完整评估报告。
    """
    task_id: str
    metrics: EvaluationMetrics
    raw_data: dict[str, Any] = field(default_factory=dict)
    recommendations: list[str] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "metrics": self.metrics.to_dict(),
            "raw_data": self.raw_data,
            "recommendations": self.recommendations,
            "generated_at": self.generated_at,
        }

    def summary(self) -> str:
        m = self.metrics
        return (
            f"Evaluation({self.task_id}): "
            f"score={m.overall_score:.2f} ({m.score_letter()}) | "
            f"completion={m.completion_rate:.0%} | "
            f"hallucination={m.hallucination_rate:.0%} | "
            f"tool_success={m.tool_call_success_rate:.0%} | "
            f"evidence_coverage={m.evidence_coverage:.0%} | "
            f"verification={m.verification_pass_rate:.0%}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation Engine
# ─────────────────────────────────────────────────────────────────────────────


class EvaluationEngine:
    """
    评估引擎。

    从 Agent 执行日志、Evidence Store、Verification 结果中收集数据，
    计算各项评估指标，生成评估报告。

    使用方式：
        engine = EvaluationEngine(metrics_store, evidence_store)
        report = await engine.evaluate_task("task-001")
        print(report.summary())
    """

    def __init__(
        self,
        metrics_store: Any | None = None,
        evidence_store: Any | None = None,
    ):
        self.metrics_store = metrics_store
        self.evidence_store = evidence_store
        self._reports: list[EvaluationReport] = []

    async def evaluate_task(
        self,
        task_id: str,
        task_result: Any | None = None,
        runtime_state: Any | None = None,
    ) -> EvaluationReport:
        """
        对单个任务进行评估。

        Args:
            task_id     : Task ID
            task_result : TaskResult（包含执行结果）
            runtime_state: RuntimeState（包含执行过程数据）

        Returns:
            EvaluationReport
        """
        # 计算各项指标
        metrics = EvaluationMetrics(task_id=task_id)

        if task_result:
            metrics.task_status = getattr(task_result, "status", "unknown")
            metrics.completion_rate = 1.0 if metrics.task_status == "completed" else 0.0
            metrics.total_steps = getattr(task_result, "steps_completed", 0)
            metrics.total_tokens = getattr(task_result, "total_tokens", 0)
            metrics.duration_seconds = getattr(task_result, "duration_seconds", 0.0)
            metrics.evidence_refs_total = len(getattr(task_result, "evidence_refs", []))

        if runtime_state:
            # 工具调用统计
            tool_records = getattr(runtime_state, "tool_call_records", [])
            metrics.tool_call_count = len(tool_records)
            metrics.tool_call_errors = sum(
                1 for r in tool_records if not getattr(r, "success", True)
            )
            if metrics.tool_call_count > 0:
                metrics.tool_call_success_rate = (
                    metrics.tool_call_count - metrics.tool_call_errors
                ) / metrics.tool_call_count

            # Evidence 统计
            evidence = getattr(runtime_state, "evidence", [])
            metrics.evidence_refs_found = len(evidence)
            if metrics.evidence_refs_total > 0:
                metrics.evidence_coverage = min(
                    metrics.evidence_refs_found / metrics.evidence_refs_total,
                    1.0,
                )

            # Step 统计
            steps = getattr(runtime_state, "recent_steps", [])
            metrics.total_steps = len(steps)
            if steps:
                metrics.avg_steps = sum(
                    getattr(s, "duration_ms", 0) for s in steps
                ) / len(steps) / 1000.0

        # 幻觉率计算
        metrics.hallucination_rate = self._calc_hallucination_rate(task_id, metrics)

        # Token 效率
        if metrics.total_steps > 0 and metrics.total_tokens > 0:
            metrics.tokens_per_step = metrics.total_tokens / metrics.total_steps

        # 综合评分
        metrics.overall_score = self._compute_overall_score(metrics)

        # 生成建议
        recommendations = self._generate_recommendations(metrics)

        report = EvaluationReport(
            task_id=task_id,
            metrics=metrics,
            raw_data={
                "task_result": task_result.to_dict() if task_result and hasattr(task_result, "to_dict") else {},
                "runtime_state": runtime_state.to_dict() if runtime_state and hasattr(runtime_state, "to_dict") else {},
            },
            recommendations=recommendations,
        )

        self._reports.append(report)
        logger.info("[Evaluation] %s", report.summary())

        return report

    async def evaluate_batch(
        self,
        task_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        批量评估多个任务。

        Returns:
            聚合的评估统计
        """
        all_reports: list[EvaluationReport] = []
        for result in task_results:
            task_id = result.get("task_id", "unknown")
            report = await self.evaluate_task(task_id, result)
            all_reports.append(report)

        # 聚合统计
        total = len(all_reports)
        if total == 0:
            return {}

        metrics_list = [r.metrics for r in all_reports]

        return {
            "total_tasks": total,
            "avg_completion_rate": sum(m.completion_rate for m in metrics_list) / total,
            "avg_hallucination_rate": sum(m.hallucination_rate for m in metrics_list) / total,
            "avg_tool_success_rate": sum(m.tool_call_success_rate for m in metrics_list) / total,
            "avg_evidence_coverage": sum(m.evidence_coverage for m in metrics_list) / total,
            "avg_overall_score": sum(m.overall_score for m in metrics_list) / total,
            "grade_distribution": {
                m.score_letter(): sum(1 for mm in metrics_list if mm.score_letter() == m.score_letter())
                for m in metrics_list
            },
            "reports": [r.to_dict() for r in all_reports],
        }

    # ── 内部计算 ──────────────────────────────────────────────────────

    def _calc_hallucination_rate(
        self,
        task_id: str,
        metrics: EvaluationMetrics,
    ) -> float:
        """计算幻觉率：无证据支撑的结论占比。"""
        if self.evidence_store is None:
            # 无法计算，返回默认值
            return 0.0

        try:
            conclusions = self.evidence_store.get_conclusions(task_id)
            if not conclusions:
                return 0.0

            unverified = 0
            for c in conclusions:
                refs = c.get("evidence_refs", []) if isinstance(c, dict) else []
                if not refs:
                    unverified += 1
                    metrics.hallucinations.append(
                        c.get("id", "unknown") if isinstance(c, dict) else str(c)
                    )

            return unverified / len(conclusions)
        except Exception:
            return 0.0

    def _compute_overall_score(self, metrics: EvaluationMetrics) -> float:
        """
        计算综合评分（加权平均）。

        权重：
        - 任务完成率  30%
        - 幻觉率      20%  (1 - rate)
        - 工具成功率  15%
        - 证据覆盖率  20%
        - 验证通过率  15%
        """
        score = (
            metrics.completion_rate * 0.30 +
            (1.0 - metrics.hallucination_rate) * 0.20 +
            metrics.tool_call_success_rate * 0.15 +
            metrics.evidence_coverage * 0.20 +
            metrics.verification_pass_rate * 0.15
        )
        return min(max(score, 0.0), 1.0)

    def _generate_recommendations(self, metrics: EvaluationMetrics) -> list[str]:
        """根据指标生成改进建议。"""
        recommendations: list[str] = []

        if metrics.completion_rate < 0.5:
            recommendations.append(
                "任务完成率较低，考虑增加 max_steps 或优化工具调用效率"
            )

        if metrics.hallucination_rate > 0.2:
            recommendations.append(
                f"幻觉率较高（{metrics.hallucination_rate:.0%}），"
                "建议强化 Evidence 收集流程，确保每个结论都有证据支撑"
            )

        if metrics.tool_call_success_rate < 0.8:
            recommendations.append(
                f"工具调用成功率较低（{metrics.tool_call_success_rate:.0%}），"
                "检查工具实现的稳定性和参数校验"
            )

        if metrics.evidence_coverage < 0.5:
            recommendations.append(
                f"证据覆盖率较低（{metrics.evidence_coverage:.0%}），"
                "建议在 Skill prompt 中强化 Evidence 引用要求"
            )

        if metrics.verification_pass_rate < 0.7:
            recommendations.append(
                f"验证通过率较低（{metrics.verification_pass_rate:.0%}），"
                "检查验证规则的有效性，避免过度严格"
            )

        if metrics.tokens_per_step > 2000:
            recommendations.append(
                f"Token 效率较低（{metrics.tokens_per_step:.0f}/step），"
                "考虑优化 prompt 长度或使用更高效的工具调用"
            )

        if not recommendations:
            recommendations.append("所有指标表现良好，继续保持")

        return recommendations

    def get_reports(self) -> list[EvaluationReport]:
        """获取所有评估报告。"""
        return list(self._reports)

    def get_aggregate_stats(self) -> dict[str, Any]:
        """获取聚合统计。"""
        if not self._reports:
            return {}
        metrics_list = [r.metrics for r in self._reports]
        total = len(metrics_list)
        return {
            "total_evaluated": total,
            "avg_overall_score": sum(m.overall_score for m in metrics_list) / total,
            "avg_hallucination_rate": sum(m.hallucination_rate for m in metrics_list) / total,
            "avg_completion_rate": sum(m.completion_rate for m in metrics_list) / total,
        }
