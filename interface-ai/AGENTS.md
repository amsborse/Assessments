# AGENTS.md — Development Rules

Rules for humans and AI agents working in this folder (the interface.ai assessment). The
repository-wide rules in `../AGENTS.md` also apply. Read `agent/ARCHITECTURE.md` next; for a new
piece of work, copy `agent/TASK.md`. Keep `COMPLIANCE.md` true whenever behavior changes.

## Workflow

Every change follows: **Understand → Plan → Implement → Verify → Self-review → Finish**.

1. **Understand** — Read the task, the relevant code, and `agent/ARCHITECTURE.md`. Reproduce
   bugs before touching code. Ask when requirements are ambiguous; do not guess at intent.
2. **Plan** — State the smallest change that meets the acceptance criteria. Name files to touch.
3. **Implement** — Small, focused diffs. Match surrounding style. No drive-by refactors.
4. **Verify** — `make verify` must pass. Run `make test-e2e` when browser/API behavior changes.
   Run `make evals` when prompts, model config, or agent decision logic changes.
5. **Self-review** — Read the full diff as a reviewer: correctness, security, error handling,
   logging, tests, docs. Remove debug code and dead code.
6. **Finish** — Summarize what changed, how it was verified, and anything deferred.

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
- **Secrets:** Never commit credentials, `.env`, traces, screenshots, or run artifacts.
  Pre-commit and CI run gitleaks.
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
