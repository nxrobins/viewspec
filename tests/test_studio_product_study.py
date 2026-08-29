from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
import zlib

import pytest

from scripts.run_studio_product_study import (
    DEFAULT_PROTOCOL,
    PNG_SIGNATURE,
    StudyError,
    _analysis_preferences,
    _analysis_sessions,
    _canonical_bytes,
    _session_template,
    _sha256_bytes,
    _strip_png_metadata,
    build_review_packets,
    build_study_plan,
    initialize_study,
    load_protocol,
    load_study,
    summarize_study,
    validate_session_record,
)


PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_bytes(value))


def _initialized(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    root = tmp_path / "study"
    initialize_study(DEFAULT_PROTOCOL, root)
    return root, load_study(root)


def _completed_session(study: dict[str, object], slot_id: str) -> dict[str, object]:
    record = _session_template(study, slot_id)
    arm = record["arm"]
    screenshot_path = study["root"] / "artifacts" / "pixel.png"
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    screenshot_path.write_bytes(PIXEL_PNG)
    evidence_path = study["root"] / "artifacts" / "mechanical.json"
    evidence_path.write_text('{"mechanical_pass":true}\n', encoding="utf-8")
    record.update(
        timing_ms={"first_value": 20_000, "three_changes": 80_000, "private_handoff": 120_000},
        desirability_rating=5,
        changes=[
            {
                "id": change_id,
                "completed": True,
                "elapsed_ms": 20_000,
                "stable_criterion_regressions": 0,
            }
            for change_id in study["protocol"]["human_study"]["task_contract"]["change_step_ids"]
        ],
        health_comprehension={
            "checked_scope": "source_artifact_and_declared_contract_health",
            "unproven_scope": "human_desirability_visual_parity_and_production_behavior",
        },
        private_handoff={
            "completed": True,
            "environment": "production_canary" if arm == "viewspec-studio" else "matched_baseline",
            "elapsed_ms": 120_000,
            "receipt_sha256": "9" * 64,
        },
        artifacts={
            "render_status": "complete",
            "source_revisions": [str(index) * 64 for index in range(4)],
            "screenshots": [
                {
                    "target": target,
                    "viewport": viewport,
                    "path": "artifacts/pixel.png",
                    "sha256": _sha256_bytes(PIXEL_PNG),
                }
                for target in study["protocol"]["human_study"]["task_contract"]["required_targets"]
                for viewport in study["protocol"]["human_study"]["task_contract"]["required_viewports"]
            ],
            "mechanical_evidence": {
                "path": "artifacts/mechanical.json",
                "sha256": _sha256_bytes(evidence_path.read_bytes()),
            },
        },
        facilitator_attestation={
            "artifact_capture_complete": True,
            "no_coaching_beyond_script": True,
            "product_only_capture": True,
            "timings_observed": True,
        },
    )
    return record


def _write_primary_sessions(root: Path, study: dict[str, object]) -> None:
    for slot in study["plan"]["participant_slots"]:
        if slot["cohort"] == "primary":
            _write_json(root / "sessions" / f"{slot['slot_id']}.json", _completed_session(study, slot["slot_id"]))


def _write_preferences(root: Path, studio_wins: int) -> None:
    packet_key = json.loads((root / "review-packet-key.json").read_text(encoding="utf-8"))
    plan = json.loads((root / "study-plan.json").read_text(encoding="utf-8"))
    primary_slots = [slot["reviewer_slot"] for slot in plan["reviewer_slots"] if slot["cohort"] == "primary"]
    packets = {item["reviewer_slot"]: item for item in packet_key["packets"]}
    for index, slot_id in enumerate(primary_slots):
        packet = packets[slot_id]
        desired_arm = "viewspec-studio" if index < studio_wins else "code-first"
        selection = next(label for label, value in packet["candidates"].items() if value["arm"] == desired_arm)
        _write_json(
            root / "preferences" / f"{slot_id}.json",
            {
                "schema_version": 1,
                "study_id": plan["study_id"],
                "reviewer_slot": slot_id,
                "status": "completed",
                "exclusion_reason": None,
                "packet_sha256": packet["packet_sha256"],
                "selection": selection,
                "ratings": {"A": 4, "B": 4},
                "reason_codes": ["visual_finish"],
                "reviewer_attestation": {
                    "no_arm_identity_seen": True,
                    "reviewed_both_targets": True,
                },
            },
        )


def _complete_study(tmp_path: Path, *, studio_wins: int = 14) -> tuple[Path, dict[str, object]]:
    root, study = _initialized(tmp_path)
    _write_primary_sessions(root, study)
    build_review_packets(root)
    _write_preferences(root, studio_wins)
    return root, study


def test_study_plan_is_seeded_balanced_and_exposes_every_primary_session_twice(tmp_path: Path) -> None:
    protocol, protocol_path = load_protocol(DEFAULT_PROTOCOL)
    left, left_key = build_study_plan(protocol, protocol_path)
    right, right_key = build_study_plan(protocol, protocol_path)

    assert left == right
    assert left_key == right_key
    primary = [slot for slot in left["participant_slots"] if slot["cohort"] == "primary"]
    assert {arm: sum(slot["arm"] == arm for slot in primary) for arm in protocol["arms"]} == {
        "code-first": 18,
        "viewspec-studio": 18,
    }
    for arm in protocol["arms"]:
        positions = sorted(
            candidate["analysis_position"]
            for entry in left_key["reviewer_mappings"][:18]
            for candidate in entry["candidates"].values()
            if candidate["arm"] == arm
        )
        assert positions == list(range(1, 19))
    for primary, reserve in zip(
        left_key["reviewer_mappings"][:18],
        left_key["reviewer_mappings"][18:],
        strict=True,
    ):
        assert reserve["candidates"] == primary["candidates"]

    initialized = initialize_study(DEFAULT_PROTOCOL, tmp_path / "initialized")
    assert initialized["lock"]["study_plan_sha256"] == _sha256_bytes(
        (tmp_path / "initialized" / "study-plan.json").read_bytes()
    )


def test_study_plan_rejects_hash_or_schedule_tampering(tmp_path: Path) -> None:
    root, _ = _initialized(tmp_path)
    plan_path = root / "study-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    original = plan["participant_slots"][0]["arm"]
    plan["participant_slots"][0]["arm"] = (
        "code-first" if original == "viewspec-studio" else "viewspec-studio"
    )
    _write_json(plan_path, plan)

    with pytest.raises(StudyError, match="no longer matches"):
        load_study(root)


def test_session_validation_is_closed_and_keeps_failures_in_analysis(tmp_path: Path) -> None:
    root, study = _initialized(tmp_path)
    first = study["plan"]["participant_slots"][0]
    record = _completed_session(study, first["slot_id"])
    validate_session_record(record, study=study)

    unknown = copy.deepcopy(record)
    unknown["claim"] = "excellent"
    with pytest.raises(StudyError, match="shape mismatch"):
        validate_session_record(unknown, study=study)

    failed = copy.deepcopy(record)
    failed["changes"][0]["completed"] = False
    failed["changes"][0]["elapsed_ms"] = None
    assert validate_session_record(failed, study=study)["status"] == "completed"

    excluded = copy.deepcopy(record)
    excluded["status"] = "excluded"
    excluded["exclusion_reason"] = "task_was_too_hard"
    with pytest.raises(StudyError, match="preregistered"):
        validate_session_record(excluded, study=study)
    assert not list((root / "sessions").iterdir())


def test_preregistered_reserve_replaces_only_an_allowed_exclusion(tmp_path: Path) -> None:
    root, study = _initialized(tmp_path)
    slots = study["plan"]["participant_slots"]
    primary = [slot for slot in slots if slot["cohort"] == "primary"]
    for slot in primary:
        record = _completed_session(study, slot["slot_id"])
        if slot == primary[0]:
            record["status"] = "excluded"
            record["exclusion_reason"] = "consent_withdrawn"
        _write_json(root / "sessions" / f"{slot['slot_id']}.json", record)
    arm = primary[0]["arm"]
    reserve = next(slot for slot in slots if slot["cohort"] == "reserve" and slot["arm"] == arm)
    _write_json(root / "sessions" / f"{reserve['slot_id']}.json", _completed_session(study, reserve["slot_id"]))

    selected, errors = _analysis_sessions(study)

    assert errors == []
    assert all(len(selected[arm_id]) == 18 for arm_id in study["protocol"]["arms"])
    assert reserve["slot_id"] in {record["slot_id"] for record in selected[arm]}


def test_png_blinding_removes_text_metadata_without_changing_critical_chunks() -> None:
    iend_at = PIXEL_PNG.rfind(b"IEND") - 4
    payload = b"source-arm\x00viewspec-studio"
    chunk_type = b"tEXt"
    text_chunk = (
        len(payload).to_bytes(4, "big")
        + chunk_type
        + payload
        + (zlib.crc32(chunk_type + payload) & 0xFFFFFFFF).to_bytes(4, "big")
    )
    tagged = PIXEL_PNG[:iend_at] + text_chunk + PIXEL_PNG[iend_at:]

    blinded = _strip_png_metadata(tagged)

    assert blinded.startswith(PNG_SIGNATURE)
    assert b"tEXt" not in blinded
    assert b"viewspec-studio" not in blinded
    assert blinded == PIXEL_PNG


def test_complete_human_study_passes_at_preregistered_wilson_threshold(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root, _ = _complete_study(tmp_path, studio_wins=14)
    monkeypatch.setattr(
        "scripts.run_studio_product_study._canary_status",
        lambda study: (True, []),
    )

    report = summarize_study(root)

    assert report["human_gate_pass"] is True
    assert report["preference"]["rate"] == pytest.approx(14 / 18)
    assert report["preference"]["wilson_interval"]["lower"] > 0.5
    assert all(report["gates"].values())
    assert report["full_product_pass"] is False


def test_preference_rate_without_confidence_above_chance_fails(tmp_path: Path) -> None:
    root, _ = _complete_study(tmp_path, studio_wins=13)

    report = summarize_study(root)

    assert report["preference"]["rate"] > 0.65
    assert report["preference"]["wilson_interval"]["lower"] < 0.5
    assert report["gates"]["blinded_preference_rate"] is True
    assert report["gates"]["blinded_preference_confidence_above_chance"] is False
    assert report["human_gate_pass"] is False


def test_incomplete_study_remains_blinded_and_does_not_report_partial_outcomes(tmp_path: Path) -> None:
    root, _ = _initialized(tmp_path)

    report = summarize_study(root)

    assert report["analysis_locked"] is True
    assert report["arm_metrics"] is None
    assert report["preference"] is None
    assert report["human_gate_pass"] is False


def test_reviewer_reserve_uses_the_excluded_reviewers_exact_pair(tmp_path: Path) -> None:
    root, study = _initialized(tmp_path)
    _write_primary_sessions(root, study)
    build_review_packets(root)
    _write_preferences(root, studio_wins=14)
    plan = study["plan"]
    primary_slot = next(slot for slot in plan["reviewer_slots"] if slot["cohort"] == "primary")
    reserve_slot = next(
        slot for slot in plan["reviewer_slots"] if slot["reserve_for"] == primary_slot["reviewer_slot"]
    )
    primary_path = root / "preferences" / f"{primary_slot['reviewer_slot']}.json"
    excluded = json.loads(primary_path.read_text(encoding="utf-8"))
    excluded["status"] = "excluded"
    excluded["exclusion_reason"] = "consent_withdrawn"
    _write_json(primary_path, excluded)
    packet_key = json.loads((root / "review-packet-key.json").read_text(encoding="utf-8"))
    packets = {item["reviewer_slot"]: item for item in packet_key["packets"]}
    reserve_packet = packets[reserve_slot["reviewer_slot"]]
    primary_packet = packets[primary_slot["reviewer_slot"]]
    assert reserve_packet["candidates"] == primary_packet["candidates"]
    selection = next(
        label for label, value in reserve_packet["candidates"].items() if value["arm"] == "viewspec-studio"
    )
    replacement = {
        **excluded,
        "reviewer_slot": reserve_slot["reviewer_slot"],
        "status": "completed",
        "exclusion_reason": None,
        "packet_sha256": reserve_packet["packet_sha256"],
        "selection": selection,
    }
    _write_json(root / "preferences" / f"{reserve_slot['reviewer_slot']}.json", replacement)

    selected, errors = _analysis_preferences(load_study(root))

    assert errors == []
    assert len(selected) == 18
    assert reserve_slot["reviewer_slot"] in {record["reviewer_slot"] for record in selected}


def test_missing_production_canary_cannot_pass_human_gate(tmp_path: Path) -> None:
    root, _ = _complete_study(tmp_path)

    report = summarize_study(root)

    assert report["production_canary"]["passed"] is False
    assert report["gates"]["production_private_review_canary"] is False
    assert report["human_gate_pass"] is False
