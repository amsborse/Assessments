"""Generated from capability harbor.member.savings_balance@v1 by `assessments codegen`. Do not edit.

Sign on to the teller console, look up member {{member_id}}, and read the current balance of their Share Savings account and the member's name.

Standalone: needs only playwright + pytest-playwright. Run against a tenant with
    TARGET_BASE_URL=http://localhost:8001 HARBOR_OPERATOR_ID=... HARBOR_OPERATOR_PASSWORD=... pytest {this file}
Irreversible steps: none.
"""

import itertools
import os
import time

from playwright.sync_api import Frame, Locator, Page

BASE_URL = os.environ.get("TARGET_BASE_URL", "http://localhost:8001")
SECRETS = {name: os.environ[env] for name, env in {'operator_id': 'HARBOR_OPERATOR_ID', 'operator_password': 'HARBOR_OPERATOR_PASSWORD'}.items()}
EXAMPLE_PARAMS = {"member_id": "48213"}
_COUNTER = itertools.count()

_MARK_JS = """([kind, a, b, token]) => {
  const norm = (s) => (s || "").replace(/\\s+/g, " ").trim();
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


class HarborMemberSavingsBalanceFlow:
    """Page object for "Member savings balance lookup" (recorded steps, most robust locator first)."""

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
                raise AssertionError(f"frame {'/'.join(names)} did not appear")
            time.sleep(0.2)

    def s01_enter_operator_id_to_sign_on(self, params: dict[str, str]) -> str | None:
        """Enter operator ID to sign on."""
        frame = self.frame()
        target = by_label(frame, 'Operator ID:')  # textbox "Operator ID:"
        # recorded fallbacks: field_name, css
        target.fill(SECRETS['operator_id'])
        return None

    def s02_enter_the_operator_password_to_sign_on(self, params: dict[str, str]) -> str | None:
        """Enter the operator password to sign on."""
        frame = self.frame()
        target = by_label(frame, 'Password:')  # textbox "Password:"
        # recorded fallbacks: field_name, css
        target.fill(SECRETS['operator_password'])
        return None

    def s03_submit_the_sign_on_form_with_the(self, params: dict[str, str]) -> str | None:
        """Submit the sign-on form with the entered credentials."""
        frame = self.frame()
        target = frame.get_by_role('button', name='Sign On', exact=True)  # button "Sign On"
        # recorded fallbacks: text, css
        target.click()
        wait_title(self.frame(), 'Harbor CU Teller Console')
        wait_title(self.frame('banner'), 'banner')
        wait_title(self.frame('nav'), 'nav')
        wait_title(self.frame('main'), 'Teller Home')
        return None

    def s04_open_member_inquiry_to_look_up_the(self, params: dict[str, str]) -> str | None:
        """Open Member Inquiry to look up the member."""
        frame = self.frame('nav')
        target = frame.get_by_role('link', name='Member Inquiry', exact=True)  # link "Member Inquiry"
        # recorded fallbacks: href, text, css
        target.click()
        wait_title(self.frame('main'), 'Member Inquiry')
        return None

    def s05_enter_the_member_number_to_look_up_the(self, params: dict[str, str]) -> str | None:
        """Enter the member number to look up the member."""
        frame = self.frame('main')
        target = by_label(frame, 'Member #:')  # textbox "Member #:"
        # recorded fallbacks: field_name, css
        target.fill(params['member_id'])
        return None

    def s06_submit_the_member_search_for_the(self, params: dict[str, str]) -> str | None:
        """Submit the member search for the entered member number."""
        frame = self.frame('main')
        target = frame.get_by_role('button', name='Search', exact=True)  # button "Search"
        # recorded fallbacks: text, css
        target.click()
        wait_title(self.frame('main'), 'Member Detail')
        return None

    def s07_read_the_share_savings_balance_from_the(self, params: dict[str, str]) -> str | None:
        """Read the Share Savings balance from the member's account table."""
        frame = self.frame('main')
        target = by_table_cell(frame, 'Share Savings', 'Balance')  # 'Balance' of row 'Share Savings'
        # recorded fallbacks: css
        return target.inner_text().strip()

    def s08_read_the_member_s_name_from_the_member(self, params: dict[str, str]) -> str | None:
        """Read the member's name from the member record."""
        frame = self.frame('main')
        target = by_label(frame, 'Name:')  # value labelled 'Name:'
        # recorded fallbacks: css
        return target.inner_text().strip()

    def run(self, params: dict[str, str]) -> dict[str, str | None]:
        self.page.goto(BASE_URL + '/')
        outputs: dict[str, str | None] = {}
        self.s01_enter_operator_id_to_sign_on(params)
        self.s02_enter_the_operator_password_to_sign_on(params)
        self.s03_submit_the_sign_on_form_with_the(params)
        self.s04_open_member_inquiry_to_look_up_the(params)
        self.s05_enter_the_member_number_to_look_up_the(params)
        self.s06_submit_the_member_search_for_the(params)
        outputs['savings_balance'] = self.s07_read_the_share_savings_balance_from_the(params)
        outputs['member_name'] = self.s08_read_the_member_s_name_from_the_member(params)
        return outputs


def test_harbor_member_savings_balance(page: Page) -> None:
    outputs = HarborMemberSavingsBalanceFlow(page).run(EXAMPLE_PARAMS)

    assert set(outputs) == {'member_name', 'savings_balance'}
    assert all(outputs.values()), outputs
