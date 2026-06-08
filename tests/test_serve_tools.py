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
    _bell_nudge_prompt,
    _bell_result,
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


# --- Bell / notification detection (doc 15) ----------------------------------


def test_wait_for_bell_registered_with_matching_schema():
    entry = TOOL_CATALOG["terminal_wait_for_bell"]
    assert entry.schema["name"] == "terminal_wait_for_bell"
    props = entry.schema["input_schema"]["properties"]
    # All args optional (sensible defaults app-side), incl. an overridable timeout.
    assert entry.schema["input_schema"]["required"] == []
    assert "timeout_ms" in props and "terminal_id" in props


def test_wait_for_bell_is_not_in_back_compat_defaults():
    assert "terminal_wait_for_bell" not in DEFAULT_SESSION_TOOLS


def test_bell_result_announces_completion_with_screen():
    # Hook-driven turn completion (Stop / idle_prompt).
    out = _bell_result({
        "belled": True, "reason": "stop", "bellCount": 1, "waitedMs": 48213,
        "text": "> ", "cursorRow": 31, "cursorCol": 2, "settled": True,
    })
    assert "finished" in out.lower()
    assert "48s" in out          # waitedMs rendered in seconds
    assert "> " in out           # screen folded in


def test_bell_result_explains_permission_prompt():
    out = _bell_result({
        "belled": True, "reason": "permission_prompt", "bellCount": 1,
        "text": "Do you want to proceed?",
    })
    assert "approval" in out.lower() or "permission" in out.lower()
    assert "terminal_send_key" in out          # tells the agent how to answer
    assert "Do you want to proceed?" in out


def test_bell_result_flags_multiple_signals():
    out = _bell_result({"belled": True, "reason": "stop", "bellCount": 3, "text": "x"})
    assert "3 signals" in out


def test_bell_result_handles_timeout_and_process_exit():
    timeout = _bell_result({"belled": False, "reason": "timeout", "text": "spinner"})
    assert "timeout" in timeout.lower()
    assert "spinner" in timeout

    exited = _bell_result({"belled": False, "reason": "process_exit", "text": ""})
    assert "exited" in exited.lower()


def test_bell_result_still_handles_bare_bell_fallback():
    out = _bell_result({"belled": True, "reason": "bell", "bellCount": 1, "text": "> "})
    assert "bell" in out.lower()
    assert "> " in out


def test_bell_result_passes_errors_through():
    assert _bell_result({"error": "no active Claude CLI terminal"}).startswith("Error:")


def test_bell_nudge_prompt_steers_to_snapshot_and_is_optional():
    # The unsolicited-push nudge (doc 15 §4b): names the reason, points at the
    # terminal, and makes acting optional so an incidental signal isn't busywork.
    p = _bell_nudge_prompt("stop")
    assert "stop" in p
    assert "terminal_snapshot" in p
    assert "idle" in p.lower()
