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
]
