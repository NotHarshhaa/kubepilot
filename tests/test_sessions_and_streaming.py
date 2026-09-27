"""Tests for multi-turn sessions, streaming responses, and cluster scan endpoints."""

from fastapi.testclient import TestClient

from app.api import routes
from app.main import create_app

client = TestClient(create_app())


def test_query_with_session_id(monkeypatch):
    monkeypatch.setattr(
        routes,
        "run_agent",
        lambda question, session_id=None: {
            "question": question,
            "answer": "Diagnosed pod restart.",
            "tools_used": ["get_pods"],
            "session_id": session_id or "test-sess-1",
        },
    )
    res = client.post("/query", json={"question": "check pods", "session_id": "sess-xyz"})
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"] == "sess-xyz"
    assert data["answer"] == "Diagnosed pod restart."


def test_query_stream_endpoint(monkeypatch):
    def fake_stream(question, session_id=None):
        yield {"type": "tool_call", "tool": "get_pods", "args": {}, "session_id": session_id}
        yield {"type": "content", "content": "Pods are running.", "session_id": session_id}
        yield {
            "type": "done",
            "question": question,
            "answer": "All pods healthy.",
            "tools_used": ["get_pods"],
            "session_id": session_id,
        }

    monkeypatch.setattr(routes, "stream_agent", fake_stream)

    res = client.post("/query/stream", json={"question": "how is cluster?", "session_id": "stream-sess"})
    assert res.status_code == 200
    assert "text/event-stream" in res.headers["content-type"]
    text = res.text
    assert 'data: {"type": "tool_call", "tool": "get_pods"' in text
    assert 'data: {"type": "done"' in text


def test_session_history_endpoint(monkeypatch):
    monkeypatch.setattr(
        routes,
        "get_session_history",
        lambda session_id: [
            {"role": "user", "content": "why is pod restarting?"},
            {"role": "assistant", "content": "OOMKilled."},
        ],
    )
    res = client.get("/sessions/sess-history-test")
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"] == "sess-history-test"
    assert len(data["messages"]) == 2


def test_cluster_scan_endpoint(monkeypatch):
    monkeypatch.setattr(
        routes,
        "cluster_health_scan",
        lambda: "Cluster Health: 0 issues.",
    )
    res = client.get("/cluster/scan")
    assert res.status_code == 200
    assert res.json()["report"] == "Cluster Health: 0 issues."
