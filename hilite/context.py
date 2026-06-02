"""Project context file discovery.

Scans the current working directory (and walks up to the git root) for
project-level AI assistant instructions such as .hermes.md, AGENTS.md,
CLAUDE.md, and .cursorrules.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

MAX_CONTEXT_SIZE = 10000

# Priority-ordered list of context file names.
# Names earlier in the list win.
_CONTEXT_FILENAMES = [
    ".hermes.md",
    "HERMES.md",
    "AGENTS.md",
    "agents.md",
    "CLAUDE.md",
    "claude.md",
]


def _git_root(start: Path | None = None) -> Path | None:
    """Return the git root for *start* (default cwd), or None if not in a repo."""
    start = start or Path.cwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start),
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _find_closest(start: Path, stop: Path, names: list[str]) -> Path | None:
    """Walk from *start* up to (and including) *stop*, looking for *names*.

    Returns the path to the first match found, or None.
    """
    current = start.resolve()
    stop = stop.resolve()
    while True:
        for name in names:
            candidate = current / name
            if candidate.is_file():
                return candidate
        if current == stop:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def load_project_context(
    cwd: Path | None = None,
    max_size: int = MAX_CONTEXT_SIZE,
) -> str | None:
    """Discover and return the highest-priority project context file content.

    Scans in this order:
        1. .hermes.md / HERMES.md — walk up to git root
        2. AGENTS.md / agents.md — cwd only
        3. CLAUDE.md / claude.md — cwd only
        4. .cursorrules — cwd only (optional)

    Only the highest-priority file found is returned.
    Content is truncated to *max_size* characters.
    """
    cwd = cwd or Path.cwd()
    root = _git_root(cwd)

    # 1. .hermes.md / HERMES.md — walk to git root (or filesystem root if no repo)
    stop = root if root else Path("/")
    match = _find_closest(cwd, stop, [".hermes.md", "HERMES.md"])
    if match:
        content = match.read_text(encoding="utf-8").strip()
        return _truncate(content, match.name, max_size)

    # 2-3. AGENTS.md / CLAUDE.md — cwd only
    for name in _CONTEXT_FILENAMES[2:]:
        candidate = cwd / name
        if candidate.is_file():
            content = candidate.read_text(encoding="utf-8").strip()
            return _truncate(content, name, max_size)

    # 4. .cursorrules — cwd only (optional, lowest priority)
    cursor_rules = cwd / ".cursorrules"
    if cursor_rules.is_file():
        content = cursor_rules.read_text(encoding="utf-8").strip()
        return _truncate(content, ".cursorrules", max_size)

    return None


def _truncate(content: str, name: str, max_size: int) -> str:
    if len(content) > max_size:
        content = content[:max_size]
        import sys

        print(
            f"[hilite] Warning: {name} truncated to {max_size} chars.",
            file=sys.stderr,
        )
    return content
