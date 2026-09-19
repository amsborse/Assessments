"""The seam between "how we perceive/act on a surface" and "the recorded flow".

The agent loop and replay engine only speak this protocol and the surface-neutral schema types
(Target, Condition, Scope). A surface implementation owns everything technology-specific:
how elements are enumerated (DOM / UI Automation / AX tree / OCR), how strategies are resolved,
how to wait, and how to redact before capture.

Implemented: `PlaywrightWebSurface` (modern and legacy web: frames, tables, no ids).
Designed for: a Windows UIA surface (roles/names/automation ids map onto the same strategies)
and a pixel surface (screenshot + OCR + coordinates) as the last resort.
"""

from dataclasses import dataclass, field
from typing import Protocol

from assessments.capability.schema import Condition, Scope, Strategy, Target


@dataclass(frozen=True)
class ElementInfo:
    ref: str
    role: str | None
    name: str
    scope: Scope


@dataclass(frozen=True)
class Observation:
    """What the agent sees: redacted text snapshot + element refs + optional screenshot."""

    text: str
    elements: dict[str, ElementInfo]
    signature: str  # coarse identity of the current screen (frame titles), for loop detection
    url: str
    screenshot_png: bytes | None = None
    dialogs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Resolution:
    count: int
    strategy: Strategy | None  # the strategy that matched uniquely
    rank: int  # index of that strategy in the target's list; >0 means a fallback was used
    tried: list[str] = field(default_factory=list)  # "kind: n matches" per strategy tried


class TargetNotFound(Exception):
    def __init__(self, target: Target, tried: list[str]) -> None:
        super().__init__(f"{target.description}: {', '.join(tried) or 'no strategies'}")
        self.target = target
        self.tried = tried


class Surface(Protocol):
    async def open(self, url: str) -> None: ...

    async def observe(self, *, screenshot: bool = True) -> Observation: ...

    async def target_for(self, ref: str, examples: dict[str, str]) -> Target:
        """Build a verified multi-strategy target for an element the agent chose by ref."""
        ...

    async def resolve(self, target: Target, params: dict[str, str]) -> Resolution: ...

    async def click(self, target: Target, params: dict[str, str]) -> Resolution: ...

    async def fill(self, target: Target, params: dict[str, str], value: str) -> Resolution: ...

    async def select(self, target: Target, params: dict[str, str], option: str) -> Resolution: ...

    async def press(self, key: str) -> None: ...

    async def read(self, target: Target, params: dict[str, str]) -> str: ...

    async def check(self, condition: Condition, params: dict[str, str]) -> bool: ...

    async def settle(self) -> None:
        """Wait for in-flight navigations/loads to finish (bounded)."""
        ...

    async def screenshot(self, *, redacted: bool = True) -> bytes: ...

    def current_url(self) -> str: ...

    def drain_dialogs(self) -> list[str]: ...
