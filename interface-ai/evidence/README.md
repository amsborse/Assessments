# Evidence

Produced by `uv run python scripts/generate_evidence.py --decider claude-code` against the local
demo bank (synthetic data). **Open [`index.html`](index.html)** for a colour-coded overview:
green succeeded, amber recovered, blue business outcome, teal model-assisted, violet a person
was involved, red failed, slate rejected/stopped.

Everything was redacted before it was written: credentials and input values appear as
`[redacted]`, labelled personal data is masked in screenshots, outputs are masked at rest.

| Run | What it shows |
| --- | --- |
| [`demo.mp4`](demo.mp4) | Narrated screen recording of the operator console through the whole flow: discovery by Claude, replays, recovery, a person taking over the live session, tenant drift. Produced by `scripts/record_demo.py`. |
| [`capability.json`](capability.json) | The artifact compiled from run 01 (`harbor.member.savings_balance@v1`). |
| [`SUMMARY.json`](SUMMARY.json) | One entry per run: status, outcome/error, recoveries, handoffs, drift. |
| `runs/01-discovery/` | **Real LLM discovery** (`claude-opus-5`): every observation, decision (tool, reason, timing), policy verdict and action in `events.jsonl`; what the model saw in `snapshots/`. |
| `runs/02`, `03` | Replay for the recorded member and for a member the model never saw. |
| `runs/04` | Malformed input → `rejected` before touching the UI. |
| `runs/05`, `06` | Business outcomes: `member_not_found`; `member_fraud_alert` (native dialog). |
| `runs/07`–`09` | Recovered: interstitial acknowledged; transient 503 retried; expired session → restart. |
| `runs/10` | Hard failure: application error page, with expected vs. observed and a screenshot. |
| `runs/11` | Escalation: a person takes over the live session, enters the supervisor override, hands back; `intervention-*.json` lists what they did. |
| `runs/12` | The same capability on a second tenant (relabelled UI): succeeds, reports locator drift. |
| `runs/13-discovery-open-sub-account/` | **Real LLM discovery of a write flow**: at `Confirm` the run pauses for approval, a person approves, and the step is recorded as irreversible. |
| `runs/14` | Replay of that draft write capability: approval is required again before committing. |
| `runs/15`, `16` | Drift on Bayside is re-derived into a draft overlay ([`overlay-bayside.json`](overlay-bayside.json)); once approved, the same capability replays there with zero drift. |
| `runs/17`, `18` | Coastal's redesigned menu breaks one step (`target_not_found`); with `--assist`, one real `claude-opus-5` call re-finds the link and the fix is proposed for review. |
| [`certification.json`](certification.json) | Golden cases replayed 5 times each: confidence score that gated the capability's approval. |
| [`mcp-session.json`](mcp-session.json) | An agent lists capabilities and invokes one by name over MCP. |

Each run directory holds `events.jsonl` (structured log), `result.json` (the result contract as
persisted) and, where relevant, `screenshots/`, `snapshots/`, `intervention-*.json`.
