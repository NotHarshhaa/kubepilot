"""Tests for webhook notifications (generic, Slack, Discord)."""

from unittest.mock import patch

from app.notifications.webhook import dispatch_notification
from app.tools.approvals import manager


def test_dispatch_notification_generic_and_slack(monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "http://example.com/webhook")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "http://hooks.slack.com/services/xxx")
    from app.config import settings as settings_module

    settings_module.get_settings.cache_clear()

    with patch("httpx.post") as mock_post:
        dispatch_notification(
            event_type="approval_requested",
            approval_id="appr-123",
            tool="restart_deployment",
            args={"deployment": "api"},
            status="pending",
        )
        assert mock_post.call_count == 2
        calls = [c[0][0] for c in mock_post.call_args_list]
        assert "http://example.com/webhook" in calls
        assert "http://hooks.slack.com/services/xxx" in calls


def test_dispatch_notification_discord(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "http://discord.com/api/webhooks/xxx")
    from app.config import settings as settings_module

    settings_module.get_settings.cache_clear()

    with patch("httpx.post") as mock_post:
        dispatch_notification(
            event_type="approval_granted",
            approval_id="appr-456",
            tool="scale_deployment",
            args={"deployment": "worker", "replicas": 5},
            status="approved",
            result="scaled to 5",
            note="approved by oncall",
        )
        assert mock_post.call_count == 1
        args, kwargs = mock_post.call_args
        assert args[0] == "http://discord.com/api/webhooks/xxx"
        assert "embeds" in kwargs["json"]
        embed = kwargs["json"]["embeds"][0]
        assert "Approval Granted" in embed["title"]


def test_dispatch_notification_handles_network_failure(monkeypatch):
    monkeypatch.setenv("WEBHOOK_URL", "http://failing.endpoint.local")
    from app.config import settings as settings_module

    settings_module.get_settings.cache_clear()

    with patch("httpx.post", side_effect=Exception("Connection refused")):
        # Must not raise
        dispatch_notification(
            event_type="approval_requested",
            approval_id="appr-789",
            tool="restart_deployment",
            args={},
            status="pending",
        )


def test_approval_manager_triggers_notification(monkeypatch):
    from app.tools import registry
    from app.tools.registry import ToolSpec

    name = "dummy_notif_op"
    registry._REGISTRY[name] = ToolSpec(
        name=name,
        description="dummy",
        category=registry.SafetyCategory.OPERATIONAL,
        func=lambda: "ok",
    )

    with patch("app.tools.approvals.dispatch_notification") as mock_dispatch:
        rec = manager.create(name, {})
        mock_dispatch.assert_called_with("approval_requested", rec.id, name, {}, "pending")

        manager.approve(rec.id, note="looks good")
        mock_dispatch.assert_called_with(
            "approval_granted",
            rec.id,
            name,
            {},
            "approved",
            result="ok",
            note="looks good",
        )
