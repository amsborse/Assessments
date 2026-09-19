"""Generate a standalone Playwright page object + pytest test from a capability.

The output depends only on `playwright` and `pytest-playwright` — not on this project — so a QA
team can drop it into their suite, and a reviewer can read the recorded flow as plain code.
For each step the most robust strategy expressible in Playwright is used; label and table-cell
strategies get small helpers (they are how legacy table layouts are addressed).
"""

import json
import keyword
import re

from assessments.capability.schema import (
    ActionKind,
    Capability,
    CssStrategy,
    FieldNameStrategy,
    HrefStrategy,
    LabelStrategy,
    Literal_,
    ParamRef,
    RoleStrategy,
    ScreenTitle,
    SecretValueRef,
    Step,
    Strategy,
    TableCellStrategy,
    Target,
    TextStrategy,
)

_HELPERS = '''
_MARK_JS = """([kind, a, b, token]) => {
  const norm = (s) => (s || "").replace(/\\\\s+/g, " ").trim();
  let hits = [];
  if (kind === "label") {
    for (const cell of document.querySelectorAll("td,th")) {
      if (norm(cell.innerText) !== a || !cell.nextElementSibling) continue;
      const next = cell.nextElementSibling;
      hits.push(next.querySelector("input,select,textarea") || next);
    }
  } else {
    for (const table of document.querySelectorAll("table")) {
      if (table.rows.length < 2) continue;
      const idx = [...table.rows[0].cells].map((c) => norm(c.innerText)).indexOf(b);
      if (idx < 0) continue;
      for (const row of [...table.rows].slice(1)) {
        if ([...row.cells].some((c, i) => i !== idx && norm(c.innerText) === a)) hits.push(row.cells[idx]);
      }
    }
  }
  hits.forEach((e) => e.setAttribute("data-gen", token));
  return hits.length;
}"""


def _marked(frame: Frame, kind: str, a: str, b: str = "") -> Locator:
    token = f"g{next(_COUNTER)}"
    frame.evaluate(_MARK_JS, [kind, a, b, token])
    return frame.locator(f'[data-gen="{token}"]')


def by_label(frame: Frame, label: str) -> Locator:
    """Control or value in the cell next to a label cell (legacy table layouts)."""
    return _marked(frame, "label", label)


def by_table_cell(frame: Frame, row_key: str, column: str) -> Locator:
    """Cell in the row containing `row_key`, under the header `column`."""
    return _marked(frame, "cell", row_key, column)


def wait_title(frame: Frame, title: str, timeout_s: float = 10) -> None:
    deadline = time.monotonic() + timeout_s
    while frame.title().strip() != title:
        if time.monotonic() > deadline:
            raise AssertionError(f"expected screen {title!r}, got {frame.title()!r}")
        time.sleep(0.2)
'''


def _ident(text: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if len(name) > 40:  # cut at a word boundary, not mid-word
        name = name[:40].rsplit("_", 1)[0]
    name = name or "step"
    return f"{name}_" if keyword.iskeyword(name) else name


def _tmpl(text: str) -> str:
    """A Python expression for a string that may contain {{param}} templates."""
    parts = re.split(r"\{\{\s*([a-z][a-z0-9_]*)\s*\}\}", text)
    if len(parts) == 1:
        return repr(text)
    pieces = [
        f"params[{part!r}]" if i % 2 else repr(part)
        for i, part in enumerate(parts)
        if part or i % 2
    ]
    return " + ".join(pieces)


def _strategy_expr(strategy: Strategy) -> str:
    match strategy:
        case RoleStrategy(role=role, name=name):
            return f"frame.get_by_role({role!r}, name={_tmpl(name)}, exact=True)"
        case FieldNameStrategy(name=name):
            return f"frame.locator('[name=\"' + {_tmpl(name)} + '\"]')"
        case HrefStrategy(href=href):
            return f"frame.locator('a[href=\"' + {_tmpl(href)} + '\"]')"
        case TextStrategy(text=text):
            return f"frame.get_by_text({_tmpl(text)}, exact=True)"
        case LabelStrategy(label=label):
            return f"by_label(frame, {_tmpl(label)})"
        case TableCellStrategy(row_key=row, column=column):
            return f"by_table_cell(frame, {_tmpl(row)}, {_tmpl(column)})"
        case CssStrategy(selector=selector):
            return f"frame.locator({selector!r})"
    raise ValueError(strategy)


def _frame_expr(target: Target) -> str:
    return "self.frame(" + ", ".join(repr(n) for n in target.scope.frames) + ")"


def _value_expr(step: Step) -> str:
    match step.value:
        case ParamRef(param=name):
            return f"params[{name!r}]"
        case SecretValueRef(secret=name):
            return f"SECRETS[{name!r}]"
        case Literal_(literal=text):
            return _tmpl(text)
    raise ValueError(step.id)


def _step_method(step: Step) -> str:
    target = step.target
    lines = [
        f"    def {step.id}_{_ident(step.intent)}(self, params: dict[str, str]) -> str | None:",
        f'        """{step.intent.replace(chr(34), chr(39))}"""',
    ]
    if target is not None:
        lines.append(f"        frame = {_frame_expr(target)}")
        lines.append(
            f"        target = {_strategy_expr(target.strategies[0])}  # {target.description}"
        )
        if len(target.strategies) > 1:
            kinds = ", ".join(s.kind for s in target.strategies[1:])
            lines.append(f"        # recorded fallbacks: {kinds}")
    match step.action:
        case ActionKind.CLICK:
            lines.append("        target.click()")
        case ActionKind.FILL:
            lines.append(f"        target.fill({_value_expr(step)})")
        case ActionKind.SELECT:
            lines.append(f"        target.select_option(label={_value_expr(step)})")
        case ActionKind.PRESS:
            lines.append(f"        self.page.keyboard.press({step.key!r})")
        case ActionKind.NAVIGATE:
            lines.append(f"        self.page.goto(BASE_URL + {_tmpl(step.url or '/')})")
        case ActionKind.EXTRACT:
            lines.append("        return target.inner_text().strip()")
    for cond in step.expect:
        if isinstance(cond, ScreenTitle):
            frames = ", ".join(repr(n) for n in cond.scope.frames)
            lines.append(f"        wait_title(self.frame({frames}), {_tmpl(cond.title)})")
    if step.action is not ActionKind.EXTRACT:
        lines.append("        return None")
    return "\n".join(lines)


def generate_test(capability: Capability, example_params: dict[str, str]) -> str:
    class_name = "".join(w.capitalize() for w in re.split(r"[._]", capability.id)) + "Flow"
    outputs = {s.id: s.output for s in capability.steps if s.output}
    secrets = {name: ref.env for name, ref in capability.secrets.items()}
    methods = "\n\n".join(_step_method(s) for s in capability.steps)
    calls = "\n".join(
        f"        outputs[{outputs[s.id]!r}] = self.{s.id}_{_ident(s.intent)}(params)"
        if s.id in outputs
        else f"        self.{s.id}_{_ident(s.intent)}(params)"
        for s in capability.steps
    )
    return f'''"""Generated from capability {capability.ref} by `assessments codegen`. Do not edit.

{capability.description}

Standalone: needs only playwright + pytest-playwright. Run against a tenant with
    TARGET_BASE_URL=http://localhost:8001 {" ".join(f"{env}=..." for env in secrets.values())} pytest {{this file}}
Irreversible steps: {[s.id for s in capability.steps if s.risk.value == "irreversible"] or "none"}.
"""

import itertools
import os
import time

from playwright.sync_api import Frame, Locator, Page

BASE_URL = os.environ.get("TARGET_BASE_URL", "http://localhost:8001")
SECRETS = {{name: os.environ[env] for name, env in {secrets!r}.items()}}
EXAMPLE_PARAMS = {json.dumps(example_params)}
_COUNTER = itertools.count()
{_HELPERS}

class {class_name}:
    """Page object for "{capability.title}" (recorded steps, most robust locator first)."""

    def __init__(self, page: Page) -> None:
        self.page = page

    def frame(self, *names: str, timeout_s: float = 10) -> Frame:
        """The named (nested) frame, waiting for it to attach after a navigation."""
        deadline = time.monotonic() + timeout_s
        while True:
            frame: Frame | None = self.page.main_frame
            for name in names:
                children = frame.child_frames if frame else []
                frame = next((f for f in children if f.name == name and not f.is_detached()), None)
            if frame is not None:
                return frame
            if time.monotonic() > deadline:
                raise AssertionError(f"frame {{'/'.join(names)}} did not appear")
            time.sleep(0.2)

{methods}

    def run(self, params: dict[str, str]) -> dict[str, str | None]:
        self.page.goto(BASE_URL + {capability.entry_url!r})
        outputs: dict[str, str | None] = {{}}
{calls}
        return outputs


def test_{_ident(capability.id)}(page: Page) -> None:
    outputs = {class_name}(page).run(EXAMPLE_PARAMS)

    assert set(outputs) == {set(capability.outputs)!r}
    assert all(outputs.values()), outputs
'''
