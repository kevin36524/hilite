"""Add, remove, and inspect MCP server definitions in config.yaml.

Shared core used by both the ``hilite mcp`` CLI subcommands and the
``mcp_add_server`` agent tool.  Server *definitions* (with secrets) live in
the global ``~/.hilite/config.yaml`` under the ``mcp_servers`` key; project
scope writes to ``<project>/.hilite/config.yaml`` instead.

These helpers only edit the ``mcp_servers`` section -- all other keys in the
file are preserved.  Comments are not preserved (PyYAML does not round-trip
them); a clear trade-off for v1.

NOTE: servers are only connected at startup (no dynamic reload, per spec
§2.6), so a freshly added server takes effect on the next ``hilite`` run.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from hilite.config import hilite_home

# Server names become part of the tool prefix ``mcp_{server}_{tool}``.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _config_path(*, scope: str, project_root: Path | None) -> Path:
    """Resolve the config.yaml path for the given scope."""
    if scope == "global":
        return hilite_home() / "config.yaml"
    if scope == "project":
        if project_root is None:
            project_root = Path.cwd()
        return project_root / ".hilite" / "config.yaml"
    raise ValueError(f"Unknown scope '{scope}' (expected 'global' or 'project').")


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file into a dict, or return {} if it does not exist."""
    if not path.exists():
        return {}
    import yaml

    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} is not a YAML mapping.")
    return data


def _dump_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write a dict to a YAML file, creating parent dirs as needed."""
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)


def build_server_config(
    *,
    command: str | None = None,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    url: str | None = None,
    transport: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: int | None = None,
    connect_timeout: int | None = None,
    client_cert: str | list[str] | None = None,
    client_key: str | None = None,
    ssl_verify: bool | None = None,
    auth: str | None = None,
    oauth: dict[str, Any] | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Assemble and validate a single MCP server config dict.

    Exactly one transport must be specified: ``command`` (stdio) or ``url``
    (HTTP/SSE).  Transport-specific keys are validated against that choice.
    ``None`` values are omitted from the result so the written YAML stays
    minimal.  Raises ``ValueError`` on invalid combinations.
    """
    if command and url:
        raise ValueError(
            "Specify either 'command' (stdio) or 'url' (HTTP/SSE), not both."
        )
    if not command and not url:
        raise ValueError("A server needs either 'command' (stdio) or 'url' (HTTP/SSE).")

    is_http = url is not None
    http_only = {
        "transport": transport,
        "headers": headers,
        "client_cert": client_cert,
        "client_key": client_key,
        "auth": auth,
        "oauth": oauth,
    }
    if not is_http:
        bad = [k for k, v in http_only.items() if v is not None]
        if bad:
            raise ValueError(
                f"{', '.join(sorted(bad))} only apply to HTTP/SSE servers "
                f"(those with a 'url'), not stdio servers."
            )
    if not is_http and (args is not None and not isinstance(args, list)):
        raise ValueError("'args' must be a list of strings.")
    if transport is not None and transport not in ("streamable-http", "sse"):
        raise ValueError("'transport' must be 'streamable-http' or 'sse'.")
    if client_key is not None and isinstance(client_cert, list):
        raise ValueError(
            "Cannot use 'client_key' when 'client_cert' is a list. "
            "Combine into a single 'client_cert' list."
        )

    cfg: dict[str, Any] = {}
    if command:
        cfg["command"] = command
        if args:
            cfg["args"] = list(args)
        if cwd:
            cfg["cwd"] = cwd
    else:
        cfg["url"] = url
        if transport:
            cfg["transport"] = transport
        if headers:
            cfg["headers"] = dict(headers)
        if client_cert is not None:
            cfg["client_cert"] = client_cert
        if client_key is not None:
            cfg["client_key"] = client_key
        if ssl_verify is not None:
            cfg["ssl_verify"] = ssl_verify
        if auth:
            cfg["auth"] = auth
        if oauth:
            cfg["oauth"] = dict(oauth)

    # env applies to both transports
    if env:
        cfg["env"] = dict(env)
    if timeout is not None:
        cfg["timeout"] = timeout
    if connect_timeout is not None:
        cfg["connect_timeout"] = connect_timeout
    if enabled is not None:
        cfg["enabled"] = enabled

    return cfg


def get_server_definitions(
    *, scope: str = "global", project_root: Path | None = None
) -> dict[str, dict[str, Any]]:
    """Return the raw ``mcp_servers`` definitions from the scoped config file.

    Unlike ``config.load_mcp_config``, this returns the unfiltered definitions
    (ignoring enabled/disabled activation), suitable for listing and editing.
    """
    path = _config_path(scope=scope, project_root=project_root)
    data = _load_yaml(path)
    servers = data.get("mcp_servers", {})
    return servers if isinstance(servers, dict) else {}


def add_mcp_server(
    name: str,
    server_config: dict[str, Any],
    *,
    scope: str = "global",
    project_root: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Write a server definition into the scoped config.yaml.

    Returns the path written.  Raises ``ValueError`` if the name is invalid
    or already exists (unless ``overwrite=True``).
    """
    if not _NAME_RE.match(name):
        raise ValueError(
            f"Invalid server name '{name}'. Use letters, numbers, '-' and '_' "
            f"(must start alphanumeric)."
        )

    path = _config_path(scope=scope, project_root=project_root)
    data = _load_yaml(path)
    servers = data.get("mcp_servers")
    if not isinstance(servers, dict):
        servers = {}

    if name in servers and not overwrite:
        raise ValueError(
            f"MCP server '{name}' already exists in {path}. "
            f"Use overwrite to replace it."
        )

    servers[name] = server_config
    data["mcp_servers"] = servers
    _dump_yaml(path, data)
    return path


def remove_mcp_server(
    name: str,
    *,
    scope: str = "global",
    project_root: Path | None = None,
) -> bool:
    """Remove a server definition from the scoped config.yaml.

    Returns True if a server was removed, False if it was not present.
    """
    path = _config_path(scope=scope, project_root=project_root)
    data = _load_yaml(path)
    servers = data.get("mcp_servers")
    if not isinstance(servers, dict) or name not in servers:
        return False

    del servers[name]
    data["mcp_servers"] = servers
    _dump_yaml(path, data)
    return True
