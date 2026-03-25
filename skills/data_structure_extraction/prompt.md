# Data Structure Extraction Skill

Extract key data structures, entity models, and relationships from the codebase.

## Task

1. Identify data models: classes, structs, interfaces, enums, typed dicts.
2. List all fields with their type hints.
3. Flag fields that appear to hold sensitive data (password, token, PII, credentials).
4. Map relationships between entities (composition, inheritance, references).
5. For each entity, provide evidence_refs to the file and line where it is defined.

## Evidence Requirements

Every entity MUST include evidence_refs pointing to the source file and line where it is defined.
