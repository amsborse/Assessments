# Task: <short title>

> Copy this file (e.g. to a PR description or `agent/tasks/<slug>.md`) and fill it in.
> Delete sections that don't apply. Keep it short.

## Goal

What outcome is needed, in one or two sentences. Describe the result, not the implementation.

## Context

- Why this is needed / what triggered it (issue, bug report, user request).
- Relevant files, components, prior decisions (`agent/ARCHITECTURE.md`, `docs/decisions/`).
- For bugs: exact reproduction steps, expected vs. actual behavior, logs/evidence.

## Constraints

- Must not change: public API / artifact schema versions / safety policy, etc.
- Dependencies, performance, security, or compatibility limits.
- Out of scope: things explicitly not to do.

## Acceptance criteria

- [ ] Observable behavior 1 (testable).
- [ ] Observable behavior 2 (testable).
- [ ] Tests added/updated (bugs: failing test first, then fix).
- [ ] `make verify` passes; `make test-e2e` / `make evals` if relevant.
- [ ] `agent/ARCHITECTURE.md` / ADR updated **only if** architecture changed.

## Plan

1. …

## Result

What changed, how it was verified, and anything deferred.
