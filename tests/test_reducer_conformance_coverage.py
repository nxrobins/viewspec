"""C-13: reducer conformance never reports a pass for what it did not run.

Conformance always replays the initial state with zero events, and compares the state, every
selector, every visibility verdict and every state text between the Python reference and the
generated reducer under Node. Its status is ``failed``, ``passed`` (a replay assertion with at least
one event passed), or ``initial_state_only`` (no event was replayed). ``viewspec check`` and plain
``viewspec prove`` run the author's replay assertions through the Python reference, which needs no
Node.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import viewspec.state_ir as state_ir_module
from viewspec.app_bundle import AppBundle, AppBundleError, compile_app, prove_app, validate_app_text
from viewspec.app_starters import starter_react_app_bundle
from viewspec.app_state_artifacts import _state_conformance_status
from viewspec.cli import main as cli_main
from viewspec.state_ir import check_reducer_conformance

NODE = shutil.which("node")
NODE_REASON = "Node.js is required for reducer conformance"
INITIAL_STATE_ONLY = "initial_state_only"


def _write(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path


def _codes(entries: list[dict]) -> list[str]:
    return [entry["code"] for entry in entries]


def _wrong_expectation_bundle() -> dict:
    """The starter app, with its replay assertion expecting a state value that it never reaches."""
    bundle = starter_react_app_bundle()
    assertion = bundle["state_replay_assertions"][0]
    assertion["expect_state"][next(iter(assertion["expect_state"]))] = "WRONG"
    return bundle


def _initial_divergence_bundle() -> dict:
    """No replay assertion, and the two implementations already disagree at the initial state.

    ``filter_eq`` with ``value: null`` keeps a record that lacks the field in the Python reference and
    drops it in the generated reducer (audit finding C-16), so the page is baked visible from the Python
    answer and the generated reducer hides it before any event.
    """
    bundle = starter_react_app_bundle()
    bundle.update(
        state=[
            {
                "id": "tickets",
                "kind": "collection",
                "scope": "app",
                "initial": {"value": [{"id": "b"}, {"id": "c", "owner": "kim"}]},
            }
        ],
        mutations=[],
        state_replay_assertions=[],
        selectors=[
            {
                "id": "unassigned",
                "source_state": "tickets",
                "ops": [{"op": "filter_eq", "field": "owner", "value": None}],
            }
        ],
        visibility=[
            {
                "id": "v",
                "screen_id": "queue",
                "target_ref": "binding:inc_1043_status",
                "when": {"selector": "unassigned", "is": "non_empty"},
            }
        ],
    )
    return bundle


def _no_assertion_bundle() -> dict:
    """The starter app without its replay assertion: nothing diverges and no event is replayed."""
    bundle = starter_react_app_bundle()
    bundle["state_replay_assertions"] = []
    return bundle


def _shell_state_summary(site: Path) -> dict:
    return json.loads((site / "shell_manifest.json").read_text(encoding="utf-8"))["state_ir"]


def _shell_status(site: Path) -> str:
    return _shell_state_summary(site)["reducer_conformance"]


# check and plain prove run the author's replay assertions


def test_check_exits_2_on_a_wrong_replay_expectation(tmp_path, capsys):
    path = _write(tmp_path, "wrong.app.json", _wrong_expectation_bundle())

    assert cli_main(["check", str(path)]) == 2
    out = capsys.readouterr().out
    assert "APP_STATE_REPLAY_STATE_MISMATCH" in out
    assert "'WRONG'" in out

    assert cli_main(["check", str(path), "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    mismatch = next(issue for issue in result["issues"] if issue["code"] == "APP_STATE_REPLAY_STATE_MISMATCH")
    assert mismatch["path"] == "$.state_replay_assertions.triage_replay.expect_state.incidents_state"
    assert "assertion=triage_replay" in mismatch["message"]
    assert "result=state" in mismatch["message"]


def test_plain_prove_exits_2_on_a_wrong_replay_expectation(tmp_path, capsys):
    path = _write(tmp_path, "wrong.app.json", _wrong_expectation_bundle())

    assert cli_main(["prove", str(path), "--out", str(tmp_path / "p1")]) == 2
    assert "APP_STATE_REPLAY_STATE_MISMATCH" in capsys.readouterr().out

    report = prove_app(app_path=path, out_dir=tmp_path / "p2", cwd=tmp_path)
    assert report["ok"] is False
    assert report["errors"][0]["code"] == "APP_STATE_REPLAY_STATE_MISMATCH"


def test_the_check_replay_needs_no_node(monkeypatch):
    def no_subprocess(*_args, **_kwargs):
        raise AssertionError("check must not start a process")

    monkeypatch.setattr(subprocess, "run", no_subprocess)

    wrong = validate_app_text(json.dumps(_wrong_expectation_bundle()))
    assert wrong["ok"] is False
    assert "APP_STATE_REPLAY_STATE_MISMATCH" in _codes(wrong["issues"])
    assert validate_app_text(json.dumps(starter_react_app_bundle()))["ok"] is True


def test_check_reports_every_failed_replay_expectation():
    bundle = _wrong_expectation_bundle()
    bundle["state_replay_assertions"][0]["expect_selectors"]["active_incidents"] = []

    result = validate_app_text(json.dumps(bundle), compile_check=False)

    assert result["ok"] is False
    assert _codes(result["issues"]) == ["APP_STATE_REPLAY_STATE_MISMATCH", "APP_STATE_REPLAY_SELECTOR_MISMATCH"]


def test_appbundle_load_rejects_a_wrong_replay_expectation(tmp_path):
    path = _write(tmp_path, "wrong.app.json", _wrong_expectation_bundle())

    with pytest.raises(AppBundleError) as caught:
        AppBundle.load(path)

    assert "APP_STATE_REPLAY_STATE_MISMATCH" in _codes(caught.value.issues)


# conformance always replays the initial state


@pytest.mark.skipif(NODE is None, reason=NODE_REASON)
def test_compile_fails_when_the_initial_state_diverges(tmp_path, capsys):
    path = _write(tmp_path, "norep.app.json", _initial_divergence_bundle())

    # Python alone cannot see a difference between the two implementations.
    assert validate_app_text(path.read_text(encoding="utf-8"))["ok"] is True

    compiled = compile_app(path, out_dir=tmp_path / "site", cwd=tmp_path)

    assert compiled["ok"] is False
    error = compiled["errors"][0]
    assert error["code"] == "APP_STATE_REDUCER_CONFORMANCE_FAILED"
    assert (
        'selector unassigned is [{"id":"b"}] in the Python reference and [] in the generated reducer'
        in error["message"]
    )
    assert not (tmp_path / "site" / "shell_manifest.json").exists()

    assert cli_main(["compile", str(path), "--out", str(tmp_path / "site-cli")]) == 2
    assert "APP_STATE_REDUCER_CONFORMANCE_FAILED" in capsys.readouterr().out


@pytest.mark.skipif(NODE is None, reason=NODE_REASON)
def test_conformance_names_each_value_that_diverges_at_the_initial_state():
    report = check_reducer_conformance(_initial_divergence_bundle())

    assert report["ok"] is False
    assert report["status"] == "failed"
    assert report["assertion_count"] == 0
    assert report["initial_replay"] == {
        "status": "failed",
        "event_count": 0,
        "state_matches": True,
        "selector_matches": False,
        "visibility_matches": False,
    }
    assert _codes(report["errors"]) == ["APP_STATE_REDUCER_CONFORMANCE_FAILED"] * 2
    assert [error["path"] for error in report["errors"]] == ["$.selectors.unassigned", "$.visibility.v"]
    assert report["errors"][0]["message"] == (
        "Generated reducer diverged from the Python reference at the initial state (zero events): "
        'selector unassigned is [{"id":"b"}] in the Python reference and [] in the generated reducer.'
    )
    assert (
        "visibility rule v is true in the Python reference and false in the generated reducer"
        in (report["errors"][1]["message"])
    )


@pytest.mark.skipif(NODE is None, reason=NODE_REASON)
def test_a_generated_initial_state_that_differs_fails_conformance(monkeypatch):
    original = state_ir_module.generate_javascript_reducer

    def tampered(payload: dict) -> str:
        source = original(payload)
        assert '"selected_incident": null' in source
        return source.replace('"selected_incident": null', '"selected_incident": "inc_1042"', 1)

    monkeypatch.setattr(state_ir_module, "generate_javascript_reducer", tampered)

    report = check_reducer_conformance(_no_assertion_bundle())

    assert report["ok"] is False
    assert report["status"] == "failed"
    assert report["initial_replay"]["state_matches"] is False
    assert report["initial_replay"]["visibility_matches"] is False
    assert report["errors"][0]["path"] == "$.state.selected_incident"
    assert (
        'state selected_incident is null in the Python reference and "inc_1042" in the generated reducer'
        in report["errors"][0]["message"]
    )


@pytest.mark.skipif(NODE is None, reason=NODE_REASON)
def test_a_generated_reducer_that_throws_at_the_initial_state_fails_conformance(monkeypatch):
    original = state_ir_module.generate_javascript_reducer

    def throwing(payload: dict) -> str:
        source = original(payload)
        head = "export function selectViewSpecState(state) {"
        assert head in source
        return source.replace(head, head + '\n  throw new Error("selector boom");', 1)

    monkeypatch.setattr(state_ir_module, "generate_javascript_reducer", throwing)

    report = check_reducer_conformance(_no_assertion_bundle())

    assert report["ok"] is False
    assert report["initial_replay"]["status"] == "failed"
    assert "at the initial state" in report["errors"][0]["message"]
    assert "selector boom" in report["errors"][0]["message"]


# the status distinguishes three cases


@pytest.mark.skipif(NODE is None, reason=NODE_REASON)
def test_no_assertion_and_no_divergence_is_initial_state_only(tmp_path):
    bundle = _no_assertion_bundle()

    report = check_reducer_conformance(bundle)

    assert report["ok"] is True
    assert report["status"] == INITIAL_STATE_ONLY
    assert report["assertion_count"] == 0
    assert report["initial_replay"]["status"] == "passed"

    compiled = compile_app(_write(tmp_path, "app.json", bundle), out_dir=tmp_path / "site", cwd=tmp_path)
    assert compiled["ok"] is True
    assert compiled["state_reducer_conformance"]["status"] == INITIAL_STATE_ONLY
    summary = _shell_state_summary(tmp_path / "site")
    assert summary["reducer_conformance"] == INITIAL_STATE_ONLY
    # replay_ok stays a boolean; the count beside it says that no replay assertion ran.
    assert summary["replay_ok"] is True
    assert summary["replay_assertion_count"] == 0
    state_manifest = json.loads((tmp_path / "site" / "state_manifest.json").read_text(encoding="utf-8"))
    assert state_manifest["reducer_conformance"]["status"] == INITIAL_STATE_ONLY


@pytest.mark.skipif(NODE is None, reason=NODE_REASON)
def test_the_proof_report_summary_and_support_bundle_carry_the_status(tmp_path):
    path = _write(tmp_path, "app.json", _no_assertion_bundle())

    proof = prove_app(app_path=path, out_dir=tmp_path / "proof", cwd=tmp_path, with_shell=True)

    assert proof["ok"] is True
    assert proof["state_reducer_conformance"]["status"] == INITIAL_STATE_ONLY
    summary = (tmp_path / "proof" / "APP_PROOF.md").read_text(encoding="utf-8")
    assert "- State reducer conformance: `initial_state_only`" in summary
    assert "- State replay: `not run: no replay assertions`" in summary
    support = json.loads((tmp_path / "proof" / "app_support_bundle.json").read_text(encoding="utf-8"))
    assert support["shell"]["state_reducer_conformance"] == INITIAL_STATE_ONLY


@pytest.mark.skipif(NODE is None, reason=NODE_REASON)
def test_a_replay_assertion_without_events_is_initial_state_only():
    bundle = starter_react_app_bundle()
    bundle["state_replay_assertions"] = [
        {
            "id": "at_rest",
            "events": [],
            "expect_state": {"selected_incident": None},
            "expect_selectors": {},
            "expect_visibility": {"show_triaged_status": False},
        }
    ]

    report = check_reducer_conformance(bundle)

    assert report["ok"] is True
    assert report["assertion_count"] == 1
    assert report["status"] == INITIAL_STATE_ONLY


@pytest.mark.skipif(NODE is None, reason=NODE_REASON)
def test_a_passing_replay_with_events_is_passed(tmp_path):
    bundle = starter_react_app_bundle()

    report = check_reducer_conformance(bundle)

    assert report["ok"] is True
    assert report["status"] == "passed"
    assert report["initial_replay"]["status"] == "passed"
    assert report["replays"][0]["event_count"] == 1

    compiled = compile_app(_write(tmp_path, "app.json", bundle), out_dir=tmp_path / "site", cwd=tmp_path)
    assert compiled["ok"] is True
    summary = _shell_state_summary(tmp_path / "site")
    assert summary["reducer_conformance"] == "passed"
    assert summary["replay_ok"] is True
    assert summary["replay_assertion_count"] == 1


@pytest.mark.parametrize(
    ("report", "status"),
    [
        ({"ok": False, "replays": [{"status": "passed", "event_count": 1}]}, "failed"),
        (
            {"ok": True, "replays": [{"status": "passed", "event_count": 0}, {"status": "passed", "event_count": 2}]},
            "passed",
        ),
        ({"ok": True, "replays": [{"status": "passed", "event_count": 0}]}, INITIAL_STATE_ONLY),
        ({"ok": True, "replays": []}, INITIAL_STATE_ONLY),
        ({"ok": True}, INITIAL_STATE_ONLY),
        (None, None),
    ],
)
def test_the_status_distinguishes_three_cases(report, status):
    assert _state_conformance_status(report) == status


# without Node, conformance fails closed


def test_without_node_compile_fails_closed_and_check_still_replays(tmp_path, monkeypatch):
    no_node = tmp_path / "no-node"
    no_node.mkdir()
    monkeypatch.setenv("PATH", str(no_node))
    path = _write(tmp_path, "app.json", starter_react_app_bundle())

    assert validate_app_text(path.read_text(encoding="utf-8"))["ok"] is True
    wrong = validate_app_text(json.dumps(_wrong_expectation_bundle()))
    assert "APP_STATE_REPLAY_STATE_MISMATCH" in _codes(wrong["issues"])

    compiled = compile_app(path, out_dir=tmp_path / "site", cwd=tmp_path)

    assert compiled["ok"] is False
    assert compiled["errors"][0]["code"] == "APP_STATE_REDUCER_NODE_UNAVAILABLE"
