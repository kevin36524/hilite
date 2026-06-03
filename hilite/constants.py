"""Constants and path helpers for HiLite."""

from pathlib import Path

VERSION = "0.1.0"
HILITE_HOME = Path.home() / ".hilite"
SESSIONS_DIR = HILITE_HOME / "sessions"
MEMORIES_DIR = HILITE_HOME / "memories"
SOUL_PATH = HILITE_HOME / "SOUL.md"
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TURNS = 50
