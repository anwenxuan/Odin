"""
Prompt Runner Module

统一渲染 Prompt 模板并执行模型调用，强制结构化输出。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from core.errors import (
    EvidenceRefError,
    ModelCallError,
    PromptRenderError,
    SchemaValidationError,
)
from core.schema_validator import default_validator


# ─────────────────────────────────────────────────────────────────────────────
# PromptTemplate
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PromptTemplate:
    """
    标准化 Prompt 模板。

    组成：
    - system_prompt   : 角色与能力边界（固定不变）
    - task_prompt     : 本次任务描述（支持 {{variable}} 插值）
    - constraints     : 强制输出约束（证据引用规则等）
    - evidence_policy : 证据引用策略（每个结论必须引用 MEU）
    """
    system_prompt: str
    task_prompt: str
    constraints: str
    evidence_policy: str = ""
    render_cache: dict[str, str] = field(default_factory=dict)

    def render(self, variables: dict[str, Any]) -> "RenderedPrompt":
        """
        渲染模板变量，返回组合后的完整 prompt 字符串。

        支持：
        - `{{variable_name}}`          → 简单字符串插值
        - `{{variable_name::default}}` → 带默认值
        """
        rendered_task = self._render_text(self.task_prompt, variables)
        rendered_constraints = self._render_text(self.constraints, variables)
        return RenderedPrompt(
            system=self.system_prompt,
            task=rendered_task,
            constraints=rendered_constraints,
            evidence_policy=self.evidence_policy,
        )

    def _render_text(self, text: str, variables: dict[str, Any]) -> str:
        """渲染单个文本块中的 {{variable}} 占位符。"""
        def replacer(match: re.Match) -> str:
            full = match.group(0)   # 完整匹配 {{...}}
            expr = match.group(1)   # 内部表达式
            # 处理 default 语法：{{var::default}}
            if "::" in expr:
                name, default_val = expr.split("::", 1)
                value = variables.get(name.strip(), default_val)
            else:
                value = variables.get(expr.strip())
            if value is None:
                raise PromptRenderError(
                    f"Missing template variable '{full}' and no default provided."
                )
            return str(value)

        return re.sub(r"\{\{([^}]+)\}\}", replacer, text)


@dataclass
class RenderedPrompt:
    """渲染后的完整 prompt，可直接发送给模型。"""
    system: str
    task: str
    constraints: str
    evidence_policy: str = ""

    def to_messages(self) -> list[dict[str, str]]:
        """
        转换为模型 API 的 messages 格式。
        system → user（task + constraints）
        """
        user_content = self.task
        if self.constraints:
            user_content += "\n\n## Output Constraints\n" + self.constraints
        if self.evidence_policy:
            user_content += "\n\n## Evidence Policy\n" + self.evidence_policy

        messages = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": user_content},
        ]
        return messages

    def to_full_text(self) -> str:
        """合并为一个完整文本（用于不支持 messages 格式的场景）。"""
        parts = [
            "=== SYSTEM ===\n" + self.system,
            "=== TASK ===\n" + self.task,
        ]
        if self.constraints:
            parts.append("=== CONSTRAINTS ===\n" + self.constraints)
        if self.evidence_policy:
            parts.append("=== EVIDENCE POLICY ===\n" + self.evidence_policy)
        return "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# PromptTemplateLoader
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PromptTemplateLoader:
    """
    从文件加载 Prompt 模板。

    支持：
    - base/  目录下的共享模板片段
    - skill 级别覆盖
    """
    base_dir: Path

    def load_base(self) -> dict[str, str]:
        """加载 base/ 目录下的共享模板片段。"""
        base: dict[str, str] = {}
        base_dir = Path(self.base_dir)
        if not base_dir.is_dir():
            return base
        for fname in ["system_prompt.md", "constraints.md", "evidence_policy.md"]:
            path = base_dir / fname
            if path.is_file():
                key = fname.replace(".md", "")
                base[key] = path.read_text(encoding="utf-8")
        return base

    def merge(self, base: dict[str, str], override: dict[str, str]) -> PromptTemplate:
        """
        合并 base 模板与 Skill 级别 override。

        优先级：override > base > 默认值
        """
        return PromptTemplate(
            system_prompt=override.get("system_prompt", "").strip()
                         or base.get("system_prompt", _DEFAULT_SYSTEM_PROMPT),
            task_prompt=override.get("task_prompt", "").strip()
                       or base.get("task_prompt", ""),
            constraints=override.get("constraints", "").strip()
                       or base.get("constraints", _DEFAULT_CONSTRAINTS),
            evidence_policy=override.get("evidence_policy", "").strip()
                           or base.get("evidence_policy", _DEFAULT_EVIDENCE_POLICY),
        )

    def load_from_directory(self, skill_dir: Path) -> PromptTemplate:
        """
        加载 Skill 目录中的 prompt.md（支持 YAML frontmatter 元数据）。
        """
        prompt_path = skill_dir / "prompt.md"
        if not prompt_path.is_file():
            return PromptTemplate(
                system_prompt=_DEFAULT_SYSTEM_PROMPT,
                task_prompt="",
                constraints=_DEFAULT_CONSTRAINTS,
            )

        content = prompt_path.read_text(encoding="utf-8")
        parts = self._split_frontmatter(content)
        override: dict[str, str] = {}

        if parts[0]:  # YAML frontmatter
            try:
                override = yaml.safe_load(parts[0]) or {}
            except yaml.YAMLError:
                pass

        override["task_prompt"] = parts[1]  # Markdown body = task_prompt

        base = self.load_base()
        return self.merge(base, override)

    def _split_frontmatter(self, content: str) -> tuple[str, str]:
        """分离 YAML frontmatter 与 Markdown body。"""
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                return parts[1].strip(), parts[2].strip()
        return "", content


# ─────────────────────────────────────────────────────────────────────────────
# PromptRunner
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PromptRunner:
    """
    Prompt 执行器。

    职责：
    1. 渲染 Prompt 模板
    2. 调用 LLM API
    3. 解析输出为 JSON
    4. 用 output_schema 校验输出
    5. 校验 evidence_refs 是否存在
    """
    # 可注入的模型调用实现
    model_caller: Callable[
        [list[dict[str, str]], dict[str, Any]],
        str
    ] | None = None
    # 可注入的 JSON 解析器
    json_parser: Callable[[str], dict[str, Any]] | None = None
    # Evidence store 引用（用于校验 evidence_refs）
    evidence_store: Any = None
    # 模板加载器
    template_loader: PromptTemplateLoader | None = None
    # 最大重试次数（Schema 校验失败时）
    max_retries: int = 2

    def __post_init__(self):
        self._model_caller = self.model_caller or _default_model_caller
        self._json_parser = self.json_parser or _default_json_parser

    def run(
        self,
        prompt_template: PromptTemplate,
        input_payload: dict[str, Any],
        output_schema: dict[str, Any] | None = None,
        skill_id: str = "unknown",
        evidence_required: bool = True,
        model_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        执行 Prompt → 模型调用 → JSON 解析 → Schema 校验 → Evidence 校验。

        Args:
            prompt_template : 渲染前的模板
            input_payload   : 模板变量值
            output_schema   : 输出约束 JSON Schema（可选）
            skill_id        : 当前 Skill ID（用于日志）
            evidence_required: 是否强制要求 evidence_refs
            model_config    : 传给模型的可选配置（temperature, model 等）

        Returns:
            校验通过的输出 JSON dict
        """
        # Step 1: 渲染模板
        rendered = prompt_template.render(input_payload)
        messages = rendered.to_messages()

        # Step 2: 调用模型（带重试）
        raw_output = None
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                raw_output = self._model_caller(messages, model_config or {})
                break
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    continue
        else:
            raise ModelCallError(
                model=model_config.get("model", "unknown") if model_config else "unknown",
                reason=str(last_error),
            )

        if raw_output is None:
            raise ModelCallError(
                model="unknown",
                reason="Model returned empty output",
            )

        # Step 3: JSON 解析
        try:
            output = self._json_parser(raw_output)
        except Exception as exc:
            raise SchemaValidationError(
                schema_path="N/A",
                raw_output=raw_output[:500],
                jsonschema_error=f"Failed to parse model output as JSON: {exc}",
            )

        # Step 4: Schema 校验
        if output_schema is not None:
            errors = default_validator.validate(output, output_schema)
            if errors:
                raise SchemaValidationError(
                    schema_path="output_schema",
                    raw_output=raw_output[:500],
                    jsonschema_error="; ".join(errors),
                )

        # Step 5: Evidence 校验（如果要求）
        if evidence_required:
            self._validate_evidence_refs(output, skill_id)

        return output

    def _validate_evidence_refs(self, output: dict[str, Any], skill_id: str) -> None:
        """递归收集所有 evidence_refs 并校验是否存在。"""
        refs = self._collect_evidence_refs(output)
        if self.evidence_store is not None:
            for ref in refs:
                if not self.evidence_store.has(ref):
                    raise EvidenceRefError(ref)

    def _collect_evidence_refs(self, obj: Any) -> set[str]:
        """递归收集对象中所有 evidence_refs。"""
        refs: set[str] = set()
        if isinstance(obj, dict):
            # 直接字段
            if "evidence_refs" in obj:
                vals = obj["evidence_refs"]
                if isinstance(vals, list):
                    refs.update(str(v) for v in vals)
                elif isinstance(vals, str):
                    refs.add(vals)
            # 递归
            for v in obj.values():
                refs |= self._collect_evidence_refs(v)
        elif isinstance(obj, list):
            for item in obj:
                refs |= self._collect_evidence_refs(item)
        return refs


# ─────────────────────────────────────────────────────────────────────────────
# Default implementations (可被调用方覆盖)
# ─────────────────────────────────────────────────────────────────────────────

def _default_model_caller(
    messages: list[dict[str, str]],
    config: dict[str, Any],
) -> str:
    """
    默认模型调用桩函数。

    生产环境应替换为真实 LLM API 调用（如 OpenAI、Anthropic、Ollama）。
    这里返回原始输出以便测试。
    """
    # 检查是否配置了真实 provider
    provider = config.get("provider", "").lower()
    model = config.get("model", "gpt-4o")

    if provider == "openai":
        import os
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "openai package not installed. Install with: pip install openai"
            )
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=config.get("temperature", 0.0),
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""

    elif provider == "anthropic":
        import os
        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "anthropic package not installed. Install with: pip install anthropic"
            )
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        # Anthropic /messages API
        response = client.messages.create(
            model=model,
            max_tokens=config.get("max_tokens", 4096),
            messages=[
                {"role": m["role"], "content": m["content"]}
                for m in messages
            ],
        )
        return response.content[0].text

    # 无真实 provider → 返回占位输出（用于骨架测试）
    raise NotImplementedError(
        f"No real model provider configured. "
        f"Set config.provider='openai' or 'anthropic', or implement a custom model_caller. "
        f"Messages: {messages!r}"
    )


def _default_json_parser(raw: str) -> dict[str, Any]:
    """从模型输出中提取 JSON（支持包裹在 markdown 代码块中）。"""
    import json
    import re

    # 尝试直接解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 尝试从 markdown 代码块提取
    code_block_pattern = re.compile(
        r"```(?:json)?\s*\n?(.*?)\n?```",
        re.DOTALL,
    )
    match = code_block_pattern.search(raw)
    if match:
        candidate = match.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"Could not parse JSON from model output. "
        f"First 300 chars: {raw[:300]!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Default template content
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_SYSTEM_PROMPT = """\
You are a code research skill executor.

Your role: analyze source code repositories systematically, following the task prompt precisely.

Rules:
1. Output ONLY valid JSON that conforms exactly to the output schema.
2. Do NOT include explanations, apologies, or text outside the JSON output.
3. If insufficient information is available, set confidence <= 0.4 and mark uncertainty.
4. Every conclusion MUST reference at least one code evidence (evidence_ref).
5. Never invent file paths, line numbers, or function names without reading the actual source code.
"""

_DEFAULT_CONSTRAINTS = """\
1. Output must strictly follow the provided output_schema.
2. All field values must match their specified types.
3. Every conclusion object must include a non-empty `evidence_refs` array.
4. Confidence scores must be between 0.0 and 1.0.
5. If evidence is insufficient, mark the conclusion as uncertain.
6. Do not omit any required fields.
7. Arrays must not be empty unless explicitly allowed by the schema.
"""

_DEFAULT_EVIDENCE_POLICY = """\
Evidence Linking Rule (CRITICAL):
- Every conclusion, finding, or claim in the output MUST include at least one `evidence_ref`.
- An `evidence_ref` is a string identifier for a Minimum Evidence Unit (MEU).
- MEUs must be stored in the EvidenceStore before they can be referenced.
- Unreferenced conclusions are considered INVALID and will cause the step to FAIL.
- Format for evidence_refs: free-form strings that uniquely identify the source (e.g., "auth.py::validate_token:42").
"""
