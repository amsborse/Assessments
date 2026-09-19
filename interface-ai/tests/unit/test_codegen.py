from typing import Any

from assessments.capability.codegen import generate_test
from assessments.capability.schema import Capability

from .test_schema import capability


class _Frame:
    def __init__(self, name: str, children: list["_Frame"] | None = None) -> None:
        self.name = name
        self._children = children or []

    @property
    def child_frames(self) -> list["_Frame"]:
        return self._children

    def is_detached(self) -> bool:
        return False


class _LateFrameset(_Frame):
    """A frameset page whose child frames attach only after a few polls (a real navigation)."""

    def __init__(self, polls_before_attached: int) -> None:
        super().__init__("")
        self._polls = polls_before_attached
        self._banner = _Frame("banner")

    @property
    def child_frames(self) -> list[_Frame]:
        self._polls -= 1
        return [self._banner] if self._polls < 0 else []


class _Page:
    def __init__(self, main_frame: _Frame) -> None:
        self.main_frame = main_frame


def _flow_class() -> Any:
    source = generate_test(Capability.model_validate(capability()), {"member_id": "12345"})
    namespace: dict[str, Any] = {"__name__": "generated"}
    exec(compile(source, "generated.py", "exec"), namespace)  # noqa: S102 - our own output
    return next(v for k, v in namespace.items() if k.endswith("Flow"))


def test_generated_frame_lookup_waits_for_frames_to_attach() -> None:
    # Regression: right after a click that loads a frameset, the child frames may not exist yet;
    # the generated page object failed with StopIteration instead of waiting.
    page = _Page(_LateFrameset(polls_before_attached=3))

    frame = _flow_class()(page).frame("banner")

    assert frame.name == "banner"
