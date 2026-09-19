# Computer-Use Automation System — Design Report

Goal in natural language → Claude drives a live legacy UI → a typed, versioned **capability** →
deterministic replay with typed I/O and an explicit outcome taxonomy → human takeover of the
*same* live session → evidence for every run. Target: a local "Harbor Teller Console" (framesets,
table layouts, labels in neighbouring cells, no ids, faults on demand) plus a second tenant
variant. Evidence: [`evidence/`](evidence/) (open `evidence/index.html` for the overview).

## 1. Architecture

```
task spec (goal + typed contract) → Discovery: observe → decide (Claude) → policy → act → compile
                                                          │                                  │
       SessionControl (who may act) ── Surface (Playwright; seam for UIA)        capability (JSON, git)
                │                                          │                                  │
       operator console (same process)       Replay: validate → resolve ▸ policy ▸ act ▸ checkpoint
                                                         + known-state handling → ReplayResult
```

One Python 3.12 process runs FastAPI (catalog API, operator console), Playwright, the agent loop
and the replay engine on one asyncio loop. Handoff must operate the *same* browser automation
holds; in one process that is a function call, not distributed locking. The cost — one process
is one scaling unit — is fine here; the seam for scale-out is the `LiveSession` boundary.

- **Perception:** an accessibility-style snapshot of every frame (roles, names incl. adjacent-cell
  labels, table rows, element refs) plus a redacted screenshot. The model acts on refs and never
  writes selectors. Coordinate-only computer use was rejected as the primary mechanism: a click at
  (x, y) records nothing replayable, while the snapshot maps onto desktop accessibility trees.
- **Stateless decisions:** each step is one model request (cached prefix + task, compact
  history, current observation). Bounded context; every decision is reproducible from its log.
- **Contract first:** a task spec declares typed inputs/outputs/secrets; the model discovers
  *how*. It types `{{member_id}}` placeholders and uses `fill_secret`, so it never sees input
  values or credentials; raw values it types anyway are canonicalized into parameters.
- **Model access:** one `Decider` protocol, two transports — the Messages API (tool use,
  screenshot + snapshot) and the Claude Code CLI on a subscription (`claude -p`, isolated: no
  tools/settings/memory, JSON-schema reply, snapshot only). The evidence used the latter with
  `claude-opus-5`.

## 2. Artifact schema

`capability/1` ([schema](src/assessments/capability/schema.py),
[example](evidence/capability.json)) is a *contract* plus a *flow*:

| Part | Contents | Why |
| --- | --- | --- |
| Identity | `id`, `version`, `title`, `description`, `app {product, product_version, surface, profile}` | Invoked by name; `product`, not tenant, is the reuse key. |
| Contract | `inputs` (type, pattern/enum, **sensitivity**), `outputs`, `secrets` (env/vault refs), `outcomes` | Validated before touching the UI; sensitivity drives redaction; business outcomes are part of the API. |
| Flow | `steps[]`: `intent`, `action`, `target`, `value` (`param`/`secret`/`literal` + templates), `risk`, `expect[]`, `timeout_ms` | No transcript, no raw values. |
| Target | `scope` (frame/window path) + `strategies[]` in robustness order: `role`+name → `label` → `field_name` → `table_cell` (row key × column) → `text` → `css` | Each strategy is **verified at record time** to resolve uniquely to the element acted on. |
| Checkpoints | `screen_title`, `text_visible`, `target_present`, `dialog`, `http_status` | Surface-neutral. |
| Governance | `review {draft\|approved}`, `provenance`, derived `side_effects` | Unattended use requires approval. |

Form-field `name`s are the server's POST contract and survive re-theming; table values are
addressed by meaning ("Share Savings" × "Balance"), not position; CSS paths are a reported last
resort. Invalid references (unknown params/secrets, outputs never extracted) fail validation.

## 3. Determinism & error handling

Replay never calls a model. Per step: **wait (bounded) for a unique target → policy → act →
wait (bounded) for the checkpoint**, polling every 250 ms — condition-based, no fixed sleeps. A
handled state (dismissal, backoff, restart, a person's handoff) restarts the wait's budget.
While waiting, the engine scans **known states** (curated per vendor product in the app profile,
overridable per capability), each with a deliberate response:

| Class | Demo states | Response | Result |
| --- | --- | --- | --- |
| Business outcome | `member_not_found`, `invalid_member_number`, `member_fraud_alert` (dialog), `deposit_below_minimum` | stop, return the code | `business_outcome` |
| Recoverable | `maintenance_notice`, `host_unavailable` (503), `session_expired` | dismiss / reload + backoff / restart (side-effect-free only), capped | `recoveries[]` |
| Escalate | `supervisor_override_required` | hand off the live session, resume | `handoffs[]` |
| Failure | `application_error`, bad credentials, recovery exhausted, target/checkpoint timeout, unknown dialog, parse error | stop with evidence | `failed` |

Params are validated first (`rejected`, nothing touched). Failures carry the step, intent,
**expected** conditions, **observed** screen (redacted excerpt + frame titles) and screenshot
paths. **Drift** is reported, not hidden: steps that resolve only via a fallback strategy are
listed in `degraded_locators`. A real bug surfaced this way: after re-login the frameset reloads
and Playwright keeps detached frames in `child_frames`, so restarts matched a stale frame; fixed
test-first.

## 4. Heterogeneity & multi-tenant

**Surface seam.** The loop and engine speak only the `Surface` protocol and surface-neutral types.
Desktop maps directly: UIA `ControlType`+`Name` → `role`, `LabeledBy` → `label`, `AutomationId` →
`field_name`, grid cells → `table_cell`, window path → `scope`, window title → `screen_title`.
Surfaces without an accessibility tree (Citrix, canvas) add an OCR/image-anchor strategy kind;
the artifact format does not change.

**Reuse.** Capabilities and profiles are keyed by vendor product and carry no tenant data (base
URL and credentials bind at invocation). Differences are absorbed in layers: (1) multi-strategy
targets — tenant B relabels three controls and a column header, and tenant A's artifact still
succeeds, reporting 4 degraded steps; (2) tenant overlays patching specific targets by step id
(designed, not built); (3) re-discovery when drift exceeds an overlay. `degraded_locators` from
production replays feed drift management: degradation across many tenants means a product
version change (new capability version); on one tenant, configuration (add an overlay).

## 5. Escalation & handoff

**Detect.** Discovery: the model calls `request_help`, 3 consecutive failed actions, the same
action on the same screen 3×, or an approval requirement. Replay: an `escalate` state, an unsafe
restart, or an unauthorized irreversible step.

**Control model** ([control.py](src/assessments/session/control.py)): per session
`AUTOMATION → AWAITING_HUMAN → HUMAN → AUTOMATION` (or `ENDED`). Exactly one party may act:
automation asserts it holds control before every action; console actions assert the caller holds
the claim. The request carries goal/capability, step, intent, detected state, redacted
screenshot, recent actions and the **allowed resolutions** (`resume`, `skip_step`,
`approve`/`deny`, `abort`). While waiting the session is frozen intact; unanswered requests
time out to `abort`.

**Take control / hand back.** The operator console shows the live session (masked until
claimed); clicks, typing and keys go to the same Playwright page. Human actions are recorded
from the console (typed text as length only) and from DOM events in the page. On release the
engine re-verifies from the current screen before continuing. Mocked: operator identity (SSO in
production) and a polling console instead of co-browsing; the evidence uses a simulated operator
that acts only through the console API.

## 6. Safety

- **Allowlists:** hosts (policy check before every action *and* network interception), action
  types, keys (Enter is disallowed so submissions go through a named, classifiable control).
- **Risk:** clicking a control matching the product's irreversible patterns is `irreversible`.
  Discovery pauses for human **approval** and records the step as irreversible. Replay runs it
  only for an **approved** capability *and* an explicit `allow_irreversible`; otherwise it asks
  for approval. Flows with side effects are never auto-restarted. Blocking outright is safe but
  useless for write flows; approval keeps a person accountable for commits while reads run
  unattended.
- **Data:** credentials by reference only; input values never reach the model or artifacts.
  Redaction before anything is persisted or sent: label-based (masked in the DOM, so screenshots
  too), pattern-based (SSN, phone, email, Luhn-valid cards, account numbers), and exact known
  values. Outputs are returned to the caller but masked at rest. Playwright traces are off.
- **Model output is untrusted:** tool calls are schema-validated, page text is data, and policy
  sits outside the model, so prompt injection can only propose actions the policy judges.

Limits: redaction is heuristic; control names are a proxy for risk; the model and discovery
snapshots see business values such as balances (never names, identifiers or credentials); operator
auth is mocked.

## 7. Cuts

Left out: desktop/pixel surfaces (seam above), tenant overlays and a drift report (the data
exists), real co-browsing, a vault (env/.env behind `resolve_secrets`), worker scale-out, an
OpenAI decider (the `Decider` seam), and an eval runner (cases in `evals/`).

Next: (1) overlays + drift report from `degraded_locators`; (2) bounded single-step LLM repair on
`target_not_found`, policy-checked and proposed as an overlay, never applied silently;
(3) multi-run stability scoring to gate `draft → approved`; (4) a UIA surface for one desktop app;
(5) operator SSO and a CDP-screencast console.
