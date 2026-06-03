"""OAuth 2.1 PKCE support for MCP HTTP/SSE servers.

Builds an ``httpx.Auth`` (the MCP SDK's ``OAuthClientProvider``) that the
transport layer attaches to its HTTP client.  The SDK drives the full flow
internally -- metadata discovery, dynamic client registration, PKCE, the
authorization-code exchange, and token refresh.  We only supply:

  * ``client_metadata``  -- what kind of client we are (scope, redirect URI)
  * ``storage``          -- where tokens/client-info persist (disk, 0600)
  * ``redirect_handler`` -- opens the browser at the authorization URL
  * ``callback_handler`` -- captures ``?code=...&state=...`` on localhost

Tokens are stored on disk under ``~/.hilite/mcp-oauth/<server>.json`` (NOT the
macOS keychain -- the keychain is only used for HiLite's own Anthropic key).

The MCP SDK's OAuth machinery is an optional import; if it is unavailable we
fail with a clear, actionable error rather than at import time.
"""

from __future__ import annotations

import json
import socket
import sys
import webbrowser
from pathlib import Path
from typing import Any

# Lazy import -- MCP SDK OAuth is an optional sub-module
try:
    from mcp.client.auth import OAuthClientProvider, TokenStorage
    from mcp.shared.auth import (
        OAuthClientInformationFull,
        OAuthClientMetadata,
        OAuthToken,
    )

    _HAS_OAUTH = True
except ImportError:  # pragma: no cover - depends on optional extra
    _HAS_OAUTH = False
    TokenStorage = object  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Token storage (disk)
# ---------------------------------------------------------------------------


def _oauth_dir() -> Path:
    """Return ``~/.hilite/mcp-oauth/``, creating it with 0700 perms."""
    d = Path.home() / ".hilite" / "mcp-oauth"
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    return d


def _token_path(server_name: str) -> Path:
    """Path to the persisted token+client file for a server."""
    return _oauth_dir() / f"{server_name}.json"


class DiskTokenStorage(TokenStorage):  # type: ignore[misc]
    """``TokenStorage`` backed by a single JSON file per server.

    Layout::

        {"tokens": {...OAuthToken...}, "client_info": {...ClientInfo...}}

    Written with mode ``0600``.  Reads tolerate a missing or corrupt file
    (returns ``None`` so the SDK re-runs the flow).
    """

    def __init__(self, server_name: str) -> None:
        self.server_name = server_name
        self.path = _token_path(server_name)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self.path.chmod(0o600)

    async def get_tokens(self) -> Any:
        raw = self._read().get("tokens")
        return OAuthToken.model_validate(raw) if raw else None

    async def set_tokens(self, tokens: Any) -> None:
        data = self._read()
        data["tokens"] = tokens.model_dump(mode="json", exclude_none=True)
        self._write(data)

    async def get_client_info(self) -> Any:
        raw = self._read().get("client_info")
        return OAuthClientInformationFull.model_validate(raw) if raw else None

    async def set_client_info(self, client_info: Any) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(data)

    def seed_client_info(self, client_info: Any) -> None:
        """Pre-store client info synchronously (for pre-registered clients)."""
        if self._read().get("client_info"):
            return  # don't clobber a previously registered client
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(data)


# ---------------------------------------------------------------------------
# Interactive browser flow handlers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Pick a free localhost TCP port."""
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


async def _redirect_handler(authorization_url: str) -> None:
    """Open the system browser at the authorization URL."""
    if not sys.stdin.isatty():
        raise RuntimeError(
            "OAuth authentication requires a browser, but HiLite is running "
            "non-interactively. Run HiLite once in an interactive terminal to "
            "complete authentication; tokens are then cached for later runs."
        )
    print(
        f"[hilite] Opening browser for OAuth authorization:\n  {authorization_url}",
        file=sys.stderr,
    )
    webbrowser.open(authorization_url)


def _make_callback_handler(port: int):
    """Build a callback handler that captures the auth code on *port*."""

    async def callback_handler() -> tuple[str, str | None]:
        import anyio

        return await anyio.to_thread.run_sync(lambda: _capture_code(port))

    return callback_handler


def _capture_code(port: int) -> tuple[str, str | None]:
    """Run a one-shot localhost HTTP server, return ``(code, state)``."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, urlparse

    captured: dict[str, str | None] = {"code": None, "state": None, "error": None}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = parse_qs(urlparse(self.path).query)
            captured["code"] = (query.get("code") or [None])[0]
            captured["state"] = (query.get("state") or [None])[0]
            captured["error"] = (query.get("error") or [None])[0]
            ok = captured["code"] is not None
            self.send_response(200 if ok else 400)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            body = (
                b"<html><body><h1>Authentication successful</h1>"
                b"<p>You can close this window and return to HiLite.</p></body></html>"
                if ok
                else b"<html><body><h1>Authentication failed</h1></body></html>"
            )
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:  # silence default logging
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    # Bound the wait so a never-arriving redirect doesn't hang forever.
    server.timeout = 300  # seconds
    print(f"[hilite] Waiting for OAuth callback on http://127.0.0.1:{port} "
          f"(up to {server.timeout}s) ...", file=sys.stderr)
    try:
        server.handle_request()
    finally:
        server.server_close()

    if captured["error"]:
        raise RuntimeError(f"OAuth authorization failed: {captured['error']}")
    if not captured["code"]:
        raise RuntimeError("OAuth callback did not include an authorization code.")
    return captured["code"], captured["state"]


# ---------------------------------------------------------------------------
# Provider construction
# ---------------------------------------------------------------------------


def build_oauth_provider(
    server_name: str, url: str, oauth_cfg: dict[str, Any] | None
) -> Any:
    """Build an ``OAuthClientProvider`` (an ``httpx.Auth``) for *url*.

    ``oauth_cfg`` mirrors the ``oauth:`` block in config.yaml:
        client_id, client_secret, scope, redirect_port, client_name.

    Raises ``RuntimeError`` if the MCP SDK's OAuth support is unavailable.
    """
    if not _HAS_OAUTH:
        raise RuntimeError(
            "MCP SDK OAuth support is not available. "
            "Install the MCP extra: uv tool install --editable '.[mcp]'"
        )

    cfg = oauth_cfg or {}
    port = cfg.get("redirect_port") or _free_port()
    redirect_uri = f"http://localhost:{port}/callback"

    metadata = OAuthClientMetadata(
        redirect_uris=[redirect_uri],
        client_name=cfg.get("client_name", "HiLite Agent"),
        scope=cfg.get("scope"),
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method=(
            "client_secret_post" if cfg.get("client_secret") else "none"
        ),
    )

    storage = DiskTokenStorage(server_name)

    # If the gateway issues a pre-registered client, seed it so the SDK skips
    # dynamic registration.
    client_id = cfg.get("client_id")
    if client_id:
        try:
            storage.seed_client_info(
                OAuthClientInformationFull(
                    client_id=client_id,
                    client_secret=cfg.get("client_secret"),
                    redirect_uris=[redirect_uri],
                    client_name=cfg.get("client_name", "HiLite Agent"),
                    scope=cfg.get("scope"),
                    grant_types=["authorization_code", "refresh_token"],
                    response_types=["code"],
                    token_endpoint_auth_method=(
                        "client_secret_post" if cfg.get("client_secret") else "none"
                    ),
                )
            )
        except Exception as e:  # pragma: no cover - defensive
            print(
                f"[hilite] Warning: could not seed OAuth client info for "
                f"'{server_name}': {e}",
                file=sys.stderr,
            )

    return OAuthClientProvider(
        server_url=url,
        client_metadata=metadata,
        storage=storage,
        redirect_handler=_redirect_handler,
        callback_handler=_make_callback_handler(port),
    )


# ---------------------------------------------------------------------------
# Auth-failure detection (used for 401 recovery messaging)
# ---------------------------------------------------------------------------


def is_auth_failure(error: Exception) -> bool:
    """Return True if *error* looks like an OAuth/authentication failure."""
    name = type(error).__name__
    msg = str(error).lower()

    if "unauthorized" in msg or "401" in msg:
        return True
    if name in ("OAuthFlowError", "OAuthTokenError", "OAuthRegistrationError"):
        return True
    if name == "McpError":
        code = getattr(error, "code", None)
        if code and str(code).lower() in ("unauthorized", "invalid_request"):
            return True
    if name == "HTTPStatusError":
        resp = getattr(error, "response", None)
        if resp and getattr(resp, "status_code", None) == 401:
            return True
    return False
