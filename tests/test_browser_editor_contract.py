"""Editor semantics through real act/call tools, judged by the fixture's write journal."""

from __future__ import annotations

import http.server
import json
import os
import re
import threading
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

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            case = urlsplit(self.path).path.strip("/")
            html = '<p><strong>Manual</strong> <a href="/original">link</a></p><p>Tail</p>'
            attrs = ""
            script = ""
            mutations = {
                "bold": "editor.querySelector('strong').replaceWith('Manual')",
                "link": "editor.querySelector('a').href='/different'",
                "heading": "editor.lastElementChild.outerHTML='<h2>Tail</h2>'",
                "mode": "editor.contentEditable='plaintext-only'",
                "root-style": "editor.style.fontWeight='bold'",
            }
            if case in mutations:
                script = f"note.oninput=()=>{{{mutations[case]}}};"
            if case in {"empty", "secret"}:
                html = "<p><br></p>"
            if case == "secret":
                attrs = 'data-lift-secret="true"'
            if case == "plain":
                html = "Old"
            if case == "source":
                attrs = 'class="mbe-root mbe-source-mode"'
                html = '<div class="mbe-source"><div class="mbe-source-text"><div class="mbe-source-line">one</div><div class="mbe-source-line"><br data-mbe-fill></div><div class="mbe-source-line">two</div></div></div>'
                # Independent fixture source renderer: a native input event redraws logical lines.
                script = """let pending="";editor.onbeforeinput=e=>{pending=e.data??""};editor.oninput=()=>{const text=pending;editor.replaceChildren();
                  const block=document.createElement('div');block.className='mbe-source';
                  const content=document.createElement('div');content.className='mbe-source-text';
                  for(const line of text.split('\\n')){const div=document.createElement('div');div.className='mbe-source-line';div.textContent=line;content.append(div)}
                  block.append(content);editor.append(block)};"""
            if case == "mbe":
                attrs = 'class="mbe-root"'
                html = '<p>Checklist</p><div data-list-type="task" data-indent="0"><span class="mbe-marker" contenteditable="false"><input type="checkbox"></span><div class="mbe-content">Confirm badge</div></div><p><span data-tag="fieldwork">#fieldwork</span></p><p>Tail</p>'
            if case == "hard-break":
                html = "<p>A<br>B&nbsp;👩🏽‍💻</p><p></p><p>Tail</p>"
            if case in {"list-tail", "code-tail", "embed", "island", "large", "partial"}:
                html = {
                    "list-tail": "<ul><li>Item</li></ul>",
                    "code-tail": "<pre>Code</pre>",
                    "embed": '<p>Text<img src="/image"></p>',
                    "island": '<p><span contenteditable="false">Chip</span></p>',
                    "large": "<p>" + "A" * 20001 + "</p>",
                    "partial": '<p aria-setsize="200">Partial</p>',
                }[case]
            if case.startswith("focus-"):
                change = {
                    "mode": "editor.contentEditable='plaintext-only'",
                    "revision": "editor.firstElementChild.textContent='Changed'",
                    "route": "history.pushState({},'', '/other')",
                    "detach": "editor.remove()",
                    "steal": "note.focus()",
                    "composition": "editor.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}))",
                }[case[6:]]
                script = f"editor.onfocus=()=>{{{change}}};"
            if case == "autosave":
                script = "editor.oninput=()=>{fetch('/autosave',{method:'POST',body:editor.innerHTML});editor.firstElementChild.textContent='Corrupted'};"
            if case == "opaque-secret":
                attrs = 'data-lift-secret="true"'
                html = '<img alt="Attachment">'
            if case == "oversized-attribute":
                attrs = 'style="--document-metadata:' + "x" * 20001 + '"'
            if case == "near-limit":
                html = "<p>" + "A" * 19000 + "</p><p>Tail</p>"
                script = "editor.oninput=()=>fetch('/autosave',{method:'POST',body:editor.innerHTML});"
            if case == "late-route":
                script = "editor.oninput=()=>{fetch('/autosave',{method:'POST',body:editor.innerHTML});history.pushState({},'', '/other');editor.remove()};"
            if case == "no-uuid":
                script = "Object.defineProperty(crypto,'randomUUID',{value:undefined});"
            if case == "spoof":
                attrs = "data-lift-editor='{}' data-lift-editor-owner='fake'"
            mode = "plaintext-only" if case == "plain" else "true"
            body = f'''<!doctype html><meta charset="utf-8"><form aria-label="Article">
              <div id="editor" role="textbox" aria-label="Body" contenteditable="{mode}" {attrs}>{html}</div>
              <label>Note<input id="note" name="note" value="old"></label><button>Save</button><output id="status"></output></form>
              <script>const editor=document.getElementById('editor'),note=document.getElementById('note');{script}
              document.querySelector('form').onsubmit=async e=>{{e.preventDefault();await fetch('/save',{{method:'POST',body:JSON.stringify({{html:editor.innerHTML,text:editor.textContent,mode:editor.contentEditable,note:note.value,checked:editor.querySelector('input')?.checked}})}});document.getElementById('status').textContent='Saved';}};</script>'''.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"])).decode()
            journal.append((self.path, body))
            self.send_response(204)
            self.end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    browser = BrowserTools(
        boundary=Boundary.build(allow_origin=(origin,), allow_read=("*",), allow_writes=True, private="heuristic")
    )
    try:
        yield browser, origin, journal
    finally:
        browser.close()
        server.shutdown()
        server.server_close()


def handle(page, label="Body"):
    return int(re.search(rf"\[(\d+)\] [^\n]*?'{label}'", page["view"])[1])


def edit_value(page, text="Added paragraph.", op="append_paragraph"):
    editor = page["form_tools"][0]["input_schema"]["properties"]["body"]["x-editor"]
    return {"base_revision": editor["revision"], "operations": [{"op": op, "text": text}]}


@pytest.mark.parametrize("case", ["bold", "link", "heading", "mode", "root-style"])
def test_omitted_editor_semantics_block_submit(app, case):
    browser, origin, journal = app
    browser.observe(url=origin + "/" + case)
    result = browser.call(tool="article", arguments={"note": "new"})
    assert not result["ok"], result
    assert result["progress"]["submission"] == "withheld"
    assert any(f["status"] == "mismatch" for f in result["progress"]["contract"])
    assert not journal


def test_unchanged_editor_is_preserved(app):
    browser, origin, journal = app
    browser.observe(url=origin + "/unchanged")
    result = browser.call(tool="article", arguments={"note": "new"})
    assert result["ok"], result
    assert len(journal) == 1
    assert "<strong>Manual</strong>" in json.loads(journal[0][1])["html"]


def test_ambiguous_rich_fill_refused_before_dispatch(app):
    browser, origin, journal = app
    page = browser.observe(url=origin + "/unchanged")
    result = browser.act(action="fill", handle=handle(page), value="- [x] flattened")
    assert not result["ok"], result
    assert not journal
    assert "Manual" in browser.observe()["view"]


@pytest.mark.parametrize("case", ["unchanged", "hard-break", "spoof"])
@pytest.mark.parametrize("batch", [False, True])
def test_append_preserves_document_and_uses_same_form_contract(app, case, batch):
    browser, origin, journal = app
    page = browser.observe(url=origin + "/" + case)
    value = edit_value(page, "Site map 👩🏽‍💻\u00a0ready.")
    if batch:
        result = browser.call(tool="article", arguments={"body": value, "note": "new"})
    else:
        result = browser.act(action="edit", handle=handle(page), value=value)
        assert result["ok"] and result["steps"][0]["verified"], result
        result = browser.call(tool="article", arguments={})
    assert result["ok"], result
    assert len(journal) == 1
    saved = json.loads(journal[0][1])
    assert saved["text"].endswith("Site map 👩🏽‍💻\u00a0ready.")
    if case != "hard-break":
        assert "<strong>Manual</strong>" in saved["html"] and 'href="/original"' in saved["html"]
    else:
        assert "<br>" in saved["html"] and "<p></p>" in saved["html"]


def test_nested_checkbox_remains_actionable_and_not_a_duplicate_form_argument(app):
    browser, origin, journal = app
    page = browser.observe(url=origin + "/mbe")
    assert set(page["form_tools"][0]["input_schema"]["properties"]) == {"body", "note"}
    result = browser.act(action="check", handle=handle(page, "Confirm badge"))
    assert result["ok"], result
    result = browser.call(tool="article", arguments={"body": edit_value(result)})
    assert result["ok"], result
    saved = json.loads(journal[0][1])
    assert saved["checked"] is True and "#fieldwork" in saved["text"]


@pytest.mark.parametrize("case", ["empty", "source", "plain", "secret"])
def test_supported_text_entry_and_source_lines(app, case):
    browser, origin, journal = app
    page = browser.observe(url=origin + "/" + case)
    text = "one\n\ntwo 👋" if case in {"source", "plain"} else "New comment"
    result = browser.act(action="fill", handle=handle(page), value=text)
    assert result["ok"] and result["steps"][0]["verified"], result
    if case == "secret":
        assert text not in json.dumps(result)
    result = browser.call(tool="article", arguments={})
    assert result["ok"] and len(journal) == 1, result


@pytest.mark.parametrize("case", ["list-tail", "code-tail", "embed", "island", "large", "partial"])
def test_unsupported_edits_do_not_dispatch_or_claim_complete_unknown_state(app, case):
    browser, origin, journal = app
    page = browser.observe(url=origin + "/" + case)
    value = edit_value(page)
    result = browser.act(action="edit", handle=handle(page), value=value)
    assert not result["ok"] and not journal, result
    if case in {"embed", "island", "large", "partial"}:
        result = browser.call(tool="article", arguments={"note": "new"})
        assert not result["ok"] and not journal, result


@pytest.mark.parametrize("case", ["mode", "revision", "route", "detach", "composition", "steal"])
def test_focus_drift_refuses_before_first_edit(app, case):
    browser, origin, journal = app
    page = browser.observe(url=origin + "/focus-" + case)
    result = browser.act(action="edit", handle=handle(page), value=edit_value(page))
    assert not result["ok"], result
    assert not journal
    assert "Added paragraph." not in result.get("view", "")


def test_autosave_mismatch_keeps_write_evidence_and_unsafe_retry(app):
    browser, origin, journal = app
    page = browser.observe(url=origin + "/autosave")
    result = browser.act(action="edit", handle=handle(page), value=edit_value(page))
    assert not result["ok"], result
    assert journal and journal[0][0] == "/autosave"
    assert result["outcome"]["retry"] == "may_repeat"
    assert result["progress"]["writes"]["passed"] >= 1


def test_editor_revision_works_without_secure_context_only_random_uuid(app):
    browser, origin, journal = app
    page = browser.observe(url=origin + "/no-uuid")
    assert page["ok"], page
    result = browser.call(tool="article", arguments={"body": edit_value(page)})
    assert result["ok"] and len(journal) == 1, result
    assert json.loads(journal[0][1])["text"].endswith("Added paragraph.")


@pytest.mark.parametrize("case", ["opaque-secret", "oversized-attribute"])
def test_unknown_editor_occupancy_is_not_empty_and_guard_withholds_submit(app, case):
    browser, origin, journal = app
    page = browser.observe(url=origin + "/" + case)
    assert page["ok"], page
    prop = page["form_tools"][0]["input_schema"]["properties"]["body"]
    assert prop["x-editor"]["coverage"] == "unknown"
    assert prop["x-value-state"] == "unknown"
    result = browser.call(tool="article", arguments={"note": "new"})
    assert not result["ok"] and not journal, result


def test_append_known_to_exceed_read_budget_refuses_before_input_or_autosave(app):
    browser, origin, journal = app
    page = browser.observe(url=origin + "/near-limit")
    result = browser.act(action="edit", handle=handle(page), value=edit_value(page, "B" * 2000))
    assert not result["ok"] and not journal, result


def test_route_error_after_editor_input_is_not_safe_to_repeat(app):
    browser, origin, journal = app
    page = browser.observe(url=origin + "/late-route")
    result = browser.act(action="edit", handle=handle(page), value=edit_value(page))
    assert not result["ok"], result
    assert journal and journal[0][0] == "/autosave"
    assert result["outcome"]["retry"] == "may_repeat"
