"""
core/pipeline_executor.py — 支持 DAG 分层并行执行的 Workflow 执行器

在 WorkflowExecutor 的基础上，按 DAG 分层：

- Stage 1（可并行）：repo_discovery
- Stage 2（可并行）：entrypoints_detection, sink_detection, dependency_analysis
- Stage 3（可并行）：call_graph_trace, auth_logic_detection
- Stage 4：input_flow_analysis（等 stage 2, 3）
- Stage 5：attack_surface_mapping（等 stage 4）
- Stage 6：vulnerability_hypothesis（等 stage 5）
- Stage 7：exploit_generation（等 stage 6）
- Stage 8：report_generation（等全部）

同一层内的 steps 可以并行执行（threading / concurrent.futures），
跨层必须等待依赖完成。

使用方式：
    executor = PipelineExecutor(
        skill_registry=registry,
        llm_adapter=llm_adapter,
        tool_executor=tool_executor,
        evidence_store=evidence_store,
        memory_store=memory_store,
    )
    result = executor.run_parallel("vulnerability_research", inputs)
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.workflow_orchestrator import (
    WorkflowDefinition,
    WorkflowStep,
    WorkflowExecutor,
    WorkflowRun,
)
from core.skill_loader import SkillRegistry, SkillPackage
from core.execution_context import ExecutionStatus
from memory.evidence_store import EvidenceStore
from memory.memory_store import MemoryStore
from agent.llm_adapter import LLMAdapter
from agent.loop import LoopConfig
from agent.skill_agent import SkillAgent, SkillAgentResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Execution Layer
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ExecutionLayer:
    """
    同一批可以并行执行的 steps。

    所有 step_ids 在这层的 steps 没有相互依赖。
    """
    stage: int
    step_ids: list[str]
    steps: list[WorkflowStep]
    depends_on: list[str]   # 依赖的 layer 的 stage 编号

    @property
    def can_parallelize(self) -> bool:
        return len(self.step_ids) > 1


# ─────────────────────────────────────────────────────────────────────────────
# ParallelStepResult
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ParallelStepResult:
    """并行执行的单步结果。"""
    step_id: str
    skill_id: str
    status: str              # succeeded | failed
    output: dict[str, Any] | None
    agent_result: SkillAgentResult | None
    duration_ms: int
    error: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# PipelineExecutor
# ─────────────────────────────────────────────────────────────────────────────

class PipelineExecutor:
    """
    支持 DAG 分层并行执行的 Workflow 执行器。

    在 WorkflowExecutor 的基础上，增加了：
    1. DAG 分层算法（将步骤分组到可并行执行的 layers）
    2. ThreadPoolExecutor 并行执行同层 steps
    3. 层间屏障（barrier）确保依赖步骤全部完成后再执行下一层
    """

    def __init__(
        self,
        skill_registry: SkillRegistry,
        llm_adapter: LLMAdapter,
        tool_executor: Any,   # tools.executor.ToolExecutor
        evidence_store: EvidenceStore | None = None,
        memory_store: MemoryStore | None = None,
        loop_config: LoopConfig | None = None,
        max_workers_per_layer: int = 4,
    ):
        self.skill_registry = skill_registry
        self.llm = llm_adapter
        self.tools = tool_executor
        self.evidence_store = evidence_store
        self.memory_store = memory_store
        self.loop_config = loop_config or LoopConfig()
        self.max_workers = max_workers_per_layer

    def run_parallel(
        self,
        workflow_id: str,
        inputs: dict[str, Any],
        version: str | None = None,
    ) -> WorkflowRun:
        """
        执行 Workflow，按 DAG 分层并行执行 steps。

        Args:
            workflow_id : Workflow ID
            inputs      : Workflow 输入参数
            version     : Workflow 版本

        Returns:
            WorkflowRun，包含所有 step 的执行结果
        """
        # 加载 Workflow 定义
        executor = WorkflowExecutor(
            skill_registry=self.skill_registry,
            prompt_runner=None,
        )
        workflows_dir = Path(__file__).parent.parent / "workflows"
        executor.load_from_directory(workflows_dir)

        try:
            wf_def = executor.get(workflow_id, version)
        except Exception as exc:
            logger.error("无法加载 Workflow '%s': %s", workflow_id, exc)
            raise

        # 创建 WorkflowRun
        run_id = str(uuid.uuid4())
        run_ctx = WorkflowRun(
            run_id=run_id,
            workflow_id=wf_def.id,
            workflow_version=wf_def.version,
            inputs=inputs,
            status=ExecutionStatus.RUNNING,
        )

        # 分层
        layers = self._group_by_dag_level(wf_def)
        logger.info(
            "[Pipeline] '%s' — 共 %d 层，%d 个 steps",
            workflow_id,
            len(layers),
            len(wf_def.steps),
        )

        # 汇总所有 step outputs
        all_outputs: dict[str, dict[str, Any]] = {}
        step_results: dict[str, Any] = {}

        # 逐层执行
        for layer in layers:
            layer_label = f"Stage {layer.stage} ({len(layer.step_ids)} steps)"
            logger.info(
                "[Pipeline] === %s === (可并行: %s, 依赖层: %s)",
                layer_label,
                layer.can_parallelize,
                layer.depends_on,
            )

            # 并行执行当前层的 steps
            layer_step_results = self._run_layer_parallel(
                layer=layer,
                wf_def=wf_def,
                inputs=inputs,
                all_outputs=all_outputs,
                run_id=run_id,
            )

            # 汇总结果
            for step_id, result in layer_step_results.items():
                all_outputs[step_id] = result.output or {}
                step_results[step_id] = {
                    "skill_id": result.skill_id,
                    "status": result.status,
                    "duration_ms": result.duration_ms,
                    "error": result.error,
                }

            # 检查是否有失败
            failed = [sid for sid, r in layer_step_results.items() if r.status == "failed"]
            if failed:
                logger.warning("[Pipeline] 层 %d 有失败步骤: %s", layer.stage, failed)
                if layer.stage == 1:
                    # 关键路径失败 → 终止
                    run_ctx.status = ExecutionStatus.FAILED
                    run_ctx.error = f"关键步骤失败: {', '.join(failed)}"
                    run_ctx.finished_at = datetime.now(timezone.utc).isoformat()
                    return run_ctx

        # 全部成功
        run_ctx.status = ExecutionStatus.SUCCEEDED
        run_ctx.finished_at = datetime.now(timezone.utc).isoformat()
        return run_ctx

    # ── DAG 分层算法 ──────────────────────────────────────────────────────────

    def _group_by_dag_level(self, definition: WorkflowDefinition) -> list[ExecutionLayer]:
        """
        Kahn 算法变体：将 DAG 分层。

        每一层内的 steps 没有相互依赖，可以并行执行。
        层之间按依赖关系顺序执行。
        """
        step_map: dict[str, WorkflowStep] = {s.id: s for s in definition.steps}

        # 入度（被多少步骤依赖）
        in_degree: dict[str, int] = {s.id: 0 for s in definition.steps}
        # 出度（依赖多少步骤）
        out_degree: dict[str, int] = defaultdict(int)

        for step in definition.steps:
            for dep in step.depends_on:
                if dep in in_degree:
                    in_degree[dep] += 1
            out_degree[step.id] = len(step.depends_on)

        layers: list[ExecutionLayer] = []
        stage = 1
        completed: set[str] = set()

        while len(completed) < len(definition.steps):
            # 找出所有入度为 0 且未完成的 steps（可以并行）
            ready = [
                s for s in definition.steps
                if s.id not in completed and in_degree[s.id] == 0
            ]

            if not ready:
                raise ValueError(
                    f"Cyclic dependency detected or unresolved steps: "
                    f"completed={completed}, remaining="
                    f"{[s.id for s in definition.steps if s.id not in completed]}"
                )

            ready_ids = [s.id for s in ready]
            ready_deps = set()
            for s in ready:
                for dep in s.depends_on:
                    ready_deps.add(dep)

            layers.append(ExecutionLayer(
                stage=stage,
                step_ids=ready_ids,
                steps=ready,
                depends_on=sorted(set(ready_deps)),
            ))

            # 标记这批 steps 为完成（更新入度）
            for s in ready:
                completed.add(s.id)
                # 其他依赖此 step 的步骤入度 -1
                for other in definition.steps:
                    if s.id in other.depends_on:
                        in_degree[other.id] -= 1

            stage += 1

        return layers

    # ── 层内并行执行 ─────────────────────────────────────────────────────────

    def _run_layer_parallel(
        self,
        layer: ExecutionLayer,
        wf_def: WorkflowDefinition,
        inputs: dict[str, Any],
        all_outputs: dict[str, dict[str, Any]],
        run_id: str,
    ) -> dict[str, ParallelStepResult]:
        """
        并行执行一个 layer 内的所有 steps。
        """
        results: dict[str, ParallelStepResult] = {}

        if len(layer.steps) == 1:
            # 单步，不需要并行
            result = self._execute_single_step(
                step=layer.steps[0],
                inputs=inputs,
                all_outputs=all_outputs,
                run_id=run_id,
            )
            results[layer.steps[0].id] = result
            return results

        # 多步并行
        with ThreadPoolExecutor(max_workers=min(len(layer.steps), self.max_workers)) as pool:
            futures: dict[str, Future] = {}

            for step in layer.steps:
                future = pool.submit(
                    self._execute_single_step,
                    step=step,
                    inputs=inputs,
                    all_outputs=all_outputs,
                    run_id=run_id,
                )
                futures[step.id] = future

            for step_id, future in futures.items():
                try:
                    results[step_id] = future.result(
                        timeout=self.loop_config.timeout_per_step_sec * len(layer.steps)
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("[Pipeline] Step '%s' 执行异常", step_id)
                    results[step_id] = ParallelStepResult(
                        step_id=step_id,
                        skill_id="unknown",
                        status="failed",
                        output=None,
                        agent_result=None,
                        duration_ms=0,
                        error=str(exc),
                    )

        return results

    def _execute_single_step(
        self,
        step: WorkflowStep,
        inputs: dict[str, Any],
        all_outputs: dict[str, dict[str, Any]],
        run_id: str,
    ) -> ParallelStepResult:
        """执行单个 Step。"""
        import time
        started = time.monotonic()

        # 解析 step inputs（变量替换）
        resolved_inputs = self._resolve_inputs(step.with_params, inputs, all_outputs)

        # 获取 Skill
        try:
            skill_pkg = self.skill_registry.get(step.skill)
        except Exception as exc:
            return ParallelStepResult(
                step_id=step.id,
                skill_id=step.skill,
                status="failed",
                output=None,
                agent_result=None,
                duration_ms=0,
                error=f"Skill not found: {exc}",
            )

        # 创建 SkillAgent
        agent = SkillAgent(
            skill=skill_pkg,
            llm_adapter=self.llm,
            tool_executor=self.tools,
            evidence_store=self.evidence_store,
            memory_store=self.memory_store,
            loop_config=self.loop_config,
        )

        # 执行
        try:
            agent_result = agent.run(
                inputs=resolved_inputs,
                context={"session_id": run_id, "repo_path": inputs.get("repo_path", "")},
                run_id=run_id,
            )
            duration_ms = int((time.monotonic() - started) * 1000)

            logger.info(
                "[Pipeline] ✓ %s (skill=%s) — %s, %dms, %d tool_calls",
                step.id,
                step.skill,
                agent_result.status,
                duration_ms,
                agent_result.loop_result.tool_call_count if agent_result.loop_result else 0,
            )

            return ParallelStepResult(
                step_id=step.id,
                skill_id=step.skill,
                status=agent_result.status,
                output=agent_result.skill_output,
                agent_result=agent_result,
                duration_ms=duration_ms,
                error=agent_result.error,
            )

        except Exception as exc:  # noqa: BLE001
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.exception("[Pipeline] ✗ %s 异常: %s", step.id, exc)
            return ParallelStepResult(
                step_id=step.id,
                skill_id=step.skill,
                status="failed",
                output=None,
                agent_result=None,
                duration_ms=duration_ms,
                error=str(exc),
            )

    # ── 变量解析 ─────────────────────────────────────────────────────────────

    def _resolve_inputs(
        self,
        with_params: dict[str, Any],
        workflow_inputs: dict[str, Any],
        step_outputs: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """
        解析 step 的 with_params 中的 ${variables} 引用。

        支持：
        - ${inputs.x}       → workflow_inputs["x"]
        - ${steps.S.outputs.y} → step_outputs["S"]["y"]
        """
        import re
        import json

        def resolve_value(val: Any) -> Any:
            if isinstance(val, str):
                def repl(m: re.Match) -> str:
                    path = m.group(1)
                    parts = path.split(".")
                    if parts[0] == "inputs" and len(parts) >= 2:
                        v = workflow_inputs.get(parts[1])
                        if v is not None:
                            return json.dumps(v)
                    if parts[0] == "steps" and len(parts) >= 4 and parts[2] == "outputs":
                        step_id = parts[1]
                        key = parts[3]
                        v = step_outputs.get(step_id, {}).get(key)
                        if v is not None:
                            return json.dumps(v)
                    return m.group(0)

                resolved = re.sub(r"\$\{([^}]+)\}", repl, val)
                try:
                    return json.loads(resolved)
                except (json.JSONDecodeError, TypeError):
                    return resolved
            elif isinstance(val, dict):
                return {k: resolve_value(v) for k, v in val.items()}
            elif isinstance(val, list):
                return [resolve_value(item) for item in val]
            return val

        return resolve_value(with_params)
