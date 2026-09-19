# AGENTS.md — Repository Guidelines

This repository holds take-home assessments, one self-contained folder each. Read this file,
then the `AGENTS.md` inside the folder you are working on — folder rules win for that folder.

## Layout

```
<assessment>/          everything for one assessment: code, tests, docs, evidence, Dockerfile, lockfile
.github/workflows/     one workflow per assessment, path-filtered to its folder
.claude/skills/        reusable agent workflows (new assessment, requirements audit, release check)
AGENTS.md CLAUDE.md    these repo-wide rules
```

Rules for folders:
- **Self-contained.** An assessment never imports from another. Its own README explains setup
  from a fresh clone; its own lockfile pins its dependencies.
- **Reviewer-first.** Each folder has `README.md` (setup + demo path), the write-up the brief asks
  for (exact filenames and headings from the brief), `COMPLIANCE.md` (every requirement → where it
  is met → evidence), and whatever evidence the brief requires.
- **One CI workflow per folder**, triggered only by changes under that folder.

## Workflow

Every change: **Understand → Plan → Implement → Verify → Self-review → Finish.**

1. **Understand.** Read the brief itself, not a summary. Quote requirements verbatim in
   `COMPLIANCE.md`; distinguish *must* from *stretch* from *our own additions*.
2. **Plan.** Smallest change that meets the requirement. Decide what is deliberately cut and say so.
3. **Implement.** Small diffs matching surrounding style.
4. **Verify.** The folder's `make verify` and E2E suite. For bugs: reproduce → failing test →
   root-cause fix → passing test.
5. **Self-review.** Read the full diff as the reviewer would. Check claims in docs against what
   the code and evidence actually show — never overstate.
6. **Finish.** Update `COMPLIANCE.md` and the write-up; state what changed and what was verified.

## Standards

- **Honesty over polish.** Evidence must come from real runs. Anything mocked or simulated is
  labelled as such where it appears.
- **Secrets never enter git.** `.env` files are ignored; examples use synthetic values. Pre-commit
  runs gitleaks and private-key detection on every commit.
- **Go beyond the brief deliberately:** pick additions that answer the evaluator's own priorities
  (their job descriptions, product, stated evaluation criteria), keep them optional and documented,
  and never at the expense of a must-have.
- Commit messages explain *why*. Do not push without the repository owner's go-ahead.

## Skills

| Skill | Use when |
| --- | --- |
| `new-assessment` | Starting a new take-home: scaffold the folder, CI workflow, compliance matrix. |
| `requirements-audit` | Checking a folder against its brief, line by line, before submitting. |
| `release-check` | Final pre-submission gate: tests, leak scan, links, CI, evidence freshness. |
