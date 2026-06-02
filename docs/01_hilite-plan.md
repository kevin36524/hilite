# HiLite Plan

> Date: 2026-06-01
>
> Constraints: Mac only, Claude Code CLI (`claude -p`) only, local only.

---

## 1. What is HiLite?

**HiLite** (formerly "hermes-lite") is a bare-bones AI agent that:
- Runs on **macOS only**
- Calls the **Anthropic API directly** using Claude Code credentials (subscription-friendly)
- Runs **locally** with no messaging, no gateway, no web dashboard
- Implements its own **tool-calling loop** (file ops, shell, Python)
- Is the **agentic harness** itself — not a wrapper around `claude -p`

**Key insight:** HiLite does NOT wrap `claude -p`. Instead, it reads Claude Code's saved OAuth credentials and calls the Anthropic API directly. This gives us full native tool-calling while supporting subscription users.

---

## 2. The Three Architectures Compared

### 2.1 Original Hermes (`cli.py`)

```
You run "hermes"
    → cli.py (15K lines, custom TUI)
        → calls Anthropic API via anthropic_adapter.py
        → handles tool loop internally
        → runs Hermes tools
```

- **Agentic harness:** Hermes itself
- **Auth:** API key or Claude Code creds
- **Input:** Custom TUI (prompt_toolkit)

### 2.2 Claude Code CLI (`claude -p`)

```
You run "echo 'prompt' | claude -p"
    → claude binary handles EVERYTHING
        → calls Anthropic internally
        → handles tool loop internally
        → runs Claude's built-in tools
```

- **Agentic harness:** Claude Code CLI itself
- **Auth:** Claude Code OAuth (built-in)
- **Input:** Piped stdin
- **Your code's role:** NONE — Claude does everything

### 2.3 HiLite (our approach)

```
You run "hilite"
    → hilite.py (~100 lines, simple script)
        → reads your input
        → calls Anthropic API via anthropic_adapter.py
        → handles tool loop internally
        → runs HiLite tools
```

- **Agentic harness:** **HiLite itself** (YOUR code)
- **Auth:** Claude Code OAuth creds (via adapter) OR API key
- **Input:** Simple stdin loop

| Question                   | `cli.py`                    | `claude -p`           | **HiLite**             |
| -------------------------- | --------------------------- | --------------------- | ---------------------- |
| What do I run?             | `hermes`                    | `claude -p`           | `hilite`               |
| Who handles input?         | `cli.py` (fancy TUI)        | `claude` binary       | Simple `input()` loop  |
| Who calls the LLM?         | `anthropic_adapter.py`      | `claude` internally   | `anthropic_adapter.py` |
| Who handles the tool loop? | `conversation_loop.py`      | `claude` internally   | Simplified loop        |
| Who runs the tools?        | Hermes tools                | Claude built-in tools | HiLite tools           |
| Auth method                | API key / Claude Code creds | Claude Code OAuth     | **Claude Code OAuth**  |
| Can customize tools?       | Yes                         | No (fixed set)        | Yes                    |

---

## 3. How Auth Works (The Magic)

The Anthropic API is stateless and doesn't care HOW you authenticate — it accepts:
1. `x-api-key: sk-ant-api...` (API keys)
2. `Authorization: Bearer <jwt>` (OAuth tokens from Claude Code login)

HiLite reads the OAuth token from Claude Code's credential storage:

```
┌──────────────────────────────────────────────────────────────┐
│  macOS Keychain                                              │
│  └── "Claude Code-credentials"                               │
│       └── { claudeAiOauth: { accessToken: "..." } }          │
│            ↑                                                 │
│            │ security find-generic-password                  │
│            ▼                                                 │
│  ┌─────────────────┐     ┌──────────────────────────┐       │
│  │  anthropic.py   │────▶│  anthropic.Anthropic(    │       │
│  │  (adapter)      │     │    auth_token=token      │       │
│  └─────────────────┘     │  )                       │       │
│                          └──────────────────────────┘       │
└──────────────────────────────────────────────────────────────┘
```

This means: **if you've ever logged into Claude Code, HiLite works with your subscription — no API key needed.**

---

## 4. Constraints Recap

| Constraint | Implication |
|------------|-------------|
| **Mac only** | Drop Windows, Linux-specific, Docker, Termux, Nix packaging |
| **Anthropic only** | Drop OpenRouter, OpenAI, Gemini, Bedrock, Azure, etc. |
| **Local only** | Drop WhatsApp, Telegram, Discord, Slack, gateway, all messaging |
| **Bare bones** | Strip everything non-essential |

---

## 5. Architecture Decision: Fork vs. Extract

| Approach | Pros | Cons |
|----------|------|------|
| **A. Fork & delete** from existing repo | Keeps git history, easier to trace origins | Leftover cruft, hard to fully detangle |
| **B. Clean-room extract** | Truly minimal, no baggage, easier to understand | More upfront work, lose history |

**Recommendation: B (Clean-room extract)** — Build a minimal `hilite/` directory from scratch, copying only the specific functions/classes needed from the original. The original codebase is so large and entangled that surgical deletion is harder than selective extraction.

---

## 6. What to Keep (The "Core 15")

These are the absolutely essential pieces from the original `hermes-agent` codebase:

| # | Original File | New Location | Purpose | Est. Lines (orig) |
|---|---------------|--------------|---------|-------------------|
| 1 | `run_agent.py` | `agent/agent.py` | `AIAgent` class — heart of the system | ~4,800 |
| 2 | `agent/conversation_loop.py` | `agent/conversation_loop.py` | Main turn loop (model → tools → repeat) | ~4,750 |
| 3 | `agent/agent_init.py` | `agent/agent.py` (merged) | Agent init | ~1,400 |
| 4 | `agent/chat_completion_helpers.py` | `agent/chat.py` | API dispatch, streaming, retries | ~? |
| 5 | `agent/anthropic_adapter.py` | `agent/anthropic.py` | **Only** provider adapter + auth | ~? |
| 6 | `agent/prompt_builder.py` | `agent/prompt.py` | System prompt assembly | ~? |
| 7 | `agent/memory_manager.py` | `agent/memory.py` | Memory orchestration | ~? |
| 8 | `model_tools.py` | `tools/orchestrator.py` | Tool discovery layer | ~? |
| 9 | `tools/registry.py` | `tools/registry.py` | Tool schema registry & dispatch | ~? |
| 10 | `hermes_constants.py` | `constants.py` | `HERMES_HOME`, path helpers | ~? |
| 11 | `hermes_state.py` | `state.py` | SQLite session storage | ~? |
| 12 | `hermes_logging.py` | `logging_config.py` | Logging setup | ~? |
| 13 | `utils.py` | `utils.py` | Atomic writes, JSON helpers | ~? |
| 14 | `tools/file_tools.py` | `tools/file.py` | Read/write files | ~? |
| 15 | `tools/code_execution_tool.py` | `tools/shell.py` | Shell/Python execution | ~? |

**Total original footprint:** ~15,000+ lines across ~430 files.

**Target footprint:** ~600-800 lines in ~10-15 files.

---

## 7. What to Remove (Major Chunks)

### Entire Directories (Safe to Delete)

| Directory | Original Size | Why Remove |
|-----------|--------------|------------|
| `gateway/` | ~60 files, 20K lines | No messaging platforms |
| `web/` | Entire directory | No dashboard |
| `ui-tui/` | Entire directory | No TUI frontend |
| `tui_gateway/` | Entire directory | No TUI gateway |
| `acp_adapter/` | Entire directory | No editor integration |
| `acp_registry/` | Entire directory | No ACP registry |
| `docker/` | Entire directory | No Docker |
| `nix/` | Entire directory | No Nix |
| `packaging/` | Entire directory | No packaging |
| `cron/` | Entire directory | No scheduling |
| `optional-skills/` | Entire directory | Not needed |
| `optional-mcps/` | Entire directory | Not needed |
| `plugins/` | Entire directory | Plugin system not needed |
| `apps/` | Entire directory | No desktop app |
| `docs/` | Entire directory | Separate docs site |
| `website/` | Entire directory | Separate website |
| `.github/` | Entire directory | No CI workflows |
| `tests/` | Entire directory | Can add back later |

### Individual Files to Remove

| File(s) | Why Remove |
|---------|------------|
| `cli.py` (full 15K lines) | `claude -p` replaces the TUI REPL |
| `batch_runner.py` | No batch trajectory generation |
| `trajectory_compressor.py` | No RL trajectory compression |
| `setup-hermes.sh` | No install script (Mac-only, simple pip install) |
| `scripts/install.ps1` | Windows installer |
| `hermes_bootstrap.py` | Windows UTF-8 fix — no-op on Mac |
| `hermes_cli/gateway_windows.py` | Windows service integration |
| `constraints-termux.txt` | Android/Termux constraints |

### Provider Adapters (Keep Only Anthropic)

| Adapter | Keep? |
|---------|-------|
| `agent/anthropic_adapter.py` | **YES** — Only one we need |
| `agent/bedrock_adapter.py` | No — AWS Bedrock |
| `agent/gemini_native_adapter.py` | No — Google Gemini |
| `agent/gemini_cloudcode_adapter.py` | No — Google Cloud Code |
| `agent/codex_responses_adapter.py` | No — OpenAI Codex |
| `agent/azure_identity_adapter.py` | No — Azure |
| `agent/copilot_acp_client.py` | No — GitHub Copilot |
| `agent/browser_provider.py` | No — Browser use provider |
| `agent/lmstudio_reasoning.py` | No — LM Studio |
| `agent/plugin_llm.py` | No — Plugin LLM wrapper |

### Tools to Remove

| Tool File(s) | Why Remove |
|--------------|------------|
| `tools/browser_*.py` (~6 files) | Heavy, optional |
| `tools/image_generation_tool.py` | Optional |
| `tools/computer_use_tool.py` + `tools/computer_use/` | Optional, heavy deps |
| `tools/mcp_tool.py`, `tools/mcp_*.py` | Can add back later |
| `tools/cronjob_tools.py` | No scheduling |
| `tools/discord_tool.py` | No messaging |
| `tools/send_message_tool.py` | No messaging |
| `tools/homeassistant_tool.py` | No smart home |
| `tools/delegate_tool.py` | No subagent spawning |
| `tools/mixture_of_agents_tool.py` | No MoA |
| `tools/skill_manager_tool.py` | Simpler skill system |
| `tools/session_search_tool.py` | Optional search |
| `tools/memory_tool.py` | Built into memory.py |
| `tools/clarify_tool.py`, `tools/clarify_gateway.py` | Optional |
| `tools/neutts_*.py` | No TTS |
| `tools/fal_common.py` | No image gen |
| `tools/feishu_*.py` | No Feishu |
| `tools/microsoft_graph_*.py` | No MS Graph |
| `tools/openrouter_client.py` | No OpenRouter |

### Skills (Keep Minimal)

The `skills/` directory has ~43 files across 20+ categories. Keep at most 2-3 essential ones:

- `software-development/` — if any are genuinely useful
- `github/` — if GitHub integration is desired
- Everything else: `autonomous-ai-agents/`, `creative/`, `data-science/`, `devops/`, `diagramming/`, `dogfood/`, `domain/`, `email/`, `gaming/`, `gifs/`, `index-cache/`, `inference-sh/`, `mcp/`, `media/`, `mlops/`, `note-taking/`, `productivity/`, `red-teaming/`, `research/`, `smart-home/`, `social-media/`, `yuanbao/` — all removable.

---

## 8. Proposed Directory Structure

```
hilite/
├── hilite.py                   # Entry point (~100 lines)
│   # Reads user input, creates AIAgent, runs conversation loop
│
├── agent/
│   ├── __init__.py
│   ├── agent.py                # Core AIAgent (stripped from run_agent.py)
│   ├── conversation_loop.py    # Turn loop (stripped)
│   ├── anthropic.py            # Anthropic-only adapter + auth
│   ├── prompt.py               # System prompt assembly (simplified)
│   ├── memory.py               # Minimal memory
│   └── display.py              # Simple output formatting
│
├── tools/
│   ├── __init__.py
│   ├── registry.py             # Tool schema registry (simplified)
│   ├── file.py                 # Read/write/list files
│   ├── shell.py                # Execute shell commands
│   └── python.py               # Execute Python code
│
├── skills/                     # Optional — 2-3 essentials
│   └── (empty or minimal)
│
├── utils.py                    # Helpers (stripped)
├── config.py                   # Minimal config (env vars + argparse)
├── constants.py                # Paths, constants
├── state.py                    # Minimal state (json or sqlite)
├── logging_config.py           # Simple logging setup
│
├── pyproject.toml              # Minimal deps
└── README.md
```

---

## 9. State Management — What HiLite is Responsible For

**Critical:** The Anthropic API is **completely stateless**. It does not remember anything between calls. HiLite must manage ALL state.

```
┌─────────────────────────────────────────────────────────────────┐
│  ANTHROPIC API (stateless)                                      │
│  ├── Receives: full message history + current prompt            │
│  ├── Returns: response (text or tool_use blocks)                │
│  └── Remembers: NOTHING between calls                           │
└─────────────────────────────────────────────────────────────────┘
                              │
                              │ HTTP request
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  HILITE (keeps all state)                                       │
│  ├── Conversation history (messages array)                      │
│  ├── Persistent memory (json/sqlite across sessions)            │
│  ├── Tool schemas (what tools are available)                    │
│  ├── Session metadata (session ID, timestamps)                  │
│  └── Config (model choice, system prompt, etc.)                 │
└─────────────────────────────────────────────────────────────────┘
```

### State Comparison

| State | Original Hermes | HiLite (suggested) |
|-------|----------------|-------------------|
| **Conversation history** | SQLite (`hermes_state.py`) | In-memory list or JSON file |
| **Persistent memory** | SQLite + FTS5 + Honcho | Skip v1, or simple JSON |
| **Skills** | Auto-create from experience | Skip v1 |
| **Context compression** | LLM-based compression | Simple truncation or turn limit |
| **Session metadata** | Full session tracking | Minimal (timestamp, maybe title) |
| **User preferences** | Complex user profiles | Skip v1 |

---

## 10. Additional Things That Can Be Trimmed

Beyond the major chunks above, these individual subsystems can be simplified or removed:

### 10.1 Authentication & Credentials

| Original | What to Do |
|----------|-----------|
| `agent/credential_pool.py` | Remove — single provider, single key |
| `agent/credential_sources.py` | Remove — single provider, single key |
| `agent/credential_persistence.py` | Remove — single provider, single key |
| `hermes_cli/auth.py`, `auth_commands.py` | Remove — no OAuth, no portal login |
| `hermes_cli/azure_detect.py` | Remove — no Azure |
| `hermes_cli/copilot_auth.py` | Remove — no Copilot |
| `hermes_cli/dingtalk_auth.py` | Remove — no DingTalk |
| `hermes_cli/portal_*.py` | Remove — no Nous Portal |

**Replacement:** Just read Claude Code credentials from keychain/file, or fall back to `ANTHROPIC_API_KEY` from env.

### 10.2 Error Handling & Retries

| Original | What to Do |
|----------|-----------|
| `agent/error_classifier.py` | Simplify — complex classification across 10+ providers |
| `agent/retry_utils.py` | Keep basic tenacity retry |
| `agent/rate_limit_tracker.py` | Remove — single provider, no rate limit pooling |

**Replacement:** Basic HTTP retry with exponential backoff.

### 10.3 Model Metadata

| Original | What to Do |
|----------|-----------|
| `agent/model_metadata.py` | Simplify — tracks 200+ models |

**Replacement:** Hardcode context lengths for Claude models.

### 10.4 Context Management

| Original | What to Do |
|----------|-----------|
| `agent/context_compressor.py` | Simplify — LLM-based compression |
| `agent/conversation_compression.py` | Simplify |
| `agent/context_engine.py` | Remove — context file scanning |
| `agent/context_references.py` | Remove |
| `agent/manual_compression_feedback.py` | Remove |

**Replacement:** Simple truncation or turn-count-based dropping.

### 10.5 Message Sanitization

| Original | What to Do |
|----------|-----------|
| `agent/message_sanitization.py` | Simplify — elaborate surrogate/non-ASCII for multiple providers |

**Replacement:** Minimal UTF-8 validation for Anthropic only.

### 10.6 Usage & Analytics

| Original | What to Do |
|----------|-----------|
| `agent/usage_pricing.py` | Remove — cost estimation |
| `agent/account_usage.py` | Remove — usage tracking |
| `agent/insights.py` | Remove — analytics |
| `agent/nous_rate_guard.py` | Remove — Nous-specific |
| `agent/portal_tags.py` | Remove — Nous-specific |

### 10.7 Background & Automation

| Original | What to Do |
|----------|-----------|
| `agent/background_review.py` | Remove — auto skill-improvement |
| `agent/curator.py`, `curator_backup.py` | Remove — skill curation |
| `agent/onboarding.py` | Remove — first-run wizard |

### 10.8 Advanced Features

| Original | What to Do |
|----------|-----------|
| `agent/iteration_budget.py` | Simplify — just a counter |
| `agent/tool_guardrails.py` | Simplify or remove — approval system |
| `agent/subdirectory_hints.py` | Remove — advanced feature |
| `agent/think_scrubber.py` | Remove — for reasoning models (optional) |
| `agent/lsp/` | Remove — LSP integration |
| `agent/display.py` | Simplify — Kawaii spinners → plain text |
| `agent/redact.py` | Remove — PII scrubbing (optional) |
| `agent/file_safety.py` | Simplify — path security |
| `agent/i18n.py` | Remove — English only |
| `agent/jiter_preload.py` | Remove — performance optimization |
| `agent/models_dev.py` | Remove — dev model list |
| `agent/moonshot_schema.py` | Remove — Moonshot-specific |
| `agent/gemini_schema.py` | Remove — Gemini-specific |

### 10.9 CLI Framework (hermes_cli/)

The original `hermes_cli/` has ~119 files. For bare-bones, most can go:

| Original | What to Do |
|----------|-----------|
| `hermes_cli/commands.py` | Simplify — remove gateway, cron, model switching |
| `hermes_cli/gateway.py` | Remove — no gateway |
| `hermes_cli/cron.py` | Remove — no cron |
| `hermes_cli/model_switch.py` | Remove — single model |
| `hermes_cli/browser_connect.py` | Remove — no browser |
| `hermes_cli/dashboard_auth/` | Remove — no dashboard |
| `hermes_cli/checkpoints.py` | Remove — no checkpoint system |
| `hermes_cli/backup.py` | Remove — no backup system |
| `hermes_cli/doctor.py` | Remove — diagnostic tool |
| `hermes_cli/dump.py` | Remove — state dump |
| `hermes_cli/claw.py` | Remove — OpenClaw migration |
| `hermes_cli/bundles.py` | Remove — bundle management |
| `hermes_cli/completion.py` | Remove — shell completion |
| `hermes_cli/curses_ui.py` | Remove — curses UI |
| `hermes_cli/debug.py` | Remove — debug tools |
| `hermes_cli/fallback_cmd.py` | Remove — fallback commands |
| `hermes_cli/fallback_config.py` | Simplify |
| `hermes_cli/banner.py` | Remove — ASCII art banner |
| `hermes_cli/colors.py` | Remove or simplify |
| `hermes_cli/build_info.py` | Remove |
| `hermes_cli/codex_models.py` | Remove — Codex-specific |
| `hermes_cli/codex_runtime_*.py` | Remove — Codex runtime |
| `hermes_cli/container_boot.py` | Remove — container support |

**Replacement:** A minimal `argparse` CLI in `hilite.py` or `config.py`.

### 10.10 Other Files

| Original | What to Do |
|----------|-----------|
| `mini_swe_runner.py` | Remove — SWE benchmark runner |
| `mcp_serve.py` | Remove — MCP server |
| `toolset_distributions.py` | Simplify — toolset definitions |
| `hermes_time.py` | Simplify — timezone handling |
| `hermes-already-has-routines.md` | Remove — docs |
| `AGENTS.md` | Remove — docs |
| `flake.nix`, `flake.lock` | Remove — Nix |
| `Dockerfile`, `.dockerignore` | Remove — Docker |
| `docker-compose.yml`, `docker-compose.windows.yml` | Remove — Docker |
| `.hadolint.yaml` | Remove — Docker linting |
| `setup.py` | Remove — use pyproject.toml only |
| `MANIFEST.in` | Remove — use pyproject.toml only |
| `package.json`, `package-lock.json` | Remove — Node.js deps |

---

## 11. Dependency Simplification

### Minimal `pyproject.toml` for HiLite

```toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "hilite"
version = "0.1.0"
description = "Bare-bones agent for Claude Code CLI users"
requires-python = ">=3.11"
dependencies = [
    "anthropic>=0.40.0",      # Anthropic SDK
    "rich>=13.0.0",            # Terminal formatting (optional)
    "pyyaml>=6.0",             # Config file parsing
]
```

**Removed deps and why:**
- `openai` — Not needed (using Anthropic SDK directly)
- `prompt_toolkit` — Not needed (no interactive TUI)
- `croniter` — No cron scheduling
- `PyJWT` — No GitHub App auth
- `psutil` — No process management (gateway)
- `fastapi`, `uvicorn` — No web server
- `ptyprocess` — No pseudo-terminal
- `python-telegram-bot`, `discord.py`, `slack-bolt` — No messaging
- `aiohttp` — Not needed
- `tenacity` — Optional (can implement simple retry)
- `jinja2` — Optional (can use f-strings)
- `pydantic` — Optional (can use dataclasses)
- `requests` — Optional (Anthropic SDK handles HTTP)
- `fire` — Optional (can use argparse)
- `ruamel.yaml` — Use `pyyaml` instead

---

## 12. How It Works (Simplified Flow)

```
┌─────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│ You run │────▶│  hilite.py          │────▶│  AIAgent            │
│ hilite  │     │  (thin entry point) │     │  (core agent class) │
└─────────┘     └─────────────────────┘     └─────────────────────┘
                                                    │
                          ┌─────────────────────────┼─────────────────────────┐
                          ▼                         ▼                         ▼
                    ┌──────────┐           ┌──────────────┐           ┌──────────────┐
                    │ Anthropic│           │  Tools       │           │  State       │
                    │  Adapter │           │ (file,       │           │ (messages,   │
                    │(reads     │           │  shell,      │           │  memory)     │
                    │ creds)   │           │  python)     │           │              │
                    └──────────┘           └──────────────┘           └──────────────┘
                          │                       │
                          └───────────────────────┘
                                    │
                                    ▼
                          ┌─────────────────────┐
                          │  Response to user   │
                          │  via stdout         │
                          └─────────────────────┘
```

### Entry Point (`hilite.py`)

```python
#!/usr/bin/env python3
"""HiLite — Bare-bones agent for Claude Code users."""

import sys
from agent.agent import AIAgent


def main():
    agent = AIAgent(model="claude-sonnet-4-20250514")

    # Read user prompt from stdin
    user_message = sys.stdin.read().strip() or "Hello!"

    response = agent.run_conversation(user_message)
    print(response)


if __name__ == "__main__":
    main()
```

---

## 13. Implementation Phases

### Phase 1: Skeleton (~2 hours)
- Create `hilite.py` entry point
- Minimal `AIAgent` class with Anthropic client (with auth)
- Hardcoded tool schema (file, shell, python)
- Single-turn conversation loop
- **Deliverable:** Can run `echo "hello" | python hilite.py` and get a response

### Phase 2: Tools (~2 hours)
- Port file tools (read, write, list, search)
- Port shell tool (execute_command)
- Port Python tool (execute_python)
- **Deliverable:** Agent can read files and run shell commands

### Phase 3: Conversation Loop (~3 hours)
- Multi-turn loop with tool calling
- Message history management
- Basic error handling
- **Deliverable:** Full tool-use loop (ask → think → tool → result → respond)

### Phase 4: Memory & Polish (~2 hours)
- JSON/SQLite conversation persistence
- System prompt customization
- Config file support
- **Deliverable:** Conversations persist across runs

**Estimated total: ~600-800 lines of Python** (vs. ~45,000+ in the original).

---

## 14. Key Design Decisions

### 14.1 SDK Choice: Anthropic SDK Directly

Use the Anthropic SDK directly (not OpenAI SDK with adapter). We're Anthropic-only and want full Claude feature support (thinking blocks, extended thinking, etc.).

### 14.2 Async vs. Sync

**Sync.** `hilite` is inherently synchronous. Use the Anthropic SDK's sync client. Simpler code, no `asyncio` complexity.

### 14.3 State Persistence

**Start with JSON files**, upgrade to SQLite later if needed. For a single-user local tool, JSON is simpler and perfectly adequate.

### 14.4 Tool Schema Format

Use Anthropic's native tool format directly. Simpler, no translation layer from OpenAI function-calling schema.

---

## 15. Files to Potentially Port (Detailed)

If doing a selective extraction, these are the specific functions/classes to pull from each original file:

### From `run_agent.py`
- `class AIAgent` — Core class (strip __init__ to essentials)
- `AIAgent.run_conversation()` — Entry point
- `AIAgent._create_anthropic_client()` — Client construction with auth
- `AIAgent._build_system_prompt()` — System prompt assembly
- Tool execution dispatch logic

### From `agent/conversation_loop.py`
- `run_conversation()` — Main loop body
- Tool call parsing and dispatch
- Message history management
- Interrupt handling (simplify)

### From `agent/anthropic_adapter.py`
- `read_claude_code_credentials()` — Read OAuth from keychain/file
- `build_anthropic_client()` — Client setup with auth
- `prepare_anthropic_messages()` — Message format conversion
- `call_anthropic()` — API call with tool support
- Thinking block handling

### From `agent/prompt_builder.py`
- `build_system_prompt()` — System prompt template
- Tool description formatting

### From `tools/registry.py`
- `ToolRegistry` class — Schema registration
- `get_tool_schemas()` — Return schemas for API
- `execute_tool()` — Dispatch to handler

### From `tools/file_tools.py`
- `read_file()` — Read file contents
- `write_file()` — Write file contents
- `list_directory()` — List files
- `search_files()` — Search with ripgrep (optional)

### From `tools/code_execution_tool.py`
- `execute_shell_command()` — Run shell commands
- `execute_python_code()` — Run Python code

---

## 16. Summary

| Metric | Original Hermes | HiLite (Target) |
|--------|----------------|----------------|
| Total Python files | ~430 | ~10-15 |
| Total lines of code | ~45,000+ | ~600-800 |
| Dependencies | ~30+ core + 30+ extras | ~3-5 |
| Entry points | CLI, Gateway, ACP, Batch | Single CLI |
| Providers | 15+ | 1 (Anthropic) |
| Platforms | 15+ messaging | None (local only) |
| Tools | 40+ | 3-5 |
| Skills | 43 files, 20+ categories | 0-3 |
| OS Support | Mac, Linux, Windows, Termux, Docker | Mac only |
| Auth | API key / Claude Code creds | **Claude Code OAuth** |
| Install method | curl shell script | `pip install` |

---

*Generated 2026-06-01. This plan is a living document — update as implementation progresses.*
