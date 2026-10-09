"""Automated Diagnostic Runbooks for Kubernetes Workloads.

Provides specialized multi-signal deep triage workflows for:
- CrashLoopBackOff (exit codes, crash logs, probe failures, failed mounts)
- OOMKilled (memory limits vs requests, cgroup kill, Prometheus usage trends)
- ImagePullBackOff / ErrImagePull (tag typos, 401/403 auth, rate limits)
- Pending / Unschedulable Pods (resource exhaustion, taints/tolerations, affinity, PVCs)
"""

from dataclasses import dataclass, field
from typing import Any

from kubernetes import client

from app.config.settings import get_settings
from app.tools.kubernetes.tools import core
from app.tools.registry import SafetyCategory, register


@dataclass
class DiagnosticFinding:
    symptom: str
    runbook: str
    target: str
    root_cause: str
    severity: str
    evidence: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


def _get_pod_events(core_api: client.CoreV1Api, namespace: str, pod_name: str) -> list[Any]:
    try:
        events = core_api.list_namespaced_event(
            namespace,
            field_selector=f"involvedObject.name={pod_name},involvedObject.kind=Pod",
        ).items
        return events
    except Exception:
        return []


def _get_pod_previous_logs(
    core_api: client.CoreV1Api, namespace: str, pod_name: str, container_name: str, tail: int = 40
) -> str:
    try:
        return core_api.read_namespaced_pod_log(
            name=pod_name,
            namespace=namespace,
            container=container_name,
            previous=True,
            tail_lines=tail,
        )
    except Exception:
        # Try current logs if previous not available
        try:
            return core_api.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container=container_name,
                previous=False,
                tail_lines=tail,
            )
        except Exception as exc:
            return f"(Could not retrieve logs: {exc})"


def diagnose_crashloop(pod: Any, namespace: str) -> DiagnosticFinding:
    """Runbook for CrashLoopBackOff triage."""
    core_api = core()
    pod_name = pod.metadata.name
    statuses = pod.status.container_statuses or []
    events = _get_pod_events(core_api, namespace, pod_name)

    evidence = []
    root_causes = []
    recommendations = []
    severity = "P1 - Critical"

    # Analyze container exit states
    for c in statuses:
        restarts = c.restart_count
        waiting_reason = c.state.waiting.reason if c.state and c.state.waiting else ""
        last_term = c.last_state.terminated if c.last_state else None

        if waiting_reason == "CrashLoopBackOff" or restarts > 0 or last_term:
            exit_code = last_term.exit_code if last_term else "?"
            term_reason = last_term.reason if last_term else "Unknown"

            evidence.append(
                f"Container '{c.name}': {restarts} restarts, last exit code {exit_code} "
                f"(reason: {term_reason})"
            )

            # Retrieve crash logs
            logs = _get_pod_previous_logs(core_api, namespace, pod_name, c.name, tail=25)
            log_preview = "\n    ".join(logs.strip().splitlines()[-8:]) if logs else "No log output."
            evidence.append(f"Crash log tail for container '{c.name}':\n    {log_preview}")

            # Exit code interpretation
            if exit_code == 137 or term_reason == "OOMKilled":
                root_causes.append(
                    f"Container '{c.name}' was killed by OOM (Out Of Memory, exit 137). Limit exceeded."
                )
                recommendations.append(f"Increase memory limit for container '{c.name}' in deployment.")
            elif exit_code == 1:
                root_causes.append(
                    f"Container '{c.name}' process exited with exit code 1 "
                    f"(general unhandled application error)."
                )
                recommendations.append(
                    f"Review application logs for '{c.name}' for unhandled exceptions or connection errors."
                )
            elif exit_code == 127:
                root_causes.append(
                    f"Container '{c.name}' command not found (exit code 127). Invalid entrypoint or binary."
                )
                recommendations.append("Verify container image PATH and entrypoint/command configuration.")
            elif exit_code == 126:
                root_causes.append(f"Container '{c.name}' executable permission denied (exit code 126).")
                recommendations.append("Ensure executable binary has execute (+x) permissions inside image.")
            elif exit_code == 143:
                root_causes.append(
                    f"Container '{c.name}' terminated by SIGTERM (exit code 143), "
                    f"likely failing health probes."
                )
            else:
                root_causes.append(f"Container '{c.name}' terminated repeatedly with exit code {exit_code}.")

    # Check probe failure events & volume mounts
    for ev in events:
        if ev.type == "Warning":
            msg = ev.message or ""
            reason = ev.reason or ""
            if "probe failed" in msg.lower() or "Unhealthy" in reason:
                evidence.append(f"Event: [{reason}] {msg}")
                if "Liveness probe" in msg:
                    root_causes.append(f"Liveness probe failure killed the container: {msg}")
                    recommendations.append(
                        "Increase livenessProbe initialDelaySeconds, periodSeconds, or timeoutSeconds."
                    )
            elif "FailedMount" in reason or "MountVolume" in reason:
                evidence.append(f"Event: [{reason}] {msg}")
                root_causes.append(f"Volume mount failure preventing container start: {msg}")
                recommendations.append("Check ConfigMap or Secret referenced by volume mounts exists.")

    if not recommendations:
        recommendations.append(
            "Inspect application configuration and environment variables for invalid credentials or URLs."
        )

    return DiagnosticFinding(
        symptom=f"Pod {pod_name} is in CrashLoopBackOff",
        runbook="CrashLoopBackOff",
        target=f"{namespace}/{pod_name}",
        root_cause="; ".join(root_causes) if root_causes else "Container application crashing upon startup",
        severity=severity,
        evidence=evidence,
        recommendations=recommendations,
    )


def diagnose_oomkilled(pod: Any, namespace: str) -> DiagnosticFinding:
    """Runbook for OOMKilled triage."""
    pod_name = pod.metadata.name
    statuses = pod.status.container_statuses or []
    spec_containers = pod.spec.containers or []
    container_specs = {c.name: c for c in spec_containers}

    evidence = []
    root_causes = []
    recommendations = []

    for c in statuses:
        last_term = c.last_state.terminated if c.last_state else None
        current_term = c.state.terminated if c.state else None
        term = current_term or last_term

        if (term and (term.reason == "OOMKilled" or term.exit_code == 137)) or (c.restart_count > 0):
            c_spec = container_specs.get(c.name)
            resources = c_spec.resources if c_spec else None
            limits = resources.limits if resources and resources.limits else {}
            requests = resources.requests if resources and resources.requests else {}

            mem_limit = limits.get("memory", "none (unlimited)")
            mem_req = requests.get("memory", "none")

            evidence.append(
                f"Container '{c.name}' was OOMKilled with exit code 137. "
                f"Configured memory limit: {mem_limit}, request: {mem_req}."
            )
            root_causes.append(
                f"Container '{c.name}' exceeded its cgroup memory limit ({mem_limit}) "
                f"and was terminated by Linux kernel OOM-killer."
            )
            recommendations.append(
                f"Increase memory limit for '{c.name}' (current: {mem_limit}). "
                f"Recommended: double the current limit or investigate heap/buffer leak."
            )

    if not root_causes:
        root_causes.append(f"Pod {pod_name} experienced memory pressure eviction or memory starvation.")
        recommendations.append("Review node memory capacity and container memory requests/limits.")

    return DiagnosticFinding(
        symptom=f"Pod {pod_name} terminated with OOMKilled",
        runbook="OOMKilled",
        target=f"{namespace}/{pod_name}",
        root_cause="; ".join(root_causes),
        severity="P1 - Critical",
        evidence=evidence,
        recommendations=recommendations,
    )


def diagnose_image_pull(pod: Any, namespace: str) -> DiagnosticFinding:
    """Runbook for ImagePullBackOff / ErrImagePull triage."""
    core_api = core()
    pod_name = pod.metadata.name
    events = _get_pod_events(core_api, namespace, pod_name)
    statuses = pod.status.container_statuses or []

    evidence = []
    root_causes = []
    recommendations = []

    for c in statuses:
        waiting = c.state.waiting if c.state else None
        if waiting and waiting.reason in ("ImagePullBackOff", "ErrImagePull"):
            evidence.append(
                f"Container '{c.name}' in state {waiting.reason}: {waiting.message or 'Waiting on image'}"
            )

    # Inspect events for exact error strings
    image_names = [c.image for c in (pod.spec.containers or [])]
    evidence.append(f"Target Images: {', '.join(image_names)}")

    pull_secrets = [s.name for s in (pod.spec.image_pull_secrets or [])]
    evidence.append(f"Configured imagePullSecrets: {pull_secrets or 'None'}")

    for ev in events:
        if ev.reason in ("Failed", "ErrImagePull", "ImagePullBackOff"):
            msg = ev.message or ""
            evidence.append(f"Event [{ev.reason}]: {msg}")
            if "not found" in msg.lower() or "manifest unknown" in msg.lower():
                root_causes.append(f"Image tag or repository does not exist in registry: {msg}")
                recommendations.append(
                    "Check image repository name and tag spelling. Verify image was pushed to registry."
                )
            elif "unauthorized" in msg.lower() or "authentication required" in msg.lower():
                root_causes.append("Private container registry authentication failure (401/403).")
                recommendations.append(
                    "Configure imagePullSecrets with valid docker-registry credentials, "
                    "or update ServiceAccount."
                )
            elif "toomanyrequests" in msg.lower() or "rate limit" in msg.lower():
                root_causes.append(
                    "Container registry rate limit exceeded (e.g., Docker Hub anonymous pull limit)."
                )
                recommendations.append(
                    "Add authenticated credentials or switch to internal mirror to avoid rate limits."
                )
            elif "i/o timeout" in msg.lower() or "no such host" in msg.lower():
                root_causes.append(f"Network connectivity/DNS failure resolving container registry: {msg}")
                recommendations.append(
                    "Check cluster egress network policies, NAT gateways, and CoreDNS health."
                )

    if not root_causes:
        root_causes.append(f"Unable to pull container image for pod '{pod_name}'.")
        recommendations.append("Verify image repository path, tag, and registry pull permissions.")

    return DiagnosticFinding(
        symptom=f"Pod {pod_name} failed to pull image",
        runbook="ImagePullBackOff",
        target=f"{namespace}/{pod_name}",
        root_cause="; ".join(root_causes),
        severity="P2 - Major",
        evidence=evidence,
        recommendations=recommendations,
    )


def diagnose_pending_pod(pod: Any, namespace: str) -> DiagnosticFinding:
    """Runbook for Pending / Unschedulable Pod triage."""
    core_api = core()
    pod_name = pod.metadata.name
    events = _get_pod_events(core_api, namespace, pod_name)

    evidence = []
    root_causes = []
    recommendations = []

    # Container resource requests
    total_cpu_req = []
    total_mem_req = []
    for c in pod.spec.containers or []:
        res = c.resources
        if res and res.requests:
            if "cpu" in res.requests:
                total_cpu_req.append(f"{c.name}:{res.requests['cpu']}")
            if "memory" in res.requests:
                total_mem_req.append(f"{c.name}:{res.requests['memory']}")

    if total_cpu_req or total_mem_req:
        evidence.append(
            f"Resource requests: CPU=[{', '.join(total_cpu_req)}], Memory=[{', '.join(total_mem_req)}]"
        )

    # Check node selector / tolerations
    if pod.spec.node_selector:
        evidence.append(f"NodeSelector: {pod.spec.node_selector}")
    if pod.spec.tolerations:
        tolerations_summary = [t.key for t in pod.spec.tolerations if t.key]
        evidence.append(f"Tolerations keys: {tolerations_summary}")

    # Inspect scheduling events
    for ev in events:
        if ev.reason == "FailedScheduling":
            msg = ev.message or ""
            evidence.append(f"FailedScheduling Event: {msg}")
            if "insufficient cpu" in msg.lower():
                root_causes.append("Insufficient CPU capacity on cluster nodes to satisfy pod requests.")
                recommendations.append("Scale worker node group or reduce CPU requests in pod spec.")
            elif "insufficient memory" in msg.lower():
                root_causes.append("Insufficient memory capacity on cluster nodes to satisfy pod requests.")
                recommendations.append("Scale worker node group or reduce memory requests in pod spec.")
            elif "didn't match pod's node affinity/selector" in msg.lower():
                root_causes.append("No nodes match the required nodeSelector or node affinity rules.")
                recommendations.append("Verify node labels match the pod's nodeSelector and nodeAffinity.")
            elif "had untolerated taint" in msg.lower():
                root_causes.append(f"Cluster nodes possess taints that the pod does not tolerate: {msg}")
                recommendations.append(
                    "Add matching toleration to pod spec or remove unwanted taint from node."
                )
            elif "unbound immediate persistentvolumeclaims" in msg.lower():
                root_causes.append("Pod requires PersistentVolumeClaim that is currently unbound/pending.")
                recommendations.append(
                    "Check PVC status (kubectl get pvc) and verify StorageClass provisioner."
                )

    # Check PVC bindings
    for vol in pod.spec.volumes or []:
        if vol.persistent_volume_claim:
            pvc_name = vol.persistent_volume_claim.claim_name
            try:
                pvc = core_api.read_namespaced_persistent_volume_claim(pvc_name, namespace)
                if pvc.status.phase != "Bound":
                    evidence.append(f"Referenced PVC '{pvc_name}' status is {pvc.status.phase}.")
                    root_causes.append(f"Storage dependency PVC '{pvc_name}' is {pvc.status.phase}.")
                    recommendations.append(
                        f"Investigate why PVC '{pvc_name}' is not Bound (storage quota or missing PV)."
                    )
            except Exception:
                pass

    if not root_causes:
        root_causes.append(
            "Pod is unschedulable due to cluster resource constraints or scheduling predicates."
        )
        recommendations.append("Inspect node capacity, taints, and scheduling queue.")

    return DiagnosticFinding(
        symptom=f"Pod {pod_name} is unschedulable in Pending state",
        runbook="PendingPod",
        target=f"{namespace}/{pod_name}",
        root_cause="; ".join(root_causes),
        severity="P2 - Major",
        evidence=evidence,
        recommendations=recommendations,
    )


def format_finding(finding: DiagnosticFinding) -> str:
    """Format a DiagnosticFinding into an executive markdown diagnostic report."""
    lines = [
        f"### Diagnostic Report: {finding.target}",
        f"- **Runbook**: `{finding.runbook}`",
        f"- **Severity**: {finding.severity}",
        f"- **Symptom**: {finding.symptom}",
        "",
        "#### Root Cause Analysis",
        f"> {finding.root_cause}",
        "",
        "#### Key Evidence",
    ]
    for ev in finding.evidence:
        lines.append(f"- {ev}")

    lines.append("")
    lines.append("#### Recommended Actions")
    for idx, rec in enumerate(finding.recommendations, 1):
        lines.append(f"{idx}. {rec}")

    return "\n".join(lines)


def auto_detect_and_run(pod: Any, namespace: str, runbook_type: str = "auto") -> DiagnosticFinding:
    """Select the best runbook based on pod symptoms or forced type."""
    if runbook_type.lower() == "crashloop":
        return diagnose_crashloop(pod, namespace)
    if runbook_type.lower() == "oom":
        return diagnose_oomkilled(pod, namespace)
    if runbook_type.lower() in ("imagepull", "image"):
        return diagnose_image_pull(pod, namespace)
    if runbook_type.lower() in ("pending", "sched"):
        return diagnose_pending_pod(pod, namespace)

    # Auto-detection
    phase = pod.status.phase or ""
    if phase == "Pending":
        return diagnose_pending_pod(pod, namespace)

    statuses = pod.status.container_statuses or []
    for c in statuses:
        if c.state and c.state.waiting:
            reason = c.state.waiting.reason or ""
            if reason in ("ImagePullBackOff", "ErrImagePull"):
                return diagnose_image_pull(pod, namespace)
            if reason == "CrashLoopBackOff":
                return diagnose_crashloop(pod, namespace)

        if c.last_state and c.last_state.terminated:
            term_reason = c.last_state.terminated.reason or ""
            exit_code = c.last_state.terminated.exit_code
            if term_reason == "OOMKilled" or exit_code == 137:
                return diagnose_oomkilled(pod, namespace)
            if exit_code != 0:
                return diagnose_crashloop(pod, namespace)

        if c.restart_count > 0:
            return diagnose_crashloop(pod, namespace)

    # Fallback to CrashLoop
    return diagnose_crashloop(pod, namespace)


@register(SafetyCategory.READ)
def run_diagnostic_runbook(
    pod_name: str = "",
    namespace: str = "",
    runbook_type: str = "auto",
) -> str:
    """Run an automated diagnostic runbook for Kubernetes workload failures.
    Supported runbooks: 'crashloop', 'oom', 'imagepull', 'pending', or 'auto'.
    If pod_name is omitted, scans the namespace for unhealthy pods and diagnoses them."""
    ns = namespace or get_settings().default_namespace
    core_api = core()

    try:
        if pod_name:
            pod = core_api.read_namespaced_pod(name=pod_name, namespace=ns)
            finding = auto_detect_and_run(pod, ns, runbook_type=runbook_type)
            return format_finding(finding)

        # No specific pod provided: triage problematic pods in namespace
        if ns.upper() == "ALL":
            pods = core_api.list_pod_for_all_namespaces().items
        else:
            pods = core_api.list_namespaced_pod(namespace=ns).items

        unhealthy_pods = []
        for p in pods:
            phase = p.status.phase
            statuses = p.status.container_statuses or []
            restarts = sum(s.restart_count for s in statuses)
            has_issues = (
                phase not in ("Running", "Succeeded")
                or restarts > 0
                or any(
                    s.state
                    and s.state.waiting
                    and s.state.waiting.reason
                    in ("CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError")
                    for s in statuses
                )
                or any(
                    s.last_state and s.last_state.terminated and s.last_state.terminated.reason == "OOMKilled"
                    for s in statuses
                )
            )
            if has_issues:
                unhealthy_pods.append(p)

        if not unhealthy_pods:
            scope = "all namespaces" if ns.upper() == "ALL" else f"namespace '{ns}'"
            return (
                f"No unhealthy pods detected in {scope}. "
                f"All {len(pods)} pod(s) are running normally."
            )

        reports = [
            "## KubePilot Automated Diagnostic Runbook Suite",
            f"Detected {len(unhealthy_pods)} problematic pod(s). Running automated triage...\n",
        ]
        for p in unhealthy_pods[:5]:  # Diagnose top 5
            pod_ns = p.metadata.namespace
            finding = auto_detect_and_run(p, pod_ns, runbook_type=runbook_type)
            reports.append(format_finding(finding))
            reports.append("\n---\n")

        if len(unhealthy_pods) > 5:
            reports.append(f"... and {len(unhealthy_pods) - 5} more unhealthy pod(s).")

        return "\n".join(reports)
    except Exception as exc:
        return f"Error executing diagnostic runbook in namespace '{ns}': {exc}"
