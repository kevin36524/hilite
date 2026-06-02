"""Skills system -- procedural knowledge as markdown files.

Skills are reusable markdown files with YAML frontmatter that HiLite
can load on demand via the ``skill_view`` tool or ``/skill-name`` slash
commands.

Discovery paths (later paths override earlier ones):
  1. ~/.hilite/skills/*.md          -- global personal skills
  2. .hilite/skills/*.md            -- project-level HiLite skills
  3. .agents/skills/*.md            -- generic cross-tool agent skills
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path



# ---------------------------------------------------------------------------
# Skill dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Skill:
    """A parsed skill with its metadata and body."""

    name: str
    description: str
    body: str
    path: Path
    tags: list[str] | None = None
    version: str | None = None
    author: str | None = None


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _discover_skill_dirs(project_root: Path | None = None) -> list[Path]:
    """Return skill directories in precedence order."""
    dirs: list[Path] = [Path.home() / ".hilite" / "skills"]
    if project_root:
        dirs.extend([
            project_root / ".hilite" / "skills",
            project_root / ".agents" / "skills",
        ])
    return [d for d in dirs if d.exists() and d.is_dir()]


def discover_skills(project_root: Path | None = None) -> list[Skill]:
    """Scan all skill directories and return skills.

    Later directories override earlier ones for duplicate skill names.
    """
    dirs = _discover_skill_dirs(project_root)
    seen: dict[str, Skill] = {}

    for directory in dirs:
        for path in directory.glob("*.md"):
            if not path.is_file():
                continue
            try:
                skill = _parse_skill(path)
            except Exception as e:
                print(
                    f"[hilite] Warning: failed to parse skill '{path.name}': {e}",
                    file=sys.stderr,
                )
                continue

            if skill.name in seen:
                # Later directory wins -- override
                pass
            seen[skill.name] = skill

    return list(seen.values())


def _parse_skill(path: Path) -> Skill:
    """Parse a single skill markdown file.

    Expects YAML frontmatter delimited by ``---``.
    """
    content = path.read_text(encoding="utf-8")

    # Extract frontmatter
    name = path.stem
    description = ""
    body = content.strip()
    tags: list[str] | None = None
    version: str | None = None
    author: str | None = None

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter = parts[1].strip()
            body = parts[2].strip()

            # Simple YAML frontmatter parsing (avoid heavy dependency)
            for line in frontmatter.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    key, value = line.split(":", 1)
                    key = key.strip().lower()
                    value = value.strip().strip('"').strip("'")

                    if key == "name":
                        name = value or name
                    elif key == "description":
                        description = value
                    elif key == "version":
                        version = value
                    elif key == "author":
                        author = value
                    elif key == "tags":
                        # Handle [tag1, tag2] or - tag1 / - tag2
                        if value.startswith("[") and value.endswith("]"):
                            tags = [
                                t.strip().strip('"').strip("'")
                                for t in value[1:-1].split(",")
                            ]
                        else:
                            tags = [value] if value else None

    # If description not in frontmatter, use first paragraph of body
    if not description:
        first_para = re.split(r"\n\s*\n", body, maxsplit=1)[0].strip()
        # Remove markdown heading markers
        first_para = re.sub(r"^#+\s*", "", first_para)
        description = first_para[:120] if first_para else f"Skill: {name}"

    return Skill(
        name=name,
        description=description,
        body=body,
        path=path,
        tags=tags,
        version=version,
        author=author,
    )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_skill(name: str, project_root: Path | None = None) -> str:
    """Load a skill by name and return its body (frontmatter stripped).

    Returns an error message if the skill is not found.
    """
    dirs = _discover_skill_dirs(project_root)

    # Search in reverse precedence (later dirs win)
    for directory in reversed(dirs):
        path = directory / f"{name}.md"
        if path.exists():
            try:
                skill = _parse_skill(path)
                return skill.body
            except Exception as e:
                return f"Error: Failed to parse skill '{name}': {e}"

    return f"Error: Skill '{name}' not found."


def list_skill_names(project_root: Path | None = None) -> list[tuple[str, str]]:
    """Return a list of (name, description) tuples for all discovered skills."""
    skills = discover_skills(project_root)
    return [(s.name, s.description) for s in sorted(skills, key=lambda s: s.name)]


# ---------------------------------------------------------------------------
# System prompt integration
# ---------------------------------------------------------------------------


def build_skills_index(project_root: Path | None = None) -> str | None:
    """Build a compact skills index suitable for injection into the system prompt.

    Returns *None* if no skills are discovered.
    """
    skills = discover_skills(project_root)
    if not skills:
        return None

    lines = [
        "You have access to the following skills. Use them when relevant by calling",
        "the skill_view tool with the skill name.",
        "",
    ]
    for skill in sorted(skills, key=lambda s: s.name):
        lines.append(f"- {skill.name}: {skill.description}")

    return "\n".join(lines)
