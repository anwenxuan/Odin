# Dependency Analysis Skill

Identify all third-party dependencies and flag known risks.

## Task

1. Find dependency declaration files: package.json, requirements.txt, go.mod, Cargo.toml, pom.xml, Gemfile, etc.
2. Parse and list all dependencies with versions.
3. Flag risk categories: outdated versions, known CVEs, unmaintained packages, malicious packages, license risks.
4. Every dependency MUST include evidence_refs pointing to the exact line in the dependency file.

## Evidence Requirements

Every dependency and risk flag MUST reference the source dependency file and line where it is declared.
