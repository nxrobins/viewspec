from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from viewspec.app_bundle import (
    compile_app,
    compile_app_tool,
    init_app_tool,
    prove_app,
    starter_react_app_bundle,
    validate_app_text,
)
from viewspec.app_react_verify import verify_react_app_artifact_dir
from viewspec.cli import main as cli_main
from viewspec.local_tools import file_hash


REACT_APP_TARGET = "react-tailwind-app"
ROOT = Path(__file__).resolve().parents[1]


def _react_app_bundle() -> dict:
    return starter_react_app_bundle("internal_tool")


def _write_app(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
    assert "browser history, routes, and unknown path" in runtime_test
    assert "state actions rebind data and visibility" in runtime_test
    assert "inc_1043_status" in runtime_test
    assert "show_triaged_status" in runtime_test


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
        ("npm", "run", "build"),
        ("npm", "run", "viewspec:verify"),
    ]


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
        ("npm", "run", "build"),
        ("npm", "run", "viewspec:verify"),
    ]


def test_prove_app_react_target_records_exact_host_runtime(tmp_path, monkeypatch):
    app_path = tmp_path / "viewspec.app.json"
    proof_dir = tmp_path / "app-proof"
    _write_app(app_path, _react_app_bundle())

    def fake_verify(path, *, install):
        assert Path(path) == proof_dir / "react-app"
        assert install is True
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


def test_prove_app_cli_accepts_react_target_and_install(tmp_path, monkeypatch, capsys):
    app_path = tmp_path / "viewspec.app.json"
    proof_dir = tmp_path / "app-proof"
    _write_app(app_path, _react_app_bundle())

    monkeypatch.setattr(
        "viewspec.app_pipeline.verify_react_app_artifact_dir",
        lambda path, *, install: {
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
