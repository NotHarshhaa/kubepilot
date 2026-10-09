"""Model Context Protocol (MCP) JSON-RPC protocol models and utilities."""

import inspect
from typing import Any, get_type_hints

from app.tools.registry import ToolSpec


def python_type_to_json_schema(py_type: Any) -> str:
    """Map python types to JSON schema types."""
    if py_type is int:
        return "integer"
    if py_type is float:
        return "number"
    if py_type is bool:
        return "boolean"
    if py_type in (list, list[str]):
        return "array"
    if py_type is dict:
        return "object"
    return "string"


def tool_spec_to_mcp_tool(spec: ToolSpec) -> dict[str, Any]:
    """Convert a KubePilot ToolSpec into an MCP tool definition with JSON Schema."""
    sig = inspect.signature(spec.func)
    try:
        type_hints = get_type_hints(spec.func)
    except Exception:
        type_hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        py_type = type_hints.get(name, str)
        json_type = python_type_to_json_schema(py_type)

        prop_doc = f"Parameter '{name}'"
        properties[name] = {
            "type": json_type,
            "description": prop_doc,
        }

        if param.default is inspect.Parameter.empty:
            required.append(name)

    return {
        "name": spec.name,
        "description": f"[{spec.category.value.upper()}] {spec.description}",
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }
