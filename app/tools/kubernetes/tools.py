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


def networking() -> client.NetworkingV1Api:
    _configure()
    return client.NetworkingV1Api()


def autoscaling() -> client.AutoscalingV2Api:
    _configure()
    return client.AutoscalingV2Api()


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
            when = event.last_timestamp or event.first_timestamp or event.event_time or "unknown time"
            lines.append(
                f"  [{when}] {event.type} {event.reason} x{event.count or 1} "
                f"{event.involved_object.kind}/{event.involved_object.name}: {event.message}"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error listing events in '{ns}': {exc}"


@register(SafetyCategory.READ)
def get_logs(
    pod: str,
    namespace: str = "",
    container: str = "",
    tail_lines: int = 100,
    previous: bool = False,
) -> str:
    """Read recent container logs from a pod (last N lines).
    Set previous=True to view logs from previous terminated/crashed instance (CrashLoopBackOff).
    Omit container for single-container pods."""
    ns = _namespace(namespace)
    try:
        logs = core().read_namespaced_pod_log(
            name=pod,
            namespace=ns,
            container=container or None,
            tail_lines=tail_lines,
            previous=previous,
            timestamps=True,
        )
        prev_str = " (previous terminated instance)" if previous else ""
        if not logs.strip():
            return f"Pod '{pod}' in '{ns}' has no log output{prev_str} (container={container or 'default'})."
        return f"Last {tail_lines} log lines from pod '{pod}' in '{ns}'{prev_str}:\n{logs}"
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
        elif kind == "ingress":
            obj = networking().read_namespaced_ingress(name, ns)
        elif kind in ("pvc", "persistentvolumeclaim"):
            obj = core().read_namespaced_persistent_volume_claim(name, ns)
        elif kind in ("hpa", "horizontalpodautoscaler"):
            obj = autoscaling().read_namespaced_horizontal_pod_autoscaler(name, ns)
        else:
            return (
                f"Unsupported kind '{kind}'. Supported: pod, deployment, service, "
                "statefulset, daemonset, job, node, ingress, pvc, hpa."
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


@register(SafetyCategory.READ)
def get_ingresses(namespace: str = "") -> str:
    """List ingress resources in a namespace with hosts, paths, backend services, and TLS status."""
    ns = _namespace(namespace)
    try:
        items = networking().list_namespaced_ingress(ns).items
        if not items:
            return f"No ingresses found in namespace '{ns}'."
        lines = [f"Ingresses in namespace '{ns}':"]
        for ing in items:
            rules = []
            for rule in ing.spec.rules or []:
                host = rule.host or "*"
                for path_rule in rule.http.paths if rule.http else []:
                    backend = path_rule.backend
                    svc_name = backend.service.name if backend and backend.service else "?"
                    svc_port = (
                        backend.service.port.number or backend.service.port.name
                        if backend and backend.service and backend.service.port
                        else "?"
                    )
                    rules.append(f"{host}{path_rule.path or '/'} -> {svc_name}:{svc_port}")
            tls_hosts = []
            for t in ing.spec.tls or []:
                tls_hosts.extend(t.hosts or [])
            tls_info = f"tls=[{', '.join(tls_hosts)}]" if tls_hosts else "tls=none"
            lines.append(
                f"  {ing.metadata.name}  class={ing.spec.ingress_class_name or 'none'} "
                f"{tls_info} rules=[{', '.join(rules)}] age={_age(ing.metadata.creation_timestamp)}"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error listing ingresses in '{ns}': {exc}"


@register(SafetyCategory.READ)
def get_pvc_status(namespace: str = "") -> str:
    """List PersistentVolumeClaims in a namespace with phase, storage class,
    capacity, and volume name."""
    ns = _namespace(namespace)
    try:
        items = core().list_namespaced_persistent_volume_claim(ns).items
        if not items:
            return f"No PVCs found in namespace '{ns}'."
        lines = [f"PersistentVolumeClaims in namespace '{ns}':"]
        for pvc in items:
            status = pvc.status
            capacity = "?"
            if status.capacity and "storage" in status.capacity:
                capacity = status.capacity["storage"]
            elif (
                pvc.spec.resources
                and pvc.spec.resources.requests
                and "storage" in pvc.spec.resources.requests
            ):
                capacity = pvc.spec.resources.requests["storage"]
            sc = pvc.spec.storage_class_name or "none"
            vol = pvc.spec.volume_name or "none"
            lines.append(
                f"  {pvc.metadata.name}  status={status.phase} storageclass={sc} "
                f"capacity={capacity} volume={vol} "
                f"access_modes={pvc.spec.access_modes or []} age={_age(pvc.metadata.creation_timestamp)}"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error listing PVCs in '{ns}': {exc}"


@register(SafetyCategory.READ)
def get_hpa(namespace: str = "") -> str:
    """List HorizontalPodAutoscalers in a namespace with target reference,
    min/max replicas, current replicas, and condition states."""
    ns = _namespace(namespace)
    try:
        items = autoscaling().list_namespaced_horizontal_pod_autoscaler(ns).items
        if not items:
            return f"No HPAs found in namespace '{ns}'."
        lines = [f"HorizontalPodAutoscalers in namespace '{ns}':"]
        for hpa in items:
            spec = hpa.spec
            status = hpa.status
            target = f"{spec.scale_target_ref.kind}/{spec.scale_target_ref.name}"
            current_rep = status.current_replicas or 0
            desired_rep = status.desired_replicas or 0
            lines.append(
                f"  {hpa.metadata.name}  target={target} min={spec.min_replicas or 1} "
                f"max={spec.max_replicas} current={current_rep} desired={desired_rep} "
                f"age={_age(hpa.metadata.creation_timestamp)}"
            )
            for cond in status.conditions or []:
                if cond.status == "False":
                    lines.append(f"    condition {cond.type}=False ({cond.reason}): {cond.message}")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error listing HPAs in '{ns}': {exc}"


@register(SafetyCategory.READ)
def cluster_health_scan() -> str:
    """Scan the entire cluster across all namespaces for unhealthy pods, crashing containers,
    node issues (MemoryPressure, NotReady), warning events, and stuck deployments.
    Provides an instant cluster-wide triage report."""
    try:
        report_lines = ["=== KubePilot Cluster Health Triage ==="]
        issues_found = 0

        # 1. Nodes check
        try:
            nodes = core().list_node().items
            unhealthy_nodes = []
            for n in nodes:
                node_name = n.metadata.name
                conditions = {c.type: c.status for c in (n.status.conditions or [])}
                reasons = []
                if conditions.get("Ready") != "True":
                    reasons.append("NotReady")
                if conditions.get("MemoryPressure") == "True":
                    reasons.append("MemoryPressure")
                if conditions.get("DiskPressure") == "True":
                    reasons.append("DiskPressure")
                if conditions.get("PIDPressure") == "True":
                    reasons.append("PIDPressure")
                if reasons:
                    unhealthy_nodes.append(f"{node_name} ({', '.join(reasons)})")

            if unhealthy_nodes:
                issues_found += len(unhealthy_nodes)
                report_lines.append(f"\n[ALERT] Unhealthy Nodes ({len(unhealthy_nodes)}):")
                for un in unhealthy_nodes:
                    report_lines.append(f"  - {un}")
            else:
                report_lines.append(f"\n[OK] Nodes: {len(nodes)} total, all Ready.")
        except Exception as exc:
            report_lines.append(f"\n[WARN] Could not inspect nodes: {exc}")

        # 2. Pods check across all namespaces
        try:
            all_pods = core().list_pod_for_all_namespaces().items
            problem_pods = []
            for p in all_pods:
                ns = p.metadata.namespace
                pod_name = p.metadata.name
                phase = p.status.phase or "Unknown"
                statuses = p.status.container_statuses or []
                restarts = sum(s.restart_count for s in statuses)
                crashes = []
                for s in statuses:
                    if s.state and s.state.waiting:
                        reason = s.state.waiting.reason or "Waiting"
                        if reason in (
                            "CrashLoopBackOff",
                            "ImagePullBackOff",
                            "ErrImagePull",
                            "CreateContainerConfigError",
                        ):
                            crashes.append(f"{s.name}:{reason}")
                    if s.last_state and s.last_state.terminated:
                        term_reason = s.last_state.terminated.reason or ""
                        if term_reason == "OOMKilled":
                            crashes.append(f"{s.name}:OOMKilled")

                is_problem = (phase not in ("Running", "Succeeded")) or (len(crashes) > 0) or (restarts > 5)
                if is_problem:
                    details = []
                    if phase != "Running":
                        details.append(f"phase={phase}")
                    if restarts > 0:
                        details.append(f"restarts={restarts}")
                    if crashes:
                        details.append(f"crashes=[{', '.join(crashes)}]")
                    problem_pods.append(f"{ns}/{pod_name}: {', '.join(details)}")

            if problem_pods:
                issues_found += len(problem_pods)
                report_lines.append(f"\n[ALERT] Problematic Pods ({len(problem_pods)} of {len(all_pods)}):")
                for pp in problem_pods[:20]:
                    report_lines.append(f"  - {pp}")
                if len(problem_pods) > 20:
                    report_lines.append(f"  ... and {len(problem_pods) - 20} more.")
            else:
                report_lines.append(f"\n[OK] Pods: {len(all_pods)} total, all healthy.")
        except Exception as exc:
            report_lines.append(f"\n[WARN] Could not inspect pods: {exc}")

        # 3. Deployments check
        try:
            deployments = apps().list_deployment_for_all_namespaces().items
            stuck_deps = []
            for d in deployments:
                ns = d.metadata.namespace
                name = d.metadata.name
                desired = d.spec.replicas or 0
                ready = d.status.ready_replicas or 0
                unavailable = d.status.unavailable_replicas or 0
                if ready < desired or unavailable > 0:
                    stuck_deps.append(f"{ns}/{name} (ready={ready}/{desired}, unavailable={unavailable})")

            if stuck_deps:
                issues_found += len(stuck_deps)
                report_lines.append(f"\n[ALERT] Degraded Deployments ({len(stuck_deps)}):")
                for sd in stuck_deps:
                    report_lines.append(f"  - {sd}")
            else:
                report_lines.append(f"\n[OK] Deployments: {len(deployments)} total, all ready.")
        except Exception as exc:
            report_lines.append(f"\n[WARN] Could not inspect deployments: {exc}")

        # 4. Recent Warning events
        try:
            warning_events = core().list_event_for_all_namespaces(field_selector="type=Warning").items
            if warning_events:

                def _event_time(event):
                    return event.last_timestamp or event.event_time or event.first_timestamp

                warning_events.sort(
                    key=lambda e: _event_time(e).timestamp() if _event_time(e) else 0.0,
                    reverse=True,
                )
                report_lines.append(f"\n[ALERT] Recent Warning Events (top {min(len(warning_events), 10)}):")
                for ev in warning_events[:10]:
                    ev_ns = ev.involved_object.namespace or "cluster"
                    report_lines.append(
                        f"  - [{ev_ns}] {ev.involved_object.kind}/{ev.involved_object.name}: "
                        f"{ev.reason} - {ev.message}"
                    )
            else:
                report_lines.append("\n[OK] Warning Events: none recently.")
        except Exception as exc:
            report_lines.append(f"\n[WARN] Could not inspect events: {exc}")

        report_lines.append(
            f"\nSummary: {issues_found} potential issue(s) detected across cluster."
            if issues_found > 0
            else "\nSummary: Cluster is healthy. No immediate anomalies found."
        )
        return "\n".join(report_lines)
    except Exception as exc:
        return f"Error running cluster health scan: {exc}"
