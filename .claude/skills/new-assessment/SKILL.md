---
name: new-assessment
description: Scaffold a new take-home assessment folder in this repo from a brief (PDF/markdown) — folder layout, path-filtered CI workflow, verbatim requirements matrix, and a plan that separates must-haves, stretch goals, and differentiators. Use when the user starts a new assessment.
---

# New assessment

Goal: a folder that a reviewer can clone, run, and audit against their own brief — and a plan
that is clearly better than the median submission.

## 1. Read the brief completely

- Read the whole brief yourself (PDF pages, attachments). Do not skim.
- Extract **every** requirement verbatim into a table: id, quote, type (`must` / `stretch` /
  `deliverable` / `constraint`), notes. Deliverable paths and headings are requirements too
  ("use these exact paths").
- Note the evaluation criteria and their order — they decide where depth goes.

## 2. Research the evaluator (differentiation)

- Company product, the team the role sits in, and its job descriptions: what vocabulary do they
  use for what they value (e.g. "confidence scoring", "human-in-the-loop", "evals")?
- Search for public submissions of the same brief. List what the median one does; plan at least
  2–3 additions that (a) answer the evaluator's stated priorities and (b) others lack.
- Record findings (with source links) in the folder's `COMPLIANCE.md` under "Beyond the brief".

## 3. Scaffold

```
<name>/
  README.md        setup from a fresh clone, demo path with exact commands
  <WRITEUP>.md     exactly the filename + headings the brief asks for
  COMPLIANCE.md    requirement → where met → evidence (links), plus beyond-the-brief
  AGENTS.md CLAUDE.md   the rules for this folder — authoritative, no repo-wide file above it
  src/ tests/{unit,integration,e2e}/ evidence/ docs/
  pyproject.toml (or package.json) + lockfile, Makefile with verify/test-e2e, Dockerfile if useful
```

- Copy `.github/workflows/interface-ai.yml` to `.github/workflows/<name>.yml`; change the
  `paths:` filters, `working-directory`, cache globs and Docker context to the new folder.
- Add the folder to `.github/dependabot.yml` and to the root `README.md` table.

## 4. Plan before code

Present: architecture sketch, the requirements table, depth choices mapped to evaluation
criteria, differentiators, and explicit cuts. Then implement incrementally, verifying after
each step (the workflow the new folder's own `AGENTS.md` sets out).
