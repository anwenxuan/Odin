"""
cli/commands/analyze.py — analyze 命令实现

核心流程：
1. 解析 repo 输入（GitHub URL → git clone；本地路径 → 直接使用）
2. 初始化组件（SkillRegistry / ToolExecutor / EvidenceStore / LLMAdapter）
3. 执行 Workflow
4. 输出报告
"""

from __future__ import annotations

import json
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Any

import yaml

from agent.llm_adapter import create_adapter
from agent.loop import LoopConfig
from agent.skill_agent import SkillAgent
from core.skill_loader import SkillRegistry
from core.workflow_orchestrator import WorkflowExecutor
from memory.evidence_store import EvidenceStore
from memory.memory_store import MemoryStore
from tools.executor import ToolExecutor

logger = logging.getLogger("odin.analyze")


def analyze_command(args: Any) -> int:
    """
    执行仓库分析。

    用法（通过 main.py 调用）：
        args.repo           : GitHub URL 或本地路径
        args.workflow       : 工作流 ID
        args.provider       : LLM 提供商
        args.model          : 模型名称
        args.output         : 输出文件路径
        args.output_format  : markdown | json
        args.focus_paths   : 重点分析路径
        args.max_iterations: 最大迭代次数
        args.verbose        : 详细日志
        args.branch         : Git 分支
        args.no_cache       : 禁用缓存
    """
    session_id = f"sess_{uuid.uuid4().hex[:8]}"

    # 1. 初始化仓库路径
    repo_path = _resolve_repo(args.repo, args.branch, args.no_cache)
    if repo_path is None:
        return 1

    try:
        # 2. 初始化组件
        evidence_store = EvidenceStore(
            persist_path=Path("data/evidence") if not args.verbose else None
        )
        memory_store = MemoryStore(
            persist_path=Path("data/memory") if not args.verbose else None
        )

        # 3. 初始化 ToolExecutor
        tool_executor = ToolExecutor(
            repo_path=repo_path,
            session_id=session_id,
        )
        tool_executor.auto_load_builtin()
        logger.info(
            "[Tools] 已注册 %d 个内置工具: %s",
            len(tool_executor.list_tool_names()),
            ", ".join(tool_executor.list_tool_names()),
        )

        # 4. 初始化 LLM Adapter
        llm = create_adapter(provider=args.provider, default_model=args.model)
        logger.info("[LLM] Provider: %s, Model: %s", args.provider, args.model)

        # 5. 加载 Skills
        skills_dir = Path(__file__).parent.parent.parent / "skills"
        registry = SkillRegistry()
        loaded = registry.load_from_directory(skills_dir)
        logger.info("[Skills] 已加载 %d 个 Skills", len(loaded))
        for pkg in loaded:
            logger.info("  - %s @ %s", pkg.metadata.id, pkg.metadata.version)

        # 6. 加载 Workflow 定义
        workflows_dir = Path(__file__).parent.parent.parent / "workflows"
        wf_executor = WorkflowExecutor(
            skill_registry=registry,
            prompt_runner=None,
            evidence_store=evidence_store,
            memory_store=memory_store,
        )
        wf_executor.load_from_directory(workflows_dir)

        # 7. 配置 LoopConfig
        loop_config = LoopConfig(
            max_iterations=args.max_iterations,
            verbose=args.verbose,
            evidence_required=True,
            require_final_json=True,
            allow_fallback_on_error=True,
        )

        # 8. 构建 inputs
        inputs: dict[str, Any] = {
            "repo_url": args.repo,
            "repo_path": str(repo_path),
        }
        if args.focus_paths:
            inputs["focus_paths"] = args.focus_paths

        # 9. 执行分析
        results = _run_workflow(
            workflow_id=args.workflow,
            inputs=inputs,
            registry=registry,
            llm=llm,
            tool_executor=tool_executor,
            evidence_store=evidence_store,
            memory_store=memory_store,
            loop_config=loop_config,
        )

        # 10. 输出结果
        _output_results(
            results=results,
            evidence_store=evidence_store,
            output_path=args.output,
            output_format=args.output_format,
        )

        logger.info("[Done] 分析完成，报告已输出")
        return 0

    finally:
        # 11. 清理临时目录（如果是克隆的仓库）
        if args.repo.startswith("http://") or args.repo.startswith("https://") or args.repo.startswith("git@"):
            if repo_path and str(repo_path).startswith(tempfile.gettempdir()):
                import shutil as _shutil
                try:
                    _shutil.rmtree(repo_path)
                    logger.debug("已清理临时目录: %s", repo_path)
                except OSError:
                    pass


def _resolve_repo(repo_input: str, branch: str | None, no_cache: bool) -> Path | None:
    """解析仓库输入（GitHub URL → clone；本地路径 → 直接使用）。"""
    repo_input = repo_input.strip()

    # 本地路径
    local_path = Path(repo_input)
    if local_path.exists() and local_path.is_dir():
        if (local_path / ".git").exists() or (local_path / ".git").is_dir():
            logger.info("[Repo] 使用本地 Git 仓库: %s", local_path)
        else:
            logger.info("[Repo] 使用本地目录（非 Git）: %s", local_path)
        return local_path

    # GitHub / GitLab URL
    if repo_input.startswith("http://") or repo_input.startswith("https://") or repo_input.startswith("git@"):
        return _clone_repo(repo_input, branch)

    logger.error("无法识别的仓库路径: %s", repo_input)
    return None


def _clone_repo(url: str, branch: str | None) -> Path | None:
    """克隆远程仓库到临时目录。"""
    import subprocess

    clone_dir = Path(tempfile.mkdtemp(prefix="odin_repo_"))
    cmd = ["git", "clone"]
    if branch:
        cmd += ["--branch", branch, "--depth", "1"]
    cmd += [url, str(clone_dir)]

    logger.info("[Git] 执行: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error("克隆失败: %s", result.stderr[:200])
            return None
        logger.info("[Git] 仓库克隆成功: %s", clone_dir)
        return clone_dir
    except subprocess.TimeoutExpired:
        logger.error("克隆超时（120s）")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error("克隆异常: %s", exc)
        return None


def _run_workflow(
    workflow_id: str,
    inputs: dict[str, Any],
    registry: SkillRegistry,
    llm: Any,
    tool_executor: ToolExecutor,
    evidence_store: EvidenceStore,
    memory_store: MemoryStore,
    loop_config: LoopConfig,
) -> dict[str, Any]:
    """
    执行 Workflow，优先使用 SkillAgent（Agent Loop），
    如果 SkillAgent 不可用则降级到 PromptRunner。
    """
    workflows_dir = Path(__file__).parent.parent.parent / "workflows"

    # 加载 Workflow 定义
    executor = WorkflowExecutor(
        skill_registry=registry,
        prompt_runner=None,  # 不使用旧版 PromptRunner
        evidence_store=evidence_store,
        memory_store=memory_store,
    )
    executor.load_from_directory(workflows_dir)

    try:
        wf_def = executor.get(workflow_id)
    except Exception as exc:
        logger.error("无法加载 Workflow '%s': %s", workflow_id, exc)
        # Fallback: 直接执行第一个 Skill
        logger.info("尝试降级到单 Skill 模式...")
        return _run_single_skill_fallback(
            registry=registry,
            llm=llm,
            tool_executor=tool_executor,
            evidence_store=evidence_store,
            memory_store=memory_store,
            loop_config=loop_config,
            inputs=inputs,
        )

    # 逐个执行 Steps（使用 SkillAgent）
    logger.info("[Workflow] 执行 '%s'，共 %d 个 Steps", workflow_id, len(wf_def.steps))

    step_outputs: dict[str, dict[str, Any]] = {}
    step_results: dict[str, Any] = {}
    run_id = f"run_{uuid.uuid4().hex[:12]}"

    for step in wf_def.steps:
        step_inputs = _resolve_step_inputs(step.with_params, inputs, step_outputs)
        logger.info("[Step] 执行: %s (skill=%s)", step.id, step.skill)

        # 获取 Skill
        try:
            skill_pkg = registry.get(step.skill)
        except Exception as exc:
            logger.error("Skill '%s' 未找到: %s", step.skill, exc)
            step_results[step.id] = {"status": "failed", "error": str(exc)}
            continue

        # 执行 SkillAgent
        agent = SkillAgent(
            skill=skill_pkg,
            llm_adapter=llm,
            tool_executor=tool_executor,
            evidence_store=evidence_store,
            memory_store=memory_store,
            loop_config=loop_config,
        )

        try:
            result = agent.run(
                inputs=step_inputs,
                context={"session_id": run_id, "repo_path": str(tool_executor._repo_path)},
                run_id=run_id,
            )
            step_outputs[step.id] = result.skill_output
            step_results[step.id] = {
                "status": result.status,
                "skill_output": result.skill_output,
                "loop_result": result.loop_result.to_dict() if result.loop_result else None,
                "evidence_stored": result.evidence_stored,
                "error": result.error,
            }
            logger.info(
                "[Step] ✓ %s — status=%s, tool_calls=%d, iterations=%d",
                step.id,
                result.status,
                result.loop_result.tool_call_count if result.loop_result else 0,
                result.loop_result.iterations if result.loop_result else 0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[Step] ✗ %s 异常: %s", step.id, exc)
            step_results[step.id] = {"status": "failed", "error": str(exc)}

    return {
        "workflow_id": workflow_id,
        "run_id": run_id,
        "steps": step_results,
        "evidence_store_stats": evidence_store.stats(),
        "memory_store_summary": memory_store.summary(),
    }


def _resolve_step_inputs(
    with_params: dict[str, Any],
    workflow_inputs: dict[str, Any],
    step_outputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    解析 step 的 with_params 中的变量引用。

    支持：
    - ${inputs.x}       → workflow_inputs["x"]
    - ${steps.S.outputs.y} → step_outputs["S"]["y"]
    """
    import re
    import json

    def resolve_value(val: Any) -> Any:
        if isinstance(val, str):
            def repl(m: re.Match) -> str:
                path = m.group(1)
                parts = path.split(".")
                if parts[0] == "inputs" and len(parts) >= 2:
                    v = workflow_inputs.get(parts[1])
                    if v is not None:
                        return json.dumps(v)
                if parts[0] == "steps" and len(parts) >= 4 and parts[2] == "outputs":
                    step_id = parts[1]
                    key = parts[3]
                    v = step_outputs.get(step_id, {}).get(key)
                    if v is not None:
                        return json.dumps(v)
                return m.group(0)

            resolved = re.sub(r"\$\{([^}]+)\}", repl, val)
            try:
                return json.loads(resolved)
            except (json.JSONDecodeError, TypeError):
                return resolved
        elif isinstance(val, dict):
            return {k: resolve_value(v) for k, v in val.items()}
        elif isinstance(val, list):
            return [resolve_value(item) for item in val]
        return val

    return resolve_value(with_params)


def _run_single_skill_fallback(
    registry: SkillRegistry,
    llm: Any,
    tool_executor: ToolExecutor,
    evidence_store: EvidenceStore,
    memory_store: MemoryStore,
    loop_config: LoopConfig,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """降级模式：没有 workflow.yaml 时，直接执行 repo_map Skill。"""
    try:
        skill_pkg = registry.get("repo_map")
    except Exception:
        # 找一个可用的 skill
        all_skills = registry.list_all()
        if not all_skills:
            return {"error": "没有可用的 Skills"}
        skill_pkg = all_skills[0]

    agent = SkillAgent(
        skill=skill_pkg,
        llm_adapter=llm,
        tool_executor=tool_executor,
        evidence_store=evidence_store,
        memory_store=memory_store,
        loop_config=loop_config,
    )

    run_id = f"run_{uuid.uuid4().hex[:12]}"
    result = agent.run(inputs=inputs, run_id=run_id)

    return {
        "workflow_id": "single_skill_fallback",
        "run_id": run_id,
        "skill": result.skill_id,
        "status": result.status,
        "skill_output": result.skill_output,
        "evidence_store_stats": evidence_store.stats(),
    }


def _output_results(
    results: dict[str, Any],
    evidence_store: EvidenceStore,
    output_path: Path | None,
    output_format: str,
) -> None:
    """输出分析结果。"""
    if output_format == "json":
        output_json = json.dumps(results, indent=2, ensure_ascii=False, default=str)
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(output_json, encoding="utf-8")
            logger.info("JSON 结果已写入: %s", output_path)
        else:
            print(output_json)
        return

    # Markdown 格式
    lines = [
        "# Odin Code Research Report",
        "",
        f"**Workflow**: {results.get('workflow_id', 'N/A')}",
        f"**Run ID**: {results.get('run_id', 'N/A')}",
        "",
    ]

    steps = results.get("steps", {})
    if steps:
        lines.append("## Steps")
        lines.append("")
        for step_id, step_result in steps.items():
            status_icon = "✓" if step_result.get("status") == "succeeded" else "✗"
            lines.append(f"### {status_icon} {step_id}")
            lines.append(f"- Status: `{step_result.get('status', 'unknown')}`")
            if step_result.get("error"):
                lines.append(f"- Error: {step_result['error']}")
            loop_result = step_result.get("loop_result")
            if loop_result:
                lines.append(f"- Tool calls: `{loop_result.get('tool_call_count', 0)}`")
                lines.append(f"- Iterations: `{loop_result.get('iterations', 0)}`")
                lines.append(f"- Duration: `{loop_result.get('total_duration_ms', 0)}ms`")
            skill_output = step_result.get("skill_output", {})
            if skill_output:
                # 提取摘要
                summary_fields = ["modules", "entrypoints", "sinks", "findings",
                                  "hypotheses", "attack_surfaces"]
                for field in summary_fields:
                    if field in skill_output and isinstance(skill_output[field], list):
                        lines.append(f"- **{field}**: `{len(skill_output[field])}` items")
            lines.append("")

    # Evidence 统计
    stats = results.get("evidence_store_stats", {})
    if stats:
        lines.append("## Evidence")
        lines.append(f"- Total MEUs: `{stats.get('total_meus', 0)}`")
        lines.append("")

    output = "\n".join(lines)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(output, encoding="utf-8")
        logger.info("Markdown 报告已写入: %s", output_path)
    else:
        print(output)
