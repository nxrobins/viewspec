"""The static shell keeps reducer data inside its inline script.

An AppBundle V4 static shell (the html-tailwind-app target) inlines the generated reducer in a
<script> element, and the HTML parser ends a script element at the first closing script tag it
meets, whatever the JavaScript around it means. The reducer writes the bundle's data as JSON
literals: the initial state, the mutations, the selectors, the visibility rules and the state text
rules. Each literal escapes <, > and &, so a value may hold markup and still stay a value.

Everything used here was already in the v0.4.0 tag; this file belongs to the 0.4.1 patch release.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import viewspec
from viewspec.app_bundle import compile_app
from viewspec.app_starters import starter_react_app_bundle
from viewspec.state_ir import (
    generate_browser_reducer_script,
    generate_javascript_reducer,
    generate_typescript_reducer,
)

# A state value that ends the inline script if the reducer writes it raw. The element after it only
# sets a marker, so nothing else can run even where a test fails.
PROBE = "</script><script>window.__vsProbe = 1</script>"
# Adds a comment opener and an ampersand: inside a script element, "<!--" followed by "<script"
# changes where the HTML parser ends the script.
MARKUP = PROBE + " <!-- & -->"

_SCRIPT_OPENER_RE = re.compile(r"<script\b", re.IGNORECASE)
_SCRIPT_CLOSER_RE = re.compile(r"</script", re.IGNORECASE)
# The parser's view of a script element: it ends at the first closing tag.
_SCRIPT_RE = re.compile(r"<script\b([^>]*)>([\s\S]*?)</script>", re.IGNORECASE)
_LITERAL_NAMES = ("initialState", "mutations", "selectors", "visibilityRules", "stateTextRules")


def _probe_bundle(note: str) -> dict[str, Any]:
    """The React starter (AppBundle V4) with one more scalar string state and a visibility rule on it."""
    app = starter_react_app_bundle()
    app["schema_version"] = 4
    app["state"].append({"id": "probe_note", "kind": "scalar", "scope": "app", "initial": {"value": note}})
    app["visibility"].append(
        {
            "id": "show_probe_note",
            "screen_id": "queue",
            "target_ref": "binding:inc_1042_severity",
            "when": {"state": "probe_note", "is": "truthy"},
        }
    )
    return app


def _bundle_with_markup_in_every_literal() -> dict[str, Any]:
    """The probe bundle with the markup in each of the five JSON literals the reducer writes."""
    app = _probe_bundle(MARKUP)
    app["visibility"][-1]["when"] = {"state": "probe_note", "equals": MARKUP}
    app["mutations"][0]["ops"].append({"op": "set", "state": "probe_note", "value": MARKUP})
    app["selectors"].append(
        {
            "id": "probe_incidents",
            "source_state": "incidents_state",
            "ops": [{"op": "filter_eq", "field": "status", "value": MARKUP}],
        }
    )
    app["state_text"] = [
        {
            "id": "probe_text",
            "screen_id": "queue",
            "target_ref": "binding:inc_1042_status",
            "state": "probe_note",
            "template": MARKUP + " {value}",
        }
    ]
    return app


def _compile(work: Path, app: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    work.mkdir()
    app_path = work / "viewspec.app.json"
    app_path.write_text(json.dumps(app, indent=2, sort_keys=True), encoding="utf-8")
    out_dir = work / "app-dist"
    return compile_app(app_path, out_dir=out_dir, cwd=work), out_dir


def _json_literals(source: str) -> dict[str, tuple[str, Any]]:
    """Each JSON literal the reducer writes, as its source text and its decoded value."""
    decoder = json.JSONDecoder()
    literals: dict[str, tuple[str, Any]] = {}
    for name in _LITERAL_NAMES:
        match = re.search(rf"^(?:export )?const {name}\b[^=\n]*= ", source, re.MULTILINE)
        if match is not None:
            value, end = decoder.raw_decode(source, match.end())
            literals[name] = (source[match.end() : end], value)
    return literals


def _initial_state_in_node(tmp_path: Path, shell: str) -> dict[str, Any]:
    """Run the shell's inline reducer, cut where the HTML parser cuts it, and read its initial state."""
    [reducer] = [body for _attrs, body in _SCRIPT_RE.findall(shell) if "const ViewSpecStateRuntime" in body]
    script = tmp_path / "initial-state.js"
    script.write_text(
        reducer + "\nprocess.stdout.write(JSON.stringify(ViewSpecStateRuntime.initialState));\n", encoding="utf-8"
    )
    completed = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="AppBundle V4 compiles check reducer conformance under Node")
def test_a_state_string_with_a_closing_script_tag_stays_inside_the_static_shell_reducer(tmp_path):
    plain_report, plain_dir = _compile(tmp_path / "plain", _probe_bundle("plain note"))
    probe_report, probe_dir = _compile(tmp_path / "probe", _probe_bundle(PROBE))

    assert plain_report["ok"] is True, plain_report["errors"]
    assert probe_report["ok"] is True, probe_report["errors"]
    assert viewspec.check(probe_dir)["ok"] is True
    plain = (plain_dir / "index.html").read_text(encoding="utf-8")
    shell = (probe_dir / "index.html").read_text(encoding="utf-8")

    # No closing tag from the data reaches the HTML, and no script element was cut short or added.
    assert "ViewSpecStateRuntime" in shell
    assert PROBE not in shell
    assert len(_SCRIPT_CLOSER_RE.findall(shell)) == len(_SCRIPT_CLOSER_RE.findall(plain))
    assert len(_SCRIPT_OPENER_RE.findall(shell)) == len(_SCRIPT_OPENER_RE.findall(plain))

    # The reducer's initial state still holds the exact string, in the browser reducer and in the
    # TypeScript reducer written beside the shell.
    assert _initial_state_in_node(tmp_path, shell)["probe_note"] == PROBE
    typescript = (probe_dir / "state_reducer.ts").read_text(encoding="utf-8")
    assert _json_literals(typescript)["initialState"][1]["probe_note"] == PROBE


@pytest.mark.parametrize(
    "generate",
    [generate_typescript_reducer, generate_javascript_reducer, generate_browser_reducer_script],
    ids=["typescript", "javascript", "browser"],
)
def test_every_reducer_json_literal_escapes_markup_characters(generate):
    source = generate(_bundle_with_markup_in_every_literal())

    assert "</script" not in source.lower()
    assert "<!--" not in source
    literals = _json_literals(source)
    assert sorted(literals) == sorted(_LITERAL_NAMES)
    for name, (text, _value) in literals.items():
        assert not {"<", ">", "&"} & set(text), f"{name} holds a raw <, > or &"

    # The escapes keep every value.
    assert literals["initialState"][1]["probe_note"] == MARKUP
    assert {"op": "set", "state": "probe_note", "value": MARKUP} in literals["mutations"][1][0]["ops"]
    assert {"op": "filter_eq", "field": "status", "value": MARKUP} in literals["selectors"][1][-1]["ops"]
    assert literals["visibilityRules"][1][-1] == {
        "id": "show_probe_note",
        "when": {"state": "probe_note", "equals": MARKUP},
    }
    assert literals["stateTextRules"][1] == [
        {"id": "probe_text", "state": "probe_note", "template": MARKUP + " {value}"}
    ]
