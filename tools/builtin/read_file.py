"""
tools/builtin/read_file.py — 读取文件内容

支持：
- 整文件读取
- 行范围截取（line_start / line_end）
- 自动检测文件编码（UTF-8 / GBK / Latin-1）
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.base import Tool, ToolResult, ToolContext, tool

_MAX_BYTES = 512 * 1024   # 单次最多读取 512KB


@tool
class ReadFileTool:
    """读取文件内容，支持行范围截取。"""

    name = "read_file"
    description = (
        "读取指定文件的内容。支持整文件读取和行范围截取。\n"
        "用途：查看源代码、配置文件、测试文件等。\n"
        "限制：单次最多读取 512KB。\n"
        "注意：必须先调用 list_dir 了解目录结构，再用 read_file 深入查看具体文件。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "文件路径，相对于仓库根目录，例如：src/main.py、config/settings.json"
                ),
            },
            "line_start": {
                "type": "integer",
                "description": "起始行号（1-indexed，包含此行）。留空则从头开始。",
            },
            "line_end": {
                "type": "integer",
                "description": "结束行号（1-indexed，包含此行）。留空则读到文件末尾。",
            },
            "max_bytes": {
                "type": "integer",
                "description": "最大读取字节数（默认 512KB）。超出部分截断。",
                "default": 524288,
            },
        },
        "required": ["path"],
    }

    def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        rel_path = args.get("path", "").strip()
        if not rel_path:
            return ToolResult.err("path 参数不能为空")

        # 解析路径
        if ctx.repo_path is None:
            return ToolResult.err("repo_path 未设置，无法解析文件路径")

        abs_path = ctx.resolve_path(rel_path)
        if abs_path is None:
            return ToolResult.err(f"路径不安全或不存在：{rel_path}")

        if not abs_path.is_file():
            # 尝试常见变体（大小写、相对路径）
            alternatives = self._try_alternatives(ctx.repo_path, rel_path)
            if alternatives:
                alt_note = f"\n\n可能想找的文件：\n" + "\n".join(f"  - {a}" for a in alternatives)
            else:
                alt_note = ""
            return ToolResult.err(f"路径不是文件：{rel_path}{alt_note}")

        # 读取内容
        try:
            content = self._read_with_encoding(abs_path, args.get("max_bytes", _MAX_BYTES))
        except UnicodeDecodeError as exc:
            return ToolResult.err(f"文件编码无法解析（尝试了 UTF-8/GBK/Latin-1）：{exc}")
        except OSError as exc:
            return ToolResult.err(f"读取文件失败：{exc}")

        # 行范围截取
        line_start = args.get("line_start")
        line_end = args.get("line_end")
        if line_start is not None or line_end is not None:
            lines = content.splitlines(keepends=True)
            start_idx = (line_start - 1) if line_start else 0
            end_idx = line_end if line_end else len(lines)
            content = "".join(lines[start_idx:end_idx])

        # 截断超长输出（避免撑爆 LLM context）
        max_display = 300 * 1024  # 300KB
        truncated = False
        if len(content) > max_display:
            content = content[:max_display]
            truncated = True

        file_info = self._get_file_info(abs_path)
        header = f"=== File: {rel_path} ===\n"
        header += f"Size: {file_info['size_str']} | Lines: {file_info['lines']}\n"
        if truncated:
            header += f"[输出已截断，完整文件 {file_info['size_str']}]"
        header += "\n"

        return ToolResult.ok(
            header + content,
            file_path=str(abs_path),
            line_start=line_start,
            line_end=line_end,
            total_lines=file_info["lines"],
            truncated=truncated,
        )

    def _read_with_encoding(self, path: Path, max_bytes: int) -> str:
        """尝试多种编码读取文件。"""
        for enc in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
            try:
                raw = path.read_bytes()[:max_bytes]
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        # 最后尝试 errors='replace'
        raw = path.read_bytes()[:max_bytes]
        return raw.decode("utf-8", errors="replace")

    def _get_file_info(self, path: Path) -> dict[str, Any]:
        size = path.stat().st_size
        if size < 1024:
            size_str = f"{size}B"
        elif size < 1024 * 1024:
            size_str = f"{size / 1024:.1f}KB"
        else:
            size_str = f"{size / (1024 * 1024):.1f}MB"

        try:
            with path.open(encoding="utf-8", errors="replace") as f:
                lines = sum(1 for _ in f)
        except OSError:
            lines = 0

        return {"size": size, "size_str": size_str, "lines": lines}

    def _try_alternatives(self, repo_root: Path, rel_path: str) -> list[str]:
        """尝试常见路径变体。"""
        alts = []
        name = Path(rel_path).name
        try:
            for p in repo_root.rglob(name):
                try:
                    p.relative_to(repo_root)
                    alts.append(str(p))
                except ValueError:
                    pass
        except OSError:
            pass
        return alts[:5]
