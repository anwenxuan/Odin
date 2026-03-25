---
name: Odin AI Code Research System — Full Technical Analysis
overview: "Analyze the Odin AI Code Research System (Python multi-agent code analysis platform) across 5 phases: service identification, core execution path, local debugging guide, problem diagnosis handbook, and observability improvements."
todos: []
isProject: false
---

# Odin — AI Code Research System: Technical Analysis Plan

> **Output Language: 中文 (Chinese)**

## Phase 1: Service Identification

- Core duty, external interfaces (API endpoints), dependencies (OpenAI/Anthropic/Ollama LLM, SQLite, Git), startup entry (odin.py CLI → serve command → FastAPI/Uvicorn)
- Directory tree already explored; will read `odin.py`, `cli/commands/serve.py`, `cli/commands/analyze.py`, `.env.example`

## Phase 2: Core Execution Path

- Choose the **analyze command** as the primary path (most important user-facing workflow)
- Trace: `analyze <repo>` → `execute_workflow()` → `PipelineExecutor.execute()` → `SkillAgent.run()` → `AgentLoop.run()` → `LLMAdapter.call()` + `ToolExecutor.execute()`
- Read key files: `cli/commands/analyze.py`, `core/workflow_orchestrator.py`, `core/pipeline_executor.py`, `agent/skill_agent.py`, `agent/loop.py`, `agent/llm_adapter.py`, `tools/executor.py`
- Mark risk points inline

## Phase 3: Local Debugging Manual

- Docker-compose for LLM (Ollama) or API key setup
- Environment variables from `.env.example`
- Startup commands: `python odin.py serve` and `python odin.py analyze <repo>`
- Health check: `GET /health`, smoke test: `POST /analyze` with minimal payload

## Phase 4: Problem Location Guide

- 3 most probable failure scenarios:
  1. LLM API call failure (network / auth / rate limit)
  2. Tool execution failure (file not found, permission denied, shell command timeout)
  3. Workflow step dependency deadlock (DAG resolution failure)
- For each: log keywords, key functions, SQL queries, root cause summary

## Phase 5: Observability Improvements

- Log gaps in `agent/loop.py` (missing iteration count logging), `core/pipeline_executor.py` (missing step timing)
- Debug endpoint suggestions: `GET /debug/tools`, `GET /debug/memory`, `GET /debug/workflow/{id}/state`
- Trace ID injection: add `trace_id` to `ToolContext` / `AgentState`, propagate through `execute()` calls

Read files in parallel:

- `odin.py`, `cli/commands/analyze.py`, `cli/commands/serve.py`, `.env.example`
- `agent/skill_agent.py`, `agent/loop.py`, `agent/llm_adapter.py`
- `core/workflow_orchestrator.py`, `core/pipeline_executor.py`
- `tools/executor.py`, `tools/builtin/run_shell.py`
- `memory/evidence_store.py`, `rag/store.py`

