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
