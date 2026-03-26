"""
tests/conftest.py — Pytest Configuration

Stubs broken third-party imports to allow isolated testing of agent submodules
without pulling in the entire codebase.
"""
import sys
from unittest.mock import MagicMock

# Stub the broken core imports that cascade through agent/__init__.py
sys.modules.setdefault("core", MagicMock())
sys.modules.setdefault("core.prompt_runner", MagicMock())
sys.modules.setdefault("core.skill_loader", MagicMock())
sys.modules.setdefault("core.pipeline_executor", MagicMock())
sys.modules.setdefault("core.workflow_orchestrator", MagicMock())
