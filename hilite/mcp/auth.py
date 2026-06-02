"""mTLS certificate resolution for MCP HTTP/SSE transports.

Parses ``client_cert`` / ``client_key`` / ``ssl_verify`` config and
produces values suitable for httpx ``cert=`` and ``verify=`` parameters.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def resolve_client_cert(
    client_cert: Any, client_key: str | None = None, ssl_verify: bool = True
) -> tuple[str | tuple[str, ...] | None, bool]:
    """Parse client_cert / client_key config for httpx.

    Returns ``(cert_value, ssl_verify)`` where *cert_value* is:
      - *None*: no cert configured
      - *str*: single PEM file path (combined cert + key)
      - ``(cert_path, key_path)``: separate cert and key files
      - ``(cert_path, key_path, password)``: with key passphrase

    Raises:
        FileNotFoundError: if a cert/key file does not exist.
        ValueError: on invalid config combinations.
    """
    cert_value: str | tuple[str, ...] | None = None

    if isinstance(client_cert, str):
        cert_path = _expand_path(client_cert)
        if client_key:
            key_path = _expand_path(client_key)
            cert_value = (cert_path, key_path)
        else:
            cert_value = cert_path
    elif isinstance(client_cert, list):
        if client_key:
            raise ValueError(
                "Cannot use 'client_key' when 'client_cert' is a list. "
                "Combine into a single 'client_cert' list."
            )
        expanded = [_expand_path(p) for p in client_cert]
        if len(expanded) == 1:
            cert_value = expanded[0]
        elif len(expanded) == 2:
            cert_value = (expanded[0], expanded[1])
        elif len(expanded) == 3:
            cert_value = (expanded[0], expanded[1], expanded[2])
        else:
            raise ValueError(
                "'client_cert' list must have 1-3 elements (cert, key, passphrase)."
            )

    # Validate files exist
    if isinstance(cert_value, str):
        _ensure_file(cert_value, "client_cert")
    elif isinstance(cert_value, tuple):
        _ensure_file(cert_value[0], "client_cert")
        if len(cert_value) >= 2:
            _ensure_file(cert_value[1], "client_key")

    return cert_value, ssl_verify


def _expand_path(path: str) -> str:
    """Expand ~ and environment variables in a path."""
    return os.path.expanduser(os.path.expandvars(path))


def _ensure_file(path: str, label: str) -> None:
    """Raise FileNotFoundError with a clear message if path does not exist."""
    if not Path(path).is_file():
        raise FileNotFoundError(f"MCP {label} file not found: {path}")
