"""Core AIAgent class -- the heart of HiLite."""

from __future__ import annotations

from pathlib import Path

from anthropic.types import (
    Message,
    MessageParam,
    TextBlock,
    TextBlockParam,
    ToolResultBlockParam,
    ToolUseBlock,
    ToolUseBlockParam,
)

from hilite.agent.anthropic import build_client
from hilite.context import load_project_context
from hilite.memory import MemoryStore, get_project_key
from hilite.state import generate_session_id, load_session, save_session
from hilite.tools.registry import ToolRegistry

DEFAULT_SYSTEM_PROMPT = (
    "You are HiLite, a helpful AI assistant. You have access to tools that let you "
    "read and write files, run shell commands, and execute Python code. "
    "Use tools when they help answer the user's question. "
    "Always think step by step."
)


def _serialize_messages(messages: list[MessageParam]) -> list[dict]:
    """Convert MessageParam list to JSON-serializable dicts."""
    result = []
    for msg in messages:
        m: dict = {"role": msg["role"], "content": msg["content"]}
        result.append(m)
    return result


def _deserialize_messages(data: list[dict]) -> list[MessageParam]:
    """Convert JSON dicts back to MessageParam list."""
    return [MessageParam(role=m["role"], content=m["content"]) for m in data]


class AIAgent:
    """AI agent with tool-calling loop, session persistence, and memory."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-6-20250601",
        system_prompt: str | None = None,
        max_turns: int = 50,
        session_id: str | None = None,
    ):
        self.model = model
        self.max_turns = max_turns
        self.client = build_client()
        self.tools = ToolRegistry()
        self.session_id = session_id or generate_session_id()

        # Build the stable system prompt (frozen for the session)
        home = Path.home() / ".hilite"
        project_key = get_project_key()
        self.memory = MemoryStore(home, project_key=project_key)
        project_context = load_project_context()

        if system_prompt:
            # User override takes precedence over SOUL.md but still gets
            # memory blocks and project context appended.
            self.system_prompt = system_prompt
        else:
            self.system_prompt = self.memory.build_system_prompt(
                default_identity=DEFAULT_SYSTEM_PROMPT,
                project_context=project_context,
            )

        # Load existing session if available
        session_data = load_session(self.session_id) if session_id else None
        if session_data:
            self.messages = _deserialize_messages(session_data.get("messages", []))
        else:
            self.messages: list[MessageParam] = []

    def run_conversation(self, user_message: str) -> str:
        """Run a full conversation turn, including any tool calls."""
        # Add user message to history
        self.messages.append(
            MessageParam(role="user", content=user_message)
        )

        turn = 0
        while turn < self.max_turns:
            turn += 1

            # Call the model
            response = self._call_model()

            # Extract content blocks
            content_blocks = response.content

            # Check for tool_use blocks
            tool_uses = [
                block for block in content_blocks
                if isinstance(block, ToolUseBlock)
            ]
            text_blocks = [
                block for block in content_blocks
                if isinstance(block, TextBlock)
            ]

            if not tool_uses:
                # No tools called -- we're done
                text = "\n".join(block.text for block in text_blocks)
                self.messages.append(
                    MessageParam(
                        role="assistant",
                        content=[TextBlockParam(type="text", text=text)] if text else [],
                    )
                )
                self._save_session()
                return text

            # Build assistant message with tool uses
            assistant_content: list[ToolUseBlockParam | TextBlockParam] = []
            for block in text_blocks:
                assistant_content.append(
                    TextBlockParam(type="text", text=block.text)
                )
            for block in tool_uses:
                assistant_content.append(
                    ToolUseBlockParam(
                        type="tool_use",
                        id=block.id,
                        name=block.name,
                        input=block.input,
                    )
                )

            self.messages.append(
                MessageParam(role="assistant", content=assistant_content)
            )

            # Execute tools and build tool_result messages
            tool_results: list[ToolResultBlockParam] = []
            for block in tool_uses:
                result = self.tools.execute(block.name, block.input)
                tool_results.append(
                    ToolResultBlockParam(
                        type="tool_result",
                        tool_use_id=block.id,
                        content=str(result),
                    )
                )

            self.messages.append(
                MessageParam(role="user", content=tool_results)
            )

        # Max turns reached
        self._save_session()
        return "[Max turns reached]"

    def _save_session(self) -> None:
        """Persist the current conversation to disk."""
        save_session(self.session_id, _serialize_messages(self.messages))

    def _call_model(self) -> Message:
        """Call the Anthropic API with current state."""
        return self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=self.system_prompt,
            messages=self.messages,
            tools=self.tools.get_schemas(),
        )
