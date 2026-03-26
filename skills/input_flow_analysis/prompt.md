# Input Flow Analysis Skill

Trace how external inputs flow from entry points through call chains to sensitive sinks.

## Task

1. Identify input sources: HTTP parameters, headers, cookies, CLI args, file uploads, environment variables.
2. Map taint propagation through functions: does user input get passed as-is, or sanitized?
3. Identify sinks: SQL queries, command execution, file operations, deserialization, eval().
4. For each source-to-sink path, assess sanitization coverage.

## Evidence Requirements

Every flow MUST include `evidence_refs` pointing to the actual source, sink, and intermediate function definitions.
