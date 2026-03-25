"""
agent/skill_agent.py — SkillAgent

将单个 Skill 封装为可循环执行的 Agent：

1. 接收 Skill 配置（prompt / input_schema / output_schema）
2. 初始化 AgentState
3. 构建 system prompt（注入工具描述 + Evidence 规则）
4. 调用 AgentLoop 执行
5. 校验输出（Schema + Evidence）
6. 存入 EvidenceStore / MemoryStore
7. 返回标准化 Skill 输出
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.messages import HumanMessage, SystemMessage
from agent.state import AgentState, LoopConfig
from agent.loop import AgentLoop, LoopResult
from agent.llm_adapter import LLMAdapter

from core.prompt_runner import PromptTemplate, PromptTemplateLoader
from core.skill_loader import SkillPackage
from memory.evidence_store import EvidenceStore
from memory.memory_store import MemoryStore
from memory.models import MinimumEvidenceUnit, ArtifactKind

from tools.executor import ToolExecutor
from tools.base import ToolResult

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SkillAgent
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SkillAgentResult:
    """SkillAgent 一次执行的结果。"""
    skill_id: str
    skill_version: str
    status: str                    # succeeded | failed | max_iterations | schema_error
    skill_output: dict[str, Any]   # 校验通过的结构化输出
    raw_output: str               # LLM 原始输出
    loop_result: LoopResult | None
    evidence_stored: int           # 存入 EvidenceStore 的 MEU 数量
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "status": self.status,
            "skill_output": self.skill_output,
            "raw_output": self.raw_output[:500] if self.raw_output else "",
            "evidence_stored": self.evidence_stored,
            "error": self.error,
            "loop_summary": self.loop_result.to_dict() if self.loop_result else None,
        }


class SkillAgent:
    """
    将单个 Skill 封装为可循环执行的 AI Agent。

    使用方式：
        agent = SkillAgent(
            skill=skill_pkg,
            llm_adapter=OpenAIAdapter(),
            tool_executor=executor,
            evidence_store=evidence_store,
        )
        result = agent.run(inputs={"repo_path": "/tmp/my-repo"})
    """

    def __init__(
        self,
        skill: SkillPackage,
        llm_adapter: LLMAdapter,
        tool_executor: ToolExecutor,
        evidence_store: EvidenceStore | None = None,
        memory_store: MemoryStore | None = None,
        loop_config: LoopConfig | None = None,
        prompts_dir: Path | None = None,
    ):
        self.skill = skill
        self.llm = llm_adapter
        self.tools = tool_executor
        self.evidence_store = evidence_store
        self.memory_store = memory_store
        self.loop_config = loop_config or LoopConfig()
        self._prompts_dir = prompts_dir

    def run(
        self,
        inputs: dict[str, Any],
        context: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> SkillAgentResult:
        """
        执行 Skill。

        Args:
            inputs  : Skill 的输入参数（如 repo_path、module_map 等）
            context : 额外的运行时上下文
            run_id  : WorkflowRun ID（用于 Artifact 存储）

        Returns:
            SkillAgentResult，Skill 的标准化输出
        """
        context = context or {}
        run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"

        # 1. 初始化 AgentState
        state = self._init_state(run_id, context)

        # 2. 构建 system prompt
        system_prompt = self._build_system_prompt(inputs)

        # 3. 构建 user prompt（Task + 约束）
        task_prompt = self._build_task_prompt(inputs)

        # 4. 初始化 AgentLoop
        loop = AgentLoop(
            llm_adapter=self.llm,
            tool_executor=self.tools,
            state=state,
            system_prompt=system_prompt,
        )

        # 5. 注入初始消息
        state.add_message(SystemMessage(content=system_prompt))
        state.add_message(HumanMessage(content=task_prompt))

        # 6. 执行循环
        loop_result: LoopResult | None = None
        try:
            loop_result = loop.run()
        except Exception as exc:  # noqa: BLE001
            logger.exception("[%s] SkillAgent 执行异常", self.skill.metadata.id)
            state.mark_failed(str(exc))
            return SkillAgentResult(
                skill_id=self.skill.metadata.id,
                skill_version=self.skill.metadata.version,
                status="failed",
                skill_output={},
                raw_output="",
                loop_result=loop_result,
                evidence_stored=0,
                error=str(exc),
            )

        # 7. 提取并校验输出
        status, skill_output, parse_error = self._extract_and_validate(
            loop_result, state
        )

        # 8. 存入 EvidenceStore / MemoryStore
        evidence_count = self._store_results(
            skill_output, state, run_id, loop_result
        )

        return SkillAgentResult(
            skill_id=self.skill.metadata.id,
            skill_version=self.skill.metadata.version,
            status=status,
            skill_output=skill_output,
            raw_output=loop_result.output,
            loop_result=loop_result,
            evidence_stored=evidence_count,
            error=parse_error,
        )

    # ── 内部方法 ────────────────────────────────────────────────────────────

    def _init_state(
        self,
        run_id: str,
        context: dict[str, Any],
    ) -> AgentState:
        """初始化 AgentState。"""
        return AgentState(
            skill_id=self.skill.metadata.id,
            skill_name=self.skill.metadata.name,
            session_id=context.get("session_id", ""),
            run_id=run_id,
            config=self.loop_config,
        )

    def _build_system_prompt(self, inputs: dict[str, Any]) -> str:
        """
        构建完整的 system prompt。

        包含：
        1. 工具描述
        2. Evidence 引用规则
        3. Skill 角色定义
        """
        parts = [
            "You are an expert code research agent.",
            "",
            "## Your Task",
            self.skill.metadata.description,
            "",
            "## Critical Rules",
            "1. ALWAYS call tools to read actual code — do NOT guess or infer.",
            "2. Each conclusion MUST be backed by a tool-read evidence snippet.",
            "3. When you have gathered enough information, output a JSON object.",
            "4. Output ONLY valid JSON — no explanations, no markdown, no apologies.",
            "",
        ]

        # 添加工具描述
        tool_list = self.tools.list_tools()
        if tool_list:
            parts.append("## Available Tools")
            for t in tool_list:
                parts.append(f"\n### {t['name']}")
                parts.append(t["description"])
                schema = t.get("input_schema", {})
                props = schema.get("properties", {})
                if props:
                    parts.append("Parameters:")
                    for pname, pdef in props.items():
                        ptype = pdef.get("type", "any")
                        pdesc = pdef.get("description", "")
                        parts.append(f"  - {pname} ({ptype}): {pdesc}")
            parts.append("")

        # Evidence 规则
        if self.loop_config.evidence_required:
            parts.extend([
                "",
                "## Evidence Policy",
                "Every finding in your JSON output must include an `evidence_refs` array.",
                "Evidence refs should identify the file and line range, e.g.: 'src/auth.py::validate_token:42'",
                "Unreferenced conclusions will cause the step to FAIL.",
                "",
            ])

        # Evidence 引用格式说明
        parts.extend([
            "",
            "## Output Format",
            "Output a JSON object conforming to the skill's output_schema.",
            "Use `evidence_refs` array to cite evidence for each finding.",
            "",
        ])

        return "\n".join(parts)

    def _build_task_prompt(self, inputs: dict[str, Any]) -> str:
        """
        构建 user prompt（Task）。

        包含：
        1. Skill 原始 prompt.md 内容
        2. 输入参数格式化
        """
        parts = []

        # Skill 的 prompt.md 内容
        if self.skill.prompt_text:
            parts.append(self.skill.prompt_text)
            parts.append("")

        # 输入参数
        if inputs:
            parts.append("## Inputs")
            for key, value in inputs.items():
                if isinstance(value, (dict, list)):
                    value_str = json.dumps(value, indent=2, ensure_ascii=False)
                else:
                    value_str = str(value)
                parts.append(f"- **{key}**: {value_str}")
            parts.append("")

        # 输出约束
        output_schema = self.skill.output_schema
        if output_schema:
            parts.append("## Output Schema")
            parts.append("Your JSON output must conform to this schema:")
            parts.append(json.dumps(output_schema, indent=2, ensure_ascii=False))
            parts.append("")

        return "\n".join(parts)

    def _extract_and_validate(
        self,
        loop_result: LoopResult,
        state: AgentState,
    ) -> tuple[str, dict[str, Any], str | None]:
        """
        从 LLM 输出中提取 JSON 并校验。

        Returns:
            (status, output_dict, error_or_none)
        """
        # 检查循环状态
        if loop_result.status not in ("succeeded", "max_iterations"):
            return loop_result.status, {}, loop_result.error

        # 尝试解析 JSON
        parsed = loop_result.parsed_output
        if parsed is None:
            if self.loop_config.require_final_json:
                return "schema_error", {}, "LLM output is not valid JSON"
            else:
                # 不强制 JSON → 返回原始文本
                return "succeeded", {"text": loop_result.output}, None

        # Schema 校验
        output_schema = self.skill.output_schema
        if output_schema:
            from core.schema_validator import default_validator
            errors = default_validator.validate(parsed, output_schema)
            if errors:
                err_msg = f"Schema validation failed: {'; '.join(errors)}"
                # 如果开启 fallback，尝试返回部分结果
                if self.loop_config.allow_fallback_on_error:
                    logger.warning("[%s] Schema 校验失败但允许 fallback: %s", self.skill.metadata.id, err_msg)
                    return "succeeded", parsed, err_msg
                return "schema_error", parsed, err_msg

        # Evidence 校验
        if self.loop_config.evidence_required and self.evidence_store is not None:
            missing = self._validate_evidence_refs(parsed)
            if missing:
                logger.warning(
                    "[%s] Evidence refs 缺失（警告）: %s",
                    self.skill.metadata.id,
                    missing,
                )

        return "succeeded", parsed, None

    def _validate_evidence_refs(self, output: dict[str, Any]) -> list[str]:
        """递归收集并校验所有 evidence_refs。"""
        if self.evidence_store is None:
            return []

        refs = self._collect_refs(output)
        missing = []
        for ref in refs:
            if not self.evidence_store.has(ref):
                missing.append(ref)
        return missing

    def _collect_refs(self, obj: Any) -> list[str]:
        """递归收集对象中所有 evidence_refs。"""
        refs: list[str] = []
        if isinstance(obj, dict):
            if "evidence_refs" in obj:
                val = obj["evidence_refs"]
                if isinstance(val, list):
                    refs.extend(str(v) for v in val)
                elif isinstance(val, str):
                    refs.append(val)
            for v in obj.values():
                refs.extend(self._collect_refs(v))
        elif isinstance(obj, list):
            for item in obj:
                refs.extend(self._collect_refs(item))
        return refs

    def _store_results(
        self,
        skill_output: dict[str, Any],
        state: AgentState,
        run_id: str,
        loop_result: LoopResult,
    ) -> int:
        """存入 EvidenceStore 和 MemoryStore。"""
        count = 0

        # 存入 EvidenceStore
        if self.evidence_store is not None:
            from memory.models import MinimumEvidenceUnit
            import uuid as _uuid

            meus = self._extract_meus(skill_output, run_id)
            for meu in meus:
                self.evidence_store.put(meu)
                count += 1

        # 存入 MemoryStore
        if self.memory_store is not None:
            self.memory_store.put_artifact(
                run_id=run_id,
                skill_id=self.skill.metadata.id,
                skill_version=self.skill.metadata.version,
                kind=self.skill.metadata.tags[0] if self.skill.metadata.tags else "general",
                content=skill_output,
                summary=self._summarize_output(skill_output),
                evidence_refs=self._collect_refs(skill_output),
            )

        return count

    def _extract_meus(
        self,
        output: dict[str, Any],
        run_id: str,
    ) -> list[MinimumEvidenceUnit]:
        """从 Skill 输出中提取 MEU。"""
        from memory.models import EvidenceType, CallRelation
        import uuid as _uuid

        meus: list[MinimumEvidenceUnit] = []

        def traverse(obj: Any, path: str = "") -> None:
            if isinstance(obj, dict):
                if "file_path" in obj or "symbol" in obj or "snippet" in obj:
                    try:
                        meu = MinimumEvidenceUnit(
                            meu_id=f"MEU-{_uuid.uuid4().hex[:12]}",
                            repo=obj.get("repo", ""),
                            commit=obj.get("commit", ""),
                            file_path=obj.get("file_path", path),
                            symbol=obj.get("symbol", ""),
                            line_start=obj.get("line_start"),
                            line_end=obj.get("line_end"),
                            snippet=obj.get("snippet", ""),
                            evidence_type=EvidenceType.CODE_SNIPPET,
                            extracted_by=self.skill.metadata.id,
                            confidence=float(obj.get("confidence", 0.8)),
                            tags=self.skill.metadata.tags.copy(),
                        )
                        meus.append(meu)
                    except Exception:
                        pass
                for k, v in obj.items():
                    traverse(v, f"{path}.{k}" if path else k)
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    traverse(item, f"{path}[{i}]")

        traverse(output)
        return meus

    def _summarize_output(self, output: dict[str, Any]) -> str:
        """为 Artifact 生成一句话摘要。"""
        if not output:
            return "No output"
        keys = list(output.keys())[:3]
        if "modules" in output:
            return f"Found {len(output.get('modules', []))} modules"
        if "findings" in output:
            return f"Found {len(output.get('findings', []))} findings"
        if "hypotheses" in output:
            return f"Generated {len(output.get('hypotheses', []))} vulnerability hypotheses"
        if "report_markdown" in output:
            return "Generated analysis report"
        return f"Skill completed with keys: {', '.join(keys)}"
