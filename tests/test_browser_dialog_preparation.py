"""Non-native preparation: joint UI state and an independent write journal, Save separate."""

from __future__ import annotations

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

    journal = []
    scripts = {
        "normal": "",
        "title-echo": "first.oninput=()=>scope.setAttribute('aria-label',first.value);",
        "title-echo-blur": "first.onblur=()=>scope.setAttribute('aria-label',first.value);",
        "title-unrelated": "first.oninput=()=>scope.setAttribute('aria-label','Another contact');",
        "title-unedited": "first.oninput=()=>scope.setAttribute('aria-label','alice@example.test');",
        "late": "email.oninput=()=>first.value='Overwritten';",
        "omitted": "email.oninput=()=>tier.value='Enterprise';",
        "blur": "email.onblur=()=>tier.value='Enterprise';",
        "autosave": "email.oninput=()=>{fetch('/autosave',{method:'POST',body:JSON.stringify({email:email.value})});tier.value='Enterprise'};",
        "route": "email.oninput=()=>history.pushState({},'', '/other-contact');",
        "replace": "email.oninput=()=>scope.replaceWith(scope.cloneNode(true));",
        "move-field": "email.oninput=()=>document.body.append(first);",
        "new-field": "email.oninput=()=>{const el=document.createElement('input');el.name='extra';scope.append(el)};",
        "enabled-field": "tier.disabled=true;email.oninput=()=>tier.disabled=false;",
        "type": "email.oninput=()=>first.type='hidden';",
        "unknown": "scope.insertAdjacentHTML('beforeend','<button type=button role=checkbox aria-checked=mixed>Terms</button>');",
        "native": "const f=document.createElement('form');tier.replaceWith(f);f.append(tier);",
        "disabled-save": "save.disabled=true;email.onblur=()=>save.disabled=false;",
        "pending": "email.onblur=()=>{scope.setAttribute('aria-busy','true');fetch('/validate').then(()=>scope.removeAttribute('aria-busy'))};",
        "pending-drift": "email.onblur=()=>{scope.setAttribute('aria-busy','true');fetch('/validate').then(()=>{tier.value='Enterprise';scope.removeAttribute('aria-busy')})};",
        "busy": "email.onblur=()=>scope.setAttribute('aria-busy','true');",
        "secret": "tier.type='password';tier.value='private-original';",
        "secret-drift": "tier.type='password';tier.value='private-original';email.oninput=()=>tier.value='private-changed';",
        "equivalent-field": "first.oninput=()=>first.replaceWith(first.cloneNode(true));",
        "nested": "scope.insertAdjacentHTML('beforeend','<div role=dialog aria-label=Nested><label>Nested value<input name=nested value=Untouched></label></div>');",
        "coverage": "for(let i=0;i<257;i++){const el=document.createElement('input');el.name='extra'+i;scope.append(el)};",
        "required": "first.required=true;first.value='';",
        "typed": """
          scope.insertAdjacentHTML('beforeend', `<label>Terms<input id=terms name=terms type=checkbox></label>
            <label>Level<select id=level name=level><option>Basic</option><option>Pro</option></select></label>
            <label>Due<input id=due name=due type=date></label>
            <label>Standard<input name=urgency type=radio value=standard checked></label>
            <label>Priority<input name=urgency type=radio value=priority></label>
            <div id=summary role=textbox aria-label=Summary contenteditable=true><p><br></p></div>`);
          save.onclick=async()=>{await fetch('/save',{method:'POST',body:JSON.stringify({
            terms:document.getElementById('terms').checked,level:document.getElementById('level').value,
            due:document.getElementById('due').value,urgency:scope.querySelector('[name=urgency]:checked').value,
            summary:document.getElementById('summary').innerText})});};
        """,
        "portal": """
          scope.insertAdjacentHTML('beforeend', '<button type=button role=combobox aria-label=Priority aria-expanded=false aria-controls=options id=choice>Standard</button>');
          document.body.insertAdjacentHTML('beforeend','<div role=listbox id=options hidden><div role=option aria-selected=true>Standard</div><div role=option aria-selected=false>High</div></div>');
          const choice=document.getElementById('choice'),options=document.getElementById('options');
          choice.onclick=()=>{choice.setAttribute('aria-expanded','true');options.hidden=false};
          options.onclick=e=>{if(e.target.getAttribute('role')!=='option')return;
            options.querySelectorAll('[role=option]').forEach(el=>el.setAttribute('aria-selected',String(el===e.target)));
            choice.textContent=e.target.textContent;choice.setAttribute('aria-expanded','false');options.hidden=true;};
        """,
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            case = urlsplit(self.path).path.strip("/")
            if case == "validate":
                time.sleep(0.15)
                body = b"{}"
            else:
                body = f"""<!doctype html><title>Dialog preparation</title>
                <div role="dialog" aria-label="Contact" id="scope">
                <label>Name<input name="name" id="first" value="Original"></label>
                <label>Email<input name="email" id="email" type="email" value="old@example.test"></label>
                <label>Tier<input name="tier" id="tier" value="Standard"></label>
                <button type="button" id="save">Save</button>
                <button type="button" id="drift">Change scope</button>
                </div><output id="status"></output>
                <script>
                const scope=document.getElementById('scope'),first=document.getElementById('first'),
                  email=document.getElementById('email'),tier=document.getElementById('tier'),save=document.getElementById('save');
                document.getElementById('drift').onclick=()=>scope.setAttribute('aria-label','Different contact');
                document.addEventListener('input',()=>document.body.dataset.edits=String(Number(document.body.dataset.edits||0)+1));
                save.onclick=async()=>{{await fetch('/save',{{method:'POST',body:JSON.stringify({{
                  name:scope.querySelector('[name=name]').value,email:email.value,tier:tier.value,
                  nested:scope.querySelector('[name=nested]')?.value
                }})}});document.getElementById('status').textContent='Saved'}};
                {scripts.get(case, "")}
                </script>""".encode()
            if case in {"frame", "frame-route"}:
                child = "normal" if case == "frame" else "route"
                body = (
                    f'<iframe title="Contact editor" src="/{child}" style="width:100%;height:600px"></iframe>'.encode()
                )
            self.send_response(200)
            self.send_header("Content-Type", "application/json" if case == "validate" else "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            journal.append({"path": self.path, "body": body})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
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
        thread.join()


def observe(app, case):
    result = app[0].observe(url=app[1] + "/" + case)
    assert result["ok"], result
    assert not result["form_tools"] or case == "native"
    assert any(s["name"] == "contact" for s in result["preparation_tools"])
    return result


def prepare(app, case, **kwargs):
    before = observe(app, case)
    return app[0].prepare(
        scope="contact",
        arguments={"name": "Alice", "email": "alice@example.test"},
        observation_id=before["observation_id"],
        **kwargs,
    )


def click(app, name):
    tools = app[0]
    handle = next(h for h, c in tools._history[tools._latest].handles.items() if c.name == name)
    return tools.act(handle=handle, action="click")


@pytest.mark.parametrize(
    "case",
    ["normal", "disabled-save", "pending", "equivalent-field", "nested", "title-echo", "title-echo-blur", "frame"],
)
def test_prepare_verifies_fields_and_never_saves(app, case):
    result = prepare(app, case)
    assert result["ok"], result
    assert result["progress"]["submission"] == "not_requested"
    assert all(f["status"] == "matched" for f in result["progress"]["contract"])
    assert result["evidence"]["control_verification"] == "matched"
    assert not app[2]
    assert click(app, "Save")["ok"]
    assert app[2] == [
        {
            "path": "/save",
            "body": {
                "name": "Alice",
                "email": "alice@example.test",
                "tier": "Standard",
                **({"nested": "Untouched"} if case == "nested" else {}),
            },
        }
    ]


@pytest.mark.parametrize(
    "case",
    [
        "late",
        "title-unrelated",
        "title-unedited",
        "omitted",
        "blur",
        "route",
        "frame-route",
        "replace",
        "move-field",
        "new-field",
        "enabled-field",
        "type",
        "pending-drift",
        "secret-drift",
    ],
)
def test_changed_contract_cannot_report_prepared(app, case):
    result = prepare(app, case)
    assert not result["ok"], result
    assert result["outcome"]["status"] in {"failed", "uncertain"}
    assert result["evidence"]["control_verification"] in {"mismatch", "unknown"}
    assert result["progress"]["submission"] == "not_requested"
    assert not app[2]
    assert "private-original" not in json.dumps(result) and "private-changed" not in json.dumps(result)


@pytest.mark.parametrize("case", ["unknown", "native", "coverage"])
def test_unknown_coverage_refuses_before_editing(app, case):
    result = prepare(app, case)
    assert not result["ok"], result
    assert not app[2]
    assert "Original" in result["view"] and "old@example.test" in result["view"]
    assert not result.get("steps")


def test_private_omission_is_verified_without_disclosure(app):
    result = prepare(app, "secret")
    assert result["ok"], result
    assert all(x["status"] == "matched" for x in result["progress"]["contract"])
    assert "private-original" not in json.dumps(result)
    assert not app[2]


def test_autosave_failure_retains_write_evidence(app):
    result = prepare(app, "autosave")
    assert not result["ok"], result
    assert app[2] == [{"path": "/autosave", "body": {"email": "alice@example.test"}}]
    assert result["progress"]["writes"]["passed"] == 1
    assert result["outcome"]["retry"] == "may_repeat"


def test_pending_preparation_has_a_bounded_wait(app):
    started = time.monotonic()
    result = prepare(app, "busy", timeout_ms=450)
    assert not result["ok"], result
    assert result["errors"][0]["code"] == "BROWSER_FORM_PENDING"
    assert time.monotonic() - started < 8
    assert not app[2]


def test_observed_scope_semantics_are_checked_before_editing(app):
    before = observe(app, "normal")
    assert click(app, "Change scope")["ok"]
    result = app[0].prepare(scope="contact", arguments={"name": "Alice"}, observation_id=before["observation_id"])
    assert not result["ok"], result
    assert "Original" in result["view"]
    assert not result.get("steps") and not app[2]


@pytest.mark.parametrize("arguments", [{"name": "Alice", "foreign": "x"}, {"name": "Alice", "email": "invalid"}])
def test_all_arguments_are_validated_before_mutation(app, arguments):
    before = observe(app, "normal")
    result = app[0].prepare(scope="contact", arguments=arguments, observation_id=before["observation_id"])
    assert not result["ok"], result
    assert "Original" in app[0].observe()["view"]
    assert not app[2]


def test_shared_typed_fields_and_editor_are_jointly_verified(app):
    before = observe(app, "typed")
    schema = before["preparation_tools"][0]["input_schema"]["properties"]
    result = app[0].prepare(
        scope="contact",
        arguments={
            "terms": True,
            "level": "Pro",
            "due": "2026-12-01",
            "urgency": "priority",
            "summary": {
                "base_revision": schema["summary"]["x-editor"]["revision"],
                "operations": [{"op": "replace_text", "text": "Prepared description"}],
            },
        },
    )
    assert result["ok"], result
    assert all(x["status"] == "matched" for x in result["progress"]["contract"])
    assert not app[2]
    assert click(app, "Save")["ok"]
    assert app[2] == [
        {
            "path": "/save",
            "body": {
                "terms": True,
                "level": "Pro",
                "due": "2026-12-01",
                "urgency": "priority",
                "summary": "Prepared description",
            },
        }
    ]


def test_portalled_choice_remains_owned_by_its_dialog_field(app):
    observe(app, "portal")
    result = app[0].prepare(scope="contact", arguments={"name": "Alice", "priority": "High"})
    assert result["ok"], result
    assert "High" in result["view"]
    assert all(x["status"] == "matched" for x in result["progress"]["contract"])
    assert not app[2]


def test_required_fields_can_be_preserved_or_cleared_without_saving(app):
    observe(app, "required")
    first = app[0].prepare(scope="contact", arguments={"email": "alice@example.test"})
    assert first["ok"], first
    filled = app[0].prepare(scope="contact", arguments={"name": "Alice"})
    assert filled["ok"], filled
    cleared = app[0].prepare(scope="contact", arguments={"name": ""})
    assert cleared["ok"], cleared
    assert all(x["status"] == "matched" for x in cleared["progress"]["contract"])
    assert not app[2]


def test_expired_preparation_budget_starts_no_fields(app):
    result = prepare(app, "normal", timeout_ms=0)
    assert not result["ok"], result
    assert result["errors"][0]["code"] == "BROWSER_FORM_PENDING"
    assert "Original" in result["view"] and "old@example.test" in result["view"]
    assert not result.get("steps") and not app[2]
