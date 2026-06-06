"""Moodle Web Services client.

Calls a real Moodle instance's REST endpoint with a wstoken. Activated
by setting `MOODLE_URL` + `MOODLE_TOKEN` in the environment.

Security:
  - SSRF guard rejects loopback, link-local, RFC1918, and metadata IPs
    unless `MOODLE_WS_ALLOW_INSECURE=1` is set (for local development).
  - Function name validated against `^[a-z][a-z0-9_]+$` to prevent
    parameter injection through the wsfunction string.
  - HTTPS required unless allow-insecure is set.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import (
    MOODLE_TOKEN,
    MOODLE_URL,
    MOODLE_WS_ALLOW_INSECURE,
)

_WS_FUNCTION_RE = re.compile(r"^[a-z][a-z0-9_]+$")


class WSConfigError(RuntimeError):
    """MOODLE_URL / MOODLE_TOKEN not set, or URL fails SSRF policy."""


class WSCallError(RuntimeError):
    """Moodle returned an exception payload or transport failed."""


def configured() -> bool:
    return bool(MOODLE_URL and MOODLE_TOKEN)


def _is_private_ip(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True  # unresolvable — treat as unsafe
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            return True
        # AWS / GCP / Azure metadata IPs
        if str(ip) in {"169.254.169.254", "fd00:ec2::254"}:
            return True
    return False


def validate_url(url: str) -> str:
    """Return the normalized URL or raise WSConfigError."""
    if not url:
        raise WSConfigError("MOODLE_URL is not set.")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise WSConfigError(f"MOODLE_URL must be http(s), got: {parsed.scheme}")
    if parsed.scheme == "http" and not MOODLE_WS_ALLOW_INSECURE:
        raise WSConfigError(
            "MOODLE_URL must use https. Set MOODLE_WS_ALLOW_INSECURE=1 to override."
        )
    host = parsed.hostname or ""
    if not host:
        raise WSConfigError("MOODLE_URL has no host.")
    if not MOODLE_WS_ALLOW_INSECURE and _is_private_ip(host):
        raise WSConfigError(
            f"MOODLE_URL host {host!r} resolves to a private/loopback/metadata IP. "
            "Refusing for SSRF safety. Set MOODLE_WS_ALLOW_INSECURE=1 to override."
        )
    return url.rstrip("/")


def validate_function(name: str) -> str:
    if not _WS_FUNCTION_RE.match(name):
        raise WSCallError(f"Invalid Moodle WS function name: {name!r}")
    return name


async def call(
    client: httpx.AsyncClient,
    function: str,
    args: dict[str, Any] | None = None,
) -> Any:
    """Invoke a Moodle WS function via REST.

    Returns the decoded JSON body. Raises WSCallError on Moodle exception
    payloads or HTTP failures.
    """
    if not configured():
        raise WSConfigError("MOODLE_URL and MOODLE_TOKEN must be set.")
    base = validate_url(MOODLE_URL)
    fn = validate_function(function)
    url = f"{base}/webservice/rest/server.php"
    data: dict[str, str] = {
        "wstoken": MOODLE_TOKEN,
        "wsfunction": fn,
        "moodlewsrestformat": "json",
    }
    for k, v in (args or {}).items():
        for fk, fv in _flatten(k, v):
            data[fk] = fv
    try:
        resp = await client.post(url, data=data)
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPError as e:
        raise WSCallError(f"HTTP error calling {fn}: {e}") from e
    except ValueError as e:
        raise WSCallError(f"Non-JSON response from {fn}: {e}") from e
    if isinstance(payload, dict) and payload.get("exception"):
        raise WSCallError(
            f"Moodle exception: {payload.get('errorcode')} — {payload.get('message')}"
        )
    return payload


def _flatten(key: str, value: Any) -> list[tuple[str, str]]:
    """Encode nested args using Moodle's bracketed-key convention.

    Example: {"options": {"limit": 5}} -> [("options[limit]", "5")]
    """
    if isinstance(value, dict):
        out: list[tuple[str, str]] = []
        for k, v in value.items():
            out.extend(_flatten(f"{key}[{k}]", v))
        return out
    if isinstance(value, list):
        out = []
        for i, v in enumerate(value):
            out.extend(_flatten(f"{key}[{i}]", v))
        return out
    if isinstance(value, bool):
        return [(key, "1" if value else "0")]
    return [(key, str(value))]


async def list_functions(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Enumerate functions available to the current token via core_webservice_get_site_info."""
    info = await call(client, "core_webservice_get_site_info")
    if not isinstance(info, dict):
        return []
    return list(info.get("functions") or [])
