"""MCP session wrapper -- thin async facade over ClientSession.

Each MCPSession wraps a single MCP server's session and provides
``list_tools()`` and ``call_tool()``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hilite.mcp.transport import TransportConfig, connect_transport

if TYPE_CHECKING:
    from mcp.types import CallToolResult, ListToolsResult, Tool as MCPTool

# Lazy import -- MCP SDK is an optional dependency
try:
    from mcp import ClientSession, Implementation

    _HAS_MCP = True
except ImportError:
    _HAS_MCP = False


class MCPSession:
    """Async session for a single MCP server.

    Usage::

        session = MCPSession("slack", transport_config)
        await session.connect()
        tools = await session.list_tools()
        result = await session.call_tool("slack_post_message", {...})
        await session.close()
    """

    def __init__(self, server_name: str, config: TransportConfig):
        self.server_name = server_name
        self.config = config
        self._session: Any | None = None
        self._streams: tuple[Any, Any] | None = None
        self._transport_ctx: Any | None = None

    async def connect(self) -> None:
        """Open the transport, perform the MCP initialize handshake."""
        if not _HAS_MCP:
            raise RuntimeError(
                "MCP SDK not installed. Run: pip install 'mcp>=1.0.0'"
            )

        self._transport_ctx = connect_transport(self.config)
        read_stream, write_stream = await self._transport_ctx.__aenter__()
        self._streams = (read_stream, write_stream)

        self._session = ClientSession(
            read_stream,
            write_stream,
            client_info=Implementation(name="hilite", version="0.1.0"),
        )
        await self._session.initialize()

    async def close(self) -> None:
        """Close the session and underlying transport."""
        if self._transport_ctx is not None:
            try:
                await self._transport_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            finally:
                self._transport_ctx = None
                self._streams = None
                self._session = None

    async def list_tools(self) -> list[MCPTool]:
        """Return all tools exposed by this server."""
        if self._session is None:
            raise RuntimeError(f"MCP session '{self.server_name}' not connected")
        result: ListToolsResult = await self._session.list_tools()
        return result.tools

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> CallToolResult:
        """Call a tool by name with the given arguments."""
        if self._session is None:
            raise RuntimeError(f"MCP session '{self.server_name}' not connected")
        return await self._session.call_tool(
            tool_name, arguments=arguments or {}
        )

    def __repr__(self) -> str:
        return f"<MCPSession '{self.server_name}' connected={self._session is not None}>"
