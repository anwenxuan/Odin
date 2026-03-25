"""
Workflow Orchestrator Module

解析 Workflow YAML，按 DAG/顺序执行 steps，管理上下文传参与失败重试。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from core.errors import (
    WorkflowCyclicDependencyError,
    WorkflowNotFoundError,
    WorkflowParseError,
    WorkflowStepError,
    WorkflowStepTimeoutError,
)
from core.execution_context import (
    ExecutionContext,
    ExecutionStatus,
    StepResult,
    WorkflowRun,
    new_workflow_run,
)
from core.skill_loader import SkillRegistry
from core.prompt_runner import PromptRunner, PromptTemplateLoader
from memory.evidence_store import EvidenceStore
from memory.memory_store import MemoryStore


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Definition Models
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WorkflowStep:
    """Workflow Step 定义（解析自 YAML）。"""
    id: str
    skill: str
    depends_on: list[str] = field(default_factory=list)
    with_params: dict[str, Any] = field(default_factory=dict)
    timeout_sec: int = 120
    retry: int = 0          # 失败重试次数
    fallback: str | None = None  # 失败时 fallback 到哪个 step
    outputs: dict[str, str] = field(default_factory=dict)  # name: jsonpath


@dataclass
class WorkflowDefinition:
    """完整 Workflow 定义（解析自 YAML）。"""
    id: str
    version: str
    description: str
    inputs_schema: dict[str, Any] | None = None
    steps: list[WorkflowStep] = field(default_factory=list)
    outputs: dict[str, str] = field(default_factory=dict)


@dataclass
class WorkflowExecutor:
    """
    Workflow 执行器。

    使用方法：
        registry = SkillRegistry()
        runner   = PromptRunner()
        executor  = WorkflowExecutor(registry, runner)
        result    = executor.run("vulnerability_research", {"repo_url": "..."})
    """
    skill_registry: SkillRegistry
    prompt_runner: PromptRunner | None = None
    evidence_store: EvidenceStore | None = None
    memory_store: MemoryStore | None = None
    _workflows: dict[str, WorkflowDefinition] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ── Workflow 定义加载 ───────────────────────────────────────────────────

    def load_from_file(self, path: Path | str) -> WorkflowDefinition:
        """从 YAML 文件加载 Workflow 定义。"""
        path = Path(path)
        if not path.is_file():
            raise WorkflowNotFoundError(f"Workflow file not found: {path}")

        try:
            raw = path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise WorkflowParseError(f"Invalid YAML in {path}: {exc}") from exc

        if not isinstance(data, dict):
            raise WorkflowParseError(f"Workflow YAML must be a root dict: {path}")

        return self._parse_workflow(data, path)

    def load_from_directory(self, root: Path | str) -> list[WorkflowDefinition]:
        """扫描目录加载所有 workflow.yaml。"""
        root = Path(root)
        loaded: list[WorkflowDefinition] = []
        for subdir in sorted(root.iterdir()):
            if not subdir.is_dir():
                continue
            wf_path = subdir / "workflow.yaml"
            if wf_path.is_file():
                wf = self.load_from_file(wf_path)
                self.register(wf)
                loaded.append(wf)
        return loaded

    def register(self, definition: WorkflowDefinition) -> None:
        """注册一个 Workflow 定义。"""
        key = f"{definition.id}@{definition.version}"
        with self._lock:
            self._workflows[key] = definition

    def get(self, workflow_id: str, version: str | None = None) -> WorkflowDefinition:
        """获取已注册的 Workflow。"""
        with self._lock:
            key = f"{workflow_id}@{version}" if version else None
            if key and key in self._workflows:
                return self._workflows[key]
            candidates = {
                k: v for k, v in self._workflows.items()
                if v.id == workflow_id
            }
            if not candidates:
                raise WorkflowNotFoundError(
                    f"Workflow '{workflow_id}' not found."
                )
            if version:
                raise WorkflowNotFoundError(
                    f"Workflow '{workflow_id}@{version}' not found."
                )
            return candidates[sorted(candidates.keys())[-1]]

    def _parse_workflow(
        self,
        data: dict[str, Any],
        source_path: Path,
    ) -> WorkflowDefinition:
        """解析 YAML dict 为 WorkflowDefinition。"""
        required = ["id", "version", "steps"]
        for field_name in required:
            if field_name not in data:
                raise WorkflowParseError(
                    f"Workflow missing required field: '{field_name}' "
                    f"(in {source_path})"
                )

        steps = []
        for i, step_data in enumerate(data.get("steps", []), 1):
            if not isinstance(step_data, dict):
                raise WorkflowParseError(
                    f"Step #{i} must be a dict: {step_data}"
                )
            if "id" not in step_data:
                raise WorkflowParseError(
                    f"Step #{i} missing 'id' field: {step_data}"
                )
            if "skill" not in step_data:
                raise WorkflowParseError(
                    f"Step '{step_data['id']}' missing 'skill' field."
                )
            steps.append(WorkflowStep(
                id=str(step_data["id"]),
                skill=str(step_data["skill"]),
                depends_on=list(step_data.get("depends_on", [])),
                with_params=dict(step_data.get("with", {})),
                timeout_sec=int(step_data.get("timeout_sec", 120)),
                retry=int(step_data.get("retry", 0)),
                fallback=step_data.get("fallback"),
                outputs=dict(step_data.get("outputs", {})),
            ))

        return WorkflowDefinition(
            id=str(data["id"]),
            version=str(data["version"]),
            description=str(data.get("description", "")),
            inputs_schema=data.get("inputs_schema"),
            steps=steps,
            outputs=dict(data.get("outputs", {})),
        )

    # ── Workflow 执行 ───────────────────────────────────────────────────────

    def run(
        self,
        workflow_id: str,
        inputs: dict[str, Any],
        version: str | None = None,
    ) -> WorkflowRun:
        """
        执行指定 Workflow。

        流程：
        1. 解析依赖 DAG
        2. 按拓扑顺序执行 steps（可并行化的 steps 并行执行）
        3. 每步：解析变量引用 → 调用 PromptRunner → 存储 evidence → 记录结果
        4. 全部成功 → 聚合 outputs；任意失败 → 可选 retry/fallback
        """
        definition = self.get(workflow_id, version)
        run_ctx = new_workflow_run(definition.id, definition.version, inputs)

        # 依赖图拓扑排序
        try:
            execution_order = self._topological_sort(definition)
        except ValueError as exc:
            raise WorkflowCyclicDependencyError(str(exc)) from exc

        exec_ctx = ExecutionContext(workflow_run=run_ctx)

        for step in execution_order:
            step_result = self._execute_step(
                step, definition, exec_ctx, run_ctx
            )
            run_ctx.add_step_result(step_result)

            if not step_result.succeeded:
                if step.retry > 0:
                    # 重试逻辑
                    for attempt in range(step.retry):
                        step_result = self._execute_step(
                            step, definition, exec_ctx, run_ctx,
                            attempt=attempt + 2,
                        )
                        run_ctx.add_step_result(step_result)
                        if step_result.succeeded:
                            break
                if not step_result.succeeded:
                    # 失败后更新 run 状态
                    run_ctx.status = ExecutionStatus.FAILED
                    run_ctx.error = (
                        f"Step '{step.id}' failed after {step.retry + 1} attempt(s): "
                        f"{step_result.error}"
                    )
                    run_ctx.finished_at = datetime.now(timezone.utc).isoformat()
                    return run_ctx

        # 全部成功
        run_ctx.status = ExecutionStatus.SUCCEEDED
        run_ctx.finished_at = datetime.now(timezone.utc).isoformat()
        return run_ctx

    def _execute_step(
        self,
        step: WorkflowStep,
        definition: WorkflowDefinition,
        exec_ctx: ExecutionContext,
        run_ctx: WorkflowRun,
        attempt: int = 1,
    ) -> StepResult:
        """执行单个 Step。"""
        started = datetime.now(timezone.utc)
        result = StepResult(
            step_id=step.id,
            skill_id=step.skill,
            status=ExecutionStatus.RUNNING,
            started_at=started.isoformat(),
            attempt=attempt,
        )

        try:
            # 1. 等待依赖 steps 完成（依赖检查已在拓扑排序时保证）
            # 2. 解析变量引用
            resolved_inputs = exec_ctx.get_resolved_inputs(step.id, step.with_params)

            # 3. 加载 Skill
            skill_pkg = self.skill_registry.get(step.skill)

            # 4. 渲染 prompt
            loader = PromptTemplateLoader(skill_pkg.root_dir.parent.parent / "prompts")
            template = loader.load_from_directory(skill_pkg.root_dir)

            # 5. 执行 PromptRunner
            skill_output = self.prompt_runner.run(
                prompt_template=template,
                input_payload={
                    "skill_id": skill_pkg.metadata.id,
                    "repo_path": resolved_inputs.get("repo_path", ""),
                    "inputs": resolved_inputs,
                },
                output_schema=skill_pkg.output_schema,
                skill_id=skill_pkg.metadata.id,
                evidence_required=skill_pkg.metadata.requirements.get(
                    "evidence_required", True
                ),
            )

            # 6. 存储 evidence refs 到 EvidenceStore
            if self.evidence_store is not None:
                self._store_evidence(skill_output, skill_pkg.metadata.id, run_ctx)

            # 7. 记录输出
            finished = datetime.now(timezone.utc)
            result.status = ExecutionStatus.SUCCEEDED
            result.output = skill_output
            result.finished_at = finished.isoformat()
            result.duration_ms = int(
                (finished - started).total_seconds() * 1000
            )

        except Exception as exc:  # noqa: BLE001
            finished = datetime.now(timezone.utc)
            result.status = ExecutionStatus.FAILED
            result.error = f"{type(exc).__name__}: {exc}"
            result.finished_at = finished.isoformat()
            result.duration_ms = int(
                (finished - started).total_seconds() * 1000
            )

        return result

    def _store_evidence(
        self,
        skill_output: dict[str, Any],
        skill_id: str,
        run_ctx: WorkflowRun,
    ) -> None:
        """从 skill output 中提取 MEUs 并存入 EvidenceStore。"""
        if self.evidence_store is None:
            return
        from memory.models import MinimumEvidenceUnit
        import uuid

        meus = self._extract_meus(skill_output, skill_id, run_ctx.run_id)
        for meu in meus:
            self.evidence_store.put(meu)

    def _extract_meus(
        self,
        obj: Any,
        skill_id: str,
        run_id: str,
    ) -> list["MinimumEvidenceUnit"]:
        """递归从 skill output 中提取可转为 MEU 的证据。"""
        from memory.models import MinimumEvidenceUnit
        import uuid

        meus: list[MinimumEvidenceUnit] = []

        def traverse(o: Any) -> None:
            if isinstance(o, dict):
                # 尝试识别 MEU 候选字段
                if "file_path" in o or "snippet" in o or "line_start" in o:
                    try:
                        meu = MinimumEvidenceUnit(
                            meu_id=f"MEU-{uuid.uuid4().hex[:12]}",
                            repo=o.get("repo", ""),
                            commit=o.get("commit", ""),
                            file_path=o.get("file_path", ""),
                            symbol=o.get("symbol", ""),
                            line_start=o.get("line_start"),
                            line_end=o.get("line_end"),
                            snippet=o.get("snippet", ""),
                            relation=o.get("relation"),
                            extracted_by=skill_id,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                        )
                        meus.append(meu)
                    except Exception:
                        pass
                for v in o.values():
                    traverse(v)
            elif isinstance(o, list):
                for item in o:
                    traverse(item)

        traverse(obj)
        return meus

    def _topological_sort(
        self,
        definition: WorkflowDefinition,
    ) -> list[WorkflowStep]:
        """Kahn 算法拓扑排序，检测环。"""
        step_map: dict[str, WorkflowStep] = {s.id: s for s in definition.steps}
        in_degree: dict[str, int] = {
            s.id: 0 for s in definition.steps
        }

        for step in definition.steps:
            for dep in step.depends_on:
                if dep not in step_map:
                    raise ValueError(
                        f"Step '{step.id}' depends on unknown step '{dep}'."
                    )
                in_degree[step.id] += 1

        queue: list[str] = [sid for sid, d in in_degree.items() if d == 0]
        sorted_ids: list[str] = []

        while queue:
            sid = queue.pop(0)
            sorted_ids.append(sid)
            for step in definition.steps:
                if sid in step.depends_on:
                    in_degree[step.id] -= 1
                    if in_degree[step.id] == 0:
                        queue.append(step.id)

        if len(sorted_ids) != len(definition.steps):
            raise ValueError(
                "Cyclic dependency detected in workflow steps."
            )

        return [step_map[sid] for sid in sorted_ids]
