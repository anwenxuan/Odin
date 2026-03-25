"""
Example: Run a Workflow

Demonstrates how to execute a workflow using the Odin framework.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.skill_loader import SkillRegistry
from core.workflow_orchestrator import WorkflowExecutor
from core.prompt_runner import PromptRunner, PromptTemplateLoader
from memory.evidence_store import EvidenceStore
from memory.memory_store import MemoryStore


def main():
    parser = argparse.ArgumentParser(description="Run an Odin workflow")
    parser.add_argument(
        "--workflow",
        choices=["vulnerability_research", "codebase_research", "architecture_analysis"],
        default="codebase_research",
        help="Workflow to run",
    )
    parser.add_argument(
        "--repo-url",
        required=True,
        help="GitHub URL or local path to the repository",
    )
    parser.add_argument(
        "--focus-paths",
        nargs="*",
        help="Optional list of paths to prioritize",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=8,
        help="Max call graph depth (vulnerability_research only)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write result JSON to this file",
    )
    parser.add_argument(
        "--provider",
        choices=["openai", "anthropic", "mock"],
        default="mock",
        help="LLM provider",
    )
    args = parser.parse_args()

    # ── Initialize components ─────────────────────────────────────────────
    skills_dir = PROJECT_ROOT / "skills"
    workflows_dir = PROJECT_ROOT / "workflows"

    registry = SkillRegistry()
    loaded = registry.load_from_directory(skills_dir)
    print(f"[Odin] Loaded {len(loaded)} skills:")
    for pkg in loaded:
        print(f"  - {pkg.skill_key}: {pkg.metadata.name}")

    evidence_store = EvidenceStore()
    memory_store = MemoryStore()

    # ── Configure Prompt Runner ──────────────────────────────────────────────
    def mock_model_caller(messages: list[dict], config: dict) -> str:
        """Mock LLM for testing without API keys."""
        print(f"[Mock LLM] Received {len(messages)} message(s)")
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            print(f"  [{role}]: {content[:200]}...")
        return json.dumps({
            "evidence_refs": ["mock/src/main.py::main:1"],
            "modules": [],
            "entrypoints": [],
            "hypotheses": [],
        })

    def real_openai_caller(messages: list[dict], config: dict) -> str:
        """Real OpenAI API call."""
        import os
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("pip install openai")
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model=config.get("model", "gpt-4o"),
            messages=messages,
            temperature=config.get("temperature", 0.0),
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or ""

    if args.provider == "mock":
        caller = mock_model_caller
    elif args.provider == "openai":
        caller = real_openai_caller
    else:
        caller = mock_model_caller

    runner = PromptRunner(
        model_caller=caller,
        evidence_store=evidence_store,
        template_loader=PromptTemplateLoader(PROJECT_ROOT / "prompts"),
    )

    # ── Initialize Workflow Executor ────────────────────────────────────────
    executor = WorkflowExecutor(
        skill_registry=registry,
        prompt_runner=runner,
        evidence_store=evidence_store,
        memory_store=memory_store,
    )
    executor.load_from_directory(workflows_dir)

    print(f"\n[Odin] Loaded workflows: "
          f"{[wf.id for wf in executor.load_from_directory(workflows_dir)]}")

    # ── Build inputs ────────────────────────────────────────────────────────
    inputs = {"repo_url": args.repo_url}
    if args.focus_paths:
        inputs["focus_paths"] = args.focus_paths
    if args.max_depth:
        inputs["max_depth"] = args.max_depth

    print(f"\n[Odin] Running workflow '{args.workflow}' with inputs:")
    print(f"  {json.dumps(inputs, indent=2)}")

    # ── Execute ────────────────────────────────────────────────────────────
    result = executor.run(args.workflow, inputs)

    print(f"\n[Odin] Workflow finished: {result.status.value}")
    print(f"  Run ID: {result.run_id}")
    for step_id, step_result in result.step_results.items():
        icon = "✓" if step_result.succeeded else "✗"
        duration = step_result.duration_ms
        print(f"  {icon} {step_id} ({step_result.skill_id}): "
              f"{step_result.status.value} ({duration}ms)")
        if step_result.error:
            print(f"    Error: {step_result.error}")

    # ── Output ─────────────────────────────────────────────────────────────
    output = result.to_summary()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as fh:
            json.dump(output, fh, ensure_ascii=False, indent=2)
        print(f"\n[Odin] Result written to {args.output}")

    # Evidence stats
    print(f"\n[Odin] Evidence Store stats: {evidence_store.stats()}")
    print(f"[Odin] Memory Store summary: {memory_store.summary()}")


if __name__ == "__main__":
    main()
