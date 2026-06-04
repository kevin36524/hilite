# HiLite Documentation

This folder contains planning and design documents for the HiLite project.

## Files

- **[hilite-plan.md](01_hilite-plan.md)** — Comprehensive plan covering architecture, what to keep, what to remove, directory structure, dependency simplification, and implementation phases.
- **[serve-mode-spec.md](05_serve-mode-spec.md)** — Long-lived `--serve` mode: an ndjson streaming/interactive backend so a GUI front-end can drive HiLite as an agent backend. Reuses the existing tool-calling loop; adds an event sink and two interactive tools.
- **[dynamic-tools-spec.md](06_dynamic-tools-spec.md)** — Per-session on-demand tool injection via a `tools` allowlist on `start`, plus a catalog of frontend/UI tools that drive the front-end through a new `ui_action` event (generalizes the serve-mode `ask_user` pattern). One-shot path unchanged.
- **[terminal-control-tools-spec.md](07_terminal-control-tools-spec.md)** — Three **blocking** frontend tools (`terminal_snapshot` / `terminal_send_text` / `terminal_send_key`) that let a serve-mode agent observe and drive a front-end-owned interactive terminal (e.g. navigate `claude`'s `/mcp` menu). The first users of doc 06's blocking `ui_request`/`ui_result` path; additive to `serve.py`, no protocol changes.

## Quick Reference

**Goal:** A bare-bones version of Hermes agent that:
- Runs on **macOS only**
- Calls the **Anthropic API directly** using Claude Code OAuth credentials (subscription-friendly)
- Runs **locally** with no messaging, no gateway
- **Is the agentic harness itself** — implements its own tool-calling loop

**Target footprint:** ~600-800 lines of Python across ~10-15 files (vs. ~45,000+ lines across ~430 files in original Hermes).

**Target dependencies:** `anthropic`, `rich`, `pyyaml` (vs. ~60+ deps in original).

**Key decisions:**
- Clean-room extract (not fork & delete)
- Anthropic SDK directly (not OpenAI SDK with adapter)
- Synchronous (not async)
- JSON files for state (not SQLite/FTS5)
- Anthropic native tool format (not OpenAI function-calling schema)
- Auth via Claude Code OAuth credentials (reads `~/.claude/.credentials.json` or macOS Keychain)
