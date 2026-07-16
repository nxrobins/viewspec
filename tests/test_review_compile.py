from __future__ import annotations

import json
import os
from pathlib import Path
import time

from hypothesis import given, strategies as st
import pytest

import viewspec.review_compile as review_compile
from viewspec.app_bundle import starter_app_bundle
from viewspec.intent_tools import starter_intent_payload
from viewspec.review_compile import (
    DESIGN_MAX_BYTES,
    INTENT_SOURCE_MAX_BYTES,
    GenerationGate,
    bounded_review_phase,
    build_review_revision,
    capture_source_snapshot,
    load_review_revision,
)
from viewspec.review_contract import ReviewContractError
from viewspec.local_tools import check_artifact_dir


def _write_json(path, payload, *, total_bytes: int | None = None) -> bytes:
    content = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if total_bytes is not None:
        assert len(content) <= total_bytes
        content += b" " * (total_bytes - len(content))
    path.write_bytes(content)
    return content


def test_capture_intent_snapshot_reads_exact_bytes_once(tmp_path) -> None:
    source = tmp_path / "viewspec.intent.json"
    design = tmp_path / "DESIGN.md"
    source_bytes = _write_json(source, starter_intent_payload())
    design_bytes = b"---\nname: Test\n---\n"
    design.write_bytes(design_bytes)

    snapshot = capture_source_snapshot(source, design_path=design)

    assert snapshot.source_kind == "intent_bundle"
    assert snapshot.source_bytes == source_bytes
    assert snapshot.design_bytes == design_bytes
    assert len(snapshot.source_sha256) == len(snapshot.design_sha256) == 64


def test_capture_app_snapshot_detects_app_contract(tmp_path) -> None:
    source = tmp_path / "viewspec.app.json"
    source_bytes = _write_json(source, starter_app_bundle())

    snapshot = capture_source_snapshot(source)

    assert snapshot.source_kind == "app_bundle"
    assert snapshot.source_bytes == source_bytes
    assert snapshot.design_bytes is None
    assert snapshot.design_sha256 is None


def test_intent_source_limit_is_inclusive_and_limit_plus_one_fails(tmp_path) -> None:
    source = tmp_path / "viewspec.intent.json"
    _write_json(source, starter_intent_payload(), total_bytes=INTENT_SOURCE_MAX_BYTES)
    assert len(capture_source_snapshot(source).source_bytes) == INTENT_SOURCE_MAX_BYTES

    _write_json(source, starter_intent_payload(), total_bytes=INTENT_SOURCE_MAX_BYTES + 1)
    with pytest.raises(ReviewContractError) as raised:
        capture_source_snapshot(source)

    assert raised.value.code == "REVIEW_SOURCE_TOO_LARGE"


def test_design_limit_is_inclusive_and_limit_plus_one_fails(tmp_path) -> None:
    source = tmp_path / "viewspec.intent.json"
    design = tmp_path / "DESIGN.md"
    _write_json(source, starter_intent_payload())
    design.write_bytes(b"x" * DESIGN_MAX_BYTES)
    assert len(capture_source_snapshot(source, design_path=design).design_bytes or b"") == DESIGN_MAX_BYTES

    design.write_bytes(b"x" * (DESIGN_MAX_BYTES + 1))
    with pytest.raises(ReviewContractError) as raised:
        capture_source_snapshot(source, design_path=design)

    assert raised.value.code == "REVIEW_SOURCE_TOO_LARGE"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="platform has no symlink support")
def test_capture_rejects_symlink_and_hardlink_aliases(tmp_path) -> None:
    source = tmp_path / "source.json"
    _write_json(source, starter_intent_payload())
    symlink = tmp_path / "symlink.json"
    symlink.symlink_to(source)

    with pytest.raises(ReviewContractError) as raised:
        capture_source_snapshot(symlink)
    assert raised.value.code == "REVIEW_FILESYSTEM_UNSAFE"

    hardlink = tmp_path / "hardlink.json"
    os.link(source, hardlink)
    with pytest.raises(ReviewContractError) as raised:
        capture_source_snapshot(source)
    assert raised.value.code == "REVIEW_FILESYSTEM_UNSAFE"


def test_capture_rejects_atomic_replacement_during_read(tmp_path) -> None:
    source = tmp_path / "source.json"
    replacement = tmp_path / "replacement.json"
    _write_json(source, starter_intent_payload("table"))
    _write_json(replacement, starter_intent_payload("dashboard"))

    def replace_source() -> None:
        replacement.replace(source)

    with pytest.raises(ReviewContractError) as raised:
        capture_source_snapshot(source, _after_source_read=replace_source)

    assert raised.value.code == "REVIEW_SOURCE_CHANGED_DURING_CAPTURE"


@given(observations=st.integers(min_value=1, max_value=100), stale_offset=st.integers(min_value=1, max_value=99))
def test_generation_gate_only_promotes_the_latest_observation(observations: int, stale_offset: int) -> None:
    gate = GenerationGate()
    generations = [gate.observe() for _ in range(observations)]
    latest = generations[-1]

    assert gate.promote(latest) == latest
    assert gate.promoted_generation == latest

    if observations > 1:
        stale = max(1, latest - min(stale_offset, latest - 1))
        with pytest.raises(ReviewContractError) as raised:
            gate.promote(stale)
        assert raised.value.code == "REVIEW_REVISION_SUPERSEDED"
        assert gate.promoted_generation == latest


def test_build_intent_revision_promotes_an_exact_checked_artifact(tmp_path) -> None:
    source = tmp_path / "viewspec.intent.json"
    _write_json(source, starter_intent_payload())
    snapshot = capture_source_snapshot(source)
    gate = GenerationGate()
    generation = gate.observe()

    built = build_review_revision(
        snapshot,
        session_dir=tmp_path / "review-state",
        revision_number=1,
        generation=generation,
        gate=gate,
        target="html-tailwind",
    )

    assert built.revision.number == 1
    assert built.revision.source_sha256 == snapshot.source_sha256
    assert built.artifact_dir.joinpath("index.html").is_file()
    assert built.artifact_dir.joinpath("provenance_manifest.json").is_file()
    assert check_artifact_dir(built.artifact_dir)["ok"] is True
    assert built.manifest_indexes[None].manifest_sha256 == built.revision.root_manifest_sha256
    assert gate.promoted_generation == generation


def test_build_app_revision_indexes_every_checked_screen(tmp_path) -> None:
    source = tmp_path / "viewspec.app.json"
    _write_json(source, starter_app_bundle())
    snapshot = capture_source_snapshot(source)
    gate = GenerationGate()

    built = build_review_revision(
        snapshot,
        session_dir=tmp_path / "review-state",
        revision_number=1,
        generation=gate.observe(),
        gate=gate,
        target="html-tailwind-app",
    )

    assert built.revision.source_kind == "app_bundle"
    assert built.revision.root_manifest_kind == "shell_manifest"
    assert set(built.manifest_indexes) == {"queue", "detail"}
    assert built.artifact_dir.joinpath("shell_manifest.json").is_file()
    for screen_id, manifest in built.manifest_indexes.items():
        screen_artifact = built.artifact_dir / "screens" / str(screen_id) / "artifact"
        assert check_artifact_dir(screen_artifact)["ok"] is True
        assert manifest.screen_id == screen_id


def test_react_app_review_requires_explicit_locked_dependency_install(tmp_path) -> None:
    source = tmp_path / "viewspec.app.json"
    _write_json(source, starter_app_bundle())
    gate = GenerationGate()

    with pytest.raises(ReviewContractError) as raised:
        build_review_revision(
            capture_source_snapshot(source),
            session_dir=tmp_path / "review-state",
            revision_number=1,
            generation=gate.observe(),
            gate=gate,
            target="react-tailwind-app",
        )

    assert raised.value.code == "REVIEW_SOURCE_UNSUPPORTED"
    assert "--install" in raised.value.fix
    assert not (tmp_path / "review-state" / "revisions" / "1").exists()


def test_react_app_review_inlines_checked_runtime_and_survives_reload(tmp_path, monkeypatch) -> None:
    source = tmp_path / "viewspec.app.json"
    state = tmp_path / "review-state"
    _write_json(source, starter_app_bundle())

    def fake_npm(command, *, cwd, **kwargs):
        del kwargs
        if tuple(command[:3]) == ("npm", "run", "build"):
            runtime = Path(cwd) / "runtime-dist"
            assets = runtime / "assets"
            assets.mkdir(parents=True)
            assets.joinpath("main.js").write_text("document.getElementById('root').textContent='ready';", encoding="utf-8")
            assets.joinpath("main.css").write_text("body{margin:0}", encoding="utf-8")
            runtime.joinpath("index.html").write_text(
                '<!doctype html><html><head><link rel="stylesheet" crossorigin href="./assets/main.css"></head>'
                '<body><div id="root"></div><script type="module" crossorigin src="./assets/main.js"></script></body></html>',
                encoding="utf-8",
            )
        return object()

    monkeypatch.setattr("viewspec.review_compile.subprocess.run", fake_npm)
    gate = GenerationGate()
    built = build_review_revision(
        capture_source_snapshot(source),
        session_dir=state,
        revision_number=1,
        generation=gate.observe(),
        gate=gate,
        target="react-tailwind-app",
        allow_install=True,
    )

    html = built.artifact_dir.joinpath("index.html").read_text(encoding="utf-8")
    assert "<script type=\"module\">" in html
    assert "<style>body{margin:0}</style>" in html
    assert not built.artifact_dir.joinpath("assets").exists()
    assert built.revision.root_manifest_kind == "review_react_manifest"
    assert set(built.manifest_indexes) == {"queue", "detail"}
    assert load_review_revision(state, 1).revision == built.revision


def test_remote_runtime_reference_prevents_candidate_promotion(tmp_path, monkeypatch) -> None:
    source = tmp_path / "viewspec.intent.json"
    state = tmp_path / "review-state"
    _write_json(source, starter_intent_payload())
    original_compile = review_compile.compile_intent_bundle_file_tool

    def compile_with_remote(*args, **kwargs):
        result = original_compile(*args, **kwargs)
        artifact = Path(kwargs["cwd"]) / args[1] / "index.html"
        artifact.write_text(
            artifact.read_text(encoding="utf-8").replace("</body>", '<img src="https://example.test/pixel"></body>'),
            encoding="utf-8",
        )
        return result

    monkeypatch.setattr(review_compile, "compile_intent_bundle_file_tool", compile_with_remote)
    gate = GenerationGate()
    with pytest.raises(ReviewContractError) as raised:
        build_review_revision(
            capture_source_snapshot(source),
            session_dir=state,
            revision_number=1,
            generation=gate.observe(),
            gate=gate,
            target="html-tailwind",
        )

    assert raised.value.code == "REVIEW_EXTERNAL_REFERENCE_FORBIDDEN"
    assert not state.joinpath("revisions", "1").exists()


def test_invalid_app_candidate_never_replaces_last_good_revision(tmp_path) -> None:
    source = tmp_path / "viewspec.app.json"
    state = tmp_path / "review-state"
    _write_json(source, starter_app_bundle())
    gate = GenerationGate()
    first = build_review_revision(
        capture_source_snapshot(source),
        session_dir=state,
        revision_number=1,
        generation=gate.observe(),
        gate=gate,
        target="html-tailwind-app",
    )

    invalid = starter_app_bundle()
    invalid["routes"] = []
    _write_json(source, invalid)
    with pytest.raises(ReviewContractError) as raised:
        build_review_revision(
            capture_source_snapshot(source),
            session_dir=state,
            revision_number=2,
            generation=gate.observe(),
            gate=gate,
            target="html-tailwind-app",
        )

    assert raised.value.code == "REVIEW_SOURCE_INVALID"
    assert first.artifact_dir.joinpath("shell_manifest.json").is_file()
    assert not (state / "revisions" / "2").exists()


def test_stale_generation_is_rejected_before_revision_write(tmp_path) -> None:
    source = tmp_path / "viewspec.intent.json"
    _write_json(source, starter_intent_payload())
    snapshot = capture_source_snapshot(source)
    gate = GenerationGate()
    stale = gate.observe()
    gate.observe()

    with pytest.raises(ReviewContractError) as raised:
        build_review_revision(
            snapshot,
            session_dir=tmp_path / "review-state",
            revision_number=1,
            generation=stale,
            gate=gate,
            target="html-tailwind",
        )

    assert raised.value.code == "REVIEW_REVISION_SUPERSEDED"
    assert not (tmp_path / "review-state" / "revisions").exists()


def test_failed_candidate_preserves_the_last_good_revision(tmp_path) -> None:
    source = tmp_path / "viewspec.intent.json"
    state = tmp_path / "review-state"
    _write_json(source, starter_intent_payload())
    gate = GenerationGate()
    first = build_review_revision(
        capture_source_snapshot(source),
        session_dir=state,
        revision_number=1,
        generation=gate.observe(),
        gate=gate,
        target="html-tailwind",
    )

    _write_json(source, {"schema_version": 1, "substrate": {}, "view_spec": {}})
    with pytest.raises(ReviewContractError) as raised:
        build_review_revision(
            capture_source_snapshot(source),
            session_dir=state,
            revision_number=2,
            generation=gate.observe(),
            gate=gate,
            target="html-tailwind",
        )

    assert raised.value.code == "REVIEW_SOURCE_INVALID"
    assert first.revision_dir.is_dir()
    assert first.artifact_dir.joinpath("index.html").is_file()
    assert not (state / "revisions" / "2").exists()


def test_bounded_phase_interrupts_instead_of_returning_partial_success() -> None:
    with pytest.raises(ReviewContractError) as raised:
        with bounded_review_phase("REVIEW_COMPILE_TIMEOUT", 0.02):
            time.sleep(0.2)

    assert raised.value.code == "REVIEW_COMPILE_TIMEOUT"
    assert raised.value.http_status == 504
