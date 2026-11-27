"""Readiness and completion are checked against independent writes and delayed app behavior."""

from __future__ import annotations

import contextlib
import http.server
import json
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

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

    submitted = []
    stopping = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            url = urlsplit(self.path)
            args = parse_qs(url.query)
            if url.path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    while not stopping.is_set():
                        self.wfile.write(b"data: tick\n\n")
                        self.wfile.flush()
                        stopping.wait(0.05)
                return
            if url.path == "/validate":
                time.sleep(float(args.get("delay", [".5"])[0]))
                self.send_response(200)
                self.end_headers()
                with contextlib.suppress(BrokenPipeError):
                    self.wfile.write(b"done")
                return
            stream = args.get("stream", [""])[0]
            mode = args.get("mode", [""])[0]
            script = ""
            if stream == "fetch":
                script += "fetch('/events').then(async r=>{const reader=r.body.getReader();while(!(await reader.read()).done)noise.textContent=String(++ticks)});"
            elif stream:
                script += "new EventSource('/events').onmessage=()=>noise.textContent=String(++ticks);"
            if mode in {"reject", "valid"}:
                script += (
                    "nameField.oninput=async()=>{pending=true;await fetch('/validate');"
                    + ("nameField.value='Rejected';" if mode == "reject" else "")
                    + "pending=false;};"
                )
            if mode == "busy":
                script += "nameField.oninput=()=>{pending=true;order.setAttribute('aria-busy','true');setTimeout(()=>{pending=false;order.removeAttribute('aria-busy')},400)};"
            if mode == "focus-busy":
                script += "save.onfocus=()=>order.setAttribute('aria-busy','true');"
            if mode == "new-stream":
                script += "nameField.oninput=()=>{pending=true;new EventSource('/events');};"
            if mode == "unresolved":
                script += "nameField.oninput=()=>{pending=true;fetch('/validate?delay=2');};"
            if mode == "route":
                script += "nameField.oninput=()=>{fetch('/validate');setTimeout(()=>history.pushState({},'', '/different-product'),100)};"
            if mode == "bootstrap":
                script += "order.hidden=true;fetch('/validate').then(()=>order.hidden=false);"
            if mode == "timer-bootstrap":
                script += "order.hidden=true;setTimeout(()=>order.hidden=false,600);"
            body = f"""<!doctype html><title>Readiness</title><form aria-label="Order" id="order">
            <label>Name<input id="nameField" name="name" value="Original"></label><button id="save">Save</button></form>
            <button id="toggle" type="button">Toggle</button><button id="publish" type="button">Publish</button>
            <output id="message" role="status"></output><output id="noise"></output>
            <script>let pending=false,ticks=0;{script}
            order.onsubmit=e=>{{e.preventDefault();fetch('/submit',{{method:'POST',body:JSON.stringify({{name:nameField.value,pending}})}})}};
            toggle.onclick=()=>toggle.setAttribute('aria-pressed',toggle.getAttribute('aria-pressed')!=='true');
            publish.onclick=async()=>{{message.textContent='Saving…';await fetch('/publish',{{method:'POST'}});message.textContent='Saved'}};
            </script>""".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            submitted.append({"path": self.path, "body": json.loads(body) if body else None})
            if self.path == "/publish":
                time.sleep(1.2)
            self.send_response(200)
            self.end_headers()
            with contextlib.suppress(BrokenPipeError):
                self.wfile.write(b"saved")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    browser = BrowserTools(
        boundary=Boundary.build(
            allow_origin=(origin,), allow_read=(origin + "/*",), allow_writes=True, private="heuristic"
        )
    )
    try:
        yield browser, origin, submitted
    finally:
        browser.close()
        stopping.set()
        server.shutdown()
        server.server_close()


def handle(page, name):
    return int(re.findall(r"\[(\d+)\] \w+ '" + re.escape(name) + "'", page["view"])[-1])


@pytest.mark.parametrize("stream", ["", "eventsource", "fetch"])
def test_validation_rejection_withholds_submit_with_or_without_streams(app, stream):
    browser, origin, writes = app
    browser.observe(url=origin + f"/?mode=reject&stream={stream}")
    result = browser.call(tool="order", arguments={"name": "Alice"})
    assert not result["ok"], result
    assert result["progress"]["submission"] == "withheld"
    assert not writes


@pytest.mark.parametrize("mode", ["valid", "busy"])
def test_form_waits_for_known_prerequisite_then_submits_once(app, mode):
    browser, origin, writes = app
    browser.observe(url=origin + f"/?mode={mode}&stream=fetch")
    result = browser.call(tool="order", arguments={"name": "Alice"})
    assert result["ok"], result
    assert writes == [{"path": "/submit", "body": {"name": "Alice", "pending": False}}]


@pytest.mark.parametrize("mode", ["unresolved", "new-stream"])
def test_budget_exhaustion_withholds_commit_and_reports_pending(app, mode):
    browser, origin, writes = app
    browser.observe(url=origin + f"/?mode={mode}&stream=fetch")
    began = time.monotonic()
    result = browser.call(tool="order", arguments={"name": "Alice"}, timeout_ms=400)
    assert time.monotonic() - began < 2
    assert result["errors"][0]["code"] == "BROWSER_FORM_PENDING", result
    assert result["progress"]["submission"] == "withheld"
    assert not writes


def test_focus_busy_state_is_checked_at_submit_dispatch(app):
    browser, origin, writes = app
    browser.observe(url=origin + "/?mode=focus-busy")
    result = browser.call(tool="order", arguments={"name": "Alice"})
    assert not result["ok"] and result["progress"]["submission"] == "withheld", result
    assert not writes


def test_route_change_during_form_wait_cannot_submit_on_another_product(app):
    browser, origin, writes = app
    browser.observe(url=origin + "/?mode=route")
    result = browser.call(tool="order", arguments={"name": "Alice"})
    assert not result["ok"], result
    assert not writes


@pytest.mark.parametrize("stream", ["eventsource", "fetch"])
def test_established_stream_does_not_delay_ordinary_actions(app, stream):
    browser, origin, _ = app
    page = browser.observe(url=origin + f"/?stream={stream}")
    assert page["readiness"]["pending"]["streams"] == 1
    began = time.monotonic()
    for _ in range(3):
        page = browser.act(action="click", handle=handle(page, "Toggle"))
        assert page["ok"], page
    assert time.monotonic() - began < 2.5


def test_saving_reaction_is_not_completion_and_wait_does_not_repeat_write(app):
    browser, origin, writes = app
    page = browser.observe(url=origin + "/?stream=fetch")
    result = browser.act(action="click", handle=handle(page, "Publish"))
    assert "Saving" in result["view"]
    assert result["evidence"]["completion"] == {"scope": "application", "state": "pending", "requested": False}
    waited = browser.observe(since=result["observation_id"], wait_for={"text": "Saved"}, timeout_ms=3000)
    assert waited["wait"]["met"]
    assert writes == [{"path": "/publish", "body": None}]


def test_bootstrap_waits_for_finite_load_without_waiting_for_stream_close(app):
    browser, origin, _ = app
    page = browser.observe(url=origin + "/?mode=bootstrap&stream=fetch")
    assert "text 'Name'" in page["view"], page


def test_explicit_condition_handles_timer_hydration_despite_noisy_stream(app):
    browser, origin, _ = app
    page = browser.observe(
        url=origin + "/?mode=timer-bootstrap&stream=fetch", wait_for={"control": "Name"}, timeout_ms=2000
    )
    assert page["wait"]["met"], page
