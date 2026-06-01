"""Constants and path helpers for HiLite."""

from pathlib import Path

VERSION = "0.1.0"
HILITE_HOME = Path.home() / ".hilite"
SESSIONS_DIR = HILITE_HOME / "sessions"
DEFAULT_MODEL = "claude-sonnet-4-6-20250601"
DEFAULT_MAX_TURNS = 50
