"""HTTP API for KubePilot: natural-language queries plus the approval workflow."""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agent.graph import run_agent
from app.config.settings import get_settings
from app.tools.approvals import ApprovalError, manager

router = APIRouter()


class QueryRequest(BaseModel):
    question: str


class ApprovalDecision(BaseModel):
    note: str | None = None


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "read_only": settings.read_only,
        "require_approval": settings.require_approval,
        "llm_provider": settings.llm_provider,
    }


@router.post("/query")
def query(request: QueryRequest) -> dict:
    try:
        return run_agent(request.question)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Agent failed: {exc}") from exc


@router.get("/approvals")
def list_approvals() -> dict:
    return {
        "pending": [asdict(record) for record in manager.list_pending()],
        "history": [asdict(record) for record in manager.list_all()],
    }


@router.post("/approvals/{approval_id}/approve")
def approve(approval_id: str, decision: ApprovalDecision | None = None) -> dict:
    try:
        record = manager.approve(approval_id)
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"approval": asdict(record), "note": decision.note if decision else None}


@router.post("/approvals/{approval_id}/deny")
def deny(approval_id: str, decision: ApprovalDecision | None = None) -> dict:
    try:
        record = manager.deny(approval_id)
    except ApprovalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"approval": asdict(record), "note": decision.note if decision else None}
