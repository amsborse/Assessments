"""App profiles: knowledge shared by every capability (and tenant) on one vendor product.

Exceptional runtime states — "record not found", session expiry, maintenance interstitials,
permission blocks, error pages — belong to the *product*, not to one recorded flow. Curating
them once per product amortises the effort across every tenant running it and every
capability recorded against it. A capability may add or override states by id.
"""

import re
from pathlib import Path

from pydantic import Field

from assessments.capability.schema import Capability, KnownState, Model


class AppProfile(Model):
    id: str
    product: str
    description: str
    known_states: list[KnownState] = Field(default_factory=list)
    redact_labels: list[str] = Field(
        default_factory=list, description="Labels whose adjacent value is masked on the surface."
    )
    irreversible_controls: list[str] = Field(
        default_factory=list,
        description="Regexes over control names that commit changes (e.g. '^confirm$').",
    )

    def is_irreversible(self, control_name: str) -> bool:
        name = control_name.strip()
        return any(re.search(p, name, re.IGNORECASE) for p in self.irreversible_controls)


def effective_states(profile: AppProfile, capability: Capability) -> list[KnownState]:
    """Capability states override profile states with the same id; capability-only ones first."""
    own = {s.id: s for s in capability.outcomes}
    shared = [s for s in profile.known_states if s.id not in own]
    return [*capability.outcomes, *shared]


def load_profile(catalog_dir: Path, profile_id: str) -> AppProfile:
    path = catalog_dir / "profiles" / f"{profile_id}.json"
    return AppProfile.model_validate_json(path.read_text(encoding="utf-8"))
