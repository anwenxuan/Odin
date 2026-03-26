# Auth Logic Detection Skill

Locate authentication and authorization logic throughout the codebase.

## Task

1. Find auth-related files and functions (login, verify, authenticate, authorize, check_permission).
2. Trace the authentication flow: how is identity established and verified?
3. Identify guard functions: decorators, middleware, checks that enforce auth.
4. Assess guard coverage: are there endpoints or operations NOT covered by any guard?
5. Look for bypass conditions (e.g., env vars, debug flags, internal IPs).
6. Every guard point MUST include evidence_refs to the exact file and line.

## Evidence Requirements

Every auth_flow and guard_point MUST include valid evidence_refs pointing to the source code.
