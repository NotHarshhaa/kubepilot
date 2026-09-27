import pytest

from app.tools import registry
from app.tools.approvals import ApprovalError, guarded_tool, manager
from app.tools.registry import ToolSpec

_counter = {"n": 0}


def _register_dummy(calls: list) -> ToolSpec:
    """Register a unique dummy operational tool and return its spec."""

    def dummy_action(value: str = "x") -> str:
        calls.append(value)
        return f"executed with {value}"

    _counter["n"] += 1
    name = f"dummy_action_{_counter['n']}"
    spec = ToolSpec(
        name=name,
        description="dummy operational tool for tests",
        category=registry.SafetyCategory.OPERATIONAL,
        func=dummy_action,
    )
    registry._REGISTRY[name] = spec
    return spec


def test_guard_creates_approval_and_does_not_execute(configure):
    configure(REQUIRE_APPROVAL="true", READ_ONLY="false")
    calls: list = []
    spec = _register_dummy(calls)
    result = guarded_tool(spec)(value="abc")

    assert calls == [], "operational tool must not execute while pending approval"
    assert "NOT executed" in result
    pending = manager.list_pending()
    assert len(pending) == 1
    assert pending[0].args == {"value": "abc"}


def test_approve_executes_pending_action(configure):
    configure(REQUIRE_APPROVAL="true", READ_ONLY="false")
    calls: list = []
    spec = _register_dummy(calls)
    guarded_tool(spec)(value="abc")

    record = manager.list_pending()[0]
    approved = manager.approve(record.id)
    assert approved.status == "approved"
    assert approved.result == "executed with abc"
    assert calls == ["abc"]


def test_deny_never_executes(configure):
    configure(REQUIRE_APPROVAL="true", READ_ONLY="false")
    calls: list = []
    spec = _register_dummy(calls)
    guarded_tool(spec)()

    record = manager.list_pending()[0]
    denied = manager.deny(record.id)
    assert denied.status == "denied"
    assert calls == []
    with pytest.raises(ApprovalError):
        manager.approve(record.id)


def test_read_only_blocks_operational_tools(configure):
    configure(READ_ONLY="true")
    calls: list = []
    spec = _register_dummy(calls)
    result = guarded_tool(spec)()
    assert "read-only" in result
    assert calls == []
    assert manager.list_pending() == []


def test_guard_executes_directly_when_approval_disabled(configure):
    configure(REQUIRE_APPROVAL="false", READ_ONLY="false")
    calls: list = []
    spec = _register_dummy(calls)
    result = guarded_tool(spec)(value="direct")
    assert result == "executed with direct"
    assert calls == ["direct"]


def test_unknown_approval_id_raises():
    with pytest.raises(ApprovalError):
        manager.get("no-such-id")


def test_approve_twice_is_rejected(configure):
    configure(REQUIRE_APPROVAL="true", READ_ONLY="false")
    spec = _register_dummy([])
    guarded_tool(spec)()
    record = manager.list_pending()[0]

    manager.approve(record.id)
    with pytest.raises(ApprovalError):
        manager.approve(record.id)
