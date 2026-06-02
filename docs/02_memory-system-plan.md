
# HiLite Memory & Identity System — Implementation Plan

**Status:** Draft for review  
**Author:** HiLite  
**Date:** 2026-06-01  
**Related:** [[01_hilite-plan]] — original bare-bones plan

---

## Summary

This document specifies the memory and identity subsystem for HiLite. It ports the core concepts from the original Hermes agent (`SOUL.md`, `USER.md`, `MEMORY.md`, project context files, security scanning) into the lightweight HiLite codebase. It also introduces a **memory management prompts layer** — system prompt instructions that teach the agent *when and how* to use memory tools.

**Target:** ~200-250 lines of new code across 3-4 files.

---

## 1. What We Are Adding

### 1.1 SOUL.md — Agent Identity / Persona

**Purpose:** Defines the agent's personality, tone, communication style, and voice. Completely replaces the hardcoded default identity when present.

**Location:** `~/.hilite/SOUL.md`

**Behavior:**
- On first run, seed a default SOUL.md with a friendly template (editable by the user).
- At session start, load SOUL.md if it exists.
- If present, it **replaces** `DEFAULT_SYSTEM_PROMPT` entirely (not appends).
- If absent, fall back to the existing hardcoded default.
- Loaded fresh each session — no restart needed for edits to take effect.

**Default template (seeded):**
```markdown
# HiLite Persona

<!--
This file defines HiLite's personality and tone.
Edit this to customize how HiLite communicates with you.

Examples:
  - "You are a warm, playful assistant who uses kaomoji occasionally."
  - "You are a concise technical expert. No fluff, just facts."
  - "You speak like a friendly coworker who happens to know everything."

This file is loaded fresh each message -- no restart needed.
Delete the contents (or this file) to use the default personality.
-->

You are HiLite, a helpful AI assistant. You have access to tools that let you
read and write files, run shell commands, and execute Python code.
Use tools when they help answer the user's question.
Always think step by step.
```

**System prompt position:** Slot #1 (first content in the system prompt).

---

### 1.2 USER.md — User Profile Memory

**Purpose:** Stores what the agent knows about the **user** — preferences, communication style, expectations, workflow habits, technical background, project context the user cares about.

**Location:** `~/.hilite/memories/USER.md`

**Content format:** `§`-delimited entries (section sign, U+00A7). Each entry is a single declarative fact.

```markdown
# User Profile

§ The user prefers concise answers. They get annoyed by fluff.
§ The user works primarily in Python and Rust.
§ The user uses Neovim with a custom keybinding setup.
§ The user's preferred shell is fish.
```

**System prompt position:** Appended after SOUL.md, labeled as "What you know about the user:".

**Frozen snapshot:** Read once at session start. Not mutated mid-session (preserves upstream prefix cache).

---

### 1.3 MEMORY.md — Agent Notes / Environment Memory

**Purpose:** Stores the agent's personal notes about the **world/environment** — project conventions, tool quirks, things learned, directory structures, build commands, gotchas.

**Location:** `~/.hilite/memories/MEMORY.md`

**Content format:** Same `§`-delimited entries as USER.md.

```markdown
# Environment Notes

§ This project uses `uv` for dependency management, not pip.
§ The test command is `pytest tests/ -x` (stop on first failure).
§ The project root has a `.claude/settings.json` with custom permissions.
```

**System prompt position:** Appended after USER.md, labeled as "Things you have observed:".

**Frozen snapshot:** Same as USER.md — read once at session start.

---

### 1.4 Project Context Files — AGENTS.md, CLAUDE.md, etc.

**Purpose:** Project-level coding conventions and instructions for AI assistants working on a specific codebase.

**Files scanned (in priority order):**
1. `.hermes.md` / `HERMES.md` — walk up to git root, closest wins
2. `AGENTS.md` / `agents.md` — current working directory only
3. `CLAUDE.md` / `claude.md` — current working directory only
4. `.cursorrules` / `.cursor/rules/*.mdc` — current working directory only (optional)

**System prompt position:** After memory blocks, labeled as "Project context:".

**Behavior:**
- Only the highest-priority file found is loaded (not all of them).
- Content is truncated to a reasonable limit (e.g., 10k chars) to avoid bloating the system prompt.
- Security-scanned before inclusion (same as all other files).

---

### 1.5 Security Scanning

**Purpose:** Prevent prompt injection attacks from user-editable files entering the system prompt.

**What gets scanned:**
- SOUL.md content
- USER.md entries
- MEMORY.md entries
- Project context file content

**Scan patterns:**
- XML-style tags that look like system instructions (`<system>`, `</system>`, `<instructions>`, etc.)
- Role override attempts (`ignore previous instructions`, `you are now`, `new role:`)
- Delimiter escapes (`---`, `###`, repeated backticks meant to break out of prompts)

**Action on match:** Replace the suspicious segment with `[BLOCKED: suspicious content]` and log a warning to stderr. Do not refuse to run — sanitize and continue.

---

### 1.6 Memory Management Tool

**Purpose:** Allow the agent to read and append to USER.md and MEMORY.md during a conversation.

**New tool: `memory_manage`**

```json
{
  "name": "memory_manage",
  "description": "Manage persistent memory. Read or append entries to the user profile (USER.md) or environment notes (MEMORY.md). Use this to remember important facts about the user or project for future sessions.",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["read", "append"],
        "description": "Whether to read current entries or append a new one."
      },
      "section": {
        "type": "string",
        "enum": ["user", "memory"],
        "description": "Which memory file to target. 'user' = what you know about the user. 'memory' = things you have observed about the environment."
      },
      "content": {
        "type": "string",
        "description": "For 'append': the declarative fact to save. Write as a single concise sentence starting with a verb. Example: 'The user prefers dark mode in all tools.'"
      }
    },
    "required": ["action", "section"]
  }
}
```

**Tool handler behavior:**
- `read`: Return the current contents of the specified file (from disk, not the frozen snapshot).
- `append`: Append a `§`-prefixed entry to the file, with a timestamp comment. Write to disk immediately. Return confirmation.

**Important:** Appends go to disk but do NOT update the frozen system prompt snapshot mid-session. The new memory appears in the next session.

---

### 1.7 Memory Management Prompts Layer

**Purpose:** Teach the agent *when* and *how* to use the memory tool. This is a dedicated section in the system prompt that instructs the model on memory best practices.

**System prompt position:** After all context files, before the default tool usage instructions.

**Content:**
```
## Memory Management

You have access to a persistent memory system via the `memory_manage` tool.
There are two sections:
- **user**: Facts about the user's preferences, style, background, and habits.
- **memory**: Facts about the environment, project conventions, tools, and things you have learned.

### When to save memory
- The user explicitly asks you to "remember" something.
- You learn a non-obvious fact about the user's preferences (e.g., shell choice, editor, coding style).
- You discover a project convention (e.g., "tests go in tests/", "use uv not pip").
- You make a mistake and learn how to avoid it next time.
- You learn something that would save time in future sessions.

### When NOT to save memory
- Obvious or generic facts (e.g., "Python is a programming language").
- Temporary or session-specific state (e.g., "the user is debugging issue #123").
- Facts that are already in the system prompt or project context files.

### How to write entries
- Write each entry as a single concise declarative sentence.
- Start with a verb when describing preferences or conventions.
- Be specific. "The user uses Neovim" is better than "The user likes editors."
- Do not include the current date unless the fact is time-sensitive.

### Memory hygiene
- If you learn that a previous memory is wrong, append a correction rather than deleting.
- Periodically review memories (use `memory_manage` with action="read") to avoid stale or redundant entries.
```

---

## 2. System Prompt Assembly Order

The final system prompt is assembled in this order:

```
1. SOUL.md content (or DEFAULT_SYSTEM_PROMPT fallback)
2. [User profile block] — header + USER.md frozen snapshot
3. [Environment notes block] — header + MEMORY.md frozen snapshot
4. [Project context block] — header + highest-priority context file found
5. [Memory management instructions] — the prompts layer (§1.7)
6. [Default tool usage instructions] — existing instructions from agent.py
```

**Caching strategy:** The entire prefix (items 1-5) is stable across messages in a session. Only item 6 (if we add dynamic parts later) or the user messages vary. This maximizes prompt cache hit rate.

---

## 3. File Changes

### New Files

| File | Purpose | ~Lines |
|------|---------|--------|
| `hilite/memory.py` | MemoryStore class: load/save SOUL.md, USER.md, MEMORY.md; security scanning; snapshot building | ~120 |
| `hilite/context.py` | Project context file discovery and loading | ~40 |
| `hilite/tools/memory.py` | `memory_manage` tool handler (read/append) | ~30 |

### Modified Files

| File | Changes |
|------|---------|
| `hilite/constants.py` | Add `MEMORIES_DIR`, `SOUL_PATH`, `MAX_CONTEXT_FILE_SIZE` |
| `hilite/agent/agent.py` | Integrate MemoryStore into system prompt assembly; pass memory tool schemas |
| `hilite/tools/registry.py` | Register `memory_manage` tool |
| `hilite/config.py` | Add `_ensure_default_soul_md()` to seed default SOUL.md on first run |
| `hilite.py` | No changes needed (all internal) |

---

## 4. Data Flow

```
Session Start
    │
    ▼
┌─────────────────┐
│ Load SOUL.md    │ ──► Use if exists, else DEFAULT_SYSTEM_PROMPT
│ (or seed default)│
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Load USER.md    │ ──► Read §-entries, build "user" block
│ Load MEMORY.md  │ ──► Read §-entries, build "memory" block
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Scan project    │ ──► Walk cwd → git root for .hermes.md, etc.
│ context files   │
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Security scan   │ ──► Sanitize all content, block injections
│ all content     │
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Assemble system │ ──► Order: soul → user → memory → context →
│ prompt          │      memory-mgmt-instructions → tool-usage
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ Frozen snapshot │ ──► Stored in AIAgent, never mutated mid-session
│ (prefix cache)  │
└─────────────────┘
    │
    ▼
  [API calls...]
    │
    ▼
┌─────────────────┐
│ memory_manage   │ ──► Appends to disk (USER.md or MEMORY.md)
│ tool calls      │     Does NOT update frozen snapshot
└─────────────────┘
    │
    ▼
  [Next session]  ──► New load reads the appended entries
```

---

## 5. Edge Cases & Decisions

| Question | Decision |
|----------|----------|
| What if SOUL.md is empty? | Treat as absent — fall back to DEFAULT_SYSTEM_PROMPT. |
| What if USER.md/MEMORY.md don't exist? | Create empty files on first read. Empty = no block added to system prompt. |
| What if a memory entry is very long? | Truncate individual entries to 500 chars. Warn on stderr. |
| What if the total memory section is huge? | Cap the combined user+memory block at 8k chars. Truncate oldest entries first. |
| What if the user edits a file mid-session? | Changes take effect in the **next** session. The frozen snapshot is immutable. |
| Should memoryManage be auto-called? | No — the agent decides when to call it based on the memory management prompts layer. |
| What about deduplication? | Phase 2 — not in initial implementation. For now, append-only. |
| What about deleting/correcting entries? | Phase 2. For now, the agent can append a correction entry. |

---

## 6. Testing Checklist

- [ ] First run seeds default SOUL.md in `~/.hilite/`
- [ ] SOUL.md content replaces default system prompt
- [ ] Empty SOUL.md falls back to default
- [ ] USER.md and MEMORY.md entries appear in system prompt
- [ ] Project context file (.hermes.md) is loaded when present
- [ ] Security scanner blocks `<system>` injection attempts
- [ ] Security scanner blocks role-override attempts
- [ ] `memory_manage` tool appends to disk correctly
- [ ] `memory_manage` tool reads current disk contents
- [ ] Appended entries appear in next session's system prompt
- [ ] Frozen snapshot is not mutated mid-session
- [ ] System prompt assembly order is correct

---

## 7. Future Enhancements (Not in this plan)

- **Deduplication / compaction** — Periodic merge of redundant entries
- **Entry deletion / editing** — Allow the agent to remove or update specific entries
- **Semantic search** — Vector-based retrieval instead of dumping all entries
- **Per-project memory** — Separate USER.md/MEMORY.md per git repo
- **Auto-memory** — Proactive memory extraction without explicit tool calls
- **Memory review prompts** — Periodic "review your memories for accuracy" nudges
