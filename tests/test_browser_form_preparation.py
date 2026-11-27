"""Native preparation is judged by an independent server journal, including premature commits."""

from __future__ import annotations

import contextlib
import http.server
import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

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

    scripts = {
        "normal": "",
        "disabled-field-enabled": "keep.disabled=true;noteField.oninput=()=>{keep.disabled=false;keep.value='Unexpected'};",
        "frame-route": "noteField.oninput=()=>history.pushState({},'', '/other-record');",
        "adopt-child": "history.replaceState({},'',parent.location.href);noteField.oninput=()=>{parent.document.body.append(order);order.requestSubmit(save)};",
        "adopt-before-child": "history.replaceState({},'',parent.location.href);document.getElementById('drift').onclick=()=>parent.document.body.append(order);",
        "coverage": "for(let i=0;i<257;i++){let el=document.createElement('input');el.name='extra'+i;el.value='kept';order.append(el)};",
        "failed-step-autosave": "nameField.oninput=()=>{fetch('/autosave',{method:'POST',body:JSON.stringify({name:nameField.value})});nameField.value='Rejected'};",
        "private-enum": "let el=document.createElement('input');el.type='password';el.name='password';el.value='dispatched';order.append(el);",
        "fieldset": "let box=document.createElement('fieldset');save.replaceWith(box);box.append(save);box.disabled=true;noteField.onblur=()=>box.disabled=false;",
        "aria": "save.setAttribute('aria-disabled','true');noteField.onblur=()=>save.setAttribute('aria-disabled','false');",
        "field-equivalent": "noteField.oninput=()=>{let copy=nameField.cloneNode(true);copy.value=nameField.value;nameField.replaceWith(copy)};",
        "external": "nameField.setAttribute('form','order');document.body.append(nameField.parentElement);",
        "buttonless": "save.remove();",
        "buttonless-gains": "save.remove();noteField.oninput=()=>{let button=document.createElement('button');button.textContent='Delete';order.append(button)};",
        "duplicate-submit": "save.onclick=()=>{order.requestSubmit(save);order.requestSubmit(save)};",
        "native-novalidate": "save.formNoValidate=true;noteField.oninput=()=>noteField.setCustomValidity('Rejected');",
        "pointer-drift": "save.onpointerdown=()=>save.value='delete';",
        "click-drift": "save.onclick=()=>save.setAttribute('formaction','/different-commit');",
        "before-call": "document.getElementById('drift').onclick=()=>save.value='delete';",
        "cleanup": "noteField.oninput=()=>order.requestSubmit(save);",
        "secret": "save.value='credential-value';save.setAttribute('formaction','/commit?token=credential-value');let p=document.createElement('input');p.type='password';p.name='password';p.value='credential-value';order.append(p);",
        "disabled-sync": "save.disabled=true;noteField.oninput=()=>save.disabled=false;",
        "disabled-timer": "save.disabled=true;noteField.oninput=()=>setTimeout(()=>save.disabled=false,350);",
        "disabled-after-budget": "save.disabled=true;noteField.oninput=()=>setTimeout(()=>save.disabled=false,1800);",
        "disabled-blur": "save.disabled=true;noteField.onblur=()=>save.disabled=false;",
        "disabled-never-autosave": "save.disabled=true;nameField.oninput=()=>fetch('/autosave',{method:'POST',body:JSON.stringify({name:nameField.value})});",
        "disabled-validation": "save.disabled=true;noteField.oninput=async()=>{pending=true;await fetch('/validate');pending=false;save.disabled=false};",
        "blur-validation": "noteField.onblur=async()=>{pending=true;await fetch('/validate');noteField.value='Rejected';pending=false};",
        "focus-validation": "save.onfocus=async()=>{pending=true;await fetch('/validate');noteField.value='Rejected';pending=false};",
        "submit-during-input": "nameField.oninput=()=>order.requestSubmit(save);",
        "fill-only-submit-during-input": "nameField.oninput=()=>order.requestSubmit(save);",
        "owner-change": "noteField.oninput=()=>save.setAttribute('form','other');",
        "field-owner-change": "noteField.oninput=()=>nameField.setAttribute('form','other');",
        "extra-visible-field": "noteField.oninput=()=>{let extra=document.createElement('input');extra.name='extra';extra.value='Unexpected';extra.setAttribute('aria-label','Extra');order.append(extra)};",
        "focus-owner-change": "save.onfocus=()=>save.setAttribute('form','other');",
        "action-change": "noteField.oninput=()=>save.setAttribute('formaction','/different-commit');",
        "value-change": "noteField.oninput=()=>save.value='delete';",
        "type-change": "noteField.oninput=()=>save.type='button';save.onclick=()=>{if(save.type==='button')fetch('/button-action',{method:'POST',body:'{}'})};",
        "replacement-equivalent": "noteField.oninput=()=>save.replaceWith(save.cloneNode(true));",
        "replacement-changed": "noteField.oninput=()=>{let copy=save.cloneNode(true);copy.value='delete';save.replaceWith(copy)};",
        "omitted-drift": "noteField.oninput=()=>keep.value='Changed';",
        "route-drift": "noteField.oninput=()=>history.pushState({},'', '/other-record');",
        "native-invalid": "noteField.oninput=()=>noteField.setCustomValidity('Rejected by application');",
        "multiple-actions": "save.disabled=true;save.value='publish';save.textContent='Publish';let draft=document.createElement('button');draft.name='action';draft.value='draft';draft.textContent='Save draft';order.append(draft);noteField.oninput=()=>save.disabled=false;",
    }
    journal = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass

        def do_GET(self):
            case = urlsplit(self.path).path.strip("/")
            frames = {
                "frame": "disabled-blur",
                "frame-changes-route": "frame-route",
                "frame-adopt": "adopt-child",
                "frame-adopt-before": "adopt-before-child",
            }
            if case in frames:
                child = frames[case]
                body = f'<iframe src="/{child}" style="width:100%;height:700px"></iframe>'.encode()
            elif case == "validate":
                time.sleep(0.4)
                body = b"done"
            else:
                script = scripts[case]
                body = f"""<!doctype html><title>Preparation</title>
                <form id="order" aria-label="Order" action="/commit" method="post">
                  <label>Name<input id="nameField" name="name" value="Old name"></label>
                  <label>Note<input id="noteField" name="note" value="Old note"></label>
                  <label>Keep<input id="keep" name="keep" value="Preserved"></label>
                  <button id="save" name="action" value="save">Save</button>
                </form>
                <form id="other" aria-label="Other" action="/other-commit" method="post">
                  <input type="hidden" name="record" value="other-record">
                </form><output id="status" role="status"></output>
                <button id="drift" type="button">Change operation</button><script>
                let pending=false;
                const order=document.getElementById('order'),save=document.getElementById('save'),
                  nameField=document.getElementById('nameField'),noteField=document.getElementById('noteField'),
                  keep=document.getElementById('keep');
                for(const form of document.forms) form.onsubmit=async e=>{{
                  e.preventDefault();
                  const values=Object.fromEntries(new FormData(form,e.submitter));
                  await fetch(e.submitter?.getAttribute('formaction')||form.getAttribute('action'),{{method:'POST',
                    body:JSON.stringify({{form:form.id,values,pending,submitter:e.submitter?.value}})}});
                  document.getElementById('status').textContent='Saved';
                }};
                {script}
                </script>""".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body)

        def do_POST(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
            journal.append({"path": self.path, "body": json.loads(data)})
            self.send_response(204)
            self.end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    browser = BrowserTools(
        boundary=Boundary.build(
            allow_origin=(origin,), allow_read=(origin + "/*",), allow_writes=True, private="heuristic"
        )
    )
    try:
        yield browser, origin, journal
    finally:
        browser.close()
        server.shutdown()
        server.server_close()


def observe(app, case):
    result = app[0].observe(url=app[1] + "/" + case)
    assert result["ok"], result
    return result


def call(app, case, **kwargs):
    observe(app, case)
    return app[0].call(tool="order", arguments={"name": "Alice", "note": "New note"}, **kwargs)


@pytest.mark.parametrize(
    "case",
    [
        "normal",
        "disabled-sync",
        "disabled-timer",
        "disabled-blur",
        "disabled-validation",
        "replacement-equivalent",
        "field-equivalent",
        "fieldset",
        "aria",
        "external",
        "buttonless",
        "native-novalidate",
        "frame",
    ],
)
def test_preparation_then_one_correct_commit(app, case):
    result = call(app, case)
    assert result["ok"], result
    assert len(app[2]) == 1, app[2]
    body = app[2][0]["body"]
    assert body["values"]["name"] == "Alice" and body["values"]["note"] == "New note"
    assert body["values"]["keep"] == "Preserved" and not body["pending"]
    assert result["progress"]["preparation"]["native_submissions"] == 1
    assert result["progress"]["preparation"]["activation"] == "dispatched"


@pytest.mark.parametrize(
    "case",
    [
        "blur-validation",
        "focus-validation",
        "owner-change",
        "field-owner-change",
        "extra-visible-field",
        "focus-owner-change",
        "action-change",
        "value-change",
        "type-change",
        "replacement-changed",
        "omitted-drift",
        "route-drift",
        "buttonless-gains",
        "pointer-drift",
        "click-drift",
        "frame-changes-route",
    ],
)
def test_changed_contract_withholds_commit(app, case):
    result = call(app, case)
    assert not result["ok"], result
    assert not app[2], app[2]
    assert result["progress"]["submission"] == "withheld"
    assert result["progress"]["preparation"]["native_submissions"] == 0


@pytest.mark.parametrize("submit", [True, False])
def test_native_submit_during_preparation_is_prevented(app, submit):
    result = call(app, "submit-during-input", submit=submit)
    assert result["ok"], result
    phase = result["progress"]["preparation"]
    assert phase["prevented_submissions"] == 1
    assert phase["native_submissions"] == int(submit)
    assert len(app[2]) == int(submit)
    if submit:
        assert app[2][0]["body"]["values"]["note"] == "New note"


def test_duplicate_native_submission_is_prevented(app):
    result = call(app, "duplicate-submit")
    assert result["ok"], result
    assert len(app[2]) == 1
    assert result["progress"]["preparation"]["prevented_submissions"] == 2


def test_expired_budget_cannot_start_late_click(app):
    result = call(app, "disabled-after-budget", timeout_ms=700)
    assert not result["ok"] and result["errors"][0]["code"] == "BROWSER_FORM_PENDING", result
    time.sleep(1.9)
    assert not app[2]
    assert result["progress"]["preparation"]["activation"] == "not_dispatched"


def test_withheld_submission_retains_autosave_and_unsafe_retry(app):
    result = call(app, "disabled-never-autosave", timeout_ms=500)
    assert not result["ok"] and result["progress"]["submission"] == "withheld", result
    assert [item["path"] for item in app[2]] == ["/autosave"]
    assert result["progress"]["writes"]["passed"] == 1
    assert result["outcome"]["retry"] == "may_repeat"
    assert all(field["status"] == "matched" for field in result["progress"]["contract"])


def test_multi_submitter_choice_is_required_before_edits(app):
    page = observe(app, "multiple-actions")
    tools = app[0]
    result = tools.call(tool="order", arguments={"name": "Alice", "note": "New note"})
    assert not result["ok"] and result["errors"][0]["code"] == "AGENT_FORM_SUBMITTER_AMBIGUOUS", result
    assert not app[2]
    saved = tools._history[tools._latest]
    handle = next(h for h, c in saved.handles.items() if c.name == "Publish")
    assert handle in page["form_tools"][0]["submitters"]
    result = tools.call(tool="order", arguments={"name": "Alice", "note": "New note"}, submitter=handle)
    assert result["ok"], result
    assert len(app[2]) == 1 and app[2][0]["body"]["submitter"] == "publish"


def test_observation_pins_semantics_before_any_field_edit(app):
    page = observe(app, "before-call")
    tools = app[0]
    saved = tools._history[tools._latest]
    handle = next(h for h, c in saved.handles.items() if c.name == "Change operation")
    assert tools.act(action="click", handle=handle)["ok"]
    result = tools.call(
        tool="order", arguments={"name": "Alice", "note": "New note"}, observation_id=page["observation_id"]
    )
    assert not result["ok"], result
    assert not result.get("steps") and not app[2]


def test_native_invalid_is_activation_without_native_submission(app):
    result = call(app, "native-invalid")
    assert result["ok"] and result["outcome"]["status"] == "failed", result
    assert not app[2]
    phase = result["progress"]["preparation"]
    assert phase["activation"] == "dispatched" and phase["native_submissions"] == 0


def test_fill_only_guard_is_cleaned_up(app):
    result = call(app, "cleanup", submit=False)
    assert result["ok"] and not app[2], result
    saved = app[0]._history[app[0]._latest]
    handle = next(h for h, c in saved.handles.items() if c.name == "Note")
    assert app[0].act(action="fill", handle=handle, value="Bob")["ok"]
    assert len(app[2]) == 1 and app[2][0]["body"]["values"]["note"] == "Bob"


def test_private_submission_descriptor_never_leaves_page(app):
    result = call(app, "secret")
    assert result["ok"], result
    assert "credential-value" not in json.dumps(result)
    assert len(app[2]) == 1


@pytest.mark.parametrize(
    "submitter,submit", [(False, True), (999, True), ("save", True), ("valid", False), ("unrelated", True)]
)
def test_invalid_submitter_is_rejected_before_field_writes(app, submitter, submit):
    page = observe(app, "disabled-never-autosave")
    if submitter == "valid":
        submitter = page["form_tools"][0]["submitters"][0]
    elif submitter == "unrelated":
        submitter = next(h for h, c in app[0]._history[app[0]._latest].handles.items() if c.name == "Change operation")
    result = app[0].call(tool="order", arguments={"name": "Alice"}, submitter=submitter, submit=submit)
    assert not result["ok"] and result["errors"][0]["code"] == "AGENT_FORM_SUBMITTER_INVALID", result
    assert not app[2]


def test_unknown_member_coverage_refuses_before_edits(app):
    result = call(app, "coverage")
    assert not result["ok"] and not result.get("steps") and not app[2], result


def test_failed_field_step_retains_autosave_evidence(app):
    result = call(app, "failed-step-autosave")
    assert not result["ok"] and len(app[2]) == 1, result
    assert result["progress"]["writes"]["passed"] == 1
    assert result["progress"]["preparation"]["activation"] == "not_dispatched"
    assert result["outcome"]["retry"] == "may_repeat"


def test_private_values_do_not_corrupt_preparation_enum(app):
    result = call(app, "private-enum")
    assert result["ok"], result
    assert result["progress"]["preparation"]["activation"] == "dispatched"
    assert app[2][0]["body"]["values"]["password"] == "dispatched"


def test_guard_travels_with_form_adopted_into_another_document(app):
    result = call(app, "frame-adopt")
    assert not result["ok"] and not app[2], result
    assert result["progress"]["submission"] == "withheld"
    assert result["progress"]["preparation"]["prevented_submissions"] == 1


def test_observed_document_identity_survives_same_url_adoption(app):
    page = observe(app, "frame-adopt-before")
    tools = app[0]
    saved = tools._history[tools._latest]
    handle = next(h for h, c in saved.handles.items() if c.name == "Change operation")
    assert tools.act(action="click", handle=handle)["ok"]
    result = tools.call(
        tool="order", arguments={"name": "Alice", "note": "New note"}, observation_id=page["observation_id"]
    )
    assert not result["ok"] and not result.get("steps") and not app[2], result


def test_newly_enabled_field_requires_a_new_observation(app):
    result = call(app, "disabled-field-enabled")
    assert not result["ok"] and not app[2], result
    assert result["progress"]["submission"] == "withheld"
