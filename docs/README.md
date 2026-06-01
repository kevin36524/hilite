# Hermes Lite Documentation

This folder contains planning and design documents for the Hermes Lite project.

## Files

- **[hermes-lite-plan.md](hermes-lite-plan.md)** — Comprehensive plan covering what to keep, what to remove, directory structure, dependency simplification, and implementation phases.

## Quick Reference

**Goal:** A bare-bones version of Hermes agent that:
- Runs on **macOS only**
- Works with **Claude Code CLI (`claude -p`)** only
- Runs **locally** (no messaging, no gateway)

**Target footprint:** ~600-800 lines of Python across ~10-15 files (vs. ~45,000+ lines across ~430 files in original Hermes).

**Target dependencies:** `anthropic`, `rich`, `pyyaml` (vs. ~60+ deps in original).

**Key decisions:**
- Clean-room extract (not fork & delete)
- Use Anthropic SDK directly (not OpenAI SDK with adapter)
- Synchronous (not async)
- Minimal SQLite or JSON for state (no FTS5, no WAL)
- Anthropic native tool format (not OpenAI function-calling schema)
