"""
tools/builtin — 内置工具集

所有工具使用 @tool 装饰器自动注册到全局注册表。
ToolExecutor.auto_load_builtin() 会加载本目录下的所有模块。
"""

from tools.builtin.read_file import ReadFileTool
from tools.builtin.list_dir import ListDirTool
from tools.builtin.search_code import SearchCodeTool
from tools.builtin.run_shell import RunShellTool
from tools.builtin.git_ops import GitCloneTool, GitLogTool, GitDiffTool
from tools.builtin.detect_lang import DetectLangTool

__all__ = [
    "ReadFileTool",
    "ListDirTool",
    "SearchCodeTool",
    "RunShellTool",
    "GitCloneTool",
    "GitLogTool",
    "GitDiffTool",
    "DetectLangTool",
]
