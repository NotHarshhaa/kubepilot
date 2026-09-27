"""Tests for incident reporting and post-mortem generation."""

from fastapi.testclient import TestClient

from app.main import create_app
from app.reporting import generate_post_mortem

client = TestClient(create_app())


def test_generate_post_mortem_structure():
    report = generate_post_mortem(
        title="Payment Service Outage",
        summary="Payment gateway pods crashed after schema migration.",
        root_cause="Missing database index caused connection timeouts and OOMKilled.",
        severity="P1 - Critical",
        namespace="payments",
        impacted_services=["payment-api", "billing-worker"],
        timeline=[
            {"time": "14:00 UTC", "component": "migration-job", "description": "Migration applied."},
            {
                "time": "14:05 UTC",
                "component": "payment-api",
                "description": "Pods entered CrashLoopBackOff.",
            },
        ],
        evidence=[
            "Pod payment-api restarted 8 times",
            "Prometheus: http_requests_total 504 spikes to 420 rps",
        ],
        remediation_actions=[
            "Rolled back deployment to previous revision",
            "Added missing compound index to payments table",
        ],
        action_items=[
            "Enforce dry-run database migration in CI pipeline",
            "Set explicit memory limits and liveness probe thresholds",
        ],
    )

    assert "# Incident Post-Mortem: Payment Service Outage" in report
    assert "P1 - Critical" in report
    assert "payments" in report
    assert "payment-api, billing-worker" in report
    assert "Missing database index" in report
    assert "14:00 UTC" in report
    assert "Prometheus: http_requests_total 504 spikes" in report
    assert "Rolled back deployment to previous revision" in report
    assert "Enforce dry-run database migration" in report


def test_incident_report_api_endpoint():
    payload = {
        "title": "Auth API Latency Spike",
        "summary": "Auth API experienced 10s latency.",
        "root_cause": "Thread starvation in connection pool.",
        "severity": "P2 - Major",
        "namespace": "auth",
        "impacted_services": ["auth-api"],
    }
    response = client.post("/reports/incident", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "report_markdown" in data
    assert "# Incident Post-Mortem: Auth API Latency Spike" in data["report_markdown"]
