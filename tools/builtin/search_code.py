"""
tools/builtin/search_code.py — 在代码库中搜索匹配正则表达式的代码

功能：
- 正则表达式全文搜索
- 文件类型过滤（*.py / *.js 等）
- 行上下文（显示匹配行及前后几行）
- 匹配统计（命中次数、涉及文件数）
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.base import Tool, ToolResult, ToolContext, tool

_DEFAULT_CONTEXT_LINES = 2  # 上下文行数
_MAX_MATCHES = 100         # 最多显示匹配数
_MAX_FILE_SCAN = 500       # 最多扫描文件数


@tool
class SearchCodeTool:
    """在代码库中搜索匹配正则表达式或关键词的代码片段。"""

    name = "search_code"
    description = (
        "在代码库中搜索匹配正则表达式或关键词的代码。\n"
        "用途：\n"
        "  - 找某个函数/变量在哪里定义和使用\n"
        "  - 定位特定 API 调用（如 os.system、eval、exec）\n"
        "  - 找所有包含敏感关键词的文件（如 password、token、secret）\n"
        "建议：先用宽泛搜索了解覆盖范围，再用精确正则深入定位。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": (
                    "正则表达式或关键词。\n"
                    "示例：\n"
                    "  - 关键词：password、token、eval\n"
                    "  - 正则：def \\w+\\(.*\\):  （匹配函数定义）\n"
                    "  - 正则：import (os|subprocess)  （匹配导入语句）\n"
                    "  - 正则：\\.(execute|system|call)\\(" （匹配危险方法调用）"
                ),
            },
            "path": {
                "type": "string",
                "description": "搜索路径（相对于仓库根）。留空则搜索全仓库。",
            },
            "file_filter": {
                "type": "string",
                "description": "文件扩展名过滤，如：*.py、*.js、*.go（支持逗号分隔多个）。",
            },
            "context_lines": {
                "type": "integer",
                "description": f"每个匹配周围显示的上下文行数（默认 {_DEFAULT_CONTEXT_LINES}）。",
                "default": _DEFAULT_CONTEXT_LINES,
            },
            "case_sensitive": {
                "type": "boolean",
                "description": "是否区分大小写（默认 False，即不区分）。",
                "default": False,
            },
            "is_regex": {
                "type": "boolean",
                "description": "pattern 是否为正则表达式（默认 True）。设为 False 则做字面匹配。",
                "default": True,
            },
        },
        "required": ["pattern"],
    }

    def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if ctx.repo_path is None:
            return ToolResult.err("repo_path 未设置")

        pattern_raw = args.get("pattern", "").strip()
        if not pattern_raw:
            return ToolResult.err("pattern 参数不能为空")

        search_path = args.get("path", "").strip()
        file_filters = self._parse_filters(args.get("file_filter", ""))
        context_lines = int(args.get("context_lines", _DEFAULT_CONTEXT_LINES))
        case_sensitive = bool(args.get("case_sensitive", False))
        is_regex = bool(args.get("is_regex", True))

        # 解析搜索路径
        if search_path:
            base = ctx.resolve_path(search_path)
        else:
            base = ctx.repo_path

        if base is None or not base.exists():
            return ToolResult.err(f"搜索路径不存在：{search_path or '/'}")

        # 编译正则
        try:
            if is_regex:
                flags = 0 if case_sensitive else re.IGNORECASE
                compiled = re.compile(pattern_raw, flags)
            else:
                compiled = re.compile(re.escape(pattern_raw), 0 if case_sensitive else re.IGNORECASE)
        except re.error as exc:
            return ToolResult.err(f"正则表达式错误：{exc}")

        # 搜索
        all_matches: list[dict[str, Any]] = []
        scanned_files = 0

        for file_path in self._iter_files(base, file_filters):
            scanned_files += 1
            if scanned_files > _MAX_FILE_SCAN:
                break

            matches = self._search_file(file_path, compiled, context_lines)
            if matches:
                all_matches.extend(matches)
                if len(all_matches) >= _MAX_MATCHES:
                    break

        # 格式化输出
        if not all_matches:
            if scanned_files >= _MAX_FILE_SCAN:
                note = f"\n（已扫描 {scanned_files} 个文件，仍未找到匹配）"
            else:
                note = f"\n（扫描了 {scanned_files} 个文件）"
            return ToolResult.ok(
                f"No matches found for: {pattern_raw}{note}",
                total_matches=0,
                files_scanned=scanned_files,
            )

        # 分文件聚合输出
        output_parts = [
            f"=== Search: {pattern_raw} ===",
            f"Files scanned: {scanned_files}",
            f"Files with matches: {len(set(m['file'] for m in all_matches))}",
            f"Total matches: {len(all_matches)}",
            "",
        ]

        current_file = None
        for m in all_matches:
            if m["file"] != current_file:
                current_file = m["file"]
                output_parts.append(f"\n--- {current_file} ---")
            # 行号 + 内容
            marker = ">>>" if m["is_match_line"] else "   "
            line_num = str(m["line_num"]).rjust(4)
            output_parts.append(f"  {line_num} {marker} {m['content']}")

        if len(all_matches) >= _MAX_MATCHES:
            output_parts.append(f"\n[显示前 {_MAX_MATCHES} 个匹配，搜索未完全穷尽]")

        # 统计
        files_with_hits = len(set(m["file"] for m in all_matches))
        metadata = {
            "total_matches": len(all_matches),
            "files_scanned": scanned_files,
            "files_with_matches": files_with_hits,
            "pattern": pattern_raw,
        }

        return ToolResult.ok("\n".join(output_parts), **metadata)

    def _parse_filters(self, raw: str) -> list[str]:
        if not raw:
            return []
        return [f.strip() for f in raw.split(",") if f.strip()]

    def _iter_files(self, root: Path, filters: list[str]) -> list[Path]:
        """迭代匹配过滤条件的文件。"""
        result: list[Path] = []

        # 二进制文件扩展名
        skip_exts = {
            ".exe", ".dll", ".so", ".dylib", ".o", ".obj",
            ".class", ".jar", ".war",
            ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
            ".pdf", ".zip", ".tar", ".gz", ".rar",
            ".mp3", ".mp4", ".wav",
            ".pyc", ".pyo", ".pyz",
        }

        def accept(path: Path) -> bool:
            if path.suffix.lower() in skip_exts:
                return False
            if filters:
                return any(self._glob_match(path.name, f) for f in filters)
            return True

        try:
            for p in root.rglob("*"):
                if p.is_file() and accept(p):
                    result.append(p)
        except PermissionError:
            pass

        return result

    def _glob_match(self, name: str, pattern: str) -> bool:
        """简单的 glob 匹配（支持 * 和 ?）。"""
        regex = pattern.replace(".", r"\.").replace("**/", ".*/").replace("**", ".*").replace("*", "[^/]*").replace("?", ".")
        try:
            return bool(re.match(f"^{regex}$", name))
        except re.error:
            return False

    def _search_file(
        self, path: Path, compiled: re.Pattern, context_lines: int
    ) -> list[dict[str, Any]]:
        """搜索单个文件。"""
        matches: list[dict[str, Any]] = []
        try:
            with path.open(encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError:
            return matches

        for i, line in enumerate(lines):
            line_num = i + 1
            content = line.rstrip("\n\r")
            m = compiled.search(content)
            if m:
                matches.append({
                    "file": str(path),
                    "line_num": line_num,
                    "content": content,
                    "is_match_line": True,
                })
                # 追加上下文
                for j in range(1, context_lines + 1):
                    if i + j < len(lines):
                        ctx_line = lines[i + j].rstrip("\n\r")
                        matches.append({
                            "file": str(path),
                            "line_num": line_num + j,
                            "content": ctx_line,
                            "is_match_line": False,
                        })
                for j in range(1, context_lines + 1):
                    if i - j >= 0:
                        ctx_line = lines[i - j].rstrip("\n\r")
                        matches.append({
                            "file": str(path),
                            "line_num": line_num - j,
                            "content": ctx_line,
                            "is_match_line": False,
                        })
        return matches
