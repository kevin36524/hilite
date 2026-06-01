"""Anthropic adapter -- auth (Claude Code creds or API key) + client setup."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from anthropic import Anthropic


def _read_claude_code_credentials() -> str | None:
    """Read OAuth token from Claude Code's macOS keychain entry.

    Claude Code stores credentials under the service name
    'Claude Code-credentials' in the macOS keychain.
    """
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s", "Claude Code-credentials",
                "-w",  # output password only
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None

        # The password field contains a JSON blob
        creds_json = json.loads(result.stdout.strip())
        oauth = creds_json.get("claudeAiOauth", {})
        token = oauth.get("accessToken")
        return token if token else None

    except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, OSError):
        return None


def _read_claude_code_credentials_file() -> str | None:
    """Fallback: read credentials from Claude Code's config file."""
    config_path = Path.home() / ".claude" / "settings.json"
    if not config_path.exists():
        return None

    try:
        with open(config_path) as f:
            data = json.load(f)
        # Claude Code may store a bearer token here
        token = data.get("bearerToken") or data.get("oauth", {}).get("accessToken")
        return token if token else None
    except (json.JSONDecodeError, OSError):
        return None


def get_auth_token() -> str:
    """Get an Anthropic auth token.

    Priority:
        1. ANTHROPIC_API_KEY env var (API key)
        2. Claude Code OAuth token from keychain
        3. Claude Code OAuth token from settings file

    Raises:
        RuntimeError: if no credentials are found.
    """
    # 1. Explicit API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return api_key

    # 2. Claude Code OAuth from keychain
    oauth_token = _read_claude_code_credentials()
    if oauth_token:
        return oauth_token

    # 3. Claude Code OAuth from file
    oauth_token = _read_claude_code_credentials_file()
    if oauth_token:
        return oauth_token

    raise RuntimeError(
        "No Anthropic credentials found. "
        "Set ANTHROPIC_API_KEY env var, or login with Claude Code (`claude login`)."
    )


def build_client() -> Anthropic:
    """Build an Anthropic client with available credentials."""
    token = get_auth_token()

    # Detect if it's an API key (starts with sk-ant) or OAuth JWT
    if token.startswith("sk-ant-"):
        return Anthropic(api_key=token)
    else:
        # OAuth JWT -- pass as auth_token (Anthropic SDK supports this)
        return Anthropic(auth_token=token)
