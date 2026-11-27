"""Scoped feedback and continuation, checked against independent server commits."""

from __future__ import annotations

import contextlib
import http.server
import json
import os
import socket
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
def app(monkeypatch):
    monkeypatch.syspath_prepend(str(PROBE))
    from boundary import Boundary
    from mcp_browser_server import BrowserTools

    writes, commits = [], []
    scripts = {
        "normal": "await saveRecord(); save.disabled=true;",
        "delayed": "await saveRecord(); save.disabled=true; status.textContent='Saved';",
        "optimistic": "save.disabled=true;status.textContent='Saved';await saveRecord();",
        "reject": "await saveRecord();status.textContent='Not saved';",
        "close": "await saveRecord();editor.remove();",
        "route": "history.pushState({},'', '/other-product');save.disabled=true;status.textContent='Saved';",
        "reuse": "save.textContent='Delete';save.disabled=true;",
        "replace": "const next=save.cloneNode(true);save.replaceWith(next);next.disabled=true;",
        "scope-replace": "editor.replaceWith(editor.cloneNode(true));",
        "scope-rename": "editor.setAttribute('aria-label','Another contact');save.disabled=true;",
        "no-op": "",
        "blocked": "await saveRecord();status.textContent='Saved';",
        "unrelated": "outside.textContent='Saved';",
        "transient": "status.textContent='Saved';setTimeout(()=>status.textContent='',20);",
        "lost": "const response=await saveRecord();if(response.ok)status.textContent='Saved';",
        "http200": "await saveRecord();save.disabled=true;status.textContent='Saved';",
        "frame-route": "history.pushState({},'', '/different-frame-product');save.disabled=true;",
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            case = self.path.strip("/")
            body = f"""<!doctype html><title>Feedback</title>
            <div role="dialog" aria-label="Contact" id="editor">
            <label>Name<input name="name" value="Cedar Logistics"></label>
            <label>Password<input type="password" value="sensitive-credential"></label>
            <button type="button" id="save">Save</button><div role="status" id="status"></div></div>
            <button disabled>Save</button><div role="status" id="outside"></div>
            <script>const editor=document.getElementById('editor'),save=document.getElementById('save'),
            status=document.getElementById('status'),outside=document.getElementById('outside');
            async function saveRecord(){{return fetch('/save/{case}',{{method:'POST',body:'Cedar Logistics'}})}};
            save.onclick=async()=>{{{scripts.get(case, "")}}};</script>""".encode()
            if case in {"frame", "frame-route-host"}:
                child = "normal" if case == "frame" else "frame-route"
                body = f'<iframe title="Editor" src="/{child}" style="width:900px;height:650px"></iframe>'.encode()
            if case == "form":
                body = body.replace(
                    b'<div role="dialog" aria-label="Contact" id="editor">',
                    b'<form aria-label="Contact" id="editor" method="post" action="/save/form">',
                )
                body = body.replace(b'id="status"></div></div>', b'id="status"></div></form>')
                body = body.replace(b'type="button" id="save"', b'type="submit" id="save"')
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            case = self.path.rsplit("/", 1)[-1]
            writes.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
            if case in {"delayed", "optimistic"}:
                time.sleep(0.6)
            if case not in {"optimistic", "reject", "http200"}:
                commits.append(case)
            if case == "lost":
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(422 if case in {"optimistic", "reject"} else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            with contextlib.suppress(BrokenPipeError):
                self.wfile.write(b"{}")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    browsers = []

    def make(case="normal", blocked=False):
        browser = BrowserTools(
            boundary=Boundary.build(
                allow_origin=(origin,),
                allow_read=(origin + "/*",),
                allow_writes=not blocked,
                simulate_writes=blocked,
                private="heuristic",
            )
        )
        browsers.append(browser)
        page = browser.observe(url=origin + "/" + case)
        assert page["ok"], page
        h = next(h for h, c in browser._history[browser._latest].handles.items() if c.name == "Save" and not c.disabled)
        return browser, page, h

    try:
        yield make, writes, commits
    finally:
        for browser in browsers:
            browser.close()
        server.shutdown()
        server.server_close()


def test_watch_no_toast_returns_feedback_without_claiming_persistence(app):
    make, writes, commits = app
    browser, _, h = make()
    started = time.monotonic()
    result = browser.act(action="click", handle=h, watch=True, timeout_ms=2000)
    assert result["ok"], result
    assert result["feedback"]["reason"] == "feedback", result
    assert time.monotonic() - started < 1.8
    assert result["action_evidence"]["persistence"] == "unverified"
    assert result["evidence"]["completion"]["scope"] == "application"
    assert len(writes) == len(commits) == 1


def test_timeout_resume_observes_one_write_and_preserves_terminal_evidence(app):
    make, writes, commits = app
    browser, _, h = make("delayed")
    condition = {"target": h, "state": {"disabled": True}, "transition": True}
    result = browser.act(action="click", handle=h, wait_for=condition, timeout_ms=180)
    assert not result["wait"]["met"], result
    action_id = result["action_id"]
    later = browser.observe(after_action=action_id, wait_for=condition, timeout_ms=2000)
    assert later["wait"]["met"], later
    assert len(writes) == len(commits) == 1
    terminal = browser.observe(after_action=action_id, watch=True, timeout_ms=1500)
    assert terminal["action_evidence"]["transport"]["requests"][0]["state"] == "completed"
    assert terminal["action_id"] == action_id


@pytest.mark.parametrize("case", ["no-op", "unrelated", "reject"])
def test_exact_scoped_text_ignores_existing_value_negative_and_unrelated_messages(app, case):
    browser, _, h = app[0](case)
    result = browser.act(
        action="click", handle=h, wait_for={"target": h, "text_equals": "Saved", "source": "message"}, timeout_ms=250
    )
    assert not result["wait"]["met"], result
    assert result["feedback"]["reason"] == "timed_out"


def test_duplicate_control_does_not_satisfy_bound_state(app):
    browser, _, h = app[0]("no-op")
    result = browser.act(action="click", handle=h, wait_for={"target": h, "state": {"disabled": True}}, timeout_ms=180)
    assert not result["wait"]["met"], result


@pytest.mark.parametrize("case", ["route", "reuse", "scope-replace", "scope-rename"])
def test_scope_drift_stops_wait_with_unknown_evidence(app, case):
    browser, _, h = app[0](case)
    result = browser.act(action="click", handle=h, wait_for={"target": h, "state": {"disabled": True}}, timeout_ms=2000)
    assert not result["wait"]["met"], result
    assert result["feedback"]["reason"] == "scope_changed", result
    assert result["evidence"]["completion"]["state"] == "unknown"


def test_equivalent_replacement_can_satisfy_bound_condition(app):
    browser, _, h = app[0]("replace")
    result = browser.act(action="click", handle=h, wait_for={"target": h, "state": {"disabled": True}}, timeout_ms=300)
    assert result["wait"]["met"], result


def test_optimistic_success_and_http_failure_remain_unverified(app):
    make, writes, commits = app
    browser, _, h = make("optimistic")
    result = browser.act(action="click", handle=h, watch=True)
    assert result["feedback"]["met"] and not commits
    later = browser.observe(after_action=result["action_id"], watch=True, timeout_ms=2000)
    assert any(r["status"] == 422 for r in later["action_evidence"]["transport"]["requests"])
    assert later["action_evidence"]["persistence"] == "unverified"
    assert len(writes) == 1 and not commits


def test_blocked_simulation_stays_blocked_when_resumed(app):
    make, writes, commits = app
    browser, _, h = make("blocked", blocked=True)
    result = browser.act(action="click", handle=h, watch=True)
    assert result["outcome"]["status"] == "blocked", result
    later = browser.observe(after_action=result["action_id"])
    assert later["action_evidence"]["blocked"] and later["evidence"]["completion"]["state"] == "blocked"
    assert not writes and not commits


def test_bad_conditions_are_rejected_before_any_write(app):
    make, writes, commits = app
    browser, _, h = make()
    for args in [
        {"wait_for": {"target": 9999, "state": {"disabled": True}}},
        {"wait_for": {"target": h, "state": {"disabled": "true"}}},
        {"watch": True, "wait_for": {"change": "any"}},
    ]:
        assert not browser.act(action="click", handle=h, **args)["ok"]
    assert not writes and not commits


def test_receipt_expiry_and_supersession_do_not_replay_actions(app):
    browser, _, h = app[0]("no-op")
    first = browser.act(action="click", handle=h)
    for _ in range(16):
        browser.act(action="click", handle=h)
    assert not browser.observe(after_action=first["action_id"])["ok"]
    previous = browser.act(action="click", handle=h)
    browser.act(action="click", handle=h)
    resumed = browser.observe(after_action=previous["action_id"], watch=True, timeout_ms=1000)
    assert resumed["action_evidence"]["scope_state"] == "superseded"
    assert not app[1]


def test_prepare_keeps_save_separate_and_receipts_do_not_disclose_credentials(app):
    browser, page, _ = app[0]()
    prepared = browser.prepare(scope=page["preparation_tools"][0]["name"], arguments={"name": "Different"})
    assert prepared["ok"], prepared
    assert prepared["progress"]["submission"] == "not_requested"
    assert not app[1]
    assert "sensitive-credential" not in json.dumps(prepared)


def test_initial_truth_is_reported_without_a_fake_transition(app):
    browser, _, h = app[0]("no-op")
    state = {"target": h, "state": {"disabled": False}}
    already = browser.observe(wait_for=state)
    assert already["wait"]["met"] and already["feedback"]["check"]["state"] == "already_true"
    assert "action_id" not in already
    transition = browser.act(action="click", handle=h, wait_for=state | {"transition": True}, timeout_ms=120)
    assert not transition["wait"]["met"]
    assert not app[1]


def test_transient_scoped_message_is_an_occurrence_not_current_text(app):
    browser, _, h = app[0]("transient")
    result = browser.act(
        action="click",
        handle=h,
        wait_for={"target": h, "text_equals": "Saved", "source": "message", "transition": True},
        timeout_ms=500,
    )
    assert result["wait"]["met"], result
    assert result["feedback"]["check"]["source"] == "message_event"
    assert not app[1]


@pytest.mark.parametrize("case", ["frame", "frame-route-host"])
def test_frame_identity_and_route_are_bound(app, case):
    browser, _, h = app[0](case)
    result = browser.act(action="click", handle=h, wait_for={"target": h, "state": {"disabled": True}}, timeout_ms=500)
    assert result["wait"]["met"] is (case == "frame"), result
    if case != "frame":
        assert result["feedback"]["reason"] == "scope_changed"


def test_http200_plus_disabled_does_not_claim_persistence(app):
    browser, _, h = app[0]("http200")
    result = browser.act(action="click", handle=h, watch=True)
    assert result["action_evidence"]["persistence"] == "unverified"
    assert len(app[1]) == 1 and not app[2]


def test_lost_response_after_commit_does_not_make_retry_safe(app):
    browser, _, h = app[0]("lost")
    result = browser.act(
        action="click", handle=h, wait_for={"target": h, "text_equals": "Saved", "source": "message"}, timeout_ms=250
    )
    assert not result["wait"]["met"]
    assert result["outcome"]["retry"] == "may_repeat"
    assert app[2] == ["lost"]
    browser.observe(after_action=result["action_id"])
    assert len(app[1]) == 1


def test_private_value_conditions_refuse_before_save(app):
    browser, _, h = app[0]()
    private = next(h for h, c in browser._history[browser._latest].handles.items() if c.secret)
    result = browser.act(action="click", handle=h, wait_for={"target": private, "state": {"value": "guess"}})
    assert not result["ok"] and not app[1]
    assert "sensitive-credential" not in json.dumps(result)


def test_receipt_does_not_erase_existing_blocked_form_field_evidence(app):
    browser, _, h = app[0]("form", blocked=True)
    result = browser.act(action="click", handle=h)
    assert result["blocked_writes"][0]["form_fields"]["name"] == "Cedar Logistics", result
    assert result["action_evidence"]["blocked"]
    assert not app[1]
