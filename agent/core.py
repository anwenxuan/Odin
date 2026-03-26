"""
agent/core.py — Agent Core: Unified Integration Entry Point

整合 AgentRuntime / Planner / Executor 的统一入口。

设计理念：
- agent/runtime.py 负责高级功能（Checkpoint / Observer / Retry / Verification / Memory）
- agent/core.py   负责将 Loop / Planner / Executor 三大核心组件串联
- 提供给外部（CLI / API）一个简洁的调用接口

三层调用链：
    CLI/API
        ↓
    AgentCore.run(task, context)
        ↓
    ┌───────────────────────────────────────┐
    │  Planner.decompose()   ← 任务拆解     │
    │  AgentLoop.run()      ← 执行循环     │
    │  ToolExecutor.execute()← 工具执行     │
    └───────────────────────────────────────┘
        ↓
    AgentCoreResult

与 agent/runtime.py 的关系：
- runtime.py 是完整实现，包含 Checkpoint / Observer / ErrorHandler / RetryManager
- core.py   是简化版入口，直接串联三大核心（Loop / Planner / Executor）
- 外部调用推荐用 AgentCore，框架内部开发推荐用 AgentRuntime
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agent.checkpoint import CheckpointManager
from agent.error_handler import ErrorClassifier, ErrorHandler
from agent.loop import AgentLoop, LoopResult
from agent.llm_adapter import LLMAdapter
from agent.messages import SystemMessage
from agent.observer import AgentObserver, NullObserver
from agent.planner import Action, Planner, TaskDecomposition
from agent.retry import RetryManager
from agent.state import AgentState, LoopConfig
from tools.executor import ToolExecutor
from tools.base import ToolResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# AgentCoreConfig — AgentCore 行为配置
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class AgentCoreConfig:
    """
    AgentCore 行为配置（简化版）。

    对比 RuntimeConfig：
    - 只包含最核心的配置项
    - Checkpoint / Sandbox / Verification 默认关闭（按需开启）
    """
    # Loop
    max_iterations: int = 20
    max_total_tool_calls: int = 100
    timeout_per_step_sec: int = 120

    # Planner
    planner_enabled: bool = True
    planner_model: str | None = None

    # Memory
    evidence_required: bool = True
    max_evidence_refs: int = 50

    # Recovery
    checkpoint_enabled: bool = False
    checkpoint_interval: int = 10
    checkpoint_dir: str = ".odin/checkpoints"

    # Error
    allow_fallback_on_error: bool = True
    max_consecutive_errors: int = 3

    # Debug
    verbose: bool = False
    trace_messages: bool = False

    def to_loop_config(self) -> LoopConfig:
        return LoopConfig(
            max_iterations=self.max_iterations,
            max_total_tool_calls=self.max_total_tool_calls,
            timeout_per_step_sec=self.timeout_per_step_sec,
            output_format="json",
            require_final_json=True,
            evidence_required=self.evidence_required,
            max_evidence_refs=self.max_evidence_refs,
            verbose=self.verbose,
            trace_messages=self.trace_messages,
            allow_fallback_on_error=self.allow_fallback_on_error,
            max_consecutive_errors=self.max_consecutive_errors,
        )


# ─────────────────────────────────────────────────────────────────────────────
# AgentCoreResult — AgentCore 执行结果
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class AgentCoreResult:
    """
    AgentCore 的执行结果。

    在 LoopResult 基础上增加：
    - plan              : 使用的执行计划
    - checkpoint_id    : 使用的 Checkpoint
    - errors           : 错误记录
    """
    status: str
    output: str
    parsed_output: dict[str, Any] | None
    tool_call_count: int
    total_duration_ms: int
    iterations: int
    error: str | None = None

    # Plan
    plan: TaskDecomposition | None = None
    checkpoint_id: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    # 透传
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
            "plan": self.plan.to_dict() if self.plan else None,
            "checkpoint_id": self.checkpoint_id,
            "evidence_refs": self.evidence_refs,
            "errors": self.errors,
            "tool_stats": self.tool_stats,
        }

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"

    @property
    def failed(self) -> bool:
        return self.status == "failed"


# ─────────────────────────────────────────────────────────────────────────────
# AgentCore — 统一入口
# ─────────────────────────────────────────────────────────────────────────────


class AgentCore:
    """
    Agent 核心整合入口。

    串联三大核心组件：
    1. Planner   — 任务拆解为 Action 序列
    2. AgentLoop — 执行循环（LLM → tool_calls → 执行 → 继续）
    3. ToolExecutor — 工具执行

    可选集成：
    - CheckpointManager — 定期保存状态
    - ErrorHandler     — 错误分类与恢复
    - Observer         — 执行观察

    使用方式：
        core = AgentCore(
            llm_adapter=OpenAIAdapter(),
            tool_executor=ToolExecutor(repo_path="/tmp/repo"),
            config=AgentCoreConfig(planner_enabled=True),
        )
        result = core.run(
            task="分析 /repo 中的 SQL 注入漏洞",
            system_prompt="你是一个安全研究员...",
        )
        print(result.output)
    """

    def __init__(
        self,
        llm_adapter: LLMAdapter,
        tool_executor: ToolExecutor,
        config: AgentCoreConfig | None = None,
        planner: Planner | None = None,
        observers: list[AgentObserver] | None = None,
    ):
        self.llm = llm_adapter
        self.tools = tool_executor
        self.config = config or AgentCoreConfig()
        self.planner = planner or Planner(llm_adapter, config=self.config)
        self.observers: list[AgentObserver] = observers or [NullObserver()]

        # 可选组件
        self.checkpoint_mgr: CheckpointManager | None = None
        if self.config.checkpoint_enabled:
            self.checkpoint_mgr = CheckpointManager(self.config.checkpoint_dir)
        self.error_handler = ErrorHandler(
            allow_fallback=self.config.allow_fallback_on_error
        )
        self.retry_mgr = RetryManager()

        # 内部状态
        self._current_state: AgentState | None = None
        self._task_id_counter = 0

    # ── 主入口 ──────────────────────────────────────────────────────────────

    def run(
        self,
        task: str,
        system_prompt: str | None = None,
        context: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> AgentCoreResult:
        """
        执行任务。

        流程：
        1. 初始化状态
        2. Planner 拆解任务（可选）
        3. AgentLoop 执行循环
        4. Checkpoint 保存（可选）
        5. 汇总结果

        Args:
            task         : 任务描述
            system_prompt: 系统提示词
            context      : 执行上下文（可用工具列表等）
            task_id      : 可选的 Task ID

        Returns:
            AgentCoreResult
        """
        t0 = time.monotonic()
        self._task_id_counter += 1
        task_id = task_id or f"core-task-{self._task_id_counter}"

        # 构建 system prompt
        full_prompt = self._build_system_prompt(task, system_prompt)

        # 初始化状态
        state = self._init_state(task_id, full_prompt)

        # Observer 通知
        self._notify("runtime_start", {"task_id": task_id, "task": task})

        loop_result: LoopResult | None = None
        plan: TaskDecomposition | None = None
        checkpoint_id: str | None = None
        errors: list[dict[str, Any]] = []

        try:
            # Planner
            if self.config.planner_enabled:
                plan = self._do_plan(task, context)
                if plan:
                    self._notify("plan_created", {
                        "plan": [a.to_dict() for a in plan.actions],
                        "summary": plan.summary,
                    })

            # AgentLoop
            loop_result = self._run_loop(state, full_prompt)

            # Checkpoint
            if self.checkpoint_mgr and state.iteration % self.config.checkpoint_interval == 0:
                checkpoint_id = self._do_checkpoint(task_id, state, plan)

        except Exception as exc:
            logger.exception("[%s] Core execution failed", task_id)
            errors.append({
                "type": "execution_error",
                "message": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        # 汇总结果
        duration_ms = int((time.monotonic() - t0) * 1000)

        result = AgentCoreResult(
            status=loop_result.status if loop_result else "failed",
            output=loop_result.output if loop_result else "",
            parsed_output=loop_result.parsed_output if loop_result else None,
            tool_call_count=loop_result.tool_call_count if loop_result else 0,
            total_duration_ms=duration_ms,
            iterations=loop_result.iterations if loop_result else 0,
            error=loop_result.error if loop_result else None,
            plan=plan,
            checkpoint_id=checkpoint_id,
            evidence_refs=list(state.evidence_refs),
            errors=errors,
            tool_stats=loop_result.tool_stats if loop_result else {},
            state_summary=loop_result.state_summary if loop_result else {},
        )

        self._notify("runtime_complete", result.to_dict())
        return result

    # ── 初始化 ───────────────────────────────────────────────────────────────

    def _init_state(self, task_id: str, system_prompt: str) -> AgentState:
        """初始化 AgentState。"""
        state = AgentState(
            skill_id=task_id,
            config=self.config.to_loop_config(),
        )
        self._current_state = state
        return state

    def _build_system_prompt(self, task: str, system_prompt: str | None) -> str:
        """构建 system prompt。"""
        parts = []

        if system_prompt:
            parts.append(system_prompt)

        if self.config.evidence_required:
            parts.append(
                "\n## Evidence Policy\n"
                "Every conclusion you make MUST be backed by evidence. "
                "Do NOT make claims without citing specific file paths, "
                "line numbers, or code snippets."
            )

        if self.config.planner_enabled:
            parts.append(
                "\n## Planning\n"
                "When given a complex task, break it down into steps. "
                "Use the planner to generate an ActionPlan before taking steps."
            )

        return "\n".join(parts) if parts else ""

    # ── Planner ─────────────────────────────────────────────────────────────

    def _do_plan(self, task: str, context: dict[str, Any] | None = None) -> TaskDecomposition | None:
        """调用 Planner 拆解任务。"""
        try:
            plan = self.planner.decompose(
                task=task,
                context=context.get("context", "") if context else "",
                tools=self.tools.list_tool_names(),
            )
            return plan
        except Exception:
            logger.exception("[Planner] Failed to decompose task")
            return None

    # ── Loop ───────────────────────────────────────────────────────────────

    def _run_loop(self, state: AgentState, system_prompt: str) -> LoopResult:
        """运行 AgentLoop。"""
        loop = AgentLoop(
            llm_adapter=self.llm,
            tool_executor=self.tools,
            state=state,
            system_prompt=system_prompt,
        )

        # 主循环
        result = loop.run()

        return result

    # ── Checkpoint ─────────────────────────────────────────────────────────

    def _do_checkpoint(
        self,
        task_id: str,
        state: AgentState,
        plan: TaskDecomposition | None,
    ) -> str | None:
        """保存 Checkpoint。"""
        if not self.checkpoint_mgr:
            return None

        try:
            checkpoint_id = self.checkpoint_mgr.save(
                state=self._state_to_runtime_state(task_id, state),
                memory_snapshot={"evidence_refs": list(state.evidence_refs)},
                plan_snapshot=[a.to_dict() for a in plan.actions] if plan else [],
            )
            self._notify("checkpoint_saved", {
                "checkpoint_id": checkpoint_id,
                "step": state.iteration,
            })
            return checkpoint_id
        except Exception:
            logger.exception("[Checkpoint] Failed to save")
            return None

    def _state_to_runtime_state(self, task_id: str, state: AgentState) -> Any:
        """将 AgentState 转换为 CheckpointManager 可接受的格式。"""
        from agent.runtime import RuntimeState
        rs = RuntimeState(
            task_id=task_id,
            agent_state=state,
            step=state.iteration,
        )
        return rs

    # ── Observer ───────────────────────────────────────────────────────────

    def add_observer(self, observer: AgentObserver) -> None:
        """添加观察者。"""
        self.observers.append(observer)

    def _notify(self, event: str, data: dict[str, Any]) -> None:
        """广播事件给所有观察者。"""
        state = self._current_state
        for obs in self.observers:
            try:
                obs.on_event(event, data, state)
            except Exception:
                logger.exception("Observer %s raised", obs)

    # ── 便捷方法 ───────────────────────────────────────────────────────────

    def run_sync(self, task: str, **kwargs: Any) -> AgentCoreResult:
        """同步执行（与 run 相同，保留命名一致性）。"""
        return self.run(task, **kwargs)

    def get_state(self) -> AgentState | None:
        """获取当前状态。"""
        return self._current_state

    def get_config(self) -> AgentCoreConfig:
        """获取配置。"""
        return self.config

    def update_config(self, **kwargs: Any) -> None:
        """更新配置。"""
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)


# ─────────────────────────────────────────────────────────────────────────────
# 便捷工厂函数
# ─────────────────────────────────────────────────────────────────────────────


def create_agent_core(
    llm_adapter: LLMAdapter,
    repo_path: str | None = None,
    **kwargs: Any,
) -> AgentCore:
    """
    快速创建 AgentCore 的工厂函数。

    自动完成：
    1. ToolExecutor 初始化
    2. 内置工具加载
    3. AgentCore 创建

    Args:
        llm_adapter: LLM 适配器
        repo_path   : 代码仓库路径
        **kwargs    : 传递给 AgentCoreConfig 的参数

    Returns:
        AgentCore 实例
    """
    executor = ToolExecutor(repo_path=repo_path)
    executor.auto_load_builtin()

    config = AgentCoreConfig(**kwargs)

    return AgentCore(
        llm_adapter=llm_adapter,
        tool_executor=executor,
        config=config,
    )
