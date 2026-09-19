import json

from playwright.sync_api import Page


def test_browser_reaches_running_service(page: Page, live_server: str) -> None:
    response = page.goto(f"{live_server}/healthz")

    assert response is not None
    assert response.status == 200
    assert response.headers["x-request-id"]
    assert json.loads(page.inner_text("body"))["status"] == "ok"


def test_browser_sees_json_error_for_unknown_route(page: Page, live_server: str) -> None:
    response = page.goto(f"{live_server}/missing")

    assert response is not None
    assert response.status == 404
    assert json.loads(page.inner_text("body"))["error"]["code"] == "not_found"
