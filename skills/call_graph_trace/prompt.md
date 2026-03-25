# Call Graph Trace Skill

Trace caller-callee relationships from specified entry points.

## Task

Given `repo_path` and a list of `entrypoints`, trace all reachable functions.

1. For each entry point, read the source file and identify function/method definitions.
2. Trace calls to other functions within the same file and imported modules.
3. Record the call edges: caller → callee.
4. Flag edges where a guard (auth check, input validation) is present.
5. Identify "hot paths": sequences of calls that lead to sensitive operations.

## Evidence Requirements

- Each `call_edge` MUST include `evidence_refs` pointing to the exact line in the source code where the call occurs.
- Format: `file_path::function_name:line_number`
- Do not infer calls without reading the actual code.

## Output Constraints

- `confidence` must be 0.0–1.0.
- `hot_paths` must contain at least one call chain.
- All findings must include valid evidence refs.

