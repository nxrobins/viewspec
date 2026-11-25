"""Real browser contracts judged by an independent server-side request journal."""

from __future__ import annotations

import http.server
import json
import os
import threading
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

    journal = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            mode = urlsplit(self.path).path.strip("/")
            scripts = {
                "immediate": "nameField.oninput=()=>nameField.value='Rejected'",
                "delayed": "nameField.oninput=()=>setTimeout(()=>nameField.value='Delayed',0)",
                "later": "noteField.oninput=()=>nameField.value='Changed'",
                "omitted": "noteField.oninput=()=>nameField.value='Changed'",
                "focus": "save.onfocus=()=>nameField.value='Changed'",
                "click": "save.onclick=()=>nameField.value='Changed'",
                "blur": "noteField.onblur=()=>nameField.value='Changed'",
                "route": "save.onfocus=()=>history.pushState({},'', '/different-product')",
                "identity": "noteField.oninput=()=>nameField.setAttribute('aria-label','Different product')",
                "noop": "noteField.oninput=()=>terms.setAttribute('aria-checked','false')",
                "autosave": "nameField.oninput=()=>fetch('/autosave',{method:'POST',body:nameField.value});noteField.oninput=()=>noteField.value='Rejected'",
                "protocol": "noteField.oninput=()=>document.querySelector('[name=token]').value='renewed'",
                "secret": "noteField.oninput=()=>document.querySelector('[name=password]').value='secret-changed'",
                "replace": "noteField.oninput=()=>{const copy=nameField.cloneNode();copy.value=nameField.value;nameField.replaceWith(copy)}",
                "type": "noteField.oninput=()=>nameField.type='hidden'",
                "hidden-heading": "noteField.oninput=()=>document.querySelector('h2').textContent='December 2026'",
            }
            fields = """<label>Name<input id="nameField" name="name" value="Original"></label>
                <label>Note<input id="noteField" name="note" value="Old"></label>
                <input type="hidden" name="token" value="protocol">"""
            if mode == "hidden-heading":
                fields = """<section><div hidden><h2>November 2026</h2></div>
                    <label>Name<input id="nameField" name="name" value="Original"></label></section>
                    <section><label>Note<input id="noteField" name="note" value="Old"></label></section>"""
            if mode in {"noop", "aria"}:
                fields += f'<button id="terms" type="button" name="terms" role="checkbox" aria-checked="{str(mode == "noop").lower()}">Terms</button>'
            if mode == "focus":
                fields += """<button type="button" onclick="save.onfocus=null;nameField.value='New task'">Reset guard case</button>"""
            if mode == "unknown":
                fields += '<button type="button" name="terms" role="checkbox" aria-checked="mixed">Terms</button>'
            if mode == "secret":
                fields += '<label>Password<input type="password" name="password" value="secret-original"></label>'
            if mode in {"dates", "dmy"}:
                placeholder = "25/11/2026" if mode == "dmy" else "11/25/2026"
                fields += f'<label>Start date<input name="start" placeholder="{placeholder}"></label><label>End date<input name="end" placeholder="{placeholder}"></label>'
            if mode in {"independent", "ambiguous"}:
                for key in ["start", "end"]:
                    fields += f'''<label>{key.title()} date<input name="{key}" id="{key}" readonly aria-controls="cal-{key}" onclick="document.querySelectorAll('.calendar').forEach(c=>c.hidden=true);document.getElementById('cal-{key}').hidden=false"></label>
                    <div class="calendar" id="cal-{key}" hidden><h2>December 2026</h2><div role="grid">'''
                    for day in range(1, 32):
                        display = f"{day:02}/12/2026" if mode == "ambiguous" else f"2026-12-{day:02}"
                        fields += f"""<button type="button" aria-label="December {day}, 2026" onclick="document.getElementById('{key}').value='{display}'">{day}</button>"""
                    fields += "</div></div>"
            if mode == "color":
                fields += '<label>Color<input name="color" type="color" oninput="document.querySelector(\'[name=events]\').value+=\'input,\'" onchange="document.querySelector(\'[name=events]\').value+=\'change,\'"></label><input name="events" type="hidden" value="">'
            body = f'<!doctype html><title>Form</title><form aria-label="Order" method="post" action="/commit">{fields}<button id="save">Save</button></form><script>{scripts.get(mode, "")}</script>'
            if mode == "receipt":
                body = "<title>Receipt</title><h1>Saved</h1>"
            raw = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode()
            journal.append((self.path, body))
            self.send_response(303 if self.path == "/commit" else 200)
            if self.path == "/commit":
                self.send_header("Location", "/receipt")
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    tools = BrowserTools(
        boundary=Boundary.build(
            allow_origin=(origin,), allow_read=(origin + "/*",), allow_writes=True, private="heuristic"
        )
    )
    try:
        yield tools, origin, journal
    finally:
        tools.close()
        server.shutdown()
        server.server_close()


def call(app, mode, arguments=None, **kwargs):
    tools, origin, _ = app
    page = tools.observe(url=origin + "/" + mode)
    assert page["ok"], page
    return tools.call(tool="order", arguments=arguments or {"name": "Alice", "note": "New"}, **kwargs)


@pytest.mark.parametrize(
    "mode",
    [
        "immediate",
        "delayed",
        "later",
        "omitted",
        "focus",
        "click",
        "blur",
        "noop",
        "aria",
        "identity",
        "type",
        "route",
        "secret",
    ],
)
def test_unmet_contract_never_submits(app, mode):
    arguments = {"note": "New"} if mode == "omitted" else {"name": "Alice", "note": "New"}
    if mode in {"noop", "aria"}:
        arguments["terms"] = True
    result = call(app, mode, arguments)
    assert not result["ok"], result
    assert not app[2], (result, app[2])
    assert result["outcome"]["status"] in {"failed", "uncertain"}
    assert result["progress"]["submission"] in {"withheld", "not_run"}
    if mode == "secret":
        assert "secret-original" not in json.dumps(result) and "secret-changed" not in json.dumps(result)
    # No guard survives an operation. A subsequent unrelated correct form can submit normally.
    recovered = call(app, "normal")
    assert recovered["ok"], recovered
    assert len(app[2]) == 1 and app[2][0][0] == "/commit"


@pytest.mark.parametrize("mode", ["normal", "protocol", "replace", "hidden-heading"])
def test_correct_contract_submits_exactly_once(app, mode):
    result = call(app, mode)
    assert result["ok"], result
    assert result["progress"]["submission"] == "attempted"
    assert all(f["status"] == "matched" for f in result["progress"]["contract"])
    assert len(app[2]) == 1
    data = parse_qs(app[2][0][1])
    assert data["name"] == ["Alice"] and data["note"] == ["New"]
    if mode != "hidden-heading":
        assert data["token"] == ["renewed" if mode == "protocol" else "protocol"]


def test_autosave_is_retained_and_retry_is_not_safe(app):
    result = call(app, "autosave")
    assert not result["ok"], result
    assert app[2] == [("/autosave", "Alice")]
    assert result["progress"]["writes"]["passed"] == 1
    assert result["outcome"]["status"] == "failed"
    assert result["outcome"]["retry"] == "may_repeat"


def test_fill_only_also_checks_final_state(app):
    result = call(app, "later", submit=False)
    assert not result["ok"] and result["outcome"]["status"] == "failed", result
    assert not app[2] and result["progress"]["submission"] == "not_requested"
    assert result["progress"]["contract"][0]["status"] == "mismatch"


@pytest.mark.parametrize("mode,start,end", [("dates", "12/01/2026", "12/18/2026"), ("dmy", "01/12/2026", "18/12/2026")])
def test_editable_date_pair_uses_visible_format_and_one_correct_post(app, mode, start, end):
    result = call(app, mode, {"start": "2026-12-01", "end": "2026-12-18"})
    assert result["ok"], result
    assert len(app[2]) == 1
    data = parse_qs(app[2][0][1])
    assert data["start"] == [start] and data["end"] == [end]


def test_color_normalization_events_and_validation_before_mutation(app):
    result = call(app, "color", {"color": "invalid"})
    assert not result["ok"] and not app[2]
    result = app[0].call(tool="order", arguments={"color": "#2878B5"})
    assert result["ok"], result
    data = parse_qs(app[2][0][1])
    assert data["color"] == ["#2878b5"]
    assert data["events"] == ["input,change,"]


def test_unknown_preservation_stops_before_any_edit(app):
    result = call(app, "unknown")
    assert not result["ok"] and result["outcome"]["status"] == "uncertain", result
    assert not result.get("steps") and not app[2]
    assert result["progress"]["submission"] == "withheld"


def test_guard_is_removed_without_reloading_the_document(app):
    tools, _, journal = app
    result = call(app, "focus")
    assert not result["ok"] and not journal

    def click(name):
        page = tools._history[tools._latest]
        handle = next(h for h, c in page.handles.items() if c.name == name)
        return tools.act(action="click", handle=handle)

    assert click("Reset guard case")["ok"]
    assert click("Save")["ok"]
    assert len(journal) == 1 and parse_qs(journal[0][1])["name"] == ["New task"]


def test_independent_readonly_calendars_keep_each_endpoint(app):
    result = call(app, "independent", {"start": "2026-12-01", "end": "2026-12-18"})
    assert result["ok"], result
    assert len(app[2]) == 1
    data = parse_qs(app[2][0][1])
    assert data["start"] == ["2026-12-01"] and data["end"] == ["2026-12-18"]


def test_ambiguous_numeric_date_cannot_prove_the_requested_date(app):
    result = call(app, "ambiguous", {"start": "2026-12-01", "end": "2026-12-18"})
    assert not result["ok"] and not app[2], result
    assert result["outcome"]["status"] == "uncertain"
    assert result["steps"][0]["verified"] is None
