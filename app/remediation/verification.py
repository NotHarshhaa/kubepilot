"""Automated AI Post-Remediation Verification Engine.

Validates whether operational actions (restarts, scale, rollbacks) successfully resolved
the issue by inspecting rollout completion, pod readiness, crash rates, and warning events.
"""

from dataclasses import dataclass, field
from enum import Enum

from app.config.settings import get_settings
from app.tools.kubernetes.tools import apps, core
from app.tools.registry import SafetyCategory, register


class VerificationVerdict(str, Enum):
    VERIFIED_HEALTHY = "VERIFIED_HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED_ROLLOUT = "FAILED_ROLLOUT"
    IN_PROGRESS = "IN_PROGRESS"


@dataclass
class VerificationResult:
    target: str
    namespace: str
    action_type: str
    verdict: VerificationVerdict
    desired_replicas: int
    ready_replicas: int
    updated_replicas: int
    unavailable_replicas: int
    active_crashes: list[str] = field(default_factory=list)
    recent_warnings: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)


def run_post_action_verification(
    target: str,
    namespace: str = "",
    action_type: str = "restart",
) -> VerificationResult:
    """Inspect the live cluster state of a deployment to verify if remediation was successful."""
    ns = namespace or get_settings().default_namespace
    apps_api = apps()
    core_api = core()

    try:
        dep = apps_api.read_namespaced_deployment(name=target, namespace=ns)
    except Exception as exc:
        return VerificationResult(
            target=target,
            namespace=ns,
            action_type=action_type,
            verdict=VerificationVerdict.FAILED_ROLLOUT,
            desired_replicas=0,
            ready_replicas=0,
            updated_replicas=0,
            unavailable_replicas=0,
            details=[f"Could not find deployment '{target}' in '{ns}': {exc}"],
            next_steps=["Verify target deployment name and namespace."],
        )

    spec = dep.spec
    status = dep.status
    desired = spec.replicas or 0
    ready = status.ready_replicas or 0
    updated = status.updated_replicas or 0
    unavailable = status.unavailable_replicas or 0
    observed_gen = status.observed_generation or 0
    gen = dep.metadata.generation or 0

    details = []
    active_crashes = []
    recent_warnings = []
    next_steps = []

    # 1. Rollout progression
    if observed_gen < gen:
        details.append(
            f"Rollout pending: observed generation ({observed_gen}) < spec generation ({gen})."
        )
    elif updated < desired:
        details.append(f"Rollout in progress: {updated}/{desired} replicas updated.")
    elif ready < desired:
        details.append(f"Waiting for ready replicas: {ready}/{desired} pods ready.")
    else:
        details.append(f"All {ready}/{desired} replicas are updated and running.")

    # 2. Check pods associated with this deployment
    try:
        selector = dep.spec.selector.match_labels or {}
        sel_str = ",".join(f"{k}={v}" for k, v in selector.items()) if selector else None
        pods = core_api.list_namespaced_pod(namespace=ns, label_selector=sel_str).items

        for p in pods:
            p_name = p.metadata.name
            c_statuses = p.status.container_statuses or []
            for c in c_statuses:
                restarts = c.restart_count
                waiting = c.state.waiting if c.state else None
                last_term = c.last_state.terminated if c.last_state else None

                if waiting and waiting.reason in (
                    "CrashLoopBackOff",
                    "ImagePullBackOff",
                    "ErrImagePull",
                    "CreateContainerConfigError",
                ):
                    active_crashes.append(f"{p_name}/{c.name} in {waiting.reason}")

                if last_term and last_term.reason == "OOMKilled":
                    active_crashes.append(f"{p_name}/{c.name} terminated by OOMKilled")

                if restarts > 0 and not c.ready:
                    active_crashes.append(f"{p_name}/{c.name} restarting ({restarts} times)")
    except Exception as exc:
        details.append(f"Could not inspect child pods: {exc}")

    # 3. Warning events
    try:
        events = core_api.list_namespaced_event(
            namespace=ns,
            field_selector=f"involvedObject.name={target}",
        ).items
        for ev in events:
            if ev.type == "Warning":
                recent_warnings.append(f"[{ev.reason}] {ev.message}")
    except Exception:
        pass

    # 4. Formulate Verdict
    if active_crashes or (ready == 0 and desired > 0):
        verdict = VerificationVerdict.FAILED_ROLLOUT
        next_steps.append("Rollback to previous stable revision: `rollback_deployment`.")
        next_steps.append("Run deep triage runbook: `run_diagnostic_runbook`.")
    elif unavailable > 0 or updated < desired:
        verdict = VerificationVerdict.DEGRADED
        next_steps.append("Monitor rollout status for completion: `rollout_status`.")
        next_steps.append("Check node capacity if pods are stuck in Pending.")
    else:
        verdict = VerificationVerdict.VERIFIED_HEALTHY
        next_steps.append("Remediation successful! No further action required.")

    return VerificationResult(
        target=target,
        namespace=ns,
        action_type=action_type,
        verdict=verdict,
        desired_replicas=desired,
        ready_replicas=ready,
        updated_replicas=updated,
        unavailable_replicas=unavailable,
        active_crashes=active_crashes,
        recent_warnings=recent_warnings,
        details=details,
        next_steps=next_steps,
    )


def format_verification_report(res: VerificationResult) -> str:
    """Format verification result as an executive markdown report."""
    verdict_badge = {
        VerificationVerdict.VERIFIED_HEALTHY: "🟢 VERIFIED HEALTHY (Success)",
        VerificationVerdict.DEGRADED: "🟡 DEGRADED / IN PROGRESS",
        VerificationVerdict.FAILED_ROLLOUT: "🔴 FAILED ROLLOUT (Action Needed)",
        VerificationVerdict.IN_PROGRESS: "🔵 IN PROGRESS",
    }.get(res.verdict, str(res.verdict.value))

    lines = [
        f"### KubePilot Remediation Verification: {res.namespace}/{res.target}",
        f"- **Remediation Action**: `{res.action_type}`",
        f"- **Verification Verdict**: **{verdict_badge}**",
        (
            f"- **Replica Status**: Ready: `{res.ready_replicas}/{res.desired_replicas}` | "
            f"Updated: `{res.updated_replicas}` | Unavailable: `{res.unavailable_replicas}`"
        ),
        "",
        "#### Verification Findings",
    ]

    for d in res.details:
        lines.append(f"- {d}")

    if res.active_crashes:
        lines.append("\n#### ⚠️ Active Container Crashes:")
        for ac in res.active_crashes:
            lines.append(f"- {ac}")

    if res.recent_warnings:
        lines.append("\n#### Recent Warning Events:")
        for w in res.recent_warnings[:5]:
            lines.append(f"- {w}")

    lines.append("\n#### Recommended Next Steps")
    for idx, step in enumerate(res.next_steps, 1):
        lines.append(f"{idx}. {step}")

    return "\n".join(lines)


@register(SafetyCategory.READ)
def verify_remediation(target: str, namespace: str = "", action_type: str = "restart") -> str:
    """Verify whether a remediation action (restart, scale, rollback) for a deployment was successful.
    Inspects rollout progression, pod readiness, crashing containers, and warning events."""
    res = run_post_action_verification(target=target, namespace=namespace, action_type=action_type)
    return format_verification_report(res)
