"""Tests for MCP client, schema normalization, and config loading."""

import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Schema normalization
# ---------------------------------------------------------------------------


def test_normalize_const():
    from hilite.mcp.client import _normalize_schema

    s = _normalize_schema({"type": "string", "const": "hello"})
    assert s["enum"] == ["hello"]
    assert "const" not in s


def test_normalize_pattern_properties_dropped():
    from hilite.mcp.client import _normalize_schema

    s = _normalize_schema(
        {"type": "object", "properties": {}, "patternProperties": {".*": {"type": "string"}}}
    )
    assert "patternProperties" not in s


def test_normalize_anyof_flatten():
    from hilite.mcp.client import _normalize_schema

    s = _normalize_schema({"anyOf": [{"type": "string"}, {"type": "null"}]})
    assert s["type"] == "string"


def test_normalize_unsupported_keywords_dropped():
    from hilite.mcp.client import _normalize_schema

    s = _normalize_schema(
        {
            "type": "object",
            "$id": "foo",
            "$schema": "http://json-schema.org/draft-07/schema#",
            "definitions": {"Foo": {"type": "string"}},
            "examples": [{"x": 1}],
        }
    )
    assert "$id" not in s
    assert "$schema" not in s
    assert "definitions" not in s
    assert "examples" not in s


def test_normalize_required_pruned():
    from hilite.mcp.client import _normalize_schema

    s = _normalize_schema(
        {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a", "b"]}
    )
    assert s["required"] == ["a"]


def test_normalize_nested_properties():
    from hilite.mcp.client import _normalize_schema

    s = _normalize_schema(
        {"type": "object", "properties": {"nested": {"const": 42, "description": "test"}}}
    )
    assert s["properties"]["nested"]["enum"] == [42]


def test_normalize_if_then_else_dropped():
    from hilite.mcp.client import _normalize_schema

    s = _normalize_schema(
        {
            "type": "object",
            "if": {"properties": {"type": {"const": "foo"}}},
            "then": {"properties": {"value": {"type": "string"}}},
        }
    )
    assert "if" not in s
    assert "then" not in s


# ---------------------------------------------------------------------------
# MCP config loading
# ---------------------------------------------------------------------------


def test_load_mcp_config_empty(tmp_path: Path):
    from hilite.config import load_mcp_config

    # No config files exist
    result = load_mcp_config(tmp_path)
    assert result == {}


def test_load_mcp_config_global_only(monkeypatch, tmp_path: Path):
    from hilite.config import load_mcp_config

    home = tmp_path / "home"
    home.mkdir()
    hilite_dir = home / ".hilite"
    hilite_dir.mkdir()
    config_file = hilite_dir / "config.yaml"
    config_file.write_text(
        "mcp_servers:\n"
        "  slack:\n"
        "    command: npx\n"
        "    args: [-y, '@modelcontextprotocol/server-slack']\n"
        "    enabled: true\n"
        "  disabled-server:\n"
        "    command: echo\n"
        "    enabled: false\n"
    )

    monkeypatch.setattr(Path, "home", lambda: home)
    result = load_mcp_config(tmp_path)
    assert "slack" in result
    assert "disabled-server" not in result


def test_load_mcp_config_project_override(monkeypatch, tmp_path: Path):
    from hilite.config import load_mcp_config

    home = tmp_path / "home"
    home.mkdir()
    hilite_dir = home / ".hilite"
    hilite_dir.mkdir()
    (hilite_dir / "config.yaml").write_text(
        "mcp_servers:\n"
        "  shared:\n"
        "    command: global-cmd\n"
        "    env:\n"
        "      FOO: bar\n"
    )

    monkeypatch.setattr(Path, "home", lambda: home)

    # Project-level overrides global
    project_config = tmp_path / ".hilite" / "config.yaml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text(
        "mcp_servers:\n"
        "  shared:\n"
        "    command: project-cmd\n"
        "    env:\n"
        "      FOO: baz\n"
    )

    result = load_mcp_config(tmp_path)
    assert result["shared"]["command"] == "project-cmd"


def test_load_mcp_config_enabled_filter(monkeypatch, tmp_path: Path):
    from hilite.config import load_mcp_config

    home = tmp_path / "home"
    home.mkdir()
    hilite_dir = home / ".hilite"
    hilite_dir.mkdir()
    (hilite_dir / "config.yaml").write_text(
        "mcp_servers:\n"
        "  alpha:\n    command: a\n"
        "  beta:\n    command: b\n"
        "  gamma:\n    command: c\n"
    )

    monkeypatch.setattr(Path, "home", lambda: home)

    project_config = tmp_path / ".hilite" / "config.yaml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text("mcp:\n  enabled:\n    - alpha\n    - beta\n")

    result = load_mcp_config(tmp_path)
    assert set(result.keys()) == {"alpha", "beta"}


def test_load_mcp_config_disabled_filter(monkeypatch, tmp_path: Path):
    from hilite.config import load_mcp_config

    home = tmp_path / "home"
    home.mkdir()
    hilite_dir = home / ".hilite"
    hilite_dir.mkdir()
    (hilite_dir / "config.yaml").write_text(
        "mcp_servers:\n"
        "  alpha:\n    command: a\n"
        "  beta:\n    command: b\n"
    )

    monkeypatch.setattr(Path, "home", lambda: home)

    project_config = tmp_path / ".hilite" / "config.yaml"
    project_config.parent.mkdir(parents=True)
    project_config.write_text("mcp:\n  disabled:\n    - beta\n")

    result = load_mcp_config(tmp_path)
    assert set(result.keys()) == {"alpha"}


# ---------------------------------------------------------------------------
# mTLS cert resolution
# ---------------------------------------------------------------------------


def test_resolve_client_cert_single_file(tmp_path: Path):
    from hilite.mcp.auth import resolve_client_cert

    cert = tmp_path / "cert.pem"
    cert.write_text("CERT")
    result, verify = resolve_client_cert(str(cert))
    assert result == str(cert)
    assert verify is True


def test_resolve_client_cert_with_key(tmp_path: Path):
    from hilite.mcp.auth import resolve_client_cert

    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("CERT")
    key.write_text("KEY")
    result, verify = resolve_client_cert(str(cert), str(key))
    assert result == (str(cert), str(key))


def test_resolve_client_cert_list_two_elements(tmp_path: Path):
    from hilite.mcp.auth import resolve_client_cert

    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("CERT")
    key.write_text("KEY")
    result, verify = resolve_client_cert([str(cert), str(key)])
    assert result == (str(cert), str(key))


def test_resolve_client_cert_list_three_elements(tmp_path: Path):
    from hilite.mcp.auth import resolve_client_cert

    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("CERT")
    key.write_text("KEY")
    result, verify = resolve_client_cert([str(cert), str(key), "passphrase"])
    assert result == (str(cert), str(key), "passphrase")


def test_resolve_client_cert_missing_file(tmp_path: Path):
    from hilite.mcp.auth import resolve_client_cert

    with pytest.raises(FileNotFoundError):
        resolve_client_cert(str(tmp_path / "nonexistent.pem"))


def test_resolve_client_cert_list_with_separate_key_error(tmp_path: Path):
    from hilite.mcp.auth import resolve_client_cert

    cert = tmp_path / "cert.pem"
    cert.write_text("CERT")
    with pytest.raises(ValueError, match="Cannot use 'client_key'"):
        resolve_client_cert([str(cert)], str(tmp_path / "key.pem"))


# ---------------------------------------------------------------------------
# Env interpolation
# ---------------------------------------------------------------------------


def test_resolve_env_interpolation():
    from hilite.mcp.client import _resolve_env

    os.environ["TEST_VAR_12345"] = "hello"
    try:
        result = _resolve_env({"KEY": "${TEST_VAR_12345}", "STATIC": "value"})
        assert result == {"KEY": "hello", "STATIC": "value"}
    finally:
        del os.environ["TEST_VAR_12345"]


def test_resolve_env_none():
    from hilite.mcp.client import _resolve_env

    assert _resolve_env(None) is None


# ---------------------------------------------------------------------------
# MCP tool name prefixing
# ---------------------------------------------------------------------------


def test_mcp_tool_to_tool_param():
    from anthropic.types import ToolParam
    from mcp.types import Tool as MCPTool

    from hilite.mcp.client import _mcp_tool_to_tool_param

    tool = MCPTool(
        name="post_message",
        description="Post a message to Slack",
        inputSchema={
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["channel", "message"],
        },
    )
    param = _mcp_tool_to_tool_param("slack", tool)
    assert param is not None
    assert isinstance(param, dict)
    assert param["name"] == "mcp_slack_post_message"
    assert param["description"] == "Post a message to Slack"
    assert param["input_schema"]["type"] == "object"


def test_mcp_tool_to_tool_param_bad_schema():
    from mcp.types import Tool as MCPTool

    from hilite.mcp.client import _mcp_tool_to_tool_param

    # Schema that cannot be normalized (only unsupported keywords)
    tool = MCPTool(
        name="bad_tool",
        description="A tool with an invalid schema",
        inputSchema={"$id": "foo", "$schema": "http://..."},
    )
    param = _mcp_tool_to_tool_param("server", tool)
    assert param is not None
    assert param["input_schema"]["type"] == "object"
