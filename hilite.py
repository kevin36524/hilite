#!/usr/bin/env python3
"""HiLite -- Bare-bones agent for Claude Code users.

Usage:
    echo "Your prompt" | python hilite.py
    python hilite.py --prompt "Your prompt"
    python hilite.py --session abc123 --prompt "Continue..."
    python hilite.py --list-sessions
"""

import argparse
import sys

from hilite.agent.agent import AIAgent
from hilite.config import load_config
from hilite.state import list_sessions


def main():
    parser = argparse.ArgumentParser(description="HiLite -- Bare-bones AI agent")
    parser.add_argument("--prompt", "-p", help="User prompt (alternative to stdin)")
    parser.add_argument("--model", "-m", help="Model override (e.g., claude-sonnet-4-6)")
    parser.add_argument("--system", "-s", help="System prompt override")
    parser.add_argument("--session", help="Session ID to continue a conversation")
    parser.add_argument("--list-sessions", action="store_true", help="List saved sessions")
    args = parser.parse_args()

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

    config = load_config()
    model = args.model or config.get("model", "claude-sonnet-4-6-20250601")

    # Read prompt
    if args.prompt:
        user_message = args.prompt
    elif not sys.stdin.isatty():
        user_message = sys.stdin.read().strip()
    else:
        user_message = input("HiLite> ").strip()

    if not user_message:
        print("Error: No prompt provided. Use --prompt or pipe stdin.", file=sys.stderr)
        sys.exit(1)

    agent = AIAgent(
        model=model,
        system_prompt=args.system,
        session_id=args.session,
    )

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
