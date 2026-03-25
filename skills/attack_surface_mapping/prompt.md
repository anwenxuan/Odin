# Attack Surface Mapping Skill

Map the attack surface by correlating entry points, authentication flows, and dangerous sinks.

## Task

1. For each entry point, determine what sinks it can reach (use call_graph and input_flow outputs).
2. Assess authentication coverage: is the endpoint / the sink protected by auth?
3. Identify surfaces where:
   - An unauthenticated endpoint reaches a dangerous sink
   - Input is not sanitized before reaching a sink
   - Auth logic has gaps (e.g., some routes protected, others not)
4. Score each surface for risk.

## Evidence Requirements

Every attack surface MUST reference the specific entry point, sink, and auth gap via evidence_refs.
