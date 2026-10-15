from fastapi.testclient import TestClient

from app.api import routes
from app.main import create_app
from app.tools import registry
from app.tools.approvals import manager
from app.tools.registry import ToolSpec

client = TestClient(create_app())


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "read_only" in body


def test_query_returns_agent_result(monkeypatch):
    monkeypatch.setattr(
        routes,
        "run_agent",
        lambda question: {
            "question": question,
            "answer": "CrashLoopBackOff due to bad image tag.",
            "tools_used": ["get_pods", "get_logs"],
        },
    )
    response = client.post("/query", json={"question": "why is nginx restarting?"})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"].startswith("CrashLoopBackOff")
    assert body["tools_used"] == ["get_pods", "get_logs"]


def test_query_requires_question():
    response = client.post("/query", json={})
    assert response.status_code == 422


def test_approve_endpoint_executes_pending_action(monkeypatch):
    calls: list = []

    def dummy_op(value: str = "x") -> str:
        calls.append(value)
        return "executed"

    name = "dummy_api_action"
    registry._REGISTRY[name] = ToolSpec(
        name=name,
        description="dummy",
        category=registry.SafetyCategory.OPERATIONAL,
        func=dummy_op,
    )
    monkeypatch.setenv("REQUIRE_APPROVAL", "true")
    monkeypatch.setenv("READ_ONLY", "false")
    from app.config import settings as settings_module

    settings_module.get_settings.cache_clear()

    record = manager.create(name, {"value": "v1"})

    listed = client.get("/approvals").json()
    assert any(p["id"] == record.id for p in listed["pending"])

    denied = client.post(f"/approvals/{record.id}/deny")
    assert denied.status_code == 200
    assert denied.json()["approval"]["status"] == "denied"
    assert calls == []

    record2 = manager.create(name, {"value": "v2"})
    approved = client.post(f"/approvals/{record2.id}/approve", json={"note": "lgtm"})
    assert approved.status_code == 200
    assert approved.json()["approval"]["result"] == "executed"
    assert approved.json()["note"] == "lgtm"
    assert calls == ["v2"]


def test_approve_unknown_id_conflict():
    response = client.post("/approvals/bogus/approve")
    assert response.status_code in (404, 409)


def test_diagnostics_runbook_endpoint(monkeypatch):
    from app.diagnostics import runbooks

    monkeypatch.setattr(runbooks, "run_diagnostic_runbook", lambda **kwargs: "Mock Diagnostic Report")

    response = client.post("/diagnostics/runbook", json={"pod_name": "api-pod", "namespace": "default"})
    assert response.status_code == 200
    assert response.json()["report"] == "Mock Diagnostic Report"


def test_gitops_endpoints(monkeypatch):
    from app.tools.gitops import argocd, helm

    monkeypatch.setattr(helm, "helm_list", lambda **kwargs: "Mock Helm List")
    monkeypatch.setattr(argocd, "get_argocd_apps", lambda **kwargs: "Mock ArgoCD Apps")

    res_helm = client.get("/gitops/helm")
    assert res_helm.status_code == 200
    assert res_helm.json()["output"] == "Mock Helm List"

    res_argo = client.get("/gitops/argocd")
    assert res_argo.status_code == 200
    assert res_argo.json()["output"] == "Mock ArgoCD Apps"


def test_advisor_and_verification_endpoints(monkeypatch):
    from app.advisor import resources
    from app.remediation import verification

    monkeypatch.setattr(resources, "analyze_resource_optimization", lambda **kwargs: "Mock Advisor Report")
    monkeypatch.setattr(verification, "verify_remediation", lambda **kwargs: "Mock Verify Report")

    res_adv = client.get("/advisor/resources?namespace=default")
    assert res_adv.status_code == 200
    assert res_adv.json()["report"] == "Mock Advisor Report"

    res_ver = client.post(
        "/remediation/verify",
        json={"target": "api-gateway", "namespace": "default", "action_type": "restart"},
    )
    assert res_ver.status_code == 200
    assert res_ver.json()["report"] == "Mock Verify Report"
