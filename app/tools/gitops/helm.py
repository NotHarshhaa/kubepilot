"""Helm release inspection and rollback tools."""

import base64
import gzip
import json
import shutil
import subprocess

from app.config.settings import get_settings
from app.tools.kubernetes.tools import core
from app.tools.registry import SafetyCategory, register


def _namespace(namespace: str = "") -> str:
    return namespace or get_settings().default_namespace


def _decode_helm_secret(raw_b64: str) -> dict | None:
    """Decode Helm v3 release data stored in Kubernetes Secret."""
    try:
        # Helm v3 encodes data as base64(gzip(json_string)) or double base64
        compressed = base64.b64decode(raw_b64)
        # Check if gzip header (0x1f, 0x8b)
        if compressed.startswith(b"\x1f\x8b"):
            decompressed = gzip.decompress(compressed)
            return json.loads(decompressed.decode("utf-8"))
        # Some versions double base64 encode
        try:
            second_decode = base64.b64decode(compressed)
            if second_decode.startswith(b"\x1f\x8b"):
                decompressed = gzip.decompress(second_decode)
                return json.loads(decompressed.decode("utf-8"))
            return json.loads(second_decode.decode("utf-8"))
        except Exception:
            return json.loads(compressed.decode("utf-8"))
    except Exception:
        return None


def _list_helm_from_k8s(namespace: str) -> list[dict]:
    """Inspect Helm releases via Kubernetes secrets (sh.helm.release.v1.*)."""
    core_api = core()
    releases_by_name: dict[str, dict] = {}

    try:
        if namespace.upper() == "ALL":
            secrets = core_api.list_secret_for_all_namespaces(label_selector="owner=helm").items
        else:
            secrets = core_api.list_namespaced_secret(namespace, label_selector="owner=helm").items
    except Exception:
        secrets = []

    for sec in secrets:
        sec_name = sec.metadata.name or ""
        if not sec_name.startswith("sh.helm.release.v1."):
            continue
        data = sec.data or {}
        raw_rel = data.get("release")
        if not raw_rel:
            continue
        rel_info = _decode_helm_secret(raw_rel)
        if not rel_info:
            continue

        name = rel_info.get("name", "")
        ns = sec.metadata.namespace or namespace
        rev = rel_info.get("version", 1)
        chart_meta = rel_info.get("chart", {}).get("metadata", {})
        chart = f"{chart_meta.get('name', 'unknown')}-{chart_meta.get('version', '')}"
        app_version = chart_meta.get("appVersion", "-")
        info = rel_info.get("info", {})
        status = info.get("status", "unknown")
        updated = info.get("last_deployed", "")

        entry = {
            "name": name,
            "namespace": ns,
            "revision": rev,
            "updated": updated,
            "status": status,
            "chart": chart,
            "app_version": app_version,
            "description": info.get("description", ""),
        }

        key = f"{ns}/{name}"
        if key not in releases_by_name or rev > releases_by_name[key]["revision"]:
            releases_by_name[key] = entry

    return list(releases_by_name.values())


@register(SafetyCategory.READ)
def helm_list(namespace: str = "") -> str:
    """List Helm releases across the cluster or in a specific namespace with revision,
    chart, status, and app version. Use namespace='ALL' for all namespaces."""
    ns = _namespace(namespace)

    # 1. Try helm CLI if available
    if shutil.which("helm"):
        try:
            cmd = ["helm", "list", "-o", "json"]
            if ns.upper() == "ALL":
                cmd.append("-A")
            else:
                cmd.extend(["-n", ns])
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if proc.returncode == 0:
                releases = json.loads(proc.stdout)
                target_str = "all namespaces" if ns.upper() == "ALL" else f"namespace '{ns}'"
                if not releases:
                    return f"No Helm releases found in {target_str}."
                lines = [f"Helm releases ({target_str}):"]
                for r in releases:
                    app_v = r.get("app_version", "-")
                    lines.append(
                        f"  {r.get('name')} (ns={r.get('namespace')}) rev={r.get('revision')} "
                        f"status={r.get('status')} chart={r.get('chart')} app_version={app_v}"
                    )
                return "\n".join(lines)
        except Exception:
            pass  # Fallback to K8s secrets

    # 2. Inspect via Kubernetes API
    try:
        releases = _list_helm_from_k8s(ns)
        if not releases:
            return (
                f"No Helm releases found in {'all namespaces' if ns.upper() == 'ALL' else f'namespace {ns}'}."
            )
        lines = [f"Helm releases ({'all namespaces' if ns.upper() == 'ALL' else f'namespace {ns}'}):"]
        for r in releases:
            lines.append(
                f"  {r['name']} (ns={r['namespace']}) rev={r['revision']} "
                f"status={r['status']} chart={r['chart']} app_version={r['app_version']}"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error listing Helm releases in '{ns}': {exc}"


@register(SafetyCategory.READ)
def helm_status(release: str, namespace: str = "") -> str:
    """Get detailed status, current revision, notes, and description for a Helm release."""
    ns = _namespace(namespace)

    if shutil.which("helm"):
        try:
            cmd = ["helm", "status", release, "-n", ns, "-o", "json"]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                info = data.get("info", {})
                lines = [
                    f"Helm Release: {data.get('name')} (namespace: {data.get('namespace', ns)})",
                    f"  Status: {info.get('status')}",
                    f"  Revision: {data.get('version')}",
                    f"  First Deployed: {info.get('first_deployed')}",
                    f"  Last Deployed: {info.get('last_deployed')}",
                    f"  Description: {info.get('description')}",
                ]
                notes = info.get("notes")
                if notes:
                    lines.append(f"  Notes: {notes.strip()}")
                return "\n".join(lines)
        except Exception:
            pass

    # Inspect via Kubernetes Secrets
    try:
        secrets = core().list_namespaced_secret(ns, label_selector=f"name={release},owner=helm").items
        if not secrets:
            secrets = [
                s
                for s in core().list_namespaced_secret(ns).items
                if (s.metadata.name or "").startswith(f"sh.helm.release.v1.{release}.")
            ]
        if not secrets:
            return f"Helm release '{release}' not found in namespace '{ns}'."

        # Find latest revision
        latest_rel = None
        max_rev = -1
        for s in secrets:
            data = s.data or {}
            raw = data.get("release")
            if not raw:
                continue
            decoded = _decode_helm_secret(raw)
            if decoded and decoded.get("version", 0) > max_rev:
                max_rev = decoded.get("version", 0)
                latest_rel = decoded

        if not latest_rel:
            return f"Unable to decode Helm release data for '{release}' in namespace '{ns}'."

        info = latest_rel.get("info", {})
        chart_meta = latest_rel.get("chart", {}).get("metadata", {})
        lines = [
            f"Helm Release: {release} (namespace: {ns})",
            f"  Status: {info.get('status')}",
            f"  Revision: {latest_rel.get('version')}",
            f"  Chart: {chart_meta.get('name')}-{chart_meta.get('version')}",
            f"  App Version: {chart_meta.get('appVersion', '-')}",
            f"  Last Deployed: {info.get('last_deployed')}",
            f"  Description: {info.get('description')}",
        ]
        notes = info.get("notes")
        if notes:
            lines.append(f"  Notes: {notes.strip()[:300]}...")
        return "\n".join(lines)
    except Exception as exc:
        return f"Error getting status for Helm release '{release}' in '{ns}': {exc}"


@register(SafetyCategory.READ)
def helm_history(release: str, namespace: str = "") -> str:
    """View revision history for a Helm release, including prior versions, status, and deploy timestamps."""
    ns = _namespace(namespace)

    if shutil.which("helm"):
        try:
            cmd = ["helm", "history", release, "-n", ns, "-o", "json"]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if proc.returncode == 0:
                history = json.loads(proc.stdout)
                if not history:
                    return f"No history found for release '{release}'."
                lines = [f"History for Helm release '{release}' in namespace '{ns}':"]
                for h in history:
                    lines.append(
                        f"  Revision {h.get('revision')}: status={h.get('status')} "
                        f"chart={h.get('chart')} app_version={h.get('app_version', '-')} "
                        f"updated={h.get('updated', '')[:19]} ({h.get('description', '')})"
                    )
                return "\n".join(lines)
        except Exception:
            pass

    # Inspect via Kubernetes Secrets
    try:
        secrets = [
            s
            for s in core().list_namespaced_secret(ns).items
            if (s.metadata.name or "").startswith(f"sh.helm.release.v1.{release}.")
        ]
        if not secrets:
            return f"No Helm history found for release '{release}' in namespace '{ns}'."

        history_items = []
        for s in secrets:
            data = s.data or {}
            raw = data.get("release")
            if not raw:
                continue
            decoded = _decode_helm_secret(raw)
            if not decoded:
                continue
            info = decoded.get("info", {})
            chart_meta = decoded.get("chart", {}).get("metadata", {})
            history_items.append(
                {
                    "revision": decoded.get("version", 0),
                    "status": info.get("status", "unknown"),
                    "chart": f"{chart_meta.get('name')}-{chart_meta.get('version')}",
                    "app_version": chart_meta.get("appVersion", "-"),
                    "updated": info.get("last_deployed", ""),
                    "description": info.get("description", ""),
                }
            )

        history_items.sort(key=lambda x: x["revision"])
        lines = [f"History for Helm release '{release}' in namespace '{ns}':"]
        for h in history_items:
            lines.append(
                f"  Revision {h['revision']}: status={h['status']} "
                f"chart={h['chart']} app_version={h['app_version']} "
                f"updated={h['updated'][:19] if h['updated'] else '-'} ({h['description']})"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"Error fetching history for Helm release '{release}' in '{ns}': {exc}"


@register(SafetyCategory.OPERATIONAL)
def rollback_helm_release(release: str, revision: int = 0, namespace: str = "") -> str:
    """Roll back a Helm release to a previous revision. If revision is 0, rolls back
    to immediately preceding revision. Requires human approval."""
    ns = _namespace(namespace)

    if shutil.which("helm"):
        try:
            cmd = ["helm", "rollback", release]
            if revision > 0:
                cmd.append(str(revision))
            cmd.extend(["-n", ns])
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode == 0:
                rev_str = f"revision {revision}" if revision > 0 else "previous revision"
                return f"Successfully rolled back Helm release '{release}' in '{ns}' to {rev_str}."
            return f"Helm rollback command failed: {proc.stderr.strip()}"
        except Exception as exc:
            return f"Error executing Helm rollback: {exc}"

    return (
        f"Helm CLI is required on the host system to perform 'helm rollback {release} "
        f"{revision or ''} -n {ns}' safely."
    )
