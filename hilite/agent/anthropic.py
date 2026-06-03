"""Anthropic adapter -- auth (Claude Code creds or API key) + client setup."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from anthropic import Anthropic

# Service name for HiLite's own keychain entry. Store a key with:
#   security add-generic-password -U -s "hilite" -a "$USER" -w "sk-ant-..."
HILITE_KEYCHAIN_SERVICE = "hilite"


def _read_hilite_keychain() -> str | None:
    """Read HiLite's dedicated API key from the macOS keychain.

    This is HiLite's own entry (service ``hilite``), separate from Claude
    Code's credentials, so it can be managed independently.
    """
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s", HILITE_KEYCHAIN_SERVICE,
                "-w",  # output password only
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        token = result.stdout.strip()
        return token if token else None
    except (subprocess.TimeoutExpired, OSError):
        return None


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


def _read_claude_code_api_key() -> str | None:
    """Read an API key from Claude Code's 'Claude Code' keychain entry.

    When Claude Code is authenticated with an API key (rather than a personal
    Claude.ai OAuth subscription), the key is stored in the macOS keychain under
    the service name 'Claude Code' (not 'Claude Code-credentials', which holds
    only MCP OAuth tokens). The password is the raw ``sk-ant-...`` key.
    """
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s", "Claude Code",
                "-w",  # output password only
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None

        token = result.stdout.strip()
        return token if token.startswith("sk-ant-") else None

    except (subprocess.TimeoutExpired, OSError):
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
        2. HiLite's own keychain entry (service 'hilite')
        3. Claude Code OAuth token from keychain (personal Claude.ai login)
        4. Claude Code API key from keychain ('Claude Code' service)
        5. Claude Code OAuth token from settings file

    Raises:
        RuntimeError: if no credentials are found.
    """
    # 1. Explicit API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return api_key

    # 2. HiLite's dedicated keychain entry
    hilite_key = _read_hilite_keychain()
    if hilite_key:
        return hilite_key

    # 3. Claude Code OAuth from keychain (personal Claude.ai subscription)
    oauth_token = _read_claude_code_credentials()
    if oauth_token:
        return oauth_token

    # 4. Claude Code API key from keychain (API-key auth, e.g. enterprise)
    keychain_key = _read_claude_code_api_key()
    if keychain_key:
        return keychain_key

    # 5. Claude Code OAuth from file
    oauth_token = _read_claude_code_credentials_file()
    if oauth_token:
        return oauth_token

    raise RuntimeError(
        "No Anthropic credentials found. Set ANTHROPIC_API_KEY, store a key in "
        "the keychain (service 'hilite'), or login with Claude Code."
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
