"""
AI Code Research System - Core Package

极简核心：Skill Loader | Workflow Orchestrator | Prompt Runner | Memory System
"""
from core.skill_loader import SkillRegistry, SkillPackage, SkillMetadata
from core.workflow_orchestrator import (
    WorkflowExecutor,
    WorkflowDefinition,
    WorkflowStep,
)
from core.prompt_runner import PromptRunner, PromptTemplate, PromptTemplateLoader
from core.execution_context import ExecutionContext, WorkflowRun, StepResult
from core.errors import (
    AIResearchError,
    SkillLoadError,
    SkillNotFoundError,
    WorkflowNotFoundError,
    WorkflowParseError,
    WorkflowStepError,
    ModelCallError,
    SchemaValidationError,
    EvidenceRefError,
)

__version__ = "0.2.0"

__all__ = [
    # skill loader
    "SkillRegistry",
    "SkillPackage",
    "SkillMetadata",
    # orchestrator
    "WorkflowExecutor",
    "WorkflowDefinition",
    "WorkflowStep",
    # prompt runner
    "PromptRunner",
    "PromptTemplate",
    "PromptTemplateLoader",
    # execution context
    "ExecutionContext",
    "WorkflowRun",
    "StepResult",
    # errors
    "AIResearchError",
    "SkillLoadError",
    "SkillNotFoundError",
    "WorkflowNotFoundError",
    "WorkflowParseError",
    "WorkflowStepError",
    "ModelCallError",
    "SchemaValidationError",
    "EvidenceRefError",
]
