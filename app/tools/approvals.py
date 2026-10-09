"""Human-in-the-loop approval flow for operational (non-read) tools.

The agent never executes operational tools directly: the guard turns them into
a proposal with an approval ID. A human approves or denies via the API, and the
action only runs on approval. Every transition is written to the audit log.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from app.config.settings import get_settings
from app.notifications import dispatch_notification
from app.tools.audit import log_event
from app.tools.registry import ToolSpec


@dataclass
class ApprovalRecord:
    id: str
    tool: str
    args: dict
    requested_at: str
    status: str = "pending"
    result: str | None = None
    note: str | None = None


class ApprovalError(Exception):
    pass


class ApprovalManager:
    def __init__(self) -> None:
        self._records: dict[str, ApprovalRecord] = {}

    def create(self, tool: str, args: dict) -> ApprovalRecord:
        record = ApprovalRecord(
            id=uuid.uuid4().hex[:12],
            tool=tool,
            args=args,
            requested_at=datetime.now(UTC).isoformat(),
        )
        self._records[record.id] = record
        log_event("approval_requested", approval_id=record.id, tool=tool, args=args)
        dispatch_notification("approval_requested", record.id, tool, args, "pending")
        return record

    def get(self, approval_id: str) -> ApprovalRecord:
        try:
            return self._records[approval_id]
        except KeyError:
            raise ApprovalError(f"Unknown approval ID: {approval_id}") from None

    def list_pending(self) -> list[ApprovalRecord]:
        return [r for r in self._records.values() if r.status == "pending"]

    def list_all(self) -> list[ApprovalRecord]:
        return list(self._records.values())

    def approve(self, approval_id: str, note: str | None = None) -> ApprovalRecord:
        record = self.get(approval_id)
        if record.status != "pending":
            raise ApprovalError(f"Approval {approval_id} is already '{record.status}'.")
        from app.tools.registry import get_tool

        spec = get_tool(record.tool)
        if spec is None:
            raise ApprovalError(f"Tool '{record.tool}' is not registered.")
        record.result = str(spec.func(**record.args))
        if record.tool in ("restart_deployment", "scale_deployment", "rollback_deployment"):
            try:
                from app.remediation.verification import run_post_action_verification

                dep_name = record.args.get("deployment") or record.args.get("name")
                ns = record.args.get("namespace", "")
                if dep_name:
                    verif = run_post_action_verification(
                        target=dep_name, namespace=ns, action_type=record.tool
                    )
                    record.result += (
                        f"\n[Post-Action Verification: {verif.verdict.value}] "
                        f"Ready: {verif.ready_replicas}/{verif.desired_replicas} replicas."
                    )
            except Exception:
                pass
        record.status = "approved"
        record.note = note
        log_event("approval_granted", approval_id=record.id, tool=record.tool, note=note)
        dispatch_notification(
            "approval_granted",
            record.id,
            record.tool,
            record.args,
            "approved",
            result=record.result,
            note=note,
        )
        return record

    def deny(self, approval_id: str, note: str | None = None) -> ApprovalRecord:
        record = self.get(approval_id)
        if record.status != "pending":
            raise ApprovalError(f"Approval {approval_id} is already '{record.status}'.")
        record.status = "denied"
        record.note = note
        log_event("approval_denied", approval_id=record.id, tool=record.tool, note=note)
        dispatch_notification(
            "approval_denied",
            record.id,
            record.tool,
            record.args,
            "denied",
            note=note,
        )
        return record


manager = ApprovalManager()


def guarded_tool(spec: ToolSpec):
    """Wrap an operational tool so the agent can only *propose* it."""

    def wrapper(**kwargs) -> str:
        settings = get_settings()
        if settings.read_only:
            log_event("tool_blocked_read_only", tool=spec.name, args=kwargs)
            return (
                f"Blocked: KubePilot is running in read-only mode, so '{spec.name}' "
                f"was not executed. Report this to the user."
            )
        if settings.require_approval:
            record = manager.create(spec.name, kwargs)
            return (
                f"Action '{spec.name}' was proposed but NOT executed. It requires human "
                f"approval. Approval ID: {record.id}. Tell the user to approve or deny it "
                f"via POST /approvals/{record.id}/approve (or /deny)."
            )
        result = str(spec.func(**kwargs))
        log_event("tool_executed", tool=spec.name, args=kwargs)
        return result

    return wrapper
