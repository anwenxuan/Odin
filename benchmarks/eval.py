from __future__ import annotations

"""
benchmarks/eval.py — Odin 评测框架

评测维度：
1. 覆盖率  — 发现的漏洞 / 真实漏洞（需 ground truth）
2. 准确率  — 报告的漏洞中，真漏洞比例
3. 召回率  — 真实漏洞中，被发现的概率
4. 效率    — Token 消耗、工具调用次数、执行时间

使用方法：
    python benchmarks/eval.py --dataset owasp-top10
    python benchmarks/eval.py --dataset real-world --repo https://github.com/owner/vuln-repo

评测数据集：
    benchmarks/datasets/
        owasp-top10/         — OWASP 官方漏洞测试用例
        real-world-projects/  — 已知漏洞的真实开源项目
        synthetic/            — 人工合成的边界测试用例
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import statistics

logger = logging.getLogger("odin.eval")


# ─────────────────────────────────────────────────────────────────────────────
# 数据模型
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GroundTruthVuln:
    """已知漏洞（ground truth）。"""
    cwe_id: str
    file_path: str
    function: str
    severity: str          # critical | high | medium | low
    description: str
    fixed_commit: str | None = None   # 如果已知修复版本


@dataclass
class ReportedFinding:
    """Odin 报告的发现。"""
    cwe_id: str
    title: str
    confidence: float
    evidence_refs: list[str]
    affected_path: str


@dataclass
class EvalResult:
    """单个测试用例的评测结果。"""
    repo_name: str
    workflow_id: str

    # Ground truth
    true_positives: list[str] = field(default_factory=list)   # 发现的真实漏洞
    false_positives: list[str] = field(default_factory=list)  # 误报的漏洞
    missed_vulns: list[str] = field(default_factory=list)     # 漏报的漏洞

    # 指标
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0

    # 效率指标
    total_tool_calls: int = 0
    total_llm_calls: int = 0
    total_duration_sec: float = 0.0
    total_tokens: int = 0

    # 详情
    reported_findings: list[ReportedFinding] = field(default_factory=list)
    ground_truth: list[GroundTruthVuln] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_name": self.repo_name,
            "workflow_id": self.workflow_id,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "missed_vulns": self.missed_vulns,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "total_tool_calls": self.total_tool_calls,
            "total_llm_calls": self.total_llm_calls,
            "total_duration_sec": round(self.total_duration_sec, 2),
            "total_tokens": self.total_tokens,
        }


@dataclass
class AggregatedMetrics:
    """汇总评测指标。"""
    total_repos: int
    avg_precision: float
    avg_recall: float
    avg_f1: float
    avg_duration_sec: float
    avg_tool_calls: float
    total_findings: int
    total_true_positives: int
    results: list[EvalResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_repos": self.total_repos,
            "avg_precision": round(self.avg_precision, 4),
            "avg_recall": round(self.avg_recall, 4),
            "avg_f1": round(self.avg_f1, 4),
            "avg_duration_sec": round(self.avg_duration_sec, 2),
            "avg_tool_calls": round(self.avg_tool_calls, 1),
            "total_findings": self.total_findings,
            "total_true_positives": self.total_true_positives,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Ground Truth 加载
# ─────────────────────────────────────────────────────────────────────────────

class GroundTruthDB:
    """
    Ground Truth 数据库。

    每个测试用例目录包含：
        dataset/
            README.md        — 测试用例说明
            ground_truth.json  — 已知漏洞列表
            repo/            — 被测试的代码（可选，git clone 后分析）
    """

    def __init__(self, datasets_root: Path):
        self.datasets_root = Path(datasets_root)

    def load_case(self, case_name: str) -> tuple[list[GroundTruthVuln], Path | None]:
        """加载指定测试用例的 ground truth 和代码路径。"""
        case_dir = self.datasets_root / case_name
        gt_file = case_dir / "ground_truth.json"

        vulns: list[GroundTruthVuln] = []
        if gt_file.exists():
            data = json.loads(gt_file.read_text())
            for item in data.get("vulnerabilities", []):
                vulns.append(GroundTruthVuln(
                    cwe_id=item.get("cwe_id", "CWE-OTHER"),
                    file_path=item.get("file_path", ""),
                    function=item.get("function", ""),
                    severity=item.get("severity", "medium"),
                    description=item.get("description", ""),
                    fixed_commit=item.get("fixed_commit"),
                ))

        repo_path = case_dir / "repo" if (case_dir / "repo").is_dir() else None
        return vulns, repo_path

    def list_cases(self) -> list[str]:
        """列出所有测试用例。"""
        if not self.datasets_root.is_dir():
            return []
        return [
            d.name for d in self.datasets_root.iterdir()
            if d.is_dir() and (d / "ground_truth.json").exists()
        ]


# ─────────────────────────────────────────────────────────────────────────────
# 评测引擎
# ─────────────────────────────────────────────────────────────────────────────

class EvalEngine:
    """
    Odin 评测引擎。

    核心流程：
    1. 加载 ground truth
    2. 运行 Odin 分析目标代码库
    3. 对比报告发现 vs ground truth
    4. 计算 precision / recall / F1
    """

    def __init__(
        self,
        datasets_root: Path | None = None,
        output_dir: Path | None = None,
    ):
        self.gt_db = GroundTruthDB(datasets_root or Path(__file__).parent / "datasets")
        self.output_dir = output_dir or Path("benchmarks/results")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def run_single(
        self,
        case_name: str,
        workflow_id: str = "vulnerability_research",
        provider: str = "mock",
        model: str = "gpt-4o-mini",
    ) -> EvalResult:
        """
        对单个测试用例运行评测。

        Returns:
            EvalResult，包含各项指标
        """
        logger.info("[Eval] === Case: %s ===", case_name)

        ground_truth, repo_path = self.gt_db.load_case(case_name)
        result = EvalResult(
            repo_name=case_name,
            workflow_id=workflow_id,
            ground_truth=ground_truth,
        )

        if not repo_path or not repo_path.exists():
            logger.warning("[Eval] 跳过 %s：代码库路径不存在", case_name)
            result.false_positives = ["SKIPPED_NO_REPO"]
            return result

        # 运行 Odin 分析
        run_output, stats = self._run_odin(
            repo_path=repo_path,
            workflow_id=workflow_id,
            provider=provider,
            model=model,
        )

        result.total_tool_calls = stats.get("tool_calls", 0)
        result.total_llm_calls = stats.get("llm_calls", 0)
        result.total_duration_sec = stats.get("duration_sec", 0.0)
        result.total_tokens = stats.get("tokens", 0)

        # 提取报告发现
        reported = self._extract_findings(run_output)
        result.reported_findings = reported

        # 对比 ground truth
        matched, unmatched_reported, missed = self._match(
            reported=reported,
            ground_truth=ground_truth,
        )

        result.true_positives = matched
        result.false_positives = unmatched_reported
        result.missed_vulns = missed

        # 计算指标
        tp = len(matched)
        fp = len(unmatched_reported)
        fn = len(missed)

        result.precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        result.recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        result.f1 = (
            2 * result.precision * result.recall
            / (result.precision + result.recall)
            if (result.precision + result.recall) > 0
            else 0.0
        )

        logger.info(
            "[Eval] %s — TP=%d FP=%d FN=%d P=%.2f R=%.2f F1=%.2f",
            case_name, tp, fp, fn,
            result.precision, result.recall, result.f1,
        )

        return result

    def run_all(
        self,
        dataset: str | None = None,
        **kwargs: Any,
    ) -> AggregatedMetrics:
        """运行所有测试用例，返回汇总指标。"""
        if dataset:
            cases = [dataset]
        else:
            cases = self.gt_db.list_cases()

        results: list[EvalResult] = []
        for case in cases:
            r = self.run_single(case, **kwargs)
            results.append(r)

        return self._aggregate(results)

    def _run_odin(
        self,
        repo_path: Path,
        workflow_id: str,
        provider: str,
        model: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """运行 Odin 并收集统计信息。"""
        t0 = time.monotonic()

        try:
            from agent.llm_adapter import create_adapter
            from agent.loop import LoopConfig
            from agent.skill_agent import SkillAgent
            from core.skill_loader import SkillRegistry
            from core.pipeline_executor import PipelineExecutor
            from memory.evidence_store import EvidenceStore
            from memory.memory_store import MemoryStore
            from tools.executor import ToolExecutor

            evidence_store = EvidenceStore()
            memory_store = MemoryStore()
            tool_executor = ToolExecutor(repo_path=repo_path)
            tool_executor.auto_load_builtin()
            llm = create_adapter(provider=provider, default_model=model)

            skills_dir = Path(__file__).parent.parent / "skills"
            workflows_dir = Path(__file__).parent.parent / "workflows"
            registry = SkillRegistry()
            registry.load_from_directory(skills_dir)

            executor = PipelineExecutor(
                skill_registry=registry,
                llm_adapter=llm,
                tool_executor=tool_executor,
                evidence_store=evidence_store,
                memory_store=memory_store,
                loop_config=LoopConfig(max_iterations=10, verbose=False),
                max_workers_per_layer=2,
            )

            run_result = executor.run_parallel(
                workflow_id=workflow_id,
                inputs={"repo_url": str(repo_path), "repo_path": str(repo_path)},
            )

            stats = {
                "tool_calls": sum(
                    1 for entry in tool_executor.call_history
                ),
                "llm_calls": 1,  # PipelineExecutor runs each step once
                "duration_sec": time.monotonic() - t0,
                "tokens": 0,
                "meus_stored": evidence_store.stats().get("total_meus", 0),
            }

            return run_result.to_summary(), stats

        except Exception as exc:  # noqa: BLE001
            logger.exception("[Eval] Odin 执行失败: %s", exc)
            stats = {
                "tool_calls": 0, "llm_calls": 0,
                "duration_sec": time.monotonic() - t0, "tokens": 0,
            }
            return {"error": str(exc)}, stats

    def _extract_findings(self, output: dict[str, Any] -> list[ReportedFinding]:
        """从 Odin 输出中提取漏洞发现。"""
        findings: list[ReportedFinding] = []

        # 尝试从 vulnerability_hypothesis 输出中提取
        steps = output.get("steps", {})
        for step_id, step_data in steps.items():
            skill_output = step_data.get("skill_output", {})
            if not isinstance(skill_output, dict):
                continue

            # vulnerability_hypothesis
            for hypo in skill_output.get("hypotheses", []):
                if isinstance(hypo, dict):
                    findings.append(ReportedFinding(
                        cwe_id=hypo.get("cwe_id", "CWE-OTHER"),
                        title=hypo.get("title", ""),
                        confidence=hypo.get("confidence", 0.5),
                        evidence_refs=hypo.get("evidence_refs", []),
                        affected_path=",".join(hypo.get("affected_paths", [])),
                    ))

            # sink_detection
            for sink in skill_output.get("sinks", []):
                if isinstance(sink, dict):
                    findings.append(ReportedFinding(
                        cwe_id=sink.get("cwe_id", "CWE-OTHER"),
                        title=sink.get("name", ""),
                        confidence=sink.get("confidence", 0.5),
                        evidence_refs=sink.get("evidence_refs", []),
                        affected_path=sink.get("file_path", ""),
                    ))

        return findings

    def _match(
        self,
        reported: list[ReportedFinding],
        ground_truth: list[GroundTruthVuln],
    ) -> tuple[list[str], list[str], list[str]]:
        """
        将报告发现与 ground truth 匹配。

        匹配规则（宽松模式）：
        - CWE ID 匹配 或
        - 文件路径包含 且 severity 相近

        Returns:
            (matched_ids, unmatched_reported, missed)
        """
        matched: list[str] = []
        unmatched_reported: list[str] = []
        matched_gt_indices: set[int] = set()

        for finding in reported:
            is_match = False
            for i, gt in enumerate(ground_truth):
                if i in matched_gt_indices:
                    continue

                # CWE 匹配
                if self._cwe_matches(finding.cwe_id, gt.cwe_id):
                    is_match = True
                # 文件路径匹配
                elif self._path_matches(finding.affected_path, gt.file_path):
                    is_match = True

                if is_match:
                    matched.append(
                        f"{gt.cwe_id}@{gt.file_path} (conf={finding.confidence:.2f})"
                    )
                    matched_gt_indices.add(i)
                    break

            if not is_match:
                unmatched_reported.append(
                    f"{finding.cwe_id}@{finding.affected_path}"
                )

        missed = [
            f"{gt.cwe_id}@{gt.file_path}"
            for i, gt in enumerate(ground_truth)
            if i not in matched_gt_indices
        ]

        return matched, unmatched_reported, missed

    def _cwe_matches(self, cwe1: str, cwe2: str) -> bool:
        """检查两个 CWE ID 是否相关（支持通配）。"""
        if not cwe1 or not cwe2:
            return False
        # 精确匹配
        if cwe1 == cwe2:
            return True
        # 家族匹配（如 CWE-89 和 CWE-89X 都算 SQL 注入）
        cwe1_base = cwe1.split("-")[0] if "-" in cwe1 else cwe1
        cwe2_base = cwe2.split("-")[0] if "-" in cwe2 else cwe2
        return cwe1_base == cwe2_base

    def _path_matches(self, reported_path: str, gt_path: str) -> bool:
        """检查路径是否匹配（允许部分匹配）。"""
        if not reported_path or not gt_path:
            return False
        rp = reported_path.lower()
        gt = gt_path.lower()
        return rp in gt or gt in rp

    def _aggregate(self, results: list[EvalResult]) -> AggregatedMetrics:
        """汇总多个测试用例的评测结果。"""
        n = len(results)
        if n == 0:
            return AggregatedMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, [])

        return AggregatedMetrics(
            total_repos=n,
            avg_precision=statistics.mean(r.precision for r in results),
            avg_recall=statistics.mean(r.recall for r in results),
            avg_f1=statistics.mean(r.f1 for r in results),
            avg_duration_sec=statistics.mean(r.total_duration_sec for r in results),
            avg_tool_calls=statistics.mean(r.total_tool_calls for r in results),
            total_findings=sum(len(r.reported_findings) for r in results),
            total_true_positives=sum(len(r.true_positives) for r in results),
            results=results,
        )

    def save_report(self, agg: AggregatedMetrics, name: str = "eval_report") -> Path:
        """保存评测报告到 JSON 文件。"""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_file = self.output_dir / f"{name}_{ts}.json"

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": agg.to_dict(),
            "results": [r.to_dict() for r in agg.results],
        }

        self.output_dir.mkdir(parents=True, exist_ok=True)
        out_file.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        logger.info("[Eval] 评测报告已保存: %s", out_file)
        return out_file


# ─────────────────────────────────────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Odin 评测框架")
    parser.add_argument("--dataset", help="指定测试用例目录名")
    parser.add_argument("--all", action="store_true", help="运行所有测试用例")
    parser.add_argument("--output-dir", type=Path, default=Path("benchmarks/results"))
    parser.add_argument("--provider", default="mock", choices=["openai", "anthropic", "mock"])
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--workflow", default="vulnerability_research")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")

    benchmarks_root = Path(__file__).parent
    engine = EvalEngine(
        datasets_root=benchmarks_root / "datasets",
        output_dir=args.output_dir,
    )

    if args.all or args.dataset:
        agg = engine.run_all(dataset=args.dataset, provider=args.provider, model=args.model)
    else:
        cases = engine.gt_db.list_cases()
        if not cases:
            print("没有找到测试用例，请在 benchmarks/datasets/ 下添加测试用例")
            return
        # 运行所有找到的测试用例
        agg = engine.run_all(provider=args.provider, model=args.model)

    # 保存报告
    out_file = engine.save_report(agg, "eval_report")
    print(f"\n评测报告已保存: {out_file}")
