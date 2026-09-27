"""HTTP API for KubePilot: natural-language queries, streaming, sessions, approvals, and reports."""

import json
from collections.abc import Generator
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent.graph import get_session_history, run_agent, stream_agent
from app.config.settings import get_settings
from app.reporting import generate_post_mortem
from app.tools.approvals import ApprovalError, manager
from app.tools.kubernetes.tools import cluster_health_scan

router = APIRouter()


class QueryRequest(BaseModel):
    question: str
    session_id: str | None = None


class ApprovalDecision(BaseModel):
    note: str | None = None


class IncidentReportRequest(BaseModel):
    title: str
    summary: str
    root_cause: str
    severity: str = "P2 - Major"
    namespace: str = "default"
    impacted_services: list[str] | None = None
    timeline: list[dict] | None = None
    evidence: list[str] | None = None
    remediation_actions: list[str] | None = None
    action_items: list[str] | None = None


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "read_only": settings.read_only,
        "require_approval": settings.require_approval,
        "llm_provider": settings.llm_provider,
        "model_name": settings.model_name,
    }


@router.post("/query")
def query(request: QueryRequest) -> dict:
    try:
        if request.session_id is not None:
            return run_agent(request.question, session_id=request.session_id)
        return run_agent(request.question)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Agent failed: {exc}") from exc


@router.post("/query/stream")
def query_stream(request: QueryRequest):
    """Server-Sent Events (SSE) streaming endpoint for live agent execution."""

    def event_generator() -> Generator[str, None, None]:
        try:
            for event in stream_agent(request.question, session_id=request.session_id):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    history = get_session_history(session_id)
    return {"session_id": session_id, "messages": history}


@router.get("/cluster/scan")
def scan_cluster() -> dict:
    report = cluster_health_scan()
    return {"report": report}


@router.post("/reports/incident")
def create_incident_report(request: IncidentReportRequest) -> dict:
    report_md = generate_post_mortem(
        title=request.title,
        summary=request.summary,
        root_cause=request.root_cause,
        severity=request.severity,
        namespace=request.namespace,
        impacted_services=request.impacted_services,
        timeline=request.timeline,
        evidence=request.evidence,
        remediation_actions=request.remediation_actions,
        action_items=request.action_items,
    )
    return {"report_markdown": report_md}


@router.get("/approvals")
def list_approvals() -> dict:
    return {
        "pending": [asdict(record) for record in manager.list_pending()],
        "history": [asdict(record) for record in manager.list_all()],
    }


@router.post("/approvals/{approval_id}/approve")
def approve(approval_id: str, decision: ApprovalDecision | None = None) -> dict:
    try:
        note = decision.note if decision else None
        record = manager.approve(approval_id, note=note)
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"approval": asdict(record), "note": note}


@router.post("/approvals/{approval_id}/deny")
def deny(approval_id: str, decision: ApprovalDecision | None = None) -> dict:
    try:
        note = decision.note if decision else None
        record = manager.deny(approval_id, note=note)
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"approval": asdict(record), "note": note}
