# Evidence Policy — MEU Linking Rules

This document defines the evidence linking policy for the AI Code Research System.

## What is a Minimum Evidence Unit (MEU)?

A MEU is the atomic unit of evidence in the system. Each MEU captures:
- **Location**: file path, symbol name, line range
- **Content**: the actual source code snippet (verbatim)
- **Metadata**: language, framework, confidence, tags
- **Relation**: optional call/data-flow relationship

## Evidence Linking Rules

### Rule 1: Every Conclusion Must Reference at Least One MEU

Any finding, hypothesis, call edge, entity, sink, or attack surface in the output MUST include at least one `evidence_ref` string in its `evidence_refs` array.

**Invalid**: A conclusion without evidence_refs will cause the Workflow Step to FAIL.

### Rule 2: Evidence Refs Must Be Resolvable

Evidence refs must point to a location in the source code that can be verified. Acceptable formats:
- `src/auth/login.py::validate_token:42` — file path, symbol, line number
- `handlers/api.go::HandleLogin:18` — file path, function, line
- `MEU-{uuid12}` — system-assigned MEU identifier

**Unacceptable**: Generic or unverifiable references like `"auth code"` or `"user input"`.

### Rule 3: MEU Snippets Must Be Verbatim

The `snippet` field in a MEU must contain the exact text from the source file. Do not paraphrase or summarize.

### Rule 4: Confidence < 0.4 Requires Uncertainty Note

When evidence is insufficient to draw a strong conclusion (confidence < 0.4), you MUST include a plain-text `uncertainty_note` field explaining:
- What information is missing
- What assumption had to be made
- What additional analysis would strengthen the conclusion

### Rule 5: Evidence Must Precede Conclusions

Do not state a conclusion and then search for evidence to support it. Instead, read the code first, extract MEUs, and then form conclusions based on those MEUs.

## Validation at Step Boundaries

The Workflow Orchestrator enforces evidence linking at each step boundary:
1. After a Skill completes, the Orchestrator scans its output for `evidence_refs`.
2. If any `evidence_refs` array is empty or contains unresolvable references, the step FAILS.
3. Valid MEUs are stored in the EvidenceStore for cross-referencing in subsequent steps.
