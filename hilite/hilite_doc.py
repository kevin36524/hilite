"""HILITE.md -- the progressive-disclosure project context file (doc 14.1).

One ``HILITE.md`` lives at each repo root. It replaces the flat
``HERMES.md``/``AGENTS.md``/``CLAUDE.md`` precedence chain (``hilite/context.py``)
with a single tool-managed file modeled on skills:

  * the **header** -- YAML frontmatter + the ``## Overview`` section -- is always
    loaded as project context (a few hundred tokens), and
  * every other ``## Section`` is fetched on demand via the ``hilite_section``
    tool, exactly like ``load_skill`` returns a skill body.

Because only the header loads by default, the 10k char cap that bounded the old
flat file is effectively moot.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hilite.skills import parse_frontmatter_scalars, split_frontmatter

HILITE_FILENAME = "HILITE.md"

# Matches an ATX ``## Heading``; trailing HTML comments (``<!-- ... -->``) are
# stripped from the captured name so authoring annotations don't leak into the
# section index.
_SECTION_RE = re.compile(r"^##\s+(.*?)\s*$")
_COMMENT_RE = re.compile(r"<!--.*?-->")


@dataclass(frozen=True)
class HiliteDoc:
    """A parsed ``HILITE.md``: header (always loaded) + on-demand sections."""

    name: str
    description: str
    raw_frontmatter: str          # verbatim text between the --- delimiters
    meta: dict[str, str]          # parsed scalar frontmatter keys
    overview: str                 # body of the ## Overview section
    sections: dict[str, str]      # other ## section name -> body
    path: Path

    def get_section(self, name: str) -> str | None:
        """Return a section body by name (exact, then case-insensitive)."""
        if name in self.sections:
            return self.sections[name]
        lowered = name.strip().lower()
        for key, body in self.sections.items():
            if key.lower() == lowered:
                return body
        return None


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


def _parse_sections(body: str) -> tuple[str, dict[str, str]]:
    """Split a markdown body on ``## `` headings.

    Returns ``(overview, others)`` where ``overview`` is the body of the
    ``## Overview`` section (or any text before the first heading, as a
    fallback) and ``others`` maps each remaining section name to its body.
    """
    overview = ""
    others: dict[str, str] = {}
    preamble: list[str] = []
    current: str | None = None
    buf: list[str] = []

    def flush() -> None:
        text = "\n".join(buf).strip()
        if current is None:
            return
        if current.lower() == "overview":
            nonlocal overview
            overview = text
        else:
            others[current] = text

    for line in body.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            flush()
            current = _COMMENT_RE.sub("", m.group(1)).strip()
            buf = []
        elif current is None:
            preamble.append(line)
        else:
            buf.append(line)
    flush()

    # If the author skipped an explicit ## Overview, fall back to the preamble.
    if not overview:
        overview = "\n".join(preamble).strip()
    return overview, others


def parse_hilite_doc(content: str, path: Path) -> HiliteDoc:
    """Parse raw ``HILITE.md`` text into a :class:`HiliteDoc`."""
    frontmatter, body = split_frontmatter(content)
    meta = parse_frontmatter_scalars(frontmatter)
    overview, sections = _parse_sections(body)
    return HiliteDoc(
        name=meta.get("name", path.parent.name),
        description=meta.get("description", ""),
        raw_frontmatter=frontmatter,
        meta=meta,
        overview=overview,
        sections=sections,
        path=path,
    )


def find_hilite_path(cwd: Path | None = None) -> Path | None:
    """Locate ``HILITE.md`` at the repo root for *cwd* (or cwd if not a repo)."""
    cwd = cwd or Path.cwd()
    root = _git_root(cwd) or cwd
    candidate = root / HILITE_FILENAME
    return candidate if candidate.is_file() else None


def load_hilite_doc(cwd: Path | None = None) -> HiliteDoc | None:
    """Load and parse the repo's ``HILITE.md``, or None if absent/unreadable."""
    path = find_hilite_path(cwd)
    if path is None:
        return None
    try:
        return parse_hilite_doc(path.read_text(encoding="utf-8"), path)
    except OSError:
        return None


def render_header(doc: HiliteDoc) -> str:
    """Render the always-loaded header: frontmatter + Overview + a section TOC.

    The TOC names the other ``## sections`` so the agent knows what it can pull
    on demand with ``hilite_section``.
    """
    parts: list[str] = []
    if doc.raw_frontmatter:
        parts.append(f"---\n{doc.raw_frontmatter}\n---")
    if doc.overview:
        parts.append(f"## Overview\n{doc.overview}")
    if doc.sections:
        toc = "\n".join(f"- {name}" for name in doc.sections)
        parts.append(
            "## Available sections\n"
            "Fetch any of these on demand with the hilite_section tool:\n"
            f"{toc}"
        )
    return "\n\n".join(parts)


def hilite_section(name: str, cwd: Path | None = None) -> str:
    """Return a ``## section`` body from the current repo's ``HILITE.md``.

    The ``hilite_section`` tool handler. Resolves locally against the repo
    rooted at *cwd* (the serve process ``chdir``s into the session's cwd).
    """
    doc = load_hilite_doc(cwd)
    if doc is None:
        return f"Error: No {HILITE_FILENAME} found in this repo."
    body = doc.get_section(name)
    if body is None:
        available = ", ".join(doc.sections) or "(none)"
        return (
            f"Error: Section '{name}' not found in {HILITE_FILENAME}. "
            f"Available sections: {available}"
        )
    return body
