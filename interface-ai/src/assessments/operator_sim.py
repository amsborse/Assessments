"""A simulated human operator, for reproducible demos, tests and recorded evidence.

It is deliberately *outside* the automation: it acts only through the operator console HTTP API
(claim → click/type/press → release), exactly like a person using /operator. The only shortcut
is how it "sees" where to click: it reads element geometry from the live page, standing in for a
person's eyes. Real operators use the console; this exists so the handoff path can be exercised
unattended.
"""

import asyncio
import logging
from typing import Any

import httpx2 as httpx

from assessments.session.runtime import REGISTRY

logger = logging.getLogger(__name__)


class SimulatedOperator:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        operator: str = "operator.sim",
        supervisor_id: str = "",
        supervisor_pin: str = "",
        approve: bool = True,
        resume_stuck: bool = False,
        think_s: float = 0.5,
    ) -> None:
        self.client = client
        self.operator = operator
        self.supervisor_id = supervisor_id
        self.supervisor_pin = supervisor_pin
        self.approve = approve
        self.resume_stuck = resume_stuck
        self.think_s = think_s
        self.handled: list[str] = []

    async def run(self) -> None:
        """Poll for open interventions until cancelled."""
        while True:
            sessions = (await self.client.get("/api/sessions")).json()
            for s in sessions:
                iv = s.get("intervention")
                if iv and iv["status"] == "open" and iv["id"] not in self.handled:
                    self.handled.append(iv["id"])
                    await self._handle(s["id"], iv)
            await asyncio.sleep(0.3)

    async def _post(self, path: str, body: dict[str, Any]) -> None:
        response = await self.client.post(path, json={"operator": self.operator, **body})
        response.raise_for_status()

    async def _handle(self, session_id: str, iv: dict[str, Any]) -> None:
        await asyncio.sleep(self.think_s)  # a person reads the request first
        await self._post(f"/api/interventions/{iv['id']}/claim", {})
        allowed = iv["allowed_resolutions"]
        if iv["kind"] == "approval":
            action = "approve" if self.approve else "deny"
            await self._post(
                f"/api/interventions/{iv['id']}/release",
                {"action": action, "note": "reviewed request details"},
            )
            return
        if iv["context"].get("state") == "supervisor_override_required" and self.supervisor_id:
            await self._enter_override(session_id)
            await self._post(
                f"/api/interventions/{iv['id']}/release",
                {"action": "resume", "note": "supervisor override entered"},
            )
            return
        if iv["kind"] == "stuck" and self.resume_stuck:
            await self._post(
                f"/api/interventions/{iv['id']}/release",
                {"action": "resume", "note": "checked the screen; carry on"},
            )
            return
        action = "abort" if "abort" in allowed else allowed[-1]
        await self._post(
            f"/api/interventions/{iv['id']}/release",
            {"action": action, "note": "no playbook for this intervention"},
        )

    async def _enter_override(self, session_id: str) -> None:
        async def click_on(selector: str) -> None:
            x, y = await self._center(session_id, selector)
            await self._post(f"/api/sessions/{session_id}/click", {"x": x, "y": y})

        await click_on('input[name="supid"]')
        await self._post(f"/api/sessions/{session_id}/type", {"text": self.supervisor_id})
        await click_on('input[name="pin"]')
        await self._post(f"/api/sessions/{session_id}/type", {"text": self.supervisor_pin})
        await click_on('input[type="submit"][value="Override"]')
        await asyncio.sleep(1.0)

    async def _center(self, session_id: str, selector: str) -> tuple[float, float]:
        session = REGISTRY.get(session_id)
        if session is None:
            raise LookupError(session_id)
        for frame in session.surface.page.frames:
            box = (
                await frame.locator(selector).first.bounding_box()
                if await frame.locator(selector).count()
                else None
            )
            if box:
                return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        raise LookupError(f"{selector} not visible")
