# Import directly from modules (bypass agent/__init__.py to avoid cascade)
import pytest
import tempfile
from pathlib import Path

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from agent.runtime import AgentRuntime, RuntimeConfig, RuntimeState, RuntimeStatus, StepRecord
from agent.planner import Action, ActionType, ActionPriority, Planner, TaskDecomposition
from agent.checkpoint import CheckpointManager, CheckpointRecord
from agent.observer import NullObserver, LoggingObserver
from agent.llm_adapter import MockAdapter


class TestRuntimeConfig:
    def test_default_config(self):
        cfg = RuntimeConfig(task_id="test-task")
        assert cfg.task_id == "test-task"
        assert cfg.max_iterations == 50
        assert cfg.checkpoint_enabled is True
        assert cfg.checkpoint_interval == 5

    def test_to_loop_config(self):
        cfg = RuntimeConfig(max_iterations=10, evidence_required=False)
        loop_cfg = cfg.to_loop_config()
        assert loop_cfg.max_iterations == 10
        assert loop_cfg.evidence_required is False


class TestRuntimeState:
    def test_init(self):
        state = RuntimeState(task_id="task-001")
        assert state.task_id == "task-001"
        assert state.step == 0
        assert state.runtime_status == RuntimeStatus.IDLE
        assert len(list(state.recent_steps)) == 0

    def test_add_step(self):
        state = RuntimeState(task_id="task-001")
        record = StepRecord(
            step=1,
            action=None,
            tool_calls=[],
            observation="test observation",
            evidence_refs=["MEU-001"],
            duration_ms=100,
            status="success",
        )
        state.add_step(record)
        assert state.step == 1
        assert len(list(state.recent_steps)) == 1

    def test_increment_step(self):
        state = RuntimeState(task_id="task-001")
        assert state.increment_step() == 1
        assert state.increment_step() == 2

    def test_get_context_summary(self):
        state = RuntimeState(task_id="task-001")
        summary = state.get_context_summary()
        assert "task-001" in summary
        assert "Current Step: 0" in summary

    def test_to_dict(self):
        state = RuntimeState(task_id="task-001")
        d = state.to_dict()
        assert d["task_id"] == "task-001"
        assert d["step"] == 0
        assert d["runtime_status"] == "idle"


class TestStepRecord:
    def test_creation(self):
        record = StepRecord(
            step=1,
            action=None,
            tool_calls=[],
            observation="done",
            evidence_refs=["MEU-001"],
            duration_ms=200,
            status="success",
            error=None,
            tokens_used=150,
        )
        assert record.step == 1
        assert record.status == "success"
        assert record.duration_ms == 200

    def test_to_dict(self):
        record = StepRecord(
            step=2,
            action=None,
            tool_calls=[],
            observation="step 2",
            evidence_refs=["MEU-002"],
            duration_ms=300,
            status="success",
        )
        d = record.to_dict()
        assert d["step"] == 2
        assert d["status"] == "success"
        assert d["duration_ms"] == 300


class TestAgentRuntime:
    def test_init_with_defaults(self):
        adapter = MockAdapter()
        runtime = AgentRuntime(
            task_id="test-task",
            llm_adapter=adapter,
            tool_executor=None,
        )
        assert runtime.task_id == "test-task"
        assert runtime.state.task_id == "test-task"
        assert runtime.config.checkpoint_enabled is True
        assert runtime.state.runtime_status == RuntimeStatus.INITIALIZING

    def test_init_with_custom_config(self):
        adapter = MockAdapter()
        cfg = RuntimeConfig(
            task_id="custom-task",
            max_iterations=5,
            checkpoint_enabled=False,
        )
        runtime = AgentRuntime(
            task_id="custom-task",
            llm_adapter=adapter,
            tool_executor=None,
            config=cfg,
        )
        assert runtime.config.max_iterations == 5
        assert runtime.config.checkpoint_enabled is False

    def test_add_observer(self):
        adapter = MockAdapter()
        runtime = AgentRuntime(task_id="test", llm_adapter=adapter, tool_executor=None)
        obs = LoggingObserver()
        runtime.add_observer(obs)
        assert len(runtime.observers) == 2  # NullObserver + LoggingObserver

    def test_get_state(self):
        adapter = MockAdapter()
        runtime = AgentRuntime(task_id="test", llm_adapter=adapter, tool_executor=None)
        state = runtime.get_state()
        assert state.task_id == "test"


class TestCheckpointManager:
    def test_save_and_restore(self, tmp_path):
        mgr = CheckpointManager(storage_dir=tmp_path)

        # Create mock state
        state = RuntimeState(task_id="task-ckpt")
        record = CheckpointRecord(
            task_id="task-ckpt",
            step=3,
            state_snapshot={"status": "running"},
            evidence_refs=["MEU-001"],
            tool_history=[],
            memory_snapshot={"steps": []},
            plan_snapshot=[],
        )
        # Write manually for test
        ckpt_path = tmp_path / "task-ckpt" / f"{record.checkpoint_id}.json"
        ckpt_path.parent.mkdir(parents=True, exist_ok=True)
        import json
        with open(ckpt_path, "w") as f:
            json.dump(record.to_dict(), f)

        # Restore
        restored = mgr.restore("task-ckpt", record.checkpoint_id)
        assert restored.step == 3
        assert restored.task_id == "task-ckpt"

    def test_latest(self, tmp_path):
        mgr = CheckpointManager(storage_dir=tmp_path)
        # No checkpoint yet
        latest = mgr.latest("nonexistent-task")
        assert latest is None

    def test_list_empty(self, tmp_path):
        mgr = CheckpointManager(storage_dir=tmp_path)
        checkpoints = mgr.list("empty-task")
        assert checkpoints == []

    def test_prune(self, tmp_path):
        mgr = CheckpointManager(storage_dir=tmp_path)
        # Prune on empty should return []
        deleted = mgr.prune("empty-task", keep_last=3)
        assert deleted == []


class TestPlanner:
    def test_action_creation(self):
        action = Action(
            id="action-1",
            type=ActionType.READ_FILE,
            description="Read main.py",
            params={"path": "main.py"},
            depends_on=[],
            priority=ActionPriority.HIGH,
        )
        assert action.id == "action-1"
        assert action.type == ActionType.READ_FILE
        assert action.status == "pending"

    def test_action_to_dict(self):
        action = Action(
            id="a-1",
            type=ActionType.SEARCH_CODE,
            description="search",
            params={"pattern": "TODO"},
        )
        d = action.to_dict()
        assert d["id"] == "a-1"
        assert d["type"] == "search_code"
        assert d["status"] == "pending"

    def test_action_from_dict(self):
        data = {
            "id": "a-2",
            "type": "run_shell",
            "description": "run command",
            "params": {"cmd": "ls"},
            "depends_on": [],
            "priority": 2,
        }
        action = Action.from_dict(data)
        assert action.id == "a-2"
        assert action.type == ActionType.RUN_SHELL

    def test_task_decomposition_init(self):
        decomp = TaskDecomposition(
            task_description="Analyze SQL injection",
            actions=[],
            summary="3 steps",
            estimated_steps=3,
        )
        assert decomp.task_description == "Analyze SQL injection"
        assert decomp.estimated_steps == 3

    def test_task_decomposition_all_completed(self):
        actions = [
            Action(id="a1", type=ActionType.READ_FILE, description="read", status="completed"),
            Action(id="a2", type=ActionType.SEARCH_CODE, description="search", status="completed"),
        ]
        decomp = TaskDecomposition(task_description="test", actions=actions)
        assert decomp.all_completed() is True

    def test_task_decomposition_get_ready_actions(self):
        actions = [
            Action(id="a1", type=ActionType.READ_FILE, description="read", status="completed"),
            Action(id="a2", type=ActionType.SEARCH_CODE, description="search",
                   depends_on=["a1"], priority=ActionPriority.HIGH),
            Action(id="a3", type=ActionType.ANALYZE, description="analyze",
                   depends_on=["a2"], priority=ActionPriority.MEDIUM),
        ]
        decomp = TaskDecomposition(task_description="test", actions=actions)
        ready = decomp.get_ready_actions()
        assert len(ready) == 1
        assert ready[0].id == "a2"
