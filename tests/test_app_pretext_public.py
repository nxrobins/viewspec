from __future__ import annotations

import json
from pathlib import Path

import viewspec.app_bundle as app_bundle
import viewspec.cli as cli
from viewspec.app_pretext import (
    PRETEXT_NPM_INTEGRITY,
    PRETEXT_NPM_RESOLVED,
    PRETEXT_PACKAGE,
    PRETEXT_PACKAGE_TREE,
    PRETEXT_PROFILE,
    PRETEXT_PROTOCOL,
    PRETEXT_VERSION,
    build_pretext_scope,
)
from viewspec.app_tools import prove_app_tool
from viewspec.local_tools import file_hash


REACT_APP_TARGET = "react-tailwind-app"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _passed_static_analysis() -> dict[str, object]:
    return {
        "status": "passed",
        "engine": {
            "name": "freerange",
            "package": "@chenglou/freerange",
            "version": "0.0.1",
            "integrity": "sha512-test",
        },
        "runtime": {"name": "bun", "version": "1.3.14", "sha256": "b" * 64},
        "coverage": {
            "required": 6,
            "observed": 6,
            "fully_analyzed": 6,
            "partial": 0,
            "unsupported": 0,
        },
        "required_functions": ["clampMoveIndex"],
        "source_hashes": {"analyzed_sources": []},
        "findings": [],
        "audit_transcript_hash": "a" * 64,
        "errors": [],
    }


def _passed_text_layout() -> dict[str, object]:
    return {
        "status": "passed",
        "engine": {
            "name": "pretext",
            "package": PRETEXT_PACKAGE,
            "version": PRETEXT_VERSION,
            "integrity": PRETEXT_NPM_INTEGRITY,
            "package_tree_sha256": PRETEXT_PACKAGE_TREE["sha256"],
        },
        "profile": PRETEXT_PROFILE,
        "protocol": PRETEXT_PROTOCOL,
        "environment": {
            "browser": "chromium",
            "locale": "en-US",
            "device_scale_factor": 1,
            "font_status": "loaded",
        },
        "coverage": {
            "required": 6,
            "accounted": 6,
            "measured": 6,
            "hidden": 0,
            "unsupported": 0,
            "failed": 0,
        },
        "cache": {
            "prepare_calls": 2,
            "unique_inputs": 2,
            "layout_calls": 6,
            "cache_hits": 4,
        },
        "scope_digest": "c" * 64,
        "observation_digest": "d" * 64,
        "report_sha256": "e" * 64,
        "errors": [],
    }


def _proof_result(*, freerange: bool, pretext: bool) -> dict[str, object]:
    static_analysis = _passed_static_analysis() if freerange else None
    text_layout = _passed_text_layout() if pretext else None
    analyses = {
        **({"freerange": static_analysis} if static_analysis is not None else {}),
        **({"pretext": text_layout} if text_layout is not None else {}),
    }
    return {
        "ok": True,
        "target": REACT_APP_TARGET,
        "proof_level": "react_app_reference_host",
        "policy": {"network_calls": "none"},
        "paths": {},
        "route_assertions": {},
        "screens": [],
        "app": {},
        **({"static_analysis": static_analysis} if static_analysis is not None else {}),
        **({"text_layout": text_layout} if text_layout is not None else {}),
        **({"analyses": analyses} if analyses else {}),
        "errors": [],
    }


def _host_success(
    artifact_dir: str | Path,
    *,
    install: bool,
    freerange: bool = False,
    pretext: bool = False,
) -> dict[str, object]:
    artifact = Path(artifact_dir)
    static_analysis = _passed_static_analysis() if freerange else None
    text_layout = _passed_text_layout() if pretext else None
    analyses = {
        **({"freerange": static_analysis} if static_analysis is not None else {}),
        **({"pretext": text_layout} if text_layout is not None else {}),
    }
    return {
        "schema_version": 1,
        "ok": True,
        "target": REACT_APP_TARGET,
        "artifact_dir": str(artifact),
        "app_artifact_hash": file_hash(artifact / "src" / "ViewSpecApp.tsx"),
        "manifest_hash": file_hash(artifact / "viewspec_app_manifest.json"),
        "install": install,
        "assertions": {
            "route_count": 2,
            "history_assertion_count": 1,
            "unknown_route_assertion_count": 1,
            "state_action_count": 1,
            "rebound_binding_count": 1,
            "selector_assertion_count": 1,
            "visibility_assertion_count": 1,
        },
        "policy": {"install_command": "none"},
        **({"static_analysis": static_analysis} if static_analysis is not None else {}),
        **({"text_layout": text_layout} if text_layout is not None else {}),
        **({"analyses": analyses} if analyses else {}),
        "duration_ms": 1,
        "errors": [],
    }


def test_public_prove_app_and_prove_app_tool_forward_pretext_with_false_default(monkeypatch) -> None:
    prove_calls: list[dict[str, object]] = []
    tool_calls: list[dict[str, object]] = []

    def fake_prove(**kwargs):
        prove_calls.append(kwargs)
        return {"ok": True, "errors": []}

    def fake_tool(**kwargs):
        tool_calls.append(kwargs)
        return {"ok": True, "errors": []}

    monkeypatch.setattr(app_bundle._app_pipeline, "prove_app", fake_prove)
    monkeypatch.setattr(app_bundle._app_tools, "prove_app_tool", fake_tool)

    assert app_bundle.prove_app(app_path="viewspec.app.json")["ok"] is True
    assert app_bundle.prove_app(app_path="viewspec.app.json", pretext=True)["ok"] is True
    assert app_bundle.prove_app_tool(app_path="viewspec.app.json")["ok"] is True
    assert app_bundle.prove_app_tool(app_path="viewspec.app.json", pretext=True)["ok"] is True

    assert [call["pretext"] for call in prove_calls] == [False, True]
    assert [call["pretext"] for call in tool_calls] == [False, True]


def test_prove_app_tool_exposes_composed_analysis_aliases_and_proof_identity(tmp_path) -> None:
    app_path = tmp_path / "viewspec.app.json"
    _write_json(app_path, {})
    calls: list[dict[str, object]] = []

    def fake_prove(**kwargs):
        calls.append(kwargs)
        return _proof_result(freerange=bool(kwargs["freerange"]), pretext=bool(kwargs["pretext"]))

    default = prove_app_tool(
        app_path=app_path,
        out_dir=tmp_path / "default-proof",
        target=REACT_APP_TARGET,
        cwd=tmp_path,
        _prove_app=fake_prove,
    )
    composed = prove_app_tool(
        app_path=app_path,
        out_dir=tmp_path / "composed-proof",
        target=REACT_APP_TARGET,
        freerange=True,
        pretext=True,
        cwd=tmp_path,
        _prove_app=fake_prove,
    )

    assert [call["pretext"] for call in calls] == [False, True]
    assert default["metadata"]["pretext_requested"] is False
    assert default["metadata"]["text_layout"] is None
    assert default["metadata"]["analyses"] == {}

    metadata = composed["metadata"]
    assert metadata["freerange_requested"] is True
    assert metadata["pretext_requested"] is True
    assert metadata["static_analysis"] == _passed_static_analysis()
    assert metadata["text_layout"] == _passed_text_layout()
    assert metadata["analyses"] == {
        "freerange": _passed_static_analysis(),
        "pretext": _passed_text_layout(),
    }
    assert metadata["proof_identity"]["freerange_audit_transcript_hash"] == "a" * 64
    assert metadata["proof_identity"]["pretext_scope_digest"] == "c" * 64
    assert metadata["proof_identity"]["pretext_observation_digest"] == "d" * 64
    assert metadata["proof_identity"]["pretext_report_hash"] == "e" * 64


def test_prove_app_cli_forwards_pretext_default_and_composed_opt_in(monkeypatch, capsys) -> None:
    calls: list[dict[str, object]] = []

    def fake_prove_app(**kwargs):
        calls.append(kwargs)
        return _proof_result(freerange=bool(kwargs["freerange"]), pretext=bool(kwargs["pretext"]))

    monkeypatch.setattr(cli, "prove_app", fake_prove_app)

    assert cli.main(["prove-app", "--app", "viewspec.app.json", "--json"]) == 0
    default = json.loads(capsys.readouterr().out)
    assert "text_layout" not in default

    assert (
        cli.main(
            [
                "prove-app",
                "--app",
                "viewspec.app.json",
                "--target",
                REACT_APP_TARGET,
                "--freerange",
                "--pretext",
                "--json",
            ]
        )
        == 0
    )
    composed = json.loads(capsys.readouterr().out)

    assert [call["pretext"] for call in calls] == [False, True]
    assert calls[1]["freerange"] is True
    assert composed["static_analysis"]["status"] == "passed"
    assert composed["text_layout"]["status"] == "passed"
    assert set(composed["analyses"]) == {"freerange", "pretext"}


def test_pretext_rejects_non_react_target_before_any_output_write(tmp_path) -> None:
    app_path = tmp_path / "viewspec.app.json"
    output = tmp_path / "proof"
    _write_json(app_path, app_bundle.starter_app_bundle())

    proof = app_bundle.prove_app(
        app_path=app_path,
        out_dir=output,
        report_out=output / "custom-report.json",
        pretext=True,
        cwd=tmp_path,
    )

    assert proof["ok"] is False
    assert proof["errors"][0]["code"] == "APP_PRETEXT_TARGET_UNSUPPORTED"
    assert not output.exists()


def test_requested_pretext_writes_exact_pin_runtime_manifest_and_composed_report(
    tmp_path,
    monkeypatch,
) -> None:
    app_path = tmp_path / "viewspec.app.json"
    proof_dir = tmp_path / "proof"
    _write_json(app_path, app_bundle.starter_react_app_bundle())
    host_calls: list[dict[str, bool]] = []

    def fake_host(path, *, install, freerange=False, pretext=False):
        host_calls.append({"install": install, "freerange": freerange, "pretext": pretext})
        return _host_success(
            path,
            install=install,
            freerange=freerange,
            pretext=pretext,
        )

    monkeypatch.setattr("viewspec.app_pipeline.verify_react_app_artifact_dir", fake_host)

    proof = app_bundle.prove_app(
        app_path=app_path,
        out_dir=proof_dir,
        target=REACT_APP_TARGET,
        freerange=True,
        pretext=True,
        cwd=tmp_path,
    )

    assert proof["ok"] is True, proof["errors"]
    assert host_calls == [{"install": False, "freerange": True, "pretext": True}]

    generated = proof_dir / "react-app"
    package = json.loads((generated / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((generated / "package-lock.json").read_text(encoding="utf-8"))
    manifest = json.loads((generated / "viewspec_app_manifest.json").read_text(encoding="utf-8"))
    runtime = (generated / "src" / "viewspec_pretext.ts").read_text(encoding="utf-8")
    main = (generated / "src" / "main.tsx").read_text(encoding="utf-8")

    assert package["dependencies"][PRETEXT_PACKAGE] == PRETEXT_VERSION
    assert PRETEXT_PACKAGE not in package["devDependencies"]
    assert lock["packages"][""]["dependencies"][PRETEXT_PACKAGE] == PRETEXT_VERSION
    assert lock["packages"][f"node_modules/{PRETEXT_PACKAGE}"] == {
        "version": PRETEXT_VERSION,
        "resolved": PRETEXT_NPM_RESOLVED,
        "integrity": PRETEXT_NPM_INTEGRITY,
        "license": "MIT",
    }
    assert f'import {{ prepare, layout, type PreparedText }} from "{PRETEXT_PACKAGE}";' in runtime
    assert f'const PROFILE = "{PRETEXT_PROFILE}" as const;' in runtime
    assert f'const PROTOCOL = "{PRETEXT_PROTOCOL}" as const;' in runtime
    assert f'version: "{PRETEXT_VERSION}" as const' in runtime
    assert "installViewSpecPretextProbe" in runtime
    assert 'from "./viewspec_pretext"' in main

    scope = manifest["text_layout_analysis"]
    assert scope["status"] == "applicable"
    assert scope["profile"] == PRETEXT_PROFILE
    assert scope["protocol"] == PRETEXT_PROTOCOL
    assert scope["required_observation_count"] > 0
    assert manifest["runtime"]["text_layout"] == PRETEXT_PROFILE
    assert "src/viewspec_pretext.ts" in {item["path"] for item in manifest["files"]}
    assert manifest["text_layout_engine"] == {
        "package": PRETEXT_PACKAGE,
        "version": PRETEXT_VERSION,
        "resolved": PRETEXT_NPM_RESOLVED,
        "integrity": PRETEXT_NPM_INTEGRITY,
        "package_tree": PRETEXT_PACKAGE_TREE,
        "license": "MIT",
        "font_family": "Arial",
        "runtime_path": "src/viewspec_pretext.ts",
        "runtime_sha256": file_hash(generated / "src" / "viewspec_pretext.ts"),
    }

    assert proof["policy"]["freerange"] == "requested"
    assert proof["policy"]["pretext"] == "requested"
    assert proof["static_analysis"] == proof["host_report"]["static_analysis"]
    assert proof["text_layout"] == proof["host_report"]["text_layout"]
    assert proof["analyses"] == {
        "freerange": proof["static_analysis"],
        "pretext": proof["text_layout"],
    }


def test_default_react_artifact_shape_has_no_pretext_surface(tmp_path, monkeypatch) -> None:
    app_path = tmp_path / "viewspec.app.json"
    proof_dir = tmp_path / "proof"
    _write_json(app_path, app_bundle.starter_react_app_bundle())
    monkeypatch.setattr(
        "viewspec.app_pipeline.verify_react_app_artifact_dir",
        lambda path, *, install, freerange=False, pretext=False: _host_success(
            path,
            install=install,
            freerange=freerange,
            pretext=pretext,
        ),
    )

    proof = app_bundle.prove_app(
        app_path=app_path,
        out_dir=proof_dir,
        target=REACT_APP_TARGET,
        cwd=tmp_path,
    )

    assert proof["ok"] is True, proof["errors"]
    generated = proof_dir / "react-app"
    package = json.loads((generated / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((generated / "package-lock.json").read_text(encoding="utf-8"))
    manifest = json.loads((generated / "viewspec_app_manifest.json").read_text(encoding="utf-8"))
    main = (generated / "src" / "main.tsx").read_text(encoding="utf-8")

    assert PRETEXT_PACKAGE not in package["dependencies"]
    assert PRETEXT_PACKAGE not in package["devDependencies"]
    assert f"node_modules/{PRETEXT_PACKAGE}" not in lock["packages"]
    assert "text_layout_analysis" not in manifest
    assert "text_layout_engine" not in manifest
    assert "text_layout" not in manifest["runtime"]
    assert not (generated / "src" / "viewspec_pretext.ts").exists()
    assert "viewspec_pretext" not in main
    assert "pretext_runtime" not in proof["react_app"]["paths"]
    assert "text_layout" not in proof
    assert "analyses" not in proof
    assert "pretext" not in proof["policy"]


def test_zero_text_surface_scope_is_strictly_not_applicable(tmp_path) -> None:
    generated = tmp_path / "react-app"
    manifest_path = generated / "src" / "screens" / "form" / "provenance_manifest.json"
    _write_json(
        manifest_path,
        {
            "version": 1,
            "nodes": {
                "form-root": {"primitive": "surface", "ir_id": "form_root"},
                "form-input": {"primitive": "input", "ir_id": "form_input"},
            },
        },
    )

    scope = build_pretext_scope(
        {"routes": [{"id": "form_route", "path": "/", "screen_id": "form"}]},
        [{"id": "form", "paths": {"manifest": str(manifest_path)}}],
        generated,
    )

    assert scope["status"] == "not_applicable"
    assert scope["required_observation_count"] == 0
    assert scope["screens"] == [
        {
            "screen_id": "form",
            "route_id": "form_route",
            "route_path": "/",
            "surfaces": [],
        }
    ]
