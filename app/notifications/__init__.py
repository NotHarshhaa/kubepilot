"""Notification system for KubePilot."""

from app.notifications.webhook import dispatch_notification

__all__ = ["dispatch_notification"]
