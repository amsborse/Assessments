"""Tenants and per-tenant overlays.

Many institutions run the same vendor product with different branding, labels and versions. The
capability stays shared (keyed by product); a tenant carries only its *differences* as an
overlay: replacement targets for specific steps, pinned to the capability version they were made
against. Overlays are proposed automatically from drift (see `ReplayEngine(propose_overlay=True)`),
reviewed, and only approved overlays are applied unattended.

    catalog/tenants/<tenant>.json                       base URL, product, display name
    catalog/overlays/<tenant>/<capability>@v<N>.json    replacement targets for that version
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from assessments.capability.schema import (
    Capability,
    Model,
    Review,
    ReviewStatus,
    Target,
    TargetPresent,
)

TENANT_ID = r"^[a-z][a-z0-9-]*$"


class Tenant(Model):
    id: str = Field(pattern=TENANT_ID)
    name: str
    product: str
    base_url: str


class OverlayProvenance(Model):
    proposed_from_run: str
    proposed_at: datetime
    reason: str


class Overlay(Model):
    schema_version: Literal["overlay/1"] = "overlay/1"
    tenant: str = Field(pattern=TENANT_ID)
    capability: str
    base_version: int = Field(ge=1)
    targets: dict[str, Target] = Field(description="Step id → replacement target.")
    review: Review = Field(default_factory=Review)
    provenance: OverlayProvenance


class OverlayMismatch(ValueError):
    pass


def apply_overlay(capability: Capability, overlay: Overlay) -> Capability:
    """Return the capability with the overlay's targets swapped in (checkpoints included)."""
    if overlay.capability != capability.id or overlay.base_version != capability.version:
        raise OverlayMismatch(
            f"overlay is for {overlay.capability}@v{overlay.base_version}, not {capability.ref}"
        )
    steps_by_id = {s.id: s for s in capability.steps}
    unknown = set(overlay.targets) - set(steps_by_id)
    if unknown:
        raise OverlayMismatch(f"overlay references unknown steps {sorted(unknown)}")

    # Targets hold lists (not hashable): key replacements by their canonical JSON.
    replaced = {
        _key(steps_by_id[sid].target): t
        for sid, t in overlay.targets.items()
        if steps_by_id[sid].target is not None
    }
    steps = [
        s.model_copy(update={"target": overlay.targets[s.id]}) if s.id in overlay.targets else s
        for s in capability.steps
    ]
    success = [
        TargetPresent(target=replaced[_key(c.target)])
        if isinstance(c, TargetPresent) and _key(c.target) in replaced
        else c
        for c in capability.success
    ]
    return capability.model_copy(update={"steps": steps, "success": success})


def _key(target: Target | None) -> str:
    return target.model_dump_json() if target is not None else ""


class TenantStore:
    def __init__(self, catalog_dir: Path) -> None:
        self.catalog = catalog_dir

    def tenant(self, tenant_id: str) -> Tenant:
        path = self.catalog / "tenants" / f"{tenant_id}.json"
        return Tenant.model_validate_json(path.read_text(encoding="utf-8"))

    def _overlay_path(self, tenant_id: str, capability: str, version: int) -> Path:
        return self.catalog / "overlays" / tenant_id / f"{capability}@v{version}.json"

    def overlay(self, tenant_id: str, capability: Capability) -> Overlay | None:
        path = self._overlay_path(tenant_id, capability.id, capability.version)
        if not path.exists():
            return None
        return Overlay.model_validate_json(path.read_text(encoding="utf-8"))

    def save_overlay(self, overlay: Overlay) -> Path:
        path = self._overlay_path(overlay.tenant, overlay.capability, overlay.base_version)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            overlay.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8"
        )
        return path

    def approve_overlay(self, tenant_id: str, capability: Capability, reviewer: str) -> Overlay:
        overlay = self.overlay(tenant_id, capability)
        if overlay is None:
            raise FileNotFoundError(f"no overlay for {tenant_id} / {capability.ref}")
        review = Review(
            status=ReviewStatus.APPROVED, reviewed_by=reviewer, reviewed_at=datetime.now(UTC)
        )
        approved = overlay.model_copy(update={"review": review})
        self.save_overlay(approved)
        return approved
