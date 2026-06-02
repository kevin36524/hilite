"""Helpers for auto-generated skills.

Auto-skills live in ~/.hilite/skills/auto/ and follow the same
markdown+YAML frontmatter format as user-created skills.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from hilite.constants import HILITE_HOME

AUTO_SKILLS_DIR = HILITE_HOME / "skills" / "auto"


def _kebab_case(name: str) -> str:
    """Convert a string to kebab-case."""
    # Replace non-alphanumeric with hyphens
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", name)
    # Collapse multiple hyphens
    cleaned = re.sub(r"-+", "-", cleaned)
    # Strip leading/trailing hyphens
    cleaned = cleaned.strip("-")
    return cleaned.lower()


def _is_kebab_case(name: str) -> bool:
    """Check if a string is valid kebab-case."""
    return bool(re.fullmatch(r"[a-z][a-z0-9]*(-[a-z0-9]+)*", name))


def write_auto_skill(name: str, content: str) -> str:
    """Write a new auto-generated skill.

    Validates name is kebab-case.  Returns the path written or an
    error message starting with "Error:".
    """
    kebab = _kebab_case(name)
    if not _is_kebab_case(kebab):
        return f"Error: '{name}' is not valid kebab-case. Use lowercase letters, numbers, and hyphens only."

    AUTO_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    path = AUTO_SKILLS_DIR / f"{kebab}.md"

    # Ensure content has YAML frontmatter
    if not content.strip().startswith("---"):
        # Wrap bare content with minimal frontmatter
        content = f"""---
name: {kebab}
description: Auto-generated skill
---

{content}
"""

    path.write_text(content, encoding="utf-8")
    print(f"[hilite] Auto-skill written: {path}", file=sys.stderr)
    return str(path)


def update_skill(name: str, content: str) -> str:
    """Update an existing skill (any directory in discovery paths).

    Returns the path written or an error message starting with "Error:".
    """
    from hilite.skills import _discover_skill_dirs

    kebab = _kebab_case(name)
    if not _is_kebab_case(kebab):
        return f"Error: '{name}' is not valid kebab-case."

    dirs = _discover_skill_dirs()
    for directory in reversed(dirs):  # later dirs override earlier
        path = directory / f"{kebab}.md"
        if path.exists():
            path.write_text(content, encoding="utf-8")
            print(f"[hilite] Skill updated: {path}", file=sys.stderr)
            return str(path)

    return f"Error: Skill '{kebab}' not found in any skill directory."


def parse_skill_from_response(response: str) -> tuple[str, str, str] | None:
    """Parse a skill creation nudge response.

    Returns (name, description, full_content) or None if the response
    indicates NO_SKILL.
    """
    text = response.strip()
    if text.upper().startswith("NO_SKILL"):
        return None
    if "NO SKILL" in text.upper()[:20]:
        return None

    # Extract frontmatter name
    name = "auto-skill"
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1].strip()
            for line in frontmatter.splitlines():
                line = line.strip()
                if line.lower().startswith("name:"):
                    name = line.split(":", 1)[1].strip().strip('"').strip("'")
                    break

    # Extract description from frontmatter or first paragraph
    description = ""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1].strip()
            for line in frontmatter.splitlines():
                line = line.strip()
                if line.lower().startswith("description:"):
                    description = line.split(":", 1)[1].strip().strip('"').strip("'")
                    break
    if not description:
        first_para = re.split(r"\n\s*\n", text, maxsplit=1)[0].strip()
        first_para = re.sub(r"^#+\s*", "", first_para)
        description = first_para[:120] if first_para else f"Skill: {name}"

    return (name, description, text)
