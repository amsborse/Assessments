"""Agent-facing capability catalog: discover capabilities and invoke them by name with typed args.

This is the production path for the calling AI agent: it sees each capability's contract
(inputs, outputs, possible business outcomes, side effects) and gets a structured ReplayResult.
Unattended invocation requires an APPROVED capability; drafts need `attended: true`.
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from assessments.capability.certification import load_report
from assessments.capability.overlay import TenantStore
from assessments.capability.profile import effective_states, load_profile
from assessments.capability.schema import Capability, OutcomeKind, ReviewStatus
from assessments.capability.store import CapabilityNotFound, CapabilityStore
from assessments.config import Settings
from assessments.errors import AppError, NotFoundError
from assessments.replay.result import ReplayResult
from assessments.service import run_replay

router = APIRouter(prefix="/api/capabilities")
pages = APIRouter()
_CATALOG_HTML = (Path(__file__).parent / "catalog.html").read_text(encoding="utf-8")


@pages.get("/catalog", response_class=HTMLResponse, include_in_schema=False)
async def catalog_page() -> HTMLResponse:
    return HTMLResponse(_CATALOG_HTML)


class NotApproved(AppError):
    status_code = 409
    code = "capability_not_approved"


class InvokeRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    allow_irreversible: bool = False
    attended: bool = Field(default=False, description="An operator is watching the console.")


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _load(settings: Settings, ref: str) -> Capability:
    try:
        return CapabilityStore(settings.catalog_dir).load(ref)
    except CapabilityNotFound as exc:
        raise NotFoundError(f"unknown capability {ref}") from exc


def contract(settings: Settings, cap: Capability) -> dict[str, Any]:
    states = effective_states(load_profile(settings.catalog_dir, cap.app.profile), cap)
    return {
        "name": cap.id,
        "version": cap.version,
        "title": cap.title,
        "description": cap.description,
        "review": cap.review.status,
        "side_effects": cap.side_effects,
        "inputs": {k: v.model_dump(exclude_none=True) for k, v in cap.inputs.items()},
        "outputs": {k: v.model_dump(exclude_none=True) for k, v in cap.outputs.items()},
        "business_outcomes": {
            s.id: s.description for s in states if s.kind is OutcomeKind.BUSINESS
        },
    }


@router.get("")
async def list_capabilities(request: Request) -> list[dict[str, Any]]:
    settings = _settings(request)
    return [contract(settings, c) for c in CapabilityStore(settings.catalog_dir).list()]


@router.get("/{ref}")
async def get_capability(request: Request, ref: str) -> dict[str, Any]:
    return _load(_settings(request), ref).model_dump(mode="json", exclude_none=True)


@router.get("/{ref}/view")
async def view_capability(request: Request, ref: str) -> dict[str, Any]:
    """Everything a reviewer needs on one page: artifact, contract, states, confidence, tenants."""
    settings = _settings(request)
    cap = _load(settings, ref)
    profile = load_profile(settings.catalog_dir, cap.app.profile)
    report = load_report(settings.catalog_dir, cap)
    tenants_dir = settings.catalog_dir / "tenants"
    store = TenantStore(settings.catalog_dir)
    tenants = []
    for path in sorted(tenants_dir.glob("*.json")) if tenants_dir.is_dir() else []:
        tenant = store.tenant(path.stem)
        if tenant.product != cap.app.product:
            continue
        overlay = store.overlay(tenant.id, cap)
        tenants.append(
            {
                "id": tenant.id,
                "name": tenant.name,
                "overlay": None
                if overlay is None
                else {
                    "status": overlay.review.status,
                    "steps": sorted(overlay.targets),
                    "reason": overlay.provenance.reason,
                },
            }
        )
    return {
        "capability": cap.model_dump(mode="json", exclude_none=True),
        "contract": contract(settings, cap),
        "states": [
            {"id": s.id, "kind": s.kind, "description": s.description, "response": s.response}
            for s in effective_states(profile, cap)
        ],
        "certification": report.model_dump(mode="json") if report else None,
        "tenants": tenants,
    }


@router.post("/{ref}/invoke")
async def invoke(request: Request, ref: str, body: InvokeRequest) -> ReplayResult:
    settings = _settings(request)
    cap = _load(settings, ref)
    if cap.review.status is not ReviewStatus.APPROVED and not body.attended:
        raise NotApproved(
            f"{cap.ref} is {cap.review.status}; invoke with attended=true or approve it"
        )
    return await run_replay(
        settings,
        cap.ref,
        body.params,
        base_url=settings.target_base_url,
        headless=settings.browser_headless,
        allow_irreversible=body.allow_irreversible,
    )
