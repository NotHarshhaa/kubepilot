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
