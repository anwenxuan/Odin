"""
Skill Loader Module

加载与校验 Skill 包（metadata/prompt/schema/version），注册到运行时能力目录。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.errors import (
    SkillDependencyError,
    SkillLoadError,
    SkillNotFoundError,
    SkillSchemaError,
)
from core.schema_validator import default_validator


# ─────────────────────────────────────────────────────────────────────────────
# SkillMetadata
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SkillMetadata:
    """Skill 元数据，从 skill.yaml 解析。"""
    id: str
    version: str
    name: str
    description: str
    owner: str
    tags: list[str] = field(default_factory=list)
    runtime: dict[str, Any] = field(default_factory=dict)
    contracts: dict[str, str] = field(default_factory=dict)
    requirements: dict[str, Any] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, content: str) -> "SkillMetadata":
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise SkillLoadError(f"Failed to parse skill.yaml: {exc}") from exc

        if not isinstance(data, dict):
            raise SkillLoadError("skill.yaml must contain a YAML mapping (dict).")

        required = ["id", "version", "name", "description"]
        for field_name in required:
            if field_name not in data:
                raise SkillLoadError(f"skill.yaml missing required field: '{field_name}'")

        return cls(
            id=str(data["id"]),
            version=str(data["version"]),
            name=str(data["name"]),
            description=str(data["description"]),
            owner=str(data.get("owner", "unknown")),
            tags=list(data.get("tags", [])),
            runtime=dict(data.get("runtime", {})),
            contracts=dict(data.get("contracts", {})),
            requirements=dict(data.get("requirements", {})),
            dependencies=list(data.get("dependencies", [])),
        )


# ─────────────────────────────────────────────────────────────────────────────
# SkillPackage
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SkillPackage:
    """
    完整 Skill 包：包含元数据、prompt 文本、输入/输出 schema。

    懒加载：schema 和 prompt 内容在首次访问时才读取。
    """
    metadata: SkillMetadata
    root_dir: Path

    def _read_file(self, filename: str) -> str:
        path = self.root_dir / filename
        if not path.is_file():
            raise SkillLoadError(
                f"Skill '{self.metadata.id}' is missing required file: '{filename}'"
            )
        return path.read_text(encoding="utf-8")

    @property
    def prompt_text(self) -> str:
        prompt_file = self.metadata.contracts.get("prompt", "prompt.md")
        return self._read_file(prompt_file)

    @property
    def input_schema(self) -> dict[str, Any] | None:
        input_file = self.metadata.contracts.get("input_schema")
        if not input_file:
            return None
        raw = self._read_file(input_file)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SkillSchemaError(
                f"Skill '{self.metadata.id}' input_schema is not valid JSON: {exc}"
            ) from exc

    @property
    def output_schema(self) -> dict[str, Any] | None:
        output_file = self.metadata.contracts.get("output_schema")
        if not output_file:
            return None
        raw = self._read_file(output_file)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SkillSchemaError(
                f"Skill '{self.metadata.id}' output_schema is not valid JSON: {exc}"
            ) from exc

    @property
    def skill_key(self) -> str:
        """唯一标识：`skill_id@version`"""
        return f"{self.metadata.id}@{self.metadata.version}"

    def validate_schemas(self) -> list[str]:
        """
        校验 input_schema 和 output_schema 是否为合法 JSON Schema。
        Returns:
            错误列表，空=校验通过。
        """
        errors: list[str] = []
        for name, schema in [
            ("input_schema", self.input_schema),
            ("output_schema", self.output_schema),
        ]:
            if schema is None:
                continue
            errs = default_validator.validate(schema, schema)
            if errs:
                errors.extend(
                    f"[{self.metadata.id}/{name}] {e}" for e in errs
                )
        return errors


# ─────────────────────────────────────────────────────────────────────────────
# SkillRegistry
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SkillRegistry:
    """
    全局 Skill 注册表。

    支持：
    - 按 ID（含版本）精确查询
    - 按 ID 最新版本查询
    - 列出所有已注册 Skill
    - 批量加载目录
    """
    _skills: dict[str, SkillPackage] = field(default_factory=dict)

    # ── 注册操作 ────────────────────────────────────────────────────────────

    def register(self, package: SkillPackage) -> None:
        """注册一个 Skill 包。"""
        key = package.skill_key
        if key in self._skills:
            raise SkillLoadError(
                f"Duplicate skill registration: '{key}' "
                f"(existing: {self._skills[key].root_dir})"
            )
        self._skills[key] = package

    def load_from_directory(self, root: Path | str) -> list[SkillPackage]:
        """
        扫描目录，加载所有子目录中的 Skill 包。

        期望目录结构：
            root/
                skill_id/
                    skill.yaml
                    prompt.md
                    input_schema.json   (可选)
                    output_schema.json  (可选)
        """
        root = Path(root)
        loaded: list[SkillPackage] = []

        if not root.is_dir():
            raise SkillLoadError(f"Skill root directory not found: {root}")

        for skill_dir in sorted(root.iterdir()):
            if not skill_dir.is_dir():
                continue
            # 跳过 tests/ 等隐藏或特殊目录
            if skill_dir.name.startswith("_") or skill_dir.name.startswith("."):
                continue
            # 跳过不含 skill.yaml 的目录（如 docs/, tools/, 示例目录等）
            if not (skill_dir / "skill.yaml").is_file():
                continue
            try:
                pkg = self._load_skill_package(skill_dir)
                self.register(pkg)
                loaded.append(pkg)
            except Exception as exc:
                raise SkillLoadError(
                    f"Failed to load skill from '{skill_dir}': {exc}"
                ) from exc

        return loaded

    def _load_skill_package(self, skill_dir: Path) -> SkillPackage:
        """从单个 skill 子目录加载包。"""
        skill_yaml_path = skill_dir / "skill.yaml"
        if not skill_yaml_path.is_file():
            raise SkillLoadError(
                f"skill.yaml not found in '{skill_dir}'. "
                "Each skill must have a skill.yaml at its root."
            )

        metadata = SkillMetadata.from_yaml(skill_yaml_path.read_text(encoding="utf-8"))
        return SkillPackage(metadata=metadata, root_dir=skill_dir)

    # ── 查询操作 ─────────────────────────────────────────────────────────────

    def get(self, skill_id: str, version: str | None = None) -> SkillPackage:
        """
        获取 Skill，支持精确版本和最新版本。

        Args:
            skill_id: Skill ID（如 "call_graph_trace"）
            version:  指定版本（如 "1.0.0"），为 None 时返回最新版本

        Returns:
            SkillPackage
        """
        key = f"{skill_id}@{version}" if version else None

        if key and key in self._skills:
            return self._skills[key]

        # 无版本 → 找最新版本
        candidates = {
            pkg.skill_key: pkg
            for pkg in self._skills.values()
            if pkg.metadata.id == skill_id
        }
        if not candidates:
            raise SkillNotFoundError(skill_id, version)

        if version:
            raise SkillNotFoundError(skill_id, version)

        # 返回版本号最高的（字符串排序，适用于 semver）
        latest = max(
            candidates.keys(),
            key=lambda k: _parse_version(k.split("@")[1])
        )
        return candidates[latest]

    def find(self, tag: str) -> list[SkillPackage]:
        """按 tag 过滤 Skill。"""
        return [
            pkg for pkg in self._skills.values()
            if tag in pkg.metadata.tags
        ]

    def list_all(self) -> list[SkillPackage]:
        """列出所有已注册的 Skill。"""
        return list(self._skills.values())

    def validate_all(self) -> dict[str, list[str]]:
        """
        对所有已注册 Skill 执行 Schema 校验。
        Returns:
            { skill_key: [errors] }，错误列表为空=校验通过
        """
        return {
            pkg.skill_key: pkg.validate_schemas()
            for pkg in self._skills.values()
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
def _parse_version(v: str) -> tuple[int, int, int]:
    m = _VERSION_RE.search(v)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return (0, 0, 0)


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

default_registry = SkillRegistry()
