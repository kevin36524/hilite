# Hermes Lite: Bare-Bones Plan

> Date: 2026-06-01
>
> Constraints: Mac only, Claude Code CLI (`claude -p`) only, local only.

---

## 1. Constraints Recap

| Constraint | Implication |
|------------|-------------|
| **Mac only** | Drop Windows, Linux-specific, Docker, Termux, Nix packaging |
| **Claude Code CLI (`claude -p`) only** | Drop OpenRouter, OpenAI, Gemini, Bedrock, Azure, Mistral, etc. |
| **Local only** | Drop WhatsApp, Telegram, Discord, Slack, gateway, all messaging |
| **Bare bones** | Strip everything non-essential |

---

## 2. Architecture Decision: Fork vs. Extract

| Approach | Pros | Cons |
|----------|------|------|
| **A. Fork & delete** from existing repo | Keeps git history, easier to trace origins | Leftover cruft, hard to fully detangle |
| **B. Clean-room extract** | Truly minimal, no baggage, easier to understand | More upfront work, lose history |

**Recommendation: B (Clean-room extract)** — Build a minimal `hermes-lite/` directory from scratch, copying only the specific functions/classes needed from the original. The original codebase is so large and entangled that surgical deletion is harder than selective extraction.

---

## 3. What to Keep (The "Core 15")

These are the absolutely essential pieces from the original `hermes-agent` codebase:

| # | Original File | New Location | Purpose | Est. Lines (orig) |
|---|---------------|--------------|---------|-------------------|
| 1 | `run_agent.py` | `agent/agent.py` | `AIAgent` class — heart of the system | ~4,800 |
| 2 | `agent/conversation_loop.py` | `agent/conversation_loop.py` | Main turn loop (model → tools → repeat) | ~4,750 |
| 3 | `agent/agent_init.py` | `agent/agent.py` (merged) | Agent initialization | ~1,400 |
| 4 | `agent/chat_completion_helpers.py` | `agent/chat.py` | API dispatch, streaming, retries | ~? |
| 5 | `agent/anthropic_adapter.py` | `agent/anthropic.py` | **Only** provider adapter | ~? |
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

**Total original footprint of these files:** ~15,000+ lines across ~430 files.

**Target footprint:** ~600-800 lines in ~10-15 files.

---

## 4. What to Remove (Major Chunks)

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

## 5. Proposed Directory Structure

```
hermes-lite/
├── hermes_lite.py              # Entry point (~100 lines)
│   # Reads ANTHROPIC_API_KEY, creates AIAgent, runs conversation
│
├── agent/
│   ├── __init__.py
│   ├── agent.py                # Core AIAgent (stripped from run_agent.py)
│   ├── conversation_loop.py    # Turn loop (stripped)
│   ├── anthropic.py            # Anthropic-only adapter
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
├── state.py                    # Minimal state (optional sqlite)
├── logging_config.py           # Simple logging setup
│
├── pyproject.toml              # Minimal deps
└── README.md
```

---

## 6. Additional Things That Can Be Trimmed

Beyond the major chunks above, these individual subsystems can be simplified or removed:

### 6.1 Authentication & Credentials

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

**Replacement:** Just read `ANTHROPIC_API_KEY` from environment. That's it.

### 6.2 Error Handling & Retries

| Original | What to Do |
|----------|-----------|
| `agent/error_classifier.py` | Simplify — complex classification across 10+ providers |
| `agent/retry_utils.py` | Keep basic tenacity retry |
| `agent/rate_limit_tracker.py` | Remove — single provider, no rate limit pooling |

**Replacement:** Basic HTTP retry with exponential backoff.

### 6.3 Model Metadata

| Original | What to Do |
|----------|-----------|
| `agent/model_metadata.py` | Simplify — tracks 200+ models |

**Replacement:** Hardcode context lengths for Claude models (opus: 200K, sonnet: 200K, haiku: 200K).

### 6.4 Context Management

| Original | What to Do |
|----------|-----------|
| `agent/context_compressor.py` | Simplify — LLM-based compression, manual feedback |
| `agent/conversation_compression.py` | Simplify |
| `agent/context_engine.py` | Remove — context file scanning (.hermes.md) |
| `agent/context_references.py` | Remove |
| `agent/manual_compression_feedback.py` | Remove |

**Replacement:** Simple truncation or turn-count-based dropping.

### 6.5 Message Sanitization

| Original | What to Do |
|----------|-----------|
| `agent/message_sanitization.py` | Simplify — elaborate surrogate/non-ASCII for multiple providers |

**Replacement:** Minimal UTF-8 validation for Anthropic only.

### 6.6 Usage & Analytics

| Original | What to Do |
|----------|-----------|
| `agent/usage_pricing.py` | Remove — cost estimation |
| `agent/account_usage.py` | Remove — usage tracking |
| `agent/insights.py` | Remove — analytics |
| `agent/nous_rate_guard.py` | Remove — Nous-specific |
| `agent/portal_tags.py` | Remove — Nous-specific |

### 6.7 Background & Automation

| Original | What to Do |
|----------|-----------|
| `agent/background_review.py` | Remove — auto skill-improvement |
| `agent/curator.py`, `curator_backup.py` | Remove — skill curation |
| `agent/onboarding.py` | Remove — first-run wizard |

### 6.8 Advanced Features

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

### 6.9 CLI Framework (hermes_cli/)

The original `hermes_cli/` has ~119 files. For bare-bones, most can go:

| Original | What to Do |
|----------|-----------|
| `hermes_cli/commands.py` | Simplify — remove gateway, cron, model switching commands |
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

**Replacement:** A minimal `argparse` CLI in `hermes_lite.py` or `config.py`.

### 6.10 Other Files

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

## 7. Dependency Simplification

### Original `pyproject.toml` Dependencies

The original has ~30+ core deps plus 30+ optional extras (anthropic, exa, firecrawl, fal, edge-tts, modal, daytona, messaging, matrix, slack, honcho, voice, mcp, homeassistant, sms, wecom, cli, tts-premium, pty, google, youtube, web, etc.).

### Minimal `pyproject.toml` for Hermes Lite

```toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "hermes-lite"
version = "0.1.0"
description = "Bare-bones Hermes agent for Claude Code CLI"
requires-python = ">=3.11"
dependencies = [
    "anthropic>=0.40.0",      # Anthropic SDK for claude -p
    "rich>=13.0.0",            # Terminal formatting (optional but nice)
    "pyyaml>=6.0",             # Config file parsing
]
```

**Removed deps and why:**
- `openai` — Not needed (using Anthropic SDK directly)
- `prompt_toolkit` — Not needed (no interactive TUI, `claude -p` handles input)
- `croniter` — No cron scheduling
- `PyJWT` — No GitHub App auth
- `psutil` — No process management (gateway)
- `fastapi`, `uvicorn` — No web server
- `ptyprocess` — No pseudo-terminal (or keep minimal)
- `python-telegram-bot`, `discord.py`, `slack-bolt` — No messaging
- `aiohttp` — Not needed unless doing async HTTP
- `httpx` — Can use `httpx` or stdlib `urllib`
- `tenacity` — Optional (can implement simple retry)
- `jinja2` — Optional (can use f-strings)
- `pydantic` — Optional (can use dataclasses)
- `requests` — Optional (Anthropic SDK handles HTTP)
- `fire` — Optional (can use argparse)
- `ruamel.yaml` — Use `pyyaml` instead

---

## 8. How It Works (Simplified Flow)

```
┌─────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│  User runs  │────▶│  hermes_lite.py     │────▶│  AIAgent            │
│  claude -p  │     │  (thin entry point) │     │  (core agent class) │
└─────────────┘     └─────────────────────┘     └─────────────────────┘
                                                        │
                              ┌───────────────────────┼───────────────────────┐
                              ▼                       ▼                       ▼
                        ┌──────────┐           ┌──────────┐           ┌──────────┐
                        │ Anthropic│           │  Tools   │           │  Memory  │
                        │  Adapter │           │ (file,   │           │ (sqlite) │
                        │(claude -p│           │  shell,  │           │          │
                        │  compat) │           │  code)   │           │          │
                        └──────────┘           └──────────┘           └──────────┘
                              │                       │
                              └───────────────────────┘
                                        │
                                        ▼
                              ┌─────────────────────┐
                              │  Response to user   │
                              │  via stdout         │
                              └─────────────────────┘
```

### Entry Point (`hermes_lite.py`)

```python
#!/usr/bin/env python3
"""Hermes Lite — Bare-bones agent for claude -p."""

import os
import sys
from agent.agent import AIAgent


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: Set ANTHROPIC_API_KEY", file=sys.stderr)
        sys.exit(1)

    agent = AIAgent(api_key=api_key, model="claude-sonnet-4-20250514")

    # Read user prompt from stdin (piped by claude -p)
    user_message = sys.stdin.read().strip()
    if not user_message:
        user_message = "Hello!"

    response = agent.run_conversation(user_message)
    print(response)


if __name__ == "__main__":
    main()
```

---

## 9. Implementation Phases

### Phase 1: Skeleton (~2 hours)
- Create `hermes_lite.py` entry point
- Minimal `AIAgent` class with Anthropic client
- Hardcoded tool schema (file, shell, python)
- Single-turn conversation loop
- **Deliverable:** Can run `echo "hello" | python hermes_lite.py` and get a response

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
- SQLite conversation persistence
- System prompt customization
- Config file support
- **Deliverable:** Conversations persist across runs

**Estimated total: ~600-800 lines of Python** (vs. ~45,000+ in the original).

---

## 10. Key Design Decisions

### 10.1 SDK Choice: Anthropic SDK vs. OpenAI SDK

The original Hermes uses the **OpenAI SDK** for all providers (even Anthropic, via OpenRouter or an adapter). For `claude -p`, we have two options:

| Option | Pros | Cons |
|--------|------|------|
| **A. Anthropic SDK directly** | Native thinking blocks, adaptive thinking, proper tool use | Different API shape than OpenAI |
| **B. OpenAI SDK with Anthropic base_url** | Consistent with Hermes pattern, simpler port | May not support all Claude features |

**Recommendation: A** — Use the Anthropic SDK directly. We're Anthropic-only and want full `claude -p` compatibility (thinking blocks, extended thinking, etc.).

### 10.2 Async vs. Sync

The original Hermes is heavily async (asyncio everywhere, especially in the gateway). For a local CLI tool:

**Recommendation: Sync** — `claude -p` is inherently synchronous. Use the Anthropic SDK's sync client. Simpler code, no `asyncio` complexity.

### 10.3 State Persistence

The original uses SQLite with WAL mode and FTS5 search.

**Recommendation: Simplify** — Plain SQLite (no FTS5, no WAL needed for single-user) or even JSON files for conversation history. Can upgrade to SQLite later.

### 10.4 Tool Schema Format

The original uses OpenAI's function-calling schema format (even for Anthropic, via translation).

**Recommendation:** Use Anthropic's native tool format directly. Simpler, no translation layer.

---

## 11. Files to Potentially Port (Detailed)

If doing a selective extraction, these are the specific functions/classes to pull from each original file:

### From `run_agent.py`
- `class AIAgent` — Core class (strip __init__ to essentials)
- `AIAgent.run_conversation()` — Entry point (forwards to conversation_loop)
- `AIAgent._create_anthropic_client()` — Client construction
- `AIAgent._build_system_prompt()` — System prompt assembly
- Tool execution dispatch logic

### From `agent/conversation_loop.py`
- `run_conversation()` — Main loop body
- Tool call parsing and dispatch
- Message history management
- Interrupt handling (simplify)

### From `agent/anthropic_adapter.py`
- `build_anthropic_client()` — Client setup
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

## 12. Summary

| Metric | Original Hermes | Hermes Lite (Target) |
|--------|----------------|---------------------|
| Total Python files | ~430 | ~10-15 |
| Total lines of code | ~45,000+ | ~600-800 |
| Dependencies | ~30+ core + 30+ extras | ~3-5 |
| Entry points | CLI, Gateway, ACP, Batch | Single CLI |
| Providers | 15+ | 1 (Anthropic) |
| Platforms | 15+ messaging | None (local only) |
| Tools | 40+ | 3-5 |
| Skills | 43 files, 20+ categories | 0-3 |
| OS Support | Mac, Linux, Windows, Termux, Docker | Mac only |
| Install method | curl shell script | `pip install` |

---

*Generated 2026-06-01. This plan is a living document — update as implementation progresses.*
