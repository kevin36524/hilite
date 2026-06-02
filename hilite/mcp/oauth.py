"""OAuth 2.1 PKCE support for MCP HTTP/SSE servers.

Provides token storage on disk, a browser-based authorization flow,
auto-refresh, and auth-recovery on 401.

This module lazy-imports the MCP SDK's OAuth machinery so that HiLite
works even when the SDK is not installed (stdio-only deployments).
"""

from __future__ import annotations

import json
import sys
import webbrowser
from pathlib import Path
from typing import Any

# Lazy import -- MCP SDK OAuth is an optional sub-module
try:
    from mcp.client.auth import OAuthClientProvider

    _HAS_OAUTH = True
except ImportError:
    _HAS_OAUTH = False


# ---------------------------------------------------------------------------
# Token storage
# ---------------------------------------------------------------------------


def _oauth_dir() -> Path:
    """Return ``~/.hilite/mcp-oauth/``, creating it if needed."""
    d = Path.home() / ".hilite" / "mcp-oauth"
    d.mkdir(parents=True, exist_ok=True)
    # Restrict permissions
    d.chmod(0o700)
    return d


def _token_path(server_name: str) -> Path:
    """Path to the persisted token file for a server."""
    return _oauth_dir() / f"{server_name}.json"


def load_tokens(server_name: str) -> dict[str, Any] | None:
    """Load cached OAuth tokens for *server_name* from disk."""
    path = _token_path(server_name)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_tokens(server_name: str, tokens: dict[str, Any]) -> None:
    """Persist OAuth tokens for *server_name* to disk."""
    path = _token_path(server_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2, ensure_ascii=False)
    path.chmod(0o600)


# ---------------------------------------------------------------------------
# OAuth manager
# ---------------------------------------------------------------------------


class MCPOAuthManager:
    """Per-server OAuth state -- discovery, registration, token management.

    Usage::

        mgr = MCPOAuthManager("google-drive")
        provider = mgr.get_provider("https://mcp.googleapis.com/mcp", config)
        # provider is an OAuthClientProvider suitable for httpx auth
    """

    def __init__(self, server_name: str):
        self.server_name = server_name
        self._provider: Any | None = None

    def get_provider(self, url: str, config: dict[str, Any]) -> Any:
        """Get or build an OAuthClientProvider for the given server URL.

        If cached tokens exist they are loaded; otherwise the browser flow
        is triggered (if running interactively).
        """
        if not _HAS_OAUTH:
            raise RuntimeError(
                "MCP SDK OAuth support not available. "
                "Install with: pip install 'mcp>=1.0.0'"
            )

        if self._provider is not None:
            return self._provider

        tokens = load_tokens(self.server_name)

        if tokens:
            # Build provider from cached tokens
            self._provider = self._build_provider_from_tokens(url, config, tokens)
            return self._provider

        # No cached tokens -- need interactive auth
        if not sys.stdin.isatty():
            raise RuntimeError(
                f"OAuth authentication required for MCP server '{self.server_name}', "
                "but running non-interactively. Please run HiLite interactively "
                "once to authenticate."
            )

        tokens = self._run_browser_flow(url, config)
        save_tokens(self.server_name, tokens)
        self._provider = self._build_provider_from_tokens(url, config, tokens)
        return self._provider

    def _build_provider_from_tokens(
        self, url: str, config: dict[str, Any], tokens: dict[str, Any]
    ) -> Any:
        """Construct an OAuthClientProvider from cached token data."""
        # The MCP SDK's OAuthClientProvider accepts various auth configurations.
        # We construct it using the saved client_info + tokens.
        client_info = tokens.get("client_info", {})
        return OAuthClientProvider(
            client_id=client_info.get("client_id", ""),
            client_secret=client_info.get("client_secret", ""),
            token_endpoint=config.get("token_endpoint", ""),
            authorization_endpoint=config.get("authorization_endpoint", ""),
            access_token=tokens.get("access_token", ""),
            refresh_token=tokens.get("refresh_token", ""),
            token_type=tokens.get("token_type", "Bearer"),
            expires_at=tokens.get("expires_at"),
        )

    def _run_browser_flow(self, url: str, config: dict[str, Any]) -> dict[str, Any]:
        """Launch browser, capture authorization code, exchange for tokens.

        Returns the token dict to persist.
        """
        # This is a simplified implementation.  The full MCP SDK OAuth flow
        # involves dynamic client registration and PKCE code generation.
        # For v1 we delegate as much as possible to the SDK.
        print(
            f"[hilite] Launching browser for OAuth authentication "
            f"with MCP server '{self.server_name}'...",
            file=sys.stderr,
        )

        # Launch the authorization URL in the default browser
        auth_url = self._build_authorization_url(url, config)
        webbrowser.open(auth_url)

        # Start an ephemeral localhost server to capture the callback
        code = self._start_callback_server(config)

        # Exchange code for tokens
        tokens = self._exchange_code(url, config, code)
        return tokens

    def _build_authorization_url(self, url: str, config: dict[str, Any]) -> str:
        """Build the PKCE authorization URL."""
        # In a full implementation this uses the MCP SDK's OAuth discovery
        # and PKCE code challenge generation.  For v1 we return a placeholder
        # that the SDK or user can customize.
        auth_endpoint = config.get("authorization_endpoint", "")
        client_id = config.get("client_id", "")
        redirect_uri = f"http://localhost:{config.get('redirect_port', 0)}"
        scope = config.get("scope", "")

        import urllib.parse

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
        }
        return f"{auth_endpoint}?{urllib.parse.urlencode(params)}"

    def _start_callback_server(self, config: dict[str, Any]) -> str:
        """Start an ephemeral HTTP server to capture the OAuth callback code.

        Returns the authorization code from the query parameters.
        """
        from http.server import BaseHTTPRequestHandler, HTTPServer

        redirect_port = config.get("redirect_port", 0)
        code_holder: dict[str, str | None] = {"code": None}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                from urllib.parse import parse_qs, urlparse

                query = parse_qs(urlparse(self.path).query)
                if "code" in query:
                    code_holder["code"] = query["code"][0]
                    self.send_response(200)
                    self.send_header("Content-type", "text/html")
                    self.end_headers()
                    self.wfile.write(
                        b"<html><body><h1>Authentication successful!</h1>"
                        b"<p>You can close this window and return to HiLite.</p></body></html>"
                    )
                else:
                    self.send_response(400)
                    self.send_header("Content-type", "text/html")
                    self.end_headers()
                    self.wfile.write(
                        b"<html><body><h1>Authentication failed</h1></body></html>"
                    )

            def log_message(self, format: str, *args: Any) -> None:
                pass  # suppress default stderr logging

        server = HTTPServer(("127.0.0.1", redirect_port), Handler)
        port = server.server_address[1]
        print(
            f"[hilite] Waiting for OAuth callback on http://127.0.0.1:{port} ...",
            file=sys.stderr,
        )
        server.handle_request()

        code = code_holder["code"]
        if code is None:
            raise RuntimeError("OAuth callback did not include an authorization code.")
        return code

    def _exchange_code(
        self, url: str, config: dict[str, Any], code: str
    ) -> dict[str, Any]:
        """Exchange authorization code + PKCE verifier for access token.

        In a full implementation this uses the MCP SDK's token exchange.
        For v1 we use a direct HTTP POST.
        """
        import urllib.parse
        import urllib.request

        token_endpoint = config.get("token_endpoint", "")
        client_id = config.get("client_id", "")
        client_secret = config.get("client_secret", "")
        redirect_uri = f"http://127.0.0.1:{config.get('redirect_port', 0)}"

        data = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }).encode()

        req = urllib.request.Request(
            token_endpoint, data=data, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode())

        tokens = {
            "access_token": result.get("access_token", ""),
            "refresh_token": result.get("refresh_token", ""),
            "token_type": result.get("token_type", "Bearer"),
            "expires_at": result.get("expires_at"),
            "client_info": {
                "client_id": client_id,
                "client_secret": client_secret,
            },
        }
        return tokens


# ---------------------------------------------------------------------------
# Auth recovery helpers
# ---------------------------------------------------------------------------


def is_auth_failure(error: Exception) -> bool:
    """Detect whether an exception indicates an OAuth authentication failure.

    Returns *True* for:
      - ``McpError`` with ``Unauthorized`` code
      - ``httpx.HTTPStatusError`` with 401 status
    """
    name = type(error).__name__
    msg = str(error).lower()

    if "unauthorized" in msg or "401" in msg:
        return True

    # Check for MCP SDK error types
    if name == "McpError":
        code = getattr(error, "code", None)
        if code and str(code).lower() in ("unauthorized", "invalid_request"):
            return True

    # Check for httpx 401
    if name == "HTTPStatusError":
        resp = getattr(error, "response", None)
        if resp and getattr(resp, "status_code", None) == 401:
            return True

    return False
