# Computer-Use Automation System

An LLM (Claude) discovers how to accomplish a goal in a legacy back-office UI. The run is compiled
into a typed, versioned **capability**, which AI agents then invoke through **deterministic
replay**: no model in the loop, typed inputs and outputs, and an explicit outcome taxonomy. When
automation can't safely proceed, a person takes over the **same live session** from an operator
console and hands control back.

| Start here | |
| --- | --- |
| **Video walkthrough** (3½ min) | [`evidence/demo.mp4`](evidence/demo.mp4) — discovery, replay outcomes, recovery, a person taking over the live session, tenant drift, model repair, certification |
| Design write-up | [`REPORT.md`](REPORT.md) — the seven headings the brief asks for |
| Requirement-by-requirement check | [`COMPLIANCE.md`](COMPLIANCE.md) — every line of the brief → code, tests, evidence |
| Evidence (real `claude-opus-5` runs) | [`evidence/`](evidence/) — open [`evidence/index.html`](evidence/index.html) for the colour-coded overview |
| Example artifact | [`evidence/capability.json`](evidence/capability.json) |

![Architecture](docs/images/architecture.svg)

## Beyond the brief

Every core requirement is met (see [COMPLIANCE.md](COMPLIANCE.md)), plus **all six optional
stretch goals**, chosen and built as production features:

| | What it does | Try it |
| --- | --- | --- |
| **Confidence scoring** | Golden cases replayed N times → pass rate × primary-locator rate. Approval refused below 95% or 5 runs/case. | `assessments certify REF` |
| **Tenant overlays** | Locator drift on one institution becomes a proposed, reviewed overlay; the shared capability stays untouched. | `assessments overlay propose REF --tenant bayside` |
| **Bounded model repair** | Opt-in: if an element is gone, one policy-checked model call re-finds it; flagged and proposed for review, never applied silently. | `assessments replay REF --tenant coastal --assist claude-code` |
| **MCP server** | Any MCP-capable agent can list capabilities (with JSON-Schema inputs) and invoke them by name. | `assessments mcp` |
| **Code generation** | A capability becomes a standalone Playwright page object + pytest test (no dependency on this project). | `assessments codegen REF` ([sample](docs/generated/test_harbor_member_savings_balance.py)) |
| **Operator console + catalog** | Live session view, control track, activity feed, step progress; catalog renders each artifact for review. | `assessments serve` → `/catalog`, `/operator` |

## At a glance

**Every run, coloured by what happened** — green succeeded, amber recovered, blue business
outcome, teal model-assisted, violet a person was involved, red failed:

![Evidence overview](docs/images/evidence-dashboard.png)

**The capability catalog** — the artifact as a reviewer reads it: typed contract, confidence,
tenants, what callers can get back, and every step's locators ranked by robustness:

![Capability catalog](docs/images/catalog.png)

**The operator console** — a run stopped for a supervisor override; the operator holds control of
the same live browser session and hands it back:

![Operator console](docs/images/operator-console.png)

**The target** — a deliberately legacy teller console (framesets, table layouts, no ids; all data
synthetic). Credits are green with `+`, debits red in parentheses, so meaning never depends on
colour alone:

![Teller console member page](docs/images/teller-member.png)

## Setup

Requirements: [uv](https://docs.astral.sh/uv/) (installs Python 3.12 itself). `make` is optional:
every target is a one-line `uv run …` command (see [`Makefile`](Makefile)).

```sh
uv sync
uv run playwright install chromium
cp .env.example .env
```

Discovery (and the optional repair) needs Claude, via **either**:
- an API key: `ANTHROPIC_API_KEY` in `.env` (`--decider claude`), or
- a Claude subscription through the [Claude Code](https://code.claude.com) CLI: log in once with
  `claude`, then pass `--decider claude-code`. Each step is an isolated `claude -p` call (no tools,
  no settings or memory, JSON-schema-constrained reply).

Configuration is validated at startup ([`.env.example`](.env.example) lists everything):

| Variable | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` / `LLM_MODEL` | Discovery via the API (default model `claude-opus-5`). Replay never calls a model. |
| `TARGET_BASE_URL` | Default tenant app instance (the local demo bank, `http://localhost:8001`). |
| `ALLOWED_HOSTS`, `ALLOWED_PATHS`, `ALLOWED_ACTIONS` | Policy allowlists: hosts (also enforced on the network), URL paths, action types. |
| `HARBOR_OPERATOR_ID` / `HARBOR_OPERATOR_PASSWORD` | Tenant credentials, referenced by capabilities by env name only (synthetic values for the demo). |
| `HANDOFF_TIMEOUT_S` | How long a run waits for a person before aborting. |

## Demo path

Terminal 1, the target app (sign on with `teller1` / `harbor-demo`):

```sh
uv run assessments demo-bank                       # http://localhost:8001
```

Terminal 2, **discover** a capability with Claude, then **replay** it:

```sh
uv run assessments discover catalog/tasks/member_savings_balance.json --example member_id=12345 \
  --decider claude-code                            # or --decider claude with an API key
#   → catalog/capabilities/harbor.member.savings_balance/v1.json  (+ evidence in data/runs/<run>)

uv run assessments replay harbor.member.savings_balance --param member_id=48213
#   → {"status": "succeeded", "outputs": {"savings_balance": "15032.90", "member_name": "Priya Raman"}, …}

uv run assessments replay harbor.member.savings_balance --param member_id=99999
#   → {"status": "business_outcome", "outcome": {"code": "member_not_found", …}}
```

Runtime trouble and a person taking over:

```sh
uv run assessments faults notice=1          # maintenance interstitial → recovered (dismissed)
uv run assessments faults unavailable=2     # transient 503 → recovered (retried)
uv run assessments faults server_error=1    # application error page → failed, with evidence
uv run assessments replay harbor.member.savings_balance --param member_id=12345

# Restricted record → escalation. Open http://127.0.0.1:8000/operator, "Take control", click into
# the Supervisor ID / PIN fields in the live view (sup01 / 4321), click Override, then
# "Hand back and resume". The run continues on the same session.
uv run assessments replay harbor.member.savings_balance --param member_id=20417
#   (--simulate-operator lets a scripted operator do the same through the console API)
```

Other tenants of the same product (drift → overlay; redesign → repair):

```sh
uv run assessments demo-bank --port 8002 --variant b      # Bayside: relabelled screens
uv run assessments demo-bank --port 8003 --variant c      # Coastal: redesigned menu
uv run assessments replay harbor.member.savings_balance --param member_id=12345 --tenant bayside
#   → succeeded, with degraded_locators (drift)
uv run assessments overlay propose harbor.member.savings_balance --tenant bayside --param member_id=12345
uv run assessments overlay approve harbor.member.savings_balance --tenant bayside --reviewer you
uv run assessments replay harbor.member.savings_balance --param member_id=12345 --tenant bayside
#   → succeeded, overlay "bayside (approved)", no drift
uv run assessments replay harbor.member.savings_balance --param member_id=12345 --tenant coastal \
  --assist claude-code                                    # one bounded repair, flagged for review
```

Confidence, approval, and agents:

```sh
uv run assessments certify harbor.member.savings_balance --runs 5   # confidence score + report
uv run assessments capabilities approve harbor.member.savings_balance --reviewer you
uv run assessments serve                                            # /catalog, /operator, /docs
uv run assessments mcp                                              # MCP server over stdio
uv run assessments codegen harbor.member.savings_balance --param member_id=48213
```

Irreversible actions (opening a sub-account; `Confirm` commits a transfer):

```sh
uv run assessments discover catalog/tasks/open_sub_account.json --example member_id=12345 \
  --example product="Vacation Club" --example initial_deposit=25.00 --simulate-operator
uv run assessments capabilities approve harbor.member.open_sub_account --reviewer you --force \
  --notes "reviewed manually"
uv run assessments replay harbor.member.open_sub_account --param member_id=12345 \
  --param product="Vacation Club" --param initial_deposit=25.00 --allow-irreversible
```

Everything in [`evidence/`](evidence/) and the video are reproducible:

```sh
uv run python scripts/generate_evidence.py --decider claude-code
uv run --with imageio-ffmpeg python scripts/record_demo.py --decider claude-code
```

### Without live services

- No model access: `--decider offline` runs discovery with a scripted stand-in through the exact
  same loop, policy, recording and compiler. It is a test double, not discovery.
- Replay, certification, overlays, codegen and the MCP server never need a model. The demo bank is
  local.

## Development

```sh
make verify       # ruff format/lint → mypy --strict → unit + integration tests
make test-e2e     # real Chromium + three demo tenants: discovery, replay matrix, handoff, overlays, repair, MCP
make audit        # pip-audit
```

| Layer | Location |
| --- | --- |
| Unit (schema, values, redaction, policy, control, certification, overlays) | `tests/unit/` |
| Integration (API in-process) | `tests/integration/` |
| E2E (browser + demo bank, no model calls) | `tests/e2e/` |
| Model-behavior evals (separate from tests) | `evals/` |

## Docker

```sh
docker compose up --build          # demo bank :8001 + service (catalog, operator console) :8000
docker compose run --rm app replay harbor.member.savings_balance --param member_id=12345
```

Multi-stage image (`python:3.12-slim-bookworm` + Chromium headless shell), non-root, read-only root
filesystem, all capabilities dropped. ~1.3 GB, dominated by Chromium and its libraries.

## Layout

```
src/assessments/
  capability/   schema (capability/1), values, profiles, store, overlays, certification, codegen
  surfaces/     Surface protocol; web/ = Playwright implementation + injected cu.js
  agent/        action space, deciders (API, Claude Code CLI, scripted), discovery + compiler
  replay/       deterministic engine (+ opt-in bounded repair) and result contract
  session/      control-transfer state machine, live sessions, activity feed
  api/          FastAPI: capability catalog (+ page), operator console
  mcp_server.py safety.py redaction.py runs.py secrets.py cli.py
src/demo_bank/  the legacy-style target app (fault injection, tenant variants b and c)
catalog/        profiles/, tasks/, capabilities/, tenants/, overlays/, certification/
evidence/       recorded runs, dashboard, video;   REPORT.md, COMPLIANCE.md
```
