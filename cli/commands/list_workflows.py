"""cli/commands/list_workflows.py — list-workflows 命令"""
from pathlib import Path
from core.skill_loader import SkillRegistry
from core.workflow_orchestrator import WorkflowExecutor


def list_workflows_command(args) -> int:
    workflows_dir = Path(__file__).parent.parent.parent / "workflows"
    registry = SkillRegistry()
    executor = WorkflowExecutor(skill_registry=registry, prompt_runner=None)
    loaded = executor.load_from_directory(workflows_dir)

    print(f"Odin — {len(loaded)} Registered Workflows\n")
    for wf in loaded:
        print(f"  {wf.id:<35} v{wf.version}")
        print(f"    {wf.description[:80]}")
        print(f"    Steps: {len(wf.steps)}")
        for step in wf.steps:
            deps = f" (deps: {', '.join(step.depends_on)})" if step.depends_on else ""
            print(f"      {step.id} → {step.skill}{deps}")
        print()
    return 0
