# Architecture

A computer-use system for legacy back-office apps: an LLM **discovers** how to reach a goal on a
live UI, the run is compiled into a typed, versioned **capability**, and callers invoke it via
**deterministic replay**. A human can take over the live session at any point. Design rationale:
[`REPORT.md`](../REPORT.md). Decisions: [`docs/decisions/`](../docs/decisions/).

## Components

```
catalog/tasks/*.json ──▶ DiscoveryAgent ──(compile)──▶ catalog/capabilities/<id>/vN.json
                          │ observe/decide/act                   │
                          ▼                                      ▼
            Decider (Claude | scripted)                   ReplayEngine ◀── API /api/capabilities/*/invoke
                          │                                      │           CLI `assessments replay`
                          ▼                                      ▼
   Policy (allowlists, risk) ──▶ Surface (PlaywrightWebSurface + cu.js in every frame)
                                     ▲            ▲
                     SessionControl ─┘            └─ operator console (/operator, /api/sessions/*)
   RunRecorder: data/runs/<run>/{events.jsonl, screenshots/, snapshots/, intervention-*.json}
```

| Component | Location | Responsibility |
| --- | --- | --- |
| Capability schema | `capability/schema.py` | `capability/1`: contract (typed inputs/outputs/secrets/outcomes) + flow (steps, multi-strategy targets, checkpoints, risk). |
| App profile | `capability/profile.py`, `catalog/profiles/` | Per vendor product: known runtime states, redaction labels, irreversible control patterns. |
| Store | `capability/store.py` | File-backed, versioned artifacts; approval. |
| Surface | `surfaces/base.py`, `surfaces/web/` | Perceive (a11y snapshot of all frames + redacted screenshot), act, resolve/verify targets, check conditions. The only technology-specific layer. |
| Discovery | `agent/discovery.py`, `agent/decider.py`, `agent/actions.py` | Observe → decide (one stateless model call) → policy → act; stuck detection; compile to a capability. |
| Replay | `replay/engine.py`, `replay/result.py` | No model. Resolve → policy → act → checkpoint, with known-state handling; structured result. |
| Control | `session/control.py`, `session/runtime.py` | Who may act on a live session; escalate/claim/release; session registry shared with the console. |
| Safety | `safety.py`, `redaction.py`, `secrets.py` | Allowlists, irreversible-action gating, redaction, credential resolution. |
| API | `api/` | Capability catalog + invoke; operator console. |
| Demo target | `src/demo_bank/` | Legacy-style teller console with fault injection and a tenant variant. |

## Key rules

- Every automation action passes the policy and the control guard before touching the surface.
- Model output is untrusted: tool calls are schema-validated; page text is data.
- Artifacts contain no raw input values, secrets, or transcripts — references and templates only.
- Nothing is persisted without redaction; replays capture screenshots only on failure/handoff.
- Discovery, replay, and the operator console share one asyncio loop per process, so a human
  operates the exact browser session automation was using.

## Runtime

Single Python process (FastAPI + Playwright/Chromium). Config from env/`.env`. Artifacts in
`CATALOG_DIR` (git), evidence in `DATA_DIR` (gitignored). Docker Compose runs the demo bank and
the service.
