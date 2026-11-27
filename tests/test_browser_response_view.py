"""Executable bounded-result contracts; no browser or model required."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/browser-agent-probe"))
from boundary import Boundary
from mcp_browser_server import BrowserTools, build_server
from response_view import compact_json, project, qualified, response_bytes


def canonical():
    return {
        "schema_version": 1,
        "ok": False,
        "summary": "A prior field was applied; Save was blocked.",
        "observation_id": 7,
        "view": "[1] text 'Name' = 'Cedar'\n[2] button 'Save'",
        "preparation_tools": [{"name": "contact", "input_schema": {"properties": {"name": {"type": "string"}}}}],
        "outcome": {"status": "blocked", "retry": "may_repeat", "reason": "Policy blocked Save."},
        "evidence": {
            "execution": "dispatched",
            "control_verification": "mismatch",
            "completion": {"scope": "transport", "state": "blocked"},
        },
        "progress": {
            "submission": "attempted",
            "fields": [{"name": "Name", "status": "verified"}, {"name": "Notes", "status": "failed"}],
            "contract": [{"index": 1, "status": "mismatch"}],
        },
        "action_id": "action_test",
        "action_evidence": {
            "action_id": "action_test",
            "source_observation_id": 6,
            "execution": "dispatched",
            "scope_state": "observed",
            "persistence": "unverified",
            "blocked": True,
            "transport": {"association": "temporal", "truncated": False, "requests": []},
            "writes": [{"state": "blocked-and-simulated"}],
            "signals": [{"kind": "message"}],
        },
        "blocked_writes": [{"url": "https://example.test/save", "state": "blocked-and-simulated"}],
        "errors": [{"code": "BROWSER_FORM_MISMATCH", "message": "Notes did not match."}],
    }


@pytest.mark.parametrize("mode", ["full", "focused", "receipt"])
@pytest.mark.parametrize("budget", [4096, 12288, 65536])
def test_whole_reply_budget_preserves_disposition_and_partial_field_evidence(mode, budget):
    source = canonical()
    source["view"] += "\n" + "text " + '🙂\\"' * 20000
    source["preparation_tools"][0]["input_schema"]["properties"].update(
        {f"field_{i}": {"type": "string", "description": "世界" * 300, "default": "x" * 200} for i in range(100)}
    )
    source["action_evidence"]["transport"]["requests"] = [
        {"state": "completed", "status": 200, "url": "https://example.test/" + "x" * 500} for _ in range(512)
    ]
    source["blocked_writes"] *= 300
    source["errors"][0]["message"] *= 3000
    before = copy.deepcopy(source)
    result = project(source, mode=mode, budget=budget, result_id="result_test")
    assert response_bytes(result) <= budget
    assert source == before
    assert result["outcome"]["status"] == "blocked"
    assert result["outcome"]["retry"] == "may_repeat"
    assert result["evidence"]["control_verification"] == "mismatch"
    assert result["evidence"]["completion"]["state"] == "blocked"
    assert result["action_evidence"]["persistence"] == "unverified"
    assert result["action_evidence"]["blocked"]
    assert result["policy_events"]["blocked_writes"] == 300
    assert "failed" in compact_json(result["progress"])
    assert "mismatch" in compact_json(result["progress"])
    assert result["presentation"]["omitted"]


def test_small_focused_reply_keeps_complete_schema_and_qualified_addresses():
    source = canonical()
    source["form_tools"] = [{"name": "form", "submitters": [2], "input_schema": {"type": "object"}}]
    result = project(source, mode="focused", budget=12288, result_id="result_test")
    assert result["view"].startswith("[7:1]")
    assert result["preparation_tools"][0]["input_schema"] == source["preparation_tools"][0]["input_schema"]
    assert result["preparation_tools"][0]["observation_id"] == 7
    assert result["form_tools"][0]["submitters"] == ["7:2"]
    assert qualified(result) == result


def test_receipt_omits_snapshot_but_never_changes_execution_or_persistence():
    source = canonical()
    result = project(source, mode="receipt", budget=12288, result_id="result_test")
    assert "view" not in result and "preparation_tools" not in result
    assert result["evidence"] == source["evidence"]
    assert result["action_evidence"]["execution"] == "dispatched"
    assert result["action_evidence"]["writes"]["states"] == {"blocked-and-simulated": 1}


def test_huge_wait_predicate_cannot_escape_budget_or_upgrade_completion():
    source = canonical()
    source["evidence"]["completion"] = {
        "scope": "ui_condition",
        "state": "unknown",
        "condition": {"text_equals": "z" * 30000},
    }
    source["wait"] = {
        "met": False,
        "reason": "scope_changed",
        "condition": source["evidence"]["completion"]["condition"],
    }
    result = project(source, mode="full", budget=4096, result_id="result_test")
    assert response_bytes(result) <= 4096
    assert result["evidence"]["completion"]["state"] == "unknown"


def make_tools(**kwargs):
    return BrowserTools(boundary=Boundary.build(private="heuristic"), response_mode="focused", **kwargs)


def read_all(tools, result_id, section=None):
    offset = 0
    revision = None
    pieces = []
    while True:
        request = {"result_id": result_id, "offset": offset}
        if revision:
            request["revision"] = revision
        if section:
            request["section"] = section
        reply = tools.observe(details=request)
        assert reply["ok"], reply
        assert response_bytes(reply) <= tools.response_bytes
        pieces.append(reply["detail"]["text"])
        revision = reply["detail"]["revision"]
        offset = reply["detail"]["next_offset"]
        if offset is None:
            break
    return json.loads("".join(pieces))


def test_detail_paging_recovers_a_large_unicode_leaf_without_new_observations():
    tools = make_tools(response_bytes=4096)
    source = canonical()
    source["preparation_tools"][0]["input_schema"]["properties"]["name"]["description"] = '🙂"\\多' * 3000
    reply = tools._present(source, {})
    expected = qualified(source)
    assert read_all(tools, reply["result_id"]) == expected
    assert read_all(tools, reply["result_id"], "preparation_tools") == expected["preparation_tools"]
    assert tools._latest == 0 and tools._session is None


def test_expansion_reapplies_newly_learned_privacy_and_retains_unknown_state():
    tools = make_tools()
    source = {
        "ok": True,
        "summary": "opaque-credential-129",
        "form_tools": [{"name": "account", "input_schema": {"properties": {"password": {"x-value-state": "unknown"}}}}],
    }
    reply = tools._present(source, {})
    tools.privacy.remember(["opaque-credential-129"])
    detail = read_all(tools, reply["result_id"])
    assert "opaque-credential-129" not in compact_json(detail)
    assert detail["form_tools"][0]["input_schema"]["properties"]["password"]["x-value-state"] == "unknown"


def test_expired_details_never_replay_or_read_the_browser():
    tools = make_tools()
    first = tools._present(canonical(), {})["result_id"]
    for _ in range(16):
        tools._present(canonical(), {})
    result = tools.observe(details={"result_id": first})
    assert result["errors"][0]["code"] == "BROWSER_RESULT_UNKNOWN"
    assert tools._session is None


def test_changed_redaction_invalidates_an_in_progress_detail_cursor():
    tools = make_tools(response_bytes=4096)
    result_id = tools._present({"ok": True, "summary": "prefix " * 1000 + "newly-known-credential"}, {})["result_id"]
    first = tools.observe(details={"result_id": result_id})["detail"]
    assert first["next_offset"]
    tools.privacy.remember(["newly-known-credential"])
    changed = tools.observe(
        details={"result_id": result_id, "offset": first["next_offset"], "revision": first["revision"]}
    )
    assert changed["errors"][0]["code"] == "BROWSER_RESULT_CHANGED"
    assert "newly-known-credential" not in compact_json(read_all(tools, result_id))
    assert tools._latest == 0 and tools._session is None


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"handle": 1}, "BROWSER_OBSERVATION_REQUIRED"),
        ({"handle": "1:1", "observation_id": 2}, "BROWSER_OBSERVATION_CONFLICT"),
        ({"handle": "1:1", "wait_for": {"target": "2:2", "state": {"disabled": True}}}, "BROWSER_OBSERVATION_CONFLICT"),
        ({"handle": "bad"}, "BROWSER_HANDLE_INVALID"),
        ({"handle": "1:1", "response": "receipt"}, "BROWSER_INVALID_REQUEST"),
    ],
)
def test_ambiguous_or_conflicting_addresses_refused_before_browser_start(kwargs, code):
    tools = make_tools()
    result = tools.act(action="click", **kwargs)
    assert not result["ok"]
    assert result["errors"][0]["code"] == code
    assert result["evidence"]["execution"] == "not_dispatched"
    assert tools._session is None


def test_form_calls_require_their_observation_and_details_cannot_navigate():
    tools = make_tools()
    for result in (tools.prepare(scope="contact", arguments={}), tools.call(tool="contact", arguments={})):
        assert result["errors"][0]["code"] == "BROWSER_OBSERVATION_REQUIRED"
    result = tools.observe(details={"result_id": "missing"}, url="https://example.test")
    assert result["errors"][0]["code"] == "BROWSER_INVALID_REQUEST"
    assert tools._session is None


def test_mcp_transport_carries_equal_compact_text_and_structured_payloads():
    server = build_server(make_tools(response_bytes=4096))
    result = asyncio.run(server.call_tool("act", {"action": "click", "handle": 1}))
    assert result.structuredContent == json.loads(result.content[0].text)
    assert result.content[0].text == compact_json(result.structuredContent)
    assert response_bytes(result.structuredContent) <= 4096
    schemas = {t.name: t.inputSchema for t in asyncio.run(server.list_tools())}
    assert "string" in compact_json(schemas["act"]["properties"]["handle"])
    assert "details" in schemas["observe"]["properties"]


def test_mcp_detail_expansion_accepts_transport_supplied_default_arguments():
    tools = make_tools()
    result_id = tools._present(canonical(), {})["result_id"]
    server = build_server(tools)
    reply = asyncio.run(server.call_tool("observe", {"details": {"result_id": result_id, "section": "summary"}}))
    assert reply.structuredContent["ok"], reply
    assert json.loads(reply.structuredContent["detail"]["text"]) == canonical()["summary"]
    assert tools._session is None


def test_mcp_argument_validation_is_bounded_and_does_not_echo_private_input():
    tools = make_tools(response_bytes=4096)
    server = build_server(tools)
    reply = asyncio.run(server.call_tool("act", {"action": "click", "handle": {"secret": "private-input-" * 10000}}))
    assert reply.isError
    value = reply.structuredContent
    assert value["evidence"]["execution"] == "not_dispatched"
    assert "private-input-" not in compact_json(value)
    assert response_bytes(value) <= 4096
    assert tools._session is None


def test_compact_replies_preserve_dialog_download_and_validation_events():
    source = canonical()
    source["steps"] = [
        {
            "dialogs": [{"type": "confirm", "answer": "dismissed", "message": "Confirm?"}],
            "unsaved_downloads": ["invoice.pdf"],
            "invalid": [{"name": "Email", "message": "Invalid email"}],
        }
    ]
    reply = project(source, mode="focused", budget=12288, result_id="result_test")
    assert reply["step_events"][0]["unsaved_downloads"] == ["invoice.pdf"]
    assert reply["event_counts"] == {"dialogs": 1, "unsaved_downloads": 1, "invalid": 1}
    source["steps"][0]["dialogs"][0]["message"] = "x" * 50000
    reply = project(source, mode="focused", budget=4096, result_id="result_test")
    assert response_bytes(reply) <= 4096
    assert reply["event_counts"]["dialogs"] == 1
