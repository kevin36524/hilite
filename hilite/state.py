"""Minimal session state persistence (JSON-based).

Conversations are stored as JSON files in ~/.hilite/sessions/.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from hilite.constants import SESSIONS_DIR


def ensure_sessions_dir() -> Path:
    """Create the sessions directory if it doesn't exist."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return SESSIONS_DIR


def save_session(session_id: str, messages: list[dict], metadata: dict | None = None) -> None:
    """Save a conversation session to disk."""
    ensure_sessions_dir()
    path = SESSIONS_DIR / f"{session_id}.json"
    data = {
        "session_id": session_id,
        "timestamp": time.time(),
        "messages": messages,
        "metadata": metadata or {},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_session(session_id: str) -> dict | None:
    """Load a conversation session from disk."""
    path = SESSIONS_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_session_metadata(session_id: str) -> dict | None:
    """Load just the metadata portion of a session."""
    data = load_session(session_id)
    if data is None:
        return None
    return data.get("metadata")


def list_sessions() -> list[dict]:
    """List all saved sessions (newest first)."""
    ensure_sessions_dir()
    sessions = []
    for path in sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            sessions.append({
                "session_id": data.get("session_id", path.stem),
                "timestamp": data.get("timestamp", 0),
                "message_count": len(data.get("messages", [])),
            })
        except (json.JSONDecodeError, OSError):
            continue
    return sessions


def generate_session_id() -> str:
    """Generate a unique session ID."""
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}"
