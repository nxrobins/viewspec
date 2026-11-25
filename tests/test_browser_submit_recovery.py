"""Post-submit reads must not erase evidence or repeat a server-side write."""

from __future__ import annotations

import http.server
import os
import threading
import time
from pathlib import Path

import pytest

from viewspec.raw_html_actions import plan_control_action

PROBE = Path(__file__).resolve().parents[1] / "experiments/browser-agent-probe"
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("VIEWSPEC_RUN_BROWSER_BRIDGE_E2E") != "1",
        reason="opt-in browser checks",
    ),
]


@pytest.fixture
def app(monkeypatch):
    monkeypatch.syspath_prepend(str(PROBE))
    import agent_session
    import boundary
    import mcp_browser_server

    orders = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = (
                "<!doctype html><title>Receipt</title><h1>Order placed</h1><p>One order recorded.</p>"
                if self.path.startswith("/receipt")
                else """<!doctype html><title>Checkout</title><form aria-label="Checkout" method="post" action="/place">
                <label>Reference<input name="reference" value="public-reference"></label>
                <button>Place order</button></form><a href="/receipt">Receipt</a>"""
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_POST(self):
            orders.append(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            time.sleep(0.04)
            self.send_response(303)
            self.send_header("Location", "/receipt")
            self.end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    tools = mcp_browser_server.BrowserTools(
        boundary=boundary.Boundary.build(
            allow_origin=(origin,),
            allow_read=(origin + "/*",),
            allow_writes=True,
            private="heuristic",
        )
    )
    try:
        page = tools.observe(url=origin + "/checkout")
        assert page["ok"], page
        yield tools, tools._session, orders, origin, agent_session, boundary
    finally:
        tools.close()
        server.shutdown()
        server.server_close()


def submit(tools, use_call, wait):
    arguments = {"wait_for": {"change": "navigation"}} if wait else {}
    if use_call:
        return tools.call(tool="checkout", arguments={"reference": "requested-reference"}, **arguments)
    raw = tools._history[tools._latest]
    handle = next(h for h, c in raw.handles.items() if c.name == "Place order")
    return tools.act(action="click", handle=handle, **arguments)


@pytest.mark.parametrize("use_call", [False, True])
@pytest.mark.parametrize("wait", [False, True])
def test_navigation_interrupted_capture_recovers_without_repeating_submit(app, monkeypatch, use_call, wait):
    tools, session, orders, _, module, _ = app
    original = session._call
    interrupted = []

    def transport(cmd, **kwargs):
        if cmd == "snapshot" and orders and not interrupted:
            interrupted.append(True)
            raise module.BridgeError(
                "page.evaluate: Execution context was destroyed, most likely because of a navigation"
            )
        return original(cmd, **kwargs)

    monkeypatch.setattr(session, "_call", transport)
    result = submit(tools, use_call, wait)
    assert interrupted and len(orders) == 1
    assert result["ok"], result
    assert "Order placed" in result["view"]
    assert result["outcome"]["status"] == "succeeded"
    assert result["outcome"]["retry"] == "not_needed"
    assert result["progress"]["submission"] == "attempted"
    assert result["progress"]["writes"]["passed"] == 1
    assert result["steps"][-1]["step"]["name"] == "Place order"


@pytest.mark.parametrize("wait", [False, True])
def test_failed_post_submit_read_preserves_steps_and_progress(app, monkeypatch, wait):
    tools, session, orders, _, module, _ = app
    observe = session._observe

    def unreadable(**kwargs):
        if orders:
            raise module.BridgeError("The resulting page is unavailable")
        return observe(**kwargs)

    monkeypatch.setattr(session, "_observe", unreadable)
    result = submit(tools, True, wait)
    assert not result["ok"] and len(orders) == 1
    assert result["errors"][0]["code"] == "BROWSER_OBSERVATION_FAILED"
    assert result["outcome"]["status"] == "uncertain"
    assert result["outcome"]["retry"] == "may_repeat"
    assert result["progress"]["submission"] == "attempted"
    assert [f["status"] for f in result["progress"]["fields"]] == ["verified", "applied"]
    assert result["progress"]["writes"]["passed"] == 1
    assert len(result["steps"]) == 2
    monkeypatch.setattr(session, "_observe", observe)
    receipt = tools.observe()
    assert receipt["ok"] and "Order placed" in receipt["view"] and len(orders) == 1


def test_observation_restarts_if_document_changes_before_target_capture(app, monkeypatch):
    tools, session, orders, origin, _, _ = app
    original = session._call
    navigated = []

    def transport(cmd, **kwargs):
        if cmd == "targets" and not navigated:
            navigated.append(True)
            session.open(origin + "/receipt")
        return original(cmd, **kwargs)

    monkeypatch.setattr(session, "_call", transport)
    observed = tools.observe()
    assert observed["ok"], observed
    assert "Order placed" in observed["view"] and "Place order" not in observed["view"]
    assert observed["title"] == "Receipt" and observed["url"] == origin + "/receipt"
    assert not orders


def test_navigation_read_retries_are_bounded_and_preserve_completed_write(app, monkeypatch):
    tools, session, orders, _, module, _ = app
    original = session._call
    failures = []

    def transport(cmd, **kwargs):
        if cmd == "snapshot" and orders:
            failures.append(cmd)
            raise module.BridgeError("read-interrupted: still navigating")
        return original(cmd, **kwargs)

    monkeypatch.setattr(session, "_call", transport)
    result = submit(tools, True, False)
    assert not result["ok"] and len(orders) == 1
    assert 1 < len(failures) <= 12
    assert result["errors"][0]["code"] == "BROWSER_OBSERVATION_FAILED"
    assert len(result["steps"]) == 2
    assert result["progress"]["writes"]["passed"] == 1
    assert result["progress"]["submission"] == "attempted"
    assert result["outcome"]["retry"] == "may_repeat"


def test_public_observation_remains_usable_for_action_planning(app):
    _, session, orders, _, _, _ = app
    page = session.observe()
    control = next(c for c in page.handles.values() if c.name == "Reference")
    results = session.run(plan_control_action(control, "fill", "updated"), observation=page)
    assert results[0].verified is True and not orders
    assert next(c for c in session.observe().handles.values() if c.name == "Reference").value == "updated"
