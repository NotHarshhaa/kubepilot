"""Tests for the KubePilot interactive CLI module."""

from unittest.mock import patch

from app import cli
from app.tools import registry
from app.tools.approvals import manager
from app.tools.registry import ToolSpec


def test_cli_scan():
    with patch("app.cli.cluster_health_scan", return_value="All healthy") as mock_scan:
        cli.cmd_scan()
        mock_scan.assert_called_once()


def test_cli_approvals_workflow():
    name = "cli_test_tool"
    registry._REGISTRY[name] = ToolSpec(
        name=name,
        description="test tool",
        category=registry.SafetyCategory.OPERATIONAL,
        func=lambda **kwargs: "success output",
    )
    rec = manager.create(name, {"foo": "bar"})

    # List
    cli.cmd_approvals_list()

    # Approve
    cli.cmd_approvals_approve(rec.id, note="cli approved")
    assert rec.status == "approved"
    assert rec.result == "success output"

    # Deny another
    rec2 = manager.create(name, {"baz": "qux"})
    cli.cmd_approvals_deny(rec2.id, note="cli denied")
    assert rec2.status == "denied"


def test_cli_handle_question():
    def fake_stream(q, session_id=None):
        yield {"type": "tool_call", "tool": "get_pods", "args": {"namespace": "default"}}
        yield {"type": "done", "answer": "All pods healthy and ready."}

    with patch("app.cli.stream_agent", fake_stream):
        cli._handle_question("check status", "sess-test")


def test_cli_main_dispatch():
    with patch("app.cli.cmd_scan") as mock_scan:
        with patch("sys.argv", ["kubepilot", "scan"]):
            cli.main()
            mock_scan.assert_called_once()


def test_cli_runbook_and_gitops_dispatch():
    with patch("app.cli.cmd_runbook") as mock_rb:
        with patch("sys.argv", ["kubepilot", "runbook", "my-pod", "-n", "prod", "-t", "oom"]):
            cli.main()
            mock_rb.assert_called_once_with(pod_name="my-pod", namespace="prod", runbook_type="oom")

    with patch("app.cli.cmd_helm") as mock_helm:
        with patch("sys.argv", ["kubepilot", "helm", "-n", "kube-system"]):
            cli.main()
            mock_helm.assert_called_once_with(namespace="kube-system")

    with patch("app.cli.cmd_argocd") as mock_argo:
        with patch("sys.argv", ["kubepilot", "argocd", "--app", "my-app", "-n", "argocd"]):
            cli.main()
            mock_argo.assert_called_once_with(namespace="argocd", app_name="my-app")

    with patch("app.cli.cmd_mcp") as mock_mcp:
        with patch("sys.argv", ["kubepilot", "mcp"]):
            cli.main()
            mock_mcp.assert_called_once()

    with patch("app.cli.cmd_advisor") as mock_adv:
        with patch("sys.argv", ["kubepilot", "advisor", "-w", "my-app", "-n", "prod"]):
            cli.main()
            mock_adv.assert_called_once_with(namespace="prod", workload="my-app")

    with patch("app.cli.cmd_verify") as mock_ver:
        with patch("sys.argv", ["kubepilot", "verify", "web-api", "-n", "prod", "-a", "rollback"]):
            cli.main()
            mock_ver.assert_called_once_with(target="web-api", namespace="prod", action_type="rollback")
