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


def _parse_kv(pairs, label):
    """Parse a list of ``KEY=VALUE`` strings into a dict."""
    result = {}
    for item in pairs or []:
        if "=" not in item:
            print(f"Error: {label} '{item}' must be KEY=VALUE.", file=sys.stderr)
            sys.exit(2)
        key, value = item.split("=", 1)
        result[key] = value
    return result


def _unwrap_exceptions(exc):
    """Flatten an exception (group) into its leaf exceptions for display."""
    inner = getattr(exc, "exceptions", None)
    if inner:  # BaseExceptionGroup
        leaves = []
        for sub in inner:
            leaves.extend(_unwrap_exceptions(sub))
        return leaves
    return [exc]


def handle_mcp_command(argv):
    """Handle the ``hilite mcp <add|list|remove>`` subcommands."""
    from pathlib import Path

    from hilite.mcp.manage import (
        add_mcp_server,
        build_server_config,
        get_server_definitions,
        remove_mcp_server,
    )

    parser = argparse.ArgumentParser(prog="hilite mcp", description="Manage MCP servers")
    sub = parser.add_subparsers(dest="action", required=True)

    p_add = sub.add_parser("add", help="Add (or overwrite) an MCP server definition")
    p_add.add_argument("name", help="Server name (becomes the mcp_<name>_<tool> prefix)")
    # stdio transport
    p_add.add_argument("--command", help="Executable to spawn (stdio transport)")
    p_add.add_argument("--arg", action="append", dest="args", metavar="ARG",
                       help="Argument for the command (repeatable). For values starting "
                            "with '-', use --arg=-y form.")
    p_add.add_argument("--cwd", help="Working directory for the command")
    # http/sse transport
    p_add.add_argument("--url", help="Server URL (HTTP/SSE transport)")
    p_add.add_argument("--transport", choices=["streamable-http", "sse"],
                       help="HTTP transport kind (default: streamable-http)")
    p_add.add_argument("--header", action="append", dest="headers", metavar="KEY=VALUE",
                       help="HTTP header (repeatable)")
    # shared / auth
    p_add.add_argument("--env", action="append", dest="env", metavar="KEY=VALUE",
                       help="Env var, e.g. SLACK_BOT_TOKEN='${SLACK_BOT_TOKEN}' (repeatable)")
    p_add.add_argument("--timeout", type=int, help="Per-tool-call timeout (seconds)")
    p_add.add_argument("--connect-timeout", type=int, dest="connect_timeout",
                       help="Initial connection timeout (seconds)")
    p_add.add_argument("--client-cert", action="append", dest="client_cert", metavar="PATH",
                       help="mTLS client cert; repeat for [cert, key] or [cert, key, passphrase]")
    p_add.add_argument("--client-key", dest="client_key", help="mTLS private key path")
    p_add.add_argument("--no-ssl-verify", action="store_true",
                       help="Disable TLS certificate verification")
    p_add.add_argument("--auth", choices=["oauth"], help="Authentication mode")
    p_add.add_argument("--oauth", action="append", dest="oauth", metavar="KEY=VALUE",
                       help="OAuth option, e.g. scope=drive.readonly (repeatable)")
    p_add.add_argument("--scope", choices=["global", "project"], default="global",
                       help="Write to ~/.hilite (global, default) or ./.hilite (project)")
    p_add.add_argument("--disabled", action="store_true",
                       help="Add the server but mark it enabled: false")
    p_add.add_argument("--overwrite", action="store_true",
                       help="Replace an existing server with the same name")

    p_list = sub.add_parser("list", help="List configured MCP servers")
    p_list.add_argument("--scope", choices=["global", "project"], default="global")

    p_rm = sub.add_parser("remove", help="Remove an MCP server definition")
    p_rm.add_argument("name")
    p_rm.add_argument("--scope", choices=["global", "project"], default="global")

    p_auth = sub.add_parser(
        "auth",
        help="Connect to one server now (runs the OAuth flow) and report its tools",
    )
    p_auth.add_argument("name")
    p_auth.add_argument("--scope", choices=["global", "project"], default="global")
    p_auth.add_argument("--verbose", "-v", action="store_true",
                        help="Print a full traceback if the connection fails")

    ns = parser.parse_args(argv)
    project_root = Path.cwd()

    if ns.action == "add":
        client_cert = ns.client_cert
        if client_cert and len(client_cert) == 1:
            client_cert = client_cert[0]
        try:
            cfg = build_server_config(
                command=ns.command,
                args=ns.args,
                env=_parse_kv(ns.env, "--env") or None,
                cwd=ns.cwd,
                url=ns.url,
                transport=ns.transport,
                headers=_parse_kv(ns.headers, "--header") or None,
                timeout=ns.timeout,
                connect_timeout=ns.connect_timeout,
                client_cert=client_cert,
                client_key=ns.client_key,
                ssl_verify=False if ns.no_ssl_verify else None,
                auth=ns.auth,
                oauth=_parse_kv(ns.oauth, "--oauth") or None,
                enabled=False if ns.disabled else None,
            )
            path = add_mcp_server(
                ns.name, cfg, scope=ns.scope, project_root=project_root,
                overwrite=ns.overwrite,
            )
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Added MCP server '{ns.name}' to {path}")
        print("Restart hilite for the new server to be connected.", file=sys.stderr)
        return

    if ns.action == "list":
        servers = get_server_definitions(scope=ns.scope, project_root=project_root)
        if not servers:
            print(f"No MCP servers configured ({ns.scope} scope).")
            return
        for name, cfg in servers.items():
            target = cfg.get("command") or cfg.get("url") or "?"
            state = "" if cfg.get("enabled", True) else "  [disabled]"
            print(f"  {name:<20} {target}{state}")
        return

    if ns.action == "remove":
        removed = remove_mcp_server(ns.name, scope=ns.scope, project_root=project_root)
        if removed:
            print(f"Removed MCP server '{ns.name}'.")
        else:
            print(f"No MCP server named '{ns.name}' ({ns.scope} scope).", file=sys.stderr)
            sys.exit(1)
        return

    if ns.action == "auth":
        servers = get_server_definitions(scope=ns.scope, project_root=project_root)
        server_config = servers.get(ns.name)
        if server_config is None:
            print(f"No MCP server named '{ns.name}' ({ns.scope} scope). "
                  f"Add it first with 'hilite mcp add'.", file=sys.stderr)
            sys.exit(1)

        from hilite.mcp.client import authenticate_and_probe

        is_oauth = server_config.get("auth") == "oauth"
        print(f"Connecting to MCP server '{ns.name}' ({server_config.get('url') or server_config.get('command')}) ...",
              file=sys.stderr)
        if is_oauth:
            print("This server uses OAuth -- a browser window may open for you to "
                  "authorize.", file=sys.stderr)
        try:
            tool_names = authenticate_and_probe(ns.name, server_config)
        except BaseException as e:  # noqa: BLE001 -- ExceptionGroup is BaseException
            print("\nConnection failed:", file=sys.stderr)
            for leaf in _unwrap_exceptions(e):
                print(f"  {type(leaf).__name__}: {leaf}", file=sys.stderr)
            if ns.verbose:
                import traceback
                traceback.print_exc()
            else:
                print("\nRe-run with --verbose for a full traceback.", file=sys.stderr)
            sys.exit(1)

        print(f"\nConnected to '{ns.name}'. {len(tool_names)} tool(s) available:")
        for name in tool_names:
            print(f"  mcp_{ns.name}_{name}")
        if is_oauth:
            from pathlib import Path as _Path
            token_file = _Path.home() / ".hilite" / "mcp-oauth" / f"{ns.name}.json"
            print(f"\nTokens cached at {token_file} -- future runs will not "
                  f"re-prompt.", file=sys.stderr)
        return


def main():
    # `hilite mcp ...` is a subcommand group, handled before flag parsing.
    if len(sys.argv) > 1 and sys.argv[1] == "mcp":
        handle_mcp_command(sys.argv[2:])
        return

    parser = argparse.ArgumentParser(description="HiLite -- Bare-bones AI agent")
    parser.add_argument("--prompt", "-p", help="User prompt (alternative to stdin)")
    parser.add_argument("--model", "-m", help="Model override (e.g., claude-sonnet-4-6)")
    parser.add_argument("--system", "-s", help="System prompt override")
    parser.add_argument("--session", help="Session ID to continue a conversation")
    parser.add_argument("--list-sessions", action="store_true", help="List saved sessions")
    parser.add_argument("--list-skills", action="store_true", help="List available skills")
    parser.add_argument("--skill", help="Preload a skill by name for this run")
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Run as an ndjson streaming/interactive backend over stdin/stdout.",
    )
    args, remaining = parser.parse_known_args()

    if args.serve:
        from hilite.serve import run_server

        run_server(
            default_model=args.model,
            default_session=args.session,
            default_skill=args.skill,
        )
        return

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
