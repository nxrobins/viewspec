from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import viewspec.app_pretext as app_pretext
from viewspec.app_pretext import (
    PRETEXT_NPM_INTEGRITY,
    PRETEXT_NPM_RESOLVED,
    PRETEXT_PACKAGE,
    PRETEXT_PROFILE,
    PRETEXT_PROTOCOL,
    PRETEXT_VERSION,
    PretextFailure,
    build_pretext_scope,
    validate_pretext_installation,
    validate_pretext_scope,
    validate_pretext_runtime_report,
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _make_installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True)
    _write_json(
        app_dir / "package.json",
        {
            "name": "generated-app",
            "private": True,
            "dependencies": {PRETEXT_PACKAGE: PRETEXT_VERSION},
        },
    )
    _write_json(
        app_dir / "package-lock.json",
        {
            "name": "generated-app",
            "lockfileVersion": 3,
            "packages": {
                "": {
                    "name": "generated-app",
                    "dependencies": {PRETEXT_PACKAGE: PRETEXT_VERSION},
                },
                "node_modules/@chenglou/pretext": {
                    "version": PRETEXT_VERSION,
                    "resolved": PRETEXT_NPM_RESOLVED,
                    "integrity": PRETEXT_NPM_INTEGRITY,
                    "license": "MIT",
                },
            },
        },
    )
    installed = app_dir / "node_modules" / "@chenglou" / "pretext"
    _write_json(
        installed / "package.json",
        {
            "name": PRETEXT_PACKAGE,
            "version": PRETEXT_VERSION,
            "type": "module",
            "exports": {".": "./pretext.esm.js"},
        },
    )
    (installed / "pretext.esm.js").write_text("export const version = '0.0.8';\n", encoding="utf-8")
    tree = app_pretext._tree_hash(installed)
    monkeypatch.setattr(app_pretext, "PRETEXT_PACKAGE_TREE", dict(tree))
    return app_dir


def _assert_failure(code: str, operation: Any) -> PretextFailure:
    with pytest.raises(PretextFailure) as caught:
        operation()
    assert caught.value.code == code
    assert caught.value.fix
    assert caught.value.report is not None
    assert caught.value.report["status"] == "failed"
    assert caught.value.report["errors"][0]["code"] == code
    return caught.value


def _scope() -> dict[str, Any]:
    viewports = [dict(item) for item in app_pretext.PRETEXT_VIEWPORTS]
    return {
        "schema_version": 1,
        "status": "applicable",
        "profile": PRETEXT_PROFILE,
        "protocol": PRETEXT_PROTOCOL,
        "viewports": viewports,
        "screens": [
            {
                "screen_id": "queue",
                "route_id": "queue_route",
                "route_path": "/",
                "surfaces": [
                    {"surface_id": "dom-title", "ir_id": "binding_title"},
                    {"surface_id": "dom-secret", "ir_id": "binding_secret"},
                ],
            }
        ],
        "required_observation_count": 6,
    }


def _item(
    viewport_id: str,
    surface_id: str,
    ir_id: str,
    *,
    status: str,
    digest: str,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "screen_id": "queue",
        "route_id": "queue_route",
        "viewport_id": viewport_id,
        "surface_id": surface_id,
        "ir_id": ir_id,
        "input_sha256": digest,
        "input_bytes": 24,
        "status": status,
    }
    if status == "passed":
        item.update(
            {
                "available_width": 320.123456789,
                "line_height": 20.5,
                "predicted_line_count": 2,
                "observed_line_count": 2,
                "horizontal_overflow": False,
                "vertical_overflow": False,
            }
        )
    return item


def _runtime_report() -> dict[str, Any]:
    shared_input = hashlib.sha256(b"same prepared text and style").hexdigest()
    hidden_input = hashlib.sha256(b"hidden text and style").hexdigest()
    return {
        "schema_version": 1,
        "engine": {"name": "pretext", "package": PRETEXT_PACKAGE, "version": PRETEXT_VERSION},
        "profile": PRETEXT_PROFILE,
        "protocol": PRETEXT_PROTOCOL,
        "environment": {
            "browser": "chromium",
            "locale": "en-US",
            "device_scale_factor": 1,
            "font_status": "loaded",
        },
        "viewports": copy.deepcopy(_scope()["viewports"]),
        "items": [
            _item("mobile", "dom-title", "binding_title", status="passed", digest=shared_input),
            _item("tablet", "dom-title", "binding_title", status="passed", digest=shared_input),
            _item("desktop", "dom-title", "binding_title", status="passed", digest=shared_input),
            _item("mobile", "dom-secret", "binding_secret", status="hidden", digest=hidden_input),
            _item("tablet", "dom-secret", "binding_secret", status="hidden", digest=hidden_input),
            _item("desktop", "dom-secret", "binding_secret", status="hidden", digest=hidden_input),
        ],
        "summary": {
            "required": 6,
            "accounted": 6,
            "measured": 3,
            "hidden": 3,
            "unsupported": 0,
            "failed": 0,
        },
        "cache": {"prepare_calls": 1, "unique_inputs": 1, "layout_calls": 3, "cache_hits": 2},
        "errors": [],
    }


def _write_runtime_report(tmp_path: Path, report: dict[str, Any]) -> Path:
    path = tmp_path / "pretext_runtime_report.json"
    _write_json(path, report)
    return path


def test_validate_pretext_installation_returns_exact_sanitized_identity(tmp_path, monkeypatch):
    app_dir = _make_installation(tmp_path, monkeypatch)

    evidence = validate_pretext_installation(app_dir)

    assert evidence["status"] == "ready"
    assert evidence["engine"] == {
        "name": "pretext",
        "package": PRETEXT_PACKAGE,
        "version": PRETEXT_VERSION,
        "integrity": PRETEXT_NPM_INTEGRITY,
        "package_tree_sha256": app_pretext.PRETEXT_PACKAGE_TREE["sha256"],
        "profile": PRETEXT_PROFILE,
        "protocol": PRETEXT_PROTOCOL,
    }
    assert evidence["package"]["tree_files"] == 2
    assert set(evidence["configuration_sha256"]) == {
        "package_json",
        "package_lock",
        "installed_package_json",
    }
    assert all(len(value) == 64 for value in evidence["configuration_sha256"].values())
    serialized = json.dumps(evidence).lower()
    assert "raw_text" not in serialized
    assert "same prepared text and style" not in serialized


def test_pretext_installation_fails_closed_for_missing_package_version_and_lock_integrity(tmp_path, monkeypatch):
    app_dir = _make_installation(tmp_path, monkeypatch)
    (app_dir / "package-lock.json").unlink()
    _assert_failure("APP_PRETEXT_PACKAGE_MISSING", lambda: validate_pretext_installation(app_dir))

    app_dir = _make_installation(tmp_path / "version", monkeypatch)
    package_path = app_dir / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["dependencies"][PRETEXT_PACKAGE] = "^0.0.8"
    _write_json(package_path, package)
    _assert_failure("APP_PRETEXT_VERSION_MISMATCH", lambda: validate_pretext_installation(app_dir))

    app_dir = _make_installation(tmp_path / "integrity", monkeypatch)
    lock_path = app_dir / "package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["packages"]["node_modules/@chenglou/pretext"]["integrity"] = "sha512-drift"
    _write_json(lock_path, lock)
    _assert_failure("APP_PRETEXT_PACKAGE_INTEGRITY", lambda: validate_pretext_installation(app_dir))


def test_pretext_installation_rejects_installed_version_and_full_tree_drift(tmp_path, monkeypatch):
    app_dir = _make_installation(tmp_path, monkeypatch)
    installed_package_path = app_dir / "node_modules" / "@chenglou" / "pretext" / "package.json"
    installed_package = json.loads(installed_package_path.read_text(encoding="utf-8"))
    installed_package["version"] = "0.0.7"
    _write_json(installed_package_path, installed_package)
    _assert_failure("APP_PRETEXT_VERSION_MISMATCH", lambda: validate_pretext_installation(app_dir))

    app_dir = _make_installation(tmp_path / "tree", monkeypatch)
    (app_dir / "node_modules" / "@chenglou" / "pretext" / "extra.js").write_text("unexpected\n", encoding="utf-8")
    _assert_failure("APP_PRETEXT_PACKAGE_INTEGRITY", lambda: validate_pretext_installation(app_dir))


def test_pretext_installation_rejects_dev_only_package_and_lock_declarations(tmp_path, monkeypatch):
    app_dir = _make_installation(tmp_path, monkeypatch)
    package_path = app_dir / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["devDependencies"] = package.pop("dependencies")
    _write_json(package_path, package)
    _assert_failure("APP_PRETEXT_VERSION_MISMATCH", lambda: validate_pretext_installation(app_dir))

    app_dir = _make_installation(tmp_path / "lock", monkeypatch)
    lock_path = app_dir / "package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock_root = lock["packages"][""]
    lock_root["devDependencies"] = lock_root.pop("dependencies")
    _write_json(lock_path, lock)
    _assert_failure("APP_PRETEXT_PACKAGE_INTEGRITY", lambda: validate_pretext_installation(app_dir))

    app_dir = _make_installation(tmp_path / "dev-flag", monkeypatch)
    lock_path = app_dir / "package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["packages"]["node_modules/@chenglou/pretext"]["dev"] = True
    _write_json(lock_path, lock)
    _assert_failure("APP_PRETEXT_PACKAGE_INTEGRITY", lambda: validate_pretext_installation(app_dir))


def test_build_pretext_scope_is_deterministic_and_manifest_derived(tmp_path):
    output_dir = tmp_path / "react-app"
    queue_manifest = output_dir / "src" / "screens" / "queue" / "provenance_manifest.json"
    detail_manifest = output_dir / "src" / "screens" / "detail" / "provenance_manifest.json"
    _write_json(
        queue_manifest,
        {
            "version": 1,
            "manifest_schema_version": 1,
            "nodes": {
                "dom-shell": {"primitive": "surface", "ir_id": "root"},
                "dom-title": {"primitive": "text", "ir_id": "binding_title"},
                "dom-action": {"primitive": "button", "ir_id": "action_triage"},
            },
        },
    )
    _write_json(
        detail_manifest,
        {
            "version": 1,
            "manifest_schema_version": 1,
            "nodes": {
                "dom-error": {"primitive": "error_boundary", "ir_id": "error_detail"},
                "dom-input": {"primitive": "input", "ir_id": "input_owner"},
            },
        },
    )
    payload = {
        "routes": [
            {"id": "detail_route", "path": "/incident", "screen_id": "detail"},
            {"id": "queue_route", "path": "/", "screen_id": "queue"},
        ]
    }
    reports = [
        {"id": "detail", "paths": {"manifest": str(detail_manifest)}},
        {"id": "queue", "paths": {"manifest": str(queue_manifest)}},
    ]

    scope = build_pretext_scope(payload, reports, output_dir)

    assert scope["profile"] == PRETEXT_PROFILE
    assert scope["protocol"] == PRETEXT_PROTOCOL
    assert [screen["route_id"] for screen in scope["screens"]] == ["queue_route", "detail_route"]
    assert scope["screens"][0]["surfaces"] == [
        {"surface_id": "dom-action", "ir_id": "action_triage"},
        {"surface_id": "dom-title", "ir_id": "binding_title"},
    ]
    assert scope["screens"][1]["surfaces"] == [{"surface_id": "dom-error", "ir_id": "error_detail"}]
    assert scope["required_observation_count"] == 3 * len(app_pretext.PRETEXT_VIEWPORTS)


def test_build_pretext_scope_returns_strict_not_applicable_for_zero_surfaces(tmp_path):
    output_dir = tmp_path / "react-app"
    manifest = output_dir / "src" / "screens" / "form" / "provenance_manifest.json"
    _write_json(
        manifest,
        {
            "version": 1,
            "manifest_schema_version": 1,
            "nodes": {
                "dom-form": {"primitive": "surface", "ir_id": "root"},
                "dom-input": {"primitive": "input", "ir_id": "input_owner"},
            },
        },
    )

    scope = build_pretext_scope(
        {"routes": [{"id": "form_route", "path": "/", "screen_id": "form"}]},
        [{"id": "form", "paths": {"manifest": str(manifest)}}],
        output_dir,
    )

    assert scope == {
        "schema_version": 1,
        "status": "not_applicable",
        "profile": PRETEXT_PROFILE,
        "protocol": PRETEXT_PROTOCOL,
        "viewports": [dict(item) for item in app_pretext.PRETEXT_VIEWPORTS],
        "screens": [
            {
                "screen_id": "form",
                "route_id": "form_route",
                "route_path": "/",
                "surfaces": [],
            }
        ],
        "required_observation_count": 0,
    }

    invalid_applicable = copy.deepcopy(scope)
    invalid_applicable["status"] = "applicable"
    with pytest.raises(PretextFailure) as caught:
        app_pretext._validate_scope(invalid_applicable)
    assert caught.value.code == "APP_PRETEXT_SCOPE_INVALID"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda scope: scope.update(profile="tampered"),
        lambda scope: scope.update(protocol="viewspec.pretext-runtime-v2"),
        lambda scope: scope.update(required_observation_count=999),
        lambda scope: scope["viewports"][0].update(width=391),
        lambda scope: scope.update(unexpected=True),
    ],
)
def test_validate_pretext_scope_rejects_tampered_not_applicable_contract(mutation):
    scope = {
        "schema_version": 1,
        "status": "not_applicable",
        "profile": PRETEXT_PROFILE,
        "protocol": PRETEXT_PROTOCOL,
        "viewports": [dict(item) for item in app_pretext.PRETEXT_VIEWPORTS],
        "screens": [
            {
                "screen_id": "form",
                "route_id": "form_route",
                "route_path": "/",
                "surfaces": [],
            }
        ],
        "required_observation_count": 0,
    }
    mutation(scope)

    _assert_failure("APP_PRETEXT_SCOPE_INVALID", lambda: validate_pretext_scope(scope))


@pytest.mark.parametrize(
    "manifest_payload",
    [
        {"version": 1, "manifest_schema_version": 1},
        {"version": 1, "manifest_schema_version": 1, "nodes": []},
        {"version": 1, "manifest_schema_version": 1, "nodes": {"dom-title": "invalid"}},
    ],
)
def test_build_pretext_scope_rejects_missing_or_malformed_real_manifest_nodes(tmp_path, manifest_payload):
    output_dir = tmp_path / "react-app"
    manifest = output_dir / "src" / "screens" / "queue" / "provenance_manifest.json"
    _write_json(manifest, manifest_payload)

    _assert_failure(
        "APP_PRETEXT_SCOPE_INVALID",
        lambda: build_pretext_scope(
            {"routes": [{"id": "queue_route", "path": "/", "screen_id": "queue"}]},
            [{"id": "queue", "paths": {"manifest": str(manifest)}}],
            output_dir,
        ),
    )


def test_build_pretext_scope_bounds_the_real_manifest_nodes_mapping(tmp_path, monkeypatch):
    output_dir = tmp_path / "react-app"
    manifest = output_dir / "src" / "screens" / "queue" / "provenance_manifest.json"
    _write_json(
        manifest,
        {
            "version": 1,
            "manifest_schema_version": 1,
            "nodes": {
                "dom-title": {"primitive": "text", "ir_id": "binding_title"},
                "dom-action": {"primitive": "button", "ir_id": "action_triage"},
            },
        },
    )
    monkeypatch.setattr(app_pretext, "PRETEXT_MAX_MANIFEST_NODES", 1)

    _assert_failure(
        "APP_PRETEXT_SCOPE_INVALID",
        lambda: build_pretext_scope(
            {"routes": [{"id": "queue_route", "path": "/", "screen_id": "queue"}]},
            [{"id": "queue", "paths": {"manifest": str(manifest)}}],
            output_dir,
        ),
    )


def test_validate_pretext_runtime_report_accepts_exact_coverage_layout_and_cache(tmp_path):
    path = _write_runtime_report(tmp_path, _runtime_report())

    evidence = validate_pretext_runtime_report(path, _scope())

    assert evidence["status"] == "passed"
    assert evidence["coverage"] == {
        "required": 6,
        "accounted": 6,
        "measured": 3,
        "hidden": 3,
        "unsupported": 0,
        "failed": 0,
    }
    assert evidence["cache"] == {
        "prepare_calls": 1,
        "unique_inputs": 1,
        "layout_calls": 3,
        "cache_hits": 2,
    }
    assert evidence["items"][0]["available_width"] == 320.123457
    assert len(evidence["scope_sha256"]) == len(evidence["report_sha256"]) == 64
    assert "raw_text" not in json.dumps(evidence)


def test_pretext_observation_digest_is_canonical_across_report_item_order(tmp_path):
    report = _runtime_report()
    first = validate_pretext_runtime_report(_write_runtime_report(tmp_path, report), _scope())

    report["items"].reverse()
    second = validate_pretext_runtime_report(_write_runtime_report(tmp_path, report), _scope())

    canonical_items = sorted(
        first["items"],
        key=lambda item: (
            item["screen_id"],
            item["route_id"],
            item["viewport_id"],
            item["surface_id"],
        ),
    )
    assert first["observation_digest"] == second["observation_digest"]
    assert first["observation_digest"] == app_pretext._canonical_sha256(canonical_items)
    assert len(first["observation_digest"]) == 64


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda report: report.update(protocol="viewspec.pretext-runtime-v2"), "APP_PRETEXT_PROTOCOL_INVALID"),
        (lambda report: report["engine"].update(version="0.0.7"), "APP_PRETEXT_PROTOCOL_INVALID"),
        (lambda report: report["viewports"].reverse(), "APP_PRETEXT_PROTOCOL_INVALID"),
    ],
)
def test_pretext_runtime_rejects_protocol_engine_and_viewport_drift(tmp_path, mutation, code):
    report = _runtime_report()
    mutation(report)
    path = _write_runtime_report(tmp_path, report)
    _assert_failure(code, lambda: validate_pretext_runtime_report(path, _scope()))


def test_pretext_runtime_rejects_incomplete_or_contradictory_coverage(tmp_path):
    report = _runtime_report()
    report["items"].pop()
    report["summary"].update(accounted=3, hidden=1)
    path = _write_runtime_report(tmp_path, report)
    _assert_failure("APP_PRETEXT_COVERAGE_INCOMPLETE", lambda: validate_pretext_runtime_report(path, _scope()))

    report = _runtime_report()
    report["summary"]["unsupported"] = 1
    path = _write_runtime_report(tmp_path / "summary", report)
    _assert_failure("APP_PRETEXT_COVERAGE_INCOMPLETE", lambda: validate_pretext_runtime_report(path, _scope()))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: item.update(observed_line_count=3),
        lambda item: item.update(horizontal_overflow=True),
        lambda item: item.update(status="failed"),
    ],
)
def test_pretext_runtime_rejects_layout_mismatch_overflow_and_failed_status(tmp_path, mutation):
    report = _runtime_report()
    mutation(report["items"][0])
    path = _write_runtime_report(tmp_path, report)
    expected = (
        "APP_PRETEXT_LAYOUT_FAILED" if report["items"][0]["status"] == "failed" else "APP_PRETEXT_LAYOUT_MISMATCH"
    )
    _assert_failure(expected, lambda: validate_pretext_runtime_report(path, _scope()))


def test_pretext_runtime_rejects_cache_counter_drift_and_missing_cross_width_reuse(tmp_path):
    report = _runtime_report()
    report["cache"].update(prepare_calls=2, unique_inputs=2, cache_hits=0)
    path = _write_runtime_report(tmp_path, report)
    _assert_failure("APP_PRETEXT_CACHE_INVARIANT", lambda: validate_pretext_runtime_report(path, _scope()))

    report = _runtime_report()
    report["cache"]["cache_hits"] = 0
    path = _write_runtime_report(tmp_path / "reuse", report)
    _assert_failure("APP_PRETEXT_CACHE_INVARIANT", lambda: validate_pretext_runtime_report(path, _scope()))


def test_pretext_runtime_rejects_duplicate_observations_and_duplicate_json_keys(tmp_path):
    report = _runtime_report()
    report["items"].append(copy.deepcopy(report["items"][0]))
    path = _write_runtime_report(tmp_path, report)
    _assert_failure("APP_PRETEXT_DUPLICATE_EVIDENCE", lambda: validate_pretext_runtime_report(path, _scope()))

    raw = json.dumps(_runtime_report())
    raw = raw.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1', 1)
    duplicate_path = tmp_path / "duplicate-keys.json"
    duplicate_path.write_text(raw, encoding="utf-8")
    _assert_failure(
        "APP_PRETEXT_DUPLICATE_EVIDENCE",
        lambda: validate_pretext_runtime_report(duplicate_path, _scope()),
    )


def test_pretext_runtime_report_size_is_bounded_before_json_parsing(tmp_path, monkeypatch):
    path = _write_runtime_report(tmp_path, _runtime_report())
    monkeypatch.setattr(app_pretext, "PRETEXT_RUNTIME_REPORT_MAX_BYTES", 64)

    error = _assert_failure(
        "APP_PRETEXT_REPORT_TOO_LARGE",
        lambda: validate_pretext_runtime_report(path, _scope()),
    )

    assert "report_sha256" not in error.report


def test_pretext_runtime_hash_binds_the_exact_parsed_bytes(tmp_path, monkeypatch):
    report = _runtime_report()
    path = _write_runtime_report(tmp_path, report)
    original_bytes = path.read_bytes()
    replacement = copy.deepcopy(report)
    replacement["items"][0]["available_width"] = 321.123456789
    replacement_bytes = (json.dumps(replacement, indent=2, sort_keys=True) + "\n").encode()
    original_hash_file = app_pretext._hash_file
    original_json_loads = app_pretext.json.loads
    swaps: list[str] = []

    def swap_after_hash(candidate, limit, **kwargs):
        evidence = original_hash_file(candidate, limit, **kwargs)
        candidate.write_bytes(replacement_bytes)
        swaps.append("hash")
        return evidence

    def swap_after_buffer_read(raw, *args, **kwargs):
        if not swaps:
            path.write_bytes(replacement_bytes)
        swaps.append("loads")
        return original_json_loads(raw, *args, **kwargs)

    monkeypatch.setattr(app_pretext, "_hash_file", swap_after_hash)
    monkeypatch.setattr(app_pretext.json, "loads", swap_after_buffer_read)

    evidence = validate_pretext_runtime_report(path, _scope())

    assert swaps[-1] == "loads"
    assert evidence["report_sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert evidence["report_sha256"] != hashlib.sha256(path.read_bytes()).hexdigest()
    assert evidence["report_bytes"] == len(original_bytes)
    assert evidence["items"][0]["available_width"] == 320.123457
