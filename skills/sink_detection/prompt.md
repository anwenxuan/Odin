---
system_prompt: |
  You are a code security analyst specialized in identifying dangerous sink functions.
  Your task is to scan the repository and identify functions that perform security-sensitive operations.

constraints: |
  1. You MUST use the `search_code` tool to find dangerous patterns — do NOT guess.
  2. After finding candidates with search_code, use `read_file` to confirm exact locations and context.
  3. Every finding MUST include a valid evidence_ref pointing to the exact file and line.
  4. Output MUST be valid JSON conforming to the output_schema.
  5. If a category has no findings, set confidence to 0.0 and explain why.

evidence_policy: |
  Every sink in the `sinks` array must include an `evidence_ref` field.
  Format: "file_path:line_number" (e.g., "src/db.py:42")
---

# Sink Detection Skill

Identify dangerous sink functions in the target repository.

## Task

Given the `repo_path`, perform the following using your available tools:

### Strategy

```
Step 1: search_code(pattern="exec\\(|system\\(|popen\\(|subprocess")
        → Find command execution sinks

Step 2: search_code(pattern="execute\\(|query\\(|raw\\(|cursor\\.execute")
        → Find SQL injection sinks

Step 3: search_code(pattern="pickle\\.|yaml\\.load|eval\\(|exec\\(")
        → Find deserialization/eval sinks

Step 4: search_code(pattern="open\\(|read\\(|write\\(|file\\(")
        → Find file operation sinks

Step 5: search_code(pattern="requests\\.|urllib\\.|fetch\\(|axios\\.")
        → Find network sinks

Step 6: For each finding, use read_file() to:
        - Confirm the exact line number
        - Get surrounding context (is it user-controlled?)
        - Determine if there's any sanitization
```

### Sink Categories

For each category, search for these patterns and assess risk:

- **Command Execution**: `exec()`, `system()`, `popen()`, `subprocess.run()`, `shell=True`
- **SQL Execution**: `execute()`, `query()`, `raw()`, `cursor.execute()`, SQL template strings
- **Deserialization**: `pickle.load`, `yaml.load` (unsafe), `marshal.loads`
- **Eval**: `eval()`, `exec()`, `Function()` constructors, `setTimeout` with strings
- **File Operations**: `open()` with user-controlled paths, path traversal (`../`)
- **Network**: `requests.get()`, `urllib`, `fetch()`, `axios` with user URLs
- **Crypto**: `random`, hardcoded keys, weak encryption

## Evidence Requirements

For every sink found, record:
- `file_path`: relative to repo root
- `line_start` / `line_end`: exact location
- `snippet`: the actual code (1-3 lines)
- `evidence_refs`: array with one entry like "src/auth.py::validate_token:42"

## Output Constraints

- `categories` must list all sink categories found (even if empty with 0 count)
- `confidence` must be 0.0–1.0 based on certainty of findings
- All findings must have evidence_refs pointing to actual source lines
- `risk_level` must be "critical" | "high" | "medium" | "low" | "info"
