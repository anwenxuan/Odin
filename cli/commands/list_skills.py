"""cli/commands/list_skills.py — list-skills 命令"""
from pathlib import Path
from core.skill_loader import SkillRegistry


def list_skills_command(args) -> int:
    skills_dir = Path(__file__).parent.parent.parent / "skills"
    registry = SkillRegistry()
    loaded = registry.load_from_directory(skills_dir)

    print(f"Odin — {len(loaded)} Registered Skills\n")
    for pkg in sorted(loaded, key=lambda p: p.metadata.id):
        print(f"  {pkg.metadata.id:<30} v{pkg.metadata.version}  {pkg.metadata.name}")
        desc = pkg.metadata.description or ""
        print(f"    {desc[:70]}")
        if pkg.metadata.tags:
            print(f"    Tags: {', '.join(pkg.metadata.tags)}")
        print()
    return 0
