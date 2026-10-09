"""Tests for automated diagnostic runbooks."""

from unittest.mock import MagicMock, patch

from app.diagnostics.runbooks import (
    diagnose_crashloop,
    diagnose_image_pull,
    diagnose_oomkilled,
    diagnose_pending_pod,
    run_diagnostic_runbook,
)


def _mock_pod(name="test-pod", phase="Running", container_statuses=None, containers=None, node_name="node-1"):
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = "default"
    pod.status.phase = phase
    pod.status.container_statuses = container_statuses or []
    pod.spec.containers = containers or []
    pod.spec.node_name = node_name
    pod.spec.node_selector = {}
    pod.spec.tolerations = []
    pod.spec.volumes = []
    pod.spec.image_pull_secrets = []
    return pod


@patch("app.diagnostics.runbooks._get_pod_events")
@patch("app.diagnostics.runbooks._get_pod_previous_logs")
@patch("app.diagnostics.runbooks.core")
def test_diagnose_crashloop(mock_core, mock_logs, mock_events):
    mock_logs.return_value = "FATAL: dial tcp 10.0.0.5:5432: connect: connection refused"
    mock_events.return_value = []

    c_status = MagicMock()
    c_status.name = "backend"
    c_status.restart_count = 5
    c_status.state.waiting.reason = "CrashLoopBackOff"
    c_status.last_state.terminated.exit_code = 1
    c_status.last_state.terminated.reason = "Error"
    c_status.last_state.terminated.message = ""

    pod = _mock_pod("api-pod", container_statuses=[c_status])

    finding = diagnose_crashloop(pod, "default")
    assert finding.runbook == "CrashLoopBackOff"
    assert "exit code 1" in finding.root_cause
    assert any("FATAL: dial tcp" in ev for ev in finding.evidence)
    assert len(finding.recommendations) > 0


def test_diagnose_oomkilled():
    c_status = MagicMock()
    c_status.name = "worker"
    c_status.restart_count = 3
    c_status.state.waiting.reason = "CrashLoopBackOff"
    c_status.last_state.terminated.exit_code = 137
    c_status.last_state.terminated.reason = "OOMKilled"

    c_spec = MagicMock()
    c_spec.name = "worker"
    c_spec.resources.limits = {"memory": "256Mi"}
    c_spec.resources.requests = {"memory": "128Mi"}

    pod = _mock_pod("worker-pod", container_statuses=[c_status], containers=[c_spec])

    finding = diagnose_oomkilled(pod, "default")
    assert finding.runbook == "OOMKilled"
    assert "256Mi" in finding.root_cause
    assert "cgroup memory limit" in finding.root_cause
    assert any("Increase memory limit" in rec for rec in finding.recommendations)


@patch("app.diagnostics.runbooks._get_pod_events")
@patch("app.diagnostics.runbooks.core")
def test_diagnose_image_pull(mock_core, mock_events):
    ev = MagicMock()
    ev.reason = "Failed"
    ev.message = (
        'Failed to pull image "myregistry.io/app:v9.9.9": rpc error: code = NotFound desc = not found'
    )
    mock_events.return_value = [ev]

    c_status = MagicMock()
    c_status.name = "web"
    c_status.state.waiting.reason = "ImagePullBackOff"
    c_status.state.waiting.message = "Back-off pulling image"
    c_status.last_state = None

    c_spec = MagicMock()
    c_spec.image = "myregistry.io/app:v9.9.9"

    pod = _mock_pod("web-pod", phase="Pending", container_statuses=[c_status], containers=[c_spec])

    finding = diagnose_image_pull(pod, "default")
    assert finding.runbook == "ImagePullBackOff"
    assert "does not exist in registry" in finding.root_cause
    assert any("tag spelling" in rec for rec in finding.recommendations)


@patch("app.diagnostics.runbooks._get_pod_events")
@patch("app.diagnostics.runbooks.core")
def test_diagnose_pending_pod_insufficient_cpu(mock_core, mock_events):
    ev = MagicMock()
    ev.reason = "FailedScheduling"
    ev.message = "0/3 nodes available: 3 Insufficient cpu."
    mock_events.return_value = [ev]

    c_spec = MagicMock()
    c_spec.name = "heavy-task"
    c_spec.resources.requests = {"cpu": "4", "memory": "8Gi"}

    pod = _mock_pod("heavy-pod", phase="Pending", containers=[c_spec], node_name=None)

    finding = diagnose_pending_pod(pod, "default")
    assert finding.runbook == "PendingPod"
    assert "Insufficient CPU capacity" in finding.root_cause
    assert any("Scale worker node" in rec for rec in finding.recommendations)


@patch("app.diagnostics.runbooks.core")
def test_run_diagnostic_runbook_all_healthy(mock_core):
    pod = _mock_pod("ok-pod", phase="Running")
    mock_core.return_value.list_namespaced_pod.return_value.items = [pod]

    report = run_diagnostic_runbook(namespace="default")
    assert "No unhealthy pods detected" in report
