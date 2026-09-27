"""Loki log query tool (read-only)."""

import re
import time
from datetime import UTC, datetime

import httpx

from app.config.settings import get_settings
from app.tools.registry import SafetyCategory, register


def _parse_duration(duration: str) -> float:
    match = re.fullmatch(r"(\d+)([smhd])", duration.strip())
    if not match:
        raise ValueError(f"Invalid duration '{duration}' (expected forms like 30m, 1h, 24h).")
    value, unit = int(match.group(1)), match.group(2)
    return value * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def _format_streams(result: list) -> str:
    lines = []
    for stream in result:
        labels = stream.get("stream", {})
        prefix = ",".join(f"{k}={v}" for k, v in labels.items())
        for ts, line in stream.get("values", []):
            when = datetime.fromtimestamp(int(ts) / 1e9, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"{when} [{prefix}] {line}")
    return "\n".join(lines) if lines else "No log lines matched the query."


@register(SafetyCategory.READ)
def loki_query_logs(query: str, duration: str = "1h", limit: int = 100) -> str:
    """Query logs from Loki with a LogQL selector over the last 'duration'.
    Example: {namespace="default", pod="nginx-abc123"}"""
    try:
        end = time.time()
        params = {
            "query": query,
            "limit": limit,
            "start": int((end - _parse_duration(duration)) * 1e9),
            "end": int(end * 1e9),
            "direction": "backward",
        }
        url = get_settings().loki_url.rstrip("/") + "/loki/api/v1/query_range"
        response = httpx.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "success":
            raise RuntimeError(f"Loki returned error: {data.get('error', data)}")
        return _format_streams(data["data"].get("result", []))
    except Exception as exc:
        return f"Error querying Loki: {exc}"
