"""Per-run evidence: structured event log, redacted screenshots/snapshots, summaries.

Layout: <runs_dir>/<run_id>/{events.jsonl, run.json, screenshots/*.png, snapshots/*.txt}
Everything written here passes through redaction first. Playwright traces are deliberately not
recorded: they embed unredacted DOM and network bodies.
"""

import json
import logging
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from assessments.logging import run_id_var
from assessments.redaction import redact_obj, redact_text, redact_values

logger = logging.getLogger(__name__)


def new_run_id(kind: str) -> str:
    return f"{kind}_{datetime.now(UTC):%Y%m%dT%H%M%S}_{uuid.uuid4().hex[:6]}"


class RunRecorder:
    def __init__(self, runs_dir: Path, kind: str, run_id: str | None = None) -> None:
        self.run_id = run_id or new_run_id(kind)
        self.kind = kind
        self.dir = runs_dir / self.run_id
        (self.dir / "screenshots").mkdir(parents=True, exist_ok=True)
        (self.dir / "snapshots").mkdir(exist_ok=True)
        self._events = (self.dir / "events.jsonl").open("a", encoding="utf-8")
        self._seq = 0
        self.recent: deque[dict[str, Any]] = deque(maxlen=400)  # for the live activity feed
        # Exact sensitive values (secrets, PII inputs). Shared with the surface, which masks
        # them in the page before screenshots are taken.
        self.sensitive: set[str] = set()
        self._token = run_id_var.set(self.run_id)

    def add_sensitive(self, *values: str) -> None:
        """Exact values (secrets, PII params) to scrub from everything this run writes."""
        self.sensitive.update(v for v in values if v)

    def scrub(self, text: str) -> str:
        return redact_text(redact_values(text, self.sensitive))

    def event(self, type_: str, **data: Any) -> None:
        self._seq += 1
        record = {
            "seq": self._seq,
            "ts": datetime.now(UTC).isoformat(),
            "type": type_,
            **redact_obj(data, self.sensitive),
        }
        self._events.write(json.dumps(record, default=str) + "\n")
        self._events.flush()
        self.recent.append(record)
        logger.info(type_, extra={"event": record})

    def screenshot(self, name: str, jpeg: bytes) -> str:
        rel = f"screenshots/{self._seq:03d}-{name}.jpg"
        (self.dir / rel).write_bytes(jpeg)
        return rel

    def snapshot(self, name: str, text: str) -> str:
        rel = f"snapshots/{self._seq:03d}-{name}.txt"
        (self.dir / rel).write_text(self.scrub(text), encoding="utf-8")
        return rel

    def write_json(self, name: str, obj: Any) -> Path:
        path = self.dir / name
        path.write_text(
            json.dumps(redact_obj(obj, self.sensitive), indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        return path

    def close(self) -> None:
        self._events.close()
        run_id_var.reset(self._token)
