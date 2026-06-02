# HiLite Learning Loop — Implementation Plan

## Context

HiLite currently has static skills (markdown files the user creates) and passive memory (the agent can append to USER.md/MEMORY.md if explicitly instructed, but there is no autonomous trigger). The goal is to add a **closed learning loop** — the agent gets better the more you use it by autonomously creating skills from experience, persisting durable memory, improving skills when they fail, and compressing context when conversations get long.

This is inspired by Hermes Agent's learning loop, but scoped to HiLite's minimal philosophy (~200 additional lines, not thousands).

## Goals

1. **Autonomous skill creation**: After complex conversations (≥N tool calls), the agent analyzes the conversation and proposes a reusable skill.
2. **Self-curating memory**: After substantive conversations (≥N turns), the agent extracts durable facts and persists them to USER.md or MEMORY.md.
3. **Skill improvement**: When a skill was used and the conversation had errors or hit max turns, the agent proposes an improved version.
4. **Automatic context compression**: When conversations exceed a message threshold, compress the middle section into a summary.
5. **(Future) Cross-session search**: SQLite + FTS5 replacement for JSON session storage — out of scope for this plan.

## Non-Goals

- Multi-agent swarm (Hermes feature, too complex for HiLite)
- Cron/scheduled tasks (Hermes feature, out of scope)
- Multi-provider support (Hermes has 30+, HiLite stays Anthropic-focused)
- Honcho dialectic user modeling (too complex)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  User sends message → AIAgent.run_conversation()            │
│    ├─ Track: tool_call_count, skills_used, error_count      │
│    ├─ Before each model call: maybe_compress_context()      │
│    └─ After conversation ends:                              │
│         ├─ maybe_create_skill() → ~/.hilite/skills/auto/    │
│         ├─ maybe_persist_memory() → USER.md / MEMORY.md     │
│         └─ maybe_improve_skill() → update skill file        │
└─────────────────────────────────────────────────────────────┘
```

---

## Implementation

### Phase 1: Infrastructure (new files)

#### 1.1 `hilite/learning_loop.py` (new, ~180 lines)

Core orchestrator for all learning-loop features. Uses the existing Anthropic client for auxiliary "nudge" calls (no tools, simple prompts).

```python
class LearningLoopConfig:
    auto_skills_min_tool_calls: int = 5
    auto_skills_enabled: bool = True
    auto_memory_min_turns: int = 3
    auto_memory_enabled: bool = True
    skill_improvement_enabled: bool = True
    context_compression_threshold: int = 30  # messages
    context_compression_enabled: bool = True

class LearningLoop:
    def __init__(self, client, model: str, config: LearningLoopConfig)
    def maybe_create_skill(self, messages, tool_call_count: int) -> str | None
    def maybe_persist_memory(self, messages, turn_count: int) -> list[dict] | None
    def maybe_improve_skill(self, messages, skills_used: list[str], had_errors: bool, hit_max_turns: bool) -> tuple[str, str] | None
    def maybe_compress_context(self, messages: list) -> list | None
```

**Nudge prompts** (included as module-level constants):

- **Skill creation nudge**: "You are a skill curator. Review this conversation and determine if the approach is reusable enough to save as a skill... If NO, respond: NO_SKILL. If YES, respond with full markdown skill including YAML frontmatter..."
- **Memory nudge**: "You are a memory curator. Extract durable facts worth remembering... Respond with JSON: `{"entries": [{"section": "user|memory", "content": "..."}]}`"
- **Skill improvement nudge**: "This skill was used but the conversation had issues. Review and improve... If fine, respond: NO_CHANGE. Otherwise respond with updated skill markdown."
- **Context compression**: "Summarize these conversation turns into a concise paragraph preserving key decisions and findings..."

#### 1.2 `hilite/skills_auto.py` (new, ~80 lines)

Helpers for writing and updating auto-generated skills.

```python
AUTO_SKILLS_DIR = HILITE_HOME / "skills" / "auto"

def write_auto_skill(name: str, content: str) -> str:
    """Write a new auto-generated skill. Validates name is kebab-case.
    Returns the path written or an error message."""

def update_skill(name: str, content: str) -> str:
    """Update an existing skill (any directory in discovery paths).
    Returns the path written or an error message."""

def parse_skill_from_response(response: str) -> tuple[str, str, str] | None:
    """Parse a skill creation nudge response.
    Returns (name, description, full_content) or None if NO_SKILL."""
```

Auto-skills live in `~/.hilite/skills/auto/` and follow the same markdown+YAML frontmatter format as existing skills. They appear in the skills index just like user-created skills.

---

### Phase 2: Agent Integration (modify `hilite/agent/agent.py`)

#### Changes:

1. **Track conversation metadata during the loop**:
   - `self._tool_call_count: int = 0` — increment after each tool execution
   - `self._skills_used: list[str] = []` — append when `skill_view` is called
   - `self._error_count: int = 0` — increment when tool returns "Error:"
   - `self._hit_max_turns: bool = False`

2. **Before each `_call_model()`**: Check message count against compression threshold. If exceeded, call `learning_loop.maybe_compress_context(self.messages)` and replace messages.

3. **After `run_conversation()` returns**: Call learning loop nudges:
   ```python
   if self._tool_call_count >= self.config.auto_skills_min_tool_calls:
       skill = self.learning_loop.maybe_create_skill(self.messages, self._tool_call_count)
       if skill:
           write_auto_skill(skill.name, skill.content)
           print(f"[hilite] Created skill: {skill.name}", file=sys.stderr)
   
   if turn_count >= self.config.auto_memory_min_turns:
       entries = self.learning_loop.maybe_persist_memory(self.messages, turn_count)
       if entries:
           for entry in entries:
               self.memory.append_memory(entry["section"], entry["content"])
           print(f"[hilite] Persisted {len(entries)} memory entries", file=sys.stderr)
   
   if self._skills_used and (self._error_count > 0 or self._hit_max_turns):
       improved = self.learning_loop.maybe_improve_skill(...)
       if improved:
           update_skill(improved.name, improved.content)
           print(f"[hilite] Improved skill: {improved.name}", file=sys.stderr)
   ```

4. **Save metadata to session** at end:
   ```python
   metadata = {
       "tool_call_count": self._tool_call_count,
       "skills_used": self._skills_used,
       "error_count": self._error_count,
       "hit_max_turns": self._hit_max_turns,
   }
   save_session(self.session_id, _serialize_messages(self.messages), metadata)
   ```

---

### Phase 3: New Tools (modify `hilite/tools/registry.py`)

Add two new built-in tools so the agent can create/update skills on demand (not just via autonomous nudges):

#### `skill_create` tool
```yaml
name: skill_create
description: Create a new reusable skill from procedural knowledge.
input_schema:
  name: string (kebab-case)
  description: string (one-line)
  content: string (full markdown body)
```
Handler: `skills_auto.write_auto_skill`

#### `skill_update` tool
```yaml
name: skill_update
description: Update an existing skill with improved content.
input_schema:
  name: string
  content: string (full updated markdown with frontmatter)
```
Handler: `skills_auto.update_skill`

These are distinct from the learning loop nudges — the nudges are **autonomous** (happen after conversation), while the tools are **agent-driven** (happen during conversation if the user asks or the agent decides).

---

### Phase 4: Config & Memory Instructions

#### Modify `hilite/config.py`

Add learning loop defaults to `load_config()`:
```python
config.update({
    "learning_loop": {
        "auto_skills": {"enabled": True, "min_tool_calls": 5},
        "auto_memory": {"enabled": True, "min_turns": 3},
        "skill_improvement": {"enabled": True},
        "context_compression": {"enabled": True, "threshold_messages": 30},
    }
})
```

Users can override in `~/.hilite/config.yaml`:
```yaml
learning_loop:
  auto_skills:
    enabled: true
    min_tool_calls: 5
  auto_memory:
    enabled: true
    min_turns: 3
  context_compression:
    enabled: true
    threshold_messages: 30
```

#### Modify `hilite/memory.py`

Update `MEMORY_MGMT_INSTRUCTIONS` to mention that the agent **also** autonomously persists memory after conversations:
```
### Autonomous memory
In addition to explicit tool calls, HiLite may automatically suggest memory
entries after substantive conversations. These are reviewed before persistence.
```

---

### Phase 5: State Persistence (modify `hilite/state.py`)

The `metadata` parameter already exists in `save_session()` but is unused. We just need to populate it from the agent. No structural changes needed.

Optional enhancement: Add a helper to load metadata:
```python
def load_session_metadata(session_id: str) -> dict | None:
    """Load just the metadata portion of a session."""
```

---

## Files to Modify

| File | Change |
|------|--------|
| `hilite/learning_loop.py` | **New** — Core learning loop logic |
| `hilite/skills_auto.py` | **New** — Auto-skill write/update helpers |
| `hilite/agent/agent.py` | Track metadata, call learning loop nudges, context compression |
| `hilite/tools/registry.py` | Add `skill_create` and `skill_update` tool schemas + handlers |
| `hilite/config.py` | Add learning loop config defaults |
| `hilite/memory.py` | Update instructions to mention autonomous behavior |
| `hilite/state.py` | Populate metadata, optional load_session_metadata helper |

---

## Verification

### Test 1: Autonomous skill creation
```bash
# Run a conversation that triggers 5+ tool calls
python hilite.py --prompt "Create a Python script that reads a CSV, filters rows where age > 30, and writes to a new file. Then run it on a sample CSV."
# After conversation completes, check:
ls ~/.hilite/skills/auto/
# Should contain a new skill like "csv-filter-workflow.md"
```

### Test 2: Self-curating memory
```bash
# Run a conversation where the agent learns something about you
python hilite.py --prompt "I use uv for all Python projects, never pip"
# After conversation, check:
cat ~/.hilite/memories/USER.md
# Should contain an entry about uv preference
```

### Test 3: Context compression
```bash
# Create a long session by asking follow-up questions
for i in {1..20}; do
  python hilite.py --session test-compress --prompt "Tell me fact $i about Python"
done
# Check that messages are compressed when threshold is hit
python -c "import json; d=json.load(open('$HOME/.hilite/sessions/test-compress.json')); print(len(d['messages']))"
```

### Test 4: Skill improvement
```bash
# Use a skill, then cause an error
python hilite.py --skill my-skill "Do something that will fail"
# After max turns or errors, check if skill was updated
diff <(cat ~/.hilite/skills/my-skill.md) <(cat ~/.hilite/skills/auto/my-skill.md 2>/dev/null || echo "no auto version")
```

### Test 5: Manual skill creation via tool
```bash
# Ask the agent to create a skill during conversation
python hilite.py --prompt "Create a skill called 'docker-debug' that documents how to debug Docker containers in this project"
# Verify:
cat ~/.hilite/skills/auto/docker-debug.md
```

---

## Rollback Plan

All changes are additive. If issues arise:
1. Set `learning_loop.auto_skills.enabled: false` in config.yaml to disable skill creation
2. Set `learning_loop.auto_memory.enabled: false` to disable memory nudges
3. Set `learning_loop.context_compression.enabled: false` to disable compression
4. The core agent loop is unchanged — disabling learning loop features returns HiLite to its previous behavior

---

## Future Enhancements (out of scope)

- **Cross-session search (SQLite + FTS5)**: Replace JSON session storage with SQLite. Add `session_search` tool. Enables the agent to search past conversations for solutions.
- **Skill curation**: Auto-archive stale skills, deduplicate similar skills, merge overlapping skills.
- **User model deepening**: Track user preferences over time and weight them in the system prompt.
