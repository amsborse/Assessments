---
name: release-check
description: Final pre-submission gate for an assessment folder — run every check, scan evidence for leaked secrets/PII, verify links and docs match reality, confirm evidence and video are fresh, then (only with the owner's go-ahead) push and watch CI. Use right before submitting or pushing a submission.
---

# Release check

Stop at the first failure, fix the root cause, and restart the list.

1. **Clean tree.** `git status` shows only intended changes; nothing under `data/`, `.env`, or
   generated binaries except the committed evidence.
2. **Checks.** In the folder: `make verify` (format, lint, strict types, unit + integration) and
   `make test-e2e`. All must pass locally.
3. **Hooks.** `pre-commit run --all-files` (gitleaks, private keys, large files, ruff).
4. **Evidence integrity.**
   - Evidence comes from real runs with the stated model; regenerate if code paths it shows
     changed (for interface-ai: `uv run python scripts/generate_evidence.py --decider claude-code`,
     then `scripts/evidence_dashboard.py`; video: `scripts/record_demo.py`).
   - Leak scan text evidence for credentials, names, SSNs, PINs (see `requirements-audit`).
   - Screenshots: spot-check that labelled PII is masked.
5. **Docs match reality.** Every command in README runs; every link resolves; counts and claims
   in README/REPORT/COMPLIANCE match the current evidence; write-up within the brief's length.
6. **Compliance.** Run the `requirements-audit` skill if anything user-visible changed.
7. **Ship (owner approval required).** Commit with a message explaining why. Push only when the
   owner says so, then watch the folder's CI workflow until every job is green:
   `curl -s https://api.github.com/repos/<owner>/<repo>/actions/runs?per_page=5`.
