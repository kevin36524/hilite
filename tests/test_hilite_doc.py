"""Tests for the HILITE.md progressive-disclosure loader (doc 14.1)."""

from pathlib import Path

from hilite.context import load_project_context
from hilite.hilite_doc import (
    hilite_section,
    load_hilite_doc,
    parse_hilite_doc,
    render_header,
)

SAMPLE = """\
---
name: mail-api
description: Mail backend (Python/FastAPI). REST under /v1/*.
project: Yahoo Mail
role: BE
siblings:
  - mail-web (FE) — ../mail-web
---

## Overview
mail-api serves the Mail backend. Always-loaded, kept to a few lines.

## Build & test            <!-- detail section: loaded on demand -->
Run `make build` then `pytest`.

## Public surface / contracts
GET /v1/messages returns the inbox.

## Conventions
Use snake_case everywhere.
"""


def _write(tmp_path: Path, content: str = SAMPLE) -> Path:
    (tmp_path / "HILITE.md").write_text(content)
    return tmp_path


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_frontmatter_name_and_description():
    doc = parse_hilite_doc(SAMPLE, Path("HILITE.md"))
    assert doc.name == "mail-api"
    assert doc.description.startswith("Mail backend")
    assert doc.meta["role"] == "BE"


def test_overview_is_parsed_separately_from_other_sections():
    doc = parse_hilite_doc(SAMPLE, Path("HILITE.md"))
    assert "Mail backend" in doc.overview
    assert "Overview" not in doc.sections
    assert set(doc.sections) == {
        "Build & test",
        "Public surface / contracts",
        "Conventions",
    }


def test_section_heading_comment_is_stripped_from_name():
    doc = parse_hilite_doc(SAMPLE, Path("HILITE.md"))
    # The "<!-- detail section ... -->" annotation must not leak into the name.
    assert "Build & test" in doc.sections
    assert doc.sections["Build & test"] == "Run `make build` then `pytest`."


def test_get_section_is_case_insensitive():
    doc = parse_hilite_doc(SAMPLE, Path("HILITE.md"))
    assert doc.get_section("conventions") == "Use snake_case everywhere."
    assert doc.get_section("nope") is None


# ---------------------------------------------------------------------------
# Header rendering (always-loaded) vs sections (on demand)
# ---------------------------------------------------------------------------


def test_header_loads_overview_and_a_section_toc_only():
    doc = parse_hilite_doc(SAMPLE, Path("HILITE.md"))
    header = render_header(doc)
    # Always loaded: frontmatter + overview.
    assert "name: mail-api" in header
    assert "Mail backend" in header
    # TOC lists the other section names...
    assert "Build & test" in header
    assert "Conventions" in header
    # ...but NOT their bodies.
    assert "make build" not in header
    assert "snake_case" not in header


def test_load_project_context_returns_header_for_hilite_md(tmp_path: Path):
    _write(tmp_path)
    ctx = load_project_context(tmp_path)
    assert ctx is not None
    assert "mail-api" in ctx
    assert "make build" not in ctx  # section body stays out of context


# ---------------------------------------------------------------------------
# hilite_section tool (on-demand fetch)
# ---------------------------------------------------------------------------


def test_hilite_section_returns_body_on_demand(tmp_path: Path):
    _write(tmp_path)
    assert hilite_section("Build & test", tmp_path) == "Run `make build` then `pytest`."


def test_hilite_section_unknown_lists_available(tmp_path: Path):
    _write(tmp_path)
    out = hilite_section("Nonexistent", tmp_path)
    assert out.startswith("Error:")
    assert "Conventions" in out


def test_hilite_section_no_file(tmp_path: Path):
    assert hilite_section("Build & test", tmp_path).startswith("Error:")


# ---------------------------------------------------------------------------
# Acceptance: HILITE.md only -- the legacy chain is no longer read
# ---------------------------------------------------------------------------


def test_no_hilite_md_means_no_project_context(tmp_path: Path):
    assert load_hilite_doc(tmp_path) is None
    assert load_project_context(tmp_path) is None


def test_hermes_md_is_no_longer_read(tmp_path: Path):
    # A repo with only the legacy files must load NO project context.
    (tmp_path / "HERMES.md").write_text("# legacy hermes context")
    (tmp_path / "AGENTS.md").write_text("# legacy agents context")
    (tmp_path / "CLAUDE.md").write_text("# legacy claude context")
    assert load_project_context(tmp_path) is None
