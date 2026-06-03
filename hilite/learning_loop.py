"""Closed learning loop for HiLite.

Provides autonomous skill creation, memory persistence, skill improvement,
and context compression via lightweight "nudge" calls to the model.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anthropic

from hilite.constants import DEFAULT_MODEL

if TYPE_CHECKING:
    from anthropic import Anthropic

# ---------------------------------------------------------------------------
# Nudge prompts
# ---------------------------------------------------------------------------

SKILL_CREATION_NUDGE = """\
You are a skill curator. Review the following conversation and determine if the approach is reusable enough to save as a skill.

A skill is reusable procedural knowledge — a workflow, pattern, or set of steps that the agent might need again in similar situations. Good skills are:
- Concrete and actionable (not generic advice)
- Self-contained (include all necessary context)
- Well-structured with clear steps

If NO suitable skill can be extracted, respond with exactly:
NO_SKILL

If YES, respond with the FULL markdown skill file including YAML frontmatter. Example format:

---
name: csv-filter-workflow
description: Read a CSV, filter rows, and write results
tags: [python, data]
---

# CSV Filter Workflow

1. Read the CSV file with pandas
2. Apply filters using boolean indexing
3. Write the filtered data to a new CSV
4. Verify the output file exists and has expected shape

Do NOT wrap the skill in code fences. Output ONLY the skill markdown or NO_SKILL."""

MEMORY_EXTRACTION_NUDGE = """\
You are a memory curator. Extract durable facts worth remembering from this conversation.

Respond with a JSON object in this exact format:
{"entries": [{"section": "user", "content": "..."}, {"section": "memory", "content": "..."}]}

Rules:
- "user" section = facts about the user's preferences, style, background, habits (global)
- "memory" section = facts about the project — conventions, tools, structure, learnings (per-project)
- Only include genuinely durable facts that would be useful in future sessions
- Skip temporary or session-specific state
- Write each entry as a single concise declarative sentence starting with a verb
- If there are no durable facts, return {"entries": []}

Example:
{"entries": [{"section": "user", "content": "The user prefers uv over pip for Python dependency management."}, {"section": "memory", "content": "This project uses pytest for testing with fixtures in conftest.py."}]}"""

SKILL_IMPROVEMENT_NUDGE = """\
You are a skill reviewer. This skill was used during a conversation that had issues (errors or hit max turns). Review the skill and determine if it needs improvement.

Skill content:
{skill_content}

Conversation issues summary:
{issues_summary}

If the skill is fine as-is, respond with exactly:
NO_CHANGE

Otherwise, respond with the FULL updated markdown skill including YAML frontmatter. Focus on:
- Adding missing steps or edge cases
- Clarifying ambiguous instructions
- Adding error handling guidance
- Fixing outdated or incorrect information

Do NOT wrap in code fences. Output ONLY the updated skill or NO_CHANGE."""

CONTEXT_COMPRESSION_NUDGE = """\
Summarize the following conversation turns into a concise paragraph. Preserve:
- Key decisions made
- Important findings or results
- Tool outputs that matter for context
- Any errors encountered and how they were resolved

Omit:
- Greetings and pleasantries
- Redundant back-and-forth
- Verbose tool output that can be summarized
- Tangent discussions

Respond with ONLY the summary paragraph, no preamble."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class LearningLoopConfig:
    auto_skills_min_tool_calls: int = 5
    auto_skills_enabled: bool = True
    auto_memory_min_turns: int = 3
    auto_memory_enabled: bool = True
    skill_improvement_enabled: bool = True
    context_compression_threshold: int = 30
    context_compression_enabled: bool = True
    model: str = DEFAULT_MODEL


# ---------------------------------------------------------------------------
# LearningLoop
# ---------------------------------------------------------------------------

class LearningLoop:
    """Orchestrates autonomous learning-loop features."""

    def __init__(
        self,
        client,
        config: LearningLoopConfig | None = None,
    ):
        self.client = client
        self.config = config or LearningLoopConfig()
        # Set once a nudge hits a non-recoverable error (bad credentials or model
        # ID). Disables the loop for the rest of the session so we don't retry a
        # known-broken call on every turn.
        self._disabled = False

    # -- nudge helper --------------------------------------------------------

    def _nudge(self, system_prompt: str, user_prompt: str, max_tokens: int = 4096) -> str:
        """Make a lightweight auxiliary call to the model (no tools).

        Returns "" on failure. Fatal config errors (bad credentials or an
        invalid model ID) disable the loop for the rest of the session and are
        reported once, so a misconfiguration doesn't silently turn every
        learning feature into a no-op.
        """
        if self._disabled:
            return ""
        try:
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return "".join(
                block.text for block in response.content
                if hasattr(block, "text")
            )
        except anthropic.APIStatusError as e:
            if e.status_code in (401, 403, 404):
                self._disabled = True
                hint = (
                    f"invalid model ID '{self.config.model}'"
                    if e.status_code == 404
                    else "authentication failed"
                )
                print(
                    f"[hilite] Learning loop disabled for this session: "
                    f"{hint} (API error {e.status_code}).",
                    file=sys.stderr,
                )
            else:
                print(
                    f"[hilite] Learning loop nudge failed "
                    f"(API error {e.status_code}): {e.message}",
                    file=sys.stderr,
                )
            return ""
        except Exception as e:
            print(
                f"[hilite] Learning loop nudge failed ({type(e).__name__}): {e}",
                file=sys.stderr,
            )
            return ""

    # -- context compression -------------------------------------------------

    def maybe_compress_context(
        self,
        messages: list[dict],
    ) -> list[dict] | None:
        """Compress the middle section of messages if over threshold.

        Keeps the first user message and the most recent N messages,
        summarizing everything in between into a single system note.
        """
        if not self.config.context_compression_enabled:
            return None

        threshold = self.config.context_compression_threshold
        if len(messages) <= threshold:
            return None

        # Keep first message + last 10, summarize the middle
        keep_head = 1
        keep_tail = 10
        middle = messages[keep_head : len(messages) - keep_tail]

        if not middle:
            return None

        # Build a text representation of the middle messages
        lines = []
        for msg in middle:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                # tool results or multi-part content
                texts = []
                for block in content:
                    if isinstance(block, dict):
                        bt = block.get("type", "")
                        if bt == "text":
                            texts.append(block.get("text", ""))
                        elif bt == "tool_use":
                            texts.append(f"[tool: {block.get('name', '?')}] {block.get('input', {})}")
                        elif bt == "tool_result":
                            texts.append(f"[result] {block.get('content', '')[:200]}")
                content = " ".join(texts)
            lines.append(f"{role}: {str(content)[:500]}")

        context_text = "\n".join(lines)
        summary = self._nudge(
            system_prompt=CONTEXT_COMPRESSION_NUDGE,
            user_prompt=context_text,
            max_tokens=512,
        )

        if not summary or len(summary) < 20:
            return None

        compressed = (
            messages[:keep_head]
            + [
                {
                    "role": "user",
                    "content": f"[Earlier conversation summarized]\n\n{summary}",
                }
            ]
            + messages[len(messages) - keep_tail :]
        )
        print(
            f"[hilite] Compressed {len(messages)} messages -> {len(compressed)} messages",
            file=sys.stderr,
        )
        return compressed

    # -- autonomous skill creation -------------------------------------------

    def maybe_create_skill(
        self,
        messages: list[dict],
        tool_call_count: int,
    ) -> tuple[str, str, str] | None:
        """Analyze conversation and propose a reusable skill.

        Returns (name, description, full_content) or None.
        """
        if not self.config.auto_skills_enabled:
            return None
        if tool_call_count < self.config.auto_skills_min_tool_calls:
            return None

        # Build conversation text
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                texts = []
                for block in content:
                    if isinstance(block, dict):
                        bt = block.get("type", "")
                        if bt == "text":
                            texts.append(block.get("text", ""))
                        elif bt == "tool_use":
                            texts.append(f"[tool: {block.get('name', '?')}]")
                        elif bt == "tool_result":
                            texts.append(f"[result] {str(block.get('content', ''))[:300]}")
                content = " ".join(texts)
            lines.append(f"{role}: {str(content)[:800]}")

        conversation_text = "\n".join(lines)
        response = self._nudge(
            system_prompt=SKILL_CREATION_NUDGE,
            user_prompt=conversation_text,
            max_tokens=2048,
        )

        if not response or "NO_SKILL" in response.upper():
            return None

        from hilite.skills_auto import parse_skill_from_response

        parsed = parse_skill_from_response(response)
        if parsed is None:
            return None

        return parsed

    # -- memory persistence --------------------------------------------------

    def maybe_persist_memory(
        self,
        messages: list[dict],
        turn_count: int,
    ) -> list[dict] | None:
        """Extract durable facts and return memory entries.

        Returns list of {"section": "user|memory", "content": "..."} or None.
        """
        if not self.config.auto_memory_enabled:
            return None
        if turn_count < self.config.auto_memory_min_turns:
            return None

        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                texts = []
                for block in content:
                    if isinstance(block, dict):
                        bt = block.get("type", "")
                        if bt == "text":
                            texts.append(block.get("text", ""))
                        elif bt == "tool_use":
                            texts.append(f"[tool: {block.get('name', '?')}]")
                        elif bt == "tool_result":
                            texts.append(f"[result] {str(block.get('content', ''))[:200]}")
                content = " ".join(texts)
            lines.append(f"{role}: {str(content)[:600]}")

        conversation_text = "\n".join(lines)
        response = self._nudge(
            system_prompt=MEMORY_EXTRACTION_NUDGE,
            user_prompt=conversation_text,
            max_tokens=1024,
        )

        if not response:
            return None

        # Try to parse JSON response
        try:
            # Find JSON object in response
            start = response.find("{")
            end = response.rfind("}")
            if start == -1 or end == -1:
                return None
            data = json.loads(response[start : end + 1])
            entries = data.get("entries", [])
            if not entries:
                return None
            # Validate entries
            valid = []
            for entry in entries:
                if (
                    isinstance(entry, dict)
                    and "section" in entry
                    and "content" in entry
                    and entry["section"] in ("user", "memory")
                    and entry["content"]
                ):
                    valid.append(entry)
            return valid if valid else None
        except json.JSONDecodeError:
            return None

    # -- skill improvement ---------------------------------------------------

    def maybe_improve_skill(
        self,
        messages: list[dict],
        skill_name: str,
        skill_content: str,
        had_errors: bool,
        hit_max_turns: bool,
    ) -> tuple[str, str, str] | None:
        """Review a skill that caused issues and propose improvements.

        Returns (name, description, full_content) or None.
        """
        if not self.config.skill_improvement_enabled:
            return None
        if not had_errors and not hit_max_turns:
            return None

        issues = []
        if had_errors:
            issues.append("The conversation encountered tool execution errors.")
        if hit_max_turns:
            issues.append("The conversation hit the maximum turn limit.")
        issues_summary = "\n".join(issues)

        nudge = SKILL_IMPROVEMENT_NUDGE.format(
            skill_content=skill_content,
            issues_summary=issues_summary,
        )

        response = self._nudge(
            system_prompt=nudge,
            user_prompt="Please review and improve the skill if needed.",
            max_tokens=2048,
        )

        if not response or "NO_CHANGE" in response.upper():
            return None

        from hilite.skills_auto import parse_skill_from_response

        parsed = parse_skill_from_response(response)
        if parsed is None:
            return None

        return parsed
