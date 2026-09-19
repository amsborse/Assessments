"""Agent-facing capability catalog: discover contracts and invoke capabilities by name."""

import httpx2 as httpx

from assessments.api.app import create_app
from assessments.capability.store import CapabilityStore
from assessments.config import Settings

from .conftest import CAPABILITY


def client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(settings)), base_url="http://api", timeout=60
    )


async def test_catalog_exposes_the_contract(settings: Settings) -> None:
    async with client(settings) as api:
        [contract] = (await api.get("/api/capabilities")).json()

    assert contract["name"] == CAPABILITY
    assert contract["review"] == "draft"
    assert contract["side_effects"] == "safe"
    assert contract["inputs"]["member_id"]["pattern"] == r"^\d{5,9}$"
    assert set(contract["outputs"]) == {"savings_balance", "member_name"}
    assert "member_not_found" in contract["business_outcomes"]


async def test_unattended_invocation_requires_approval(settings: Settings) -> None:
    body = {"params": {"member_id": "12345"}}
    async with client(settings) as api:
        refused = await api.post(f"/api/capabilities/{CAPABILITY}/invoke", json=body)
        CapabilityStore(settings.catalog_dir).approve(CAPABILITY, reviewer="reviewer.alex")
        invoked = await api.post(f"/api/capabilities/{CAPABILITY}/invoke", json=body)

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "capability_not_approved"
    assert invoked.status_code == 200
    assert invoked.json()["status"] == "succeeded"
    assert invoked.json()["outputs"]["savings_balance"] == "2418.07"


async def test_unknown_capability_is_404(settings: Settings) -> None:
    async with client(settings) as api:
        response = await api.post("/api/capabilities/nope.nothing/invoke", json={})

    assert response.status_code == 404
