"""AppBundle V4 static-shell visibility runtime (bounded live toggling).

Playwright-free verification: structural shape of the emitted shell (one state-data block, the
reducer IIFE, exactly one runtime click listener), `node --check` syntax validity of every inline
script, the load-bearing Python==JS parity for a click's mutation sequence (SC-V4 startup no-op
included), the SC-V5 halt marker structure, safety-regex compliance, size caps, textual equivalence
of the browser reducer, determinism, and v1/v2/v3 shells staying byte-free of all of it.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_app_bundle import _stateful_app_bundle  # noqa: E402
from test_app_visibility import _visibility_app_bundle  # noqa: E402

from viewspec.app_shell import (
    APP_SHELL_STATE_DATA_ID,
    _app_shell_state_runtime_script,
    _shell_state_trigger_table,
)
from viewspec.state_ir import (
    apply_event,
    evaluate_selectors,
    evaluate_visibility,
    generate_browser_reducer_script,
    generate_typescript_reducer,
    initial_state,
    initial_visibility,
    validate_state_ir,
)

_NODE_AVAILABLE = shutil.which("node") is not None

_SCRIPT_RE = re.compile(r"<script\b([^>]*)>([\s\S]*?)</script>", re.IGNORECASE)
_INLINE_HANDLER_RE = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)


def _prove_shell(tmp_path: Path, payload: dict[str, Any], name: str = "proof") -> Path:
    from viewspec.app_bundle import prove_app

    app_path = tmp_path / f"{name}.app.json"
    app_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report = prove_app(app_path=app_path, out_dir=tmp_path / name, with_shell=True, cwd=tmp_path)
    assert report["ok"] is True, report.get("errors")
    return tmp_path / name / "app-shell"


def _inline_scripts(html: str) -> list[tuple[str, str]]:
    return [(attrs.strip(), body) for attrs, body in _SCRIPT_RE.findall(html)]


# --- structural shape --------------------------------------------------------------------------------


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="prove-app V4 requires Node.js for conformance")
def test_v4_shell_structure(tmp_path):
    shell_dir = _prove_shell(tmp_path, _visibility_app_bundle())
    html = (shell_dir / "index.html").read_text(encoding="utf-8")

    # Exactly one state-data block whose trigger table matches the declared mutations.
    data_blocks = re.findall(
        rf'<script type="application/json" id="{APP_SHELL_STATE_DATA_ID}">([\s\S]*?)</script>', html
    )
    assert len(data_blocks) == 1
    table = json.loads(data_blocks[0])
    assert table["triggers"] == _shell_state_trigger_table(_visibility_app_bundle())

    assert html.count("const ViewSpecStateRuntime = (() =>") == 1
    assert html.count("function evaluateViewSpecVisibility") == 1
    # Exactly ONE runtime click listener (the route script adds none; nav buttons use per-button
    # listeners without the data-action-id delegate).
    assert html.count("closest('[data-action-id]')") == 1
    assert "data-viewspec-state-halted" not in html  # only set at runtime, never baked

    manifest = json.loads((shell_dir / "shell_manifest.json").read_text(encoding="utf-8"))
    runtime = manifest["state_runtime"]
    assert runtime["enabled"] is True
    assert runtime["listener_count"] == 1
    assert len(runtime["reducer_js_hash"]) == 64
    assert runtime["state_js_bytes"] <= 96 * 1024


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="prove-app requires Node.js for V3+ conformance")
def test_v3_shell_carries_no_state_runtime(tmp_path):
    shell_dir = _prove_shell(tmp_path, _stateful_app_bundle(), name="v3")
    html = (shell_dir / "index.html").read_text(encoding="utf-8")
    assert "ViewSpecStateRuntime" not in html
    assert APP_SHELL_STATE_DATA_ID not in html
    assert "data-visibility-rule" not in html
    manifest = json.loads((shell_dir / "shell_manifest.json").read_text(encoding="utf-8"))
    assert "state_runtime" not in manifest


# --- syntax validity of every inline script ----------------------------------------------------------


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="node --check requires Node.js")
def test_all_inline_shell_scripts_parse(tmp_path):
    shell_dir = _prove_shell(tmp_path, _visibility_app_bundle())
    html = (shell_dir / "index.html").read_text(encoding="utf-8")
    checked = 0
    for attrs, body in _inline_scripts(html):
        if "application/json" in attrs:
            json.loads(body)  # data blocks must be valid JSON
            continue
        script_path = tmp_path / f"script-{checked}.js"
        script_path.write_text(body, encoding="utf-8")
        completed = subprocess.run(
            ["node", "--check", str(script_path)], capture_output=True, text=True, timeout=30, check=False
        )
        assert completed.returncode == 0, completed.stderr
        checked += 1
    assert checked >= 3  # route script + reducer IIFE + runtime


# --- the load-bearing parity: a click's mutation sequence, Python == generated JS (SC-V4 incl.) ------


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="parity harness requires Node.js")
def test_click_sequence_parity_and_startup_noop(tmp_path):
    payload = _visibility_app_bundle()
    state_ir, issues = validate_state_ir(payload)
    assert not issues

    # Python side: startup verdicts + verdicts after the triage click's mutation sequence.
    trigger_table = _shell_state_trigger_table(payload)
    triage = next(t for t in trigger_table if t["actionId"] == "triage_incident")
    py_initial = initial_visibility(payload, state_ir)
    current = initial_state(payload, state_ir)
    # The declared payload binding for the triage action (both mutations share this trigger, so the
    # click applies a MULTI-mutation sequence in declared order).
    assert len(triage["mutationIds"]) == 2
    payload_values = {"inc_1043_id": "inc_1043"}
    for mutation_id in triage["mutationIds"]:
        result = apply_event(current, state_ir, {"mutation_id": mutation_id, "payload_values": payload_values})
        assert result["ok"], result
    py_after = evaluate_visibility(current, evaluate_selectors(current, state_ir), state_ir)

    # JS side: import the ESM reducer artifact, replay the same click, compare maps.
    reducer_path = tmp_path / "state_reducer.mjs"
    reducer_path.write_text(generate_typescript_reducer(payload), encoding="utf-8")
    harness = tmp_path / "harness.mjs"
    harness.write_text(
        """
import { pathToFileURL } from "node:url";
const mod = await import(pathToFileURL(process.argv[2]).href);
const ids = JSON.parse(process.argv[3]);
const values = JSON.parse(process.argv[4]);
let state = mod.initialState;
const initial = mod.evaluateViewSpecVisibility(state);
for (const id of ids) state = mod.reduceViewSpecState(state, { mutation_id: id, payload_values: values });
console.log(JSON.stringify({ initial, after: mod.evaluateViewSpecVisibility(state) }));
""".lstrip(),
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["node", str(harness), str(reducer_path), json.dumps(triage["mutationIds"]), json.dumps(payload_values)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    js = json.loads(completed.stdout)
    assert js["initial"] == py_initial  # SC-V4: startup applyVisibility() is a no-op vs baked attrs
    assert js["after"] == py_after


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="prove-app V4 requires Node.js")
def test_startup_verdicts_match_baked_markers(tmp_path):
    # SC-V4 end-to-end: what the shell would apply at startup equals what compile baked.
    payload = _visibility_app_bundle()
    shell_dir = _prove_shell(tmp_path, payload)
    state_ir, _ = validate_state_ir(payload)
    verdicts = initial_visibility(payload, state_ir)
    html = (shell_dir / "index.html").read_text(encoding="utf-8")
    for rule in payload["visibility"]:
        marker = f'data-visibility-rule="{rule["id"]}" data-visibility-state="'
        start = html.index(marker) + len(marker)
        baked = html[start : html.index('"', start)]
        assert baked == ("visible" if verdicts[rule["id"]] else "hidden")


# --- SC-V5 halt marker + safety + caps ----------------------------------------------------------------


def test_runtime_script_structure_and_safety():
    script = _app_shell_state_runtime_script()
    # SC-V5: halt marker set on reducer error, cleared before each dispatch.
    assert "delete section.dataset.viewspecStateHalted;" in script
    assert "section.dataset.viewspecStateHalted = mutationId;" in script
    # Safety: the shell contract regex must not match the runtime or the reducer.
    assert not _INLINE_HANDLER_RE.search(script)
    reducer = generate_browser_reducer_script(_visibility_app_bundle())
    assert not _INLINE_HANDLER_RE.search(reducer)
    for token in ("http:", "https:", "url(", "Worker(", "importScripts"):
        assert token not in script
        assert token not in reducer
    # No console output — the halt marker is the observable signal.
    assert "console." not in script


def test_browser_reducer_is_textual_transform_only():
    payload = _visibility_app_bundle()
    module_source = generate_typescript_reducer(payload)
    browser_source = generate_browser_reducer_script(payload)
    inner = browser_source.removeprefix("const ViewSpecStateRuntime = (() => {\n")
    inner = inner[: inner.rindex("\nreturn { ")]
    assert inner == module_source.replace("\nexport const ", "\nconst ").replace("\nexport function ", "\nfunction ")
    for name in ("VIEWSPEC_STATE_PROFILE", "initialState", "reduceViewSpecState", "selectViewSpecState", "evaluateViewSpecVisibility"):
        assert name in browser_source[browser_source.rindex("return {") :]


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="prove-app V4 requires Node.js")
def test_state_js_size_cap_fails_closed(tmp_path, monkeypatch):
    import viewspec.app_shell as app_shell
    from viewspec.app_bundle import prove_app

    monkeypatch.setattr(app_shell, "APP_SHELL_MAX_STATE_JS_BYTES", 64)
    payload = _visibility_app_bundle()
    app_path = tmp_path / "viewspec.app.json"
    app_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    report = prove_app(app_path=app_path, out_dir=tmp_path / "proof", with_shell=True, cwd=tmp_path)
    assert report["ok"] is False
    assert any(error.get("code") == "APP_SHELL_SIZE_LIMIT_EXCEEDED" for error in report.get("errors", []))


# --- determinism --------------------------------------------------------------------------------------


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="prove-app V4 requires Node.js")
def test_v4_shell_is_deterministic(tmp_path):
    payload = _visibility_app_bundle()
    first = _prove_shell(tmp_path, copy.deepcopy(payload), name="one")
    second = _prove_shell(tmp_path, copy.deepcopy(payload), name="two")
    assert (first / "index.html").read_bytes() == (second / "index.html").read_bytes()
