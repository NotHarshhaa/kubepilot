"""Kubernetes investigation tools (read) and controlled operational tools."""

import json
from datetime import UTC, datetime

from kubernetes import client
from kubernetes.config import load_incluster_config, load_kube_config

from app.config.settings import get_settings
from app.tools.registry import SafetyCategory, register

_configured = False

RESTART_ANNOTATION = "kubectl.kubernetes.io/restartedAt"


def _configure() -> None:
    global _configured
    if _configured:
        return
    import os

    settings = get_settings()
    if os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token"):
        load_incluster_config()
    else:
        load_kube_config(
            config_file=os.path.expanduser(settings.kubeconfig),
            context=settings.kube_context,
        )
    _configured = True


def core() -> client.CoreV1Api:
    _configure()
    return client.CoreV1Api()


def apps() -> client.AppsV1Api:
    _configure()
    return client.AppsV1Api()


def _namespace(namespace: str = "") -> str:
    return namespace or get_settings().default_namespace


def _age(timestamp: datetime | None) -> str:
    if timestamp is None:
        return "unknown"
    delta = datetime.now(UTC) - (timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=UTC))
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes}m"
    if minutes < 60 * 24:
        return f"{minutes // 60}h"
    return f"{minutes // (60 * 24)}d"


def _container_state(status) -> str:
    state = status.state
    if state is None:
        return "unknown"
    if state.waiting is not None:
        return f"waiting({state.waiting.reason})"
    if state.running is not None:
        return "running"
    return "terminated"


def _format_pod(pod) -> str:
    statuses = pod.status.container_statuses or []
    restarts = sum(s.restart_count for s in statuses)
    ready = sum(1 for s in statuses if s.ready)
    containers = ", ".join(f"{s.name}:{_container_state(s)}" for s in statuses)
    return (
        f"  {pod.metadata.name}  phase={pod.status.phase} ready={ready}/{len(statuses)} "
        f"restarts={restarts} age={_age(pod.metadata.creation_timestamp)} "
        f"node={pod.spec.node_name}"
        f"{(' containers=' + containers) if containers else ''}"
    )


@register(SafetyCategory.READ)
def get_pods(namespace: str = "", label_selector: str = "") -> str:
    """List pods with status, restart counts, and age. Use namespace='ALL' for every
    namespace; omit namespace for the default one."""
    ns = _namespace(namespace)
    try:
        if ns.upper() == "ALL":
            pods = core().list_pod_for_all_namespaces(label_selector=label_selector or None).items
            shown = "all namespaces"
        else:
            pods = core().list_namespaced_pod(ns, label_selector=label_selector or None).items
            shown = f"namespace '{ns}'"
        if not pods:
            return f"No pods found in {shown}."
        lines = [f"Pods in {shown}:"]
        lines.extend(_format_pod(p) for p in pods)
        return "\n".join(lines)
    except Exception as exc:
        return f"Error listing pods in '{ns}': {exc}"


@register(SafetyCategory.READ)
def get_deployments(namespace: str = "") -> str:
    """List deployments in a namespace with replica readiness and age."""
    ns = _namespace(namespace)
    try:
        items = apps().list_namespaced_deployment(ns).items
        if not items:
            return f"No deployments found in namespace '{ns}'."
        lines = [f"Deployments in namespace '{ns}':"]
        for dep in items:
            status = dep.status
            lines.append(
                f"  {dep.metadata.name}  ready={status.ready_replicas or 0}/{dep.spec.replicas} "
                f"updated={status.updated_replicas or 0} available={status.available_replicas or 0} "
                f"age={_age(dep.metadata.creation_timestamp)}"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error listing deployments in '{ns}': {exc}"


@register(SafetyCategory.READ)
def get_services(namespace: str = "") -> str:
    """List services in a namespace with type, cluster IP, and ports."""
    ns = _namespace(namespace)
    try:
        items = core().list_namespaced_service(ns).items
        if not items:
            return f"No services found in namespace '{ns}'."
        lines = [f"Services in namespace '{ns}':"]
        for svc in items:
            ports = ", ".join(
                f"{p.port}->{p.target_port}/{p.protocol or 'TCP'}" for p in (svc.spec.ports or [])
            )
            lines.append(
                f"  {svc.metadata.name}  type={svc.spec.type} cluster_ip={svc.spec.cluster_ip} "
                f"ports=[{ports}] age={_age(svc.metadata.creation_timestamp)}"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error listing services in '{ns}': {exc}"


@register(SafetyCategory.READ)
def get_events(namespace: str = "", involved_object: str = "") -> str:
    """List recent Kubernetes events in a namespace, optionally filtered to one involved
    object (e.g. a pod name), newest first."""
    ns = _namespace(namespace)
    try:
        field_selector = f"involvedObject.name={involved_object}" if involved_object else None
        events = core().list_namespaced_event(ns, field_selector=field_selector).items
        def _event_time(event):
            return event.last_timestamp or event.event_time or event.first_timestamp

        events.sort(
            key=lambda e: _event_time(e).timestamp() if _event_time(e) else 0.0,
            reverse=True,
        )
        if not events:
            return f"No events found in namespace '{ns}'."
        lines = [f"Recent events in namespace '{ns}' (newest first):"]
        for event in events[:30]:
            when = (event.last_timestamp or event.first_timestamp or event.event_time or "unknown time")
            lines.append(
                f"  [{when}] {event.type} {event.reason} x{event.count or 1} "
                f"{event.involved_object.kind}/{event.involved_object.name}: {event.message}"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error listing events in '{ns}': {exc}"


@register(SafetyCategory.READ)
def get_logs(pod: str, namespace: str = "", container: str = "", tail_lines: int = 100) -> str:
    """Read recent container logs from a pod (last N lines). Omit container for single-container pods."""
    ns = _namespace(namespace)
    try:
        logs = core().read_namespaced_pod_log(
            name=pod,
            namespace=ns,
            container=container or None,
            tail_lines=tail_lines,
            timestamps=True,
        )
        if not logs.strip():
            return f"Pod '{pod}' in '{ns}' has no log output (container={container or 'default'})."
        return f"Last {tail_lines} log lines from pod '{pod}' in '{ns}':\n{logs}"
    except Exception as exc:
        return f"Error reading logs from pod '{pod}' in '{ns}': {exc}"


@register(SafetyCategory.READ)
def describe_resource(kind: str, name: str, namespace: str = "") -> str:
    """Full describe of a resource (pod, deployment, service, statefulset, daemonset, job,
    node): its spec/status plus its recent events."""
    ns = _namespace(namespace)
    kind = kind.lower()
    try:
        if kind == "pod":
            obj = core().read_namespaced_pod(name, ns)
        elif kind == "deployment":
            obj = apps().read_namespaced_deployment(name, ns)
        elif kind == "service":
            obj = core().read_namespaced_service(name, ns)
        elif kind == "statefulset":
            obj = apps().read_namespaced_stateful_set(name, ns)
        elif kind == "daemonset":
            obj = apps().read_namespaced_daemon_set(name, ns)
        elif kind == "job":
            obj = client.BatchV1Api().read_namespaced_job(name, ns)
        elif kind == "node":
            obj = core().read_node(name)
        else:
            return (
                f"Unsupported kind '{kind}'. Supported: pod, deployment, service, "
                "statefulset, daemonset, job, node."
            )
        data = json.dumps(client.ApiClient().sanitize_for_serialization(obj), indent=2, default=str)
        if len(data) > 12000:
            data = data[:12000] + "\n... (truncated)"
        try:
            object_events = get_events(namespace=ns, involved_object=name)
        except Exception:
            object_events = "(events unavailable)"
        return f"--- {kind}/{name} in namespace '{ns}' ---\n{data}\n\n{object_events}"
    except Exception as exc:
        return f"Error describing {kind}/{name} in '{ns}': {exc}"


@register(SafetyCategory.READ)
def rollout_status(deployment: str, namespace: str = "") -> str:
    """Check rollout status of a deployment: replica counts, conditions, and the health
    of its pods."""
    ns = _namespace(namespace)
    try:
        dep = apps().read_namespaced_deployment(deployment, ns)
        status = dep.status
        desired = dep.spec.replicas or 0
        lines = [f"Rollout status for deployment '{deployment}' in '{ns}':"]
        lines.append(
            f"  replicas: desired={desired} updated={status.updated_replicas or 0} "
            f"ready={status.ready_replicas or 0} available={status.available_replicas or 0} "
            f"unavailable={status.unavailable_replicas or 0}"
        )
        for condition in status.conditions or []:
            lines.append(
                f"  condition {condition.type}: {condition.status} ({condition.reason}) "
                f"{condition.message or ''}".rstrip()
            )
        selector_obj = dep.spec.selector.match_labels if dep.spec.selector else None
        selector = ",".join(f"{k}={v}" for k, v in (selector_obj or {}).items())
        if selector:
            pods = core().list_namespaced_pod(ns, label_selector=selector).items
            lines.append("  pods:")
            lines.extend(_format_pod(p) for p in pods)
        complete = (status.updated_replicas or 0) >= desired and (status.available_replicas or 0) >= desired
        lines.append(
            "Rollout complete."
            if complete
            else "Rollout not complete — inspect the pods above and recent events for the cause."
        )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error checking rollout of '{deployment}' in '{ns}': {exc}"


@register(SafetyCategory.READ)
def get_resource_usage(namespace: str = "", pod: str = "") -> str:
    """Get current CPU/memory usage per pod from the metrics API (requires metrics-server).
    Omit pod for the whole namespace."""
    ns = _namespace(namespace)
    try:
        custom = client.CustomObjectsApi()
        items = custom.list_namespaced_custom_object(
            group="metrics.k8s.io", version="v1beta1", namespace=ns, plural="pods"
        )["items"]
        if pod:
            items = [item for item in items if item["metadata"]["name"] == pod]
        if not items:
            return f"No metrics found for '{pod or ns}' (is metrics-server installed?)."
        lines = [f"Resource usage in namespace '{ns}':"]
        for item in items:
            for container in item.get("containers", []):
                usage = container.get("usage", {})
                lines.append(
                    f"  {item['metadata']['name']}/{container['name']}: "
                    f"cpu={usage.get('cpu', '?')} memory={usage.get('memory', '?')}"
                )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error fetching resource usage in '{ns}' (metrics-server may be missing): {exc}"


@register(SafetyCategory.OPERATIONAL)
def restart_deployment(deployment: str, namespace: str = "") -> str:
    """Perform a rolling restart of a deployment (like kubectl rollout restart). Requires approval."""
    ns = _namespace(namespace)
    try:
        now = datetime.now(UTC).isoformat()
        body = {"spec": {"template": {"metadata": {"annotations": {RESTART_ANNOTATION: now}}}}}
        apps().patch_namespaced_deployment(name=deployment, namespace=ns, body=body)
        return f"Rolling restart triggered for deployment '{deployment}' in '{ns}'."
    except Exception as exc:
        return f"Error restarting deployment '{deployment}' in '{ns}': {exc}"


@register(SafetyCategory.OPERATIONAL)
def scale_deployment(deployment: str, replicas: int, namespace: str = "") -> str:
    """Scale a deployment to a specific replica count. Requires approval."""
    ns = _namespace(namespace)
    try:
        apps().patch_namespaced_deployment_scale(
            name=deployment,
            namespace=ns,
            body={"spec": {"replicas": replicas}},
        )
        return f"Deployment '{deployment}' in '{ns}' scaled to {replicas} replicas."
    except Exception as exc:
        return f"Error scaling deployment '{deployment}' in '{ns}': {exc}"


@register(SafetyCategory.OPERATIONAL)
def rollback_deployment(deployment: str, namespace: str = "") -> str:
    """Roll a deployment back to its previous revision (like kubectl rollout undo). Requires approval."""
    ns = _namespace(namespace)
    try:
        apps_api = apps()
        dep = apps_api.read_namespaced_deployment(deployment, ns)
        replica_sets = apps_api.list_namespaced_replica_set(ns).items
        owned = [
            rs
            for rs in replica_sets
            if any(
                ref.kind == "Deployment" and ref.name == deployment
                for ref in (rs.metadata.owner_references or [])
            )
        ]

        def revision(rs) -> int:
            return int((rs.metadata.annotations or {}).get("deployment.kubernetes.io/revision", "0"))

        owned.sort(key=revision)
        if len(owned) < 2:
            return f"No previous revision found for deployment '{deployment}' — nothing to roll back to."
        previous = owned[-2]
        dep.spec.template = apps_api.read_namespaced_replica_set(previous.metadata.name, ns).spec.template
        apps_api.patch_namespaced_deployment(name=deployment, namespace=ns, body=dep)
        return (
            f"Rolled back '{deployment}' in '{ns}' to revision {revision(previous)} "
            f"(ReplicaSet {previous.metadata.name})."
        )
    except Exception as exc:
        return f"Error rolling back deployment '{deployment}' in '{ns}': {exc}"
