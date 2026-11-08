"""The Studio inspector map: source paths that resolve or are absent, never guessed; an overlay that is plain JS."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from viewspec.error_codes import ERROR_CODES
from viewspec.intent_tools import compile_intent_bundle_file_tool, starter_intent_payload
from viewspec.studio_inspector import (
    INSPECTOR_SCRIPT,
    STUDIO_INSPECTOR_PATH,
    _key,
    _SourceIndex,
    build_inspector_map,
    empty_inspector_map,
    find_inspector_entry,
)

_NODE_AVAILABLE = shutil.which("node") is not None


def _compiled_starter(tmp_path: Path, kind: str = "dashboard") -> tuple[Path, Path]:
    source = tmp_path / "viewspec.intent.json"
    source.write_text(json.dumps(starter_intent_payload(kind)), encoding="utf-8")
    out = tmp_path / "dist"
    report = compile_intent_bundle_file_tool(source, out, allow_outside_cwd=True)
    assert report["ok"], report
    return source, out


def test_the_map_describes_the_build_it_was_made_from(tmp_path):
    source, out = _compiled_starter(tmp_path)
    payload = build_inspector_map(source, out, generation=7)
    assert payload["generation"] == 7
    assert payload["source_kind"] == "intent_bundle"
    assert payload["errors"] == []
    (screen,) = payload["screens"]
    manifest = json.loads((out / "provenance_manifest.json").read_text(encoding="utf-8"))
    assert set(screen["ir"]) == {entry["ir_id"] for entry in manifest["nodes"].values()}
    entry = screen["bindings"]["priority_value"]
    assert entry["present_as"] == "badge"
    assert entry["source_path"] == "$.view_spec.bindings[7]"
    assert entry["motif_path"] == "$.view_spec.motifs[0]"
    assert entry["region_path"] == "$.view_spec.regions[1]"
    assert entry["intent_refs"] == ["viewspec:binding:priority_value"]
    assert entry["content_refs"] == ["node:priority#attr:value"]


def test_a_source_that_cannot_be_read_gives_an_empty_map_with_a_registered_code(tmp_path):
    source, out = _compiled_starter(tmp_path)
    source.write_text("{", encoding="utf-8")
    payload = build_inspector_map(source, out, generation=2)
    assert payload["screens"] == []
    (error,) = payload["errors"]
    assert error["code"] == "STUDIO_INSPECTOR_UNAVAILABLE"
    assert error["code"] in ERROR_CODES
    assert error["message"]
    assert error["fix"]


def test_a_missing_manifest_gives_an_empty_map_not_an_exception(tmp_path):
    source, out = _compiled_starter(tmp_path)
    (out / "provenance_manifest.json").unlink()
    payload = build_inspector_map(source, out, generation=1)
    assert payload["screens"] == []
    assert payload["errors"][0]["code"] == "STUDIO_INSPECTOR_UNAVAILABLE"


def test_the_empty_map_has_the_same_shape():
    assert empty_inspector_map() == {
        "schema_version": 1,
        "generation": 0,
        "source_kind": None,
        "screens": [],
        "errors": [],
    }


def test_lookup_order_is_ir_then_binding_then_action_within_the_named_screen():
    payload = {
        "generation": 3,
        "screens": [
            {"id": "a", "ir": {"x": {"ir_id": "x", "from": "a.ir"}}, "bindings": {}, "actions": {}},
            {
                "id": "b",
                "ir": {},
                "bindings": {"x": {"ir_id": "binding_x", "from": "b.bindings"}},
                "actions": {"go": {"ir_id": "action_go", "from": "b.actions"}},
            },
        ],
    }
    assert find_inspector_entry(payload, "x") == {"generation": 3, "screen": "a", "ir_id": "x", "from": "a.ir"}
    assert find_inspector_entry(payload, "x", screen="b")["from"] == "b.bindings"
    assert find_inspector_entry(payload, "go")["from"] == "b.actions"
    assert find_inspector_entry(payload, "go", screen="a") is None
    assert find_inspector_entry(payload, "missing") is None
    assert find_inspector_entry({"screens": []}, "x") is None


def test_content_paths_resolve_against_the_source_or_are_absent():
    bundle = {
        "substrate": {
            "nodes": {
                "revenue": {"attrs": {"value": "$1"}, "slots": {"rows": ["a", "b"]}, "edges": {"next": []}},
                "my-node": {"attrs": {"v.1": "dotted"}},
            }
        },
        "view_spec": {},
    }
    index = _SourceIndex(bundle, "$")
    assert index.content_path("node:revenue#attr:value") == "$.substrate.nodes.revenue.attrs.value"
    assert index.content_path("node:revenue#slot:rows") == "$.substrate.nodes.revenue.slots.rows"
    assert index.content_path("node:revenue#slot:rows[1]") == "$.substrate.nodes.revenue.slots.rows[1]"
    assert index.content_path("node:revenue#edge:next") == "$.substrate.nodes.revenue.edges.next"
    assert index.content_path("node:revenue") == "$.substrate.nodes.revenue"
    # Ids may hold dots and dashes, which dotted JSON path cannot carry.
    assert index.content_path("node:my-node#attr:v.1") == "$.substrate.nodes['my-node'].attrs['v.1']"
    # Fail closed: a path is only given when the source holds that value.
    assert index.content_path("node:revenue#attr:missing") is None
    assert index.content_path("node:revenue#slot:rows[2]") is None
    assert index.content_path("node:revenue#slot:nope") is None
    assert index.content_path("node:revenue#edge:nope") is None
    assert index.content_path("node:ghost#attr:value") is None
    assert index.content_path("not an address") is None


def test_json_path_keys_use_bracket_form_outside_identifiers():
    assert _key("plain_name1") == ".plain_name1"
    assert _key("1leading") == "['1leading']"
    assert _key("with-dash") == "['with-dash']"
    assert _key("back\\slash") == "['back\\\\slash']"


def test_an_entry_without_a_source_binding_gets_no_fabricated_path():
    index = _SourceIndex({"substrate": {"nodes": {}}, "view_spec": {"bindings": [], "motifs": [], "regions": []}}, "$")
    node = {
        "ir_id": "binding_generated",
        "primitive": "text",
        "content_refs": ["node:ghost#attr:value"],
        "intent_refs": ["viewspec:binding:generated"],
        "style_tokens": [],
        "props": {"binding_id": "generated"},
    }
    entry = index.entry("dom-binding_generated", node)
    assert entry["binding_id"] == "generated"
    assert entry["source_path"] is None
    assert entry["content_paths"] == [None]
    assert entry["motif_id"] is None
    assert entry["region_id"] is None
    assert entry["present_as"] is None


def test_the_overlay_is_plain_script_that_talks_only_to_the_loopback_endpoint():
    assert INSPECTOR_SCRIPT.startswith("<script>")
    assert INSPECTOR_SCRIPT.endswith("</script>")
    assert INSPECTOR_SCRIPT.count("<script") == 1
    assert "@@" not in INSPECTOR_SCRIPT
    assert f'"{STUDIO_INSPECTOR_PATH}"' in INSPECTOR_SCRIPT
    assert "http" not in INSPECTOR_SCRIPT.lower(), "the overlay must not name any origin"
    assert "import" not in INSPECTOR_SCRIPT
    assert 'event.code === "KeyI"' in INSPECTOR_SCRIPT
    assert "data-viewspec-studio-inspector" in INSPECTOR_SCRIPT
    assert "data-viewspec-app-screen" in INSPECTOR_SCRIPT
    # The registry test scans every string literal in src/viewspec, so the script may not spell a code.
    assert not re.findall(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+", INSPECTOR_SCRIPT)


@pytest.mark.skipif(not _NODE_AVAILABLE, reason="node --check requires Node.js")
def test_the_overlay_parses_as_javascript(tmp_path):
    script = tmp_path / "inspector.js"
    script.write_text(INSPECTOR_SCRIPT.removeprefix("<script>").removesuffix("</script>"), encoding="utf-8")
    subprocess.run(["node", "--check", str(script)], check=True, capture_output=True)
