"""Tests for Automated Post-Remediation Verification Engine."""

from unittest.mock import MagicMock, patch

from app.remediation.verification import (
    VerificationVerdict,
    run_post_action_verification,
    verify_remediation,
)
from app.tools.approvals import manager
from app.tools.registry import SafetyCategory, register


def _mock_deployment(name="cartservice", namespace="default", desired=3, ready=3, updated=3, unavailable=0):
    dep = MagicMock()
    dep.metadata.name = name
    dep.metadata.namespace = namespace
    dep.metadata.generation = 2
    dep.spec.replicas = desired
    dep.spec.selector.match_labels = {"app": name}
    dep.status.observed_generation = 2
    dep.status.replicas = desired
    dep.status.ready_replicas = ready
    dep.status.updated_replicas = updated
    dep.status.unavailable_replicas = unavailable
    return dep


@patch("app.remediation.verification.core")
@patch("app.remediation.verification.apps")
def test_verification_healthy(mock_apps, mock_core):
    dep = _mock_deployment(desired=3, ready=3, updated=3, unavailable=0)
    mock_apps.return_value.read_namespaced_deployment.return_value = dep

    # Healthy pods
    pod = MagicMock()
    pod.metadata.name = "cartservice-1"
    c_status = MagicMock()
    c_status.name = "cart"
    c_status.ready = True
    c_status.restart_count = 0
    c_status.state.waiting = None
    c_status.last_state.terminated = None
    pod.status.container_statuses = [c_status]
    mock_core.return_value.list_namespaced_pod.return_value.items = [pod]
    mock_core.return_value.list_namespaced_event.return_value.items = []

    res = run_post_action_verification(target="cartservice", namespace="default", action_type="restart")
    assert res.verdict == VerificationVerdict.VERIFIED_HEALTHY
    assert res.ready_replicas == 3
    assert len(res.active_crashes) == 0


@patch("app.remediation.verification.core")
@patch("app.remediation.verification.apps")
def test_verification_failed_rollout(mock_apps, mock_core):
    dep = _mock_deployment(desired=3, ready=1, updated=2, unavailable=2)
    mock_apps.return_value.read_namespaced_deployment.return_value = dep

    # Crashing pod
    pod = MagicMock()
    pod.metadata.name = "cartservice-crash"
    c_status = MagicMock()
    c_status.name = "cart"
    c_status.ready = False
    c_status.restart_count = 5
    c_status.state.waiting.reason = "CrashLoopBackOff"
    c_status.last_state.terminated = None
    pod.status.container_statuses = [c_status]
    mock_core.return_value.list_namespaced_pod.return_value.items = [pod]
    mock_core.return_value.list_namespaced_event.return_value.items = []

    res = run_post_action_verification(target="cartservice", namespace="default", action_type="restart")
    assert res.verdict == VerificationVerdict.FAILED_ROLLOUT
    assert len(res.active_crashes) > 0
    assert any("rollback_deployment" in step for step in res.next_steps)


@patch("app.remediation.verification.run_post_action_verification")
def test_verify_remediation_tool_formatter(mock_run):
    from app.remediation.verification import VerificationResult

    mock_run.return_value = VerificationResult(
        target="orders-api",
        namespace="prod",
        action_type="scale",
        verdict=VerificationVerdict.VERIFIED_HEALTHY,
        desired_replicas=5,
        ready_replicas=5,
        updated_replicas=5,
        unavailable_replicas=0,
        details=["All 5/5 replicas are updated and running."],
        next_steps=["Remediation successful! No further action required."],
    )

    out = verify_remediation("orders-api", namespace="prod", action_type="scale")
    assert "VERIFIED HEALTHY" in out
    assert "orders-api" in out
    assert "Ready: `5/5`" in out


def test_approval_post_action_verification_hook(monkeypatch):
    # Register mock restart tool
    @register(SafetyCategory.OPERATIONAL)
    def dummy_restart(deployment: str, namespace: str = "default") -> str:
        return f"Restarted {deployment}"

    monkeypatch.setattr(
        "app.remediation.verification.run_post_action_verification",
        lambda target, namespace, action_type: MagicMock(
            verdict=MagicMock(value="VERIFIED_HEALTHY"),
            ready_replicas=2,
            desired_replicas=2,
        ),
    )

    rec = manager.create("restart_deployment", {"deployment": "web-app", "namespace": "default"})
    approved = manager.approve(rec.id, note="lgtm")
    assert approved.status == "approved"
    assert "Post-Action Verification: VERIFIED_HEALTHY" in approved.result
