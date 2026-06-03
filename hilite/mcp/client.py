"""MCP client -- discovery, connection management, and tool registration.

Reads MCP server configuration, connects to enabled servers,
normalizes tool schemas, and registers them with the ToolRegistry.
Runs all async MCP I/O on a dedicated background event-loop thread.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from anthropic.types import ToolParam

from hilite.config import load_mcp_config
from hilite.mcp.session import MCPSession
from hilite.mcp.transport import TransportConfig

if TYPE_CHECKING:
    from hilite.tools.registry import ToolRegistry

# Lazy import -- MCP SDK is an optional dependency
try:
    from mcp.types import Tool as MCPTool

    _HAS_MCP = True
except ImportError:
    _HAS_MCP = False

# ---------------------------------------------------------------------------
# Dedicated event-loop thread (singleton)
# ---------------------------------------------------------------------------

_mcp_loop: asyncio.AbstractEventLoop | None = None
_mcp_thread: threading.Thread | None = None


def _ensure_mcp_loop() -> asyncio.AbstractEventLoop:
    """Start (or return) the background MCP asyncio event loop."""
    global _mcp_loop, _mcp_thread
    if _mcp_loop is not None and not _mcp_loop.is_closed():
        return _mcp_loop

    _mcp_loop = asyncio.new_event_loop()
    _mcp_thread = threading.Thread(
        target=_mcp_loop.run_forever,
        name="hilite-mcp-loop",
        daemon=True,
    )
    _mcp_thread.start()
    return _mcp_loop


def _run_on_mcp_loop(coro: Any, timeout: float | None = None) -> Any:
    """Schedule *coro* on the MCP loop and block the caller until done.

    A *timeout* (seconds) bounds how long the caller blocks; on expiry a
    ``TimeoutError`` is raised. Without it a misbehaving server could hang the
    whole CLI indefinitely.
    """
    loop = _ensure_mcp_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)


# ---------------------------------------------------------------------------
# Schema normalization
# ---------------------------------------------------------------------------


def _normalize_schema(schema: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize an MCP tool JSON Schema for Anthropic API compatibility.

    Anthropic's tool schema does not support ``const``, ``patternProperties``,
    complex ``anyOf``/``oneOf``, or several draft-07 keywords.  We do a
    best-effort transform; if the result is still invalid we return *None*
    so the tool is skipped with a warning.
    """
    if not isinstance(schema, dict):
        return None

    result: dict[str, Any] = {}

    for key, value in schema.items():
        # Drop unsupported top-level keywords
        if key in ("$id", "$schema", "definitions", "examples"):
            continue

        # const -> enum: [value]
        if key == "const":
            result["enum"] = [value]
            continue

        # patternProperties -> drop entirely
        if key == "patternProperties":
            continue

        # if/then/else -> drop
        if key in ("if", "then", "else"):
            continue

        # Flatten simple anyOf/oneOf: keep first non-null option
        if key in ("anyOf", "oneOf"):
            if isinstance(value, list):
                for opt in value:
                    if isinstance(opt, dict) and opt.get("type") != "null":
                        merged = dict(opt)
                        merged.update({k: v for k, v in result.items() if k not in merged})
                        result.update(merged)
                        break
            continue

        # Recurse into nested structures
        if key in ("properties", "additionalProperties") and isinstance(value, dict):
            result[key] = _normalize_object_schema(value)
        elif key == "items" and isinstance(value, dict):
            nested = _normalize_schema(value)
            if nested is not None:
                result[key] = nested
        elif isinstance(value, list) and key not in ("enum", "required"):
            result[key] = [
                _normalize_schema(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value

    # Ensure every property in "properties" has a "type"
    props = result.get("properties")
    if isinstance(props, dict):
        for prop_name, prop_schema in list(props.items()):
            if isinstance(prop_schema, dict) and "type" not in prop_schema:
                # Default to string if missing type
                prop_schema["type"] = "string"

    # Prune "required" of properties that were dropped
    required = result.get("required")
    if isinstance(required, list) and isinstance(props, dict):
        result["required"] = [r for r in required if r in props]

    # Must at least have a type at the root
    if "type" not in result:
        result["type"] = "object"

    return result


def _normalize_object_schema(obj: dict[str, Any]) -> dict[str, Any]:
    """Normalize a dict of property schemas."""
    out: dict[str, Any] = {}
    for k, v in obj.items():
        if isinstance(v, dict):
            normalized = _normalize_schema(v)
            if normalized is not None:
                out[k] = normalized
        else:
            out[k] = v
    return out


def _mcp_tool_to_tool_param(server_name: str, tool: MCPTool) -> ToolParam | None:
    """Convert an MCP Tool into an Anthropic ToolParam.

    Returns *None* if the schema cannot be normalized for the Anthropic API.
    """
    name = f"mcp_{server_name}_{tool.name}"
    schema = _normalize_schema(tool.inputSchema)
    if schema is None:
        return None
    return ToolParam(
        name=name,
        description=tool.description or f"MCP tool {tool.name} from server {server_name}",
        input_schema=schema,
    )


# ---------------------------------------------------------------------------
# MCPClient
# ---------------------------------------------------------------------------


class MCPClient:
    """Manages MCP server connections and registers their tools.

    Instantiated by ToolRegistry at agent startup.  Connects to all
    enabled MCP servers, discovers their tools, and registers them with
    the ToolRegistry under the ``mcp_{server}_{tool}`` naming convention.

    All async MCP operations run on a dedicated background thread so that
    the synchronous ToolRegistry.execute() path stays simple.
    """

    def __init__(self, registry: "ToolRegistry") -> None:
        self.registry = registry
        self._sessions: dict[str, MCPSession] = {}
        self._tool_to_server: dict[str, str] = {}
        # Map: prefixed tool name -> (server_name, original_tool_name)
        self._tool_mapping: dict[str, tuple[str, str]] = {}

    def discover_all(self, project_root: Path | None = None) -> None:
        """Read config, connect to all enabled servers, register tools.

        This is a blocking call that runs the async discovery coroutine on
        the dedicated MCP event-loop thread.
        """
        if not _HAS_MCP:
            print(
                "[hilite] MCP SDK not installed. Skipping MCP server discovery. "
                "Install with: pip install 'mcp>=1.0.0'",
                file=sys.stderr,
            )
            return

        # Load and merge global + project config
        configs = load_mcp_config(project_root=project_root)
        if not configs:
            return

        # Backstop: never let discovery block the CLI forever. Bound the whole
        # pass by the sum of per-server connect timeouts (the per-server
        # wait_for below is the primary guard; this catches anything it misses).
        total_timeout = sum(
            int(c.get("connect_timeout", 30) or 30) for c in configs.values()
        ) + 10
        try:
            _run_on_mcp_loop(
                self._async_discover_all(configs), timeout=total_timeout
            )
        except TimeoutError:
            print(
                f"[hilite] Warning: MCP discovery exceeded {total_timeout}s; "
                "continuing without the server(s) still connecting.",
                file=sys.stderr,
            )

    async def _async_discover_all(
        self, configs: dict[str, dict[str, Any]]
    ) -> None:
        """Async worker: connect to each server, discover tools, register."""
        for server_name, server_config in configs.items():
            try:
                await self._connect_and_register(server_name, server_config)
            except Exception as e:
                print(
                    f"[hilite] MCP server '{server_name}' failed to start: {e}",
                    file=sys.stderr,
                )

    async def _connect_and_register(
        self, server_name: str, server_config: dict[str, Any]
    ) -> None:
        """Connect to a single MCP server and register its tools."""
        cfg = _build_transport_config(server_name, server_config)

        session = MCPSession(server_name, cfg)
        target = cfg.url or cfg.command or "?"
        print(
            f"[hilite] Connecting to MCP server '{server_name}' ({target}) ...",
            file=sys.stderr,
        )
        try:
            await asyncio.wait_for(session.connect(), timeout=cfg.connect_timeout)
        except asyncio.TimeoutError:
            try:
                await session.close()
            except Exception:
                pass
            hint = ""
            if server_config.get("auth") == "oauth":
                hint = (
                    f" If it needs interactive authorization, run "
                    f"'hilite mcp auth {server_name}' once to complete the OAuth "
                    f"flow and cache tokens."
                )
            raise RuntimeError(
                f"timed out after {cfg.connect_timeout}s during connect/initialize."
                f"{hint}"
            )
        self._sessions[server_name] = session

        tools = await session.list_tools()
        schemas: list[ToolParam] = []
        handlers: dict[str, Callable[..., Any]] = {}
        seen: set[str] = set()

        for tool in tools:
            tool_param = _mcp_tool_to_tool_param(server_name, tool)
            if tool_param is None:
                print(
                    f"[hilite] Warning: skipping tool '{tool.name}' from server "
                    f"'{server_name}' -- schema normalization failed.",
                    file=sys.stderr,
                )
                continue

            prefixed_name = tool_param["name"]
            if prefixed_name in seen:
                print(
                    f"[hilite] Warning: duplicate tool name '{prefixed_name}' from "
                    f"server '{server_name}' -- skipping.",
                    file=sys.stderr,
                )
                continue
            seen.add(prefixed_name)

            # Store mapping: prefixed name -> (server, original name)
            self._tool_mapping[prefixed_name] = (server_name, tool.name)

            schemas.append(tool_param)
            handlers[prefixed_name] = self._make_tool_handler(
                server_name, tool.name
            )

        if schemas:
            self.registry.register_mcp_tools(schemas, handlers)
            print(
                f"[hilite] MCP server '{server_name}': {len(schemas)} tool(s) registered.",
                file=sys.stderr,
            )

    def _make_tool_handler(
        self, server_name: str, tool_name: str
    ) -> Callable[..., Any]:
        """Build a synchronous handler for a single MCP tool."""

        def handler(**arguments: Any) -> str:
            return self._call_mcp_tool(server_name, tool_name, arguments)

        return handler

    def _call_mcp_tool(
        self, server_name: str, tool_name: str, arguments: dict[str, Any]
    ) -> str:
        """Synchronous entry point called by ToolRegistry.execute().

        Schedules the async call on the MCP event-loop thread and blocks
        until done.  Handles connection loss with one reconnect attempt.
        """
        if not _HAS_MCP:
            return "Error: MCP SDK not installed."

        session = self._sessions.get(server_name)
        if session is None:
            return f"Error: MCP server '{server_name}' is not running."

        try:
            result = _run_on_mcp_loop(
                session.call_tool(tool_name, arguments)
            )
        except RuntimeError as e:
            if "not connected" in str(e).lower():
                # Try reconnect once
                try:
                    _run_on_mcp_loop(session.connect())
                    result = _run_on_mcp_loop(
                        session.call_tool(tool_name, arguments)
                    )
                except Exception as reconnect_err:
                    return (
                        f"Error: MCP server '{server_name}' connection lost. "
                        f"Reconnect failed: {reconnect_err}"
                    )
            else:
                raise
        except Exception as e:
            return f"Error: MCP tool call failed: {e}"

        # Extract text content from result
        texts: list[str] = []
        for content in result.content:
            if hasattr(content, "text"):
                texts.append(content.text)
            else:
                print(
                    f"[hilite] Warning: ignoring non-text content from MCP tool "
                    f"'{tool_name}' on server '{server_name}'.",
                    file=sys.stderr,
                )
        return "\n".join(texts) if texts else "[no output]"

    def shutdown(self) -> None:
        """Close all MCP sessions."""
        if not _HAS_MCP:
            return
        for name, session in list(self._sessions.items()):
            try:
                _run_on_mcp_loop(session.close())
            except Exception as e:
                print(
                    f"[hilite] Error shutting down MCP server '{name}': {e}",
                    file=sys.stderr,
                )
        self._sessions.clear()


# ---------------------------------------------------------------------------
# Config → TransportConfig builder
# ---------------------------------------------------------------------------


def authenticate_and_probe(
    server_name: str, server_config: dict[str, Any]
) -> list[str]:
    """Connect to a single server (running any OAuth flow), return tool names.

    Runs the same connect + ``tools/list`` path that startup discovery uses,
    on a private event loop.  Raises on failure so the caller can surface the
    error verbatim.  Used by ``hilite mcp auth``.
    """
    if not _HAS_MCP:
        raise RuntimeError(
            "MCP SDK not installed. Install the MCP extra: "
            "uv tool install --editable '.[mcp]'"
        )

    cfg = _build_transport_config(server_name, server_config)

    async def _probe() -> list[str]:
        # Self-contained: enter both contexts as ``async with`` so the OAuth
        # flow, handshake, and teardown all happen in one task (no dangling
        # async generators, no cross-task cancel-scope issues).
        from mcp import ClientSession, Implementation

        from hilite.mcp.transport import connect_transport

        async with connect_transport(cfg) as (read_stream, write_stream):
            async with ClientSession(
                read_stream,
                write_stream,
                client_info=Implementation(name="hilite", version="0.1.0"),
            ) as session:
                await session.initialize()
                result = await session.list_tools()
                return [t.name for t in result.tools]

    return asyncio.run(_probe())


def _build_transport_config(
    server_name: str, config: dict[str, Any]
) -> TransportConfig:
    """Build a TransportConfig from raw YAML config dict."""
    cfg = TransportConfig(
        server_name=server_name,
        command=config.get("command"),
        args=config.get("args"),
        env=_resolve_env(config.get("env")),
        cwd=config.get("cwd"),
        url=config.get("url"),
        transport=config.get("transport", "streamable-http"),
        headers=config.get("headers"),
        timeout=config.get("timeout", 60),
        connect_timeout=config.get("connect_timeout", 30),
    )

    # mTLS
    cert = config.get("client_cert")
    key = config.get("client_key")
    ssl_verify = config.get("ssl_verify", True)
    if cert or key:
        cfg = _resolve_client_cert(cfg, cert, key, ssl_verify)

    # OAuth 2.1 (HTTP/SSE only) -- build the httpx.Auth provider now; the
    # browser flow is triggered lazily by the SDK during connect.
    if config.get("auth") == "oauth":
        if not config.get("url"):
            raise ValueError(
                f"MCP server '{server_name}': auth: oauth requires a 'url' "
                f"(OAuth is only for HTTP/SSE servers)."
            )
        from dataclasses import replace

        from hilite.mcp.oauth import build_oauth_provider

        provider = build_oauth_provider(
            server_name, config["url"], config.get("oauth")
        )
        cfg = replace(cfg, oauth_provider=provider)

    return cfg


def _resolve_env(env: dict[str, str] | None) -> dict[str, str] | None:
    """Resolve ${ENV_VAR} interpolations in env dict values."""
    if not env:
        return None
    resolved: dict[str, str] = {}
    for key, value in env.items():
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            var_name = value[2:-1]
            resolved[key] = os.environ.get(var_name, "")
        else:
            resolved[key] = value
    return resolved


def _resolve_client_cert(
    cfg: TransportConfig,
    client_cert: Any,
    client_key: str | None,
    ssl_verify: bool,
) -> TransportConfig:
    """Parse client_cert / client_key config for httpx cert= parameter.

    Returns a new TransportConfig with the cert field populated:
      - str path -> single PEM file (combined cert+key)
      - 2-element list -> (cert_path, key_path) tuple
      - 3-element list -> (cert_path, key_path, password) tuple
      - client_cert str + client_key str -> (cert, key) tuple
    """
    cert_value: str | tuple[str, ...] | None = None

    if isinstance(client_cert, str):
        cert_path = _expand_path(client_cert)
        if client_key:
            key_path = _expand_path(client_key)
            cert_value = (cert_path, key_path)
        else:
            cert_value = cert_path
    elif isinstance(client_cert, list):
        if client_key:
            raise ValueError(
                "Cannot use 'client_key' when 'client_cert' is a list. "
                "Combine into a single 'client_cert' list."
            )
        expanded = [_expand_path(p) for p in client_cert]
        if len(expanded) == 1:
            cert_value = expanded[0]
        elif len(expanded) == 2:
            cert_value = (expanded[0], expanded[1])
        elif len(expanded) == 3:
            cert_value = (expanded[0], expanded[1], expanded[2])
        else:
            raise ValueError(
                "'client_cert' list must have 1-3 elements (cert, key, passphrase)."
            )

    # Validate files exist
    if isinstance(cert_value, str):
        _ensure_file(cert_value, "client_cert")
    elif isinstance(cert_value, tuple):
        _ensure_file(cert_value[0], "client_cert")
        if len(cert_value) >= 2:
            _ensure_file(cert_value[1], "client_key")

    # Return a new frozen dataclass with updated fields
    from dataclasses import replace

    return replace(
        cfg,
        client_cert=cert_value,
        ssl_verify=ssl_verify,
    )


def _expand_path(path: str) -> str:
    """Expand ~ and environment variables in a path."""
    return os.path.expanduser(os.path.expandvars(path))


def _ensure_file(path: str, label: str) -> None:
    """Raise FileNotFoundError with a clear message if path does not exist."""
    if not Path(path).is_file():
        raise FileNotFoundError(f"MCP {label} file not found: {path}")
