"""Golden pins for the compiled conformance corpus.

tests/test_hash_goldens.py pins the hash algorithm and the canonical JSON form. This module pins
what the emitters actually produce for the fixed corpus: per case and local target, the emitter
``artifact_hash``, the manifest's ``semantic_digest`` and the sha256 of every emitted file, frozen
in tests/fixtures/corpus_goldens.json. Determinism is therefore checked against values committed to
the repository rather than "same process, same result", so drift across Python, protobuf or OS
versions fails here instead of shipping green.

An intentional output change regenerates the file with ``python scripts/update_corpus_goldens.py``
and lands the diff for review. The ``provenance_manifest.json`` and ``viewspec_app_manifest.json``
file hashes embed the SDK version and rotate on a version bump; ``artifact_hash`` and
``semantic_digest`` do not.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.update_corpus_goldens import (
    APP_BUNDLE_SOURCES,
    APP_TARGETS,
    CORPUS_MANIFEST,
    GOLDENS_PATH,
    INTENT_TARGETS,
    REGENERATE_COMMAND,
    build_goldens,
    diff_goldens,
    render_goldens,
)
from viewspec import __version__
from viewspec.conformance import load_conformance_corpus

ROOT = Path(__file__).resolve().parents[1]
GOLDENS_TEXT = GOLDENS_PATH.read_text(encoding="utf-8")
GOLDENS = json.loads(GOLDENS_TEXT)
GOLDENS_RELATIVE = GOLDENS_PATH.relative_to(ROOT).as_posix()
CORPUS_CASE_IDS = [case.id for case in load_conformance_corpus(str(ROOT / CORPUS_MANIFEST)).cases]
SHA256 = re.compile(r"^[0-9a-f]{64}$")
HINT = f"If this output change is intentional, run `{REGENERATE_COMMAND}` and review the diff of {GOLDENS_RELATIVE}."


@pytest.fixture(scope="module")
def compiled(tmp_path_factory) -> dict:
    """Every corpus case and AppBundle compiled once, into a fresh directory, through the public API."""
    return build_goldens(tmp_path_factory.mktemp("corpus-goldens"))


def _fail_drift(label: str, lines: list[str], *, source_changed: bool) -> None:
    detail = "\n".join(f"  {line}" for line in lines)
    cause = (
        "The compiled source changed (source_hash differs), so the pins describe a different input.\n"
        if source_changed
        else ""
    )
    pytest.fail(f"Corpus golden drift for {label}:\n{detail}\n{cause}{HINT}", pytrace=False)


def _hashes(value: object) -> list[str]:
    if isinstance(value, dict):
        return [item for child in value.values() for item in _hashes(child)]
    if isinstance(value, str) and ("hash" in value or SHA256.match(value)):
        return [value]
    return []


def test_golden_file_is_canonically_rendered():
    assert render_goldens(GOLDENS) == GOLDENS_TEXT, (
        f"{GOLDENS_RELATIVE} is not in the generator's canonical form (sorted keys, two-space indent, "
        f"trailing newline); run `{REGENERATE_COMMAND}` instead of editing it by hand."
    )


def test_golden_file_is_pinned_to_the_current_sdk_version():
    assert GOLDENS["sdk_version"] == __version__, (
        f"{GOLDENS_RELATIVE} was generated for viewspec {GOLDENS['sdk_version']} but this is {__version__}. "
        f"The manifests embed the SDK version, so run `{REGENERATE_COMMAND}` in the release commit."
    )


def test_golden_file_covers_every_corpus_case_and_local_target():
    assert GOLDENS["schema_version"] == 1
    assert GOLDENS["corpus"] == CORPUS_MANIFEST
    assert GOLDENS["regenerate"] == REGENERATE_COMMAND
    assert GOLDENS["intent_targets"] == list(INTENT_TARGETS) == ["html-tailwind", "react-tsx", "react-tailwind-tsx"]
    assert GOLDENS["app_targets"] == list(APP_TARGETS) == ["html-tailwind-app", "react-tailwind-app"]
    assert sorted(GOLDENS["cases"]) == sorted(CORPUS_CASE_IDS), (
        f"Golden cases differ from the corpus; run `{REGENERATE_COMMAND}`."
    )
    for case_id, case in GOLDENS["cases"].items():
        assert sorted(case["targets"]) == sorted(INTENT_TARGETS), (
            f"{case_id}: targets differ; run `{REGENERATE_COMMAND}`."
        )
        assert SHA256.match(case["source_hash"])
        for target, pin in case["targets"].items():
            assert set(pin) == {"artifact_file", "artifact_hash", "semantic_digest", "files"}, (case_id, target)
            assert pin["artifact_file"] == ("index.html" if target == "html-tailwind" else "ViewSpecView.tsx")
            assert set(pin["files"]) == {pin["artifact_file"], "diagnostics.json", "provenance_manifest.json"}
            # artifact_hash is the sha256 of the artifact file, so the two pins must agree.
            assert pin["files"][pin["artifact_file"]] == pin["artifact_hash"], (case_id, target)
    assert sorted(GOLDENS["app_bundles"]) == sorted(APP_BUNDLE_SOURCES), (
        f"Golden app bundles differ; run `{REGENERATE_COMMAND}`."
    )
    for bundle_id, bundle in GOLDENS["app_bundles"].items():
        assert bundle["source"] == APP_BUNDLE_SOURCES[bundle_id]
        assert sorted(bundle["targets"]) == sorted(APP_TARGETS), (
            f"{bundle_id}: targets differ; run `{REGENERATE_COMMAND}`."
        )
        for target, pin in bundle["targets"].items():
            assert set(pin) == {"artifact_file", "artifact_hash", "screens", "files"}, (bundle_id, target)
            assert pin["files"][pin["artifact_file"]] == pin["artifact_hash"], (bundle_id, target)
            assert pin["screens"], (bundle_id, target)
            for screen_dir, screen in pin["screens"].items():
                assert f"{screen_dir}/provenance_manifest.json" in pin["files"], (bundle_id, target, screen_dir)
                assert set(screen) == {"artifact_hash", "semantic_digest"}
    hashes = _hashes(GOLDENS["cases"]) + _hashes(GOLDENS["app_bundles"])
    assert hashes and all(SHA256.match(value) for value in hashes), (
        "every pinned hash is a 64-character lowercase sha256"
    )


def test_golden_sources_match_the_checked_in_inputs(compiled):
    stale = [
        f"cases.{case_id}.source_hash"
        for case_id, case in GOLDENS["cases"].items()
        if case["source_hash"] != compiled["cases"][case_id]["source_hash"]
    ] + [
        f"app_bundles.{bundle_id}.source_hash"
        for bundle_id, bundle in GOLDENS["app_bundles"].items()
        if bundle["source_hash"] != compiled["app_bundles"][bundle_id]["source_hash"]
    ]
    assert not stale, (
        f"The checked-in inputs changed since the goldens were generated ({', '.join(stale)}); run `{REGENERATE_COMMAND}`."
    )


@pytest.mark.parametrize("target", INTENT_TARGETS)
@pytest.mark.parametrize("case_id", CORPUS_CASE_IDS)
def test_corpus_case_output_matches_golden(compiled, case_id, target):
    label = f"{case_id} ({target})"
    expected_case = GOLDENS["cases"].get(case_id)
    expected = (expected_case or {}).get("targets", {}).get(target)
    if expected is None:
        pytest.fail(f"No golden is pinned for {label}; run `{REGENERATE_COMMAND}`.", pytrace=False)
    actual_case = compiled["cases"][case_id]
    lines = diff_goldens(expected, actual_case["targets"][target])
    if lines:
        _fail_drift(label, lines, source_changed=expected_case["source_hash"] != actual_case["source_hash"])


@pytest.mark.parametrize("target", APP_TARGETS)
@pytest.mark.parametrize("bundle_id", sorted(APP_BUNDLE_SOURCES))
def test_app_bundle_output_matches_golden(compiled, bundle_id, target):
    label = f"{bundle_id} ({target})"
    expected_bundle = GOLDENS["app_bundles"].get(bundle_id)
    expected = (expected_bundle or {}).get("targets", {}).get(target)
    if expected is None:
        pytest.fail(f"No golden is pinned for {label}; run `{REGENERATE_COMMAND}`.", pytrace=False)
    actual_bundle = compiled["app_bundles"][bundle_id]
    lines = diff_goldens(expected, actual_bundle["targets"][target])
    if lines:
        _fail_drift(label, lines, source_changed=expected_bundle["source_hash"] != actual_bundle["source_hash"])
