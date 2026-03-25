# Odin — AI 代码研究系统

基于证据的代码分析框架，用于系统性漏洞研究和代码学习

---

## What's New (v0.2.0)

Odin has been upgraded from a "LLM prompt framework" into a **fully autonomous AI Agent system**:

- **Tool Executor**: LLM can now call real tools to read files, search code, run shell commands, and execute Git operations
- **Agent Loop**: Iterative LLM → tool → LLM → tool → ... → output cycle
- **CLI**: `odin analyze <repo>` for end-to-end analysis
- **Pipeline Parallelism**: DAG-layered parallel execution of independent workflow steps
- **MCP Integration**: Connect to GitHub, CVE, Jira via Model Context Protocol
- **FastAPI Server**: HTTP API for team collaboration
- **RAG Store**: Full-text search over historical analysis reports
- **Evaluation Framework**: Precision / Recall / F1 benchmarks

---

## Architecture

```
CLI / API Server
    └─→ PipelineExecutor          (DAG layered parallel execution)
              └─→ SkillAgent (per step)    (LLM loop + tool calls)
                        ├─→ LLM Adapter      (OpenAI / Anthropic / Ollama / Mock)
                        └─→ ToolExecutor     (read_file / search_code / git_ops / ...)
                                 └─→ EvidenceStore / MemoryStore / RAGStore
```

---

## Quick Start

```bash
# Install dependencies
pip install -e "."

# Or install all at once
pip install openai anthropic fastapi uvicorn pydantic pyyaml jsonschema

# List available skills
python odin.py list-skills
python odin.py list-workflows

# Analyze a local repository (mock mode — no API key needed)
python odin.py analyze ./my-repo --workflow codebase_research --provider mock --verbose

# Analyze a GitHub repo (requires OPENAI_API_KEY)
export OPENAI_API_KEY=sk-...
python odin.py analyze https://github.com/owner/repo \
  --workflow vulnerability_research --provider openai --model gpt-4o-mini

# Output to file
python odin.py analyze ./my-repo --output ./report.md --output-format markdown

# Start API Server
python -m uvicorn cli.commands.serve:app --reload --port 8080
# Then: POST /analyze with {"repo_url": "https://github.com/..."}
```

---

## Project Structure

```
Odin/
├── tools/                        # Phase 1: Tool Execution Layer
│   ├── base.py                  # Tool interface, ToolResult, ToolContext
│   ├── executor.py              # ToolExecutor — registry, dispatch, history
│   ├── registry.py              # @tool decorator
│   └── builtin/
│       ├── read_file.py         # Read file with line range
│       ├── list_dir.py          # Directory tree listing
│       ├── search_code.py       # Regex code search with context
│       ├── run_shell.py         # Safe shell commands (whitelist)
│       ├── git_ops.py           # git clone / log / diff
│       └── detect_lang.py        # Tech stack detection
│
├── agent/                       # Phase 2: Agent Loop Layer
│   ├── messages.py              # HumanMessage / AIMessage / ToolMessage / SystemMessage
│   ├── state.py                 # AgentState + LoopConfig (iteration limits, evidence rules)
│   ├── llm_adapter.py          # LLM Adapter — OpenAI / Anthropic / Ollama / Mock
│   ├── loop.py                  # AgentLoop — core iteration engine
│   ├── skill_agent.py           # SkillAgent — wraps Skill as an agent
│   └── merger.py                # AgentResultMerger — multi-agent result aggregation
│
├── core/
│   ├── skill_loader.py         # Skill loading & registry
│   ├── workflow_orchestrator.py # Workflow DAG loading & execution
│   ├── pipeline_executor.py     # Phase 4: DAG-layered parallel execution
│   ├── prompt_runner.py         # Prompt rendering, LLM call, JSON validation
│   ├── schema_validator.py      # JSON Schema Draft-2020-12
│   ├── execution_context.py     # Variable resolution ${inputs.x} / ${steps.S.outputs.y}
│   └── errors.py               # Hierarchical exception types
│
├── cli/
│   ├── main.py                 # CLI entry point
│   └── commands/
│       ├── analyze.py           # analyze <repo> command
│       ├── serve.py             # FastAPI HTTP server
│       ├── list_skills.py
│       └── list_workflows.py
│
├── mcp/                        # Phase 5: MCP Client
│   └── client.py               # MCP stdio client + GitHub / CVE / Jira adapters
│
├── rag/                        # Phase 6: RAG Store
│   └── store.py                # SQLite FTS5 full-text search over historical reports
│
├── memory/
│   ├── models.py               # MEU, ResearchArtifact, Conclusion, EvidenceLink
│   ├── evidence_store.py        # MEU storage, indexing, retrieval
│   └── memory_store.py          # Artifact & Conclusion storage
│
├── skills/                      # 12 MVP Skills
│   ├── repo_map/               # Build module map
│   ├── entrypoints_detection/   # Find HTTP/CLI/message handlers
│   ├── call_graph_trace/        # Trace call relationships
│   ├── data_structure_extraction/
│   ├── auth_logic_detection/
│   ├── input_flow_analysis/
│   ├── sink_detection/          # Identify dangerous sinks
│   ├── dependency_analysis/
│   ├── attack_surface_mapping/
│   ├── vulnerability_hypothesis/
│   ├── exploit_generation/
│   └── report_generation/
│
├── workflows/                   # 3 workflow definitions (YAML)
│   ├── vulnerability_research/  # 11 steps: full vulnerability research
│   ├── codebase_research/        # 5 steps: fast codebase understanding
│   └── architecture_analysis/   # 7 steps: deep architecture analysis
│
├── benchmarks/                  # Phase 6: Evaluation Framework
│   └── eval.py                 # Precision / Recall / F1 benchmarks
│
├── examples/
│   └── run_workflow.py
│
├── odin.py                     # CLI entry point
├── pyproject.toml
└── README.md
```

---

## Skills (12 MVP)

| Skill | Purpose | Phase |
|-------|---------|-------|
| `repo_map` | Build module map, detect tech stack | MVP |
| `entrypoints_detection` | Find HTTP/CLI handlers | MVP |
| `call_graph_trace` | Trace function call relationships | MVP |
| `data_structure_extraction` | Extract entity models | MVP |
| `auth_logic_detection` | Locate auth/authorization guards | MVP |
| `input_flow_analysis` | Trace input-to-sink data flows | MVP |
| `sink_detection` | Identify dangerous sink functions | MVP |
| `dependency_analysis` | Map third-party dependencies | MVP |
| `attack_surface_mapping` | Correlate surfaces with risk | MVP |
| `vulnerability_hypothesis` | Generate CWE-tagged vuln hypotheses | MVP |
| `exploit_generation` | Generate PoC concepts | MVP |
| `report_generation` | Produce structured Markdown report | MVP |

---

## Workflows

### `vulnerability_research` (11 steps)
Full chain: `repo_discovery → entrypoints → call_graph → auth → input_flow → sinks → deps → attack_surface → hypotheses → PoC → report`

### `codebase_research` (5 steps)
Fast understanding: `repo_discovery → entrypoints → call_graph → data_structures → report`

### `architecture_analysis` (7 steps)
Deep architecture: `repo_discovery → entrypoints → call_graph → data_structures → auth → deps → report`

---

## Evidence Model

Every conclusion in Odin is backed by a **Minimum Evidence Unit (MEU)**:

```json
{
  "meu_id": "MEU-abc123def4",
  "file_path": "src/auth/login.py",
  "symbol": "validate_token",
  "line_start": 42,
  "line_end": 58,
  "snippet": "def validate_token(token: str) -> bool: ...",
  "extracted_by": "call_graph_trace@1.0.0",
  "confidence": 0.95
}
```

**No conclusion without evidence.** The framework enforces this at runtime.

---

## Configuration

### Environment Variables

```bash
# OpenAI (recommended: gpt-4o-mini for cost efficiency)
export OPENAI_API_KEY=sk-...

# Anthropic Claude
export ANTHROPIC_API_KEY=sk-ant-...

# Optional overrides
export ODIN_PROVIDER=openai
export ODIN_MODEL=gpt-4o-mini
```

### LLM Provider Selection

```python
from agent.llm_adapter import create_adapter

# OpenAI
llm = create_adapter("openai", default_model="gpt-4o-mini")

# Anthropic
llm = create_adapter("anthropic", default_model="claude-sonnet-4-20250514")

# Local Ollama
llm = create_adapter("ollama", base_url="http://localhost:11434/v1", default_model="llama3")

# Mock (for testing)
llm = create_adapter("mock")
```

### Workflow Selection

| CLI Flag | Description |
|----------|-------------|
| `--workflow vulnerability_research` | Full security analysis (11 steps) |
| `--workflow codebase_research` | Fast understanding (5 steps) |
| `--workflow architecture_analysis` | Deep architecture (7 steps) |

---

## Phase 1 — Tool Executor

The Tool layer gives the LLM real "hands" to interact with the codebase:

| Tool | Description |
|------|-------------|
| `read_file` | Read file content with line range |
| `list_dir` | List directory tree with file type icons |
| `search_code` | Regex search with context lines |
| `run_shell` | Safe shell commands (whitelist: git, find, grep, wc...) |
| `git_clone` | Clone remote repo to local temp dir |
| `git_log` | View commit history |
| `git_diff` | View file changes between commits |
| `detect_lang` | Detect programming language & framework |

All tools use **Function Calling API** format for reliable LLM integration.

---

## Phase 2 — Agent Loop

The Agent Loop enables iterative LLM ↔ tool interaction:

```
Render Skill Prompt → LLM → Has tool_calls?
    ├─ Yes → Execute tools → Append ToolMessage → LLM (continue)
    └─ No → Extract JSON output → Validate → Store MEUs → Done
```

- Max iterations: 20 (configurable)
- Evidence enforcement: every finding must cite `evidence_refs`
- Schema validation: JSON Schema Draft-2020-12

---

## Phase 4 — Parallel Pipeline

Workflow steps are grouped into **DAG layers** and executed in parallel:

```
Layer 1: repo_discovery
Layer 2: entrypoints_detection, sink_detection, dependency_analysis  ← parallel
Layer 3: call_graph_trace, auth_logic_detection                 ← parallel
Layer 4: input_flow_analysis
Layer 5: attack_surface_mapping
Layer 6: vulnerability_hypothesis
Layer 7: exploit_generation
Layer 8: report_generation
```

Independent steps in the same layer run concurrently via `ThreadPoolExecutor`.

---

## Phase 6 — RAG Store

Historical reports are chunked and indexed via **SQLite FTS5** (no external dependencies):

```python
from rag.store import RAGStore

rag = RAGStore(persist_dir="./data/rag")
rag.index_report(run_id="run_xxx", report_text="...", repo_url="...")
context = rag.get_context_for_prompt("authentication vulnerability in JWT")
# → Injected into next analysis prompt, reducing duplicate work
```

---

## Evaluation Framework

```bash
# List available benchmark datasets
python benchmarks/eval.py

# Run all benchmarks (mock mode)
python benchmarks/eval.py --all --provider mock

# Run specific dataset
python benchmarks/eval.py --dataset owasp-sql-injection --provider openai

# Output results
python benchmarks/eval.py --all --output-dir ./benchmarks/results
```

Metrics: **Precision / Recall / F1** against ground truth vulnerability datasets.

---

## Development Roadmap

See `odin_ai_code_research_system_-_完整开发规划_b2a6d593.plan.md` for the full phased plan.

---

## License

MIT
