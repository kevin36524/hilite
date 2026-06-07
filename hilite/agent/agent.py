"""Core AIAgent class -- the heart of HiLite."""

from __future__ import annotations

import sys
from collections.abc import Callable
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

import anthropic

from hilite.agent.anthropic import build_client
from hilite.config import load_config
from hilite.constants import DEFAULT_MODEL
from hilite.context import load_project_context
from hilite.learning_loop import LearningLoop, LearningLoopConfig
from hilite.memory import MemoryStore, get_project_key
from hilite.skills import build_skills_index, load_skill
from hilite.skills_auto import update_skill, write_auto_skill
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
        model: str = DEFAULT_MODEL,
        system_prompt: str | None = None,
        max_turns: int = 50,
        session_id: str | None = None,
        project_root: Path | None = None,
        preload_skill: str | None = None,
        event_sink: Callable[[dict], None] | None = None,
    ):
        self.model = model
        self.max_turns = max_turns
        self.client = build_client()
        self._event_sink = event_sink
        self.tools = ToolRegistry()
        self.session_id = session_id or generate_session_id()
        self.project_root = project_root or Path.cwd()

        # Build the stable system prompt (frozen for the session)
        home = Path.home() / ".hilite"
        project_key = get_project_key()
        self.memory = MemoryStore(home, project_key=project_key)
        project_context = load_project_context()

        # A user-supplied system prompt overrides SOUL.md but still gets the
        # memory blocks, project context, and management instructions appended.
        self.system_prompt = self.memory.build_system_prompt(
            default_identity=DEFAULT_SYSTEM_PROMPT,
            project_context=project_context,
            identity_override=system_prompt,
        )

        # Inject skills index into system prompt
        skills_index = build_skills_index(self.project_root)
        if skills_index:
            self.system_prompt += f"\n\n{skills_index}"

        # Preload skill if requested (--skill flag)
        self._preloaded_skill: str | None = None
        if preload_skill:
            skill_content = load_skill(preload_skill, self.project_root)
            if not skill_content.startswith("Error:"):
                self._preloaded_skill = skill_content

        # Load existing session if available
        session_data = load_session(self.session_id) if session_id else None
        if session_data:
            self.messages = _deserialize_messages(session_data.get("messages", []))
        else:
            self.messages: list[MessageParam] = []

        # --- Learning loop tracking ---
        self._tool_call_count: int = 0
        self._skills_used: list[str] = []
        self._error_count: int = 0
        self._hit_max_turns: bool = False
        self._turn_count: int = 0

        # Initialize learning loop from config
        config = load_config()
        ll_cfg = config.get("learning_loop", {})
        self._learning_loop = self._build_learning_loop(ll_cfg)

    def _emit(self, event: dict) -> None:
        """Forward an event to the optional event sink (serve mode)."""
        if self._event_sink is not None:
            self._event_sink(event)

    def _build_learning_loop(self, ll_cfg: dict) -> LearningLoop | None:
        """Build a LearningLoop from config, or None if disabled."""
        # Quick check: if all sub-features are disabled, skip
        all_disabled = all(
            not ll_cfg.get(k, {}).get("enabled", True)
            for k in ("auto_skills", "auto_memory", "skill_improvement", "context_compression")
        )
        if all_disabled:
            return None

        cfg = LearningLoopConfig(
            auto_skills_enabled=ll_cfg.get("auto_skills", {}).get("enabled", True),
            auto_skills_min_tool_calls=ll_cfg.get("auto_skills", {}).get("min_tool_calls", 5),
            auto_memory_enabled=ll_cfg.get("auto_memory", {}).get("enabled", True),
            auto_memory_min_turns=ll_cfg.get("auto_memory", {}).get("min_turns", 3),
            skill_improvement_enabled=ll_cfg.get("skill_improvement", {}).get("enabled", True),
            context_compression_enabled=ll_cfg.get("context_compression", {}).get("enabled", True),
            context_compression_threshold=ll_cfg.get("context_compression", {}).get("threshold_messages", 30),
            model=self.model,
        )
        return LearningLoop(client=self.client, config=cfg)

    def run_conversation(self, user_message: str) -> str:
        """Run a full conversation turn, including any tool calls."""
        # Prepend preloaded skill content if present
        if self._preloaded_skill:
            user_message = (
                f"[Skill loaded: follow these instructions]\n\n"
                f"{self._preloaded_skill}\n\n"
                f"---\n\n{user_message}"
            )
            self._preloaded_skill = None  # only once

        # Add user message to history
        self.messages.append(
            MessageParam(role="user", content=user_message)
        )

        turn = 0
        while turn < self.max_turns:
            turn += 1
            self._turn_count = turn

            # Maybe compress context before calling model
            if self._learning_loop is not None:
                compressed = self._learning_loop.maybe_compress_context(
                    _serialize_messages(self.messages)
                )
                if compressed is not None:
                    self.messages = _deserialize_messages(compressed)

            # Call the model
            response = self._call_model()

            if response.stop_reason == "max_tokens":
                print(
                    "[hilite] Warning: response truncated at max_tokens; "
                    "output may be incomplete.",
                    file=sys.stderr,
                )

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
                # No tools called -- we're done. The assistant text already
                # streamed via `_call_model` (assistant_start/delta/stop), so we
                # do not re-emit a monolithic `assistant` event here.
                text = "\n".join(block.text for block in text_blocks)
                self.messages.append(
                    MessageParam(
                        role="assistant",
                        content=[TextBlockParam(type="text", text=text)] if text else [],
                    )
                )
                self._save_session()
                self._maybe_learn()
                return text

            # Build assistant message with tool uses
            # Text streamed via `_call_model`; here we only rebuild the assistant
            # message for history (no monolithic `assistant` re-emit).
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
                self._emit({
                    "type": "pre_tool_use",
                    "tool": block.name,
                    "input": block.input,
                })
                result = self.tools.execute(block.name, block.input)
                self._emit({
                    "type": "post_tool_use",
                    "tool": block.name,
                    "ok": not (isinstance(result, str) and result.startswith("Error:")),
                })
                tool_results.append(
                    ToolResultBlockParam(
                        type="tool_result",
                        tool_use_id=block.id,
                        content=str(result),
                    )
                )
                # Track tool usage
                self._tool_call_count += 1
                if isinstance(result, str) and result.startswith("Error:"):
                    self._error_count += 1
                if block.name == "skill_view" and isinstance(block.input, dict):
                    skill_name = block.input.get("name")
                    if skill_name and skill_name not in self._skills_used:
                        self._skills_used.append(skill_name)

            self.messages.append(
                MessageParam(role="user", content=tool_results)
            )

        # Max turns reached
        self._hit_max_turns = True
        self._save_session()
        self._maybe_learn()
        return "[Max turns reached]"

    def _maybe_learn(self) -> None:
        """Run post-conversation learning-loop nudges."""
        if self._learning_loop is None:
            return

        serialized = _serialize_messages(self.messages)
        ll = self._learning_loop
        cfg = ll.config

        # 1. Autonomous skill creation
        if cfg.auto_skills_enabled and self._tool_call_count >= cfg.auto_skills_min_tool_calls:
            try:
                skill = ll.maybe_create_skill(serialized, self._tool_call_count)
                if skill:
                    name, _desc, content = skill
                    write_auto_skill(name, content)
                    print(f"[hilite] Created skill: {name}", file=sys.stderr)
            except Exception as e:
                print(f"[hilite] Skill creation failed: {e}", file=sys.stderr)

        # 2. Self-curating memory
        if cfg.auto_memory_enabled and self._turn_count >= cfg.auto_memory_min_turns:
            try:
                entries = ll.maybe_persist_memory(serialized, self._turn_count)
                if entries:
                    for entry in entries:
                        self.memory.append_memory(entry["section"], entry["content"])
                    print(
                        f"[hilite] Persisted {len(entries)} memory entries",
                        file=sys.stderr,
                    )
            except Exception as e:
                print(f"[hilite] Memory persistence failed: {e}", file=sys.stderr)

        # 3. Skill improvement
        if cfg.skill_improvement_enabled and self._skills_used and (
            self._error_count > 0 or self._hit_max_turns
        ):
            try:
                for skill_name in self._skills_used:
                    skill_content = load_skill(skill_name, self.project_root)
                    if skill_content.startswith("Error:"):
                        continue
                    improved = ll.maybe_improve_skill(
                        messages=serialized,
                        skill_name=skill_name,
                        skill_content=skill_content,
                        had_errors=self._error_count > 0,
                        hit_max_turns=self._hit_max_turns,
                    )
                    if improved:
                        name, _desc, content = improved
                        update_skill(name, content)
                        print(f"[hilite] Improved skill: {name}", file=sys.stderr)
                        break  # Only improve one skill per conversation
            except Exception as e:
                print(f"[hilite] Skill improvement failed: {e}", file=sys.stderr)

    def _save_session(self) -> None:
        """Persist the current conversation to disk."""
        metadata = {
            "tool_call_count": self._tool_call_count,
            "skills_used": self._skills_used,
            "error_count": self._error_count,
            "hit_max_turns": self._hit_max_turns,
            "turn_count": self._turn_count,
        }
        save_session(self.session_id, _serialize_messages(self.messages), metadata)

    def _call_model(self) -> Message:
        """Call the Anthropic API with current state.

        The system prompt is sent as a cacheable block: a ``cache_control``
        breakpoint on the (session-frozen) system text caches the stable
        tools + system prefix, so only the growing message tail is re-billed on
        each turn of the tool loop.

        In serve mode (``event_sink`` set) this streams text deltas through the
        sink as ``assistant_start`` / ``assistant_delta`` / ``assistant_stop``
        events and returns the assembled final ``Message`` -- identical in shape
        to ``create()``, so the tool loop and message history are unaffected.
        The one-shot ``hilite -p`` path (no sink) stays on ``create()`` and emits
        no streaming events.
        """
        kwargs = dict(
            model=self.model,
            max_tokens=4096,
            system=[
                {
                    "type": "text",
                    "text": self.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=self.messages,
            tools=self.tools.get_schemas(),
        )
        try:
            if self._event_sink is None:
                return self.client.messages.create(**kwargs)

            with self.client.messages.stream(**kwargs) as stream:
                block_is_text = False
                for event in stream:
                    if event.type == "content_block_start":
                        block_is_text = event.content_block.type == "text"
                        if block_is_text:
                            self._emit({"type": "assistant_start"})
                    elif (
                        event.type == "content_block_delta"
                        and event.delta.type == "text_delta"
                    ):
                        self._emit({"type": "assistant_delta", "text": event.delta.text})
                    elif event.type == "content_block_stop" and block_is_text:
                        self._emit({"type": "assistant_stop"})
                        block_is_text = False
                return stream.get_final_message()
        except anthropic.APIStatusError as e:
            # The SDK already retried transient 429/5xx responses; if we're here
            # the failure persisted. Surface a clear, actionable message.
            detail = ""
            if e.status_code in (401, 403):
                detail = " (check ANTHROPIC_API_KEY / Claude Code credentials)"
            elif e.status_code == 404:
                detail = f" (is model '{self.model}' a valid model ID?)"
            elif e.status_code == 429:
                retry_after = e.response.headers.get("retry-after")
                detail = f" (rate limited{f'; retry after {retry_after}s' if retry_after else ''})"
            elif e.status_code == 529:
                detail = " (API overloaded -- try again shortly)"
            raise RuntimeError(
                f"Anthropic API error {e.status_code}{detail}: {e.message}"
            ) from e
        except anthropic.APIConnectionError as e:
            raise RuntimeError(f"Could not reach the Anthropic API: {e}") from e
