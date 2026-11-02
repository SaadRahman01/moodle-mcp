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


# ---- list_upcoming_deadlines ----

_NOW = 1_800_000_000


def _event(eid: int, name: str, timesort: int, **extra: object) -> dict:
    ev = {
        "id": eid,
        "name": f"{name} is due",
        "activityname": name,
        "modulename": "assign",
        "course": {"fullname": "Cálculo I | Turma A", "shortname": "CALC1"},
        "timesort": timesort,
        "url": f"https://moodle.example.com/mod/assign/view.php?id={eid}",
        "action": {"name": "Add submission", "actionable": True},
    }
    ev.update(extra)
    return ev


@pytest.mark.asyncio
@respx.mock
async def test_upcoming_deadlines_formats_and_sorts() -> None:
    from moodle_mcp.docs import MoodleDocs
    from moodle_mcp.tools import tool_upcoming_deadlines

    with patch.object(ws, "MOODLE_URL", "https://moodle.example.com"), \
         patch.object(ws, "MOODLE_TOKEN", "abc"), \
         patch.object(ws, "_is_private_ip", return_value=False):
        route = respx.post("https://moodle.example.com/webservice/rest/server.php").mock(
            return_value=httpx.Response(200, json={"events": [
                _event(2, "Lista 2", _NOW + 86400),
                _event(1, "Lista 1", _NOW - 3600, overdue=True),
            ]})
        )
        docs = MoodleDocs()
        await docs.__aenter__()
        try:
            out = await tool_upcoming_deadlines(docs, days=10, overdue_days=2, now=_NOW)
        finally:
            await docs.aclose()

    sent = dict(httpx.QueryParams(route.calls.last.request.content.decode()))
    assert sent["wsfunction"] == "core_calendar_get_action_events_by_timesort"
    assert sent["timesortfrom"] == str(_NOW - 2 * 86400)
    assert sent["timesortto"] == str(_NOW + 10 * 86400)

    names = [d["name"] for d in out["data"]["deadlines"]]
    assert names == ["Lista 1", "Lista 2"]
    assert out["data"]["deadlines"][0]["overdue"] is True
    md = out["markdown"]
    assert "**overdue**" in md
    assert "Cálculo I \\| Turma A" in md
    assert "[Lista 2](https://moodle.example.com/mod/assign/view.php?id=2)" in md


@pytest.mark.asyncio
@respx.mock
async def test_upcoming_deadlines_empty() -> None:
    from moodle_mcp.docs import MoodleDocs
    from moodle_mcp.tools import tool_upcoming_deadlines

    with patch.object(ws, "MOODLE_URL", "https://moodle.example.com"), \
         patch.object(ws, "MOODLE_TOKEN", "abc"), \
         patch.object(ws, "_is_private_ip", return_value=False):
        respx.post("https://moodle.example.com/webservice/rest/server.php").mock(
            return_value=httpx.Response(200, json={"events": []})
        )
        docs = MoodleDocs()
        await docs.__aenter__()
        try:
            out = await tool_upcoming_deadlines(docs, now=_NOW)
        finally:
            await docs.aclose()
    assert out["data"]["deadlines"] == []
    assert "No pending deadlines" in out["markdown"]


@pytest.mark.asyncio
@respx.mock
async def test_upcoming_deadlines_pages_with_aftereventid() -> None:
    from moodle_mcp.docs import MoodleDocs
    from moodle_mcp.tools import tool_upcoming_deadlines

    first = [_event(i, f"Item {i}", _NOW + i) for i in range(1, 51)]
    second = [_event(i, f"Item {i}", _NOW + i) for i in range(51, 61)]
    with patch.object(ws, "MOODLE_URL", "https://moodle.example.com"), \
         patch.object(ws, "MOODLE_TOKEN", "abc"), \
         patch.object(ws, "_is_private_ip", return_value=False):
        route = respx.post("https://moodle.example.com/webservice/rest/server.php").mock(
            side_effect=[
                httpx.Response(200, json={"events": first, "lastid": 50}),
                httpx.Response(200, json={"events": second, "lastid": 60}),
            ]
        )
        docs = MoodleDocs()
        await docs.__aenter__()
        try:
            out = await tool_upcoming_deadlines(docs, limit=100, now=_NOW)
        finally:
            await docs.aclose()

    assert route.call_count == 2
    second_req = dict(httpx.QueryParams(route.calls[1].request.content.decode()))
    assert second_req["aftereventid"] == "50"
    assert second_req["limitnum"] == "50"
    assert len(out["data"]["deadlines"]) == 60
    assert out["data"]["truncated"] is False


@pytest.mark.asyncio
async def test_upcoming_deadlines_unconfigured() -> None:
    from moodle_mcp.docs import MoodleDocs
    from moodle_mcp.tools import tool_upcoming_deadlines

    with patch.object(ws, "MOODLE_URL", ""), patch.object(ws, "MOODLE_TOKEN", ""):
        out = await tool_upcoming_deadlines(MoodleDocs())
    assert out["data"]["error"] == "not-configured"


def test_upcoming_deadlines_tool_is_read_only() -> None:
    from moodle_mcp.server import TOOLS

    tool = next(t for t in TOOLS if t.name == "list_upcoming_deadlines")
    if tool.annotations is None:
        pytest.skip("ToolAnnotations not in this mcp SDK")
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False
