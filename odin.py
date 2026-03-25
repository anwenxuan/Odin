#!/usr/bin/env python3
"""
Odin CLI Entry Point

安装后可使用：
    odin analyze https://github.com/owner/repo --workflow vulnerability_research
    odin list-skills
    odin list-workflows
"""

import sys
from pathlib import Path

# 添加项目根到 Python 路径
_project_root = Path(__file__).parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from cli.main import main

if __name__ == "__main__":
    sys.exit(main())
