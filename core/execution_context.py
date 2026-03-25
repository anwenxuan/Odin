"""
Execution Context Module

管理 Workflow 执行期间的状态与变量传递。
支持 `${inputs.x}` 和 `${steps.step_id.outputs.y}` 两种变量引用。
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core.errors import ContextVariableNotFoundError, WorkflowError


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


# ─────────────────────────────────────────────────────────────────────────────
# Step Result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StepResult:
    """单个 Workflow Step 的执行结果。"""

    step_id: str
    skill_id: str
    status: ExecutionStatus
    output: dict[str, Any] | None = None
    error: str | None = None
    started_at: str | None = None   # ISO8601
    finished_at: str | None = None  # ISO8601
    duration_ms: int | None = None
    attempt: int = 1

    @property
    def succeeded(self) -> bool:
        return self.status == ExecutionStatus.SUCCEEDED


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Run
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WorkflowRun:
    """一次 Workflow 执行的全局上下文。"""

    run_id: str
    workflow_id: str
    workflow_version: str
    inputs: dict[str, Any]
    status: ExecutionStatus = ExecutionStatus.PENDING
    step_results: dict[str, StepResult] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: str | None = None
    error: str | None = None

    def add_step_result(self, result: StepResult) -> None:
        self.step_results[result.step_id] = result

    def get_step_output(self, step_id: str) -> dict[str, Any]:
        if step_id not in self.step_results:
            raise ContextVariableNotFoundError(f"steps.{step_id}")
        result = self.step_results[step_id]
        if not result.succeeded:
            raise WorkflowError(
                f"Cannot use output of failed step '{step_id}' in variable resolution."
            )
        if result.output is None:
            return {}
        return result.output

    def to_summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "status": self.status.value,
            "inputs": self.inputs,
            "steps": {
                sid: {
                    "skill_id": sr.skill_id,
                    "status": sr.status.value,
                    "duration_ms": sr.duration_ms,
                    "attempt": sr.attempt,
                    "error": sr.error,
                }
                for sid, sr in self.step_results.items()
            },
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Execution Context
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ExecutionContext:
    """
    Workflow 运行时的变量上下文。

    支持两类变量引用：
      - `${inputs.x}`        → Workflow 顶层输入
      - `${steps.S.outputs.y}` → 某个 Step 的输出
    """

    workflow_run: WorkflowRun
    _cache: dict[str, Any] = field(default_factory=dict)

    def resolve(self, template: str | Any) -> Any:
        """解析变量引用并返回实际值。"""
        if not isinstance(template, str):
            return template

        # 匹配 ${inputs.x} 或 ${steps.S.outputs.y}
        import re
        pattern = r"\$\{([^}]+)\}"
        resolved = template

        for match in re.finditer(pattern, template):
            full_ref = match.group(0)
            var_path = match.group(1)
            value = self._resolve_path(var_path)
            if value is None:
                raise ContextVariableNotFoundError(full_ref)
            resolved = resolved.replace(full_ref, _json_dumps_placeholder(value))

        if resolved != template:
            import json
            try:
                return json.loads(resolved)
            except json.JSONDecodeError:
                return resolved

        return template

    def _resolve_path(self, path: str) -> Any:
        """解析 inputs.X 或 steps.S.outputs.Y 路径（支持数组下标）。"""
        # 处理数组下标：steps.S.outputs.Y[0].name → 先切出数组路径，再处理下标
        bracket_match = re.search(r"\[(\d+)\]", path)
        array_index: int | None = None
        base_path = path
        if bracket_match:
            array_index = int(bracket_match.group(1))
            base_path = path[:bracket_match.start()]

        parts = base_path.split(".")
        if not parts:
            return None

        value: Any = None
        # inputs.X[0] → inputs → X
        if parts[0] == "inputs":
            value = _get_nested(self.workflow_run.inputs, parts[1:])

        # steps.S.outputs.Y[0] → steps → S → outputs → Y
        elif parts[0] == "steps" and len(parts) >= 4 and parts[2] == "outputs":
            step_id = parts[1]
            key = parts[3]
            try:
                step_output = self.workflow_run.get_step_output(step_id)
                value = _get_nested(step_output, [key])
            except (ContextVariableNotFoundError, WorkflowError):
                return None

        if array_index is not None:
            if isinstance(value, list) and 0 <= array_index < len(value):
                return value[array_index]
            return None

        return value

    def get_resolved_inputs(self, step_id: str, with_params: dict[str, Any]) -> dict[str, Any]:
        """
        将 step 'with' 字典中的模板变量全部解析后返回。
        处理列表和嵌套结构中的模板。
        """
        return self._deep_resolve(with_params)

    def _deep_resolve(self, obj: Any) -> Any:
        if isinstance(obj, str):
            return self.resolve(obj)
        elif isinstance(obj, dict):
            return {k: self._deep_resolve(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._deep_resolve(item) for item in obj]
        return obj


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_nested(data: dict[str, Any], keys: list[str]) -> Any:
    """按 key 列表安全获取嵌套字典值。"""
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return None
    return current


_placeholder_counter = 0

def _json_dumps_placeholder(value: Any) -> str:
    """为非 JSON 兼容类型生成唯一占位符，resolve 后通过字符串替换还原。"""
    global _placeholder_counter
    _placeholder_counter += 1
    return f"__JSON_PLACEHOLDER_{_placeholder_counter}__"


def new_workflow_run(
    workflow_id: str,
    workflow_version: str,
    inputs: dict[str, Any],
) -> WorkflowRun:
    """工厂函数：创建新的 WorkflowRun。"""
    return WorkflowRun(
        run_id=str(uuid.uuid4()),
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        inputs=inputs,
    )
