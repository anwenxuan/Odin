"""
AI Code Research System - Errors Module

定义系统级错误类型，便于分层处理和调试。
"""
from __future__ import annotations

from typing import Optional, List


class AIResearchError(Exception):
    """Base exception for all AI Code Research System errors."""
    pass


# ── Skill Loader Errors ──────────────────────────────────────────────────────

class SkillLoadError(AIResearchError):
    """Raised when a Skill package fails to load or validate."""
    pass


class SkillNotFoundError(AIResearchError):
    """Raised when a requested Skill is not registered."""
    def __init__(self, skill_id: str, version: Optional[str] = None):
        self.skill_id = skill_id
        self.version = version
        msg = f"Skill '{skill_id}'"
        if version:
            msg += f"@{version}"
        msg += " not found in registry."
        super().__init__(msg)


class SkillSchemaError(AIResearchError):
    """Raised when a Skill's input/output schema is invalid."""
    pass


class SkillDependencyError(AIResearchError):
    """Raised when a Skill's declared dependencies cannot be satisfied."""
    pass


# ── Workflow Orchestrator Errors ─────────────────────────────────────────────

class WorkflowError(AIResearchError):
    """Base exception for workflow-level errors."""
    pass


class WorkflowNotFoundError(WorkflowError):
    """Raised when a requested Workflow definition is not found."""
    pass


class WorkflowParseError(WorkflowError):
    """Raised when a Workflow YAML is malformed."""
    pass


class WorkflowStepError(WorkflowError):
    """Raised when a workflow step fails during execution."""
    def __init__(self, step_id: str, skill_id: str, reason: str):
        self.step_id = step_id
        self.skill_id = skill_id
        self.reason = reason
        super().__init__(
            f"Step '{step_id}' (skill={skill_id}) failed: {reason}"
        )


class WorkflowStepTimeoutError(WorkflowStepError):
    """Raised when a workflow step exceeds its timeout."""
    def __init__(self, step_id: str, skill_id: str, timeout_sec: int):
        self.timeout_sec = timeout_sec
        super().__init__(
            step_id, skill_id,
            f"Step exceeded timeout of {timeout_sec}s"
        )


class WorkflowEvidenceViolationError(WorkflowError):
    """Raised when a step output violates evidence linking rules."""
    def __init__(self, step_id: str, missing_refs: List[str]):
        self.step_id = step_id
        self.missing_refs = missing_refs
        super().__init__(
            f"Step '{step_id}' produced conclusions without valid evidence refs: "
            f"{missing_refs}"
        )


class WorkflowCyclicDependencyError(WorkflowError):
    """Raised when workflow steps contain a cyclic dependency."""
    pass


# ── Prompt Runner Errors ───────────────────────────────────────────────────────

class PromptRunnerError(AIResearchError):
    """Base exception for Prompt Runner errors."""
    pass


class PromptRenderError(PromptRunnerError):
    """Raised when template variable substitution fails."""
    pass


class ModelCallError(PromptRunnerError):
    """Raised when the LLM API call fails."""
    def __init__(self, model: str, reason: str):
        self.model = model
        self.reason = reason
        super().__init__(f"Model '{model}' call failed: {reason}")


class SchemaValidationError(PromptRunnerError):
    """Raised when model output fails JSON Schema validation."""
    def __init__(self, schema_path: str, raw_output: str, jsonschema_error: str):
        self.schema_path = schema_path
        self.raw_output = raw_output
        self.jsonschema_error = jsonschema_error
        super().__init__(
            f"Output failed schema validation ({schema_path}): {jsonschema_error}"
        )


class EvidenceRefError(PromptRunnerError):
    """Raised when an evidence_ref in the output cannot be resolved."""
    def __init__(self, ref: str):
        self.ref = ref
        super().__init__(f"Unresolvable evidence ref: '{ref}'")


# ── Memory System Errors ──────────────────────────────────────────────────────

class MemoryError(AIResearchError):
    """Base exception for Memory System errors."""
    pass


class EvidenceNotFoundError(MemoryError):
    """Raised when a requested MEU ID does not exist in the store."""
    def __init__(self, meu_id: str):
        self.meu_id = meu_id
        super().__init__(f"Evidence unit '{meu_id}' not found in store.")


class MemoryStoreError(MemoryError):
    """Raised when a memory store operation fails (read/write/flush)."""
    pass


# ── Execution Context Errors ──────────────────────────────────────────────────

class ExecutionContextError(AIResearchError):
    """Raised for execution context related errors."""
    pass


class ContextVariableNotFoundError(ExecutionContextError):
    """Raised when a referenced context variable is not found."""
    def __init__(self, var_path: str):
        self.var_path = var_path
        super().__init__(f"Context variable not found: '{var_path}'")
