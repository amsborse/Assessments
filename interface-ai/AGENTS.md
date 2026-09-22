# AGENTS.md — Development Rules

Rules for humans and AI agents working in this folder — the interface.ai take-home. This file is
the authority for everything under `interface-ai/`; there is no repository-wide rules file. Read
`agent/ARCHITECTURE.md` next; for a new piece of work, copy `agent/TASK.md`. Keep `COMPLIANCE.md`
true whenever behavior changes.

## The folder

- **Self-contained.** Never import from another assessment folder. `README.md` explains setup from
  a fresh clone; `uv.lock` pins the dependencies; `.github/workflows/interface-ai.yml` is this
  folder's only CI workflow and is path-filtered to it.
- **Reviewer-first.** The folder carries `README.md` (setup + demo path), `REPORT.md` (the exact
  filename and headings the brief asks for), `COMPLIANCE.md` (every requirement → where it is met →
  evidence), and the evidence the brief requires under `evidence/`.

## Workflow

Every change follows: **Understand → Plan → Implement → Verify → Self-review → Finish**.

1. **Understand** — Read the brief itself, not a summary, plus the relevant code and
   `agent/ARCHITECTURE.md`. Quote requirements verbatim in `COMPLIANCE.md` and distinguish *must*
   from *stretch* from *our own additions*. Reproduce bugs before touching code. Ask when
   requirements are ambiguous; do not guess at intent.
2. **Plan** — State the smallest change that meets the acceptance criteria. Name files to touch,
   and say what is deliberately cut.
3. **Implement** — Small, focused diffs. Match surrounding style. No drive-by refactors.
4. **Verify** — `make verify` must pass. Run `make test-e2e` when browser/API behavior changes.
   Run `make evals` when prompts, model config, or agent decision logic changes.
5. **Self-review** — Read the full diff as a reviewer: correctness, security, error handling,
   logging, tests, docs. Remove debug code and dead code.
6. **Finish** — Update `COMPLIANCE.md` and the write-up. Summarize what changed, how it was
   verified, and anything deferred.

## Rules

- **Stack:** Python 3.12, FastAPI, Pydantic, Playwright. Manage dependencies with `uv` only
  (`uv add`, `uv add --dev`); commit `uv.lock`.
- **Types:** All code passes `mypy --strict`. Validate external data (env, HTTP, files, model
  output) with Pydantic at the boundary.
- **Config:** Read settings only through `assessments.config.get_settings()`. New settings go in
  `Settings` *and* `.env.example`. Tenant credentials are resolved only via
  `assessments.secrets` (by env var name). Never read `os.environ` elsewhere.
- **Logging:** Use `logging.getLogger(__name__)`; pass structured data via `extra={...}`.
  No `print`. Never log secrets, API keys, credentials, or full page content containing PII.
- **Errors:** Raise `AppError` subclasses for expected failures. Let unexpected exceptions reach
  the global handler. Never swallow exceptions silently.
- **Honesty over polish:** Evidence comes from real runs. Anything mocked or simulated is labelled
  as such where it appears. Never let a doc claim more than the code and evidence show.
- **Beyond the brief:** Go further deliberately — pick additions that answer the evaluator's own
  priorities (their job descriptions, product, stated evaluation criteria), keep them optional and
  documented, and never at the expense of a must-have.
- **Secrets:** Never commit credentials, `.env`, traces, screenshots, or run artifacts. Examples use
  synthetic values. Pre-commit and CI run gitleaks and private-key detection.
- **Commits:** The message explains *why*. Do not push without the repository owner's go-ahead.
- **Safety:** Every automation action must pass the policy (`safety.py`) and the control guard
  (`SessionControl.assert_automation`). Never widen allowlists without an explicit requirement.
- **Artifacts:** `capability/1` is a public contract. Changing its shape needs a schema version
  bump and an ADR. Artifacts must never contain raw input values, secrets, or transcripts.
- **Surfaces:** technology-specific code stays behind the `Surface` protocol; the agent loop,
  replay engine and schema stay surface-neutral.
- **Architecture docs:** Update `agent/ARCHITECTURE.md` only when the architecture changes (new
  component, boundary, data flow, or dependency). Record significant decisions as an ADR in
  `docs/decisions/`.

## Testing

- `tests/unit/` — pure logic, no I/O, no network. Fast.
- `tests/integration/` — components together in-process (e.g. FastAPI app via `TestClient`).
- `tests/e2e/` — real Chromium + in-process demo bank (discovery with a scripted decider, the
  replay matrix, handoff, approval). Marked `e2e`, run separately.
- `evals/` — model-driven behavior. Non-deterministic; never mixed into `pytest` runs.
- **Regression rule:** reproduce the bug → write a failing test → fix the root cause →
  confirm the test passes. No fix without a test that would have caught it.
- No placeholder tests. A test must assert behavior that could actually break.
- Tests never call real LLM APIs.

## Skills

| Skill | Use when |
| --- | --- |
| `requirements-audit` | Checking this folder against the brief, line by line, before submitting. |
| `release-check` | Final pre-submission gate: tests, leak scan, links, CI, evidence freshness. |

## Commands

| Command | Purpose |
| --- | --- |
| `make install` | Install deps + pre-commit hooks + Playwright Chromium |
| `make verify` | Format check, lint, typecheck, unit + integration tests |
| `make test-e2e` | Browser E2E tests |
| `make fmt` | Auto-format and auto-fix lint |
| `make audit` | Dependency vulnerability scan |
| `make up` | Run the stack in Docker |
| `make demo-bank` / `make discover` / `make replay` | Run the demo target / a discovery / a replay |
| `uv run python scripts/generate_evidence.py --decider claude-code` | Regenerate `/evidence` (real model runs) |
| `uv run --with imageio-ffmpeg python scripts/record_demo.py --decider claude-code` | Re-record `evidence/demo.mp4` |
