"""Memory and identity management for HiLite.

Handles SOUL.md (agent identity), USER.md (user profile), and per-project
MEMORY.md (environment notes). Builds a frozen system-prompt snapshot at
session start. All user-editable content is security-scanned before entering
the system prompt.
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SECTION_DELIM = "§"
MAX_ENTRY_LEN = 500
MAX_MEMORY_BLOCK = 8192
MAX_SOUL_LEN = 20000

DEFAULT_SOUL_MD = """\
# HiLite Persona

<!--
This file defines HiLite's personality and tone.
Edit this to customize how HiLite communicates with you.

Examples:
  - "You are a warm, playful assistant who uses kaomoji occasionally."
  - "You are a concise technical expert. No fluff, just facts."
  - "You speak like a friendly coworker who happens to know everything."

This file is loaded fresh each message -- no restart needed.
Delete the contents (or this file) to use the default personality.
-->

You are HiLite, a helpful AI assistant. You have access to tools that let you
read and write files, run shell commands, and execute Python code.
Use tools when they help answer the user's question.
Always think step by step.
"""

MEMORY_MGMT_INSTRUCTIONS = textwrap.dedent(
    """\
    ## Memory Management

    You have access to a persistent memory system via the `memory_manage` tool.
    There are two sections:
    - **user**: Facts about the user's preferences, style, background, and habits.
      These are global and apply across all projects.
    - **memory**: Facts about the current project — conventions, tools, build
      commands, directory structure, and things you have learned. These are
      scoped to the project you are working in.

    ### When to save memory
    - The user explicitly asks you to "remember" something.
    - You learn a non-obvious fact about the user's preferences.
    - You discover a project convention (e.g., "tests go in tests/", "use uv not pip").
    - You make a mistake and learn how to avoid it next time.
    - You learn something that would save time in future sessions.

    ### When NOT to save memory
    - Obvious or generic facts (e.g., "Python is a programming language").
    - Temporary or session-specific state (e.g., "the user is debugging issue #123").
    - Facts that are already in the system prompt or project context files.

    ### How to write entries
    - Write each entry as a single concise declarative sentence.
    - Start with a verb when describing preferences or conventions.
    - Be specific. "The user uses Neovim" is better than "The user likes editors."
    - Do not include the current date unless the fact is time-sensitive.

    ### Memory hygiene
    - If you learn that a previous memory is wrong, append a correction rather than deleting.
    - Periodically review memories (use `memory_manage` with action="read") to avoid stale or redundant entries.
    """
)

# Patterns that look like prompt-injection attempts
_SUSPICIOUS_PATTERNS = [
    re.compile(r"<\s*system\s*>", re.IGNORECASE),
    re.compile(r"<\s*/\s*system\s*>", re.IGNORECASE),
    re.compile(r"<\s*instructions?\s*>", re.IGNORECASE),
    re.compile(r"<\s*/\s*instructions?\s*>", re.IGNORECASE),
    re.compile(r"ignore\s+(all\s+)?(previous\s+)?instructions?", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"new\s+role\s*:", re.IGNORECASE),
    re.compile(r"\bNinja Coder\b", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Project key derivation (for per-project MEMORY.md)
# ---------------------------------------------------------------------------


def get_project_key(cwd: Path | None = None) -> str | None:
    """Derive a project key from the current git repo or working directory.

    Priority:
        1. Git repo name (e.g., 'hermes_lite' from /Users/kevin/project/ai/hermes_lite)
        2. Sanitised cwd directory name if not in a git repo

    Returns *None* if we can't determine a stable key.
    """
    cwd = cwd or Path.cwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
        )
        repo_root = Path(result.stdout.strip())
        return _sanitize_key(repo_root.name)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Fall back to cwd directory name
    return _sanitize_key(cwd.resolve().name)


def _sanitize_key(name: str) -> str:
    """Sanitise a directory name for use as a filesystem key."""
    # Replace filesystem-unfriendly characters
    key = re.sub(r"[^\w\-]+", "_", name).strip("_")
    if not key:
        key = "default"
    return key


# ---------------------------------------------------------------------------
# Security scanner
# ---------------------------------------------------------------------------


def _sanitize(content: str, source: str) -> str:
    """Scan *content* for suspicious patterns and replace matches.

    Logs a warning to stderr for each match so the user knows something
    was blocked.
    """
    original = content
    for pat in _SUSPICIOUS_PATTERNS:
        for match in pat.finditer(original):
            span = match.span()
            bad = original[span[0] : span[1]]
            content = content[: span[0]] + "[BLOCKED: suspicious content]" + content[span[1] :]
            print(
                f"[hilite] Security: blocked suspicious content in {source}: {bad!r}",
                file=sys.stderr,
            )
    return content


# ---------------------------------------------------------------------------
# MemoryStore
# ---------------------------------------------------------------------------


class MemoryStore:
    """Loads, sanitises, and assembles memory content for the system prompt.

    The snapshot built by :meth:`build_system_prompt` is *frozen* — it must
    not be mutated mid-session so the upstream prefix cache stays warm.

    USER.md is **global** (one per HiLite home directory).
    MEMORY.md is **per-project** (scoped to the git repo / cwd).
    """

    def __init__(self, home: Path, project_key: str | None = None) -> None:
        self.home = home
        self.memories_dir = home / "memories"
        self.soul_path = home / "SOUL.md"
        self.user_path = self.memories_dir / "USER.md"

        # MEMORY.md is scoped to the project
        self.project_key = project_key
        if project_key:
            self.memory_path = home / "projects" / project_key / "memory" / "MEMORY.md"
        else:
            # Fallback: global MEMORY.md when no project key is available
            self.memory_path = self.memories_dir / "MEMORY.md"

        # Ensure directories exist
        self.memories_dir.mkdir(parents=True, exist_ok=True)
        self.user_path.touch(exist_ok=True)
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        self.memory_path.touch(exist_ok=True)

    # -- SOUL -----------------------------------------------------------------

    def load_soul(self) -> str | None:
        """Return sanitised SOUL.md content, or *None* if absent/empty."""
        if not self.soul_path.exists():
            return None
        content = self.soul_path.read_text(encoding="utf-8").strip()
        if not content:
            return None
        content = _sanitize(content, "SOUL.md")
        if len(content) > MAX_SOUL_LEN:
            content = content[:MAX_SOUL_LEN]
            print(
                f"[hilite] Warning: SOUL.md truncated to {MAX_SOUL_LEN} chars.",
                file=sys.stderr,
            )
        return content

    # -- USER / MEMORY entries ------------------------------------------------

    def _read_entries(self, path: Path) -> list[str]:
        """Read §-delimited entries from a memory file."""
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return []

        # Split on § that appears at start of line (possibly after whitespace)
        raw = re.split(r"^\s*§\s*", text, flags=re.MULTILINE)
        entries = []
        for piece in raw:
            piece = piece.strip()
            if not piece or piece.startswith("#"):
                continue
            # Truncate overly long entries
            if len(piece) > MAX_ENTRY_LEN:
                piece = piece[:MAX_ENTRY_LEN]
                print(
                    f"[hilite] Warning: truncated long entry in {path.name}.",
                    file=sys.stderr,
                )
            entries.append(piece)
        return entries

    def _render_block(self, header: str, entries: list[str], source: str) -> str:
        """Sanitise and render a memory block, capping total size."""
        if not entries:
            return ""
        sanitized = [_sanitize(e, source) for e in entries]
        body = "\n".join(f"- {e}" for e in sanitized)
        block = f"{header}\n{body}"
        if len(block) > MAX_MEMORY_BLOCK:
            # Truncate oldest entries first by progressively dropping from the start
            while len(block) > MAX_MEMORY_BLOCK and sanitized:
                sanitized.pop(0)
                body = "\n".join(f"- {e}" for e in sanitized)
                block = f"{header}\n{body}"
            print(
                f"[hilite] Warning: {source} block truncated to {MAX_MEMORY_BLOCK} chars.",
                file=sys.stderr,
            )
        return block

    # -- System prompt assembly -----------------------------------------------

    def build_system_prompt(
        self,
        default_identity: str,
        project_context: str | None = None,
    ) -> str:
        """Assemble the full stable portion of the system prompt.

        Order:
            1. SOUL.md (or default_identity fallback)
            2. User profile block
            3. Environment notes block (per-project)
            4. Project context block
            5. Memory management instructions
        """
        parts: list[str] = []

        # 1. Identity
        soul = self.load_soul()
        if soul:
            parts.append(soul)
        else:
            parts.append(default_identity)

        # 2. User profile (global)
        user_entries = self._read_entries(self.user_path)
        user_block = self._render_block(
            "## What you know about the user:",
            user_entries,
            "USER.md",
        )
        if user_block:
            parts.append(user_block)

        # 3. Environment notes (per-project)
        mem_entries = self._read_entries(self.memory_path)
        project_label = f" ({self.project_key})" if self.project_key else ""
        mem_block = self._render_block(
            f"## Things you have observed about this project{project_label}:",
            mem_entries,
            "MEMORY.md",
        )
        if mem_block:
            parts.append(mem_block)

        # 4. Project context
        if project_context:
            ctx = _sanitize(project_context, "project context")
            parts.append(f"## Project context:\n{ctx}")

        # 5. Memory management instructions
        parts.append(MEMORY_MGMT_INSTRUCTIONS)

        return "\n\n".join(parts)

    # -- Disk I/O for the memory tool -----------------------------------------

    def read_memory_file(self, section: str) -> str:
        """Return raw contents of USER.md or MEMORY.md (for tool reads)."""
        path = self.user_path if section == "user" else self.memory_path
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def append_memory(self, section: str, content: str) -> None:
        """Append a §-prefixed entry with a timestamp comment."""
        path = self.user_path if section == "user" else self.memory_path
        # Ensure parent dir exists
        path.parent.mkdir(parents=True, exist_ok=True)

        # Clean the content: single line, no §, no newlines
        clean = " ".join(content.replace(SECTION_DELIM, "").split())
        timestamp = time.strftime("%Y-%m-%d %H:%M")
        entry = f"\n§ {clean}  <!-- {timestamp} -->\n"

        with open(path, "a", encoding="utf-8") as f:
            f.write(entry)


def ensure_default_soul(home: Path) -> None:
    """Seed a default SOUL.md if one doesn't already exist."""
    soul_path = home / "SOUL.md"
    if soul_path.exists():
        return
    home.mkdir(parents=True, exist_ok=True)
    soul_path.write_text(DEFAULT_SOUL_MD, encoding="utf-8")
