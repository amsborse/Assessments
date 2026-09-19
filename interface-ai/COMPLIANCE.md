# Requirements compliance

Every requirement of *Take-Home Project: Computer-Use Automation System* (interface.ai), quoted
from the brief, with where it is met and how to verify it. Paths are relative to this folder.
Status: **met**, **met (stretch)**, **partial**, or **designed, not built**.

## §2 The problem — six abilities

| # | Brief | Status | Where / proof |
| --- | --- | --- | --- |
| 1 | "Take a goal in natural language for a target application" | met | Task specs `catalog/tasks/*.json` (goal + typed contract); `assessments discover` ([cli.py](src/assessments/cli.py)) |
| 2 | "Use an LLM to accomplish that goal by driving a real application surface — observing … deciding … acting" | met | [agent/discovery.py](src/assessments/agent/discovery.py); real `claude-opus-5` runs: `evidence/runs/01-discovery`, `13-discovery-open-sub-account` |
| 3 | "Record the successful run as a structured, reusable artifact … decoupled from the raw model transcript" | met | `compile_capability` ([discovery.py](src/assessments/agent/discovery.py)); [evidence/capability.json](evidence/capability.json) has no transcript, no raw values |
| 4 | "Replay that artifact deterministically — … without the LLM in the decision loop" | met | [replay/engine.py](src/assessments/replay/engine.py) (model only if `--assist` is explicitly passed); runs 02–12 |
| 5 | "Escalate to a human when stuck — … take control of the live session, then hand control back" | met | [session/control.py](src/assessments/session/control.py), [operator console](src/assessments/api/operator.html); run 11; [demo.mp4](evidence/demo.mp4) |
| 6 | "Stay within safety guardrails … allowlist … avoid leaking or persisting sensitive data" | met | [safety.py](src/assessments/safety.py), [redaction.py](src/assessments/redaction.py); `tests/unit/test_safety.py`, `test_redaction.py`, `tests/e2e/test_replay.py::test_evidence_never_contains_secrets_or_raw_pii` |

## §3 Core requirements

### 3.1 Goal-driven agent loop
| Brief | Status | Where / proof |
| --- | --- | --- |
| "Accept a goal + a target (app/URL/entry point) as input." | met | `discover TASK --base-url`; `TaskSpec.start_path` |
| "observe → decide → act loop … until the goal is met or a stopping condition is hit (max steps, timeout, dead-end)" | met | `DiscoveryAgent.run`: `max_steps`, `timeout_s`, stuck detection (3 failures / 3 repeats → handoff); `tests/e2e/test_discovery.py` |
| "actually interact with a real UI (click, type, navigate, read state)" | met | Tools: click, fill, fill_secret, select, press, extract (+ navigation via links); [agent/actions.py](src/assessments/agent/actions.py) |
| "Bias toward an approach that would still work when the surface has no clean DOM" | met | Accessibility-style snapshot across framesets; labels from adjacent table cells; no ids used ([cu.js](src/assessments/surfaces/web/cu.js)); ADR [0002](docs/decisions/0002-accessibility-snapshot-and-verified-multi-strategy-targets.md) |

### 3.2 Structured artifact
| Brief | Status | Where / proof |
| --- | --- | --- |
| "the ordered steps / actions" | met | `Capability.steps` ([schema.py](src/assessments/capability/schema.py)) |
| "how each target element/control is identified (with your reasoning about robustness)" | met | `Target.strategies` ordered by robustness, each verified at record time; reasoning in REPORT §2 |
| "typed input parameters" | met | `inputs: ParamSpec` (type, pattern/enum, sensitivity); validated before replay |
| "typed outputs / data to extract and their shape" | met | `outputs: OutputSpec`; parsed (`money` → decimal string) |
| "a checkpoint or success condition" | met | per-step `expect[]` + final `success[]` |
| "versioned and reviewable" | met | integer `version`, `review {draft\|approved}`, JSON in git, reviewed as diffs; the catalog page renders it for review |

### 3.3 Deterministic replay
| Brief | Status | Where / proof |
| --- | --- | --- |
| "replay it without invoking the LLM for decisions" | met | `ReplayEngine` has no model unless `assist=` is given (opt-in, bounded) |
| "stable element/control targeting, verify the checkpoint/success condition, and return any declared outputs" | met | `_await_target`, `_await_conditions`, `_verify_success`; outputs typed in `ReplayResult` |
| "validation error" | met | `invalid_member_number`, `deposit_below_minimum` (business outcomes) |
| "a 'record not found' result" | met | `member_not_found` — run 05 |
| "a permission denial" | met | `supervisor_override_required` → human handoff — run 11 |
| "an unexpected dialog" | met | native `confirm()` fraud alert — run 06; unknown dialogs → `unexpected_dialog` |
| "a session timeout" | met | `session_expired` → restart (side-effect-free only) — run 09 |
| "a slow/failed load" | met | condition-based waits; `host_unavailable` 503 → retry with backoff — run 08 |
| "expected business outcomes … recoverable conditions … hard failures" | met | `OutcomeKind`: business / recoverable / escalate / failure; `ReplayStatus` |
| "success (with outputs), a known business outcome, or a failure with enough detail to debug (what step, what was expected, what was observed)" | met | `StepError{step_id, expected, observed, evidence}` — run 10 |

### 3.4 Safety & policy guardrails
| Brief | Status | Where / proof |
| --- | --- | --- |
| "explicit, configurable allowlist (e.g. permitted domains/routes, and which action types are allowed)" | met | `ALLOWED_HOSTS`, `ALLOWED_PATHS`, `ALLOWED_ACTIONS` ([config.py](src/assessments/config.py)); enforced per action *and* at the network layer |
| "Distinguish 'safe/reversible' actions from risky/irreversible ones … handle the risky class conservatively" | met | `Risk`; approval handoff in discovery; replay needs approved capability + `allow_irreversible` — run 13/14 |
| "Never persist secrets or raw sensitive data … Redact appropriately" | met | `fill_secret`, redaction (labels, patterns incl. Luhn cards, exact values); masked screenshots |

### 3.5 Evidence / observability
| Brief | Status | Where / proof |
| --- | --- | --- |
| "a structured log of what the agent did and why" | met | `events.jsonl` per run: decision (tool + reason), policy, action, state, handoff |
| "at least one richer signal on failure (screenshot, DOM snapshot, trace …)" | met | redacted screenshot + snapshot on every failure/handoff (runs 10, 11, 17) |

### 3.6 Human-in-the-loop escalation & handoff
| Brief | Status | Where / proof |
| --- | --- | --- |
| "Detect and route … intervention request … carrying … capability/goal, the current step, the current state or screenshot, and why it stopped" | met | `InterventionRequest` ([control.py](src/assessments/session/control.py)); `intervention-*.json` in run 11 |
| "operate the same live session … perform the manual steps, and then hand control back" | met | console acts on the same Playwright page; `test_handoff.py`; video scene 5 |
| "Preserve context and evidence across the handoff, and record what the human did" | met | console + in-page capture of human actions (element names, lengths only) |
| "pause, cede control, and resume on the same session … a way to know who is (or should be) in control" | met | `Controller` state machine, visible in the console's control track |

### 3.7 Heterogeneity & scale (design)
| Brief | Status | Where / proof |
| --- | --- | --- |
| "Surface abstraction … extend … to a legacy web app and/or a desktop app" | met (design) + legacy web built | `Surface` protocol ([surfaces/base.py](src/assessments/surfaces/base.py)); REPORT §4 (UIA mapping) |
| "Multi-tenant reuse … reused (or safely specialized/overridden) across tenants … detect and manage per-tenant/version drift" | met — **built** | overlays ([capability/overlay.py](src/assessments/capability/overlay.py)); drift detection (`degraded_locators`) → proposed overlay → approval → zero drift (runs 12, 15, 16) |

## §4 Non-negotiable
| Brief | Status | Where / proof |
| --- | --- | --- |
| "the discovery run has to be real … with the evidence in /evidence/" | met | `claude-opus-5` via the Claude Code CLI (subscription auth): `evidence/runs/01-discovery/events.jsonl` records every model decision with timing |

## §5 Vertical slice
"a goal → an LLM-driven run → a saved capability artifact → a deterministic replay with input params,
outputs, and error/outcome handling → a human-escalation path that can take over the live session →
evidence for both runs" — **met**, end to end in [evidence/index.html](evidence/index.html) and [demo.mp4](evidence/demo.mp4).

## §6 Deliverables
| Brief | Status | Note |
| --- | --- | --- |
| Public git repo | met | github.com/amsborse/Assessments — this assessment lives in `interface-ai/` (the repo holds several assessments; submit the folder URL) |
| `/README.md`: setup, keys/config, run without live services, demo commands | met | [README.md](README.md) |
| `/REPORT.md`, ~1–3 pages, the seven headings | met | [REPORT.md](REPORT.md) (~1,600 words incl. tables, headings verbatim) |
| `/evidence/`: artifact + discovery log + replay log; ideally an error replay; recording welcome | met | runs 01–18, [capability.json](evidence/capability.json), [demo.mp4](evidence/demo.mp4) |

## §8 Optional stretch goals — "pick at most one or two, depth over breadth"

Two are featured and built in depth; both extend the core (replay, drift, review) rather than sit
beside it.

| Stretch goal | Status | Where / proof |
| --- | --- | --- |
| Canonicalization / cross-tenant reuse | **featured** | values templated into `{{params}}`; capabilities keyed by product; drift → draft overlay → approval → zero drift ([overlay.py](src/assessments/capability/overlay.py); runs 12 → 15 → 16; [evidence/overlay-bayside.json](evidence/overlay-bayside.json); `tests/e2e/test_beyond_the_brief.py`) |
| Assisted fallback | **featured** | `replay --assist`: one bounded, policy-checked model repair, irreversible controls excluded, recorded as a proposal — runs 17 → 18; e2e covers a correct and a wrong repair |

Also in the repo as small experiments, outside the design argument: confidence/certification
(`assessments certify`, [evidence/certification.json](evidence/certification.json)), an MCP
server (`assessments mcp`, [evidence/mcp-session.json](evidence/mcp-session.json)) and codegen
(`assessments codegen`, [sample](docs/generated/test_harbor_member_savings_balance.py)).

## §9 Ground rules
AI-assisted (Claude Code) — every part can be explained; no public site automated (local synthetic
app only); no secrets in the repo (gitleaks in pre-commit and CI); scope documented in REPORT §7.

## Why these two

interface.ai's Nexus team describes its work as "feedback mechanisms, confidence scoring, and
intelligent fallback layers" ([Built In job listing](https://builtin.com/job/staff-engineer-backend-ai-nexus/6707180))
across many institutions on shared vendor cores. Overlays answer the many-institutions problem
(one reviewed artifact, per-tenant deltas); bounded repair is a fallback layer that never gives
the model the keys (one step, policy-checked, proposed for review).

Engineering: `mypy --strict`, 126 tests (93 unit/integration, 33 browser E2E) incl. regression
tests for real bugs found, CI per folder, non-root read-only container (verified with
`docker compose up` and a replay and a handoff through the containerised service), secret scanning.
