"""Tests for the skills system."""

from pathlib import Path

from hilite.skills import (
    Skill,
    _parse_skill,
    build_skills_index,
    discover_skills,
    load_skill,
)


# ---------------------------------------------------------------------------
# Skill parsing
# ---------------------------------------------------------------------------


def test_parse_skill_with_frontmatter(tmp_path: Path):
    path = tmp_path / "test.md"
    path.write_text(
        "---\n"
        "name: my-skill\n"
        "description: A test skill.\n"
        "tags: [test, demo]\n"
        "version: 1.0.0\n"
        "author: Test User\n"
        "---\n"
        "\n"
        "# Test Skill\n"
        "\n"
        "This is the body.\n"
    )
    skill = _parse_skill(path)
    assert skill.name == "my-skill"
    assert skill.description == "A test skill."
    assert skill.tags == ["test", "demo"]
    assert skill.version == "1.0.0"
    assert skill.author == "Test User"
    assert skill.body.startswith("# Test Skill")
    assert "This is the body." in skill.body


def test_parse_skill_without_frontmatter(tmp_path: Path):
    path = tmp_path / "plain.md"
    path.write_text("This is a plain skill without frontmatter.\n\nMore content.\n")
    skill = _parse_skill(path)
    assert skill.name == "plain"
    assert skill.description == "This is a plain skill without frontmatter."
    assert skill.tags is None
    assert skill.body == "This is a plain skill without frontmatter.\n\nMore content."


def test_parse_skill_description_from_body(tmp_path: Path):
    path = tmp_path / "nodesc.md"
    path.write_text(
        "---\n"
        "name: nodesc\n"
        "---\n"
        "\n"
        "First paragraph that becomes the description because there is none in frontmatter.\n"
    )
    skill = _parse_skill(path)
    assert skill.name == "nodesc"
    assert "First paragraph" in skill.description


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discover_skills_empty():
    skills = discover_skills()
    assert skills == []


def test_discover_skills_single_dir(tmp_path: Path):
    skills_dir = tmp_path / ".hilite" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "alpha.md").write_text("---\nname: alpha\n---\nAlpha body.\n")
    (skills_dir / "beta.md").write_text("---\nname: beta\n---\nBeta body.\n")

    skills = discover_skills(tmp_path)
    assert len(skills) == 2
    names = {s.name for s in skills}
    assert names == {"alpha", "beta"}


def test_discover_skills_override(tmp_path: Path):
    """Project-level skills override global skills with the same name."""
    # Set up global
    global_dir = tmp_path / "global" / ".hilite" / "skills"
    global_dir.mkdir(parents=True)
    (global_dir / "shared.md").write_text("---\nname: shared\n---\nGlobal version.\n")

    # Monkey-patch home directory for this test
    import hilite.skills as skills_module
    original_home = Path.home

    def fake_home():
        return tmp_path / "global"

    skills_module.Path.home = fake_home
    try:
        # Project-level override
        project_dir = tmp_path / ".hilite" / "skills"
        project_dir.mkdir(parents=True)
        (project_dir / "shared.md").write_text(
            "---\nname: shared\n---\nProject version.\n"
        )

        skills = discover_skills(tmp_path)
        assert len(skills) == 1
        assert skills[0].body == "Project version."
    finally:
        skills_module.Path.home = original_home


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def test_load_skill_found(tmp_path: Path):
    skills_dir = tmp_path / ".hilite" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "found.md").write_text("---\nname: found\n---\nSkill body here.\n")

    body = load_skill("found", tmp_path)
    assert body == "Skill body here."


def test_load_skill_not_found(tmp_path: Path):
    body = load_skill("missing", tmp_path)
    assert body.startswith("Error:")
    assert "missing" in body


# ---------------------------------------------------------------------------
# System prompt index
# ---------------------------------------------------------------------------


def test_build_skills_index_empty():
    assert build_skills_index() is None


def test_build_skills_index_with_skills(tmp_path: Path):
    skills_dir = tmp_path / ".hilite" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "skill-a.md").write_text(
        "---\nname: skill-a\ndescription: First skill.\n---\n\nBody A.\n"
    )
    (skills_dir / "skill-b.md").write_text(
        "---\nname: skill-b\ndescription: Second skill.\n---\n\nBody B.\n"
    )

    index = build_skills_index(tmp_path)
    assert index is not None
    assert "skill-a: First skill." in index
    assert "skill-b: Second skill." in index
    assert "skill_view" in index
