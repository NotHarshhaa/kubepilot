"""KubePilot Model Context Protocol (MCP) server package."""

from app.mcp.server import MCPServer, run_stdio_server

__all__ = ["MCPServer", "run_stdio_server"]
