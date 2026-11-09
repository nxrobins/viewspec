"""`viewspec studio`: rebuild on change, keep the last good build, stay on loopback, inspect the preview."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import viewspec.studio as studio_module
import viewspec.studio_inspector as studio_inspector_module
from viewspec.app_bundle import starter_app_bundle
from viewspec.cli import main as cli_main
from viewspec.intent_tools import starter_intent_payload
from viewspec.studio import (
    INSPECTOR_SCRIPT,
    STUDIO_INSPECTOR_PATH,
    STUDIO_STATUS_PATH,
    Studio,
    StudioError,
    live_reload_script,
    resolve_studio_source,
)


def _write_intent(path: Path, title: str = "Starter") -> Path:
    payload = starter_intent_payload("dashboard")
    payload["substrate"]["nodes"][payload["substrate"]["root_id"]]["attrs"]["title"] = title
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _get(url: str, *, host: str | None = None) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"Host": host} if host else {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8")


def _wait_for(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("condition did not become true")


@pytest.fixture
def running(tmp_path: Path) -> Iterator[Studio]:
    source = _write_intent(tmp_path / "viewspec.intent.json", "First Title")
    with Studio(source, port=0) as studio:
        threading.Thread(target=studio.serve, daemon=True).start()
        yield studio


def test_serves_the_compiled_page_with_live_reload(running):
    status, body = _get(running.url)
    assert status == 200
    assert "First Title" in body
    assert live_reload_script(1) in body
    assert json.loads(_get(f"{running.url.rstrip('/')}{STUDIO_STATUS_PATH}")[1]) == {"generation": 1, "errors": []}


def test_the_page_names_the_build_it_shows(running):
    # Each page carries its build's generation, so an edit that rebuilds before the page's first
    # status poll still reloads it.
    assert live_reload_script(1) in _get(running.url)[1]
    running.source.write_text(running.source.read_text(encoding="utf-8").replace("First Title", "Second"), "utf-8")
    _wait_for(lambda: running.generation == 2)
    body = _get(running.url)[1]
    assert "Second" in body
    assert live_reload_script(2) in body
    assert live_reload_script(1) not in body


def test_the_live_reload_script_takes_only_an_integer_generation():
    assert "const shown = 7;" in live_reload_script(7)
    assert "@@" not in live_reload_script(0)
    with pytest.raises(ValueError):
        live_reload_script("1; alert(1)")  # type: ignore[arg-type]


def test_serves_artifact_siblings_without_rewriting_them(running):
    status, body = _get(f"{running.url}provenance_manifest.json")
    assert status == 200
    assert STUDIO_STATUS_PATH not in body
    json.loads(body)


def test_a_source_edit_rebuilds_and_bumps_the_generation(running):
    _write_intent(running.source, "Second Title")
    _wait_for(lambda: running.generation == 2)
    assert "Second Title" in _get(running.url)[1]


def test_a_broken_edit_keeps_the_last_good_build_and_reports_the_error(running):
    running.source.write_text("{", encoding="utf-8")
    _wait_for(lambda: running.generation == 2)
    assert running.errors
    assert "First Title" in _get(running.url)[1]

    _write_intent(running.source, "Fixed Title")
    _wait_for(lambda: running.generation == 3)
    assert running.errors == []
    assert "Fixed Title" in _get(running.url)[1]


def test_a_design_edit_rebuilds(tmp_path):
    source = _write_intent(tmp_path / "viewspec.intent.json")
    design = tmp_path / "DESIGN.md"
    assert cli_main(["init", "design", "--out", str(design)]) == 0
    with Studio(source, design=design, port=0) as studio:
        assert studio.errors == []
        design.write_text("---\nname: Broken\ncolors: [\n---\n", encoding="utf-8")
        _wait_for(lambda: studio.generation == 2)
        assert studio.errors


def test_a_source_that_starts_broken_still_serves_its_errors(tmp_path):
    source = tmp_path / "viewspec.intent.json"
    source.write_text("{", encoding="utf-8")
    with Studio(source, port=0) as studio:
        threading.Thread(target=studio.serve, daemon=True).start()
        assert studio.errors
        status, body = _get(studio.url)
        assert status == 200
        assert live_reload_script(studio.generation) in body


def test_previews_an_app_bundle_and_its_screens(tmp_path):
    source = tmp_path / "viewspec.app.json"
    source.write_text(json.dumps(starter_app_bundle("internal_tool")), encoding="utf-8")
    with Studio(source, port=0) as studio:
        threading.Thread(target=studio.serve, daemon=True).start()
        assert studio.errors == []
        assert _get(studio.url)[0] == 200
        assert _get(f"{studio.url}screens/queue/artifact/index.html")[0] == 200


@pytest.mark.parametrize(
    "path",
    ["..%2f..%2f..%2fetc%2fpasswd", "%2e%2e/%2e%2e/etc/passwd", "does-not-exist.html"],
)
def test_nothing_outside_the_build_is_reachable(running, path):
    assert _get(f"{running.url}{path}")[0] == 404


def test_a_nul_byte_in_the_path_is_not_found(running):
    assert _get(f"{running.url}%00")[0] == 404


def test_refuses_foreign_host_headers(running):
    assert _get(running.url, host="evil.example")[0] == 403
    assert _get(running.url, host=f"localhost:{running.port}")[0] == 200


def test_binds_only_to_loopback(running):
    assert running.url.startswith("http://127.0.0.1:")


def test_closing_a_studio_that_never_served_does_not_hang(tmp_path):
    source = _write_intent(tmp_path / "viewspec.intent.json")
    finished = threading.Event()

    def open_and_close() -> None:
        with Studio(source, port=0):
            pass
        finished.set()

    threading.Thread(target=open_and_close, daemon=True).start()
    assert finished.wait(5)


def test_a_taken_port_is_a_coded_error(running):
    with pytest.raises(StudioError) as info:
        Studio(running.source, port=running.port)
    assert info.value.code == "STUDIO_PORT_UNAVAILABLE"


@pytest.mark.parametrize("port", [-1, 70000])
def test_an_out_of_range_port_is_a_coded_error_that_leaves_nothing_behind(tmp_path, monkeypatch, port):
    source = _write_intent(tmp_path / "viewspec.intent.json")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))

    with pytest.raises(StudioError) as info:
        Studio(source, port=port)

    assert info.value.code == "STUDIO_PORT_UNAVAILABLE"
    assert list(scratch.iterdir()) == []


def test_an_edit_during_the_first_build_is_picked_up(tmp_path, monkeypatch):
    source = _write_intent(tmp_path / "viewspec.intent.json", "First")
    compile_preview = studio_module._compile_preview
    builds: list[list[str]] = []

    def edit_after_the_first_build(*args: object) -> list[str]:
        builds.append(compile_preview(*args))
        if len(builds) == 1:
            _write_intent(source, "Second")
        return builds[-1]

    monkeypatch.setattr(studio_module, "_compile_preview", edit_after_the_first_build)

    with Studio(source, port=0) as studio:
        _wait_for(lambda: studio.generation == 2)


def test_source_discovery(tmp_path):
    with pytest.raises(StudioError) as missing:
        resolve_studio_source(None, cwd=tmp_path)
    assert missing.value.code == "STUDIO_SOURCE_NOT_FOUND"

    (tmp_path / "viewspec.intent.json").write_text("{}", encoding="utf-8")
    assert resolve_studio_source(None, cwd=tmp_path).name == "viewspec.intent.json"

    (tmp_path / "viewspec.app.json").write_text("{}", encoding="utf-8")
    with pytest.raises(StudioError) as ambiguous:
        resolve_studio_source(None, cwd=tmp_path)
    assert ambiguous.value.code == "STUDIO_SOURCE_AMBIGUOUS"
    assert "viewspec.app.json and viewspec.intent.json" in ambiguous.value.message


def test_source_discovery_finds_a_compact_source(tmp_path):
    (tmp_path / "viewspec.compact.json").write_text("{}", encoding="utf-8")
    assert resolve_studio_source(None, cwd=tmp_path).name == "viewspec.compact.json"

    (tmp_path / "viewspec.intent.json").write_text("{}", encoding="utf-8")
    with pytest.raises(StudioError) as ambiguous:
        resolve_studio_source(None, cwd=tmp_path)
    assert ambiguous.value.code == "STUDIO_SOURCE_AMBIGUOUS"
    assert "viewspec.intent.json and viewspec.compact.json" in ambiguous.value.message


def test_cli_reports_a_missing_source_with_exit_two(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli_main(["studio", "--no-open"]) == 2
    assert "STUDIO_SOURCE_NOT_FOUND" in capsys.readouterr().err


# inspector ------------------------------------------------------------------------------------


def _inspector_url(studio: Studio) -> str:
    return f"{studio.url.rstrip('/')}{STUDIO_INSPECTOR_PATH}"


def _inspector(studio: Studio) -> dict:
    status, body = _get(_inspector_url(studio))
    assert status == 200
    return json.loads(body)


def _served_artifact(studio: Studio) -> str:
    """The page Studio serves at "/" without the scripts it injects."""
    return _get(studio.url)[1].replace(live_reload_script(studio.generation), "").replace(INSPECTOR_SCRIPT, "")


def test_the_inspector_leaves_the_artifact_bytes_alone(running, tmp_path):
    out = tmp_path / "dist"
    assert cli_main(["compile", str(running.source), "--out", str(out)]) == 0
    compiled = (out / "index.html").read_bytes()
    built = (running.build_dir / "index.html").read_bytes()
    assert hashlib.sha256(built).hexdigest() == hashlib.sha256(compiled).hexdigest()
    assert (running.build_dir / "provenance_manifest.json").read_bytes() == (
        out / "provenance_manifest.json"
    ).read_bytes()

    file_text = built.decode("utf-8")
    assert INSPECTOR_SCRIPT not in file_text
    assert STUDIO_INSPECTOR_PATH not in file_text
    served = _get(running.url)[1]
    assert INSPECTOR_SCRIPT in served
    assert served.replace(live_reload_script(running.generation), "").replace(INSPECTOR_SCRIPT, "") == file_text


def test_inspector_json_maps_every_binding_to_its_source(running):
    source = json.loads(running.source.read_text(encoding="utf-8"))
    payload = _inspector(running)
    assert payload["schema_version"] == 1
    assert payload["generation"] == 1
    assert payload["source_kind"] == "intent_bundle"
    assert payload["errors"] == []
    (screen,) = payload["screens"]
    assert screen["id"] is None
    assert screen["source_path"] == "$"

    bindings = source["view_spec"]["bindings"]
    assert set(screen["bindings"]) == {binding["id"] for binding in bindings}
    for index, binding in enumerate(bindings):
        entry = screen["bindings"][binding["id"]]
        node_id, attr = binding["address"].removeprefix("node:").split("#attr:")
        assert entry["source_path"] == f"$.view_spec.bindings[{index}]"
        assert entry["content_paths"] == [f"$.substrate.nodes.{node_id}.attrs.{attr}"]
        assert entry["present_as"] == binding["present_as"]
        assert entry["motif_id"] == "metrics"
        assert entry["motif_kind"] == "dashboard"
        assert entry["region_id"] == binding["target_region"]
        assert entry["ir_id"] == f"binding_{binding['id']}"
        assert entry["dom_id"] == f"dom-binding_{binding['id']}"
        assert screen["ir"][entry["ir_id"]] == entry
    assert screen["actions"] == {}
    assert screen["ir"]["motif_metrics"]["source_path"] == "$.view_spec.motifs[0]"
    assert screen["ir"]["motif_metrics_revenue"]["source_path"] == "$.view_spec.motifs[0]"
    assert screen["ir"]["region_root"]["source_path"] == "$.view_spec.regions[0]"
    assert screen["ir"]["region_main"]["source_path"] == "$.view_spec.regions[1]"
    title = screen["ir"]["planner_starter_dashboard_page_title"]
    assert title["source_path"] == "$.view_spec"
    assert title["content_paths"] == ["$.substrate.nodes.starter_dashboard.attrs.title"]

    artifact = _served_artifact(running)
    assert set(re.findall(r'data-ir-id="([^"]+)"', artifact)) == set(screen["ir"])
    assert set(re.findall(r'data-binding-id="([^"]+)"', artifact)) == set(screen["bindings"])


def test_inspector_json_maps_every_action_to_its_source(tmp_path):
    source = tmp_path / "viewspec.intent.json"
    source.write_text(json.dumps(starter_intent_payload("form")), encoding="utf-8")
    with Studio(source, port=0) as studio:
        threading.Thread(target=studio.serve, daemon=True).start()
        (screen,) = _inspector(studio)["screens"]
        artifact = _served_artifact(studio)

    assert list(screen["actions"]) == ["submit_contact"]
    entry = screen["actions"]["submit_contact"]
    assert entry["source_path"] == "$.view_spec.actions[0]"
    assert entry["action_kind"] == "submit"
    assert entry["ir_id"] == "action_submit_contact"
    assert entry["binding_id"] is None
    assert entry["motif_id"] == "contact"
    assert entry["motif_kind"] == "form"
    assert entry["region_id"] == "main"
    assert entry["content_paths"] == ["$.substrate.nodes.name.attrs.value", "$.substrate.nodes.email.attrs.value"]
    assert screen["ir"]["action_submit_contact"] == entry
    assert screen["bindings"]["name_value"]["present_as"] == "input"
    assert re.findall(r'data-action-id="([^"]+)"', artifact) == ["submit_contact"]


def test_inspector_json_prefixes_app_bundle_paths_with_the_screen(tmp_path):
    source = tmp_path / "viewspec.app.json"
    source.write_text(json.dumps(starter_app_bundle("internal_tool")), encoding="utf-8")
    with Studio(source, port=0) as studio:
        threading.Thread(target=studio.serve, daemon=True).start()
        payload = _inspector(studio)
        status, body = _get(f"{_inspector_url(studio)}?id=region_root&screen=detail")

    assert payload["source_kind"] == "app_bundle"
    assert [screen["id"] for screen in payload["screens"]] == ["queue", "detail"]
    assert [screen["source_path"] for screen in payload["screens"]] == [
        "$.screens[0].intent_bundle",
        "$.screens[1].intent_bundle",
    ]
    queue = payload["screens"][0]
    assert queue["actions"]["open_incident"]["source_path"] == "$.screens[0].intent_bundle.view_spec.actions[0]"
    assert queue["actions"]["open_incident"]["action_kind"] == "navigate"
    assert queue["bindings"]["inc_1042_value"]["source_path"] == "$.screens[0].intent_bundle.view_spec.bindings[1]"
    assert queue["bindings"]["inc_1042_value"]["content_paths"] == [
        "$.screens[0].intent_bundle.substrate.nodes.inc_1042.attrs.value"
    ]
    assert status == 200
    one = json.loads(body)
    assert one["screen"] == "detail"
    assert one["source_path"] == "$.screens[1].intent_bundle.view_spec.regions[0]"


def test_inspector_lookups_by_id(running):
    url = _inspector_url(running)
    status, body = _get(f"{url}?id=revenue_value")
    assert status == 200
    entry = json.loads(body)
    assert entry["generation"] == 1
    assert entry["screen"] is None
    assert entry["source_path"] == "$.view_spec.bindings[1]"
    assert json.loads(_get(f"{url}?id=binding_revenue_value")[1])["source_path"] == "$.view_spec.bindings[1]"


def test_inspector_refuses_like_the_rest_of_studio(running):
    url = _inspector_url(running)
    assert _get(f"{url}?id=not_in_this_build") == _get(f"{running.url}not_in_this_build.json") == (404, "Not found.")
    assert _get(f"{url}?id=") == (404, "Not found.")
    assert _get(f"{url}?id=revenue_value&screen=queue") == (404, "Not found.")
    assert _get(url, host="evil.example") == _get(running.url, host="evil.example") == (403, "Forbidden.")
    assert _get(url, host=f"localhost:{running.port}")[0] == 200


def test_a_rebuild_refreshes_the_inspector_map_and_a_broken_edit_keeps_it(running):
    _write_intent(running.source, "Second Title")
    _wait_for(lambda: running.generation == 2)
    assert _inspector(running)["generation"] == 2

    running.source.write_text("{", encoding="utf-8")
    _wait_for(lambda: running.generation == 3)
    assert running.errors
    payload = _inspector(running)
    assert payload["generation"] == 2
    assert "revenue_value" in payload["screens"][0]["bindings"]


def test_rebuild_bumps_the_generation_only_after_the_build_is_in_place(tmp_path):
    source = _write_intent(tmp_path / "viewspec.intent.json", "First Title")
    # (generation, build_dir, errors, inspector generation) at the moment each generation is assigned.
    published: list[tuple[object, Path | None, list[str], object]] = []

    class RecordingStudio(Studio):
        def __setattr__(self, name: str, value: object) -> None:
            if name == "generation" and value:
                published.append((value, self.build_dir, list(self.errors), self.inspector["generation"]))
            super().__setattr__(name, value)

    # Not entered, so no watcher thread runs: each rebuild() below runs alone and in order.
    studio = RecordingStudio(source, port=0)
    try:
        studio.rebuild()
        first_build, first_map = studio.build_dir, studio.inspector
        assert first_build is not None and first_build.is_dir()
        assert (studio.generation, studio.errors, first_map["generation"]) == (1, [], 1)
        # The live-reload page reloads on a new generation, so the build it reloads onto is already served.
        assert published == [(1, first_build, [], 1)]

        source.write_text("{", encoding="utf-8")
        studio.rebuild()
        assert studio.generation == 2
        assert studio.errors
        assert studio.build_dir == first_build and first_build.is_dir()
        assert studio.inspector is first_map
        assert published[1] == (2, first_build, studio.errors, 1)
    finally:
        studio.close()


def test_a_map_that_cannot_be_built_keeps_the_build_and_reports_a_coded_error(tmp_path, monkeypatch):
    def unreadable(*args: object) -> dict:
        raise RuntimeError("manifest went away")

    monkeypatch.setattr(studio_inspector_module, "_screen_map", unreadable)
    source = _write_intent(tmp_path / "viewspec.intent.json", "Still Served")
    with Studio(source, port=0) as studio:
        threading.Thread(target=studio.serve, daemon=True).start()
        assert studio.errors == []
        assert studio.generation == 1
        assert "Still Served" in _get(studio.url)[1]
        payload = _inspector(studio)
    assert payload["generation"] == 1
    assert payload["screens"] == []
    assert [error["code"] for error in payload["errors"]] == ["STUDIO_INSPECTOR_UNAVAILABLE"]


def test_a_source_that_starts_broken_serves_an_empty_inspector_map(tmp_path):
    source = tmp_path / "viewspec.intent.json"
    source.write_text("{", encoding="utf-8")
    with Studio(source, port=0) as studio:
        threading.Thread(target=studio.serve, daemon=True).start()
        payload = _inspector(studio)
        assert _get(f"{_inspector_url(studio)}?id=anything") == (404, "Not found.")
    assert payload == {
        "schema_version": 1,
        "generation": 0,
        "source_kind": None,
        "source_format": None,
        "screens": [],
        "errors": [],
    }


@pytest.mark.parametrize("kind", ["intent_bundle", "app_bundle"])
def test_compile_output_never_carries_the_inspector(tmp_path, kind):
    if kind == "app_bundle":
        source = tmp_path / "viewspec.app.json"
        source.write_text(json.dumps(starter_app_bundle("internal_tool")), encoding="utf-8")
    else:
        source = _write_intent(tmp_path / "viewspec.intent.json")
    out = tmp_path / "dist"
    assert cli_main(["compile", str(source), "--out", str(out)]) == 0
    files = [path for path in out.rglob("*") if path.is_file()]
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert STUDIO_INSPECTOR_PATH not in text, path
        assert "data-viewspec-studio-inspector" not in text, path
        assert INSPECTOR_SCRIPT not in text, path
