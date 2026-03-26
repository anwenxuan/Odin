# Entrypoints Detection Skill

Identify all service entry points in the target repository.

## Task

1. Scan for HTTP handlers based on the detected framework (Express, Gin, Django, FastAPI, etc.).
2. Find CLI entry points (main functions, command definitions).
3. Detect message consumers (Kafka consumers, SQS handlers, etc.).
4. Identify WebSocket handlers, cron jobs, and background workers.
5. Note which endpoints require authentication.

## Evidence Requirements

Every entry point MUST include a valid `evidence_ref` pointing to the file and line where it is defined.
