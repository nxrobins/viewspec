from __future__ import annotations

import json
from pathlib import Path
from copy import deepcopy

import pytest

from viewspec.agent_eval import summarize_agent_eval_session
from viewspec.app_starters import starter_react_app_bundle
from viewspec.app_validation import validate_app_text
from viewspec.agent_eval_value import (
    STABLE_HOOKS,
    apply_value_trial,
    checkpoint_envelope,
    load_mutation_manifest,
    seeded_arm_order,
    seeded_trial_order,
    source_snapshot_hash,
    validate_checkpoint,
    validate_mutation_manifest,
    validate_stable_hooks,
)


MANIFEST = Path("conformance/agent-ui-v2/mutations/field-dispatch-lifecycle.json")


def _code_sources() -> dict[str, str]:
    hooks = "".join(f'<div data-eval-id="{hook}"></div>' for hook in STABLE_HOOKS)
    return {
        "submission/index.html": f"<body>{hooks}</body>",
        "submission/react/src/App.jsx": f"export default()=> <main>{hooks}</main>",
    }


def _app_sources() -> dict[str, str]:
    actions = [
        {"id": "show_escalation_guide"},
        {"id": "record_review"},
        {"id": "pause_intake"},
    ]
    payload = {
        "screens": [{"intent_bundle": {"view_spec": {"actions": actions}}}],
        "resources": [
            {
                "id": "jobs",
                "records": [
                    {"id": "J-205", "title": "Replace north terminal refrigeration sensor"},
                    {"id": "J-207", "title": "Generator test"},
                ],
            }
        ],
        "mutations": [
            {
                "id": "reveal_escalation_guide",
                "trigger": {"action_id": "show_escalation_guide"},
                "ops": [{"op": "set", "state": "guide_visible", "value": True}],
            },
            {
                "id": "increment_reviewed_count",
                "trigger": {"action_id": "record_review"},
                "ops": [{"op": "increment", "state": "reviewed_count", "amount": 1}],
            },
        ],
        "visibility": [
            {"id": "show_escalation_panel", "when": {"state": "guide_visible", "is": "truthy"}},
            {"id": "show_review_count", "when": {"state": "reviewed_count", "is": "truthy"}},
            {"id": "show_intake_paused", "when": {"state": "paused", "is": "truthy"}},
        ],
    }
    return {"viewspec.app.json": json.dumps(payload)}


def _valid_app_sources() -> dict[str, str]:
    payload = starter_react_app_bundle()
    actions = payload["screens"][0]["intent_bundle"]["view_spec"]["actions"]
    template = actions[0]
    for identity, label in (
        ("show_escalation_guide", "Show escalation guide"),
        ("record_review", "Record review"),
        ("pause_intake", "Pause intake"),
    ):
        action = deepcopy(template)
        action.update(id=identity, label=label)
        actions.append(action)
    payload["resources"][0]["records"].extend(
        [
            {
                "id": "J-205",
                "title": "Replace north terminal refrigeration sensor before evening medicine delivery window",
            },
            {"id": "J-207", "title": "Generator test"},
        ]
    )
    payload["state"].extend(
        [
            {"id": "guide_visible", "kind": "scalar", "scope": "app", "initial": {"value": False}},
            {"id": "reviewed_count", "kind": "scalar", "scope": "app", "initial": {"value": 0}},
            {"id": "intake_paused", "kind": "scalar", "scope": "app", "initial": {"value": False}},
        ]
    )
    payload["mutations"].extend(
        [
            {
                "id": "reveal_escalation_guide",
                "trigger": {"screen_id": "queue", "action_id": "show_escalation_guide"},
                "ops": [{"op": "set", "state": "guide_visible", "value": True}],
            },
            {
                "id": "increment_reviewed_count",
                "trigger": {"screen_id": "queue", "action_id": "record_review"},
                "ops": [{"op": "increment", "state": "reviewed_count", "amount": 1}],
            },
            {
                "id": "pause_intake_state",
                "trigger": {"screen_id": "queue", "action_id": "pause_intake"},
                "ops": [{"op": "set", "state": "intake_paused", "value": True}],
            },
        ]
    )
    payload["visibility"].extend(
        [
            {
                "id": "show_escalation_panel",
                "screen_id": "queue",
                "target_ref": "binding:inc_1042_id",
                "when": {"state": "guide_visible", "is": "truthy"},
            },
            {
                "id": "show_review_count",
                "screen_id": "queue",
                "target_ref": "binding:inc_1042_status",
                "when": {"state": "reviewed_count", "is": "truthy"},
            },
            {
                "id": "show_intake_paused",
                "screen_id": "queue",
                "target_ref": "binding:inc_1042_severity",
                "when": {"state": "intake_paused", "is": "truthy"},
            },
        ]
    )
    payload["state_replay_assertions"].extend(
        [
            {
                "id": "guide_replay",
                "events": [{"mutation_id": "reveal_escalation_guide"}],
                "expect_state": {"guide_visible": True},
                "expect_selectors": {},
                "expect_visibility": {"show_escalation_panel": True},
            },
            {
                "id": "review_replay",
                "events": [
                    {"mutation_id": "increment_reviewed_count"},
                    {"mutation_id": "increment_reviewed_count"},
                ],
                "expect_state": {"reviewed_count": 2},
                "expect_selectors": {},
                "expect_visibility": {"show_review_count": True},
            },
            {
                "id": "pause_replay",
                "events": [{"mutation_id": "pause_intake_state"}],
                "expect_state": {"intake_paused": True},
                "expect_selectors": {},
                "expect_visibility": {"show_intake_paused": True},
            },
        ]
    )
    return {"viewspec.app.json": json.dumps(payload)}


def test_checked_in_mutation_manifest_is_hash_bound_and_complete():
    manifest = load_mutation_manifest(MANIFEST)

    assert len(manifest["mutations"]) == 5
    assert len(manifest["controls"]) == 2
    assert len(manifest["manifest_sha256"]) == 64

    manifest["mutations"][0]["class"] = "text-layout"
    with pytest.raises(ValueError):
        validate_mutation_manifest(manifest)


def test_seeded_orders_are_stable_and_isolated():
    trials = ["a", "b", "c", "d", "e", "f", "g"]
    assert seeded_trial_order(104729, trials) == seeded_trial_order(104729, trials)
    assert sorted(seeded_trial_order(104729, trials)) == sorted(trials)
    assert seeded_arm_order(104729, ("code-first", "viewspec-core", "viewspec-deep")) == [
        "viewspec-deep",
        "code-first",
        "viewspec-core",
    ]


@pytest.mark.parametrize("arm,sources", [("code-first", _code_sources()), ("viewspec-core", _app_sources())])
def test_all_mutations_change_exactly_one_source_and_controls_are_identity(arm, sources):
    manifest = load_mutation_manifest(MANIFEST)
    baseline = source_snapshot_hash(sources)

    for trial in manifest["mutations"]:
        mutated, fact = apply_value_trial(arm=arm, trial_id=trial["id"], sources=sources)
        assert fact["changed_file_count"] == 1
        assert fact["baseline_sha256"] == baseline
        assert fact["mutated_sha256"] != baseline
        assert source_snapshot_hash(sources) == baseline
        assert source_snapshot_hash(mutated) == fact["mutated_sha256"]
        if arm == "code-first" and trial["id"] == "corrupt-reviewed-count":
            assert 'p.textContent="Review count: 2"' in mutated["submission/index.html"]
    for trial in manifest["controls"]:
        unchanged, fact = apply_value_trial(arm=arm, trial_id=trial["id"], sources=sources)
        assert unchanged == sources
        assert fact["changed_file_count"] == 0
        assert fact["mutated_sha256"] == baseline


def test_stable_hook_validation_is_target_specific_and_rejects_missing_or_duplicate_hooks():
    code = _code_sources()
    assert validate_stable_hooks("code-first", code)["ok"] is True
    missing = dict(code)
    missing["submission/index.html"] = missing["submission/index.html"].replace(
        'data-eval-id="job-j205-title"',
        'data-eval-id="missing-job-title"',
    )
    missing_report = validate_stable_hooks("code-first", missing)
    assert missing_report["ok"] is False
    assert "static:job-j205-title" in missing_report["errors"][0]

    code["submission/react/src/App.jsx"] += '<i data-eval-id="job-j207"></i>'
    report = validate_stable_hooks("code-first", code)
    assert report["ok"] is False
    assert "react:job-j207" in report["errors"][0]

    app = _app_sources()
    assert validate_stable_hooks("viewspec-deep", app)["ok"] is True
    wrong_action = json.loads(app["viewspec.app.json"])
    wrong_action["screens"][0]["intent_bundle"]["view_spec"]["actions"][0]["id"] = (
        "reveal_escalation_guide"
    )
    wrong_report = validate_stable_hooks(
        "viewspec-core",
        {"viewspec.app.json": json.dumps(wrong_action)},
    )
    assert wrong_report["ok"] is False
    assert "expected action id 'show_escalation_guide' exactly once" in wrong_report["errors"][0]
    assert "reveal_escalation_guide" in wrong_report["discovered"]["action_ids"]


def test_appbundle_mutations_trigger_preregistered_native_boundaries():
    sources = _valid_app_sources()
    assert validate_app_text(sources["viewspec.app.json"], compile_check=True)["ok"] is True

    for trial_id in ("break-escalation-action", "duplicate-j207-resource"):
        mutated, _fact = apply_value_trial(
            arm="viewspec-deep",
            trial_id=trial_id,
            sources=sources,
        )
        assert validate_app_text(mutated["viewspec.app.json"], compile_check=True)["ok"] is False
    numeric, _fact = apply_value_trial(
        arm="viewspec-deep",
        trial_id="corrupt-reviewed-count",
        sources=sources,
    )
    numeric_payload = json.loads(numeric["viewspec.app.json"])
    increment = next(
        item for item in numeric_payload["mutations"] if item["id"] == "increment_reviewed_count"
    )
    assert increment["ops"][0]["amount"] == 2
    assert validate_app_text(numeric["viewspec.app.json"], compile_check=True)["ok"] is True
    visibility, _fact = apply_value_trial(
        arm="viewspec-deep",
        trial_id="break-escalation-visibility",
        sources=sources,
    )
    visibility_payload = json.loads(visibility["viewspec.app.json"])
    guide = next(
        item for item in visibility_payload["visibility"] if item["id"] == "show_escalation_panel"
    )
    assert guide["when"]["is"] == "falsy"
    assert validate_app_text(visibility["viewspec.app.json"], compile_check=True)["ok"] is True
    layout_mutated, _fact = apply_value_trial(
        arm="viewspec-deep",
        trial_id="break-j205-mobile-geometry",
        sources=sources,
    )
    assert validate_app_text(layout_mutated["viewspec.app.json"], compile_check=True)["ok"] is True


def test_checkpoint_rejects_hash_model_and_source_drift():
    checkpoint = checkpoint_envelope(
        {
            "protocol_sha256": "a" * 64,
            "model": "gpt-test",
            "source_sha256": "b" * 64,
            "product_tree_sha256": "e" * 64,
        }
    )
    assert validate_checkpoint(
        checkpoint,
        protocol_sha256="a" * 64,
        model="gpt-test",
        source_sha256="b" * 64,
        product_tree_sha256="e" * 64,
    )["checkpoint_sha256"]
    for key, value in (
        ("protocol_sha256", "c" * 64),
        ("model", "other"),
        ("source_sha256", "d" * 64),
        ("product_tree_sha256", "f" * 64),
    ):
        arguments = {
            "protocol_sha256": "a" * 64,
            "model": "gpt-test",
            "source_sha256": "b" * 64,
            "product_tree_sha256": "e" * 64,
        }
        arguments[key] = value
        with pytest.raises(ValueError, match=key):
            validate_checkpoint(checkpoint, **arguments)


def test_repair_costs_are_in_total_and_post_establishment_premiums():
    usage = {
        "input_tokens": 100,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 20,
        "reasoning_output_tokens": 0,
    }
    session = {
        "schema_version": 1,
        "arm_id": "viewspec-deep",
        "turns": [
            {
                "step_id": "establish",
                "phase": "establishment",
                "usage": usage,
                "wall_time_ms": 100,
                "deterministic_ms": 10,
                "score": {"ok": True, "passed": 1, "total": 1},
            }
        ],
        "value_evidence": {
            "repair_turns": [
                {
                    "usage": {**usage, "input_tokens": 50, "output_tokens": 10},
                    "wall_time_ms": 70,
                    "deterministic_ms": 20,
                }
            ],
            "deterministic_overhead_ms": 35,
        },
    }

    summary = summarize_agent_eval_session(session)

    assert summary["tokens"]["total_tokens"] == 180
    assert summary["tokens"]["repair_tokens"] == 60
    assert summary["tokens"]["evolution_tokens"] == 60
    assert summary["timing"]["model_wall_ms"] == 170
    assert summary["timing"]["deterministic_ms"] == 45
    assert summary["timing"]["evolution_total_wall_ms"] == 105
