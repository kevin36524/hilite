"""Tool schema registry and dispatch."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from anthropic.types import ToolParam

from hilite.tools.file import read_file, write_file, list_directory
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
]

# Map tool names to handler functions
TOOL_HANDLERS: dict[str, Callable[..., Any]] = {
    "read_file": read_file,
    "write_file": write_file,
    "list_directory": list_directory,
    "execute_command": execute_command,
    "execute_python": execute_python,
    "memory_manage": memory_manage,
}


class ToolRegistry:
    """Simple tool registry -- schema provider and dispatcher."""

    def get_schemas(self) -> list[ToolParam]:
        """Return tool schemas for the Anthropic API."""
        return TOOL_SCHEMAS

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        """Execute a tool by name with the given arguments."""
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return f"Error: Unknown tool '{name}'"
        try:
            return handler(**arguments)
        except Exception as e:
            return f"Error executing {name}: {e}"
