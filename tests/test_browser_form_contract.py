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
            if mode in {"independent", "independent-unnamed", "ambiguous"}:
                for key in ["start", "end"]:
                    ownership = "" if mode == "independent-unnamed" else f'aria-controls="cal-{key}"'
                    fields += f'''<label>{key.title()} date<input name="{key}" id="{key}" readonly {ownership} onclick="document.querySelectorAll('.calendar').forEach(c=>c.hidden=true);document.getElementById('cal-{key}').hidden=false"></label>
                    <div class="calendar" id="cal-{key}" hidden><h2>December 2026</h2><div role="grid">'''
                    for day in range(1, 32):
                        display = f"{day:02}/12/2026" if mode == "ambiguous" else f"2026-12-{day:02}"
                        fields += f"""<button type="button" aria-label="December {day}, 2026" onclick="document.getElementById('{key}').value='{display}'">{day}</button>"""
                    fields += "</div></div>"
            if mode in {"shared-range", "shared-deferred", "shared-noop"}:
                fields += """<label>Start date<input name="start" id="start" readonly aria-controls="shared-calendar"></label>
                    <label>End date<input name="end" id="end" readonly aria-controls="shared-calendar"></label>
                    <input name="opens" id="opens" type="hidden" value="0">
                    <output id="picks">Picks 0</output>
                    <div id="shared-calendar" role="dialog" hidden><h2>December 2026</h2><div role="grid">"""
                for day in range(1, 32):
                    fields += f'<button type="button" data-date="2026-12-{day:02}">{day}</button>'
                fields += "</div></div>"
                scripts[mode] = """
                    const calendar=document.getElementById('shared-calendar');
                    const start=document.getElementById('start'), end=document.getElementById('end');
                    let picked=0, pending=null;
                    for(const field of [start,end])field.onclick=()=>{
                        opens.value=String(Number(opens.value)+1);pending=null;start.value='';end.value='';calendar.hidden=false;
                    };
                    calendar.onclick=e=>{
                        if(!e.target.dataset.date)return;
                        picks.textContent='Picks '+(++picked);
                        if(location.pathname==='/shared-noop')return;
                        if(!pending){
                            pending=e.target.dataset.date;
                            if(location.pathname!=='/shared-deferred')start.value=pending;
                        }else{start.value=pending;end.value=e.target.dataset.date;calendar.hidden=true;}
                    };
                """
            if mode in {
                "selected-choice",
                "unknown-choice",
                "changed-choice",
                "query-drift-choice",
                "ignored-selected-choice",
                "closed-selected-choice",
                "visible-selected-choice",
                "ambiguous-choice",
                "action-choice",
            }:
                selected = mode != "unknown-choice"
                expanded = mode in {
                    "visible-selected-choice",
                    "changed-choice",
                    "query-drift-choice",
                    "ambiguous-choice",
                    "action-choice",
                }
                label = "Japan<br>Create a new country" if mode == "action-choice" else "Japan"
                fields += f'''<label>Country<input name="country" id="country" role="combobox"
                    aria-controls="countries" aria-expanded="{str(expanded).lower()}" value="Japan"></label>
                    <ul id="countries" role="listbox" {"" if expanded else "hidden"}>
                    <li role="option" id="japan" aria-selected="{str(selected).lower()}">{label}</li>
                    <li role="option" aria-selected="{str(mode == "ambiguous-choice").lower()}">France</li></ul>
                    <input name="activations" id="activations" type="hidden" value="0">'''
                scripts[mode] = """
                    const country=document.getElementById('country'), list=document.getElementById('countries');
                    country.oninput=()=>{list.hidden=false;country.setAttribute('aria-expanded','true')};
                    list.onclick=e=>{
                        if(e.target.getAttribute('role')!=='option')return;
                        activations.value=String(Number(activations.value)+1);
                        if(location.pathname==='/ignored-selected-choice')return;
                        list.querySelectorAll('[role=option]').forEach(o=>o.setAttribute('aria-selected',String(o===e.target)));
                        country.value=e.target.textContent;list.hidden=true;country.setAttribute('aria-expanded','false');
                    };
                """
                if mode == "changed-choice":
                    scripts[mode] += (
                        "noteField.oninput=()=>document.getElementById('japan').setAttribute('aria-selected','false');"
                    )
                if mode == "query-drift-choice":
                    scripts[mode] += "noteField.oninput=()=>country.value='France';"
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


@pytest.mark.parametrize("mode", ["independent", "independent-unnamed"])
def test_independent_readonly_calendars_keep_each_endpoint(app, mode):
    result = call(app, mode, {"start": "2026-12-01", "end": "2026-12-18"})
    assert result["ok"], result
    assert len(app[2]) == 1
    data = parse_qs(app[2][0][1])
    assert data["start"] == ["2026-12-01"] and data["end"] == ["2026-12-18"]


def test_ambiguous_numeric_date_cannot_prove_the_requested_date(app):
    result = call(app, "ambiguous", {"start": "2026-12-01", "end": "2026-12-18"})
    assert not result["ok"] and not app[2], result
    assert result["outcome"]["status"] == "uncertain"
    assert result["steps"][0]["verified"] is None


@pytest.mark.parametrize("use_call", [False, True])
@pytest.mark.parametrize("mode", ["shared-range", "shared-deferred"])
def test_shared_range_finishes_in_open_picker_without_reactivating_end(app, use_call, mode):
    tools, origin, journal = app
    page = tools.observe(url=origin + "/" + mode)
    if use_call:
        result = tools.call(tool="order", arguments={"start": "2026-12-01", "end": "2026-12-18"})
    else:
        from tests.test_browser_bridge_e2e import handle

        result = tools.act(action="select", handle=handle(page, "Start date"), value=["2026-12-01", "2026-12-18"])
        assert result["ok"], result
        result = tools.call(tool="order", arguments={})
    assert result["ok"], result
    assert len(journal) == 1 and journal[0][0] == "/commit"
    data = parse_qs(journal[0][1])
    assert data["start"] == ["2026-12-01"] and data["end"] == ["2026-12-18"]
    assert data["opens"] == ["1"]


@pytest.mark.parametrize("submit", [False, True])
def test_reselecting_explicit_aria_selection_needs_no_activation(app, submit):
    tools, origin, journal = app
    page = tools.observe(url=origin + "/selected-choice")
    from tests.test_browser_bridge_e2e import handle

    selected = tools.act(action="select", handle=handle(page, "Country"), value="Japan")
    assert selected["ok"] and selected["steps"][0]["verified"] is True, selected
    assert not selected["steps"][0]["selection"]["changed"]
    assert "'Country' = 'Japan'" in selected["view"]
    assert selected["form_tools"][0]["input_schema"]["properties"]["country"]["default"] == "Japan"
    result = tools.call(tool="order", arguments={"country": "Japan", "note": "New"}, submit=submit)
    assert result["ok"], result
    assert all(f["status"] == "matched" for f in result["progress"]["contract"])
    if not submit:
        assert not journal
        result = tools.call(tool="order", arguments={})
        assert result["ok"], result
    data = parse_qs(journal[0][1])
    assert len(journal) == 1 and data["country"] == ["Japan"] and data["note"] == ["New"]
    assert data["activations"] == ["0"]


@pytest.mark.parametrize("submit", [False, True])
@pytest.mark.parametrize("mode", ["unknown-choice", "closed-selected-choice", "ambiguous-choice", "action-choice"])
def test_prefilled_query_without_selection_evidence_still_refuses_before_edits(app, submit, mode):
    tools, _, journal = app
    result = call(app, mode, {"note": "New"}, submit=submit)
    assert not result["ok"] and result["errors"][0]["code"] == "BROWSER_FORM_UNKNOWN", result
    assert not journal and all(f["status"] == "not_run" for f in result["progress"]["fields"])
    assert "'Note' = 'Old'" in tools.observe()["view"]


@pytest.mark.parametrize("submit", [False, True])
def test_omitted_prefilled_choice_with_explicit_selection_is_preserved(app, submit):
    _, _, journal = app
    result = call(app, "visible-selected-choice", {"note": "New"}, submit=submit)
    assert result["ok"], result
    assert all(f["status"] == "matched" for f in result["progress"]["contract"])
    assert len(journal) == int(submit)
    if submit:
        data = parse_qs(journal[0][1])
        assert data["country"] == ["Japan"] and data["activations"] == ["0"]


@pytest.mark.parametrize("mode", ["changed-choice", "query-drift-choice"])
def test_omitted_choice_losing_selection_evidence_cannot_submit(app, mode):
    result = call(app, mode, {"note": "New"})
    assert not result["ok"] and not app[2], result
    assert "'Note' = 'New'" in app[0].observe()["view"]
    assert result["progress"]["submission"] == "withheld"


def test_partial_query_commits_selected_option_before_form_submit(app):
    tools, _, journal = app
    result = call(app, "selected-choice", {"country": "Jap"}, submit=False)
    assert result["ok"] and result["steps"][0]["verified"] is True, result
    assert "query: 'Japan'" in result["view"]
    assert not journal
    repeated = tools.call(tool="order", arguments={"note": "New"})
    assert repeated["ok"], repeated
    data = parse_qs(journal[0][1])
    assert len(journal) == 1 and data["country"] == ["Japan"] and data["note"] == ["New"]
    assert data["activations"] == ["1"]


def test_selected_marker_cannot_verify_an_uncommitted_query(app):
    result = call(app, "ignored-selected-choice", {"country": "Jap"})
    assert not result["ok"] and not app[2], result
    assert result["steps"][0]["verified"] is False
    assert result["progress"]["submission"] == "not_run"


def test_shared_range_that_does_not_commit_either_endpoint_cannot_submit(app):
    result = call(app, "shared-noop", {"start": "2026-12-01", "end": "2026-12-18"})
    assert not result["ok"] and not app[2], result
    assert "Picks 2" in app[0].observe()["view"]
    assert result["steps"][0]["verified"] is False
