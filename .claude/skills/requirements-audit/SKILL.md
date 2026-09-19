---
name: requirements-audit
description: Audit an assessment folder against its original brief line by line — verify each requirement is actually met (by reading code, running commands, and opening evidence), then update COMPLIANCE.md with precise links and honest status. Use before submitting or when asked "did we meet every requirement?".
---

# Requirements audit

The output is `<folder>/COMPLIANCE.md`: a table a reviewer can use to check the submission
against their own brief in minutes.

## Procedure

1. **Source of truth is the brief**, not memory or an earlier summary. Re-read it (PDF pages or
   the copy under `<folder>/docs/`), and extract every requirement verbatim, including
   deliverable paths, headings, and "ideally"/"optional" items.
2. For each requirement, find proof **in this order**: the code that implements it (file:line),
   a test that exercises it, and evidence from a real run. Open each link you cite.
3. Status is one of: `met`, `met (stretch)`, `partial — why`, `designed, not built — where`,
   `not met — why`. Never mark `met` on the strength of documentation alone.
4. Check deliverable mechanics: exact file paths and headings, README demo commands actually
   work from a fresh clone, write-up length limits, evidence includes the specific runs asked for
   (e.g. "one replay that hits an error").
5. Check claims: every number or claim in README/REPORT (step counts, test counts, sizes,
   "no secrets in evidence") must match the current code and evidence. Fix drift.
6. Add or refresh "Beyond the brief": each addition, why it matters to this evaluator (link the
   source), and where to see it.

## For interface-ai specifically

- Brief sections to cover: 2 (six abilities), 3.1–3.7, 5 (vertical slice), 6 (deliverables and
  REPORT headings), 7 (criteria), 8 (stretch goals), 9 (ground rules).
- Useful commands (from `interface-ai/`): `make verify`, `make test-e2e`,
  `uv run python scripts/evidence_dashboard.py`, leak scan:
  `grep -rE "harbor-demo|Jordan Avery|512-44-9012" evidence --include=*.json* --include=*.txt`.
