"""Entry points shared by the CLI and the HTTP API: run discovery, run replay."""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from pydantic import Field

from assessments.agent.decider import Decider
from assessments.agent.discovery import DiscoveryAgent, DiscoveryResult, DiscoverySpec
from assessments.capability.certification import (
    CertificationReport,
    GoldenSet,
    golden_path,
    report_path,
    score,
)
from assessments.capability.overlay import (
    Overlay,
    OverlayProvenance,
    TenantStore,
    apply_overlay,
)
from assessments.capability.profile import AppProfile, load_profile
from assessments.capability.schema import (
    ActionKind,
    AppRef,
    KnownState,
    Model,
    OutputSpec,
    ParamSpec,
    ReviewStatus,
    SecretRef,
    Target,
)
from assessments.capability.store import CapabilityStore
from assessments.config import Settings
from assessments.replay.engine import ReplayEngine
from assessments.replay.result import ReplayResult
from assessments.safety import GENERIC_IRREVERSIBLE, Policy
from assessments.secrets import resolve_secrets
from assessments.session.runtime import live_session

logger = logging.getLogger(__name__)


class TaskSpec(Model):
    """A discovery request: the contract the calling agent wants, and the goal in words.

    Inputs/outputs are declared up front (the caller knows what the capability should take and
    return); the model's job is to discover *how* to do it on the surface.
    """

    capability_id: str
    title: str
    goal: str = Field(description="May reference inputs as {{name}}.")
    app: AppRef
    start_path: str = "/"
    inputs: dict[str, ParamSpec] = Field(default_factory=dict)
    outputs: dict[str, OutputSpec] = Field(default_factory=dict)
    secrets: dict[str, SecretRef] = Field(default_factory=dict)
    outcomes: list[KnownState] = Field(
        default_factory=list,
        description="Capability-specific states the caller already knows about (e.g. validation).",
    )
    max_steps: int = 30


def load_task(path: Path) -> TaskSpec:
    return TaskSpec.model_validate_json(path.read_text(encoding="utf-8"))


def build_policy(settings: Settings, profile: AppProfile) -> Policy:
    return Policy(
        allowed_hosts=tuple(settings.allowed_hosts),
        allowed_paths=tuple(settings.allowed_paths),
        allowed_actions=frozenset(ActionKind(a) for a in settings.allowed_actions),
        irreversible_patterns=tuple(profile.irreversible_controls) + tuple(GENERIC_IRREVERSIBLE),
    )


async def run_discovery(
    settings: Settings,
    task: TaskSpec,
    examples: dict[str, str],
    decider: Decider,
    *,
    base_url: str,
    headless: bool,
) -> tuple[DiscoveryResult, Path | None]:
    profile = load_profile(settings.catalog_dir, task.app.profile)
    goal = task.goal
    for name, value in examples.items():
        goal = goal.replace(f"{{{{{name}}}}}", value)
    spec = DiscoverySpec(
        goal=goal,
        start_url=urljoin(base_url, task.start_path),
        capability_id=task.capability_id,
        title=task.title,
        app=task.app,
        inputs=task.inputs,
        examples=examples,
        outputs=task.outputs,
        secrets=task.secrets,
        outcomes=task.outcomes,
        max_steps=min(task.max_steps, settings.agent_max_steps),
    )
    async with live_session(
        "discovery",
        task.capability_id,
        runs_dir=settings.data_dir / "runs",
        policy=build_policy(settings, profile),
        redact_labels=profile.redact_labels,
        headless=headless,
    ) as session:
        agent = DiscoveryAgent(
            spec,
            surface=session.surface,
            decider=decider,
            policy=session.surface.policy,
            control=session.control,
            recorder=session.recorder,
            secret_values=resolve_secrets(task.secrets),
            handoff_timeout_s=settings.handoff_timeout_s,
        )
        result = await agent.run()
        saved: Path | None = None
        if result.capability is not None:
            cap = result.capability.model_copy(update={"entry_url": task.start_path})
            cap, saved = CapabilityStore(settings.catalog_dir).save_new_version(cap)
            result.capability = cap
            session.recorder.write_json("capability.json", cap.model_dump(mode="json"))
        session.recorder.write_json(
            "result.json",
            {
                "status": result.status,
                "reason": result.reason,
                "outputs": result.outputs,
                "capability": result.capability.ref if result.capability else None,
                "human_actions": result.human_actions,
                "steps": len(result.steps),
            },
        )
        return result, saved


async def run_replay(
    settings: Settings,
    ref: str,
    params: dict[str, Any],
    *,
    base_url: str | None = None,
    headless: bool,
    allow_irreversible: bool = False,
    tenant: str | None = None,
    use_draft_overlay: bool = False,
    propose_overlay: bool = False,
    assist: Decider | None = None,
) -> ReplayResult:
    """Replay a capability. With `tenant`, use its base URL and its approved overlay (if any)."""
    cap = CapabilityStore(settings.catalog_dir).load(ref)
    profile = load_profile(settings.catalog_dir, cap.app.profile)
    overlay_label: str | None = None
    if tenant is not None:
        tenants = TenantStore(settings.catalog_dir)
        base_url = base_url or tenants.tenant(tenant).base_url
        overlay = tenants.overlay(tenant, cap)
        if overlay is not None and (
            overlay.review.status is ReviewStatus.APPROVED or use_draft_overlay
        ):
            cap = apply_overlay(cap, overlay)
            overlay_label = f"{tenant} ({overlay.review.status})"
    async with live_session(
        "replay",
        cap.ref,
        runs_dir=settings.data_dir / "runs",
        policy=build_policy(settings, profile),
        redact_labels=profile.redact_labels,
        headless=headless,
    ) as session:
        engine = ReplayEngine(
            cap,
            profile,
            surface=session.surface,
            policy=session.surface.policy,
            control=session.control,
            recorder=session.recorder,
            base_url=base_url or settings.target_base_url,
            secret_values=resolve_secrets(cap.secrets),
            allow_irreversible=allow_irreversible,
            handoff_timeout_s=settings.handoff_timeout_s,
            propose_overlay=propose_overlay,
            assist=assist,
        )
        engine.result.tenant, engine.result.overlay = tenant, overlay_label
        result = await engine.run(params)
        session.recorder.write_json("result.json", engine.persisted_result())
        return result


async def propose_overlay(
    settings: Settings,
    ref: str,
    params: dict[str, Any],
    *,
    tenant: str,
    base_url: str | None = None,
    headless: bool = True,
) -> tuple[ReplayResult, Path | None]:
    """Replay on a tenant, and turn any drifting steps into a draft overlay for review."""
    result = await run_replay(
        settings,
        ref,
        params,
        tenant=tenant,
        base_url=base_url,
        headless=headless,
        propose_overlay=True,
    )
    if not result.proposed_targets:
        return result, None
    cap = CapabilityStore(settings.catalog_dir).load(ref)
    tenants = TenantStore(settings.catalog_dir)
    existing = tenants.overlay(tenant, cap)
    targets = {
        **(existing.targets if existing else {}),
        **{k: Target.model_validate(v) for k, v in result.proposed_targets.items()},
    }
    overlay = Overlay(
        tenant=tenant,
        capability=cap.id,
        base_version=cap.version,
        targets=targets,
        provenance=OverlayProvenance(
            proposed_from_run=result.run_id,
            proposed_at=datetime.now(UTC),
            reason="steps resolved only via fallback strategies: "
            + ", ".join(sorted(result.proposed_targets)),
        ),
    )
    return result, tenants.save_overlay(overlay)


async def certify(
    settings: Settings,
    ref: str,
    *,
    runs: int = 5,
    tenant: str | None = None,
    base_url: str | None = None,
    headless: bool = True,
) -> tuple[CertificationReport, Path]:
    """Replay every golden case `runs` times, score confidence, and save the report."""
    cap = CapabilityStore(settings.catalog_dir).load(ref)
    golden = GoldenSet.model_validate_json(
        golden_path(settings.catalog_dir, cap.id).read_text(encoding="utf-8")
    )
    results: dict[str, list[dict[str, Any]]] = {}
    for case in golden.cases:
        for _ in range(runs):
            replay = await run_replay(
                settings, cap.ref, case.params, tenant=tenant, base_url=base_url, headless=headless
            )
            results.setdefault(case.name, []).append(replay.model_dump(mode="json"))
    report = score(cap, golden, results, tenant=tenant)
    path = report_path(settings.catalog_dir, cap)
    path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return report, path
