"""
agent/runtime.py — Agent Runtime Core

Agent Runtime 是整个 AI Agent 系统的核心执行引擎，整合了：
- AgentLoop    ：现有的核心循环（LLM → tool_calls → 执行 → 继续）
- RuntimeState ：扩展后的执行状态（支持 Memory/Evidence/Checkpoint）
- AgentRuntime ：对外统一入口，封装 Loop + Checkpoint + Memory + ErrorHandling

与现有 agent/loop.py 的关系：
- 复用 AgentLoop 的核心逻辑（不重写）
- 在 AgentRuntime 层增加 Checkpoint / Observer / Retry / Verification
- RuntimeState 继承现有 AgentState，扩展新字段

使用方式：
    runtime = AgentRuntime(
        task_id="task-001",
        llm_adapter=OpenAIAdapter(),
        tool_executor=executor,
        config=RuntimeConfig(...),
    )
    result = runtime.run()
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from agent.checkpoint import CheckpointManager, save_step_record
from agent.error_handler import ErrorClassifier, ErrorHandler
from agent.loop import AgentLoop, LoopResult
from agent.llm_adapter import LLMAdapter
from agent.messages import AIMessage, HumanMessage, Message, SystemMessage, ToolCall, ToolMessage
from agent.planner import Action, Planner, TaskDecomposition
from agent.retry import RetryManager
from agent.state import AgentState, LoopConfig, ToolCallRecord
from agent.observer import AgentObserver, NullObserver
from tools.executor import ToolExecutor
from tools.base import ToolResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────


class RuntimeStatus(str, Enum):
    """Agent Runtime 的整体状态。"""
    IDLE = "idle"
    INITIALIZING = "initializing"
    RUNNING = "running"
    WAITING_VERIFICATION = "waiting_verification"
    VERIFIED = "verified"
    RETRYING = "retrying"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


# ─────────────────────────────────────────────────────────────────────────────
# RuntimeConfig — AgentRuntime 行为配置
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class RuntimeConfig:
    """
    AgentRuntime 的行为配置。

    继承 LoopConfig 的所有字段，并增加 Runtime 级别的新配置。
    """
    # ── 继承自 LoopConfig ──────────────────────────────────────────────────
    max_iterations: int = 50
    max_total_tool_calls: int = 200
    timeout_per_step_sec: int = 120
    output_format: str = "json"
    require_final_json: bool = True
    evidence_required: bool = True
    max_evidence_refs: int = 50
    verbose: bool = False
    trace_messages: bool = False
    allow_fallback_on_error: bool = True
    max_consecutive_errors: int = 3

    # ── Runtime 级别新增 ───────────────────────────────────────────────────
    task_id: str = ""
    session_id: str = ""
    run_id: str = ""

    # Checkpoint 配置
    checkpoint_enabled: bool = True
    checkpoint_interval: int = 5          # 每 N 步保存一次
    checkpoint_dir: str = ".odin/checkpoints"

    # Sandbox 配置
    sandbox_enabled: bool = True

    # Verification 配置
    verification_enabled: bool = True
    verification_strict: bool = True      # 验证失败是否强制重试

    # Planner 配置
    planner_enabled: bool = True
    planner_model: str | None = None     # 使用不同模型做 Planner（可选）

    # Memory 配置
    memory_window_size: int = 50         # WorkingMemory 保留最近 N 步

    def to_loop_config(self) -> LoopConfig:
        """转换为 LoopConfig（兼容现有 AgentLoop）。"""
        return LoopConfig(
            max_iterations=self.max_iterations,
            max_total_tool_calls=self.max_total_tool_calls,
            timeout_per_step_sec=self.timeout_per_step_sec,
            output_format=self.output_format,
            require_final_json=self.require_final_json,
            evidence_required=self.evidence_required,
            max_evidence_refs=self.max_evidence_refs,
            verbose=self.verbose,
            trace_messages=self.trace_messages,
            allow_fallback_on_error=self.allow_fallback_on_error,
            max_consecutive_errors=self.max_consecutive_errors,
        )


# ─────────────────────────────────────────────────────────────────────────────
# StepRecord — 单步执行记录
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class StepRecord:
    """
    单个执行步骤的完整记录。
    用于 WorkingMemory 的 recent_steps 滚动窗口。
    """
    step: int
    action: Action | None
    tool_calls: list[ToolCall]
    observation: str
    evidence_refs: list[str]
    duration_ms: int
    status: str                    # "success" | "failed" | "retry"
    error: str | None = None
    tokens_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "action": self.action.to_dict() if self.action else None,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "observation": self.observation,
            "evidence_refs": self.evidence_refs,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error": self.error,
            "tokens_used": self.tokens_used,
        }


# ─────────────────────────────────────────────────────────────────────────────
# RuntimeState — 扩展后的运行时状态
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class RuntimeState:
    """
    扩展后的 Agent 执行状态。

    在现有 AgentState 基础上增加：
    - runtime_status     : Runtime 整体状态
    - recent_steps       : 最近 N 步的 StepRecord 滚动窗口
    - evidence           : 已收集的 MEU 列表
    - current_plan       : 当前执行计划
    - checkpoint_id      : 最近一次 Checkpoint ID
    - pending_observations: 待处理的观察结果
    """
    task_id: str
    runtime_status: RuntimeStatus = RuntimeStatus.IDLE
    agent_state: AgentState | None = None        # 内部 AgentState 引用

    # Step 追踪
    step: int = 0
    recent_steps: deque[StepRecord] = field(
        default_factory=lambda: deque(maxlen=50)
    )

    # Evidence
    evidence: list[Any] = field(default_factory=list)   # MEU list

    # Plan
    current_plan: list[Action] = field(default_factory=list)
    pending_observations: list[str] = field(default_factory=list)

    # Checkpoint
    checkpoint_id: str | None = None
    checkpoint_interval: int = 5

    # Session metadata
    session_id: str = ""
    run_id: str = ""
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: str | None = None

    # Config reference
    config: RuntimeConfig | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "runtime_status": self.runtime_status.value,
            "step": self.step,
            "recent_steps": [s.to_dict() for s in self.recent_steps],
            "evidence_count": len(self.evidence),
            "current_plan": [a.to_dict() for a in self.current_plan],
            "pending_observations": self.pending_observations,
            "checkpoint_id": self.checkpoint_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    def add_step(self, record: StepRecord) -> None:
        """添加 StepRecord 到滚动窗口。"""
        self.recent_steps.append(record)
        self.step = record.step

    def increment_step(self) -> int:
        self.step += 1
        return self.step

    def get_context_summary(self) -> str:
        """生成 LLM 可读的上下文摘要。"""
        recent = "\n".join(
            f"  Step {s.step}: {s.action.description if s.action else 'N/A'} "
            f"→ {s.status} ({s.duration_ms}ms)"
            for s in list(self.recent_steps)[-5:]
        )
        return (
            f"Task: {self.task_id}\n"
            f"Current Step: {self.step}/{self.config.max_iterations if self.config else '?'}\n"
            f"Recent Steps:\n{recent or '  (none)'}\n"
            f"Evidence Collected: {len(self.evidence)}\n"
            f"Pending Observations: {len(self.pending_observations)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# RuntimeResult — AgentRuntime 执行结果
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class RuntimeResult:
    """
    AgentRuntime 的执行结果。

    在 LoopResult 基础上增加：
    - checkpoint_id      : 使用的最后一个 Checkpoint
    - evidence_refs      : 所有收集的 Evidence 引用
    - verification_passed : 是否通过验证
    - steps              : 所有 StepRecord
    """
    status: str
    output: str
    parsed_output: dict[str, Any] | None
    tool_call_count: int
    total_duration_ms: int
    iterations: int
    error: str | None = None

    checkpoint_id: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    verification_passed: bool = True
    steps: list[StepRecord] = field(default_factory=list)

    # 来自 LoopResult 的字段
    tool_stats: dict[str, Any] = field(default_factory=dict)
    state_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "output": self.output[:500] if self.output else "",
            "parsed_output": self.parsed_output,
            "tool_call_count": self.tool_call_count,
            "total_duration_ms": self.total_duration_ms,
            "iterations": self.iterations,
            "error": self.error,
            "checkpoint_id": self.checkpoint_id,
            "evidence_refs": self.evidence_refs,
            "verification_passed": self.verification_passed,
            "steps": [s.to_dict() for s in self.steps],
            "tool_stats": self.tool_stats,
        }


# ─────────────────────────────────────────────────────────────────────────────
# AgentRuntime — 统一执行入口
# ─────────────────────────────────────────────────────────────────────────────


class AgentRuntime:
    """
    Agent Runtime — 统一执行入口。

    在现有 AgentLoop 基础上增加：
    1. Planner 集成   ：任务自动拆解为 Action 序列
    2. Checkpoint     ：定期保存状态，支持从断点恢复
    3. Observer       ：执行观察，注入 Memory
    4. ErrorHandler   ：错误分类与恢复策略
    5. RetryManager   ：智能重试
    6. 三层 Memory    ：WorkingMemory → SessionMemory → LongTermMemory

    使用方式：
        runtime = AgentRuntime(
            task_id="task-001",
            llm_adapter=OpenAIAdapter(),
            tool_executor=ToolExecutor(repo_path="/tmp/repo"),
            config=RuntimeConfig(...),
        )
        runtime.add_observer(MyObserver())
        result = runtime.run()
    """

    def __init__(
        self,
        task_id: str,
        llm_adapter: LLMAdapter,
        tool_executor: ToolExecutor,
        config: RuntimeConfig | None = None,
        system_prompt: str | None = None,
        planner: Planner | None = None,
        observers: list[AgentObserver] | None = None,
    ):
        self.task_id = task_id
        self.llm = llm_adapter
        self.tools = tool_executor
        self.config = config or RuntimeConfig(task_id=task_id)
        self.system_prompt = system_prompt
        self.planner = planner or Planner(llm_adapter, config=self.config)
        self.observers: list[AgentObserver] = observers or [NullObserver()]

        # 初始化子组件
        self.checkpoint_mgr = CheckpointManager(
            storage_dir=self.config.checkpoint_dir
        )
        self.error_handler = ErrorHandler(
            allow_fallback=self.config.allow_fallback_on_error
        )
        self.retry_mgr = RetryManager()

        # 初始化状态
        self.state = self._init_state()

        # 内部 AgentLoop（复用现有）
        self._loop: AgentLoop | None = None

    # ── 初始化 ──────────────────────────────────────────────────────────────

    def _init_state(self) -> RuntimeState:
        """初始化 RuntimeState。"""
        session_id = self.config.session_id or f"session-{uuid.uuid4().hex[:8]}"
        run_id = self.config.run_id or f"run-{uuid.uuid4().hex[:8]}"

        # 内部 AgentState（兼容现有 AgentLoop）
        agent_state = AgentState(
            skill_id=self.task_id,
            session_id=session_id,
            run_id=run_id,
            config=self.config.to_loop_config(),
        )

        state = RuntimeState(
            task_id=self.task_id,
            agent_state=agent_state,
            runtime_status=RuntimeStatus.INITIALIZING,
            config=self.config,
            session_id=session_id,
            run_id=run_id,
            checkpoint_interval=self.config.checkpoint_interval,
        )
        return state

    def _init_loop(self) -> AgentLoop:
        """初始化内部 AgentLoop。"""
        if self._loop is None:
            self._loop = AgentLoop(
                llm_adapter=self.llm,
                tool_executor=self.tools,
                state=self.state.agent_state,
                system_prompt=self._build_system_prompt(),
            )
        return self._loop

    def _build_system_prompt(self) -> str | None:
        """构建增强的 system prompt。"""
        parts = []

        if self.system_prompt:
            parts.append(self.system_prompt)

        if self.config.evidence_required:
            parts.append(
                "\n## Evidence Policy\n"
                "Every conclusion you make MUST be backed by evidence (MEU references). "
                "Do NOT make claims without citing specific file paths, line numbers, "
                "or code snippets. Use the read_file and search_code tools to gather evidence."
            )

        if self.config.planner_enabled:
            parts.append(
                "\n## Planning\n"
                "When given a complex task, first break it down into a sequence of "
                "actions. Use the planner to generate an ActionPlan before taking steps."
            )

        parts.append(
            "\n## Long-running Task Best Practices\n"
            "- Break large tasks into smaller steps\n"
            "- Verify each step's output before proceeding\n"
            "- Save important findings to evidence\n"
            "- If stuck, try a different approach"
        )

        return "\n".join(parts) if parts else None

    # ── Observer 管理 ───────────────────────────────────────────────────────

    def add_observer(self, observer: AgentObserver) -> None:
        """添加执行观察者。"""
        self.observers.append(observer)

    def remove_observer(self, observer: AgentObserver) -> None:
        """移除执行观察者。"""
        self.observers.remove(observer)

    def _notify_observers(
        self,
        event: str,
        data: dict[str, Any],
    ) -> None:
        """通知所有观察者。"""
        for obs in self.observers:
            try:
                obs.on_event(event, data, self.state)
            except Exception:
                logger.exception("[%s] Observer %s raised", self.task_id, obs)

    # ── Planner ─────────────────────────────────────────────────────────────

    async def _plan(self) -> TaskDecomposition | None:
        """调用 Planner 拆解任务。"""
        if not self.config.planner_enabled:
            return None

        try:
            decomposition = await self.planner.decompose(
                task=self.system_prompt or "",
                context=self.state.get_context_summary(),
                tools=self.tools.list_tool_names(),
            )
            self.state.current_plan = decomposition.actions
            self._notify_observers("plan_created", {
                "plan": [a.to_dict() for a in decomposition.actions],
                "summary": decomposition.summary,
            })
            logger.info(
                "[%s] Planner created %d actions: %s",
                self.task_id,
                len(decomposition.actions),
                decomposition.summary[:100],
            )
            return decomposition
        except Exception:
            logger.exception("[%s] Planner failed", self.task_id)
            return None

    # ── Checkpoint ─────────────────────────────────────────────────────────

    def _should_checkpoint(self) -> bool:
        """判断是否需要保存 Checkpoint。"""
        if not self.config.checkpoint_enabled:
            return False
        return self.state.step > 0 and self.state.step % self.state.checkpoint_interval == 0

    def _do_checkpoint(self) -> str | None:
        """执行 Checkpoint 保存。"""
        if not self._should_checkpoint():
            return None

        try:
            checkpoint_id = self.checkpoint_mgr.save(
                state=self.state,
                memory_snapshot={"recent_steps": [s.to_dict() for s in self.state.recent_steps]},
                plan_snapshot=[a.to_dict() for a in self.state.current_plan],
            )
            self.state.checkpoint_id = checkpoint_id
            self._notify_observers("checkpoint_saved", {
                "checkpoint_id": checkpoint_id,
                "step": self.state.step,
            })
            return checkpoint_id
        except Exception:
            logger.exception("[%s] Checkpoint save failed", self.task_id)
            return None

    # ── Step 执行 ──────────────────────────────────────────────────────────

    async def _execute_step(
        self,
        step: int,
        action: Action | None = None,
    ) -> StepRecord:
        """
        执行单个步骤。

        包含：LLM 调用 → 工具执行 → 观察者通知 → Checkpoint
        """
        t0 = time.monotonic()
        self._notify_observers("step_start", {"step": step, "action": action})

        # 准备 action 描述（如果有）
        action_desc = action.description if action else ""
        tool_calls_made: list[ToolCall] = []

        try:
            loop = self._init_loop()
            loop_result: LoopResult = loop.run()

            # 收集 tool_calls
            if self.state.agent_state:
                tool_calls_made = [
                    tc_record.tool_call
                    for tc_record in self.state.agent_state.tool_call_records
                    if tc_record.iteration == step
                ]

            duration_ms = int((time.monotonic() - t0) * 1000)

            record = StepRecord(
                step=step,
                action=action,
                tool_calls=tool_calls_made,
                observation=loop_result.output or "",
                evidence_refs=self.state.agent_state.evidence_refs if self.state.agent_state else [],
                duration_ms=duration_ms,
                status=loop_result.status,
                error=loop_result.error,
                tokens_used=loop_result.iterations * 100,  # rough estimate
            )

            self.state.add_step(record)
            self._notify_observers("step_complete", record.to_dict())

            return record

        except Exception as exc:
            duration_ms = int((time.monotonic() - t0) * 1000)
            self.state.runtime_status = RuntimeStatus.FAILED

            record = StepRecord(
                step=step,
                action=action,
                tool_calls=tool_calls_made,
                observation="",
                evidence_refs=[],
                duration_ms=duration_ms,
                status="failed",
                error=str(exc),
            )
            self.state.add_step(record)
            self._notify_observers("step_error", {
                "step": step,
                "error": str(exc),
            })
            return record

    # ── 主运行循环 ──────────────────────────────────────────────────────────

    def run(self) -> RuntimeResult:
        """
        同步执行 Agent Runtime。

        Returns:
            RuntimeResult，执行结果封装
        """
        self.state.runtime_status = RuntimeStatus.RUNNING
        started = time.monotonic()

        try:
            loop_result = self._run_sync()
        except Exception as exc:
            logger.exception("[%s] Runtime execution failed", self.task_id)
            self.state.runtime_status = RuntimeStatus.FAILED
            loop_result = LoopResult(
                status="failed",
                output="",
                parsed_output=None,
                tool_call_count=0,
                total_duration_ms=int((time.monotonic() - started) * 1000),
                iterations=0,
                error=str(exc),
            )

        # 构建 RuntimeResult
        self.state.finished_at = datetime.now(timezone.utc).isoformat()
        total_ms = int((time.monotonic() - started) * 1000)

        if self.state.runtime_status not in (RuntimeStatus.FAILED, RuntimeStatus.CANCELLED):
            self.state.runtime_status = RuntimeStatus.COMPLETED

        result = RuntimeResult(
            status=loop_result.status,
            output=loop_result.output,
            parsed_output=loop_result.parsed_output,
            tool_call_count=loop_result.tool_call_count,
            total_duration_ms=total_ms,
            iterations=loop_result.iterations,
            error=loop_result.error,
            checkpoint_id=self.state.checkpoint_id,
            evidence_refs=[
                e.meu_id if hasattr(e, "meu_id") else str(e)
                for e in self.state.evidence
            ],
            verification_passed=True,
            steps=list(self.state.recent_steps),
            tool_stats=loop_result.tool_stats,
            state_summary=loop_result.state_summary,
        )

        self._notify_observers("runtime_complete", result.to_dict())
        return result

    def _run_sync(self) -> LoopResult:
        """同步运行主循环。"""
        self._notify_observers("runtime_start", {
            "task_id": self.task_id,
            "config": self.config.to_dict() if hasattr(self.config, "to_dict") else {},
        })

        # 初始化 AgentLoop
        loop = self._init_loop()

        # 执行主循环（复用现有 AgentLoop.run()）
        loop_result = loop.run()

        return loop_result

    # ── 恢复 ────────────────────────────────────────────────────────────────

    def recover(self, checkpoint_id: str) -> bool:
        """
        从 Checkpoint 恢复执行。

        Args:
            checkpoint_id: 要恢复的 Checkpoint ID

        Returns:
            是否恢复成功
        """
        try:
            record = self.checkpoint_mgr.restore(self.task_id, checkpoint_id)
            self.state.step = record.step
            self.state.checkpoint_id = checkpoint_id
            self._notify_observers("runtime_recovered", {
                "checkpoint_id": checkpoint_id,
                "step": record.step,
            })
            logger.info(
                "[%s] Recovered from checkpoint %s at step %d",
                self.task_id,
                checkpoint_id,
                record.step,
            )
            return True
        except Exception:
            logger.exception("[%s] Recovery failed", self.task_id)
            return False

    def recover_latest(self) -> bool:
        """从最新 Checkpoint 恢复。"""
        latest = self.checkpoint_mgr.latest(self.task_id)
        if latest is None:
            logger.warning("[%s] No checkpoint found to recover from", self.task_id)
            return False
        return self.recover(latest.checkpoint_id)

    # ── 工具 ───────────────────────────────────────────────────────────────

    def get_state(self) -> RuntimeState:
        """获取当前 RuntimeState。"""
        return self.state

    def get_steps(self) -> list[StepRecord]:
        """获取所有执行步骤。"""
        return list(self.state.recent_steps)

    def get_checkpoint_list(self) -> list[Any]:
        """获取所有 Checkpoint。"""
        return self.checkpoint_mgr.list(self.task_id)
