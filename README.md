# HiLite

Bare-bones AI agent for Claude Code CLI users. Runs locally, calls the Anthropic API directly using Claude Code credentials, with a simple tool-calling loop.

## Quick Start

```bash
# One-shot via stdin
echo "What files are in this directory?" | python hilite.py

# One-shot via arg
python hilite.py --prompt "Explain recursion"

# Continue a conversation
python hilite.py --session 20260601-120000-001 --prompt "What was I working on?"

# List saved sessions
python hilite.py --list-sessions
```

## Setup

1. **Install dependencies:**
   ```bash
   pip install anthropic pyyaml
   ```

2. **Authenticate (choose one):**
   - Set `ANTHROPIC_API_KEY` env var
   - Or login with Claude Code: `claude login` (HiLite reads its OAuth token from the macOS keychain)

## Architecture

```
hilite.py              # CLI entry point
hilite/
├── agent/
│   ├── agent.py       # AIAgent class (tool-calling loop)
│   └── anthropic.py   # Auth + Anthropic client
├── tools/
│   ├── registry.py    # Tool schema registry
│   ├── file.py        # read_file, write_file, list_directory
│   └── shell.py       # execute_command, execute_python
├── state.py           # JSON session persistence
├── config.py          # Env var + file config
└── constants.py       # Paths and defaults
```

## Tools

| Tool | Description |
|------|-------------|
| `read_file` | Read file contents |
| `write_file` | Write content to a file |
| `list_directory` | List directory contents |
| `execute_command` | Run shell commands |
| `execute_python` | Execute Python code |

## Session Storage

Conversations are saved as JSON in `~/.hilite/sessions/`. Each session gets a unique ID (timestamp-based). Use `--session <id>` to continue any conversation.

## Constraints

- **macOS only** — reads Claude Code credentials from macOS keychain
- **Anthropic only** — no other LLM providers
- **Local only** — no messaging, no gateway, no web dashboard
- **Python 3.11+** recommended (3.9+ with `from __future__ import annotations`)
