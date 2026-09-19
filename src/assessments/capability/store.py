"""File-backed capability catalog: <catalog>/capabilities/<id>/v<N>.json.

Artifacts are plain JSON so they are diffable and reviewable in pull requests. Versions are
immutable once written; changes (including approval) produce a new version file... except the
review block, which is updated in place on approval because it records *who* vouched for that
exact version.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

from assessments.capability.schema import Capability, Review, ReviewStatus

_VERSION_RE = re.compile(r"^v(\d+)\.json$")


class CapabilityNotFound(LookupError):
    pass


class CapabilityStore:
    def __init__(self, catalog_dir: Path) -> None:
        self.root = catalog_dir / "capabilities"

    def _dir(self, capability_id: str) -> Path:
        return self.root / capability_id

    def versions(self, capability_id: str) -> list[int]:
        d = self._dir(capability_id)
        if not d.is_dir():
            return []
        found = (_VERSION_RE.match(p.name) for p in d.iterdir())
        return sorted(int(m.group(1)) for m in found if m)

    def save_new_version(self, capability: Capability) -> tuple[Capability, Path]:
        version = (self.versions(capability.id) or [0])[-1] + 1
        cap = capability.model_copy(update={"version": version})
        path = self._dir(cap.id) / f"v{version}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cap.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8")
        return cap, path

    def load(self, ref: str) -> Capability:
        """`id` (latest version) or `id@vN`."""
        capability_id, _, v = ref.partition("@v")
        versions = self.versions(capability_id)
        if not versions:
            raise CapabilityNotFound(ref)
        version = int(v) if v else versions[-1]
        path = self._dir(capability_id) / f"v{version}.json"
        if not path.exists():
            raise CapabilityNotFound(ref)
        return Capability.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[Capability]:
        if not self.root.is_dir():
            return []
        return [self.load(d.name) for d in sorted(self.root.iterdir()) if self.versions(d.name)]

    def approve(self, ref: str, reviewer: str, notes: str = "") -> Capability:
        cap = self.load(ref)
        review = Review(
            status=ReviewStatus.APPROVED,
            reviewed_by=reviewer,
            reviewed_at=datetime.now(UTC),
            notes=notes or None,
        )
        cap = cap.model_copy(update={"review": review})
        path = self._dir(cap.id) / f"v{cap.version}.json"
        path.write_text(cap.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8")
        return cap
