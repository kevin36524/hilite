"""Minimal configuration -- env vars + optional config file."""

import os
from pathlib import Path


def hilite_home() -> Path:
    """Return the HiLite home directory (~/.hilite)."""
    return Path.home() / ".hilite"


def load_config() -> dict:
    """Load configuration from env vars and optional config file.

    Priority: env vars > config file > defaults.
    """
    config = {
        "model": os.environ.get("HILITE_MODEL", "claude-sonnet-4-6-20250601"),
        "api_key": os.environ.get("ANTHROPIC_API_KEY"),
    }

    # Optional YAML config file
    config_file = hilite_home() / "config.yaml"
    if config_file.exists():
        try:
            import yaml

            with open(config_file) as f:
                file_config = yaml.safe_load(f) or {}
            config.update(file_config)
        except ImportError:
            pass

    return config
