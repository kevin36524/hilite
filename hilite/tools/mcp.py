"""Agent tool for registering MCP servers.

Thin wrapper over ``hilite.mcp.manage`` so the model can add a server
definition mid-conversation.  Note: the server is only *connected* at the
next ``hilite`` startup (no dynamic reload), so the returned message says so.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def mcp_add_server(
    name: str,
    command: str | None = None,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    url: str | None = None,
    transport: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: int | None = None,
    connect_timeout: int | None = None,
    client_cert: Any = None,
    client_key: str | None = None,
    ssl_verify: bool | None = None,
    auth: str | None = None,
    oauth: dict[str, Any] | None = None,
    scope: str = "global",
    enabled: bool | None = None,
    overwrite: bool = False,
) -> str:
    """Add an MCP server definition to config.yaml.

    Returns a human-readable status string (errors start with "Error:").
    """
    from hilite.mcp.manage import add_mcp_server, build_server_config

    try:
        cfg = build_server_config(
            command=command,
            args=args,
            env=env,
            cwd=cwd,
            url=url,
            transport=transport,
            headers=headers,
            timeout=timeout,
            connect_timeout=connect_timeout,
            client_cert=client_cert,
            client_key=client_key,
            ssl_verify=ssl_verify,
            auth=auth,
            oauth=oauth,
            enabled=enabled,
        )
        path = add_mcp_server(
            name,
            cfg,
            scope=scope,
            project_root=Path.cwd(),
            overwrite=overwrite,
        )
    except ValueError as e:
        return f"Error: {e}"

    return (
        f"Added MCP server '{name}' to {path}. "
        f"It will be connected the next time hilite starts."
    )
