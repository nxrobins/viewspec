"""Bounded presentations and qualified identities exercised against a real page/server."""

from __future__ import annotations

import http.server
import json
import os
import re
import threading
import time
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parents[1] / "experiments/browser-agent-probe"
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("VIEWSPEC_RUN_BROWSER_BRIDGE_E2E") != "1", reason="opt-in browser checks"),
]


@pytest.fixture
def site(monkeypatch):
    monkeypatch.syspath_prepend(str(PROBE))
    from boundary import Boundary
    from mcp_browser_server import BrowserTools

    writes = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            case = self.path.strip("/")
            if case == "frame":
                body = '<iframe title="Contact editor" src="/route" style="width:1000px;height:700px"></iframe>'
            elif case == "insert":
                body = """<button id="insert">Insert control</button><button id="right">Target</button>
                <script>insert.onclick=()=>{const b=document.createElement('button');b.textContent='Wrong target';
                b.onclick=()=>fetch('/wrong',{method:'POST',body:'wrong'});right.before(b)};
                right.onclick=()=>fetch('/right',{method:'POST',body:'right'});</script>"""
            else:
                background = "".join(f"<button>Background {i}</button>" for i in range(50))
                extras = (
                    "".join(f'<label>Field {i}<input name="field_{i}" value="{"v" * 150}"></label>' for i in range(120))
                    if case == "large"
                    else ""
                )
                mutation = (
                    "history.pushState({},'', '/other-product');"
                    if case == "route"
                    else "const next=editor.cloneNode(true);editor.replaceWith(next);"
                    if case == "owner"
                    else "const label=document.createElement('label');label.textContent='Required later';const x=document.createElement('input');x.name='later';x.required=true;label.append(x);save.before(label);"
                )
                body = f"""<!doctype html><title>Contact</title>{background}
                <div role="alert">External validation: verify the account.</div>
                <div role="dialog" aria-modal="true" aria-label="Contact" id="editor" style="position:fixed;inset:0;background:white;overflow:auto;z-index:10">
                <label>Name<input name="name" value="Cedar"></label>
                <label>Notes<textarea name="notes">Original.</textarea></label>
                <label>Password<input type="password" value="private-credential-2468"></label>{extras}
                <button type="button" id="save">Save</button>
                <button type="button" id="change">Change scope</button>
                <button type="button" onclick="editor.remove()">Close</button><div role="status" id="status"></div></div>
                <script>const editor=document.getElementById('editor'),save=document.getElementById('save'),
                change=document.getElementById('change'),status=document.getElementById('status');
                save.onclick=async()=>{{await fetch('/save/{case}',{{method:'POST',body:location.pathname}});save.disabled=true;status.textContent='Saved';}};
                change.onclick=()=>{{{mutation}}};</script>"""
            raw = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            writes.append((self.path, body))
            if self.path.endswith("delayed"):
                time.sleep(0.4)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    opened = []

    def make(case="normal", *, blocked=False, budget=12288):
        tools = BrowserTools(
            boundary=Boundary.build(
                allow_origin=(origin,),
                allow_read=(origin + "/*",),
                allow_writes=not blocked,
                simulate_writes=blocked,
                private="heuristic",
            ),
            response_mode="focused",
            response_bytes=budget,
        )
        opened.append(tools)
        result = tools.observe(url=origin + "/" + case)
        assert result["ok"], result
        return tools, result

    try:
        yield make, writes
    finally:
        for tools in opened:
            tools.close()
        server.shutdown()
        server.server_close()


def address(reply, name):
    match = re.search(r"^\[([0-9]+:[0-9]+)\].*? '" + re.escape(name) + r"'(?: |$)", reply["view"], re.MULTILINE)
    assert match, reply
    return match[1]


def detail(tools, result, section=None):
    request = {"result_id": result["result_id"]}
    if section:
        request["section"] = section
    parts = []
    while True:
        response = tools.observe(details=request)
        assert response["ok"], response
        parts.append(response["detail"]["text"])
        offset = response["detail"]["next_offset"]
        if offset is None:
            return json.loads("".join(parts))
        request["offset"] = offset
        request["revision"] = response["detail"]["revision"]


def test_focused_dialog_keeps_messages_and_actions_with_expandable_background(site):
    tools, page = site[0]()
    assert "Background 0'" not in page["view"]
    assert "blocked 50 controls" in page["view"]
    assert "External validation" in page["view"]
    assert address(page, "Save") and address(page, "Close")
    assert page["view_scope"]["omitted_lines"] >= 50
    full = tools.observe(observation_id=page["observation_id"], response="full", limit=15)
    assert "Background 0'" in full["view"]
    assert full["observation_id"] == page["observation_id"] == tools._latest
    assert full["presentation"]["budget_bytes"] == 12288


def test_preparation_keeps_save_separate_and_retains_exact_progress(site):
    tools, page = site[0]()
    scope = page["preparation_tools"][0]
    result = tools.prepare(
        scope=scope["name"],
        observation_id=scope["observation_id"],
        arguments={"name": "Cedar Logistics", "notes": "Exact punctuation."},
    )
    assert result["ok"], result
    assert result["evidence"]["control_verification"] == "matched"
    assert site[1] == []
    original = detail(tools, result)
    assert original["progress"]["contract"]
    assert result["outcome"] == original["outcome"]
    saved = tools.act(action="click", handle=address(result, "Save"), watch=True)
    assert saved["ok"], saved
    assert len(site[1]) == 1
    assert saved["action_evidence"]["persistence"] == "unverified"


def test_receipt_resume_and_detail_paging_do_not_advance_observation_or_write_again(site):
    tools, page = site[0]("delayed")
    saved = tools.act(action="click", handle=address(page, "Save"), watch=True, timeout_ms=120)
    assert saved["ok"], saved
    observed = tools._latest
    resumed = tools.observe(after_action=saved["action_id"], watch=True, timeout_ms=2000)
    assert resumed["feedback"]["met"], resumed
    assert "view" not in resumed and "observation_id" not in resumed
    assert tools._latest == observed
    original = detail(tools, resumed, "action_evidence")
    for _ in range(3):
        if original["transport"]["requests"][0]["state"] == "completed":
            break
        resumed = tools.observe(after_action=saved["action_id"], watch=True, timeout_ms=1500)
        original = detail(tools, resumed, "action_evidence")
    assert original["transport"]["requests"][0]["state"] == "completed"
    assert tools._latest == observed
    assert len(site[1]) == 1
    fresh = tools.observe()
    assert fresh["observation_id"] == observed + 1


def test_simulated_save_cannot_be_upgraded_by_compact_or_expanded_evidence(site):
    tools, page = site[0](blocked=True)
    saved = tools.act(action="click", handle=address(page, "Save"), watch=True)
    assert saved["outcome"]["status"] == "blocked"
    assert saved["evidence"]["completion"]["state"] == "blocked"
    assert saved["policy_events"]["blocked_writes"] >= 1
    assert detail(tools, saved)["outcome"]["status"] == "blocked"
    resumed = tools.observe(after_action=saved["action_id"])
    assert resumed["action_evidence"]["blocked"]
    assert resumed["action_evidence"]["persistence"] == "unverified"
    assert site[1] == []


def test_inserted_control_cannot_retarget_an_old_numeric_handle(site):
    tools, page = site[0]("insert")
    original = address(page, "Target")
    inserted = tools.act(action="click", handle=address(page, "Insert control"))
    assert inserted["ok"], inserted
    assert address(inserted, "Wrong target").split(":")[1] == original.split(":")[1]
    refused = tools.act(action="click", handle=int(original.split(":")[1]))
    assert refused["errors"][0]["code"] == "BROWSER_OBSERVATION_REQUIRED"
    acted = tools.act(action="click", handle=original)
    assert acted["ok"] or acted["outcome"]["status"] in {"failed", "blocked"}
    assert not any(path == "/wrong" for path, _ in site[1])


@pytest.mark.parametrize("case", ["route", "owner", "frame"])
def test_old_qualified_handle_cannot_cross_route_owner_or_frame_identity(site, case):
    tools, page = site[0](case)
    old_save = address(page, "Save")
    changed = tools.act(action="click", handle=address(page, "Change scope"))
    assert changed["ok"], changed
    refused = tools.act(action="click", handle=old_save)
    assert not refused["ok"], refused
    assert refused["outcome"]["status"] == "failed"
    assert site[1] == []


def test_changed_field_membership_is_fresh_and_old_prepare_is_refused(site):
    tools, page = site[0]("dynamic")
    before = page["preparation_tools"][0]
    changed = tools.act(action="click", handle=address(page, "Change scope"))
    after = changed["preparation_tools"][0]
    assert "later" not in before["input_schema"]["properties"]
    assert "later" in after["input_schema"]["properties"]
    stale = tools.prepare(scope=before["name"], observation_id=before["observation_id"], arguments={"name": "Wrong"})
    assert not stale["ok"], stale
    current = tools.observe()
    assert "'Wrong'" not in current["view"]
    assert site[1] == []


def test_expired_observation_cannot_use_a_newer_control(site):
    tools, page = site[0]("insert")
    old = address(page, "Target")
    for _ in range(8):
        tools.observe()
    result = tools.act(action="click", handle=old)
    assert result["errors"][0]["code"] == "BROWSER_OBSERVATION_UNKNOWN"
    assert site[1] == []


def test_large_schema_is_bounded_expandable_and_secret_safe(site):
    from response_view import response_bytes

    tools, page = site[0]("large", budget=4096)
    assert response_bytes(page) <= 4096
    assert page["presentation"]["omitted"] or page["presentation"]["summarized"]
    original = detail(tools, page)
    assert len(original["preparation_tools"][0]["input_schema"]["properties"]) >= 120
    assert "private-credential-2468" not in json.dumps(original)
    assert "private-credential-2468" not in json.dumps(page)


def test_bounded_paging_and_find_keep_the_original_observation_namespace(site):
    tools, page = site[0]("large", budget=4096)
    observation_id = page["observation_id"]
    found = tools.observe(observation_id=observation_id, find="Notes", response="full")
    assert address(found, "Notes").startswith(f"{observation_id}:")
    retained = detail(tools, found, "preparation_tools")
    assert retained[0]["observation_id"] == observation_id
    assert "notes" in retained[0]["input_schema"]["properties"]
    paged = tools.observe(observation_id=observation_id, offset=20, limit=3, response="full")
    assert paged["observation_id"] == observation_id
    assert all(h.startswith(f"{observation_id}:") for h in re.findall(r"\[([0-9]+:[0-9]+)\]", paged["view"]))
    assert tools._latest == observation_id
    assert site[1] == []


def test_qualified_scoped_conditions_bind_the_original_observation(site):
    tools, page = site[0]()
    target = address(page, "Save")
    result = tools.act(
        action="click", handle=target, wait_for={"target": target, "state": {"disabled": True}}, timeout_ms=2000
    )
    assert result["wait"]["met"], result
    later = tools.observe(after_action=result["action_id"], wait_for={"target": target, "state": {"disabled": True}})
    assert later["wait"]["met"], later
    assert len(site[1]) == 1


def test_presentation_failure_after_save_never_marks_replay_safe(site, monkeypatch):
    import mcp_browser_server

    tools, page = site[0]()

    def unavailable(*args, **kwargs):
        raise RuntimeError("presentation unavailable")

    monkeypatch.setattr(mcp_browser_server, "project", unavailable)
    saved = tools.act(action="click", handle=address(page, "Save"), watch=True)
    assert saved["errors"][0]["code"] == "BROWSER_INTERNAL_ERROR"
    assert saved["outcome"]["retry"] == "may_repeat"
    assert saved["evidence"]["execution"] == "dispatched"
    assert len(site[1]) == 1
