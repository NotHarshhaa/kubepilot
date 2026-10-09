"""KubePilot Model Context Protocol (MCP) Server.

Exposes KubePilot tools, resources, and diagnostic prompts over standard MCP (JSON-RPC 2.0).
Compatible with Claude Desktop, Cursor, Zed, Windsurf, Claude Code, and any MCP client.
"""

import json
import logging
import sys
from typing import Any

from app.config.settings import get_settings
from app.mcp.protocol import tool_spec_to_mcp_tool
from app.tools.approvals import guarded_tool
from app.tools.kubernetes.tools import cluster_health_scan, core
from app.tools.registry import SafetyCategory, all_tools, get_tool

logger = logging.getLogger("kubepilot.mcp")
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="[MCP] %(message)s")

PROTOCOL_VERSION = "2024-11-05"


class MCPServer:
    """Model Context Protocol server for KubePilot."""

    def __init__(self) -> None:
        self.server_info = {
            "name": "kubepilot",
            "version": "0.1.0",
        }

    def handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": True},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False},
            },
            "serverInfo": self.server_info,
        }

    def handle_tools_list(self) -> dict[str, Any]:
        tools = [tool_spec_to_mcp_tool(spec) for spec in all_tools()]
        return {"tools": tools}

    def handle_tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments", {})

        spec = get_tool(name)
        if not spec:
            return {
                "content": [{"type": "text", "text": f"Error: Tool '{name}' not found."}],
                "isError": True,
            }

        try:
            if spec.category is SafetyCategory.READ:
                result = str(spec.func(**arguments))
            else:
                # Wrap operational tool with approval guard
                guarded = guarded_tool(spec)
                result = str(guarded(**arguments))

            return {
                "content": [{"type": "text", "text": result}],
                "isError": False,
            }
        except Exception as exc:
            return {
                "content": [{"type": "text", "text": f"Error executing '{name}': {exc}"}],
                "isError": True,
            }

    def handle_resources_list(self) -> dict[str, Any]:
        return {
            "resources": [
                {
                    "uri": "k8s://cluster/health",
                    "name": "Cluster Health Triage",
                    "description": "Live cluster-wide health status across all nodes, pods, and deployments.",
                    "mimeType": "text/plain",
                },
                {
                    "uri": "k8s://cluster/namespaces",
                    "name": "Kubernetes Namespaces",
                    "description": "List of active namespaces in the cluster.",
                    "mimeType": "application/json",
                },
                {
                    "uri": "k8s://advisor/resources",
                    "name": "Resource & Cost Optimization Advisory",
                    "description": "AI right-sizing recommendations and projected monthly cloud savings.",
                    "mimeType": "text/plain",
                },
                {
                    "uri": "k8s://settings",
                    "name": "KubePilot Configuration",
                    "description": "Current KubePilot settings, safety mode, and provider.",
                    "mimeType": "application/json",
                },
            ]
        }

    def handle_resources_read(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = params.get("uri", "")

        if uri == "k8s://cluster/health":
            try:
                report = cluster_health_scan()
                return {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "text/plain",
                            "text": report,
                        }
                    ]
                }
            except Exception as exc:
                return {
                    "contents": [
                        {"uri": uri, "mimeType": "text/plain", "text": f"Error scanning cluster: {exc}"}
                    ]
                }

        elif uri == "k8s://cluster/namespaces":
            try:
                ns_items = core().list_namespace().items
                namespaces = [n.metadata.name for n in ns_items]
                return {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "application/json",
                            "text": json.dumps({"namespaces": namespaces}, indent=2),
                        }
                    ]
                }
            except Exception as exc:
                return {
                    "contents": [
                        {"uri": uri, "mimeType": "application/json", "text": json.dumps({"error": str(exc)})}
                    ]
                }

        elif uri == "k8s://settings":
            settings = get_settings()
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "application/json",
                        "text": json.dumps(
                            {
                                "llm_provider": settings.llm_provider,
                                "model_name": settings.model_name,
                                "default_namespace": settings.default_namespace,
                                "read_only": settings.read_only,
                                "require_approval": settings.require_approval,
                            },
                            indent=2,
                        ),
                    }
                ]
            }

        elif uri == "k8s://advisor/resources":
            try:
                from app.advisor.resources import analyze_resource_optimization

                report = analyze_resource_optimization()
                return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": report}]}
            except Exception as exc:
                return {
                    "contents": [
                        {"uri": uri, "mimeType": "text/plain", "text": f"Error running advisor: {exc}"}
                    ]
                }

        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": f"Unknown resource URI: {uri}"}]}

    def handle_prompts_list(self) -> dict[str, Any]:
        return {
            "prompts": [
                {
                    "name": "triage_workload",
                    "description": "Deep multi-signal triage of a failing pod or deployment.",
                    "arguments": [
                        {"name": "pod_name", "description": "Name of the failing pod", "required": True},
                        {"name": "namespace", "description": "Kubernetes namespace", "required": False},
                    ],
                },
                {
                    "name": "cluster_scan",
                    "description": "Perform an instant cluster-wide health triage.",
                    "arguments": [],
                },
                {
                    "name": "resource_optimization",
                    "description": "Analyze workload resource requests, limits, and cost savings.",
                    "arguments": [
                        {"name": "workload", "description": "Workload or deployment name", "required": False},
                        {"name": "namespace", "description": "Kubernetes namespace", "required": False},
                    ],
                },
                {
                    "name": "verify_remediation",
                    "description": "Verify post-remediation rollout status, crash rates, and events.",
                    "arguments": [
                        {"name": "target", "description": "Deployment name to verify", "required": True},
                        {"name": "namespace", "description": "Kubernetes namespace", "required": False},
                    ],
                },
                {
                    "name": "helm_triage",
                    "description": "Inspect and troubleshoot a Helm release.",
                    "arguments": [
                        {"name": "release", "description": "Helm release name", "required": True},
                        {"name": "namespace", "description": "Kubernetes namespace", "required": False},
                    ],
                },
            ]
        }

    def handle_prompts_get(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        args = params.get("arguments", {})

        if name == "triage_workload":
            pod = args.get("pod_name", "<pod-name>")
            ns = args.get("namespace", "default")
            prompt_text = (
                f"Please run diagnostic runbooks and investigate the pod '{pod}' in namespace '{ns}'. "
                f"Check recent events, container status, previous crash logs, and correlate with metrics. "
                f"Provide a root-cause analysis and actionable recommendations."
            )
            return {
                "description": f"Triage workload for pod {pod}",
                "messages": [{"role": "user", "content": {"type": "text", "text": prompt_text}}],
            }

        elif name == "cluster_scan":
            prompt_text = (
                "Run a complete cluster health triage using the cluster_health_scan tool. "
                "Identify any unhealthy nodes, crashing pods, degraded deployments, and warning events."
            )
            return {
                "description": "Cluster health scan",
                "messages": [{"role": "user", "content": {"type": "text", "text": prompt_text}}],
            }

        elif name == "resource_optimization":
            wl = args.get("workload", "")
            ns = args.get("namespace", "default")
            prompt_text = (
                f"Run resource optimization advisory for workload '{wl}' in namespace '{ns}'. "
                f"Identify over-provisioned or under-provisioned containers, calculate potential monthly "
                f"cloud savings, and provide recommended YAML patches."
            )
            return {
                "description": "Resource optimization advisory",
                "messages": [{"role": "user", "content": {"type": "text", "text": prompt_text}}],
            }

        elif name == "verify_remediation":
            target = args.get("target", "<target>")
            ns = args.get("namespace", "default")
            prompt_text = (
                f"Verify whether remediation for deployment '{target}' in namespace '{ns}' was successful. "
                f"Check rollout completion, container crash rates, and any new warning events."
            )
            return {
                "description": "Verify remediation",
                "messages": [{"role": "user", "content": {"type": "text", "text": prompt_text}}],
            }

        elif name == "helm_triage":
            release = args.get("release", "<release>")
            ns = args.get("namespace", "default")
            prompt_text = (
                f"Inspect Helm release '{release}' in namespace '{ns}'. "
                f"Check release status, revision history, and underlying workload health."
            )
            return {
                "description": f"Troubleshoot Helm release {release}",
                "messages": [{"role": "user", "content": {"type": "text", "text": prompt_text}}],
            }

        return {
            "description": "Unknown prompt",
            "messages": [
                {"role": "user", "content": {"type": "text", "text": f"Prompt '{name}' not found."}}
            ],
        }

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Dispatch an incoming JSON-RPC request."""
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        # Handle notifications (requests without id)
        if req_id is None:
            if method == "notifications/initialized":
                logger.info("Client initialized successfully.")
            return None

        result: Any = None
        error: dict[str, Any] | None = None

        try:
            if method == "initialize":
                result = self.handle_initialize(params)
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = self.handle_tools_list()
            elif method == "tools/call":
                result = self.handle_tools_call(params)
            elif method == "resources/list":
                result = self.handle_resources_list()
            elif method == "resources/read":
                result = self.handle_resources_read(params)
            elif method == "prompts/list":
                result = self.handle_prompts_list()
            elif method == "prompts/get":
                result = self.handle_prompts_get(params)
            else:
                error = {"code": -32601, "message": f"Method '{method}' not found"}
        except Exception as exc:
            logger.exception("Error processing method %s: %s", method, exc)
            error = {"code": -32603, "message": f"Internal error: {exc}"}

        response: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
        if error:
            response["error"] = error
        else:
            response["result"] = result

        return response


def run_stdio_server() -> None:
    """Run the MCP server over standard input/output."""
    server = MCPServer()
    logger.info("Starting KubePilot MCP Server (stdio transport)...")

    # Read lines or headers from stdin
    while True:
        line = sys.stdin.readline()
        if not line:
            break

        line = line.strip()
        if not line:
            continue

        # Check for HTTP-style Content-Length header
        if line.lower().startswith("content-length:"):
            try:
                length = int(line.split(":", 1)[1].strip())
                # Read until empty line separating headers and body
                while True:
                    sep = sys.stdin.readline()
                    if sep in ("\r\n", "\n", ""):
                        break
                body = sys.stdin.read(length)
                request = json.loads(body)
            except Exception as exc:
                logger.error("Failed to parse Content-Length framed message: %s", exc)
                continue
        else:
            try:
                request = json.loads(line)
            except Exception as exc:
                logger.error("Failed to parse JSON-RPC line: %s", exc)
                continue

        response = server.dispatch(request)
        if response is not None:
            res_str = json.dumps(response)
            # Write newline-delimited JSON-RPC response
            sys.stdout.write(res_str + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_stdio_server()
