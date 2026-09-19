# Evals

Evals measure **model-driven behavior**: does LLM discovery produce a capability that is correct
on inputs it never saw, within a step budget, without policy violations or needless handoffs?
They call a real model and are non-deterministic, so they are kept **separate from `pytest`**
(`tests/` is deterministic and never calls a model — discovery there uses a scripted decider).

Status: case format defined and cases written; the runner is deferred (see REPORT.md § Cuts).
`scripts/generate_evidence.py` is the manual, single-run version of the same check.

Why this matters, from a real run: discovery once stopped and asked for a person because the
member's name was masked (`█████`) by our own redaction, and the model judged the value
unreadable. Extraction only records *where* a value is — the value itself is read at replay — so
the system prompt now says masking is never a reason to ask for help. That is exactly the kind
of regression `must_not: ["intervention_raised"]` is meant to catch.

## Layout

| Path | Contents | Committed |
| --- | --- | --- |
| `cases/` | One JSON file per case | yes |
| `fixtures/` | Static inputs cases reference (none needed yet: the demo bank is seeded) | yes |
| `results/` | Run outputs: scores, run evidence, cost | **no** (gitignored) |

## Case format

```json
{
  "id": "discover-member-savings-balance",
  "task": "catalog/tasks/member_savings_balance.json",
  "examples": {"member_id": "12345"},
  "expect": {
    "discovery_status": "succeeded",
    "max_steps": 15,
    "must_not": ["policy_denied", "intervention_raised"],
    "replays": [
      {"params": {"member_id": "48213"}, "status": "succeeded", "outputs": {"savings_balance": "15032.90"}},
      {"params": {"member_id": "99999"}, "status": "business_outcome", "outcome": "member_not_found"}
    ]
  }
}
```

Grading is deterministic: discovery status and step count from `events.jsonl`, forbidden event
types, then **replay of the produced capability** on held-out inputs — the artifact is judged by
what it does, not by how the transcript reads. No model-graded criteria are needed for these
cases.

## Running (planned)

`make evals` runs each case N times (default 5) against a fresh demo bank and writes
`results/<timestamp>/summary.json`: pass rate per case, median steps, tokens and cost per run.
Run when prompts, tools, the snapshot format, or the model change; compare to the last baseline.
