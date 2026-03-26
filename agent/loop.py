"""
agent/loop.py — AgentLoop 核心循环

AgentLoop 是整个 Agent 系统的核心执行引擎：

    LLM 调用 → 检查 tool_calls
                    ↓ 有工具
              解析工具调用 → ToolExecutor 执行
                    ↓
              追加 ToolMessage 到历史
                    ↓
              继续调用 LLM（迭代）
                    ↓ 无工具
              提取最终输出 → 校验 → 完成

终止条件：
- LLM 不再请求工具（返回纯文本）→ 成功
- 达到 max_iterations → max_iterations 状态
- 连续工具错误超限 → 降级或失败
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agent.messages import (
    AIMessage,
    HumanMessage,
    Message,
    SystemMessage,
    ToolCall,
    ToolMessage,
)
from agent.state import AgentState, LoopConfig
from agent.llm_adapter import LLMAdapter, LLMResponse

from tools.executor import ToolExecutor
from tools.base import ToolResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# LoopResult — AgentLoop 执行结果
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LoopResult:
    """
    AgentLoop 一次执行的结果。

    包含：状态、最终输出、工具调用记录、统计信息。
    """
    status: str                    # succeeded | failed | max_iterations
    output: str                    # LLM 最终输出的原始文本
    parsed_output: dict[str, Any] | None  # 尝试解析为 JSON 的结果
    tool_call_count: int
    total_duration_ms: int
    iterations: int
    error: str | None = None
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
            "tool_stats": self.tool_stats,
        }


# ─────────────────────────────────────────────────────────────────────────────
# AgentLoop — 核心循环
# ─────────────────────────────────────────────────────────────────────────────

class AgentLoop:
    """
    Agent 执行循环引擎。

    将对话历史交给 LLM：
    - 如果 LLM 请求工具 → 执行工具，追加 ToolMessage，继续循环
    - 如果 LLM 返回纯文本 → 提取输出，结束循环

    使用方式：
        loop = AgentLoop(
            llm_adapter=OpenAIAdapter(),
            tool_executor=executor,
            state=state,
        )
        result = loop.run()
    """

    def __init__(
        self,
        llm_adapter: LLMAdapter,
        tool_executor: ToolExecutor,
        state: AgentState,
        system_prompt: str | None = None,
    ):
        self.llm = llm_adapter
        self.tools = tool_executor
        self.state = state
        self._system_prompt = system_prompt

    # ── 主循环 ──────────────────────────────────────────────────────────────

    def run(self) -> LoopResult:
        """
        执行 Agent 循环，直到 LLM 不再请求工具或达到终止条件。

        Returns:
            LoopResult，执行结果封装
        """
        started = time.monotonic()

        # 如果传入了 system_prompt，在循环开始前注入
        if self._system_prompt:
            sys_msg = SystemMessage(content=self._system_prompt)
            self.state.add_message(sys_msg)

        # 循环
        while True:
            self.state.increment_iteration()
            should_continue, reason = self.state.should_continue()

            if not should_continue:
                if self.state.iteration >= self.state.config.max_iterations:
                    self.state.mark_max_iterations()
                    logger.warning(
                        "[%s] 达到最大迭代次数 %d，强制结束",
                        self.state.skill_id,
                        self.state.iteration,
                    )
                else:
                    self.state.mark_failed(reason)
                break

            # 调用 LLM
            try:
                response = self._call_llm()
            except Exception as exc:  # noqa: BLE001
                logger.exception("[%s] LLM 调用失败", self.state.skill_id)
                self.state.mark_failed(f"LLM 调用异常: {exc}")
                break

            # 记录 AI 消息
            ai_msg = AIMessage(
                content=response.content,
                tool_calls=[
                    ToolCall(
                        id=tc.get("id", ""),
                        name=tc.get("function", {}).get("name", ""),
                        arguments=json.loads(tc.get("function", {}).get("arguments", "{}"))
                                  if isinstance(tc.get("function", {}).get("arguments"), str)
                                  else tc.get("function", {}).get("arguments", {}),
                    )
                    for tc in response.tool_calls
                ],
                metadata={
                    "model": response.model,
                    "usage": response.usage,
                },
            )
            self.state.add_message(ai_msg)

            # 无工具调用 → 循环结束
            if not ai_msg.has_tool_calls():
                self.state.mark_succeeded(response.content)
                break

            # 有工具调用 → 逐个执行
            for tool_call in ai_msg.tool_calls:
                tool_result = self._execute_tool_call(tool_call, self.state.iteration)
                # 追加 ToolMessage
                tool_msg = ToolMessage(
                    tool_call_id=tool_call.id,
                    content=tool_result.output,
                    tool_name=tool_call.name,
                    success=tool_result.success,
                    metadata={"error": tool_result.error},
                )
                self.state.add_message(tool_msg)

                if self.state.config.verbose:
                    logger.info(
                        "[%s] iter=%d tool=%s success=%s",
                        self.state.skill_id,
                        self.state.iteration,
                        tool_call.name,
                        tool_result.success,
                    )

        # 汇总结果
        duration_ms = int((time.monotonic() - started) * 1000)
        parsed_output = self._try_parse_output(self.state.raw_output)

        return LoopResult(
            status=self.state.status,
            output=self.state.raw_output,
            parsed_output=parsed_output,
            tool_call_count=self.state.total_tool_calls,
            total_duration_ms=duration_ms,
            iterations=self.state.iteration,
            tool_stats=self.tools.stats() if hasattr(self.tools, "stats") else {},
            state_summary=self.state.summary(),
        )

    # ── LLM 调用 ─────────────────────────────────────────────────────────────

    def _call_llm(self) -> LLMResponse:
        """
        构造并发送 LLM 请求。

        注入 system_prompt 和可用工具描述。
        """
        # 获取消息历史（不含 system，因为会单独注入）
        chat_messages = self.state.get_messages_for_llm()

        # 获取工具描述
        tools = None
        if self.llm.supports_tools():
            tools = self.tools.to_openai_spec()

        # 注入 system prompt
        system_parts: list[str] = []
        if self._system_prompt:
            system_parts.append(self._system_prompt)

        # 添加工具使用说明到 system prompt
        if tools:
            tool_desc = self._format_tools_for_system(tools)
            system_parts.append(
                f"\n\n## Available Tools\n\nYou have access to the following tools:\n{tool_desc}\n\n"
                f"When you want to use a tool, respond with a function call.\n"
                f"After receiving tool results, continue reasoning and use more tools if needed.\n"
                f"Once you have gathered all necessary information, return your final analysis as a JSON object."
            )

        if system_parts:
            # OpenAI 支持在 messages 中放 system 消息
            # Anthropic 也支持，但我们在 adapter 中处理了 system 分离
            # 通用方案：直接把 system 作为第一条 user 消息（仅在无 system 时）
            # 更正确：在 adapter 层处理。这里简化处理。
            pass

        response = self.llm.chat(
            messages=chat_messages,
            tools=tools,
            max_tokens=4096,
        )

        return response

    def _format_tools_for_system(self, tools: list[dict[str, Any]]) -> str:
        """将工具列表格式化为 system prompt 中的描述文本（用于不支持 tools 参数的模型）。"""
        lines = []
        for t in tools:
            fn = t.get("function", {})
            name = fn.get("name", "?")
            desc = fn.get("description", "")
            params = fn.get("parameters", {})
            lines.append(f"- {name}: {desc}")
            props = params.get("properties", {})
            if props:
                lines.append(f"  Parameters:")
                for pname, pdef in props.items():
                    ptype = pdef.get("type", "any")
                    pdesc = pdef.get("description", "")
                    req = ""
                    if pname in params.get("required", []):
                        req = " (required)"
                    lines.append(f"    - {pname} ({ptype}){req}: {pdesc}")
        return "\n".join(lines)

    # ── 工具执行 ─────────────────────────────────────────────────────────────

    def _execute_tool_call(
        self,
        tool_call: ToolCall,
        iteration: int,
    ) -> ToolResult:
        """执行单个工具调用。"""
        t0 = time.monotonic()

        if not self.tools.can_call(tool_call.name):
            result = ToolResult.err(
                f"Tool '{tool_call.name}' is disabled (too many consecutive errors)."
            )
        else:
            result = self.tools.execute(
                tool_name=tool_call.name,
                arguments=tool_call.arguments,
                iteration=iteration,
            )

        duration_ms = int((time.monotonic() - t0) * 1000)

        self.state.record_tool_call(
            tool_call=tool_call,
            output=result.output,
            success=result.success,
            duration_ms=duration_ms,
            error=result.error,
        )

        return result

    # ── 输出解析 ─────────────────────────────────────────────────────────────

    def _try_parse_output(self, raw: str) -> dict[str, Any] | None:
        """尝试将 LLM 输出解析为 JSON。"""
        if not raw:
            return None

        raw = raw.strip()

        # 尝试直接解析
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # 尝试从 markdown 代码块提取
        import re
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                pass

        return None
