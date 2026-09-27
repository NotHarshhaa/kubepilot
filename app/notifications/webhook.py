"""Webhook notification dispatcher for approval lifecycle events (Slack, Discord, generic)."""

import logging
from datetime import UTC, datetime

import httpx

from app.config.settings import get_settings

logger = logging.getLogger(__name__)


def dispatch_notification(
    event_type: str,
    approval_id: str,
    tool: str,
    args: dict,
    status: str,
    result: str | None = None,
    note: str | None = None,
) -> None:
    """Dispatch event notification to configured webhook destinations (Slack, Discord, generic).
    Never raises an exception — errors are logged and swallowed so operations continue uninterrupted.
    """
    settings = get_settings()
    now_iso = datetime.now(UTC).isoformat()

    payload = {
        "event": event_type,
        "approval_id": approval_id,
        "tool": tool,
        "args": args,
        "status": status,
        "result": result,
        "note": note,
        "timestamp": now_iso,
    }

    # 1. Generic webhook
    if settings.webhook_url:
        try:
            httpx.post(settings.webhook_url, json=payload, timeout=5.0)
        except Exception as exc:
            logger.warning(f"Failed to post to generic webhook {settings.webhook_url}: {exc}")

    # 2. Slack Incoming Webhook
    if settings.slack_webhook_url:
        try:
            status_emoji = {
                "pending": ":hourglass_flowing_sand:",
                "approved": ":white_check_mark:",
                "denied": ":x:",
            }.get(status, ":information_source:")

            slack_payload = {
                "text": f"KubePilot Action {event_type.replace('_', ' ').title()}: `{tool}` ({approval_id})",
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": f"KubePilot {status_emoji} Action {event_type.replace('_', ' ').title()}",
                            "emoji": True,
                        },
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Tool:*\n`{tool}`"},
                            {"type": "mrkdwn", "text": f"*Approval ID:*\n`{approval_id}`"},
                            {"type": "mrkdwn", "text": f"*Status:*\n*{status.upper()}*"},
                            {"type": "mrkdwn", "text": f"*Time:*\n{now_iso}"},
                        ],
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*Parameters:*\n```{args}```"},
                    },
                ],
            }
            if note:
                slack_payload["blocks"].append(
                    {"type": "section", "text": {"type": "mrkdwn", "text": f"*Decision Note:*\n{note}"}}
                )
            if result:
                slack_payload["blocks"].append(
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*Execution Result:*\n```{result[:500]}```"},
                    }
                )

            httpx.post(settings.slack_webhook_url, json=slack_payload, timeout=5.0)
        except Exception as exc:
            logger.warning(f"Failed to post to Slack webhook: {exc}")

    # 3. Discord Webhook
    if settings.discord_webhook_url:
        try:
            color = {
                "pending": 0xF59E0B,  # amber/yellow
                "approved": 0x10B981,  # green
                "denied": 0xEF4444,  # red
            }.get(status, 0x3B82F6)  # blue default

            fields = [
                {"name": "Tool", "value": f"`{tool}`", "inline": True},
                {"name": "Approval ID", "value": f"`{approval_id}`", "inline": True},
                {"name": "Status", "value": status.upper(), "inline": True},
            ]
            if note:
                fields.append({"name": "Note", "value": note, "inline": False})
            if result:
                fields.append({"name": "Result", "value": f"```{result[:500]}```", "inline": False})

            discord_payload = {
                "embeds": [
                    {
                        "title": f"KubePilot: {event_type.replace('_', ' ').title()}",
                        "color": color,
                        "fields": fields,
                        "description": f"Args: `{args}`",
                        "timestamp": now_iso,
                    }
                ]
            }
            httpx.post(settings.discord_webhook_url, json=discord_payload, timeout=5.0)
        except Exception as exc:
            logger.warning(f"Failed to post to Discord webhook: {exc}")
