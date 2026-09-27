"""Formatting helpers for the Kubernetes tools, tested with lightweight stand-ins."""

from types import SimpleNamespace

from app.tools.kubernetes import tools as k8s
from app.tools.loki.loki import _parse_duration as loki_duration
from app.tools.prometheus.prometheus import _parse_duration as prom_duration


def _make_pod(name="nginx-abc", phase="Running", restarts=3, ready=True, waiting=None):
    state = SimpleNamespace(waiting=waiting, running=SimpleNamespace(), terminated=None)
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, creation_timestamp=None, namespace="default"),
        spec=SimpleNamespace(node_name="node-1"),
        status=SimpleNamespace(
            phase=phase,
            container_statuses=[
                SimpleNamespace(
                    name="app",
                    ready=ready,
                    restart_count=restarts,
                    state=state,
                )
            ],
        ),
    )


def test_format_pod_shows_restarts_and_state():
    pod = _make_pod()
    line = k8s._format_pod(pod)
    assert "nginx-abc" in line
    assert "restarts=3" in line
    assert "ready=1/1" in line
    assert "running" in line


def test_format_pod_shows_waiting_reason():
    pod = _make_pod(waiting=SimpleNamespace(reason="CrashLoopBackOff"))
    line = k8s._format_pod(pod)
    assert "waiting(CrashLoopBackOff)" in line


def test_age_formats_minutes_hours_days():
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    assert k8s._age(now - timedelta(minutes=5)) == "5m"
    assert k8s._age(now - timedelta(hours=3)) == "3h"
    assert k8s._age(now - timedelta(days=2)) == "2d"
    assert k8s._age(None) == "unknown"


def test_duration_parsers_agree():
    for text, seconds in [("30m", 1800), ("1h", 3600), ("2d", 172800)]:
        assert prom_duration(text) == seconds
        assert loki_duration(text) == seconds
