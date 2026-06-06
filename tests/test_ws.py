"""Tests for the Moodle Web Services client (SSRF guard, function call)."""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from moodle_mcp import ws


def test_validate_function_rejects_uppercase() -> None:
    with pytest.raises(ws.WSCallError):
        ws.validate_function("Core_Get_Site_Info")


def test_validate_function_rejects_injection() -> None:
    with pytest.raises(ws.WSCallError):
        ws.validate_function("core_get_site_info; rm -rf /")


def test_validate_function_accepts_valid() -> None:
    assert ws.validate_function("core_course_get_courses") == "core_course_get_courses"


def test_validate_url_rejects_empty() -> None:
    with pytest.raises(ws.WSConfigError):
        ws.validate_url("")


def test_validate_url_rejects_non_http() -> None:
    with pytest.raises(ws.WSConfigError):
        ws.validate_url("ftp://moodle.example.com/")


def test_validate_url_rejects_loopback() -> None:
    with patch.object(ws, "MOODLE_WS_ALLOW_INSECURE", False), \
         pytest.raises(ws.WSConfigError):
        ws.validate_url("https://127.0.0.1/")


def test_validate_url_accepts_real_host() -> None:
    with patch.object(ws, "_is_private_ip", return_value=False):
        assert ws.validate_url("https://moodle.example.com/").endswith(".com")


def test_flatten_handles_nested_dict() -> None:
    pairs = dict(ws._flatten("options", {"limit": 5, "offset": 10}))
    assert pairs == {"options[limit]": "5", "options[offset]": "10"}


def test_flatten_handles_list() -> None:
    pairs = dict(ws._flatten("ids", [1, 2, 3]))
    assert pairs == {"ids[0]": "1", "ids[1]": "2", "ids[2]": "3"}


def test_flatten_handles_bool() -> None:
    pairs = dict(ws._flatten("flag", True))
    assert pairs == {"flag": "1"}


@pytest.mark.asyncio
@respx.mock
async def test_call_raises_on_moodle_exception() -> None:
    with patch.object(ws, "MOODLE_URL", "https://moodle.example.com"), \
         patch.object(ws, "MOODLE_TOKEN", "abc"), \
         patch.object(ws, "_is_private_ip", return_value=False):
        respx.post("https://moodle.example.com/webservice/rest/server.php").mock(
            return_value=httpx.Response(200, json={
                "exception": "moodle_exception",
                "errorcode": "invalidtoken",
                "message": "Invalid token",
            })
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(ws.WSCallError, match="invalidtoken"):
                await ws.call(client, "core_webservice_get_site_info")


@pytest.mark.asyncio
@respx.mock
async def test_call_returns_payload_on_success() -> None:
    with patch.object(ws, "MOODLE_URL", "https://moodle.example.com"), \
         patch.object(ws, "MOODLE_TOKEN", "abc"), \
         patch.object(ws, "_is_private_ip", return_value=False):
        respx.post("https://moodle.example.com/webservice/rest/server.php").mock(
            return_value=httpx.Response(200, json={
                "sitename": "Test Moodle",
                "functions": [{"name": "core_course_get_courses", "version": "4.4"}],
            })
        )
        async with httpx.AsyncClient() as client:
            payload = await ws.call(client, "core_webservice_get_site_info")
        assert payload["sitename"] == "Test Moodle"


@pytest.mark.asyncio
@respx.mock
async def test_list_functions_extracts_function_names() -> None:
    with patch.object(ws, "MOODLE_URL", "https://moodle.example.com"), \
         patch.object(ws, "MOODLE_TOKEN", "abc"), \
         patch.object(ws, "_is_private_ip", return_value=False):
        respx.post("https://moodle.example.com/webservice/rest/server.php").mock(
            return_value=httpx.Response(200, json={
                "functions": [
                    {"name": "fn_a", "version": "4.4"},
                    {"name": "fn_b", "version": "4.4"},
                ],
            })
        )
        async with httpx.AsyncClient() as client:
            fns = await ws.list_functions(client)
        assert [f["name"] for f in fns] == ["fn_a", "fn_b"]


def test_configured_false_when_unset() -> None:
    with patch.object(ws, "MOODLE_URL", ""), patch.object(ws, "MOODLE_TOKEN", ""):
        assert ws.configured() is False


def test_configured_true_when_set() -> None:
    with patch.object(ws, "MOODLE_URL", "https://m.example.com"), \
         patch.object(ws, "MOODLE_TOKEN", "tok"):
        assert ws.configured() is True
