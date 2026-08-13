from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from viewspec.agent_eval import (
    AGENT_UI_EVAL_ARMS,
    AgentEvalProtocol,
    load_agent_eval_protocol,
    parse_codex_jsonl,
    summarize_agent_eval_session,
    summarize_agent_eval_shakedown_exit,
    summarize_agent_eval_study,
)


PROTOCOL_PATH = Path("conformance/agent-ui-v1/protocol.json")
VALUE_PROTOCOL_PATH = Path("conformance/agent-ui-v2/protocol.json")


def _usage(input_tokens: int = 100, output_tokens: int = 20) -> dict[str, int]:
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": 60,
        "cache_write_input_tokens": 0,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": 5,
    }


def _target(target_id: str, *, parity: float = 1.0, passed: bool = True) -> dict:
    return {
        "id": target_id,
        "applicable": True,
        "build": {"ok": True},
        "functional_acceptance": 1.0 if passed else 0.9,
        "layout_fidelity": 0.95,
        "passed": passed,
        "parity": parity,
        "parity_by_viewport": {"390": parity, "768": parity, "1440": parity},
        "score_artifact": f"fixture/{target_id}/browser-score.json",
    }


def _artifact_integrity(reference_count: int = 1) -> dict:
    return {
        "checked": True,
        "complete": True,
        "declared_reference_count": reference_count,
        "missing": [],
        "errors": [],
    }


def _session(
    arm: str,
    *,
    token_scale: int = 1,
    scores: tuple[tuple[int, int], ...] = ((8, 10), (10, 10)),
    deterministic_ms: int = 100,
) -> dict:
    return {
        "schema_version": 1,
        "protocol_id": "test",
        "task_id": "test-task",
        "arm_id": arm,
        "seed": 1,
        "turns": [
            {
                "step_id": f"step-{index}",
                "usage": _usage(100 * token_scale, 20 * token_scale),
                "wall_time_ms": 1_000 * token_scale,
                "deterministic_ms": deterministic_ms,
                "score": {"ok": passed == total, "passed": passed, "total": total},
            }
            for index, (passed, total) in enumerate(scores)
        ],
    }


def test_checked_in_agent_ui_protocol_is_pre_registered_and_held_out():
    protocol = load_agent_eval_protocol(PROTOCOL_PATH)

    assert protocol.id == "viewspec-agent-ui-pilot-v1"
    assert protocol.arms == AGENT_UI_EVAL_ARMS
    assert protocol.seeds == (104729,)
    assert [task.id for task in protocol.tasks] == ["field-dispatch"]
    task = protocol.tasks[0]
    assert len(task.steps) == 5
    assert task.steps[-1].click_button == "Show escalation guide"
    assert task.steps[-1].click_reveals == "Escalation owner: Maya Chen"
    assert PROTOCOL_PATH.parent.joinpath(task.reference).is_file()


def test_protocol_rejects_arm_order_drift():
    payload = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    payload["arms"] = list(reversed(payload["arms"]))

    with pytest.raises(ValueError, match="arms must be exactly"):
        AgentEvalProtocol.from_json(payload)


def test_checked_in_pilot_result_is_bound_to_the_protocol_and_scoped_as_pilot():
    result = json.loads(
        Path("conformance/agent-ui-v1/pilot-result-2026-07-22.json").read_text(encoding="utf-8")
    )

    assert result["schema_version"] == 1
    assert result["status"] == "pilot_only"
    assert result["sample_size_met"] is False
    assert result["evidence"]["protocol_sha256"] == hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest()
    assert result["arms"]["viewspec-core"]["native_proofs_passed"] == "5/5"
    assert result["arms"]["viewspec-deep"]["native_proofs_passed"] == "0/5"
    assert result["candidate_gate_results"]["deep_mutation_detection"] is None


def test_codex_jsonl_parser_ignores_warnings_and_accounts_for_cache():
    payload = "\n".join(
        [
            "a diagnostic that is not JSON",
            '{"type":"thread.started","thread_id":"thread-1"}',
            '{"type":"turn.started"}',
            (
                '{"type":"item.completed","item":{"id":"cmd-1","type":"command_execution",'
                '"command":"sed -n 1,20p /tmp/sites-building/SKILL.md","aggregated_output":"ok\\n",'
                '"exit_code":0,"status":"completed"}}'
            ),
            (
                '{"type":"item.completed","item":{"id":"edit-1","type":"file_change",'
                '"changes":[{"path":"/tmp/workspace/submission/index.html","kind":"update"}],'
                '"status":"completed"}}'
            ),
            '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}',
            (
                '{"type":"turn.completed","usage":{"input_tokens":120,"cached_input_tokens":80,'
                '"cache_write_input_tokens":3,"output_tokens":20,"reasoning_output_tokens":4}}'
            ),
        ]
    )

    parsed = parse_codex_jsonl(payload)

    assert parsed == {
        "thread_id": "thread-1",
        "agent_message": "done",
        "usage": {
            "input_tokens": 120,
            "cached_input_tokens": 80,
            "cache_write_input_tokens": 3,
            "output_tokens": 20,
            "reasoning_output_tokens": 4,
        },
        "completed": True,
        "event_count": 6,
        "telemetry": {
            "event_types": {"item.completed": 3, "thread.started": 1, "turn.completed": 1, "turn.started": 1},
            "item_types": {"agent_message": 1, "command_execution": 1, "file_change": 1},
            "command_count": 1,
            "command_failure_count": 0,
            "command_output_bytes": 3,
            "commands": [
                {
                    "id": "cmd-1",
                    "status": "completed",
                    "exit_code": 0,
                    "command_sha256": hashlib.sha256(
                        b"sed -n 1,20p /tmp/sites-building/SKILL.md"
                    ).hexdigest(),
                    "command_bytes": 41,
                    "output_bytes": 3,
                }
            ],
            "file_change_count": 1,
            "file_changes": [{"path_tail": "workspace/submission/index.html", "kind": "update"}],
            "skill_reads": ["sites-building"],
        },
    }


def test_protocol_accepts_explicit_text_geometry_contract_without_changing_archived_protocol():
    payload = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    payload["tasks"][0]["steps"][0]["acceptance"]["text_geometry"] = [
        {
            "text": "Stormwater pump failure",
            "identity": "incident-title",
            "resource": {"record_id": "INC-1042", "field": "title"},
            "viewport_width": 390,
            "minimum_lines": 2,
            "maximum_lines": 5,
            "minimum_width_px": 180,
            "no_word_fragmentation": True,
            "no_clip": True,
        }
    ]

    protocol = AgentEvalProtocol.from_json(payload)
    geometry = protocol.tasks[0].steps[0].to_score_spec()["text_geometry"]

    assert geometry == [
        {
            "text": "Stormwater pump failure",
            "identity": "incident-title",
            "resource": {"record_id": "INC-1042", "field": "title"},
            "viewport_width": 390,
            "minimum_lines": 2,
            "maximum_lines": 5,
            "minimum_width_px": 180,
            "no_word_fragmentation": True,
            "no_clip": True,
        }
    ]


def test_value_premium_protocol_is_separate_long_horizon_and_executable():
    protocol = load_agent_eval_protocol(VALUE_PROTOCOL_PATH)

    assert protocol.schema_version == 2
    assert protocol.id == "viewspec-agent-ui-value-premium-v2.3"
    assert protocol.qualification_max_turns == 2
    assert protocol.qualification["trigger"] == "post-lifecycle-ineligible-or-layout-miss"
    assert protocol.evaluation_mode == "value_premium"
    assert protocol.primary_arm == "viewspec-deep"
    assert protocol.seeds == (104729, 130363, 155921)
    assert protocol.minimum_sessions_per_arm == 18
    assert "minimum_token_reduction" not in protocol.success_criteria
    assert protocol.success_criteria["maximum_total_token_premium"] == 3.0
    assert protocol.success_criteria["minimum_mutation_detection_rate"] == 0.9
    task = protocol.task("field-dispatch-lifecycle")
    assert task.primary_heading == "Field Dispatch"
    assert len(task.steps) == 10
    assert task.steps[0].phase == "establishment"
    assert task.steps[-1].phase == "repair"
    assert task.steps[6].to_score_spec()["interactions"] == [
        {"button": "Show escalation guide", "reveals": "Escalation owner: Maya Chen"},
        {"button": "Record review", "reveals": "Review count: 1"},
        {"button": "Pause intake", "reveals": "Dispatch intake paused"},
    ]
    assert VALUE_PROTOCOL_PATH.parent.joinpath(task.reference).is_file()


def test_v2_qualification_contract_is_bounded_and_exact():
    payload = json.loads(VALUE_PROTOCOL_PATH.read_text(encoding="utf-8"))
    payload["qualification"]["max_turns_per_arm"] = 0
    with pytest.raises(ValueError, match="max_turns_per_arm"):
        AgentEvalProtocol.from_json(payload)

    payload = json.loads(VALUE_PROTOCOL_PATH.read_text(encoding="utf-8"))
    payload["qualification"]["feedback"] = "full-history"
    with pytest.raises(ValueError, match="qualification policy"):
        AgentEvalProtocol.from_json(payload)


def test_value_protocol_rejects_click_and_interactions_in_the_same_step():
    payload = json.loads(VALUE_PROTOCOL_PATH.read_text(encoding="utf-8"))
    payload["tasks"][0]["steps"][0]["acceptance"]["click"] = {
        "button": "One",
        "reveals": "First",
    }
    payload["tasks"][0]["steps"][0]["acceptance"]["interactions"] = [
        {"button": "Two", "reveals": "Second"}
    ]

    with pytest.raises(ValueError, match="both click and interactions"):
        AgentEvalProtocol.from_json(payload)


def test_session_summary_separates_model_and_deterministic_costs():
    summary = summarize_agent_eval_session(
        _session("viewspec-deep", scores=((9, 10), (8, 10), (10, 10)), deterministic_ms=250)
    )

    assert summary["accepted_turn_count"] == 1
    assert summary["acceptance_rate"] == pytest.approx(1 / 3)
    assert summary["final_acceptance"] == 1.0
    assert summary["regression_count"] == 0
    assert summary["tokens"]["input_tokens"] == 300
    assert summary["tokens"]["uncached_input_tokens"] == 120
    assert summary["tokens"]["total_tokens"] == 360
    assert summary["tokens"]["iteration_tokens"] == 240
    assert summary["timing"]["model_wall_ms"] == 3_000
    assert summary["timing"]["deterministic_ms"] == 750
    assert summary["timing"]["total_wall_ms"] == 3_750
    assert summary["timing"]["proof_overhead_ratio"] == 0.2
    assert summary["timing"]["iteration_total_wall_ms"] == 2_500


def test_session_summary_records_lifecycle_and_value_evidence():
    session = _session("viewspec-deep")
    session["turns"][0]["phase"] = "establishment"
    session["turns"][1]["phase"] = "evolution"
    session["value_evidence"] = {
        "artifact_integrity": _artifact_integrity(),
        "mutation_trials": [
            {
                "id": "state",
                "applicable": True,
                "detected": True,
                "repaired": True,
            }
        ],
        "negative_control_trials": [
            {"id": "control", "applicable": True, "detected": False}
        ],
        "target_trials": [
            _target("static", parity=1.0),
            _target("react", parity=0.98),
        ],
    }

    summary = summarize_agent_eval_session(session)

    assert summary["tokens"]["evolution_tokens"] == 120
    assert summary["timing"]["evolution_total_wall_ms"] == 1_100
    assert summary["lifecycle_activity"]["establishment"]["turn_count"] == 1
    assert summary["lifecycle_activity"]["evolution"]["turn_count"] == 1
    assert summary["value_evidence"]["mutation"]["detection_rate"] == 1.0
    assert summary["value_evidence"]["mutation"]["repair_rate"] == 1.0
    assert summary["value_evidence"]["mutation"]["false_positive_rate"] == 0.0
    assert summary["value_evidence"]["cross_target"]["pass_rate"] == 1.0
    assert summary["value_evidence"]["cross_target"]["minimum_parity"] == 0.98


def test_missing_react_parity_is_incomplete_instead_of_inheriting_static_parity():
    session = _session("viewspec-deep")
    react = _target("native-react", parity=0.98, passed=False)
    react.update(
        build={"ok": False},
        functional_acceptance=0.0,
        layout_fidelity=None,
        parity=None,
        parity_by_viewport={},
    )
    session["value_evidence"] = {
        "artifact_integrity": _artifact_integrity(2),
        "target_trials": [_target("static-shell"), react],
    }

    summary = summarize_agent_eval_session(session)

    assert summary["value_evidence"]["structural_evidence_complete"] is True
    assert summary["value_evidence"]["cross_target"]["evidence_complete"] is False
    assert summary["value_evidence"]["cross_target"]["complete_count"] == 1
    assert summary["value_evidence"]["cross_target"]["minimum_parity"] is None
    assert summary["value_evidence"]["evidence_complete"] is False


def test_inapplicable_controls_do_not_create_a_false_positive_pass():
    session = _session("viewspec-deep")
    session["value_evidence"] = {
        "negative_control_trials": [
            {
                "id": f"control-{index}",
                "order": index,
                "applicable": False,
                "baseline_sha256": "a" * 64,
                "invalid_reason": "baseline_ineligible",
            }
            for index in range(2)
        ]
    }

    summary = summarize_agent_eval_session(session)

    assert summary["value_evidence"]["mutation"]["negative_control_count"] == 2
    assert summary["value_evidence"]["mutation"]["applicable_negative_control_count"] == 0
    assert summary["value_evidence"]["mutation"]["false_positive_rate"] is None


def test_qualification_turns_count_toward_final_quality_and_post_establishment_cost():
    session = _session("viewspec-deep", scores=((8, 10), (9, 10)))
    session["turns"][0]["phase"] = "establishment"
    session["turns"][1]["phase"] = "evolution"
    session["qualification_turns"] = [
        {
            "step_id": "baseline-qualification-1",
            "phase": "qualification",
            "usage": _usage(50, 10),
            "wall_time_ms": 700,
            "deterministic_ms": 80,
            "score": {"ok": True, "passed": 10, "total": 10},
        }
    ]

    summary = summarize_agent_eval_session(session)

    assert summary["turn_count"] == 3
    assert summary["lifecycle_turn_count"] == 2
    assert summary["qualification_turn_count"] == 1
    assert summary["final_acceptance"] == 1.0
    assert summary["tokens"]["total_tokens"] == 300
    assert summary["tokens"]["qualification_tokens"] == 60
    assert summary["tokens"]["evolution_tokens"] == 180
    assert summary["timing"]["qualification_model_wall_ms"] == 700
    assert summary["timing"]["qualification_deterministic_ms"] == 80


def test_session_summary_reports_the_monotonically_selected_qualification_turn():
    session = _session("viewspec-deep", scores=((8, 10), (10, 10)))
    session["qualification_turns"] = [
        {
            "step_id": "baseline-qualification-1",
            "phase": "qualification",
            "usage": _usage(50, 10),
            "wall_time_ms": 700,
            "deterministic_ms": 80,
            "score": {"ok": False, "passed": 7, "total": 10},
        }
    ]
    session["qualification"] = {
        "selected_turn": {"kind": "lifecycle", "index": 1},
    }

    summary = summarize_agent_eval_session(session)

    assert summary["selected_turn"] == {"kind": "lifecycle", "index": 1}
    assert summary["turn_count"] == 3
    assert summary["final_acceptance"] == 1.0
    assert summary["tokens"]["qualification_tokens"] == 60


def test_session_summary_uses_minimum_viewport_anchor_similarity_for_layout():
    session = _session("viewspec-deep")
    session["turns"][-1]["score"] = {
        "ok": True,
        "passed": 2,
        "total": 2,
        "dimensions": {"layout_fidelity": {"passed": 2, "total": 2}},
        "viewports": [
            {"layout_fidelity": 0.96, "criteria": []},
            {"layout_fidelity": 0.91, "criteria": []},
            {"layout_fidelity": 0.94, "criteria": []},
        ],
    }

    summary = summarize_agent_eval_session(session)

    assert summary["final_dimensions"]["layout_fidelity"]["score"] == 1.0
    assert summary["final_layout_fidelity"] == 0.91


def test_study_summary_compares_all_arms_but_marks_pilot_note():
    protocol = load_agent_eval_protocol(PROTOCOL_PATH)
    sessions = [
        _session("code-first", token_scale=2, scores=((7, 10), (9, 10))),
        _session("viewspec-core", scores=((8, 10), (10, 10))),
        _session("viewspec-deep", scores=((9, 10), (10, 10)), deterministic_ms=50),
    ]

    report = summarize_agent_eval_study(sessions, success_criteria=protocol.success_criteria)

    assert report["session_count"] == 3
    assert report["gates"]["status"] == "pilot_only"
    assert report["gates"]["sample_size_met"] is False
    assert "pilot" in report["gates"]["note"].lower()
    assert report["comparisons"]["viewspec-deep"]["acceptance_delta"] == pytest.approx(0.1)
    assert report["comparisons"]["viewspec-deep"]["token_reduction"] == pytest.approx(0.5)


def test_value_premium_study_requires_more_value_with_bounded_cost():
    protocol = load_agent_eval_protocol(VALUE_PROTOCOL_PATH)

    def quality_score(stable: bool) -> dict:
        return {
            "ok": stable,
            "passed": 10 if stable else 9,
            "total": 10,
            "dimensions": {
                "semantics": {"passed": 1, "total": 1},
                "layout_fidelity": {"passed": 9, "total": 10},
            },
            "viewports": [
                {
                    "viewport": {"width": 390, "height": 844},
                    "criteria": [{"id": "stable-contract", "passed": stable}],
                }
            ],
        }

    baseline = _session("code-first")
    baseline["turns"][0]["phase"] = "establishment"
    baseline["turns"][1]["phase"] = "evolution"
    baseline["turns"][0]["score"] = quality_score(True)
    baseline["turns"][1]["score"] = quality_score(False)

    core = _session("viewspec-core")
    deep = _session("viewspec-deep", token_scale=2, deterministic_ms=100)
    for session in (core, deep):
        session["turns"][0]["phase"] = "establishment"
        session["turns"][1]["phase"] = "evolution"
        session["turns"][0]["score"] = quality_score(True)
        session["turns"][1]["score"] = quality_score(True)
        for turn in session["turns"]:
            turn["proof"] = {"ok": True}
            turn["wall_time_ms"] = 1_500
    deep["value_evidence"] = {
        "artifact_integrity": _artifact_integrity(14),
        "mutation_trials": [
            {
                "id": f"mutation-{index}",
                "order": index,
                "applicable": True,
                "repair_applicable": True,
                "baseline_sha256": "a" * 64,
                "mutated_sha256": f"{index:064x}",
                "expected_detectors": ["expected"],
                "observed_detectors": ["expected"],
                "detected": True,
                "repaired": True,
                "repaired_sha256": "c" * 64,
                "repair_usage": _usage(0, 0),
                "repair_wall_time_ms": 0,
                "deterministic_ms": 1,
                "commands": ["score"],
                "artifacts": {
                    "root": "fixture",
                    "score_status": "recorded",
                    "score": "fixture/browser-score.json",
                },
            }
            for index in range(10)
        ],
        "negative_control_trials": [
            {
                "id": f"control-{index}",
                "order": index + 10,
                "applicable": True,
                "baseline_sha256": "a" * 64,
                "detected": False,
                "deterministic_ms": 1,
                "commands": ["score"],
                "artifacts": {
                    "root": "fixture",
                    "score_status": "recorded",
                    "score": "fixture/browser-score.json",
                },
            }
            for index in range(2)
        ],
        "target_trials": [
            _target("static-shell", parity=1.0),
            _target("native-react", parity=0.98),
        ],
    }
    environment = {
        "controls": {"ignore_user_config": True},
        "versions": {"codex": "codex-cli test"},
        "inputs": {"protocol": {"sha256": "b" * 64}},
    }
    for session in (baseline, core, deep):
        session["model"] = "gpt-test-pinned"
        session["environment"] = environment

    report = summarize_agent_eval_study(
        [baseline, core, deep],
        success_criteria=protocol.success_criteria,
        minimum_sessions_per_arm=1,
        evaluation_mode=protocol.evaluation_mode,
        primary_arm=protocol.primary_arm,
    )

    assert report["evaluation_mode"] == "value_premium"
    assert report["comparisons"]["viewspec-deep"]["total_token_premium"] == 2.0
    assert report["comparisons"]["viewspec-deep"]["regression_reduction"] == 1.0
    assert report["gates"]["results"]["functional_quality"] is True
    assert report["gates"]["results"]["visual_quality"] is True
    assert report["gates"]["results"]["mutation_detection"] is True
    assert report["gates"]["results"]["cross_target_parity"] is True
    assert report["gates"]["pass"] is True

    deep_second = json.loads(json.dumps(deep))
    deep_second["seed"] = 2
    deep_failed_proof = json.loads(json.dumps(deep))
    deep_failed_proof["seed"] = 3
    deep_failed_proof["turns"][0]["proof"]["ok"] = False
    proof_failure_report = summarize_agent_eval_study(
        [baseline, core, deep, deep_second, deep_failed_proof],
        success_criteria=protocol.success_criteria,
        minimum_sessions_per_arm=1,
        evaluation_mode=protocol.evaluation_mode,
        primary_arm=protocol.primary_arm,
    )

    assert proof_failure_report["arms"]["viewspec-deep"]["median_native_proof_pass_rate"] == 1.0
    assert proof_failure_report["arms"]["viewspec-deep"]["pooled_native_proof_pass_rate"] == pytest.approx(5 / 6)
    assert proof_failure_report["arms"]["viewspec-deep"]["pooled_selected_native_proof_pass_rate"] == 1.0
    assert proof_failure_report["arms"]["viewspec-deep"]["pooled_intermediate_native_proof_detection_rate"] == pytest.approx(1 / 3)
    assert proof_failure_report["gates"]["results"]["native_proof_health"] is True
    assert proof_failure_report["gates"]["pass"] is True

    deep_failed_selected = json.loads(json.dumps(deep))
    deep_failed_selected["seed"] = 4
    deep_failed_selected["turns"][-1]["proof"]["ok"] = False
    selected_failure_report = summarize_agent_eval_study(
        [baseline, core, deep, deep_failed_selected],
        success_criteria=protocol.success_criteria,
        minimum_sessions_per_arm=1,
        evaluation_mode=protocol.evaluation_mode,
        primary_arm=protocol.primary_arm,
    )

    assert selected_failure_report["arms"]["viewspec-deep"]["pooled_selected_native_proof_pass_rate"] == 0.5
    assert selected_failure_report["gates"]["results"]["native_proof_health"] is False
    assert selected_failure_report["gates"]["pass"] is False


def test_value_premium_study_does_not_pass_missing_assurance_evidence():
    protocol = load_agent_eval_protocol(VALUE_PROTOCOL_PATH)
    sessions = [_session(arm) for arm in AGENT_UI_EVAL_ARMS]
    sessions[-1]["value_evidence"] = {
        "negative_control_trials": [
            {
                "id": f"control-{index}",
                "order": index,
                "applicable": False,
                "baseline_sha256": "a" * 64,
                "invalid_reason": "baseline_ineligible",
            }
            for index in range(2)
        ]
    }

    report = summarize_agent_eval_study(
        sessions,
        success_criteria=protocol.success_criteria,
        minimum_sessions_per_arm=1,
        evaluation_mode=protocol.evaluation_mode,
        primary_arm=protocol.primary_arm,
    )

    assert report["gates"]["results"]["mutation_detection"] is None
    assert report["gates"]["results"]["mutation_false_positives"] is None
    assert report["gates"]["results"]["cross_target_pass"] is None
    assert report["gates"]["pass"] is False


def test_one_seed_shakedown_exit_is_independent_from_population_value_gates():
    protocol = load_agent_eval_protocol(VALUE_PROTOCOL_PATH)
    score = {
        "ok": True,
        "passed": 6,
        "total": 6,
        "dimensions": {
            "semantics": {"passed": 3, "total": 3},
            "interaction": {"passed": 3, "total": 3},
        },
        "viewports": [
            {"viewport": {"width": width}, "layout_fidelity": 0.8, "criteria": []}
            for width in (390, 768, 1440)
        ],
    }
    sessions = []
    for arm in AGENT_UI_EVAL_ARMS:
        proof = None
        if arm == "viewspec-core":
            proof = {"ok": True}
        elif arm == "viewspec-deep":
            proof = {
                "ok": True,
                "static_analysis": {"status": "passed"},
                "text_layout": {"status": "passed"},
            }
        session = {
            "schema_version": 1,
            "protocol_id": protocol.id,
            "task_id": "field-dispatch-lifecycle",
            "arm_id": arm,
            "seed": 104729,
            "model": "gpt-test",
            "turns": [
                {
                    "step_id": "repair-and-finalize",
                    "phase": "repair",
                    "usage": _usage(),
                    "wall_time_ms": 1,
                    "deterministic_ms": 1,
                    "score": score,
                    "proof": proof,
                }
            ],
        }
        if arm != "code-first":
            session["value_evidence"] = {
                "artifact_integrity": _artifact_integrity(20),
                "baseline": {"eligible": True, "source_sha256": "a" * 64},
                "mutation_trials": [
                    {
                        "id": f"mutation-{index}",
                        "order": index,
                        "applicable": True,
                        "repair_applicable": True,
                        "baseline_sha256": "a" * 64,
                        "mutated_sha256": f"{index + 1:064x}",
                        "repaired_sha256": "c" * 64,
                        "expected_detectors": ["expected"],
                        "observed_detectors": ["expected"],
                        "detected": True,
                        "repaired": True,
                        "repair_usage": _usage(),
                        "repair_wall_time_ms": 1,
                        "deterministic_ms": 1,
                        "commands": ["score"],
                        "artifacts": {
                            "root": "fixture",
                            "score_status": "recorded",
                            "score": "fixture/browser-score.json",
                        },
                    }
                    for index in range(5)
                ],
                "negative_control_trials": [
                    {
                        "id": f"control-{index}",
                        "order": index + 5,
                        "applicable": True,
                        "baseline_sha256": "a" * 64,
                        "detected": False,
                        "deterministic_ms": 1,
                        "commands": ["score"],
                        "artifacts": {
                            "root": "fixture",
                            "score_status": "recorded",
                            "score": "fixture/browser-score.json",
                        },
                    }
                    for index in range(2)
                ],
                "target_trials": [
                    _target("static-shell", parity=1.0),
                    _target("native-react", parity=0.96),
                ],
                "repair_turns": [
                    {
                        "usage": _usage(),
                        "wall_time_ms": 1,
                        "deterministic_ms": 0,
                    }
                    for _index in range(5)
                ],
                "deterministic_overhead_ms": 7,
            }
        sessions.append(session)

    exit_result = summarize_agent_eval_shakedown_exit(
        sessions,
        success_criteria=protocol.success_criteria,
    )
    study = summarize_agent_eval_study(
        sessions,
        success_criteria=protocol.success_criteria,
        minimum_sessions_per_arm=18,
        evaluation_mode=protocol.evaluation_mode,
        primary_arm=protocol.primary_arm,
    )

    assert exit_result["pass"] is True
    assert all(result["pass"] for result in exit_result["arms"].values())
    assert study["shakedown_exit"] == exit_result
    assert study["gates"]["status"] == "pilot_only"
    assert study["gates"]["pass"] is False


def test_study_without_every_arm_is_inconclusive():
    protocol = load_agent_eval_protocol(PROTOCOL_PATH)

    report = summarize_agent_eval_study(
        [_session("code-first")],
        success_criteria=protocol.success_criteria,
    )

    assert report["gates"]["status"] == "inconclusive"
    assert report["gates"]["pass"] is False


def test_study_rejects_incomplete_or_mixed_model_provenance():
    protocol = load_agent_eval_protocol(PROTOCOL_PATH)
    sessions = [_session(arm) for arm in AGENT_UI_EVAL_ARMS]
    environment = {
        "controls": {"ignore_user_config": True},
        "versions": {"codex": "codex-cli test"},
        "inputs": {"protocol": {"sha256": "a" * 64}},
    }
    for session in sessions:
        session["model"] = "gpt-test-pinned"
        session["environment"] = environment

    controlled = summarize_agent_eval_study(
        sessions,
        success_criteria=protocol.success_criteria,
        minimum_sessions_per_arm=1,
    )
    assert controlled["gates"]["results"]["provenance_complete"] is True
    assert controlled["gates"]["results"]["provenance_consistent"] is True

    sessions[-1]["model"] = "different-model"
    mixed = summarize_agent_eval_study(
        sessions,
        success_criteria=protocol.success_criteria,
        minimum_sessions_per_arm=1,
    )
    assert mixed["gates"]["results"]["provenance_complete"] is True
    assert mixed["gates"]["results"]["provenance_consistent"] is False
