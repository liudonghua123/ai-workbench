"""MCP exposure layer for the workbench control plane."""

from app.mcp.server import http_app, mcp

__all__ = ["http_app", "mcp"]
