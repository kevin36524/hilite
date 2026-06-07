"""Project context loading.

As of doc 14.1, HiLite reads a single **``HILITE.md``** at the repo root as
project context, treated as a *progressive-disclosure* doc: only the header
(frontmatter + ``## Overview``) is loaded by default, with the remaining
``## sections`` fetched on demand via the ``hilite_section`` tool. The legacy
flat ``HERMES.md``/``AGENTS.md``/``CLAUDE.md``/``.cursorrules`` precedence chain
is no longer read -- see ``hilite/hilite_doc.py``.
"""

from __future__ import annotations

from pathlib import Path

from hilite.hilite_doc import load_hilite_doc, render_header


def load_project_context(cwd: Path | None = None) -> str | None:
    """Return the ``HILITE.md`` header for the repo at *cwd*, or None if absent.

    The header (frontmatter + ``## Overview`` + a TOC of the other section
    names) is always loaded; individual ``## sections`` are fetched on demand
    via ``hilite_section``. If the repo has no ``HILITE.md``, no project context
    is loaded -- first-use registration is driven app-side (doc 14.2).
    """
    doc = load_hilite_doc(cwd)
    if doc is None:
        return None
    return render_header(doc)
