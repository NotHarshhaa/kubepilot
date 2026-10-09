"""Argo CD GitOps application inspection and synchronization tools."""

from kubernetes import client

from app.tools.kubernetes.tools import _configure
from app.tools.registry import SafetyCategory, register


def _custom_objects() -> client.CustomObjectsApi:
    _configure()
    return client.CustomObjectsApi()


@register(SafetyCategory.READ)
def get_argocd_apps(namespace: str = "argocd", app_name: str = "") -> str:
    """Inspect Argo CD Application resources in the cluster. Reports sync status (Synced/OutOfSync),
    health status (Healthy/Degraded/Progressing), repo URL, target revision, and destination namespace.
    If app_name is provided, shows deep inspection including sync errors."""
    try:
        custom = _custom_objects()
        if app_name:
            try:
                item = custom.get_namespaced_custom_object(
                    group="argoproj.io",
                    version="v1alpha1",
                    namespace=namespace,
                    plural="applications",
                    name=app_name,
                )
                items = [item]
            except Exception as exc:
                return f"Argo CD application '{app_name}' not found in namespace '{namespace}': {exc}"
        else:
            try:
                res = custom.list_namespaced_custom_object(
                    group="argoproj.io",
                    version="v1alpha1",
                    namespace=namespace,
                    plural="applications",
                )
                items = res.get("items", [])
            except Exception as exc:
                return (
                    f"Could not list Argo CD applications in namespace '{namespace}' "
                    f"(verify Argo CD CRDs and namespace): {exc}"
                )

        if not items:
            return f"No Argo CD applications found in namespace '{namespace}'."

        lines = [f"Argo CD Applications (namespace '{namespace}'):"]
        for app in items:
            name = app.get("metadata", {}).get("name", "unknown")
            spec = app.get("spec", {})
            status = app.get("status", {})

            source = spec.get("source", {})
            repo = source.get("repoURL", "unknown")
            target_rev = source.get("targetRevision", "HEAD")
            path = source.get("path") or source.get("chart") or "."

            destination = spec.get("destination", {})
            dest_ns = destination.get("namespace", "default")

            sync_status = status.get("sync", {}).get("status", "Unknown")
            health_status = status.get("health", {}).get("status", "Unknown")
            health_message = status.get("health", {}).get("message", "")

            lines.append(
                f"  {name}  health={health_status} sync={sync_status} "
                f"repo={repo} rev={target_rev} path={path} dest_ns={dest_ns}"
            )

            # If inspecting single app or has issues, show details
            if app_name or sync_status == "OutOfSync" or health_status in ("Degraded", "Missing"):
                if health_message:
                    lines.append(f"    Health Issue: {health_message}")
                sync_conds = status.get("conditions", [])
                for cond in sync_conds:
                    lines.append(f"    Condition: [{cond.get('type')}] {cond.get('message')}")

                # Check resources
                resources = status.get("resources", [])
                troubled_resources = [
                    r
                    for r in resources
                    if r.get("status") == "OutOfSync"
                    or (r.get("health", {}).get("status") in ("Degraded", "Missing"))
                ]
                if troubled_resources:
                    lines.append(f"    Problematic Resources ({len(troubled_resources)}):")
                    for r in troubled_resources[:10]:
                        r_kind = r.get("kind", "")
                        r_name = r.get("name", "")
                        r_health = r.get("health", {}).get("status", "none")
                        r_sync = r.get("status", "")
                        lines.append(f"      - {r_kind}/{r_name} (health={r_health}, sync={r_sync})")

        return "\n".join(lines)
    except Exception as exc:
        return f"Error querying Argo CD applications: {exc}"


@register(SafetyCategory.READ)
def get_argocd_app_diff(app_name: str, namespace: str = "argocd") -> str:
    """Show details of resources that are OutOfSync or Degraded for an Argo CD application."""
    try:
        custom = _custom_objects()
        app = custom.get_namespaced_custom_object(
            group="argoproj.io",
            version="v1alpha1",
            namespace=namespace,
            plural="applications",
            name=app_name,
        )
        status = app.get("status", {})
        sync_status = status.get("sync", {}).get("status", "Unknown")
        health_status = status.get("health", {}).get("status", "Unknown")
        resources = status.get("resources", [])

        lines = [f"Argo CD Application Diff: {app_name} (health={health_status}, sync={sync_status})"]

        out_of_sync = [r for r in resources if r.get("status") == "OutOfSync"]
        degraded = [r for r in resources if r.get("health", {}).get("status") == "Degraded"]

        if not out_of_sync and not degraded:
            lines.append("  All resources are currently synchronized and healthy.")
            return "\n".join(lines)

        if out_of_sync:
            lines.append(f"\n  OutOfSync Resources ({len(out_of_sync)}):")
            for r in out_of_sync:
                grp, kind, name = r.get("group", "core"), r.get("kind"), r.get("name")
                ns_val = r.get("namespace", "-")
                lines.append(f"    - {grp}/{kind}/{name} (namespace: {ns_val})")

        if degraded:
            lines.append(f"\n  Degraded Resources ({len(degraded)}):")
            for r in degraded:
                grp, kind, name = r.get("group", "core"), r.get("kind"), r.get("name")
                msg_val = r.get("health", {}).get("message", "degraded")
                lines.append(f"    - {grp}/{kind}/{name}: {msg_val}")

        return "\n".join(lines)
    except Exception as exc:
        return f"Error getting Argo CD app diff for '{app_name}': {exc}"


@register(SafetyCategory.OPERATIONAL)
def sync_argocd_app(app_name: str, namespace: str = "argocd", prune: bool = False) -> str:
    """Trigger a synchronization of an Argo CD application. Requires human approval."""
    try:
        custom = _custom_objects()
        # Trigger sync by adding operation field to the Application resource
        body = {
            "operation": {
                "sync": {
                    "prune": prune,
                    "syncStrategy": {"hook": {}},
                }
            }
        }
        custom.patch_namespaced_custom_object(
            group="argoproj.io",
            version="v1alpha1",
            namespace=namespace,
            plural="applications",
            name=app_name,
            body=body,
        )
        return (
            f"Sync initiated for Argo CD application '{app_name}' in namespace '{namespace}' (prune={prune})."
        )
    except Exception as exc:
        return f"Error syncing Argo CD application '{app_name}' in '{namespace}': {exc}"
