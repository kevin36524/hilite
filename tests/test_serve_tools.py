"""Serve-mode tool catalog tests (doc 13 cross-repo delegation).

Covers that the three delegation tools register on demand, are gated out of the
back-compat defaults, and that their result formatters render contracts / handles
/ barriers (and pass errors through) the way the model should read them.
"""

from hilite.serve import (
    DEFAULT_SESSION_TOOLS,
    TOOL_CATALOG,
    _ack_result,
    _await_result,
    _dispatch_result,
    _projects_result,
    _workspace_result,
)

DELEGATION_TOOLS = ["run_in_workspace", "dispatch_to_workspace", "await_workspaces"]

DISCOVERY_TOOLS = [
    "write_hilite_md",
    "find_relevant_projects",
    "open_project_session",
    "report_to_parent",
]


def test_delegation_tools_registered_with_matching_schema():
    for name in DELEGATION_TOOLS:
        assert name in TOOL_CATALOG, f"{name} missing from catalog"
        assert TOOL_CATALOG[name].schema["name"] == name


def test_delegation_tools_are_not_in_back_compat_defaults():
    # They must be injected explicitly via the orchestrator allowlist, never on by
    # default (doc 13 §4.1).
    for name in DELEGATION_TOOLS:
        assert name not in DEFAULT_SESSION_TOOLS


def test_run_schema_requires_workspace_and_task():
    schema = TOOL_CATALOG["run_in_workspace"].schema["input_schema"]
    assert set(schema["required"]) == {"workspace", "task"}


def test_await_schema_requires_handles_array():
    schema = TOOL_CATALOG["await_workspaces"].schema["input_schema"]
    assert schema["required"] == ["handles"]
    assert schema["properties"]["handles"]["type"] == "array"


def test_workspace_result_renders_contract_fields():
    out = _workspace_result({
        "workspace": "BE",
        "summary": "added endpoint",
        "filesChanged": ["api/foo.py"],
        "contract": "GET /v1/foo",
        "followUps": ["wire FE"],
    })
    assert "Workspace: BE" in out
    assert "added endpoint" in out
    assert "api/foo.py" in out
    assert "GET /v1/foo" in out
    assert "wire FE" in out


def test_result_formatters_pass_errors_through():
    assert _workspace_result({"error": "unknown workspace"}).startswith("Error:")
    assert _dispatch_result({"error": "busy"}).startswith("Error:")
    assert _await_result({"error": "nope"}).startswith("Error:")


def test_dispatch_result_reports_handle():
    out = _dispatch_result({"workspace": "FE", "handle": "w2", "status": "running"})
    assert "w2" in out and "FE" in out


def test_await_result_concatenates_each_contract():
    out = _await_result({"results": [
        {"workspace": "BE", "summary": "s1"},
        {"workspace": "FE", "summary": "s2"},
    ]})
    assert "Workspace: BE" in out and "Workspace: FE" in out
    assert "s1" in out and "s2" in out


# --- Dynamic project discovery tools (doc 14.1) ------------------------------


def test_discovery_tools_registered_with_matching_schema():
    for name in DISCOVERY_TOOLS:
        assert name in TOOL_CATALOG, f"{name} missing from catalog"
        assert TOOL_CATALOG[name].schema["name"] == name


def test_discovery_tools_are_not_in_back_compat_defaults():
    for name in DISCOVERY_TOOLS:
        assert name not in DEFAULT_SESSION_TOOLS


def test_write_hilite_md_schema_required_fields():
    schema = TOOL_CATALOG["write_hilite_md"].schema["input_schema"]
    assert set(schema["required"]) == {"path", "frontmatter", "overview"}


def test_open_project_session_schema_required_fields():
    schema = TOOL_CATALOG["open_project_session"].schema["input_schema"]
    assert set(schema["required"]) == {"project", "repo", "task"}


def test_report_to_parent_schema_requires_summary():
    schema = TOOL_CATALOG["report_to_parent"].schema["input_schema"]
    assert schema["required"] == ["summary"]


def test_ack_result_falls_back_and_passes_errors_through():
    assert _ack_result(None, "ok msg") == "ok msg"
    assert _ack_result("custom", "ok msg") == "custom"
    assert _ack_result({"message": "wrote it"}, "ok msg") == "wrote it"
    assert _ack_result({"error": "boom"}, "ok msg").startswith("Error:")


def test_projects_result_renders_ranked_matches():
    out = _projects_result({"matches": [
        {"name": "mail-api", "role": "BE", "repo": "../mail-api",
         "description": "Mail backend"},
        {"name": "mail-web", "role": "FE", "repo": "../mail-web"},
    ]})
    assert "mail-api" in out and "mail-web" in out
    assert "BE" in out and "Mail backend" in out


def test_projects_result_handles_empty_and_errors():
    assert _projects_result({"matches": []}) == "No matching projects found."
    assert _projects_result({"error": "index missing"}).startswith("Error:")
