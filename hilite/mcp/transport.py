"""MCP transport wrappers -- stdio, HTTP/SSE, streamable-http.

Abstracts over the MCP SDK transport clients so that the session layer
does not need to know which transport is in use.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncGenerator

if TYPE_CHECKING:
    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

# Lazy import -- MCP SDK is an optional dependency
try:
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    _HAS_MCP = True
except ImportError:
    _HAS_MCP = False


# ---------------------------------------------------------------------------
# Transport config dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransportConfig:
    """Normalized transport configuration for a single MCP server."""

    server_name: str
    # stdio fields
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    cwd: str | None = None
    # HTTP/SSE fields
    url: str | None = None
    transport: str = "streamable-http"  # "streamable-http" | "sse"
    headers: dict[str, str] | None = None
    # TLS fields
    client_cert: str | tuple[str, ...] | None = None
    ssl_verify: bool = True
    # OAuth fields
    oauth_provider: Any | None = None
    # Common
    timeout: int = 60
    connect_timeout: int = 30


# ---------------------------------------------------------------------------
# Stdio transport
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _stdio_transport(
    cfg: TransportConfig,
) -> AsyncGenerator[
    tuple[MemoryObjectReceiveStream, MemoryObjectSendStream], None
]:
    """Spawn an MCP server subprocess and yield the JSON-RPC streams."""
    if not _HAS_MCP:
        raise RuntimeError("MCP SDK not installed. Run: pip install 'mcp>=1.0.0'")

    params = StdioServerParameters(
        command=cfg.command or "",
        args=cfg.args or [],
        env=cfg.env,
        cwd=cfg.cwd,
    )

    async with stdio_client(params, errlog=sys.stderr) as (read_stream, write_stream):
        yield (read_stream, write_stream)


# ---------------------------------------------------------------------------
# HTTP / SSE transport
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _http_transport(
    cfg: TransportConfig,
) -> AsyncGenerator[
    tuple[MemoryObjectReceiveStream, MemoryObjectSendStream], None
]:
    """Connect to an MCP server over HTTP or SSE."""
    if not _HAS_MCP:
        raise RuntimeError("MCP SDK not installed. Run: pip install 'mcp>=1.0.0'")

    from mcp.client.sse import sse_client
    from mcp.client.streamable_http import streamable_http_client

    url = cfg.url or ""

    if cfg.transport == "sse":
        # SSE transport via MCP SDK
        factory = _make_http_client_factory(cfg.client_cert, cfg.ssl_verify)
        async with sse_client(
            url,
            headers=cfg.headers,
            timeout=cfg.connect_timeout,
            httpx_client_factory=factory,
        ) as (read_stream, write_stream):
            yield (read_stream, write_stream)
    else:
        # Streamable HTTP (default)
        import httpx

        kwargs: dict[str, Any] = {
            "follow_redirects": True,
            "verify": cfg.ssl_verify,
            "timeout": cfg.connect_timeout,
        }
        if cfg.headers:
            kwargs["headers"] = cfg.headers
        if cfg.client_cert:
            kwargs["cert"] = cfg.client_cert

        http_client = httpx.AsyncClient(**kwargs)
        try:
            async with streamable_http_client(
                url, http_client=http_client
            ) as (read_stream, write_stream, _get_session_id):
                yield (read_stream, write_stream)
        finally:
            await http_client.aclose()


def _make_http_client_factory(
    cert: str | tuple[str, ...] | None,
    verify: bool,
):
    """Build an httpx client factory for SSE transport.

    The MCP SDK's sse_client accepts an ``httpx_client_factory`` callback.
    We inject cert/verify into the factory so mTLS works over SSE.
    """
    def factory(
        headers: dict[str, str] | None = None,
        timeout: Any | None = None,
        auth: Any | None = None,
    ):
        import httpx

        kwargs: dict[str, Any] = {
            "follow_redirects": True,
            "verify": verify,
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        if headers is not None:
            kwargs["headers"] = headers
        if auth is not None:
            kwargs["auth"] = auth
        if cert is not None:
            kwargs["cert"] = cert
        return httpx.AsyncClient(**kwargs)

    return factory


# ---------------------------------------------------------------------------
# Transport dispatcher
# ---------------------------------------------------------------------------


@asynccontextmanager
async def connect_transport(
    cfg: TransportConfig,
) -> AsyncGenerator[
    tuple[MemoryObjectReceiveStream, MemoryObjectSendStream], None
]:
    """Connect to an MCP server using the appropriate transport.

    Dispatches to stdio or HTTP/SSE based on the config fields present.
    """
    if cfg.command:
        async with _stdio_transport(cfg) as streams:
            yield streams
    elif cfg.url:
        async with _http_transport(cfg) as streams:
            yield streams
    else:
        raise ValueError(
            f"MCP server '{cfg.server_name}': neither 'command' nor 'url' configured"
        )
