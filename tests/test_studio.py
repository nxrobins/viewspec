"""`viewspec studio`: rebuild on change, keep the last good build, stay on loopback."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import viewspec.studio as studio_module
from viewspec.app_bundle import starter_app_bundle
from viewspec.cli import main as cli_main
from viewspec.intent_tools import starter_intent_payload
from viewspec.studio import (
    LIVE_RELOAD_SCRIPT,
    STUDIO_STATUS_PATH,
    Studio,
    StudioError,
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
    assert LIVE_RELOAD_SCRIPT in body
    assert json.loads(_get(f"{running.url.rstrip('/')}{STUDIO_STATUS_PATH}")[1]) == {"generation": 1, "errors": []}


def test_serves_artifact_siblings_without_rewriting_them(running):
    status, body = _get(f"{running.url}provenance_manifest.json")
    assert status == 200
    assert LIVE_RELOAD_SCRIPT not in body
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
        assert LIVE_RELOAD_SCRIPT in body


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


def test_cli_reports_a_missing_source_with_exit_two(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli_main(["studio", "--no-open"]) == 2
    assert "STUDIO_SOURCE_NOT_FOUND" in capsys.readouterr().err
