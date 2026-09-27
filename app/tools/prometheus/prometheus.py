"""Prometheus query tools (read-only)."""

import time

import httpx

from app.config.settings import get_settings
from app.tools.registry import SafetyCategory, register


def _get(path: str, params: dict) -> dict:
    url = get_settings().prometheus_url.rstrip("/") + path
    response = httpx.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    if data.get("status") != "success":
        raise RuntimeError(f"Prometheus returned error: {data.get('error', data)}")
    return data["data"]


def _format_result(data: dict) -> str:
    result_type = data.get("resultType")
    items = data.get("result", [])
    if result_type == "scalar" or (result_type is None and isinstance(items, list) and not items):
        if result_type == "scalar":
            return f"Result (scalar): {items[1]}"
        return "No data."
    if not items:
        return "No data."
    lines = []
    for item in items:
        metric = item.get("metric", {})
        label = "{" + ", ".join(f'{k}="{v}"' for k, v in metric.items()) + "}" if metric else "{}"
        if item.get("value"):
            lines.append(f"{label} = {item['value'][1]}")
        elif item.get("values"):
            series = item["values"]
            lines.append(f"{label} = {len(series)} points, latest={series[-1][1]} at ts={series[-1][0]}")
    return "\n".join(lines)


@register(SafetyCategory.READ)
def prom_query(query: str) -> str:
    """Run an instant PromQL query against Prometheus.
    Example: sum(rate(http_requests_total{status=~"5.."}[5m])) by (pod)"""
    try:
        return _format_result(_get("/api/v1/query", {"query": query}))
    except Exception as exc:
        return f"Error querying Prometheus: {exc}"


@register(SafetyCategory.READ)
def prom_query_range(query: str, duration: str = "1h", step: str = "60s") -> str:
    """Run a PromQL range query over the last 'duration' (e.g. 30m, 1h, 24h) with a 'step' interval."""
    try:
        end = time.time()
        start = end - _parse_duration(duration)
        data = _get("/api/v1/query_range", {"query": query, "start": start, "end": end, "step": step})
        return _format_result(data)
    except Exception as exc:
        return f"Error querying Prometheus range: {exc}"


def _parse_duration(duration: str) -> float:
    import re

    match = re.fullmatch(r"(\d+)([smhd])", duration.strip())
    if not match:
        raise ValueError(f"Invalid duration '{duration}' (expected forms like 30m, 1h, 24h).")
    value, unit = int(match.group(1)), match.group(2)
    return value * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
