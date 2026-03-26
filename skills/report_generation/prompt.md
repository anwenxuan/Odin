# Report Generation Skill

Generate a standardized research report from all prior skill outputs.

## Task

1. Parse all skill outputs provided in `skill_outputs`.
2. Build the standard report sections:
   - Repository Overview
   - Core Modules
   - Entry Points
   - Execution Paths
   - Key Data Structures
   - Attack Surfaces
   - Vulnerability Hypotheses
   - Evidence Index (MEU listing)
   - Confidence & Limitations
   - Next Research Steps
3. For each finding, cross-reference evidence refs.
4. Calculate summary statistics.

## Output Requirements

- The `report_markdown` field must contain the full report in Markdown format.
- The `finding_index` must list every significant finding across all skills.
- The `metadata` must include repository info, workflow ID, and aggregate statistics.
