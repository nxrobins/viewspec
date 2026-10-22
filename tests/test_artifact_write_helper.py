"""The shared artifact-set writer: ordered atomic writes, one sentinel, one warning (Q-3, Q-10)."""

import json
import logging
from pathlib import Path

import pytest

from viewspec.emitters import _write
from viewspec.emitters._write import ARTIFACT_WRITE_FAILED_SENTINEL, write_artifact_set, write_text_atomic
from viewspec.emitters.html_tailwind import emit_compiler_result as emit_html
from viewspec.emitters.react_tsx import emit_compiler_result as emit_react
from viewspec.emitters.react_tailwind_tsx import emit_compiler_result as emit_react_tailwind
from viewspec.raw_html import compile_html, write_html_compile_result
from viewspec.sdk.builder import ViewSpecBuilder
from viewspec.types import DEFAULT_STYLE_TOKEN_VALUES
from viewspec import compile as compile_bundle

EXPECTED_SENTINEL = {"code": "ARTIFACT_WRITE_FAILED", "message": "disk full", "severity": "error", "version": 1}


def _compiled():
    builder = ViewSpecBuilder("write_helper", root_attrs={"title": "Write helper"})
    dashboard = builder.add_dashboard("numbers", group_id="metrics")
    dashboard.add_card(label="Open", value="4", id="open")
    return compile_bundle(builder.build_bundle()).result


def _fail_on(monkeypatch, failing_name: str | None):
    """Make the shared writer fail for ``failing_name`` (every file when None) and record the write order."""
    order: list[str] = []
    real = write_text_atomic

    def fake(path: Path, text: str) -> None:
        order.append(path.name)
        if failing_name is None or path.name == failing_name:
            raise OSError("disk full")
        real(path, text)

    monkeypatch.setattr(_write, "write_text_atomic", fake)
    return order


def test_write_artifact_set_writes_in_order_and_consumes_generators_lazily(tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"

    def files():
        yield first, "one"
        # The raw-HTML writer derives the manifest from the index.html it just wrote.
        yield second, first.read_text(encoding="utf-8") + " two"

    write_artifact_set(tmp_path, files())

    assert second.read_text(encoding="utf-8") == "one two"
    assert not (tmp_path / ARTIFACT_WRITE_FAILED_SENTINEL).exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["first.txt", "second.txt"]


@pytest.mark.parametrize(
    ("emit", "artifact_name"),
    [(emit_html, "index.html"), (emit_react, "ViewSpecView.tsx"), (emit_react_tailwind, "ViewSpecView.tsx")],
)
def test_every_emitter_records_the_same_sentinel_and_reraises(tmp_path, monkeypatch, emit, artifact_name):
    order = _fail_on(monkeypatch, "diagnostics.json")
    out = tmp_path / "out"

    with pytest.raises(OSError, match="disk full"):
        emit(_compiled(), dict(DEFAULT_STYLE_TOKEN_VALUES), output_dir=out)

    assert order == [artifact_name, "provenance_manifest.json", "diagnostics.json", ARTIFACT_WRITE_FAILED_SENTINEL]
    assert json.loads((out / ARTIFACT_WRITE_FAILED_SENTINEL).read_text(encoding="utf-8")) == EXPECTED_SENTINEL
    assert (out / artifact_name).exists()
    assert not (out / "diagnostics.json").exists()


def test_raw_html_writer_records_the_same_sentinel(tmp_path, monkeypatch):
    order = _fail_on(monkeypatch, "lift.json")
    result = compile_html("<html><body><main><h1>Title</h1><p>Body</p></main></body></html>", source_name="page.html")

    with pytest.raises(OSError, match="disk full"):
        write_html_compile_result(result, tmp_path, include_lift=True)

    assert order == ["index.html", "provenance_manifest.json", "diagnostics.json", "lift.json", ARTIFACT_WRITE_FAILED_SENTINEL]
    assert json.loads((tmp_path / ARTIFACT_WRITE_FAILED_SENTINEL).read_text(encoding="utf-8")) == EXPECTED_SENTINEL
    manifest = json.loads((tmp_path / "provenance_manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifact_hash"]


def test_sentinel_failure_is_logged_once_under_the_callers_logger(tmp_path, monkeypatch, caplog):
    _fail_on(monkeypatch, None)
    out = tmp_path / "out"

    with caplog.at_level(logging.WARNING, logger="viewspec"):
        with pytest.raises(OSError, match="disk full"):
            emit_html(_compiled(), dict(DEFAULT_STYLE_TOKEN_VALUES), output_dir=out)

    records = [record for record in caplog.records if "Could not record the artifact write failure" in record.getMessage()]
    assert len(records) == 1
    assert records[0].name == "viewspec.emitters.html_tailwind"
    assert records[0].exc_info is not None
    assert str(out / ARTIFACT_WRITE_FAILED_SENTINEL) in records[0].getMessage()
    assert not (out / ARTIFACT_WRITE_FAILED_SENTINEL).exists()
