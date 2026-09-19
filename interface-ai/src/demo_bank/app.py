"""Harbor CU "Teller Console" — a deliberately legacy-style back-office app used as the target.

It imitates the surfaces we automate in production: server-rendered pages inside a frameset,
table-based layout, labels in neighbouring cells (no <label for>), no ids or test ids, and
runtime exceptional states. All data is synthetic.

Exceptional states can be injected via `POST /__faults` (a test-harness endpoint, not part of
the "app"). Each fault is a counter consumed by the next matching request.

`variant="b"` models a second tenant on the same vendor product (v4.3, different branding and
relabelled controls, same form contract) to exercise locator fallback and drift reporting.
"""

import asyncio
import html
import secrets
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Annotated

from fastapi import Cookie, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel

SESSION_COOKIE = "HCSESSID"
SESSION_IDLE_SECONDS = 15 * 60
OPERATOR_USER = "teller1"
OPERATOR_PASSWORD = "harbor-demo"  # synthetic credential for the local demo app only
SUPERVISOR_ID = "sup01"
SUPERVISOR_PIN = "4321"  # synthetic


@dataclass
class Account:
    suffix: str
    description: str
    balance: Decimal


@dataclass
class Member:
    number: str
    name: str
    ssn: str
    dob: str
    address: str
    phone: str
    since: str
    accounts: list[Account]
    restricted: bool = False
    alert: str | None = None


def _money(value: Decimal) -> str:
    return f"${value:,.2f}"


MEMBERS: dict[str, Member] = {
    m.number: m
    for m in [
        Member(
            "12345",
            "Jordan Avery",
            "512-44-9012",
            "03/14/1986",
            "118 Harbor View Rd, Portland ME 04101",
            "(207) 555-0142",
            "2009",
            [
                Account("S01", "Share Savings", Decimal("2418.07")),
                Account("S10", "Share Draft Checking", Decimal("913.55")),
                Account("S40", "Holiday Club", Decimal("150.00")),
            ],
        ),
        Member(
            "48213",
            "Priya Raman",
            "610-22-7781",
            "11/02/1979",
            "42 Anchor Ln, Bath ME 04530",
            "(207) 555-0199",
            "2015",
            [
                Account("S01", "Share Savings", Decimal("15032.90")),
                Account("S10", "Share Draft Checking", Decimal("2210.14")),
            ],
        ),
        Member(
            "20417",
            "Casey Lindqvist",
            "401-87-3321",
            "07/29/1990",
            "9 Pier St, Rockland ME 04841",
            "(207) 555-0107",
            "2018",
            [Account("S01", "Share Savings", Decimal("7780.00"))],
            restricted=True,
        ),
        Member(
            "55555",
            "Morgan Blake",
            "233-19-6604",
            "01/05/1968",
            "300 Lighthouse Ave, Camden ME 04843",
            "(207) 555-0175",
            "1995",
            [Account("S01", "Share Savings", Decimal("421.33"))],
            alert="Member has an active fraud alert. "
            "Verify photo ID before disclosing information.",
        ),
    ]
}

PRODUCTS = ["Holiday Club", "Vacation Club", "12-Month Share Certificate"]

# Synthetic recent activity shown on every member page: (posted, description, suffix, amount).
# Positive = credit, negative = debit.
ACTIVITY: list[tuple[str, str, str, Decimal]] = [
    ("09/15", "Payroll direct deposit - HARBOR MARINE SUPPLY", "S10", Decimal("1842.60")),
    ("09/14", "Debit card - HANNAFORD #2231", "S10", Decimal("-86.42")),
    ("09/12", "Transfer to Share Savings", "S10", Decimal("-250.00")),
    ("09/12", "Transfer from Share Draft Checking", "S01", Decimal("250.00")),
    ("09/10", "ACH - CENTRAL MAINE POWER", "S10", Decimal("-112.37")),
    ("09/01", "Dividend", "S01", Decimal("3.41")),
    ("08/30", "Shared branch withdrawal", "S10", Decimal("-60.00")),
]


def _signed(amount: Decimal) -> str:
    """Accounting style: credits with +, debits in parentheses (never colour alone)."""
    return f"+{_money(amount)}" if amount >= 0 else f"({_money(-amount)})"


def _activity_table() -> str:
    rows = "".join(
        f"<tr><td>{d}</td><td>{html.escape(desc)}</td><td>{sfx}</td>"
        f'<td class="{"type-cr" if amt >= 0 else "type-dr"}">{"Credit" if amt >= 0 else "Debit"}</td>'
        f'<td align="right" class="{"cr" if amt >= 0 else "dr"}">{_signed(amt)}</td></tr>'
        for d, desc, sfx, amt in ACTIVITY
    )
    credits = sum((a for *_, a in ACTIVITY if a > 0), Decimal(0))
    debits = -sum((a for *_, a in ACTIVITY if a < 0), Decimal(0))
    return (
        '<p class="section">Recent activity, last 30 days: '
        f'<span class="cr">{_signed(credits)} in</span>, <span class="dr">{_signed(-debits)} out</span></p>'
        '<table border="1" cellpadding="3" cellspacing="0" width="90%">'
        '<tr class="hdr"><td><b>Posted</b></td><td><b>Transaction</b></td><td><b>Suffix</b></td>'
        f"<td><b>Type</b></td><td><b>Amount</b></td></tr>{rows}</table>"
    )


@dataclass
class Session:
    user: str
    last_seen: float
    overrides: set[str] = field(default_factory=set)
    pending: dict[str, str] = field(default_factory=dict)


class Faults(BaseModel):
    """Counters: each is decremented when the fault fires. slow_ms applies to every request."""

    slow_ms: int = 0
    notice: int = 0  # maintenance interstitial before member detail
    unavailable: int = 0  # 503 page on member detail
    server_error: int = 0  # 500 "yellow screen" on member detail
    expire_session: int = 0  # next authenticated request finds the session expired


class State:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}
        self.faults = Faults()
        self.confirmation_seq = 100230


# Styling is applied only through this stylesheet and a class on <body>: element structure,
# text and names stay exactly as recorded, so capabilities (incl. CSS-path fallbacks) still
# resolve. No text-transform or ::before content: both would change what automation reads.
_CSS = """
body { margin: 0; padding: 0 18px 24px; font: 13px/1.45 "Segoe UI", Tahoma, Verdana, sans-serif;
  color: #1d2a36; background: #f2f5f8 !important; }
td { font-size: 13px; }
a { color: #0f4c75; }
a:hover { color: #b8732a; }
.err { color: #9b1c1c; font-weight: 600; background: #fdf0ef; border: 1px solid #efc3bf;
  border-left: 4px solid #9b1c1c; padding: 8px 12px; border-radius: 3px; display: inline-block; }
table[width="100%"] > tbody > tr > td[bgcolor] { background: linear-gradient(#1d4f73, #123a58) !important;
  border-bottom: 3px solid #b8893a; padding: 9px 12px !important; border-radius: 4px 4px 0 0; }
table[width="100%"] > tbody > tr > td[bgcolor] b { font-size: 16px; font-weight: 600; letter-spacing: .01em; }
body.main > table:first-child { margin-top: 16px; }
.hdr, tr.hdr td { background: #dfe7ef !important; color: #123a58; }
td.hdr { border-bottom: 1px solid #c3d0dc; padding: 6px 8px !important; }
input[type=text], input[type=password], select {
  font: inherit; padding: 5px 7px; border: 1px solid #9fb0c0; border-radius: 3px;
  background: #fff; box-shadow: inset 0 1px 2px rgba(18,58,88,.12); }
input[type=text]:focus, input[type=password]:focus, select:focus {
  outline: 2px solid #b8893a; outline-offset: 0; border-color: #b8893a; }
input[type=submit], input[type=reset] {
  font: inherit; font-weight: 600; padding: 5px 16px; border-radius: 3px; cursor: pointer;
  color: #fff; border: 1px solid #0e3350; background: linear-gradient(#2a6592, #174a70); }
input[type=reset] { color: #1d2a36; border-color: #9fb0c0; background: linear-gradient(#fdfdfd, #e3e8ed); }
input[type=submit]:hover { background: linear-gradient(#317aad, #1b5781); }
table[border="1"] { border-collapse: collapse; border: 1px solid #b7c5d2; background: #fff; }
table[border="1"] td { border: 1px solid #d5dee6; padding: 6px 9px !important; }
table[border="1"] tr:nth-child(even) td { background: #f6f8fa; }
table[border="1"] td[align="right"] { font-variant-numeric: tabular-nums; }
td[align="right"] > b { color: #4a5a69; font-weight: 600; }
p { max-width: 70ch; }
/* money semantics: green = credit (money in), red = debit (money out); sign/parentheses carry
   the same meaning for colour-blind users */
.cr, td.cr { color: #1f7a4d; font-weight: 600; font-variant-numeric: tabular-nums; }
.dr, td.dr { color: #b3261e; font-weight: 600; font-variant-numeric: tabular-nums; }
td.type-cr, td.type-dr { font-size: 12px; font-weight: 600; }
td.type-cr { color: #1f7a4d; }
td.type-dr { color: #b3261e; }
table[border="1"] tr td.type-cr { box-shadow: inset 3px 0 0 #1f7a4d; }
table[border="1"] tr td.type-dr { box-shadow: inset 3px 0 0 #b3261e; }
p.section { font-weight: 600; color: #123a58; margin: 0 0 8px; }
/* sign-on */
body.signon { display: flex; flex-direction: column; align-items: center; padding-top: 8vh; }
body.signon > table:first-child { width: 420px; }
body.signon form { box-sizing: border-box; width: 420px; background: #fff; border: 1px solid #c3d0dc; border-top: 0;
  border-radius: 0 0 4px 4px; padding: 18px 20px 20px; box-shadow: 0 6px 18px rgba(18,58,88,.10); }
body.signon br { display: none; }
body.signon input[type=text], body.signon input[type=password] { width: 230px; }
body.signon input[type=submit] { padding: 7px 22px; }
/* banner frame */
body.banner { background: linear-gradient(90deg, #0f3450, #1d4f73) !important; color: #dfe9f2;
  padding: 0 18px; overflow: hidden; }
body.banner table { height: 54px; }
body.banner font { color: #fff !important; font-size: 19px; font-weight: 600; }
body.banner td[align="right"] { color: #e8c98f; font-size: 13px; }
/* nav frame */
body.nav { background: #e6ecf1 !important; padding: 14px 10px; border-right: 1px solid #c3d0dc; }
body.nav table { width: 100%; }
body.nav td { padding: 2px 0 !important; }
body.nav td a { display: inline-block; width: calc(100% - 16px); padding: 7px 10px; border-radius: 3px;
  text-decoration: none; }
body.nav td a font { color: #123a58 !important; font-weight: 600; }
body.nav td a:hover { background: #fff; }
body.nav a[target="_top"] { color: #9b1c1c; }
body.nav ul.menu { list-style: none; margin: 0; padding: 0; }
body.nav ul.menu a { display: block; padding: 7px 10px; border-radius: 3px; text-decoration: none;
  color: #123a58; font-weight: 600; }
body.nav ul.menu a:hover { background: #fff; }
"""


def _page(title: str, body: str, *, onload: str = "") -> HTMLResponse:
    # Legacy markup: tables, <font>, bgcolor; no ids, no test ids, no semantic landmarks.
    kind = {"banner": "banner", "nav": "nav", "Harbor CU - Sign On": "signon"}.get(title, "main")
    return HTMLResponse(
        f"""<html><head><title>{html.escape(title)}</title>
<style>{_CSS}</style></head>
<body class="{kind}" bgcolor="#f4f4ec"{f' onload="{onload}"' if onload else ""}>{body}</body></html>"""
    )


def _title_bar(text: str) -> str:
    return (
        '<table width="100%" cellpadding="3" cellspacing="0"><tr><td bgcolor="#1f3b63">'
        f'<font color="white" size="3"><b>{html.escape(text)}</b></font></td></tr></table><br>'
    )


VARIANTS: dict[str, dict[str, str]] = {
    "a": {
        "brand": "Harbor Community Credit Union - Teller Console v4.2",
        "nav_inquiry": "Member Inquiry",
        "member_label": "Member #:",
        "search": "Search",
        "balance_col": "Balance",
    },
    "b": {
        "brand": "Bayside Federal Credit Union - Teller Console v4.3",
        "nav_inquiry": "Member Lookup",
        "member_label": "Member Number:",
        "search": "Find",
        "balance_col": "Current Balance",
    },
    # A deeper redesign of the same product: the menu gained an entry and renamed inquiry, so
    # every recorded locator for that link fails (text, role name and structural path).
    "c": {
        "brand": "Coastal Credit Union - Teller Console v5.0",
        "nav_inquiry": "Find a Member",
        "nav_extra": "Dashboard",
        "nav_style": "list",
        "inquiry_path": "/main/find",
        "member_label": "Member #:",
        "search": "Search",
        "balance_col": "Balance",
    },
}


def create_demo_bank(variant: str = "a") -> FastAPI:
    app = FastAPI(title="Harbor CU Teller Console (demo)", docs_url=None, redoc_url=None)
    state = State()
    app.state.bank = state
    ui = VARIANTS[variant]

    async def slow() -> None:
        if state.faults.slow_ms:
            await asyncio.sleep(state.faults.slow_ms / 1000)

    def session_for(sid: str | None, *, mid_flow: bool = False) -> Session | None:
        if not sid or sid not in state.sessions:
            return None
        sess = state.sessions[sid]
        if mid_flow and state.faults.expire_session > 0:
            state.faults.expire_session -= 1
            del state.sessions[sid]
            return None
        if time.monotonic() - sess.last_seen > SESSION_IDLE_SECONDS:
            del state.sessions[sid]
            return None
        sess.last_seen = time.monotonic()
        return sess

    def expired() -> HTMLResponse:
        # Rendered inside whatever frame asked; the top window must be re-logged in.
        return _page(
            "Session Expired",
            _title_bar("Session Expired")
            + '<p class="err">Your session has expired due to inactivity. Please sign on again.</p>'
            '<a href="/" target="_top">Return to Sign On</a>',
        )

    # ------------------------------------------------------------------ test harness
    @app.post("/__faults")
    async def set_faults(faults: Faults) -> Faults:
        state.faults = faults
        return state.faults

    @app.get("/__faults")
    async def get_faults() -> Faults:
        return state.faults

    # ------------------------------------------------------------------ sign on
    @app.get("/", response_class=HTMLResponse)
    async def signon(msg: str = "") -> HTMLResponse:
        err = f'<tr><td colspan="2" class="err">{html.escape(msg)}</td></tr>' if msg else ""
        return _page(
            "Harbor CU - Sign On",
            _title_bar(ui["brand"])
            + f"""<form method="post" action="/signon"><table border="0" cellpadding="4">{err}
<tr><td align="right">Operator ID:</td><td><input type="text" name="opid" size="12"></td></tr>
<tr><td align="right">Password:</td><td><input type="password" name="pw" size="12"></td></tr>
<tr><td></td><td><input type="submit" value="Sign On"></td></tr></table></form>""",
        )

    @app.post("/signon")
    async def do_signon(
        opid: Annotated[str, Form()] = "", pw: Annotated[str, Form()] = ""
    ) -> Response:
        await slow()
        if opid != OPERATOR_USER or pw != OPERATOR_PASSWORD:
            return RedirectResponse("/?msg=Invalid+operator+ID+or+password.", status_code=303)
        sid = secrets.token_urlsafe(16)
        state.sessions[sid] = Session(user=opid, last_seen=time.monotonic())
        resp = RedirectResponse("/console", status_code=303)
        resp.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax")
        return resp

    @app.get("/console", response_class=HTMLResponse)
    async def console(
        hcsessid: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> Response:
        if session_for(hcsessid) is None:
            return RedirectResponse("/?msg=Please+sign+on.", status_code=303)
        return HTMLResponse(
            """<html><head><title>Harbor CU Teller Console</title></head>
<frameset rows="56,*" border="0" frameborder="0">
  <frame src="/banner" name="banner" scrolling="no">
  <frameset cols="210,*" border="0" frameborder="0">
    <frame src="/nav" name="nav">
    <frame src="/main/welcome" name="main">
  </frameset>
</frameset></html>"""
        )

    @app.get("/banner", response_class=HTMLResponse)
    async def banner() -> HTMLResponse:
        return _page(
            "banner",
            '<table width="100%"><tr><td><font size="4" color="#1f3b63"><b>Harbor Community CU'
            '</b></font></td><td align="right">Branch 004 &nbsp; Drawer 12</td></tr></table>',
        )

    @app.get("/nav", response_class=HTMLResponse)
    async def nav() -> HTMLResponse:
        links = [
            *([("/main/welcome", ui["nav_extra"])] if "nav_extra" in ui else []),
            (ui.get("inquiry_path", "/main/inquiry"), ui["nav_inquiry"]),
            ("/main/welcome", "Teller Home"),
            ("/main/reports", "Daily Reports"),
        ]
        if ui.get("nav_style") == "list":
            # v5.0 redesign: the menu is a list, not a table.
            items = "".join(f'<li><a href="{u}" target="main">{t}</a></li>' for u, t in links)
            return _page(
                "nav",
                f'<ul class="menu">{items}</ul><p><a href="/signoff" target="_top">Sign Off</a></p>',
            )
        rows = "".join(
            f'<tr><td><img src="data:," width="8"> <a href="{u}" target="main">'
            f'<font color="#1f3b63">{t}</font></a></td></tr>'
            for u, t in links
        )
        return _page(
            "nav",
            f'<table cellpadding="4">{rows}<tr><td><br><a href="/signoff" target="_top">'
            "Sign Off</a></td></tr></table>",
        )

    @app.get("/signoff")
    async def signoff(
        hcsessid: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> Response:
        state.sessions.pop(hcsessid or "", None)
        return RedirectResponse("/?msg=Signed+off.", status_code=303)

    # ------------------------------------------------------------------ main frame pages
    @app.get("/main/welcome", response_class=HTMLResponse)
    async def welcome(
        hcsessid: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> Response:
        if (sess := session_for(hcsessid)) is None:
            return expired()
        return _page(
            "Teller Home",
            _title_bar("Teller Home")
            + f"<p>Welcome, {html.escape(sess.user)}. Select a function from the menu.</p>",
        )

    @app.get("/main/reports", response_class=HTMLResponse)
    async def reports(
        hcsessid: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> Response:
        if session_for(hcsessid) is None:
            return expired()
        return _page("Daily Reports", _title_bar("Daily Reports") + "<p>No reports queued.</p>")

    def inquiry_form(msg: str = "", value: str = "") -> HTMLResponse:
        err = f'<p class="err">{html.escape(msg)}</p>' if msg else ""
        return _page(
            "Member Inquiry",
            _title_bar("Member Inquiry")
            + err
            + f"""<form method="post" action="/main/inquiry"><table cellpadding="3">
<tr><td class="hdr" colspan="2"><b>Search Criteria</b></td></tr>
<tr><td align="right">{ui["member_label"]}</td><td><input type="text" name="mbrno" size="10"
 value="{html.escape(value)}"></td></tr>
<tr><td align="right">Last Name:</td><td><input type="text" name="lname" size="16"></td></tr>
<tr><td></td><td><input type="submit" value="{ui["search"]}"> <input type="reset" value="Clear">
</td></tr>
</table></form>""",
        )

    @app.get("/main/find", response_class=HTMLResponse)  # v5.0 route for the same screen
    @app.get("/main/inquiry", response_class=HTMLResponse)
    async def inquiry(
        hcsessid: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> Response:
        if session_for(hcsessid) is None:
            return expired()
        return inquiry_form()

    @app.post("/main/inquiry")
    async def do_inquiry(
        mbrno: Annotated[str, Form()] = "",
        lname: Annotated[str, Form()] = "",
        hcsessid: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> Response:
        await slow()
        if session_for(hcsessid, mid_flow=True) is None:
            return expired()
        mbrno = mbrno.strip()
        if not mbrno and not lname.strip():
            return inquiry_form("Enter a member number or last name.")
        if mbrno and not (mbrno.isdigit() and 5 <= len(mbrno) <= 9):
            return inquiry_form("Member number must be 5-9 digits.", mbrno)
        if mbrno not in MEMBERS:
            return inquiry_form(f"No member found matching {mbrno}.", mbrno)
        if state.faults.notice > 0:
            state.faults.notice -= 1
            return _page(
                "System Notice",
                _title_bar("System Notice")
                + "<p>Core processing will be unavailable tonight from 11:00 PM to 1:00 AM for "
                "scheduled maintenance.</p>"
                f'<form method="get" action="/main/member"><input type="hidden" name="m" '
                f'value="{mbrno}"><input type="submit" value="Acknowledge"></form>',
            )
        return RedirectResponse(f"/main/member?m={mbrno}", status_code=303)

    @app.get("/main/member", response_class=HTMLResponse)
    async def member(
        m: str, hcsessid: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None
    ) -> Response:
        await slow()
        if (sess := session_for(hcsessid)) is None:
            return expired()
        if state.faults.unavailable > 0:
            state.faults.unavailable -= 1
            return HTMLResponse(
                "<html><head><title>Service Unavailable</title></head><body><h2>Service "
                "Unavailable</h2><p>The host did not respond in time (HCX-503). Please retry.</p>"
                "</body></html>",
                status_code=503,
            )
        if state.faults.server_error > 0:
            state.faults.server_error -= 1
            return HTMLResponse(
                '<html><head><title>Runtime Error</title></head><body bgcolor="#ffffcc">'
                "<h1>Server Error in '/TellerConsole' Application.</h1><h2><i>Object reference "
                "not set to an instance of an object.</i></h2><b>Exception Details:</b> "
                "System.NullReferenceException</body></html>",
                status_code=500,
            )
        mem = MEMBERS.get(m)
        if mem is None:
            return inquiry_form(f"No member found matching {m}.", m)
        if mem.restricted and m not in sess.overrides:
            return _page(
                "Access Restricted",
                _title_bar("Access Restricted")
                + '<p class="err">Employee account. Supervisor override required to view this '
                "member.</p>"
                f"""<form method="post" action="/main/override"><input type="hidden" name="m"
 value="{m}"><table cellpadding="3">
<tr><td align="right">Supervisor ID:</td><td><input type="text" name="supid" size="8"></td></tr>
<tr><td align="right">PIN:</td><td><input type="password" name="pin" size="6"></td></tr>
<tr><td></td><td><input type="submit" value="Override"></td></tr></table></form>""",
            )
        onload = ""
        if mem.alert:
            onload = f"if(!confirm('{mem.alert} Continue?')){{location.href='/main/inquiry'}}"
        acct_rows = "".join(
            f'<tr><td>{a.suffix}</td><td>{a.description}</td><td align="right">'
            f'{_money(a.balance)}</td><td align="right">{_money(a.balance)}</td></tr>'
            for a in mem.accounts
        )
        return _page(
            "Member Detail",
            _title_bar("Member Detail")
            + f"""<table cellpadding="2">
<tr><td align="right"><b>Member #:</b></td><td>{mem.number}</td>
    <td align="right"><b>Member Since:</b></td><td>{mem.since}</td></tr>
<tr><td align="right"><b>Name:</b></td><td>{html.escape(mem.name)}</td>
    <td align="right"><b>SSN:</b></td><td>{mem.ssn}</td></tr>
<tr><td align="right"><b>DOB:</b></td><td>{mem.dob}</td>
    <td align="right"><b>Phone:</b></td><td>{mem.phone}</td></tr>
<tr><td align="right"><b>Address:</b></td><td colspan="3">{html.escape(mem.address)}</td></tr>
</table><br>
<table border="1" cellpadding="3" cellspacing="0" width="90%">
<tr class="hdr"><td><b>Suffix</b></td><td><b>Description</b></td><td><b>{ui["balance_col"]}</b></td>
<td><b>Available</b></td></tr>{acct_rows}</table><br>
<a href="/main/subaccount?m={mem.number}">Open Sub-Account</a> |
<a href="/main/inquiry">New Inquiry</a><br><br>{_activity_table()}""",
            onload=onload,
        )

    @app.post("/main/override")
    async def override(
        m: Annotated[str, Form()],
        supid: Annotated[str, Form()] = "",
        pin: Annotated[str, Form()] = "",
        hcsessid: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> Response:
        if (sess := session_for(hcsessid)) is None:
            return expired()
        if supid != SUPERVISOR_ID or pin != SUPERVISOR_PIN:
            return _page(
                "Access Restricted",
                _title_bar("Access Restricted") + '<p class="err">Override rejected.</p>',
            )
        sess.overrides.add(m)
        return RedirectResponse(f"/main/member?m={m}", status_code=303)

    # ------------------------------------------------------------------ sub-account (irreversible)
    @app.get("/main/subaccount", response_class=HTMLResponse)
    async def subaccount(
        m: str, msg: str = "", hcsessid: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None
    ) -> Response:
        if session_for(hcsessid) is None:
            return expired()
        if m not in MEMBERS:
            return inquiry_form(f"No member found matching {m}.", m)
        options = "".join(f"<option>{p}</option>" for p in PRODUCTS)
        err = f'<p class="err">{html.escape(msg)}</p>' if msg else ""
        return _page(
            "Open Sub-Account",
            _title_bar(f"Open Sub-Account - Member {m}")
            + err
            + f"""<form method="post" action="/main/subaccount/review"><input type="hidden"
 name="m" value="{m}"><table cellpadding="3">
<tr><td align="right">Product:</td><td><select name="prod"><option value="">-- select --</option>
{options}</select></td></tr>
<tr><td align="right">Initial Deposit:</td><td><input type="text" name="amt" size="10"></td></tr>
<tr><td align="right">Funding Suffix:</td><td><input type="text" name="src" size="4"
 value="S01"></td></tr>
<tr><td></td><td><input type="submit" value="Continue"></td></tr></table></form>""",
        )

    @app.post("/main/subaccount/review")
    async def subaccount_review(
        m: Annotated[str, Form()],
        prod: Annotated[str, Form()] = "",
        amt: Annotated[str, Form()] = "",
        src: Annotated[str, Form()] = "",
        hcsessid: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> Response:
        if (sess := session_for(hcsessid)) is None:
            return expired()
        try:
            amount = Decimal(amt.replace("$", "").replace(",", ""))
        except ArithmeticError:
            amount = Decimal(-1)
        if prod not in PRODUCTS:
            return RedirectResponse(f"/main/subaccount?m={m}&msg=Select+a+product.", 303)
        if amount < 5:
            return RedirectResponse(
                f"/main/subaccount?m={m}&msg=Initial+deposit+must+be+at+least+%245.00.", 303
            )
        token = secrets.token_hex(4)
        sess.pending[token] = f"{m}|{prod}|{amount}|{src}"
        return _page(
            "Review Sub-Account",
            _title_bar("Review Sub-Account")
            + f"""<table cellpadding="3"><tr><td align="right">Member #:</td><td>{m}</td></tr>
<tr><td align="right">Product:</td><td>{html.escape(prod)}</td></tr>
<tr><td align="right">Initial Deposit:</td><td>{_money(amount)}</td></tr>
<tr><td align="right">Funding Suffix:</td><td>{html.escape(src)}</td></tr></table>
<p>Press <b>Confirm</b> to open the sub-account. This transfer cannot be reversed.</p>
<form method="post" action="/main/subaccount/confirm"><input type="hidden" name="t"
 value="{token}"><input type="submit" value="Confirm"></form>
<a href="/main/subaccount?m={m}">Back</a>""",
        )

    @app.post("/main/subaccount/confirm")
    async def subaccount_confirm(
        t: Annotated[str, Form()],
        hcsessid: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> Response:
        if (sess := session_for(hcsessid)) is None:
            return expired()
        if t not in sess.pending:
            return _page(
                "Error", _title_bar("Error") + '<p class="err">Request already processed.</p>'
            )
        m, prod, _amount, _src = sess.pending.pop(t).split("|")
        state.confirmation_seq += 1
        return _page(
            "Sub-Account Opened",
            _title_bar("Sub-Account Opened") + f"<p>{html.escape(prod)} opened for member {m}.</p>"
            '<table cellpadding="3"><tr><td align="right">Confirmation #:</td>'
            f"<td><b>HC{state.confirmation_seq}</b></td></tr></table>",
        )

    @app.middleware("http")
    async def no_cache(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        response.headers["cache-control"] = "no-store"
        return response

    return app
