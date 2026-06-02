"""memory_manage tool — lets the agent read and append to USER.md / MEMORY.md."""

from __future__ import annotations

from pathlib import Path

from hilite.memory import MemoryStore, get_project_key


def _make_store() -> MemoryStore:
    """Create a MemoryStore scoped to the current project."""
    return MemoryStore(Path.home() / ".hilite", project_key=get_project_key())


def memory_manage(action: str, section: str, content: str = "") -> str:
    """Read or append persistent memory entries.

    Args:
        action: Either "read" or "append".
        section: Either "user" (USER.md) or "memory" (MEMORY.md).
        content: For "append", the declarative fact to save.

    Returns:
        Confirmation message or the current file contents.
    """
    store = _make_store()

    if action == "read":
        raw = store.read_memory_file(section)
        if not raw.strip():
            return f"No entries in {section} memory yet."
        return raw

    if action == "append":
        if not content or not content.strip():
            return "Error: 'content' is required for append action."
        store.append_memory(section, content.strip())
        project_hint = f" ({store.project_key})" if store.project_key and section == "memory" else ""
        return f"Saved to {section} memory{project_hint}: {content.strip()}"

    return f"Error: unknown action '{action}'. Use 'read' or 'append'."
