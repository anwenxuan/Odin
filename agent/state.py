"""
agent/state.py — Agent 状态与循环配置

- AgentState  : Agent 执行过程中的完整状态（消息历史 / 工具调用记录 / MEU 缓存 / 发现物）
- LoopConfig  : AgentLoop 的行为配置（最大迭代次数 / 超时 / 输出格式等）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agent.messages import Message, ToolCall

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# LoopConfig — AgentLoop 行为配置
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LoopConfig:
    """
    AgentLoop 的行为配置。

    控制循环何时终止、如何处理错误、如何格式化输出。
    """
    # 循环控制
    max_iterations: int = 20          # 最大工具调用轮次（防无限循环）
    max_total_tool_calls: int = 100  # 全局工具调用上限

    # 超时控制
    timeout_per_step_sec: int = 120   # 每个 Skill 的超时时间

    # 输出控制
    output_format: str = "json"       # json | markdown
    require_final_json: bool = True   # 是否强制要求最终输出为有效 JSON

    # Evidence 规则
    evidence_required: bool = True   # 每个结论是否必须引用 MEU
    max_evidence_refs: int = 50      # 单个 Skill 输出中最多允许的 evidence_ref 数

    # 调试
    verbose: bool = False             # 是否打印详细日志
    trace_messages: bool = False     # 是否记录每轮消息内容

    # 降级策略
    allow_fallback_on_error: bool = True  # 工具连续失败时是否降级（跳过该工具继续）
    max_consecutive_errors: int = 3       # 连续失败多少轮后触发降级


# ─────────────────────────────────────────────────────────────────────────────
# ToolCallRecord — 单次工具调用的完整记录
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ToolCallRecord:
    """单次工具调用的完整记录（包含请求和响应）。"""
    iteration: int
    tool_call: ToolCall
    tool_result_output: str   # 截断到一定长度便于日志
    success: bool
    duration_ms: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "tool_name": self.tool_call.name,
            "tool_args": self.tool_call.arguments,
            "success": self.success,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "output_preview": self.tool_result_output[:200],
        }


# ─────────────────────────────────────────────────────────────────────────────
# AgentState — Agent 执行期间的完整状态
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AgentState:
    """
    Agent 执行期间的完整状态。

    包含：
    - 对话消息历史（messages）
    - 工具调用记录（tool_call_records）
    - MEU 缓存（从工具返回的代码片段中提取）
    - 收集到的最终发现物（collected_findings）
    - 循环统计（iteration / tool_calls 等）
    """
    skill_id: str
    skill_name: str = ""
    session_id: str = ""
    run_id: str = ""

    # 消息历史（由 SkillAgent 和 AgentLoop 管理）
    messages: list[Message] = field(default_factory=list)

    # 工具调用记录
    tool_call_records: list[ToolCallRecord] = field(default_factory=list)
    total_tool_calls: int = 0
    consecutive_errors: int = 0

    # 循环控制
    iteration: int = 0
    config: LoopConfig = field(default_factory=LoopConfig)

    # MEU 缓存（evidence_store 中已有，但这里缓存一份便于快速引用）
    evidence_refs: list[str] = field(default_factory=list)

    # 最终发现物（AgentLoop 提取后汇总至此）
    collected_findings: list[dict[str, Any]] = field(default_factory=list)
    raw_output: str = ""    # LLM 最终输出的原始文本

    # 元数据
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: str | None = None
    status: str = "pending"   # pending | running | succeeded | failed | max_iterations

    # ── 消息历史管理 ────────────────────────────────────────────────────────

    def add_message(self, msg: Message) -> None:
        self.messages.append(msg)
        if self.config.trace_messages:
            logger.debug(
                "[%s] %s: %s",
                self.skill_id,
                msg.role.value.upper(),
                msg.content[:100],
            )

    def get_messages_for_llm(self) -> list[dict[str, Any]]:
        """
        将消息历史转换为 LLM API 的 messages 格式。
        跳过 system 类型的消息（由 adapter 单独注入）。
        """
        return [m.to_dict() for m in self.messages if m.role.value != "system"]

    # ── 工具调用记录 ─────────────────────────────────────────────────────────

    def record_tool_call(
        self,
        tool_call: ToolCall,
        output: str,
        success: bool,
        duration_ms: int,
        error: str | None = None,
    ) -> None:
        self.tool_call_records.append(
            ToolCallRecord(
                iteration=self.iteration,
                tool_call=tool_call,
                tool_result_output=output,
                success=success,
                duration_ms=duration_ms,
                error=error,
            )
        )
        self.total_tool_calls += 1
        if success:
            self.consecutive_errors = 0
        else:
            self.consecutive_errors += 1

    # ── 循环控制 ─────────────────────────────────────────────────────────────

    def should_continue(self) -> tuple[bool, str]:
        """
        判断是否继续循环。

        Returns:
            (should_continue, reason)
        """
        if self.iteration >= self.config.max_iterations:
            return False, f"达到最大迭代次数 {self.config.max_iterations}"

        if self.total_tool_calls >= self.config.max_total_tool_calls:
            return False, f"达到全局工具调用上限 {self.config.max_total_tool_calls}"

        if self.consecutive_errors >= self.config.max_consecutive_errors:
            if not self.config.allow_fallback_on_error:
                return False, f"连续 {self.consecutive_errors} 次工具调用失败，终止循环"
            else:
                return True, f"连续错误但允许降级，继续"

        return True, "继续循环"

    def increment_iteration(self) -> None:
        self.iteration += 1

    def mark_succeeded(self, raw_output: str) -> None:
        self.status = "succeeded"
        self.raw_output = raw_output
        self.finished_at = datetime.now(timezone.utc).isoformat()

    def mark_failed(self, reason: str) -> None:
        self.status = "failed"
        self.finished_at = datetime.now(timezone.utc).isoformat()
        logger.error("[%s] AgentLoop 失败: %s", self.skill_id, reason)

    def mark_max_iterations(self) -> None:
        self.status = "max_iterations"
        self.finished_at = datetime.now(timezone.utc).isoformat()

    # ── 统计与报告 ────────────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        duration = None
        if self.finished_at and self.started_at:
            try:
                t_start = datetime.fromisoformat(self.started_at)
                t_end = datetime.fromisoformat(self.finished_at)
                duration = int((t_end - t_start).total_seconds() * 1000)
            except (ValueError, TypeError):
                duration = None

        return {
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "status": self.status,
            "iterations": self.iteration,
            "total_tool_calls": self.total_tool_calls,
            "tool_call_records": [r.to_dict() for r in self.tool_call_records],
            "evidence_refs_count": len(self.evidence_refs),
            "findings_count": len(self.collected_findings),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": duration,
            "consecutive_errors": self.consecutive_errors,
        }
