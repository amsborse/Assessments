"""Render evidence/index.html from evidence/SUMMARY.json and the per-run event logs.

Colour encodes the outcome taxonomy, identically everywhere on the page:
green = succeeded, amber = recovered, blue = business outcome, violet = a person was involved,
red = failed, slate = rejected/aborted. Text labels always accompany colour.

    uv run python scripts/evidence_dashboard.py   # rebuild from existing evidence
"""

import html
import json
from pathlib import Path
from typing import Any

CATEGORIES: dict[str, tuple[str, str, str]] = {
    # key: (label, colour, meaning)
    "succeeded": ("Succeeded", "#1f7a4d", "Goal reached, outputs returned"),
    "recovered": ("Recovered", "#b7791f", "A runtime problem was handled and the run completed"),
    "business": ("Business outcome", "#2459c9", "A legitimate answer for the caller, not an error"),
    "human": ("Person involved", "#6d4bc4", "Control passed to an operator and back"),
    "failed": ("Failed", "#b3261e", "Stopped with a debuggable error"),
    "stopped": ("Rejected or stopped", "#64748b", "Nothing unsafe was attempted"),
}


def category(run: dict[str, Any]) -> str:
    status = run.get("status")
    if status == "failed":
        return "failed"
    if status in {"rejected", "aborted"}:
        return "stopped"
    if status == "business_outcome":
        return "business"
    if run.get("handoffs"):
        return "human"
    if run.get("recoveries"):
        return "recovered"
    return "succeeded"


def _decisions(run_dir: Path) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    events = run_dir / "events.jsonl"
    if not events.exists():
        return steps
    for line in events.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e["type"] == "decision" and e.get("action"):
            a = e["action"]
            target = a.get("secret") or a.get("value") or a.get("option") or a.get("output") or ""
            steps.append(
                {
                    "tool": a["tool"],
                    "detail": target,
                    "reason": a.get("reason", ""),
                    "ms": (e.get("raw") or {}).get("duration_ms"),
                }
            )
        elif e["type"] == "intervention_raised":
            steps.append(
                {"tool": "handoff", "detail": "", "reason": e.get("reason", ""), "ms": None}
            )
    return steps


def _chip(cat: str) -> str:
    label, colour, _ = CATEGORIES[cat]
    return f'<span class="chip" style="--c:{colour}">{label}</span>'


def _tags(run: dict[str, Any]) -> str:
    tags: list[tuple[str, str]] = []  # (plain label, emphasised value)
    if run.get("outcome"):
        tags.append(("outcome", run["outcome"]))
    if run.get("error"):
        tags.append(("error", run["error"]))
    for r in run.get("recoveries", []):
        state, response = r.split(":")
        tags.append((f"{state} →", response.replace("_", " ")))
    for h in run.get("handoffs", []):
        kind, res = h.split(":")
        tags.append((f"{kind} →", res))
    if run.get("degraded_locators"):
        steps = ", ".join(d.replace(":", " via ") for d in run["degraded_locators"])
        tags.append(("locator drift:", steps))
    return "".join(
        f'<span class="tag">{html.escape(label)} <b>{html.escape(value)}</b></span>'
        for label, value in tags
    )


def _links(evidence: Path, name: str) -> tuple[str, str]:
    run_dir = evidence / "runs" / name
    links = [f'<a href="runs/{name}/events.jsonl">events</a>']
    if (run_dir / "result.json").exists():
        links.append(f'<a href="runs/{name}/result.json">result</a>')
    shots = (
        sorted((run_dir / "screenshots").glob("*.jpg"))
        if (run_dir / "screenshots").exists()
        else []
    )
    interesting = [s for s in shots if "failure" in s.name or "handoff" in s.name] or shots[-1:]
    thumb = ""
    if interesting:
        rel = f"runs/{name}/screenshots/{interesting[0].name}"
        thumb = f'<a class="thumb" href="{rel}"><img src="{rel}" alt="Screenshot from {name}" loading="lazy"></a>'
    return " ".join(links), thumb


def build_dashboard(evidence: Path) -> Path:
    summary = json.loads((evidence / "SUMMARY.json").read_text(encoding="utf-8"))
    discoveries = summary.get("discoveries", [])
    replays = summary.get("replays", [])
    runs = (
        [*discoveries[:1], *replays[:-1], *discoveries[1:], *replays[-1:]]
        if replays
        else discoveries
    )
    counts: dict[str, int] = {}
    for r in runs:
        counts[category(r)] = counts.get(category(r), 0) + 1

    strip = "".join(
        f'<a class="seg" href="#{r["scenario"]}" style="--c:{CATEGORIES[category(r)][1]}" '
        f'title="{html.escape(r["title"])}: {CATEGORIES[category(r)][0]}">'
        f"<span>{r['scenario'][:2]}</span></a>"
        for r in runs
    )
    legend = "".join(
        f'<li style="--c:{colour}"><span class="sw"></span><b>{label}</b> {counts.get(key, 0)}'
        f'<span class="mean">{meaning}</span></li>'
        for key, (label, colour, meaning) in CATEGORIES.items()
    )

    disc_html = ""
    for d in discoveries:
        steps = _decisions(evidence / "runs" / d["scenario"])
        links, thumb = _links(evidence, d["scenario"])
        items = "".join(
            f'<li class="{"hand" if s["tool"] == "handoff" else ""}"><span class="tool">{html.escape(s["tool"])}</span>'
            f'<span class="det">{html.escape(str(s["detail"]))}</span>'
            f'<span class="why">{html.escape(s["reason"])}</span></li>'
            for s in steps
        )
        disc_html += f"""
<article class="disc" id="{d["scenario"]}" style="--c:{CATEGORIES[category(d)][1]}">
  <header><h3>{html.escape(d["title"])}</h3>{_chip(category(d))}</header>
  <p class="meta">{html.escape(d["model"])}, {d["steps"]} recorded steps. {html.escape(d["reason"])}</p>
  <ol class="steps">{items}</ol>
  <p class="links">{links}{f' <a href="../{d["artifact"]}">artifact</a>' if d.get("artifact") else ""}</p>
</article>"""

    rows = ""
    for r in replays:
        links, thumb = _links(evidence, r["scenario"])
        cat = category(r)
        rows += f"""
<li class="run" id="{r["scenario"]}" style="--c:{CATEGORIES[cat][1]}">
  <div class="body">
    <div class="top"><h3>{html.escape(r["title"])}</h3>{_chip(cat)}</div>
    <div class="tags">{_tags(r)}</div>
    <p class="links">{links} <span class="ms">{r["duration_ms"] / 1000:.1f}s</span></p>
  </div>{thumb}
</li>"""

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Evidence: Harbor Teller Console automation</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {{ --navy:#13233a; --paper:#eef2f5; --ink:#16202b; --muted:#5b6878; --line:#d7dee6;
  --font:"Schibsted Grotesk","Segoe UI",system-ui,sans-serif; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font:15px/1.5 var(--font); color:var(--ink); background:var(--paper); }}
a {{ color:#2459c9; }}
header.top {{ background:var(--navy); color:#fff; padding:36px 32px 28px; }}
header.top h1 {{ margin:0 0 6px; font-size:28px; line-height:1.2; }}
header.top p {{ margin:0 0 24px; max-width:70ch; color:#c6d2e2; }}
.strip {{ display:flex; gap:4px; max-width:1100px; }}
.seg {{ flex:1; height:54px; border-radius:6px; background:var(--c); display:flex; align-items:flex-end;
  padding:6px 8px; color:#fff; text-decoration:none; font-size:12px; font-weight:600;
  font-variant-numeric:tabular-nums; outline-offset:3px; }}
.seg:hover {{ filter:brightness(1.12); }}
.legend {{ list-style:none; display:flex; flex-wrap:wrap; gap:10px 22px; margin:16px 0 0; padding:0;
  max-width:1100px; font-size:14px; }}
.legend li {{ display:flex; align-items:center; gap:7px; color:#e6edf5; }}
.legend .sw {{ width:12px; height:12px; border-radius:3px; background:var(--c); }}
.legend .mean {{ display:none; }}
main {{ max-width:1100px; padding:28px 32px 56px; }}
h2 {{ font-size:19px; margin:28px 0 12px; }}
.chip {{ display:inline-block; padding:2px 10px; border-radius:999px; font-size:13px; font-weight:600;
  color:var(--c); background:color-mix(in srgb, var(--c) 12%, white); border:1px solid color-mix(in srgb, var(--c) 35%, white); white-space:nowrap; }}
.disc {{ background:#fff; border:1px solid var(--line); border-top:4px solid var(--c); border-radius:10px;
  padding:18px 20px; margin-bottom:16px; }}
.disc header {{ display:flex; gap:12px; align-items:center; justify-content:space-between; flex-wrap:wrap; }}
.disc h3, .run h3 {{ margin:0; font-size:16px; }}
.meta {{ color:var(--muted); margin:6px 0 12px; font-size:14px; max-width:80ch; }}
.steps {{ margin:0; padding:0; list-style:none; counter-reset:step; display:grid; gap:4px; font-size:14px; }}
.steps li {{ counter-increment:step; display:grid; grid-template-columns:26px 96px minmax(90px, 200px) 1fr; gap:12px; padding:3px 0; }}
.steps li::before {{ content:counter(step); color:var(--muted); font-variant-numeric:tabular-nums; text-align:right; }}
.steps .tool {{ font-weight:600; }}
.steps .det {{ color:#2459c9; overflow-wrap:anywhere; }}
.steps .why {{ color:var(--muted); }}
.steps li.hand .tool {{ color:#6d4bc4; }}
.runs {{ list-style:none; margin:0; padding:0; display:grid; gap:10px; }}
.run {{ display:flex; gap:16px; align-items:stretch; background:#fff; border:1px solid var(--line);
  border-left:6px solid var(--c); border-radius:10px; padding:14px 16px; }}
.run .body {{ flex:1; min-width:0; }}
.run .top {{ display:flex; gap:12px; align-items:center; justify-content:space-between; flex-wrap:wrap; }}
.tags {{ display:flex; flex-wrap:wrap; gap:6px; margin:8px 0 4px; }}
.tag {{ font-size:13px; padding:2px 8px; border-radius:6px; background:#f1f4f7; color:#334155; }}
.links {{ margin:6px 0 0; font-size:13px; display:flex; gap:12px; flex-wrap:wrap; }}
.ms {{ color:var(--muted); font-variant-numeric:tabular-nums; }}
.thumb img {{ width:180px; height:112px; object-fit:cover; object-position:top left; border-radius:6px;
  border:1px solid var(--line); display:block; }}
footer {{ color:var(--muted); font-size:13px; margin-top:32px; max-width:80ch; }}
@media (max-width:720px) {{
  header.top, main {{ padding-left:16px; padding-right:16px; }}
  .run {{ flex-direction:column; }} .thumb img {{ width:100%; height:auto; }}
  .steps li {{ grid-template-columns:26px 1fr; gap:0 10px; }} .steps .det, .steps .why {{ grid-column:2; }}
  .seg span {{ display:none; }}
}}
</style></head>
<body>
<header class="top">
  <h1>Harbor Teller Console: discovered by Claude, replayed without it</h1>
  <p>Each block is one run against the local legacy teller app. Colour shows what happened; hover or tap for the scenario.</p>
  <nav class="strip" aria-label="Runs by outcome">{strip}</nav>
  <ul class="legend">{legend}</ul>
</header>
<main>
  <h2>Discovery: the model works out how</h2>
  {disc_html}
  <h2>Replay: deterministic, no model in the loop</h2>
  <ol class="runs">{rows}</ol>
  <footer>Generated by <code>scripts/generate_evidence.py</code>. All files are redacted before they are written:
  credentials and input values appear as [redacted], labelled personal data is masked in screenshots,
  and outputs are masked at rest.</footer>
</main>
</body></html>
"""
    out = evidence / "index.html"
    out.write_text(page, encoding="utf-8")
    return out


if __name__ == "__main__":
    print(build_dashboard(Path(__file__).resolve().parents[1] / "evidence"))
