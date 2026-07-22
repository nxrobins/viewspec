"""Opt-in, non-mocked browser acceptance tests for Pretext and Freerange.

These tests intentionally perform network-backed ``npm ci`` installs and launch real
Playwright Chromium.  They are excluded from ordinary runs by the ``e2e`` marker and
the explicit ``VIEWSPEC_RUN_PRETEXT_E2E=1`` environment gate.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from viewspec.app_bundle import prove_app, starter_react_app_bundle
from viewspec.app_freerange import (
    FREERANGE_NPM_INTEGRITY,
    FREERANGE_PACKAGE,
    FREERANGE_PACKAGE_TREE,
    FREERANGE_PROTOCOL,
    FREERANGE_VERSION,
)
from viewspec.app_pretext import (
    PRETEXT_NPM_INTEGRITY,
    PRETEXT_NPM_RESOLVED,
    PRETEXT_PACKAGE,
    PRETEXT_PACKAGE_TREE,
    PRETEXT_PROFILE,
    PRETEXT_PROTOCOL,
    PRETEXT_VERSION,
    PRETEXT_VIEWPORTS,
)
from viewspec.local_tools import file_hash


E2E_OPT_IN = "VIEWSPEC_RUN_PRETEXT_E2E"
E2E_ARTIFACT_DIR = "VIEWSPEC_PRETEXT_E2E_ARTIFACT_DIR"
PREBUILT_NODE_MODULES = "VIEWSPEC_HOST_VERIFY_NODE_MODULES_DIR"
REACT_APP_TARGET = "react-tailwind-app"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_NUMERIC_FUNCTIONS = (
    "clampMoveIndex",
    "addFiniteNumbers",
    "compareFiniteNumbers",
    "applySortDirection",
    "stableSortIndexDelta",
    "normalizeSliceIndex",
)


def _numeric_v3_app_bundle() -> dict[str, Any]:
    """Return a valid starter whose runtime uses every Freerange numeric helper."""
    app = starter_react_app_bundle("internal_tool")
    app["schema_version"] = 3
    app.pop("visibility")
    app["state_replay_assertions"][0].pop("expect_visibility")
    app["state"].append({"id": "triage_count", "kind": "scalar", "scope": "app", "initial": {"value": 0}})
    app["mutations"][0]["ops"].extend(
        [
            {
                "op": "move",
                "state": "incidents_state",
                "item_id": {"from_payload": "inc_1043_id"},
                "to_index": 0,
            },
            {"op": "increment", "state": "triage_count", "amount": 1},
        ]
    )
    app["selectors"][0]["ops"].extend(
        [
            {"op": "sort_by", "field": "severity", "direction": "desc"},
            {"op": "slice", "start": 0, "end": 1},
        ]
    )
    expected = app["state_replay_assertions"][0]
    expected["expect_state"]["incidents_state"] = [
        {"id": "inc_1043", "severity": "medium", "status": "investigating"},
        {"id": "inc_1042", "severity": "high", "status": "investigating"},
    ]
    expected["expect_state"]["triage_count"] = 1
    expected["expect_selectors"]["active_incidents"] = [
        {"id": "inc_1043", "severity": "medium", "status": "investigating"}
    ]
    return app


def _scenario_root(tmp_path: Path, scenario: str) -> Path:
    configured = os.environ.get(E2E_ARTIFACT_DIR)
    root = Path(configured).resolve() if configured else tmp_path
    root.mkdir(parents=True, exist_ok=True)
    scenario_root = root / scenario
    if scenario_root.exists():
        pytest.fail(
            f"Refusing to overwrite existing E2E artifact path: {scenario_root}. Choose a fresh {E2E_ARTIFACT_DIR}."
        )
    scenario_root.mkdir()
    return scenario_root


def _assert_pretext_evidence(
    report: dict[str, Any],
    *,
    required: int,
    measured: int,
    hidden: int,
    unique_inputs: int,
) -> None:
    text_layout = report["text_layout"]
    assert text_layout["status"] == "passed"
    assert text_layout["errors"] == []
    assert text_layout["font_family"] == "Arial"
    assert text_layout["profile"] == PRETEXT_PROFILE
    assert text_layout["protocol"] == PRETEXT_PROTOCOL
    assert text_layout["engine"] == {
        "name": "pretext",
        "package": PRETEXT_PACKAGE,
        "version": PRETEXT_VERSION,
        "integrity": PRETEXT_NPM_INTEGRITY,
        "package_tree_sha256": PRETEXT_PACKAGE_TREE["sha256"],
    }
    assert text_layout["viewports"] == list(PRETEXT_VIEWPORTS)
    assert text_layout["environment"]["browser"] == "chromium"
    assert text_layout["environment"]["device_scale_factor"] == 1
    assert text_layout["environment"]["font_status"] == "loaded"
    assert text_layout["environment"]["locale"].lower().startswith("en")

    coverage = text_layout["coverage"]
    assert coverage == {
        "required": required,
        "accounted": required,
        "measured": measured,
        "hidden": hidden,
        "unsupported": 0,
        "failed": 0,
    }
    assert measured > 0
    assert len(text_layout["items"]) == required
    assert {item["status"] for item in text_layout["items"]} <= {"passed", "hidden"}
    assert all("text" not in item for item in text_layout["items"])

    cache = text_layout["cache"]
    assert cache == {
        "prepare_calls": unique_inputs,
        "unique_inputs": unique_inputs,
        "layout_calls": measured,
        "cache_hits": measured - unique_inputs,
    }
    assert cache["cache_hits"] > 0

    installation = text_layout["installation"]
    assert installation["status"] == "ready"
    assert installation["errors"] == []
    assert installation["package"] == {
        "name": PRETEXT_PACKAGE,
        "version": PRETEXT_VERSION,
        "resolved": PRETEXT_NPM_RESOLVED,
        "integrity": PRETEXT_NPM_INTEGRITY,
        "tree_sha256": PRETEXT_PACKAGE_TREE["sha256"],
        "tree_bytes": PRETEXT_PACKAGE_TREE["bytes"],
        "tree_files": PRETEXT_PACKAGE_TREE["files"],
    }
    for digest_key in ("scope_digest", "observation_digest", "report_sha256"):
        assert SHA256_RE.fullmatch(text_layout[digest_key])


def _assert_freerange_evidence(report: dict[str, Any]) -> None:
    static_analysis = report["static_analysis"]
    assert static_analysis["status"] == "passed"
    assert static_analysis["errors"] == []
    assert static_analysis["findings"] == []
    assert static_analysis["required_functions"] == list(REQUIRED_NUMERIC_FUNCTIONS)
    assert static_analysis["coverage"] == {
        "required": 6,
        "observed": 6,
        "analyzed": 6,
        "fully_analyzed": 6,
        "partial": 0,
        "unsupported": 0,
    }
    engine = static_analysis["engine"]
    assert engine["name"] == "freerange"
    assert engine["package"] == FREERANGE_PACKAGE
    assert engine["version"] == FREERANGE_VERSION
    assert engine["integrity"] == FREERANGE_NPM_INTEGRITY
    assert engine["package_tree_sha256"] == FREERANGE_PACKAGE_TREE["sha256"]
    assert engine["protocol"] == FREERANGE_PROTOCOL
    assert static_analysis["runtime"]["name"] == "bun"
    assert static_analysis["runtime"]["status"] == "ready"
    assert SHA256_RE.fullmatch(static_analysis["runtime"]["sha256"])
    assert SHA256_RE.fullmatch(static_analysis["audit_transcript_hash"])


def _assert_manifest_evidence(proof_dir: Path, report: dict[str, Any], *, freerange: bool) -> None:
    manifest_path = proof_dir / "react-app" / "viewspec_app_manifest.json"
    assert Path(report["paths"]["react_app_manifest"]).resolve() == manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    text_scope = manifest["text_layout_analysis"]
    assert text_scope["status"] == "applicable"
    assert text_scope["profile"] == PRETEXT_PROFILE
    assert text_scope["protocol"] == PRETEXT_PROTOCOL
    assert text_scope["viewports"] == list(PRETEXT_VIEWPORTS)
    assert text_scope["required_observation_count"] == report["text_layout"]["coverage"]["required"]

    engine = manifest["text_layout_engine"]
    assert engine["package"] == PRETEXT_PACKAGE
    assert engine["version"] == PRETEXT_VERSION
    assert engine["resolved"] == PRETEXT_NPM_RESOLVED
    assert engine["integrity"] == PRETEXT_NPM_INTEGRITY
    assert engine["package_tree"] == PRETEXT_PACKAGE_TREE
    assert engine["font_family"] == "Arial"
    assert engine["runtime_path"] == "src/viewspec_pretext.ts"
    assert SHA256_RE.fullmatch(engine["runtime_sha256"])
    runtime_path = proof_dir / "react-app" / engine["runtime_path"]
    assert file_hash(runtime_path) == engine["runtime_sha256"]
    manifest_files = {item["path"]: item["sha256"] for item in manifest["files"]}
    assert manifest_files[engine["runtime_path"]] == engine["runtime_sha256"]
    assert manifest["runtime"]["text_layout"] == PRETEXT_PROFILE
    assert report["text_layout"]["engine"]["package"] == engine["package"]
    assert report["text_layout"]["engine"]["version"] == engine["version"]
    assert report["text_layout"]["engine"]["integrity"] == engine["integrity"]
    assert report["text_layout"]["engine"]["package_tree_sha256"] == engine["package_tree"]["sha256"]

    numeric_scope = manifest["numeric_analysis"]
    if freerange:
        assert numeric_scope["status"] == "applicable"
        assert numeric_scope["required_functions"] == list(REQUIRED_NUMERIC_FUNCTIONS)
        assert numeric_scope["kernel_path"] == "src/viewspec_numeric.ts"
        numeric_file = numeric_scope["files"][0]
        assert numeric_file["path"] == numeric_scope["kernel_path"]
        assert SHA256_RE.fullmatch(numeric_file["sha256"])
        assert file_hash(proof_dir / "react-app" / numeric_file["path"]) == numeric_file["sha256"]
        assert manifest_files[numeric_file["path"]] == numeric_file["sha256"]
        assert report["static_analysis"]["source_hashes"]["analyzed_sources"] == [
            {"path": numeric_file["path"], "sha256": numeric_file["sha256"]}
        ]
    else:
        assert numeric_scope["status"] == "not_applicable"
        assert numeric_scope["required_functions"] == []


@pytest.mark.e2e
@pytest.mark.skipif(
    os.environ.get(E2E_OPT_IN) != "1",
    reason=f"set {E2E_OPT_IN}=1 to run real npm/Bun/Playwright E2E proofs",
)
@pytest.mark.parametrize(
    ("scenario", "freerange", "expected_pretext"),
    [
        pytest.param(
            "pretext-standalone",
            False,
            {"required": 36, "measured": 33, "hidden": 3, "unique_inputs": 11},
            id="pretext-standalone",
        ),
        pytest.param(
            "pretext-freerange-composed",
            True,
            {"required": 36, "measured": 36, "hidden": 0, "unique_inputs": 12},
            id="pretext-freerange-composed",
        ),
    ],
)
def test_public_prove_app_real_pretext_browser_e2e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    freerange: bool,
    expected_pretext: dict[str, int],
) -> None:
    """Prove real standalone and composed apps through the public API boundary."""
    monkeypatch.delenv(PREBUILT_NODE_MODULES, raising=False)
    scenario_root = _scenario_root(tmp_path, scenario)
    app_path = scenario_root / "viewspec.app.json"
    proof_dir = scenario_root / "proof"
    payload = _numeric_v3_app_bundle() if freerange else starter_react_app_bundle("internal_tool")
    app_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = prove_app(
        app_path=app_path,
        out_dir=proof_dir,
        target=REACT_APP_TARGET,
        install=True,
        freerange=freerange,
        pretext=True,
        cwd=scenario_root,
    )

    assert report["ok"] is True, json.dumps(report.get("errors", []), indent=2)
    assert report["errors"] == []
    assert report["target"] == REACT_APP_TARGET
    assert report["proof_level"] == "react_app_reference_host"
    assert report["policy"]["install"] is True
    assert report["policy"]["install_command"] == "npm ci --ignore-scripts"
    assert report["policy"]["network_calls"] == "package_install_only"
    assert report["policy"]["pretext"] == "requested"

    host = report["host_report"]
    assert host["ok"] is True
    assert host["install"] is True
    assert host["errors"] == []
    assert host["policy"]["install_command"] == "npm ci --ignore-scripts"
    assert host["policy"]["browser_command"] == "npm run viewspec:verify"
    assert host["policy"]["pretext_command"] == "npm run viewspec:verify-pretext"
    assert host["assertions"] == {
        "route_count": 2,
        "history_assertion_count": 1,
        "unknown_route_assertion_count": 1,
        "state_action_count": 1,
        "rebound_binding_count": 6,
        "selector_assertion_count": 1,
        "visibility_assertion_count": 0 if freerange else 1,
    }
    assert host["phases"] == {
        "artifact_integrity": "passed",
        "typecheck": "passed",
        "freerange": "passed" if freerange else "not_requested",
        "build": "passed",
        "browser": "passed",
        "pretext": "passed",
        "final_integrity": "passed",
    }

    _assert_pretext_evidence(report, **expected_pretext)
    _assert_manifest_evidence(proof_dir, report, freerange=freerange)
    assert host["text_layout"] == report["text_layout"]
    assert report["analyses"]["pretext"] == report["text_layout"]
    assert host["analyses"]["pretext"] == report["text_layout"]

    if freerange:
        assert report["policy"]["freerange"] == "requested"
        assert host["policy"]["freerange"] == "requested"
        _assert_freerange_evidence(report)
        assert set(report["analyses"]) == {"freerange", "pretext"}
        assert report["analyses"]["freerange"] == report["static_analysis"]
        assert host["static_analysis"] == report["static_analysis"]
    else:
        assert report["policy"]["freerange"] == "not_requested"
        assert host["policy"]["freerange"] == "not_requested"
        assert set(report["analyses"]) == {"pretext"}
        assert "static_analysis" not in report
        assert "static_analysis" not in host

    report_path = proof_dir / "app_proof_report.json"
    assert Path(report["paths"]["report"]).resolve() == report_path.resolve()
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
    retained_paths = {
        "proof_summary": proof_dir / "APP_PROOF.md",
        "support_bundle": proof_dir / "app_support_bundle.json",
    }
    for path_key, expected_path in retained_paths.items():
        actual_path = Path(report["paths"][path_key])
        assert actual_path.resolve() == expected_path.resolve()
        assert actual_path.is_file()
        assert actual_path.stat().st_size > 0
