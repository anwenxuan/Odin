# Output Constraints

All Skill outputs MUST strictly satisfy the following constraints.

## Schema Compliance

1. Output MUST be a valid JSON object conforming to the declared `output_schema`.
2. All required fields must be present and non-null (unless the schema explicitly allows null).
3. All field values must match their declared types.
4. Array fields must not be empty unless the schema explicitly sets `minItems: 0`.
5. String fields must not be empty strings unless the schema explicitly allows empty strings.
6. Numeric fields (`confidence`, `line_start`, etc.) must be within their declared ranges.

## Evidence References

1. Every conclusion, finding, hypothesis, or claim object MUST include an `evidence_refs` array.
2. `evidence_refs` MUST contain at least one evidence reference string.
3. Evidence reference format: `file_path::symbol:line` or `MEU-{id}`.
4. Each reference in `evidence_refs` must point to a real, readable location in the source code.
5. Do not include the same evidence_ref twice in the same `evidence_refs` array.

## Confidence Scores

1. All `confidence` values must be between `0.0` and `1.0` (inclusive).
2. If confidence is below `0.4`, you MUST provide an `uncertainty_note` explaining why.
3. High confidence (`>= 0.8`) requires multiple independent evidence sources.
4. Confidence must reflect the strength of available evidence, not your certainty about the assessment.

## Prohibition

1. Do NOT output explanations, headers, or prose outside the JSON structure.
2. Do NOT omit required fields to "simplify" the output.
3. Do NOT invent code snippets, file paths, or line numbers.
4. Do NOT mark conclusions as high confidence if they lack strong evidence.
