"""Minimal configuration -- env vars + optional config file."""

import os
from pathlib import Path

from hilite.memory import ensure_default_soul


def hilite_home() -> Path:
    """Return the HiLite home directory (~/.hilite)."""
    return Path.home() / ".hilite"


def load_config() -> dict:
    """Load configuration from env vars and optional config file.

    Priority: env vars > config file > defaults.
    Also seeds a default SOUL.md on first run.
    """
    config = {
        "model": os.environ.get("HILITE_MODEL", "claude-sonnet-4-6-20250601"),
        "api_key": os.environ.get("ANTHROPIC_API_KEY"),
    }

    # Optional YAML config file
    home = hilite_home()
    config_file = home / "config.yaml"
    if config_file.exists():
        try:
            import yaml

            with open(config_file) as f:
                file_config = yaml.safe_load(f) or {}
            config.update(file_config)
        except ImportError:
            pass

    # Seed default SOUL.md on first run
    ensure_default_soul(home)

    return config


# ---------------------------------------------------------------------------
# MCP configuration
# ---------------------------------------------------------------------------


def load_mcp_config(project_root: Path | None = None) -> dict[str, dict]:
    """Load and merge global + project-level MCP server configurations.

    Resolution rules (per spec §2.1.1):
      1. Read global ``~/.hilite/config.yaml`` → get ``mcp_servers`` definitions.
      2. Read project ``.hilite/config.yaml`` → get ``mcp.enabled`` / ``mcp.disabled``
         and any project-level ``mcp_servers``.
      3. Apply activation filters:
         - ``mcp.enabled`` present → only those servers active
         - ``mcp.disabled`` present → those servers excluded
         - neither → all globally-enabled servers active
      4. A server with ``enabled: false`` in global config is always skipped.
      5. Project-level ``mcp_servers`` entries override global on name collision.

    Returns a dict of ``{server_name: server_config}`` for all active servers.
    """
    global_servers: dict[str, dict] = {}
    project_servers: dict[str, dict] = {}
    enabled_filter: list[str] | None = None
    disabled_filter: list[str] | None = None

    # --- global config ---
    home = hilite_home()
    global_file = home / "config.yaml"
    if global_file.exists():
        try:
            import yaml

            with open(global_file) as f:
                global_cfg = yaml.safe_load(f) or {}
            global_servers = global_cfg.get("mcp_servers", {})
        except ImportError:
            pass
        except Exception:
            pass

    # --- project config ---
    if project_root:
        project_file = project_root / ".hilite" / "config.yaml"
        if project_file.exists():
            try:
                import yaml

                with open(project_file) as f:
                    project_cfg = yaml.safe_load(f) or {}

                mcp_section = project_cfg.get("mcp", {})
                if "enabled" in mcp_section:
                    enabled_filter = mcp_section["enabled"]
                if "disabled" in mcp_section:
                    disabled_filter = mcp_section["disabled"]

                project_servers = project_cfg.get("mcp_servers", {})
            except ImportError:
                pass
            except Exception:
                pass

    # --- merge: project-level overrides global ---
    merged = dict(global_servers)
    merged.update(project_servers)

    # --- apply filters ---
    active: dict[str, dict] = {}
    for name, cfg in merged.items():
        # Skip globally disabled servers
        if cfg.get("enabled") is False and name in global_servers:
            continue

        # Apply enabled filter
        if enabled_filter is not None and name not in enabled_filter:
            continue

        # Apply disabled filter
        if disabled_filter is not None and name in disabled_filter:
            continue

        active[name] = cfg

    return active
