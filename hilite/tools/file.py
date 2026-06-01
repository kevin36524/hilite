"""File tools -- read, write, list."""

from __future__ import annotations

from pathlib import Path


def _resolve_path(path: str) -> Path:
    """Resolve a path string to an absolute Path."""
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p.resolve()


def read_file(path: str) -> str:
    """Read the contents of a file."""
    p = _resolve_path(path)
    if not p.exists():
        return f"Error: File not found: {p}"
    if not p.is_file():
        return f"Error: Not a file: {p}"
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        return f"Error: File is not valid UTF-8 text: {p}"
    except OSError as e:
        return f"Error reading file: {e}"


def write_file(path: str, content: str) -> str:
    """Write content to a file. Creates parent directories if needed."""
    p = _resolve_path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return f"File written successfully: {p}"
    except OSError as e:
        return f"Error writing file: {e}"


def list_directory(path: str) -> str:
    """List files and directories at a given path."""
    p = _resolve_path(path)
    if not p.exists():
        return f"Error: Directory not found: {p}"
    if not p.is_dir():
        return f"Error: Not a directory: {p}"

    lines: list[str] = [f"Contents of {p}:", ""]
    try:
        entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        for entry in entries:
            prefix = "[DIR]  " if entry.is_dir() else "[FILE] "
            lines.append(f"{prefix}{entry.name}")
        return "\n".join(lines)
    except OSError as e:
        return f"Error listing directory: {e}"
