from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from viewspec.app_bundle import (
    compile_app,
    compile_app_tool,
    init_app_tool,
    prove_app,
    starter_app_bundle,
    starter_react_app_bundle,
    validate_app_text,
)
from viewspec.app_pretext import PRETEXT_PROFILE, PRETEXT_PROTOCOL, PRETEXT_VIEWPORTS
from viewspec.app_react_verify import ReactAppVerifyFailure, _pretext_scope, verify_react_app_artifact_dir
from viewspec.app_react import _write_runtime_template
from viewspec.cli import main as cli_main
from viewspec.local_tools import file_hash


REACT_APP_TARGET = "react-tailwind-app"
ROOT = Path(__file__).resolve().parents[1]


def _react_app_bundle() -> dict:
    return starter_react_app_bundle("internal_tool")


def _write_app(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _numeric_v3_app_bundle() -> dict:
    app = starter_react_app_bundle("internal_tool")
    app["schema_version"] = 3
    app.pop("visibility")
    app["state_replay_assertions"][0].pop("expect_visibility")
    app["state"].append(
        {"id": "triage_count", "kind": "scalar", "scope": "app", "initial": {"value": 0}}
    )
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


def _passed_static_analysis() -> dict:
    return {
        "status": "passed",
        "engine": {
            "name": "freerange",
            "package": "@chenglou/freerange",
            "version": "0.0.1",
            "integrity": "sha512-test",
        },
        "runtime": {"name": "bun", "version": "1.2.0", "sha256": "b" * 64},
        "required_functions": [
            "clampMoveIndex",
            "addFiniteNumbers",
            "compareFiniteNumbers",
            "applySortDirection",
            "stableSortIndexDelta",
            "normalizeSliceIndex",
        ],
        "coverage": {
            "required": 6,
            "observed": 6,
            "fully_analyzed": 6,
            "partial": 0,
            "unsupported": 0,
        },
        "findings": [],
        "source_hashes": {"analyzed_sources": [], "call_sites": [], "configuration": [], "tools": []},
        "audit_transcript_hash": "a" * 64,
        "findings_transcript_hash": "c" * 64,
        "timings_ms": {"total": 1},
        "errors": [],
    }


def test_generated_react_apps_v1_through_v4_pass_strict_typescript(tmp_path):
    template_modules = ROOT / "src" / "viewspec" / "host_verify_template" / "node_modules"
    tsc = template_modules / ".bin" / "tsc"
    if not tsc.is_file() or not (template_modules / "@types" / "node").exists():
        pytest.skip("host verifier dependencies are not installed")

    payloads = {
        "v1": starter_app_bundle(),
        "v2": starter_app_bundle(resource_binding="fixture_readonly_v0"),
        "v3": _numeric_v3_app_bundle(),
        "v4": starter_react_app_bundle(),
    }
    for name, payload in payloads.items():
        app_path = tmp_path / f"{name}.app.json"
        output_dir = tmp_path / name
        _write_app(app_path, payload)
        result = compile_app(app_path, out_dir=output_dir, target=REACT_APP_TARGET, cwd=tmp_path)
        assert result["ok"] is True, result.get("errors")
        if name == "v3":
            kernel_path = output_dir / "src" / "viewspec_numeric.ts"
            reducer_source = (output_dir / "src" / "state_reducer.ts").read_text(encoding="utf-8")
            assert kernel_path.is_file()
            assert 'from "./viewspec_numeric"' in reducer_source
            for helper in (
                "clampMoveIndex",
                "addFiniteNumbers",
                "compareFiniteNumbers",
                "applySortDirection",
                "stableSortIndexDelta",
                "normalizeSliceIndex",
            ):
                assert f"function {helper}" in kernel_path.read_text(encoding="utf-8")
        output_dir.joinpath("node_modules").symlink_to(template_modules, target_is_directory=True)
        completed = subprocess.run(
            [str(tsc), "--noEmit", "-p", str(output_dir / "tsconfig.json")],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 0, f"{name}: {completed.stdout}{completed.stderr}"


def test_react_app_starter_is_the_runnable_golden_path(tmp_path, capsys):
    app = starter_react_app_bundle("internal_tool")
    validation = validate_app_text(json.dumps(app))

    assert validation["ok"] is True
    assert app["schema_version"] == 4
    assert app["resource_binding"] == "fixture_readonly_v0"
    assert app["interactive_state"] == "interactive_state_v0"
    assert app["state_replay_assertions"][0]["expect_visibility"] == {"show_triaged_status": True}

    out = tmp_path / "viewspec.app.json"
    exit_code = cli_main(["init-app", "--template", "react-app", "--out", str(out)])

    assert exit_code == 0
    assert json.loads(out.read_text(encoding="utf-8")) == app
    assert capsys.readouterr().out.strip() == str(out)


def test_react_app_golden_path_is_documented_for_agents_and_humans():
    expected = (
        "viewspec init-app --template react-app --out viewspec.app.json",
        "viewspec compile-app viewspec.app.json --target react-tailwind-app --out app-dist",
        "viewspec prove-app --app viewspec.app.json --target react-tailwind-app --install",
        "npm run dev",
    )
    for relative in ("README.md", "docs/getting-started.md", "docs/app-bundle-v0.md", "docs/agent-integration.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        for command in expected:
            assert command in text, f"{relative} is missing {command}"
        assert "do not edit generated react" in text.lower()


def test_compile_react_app_target_writes_runnable_checked_app(tmp_path):
    app_path = tmp_path / "viewspec.app.json"
    out_dir = tmp_path / "react-app"
    _write_app(app_path, _react_app_bundle())

    result = compile_app(app_path, out_dir=out_dir, target=REACT_APP_TARGET, cwd=tmp_path)

    assert result["ok"] is True
    assert result["target"] == REACT_APP_TARGET
    assert result["route_navigation"] == "browser_history_v1"
    assert result["runtime"]["resource_binding"] == "host_props_with_fixture_fallback_v1"
    assert result["runtime"]["state"] == "generated_reducer_v1"
    assert result["runtime"]["selectors"] == "generated_selectors_v1"
    assert result["runtime"]["visibility"] == "generated_visibility_v1"
    assert result["runtime"]["side_effects"] == "typed_callbacks_v1"
    assert result["route_assertions"]["browser_history_navigation"] is True
    assert result["state_reducer_conformance"]["ok"] is True

    expected_paths = {
        "index",
        "package_json",
        "package_lock",
        "vite_config",
        "tsconfig",
        "main",
        "app",
        "styles",
        "playwright_config",
        "runtime_test",
        "manifest",
        "diagnostics",
        "state_reducer",
        "state_manifest",
    }
    assert expected_paths.issubset(result["paths"])
    for key in expected_paths:
        assert Path(result["paths"][key]).exists(), key

    app_source = (out_dir / "src" / "ViewSpecApp.tsx").read_text(encoding="utf-8")
    queue_source = (out_dir / "src" / "screens" / "queue" / "ViewSpecView.tsx").read_text(encoding="utf-8")
    manifest = json.loads((out_dir / "viewspec_app_manifest.json").read_text(encoding="utf-8"))
    package_json = json.loads((out_dir / "package.json").read_text(encoding="utf-8"))
    runtime_test = (out_dir / "tests" / "viewspec-app.spec.ts").read_text(encoding="utf-8")

    assert 'from "./state_reducer"' in app_source
    assert "window.history.pushState" in app_source
    assert 'window.addEventListener("popstate"' in app_source
    assert "reduceViewSpecState" in app_source
    assert "selectViewSpecState" in app_source
    assert "evaluateViewSpecVisibility" in app_source
    assert "createInitialState" in app_source
    assert "resourceBindings" in app_source
    assert "onStateChange" in app_source
    assert "onAction" in app_source
    assert 'data-viewspec-app-screen="queue"' in app_source
    assert 'data-viewspec-app-screen="detail"' in app_source
    assert "data-viewspec-app-not-found" in app_source
    assert "visibility?: Record<string, boolean>" in queue_source
    assert 'data-visibility-rule={"show_triaged_status"}' in queue_source
    assert "hidden={visibility[\"show_triaged_status\"] === false}" in queue_source
    assert manifest["target"] == REACT_APP_TARGET
    assert manifest["entry_file"] == "src/ViewSpecApp.tsx"
    assert manifest["screen_count"] == 2
    assert manifest["screen_artifacts"][0]["artifact_hash"] == file_hash(
        out_dir / "src" / "screens" / "queue" / "ViewSpecView.tsx"
    )
    assert package_json["scripts"]["viewspec:verify"] == "playwright test --reporter=line"
    assert package_json["scripts"]["typecheck"] == "tsc --noEmit"
    assert "@chenglou/freerange" not in package_json["devDependencies"]
    assert "browser history, routes, and unknown path" in runtime_test
    assert "state actions rebind data and visibility" in runtime_test
    assert "inc_1043_status" in runtime_test
    assert "show_triaged_status" in runtime_test


def test_react_runtime_template_adds_pinned_freerange_only_when_requested(tmp_path):
    output_dir = tmp_path / "react-app"
    _write_runtime_template(_react_app_bundle(), output_dir, freerange=True)

    package = json.loads((output_dir / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((output_dir / "package-lock.json").read_text(encoding="utf-8"))
    assert package["devDependencies"]["@chenglou/freerange"] == "0.0.1"
    assert lock["packages"][""]["devDependencies"]["@chenglou/freerange"] == "0.0.1"
    locked = lock["packages"]["node_modules/@chenglou/freerange"]
    assert locked == {
        "version": "0.0.1",
        "resolved": "https://registry.npmjs.org/@chenglou/freerange/-/freerange-0.0.1.tgz",
        "integrity": "sha512-RCdvTZX66Dp5roRrld+2GH4tJV+uyo21nEsF/lxwDBjzDFagG9CnJ7go5Qim2ZDHTC40lQWNF1AprDxTDQTxfg==",
        "dev": True,
        "license": "MIT",
        "dependencies": {"typescript": "^6.0.2"},
        "bin": {"fr": "fr.ts"},
    }


def test_react_app_target_regenerates_from_changed_intent_without_source_edits(tmp_path):
    app = _react_app_bundle()
    app_path = tmp_path / "viewspec.app.json"
    out_dir = tmp_path / "react-app"
    _write_app(app_path, app)

    first = compile_app(app_path, out_dir=out_dir, target=REACT_APP_TARGET, cwd=tmp_path)
    assert first["ok"] is True
    queue_path = out_dir / "src" / "screens" / "queue" / "ViewSpecView.tsx"
    first_hash = file_hash(queue_path)
    first_app_hash = file_hash(out_dir / "src" / "ViewSpecApp.tsx")
    assert "// Generated by ViewSpec. Do not edit." in (out_dir / "src" / "ViewSpecApp.tsx").read_text(
        encoding="utf-8"
    )

    changed = deepcopy(app)
    changed["screens"][0]["intent_bundle"]["view_spec"]["actions"][0]["label"] = "Escalate"
    _write_app(app_path, changed)

    second = compile_app(app_path, out_dir=out_dir, target=REACT_APP_TARGET, cwd=tmp_path, force=True)

    assert second["ok"] is True
    assert file_hash(queue_path) != first_hash
    assert "Escalate" in queue_path.read_text(encoding="utf-8")
    assert file_hash(out_dir / "src" / "ViewSpecApp.tsx") == first_app_hash
    assert second["manifest_hash"] != first["manifest_hash"]


def test_react_app_target_escapes_app_title_in_html_and_tsx(tmp_path):
    app = _react_app_bundle()
    app["app"]["title"] = "Incident {Console} <Ops>"
    app_path = tmp_path / "viewspec.app.json"
    out_dir = tmp_path / "react-app"
    _write_app(app_path, app)

    result = compile_app(app_path, out_dir=out_dir, target=REACT_APP_TARGET, cwd=tmp_path)

    assert result["ok"] is True
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    source = (out_dir / "src" / "ViewSpecApp.tsx").read_text(encoding="utf-8")
    assert "Incident {Console} &lt;Ops&gt;" in html
    assert '<strong>{"Incident {Console} \\u003cOps\\u003e"}</strong>' in source


def test_compile_app_cli_accepts_react_app_target(tmp_path, capsys):
    app_path = tmp_path / "viewspec.app.json"
    out_dir = tmp_path / "react-app"
    _write_app(app_path, _react_app_bundle())

    exit_code = cli_main(
        [
            "compile-app",
            str(app_path),
            "--out",
            str(out_dir),
            "--target",
            REACT_APP_TARGET,
            "--json",
        ]
    )
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert result["ok"] is True
    assert result["target"] == REACT_APP_TARGET
    assert result["next_actions"] == ["npm ci", "npm run dev"]


def test_agent_tools_expose_react_app_starter_and_compile_target(tmp_path):
    initialized = init_app_tool("viewspec.app.json", template="react-app", cwd=tmp_path)

    assert initialized["ok"] is True
    assert initialized["metadata"]["template"] == "react-app"
    assert initialized["validation"]["app_schema_version"] == 4

    compiled = compile_app_tool(
        "viewspec.app.json",
        "app-dist",
        target=REACT_APP_TARGET,
        cwd=tmp_path,
    )

    assert compiled["ok"] is True
    assert compiled["metadata"]["target"] == REACT_APP_TARGET
    assert compiled["metadata"]["route_navigation"] == "browser_history_v1"
    assert compiled["metadata"]["app_artifact_hash"]
    assert compiled["metadata"]["manifest_hash"]
    assert compiled["paths"]["app"].endswith("src/ViewSpecApp.tsx")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda scope: scope.update(profile="tampered"),
        lambda scope: scope.update(required_observation_count=999),
        lambda scope: scope["viewports"][0].update(width=391),
        lambda scope: scope.update(unexpected=True),
    ],
)
def test_pretext_verifier_rejects_tampered_not_applicable_scope(mutation):
    scope = {
        "schema_version": 1,
        "status": "not_applicable",
        "profile": PRETEXT_PROFILE,
        "protocol": PRETEXT_PROTOCOL,
        "viewports": [dict(item) for item in PRETEXT_VIEWPORTS],
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

    with pytest.raises(ReactAppVerifyFailure) as caught:
        _pretext_scope({"text_layout_analysis": scope})

    assert caught.value.code == "APP_PRETEXT_SCOPE_INVALID"
    assert caught.value.text_layout["status"] == "failed"


def test_verify_react_app_runs_exact_generated_package(tmp_path, monkeypatch):
    app_path = tmp_path / "viewspec.app.json"
    out_dir = tmp_path / "react-app"
    _write_app(app_path, _react_app_bundle())
    compiled = compile_app(app_path, out_dir=out_dir, target=REACT_APP_TARGET, cwd=tmp_path)
    assert compiled["ok"] is True

    commands: list[tuple[str, ...]] = []

    def fake_run(command, *, cwd, timeout):
        commands.append(tuple(command))
        if tuple(command) == ("npm", "run", "viewspec:verify"):
            (cwd / "viewspec_runtime_report.json").write_text(
                json.dumps(
                    {
                        "route_count": 2,
                        "history_assertion_count": 1,
                        "unknown_route_assertion_count": 1,
                        "state_action_count": 1,
                        "rebound_binding_count": 1,
                        "selector_assertion_count": 1,
                        "visibility_assertion_count": 1,
                    }
                ),
                encoding="utf-8",
            )
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr("viewspec.app_react_verify._run_process", fake_run)

    report = verify_react_app_artifact_dir(out_dir, install=True)

    assert report["ok"] is True
    assert report["target"] == REACT_APP_TARGET
    assert report["app_artifact_hash"] == file_hash(out_dir / "src" / "ViewSpecApp.tsx")
    assert report["manifest_hash"] == file_hash(out_dir / "viewspec_app_manifest.json")
    assert report["assertions"]["route_count"] == 2
    assert report["assertions"]["state_action_count"] == 1
    assert report["assertions"]["rebound_binding_count"] == 1
    assert report["assertions"]["selector_assertion_count"] == 1
    assert report["assertions"]["visibility_assertion_count"] == 1
    assert commands == [
        ("npm", "ci", "--ignore-scripts"),
        ("npm", "run", "typecheck"),
        ("npm", "run", "build"),
        ("npm", "run", "viewspec:verify"),
    ]


def test_verify_react_app_runs_freerange_after_typecheck_before_build(tmp_path, monkeypatch):
    app_path = tmp_path / "numeric.app.json"
    out_dir = tmp_path / "react-app"
    _write_app(app_path, _numeric_v3_app_bundle())
    assert compile_app(app_path, out_dir=out_dir, target=REACT_APP_TARGET, cwd=tmp_path)["ok"]
    events: list[str] = []

    def fake_run(command, *, cwd, timeout):
        del timeout
        events.append(" ".join(command))
        if tuple(command) == ("npm", "run", "viewspec:verify"):
            (cwd / "viewspec_runtime_report.json").write_text(
                json.dumps(
                    {
                        "route_count": 2,
                        "history_assertion_count": 1,
                        "unknown_route_assertion_count": 1,
                        "state_action_count": 1,
                        "rebound_binding_count": 1,
                        "selector_assertion_count": 1,
                        "visibility_assertion_count": 0,
                    }
                ),
                encoding="utf-8",
            )
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    def fake_freerange(host_dir, manifest):
        events.append("freerange findings+audit")
        assert host_dir.name == "app"
        assert manifest["numeric_analysis"]["status"] == "applicable"
        assert len(manifest["numeric_analysis"]["required_functions"]) == 6
        return _passed_static_analysis()

    monkeypatch.setattr("viewspec.app_react_verify._run_process", fake_run)
    monkeypatch.setattr("viewspec.app_react_verify._run_freerange", fake_freerange)

    report = verify_react_app_artifact_dir(out_dir, install=True, freerange=True)

    assert report["ok"] is True, report["errors"]
    assert report["static_analysis"]["status"] == "passed"
    assert events == [
        "npm ci --ignore-scripts",
        "npm run typecheck",
        "freerange findings+audit",
        "npm run build",
        "npm run viewspec:verify",
    ]


def test_verify_react_app_preserves_completed_evidence_when_browser_fails(tmp_path, monkeypatch):
    app_path = tmp_path / "numeric.app.json"
    out_dir = tmp_path / "react-app"
    _write_app(app_path, _numeric_v3_app_bundle())
    assert compile_app(app_path, out_dir=out_dir, target=REACT_APP_TARGET, cwd=tmp_path)["ok"]

    def fake_run(command, *, cwd, timeout):
        del cwd, timeout
        return {
            "returncode": 1 if tuple(command) == ("npm", "run", "viewspec:verify") else 0,
            "stdout": "",
            "stderr": "browser unavailable",
        }

    monkeypatch.setattr("viewspec.app_react_verify._run_process", fake_run)
    monkeypatch.setattr(
        "viewspec.app_react_verify._run_freerange",
        lambda host_dir, manifest: _passed_static_analysis(),
    )

    report = verify_react_app_artifact_dir(out_dir, install=True, freerange=True)

    assert report["ok"] is False
    assert report["errors"][0]["code"] == "APP_REACT_VERIFY_BROWSER_FAILED"
    assert report["typecheck"]["status"] == "passed"
    assert report["static_analysis"]["status"] == "passed"
    assert report["policy"]["freerange"] == "requested"
    assert report["phases"] == {
        "artifact_integrity": "passed",
        "typecheck": "passed",
        "freerange": "passed",
        "build": "passed",
        "browser": "failed",
        "final_integrity": "not_completed",
    }


def test_verify_react_app_marks_typecheck_timeout_as_failed(tmp_path, monkeypatch):
    app_path = tmp_path / "viewspec.app.json"
    out_dir = tmp_path / "react-app"
    _write_app(app_path, _react_app_bundle())
    assert compile_app(app_path, out_dir=out_dir, target=REACT_APP_TARGET, cwd=tmp_path)["ok"]

    def fake_run(command, *, cwd, timeout):
        del cwd, timeout
        if tuple(command) == ("npm", "run", "typecheck"):
            raise ReactAppVerifyFailure(
                "APP_REACT_VERIFY_TIMEOUT",
                "typecheck timed out",
                "Fix the generated app and retry.",
            )
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr("viewspec.app_react_verify._run_process", fake_run)

    report = verify_react_app_artifact_dir(out_dir, install=True)

    assert report["ok"] is False
    assert report["errors"][0]["code"] == "APP_REACT_VERIFY_TIMEOUT"
    assert report["typecheck"]["status"] == "failed"
    assert report["phases"]["typecheck"] == "failed"


def test_verify_react_app_fails_closed_on_typescript_diagnostics(tmp_path, monkeypatch):
    app_path = tmp_path / "numeric.app.json"
    out_dir = tmp_path / "react-app"
    _write_app(app_path, _numeric_v3_app_bundle())
    assert compile_app(app_path, out_dir=out_dir, target=REACT_APP_TARGET, cwd=tmp_path)["ok"]
    commands: list[tuple[str, ...]] = []

    def fake_run(command, *, cwd, timeout):
        del cwd, timeout
        commands.append(tuple(command))
        if tuple(command) == ("npm", "run", "typecheck"):
            return {"returncode": 2, "stdout": "", "stderr": "TS2322: invalid generated type"}
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr("viewspec.app_react_verify._run_process", fake_run)
    monkeypatch.setattr(
        "viewspec.app_react_verify._run_freerange",
        lambda host_dir, manifest: pytest.fail("Freerange must not run after TypeScript diagnostics"),
    )

    report = verify_react_app_artifact_dir(out_dir, install=True, freerange=True)

    assert report["ok"] is False
    assert report["errors"][0]["code"] == "APP_REACT_VERIFY_TYPECHECK_FAILED"
    assert "TS2322" in report["errors"][0]["message"]
    assert report["typecheck"]["status"] == "failed"
    assert report["phases"]["typecheck"] == "failed"
    assert report["phases"]["freerange"] == "not_completed"
    assert commands == [
        ("npm", "ci", "--ignore-scripts"),
        ("npm", "run", "typecheck"),
    ]


def test_verify_react_app_rejects_original_output_race_and_retains_evidence(tmp_path, monkeypatch):
    app_path = tmp_path / "numeric.app.json"
    out_dir = tmp_path / "react-app"
    _write_app(app_path, _numeric_v3_app_bundle())
    assert compile_app(app_path, out_dir=out_dir, target=REACT_APP_TARGET, cwd=tmp_path)["ok"]
    original_app_hash = file_hash(out_dir / "src" / "ViewSpecApp.tsx")
    original_manifest_hash = file_hash(out_dir / "viewspec_app_manifest.json")

    def fake_run(command, *, cwd, timeout):
        del timeout
        if tuple(command) == ("npm", "run", "viewspec:verify"):
            (cwd / "viewspec_runtime_report.json").write_text(
                json.dumps(
                    {
                        "route_count": 2,
                        "history_assertion_count": 1,
                        "unknown_route_assertion_count": 1,
                        "state_action_count": 1,
                        "rebound_binding_count": 1,
                        "selector_assertion_count": 1,
                        "visibility_assertion_count": 0,
                    }
                ),
                encoding="utf-8",
            )
            original = out_dir / "src" / "ViewSpecApp.tsx"
            original.write_text(original.read_text(encoding="utf-8") + "\n// raced\n", encoding="utf-8")
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr("viewspec.app_react_verify._run_process", fake_run)
    monkeypatch.setattr(
        "viewspec.app_react_verify._run_freerange",
        lambda host_dir, manifest: _passed_static_analysis(),
    )

    report = verify_react_app_artifact_dir(out_dir, install=True, freerange=True)

    assert report["ok"] is False
    assert report["errors"][0]["code"] == "APP_REACT_VERIFY_HASH_MISMATCH"
    assert report["app_artifact_hash"] == original_app_hash
    assert report["manifest_hash"] == original_manifest_hash
    assert report["typecheck"]["status"] == "passed"
    assert report["static_analysis"]["status"] == "passed"
    assert report["assertions"]["state_action_count"] == 1
    assert report["phases"]["browser"] == "passed"
    assert report["phases"]["final_integrity"] == "failed"


def test_verify_react_app_invalidates_static_evidence_on_post_analysis_tamper(tmp_path, monkeypatch):
    app_path = tmp_path / "numeric.app.json"
    out_dir = tmp_path / "react-app"
    _write_app(app_path, _numeric_v3_app_bundle())
    assert compile_app(app_path, out_dir=out_dir, target=REACT_APP_TARGET, cwd=tmp_path)["ok"]

    def fake_run(command, *, cwd, timeout):
        del timeout
        if tuple(command) == ("npm", "run", "viewspec:verify"):
            (cwd / "viewspec_runtime_report.json").write_text(
                json.dumps(
                    {
                        "route_count": 2,
                        "history_assertion_count": 1,
                        "unknown_route_assertion_count": 1,
                        "state_action_count": 1,
                        "rebound_binding_count": 1,
                        "selector_assertion_count": 1,
                        "visibility_assertion_count": 0,
                    }
                ),
                encoding="utf-8",
            )
            kernel = cwd / "src" / "viewspec_numeric.ts"
            kernel.write_text(kernel.read_text(encoding="utf-8") + "\n// tampered\n", encoding="utf-8")
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr("viewspec.app_react_verify._run_process", fake_run)
    monkeypatch.setattr(
        "viewspec.app_react_verify._run_freerange",
        lambda host_dir, manifest: _passed_static_analysis(),
    )

    report = verify_react_app_artifact_dir(out_dir, install=True, freerange=True)

    assert report["ok"] is False
    assert report["errors"][0]["code"] == "APP_FREERANGE_SOURCE_CHANGED"
    assert report["static_analysis"]["status"] == "failed"
    assert report["static_analysis"]["validity"] == "invalidated"
    assert report["static_analysis"]["errors"][0]["code"] == "APP_FREERANGE_SOURCE_CHANGED"
    assert report["assertions"]["state_action_count"] == 1
    assert report["phases"]["browser"] == "passed"
    assert report["phases"]["final_integrity"] == "failed"


def test_verify_react_app_rejects_tampered_generated_source(tmp_path):
    app_path = tmp_path / "viewspec.app.json"
    out_dir = tmp_path / "react-app"
    _write_app(app_path, _react_app_bundle())
    compiled = compile_app(app_path, out_dir=out_dir, target=REACT_APP_TARGET, cwd=tmp_path)
    assert compiled["ok"] is True
    app_source = out_dir / "src" / "ViewSpecApp.tsx"
    app_source.write_text(app_source.read_text(encoding="utf-8") + "\n// changed\n", encoding="utf-8")

    report = verify_react_app_artifact_dir(out_dir, install=False)

    assert report["ok"] is False
    assert report["errors"][0]["code"] == "APP_REACT_VERIFY_HASH_MISMATCH"


def test_verify_react_app_uses_prebuilt_dependencies_without_network(tmp_path, monkeypatch):
    app_path = tmp_path / "viewspec.app.json"
    out_dir = tmp_path / "react-app"
    _write_app(app_path, _react_app_bundle())
    assert compile_app(app_path, out_dir=out_dir, target=REACT_APP_TARGET, cwd=tmp_path)["ok"]
    seed = tmp_path / "seed-node-modules"
    seed.mkdir()
    seed.joinpath("react").mkdir()
    commands: list[tuple[str, ...]] = []

    def fake_run(command, *, cwd, timeout):
        commands.append(tuple(command))
        node_modules = cwd / "node_modules"
        assert node_modules.is_dir()
        assert not node_modules.is_symlink()
        assert node_modules.joinpath("react").is_symlink()
        assert node_modules.joinpath("react").resolve() == seed.joinpath("react").resolve()
        node_modules.joinpath(".vite-temp").mkdir(exist_ok=True)
        if tuple(command) == ("npm", "run", "viewspec:verify"):
            (cwd / "viewspec_runtime_report.json").write_text(
                json.dumps(
                    {
                        "route_count": 2,
                        "history_assertion_count": 1,
                        "unknown_route_assertion_count": 1,
                        "state_action_count": 1,
                        "rebound_binding_count": 1,
                        "selector_assertion_count": 1,
                        "visibility_assertion_count": 1,
                    }
                ),
                encoding="utf-8",
            )
        return {"returncode": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setenv("VIEWSPEC_HOST_VERIFY_NODE_MODULES_DIR", str(seed))
    monkeypatch.setattr("viewspec.app_react_verify._run_process", fake_run)

    report = verify_react_app_artifact_dir(out_dir, install=True)

    assert report["ok"] is True
    assert commands == [
        ("npm", "run", "typecheck"),
        ("npm", "run", "build"),
        ("npm", "run", "viewspec:verify"),
    ]


def test_prove_app_react_target_records_exact_host_runtime(tmp_path, monkeypatch):
    app_path = tmp_path / "viewspec.app.json"
    proof_dir = tmp_path / "app-proof"
    _write_app(app_path, _react_app_bundle())

    def fake_verify(path, *, install, freerange=False, pretext=False):
        assert Path(path) == proof_dir / "react-app"
        assert install is True
        assert freerange is False
        assert pretext is False
        return {
            "schema_version": 1,
            "ok": True,
            "target": REACT_APP_TARGET,
            "artifact_dir": str(path),
            "app_artifact_hash": file_hash(Path(path) / "src" / "ViewSpecApp.tsx"),
            "manifest_hash": file_hash(Path(path) / "viewspec_app_manifest.json"),
            "install": True,
            "assertions": {
                "route_count": 2,
                "history_assertion_count": 1,
                "unknown_route_assertion_count": 1,
                "state_action_count": 1,
                "rebound_binding_count": 6,
                "selector_assertion_count": 1,
                "visibility_assertion_count": 1,
            },
            "policy": {
                "install_command": "npm ci --ignore-scripts",
                "build_command": "npm run build",
                "browser_command": "npm run viewspec:verify",
            },
            "duration_ms": 10,
            "errors": [],
        }

    monkeypatch.setattr("viewspec.app_pipeline.verify_react_app_artifact_dir", fake_verify)

    proof = prove_app(
        app_path=app_path,
        out_dir=proof_dir,
        target=REACT_APP_TARGET,
        install=True,
        cwd=tmp_path,
    )

    assert proof["ok"] is True, proof["errors"]
    assert proof["proof_level"] == "react_app_reference_host"
    assert proof["target"] == REACT_APP_TARGET
    assert proof["route_navigation"] == "browser_history_v1"
    assert proof["paths"]["react_app"] == str(proof_dir / "react-app")
    assert proof["paths"]["react_app_entry"].endswith("src/ViewSpecApp.tsx")
    assert proof["react_app"]["ok"] is True
    assert proof["host_report"]["ok"] is True
    assert proof["host_report"]["assertions"]["rebound_binding_count"] == 6
    assert proof["policy"]["install"] is True
    assert proof["policy"]["install_command"] == "npm ci --ignore-scripts"
    assert proof["app_artifact_hash"] == proof["host_report"]["app_artifact_hash"]
    assert Path(proof["paths"]["report"]).exists()
    assert Path(proof["paths"]["proof_summary"]).exists()


def test_failed_react_proof_summary_does_not_claim_reference_host_success(tmp_path, monkeypatch):
    app_path = tmp_path / "viewspec.app.json"
    proof_dir = tmp_path / "app-proof"
    _write_app(app_path, _react_app_bundle())

    def fake_verify(path, *, install, freerange=False, pretext=False):
        app_dir = Path(path)
        assert install is True
        assert freerange is False
        assert pretext is False
        return {
            "schema_version": 1,
            "ok": False,
            "target": REACT_APP_TARGET,
            "artifact_dir": str(app_dir),
            "app_artifact_hash": file_hash(app_dir / "src" / "ViewSpecApp.tsx"),
            "manifest_hash": file_hash(app_dir / "viewspec_app_manifest.json"),
            "install": True,
            "typecheck": {"status": "passed", "command": "npm run typecheck"},
            "assertions": {},
            "phases": {
                "artifact_integrity": "passed",
                "typecheck": "passed",
                "freerange": "not_requested",
                "build": "failed",
                "browser": "not_completed",
                "final_integrity": "not_completed",
            },
            "policy": {"install_command": "npm ci --ignore-scripts"},
            "timings_ms": {"total": 1},
            "errors": [
                {
                    "code": "APP_REACT_VERIFY_BUILD_FAILED",
                    "message": "build failed",
                    "fix": "Fix the generated app and retry.",
                }
            ],
        }

    monkeypatch.setattr("viewspec.app_pipeline.verify_react_app_artifact_dir", fake_verify)

    proof = prove_app(
        app_path=app_path,
        out_dir=proof_dir,
        target=REACT_APP_TARGET,
        install=True,
        cwd=tmp_path,
    )

    assert proof["ok"] is False
    summary = Path(proof["paths"]["proof_summary"]).read_text(encoding="utf-8")
    assert "Claim not established: the composite proof failed." in summary
    assert "passed in the exact reference host" not in summary


def test_prove_app_freerange_propagates_composite_machine_evidence(tmp_path, monkeypatch):
    app_path = tmp_path / "numeric.app.json"
    proof_dir = tmp_path / "app-proof"
    _write_app(app_path, _numeric_v3_app_bundle())

    def fake_verify(path, *, install, freerange=False, pretext=False):
        app_dir = Path(path)
        package = json.loads((app_dir / "package.json").read_text(encoding="utf-8"))
        manifest = json.loads((app_dir / "viewspec_app_manifest.json").read_text(encoding="utf-8"))
        assert install is True
        assert freerange is True
        assert pretext is False
        assert package["devDependencies"]["@chenglou/freerange"] == "0.0.1"
        assert manifest["numeric_analysis"]["status"] == "applicable"
        return {
            "schema_version": 1,
            "ok": True,
            "target": REACT_APP_TARGET,
            "artifact_dir": str(app_dir),
            "app_artifact_hash": file_hash(app_dir / "src" / "ViewSpecApp.tsx"),
            "manifest_hash": file_hash(app_dir / "viewspec_app_manifest.json"),
            "install": True,
            "typecheck": {"status": "passed", "command": "npm run typecheck"},
            "static_analysis": _passed_static_analysis(),
            "assertions": {
                "route_count": 2,
                "history_assertion_count": 1,
                "unknown_route_assertion_count": 1,
                "state_action_count": 1,
                "rebound_binding_count": 6,
                "selector_assertion_count": 1,
                "visibility_assertion_count": 0,
            },
            "policy": {
                "install_command": "npm ci --ignore-scripts",
                "typecheck_command": "npm run typecheck",
                "freerange": "requested",
                "build_command": "npm run build",
                "browser_command": "npm run viewspec:verify",
            },
            "duration_ms": 1,
            "timings_ms": {"total": 1},
            "errors": [],
        }

    monkeypatch.setattr("viewspec.app_pipeline.verify_react_app_artifact_dir", fake_verify)

    proof = prove_app(
        app_path=app_path,
        out_dir=proof_dir,
        target=REACT_APP_TARGET,
        install=True,
        freerange=True,
        cwd=tmp_path,
    )

    assert proof["ok"] is True, proof["errors"]
    assert proof["static_analysis"]["status"] == "passed"
    assert proof["static_analysis"]["coverage"]["fully_analyzed"] == 6
    assert proof["policy"]["freerange"] == "requested"
    timings = proof["timings_ms"]
    assert timings["total"] == sum(
        value for key, value in timings.items() if key != "total" and isinstance(value, int)
    )
    summary = Path(proof["paths"]["proof_summary"]).read_text(encoding="utf-8")
    assert "## Numeric Static Analysis" in summary
    assert "does not analyze Tailwind class strings or CSS" in summary
    support = json.loads(Path(proof["paths"]["support_bundle"]).read_text(encoding="utf-8"))
    assert support["static_analysis"]["status"] == "passed"
    assert support["static_analysis"]["coverage"]["fully_analyzed"] == 6


def test_prove_app_freerange_rejects_non_react_target_without_writing(tmp_path):
    app_path = tmp_path / "viewspec.app.json"
    output = tmp_path / "proof"
    _write_app(app_path, starter_app_bundle())

    proof = prove_app(app_path=app_path, out_dir=output, freerange=True, cwd=tmp_path)

    assert proof["ok"] is False
    assert proof["errors"][0]["code"] == "APP_FREERANGE_TARGET_UNSUPPORTED"
    assert not output.exists()


def test_prove_app_cli_accepts_react_target_and_install(tmp_path, monkeypatch, capsys):
    app_path = tmp_path / "viewspec.app.json"
    proof_dir = tmp_path / "app-proof"
    _write_app(app_path, _react_app_bundle())

    monkeypatch.setattr(
        "viewspec.app_pipeline.verify_react_app_artifact_dir",
        lambda path, *, install, freerange=False, pretext=False: {
            "schema_version": 1,
            "ok": True,
            "target": REACT_APP_TARGET,
            "artifact_dir": str(path),
            "app_artifact_hash": file_hash(Path(path) / "src" / "ViewSpecApp.tsx"),
            "manifest_hash": file_hash(Path(path) / "viewspec_app_manifest.json"),
            "install": install,
            "assertions": {
                "route_count": 2,
                "history_assertion_count": 1,
                "unknown_route_assertion_count": 1,
                "state_action_count": 1,
                "rebound_binding_count": 6,
                "selector_assertion_count": 1,
                "visibility_assertion_count": 1,
            },
            "policy": {"install_command": "npm ci --ignore-scripts"},
            "duration_ms": 1,
            "errors": [],
        },
    )

    exit_code = cli_main(
        [
            "prove-app",
            "--app",
            str(app_path),
            "--out",
            str(proof_dir),
            "--target",
            REACT_APP_TARGET,
            "--install",
            "--json",
        ]
    )
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert result["ok"] is True
    assert result["target"] == REACT_APP_TARGET
    assert result["host_report"]["ok"] is True


def test_prove_app_cli_forwards_freerange_opt_in(tmp_path, monkeypatch, capsys):
    app_path = tmp_path / "viewspec.app.json"
    _write_app(app_path, _numeric_v3_app_bundle())
    captured: dict[str, object] = {}

    def fake_prove_app(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "proof_level": "react_app_reference_host",
            "target": REACT_APP_TARGET,
            "app": {},
            "paths": {},
            "errors": [],
            "static_analysis": _passed_static_analysis(),
        }

    monkeypatch.setattr("viewspec.cli.prove_app", fake_prove_app)

    exit_code = cli_main(
        [
            "prove-app",
            "--app",
            str(app_path),
            "--out",
            str(tmp_path / "proof"),
            "--target",
            REACT_APP_TARGET,
            "--freerange",
            "--json",
        ]
    )
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert captured["freerange"] is True
    assert result["static_analysis"]["status"] == "passed"
