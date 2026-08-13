from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts import run_agent_ui_eval as runner
from viewspec.app_bundle import compile_app
from viewspec.agent_eval_value import STABLE_HOOKS
from viewspec.app_starters import starter_react_app_bundle
from viewspec.native_agents import BEGIN_MARKER, agent_instruction_block


_NODE = shutil.which("node")
_BUN = shutil.which("bun")
_PLAYWRIGHT = Path("src/viewspec/host_verify_template/node_modules/playwright/index.mjs")
_PRODUCT_E2E_OPT_IN = "VIEWSPEC_RUN_AGENT_UI_V2_PRODUCT_E2E"
_RETAINED_PRODUCT_FIXTURES = Path(
    "conformance/agent-ui-v2/retained-product-fixtures.json"
)
_RETAINED_PRODUCT_CASES = json.loads(
    _RETAINED_PRODUCT_FIXTURES.read_text(encoding="utf-8")
)["fixtures"]


def _healthy_score(*, failed_criterion: str | None = None) -> dict:
    criteria = [
        {
            "id": failed_criterion or "functional",
            "dimension": "semantics",
            "passed": failed_criterion is None,
        }
    ]
    return {
        "schema_version": 1,
        "ok": failed_criterion is None,
        "passed": int(failed_criterion is None),
        "total": 1,
        "viewports": [
            {
                "viewport": {"width": width, "height": height},
                "criteria": criteria,
                "layout_fidelity": 0.97,
            }
            for width, height in ((390, 844), (768, 1024), (1440, 1000))
        ],
    }


def _healthy_targets() -> list[dict]:
    return [
        {
            "id": target_id,
            "applicable": True,
            "build": {"ok": True},
            "functional_acceptance": 1.0,
            "layout_fidelity": 0.97,
            "passed": True,
            "parity": parity,
            "parity_by_viewport": {
                "390": parity,
                "768": parity,
                "1440": parity,
            },
            "score_artifact": f"fixture/{target_id}/browser-score.json",
        }
        for target_id, parity in (("static-shell", 1.0), ("native-react", 0.99))
    ]


def _healthy_targets_with_artifacts(root: Path) -> list[dict]:
    root.mkdir(parents=True, exist_ok=True)
    static_score = root / "browser-score.json"
    react_score = root / "react-score" / "browser-score.json"
    parity_score = root / "parity-score" / "browser-score.json"
    for path in (static_score, react_score, parity_score):
        runner._write(path, "{}\n")
    targets = _healthy_targets()
    targets[0]["score_artifact"] = str(static_score)
    targets[1]["score_artifact"] = str(react_score)
    targets[1]["parity_artifact"] = str(parity_score)
    return targets


def _fake_evaluation_targets(kwargs: dict) -> list[dict]:
    artifact = (
        kwargs["output"]
        / "artifacts"
        / f"{kwargs['step_index'] + 1:02d}-{kwargs['step'].id}"
    )
    return _healthy_targets_with_artifacts(artifact)


def test_retained_product_fixture_manifest_is_hash_bound_and_complete():
    manifest = json.loads(_RETAINED_PRODUCT_FIXTURES.read_text(encoding="utf-8"))

    assert manifest["schema_version"] == 1
    assert manifest["task_id"] == "field-dispatch-lifecycle"
    assert manifest["seed"] == 104729
    assert {fixture["arm"] for fixture in manifest["fixtures"]} == {
        "viewspec-core",
        "viewspec-deep",
    }
    for fixture in manifest["fixtures"]:
        source = _RETAINED_PRODUCT_FIXTURES.parent / fixture["source"]
        assert source.is_file()
        assert runner._sha256(source) == fixture["source_sha256"]
    assert manifest["gates"] == {
        "functional_acceptance": 1.0,
        "layout_fidelity_minimum": 0.6769,
        "parity_minimum_per_viewport": 0.95,
        "required_target_trials": ["static-shell", "native-react"],
        "native_proof_required": True,
        "deep_freerange_required": True,
        "deep_pretext_required": True,
    }


def _injected_failure_criterion(text: str) -> str | None:
    signatures = (
        (
            "const n=document.querySelector('[data-eval-id=\"action-show-guide\"]');"
            "if(n){n.addEventListener('click',e=>{e.preventDefault();e.stopImmediatePropagation();",
            "interaction:Show escalation guide",
        ),
        ('p.textContent="Review count: 2"', "interaction:Record review"),
        ('<style>[data-eval-id="panel-escalation-guide"]', "interaction:Show escalation guide"),
        ("n.after(n.cloneNode(true))", "resource:job-j207"),
        ("__unbroken_eval_suffix_", "text-geometry:long-title"),
    )
    return next((criterion for signature, criterion in signatures if signature in text), None)


def test_trial_artifact_manifest_distinguishes_early_detection_from_missing_score(
    tmp_path,
):
    trial_root = tmp_path / "trial"
    artifact = trial_root / "attempt-1/artifacts/10-repair-and-finalize"
    runner._write(artifact / "compile.log", "compile detector\n")

    manifest = runner._trial_artifact_manifest(
        trial_root=trial_root,
        attempt_used=1,
        final_step_id="repair-and-finalize",
        phase_timings_ms={"compile": 1, "native_proof": 1},
    )

    assert manifest == {
        "root": str(trial_root),
        "score_status": "not_run_early_detector",
        "detector_evidence": [str(artifact / "compile.log")],
    }
    with pytest.raises(RuntimeError, match="browser scoring completed"):
        runner._trial_artifact_manifest(
            trial_root=trial_root,
            attempt_used=1,
            final_step_id="repair-and-finalize",
            phase_timings_ms={"browser_score": 1},
        )


def test_artifact_integrity_resolves_every_declared_reference(tmp_path):
    trial_root = tmp_path / "trial"
    detector = trial_root / "compile.log"
    score = tmp_path / "target/browser-score.json"
    runner._write(detector, "detected\n")
    runner._write(score, "{}\n")
    evidence = {
        "mutation_trials": [
            {
                "id": "early",
                "applicable": True,
                "artifacts": {
                    "root": str(trial_root),
                    "score_status": "not_run_early_detector",
                    "detector_evidence": [str(detector)],
                },
            }
        ],
        "negative_control_trials": [],
        "target_trials": [
            {
                "id": "static-shell",
                "applicable": True,
                "score_artifact": str(score),
            }
        ],
        "repair_turns": [],
    }
    session = {"value_evidence": runner._finalize_value_evidence(evidence)}

    runner._require_value_evidence_integrity(session)
    assert evidence["artifact_integrity"]["complete"] is True
    detector.unlink()

    with pytest.raises(ValueError, match="stale or missing"):
        runner._require_value_evidence_integrity(session)
    refreshed = runner._value_evidence_artifact_integrity(evidence)
    assert refreshed["complete"] is False
    assert refreshed["missing"] == [
        {
            "field": "mutation_trials[0].artifacts.detector_evidence[0]",
            "path": str(detector),
            "expected": "file",
        }
    ]


def test_evidence_migration_rewrites_legacy_trials_and_regenerates_reports(tmp_path):
    protocol, protocol_path = runner._protocol("conformance/agent-ui-v2/protocol.json")
    run_root = tmp_path / "paired"
    runner._write(
        run_root / "pair-manifest.json",
        runner.canonical_json(
            {
                "protocol_id": protocol.id,
                "protocol_sha256": runner._sha256(protocol_path),
                "seed": 104729,
                "model": "gpt-test",
            }
        ),
    )
    for arm in protocol.arms:
        arm_root = run_root / arm
        early_root = arm_root / "value-trials/01-early"
        early_score = (
            early_root
            / "attempt-1/artifacts/10-repair-and-finalize/browser-score.json"
        )
        runner._write(early_score.parent / "compile.log", "detected\n")
        control_root = arm_root / "value-trials/02-control"
        control_score = (
            control_root
            / "attempt-1/artifacts/10-repair-and-finalize/browser-score.json"
        )
        runner._write(control_score, "{}\n")
        targets = _healthy_targets_with_artifacts(arm_root / "final-targets")
        session = {
            "schema_version": 1,
            "protocol_id": protocol.id,
            "task_id": "field-dispatch-lifecycle",
            "arm_id": arm,
            "seed": 104729,
            "model": "gpt-test",
            "environment": {
                "controls": {"ignore_user_config": True},
                "versions": {"codex": "codex-cli test"},
                "inputs": {
                    "protocol": {"sha256": runner._sha256(protocol_path)},
                    "runner": {"sha256": "a" * 64},
                },
            },
            "turns": [
                {
                    "step_id": "repair-and-finalize",
                    "phase": "repair",
                    "usage": {},
                    "wall_time_ms": 0,
                    "deterministic_ms": 0,
                    "score": _healthy_score(),
                    "proof": None if arm == "code-first" else {"ok": True},
                }
            ],
            "value_evidence": {
                "manifest": {"path": str(runner.MUTATION_MANIFEST)},
                "baseline": {"source_sha256": arm[0].encode().hex().ljust(64, "0")[:64]},
                "mutation_trials": [
                    {
                        "id": "early",
                        "order": 0,
                        "applicable": True,
                        "repair_applicable": False,
                        "baseline_sha256": "a" * 64,
                        "mutated_sha256": "b" * 64,
                        "expected_detectors": ["compile"],
                        "observed_detectors": ["compile"],
                        "detected": True,
                        "deterministic_ms": 1,
                        "phase_timings_ms": {"compile": 1},
                        "commands": ["compile"],
                        "artifacts": {
                            "root": str(early_root),
                            "score": str(early_score),
                        },
                    }
                ],
                "negative_control_trials": [
                    {
                        "id": "control",
                        "order": 1,
                        "applicable": True,
                        "baseline_sha256": "a" * 64,
                        "detected": False,
                        "deterministic_ms": 1,
                        "phase_timings_ms": {"browser_score": 1},
                        "commands": ["score"],
                        "artifacts": {
                            "root": str(control_root),
                            "score": str(control_score),
                        },
                    }
                ],
                "target_trials": targets,
                "repair_turns": [],
                "deterministic_overhead_ms": 2,
            },
        }
        runner._write(arm_root / "session.json", runner.canonical_json(session))
        runner._write(arm_root / "session.partial.json", runner.canonical_json(session))

    result = runner._migrate_evidence(
        runner.argparse.Namespace(
            protocol=str(protocol_path),
            run_root=str(run_root),
        )
    )

    assert result == 0
    migration = json.loads(
        (run_root / "evidence-migration.json").read_text(encoding="utf-8")
    )
    assert migration["model_calls_added"] == 0
    assert migration["status_counts_across_migrated_files"] == {
        "not_run_early_detector": 6,
        "recorded": 6,
    }
    report = json.loads((run_root / "study-report.json").read_text(encoding="utf-8"))
    assert report["evidence_migration"]["model_calls_added"] == 0
    assert report["shakedown_exit"]["pass"] is False
    with pytest.raises(ValueError, match="product-regression exit has not passed"):
        runner._freeze_evidence(
            runner.argparse.Namespace(
                protocol=str(protocol_path),
                run_root=str(run_root),
            )
        )
    for arm in protocol.arms:
        migrated = json.loads(
            (run_root / arm / "session.json").read_text(encoding="utf-8")
        )
        runner._require_value_evidence_integrity(migrated)
        artifacts = migrated["value_evidence"]["mutation_trials"][0]["artifacts"]
        assert artifacts["score_status"] == "not_run_early_detector"
        assert "score" not in artifacts


def test_proof_feedback_groups_repeated_source_errors_without_hiding_other_codes():
    feedback = runner._proof_feedback(
        {
            "ok": False,
            "errors": [
                {
                    "code": "APP_RESOURCE_REPEAT_AUTHORED_DUPLICATE",
                    "path": "$.screens[0].resource_views[0].repeat",
                    "message": f"duplicate field {field}",
                }
                for field in ("id", "title", "status")
            ]
            + [
                {
                    "code": "APP_PRESENTATION_PROFILE_INVARIANT_INVALID",
                    "path": "$.screens[0].presentation",
                    "message": "wide rail is hidden",
                }
            ],
        }
    )

    assert feedback.count("APP_RESOURCE_REPEAT_AUTHORED_DUPLICATE") == 1
    assert "(+2 similar)" in feedback
    assert "APP_PRESENTATION_PROFILE_INVARIANT_INVALID" in feedback


def test_source_capture_preserves_each_revision_and_measures_delta(tmp_path):
    workspace = tmp_path / "workspace"
    submission = workspace / "submission"
    submission.mkdir(parents=True)
    source = submission / "index.html"
    source.write_text("<main>first</main>\n", encoding="utf-8")

    first, previous, first_ms = runner._capture_source(
        workspace=workspace,
        artifact=tmp_path / "turn-1",
        arm="code-first",
        previous={},
    )
    source.write_text("<main>first</main>\n<button>Next</button>\n", encoding="utf-8")
    second, _current, second_ms = runner._capture_source(
        workspace=workspace,
        artifact=tmp_path / "turn-2",
        arm="code-first",
        previous=previous,
    )

    assert first["file_count"] == 1
    assert first["delta"]["added_lines"] == 1
    assert second["delta"] == {
        "added_lines": 1,
        "removed_lines": 0,
        "diff_bytes": second["delta"]["diff_bytes"],
        "diff_sha256": second["delta"]["diff_sha256"],
    }
    assert first_ms >= 0 and second_ms >= 0
    assert (tmp_path / "turn-1/source/submission/index.html").read_text(encoding="utf-8") == "<main>first</main>\n"
    assert "+<button>Next</button>" in (tmp_path / "turn-2/source.diff").read_text(encoding="utf-8")


def test_viewspec_source_capture_records_semantic_diff(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "viewspec.app.json"
    payload = starter_react_app_bundle()
    source.write_text(json.dumps(payload), encoding="utf-8")
    _first, previous, _elapsed = runner._capture_source(
        workspace=workspace,
        artifact=tmp_path / "turn-1",
        arm="viewspec-core",
        previous={},
    )
    payload["app"]["title"] = "Changed evaluation title"
    source.write_text(json.dumps(payload), encoding="utf-8")

    second, _current, _elapsed = runner._capture_source(
        workspace=workspace,
        artifact=tmp_path / "turn-2",
        arm="viewspec-core",
        previous=previous,
    )

    assert second["semantic_diff"]["ok"] is True
    assert second["semantic_diff"]["changed_fields"] == [
        {"path": "$.app.title", "left": "Incident Console", "right": "Changed evaluation title"}
    ]
    assert (tmp_path / "turn-2/semantic-diff.json").is_file()


def test_resumed_codex_turn_explicitly_restores_workspace_write(tmp_path, monkeypatch):
    commands: list[list[str]] = []

    def fake_run(command, *, cwd, timeout, env=None, merge_stderr=True):
        del cwd, timeout, env, merge_stderr
        commands.append(command)
        payload = 'stdin preamble\n' + '\n'.join(
            [
                '{"type":"thread.started","thread_id":"thread-1"}',
                '{"type":"turn.completed","usage":{}}',
            ]
        )
        return subprocess.CompletedProcess(command, 0, payload, stderr="tool warning\n"), 1

    monkeypatch.setattr(runner, "_run", fake_run)

    runner._codex_turn(
        workspace=tmp_path,
        prompt="continue",
        events_path=tmp_path / "events.jsonl",
        reference_image=None,
        thread_id="thread-1",
        model="gpt-test",
        ignore_user_config=True,
        timeout=30,
    )

    assert ["-c", 'sandbox_mode="workspace-write"'] == commands[0][6:8]
    event_lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(event_lines) == 2
    assert all(isinstance(json.loads(line), dict) for line in event_lines)
    diagnostics = (tmp_path / "events.diagnostics.log").read_text(encoding="utf-8")
    assert "stdin preamble" in diagnostics
    assert "tool warning" in diagnostics


def test_session_resumes_from_lifecycle_checkpoint_without_replaying_prior_turns(
    tmp_path,
    monkeypatch,
):
    protocol, protocol_path = runner._protocol("conformance/agent-ui-v2/protocol.json")
    task = protocol.task("field-dispatch-lifecycle")
    output = tmp_path / "run"
    workspace = output / "workspace"
    workspace.joinpath("submission/react/src").mkdir(parents=True)
    workspace.joinpath("submission/index.html").write_text(
        "<!doctype html><main>resume</main>\n",
        encoding="utf-8",
    )
    workspace.joinpath("submission/react/src/App.jsx").write_text(
        "export default()=> <main>resume</main>\n",
        encoding="utf-8",
    )
    session = {
        "schema_version": 1,
        "protocol_id": protocol.id,
        "task_id": task.id,
        "arm_id": "code-first",
        "seed": 104729,
        "model": "gpt-test",
        "thread_id": "lifecycle-thread",
        "turns": [
            {
                "step_id": step.id,
                "phase": step.phase,
                "usage": {},
                "wall_time_ms": 0,
                "deterministic_ms": 0,
                "score": _healthy_score(),
            }
            for step in task.steps[:9]
        ],
        "qualification_turns": [],
    }
    runner._write_checkpoint(
        output=output,
        protocol_path=protocol_path,
        model="gpt-test",
        workspace=workspace,
        arm="code-first",
        stage="lifecycle",
        next_index=9,
        feedback="resume feedback",
        previous_criteria=runner._criterion_states(_healthy_score()),
        session=session,
    )
    prompts: list[str] = []

    def fake_codex_turn(**kwargs):
        prompts.append(kwargs["prompt"])
        assert kwargs["thread_id"] == "lifecycle-thread"
        assert kwargs["reference_image"] is None
        runner._write(kwargs["events_path"], '{"type":"turn.completed","usage":{}}\n')
        return (
            {"thread_id": "lifecycle-thread", "completed": True, "usage": {}, "telemetry": {}},
            10,
            0,
            "",
        )

    monkeypatch.setattr(runner, "_codex_turn", fake_codex_turn)
    monkeypatch.setattr(
        runner,
        "_evaluate_turn",
        lambda **kwargs: (
            _healthy_score(),
            None,
            {"browser_score": 1},
            "healthy",
            _healthy_targets(),
        ),
    )
    args = runner.argparse.Namespace(
        protocol="conformance/agent-ui-v2/protocol.json",
        task=task.id,
        arm="code-first",
        seed=104729,
        out=str(output),
        model="gpt-test",
        with_value_trials=False,
        resume=True,
        allow_user_config=False,
        turn_timeout=10,
        no_install=False,
    )

    assert runner._run_session(args) == 0
    completed = json.loads((output / "session.json").read_text(encoding="utf-8"))
    checkpoint = json.loads((output / "checkpoint.json").read_text(encoding="utf-8"))
    assert len(prompts) == 1
    assert len(completed["turns"]) == 10
    assert completed["turns"][-1]["step_id"] == task.steps[-1].id
    assert checkpoint["stage"] == "complete"


def test_code_first_react_scaffold_builds_off_the_shared_local_seed(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.joinpath("submission").mkdir(parents=True)
    activation = runner._activate_code_first_react(workspace)
    build, _elapsed, log = runner._build_react_target(
        workspace / "submission" / "react",
        tmp_path / "artifact",
    )

    assert activation["activated"] is True
    assert activation["dependency_seed_sha256"] == build["dependency_seed_sha256"]
    assert build["ok"] is True, log
    assert (tmp_path / "artifact/react-dist/index.html").is_file()
    assert build["dist"]["files"]
    package = json.loads(
        (workspace / "submission/react/package.json").read_text(encoding="utf-8")
    )
    shared_package = json.loads(
        Path("src/viewspec/host_verify_template/package.json").read_text(encoding="utf-8")
    )
    assert package["dependencies"] == shared_package["dependencies"]
    assert package["devDependencies"] == shared_package["devDependencies"]
    assert package["dependencies"]["react"] == "19.2.7"
    assert package["dependencies"]["react-dom"] == "19.2.7"
    assert package["dependencies"]["vite"] == "8.0.16"
    assert package["dependencies"]["tailwindcss"] == "4.3.0"
    assert package["devDependencies"]["@playwright/test"] == "1.60.0"
    assert package["devDependencies"]["typescript"] == "6.0.3"


def test_viewspec_react_source_uses_proof_paths_and_core_target_builds(tmp_path):
    app_path = tmp_path / "viewspec.app.json"
    generated = tmp_path / "proof" / "react-app"
    app_path.write_text(
        json.dumps(starter_react_app_bundle(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    result = compile_app(
        app_path,
        out_dir=generated,
        target="react-tailwind-app",
        cwd=tmp_path,
    )
    proof = {"paths": {"react_app": str(generated)}}

    source = runner._react_source_from_proof(proof, tmp_path / "artifact")
    build, _elapsed, log = runner._build_react_target(source, tmp_path / "artifact")

    assert result["ok"] is True
    assert source == generated
    assert build["ok"] is True, log
    assert (tmp_path / "artifact/react-dist/index.html").is_file()


def test_deep_dependency_profile_builds_pretext_import_without_network(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.joinpath("submission").mkdir(parents=True)
    runner._activate_code_first_react(workspace)
    react = workspace / "submission" / "react"
    package_path = react / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["dependencies"][runner.PRETEXT_PACKAGE] = runner.PRETEXT_VERSION
    package["devDependencies"][runner.FREERANGE_PACKAGE] = runner.FREERANGE_VERSION
    package_path.write_text(json.dumps(package, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    react.joinpath("src/App.jsx").write_text(
        'import {prepare} from "@chenglou/pretext";\n'
        "void prepare;\nexport default function App(){return <main>Deep target</main>}\n",
        encoding="utf-8",
    )

    build, _elapsed, log = runner._build_react_target(react, tmp_path / "artifact")

    assert build["ok"] is True, log
    assert (react / "node_modules/@chenglou/pretext/package.json").is_file()
    assert (react / "node_modules/@chenglou/freerange/package.json").is_file()


def test_react_target_rejects_dependency_version_drift(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.joinpath("submission").mkdir(parents=True)
    runner._activate_code_first_react(workspace)
    package_path = workspace / "submission/react/package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["dependencies"]["react"] = "0.0.0"
    package_path.write_text(json.dumps(package), encoding="utf-8")

    build, _elapsed, log = runner._build_react_target(
        workspace / "submission" / "react",
        tmp_path / "artifact",
    )

    assert build["ok"] is False
    assert "pinned shared target contract" in log


def test_value_trials_are_rejected_for_archived_v1_before_workspace_creation(tmp_path):
    args = runner.argparse.Namespace(
        protocol="conformance/agent-ui-v1/protocol.json",
        task="field-dispatch",
        arm="code-first",
        seed=104729,
        out=str(tmp_path / "must-not-exist"),
        model="gpt-test",
        with_value_trials=True,
        resume=False,
    )

    with pytest.raises(ValueError, match="V2"):
        runner._run_session(args)
    assert not (tmp_path / "must-not-exist").exists()


@pytest.mark.skipif(not _BUN, reason="The pinned Deep proof requires Bun on PATH")
def test_run_pair_records_seeded_arm_order_before_dispatch(tmp_path, monkeypatch):
    observed: list[str] = []

    def fake_session(args):
        manifest = json.loads(
            (tmp_path / "pair/pair-manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["arm_order"]
        observed.append(args.arm)
        return 0

    monkeypatch.setattr(runner, "_run_session", fake_session)
    args = runner.argparse.Namespace(
        protocol="conformance/agent-ui-v2/protocol.json",
        task="field-dispatch-lifecycle",
        seed=104729,
        out=str(tmp_path / "pair"),
        model="gpt-5.6-sol",
        with_value_trials=True,
        resume=False,
        allow_user_config=False,
        turn_timeout=10,
        no_install=False,
    )

    assert runner._run_pair(args) == 0
    manifest = json.loads(
        (tmp_path / "pair/pair-manifest.json").read_text(encoding="utf-8")
    )
    assert observed == manifest["arm_order"]
    assert observed == ["viewspec-deep", "code-first", "viewspec-core"]
    assert manifest["model_call_budget"] == {
        "lifecycle_per_arm": 10,
        "lifecycle_total": 30,
        "qualification_per_arm_max": 2,
        "qualification_total_max": 6,
        "repair_per_eligible_arm_max": 5,
        "repair_total_max": 15,
        "maximum_total_calls": 51,
    }
    assert len(manifest["viewspec_product_tree"]["sha256"]) == 64


def test_baseline_qualification_rolls_back_non_layout_regression(
    tmp_path,
    monkeypatch,
):
    protocol, protocol_path = runner._protocol("conformance/agent-ui-v2/protocol.json")
    task = protocol.task("field-dispatch-lifecycle")
    workspace = tmp_path / "workspace"
    hooks = "".join(f'<div data-eval-id="{hook}"></div>' for hook in STABLE_HOOKS)
    baseline_static = f"<body>{hooks}<p>baseline</p></body>"
    workspace.joinpath("submission/react/src").mkdir(parents=True)
    workspace.joinpath("submission/index.html").write_text(baseline_static, encoding="utf-8")
    workspace.joinpath("submission/react/src/App.jsx").write_text(
        f"export default()=> <main>{hooks}</main>", encoding="utf-8"
    )

    def score(first: bool, second: bool) -> dict:
        criteria = [
            {"id": "first", "dimension": "semantics", "passed": first},
            {"id": "second", "dimension": "interaction", "passed": second},
        ]
        return {
            "ok": first and second,
            "passed": sum(item["passed"] for item in criteria) * 3,
            "total": 6,
            "viewports": [
                {
                    "viewport": {"width": width, "height": height},
                    "criteria": criteria,
                    "layout_fidelity": 0.9,
                }
                for width, height in ((390, 844), (768, 1024), (1440, 1000))
            ],
        }

    initial_score = score(True, False)
    evaluations = iter((score(False, True), score(True, True)))
    calls = 0

    def fake_codex_turn(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            assert workspace.joinpath("submission/index.html").read_text(encoding="utf-8") == baseline_static
            assert not workspace.joinpath("submission/extra.html").exists()
        workspace.joinpath("submission/index.html").write_text(
            baseline_static.replace("baseline", f"candidate-{calls}"),
            encoding="utf-8",
        )
        if calls == 1:
            workspace.joinpath("submission/extra.html").write_text("transient", encoding="utf-8")
        runner._write(kwargs["events_path"], '{"type":"turn.completed","usage":{}}\n')
        return {
            "thread_id": "thread-lifecycle",
            "completed": True,
            "usage": {},
            "telemetry": {},
        }, 1, 0, ""

    monkeypatch.setattr(runner, "_codex_turn", fake_codex_turn)
    monkeypatch.setattr(
        runner,
        "_evaluate_turn",
        lambda **_kwargs: (
            next(evaluations),
            None,
            {"browser_score": 1},
            "candidate feedback",
            _healthy_targets(),
        ),
    )
    args = runner.argparse.Namespace(
        arm="code-first",
        model="gpt-test",
        allow_user_config=False,
        turn_timeout=10,
        no_install=False,
    )
    initial = {"step_id": "repair-and-finalize", "score": initial_score, "proof": None, "target_trials": _healthy_targets()}
    sources = runner._source_texts(workspace, "code-first")
    session = {"turns": [initial], "qualification_turns": [], "thread_id": "thread-lifecycle"}

    final, *_rest = runner._run_baseline_qualification(
        args=args,
        protocol=protocol,
        protocol_path=protocol_path,
        task=task,
        workspace=workspace,
        output=tmp_path / "out",
        session=session,
        final_turn=initial,
        thread_id="thread-lifecycle",
        feedback="second failed",
        previous_source=sources,
        previous_criteria=runner._criterion_states(initial_score),
    )

    assert session["qualification_turns"][0]["selected"] is False
    assert session["qualification_turns"][0]["functional_regressions_against_selected"]
    assert session["qualification_turns"][1]["selected"] is True
    assert session["qualification"]["selected_turn"]["index"] == 1
    assert final is session["qualification_turns"][1]
    assert "candidate-2" in workspace.joinpath("submission/index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "trial_id,criterion_id,expected",
    [
        (
            "break-escalation-action",
            "interaction:Show escalation guide",
            "browser:interaction:Show escalation guide",
        ),
        (
            "corrupt-reviewed-count",
            "interaction:Record review",
            "browser:interaction:Record review",
        ),
        (
            "break-escalation-visibility",
            "interaction:Show escalation guide",
            "browser:interaction:Show escalation guide",
        ),
        (
            "duplicate-j207-resource",
            "resource:job-j207",
            "browser:unique-hook:job-j207",
        ),
        (
            "break-j205-mobile-geometry",
            "text-geometry:long-title",
            "browser:text-geometry:J-205",
        ),
    ],
)
def test_observed_detectors_only_credit_trial_specific_failures(
    trial_id,
    criterion_id,
    expected,
):
    score = {
        "viewports": [
            {
                "criteria": [
                    {"id": criterion_id, "passed": False},
                    {"id": "unrelated", "passed": False},
                ]
            }
        ]
    }
    observed = runner._observed_detectors(
        trial_id,
        arm="code-first",
        score=score,
        proof=None,
    )

    assert expected in observed
    assert all("unrelated" not in item for item in observed)


def test_native_detector_credit_requires_attributable_failure_evidence():
    unrelated_score = _healthy_score(failed_criterion="interaction:Pause intake")
    unrelated_proof = {
        "ok": False,
        "errors": [{"code": "APP_PRETEXT_LAYOUT_FAILED"}],
        "static_analysis": {"status": "failed"},
        "text_layout": {"status": "failed"},
    }
    for trial_id in (
        "break-escalation-action",
        "corrupt-reviewed-count",
        "break-escalation-visibility",
        "duplicate-j207-resource",
        "break-j205-mobile-geometry",
    ):
        assert runner._observed_detectors(
            trial_id,
            arm="viewspec-deep",
            score=unrelated_score,
            proof=unrelated_proof,
        ) == []

    compile_observed = runner._observed_detectors(
        "break-escalation-action",
        arm="viewspec-core",
        score={"viewports": []},
        proof={
            "ok": False,
            "errors": [
                {
                    "code": "APP_STATE_TRIGGER_ACTION_MISSING",
                    "message": (
                        "Mutation trigger references missing action "
                        "dispatch.broken_escalation_action."
                    ),
                }
            ],
        },
    )
    assert compile_observed == ["compile-or-replay:reveal_escalation_guide"]
    no_viewports = ({"viewports": []}, unrelated_proof, {}, "failed", [])
    assert runner._evaluation_infrastructure_error(
        arm="viewspec-core",
        trial_id="break-escalation-action",
        evaluation=no_viewports,
    ) == "browser_or_scorer_did_not_produce_viewports"
    attributed_compile = (
        {"viewports": []},
        {
            "ok": False,
            "errors": [
                {
                    "code": "APP_STATE_TRIGGER_ACTION_MISSING",
                    "message": (
                        "Mutation trigger references missing action "
                        "dispatch.broken_escalation_action."
                    ),
                }
            ],
        },
        {},
        "failed",
        [],
    )
    assert runner._evaluation_infrastructure_error(
        arm="viewspec-core",
        trial_id="break-escalation-action",
        evaluation=attributed_compile,
    ) is None

    generic_replay_without_matching_browser = runner._observed_detectors(
        "corrupt-reviewed-count",
        arm="viewspec-core",
        score=unrelated_score,
        proof={
            "ok": False,
            "errors": [{"code": "APP_STATE_REPLAY_ASSERTION_FAILED"}],
        },
    )
    assert generic_replay_without_matching_browser == []


@pytest.mark.parametrize(
    ("trial_id", "proof_error", "expected"),
    [
        (
            "break-escalation-visibility",
            {
                "code": "APP_VISIBILITY_REPLAY_MISMATCH",
                "message": (
                    "Replay assertion escalation_reveal observed False after event 0. "
                    "(assertion=escalation_reveal, event=0, "
                    "mutation=reveal_escalation_guide, "
                    "path=$.state_replay_assertions.escalation_reveal."
                    "expect_visibility.show_escalation_panel, expected=True actual=False)"
                ),
            },
            "replay-or-browser:show_escalation_panel",
        ),
        (
            "corrupt-reviewed-count",
            {
                "code": "APP_STATE_REPLAY_STATE_MISMATCH",
                "message": (
                    "Replay assertion review_twice observed 4 after event 1. "
                    "(assertion=review_twice, event=1, "
                    "mutation=increment_reviewed_count, "
                    "path=$.state_replay_assertions.review_twice."
                    "expect_state.reviewed_count, expected=2 actual=4)"
                ),
            },
            "replay:increment_reviewed_count",
        ),
        (
            "break-escalation-visibility",
            {
                "code": "APP_VISIBILITY_REPLAY_MISMATCH",
                "message": (
                    "Replay assertion review_two_jobs expected "
                    "expect_visibility.show_escalation_panel=False, but observed True "
                    "after event 1. (assertion=review_two_jobs, event=1, "
                    "mutation=increment_reviewed_count, "
                    "path=$.state_replay_assertions.review_two_jobs."
                    "expect_visibility.show_escalation_panel, expected=False actual=True)"
                ),
            },
            "replay-or-browser:show_escalation_panel",
        ),
        (
            "corrupt-reviewed-count",
            {
                "code": "APP_STATE_REPLAY_STATE_MISMATCH",
                "message": (
                    "Replay assertion review_two_jobs expected "
                    "expect_state.reviewed_count=2, but observed 4 after event 1. "
                    "(assertion=review_two_jobs, event=1, "
                    "mutation=increment_reviewed_count, "
                    "path=$.state_replay_assertions.review_two_jobs."
                    "expect_state.reviewed_count, expected=2 actual=4)"
                ),
            },
            "replay:increment_reviewed_count",
        ),
    ],
)
def test_precise_replay_detector_completes_without_browser_output(
    trial_id,
    proof_error,
    expected,
):
    evaluation = (
        {"viewports": []},
        {"ok": False, "errors": [proof_error]},
        {},
        "compile stopped on expected replay failure",
        [],
    )

    assert runner._observed_detectors(
        trial_id,
        arm="viewspec-core",
        score=evaluation[0],
        proof=evaluation[1],
    ) == [expected]
    assert runner._evaluation_infrastructure_error(
        arm="viewspec-core",
        trial_id=trial_id,
        evaluation=evaluation,
    ) is None


def test_precise_replay_detector_requires_trial_identity_markers():
    proof = {
        "ok": False,
        "errors": [
            {
                "code": "APP_STATE_REPLAY_STATE_MISMATCH",
                "message": (
                    "assertion=unrelated mutation=unrelated path=$.state.unrelated "
                    "expected=2 actual=4"
                ),
            }
        ],
    }

    assert runner._observed_detectors(
        "corrupt-reviewed-count",
        arm="viewspec-core",
        score={"viewports": []},
        proof=proof,
    ) == []


def test_baseline_eligibility_rejects_build_and_scorer_failures():
    hooks = {"ok": True, "errors": []}
    build_failure = _healthy_targets()
    build_failure[1] = {
        **build_failure[1],
        "build": {"ok": False},
        "passed": False,
    }
    build_report = runner._eligibility_report(
        arm="code-first",
        score=_healthy_score(),
        proof=None,
        targets=build_failure,
        hooks=hooks,
    )
    assert build_report["eligible"] is False
    assert "target native-react build was unhealthy" in build_report["reasons"]

    scorer_failure = _healthy_targets()
    scorer_failure[1] = {
        **scorer_failure[1],
        "functional_acceptance": 0.0,
        "passed": False,
    }
    scorer_report = runner._eligibility_report(
        arm="code-first",
        score=_healthy_score(),
        proof=None,
        targets=scorer_failure,
        hooks=hooks,
    )
    assert scorer_report["eligible"] is False
    assert any("native-react functional acceptance was 0.0" in item for item in scorer_report["reasons"])


def test_value_trials_retry_infrastructure_once_then_mark_invalid(
    tmp_path,
    monkeypatch,
):
    protocol, protocol_path = runner._protocol("conformance/agent-ui-v2/protocol.json")
    task = protocol.task("field-dispatch-lifecycle")
    workspace = tmp_path / "workspace"
    static = "".join(
        f'<div data-eval-id="{hook}"></div>'
        for hook in STABLE_HOOKS
    )
    workspace.joinpath("submission/react/src").mkdir(parents=True)
    workspace.joinpath("submission/index.html").write_text(
        f"<body>{static}</body>",
        encoding="utf-8",
    )
    workspace.joinpath("submission/react/src/App.jsx").write_text(
        f"export default()=> <main>{static}</main>",
        encoding="utf-8",
    )
    attempts = 0

    def fail_evaluation(**_kwargs):
        nonlocal attempts
        attempts += 1
        raise OSError("local browser unavailable")

    monkeypatch.setattr(runner, "_evaluate_turn", fail_evaluation)
    monkeypatch.setattr(
        runner,
        "_codex_turn",
        lambda **_kwargs: pytest.fail("invalid trials must not invoke repairs"),
    )
    target = {
        "id": "target",
        "applicable": True,
        "build": {"ok": True},
        "passed": True,
        "parity": 1.0,
    }
    final_turn = {
        "score": {
            "viewports": [
                {
                    "criteria": [
                        {"id": "functional", "dimension": "semantics", "passed": True}
                    ]
                }
            ]
        },
        "proof": None,
        "target_trials": [
            {**target, "id": "static-shell"},
            {**target, "id": "native-react"},
        ],
    }
    args = runner.argparse.Namespace(
        arm="code-first",
        seed=104729,
        no_install=False,
        model="gpt-test",
        allow_user_config=False,
        turn_timeout=10,
    )
    session = {"turns": [final_turn]}

    evidence = runner._run_value_trials(
        args=args,
        protocol=protocol,
        protocol_path=protocol_path,
        task=task,
        workspace=workspace,
        output=tmp_path / "out",
        session=session,
        final_turn=final_turn,
    )

    assert attempts == 14
    assert all(
        item["invalid_reason"] == "repeated_infrastructure_failure"
        for item in [
            *evidence["mutation_trials"],
            *evidence["negative_control_trials"],
        ]
    )
    assert (tmp_path / "out/checkpoint.json").is_file()


def test_bounded_baseline_qualification_stops_after_becoming_eligible(
    tmp_path,
    monkeypatch,
):
    protocol, protocol_path = runner._protocol("conformance/agent-ui-v2/protocol.json")
    task = protocol.task("field-dispatch-lifecycle")
    workspace = tmp_path / "workspace"
    hooks = "".join(f'<div data-eval-id="{hook}"></div>' for hook in STABLE_HOOKS)
    workspace.joinpath("submission/react/src").mkdir(parents=True)
    workspace.joinpath("submission/index.html").write_text(
        f"<body>{hooks}</body>", encoding="utf-8"
    )
    workspace.joinpath("submission/react/src/App.jsx").write_text(
        f"export default()=> <main>{hooks}</main>", encoding="utf-8"
    )
    initial = {
        "score": _healthy_score(failed_criterion="interaction:Record review"),
        "proof": None,
        "target_trials": _healthy_targets(),
    }
    prompts: list[str] = []

    def fake_codex_turn(**kwargs):
        prompts.append(kwargs["prompt"])
        runner._write(
            kwargs["events_path"],
            '{"type":"thread.started","thread_id":"thread-qualification"}\n'
            '{"type":"turn.completed","usage":{}}\n',
        )
        return (
            {
                "thread_id": "thread-qualification",
                "completed": True,
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 0,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 2,
                    "reasoning_output_tokens": 0,
                },
                "telemetry": {},
            },
            50,
            0,
            "",
        )

    monkeypatch.setattr(runner, "_codex_turn", fake_codex_turn)
    monkeypatch.setattr(
        runner,
        "_evaluate_turn",
        lambda **_kwargs: (
            _healthy_score(),
            None,
            {"browser_score": 5, "react_build": 2},
            "all functional criteria passed",
            _healthy_targets(),
        ),
    )
    args = runner.argparse.Namespace(
        arm="code-first",
        model="gpt-test",
        allow_user_config=False,
        turn_timeout=10,
        no_install=False,
    )
    sources = runner._source_texts(workspace, "code-first")
    session = {"turns": [initial], "qualification_turns": [], "thread_id": "thread-lifecycle"}

    final, thread_id, _feedback, _sources, _criteria = runner._run_baseline_qualification(
        args=args,
        protocol=protocol,
        protocol_path=protocol_path,
        task=task,
        workspace=workspace,
        output=tmp_path / "out",
        session=session,
        final_turn=initial,
        thread_id="thread-lifecycle",
        feedback="record review failed",
        previous_source=sources,
        previous_criteria=runner._criterion_states(initial["score"]),
    )

    assert len(prompts) == 1
    assert "attempt 1 of 2" in prompts[0]
    assert "stable\nevaluation identity" in prompts[0]
    assert thread_id == "thread-lifecycle"
    assert final["eligibility_after"]["eligible"] is True
    assert session["qualification"]["turn_count"] == 1
    assert session["qualification"]["exhausted"] is False
    checkpoint = json.loads((tmp_path / "out/checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["stage"] == "qualification"
    assert checkpoint["next_index"] == 1


def test_baseline_qualification_runs_for_layout_only_miss_and_stops_at_target(
    tmp_path,
    monkeypatch,
):
    protocol, protocol_path = runner._protocol("conformance/agent-ui-v2/protocol.json")
    task = protocol.task("field-dispatch-lifecycle")
    workspace = tmp_path / "workspace"
    hooks = "".join(f'<div data-eval-id="{hook}"></div>' for hook in STABLE_HOOKS)
    workspace.joinpath("submission/react/src").mkdir(parents=True)
    workspace.joinpath("submission/index.html").write_text(
        f"<body>{hooks}</body>", encoding="utf-8"
    )
    workspace.joinpath("submission/react/src/App.jsx").write_text(
        f"export default()=> <main>{hooks}</main>", encoding="utf-8"
    )
    score = _healthy_score()
    for viewport in score["viewports"]:
        viewport["layout_fidelity"] = 0.4
    initial = {
        "step_id": "repair-and-finalize",
        "score": score,
        "proof": None,
        "target_trials": _healthy_targets(),
    }
    improved = _healthy_score()
    for viewport in improved["viewports"]:
        viewport["layout_fidelity"] = 0.7
    prompts: list[str] = []

    def fake_codex_turn(**kwargs):
        prompts.append(kwargs["prompt"])
        runner._write(kwargs["events_path"], '{"type":"turn.completed","usage":{}}\n')
        return {
            "thread_id": "thread-lifecycle",
            "completed": True,
            "usage": {},
            "telemetry": {},
        }, 1, 0, ""

    monkeypatch.setattr(runner, "_codex_turn", fake_codex_turn)
    monkeypatch.setattr(
        runner,
        "_evaluate_turn",
        lambda **_kwargs: (
            improved,
            None,
            {"browser_score": 1},
            "layout improved",
            _healthy_targets(),
        ),
    )
    args = runner.argparse.Namespace(
        arm="code-first",
        model="gpt-test",
        allow_user_config=False,
        turn_timeout=10,
        no_install=False,
    )
    sources = runner._source_texts(workspace, "code-first")
    session = {
        "turns": [initial],
        "qualification_turns": [],
        "thread_id": "thread-lifecycle",
    }

    final, *_rest = runner._run_baseline_qualification(
        args=args,
        protocol=protocol,
        protocol_path=protocol_path,
        task=task,
        workspace=workspace,
        output=tmp_path / "out",
        session=session,
        final_turn=initial,
        thread_id="thread-lifecycle",
        feedback="layout anchors missed",
        previous_source=sources,
        previous_criteria=runner._criterion_states(score),
    )

    assert final is session["qualification_turns"][0]
    assert len(prompts) == 1
    assert "reference-anchor layout" in prompts[0]
    assert session["qualification"]["triggered"] is True
    assert session["qualification"]["turn_count"] == 1
    assert session["qualification"]["exhausted"] is False
    assert session["qualification"]["layout_target_met"] is True
    assert session["qualification"]["selected_turn"]["kind"] == "qualification"


def test_score_feedback_reports_per_viewport_layout_floor_and_target():
    score = _healthy_score()
    score["viewports"][0]["layout_fidelity"] = 0.6123456

    feedback = runner._score_feedback(score, layout_target=0.6769)

    assert "390px=0.612346" in feedback
    assert "768px=0.970000" in feedback
    assert "floor=0.612346" in feedback
    assert "target=0.676900, met=False" in feedback


def test_baseline_qualification_resumes_at_the_next_bounded_attempt(
    tmp_path,
    monkeypatch,
):
    protocol, protocol_path = runner._protocol("conformance/agent-ui-v2/protocol.json")
    task = protocol.task("field-dispatch-lifecycle")
    workspace = tmp_path / "workspace"
    hooks = "".join(f'<div data-eval-id="{hook}"></div>' for hook in STABLE_HOOKS)
    workspace.joinpath("submission/react/src").mkdir(parents=True)
    workspace.joinpath("submission/index.html").write_text(
        f"<body>{hooks}</body>", encoding="utf-8"
    )
    workspace.joinpath("submission/react/src/App.jsx").write_text(
        f"export default()=> <main>{hooks}</main>", encoding="utf-8"
    )
    unhealthy = _healthy_score(failed_criterion="interaction:Record review")
    initial = {
        "score": unhealthy,
        "proof": None,
        "target_trials": _healthy_targets(),
    }
    prompts: list[str] = []
    completions = iter(((False, 2), (True, 0)))
    evaluations = iter((unhealthy, _healthy_score()))

    def fake_codex_turn(**kwargs):
        completed, exit_code = next(completions)
        prompts.append(kwargs["prompt"])
        runner._write(kwargs["events_path"], '{"type":"turn.completed","usage":{}}\n')
        return (
            {
                "thread_id": "thread-lifecycle",
                "completed": completed,
                "usage": {},
                "telemetry": {},
            },
            10,
            exit_code,
            "",
        )

    def fake_evaluate(**_kwargs):
        score = next(evaluations)
        return score, None, {"browser_score": 1}, "compact feedback", _healthy_targets()

    monkeypatch.setattr(runner, "_codex_turn", fake_codex_turn)
    monkeypatch.setattr(runner, "_evaluate_turn", fake_evaluate)
    args = runner.argparse.Namespace(
        arm="code-first",
        model="gpt-test",
        allow_user_config=False,
        turn_timeout=10,
        no_install=False,
    )
    sources = runner._source_texts(workspace, "code-first")
    criteria = runner._criterion_states(unhealthy)
    session = {"turns": [initial], "qualification_turns": [], "thread_id": "thread-lifecycle"}

    first = runner._run_baseline_qualification(
        args=args,
        protocol=protocol,
        protocol_path=protocol_path,
        task=task,
        workspace=workspace,
        output=tmp_path / "out",
        session=session,
        final_turn=initial,
        thread_id="thread-lifecycle",
        feedback="record review failed",
        previous_source=sources,
        previous_criteria=criteria,
    )

    assert len(session["qualification_turns"]) == 1
    checkpoint = json.loads((tmp_path / "out/checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["stage"] == "qualification"
    assert checkpoint["next_index"] == 1

    final, thread_id, _feedback, _sources, _criteria = runner._run_baseline_qualification(
        args=args,
        protocol=protocol,
        protocol_path=protocol_path,
        task=task,
        workspace=workspace,
        output=tmp_path / "out",
        session=session,
        final_turn=first[0],
        thread_id=first[1],
        feedback=first[2],
        previous_source=first[3],
        previous_criteria=first[4],
    )

    assert ["attempt 1 of 2" in prompt for prompt in prompts] == [True, False]
    assert "attempt 2 of 2" in prompts[1]
    assert thread_id == "thread-lifecycle"
    assert final["eligibility_after"]["eligible"] is True
    assert session["qualification"]["turn_count"] == 2
    assert session["qualification"]["exhausted"] is False


def test_baseline_qualification_exhausts_after_two_unhealthy_attempts(
    tmp_path,
    monkeypatch,
):
    protocol, protocol_path = runner._protocol("conformance/agent-ui-v2/protocol.json")
    task = protocol.task("field-dispatch-lifecycle")
    workspace = tmp_path / "workspace"
    hooks = "".join(f'<div data-eval-id="{hook}"></div>' for hook in STABLE_HOOKS)
    workspace.joinpath("submission/react/src").mkdir(parents=True)
    workspace.joinpath("submission/index.html").write_text(
        f"<body>{hooks}</body>", encoding="utf-8"
    )
    workspace.joinpath("submission/react/src/App.jsx").write_text(
        f"export default()=> <main>{hooks}</main>", encoding="utf-8"
    )
    unhealthy = _healthy_score(failed_criterion="interaction:Record review")
    initial = {"score": unhealthy, "proof": None, "target_trials": _healthy_targets()}
    calls = 0

    def fake_codex_turn(**kwargs):
        nonlocal calls
        calls += 1
        runner._write(kwargs["events_path"], '{"type":"turn.completed","usage":{}}\n')
        return (
            {"thread_id": "thread-lifecycle", "completed": True, "usage": {}, "telemetry": {}},
            10,
            0,
            "",
        )

    monkeypatch.setattr(runner, "_codex_turn", fake_codex_turn)
    monkeypatch.setattr(
        runner,
        "_evaluate_turn",
        lambda **_kwargs: (
            unhealthy,
            None,
            {"browser_score": 1},
            "still unhealthy",
            _healthy_targets(),
        ),
    )
    args = runner.argparse.Namespace(
        arm="code-first",
        model="gpt-test",
        allow_user_config=False,
        turn_timeout=10,
        no_install=False,
    )
    sources = runner._source_texts(workspace, "code-first")
    session = {"turns": [initial], "qualification_turns": [], "thread_id": "thread-lifecycle"}

    final, *_rest = runner._run_baseline_qualification(
        args=args,
        protocol=protocol,
        protocol_path=protocol_path,
        task=task,
        workspace=workspace,
        output=tmp_path / "out",
        session=session,
        final_turn=initial,
        thread_id="thread-lifecycle",
        feedback="record review failed",
        previous_source=sources,
        previous_criteria=runner._criterion_states(unhealthy),
    )

    assert calls == 2
    assert final["eligibility_after"]["eligible"] is False
    assert session["qualification"]["turn_count"] == 2
    assert session["qualification"]["exhausted"] is True


def test_golden_eligible_value_pipeline_executes_all_trials_and_repairs(
    tmp_path,
    monkeypatch,
):
    protocol, protocol_path = runner._protocol("conformance/agent-ui-v2/protocol.json")
    task = protocol.task("field-dispatch-lifecycle")
    workspace = tmp_path / "workspace"
    hooks = "".join(f'<div data-eval-id="{hook}"></div>' for hook in STABLE_HOOKS)
    healthy_static = f"<!doctype html><body>{hooks}</body>\n"
    healthy_react = f"export default()=> <main>{hooks}</main>\n"
    workspace.joinpath("submission/react/src").mkdir(parents=True)
    workspace.joinpath("submission/index.html").write_text(healthy_static, encoding="utf-8")
    workspace.joinpath("submission/react/src/App.jsx").write_text(healthy_react, encoding="utf-8")
    evaluation_count = 0

    def fake_evaluate(**kwargs):
        nonlocal evaluation_count
        evaluation_count += 1
        text = (kwargs["workspace"] / "submission/index.html").read_text(encoding="utf-8")
        failed = _injected_failure_criterion(text)
        return (
            _healthy_score(failed_criterion=failed),
            None,
            {"browser_score": 3, "react_build": 2},
            "healthy" if failed is None else f"failed {failed}",
            _fake_evaluation_targets(kwargs),
        )

    repair_calls: list[Path] = []

    def fake_repair(**kwargs):
        assert kwargs["thread_id"] is None
        repair_calls.append(kwargs["workspace"])
        kwargs["workspace"].joinpath("submission/index.html").write_text(
            healthy_static, encoding="utf-8"
        )
        runner._write(
            kwargs["events_path"],
            '{"type":"thread.started","thread_id":"isolated-repair"}\n'
            '{"type":"turn.completed","usage":{}}\n',
        )
        return (
            {
                "thread_id": "isolated-repair",
                "completed": True,
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 0,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 2,
                    "reasoning_output_tokens": 0,
                },
                "telemetry": {},
            },
            25,
            0,
            "",
        )

    monkeypatch.setattr(runner, "_evaluate_turn", fake_evaluate)
    monkeypatch.setattr(runner, "_codex_turn", fake_repair)
    args = runner.argparse.Namespace(
        arm="code-first",
        seed=104729,
        no_install=False,
        model="gpt-test",
        allow_user_config=False,
        turn_timeout=10,
    )
    final_turn = {
        "score": _healthy_score(),
        "proof": None,
        "target_trials": _healthy_targets_with_artifacts(tmp_path / "baseline-targets"),
    }
    session = {"turns": [final_turn]}

    evidence = runner._finalize_value_evidence(
        runner._run_value_trials(
            args=args,
            protocol=protocol,
            protocol_path=protocol_path,
            task=task,
            workspace=workspace,
            output=tmp_path / "out",
            session=session,
            final_turn=final_turn,
        )
    )
    session_payload = {
        "schema_version": 1,
        "arm_id": "code-first",
        "turns": [
            {
                "step_id": "final",
                "phase": "repair",
                "usage": {},
                "wall_time_ms": 0,
                "deterministic_ms": 0,
                "score": _healthy_score(),
            }
        ],
        "value_evidence": evidence,
    }
    summary = runner.summarize_agent_eval_session(session_payload)

    assert evaluation_count == 12
    assert len(repair_calls) == 5
    assert len(evidence["mutation_trials"]) == 5
    assert len(evidence["negative_control_trials"]) == 2
    assert all(item["applicable"] for item in evidence["mutation_trials"])
    assert all(item["detected"] and item["repaired"] for item in evidence["mutation_trials"])
    assert all(item["detected"] is False for item in evidence["negative_control_trials"])
    assert summary["value_evidence"]["structural_evidence_complete"] is True
    assert summary["value_evidence"]["evidence_complete"] is True
    assert summary["value_evidence"]["mutation"]["detection_rate"] == 1.0
    assert summary["value_evidence"]["mutation"]["repair_rate"] == 1.0
    assert summary["value_evidence"]["mutation"]["false_positive_rate"] == 0.0
    assert summary["value_evidence"]["cross_target"]["minimum_parity"] == 0.99
    assert workspace.joinpath("submission/index.html").read_text(encoding="utf-8") == healthy_static
    assert workspace.joinpath("submission/react/src/App.jsx").read_text(encoding="utf-8") == healthy_react

    resumed = runner._run_value_trials(
        args=args,
        protocol=protocol,
        protocol_path=protocol_path,
        task=task,
        workspace=workspace,
        output=tmp_path / "out",
        session=session,
        final_turn=final_turn,
    )

    assert resumed == evidence
    assert evaluation_count == 12
    assert len(repair_calls) == 5


def test_interrupted_repair_turn_is_checkpointed_and_never_retried(
    tmp_path,
    monkeypatch,
):
    protocol, protocol_path = runner._protocol("conformance/agent-ui-v2/protocol.json")
    task = protocol.task("field-dispatch-lifecycle")
    workspace = tmp_path / "workspace"
    hooks = "".join(f'<div data-eval-id="{hook}"></div>' for hook in STABLE_HOOKS)
    healthy_static = f"<!doctype html><body>{hooks}</body>\n"
    healthy_react = f"export default()=> <main>{hooks}</main>\n"
    workspace.joinpath("submission/react/src").mkdir(parents=True)
    workspace.joinpath("submission/index.html").write_text(healthy_static, encoding="utf-8")
    workspace.joinpath("submission/react/src/App.jsx").write_text(healthy_react, encoding="utf-8")
    def fake_evaluate(**kwargs):
        text = (kwargs["workspace"] / "submission/index.html").read_text(encoding="utf-8")
        failed = _injected_failure_criterion(text)
        return (
            _healthy_score(failed_criterion=failed),
            None,
            {"browser_score": 1},
            "healthy" if failed is None else f"failed {failed}",
            _fake_evaluation_targets(kwargs),
        )

    model_invocations = 0

    def interrupted_then_repair(**kwargs):
        nonlocal model_invocations
        model_invocations += 1
        if model_invocations == 1:
            raise KeyboardInterrupt("simulated process interruption")
        kwargs["workspace"].joinpath("submission/index.html").write_text(
            healthy_static,
            encoding="utf-8",
        )
        runner._write(kwargs["events_path"], '{"type":"turn.completed","usage":{}}\n')
        return (
            {"thread_id": "repair-thread", "completed": True, "usage": {}, "telemetry": {}},
            10,
            0,
            "",
        )

    monkeypatch.setattr(runner, "_evaluate_turn", fake_evaluate)
    monkeypatch.setattr(runner, "_codex_turn", interrupted_then_repair)
    args = runner.argparse.Namespace(
        arm="code-first",
        seed=104729,
        no_install=False,
        model="gpt-test",
        allow_user_config=False,
        turn_timeout=10,
    )
    final_turn = {"score": _healthy_score(), "proof": None, "target_trials": _healthy_targets()}
    session = {"turns": [final_turn]}

    with pytest.raises(KeyboardInterrupt, match="simulated process interruption"):
        runner._run_value_trials(
            args=args,
            protocol=protocol,
            protocol_path=protocol_path,
            task=task,
            workspace=workspace,
            output=tmp_path / "out",
            session=session,
            final_turn=final_turn,
        )

    assert workspace.joinpath("submission/index.html").read_text(encoding="utf-8") == healthy_static
    assert list((tmp_path / "out/value-trials").glob("*/repair/repair-attempt-checkpoint.json"))

    evidence = runner._run_value_trials(
        args=args,
        protocol=protocol,
        protocol_path=protocol_path,
        task=task,
        workspace=workspace,
        output=tmp_path / "out",
        session=session,
        final_turn=final_turn,
    )

    assert model_invocations == 5
    assert len(evidence["repair_turns"]) == 5
    interrupted = [item for item in evidence["repair_turns"] if item["exit_code"] == 125]
    assert len(interrupted) == 1
    assert interrupted[0]["model_error"] == "repair result checkpoint was interrupted"


@pytest.mark.parametrize("repair_mode", ["unfixed", "regression"])
def test_value_trial_repair_rejects_unfixed_faults_and_new_regressions(
    tmp_path,
    monkeypatch,
    repair_mode,
):
    protocol, protocol_path = runner._protocol("conformance/agent-ui-v2/protocol.json")
    task = protocol.task("field-dispatch-lifecycle")
    workspace = tmp_path / "workspace"
    hooks = "".join(f'<div data-eval-id="{hook}"></div>' for hook in STABLE_HOOKS)
    healthy_static = f"<!doctype html><body>{hooks}</body>\n"
    healthy_react = f"export default()=> <main>{hooks}</main>\n"
    workspace.joinpath("submission/react/src").mkdir(parents=True)
    workspace.joinpath("submission/index.html").write_text(healthy_static, encoding="utf-8")
    workspace.joinpath("submission/react/src/App.jsx").write_text(healthy_react, encoding="utf-8")
    def fake_evaluate(**kwargs):
        text = (kwargs["workspace"] / "submission/index.html").read_text(encoding="utf-8")
        if "repair-regression" in text:
            failed = "interaction:Pause intake"
        else:
            failed = _injected_failure_criterion(text)
        return (
            _healthy_score(failed_criterion=failed),
            None,
            {"browser_score": 1},
            "healthy" if failed is None else f"failed {failed}",
            _fake_evaluation_targets(kwargs),
        )

    def fake_repair(**kwargs):
        if repair_mode == "regression":
            kwargs["workspace"].joinpath("submission/index.html").write_text(
                healthy_static + "<!-- repair-regression -->\n",
                encoding="utf-8",
            )
        runner._write(kwargs["events_path"], '{"type":"turn.completed","usage":{}}\n')
        return (
            {"thread_id": "repair-thread", "completed": True, "usage": {}, "telemetry": {}},
            10,
            0,
            "",
        )

    monkeypatch.setattr(runner, "_evaluate_turn", fake_evaluate)
    monkeypatch.setattr(runner, "_codex_turn", fake_repair)
    args = runner.argparse.Namespace(
        arm="code-first",
        seed=104729,
        no_install=False,
        model="gpt-test",
        allow_user_config=False,
        turn_timeout=10,
    )
    final_turn = {"score": _healthy_score(), "proof": None, "target_trials": _healthy_targets()}
    session = {"turns": [final_turn]}

    evidence = runner._run_value_trials(
        args=args,
        protocol=protocol,
        protocol_path=protocol_path,
        task=task,
        workspace=workspace,
        output=tmp_path / "out",
        session=session,
        final_turn=final_turn,
    )

    assert len(evidence["repair_turns"]) == 5
    assert all(item["detected"] is True for item in evidence["mutation_trials"])
    assert all(item["repaired"] is False for item in evidence["mutation_trials"])
    if repair_mode == "regression":
        assert all(not item["repair_remaining_detectors"] for item in evidence["mutation_trials"])
    else:
        assert all(item["repair_remaining_detectors"] for item in evidence["mutation_trials"])


def test_repair_feedback_includes_stable_hook_eligibility_failure(
    tmp_path,
    monkeypatch,
):
    protocol, protocol_path = runner._protocol("conformance/agent-ui-v2/protocol.json")
    task = protocol.task("field-dispatch-lifecycle")
    final_step = task.steps[-1]
    static_hooks = "".join(
        f'<div data-eval-id="{hook}"></div>' for hook in STABLE_HOOKS
    )
    react_hooks = "".join(
        f'<div data-eval-id="{hook}"></div>'
        for hook in STABLE_HOOKS
        if hook != "job-j207"
    )
    repaired_sources = {
        "submission/index.html": f"<body>{static_hooks}</body>",
        "submission/react/src/App.jsx": (
            "export default()=> <main>"
            + react_hooks
            + '<div data-eval-id={job.id === "J-207" ? "job-j207" : undefined}></div>'
            + "</main>"
        ),
    }
    monkeypatch.setattr(
        runner,
        "_evaluate_turn",
        lambda **_kwargs: (
            _healthy_score(),
            None,
            {"browser_score": 1},
            "Browser acceptance passed 133/133.",
            _healthy_targets(),
        ),
    )
    events = tmp_path / "repair/events.jsonl"
    runner._write(events, '{"type":"turn.completed","usage":{}}\n')
    args = runner.argparse.Namespace(arm="code-first", no_install=False)

    record, _turn, _deterministic_ms = runner._verify_repair_once(
        args=args,
        protocol_path=protocol_path,
        task=task,
        final_step=final_step,
        trial_id="duplicate-j207-resource",
        expected=["browser:unique-hook:job-j207"],
        record={},
        repair_workspace=tmp_path / "repair-workspace",
        repair_root=tmp_path / "repair",
        repaired_sources=repaired_sources,
        repaired_hash="a" * 64,
        parsed={"completed": True, "usage": {}, "telemetry": {}},
        model_ms=1,
        exit_code=0,
        repair_model_error=None,
        prompt_fact={},
        events=events,
    )

    assert record["repaired"] is False
    assert record["repair_eligibility"]["eligible"] is False
    assert any(
        "react:job-j207" in reason
        for reason in record["repair_eligibility"]["reasons"]
    )
    assert "Repair eligibility failed" in record["repair_feedback"]
    assert "react:job-j207" in record["repair_feedback"]


@pytest.mark.e2e
@pytest.mark.skipif(
    os.environ.get(_PRODUCT_E2E_OPT_IN) != "1",
    reason=f"set {_PRODUCT_E2E_OPT_IN}=1 to run retained V2 product gates",
)
@pytest.mark.skipif(not _NODE or not _PLAYWRIGHT.is_file(), reason="Pinned browser runtime is required")
@pytest.mark.parametrize(
    "fixture",
    _RETAINED_PRODUCT_CASES,
    ids=lambda fixture: f"{Path(fixture['source']).parts[1]}-{fixture['arm']}",
)
def test_retained_viewspec_arms_clear_v2_product_gates(tmp_path, fixture):
    manifest = json.loads(_RETAINED_PRODUCT_FIXTURES.read_text(encoding="utf-8"))
    protocol, protocol_path = runner._protocol(runner.DEFAULT_PROTOCOL)
    task = protocol.task(manifest["task_id"])
    final_step = task.steps[-1]
    gates = manifest["gates"]
    retained_root = os.environ.get("VIEWSPEC_AGENT_UI_V2_PRODUCT_E2E_ARTIFACT_DIR")
    run_root = Path(retained_root) if retained_root else tmp_path
    run_root.mkdir(parents=True, exist_ok=True)

    arm = fixture["arm"]
    case_id = f"{Path(fixture['source']).parts[1]}-{arm}"
    fixture_root = run_root / case_id
    workspace = fixture_root / "workspace"
    workspace.mkdir(parents=True)
    source = _RETAINED_PRODUCT_FIXTURES.parent / fixture["source"]
    shutil.copy2(source, workspace / "viewspec.app.json")
    score, proof, _timings, _feedback, targets = runner._evaluate_turn(
        workspace=workspace,
        output=fixture_root / "output",
        protocol_path=protocol_path,
        task=task,
        arm=arm,
        step=final_step,
        step_index=len(task.steps) - 1,
        install=True,
    )

    assert runner._dimension_score(score, excluded={"layout_fidelity"}) == gates[
        "functional_acceptance"
    ]
    assert runner._layout_fidelity(score) >= gates["layout_fidelity_minimum"]
    assert proof and proof["ok"] is gates["native_proof_required"]
    assert [trial["id"] for trial in targets] == gates["required_target_trials"]
    assert all(trial["passed"] is True for trial in targets)
    react = next(trial for trial in targets if trial["id"] == "native-react")
    assert min(react["parity_by_viewport"].values()) >= gates[
        "parity_minimum_per_viewport"
    ]
    if arm == "viewspec-deep":
        assert proof["static_analysis"]["status"] == "passed"
        assert proof["text_layout"]["status"] == "passed"


@pytest.mark.skipif(not _NODE or not _PLAYWRIGHT.is_file(), reason="Pinned browser runtime is required")
def test_browser_scorer_records_runtime_screenshot_and_text_geometry_fault(tmp_path):
    html = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><style>h1{width:200px;font-size:20px}</style></head>
<body><h1 hidden>Stormwater pump failure.</h1><main><h1 data-eval-id="incident-title">Stormwater pump failure.</h1>
<button id="guide">Show guide</button><button id="pause">Pause intake</button>
<button id="review">Record review</button><p data-eval-id="job-j207">J-207</p>
<p id="result" hidden></p></main>
<script>
document.querySelector("#guide").onclick=()=>{const el=document.querySelector("#result");el.hidden=false;el.textContent="Guide ready"};
document.querySelector("#pause").onclick=()=>{const el=document.querySelector("#result");el.hidden=false;el.textContent="Intake paused"};
document.querySelector("#review").onclick=()=>{const el=document.querySelector("#result");el.hidden=false;el.innerHTML="<span>Review count:</span> <strong>1</strong>"};
</script></body></html>
"""
    candidate = tmp_path / "candidate"
    reference = tmp_path / "reference"
    candidate.mkdir()
    reference.mkdir()
    candidate.joinpath("index.html").write_text(html, encoding="utf-8")
    reference.joinpath("index.html").write_text(html, encoding="utf-8")
    spec = {
        "required_text": ["Stormwater pump failure"],
        "forbidden_text": [],
        "required_buttons": [],
        "text_order": [],
        "visual_anchors": [],
        "interactions": [
            {"button": "Show guide", "reveals": "Guide ready"},
            {
                "button": "Record review",
                "assertions": [{"kind": "visible_text", "text": "Review count: 1"}],
            },
            {"button": "Pause intake", "reveals": "Intake paused"},
        ],
        "unique_text": ["J-207"],
        "resources": [{"identity": "job-j207", "text": "J-207", "count": 1}],
        "text_geometry": [
            {
                "text": "Stormwater pump failure",
                "identity": "incident-title",
                "resource": {"record_id": "INC-1042", "field": "title"},
                "viewport_width": 390,
                "minimum_lines": 2,
                "no_clip": True,
            }
        ],
    }
    spec_path = tmp_path / "spec.json"
    report_path = tmp_path / "report.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    result = subprocess.run(
        [
            _NODE,
            str(runner.BROWSER_SCORER),
            "--candidate",
            str(candidate),
            "--candidate-entry",
            "index.html",
            "--reference",
            str(reference / "index.html"),
            "--reference-step",
            "0",
            "--spec",
            str(spec_path),
            "--out",
            str(report_path),
            "--evidence",
            str(tmp_path / "evidence"),
        ],
        cwd=runner.ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=120,
    )

    if result.returncode == 1 and "listen EPERM" in result.stdout:
        pytest.skip("Sandbox does not allow the local browser scorer server")
    assert result.returncode == 0, result.stdout
    report = json.loads(report_path.read_text(encoding="utf-8"))
    mobile = next(item for item in report["viewports"] if item["viewport"]["width"] == 390)
    geometry = next(item for item in mobile["criteria"] if item["id"].startswith("text-geometry:"))
    interactions = [
        item for item in mobile["criteria"] if item["id"].startswith("interaction:")
    ]
    assert geometry["passed"] is True
    assert geometry["observed"]["line_count"] >= 2
    assert geometry["observed"]["text_matches"] is True
    assert len(interactions) == 3
    assert all(item["passed"] for item in interactions)
    assert next(item for item in mobile["criteria"] if item["id"] == "unique-text:J-207")["passed"]
    assert next(item for item in mobile["criteria"] if item["id"] == "resource:job-j207")["passed"]
    assert len(mobile["screenshot_sha256"]) == 64
    assert mobile["screenshot_bytes"] > 0
    assert mobile["telemetry"]["page_errors"] == []
    assert mobile["telemetry"]["document"]["elementCount"] > 0
    assert report["scorer"]["browser_version"]
    assert len(report["scorer"]["score_spec_sha256"]) == 64

    candidate.joinpath("index.html").write_text(
        html.replace(
            "Stormwater pump failure.",
            "Stormwater pump failure.__unbroken_eval_suffix_" + ("X" * 96),
        ),
        encoding="utf-8",
    )
    fault_report = tmp_path / "fault-report.json"
    fault_result = subprocess.run(
        [
            _NODE,
            str(runner.BROWSER_SCORER),
            "--candidate",
            str(candidate),
            "--candidate-entry",
            "index.html",
            "--reference",
            str(reference / "index.html"),
            "--reference-step",
            "0",
            "--spec",
            str(spec_path),
            "--out",
            str(fault_report),
            "--evidence",
            str(tmp_path / "fault-evidence"),
        ],
        cwd=runner.ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=120,
    )
    assert fault_result.returncode == 2, fault_result.stdout
    fault = json.loads(fault_report.read_text(encoding="utf-8"))
    fault_mobile = next(
        item for item in fault["viewports"] if item["viewport"]["width"] == 390
    )
    fault_geometry = next(
        item
        for item in fault_mobile["criteria"]
        if item["id"].startswith("text-geometry:")
    )
    assert fault_geometry["passed"] is False
    assert fault_geometry["observed"]["text_matches"] is False

    candidate.joinpath("index.html").write_text(
        html.replace(
            "h1{width:200px;font-size:20px}",
            "h1{width:200px;font-size:20px;margin-left:180px}",
        ),
        encoding="utf-8",
    )
    spec["visual_anchors"] = ["Stormwater pump failure."]
    spec["text_geometry"] = []
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    parity_report = tmp_path / "parity-report.json"
    parity_result = subprocess.run(
        [
            _NODE,
            str(runner.BROWSER_SCORER),
            "--candidate",
            str(candidate),
            "--candidate-entry",
            "index.html",
            "--reference",
            str(reference / "index.html"),
            "--reference-step",
            "0",
            "--spec",
            str(spec_path),
            "--out",
            str(parity_report),
            "--evidence",
            str(tmp_path / "parity-evidence"),
        ],
        cwd=runner.ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=120,
    )
    assert parity_result.returncode == 2, parity_result.stdout
    parity = json.loads(parity_report.read_text(encoding="utf-8"))
    assert min(item["layout_fidelity"] for item in parity["viewports"]) < 0.95


def _prepared_workspace(tmp_path: Path, arm: str) -> Path:
    protocol, _protocol_path = runner._protocol(runner.DEFAULT_PROTOCOL)
    output = tmp_path / arm
    output.mkdir()
    return runner._prepare_workspace(output, protocol.tasks[0], arm, 104729)


def test_viewspec_arms_receive_the_shipped_managed_agent_instructions(tmp_path: Path) -> None:
    """The eval must measure the guidance ViewSpec ships, not a copy kept inside the runner."""

    workspace = _prepared_workspace(tmp_path, "viewspec-deep")
    instructions = (workspace / "AGENTS.md").read_text(encoding="utf-8")

    assert agent_instruction_block("codex") in instructions
    assert instructions.index("Arm: viewspec-deep") < instructions.index(BEGIN_MARKER)
    assert "viewspec patch-targets" in instructions
    assert "Lane A — change the value of something that already exists (the default)" in instructions
    assert "Never use a line-based or text-diff editing tool on them" in instructions


def test_code_first_arm_receives_no_viewspec_guidance(tmp_path: Path) -> None:
    workspace = _prepared_workspace(tmp_path, "code-first")
    instructions = (workspace / "AGENTS.md").read_text(encoding="utf-8")

    assert BEGIN_MARKER not in instructions
    assert "patch-targets" not in instructions
    assert (workspace / "submission" / "index.html").is_file()


def test_managed_instruction_identity_is_recorded_for_the_viewspec_arms() -> None:
    fact = runner._managed_instruction_fact()

    assert fact["sha256"] == hashlib.sha256(agent_instruction_block("codex").encode("utf-8")).hexdigest()
    assert fact["applies_to_arms"] == ["viewspec-core", "viewspec-deep"]
    assert fact["path"] == "AGENTS.md"


def test_managed_instructions_do_not_leak_into_measured_source(tmp_path: Path) -> None:
    workspace = _prepared_workspace(tmp_path, "viewspec-core")

    sources = runner._source_files(workspace, "viewspec-core")

    assert [path.name for path in sources] == ["viewspec.app.json"]
