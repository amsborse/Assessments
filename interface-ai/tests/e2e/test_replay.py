"""Deterministic replay against the live demo bank: outcomes, recoveries, failures, drift."""

from collections.abc import Callable

from assessments.config import Settings
from assessments.replay.result import ErrorCode, ReplayStatus
from assessments.service import run_replay

from .conftest import CAPABILITY


async def test_happy_path_returns_typed_outputs_for_any_member(settings: Settings) -> None:
    result = await run_replay(
        settings,
        CAPABILITY,
        {"member_id": "48213"},
        base_url=settings.target_base_url,
        headless=True,
    )

    assert result.status is ReplayStatus.SUCCEEDED
    assert result.outputs == {"savings_balance": "15032.90", "member_name": "Priya Raman"}
    assert result.degraded_locators == []


async def test_invalid_input_is_rejected_before_touching_the_surface(settings: Settings) -> None:
    result = await run_replay(
        settings,
        CAPABILITY,
        {"member_id": "12ab"},
        base_url=settings.target_base_url,
        headless=True,
    )

    assert result.status is ReplayStatus.REJECTED
    assert result.error is not None
    assert result.error.code is ErrorCode.INVALID_INPUT
    assert result.steps_completed == 0


async def test_unknown_member_is_a_business_outcome(settings: Settings) -> None:
    result = await run_replay(
        settings,
        CAPABILITY,
        {"member_id": "99999"},
        base_url=settings.target_base_url,
        headless=True,
    )

    assert result.status is ReplayStatus.BUSINESS_OUTCOME
    assert result.outcome is not None
    assert result.outcome.code == "member_not_found"
    assert result.error is None


async def test_fraud_alert_dialog_is_dismissed_and_reported(settings: Settings) -> None:
    result = await run_replay(
        settings,
        CAPABILITY,
        {"member_id": "55555"},
        base_url=settings.target_base_url,
        headless=True,
    )

    assert result.status is ReplayStatus.BUSINESS_OUTCOME
    assert result.outcome is not None
    assert result.outcome.code == "member_fraud_alert"
    assert result.outputs == {}  # nothing disclosed


async def test_interstitial_is_dismissed(settings: Settings, faults: Callable[..., None]) -> None:
    faults(notice=1)

    result = await run_replay(
        settings,
        CAPABILITY,
        {"member_id": "12345"},
        base_url=settings.target_base_url,
        headless=True,
    )

    assert result.status is ReplayStatus.SUCCEEDED
    assert [(r.state_id, r.response) for r in result.recoveries] == [
        ("maintenance_notice", "dismiss")
    ]


async def test_transient_host_error_is_retried(
    settings: Settings, faults: Callable[..., None]
) -> None:
    faults(unavailable=2)

    result = await run_replay(
        settings,
        CAPABILITY,
        {"member_id": "12345"},
        base_url=settings.target_base_url,
        headless=True,
    )

    assert result.status is ReplayStatus.SUCCEEDED
    assert [r.state_id for r in result.recoveries] == ["host_unavailable"] * 2


async def test_persistent_host_error_exhausts_recovery(
    settings: Settings, faults: Callable[..., None]
) -> None:
    faults(unavailable=10)

    result = await run_replay(
        settings,
        CAPABILITY,
        {"member_id": "12345"},
        base_url=settings.target_base_url,
        headless=True,
    )

    assert result.status is ReplayStatus.FAILED
    assert result.error is not None
    assert result.error.code is ErrorCode.RECOVERY_EXHAUSTED
    assert result.error.step_id == "s06"
    assert result.error.evidence["screenshot"].endswith(".jpg")


async def test_application_error_page_is_a_hard_failure_with_evidence(
    settings: Settings, faults: Callable[..., None]
) -> None:
    faults(server_error=1)

    result = await run_replay(
        settings,
        CAPABILITY,
        {"member_id": "12345"},
        base_url=settings.target_base_url,
        headless=True,
    )

    assert result.status is ReplayStatus.FAILED
    assert result.error is not None
    assert result.error.code is ErrorCode.KNOWN_FAILURE_STATE
    assert result.error.state_id == "application_error"
    assert (settings.data_dir / "runs" / result.run_id / result.error.evidence["snapshot"]).exists()


async def test_session_expiry_restarts_and_completes(
    settings: Settings, faults: Callable[..., None]
) -> None:
    # Regression: after re-login the top window reloads the frameset and Playwright keeps the
    # detached frames in child_frames; frame lookup matched a stale frame, so the sign-on
    # checkpoint could never pass after a restart.
    faults(expire_session=1)

    result = await run_replay(
        settings,
        CAPABILITY,
        {"member_id": "12345"},
        base_url=settings.target_base_url,
        headless=True,
    )

    assert result.status is ReplayStatus.SUCCEEDED, result.error
    assert [(r.state_id, r.response) for r in result.recoveries] == [("session_expired", "restart")]


async def test_other_tenant_variant_succeeds_and_reports_drift(
    settings: Settings, bank_b: str
) -> None:
    result = await run_replay(
        settings, CAPABILITY, {"member_id": "12345"}, base_url=bank_b, headless=True
    )

    assert result.status is ReplayStatus.SUCCEEDED
    assert result.outputs["savings_balance"] == "2418.07"
    degraded = {d.step_id: d.used for d in result.degraded_locators}
    assert degraded["s05"] == "field_name"  # "Member #:" relabelled; form contract unchanged


async def test_evidence_never_contains_secrets_or_raw_pii(settings: Settings) -> None:
    result = await run_replay(
        settings,
        CAPABILITY,
        {"member_id": "12345"},
        base_url=settings.target_base_url,
        headless=True,
    )

    run_dir = settings.data_dir / "runs" / result.run_id
    persisted = "".join(
        p.read_text(encoding="utf-8")
        for p in run_dir.rglob("*")
        if p.suffix in {".json", ".jsonl", ".txt"}
    )
    for needle in ["harbor-demo", "12345", "Jordan Avery", "512-44-9012", "2,418.07", "2418.07"]:
        assert needle not in persisted, needle
