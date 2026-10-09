"""Tests for GitOps and Helm / Argo CD tools."""

from unittest.mock import MagicMock, patch

from app.tools.gitops.argocd import get_argocd_app_diff, get_argocd_apps
from app.tools.gitops.helm import (
    _decode_helm_secret,
    helm_list,
)
from app.tools.registry import SafetyCategory, get_tool


def test_decode_helm_secret():
    import base64
    import gzip
    import json

    payload = {
        "name": "my-release",
        "version": 3,
        "info": {"status": "deployed", "last_deployed": "2026-10-09T10:00:00Z"},
        "chart": {"metadata": {"name": "web-service", "version": "1.2.0", "appVersion": "2.0.1"}},
    }
    compressed = gzip.compress(json.dumps(payload).encode("utf-8"))
    raw_b64 = base64.b64encode(compressed).decode("utf-8")

    decoded = _decode_helm_secret(raw_b64)
    assert decoded is not None
    assert decoded["name"] == "my-release"
    assert decoded["version"] == 3
    assert decoded["info"]["status"] == "deployed"


@patch("app.tools.gitops.helm.shutil.which", return_value=None)
@patch("app.tools.gitops.helm.core")
def test_helm_list_via_secrets(mock_core, mock_which):
    import base64
    import gzip
    import json

    payload = {
        "name": "ingress-nginx",
        "version": 2,
        "info": {"status": "deployed", "last_deployed": "2026-10-09T10:00:00Z"},
        "chart": {"metadata": {"name": "ingress-nginx", "version": "4.8.0", "appVersion": "1.9.0"}},
    }
    compressed = gzip.compress(json.dumps(payload).encode("utf-8"))
    b64_rel = base64.b64encode(compressed).decode("utf-8")

    mock_secret = MagicMock()
    mock_secret.metadata.name = "sh.helm.release.v1.ingress-nginx.v2"
    mock_secret.metadata.namespace = "kube-system"
    mock_secret.data = {"release": b64_rel}

    mock_core.return_value.list_namespaced_secret.return_value.items = [mock_secret]

    out = helm_list(namespace="kube-system")
    assert "ingress-nginx" in out
    assert "rev=2" in out
    assert "deployed" in out


@patch("app.tools.gitops.helm.shutil.which")
@patch("app.tools.gitops.helm.subprocess.run")
def test_helm_list_cli_fallback(mock_run, mock_which):
    mock_which.return_value = "/usr/bin/helm"
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=(
            '[{"name": "prometheus", "namespace": "monitoring", "revision": "5", '
            '"status": "deployed", "chart": "prometheus-15.0", "app_version": "2.45.0"}]'
        ),
    )

    out = helm_list(namespace="monitoring")
    assert "prometheus" in out
    assert "rev=5" in out


def test_rollback_helm_is_operational():
    spec = get_tool("rollback_helm_release")
    assert spec is not None
    assert spec.category is SafetyCategory.OPERATIONAL


@patch("app.tools.gitops.argocd._custom_objects")
def test_get_argocd_apps(mock_custom):
    mock_custom.return_value.list_namespaced_custom_object.return_value = {
        "items": [
            {
                "metadata": {"name": "frontend-prod"},
                "spec": {
                    "source": {
                        "repoURL": "https://github.com/org/repo",
                        "targetRevision": "main",
                        "path": "deploy",
                    },
                    "destination": {"namespace": "prod"},
                },
                "status": {
                    "sync": {"status": "Synced"},
                    "health": {"status": "Healthy"},
                },
            }
        ]
    }

    out = get_argocd_apps(namespace="argocd")
    assert "frontend-prod" in out
    assert "health=Healthy" in out
    assert "sync=Synced" in out


@patch("app.tools.gitops.argocd._custom_objects")
def test_get_argocd_app_diff(mock_custom):
    mock_custom.return_value.get_namespaced_custom_object.return_value = {
        "status": {
            "sync": {"status": "OutOfSync"},
            "health": {"status": "Degraded"},
            "resources": [
                {
                    "kind": "Deployment",
                    "name": "cartservice",
                    "namespace": "shop",
                    "status": "OutOfSync",
                    "health": {"status": "Degraded", "message": "CrashLoopBackOff"},
                }
            ],
        }
    }

    out = get_argocd_app_diff(app_name="cart-app", namespace="argocd")
    assert "OutOfSync" in out
    assert "cartservice" in out
    assert "CrashLoopBackOff" in out


def test_sync_argocd_app_is_operational():
    spec = get_tool("sync_argocd_app")
    assert spec is not None
    assert spec.category is SafetyCategory.OPERATIONAL
