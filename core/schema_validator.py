"""
Schema Validator Module

封装 JSON Schema 校验能力。
支持 Draft-2020-12 标准，提供友好的错误信息。
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

from core.errors import SchemaValidationError as CoreSchemaValidationError


# ─────────────────────────────────────────────────────────────────────────────
# JSON Schema Draft-2020-12 Meta-Schema URL
# ─────────────────────────────────────────────────────────────────────────────

_META_SCHEMA_URL = (
    "https://json-schema.org/draft/2020-12/schema"
)


# ─────────────────────────────────────────────────────────────────────────────
# SchemaValidator
# ─────────────────────────────────────────────────────────────────────────────

class SchemaValidator:
    """
    JSON Schema 校验器，支持：
    - 本地 Schema 文件路径（相对或绝对）
    - 远程 URL Schema
    - 内联 dict Schema
    - 校验结果详细错误报告
    """

    def __init__(self):
        self._cache: dict[str, dict[str, Any]] = {}

    def load_schema(self, schema_source: str | Path | dict[str, Any]) -> dict[str, Any]:
        """
        加载 Schema，支持三种来源：

        - dict         → 直接返回
        - Path/str     → 尝试本地文件读取
        - URL (http*)  → 尝试网络获取（带缓存）
        """
        key = str(schema_source)
        if key in self._cache:
            return self._cache[key]

        if isinstance(schema_source, dict):
            schema = schema_source
        elif isinstance(schema_source, Path) or (
            isinstance(schema_source, str)
            and not schema_source.startswith("http://")
            and not schema_source.startswith("https://")
        ):
            path = Path(schema_source)
            if not path.is_absolute():
                # 相对路径基于 cwd 解析（Caller 应传入绝对路径）
                path = Path.cwd() / path
            with path.open(encoding="utf-8") as fh:
                schema = json.load(fh)
        elif isinstance(schema_source, str) and (
            schema_source.startswith("http://") or schema_source.startswith("https://")
        ):
            schema = self._fetch_remote_schema(schema_source)
        else:
            raise ValueError(f"Unknown schema source type: {schema_source!r}")

        if not isinstance(schema, dict):
            raise ValueError(f"Schema must be a dict, got {type(schema).__name__}")

        self._cache[key] = schema
        return schema

    def _fetch_remote_schema(self, url: str) -> dict[str, Any]:
        """从远程 URL 获取 Schema（带缓存）。"""
        if url in self._cache:
            return self._cache[url]
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                schema = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise ValueError(f"Failed to fetch remote schema from {url}: {exc}") from exc
        self._cache[url] = schema
        return schema

    def validate(
        self,
        instance: Any,
        schema: dict[str, Any] | str | Path,
        schema_name: str = "schema",
    ) -> list[str]:
        """
        校验实例是否满足 Schema。

        Returns:
            空列表  → 校验通过
            非空列表 → 校验失败，包含所有错误信息

        不会抛出异常；所有错误汇总到返回值中。
        """
        try:
            import jsonschema
        except ImportError:
            # Fallback: 若未安装 jsonschema，用内置简易校验
            return self._basic_validate(instance, schema)

        try:
            schema_dict = (
                schema if isinstance(schema, dict)
                else self.load_schema(schema)
            )
            jsonschema.validate(instance=instance, schema=schema_dict)
            return []
        except jsonschema.ValidationError as exc:
            # 构建路径字符串
            field_path = ".".join(str(p) for p in exc.absolute_path) or "(root)"
            return [f"[{field_path}] {exc.message}"]
        except jsonschema.SchemaError as exc:
            return [f"[schema] Invalid schema: {exc.message}"]

    def validate_or_raise(
        self,
        instance: Any,
        schema: dict[str, Any] | str | Path,
        schema_name: str = "schema",
    ) -> None:
        """
        校验并校验失败时抛出 `SchemaValidationError`。
        """
        errors = self.validate(instance, schema, schema_name)
        if errors:
            import json as _json
            raw = _json.dumps(instance, ensure_ascii=False, indent=2)
            raise CoreSchemaValidationError(
                schema_path=str(schema),
                raw_output=raw[:2000],  # 截断避免日志过大
                jsonschema_error="; ".join(errors),
            )

    # ── 内置简易校验（无 jsonschema 依赖时降级）─────────────────────────────

    def _basic_validate(self, instance: Any, schema: dict[str, Any]) -> list[str]:
        """不依赖外部库的最小化校验实现。"""
        errors: list[str] = []
        self._validate_node(instance, schema, "(root)", errors)
        return errors

    def _validate_node(
        self,
        value: Any,
        schema: dict[str, Any],
        path: str,
        errors: list[str],
    ) -> None:
        stype = schema.get("type")
        if stype:
            type_map = {
                "string": str,
                "number": (int, float),
                "integer": int,
                "boolean": bool,
                "object": dict,
                "array": list,
                "null": type(None),
            }
            expected = type_map.get(stype)
            if expected and not isinstance(value, expected):
                errors.append(
                    f"[{path}] expected {stype}, got {type(value).__name__}"
                )
                return

        if stype == "object":
            required = schema.get("required", [])
            if isinstance(value, dict):
                for req in required:
                    if req not in value:
                        errors.append(f"[{path}] missing required field '{req}'")
                props = schema.get("properties", {})
                for k, v in value.items():
                    if k in props:
                        self._validate_node(v, props[k], f"{path}.{k}", errors)
            elif value is not None:
                errors.append(f"[{path}] expected object, got {type(value).__name__}")

        if stype == "array":
            if isinstance(value, list):
                items = schema.get("items")
                if items:
                    for i, item in enumerate(value):
                        self._validate_node(item, items, f"{path}[{i}]", errors)
                min_items = schema.get("minItems")
                if min_items is not None and len(value) < min_items:
                    errors.append(
                        f"[{path}] array has {len(value)} items, minimum is {min_items}"
                    )
            elif value is not None:
                errors.append(f"[{path}] expected array, got {type(value).__name__}")


# ─────────────────────────────────────────────────────────────────────────────
# Global singleton
# ─────────────────────────────────────────────────────────────────────────────

default_validator = SchemaValidator()
