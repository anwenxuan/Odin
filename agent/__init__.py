"""
Agent Module — Agent 循环层

将 Workflow 的 Skill 封装为可循环执行的 Agent，让 LLM 和工具反复交互。

目录结构：
    agent/
        messages.py     — 消息模型（HumanMessage / AIMessage / ToolMessage / SystemMessage）
        state.py       — AgentState — 对话历史 + 工具调用记录 + MEU 缓存
        llm_adapter.py — LLM 适配器（统一 OpenAI / Anthropic / Ollama 接口）
        loop.py        — AgentLoop — 核心循环逻辑
        skill_agent.py — SkillAgent — 单个 Skill 的 Agent 封装
"""

from agent.messages import (
    Message,
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
    ToolCall,
)
from agent.state import AgentState, LoopConfig
from agent.llm_adapter import LLMAdapter, OpenAIAdapter, AnthropicAdapter, MockAdapter
from agent.loop import AgentLoop, LoopResult
from agent.skill_agent import SkillAgent, SkillAgentResult
from agent.merger import AgentResultMerger, AgentResult, MergedContext
from agent.runtime import (
    AgentRuntime,
    RuntimeConfig,
    RuntimeState,
    RuntimeResult,
    RuntimeStatus,
    StepRecord,
)
from agent.planner import Action, ActionType, ActionPriority, Planner, TaskDecomposition
from agent.checkpoint import CheckpointManager, CheckpointRecord, save_step_record
from agent.error_handler import (
    AgentError,
    ErrorType,
    ErrorSeverity,
    ErrorClassifier,
    ErrorHandler,
    ErrorHandlingResult,
    RecoveryStrategy,
    RecoveryPolicy,
)
from agent.retry import (
    RetryManager,
    RetryStats,
    BackoffStrategy,
    NoBackoff,
    FixedBackoff,
    LinearBackoff,
    ExponentialBackoff,
    FibonacciBackoff,
    CircuitBreaker,
    CircuitState,
)
from agent.observer import (
    AgentObserver,
    EventType,
    Event,
    NullObserver,
    LoggingObserver,
    MemoryInjectionObserver,
    MetricsObserver,
    ObserverManager,
)
from agent.sandbox import (
    SandboxGateway,
    SandboxConfig,
    SandboxMode,
    SandboxResult,
    ProcessSandbox,
    DockerSandbox,
    SandboxAuditLogger,
)
from agent.verification import (
    VerificationEngine,
    VerificationRule,
    VerificationResult,
    RuleResult,
    VerificationStatus,
    EvidencePresenceRule,
    CodeSyntaxRule,
    UnitTestRule,
    ConfidenceThresholdRule,
    CrossReferenceRule,
    register_builtin_rules,
)
from agent.observability import (
    AgentLogger,
    AgentLogRecord,
    LogLevel,
    AgentTracer,
    SpanKind,
    AgentMetrics,
)
from agent.evaluation import (
    EvaluationEngine,
    EvaluationReport,
    EvaluationMetrics,
    ReportGenerator,
    MarkdownReportFormatter,
)

__all__ = [
    # messages
    "Message",
    "HumanMessage",
    "AIMessage",
    "ToolMessage",
    "SystemMessage",
    "ToolCall",
    # state
    "AgentState",
    "LoopConfig",
    # adapters
    "LLMAdapter",
    "OpenAIAdapter",
    "AnthropicAdapter",
    "MockAdapter",
    # loop
    "AgentLoop",
    "LoopResult",
    # skill_agent
    "SkillAgent",
    "SkillAgentResult",
    # merger
    "AgentResultMerger",
    "AgentResult",
    "MergedContext",
    # runtime
    "AgentRuntime",
    "RuntimeConfig",
    "RuntimeState",
    "RuntimeResult",
    "RuntimeStatus",
    "StepRecord",
    # planner
    "Action",
    "ActionType",
    "ActionPriority",
    "Planner",
    "TaskDecomposition",
    # checkpoint
    "CheckpointManager",
    "CheckpointRecord",
    "save_step_record",
    # error_handler
    "AgentError",
    "ErrorType",
    "ErrorSeverity",
    "ErrorClassifier",
    "ErrorHandler",
    "ErrorHandlingResult",
    "RecoveryStrategy",
    "RecoveryPolicy",
    # retry
    "RetryManager",
    "RetryStats",
    "BackoffStrategy",
    "NoBackoff",
    "FixedBackoff",
    "LinearBackoff",
    "ExponentialBackoff",
    "FibonacciBackoff",
    "CircuitBreaker",
    "CircuitState",
    # observer
    "AgentObserver",
    "EventType",
    "Event",
    "NullObserver",
    "LoggingObserver",
    "MemoryInjectionObserver",
    "MetricsObserver",
    "ObserverManager",
    # sandbox
    "SandboxGateway",
    "SandboxConfig",
    "SandboxMode",
    "SandboxResult",
    "ProcessSandbox",
    "DockerSandbox",
    "SandboxAuditLogger",
    # verification
    "VerificationEngine",
    "VerificationRule",
    "VerificationResult",
    "RuleResult",
    "VerificationStatus",
    "EvidencePresenceRule",
    "CodeSyntaxRule",
    "UnitTestRule",
    "ConfidenceThresholdRule",
    "CrossReferenceRule",
    "register_builtin_rules",
    # observability
    "AgentLogger",
    "AgentLogRecord",
    "LogLevel",
    "AgentTracer",
    "SpanKind",
    "AgentMetrics",
    # evaluation
    "EvaluationEngine",
    "EvaluationReport",
    "EvaluationMetrics",
    "ReportGenerator",
    "MarkdownReportFormatter",
]
