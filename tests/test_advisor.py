"""Tests for AI Resource and Cost Optimization Advisor."""

from unittest.mock import MagicMock, patch

from app.advisor.resources import (
    analyze_deployment_resources,
    analyze_resource_optimization,
    format_cpu,
    format_memory,
    parse_cpu_to_cores,
    parse_memory_to_bytes,
)


def test_resource_parsers():
    # CPU
    assert parse_cpu_to_cores("250m") == 0.25
    assert parse_cpu_to_cores("1") == 1.0
    assert parse_cpu_to_cores("500u") == 0.0005
    assert parse_cpu_to_cores(None) == 0.0

    # Memory
    assert parse_memory_to_bytes("256Mi") == 256 * 1024 * 1024
    assert parse_memory_to_bytes("1Gi") == 1024 * 1024 * 1024
    assert parse_memory_to_bytes("500M") == 500 * 1000 * 1000
    assert parse_memory_to_bytes(None) == 0.0

    # Formatting
    assert format_cpu(0.25) == "250m"
    assert format_cpu(2.0) == "2"
    assert format_memory(256 * 1024 * 1024) == "256Mi"
    assert format_memory(2 * 1024 * 1024 * 1024) == "2Gi"


def test_analyze_deployment_over_provisioned():
    dep = MagicMock()
    dep.metadata.name = "web-frontend"
    dep.metadata.namespace = "prod"
    dep.spec.replicas = 3

    container = MagicMock()
    container.name = "app"
    container.resources.requests = {"cpu": "2", "memory": "4Gi"}
    container.resources.limits = {"cpu": "4", "memory": "8Gi"}
    dep.spec.template.spec.containers = [container]

    # Metrics observed: only 0.1 cores and 300Mi memory used (massively over-provisioned)
    pod_metrics = {
        "web-frontend-1": {"app": (0.1, 300 * 1024 * 1024)},
        "web-frontend-2": {"app": (0.1, 300 * 1024 * 1024)},
    }

    report = analyze_deployment_resources(dep, pod_metrics)
    assert report.workload == "web-frontend"
    assert report.replicas == 3
    assert len(report.containers) == 1

    advice = report.containers[0]
    assert advice.status == "OVER_PROVISIONED"
    assert advice.monthly_savings > 0
    assert "spec:" in report.patch_snippet


def test_analyze_deployment_under_provisioned():
    dep = MagicMock()
    dep.metadata.name = "payment-worker"
    dep.metadata.namespace = "prod"
    dep.spec.replicas = 1

    container = MagicMock()
    container.name = "worker"
    container.resources.requests = {"cpu": "100m", "memory": "200Mi"}
    container.resources.limits = {"cpu": "200m", "memory": "250Mi"}
    dep.spec.template.spec.containers = [container]

    # Metrics observed: 240Mi used (which is >85% of 250Mi limit) -> OOM risk
    pod_metrics = {
        "payment-worker-xyz": {"worker": (0.18, 240 * 1024 * 1024)},
    }

    report = analyze_deployment_resources(dep, pod_metrics)
    advice = report.containers[0]
    assert advice.status == "UNDER_PROVISIONED"
    assert any("OOMKilled risk" in f for f in advice.findings)


@patch("app.advisor.resources._get_pod_metrics", return_value={})
@patch("app.advisor.resources.apps")
def test_analyze_resource_optimization_tool(mock_apps, mock_metrics):
    dep = MagicMock()
    dep.metadata.name = "nginx-proxy"
    dep.metadata.namespace = "default"
    dep.spec.replicas = 2
    c = MagicMock()
    c.name = "nginx"
    c.resources.requests = {"cpu": "500m", "memory": "512Mi"}
    c.resources.limits = {"cpu": "1", "memory": "1Gi"}
    dep.spec.template.spec.containers = [c]

    mock_apps.return_value.list_namespaced_deployment.return_value.items = [dep]

    out = analyze_resource_optimization(namespace="default")
    assert "KubePilot AI Resource & Cost Optimization Advisory" in out
    assert "nginx-proxy" in out
    assert "kubectl patch deployment" in out
