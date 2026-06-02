"""MCP client package for HiLite.

Wraps the MCP SDK to provide tool discovery, connection management,
and synchronous dispatch over a dedicated asyncio event loop thread.
"""

from __future__ import annotations

from hilite.mcp.client import MCPClient

__all__ = ["MCPClient"]
