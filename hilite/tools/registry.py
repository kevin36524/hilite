"""Tool schema registry and dispatch."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from anthropic.types import ToolParam

from hilite.skills import load_skill
from hilite.skills_auto import update_skill, write_auto_skill
from hilite.tools.file import list_directory, read_file, write_file
from hilite.tools.memory import memory_manage
from hilite.tools.shell import execute_command, execute_python


# Tool schema definitions (Anthropic native format)
TOOL_SCHEMAS: list[ToolParam] = [
    ToolParam(
        name="read_file",
        description="Read the contents of a file.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file.",
                },
            },
            "required": ["path"],
        },
    ),
    ToolParam(
        name="write_file",
        description="Write content to a file. Creates parent directories if needed.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file.",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write.",
                },
            },
            "required": ["path", "content"],
        },
    ),
    ToolParam(
        name="list_directory",
        description="List files and directories at a given path.",
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative path to the directory.",
                },
            },
            "required": ["path"],
        },
    ),
    ToolParam(
        name="execute_command",
        description="Execute a shell command. Use with caution.",
        input_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 60).",
                },
            },
            "required": ["command"],
        },
    ),
    ToolParam(
        name="execute_python",
        description="Execute Python code and return the output.",
        input_schema={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute.",
                },
            },
            "required": ["code"],
        },
    ),
    ToolParam(
        name="memory_manage",
        description=(
            "Manage persistent memory. Read or append entries to the user profile "
            "(USER.md) or environment notes (MEMORY.md). Use this to remember "
            "important facts about the user or project for future sessions."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "append"],
                    "description": "Whether to read current entries or append a new one.",
                },
                "section": {
                    "type": "string",
                    "enum": ["user", "memory"],
                    "description": (
                        "Which memory file to target. 'user' = what you know about the user. "
                        "'memory' = things you have observed about the environment."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "For 'append': the declarative fact to save. Write as a single "
                        "concise sentence starting with a verb. Example: 'The user prefers "
                        "dark mode in all tools.'"
                    ),
                },
            },
            "required": ["action", "section"],
        },
    ),
    ToolParam(
        name="skill_view",
        description=(
            "Load a skill by name to get detailed instructions for a specific workflow. "
            "Skills are reusable procedural knowledge written in markdown."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the skill to load (e.g., 'slack-latest').",
                },
            },
            "required": ["name"],
        },
    ),
    ToolParam(
        name="skill_create",
        description=(
            "Create a new reusable skill from procedural knowledge. "
            "Skills are markdown files with YAML frontmatter stored in ~/.hilite/skills/auto/."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name in kebab-case (e.g., 'docker-debug').",
                },
                "description": {
                    "type": "string",
                    "description": "One-line description of what this skill does.",
                },
                "content": {
                    "type": "string",
                    "description": "Full markdown body of the skill. Include YAML frontmatter with name, description, and optional tags.",
                },
            },
            "required": ["name", "content"],
        },
    ),
    ToolParam(
        name="skill_update",
        description=(
            "Update an existing skill with improved content. "
            "The skill must already exist in a discovered skill directory."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the existing skill to update.",
                },
                "content": {
                    "type": "string",
                    "description": "Full updated markdown with frontmatter.",
                },
            },
            "required": ["name", "content"],
        },
    ),
]

# Map tool names to handler functions
TOOL_HANDLERS: dict[str, Callable[..., Any]] = {
    "read_file": read_file,
    "write_file": write_file,
    "list_directory": list_directory,
    "execute_command": execute_command,
    "execute_python": execute_python,
    "memory_manage": memory_manage,
    "skill_view": lambda name: load_skill(name),
    "skill_create": write_auto_skill,
    "skill_update": update_skill,
}


class ToolRegistry:
    """Simple tool registry -- schema provider and dispatcher.

    Supports dynamic registration of MCP tools.  Built-in tools are
    always present; MCP tools are added at ``__init__`` time when the
    MCPClient connects to configured servers.
    """

    def __init__(self) -> None:
        self._schemas: list[ToolParam] = list(TOOL_SCHEMAS)
        self._handlers: dict[str, Callable[..., Any]] = dict(TOOL_HANDLERS)
        self._mcp_client: Any = None

        # Attempt MCP discovery (optional -- gracefully skips if SDK absent)
        try:
            from hilite.mcp.client import MCPClient

            self._mcp_client = MCPClient(self)
            self._mcp_client.discover_all()
        except ImportError:
            pass  # MCP SDK not installed
        except Exception as e:
            import sys

            print(
                f"[hilite] Warning: MCP discovery failed: {e}",
                file=sys.stderr,
            )

    def register_mcp_tools(
        self, schemas: list[ToolParam], handlers: dict[str, Callable[..., Any]]
    ) -> None:
        """Called by MCPClient after discovering tools from a server."""
        self._schemas.extend(schemas)
        self._handlers.update(handlers)

    def get_schemas(self) -> list[ToolParam]:
        """Return tool schemas for the Anthropic API."""
        return self._schemas

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        """Execute a tool by name with the given arguments."""
        handler = self._handlers.get(name)
        if handler is None:
            return f"Error: Unknown tool '{name}'"
        try:
            return handler(**arguments)
        except Exception as e:
            return f"Error executing {name}: {e}"

    def shutdown(self) -> None:
        """Close MCP connections if any."""
        if self._mcp_client is not None:
            try:
                self._mcp_client.shutdown()
            except Exception:
                pass
