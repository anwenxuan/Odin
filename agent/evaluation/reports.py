"""
agent/evaluation/reports.py — Evaluation Report Generation

评估报告生成器，支持多种格式：
- Markdown：适合人类阅读
- JSON：适合程序处理
- HTML：适合 Web 展示
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from agent.evaluation.engine import EvaluationReport, EvaluationEngine


# ─────────────────────────────────────────────────────────────────────────────
# Report Generator
# ─────────────────────────────────────────────────────────────────────────────


class ReportGenerator:
    """
    评估报告生成器。

    将 EvaluationReport 转换为多种格式。

    使用方式：
        gen = ReportGenerator()
        md = gen.generate_markdown(report)
        json_str = gen.generate_json(report)
    """

    def generate_markdown(self, report: EvaluationReport) -> str:
        """生成 Markdown 格式报告。"""
        m = report.metrics

        lines = [
            f"# Evaluation Report: {report.task_id}",
            "",
            f"**Generated:** {report.generated_at}",
            f"**Overall Score:** {m.overall_score:.2f} ({m.score_letter()})",
            "",
            "## Summary",
            "",
            f"| Metric | Value |",
            f"| --- | --- |",
            f"| Completion Rate | {m.completion_rate:.1%} |",
            f"| Hallucination Rate | {m.hallucination_rate:.1%} |",
            f"| Tool Call Success | {m.tool_call_success_rate:.1%} |",
            f"| Evidence Coverage | {m.evidence_coverage:.1%} |",
            f"| Verification Pass | {m.verification_pass_rate:.1%} |",
            f"| Avg Steps | {m.avg_steps:.1f} |",
            f"| Total Tokens | {m.total_tokens:,} |",
            f"| Duration | {m.duration_seconds:.1f}s |",
            "",
            "## Performance Details",
            "",
            f"- Task Status: **{m.task_status}**",
            f"- Total Steps: {m.total_steps}",
            f"- Tool Calls: {m.tool_call_count} total, {m.tool_call_errors} errors",
            f"- Evidence Refs: {m.evidence_refs_found} / {m.evidence_refs_total}",
            f"- Tokens per Step: {m.tokens_per_step:.1f}",
            "",
        ]

        if m.hallucinations:
            lines.extend([
                "## Hallucinations Detected",
                "",
                *(f"- `{hid}`" for hid in m.hallucinations[:10]),
                "",
            ])

        if report.recommendations:
            lines.extend([
                "## Recommendations",
                "",
                *(f"- {rec}" for rec in report.recommendations),
                "",
            ])

        lines.append("## Raw Data")
        lines.append("```json")
        lines.append(json.dumps(report.raw_data, indent=2, ensure_ascii=False))
        lines.append("```")

        return "\n".join(lines)

    def generate_json(self, report: EvaluationReport) -> str:
        """生成 JSON 格式报告。"""
        return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)

    def generate_html(self, report: EvaluationReport) -> str:
        """生成 HTML 格式报告。"""
        m = report.metrics
        score_color = self._score_color(m.overall_score)

        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Evaluation: {report.task_id}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; }}
  .score {{ font-size: 48px; font-weight: bold; color: {score_color}; }}
  .grade {{ font-size: 24px; background: {score_color}22; padding: 4px 12px; border-radius: 8px; }}
  .metric {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #eee; }}
  .bar {{ background: #e0e0e0; border-radius: 4px; height: 8px; margin-top: 4px; }}
  .bar-fill {{ background: {score_color}; border-radius: 4px; height: 8px; }}
  .recommendations li {{ margin: 4px 0; }}
  .hallucinations {{ background: #fff3cd; padding: 12px; border-radius: 8px; }}
</style>
</head>
<body>
<h1>Evaluation Report</h1>
<p><strong>Task:</strong> {report.task_id}</p>
<p><strong>Generated:</strong> {report.generated_at}</p>

<div style="display: flex; align-items: center; gap: 20px; margin: 20px 0;">
  <span class="score">{m.overall_score:.2f}</span>
  <span class="grade">{m.score_letter()}</span>
</div>

<h2>Key Metrics</h2>
<div class="metric">
  <span>Completion Rate</span>
  <span>{m.completion_rate:.1%}</span>
</div>
<div class="metric">
  <span>Hallucination Rate</span>
  <span style="color: {'red' if m.hallucination_rate > 0.2 else 'green'}">{m.hallucination_rate:.1%}</span>
</div>
<div class="metric">
  <span>Tool Success Rate</span>
  <span>{m.tool_call_success_rate:.1%}</span>
</div>
<div class="metric">
  <span>Evidence Coverage</span>
  <span>{m.evidence_coverage:.1%}</span>
</div>
<div class="metric">
  <span>Verification Pass</span>
  <span>{m.verification_pass_rate:.1%}</span>
</div>

<h2>Recommendations</h2>
<ul>
{"".join(f"<li>{r}</li>" for r in report.recommendations)}
</ul>
</body>
</html>"""

    def generate_aggregate_report(self, batch_result: dict[str, Any]) -> str:
        """生成批量评估汇总报告。"""
        lines = [
            "# Batch Evaluation Report",
            "",
            f"**Total Tasks:** {batch_result.get('total_tasks', 0)}",
            "",
            "## Aggregate Metrics",
            "",
            f"| Metric | Average |",
            f"| --- | --- |",
            f"| Overall Score | {batch_result.get('avg_overall_score', 0):.2f} |",
            f"| Completion Rate | {batch_result.get('avg_completion_rate', 0):.1%} |",
            f"| Hallucination Rate | {batch_result.get('avg_hallucination_rate', 0):.1%} |",
            f"| Tool Success Rate | {batch_result.get('avg_tool_success_rate', 0):.1%} |",
            f"| Evidence Coverage | {batch_result.get('avg_evidence_coverage', 0):.1%} |",
            "",
        ]

        grade_dist = batch_result.get("grade_distribution", {})
        if grade_dist:
            lines.extend([
                "## Grade Distribution",
                "",
                f"| Grade | Count |",
                f"| --- | --- |",
                *(f"| {g} | {c} |" for g, c in sorted(grade_dist.items())),
                "",
            ])

        return "\n".join(lines)

    def _score_color(self, score: float) -> str:
        if score >= 0.9:
            return "#22c55e"   # green
        elif score >= 0.7:
            return "#f59e0b"   # yellow
        elif score >= 0.5:
            return "#f97316"   # orange
        return "#ef4444"        # red


# ─────────────────────────────────────────────────────────────────────────────
# Markdown Report Formatter (convenience wrapper)
# ─────────────────────────────────────────────────────────────────────────────


class MarkdownReportFormatter:
    """
    便捷的 Markdown 报告格式化工具。

    用于将原始数据转换为格式化的 Markdown。
    """

    @staticmethod
    def format_task_summary(
        task_id: str,
        status: str,
        steps: int,
        duration: float,
        score: float,
    ) -> str:
        return f"| {task_id} | {status} | {steps} | {duration:.1f}s | {score:.2f} |"

    @staticmethod
    def format_table(headers: list[str], rows: list[list[str]]) -> str:
        sep = "| " + " | ".join(headers) + " |"
        divider = "| " + " | ".join("---" for _ in headers) + " |"
        body = "\n".join("| " + " | ".join(row) + " |" for row in rows)
        return f"{sep}\n{divider}\n{body}"
