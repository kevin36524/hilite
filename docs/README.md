# HiLite Documentation

This folder contains planning and design documents for the HiLite project.

## Files

- **[hilite-plan.md](01_hilite-plan.md)** — Comprehensive plan covering architecture, what to keep, what to remove, directory structure, dependency simplification, and implementation phases.

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
