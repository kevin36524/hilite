#!/usr/bin/env python3
"""HiLite -- Bare-bones agent for Claude Code users.

Usage:
    echo "Your prompt" | python hilite.py
    python hilite.py --prompt "Your prompt"
    python hilite.py --session abc123 --prompt "Continue..."
    python hilite.py --list-sessions
    python hilite.py --skill slack-latest "What did I miss?"
    python hilite.py /slack-latest "What did I miss?"
"""

import argparse
import atexit
import sys

from hilite.agent.agent import AIAgent
from hilite.config import load_config
from hilite.constants import DEFAULT_MODEL
from hilite.skills import list_skill_names
from hilite.state import list_sessions


def main():
    parser = argparse.ArgumentParser(description="HiLite -- Bare-bones AI agent")
    parser.add_argument("--prompt", "-p", help="User prompt (alternative to stdin)")
    parser.add_argument("--model", "-m", help="Model override (e.g., claude-sonnet-4-6)")
    parser.add_argument("--system", "-s", help="System prompt override")
    parser.add_argument("--session", help="Session ID to continue a conversation")
    parser.add_argument("--list-sessions", action="store_true", help="List saved sessions")
    parser.add_argument("--list-skills", action="store_true", help="List available skills")
    parser.add_argument("--skill", help="Preload a skill by name for this run")
    args, remaining = parser.parse_known_args()

    if args.list_sessions:
        sessions = list_sessions()
        if not sessions:
            print("No saved sessions.")
            return
        print(f"{'Session ID':<30} {'Messages':>8} {'Timestamp':>20}")
        print("-" * 60)
        import time
        for s in sessions:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["timestamp"]))
            print(f"{s['session_id']:<30} {s['message_count']:>8} {ts:>20}")
        return

    if args.list_skills:
        skills = list_skill_names()
        if not skills:
            print("No skills found.")
            return
        print("Available skills:")
        for name, desc in skills:
            print(f"  {name:<20} {desc}")
        return

    config = load_config()
    model = args.model or config.get("model", DEFAULT_MODEL)

    # Read prompt
    if args.prompt:
        user_message = args.prompt
    elif remaining:
        # Positional arguments after flags
        user_message = " ".join(remaining)
    elif not sys.stdin.isatty():
        user_message = sys.stdin.read().strip()
    else:
        user_message = input("HiLite> ").strip()

    if not user_message:
        print("Error: No prompt provided. Use --prompt or pipe stdin.", file=sys.stderr)
        sys.exit(1)

    # Handle /skill-name slash command
    preload_skill = args.skill
    if user_message.startswith("/"):
        parts = user_message[1:].split(None, 1)
        if parts:
            preload_skill = parts[0]
            if len(parts) > 1:
                user_message = parts[1]
            else:
                user_message = ""

    agent = AIAgent(
        model=model,
        system_prompt=args.system,
        session_id=args.session,
        preload_skill=preload_skill,
    )

    # Ensure MCP connections are cleaned up on exit
    atexit.register(agent.tools.shutdown)

    try:
        response = agent.run_conversation(user_message)
        print(response)
        print(f"\n[Session: {agent.session_id}]", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
