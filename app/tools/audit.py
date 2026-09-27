"""Append-only audit log for tool calls, approvals, and security-relevant events."""

import json
import os
import time

from app.config.settings import get_settings


def log_event(event: str, **fields) -> None:
    path = get_settings().audit_log_path
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        **fields,
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, default=str) + "\n")
