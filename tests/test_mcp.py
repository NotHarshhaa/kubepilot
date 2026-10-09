"""Tests for Model Context Protocol (MCP) server."""

from unittest.mock import MagicMock, patch

from app.mcp.protocol import tool_spec_to_mcp_tool
from app.mcp.server import MCPServer
from app.tools.registry import SafetyCategory, ToolSpec


def test_tool_spec_to_mcp_tool():
    def dummy_func(deployment: str, replicas: int, namespace: str = "default") -> str:
        """Dummy scale function."""
        return "ok"

    spec = ToolSpec(
        name="dummy_tool",
        description="Dummy description",
        category=SafetyCategory.OPERATIONAL,
        func=dummy_func,
    )

    mcp_tool = tool_spec_to_mcp_tool(spec)
    assert mcp_tool["name"] == "dummy_tool"
    assert "[OPERATIONAL]" in mcp_tool["description"]
    schema = mcp_tool["inputSchema"]
    assert schema["type"] == "object"
    assert "deployment" in schema["properties"]
    assert schema["properties"]["replicas"]["type"] == "integer"
    assert "deployment" in schema["required"]
    assert "replicas" in schema["required"]
    assert "namespace" not in schema["required"]


def test_mcp_initialize():
    server = MCPServer()
    req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    }
    res = server.dispatch(req)
    assert res["id"] == 1
    assert res["result"]["serverInfo"]["name"] == "kubepilot"
    assert "tools" in res["result"]["capabilities"]


def test_mcp_tools_list():
    server = MCPServer()
    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    res = server.dispatch(req)
    assert res["id"] == 2
    tools = res["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "get_pods" in tool_names
    assert "run_diagnostic_runbook" in tool_names
    assert "helm_list" in tool_names
    assert "get_argocd_apps" in tool_names


@patch("app.mcp.server.get_tool")
def test_mcp_tools_call_read(mock_get_tool):
    mock_func = MagicMock(return_value="pod-1 running")
    mock_spec = ToolSpec(
        name="get_pods",
        description="List pods",
        category=SafetyCategory.READ,
        func=mock_func,
    )
    mock_get_tool.return_value = mock_spec

    server = MCPServer()
    req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "get_pods", "arguments": {"namespace": "default"}},
    }
    res = server.dispatch(req)
    assert res["id"] == 3
    assert res["result"]["isError"] is False
    assert "pod-1 running" in res["result"]["content"][0]["text"]


def test_mcp_resources_and_prompts():
    server = MCPServer()

    # Resources list
    r_list = server.dispatch({"jsonrpc": "2.0", "id": 4, "method": "resources/list"})
    assert any(r["uri"] == "k8s://cluster/health" for r in r_list["result"]["resources"])

    # Prompts list & get
    p_list = server.dispatch({"jsonrpc": "2.0", "id": 5, "method": "prompts/list"})
    assert any(p["name"] == "triage_workload" for p in p_list["result"]["prompts"])

    p_get = server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "prompts/get",
            "params": {"name": "triage_workload", "arguments": {"pod_name": "checkout-api"}},
        }
    )
    assert "checkout-api" in p_get["result"]["messages"][0]["content"]["text"]


def test_mcp_unknown_method():
    server = MCPServer()
    req = {"jsonrpc": "2.0", "id": 7, "method": "unknown/foo"}
    res = server.dispatch(req)
    assert "error" in res
    assert res["error"]["code"] == -32601


def test_mcp_notification_no_response():
    server = MCPServer()
    req = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    res = server.dispatch(req)
    assert res is None
