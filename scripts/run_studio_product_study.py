#!/usr/bin/env python3
"""Plan, blind, and score the preregistered ViewSpec Studio human-value study."""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
import hashlib
import html
import json
from pathlib import Path
import random
import shutil
import statistics
import struct
import tempfile
from typing import Any
import zlib

if __package__:
    from scripts.check_studio_production_canary import CanaryError, evaluate_canary
else:
    from check_studio_production_canary import CanaryError, evaluate_canary


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTOCOL = ROOT / "conformance" / "studio-product-v1" / "protocol.json"
STUDY_SCHEMA_VERSION = 1
SESSION_FIELDS = {
    "schema_version",
    "study_id",
    "slot_id",
    "arm",
    "status",
    "exclusion_reason",
    "eligibility",
    "timing_ms",
    "desirability_rating",
    "changes",
    "health_comprehension",
    "private_handoff",
    "generated_output_edits",
    "runtime_failures",
    "artifacts",
    "facilitator_attestation",
}
ELIGIBILITY_FIELDS = {"consent_recorded", "new_to_viewspec", "recent_coding_agent_ui_use"}
TIMING_FIELDS = {"first_value", "three_changes", "private_handoff"}
CHANGE_FIELDS = {"id", "completed", "elapsed_ms", "stable_criterion_regressions"}
HEALTH_FIELDS = {"checked_scope", "unproven_scope"}
HANDOFF_FIELDS = {"completed", "environment", "elapsed_ms", "receipt_sha256"}
ARTIFACT_FIELDS = {"render_status", "source_revisions", "screenshots", "mechanical_evidence"}
SCREENSHOT_FIELDS = {"target", "viewport", "path", "sha256"}
FILE_REF_FIELDS = {"path", "sha256"}
FACILITATOR_FIELDS = {
    "artifact_capture_complete",
    "no_coaching_beyond_script",
    "product_only_capture",
    "timings_observed",
}
PREFERENCE_FIELDS = {
    "schema_version",
    "study_id",
    "reviewer_slot",
    "status",
    "exclusion_reason",
    "packet_sha256",
    "selection",
    "ratings",
    "reason_codes",
    "reviewer_attestation",
}
PREFERENCE_RATING_FIELDS = {"A", "B"}
REVIEWER_ATTESTATION_FIELDS = {"no_arm_identity_seen", "reviewed_both_targets"}
SHA256_CHARS = frozenset("0123456789abcdef")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class StudyError(ValueError):
    """Raised when preregistered study evidence is invalid or incomplete."""


def _canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StudyError(f"Could not read JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StudyError(f"{path} must contain one JSON object")
    return value


def _write_atomic(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(value)
    temporary.replace(path)


def _write_json(path: Path, value: object) -> None:
    _write_atomic(path, _canonical_bytes(value))


def _exact(value: Mapping[str, Any], fields: set[str], noun: str) -> None:
    if set(value) != fields:
        missing = sorted(fields - set(value))
        unknown = sorted(set(value) - fields)
        raise StudyError(f"{noun} shape mismatch; missing={missing}, unknown={unknown}")


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= SHA256_CHARS


def _bounded_int(value: object, noun: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise StudyError(f"{noun} must be an integer from {minimum} through {maximum}")
    return value


def _resolve_input(protocol_path: Path, relative: object, noun: str) -> tuple[str, Path, str]:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise StudyError(f"{noun} must be a non-empty protocol-relative path")
    path = (protocol_path.parent / relative).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise StudyError(f"{noun} escapes the repository") from exc
    if not path.is_file() or path.is_symlink():
        raise StudyError(f"{noun} is not a regular repository file: {path}")
    return relative, path, _sha256_file(path)


def load_protocol(path: str | Path = DEFAULT_PROTOCOL) -> tuple[dict[str, Any], Path]:
    protocol_path = Path(path).resolve()
    protocol = _read_object(protocol_path)
    if protocol.get("schema_version") != 1 or protocol.get("id") != "viewspec-studio-product-v1":
        raise StudyError("Unsupported Studio product protocol")
    if protocol.get("arms") != ["code-first", "viewspec-studio"]:
        raise StudyError("Human study requires the exact code-first and viewspec-studio arms")
    human = protocol.get("human_study")
    gates = protocol.get("human_gates")
    if not isinstance(human, dict) or human.get("schema_version") != STUDY_SCHEMA_VERSION:
        raise StudyError("Protocol human_study is missing or unsupported")
    if not isinstance(gates, dict):
        raise StudyError("Protocol human_gates is missing")
    if human.get("participant_primary_slots_per_arm") != gates.get("minimum_sessions_per_arm"):
        raise StudyError("Primary participant slots must equal the preregistered per-arm minimum")
    if human.get("blinded_comparisons") != gates.get("minimum_blinded_comparisons"):
        raise StudyError("Primary blinded comparisons must equal the preregistered minimum")
    return protocol, protocol_path


def _study_inputs(protocol: dict[str, Any], protocol_path: Path) -> dict[str, Any]:
    human = protocol["human_study"]
    reference_rel, _, reference_sha = _resolve_input(protocol_path, human["reference"], "study reference")
    task = human["task_contract"]
    source_rel, source_path, source_sha = _resolve_input(
        protocol_path,
        task["source_protocol"],
        "source task protocol",
    )
    source_protocol = _read_object(source_path)
    tasks = source_protocol.get("tasks")
    if not isinstance(tasks, list):
        raise StudyError("Source task protocol has no tasks array")
    matches = [item for item in tasks if isinstance(item, dict) and item.get("id") == task["task_id"]]
    if len(matches) != 1:
        raise StudyError("Source task id does not resolve exactly once")
    step_ids = [item.get("id") for item in matches[0].get("steps", []) if isinstance(item, dict)]
    required_steps = [task["initial_step_id"], *task["change_step_ids"]]
    if len(task.get("change_step_ids", [])) != 3 or not all(step in step_ids for step in required_steps):
        raise StudyError("Human study task must bind one initial and three declared source steps")
    return {
        "product_protocol": {
            "path": str(protocol_path.relative_to(ROOT)),
            "sha256": _sha256_file(protocol_path),
        },
        "runner": {
            "path": str(Path(__file__).resolve().relative_to(ROOT)),
            "sha256": _sha256_file(Path(__file__).resolve()),
        },
        "reference": {"path": reference_rel, "sha256": reference_sha},
        "source_task_protocol": {"path": source_rel, "sha256": source_sha},
        "task_id": task["task_id"],
        "step_ids": required_steps,
    }


def build_study_plan(protocol: dict[str, Any], protocol_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    human = protocol["human_study"]
    arms = list(protocol["arms"])
    primary_count = _bounded_int(
        human["participant_primary_slots_per_arm"],
        "participant_primary_slots_per_arm",
        minimum=1,
    )
    reserve_count = _bounded_int(
        human["participant_reserve_slots_per_arm"],
        "participant_reserve_slots_per_arm",
        minimum=0,
    )
    review_count = _bounded_int(human["blinded_comparisons"], "blinded_comparisons", minimum=1)
    review_reserve_count = _bounded_int(
        human["blinded_comparison_reserve_slots"],
        "blinded_comparison_reserve_slots",
        minimum=0,
    )
    if review_count != primary_count:
        raise StudyError("Every analyzed participant session must appear in exactly one primary comparison")
    if review_reserve_count not in {0, review_count}:
        raise StudyError("Reviewer reserves must provide zero or one exact replacement per primary comparison")
    seed = _bounded_int(human["seed"], "human study seed", minimum=0)
    rng = random.Random(seed)
    participants: list[dict[str, Any]] = []
    arm_sequences = dict.fromkeys(arms, 0)
    assignments: list[tuple[str, str]] = []
    for cohort, count in (("primary", primary_count), ("reserve", reserve_count)):
        cohort_arms = [arm for arm in arms for _ in range(count)]
        rng.shuffle(cohort_arms)
        assignments.extend((cohort, arm) for arm in cohort_arms)
    for index, (cohort, arm) in enumerate(assignments, start=1):
        arm_sequences[arm] += 1
        participants.append(
            {
                "slot_id": f"P{index:03d}",
                "cohort": cohort,
                "arm": arm,
                "arm_sequence": arm_sequences[arm],
            }
        )

    code_positions = list(range(1, primary_count + 1))
    studio_positions = list(range(1, primary_count + 1))
    rng.shuffle(code_positions)
    rng.shuffle(studio_positions)
    reviewers: list[dict[str, Any]] = []
    key_entries: list[dict[str, Any]] = []
    for index in range(1, review_count + 1):
        code_position = code_positions[index - 1]
        studio_position = studio_positions[index - 1]
        studio_label = "A" if rng.randrange(2) == 0 else "B"
        mapping = {
            studio_label: {"arm": "viewspec-studio", "analysis_position": studio_position},
            "B" if studio_label == "A" else "A": {
                "arm": "code-first",
                "analysis_position": code_position,
            },
        }
        reviewer_slot = f"R{index:03d}"
        reviewers.append({"reviewer_slot": reviewer_slot, "cohort": "primary", "reserve_for": None})
        key_entries.append({"reviewer_slot": reviewer_slot, "candidates": mapping})
    for index in range(1, review_reserve_count + 1):
        primary_entry = key_entries[index - 1]
        reviewer_slot = f"R{review_count + index:03d}"
        primary_slot = primary_entry["reviewer_slot"]
        mapping = {label: dict(candidate) for label, candidate in primary_entry["candidates"].items()}
        reviewers.append(
            {"reviewer_slot": reviewer_slot, "cohort": "reserve", "reserve_for": primary_slot}
        )
        key_entries.append({"reviewer_slot": reviewer_slot, "candidates": mapping})

    primary_key = key_entries[:review_count]
    for arm in arms:
        observed = sorted(
            candidate["analysis_position"]
            for entry in primary_key
            for candidate in entry["candidates"].values()
            if candidate["arm"] == arm
        )
        expected = list(range(1, primary_count + 1))
        if observed != expected:
            raise StudyError(f"Primary blinded schedule does not expose every {arm} session exactly once")

    plan = {
        "schema_version": STUDY_SCHEMA_VERSION,
        "study_id": human["id"],
        "seed": seed,
        "design": human["design"],
        "inputs": _study_inputs(protocol, protocol_path),
        "participant_slots": participants,
        "reviewer_slots": reviewers,
        "analysis": {
            "primary_arm": human["primary_arm"],
            "minimum_sessions_per_arm": primary_count,
            "minimum_blinded_comparisons": review_count,
            "reserve_policy": human["reserve_policy"],
            "failure_policy": human["failure_policy"],
        },
    }
    plan_sha = _sha256_bytes(_canonical_bytes(plan))
    blinding_key = {
        "schema_version": STUDY_SCHEMA_VERSION,
        "study_id": human["id"],
        "plan_sha256": plan_sha,
        "reviewer_mappings": key_entries,
    }
    return plan, blinding_key


def _study_readme() -> str:
    return """# ViewSpec Studio human-value study

This directory is a preregistered, between-subjects study. Do not edit `study-plan.json`,
`blinding-key.json`, or `study-lock.json` after initialization. Participant slots are anonymous
study identifiers, not names or email addresses.

Recruit primary slots first. Activate reserve slots in their recorded arm order only when a
primary slot has one allowed pre-exposure exclusion. Product failure, low ratings, slow work, and
incomplete changes remain outcomes; they are never exclusions.

Store one closed `sessions/P###.json` record per exposed participant. After all analyzed sessions
exist, run `build-review-packets`. Give each independent reviewer only their `R###` packet and do
not expose the study plan, source session, tool chrome, filenames, or blinding keys. Store one
closed `preferences/R###.json` record per reviewer. Ties remain in the preference denominator.

The summary cannot pass without the preregistered sample, verified artifacts, a 95% Wilson lower
bound above chance, and separately verified production-canary evidence. Automated test fixtures
must never be placed in a real study directory.
"""


def initialize_study(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol, resolved_protocol = load_protocol(protocol_path)
    destination = output.resolve()
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise StudyError(f"Study output must be an empty directory: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    plan, key = build_study_plan(protocol, resolved_protocol)
    _write_json(destination / "study-plan.json", plan)
    _write_json(destination / "blinding-key.json", key)
    (destination / "blinding-key.json").chmod(0o600)
    lock = {
        "schema_version": STUDY_SCHEMA_VERSION,
        "study_id": plan["study_id"],
        "product_protocol_sha256": plan["inputs"]["product_protocol"]["sha256"],
        "source_task_protocol_sha256": plan["inputs"]["source_task_protocol"]["sha256"],
        "reference_sha256": plan["inputs"]["reference"]["sha256"],
        "runner_sha256": plan["inputs"]["runner"]["sha256"],
        "study_plan_sha256": _sha256_file(destination / "study-plan.json"),
        "blinding_key_sha256": _sha256_file(destination / "blinding-key.json"),
    }
    _write_json(destination / "study-lock.json", lock)
    (destination / "study-lock.json").chmod(0o600)
    _write_atomic(destination / "README.md", _study_readme().encode("utf-8"))
    (destination / "sessions").mkdir()
    (destination / "preferences").mkdir()
    return {"plan": plan, "lock": lock, "root": str(destination)}


def load_study(study_root: str | Path, protocol_path: str | Path = DEFAULT_PROTOCOL) -> dict[str, Any]:
    root = Path(study_root).resolve()
    protocol, resolved_protocol = load_protocol(protocol_path)
    expected_plan, expected_key = build_study_plan(protocol, resolved_protocol)
    plan_path = root / "study-plan.json"
    key_path = root / "blinding-key.json"
    lock_path = root / "study-lock.json"
    plan = _read_object(plan_path)
    key = _read_object(key_path)
    lock = _read_object(lock_path)
    if plan != expected_plan or key != expected_key:
        raise StudyError("Study plan or blinding key no longer matches the preregistered protocol and seed")
    expected_lock = {
        "schema_version": STUDY_SCHEMA_VERSION,
        "study_id": plan["study_id"],
        "product_protocol_sha256": plan["inputs"]["product_protocol"]["sha256"],
        "source_task_protocol_sha256": plan["inputs"]["source_task_protocol"]["sha256"],
        "reference_sha256": plan["inputs"]["reference"]["sha256"],
        "runner_sha256": plan["inputs"]["runner"]["sha256"],
        "study_plan_sha256": _sha256_file(plan_path),
        "blinding_key_sha256": _sha256_file(key_path),
    }
    if lock != expected_lock:
        raise StudyError("Study lock does not match its plan, key, protocol, task, and reference hashes")
    return {"root": root, "protocol": protocol, "plan": plan, "blinding_key": key, "lock": lock}


def _slot(plan: dict[str, Any], slot_id: str) -> dict[str, Any]:
    matches = [item for item in plan["participant_slots"] if item.get("slot_id") == slot_id]
    if len(matches) != 1:
        raise StudyError(f"Unknown participant slot {slot_id!r}")
    return matches[0]


def _safe_file(
    root: Path,
    value: Mapping[str, Any],
    noun: str,
    *,
    fields: set[str] = FILE_REF_FIELDS,
) -> Path:
    _exact(value, fields, noun)
    relative = value.get("path")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise StudyError(f"{noun} path must be a non-empty study-relative path")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise StudyError(f"{noun} path escapes the study root") from exc
    if not path.is_file() or path.is_symlink():
        raise StudyError(f"{noun} path is not one regular file")
    if not _is_sha256(value.get("sha256")) or _sha256_file(path) != value["sha256"]:
        raise StudyError(f"{noun} SHA-256 does not match its bytes")
    return path


def validate_session_record(
    record: Mapping[str, Any],
    *,
    study: dict[str, Any],
    verify_artifacts: bool = True,
) -> dict[str, Any]:
    _exact(record, SESSION_FIELDS, "session record")
    plan = study["plan"]
    protocol = study["protocol"]
    root = study["root"]
    if record.get("schema_version") != STUDY_SCHEMA_VERSION or record.get("study_id") != plan["study_id"]:
        raise StudyError("Session schema or study id is not bound to this study")
    slot_id = record.get("slot_id")
    if not isinstance(slot_id, str):
        raise StudyError("Session slot_id must be a string")
    slot = _slot(plan, slot_id)
    if record.get("arm") != slot["arm"]:
        raise StudyError("Session arm does not match its preregistered slot")
    status = record.get("status")
    if status not in {"completed", "excluded"}:
        raise StudyError("Session status must be completed or excluded")
    eligibility = record.get("eligibility")
    if not isinstance(eligibility, dict):
        raise StudyError("Session eligibility must be an object")
    _exact(eligibility, ELIGIBILITY_FIELDS, "session eligibility")
    if any(type(value) is not bool for value in eligibility.values()):
        raise StudyError("Eligibility values must be booleans")
    if status == "excluded":
        if record.get("exclusion_reason") not in protocol["human_study"]["allowed_exclusion_reasons"]:
            raise StudyError("Excluded session does not use a preregistered pre-exposure reason")
        return dict(record)
    if record.get("exclusion_reason") is not None:
        raise StudyError("Completed session cannot declare an exclusion reason")
    if eligibility != {
        "consent_recorded": True,
        "new_to_viewspec": True,
        "recent_coding_agent_ui_use": True,
    }:
        raise StudyError("Completed sessions must be consenting target users who are new to ViewSpec")

    timing = record.get("timing_ms")
    if not isinstance(timing, dict):
        raise StudyError("Completed session timing_ms must be an object")
    _exact(timing, TIMING_FIELDS, "session timing_ms")
    for name, value in timing.items():
        _bounded_int(value, f"timing_ms.{name}", maximum=24 * 60 * 60 * 1000)
    _bounded_int(record.get("desirability_rating"), "desirability_rating", minimum=1, maximum=5)

    changes = record.get("changes")
    expected_changes = protocol["human_study"]["task_contract"]["change_step_ids"]
    if not isinstance(changes, list) or len(changes) != len(expected_changes):
        raise StudyError("Completed session must record all three preregistered change attempts")
    for expected_id, change in zip(expected_changes, changes, strict=True):
        if not isinstance(change, dict):
            raise StudyError("Session change record must be an object")
        _exact(change, CHANGE_FIELDS, "session change")
        if change.get("id") != expected_id or type(change.get("completed")) is not bool:
            raise StudyError("Session changes must preserve preregistered order and boolean completion")
        if change["completed"]:
            _bounded_int(change.get("elapsed_ms"), f"change {expected_id} elapsed_ms")
        elif change.get("elapsed_ms") is not None:
            _bounded_int(change.get("elapsed_ms"), f"change {expected_id} elapsed_ms")
        _bounded_int(change.get("stable_criterion_regressions"), "stable criterion regressions")

    health = record.get("health_comprehension")
    if not isinstance(health, dict):
        raise StudyError("health_comprehension must be an object")
    _exact(health, HEALTH_FIELDS, "health comprehension")
    for value in health.values():
        if not isinstance(value, str):
            raise StudyError("Health comprehension answers must be strings")

    handoff = record.get("private_handoff")
    if not isinstance(handoff, dict):
        raise StudyError("private_handoff must be an object")
    _exact(handoff, HANDOFF_FIELDS, "private handoff")
    if type(handoff.get("completed")) is not bool:
        raise StudyError("private_handoff.completed must be a boolean")
    if handoff.get("environment") not in {"matched_baseline", "not_available", "production_canary"}:
        raise StudyError("private_handoff.environment is unsupported")
    if handoff["completed"]:
        _bounded_int(handoff.get("elapsed_ms"), "private handoff elapsed_ms")
        if not _is_sha256(handoff.get("receipt_sha256")):
            raise StudyError("Completed private handoff requires one receipt SHA-256")
    elif handoff.get("elapsed_ms") is not None or handoff.get("receipt_sha256") is not None:
        raise StudyError("Incomplete private handoff cannot claim timing or receipt evidence")

    generated_edits = record.get("generated_output_edits")
    if slot["arm"] == "code-first":
        if generated_edits is not None:
            raise StudyError("Code-first sessions must record generated_output_edits as null")
    else:
        _bounded_int(generated_edits, "generated_output_edits")
    failures = record.get("runtime_failures")
    if not isinstance(failures, list) or any(not isinstance(value, str) or not value for value in failures):
        raise StudyError("runtime_failures must be an array of non-empty strings")

    artifacts = record.get("artifacts")
    if not isinstance(artifacts, dict):
        raise StudyError("Session artifacts must be an object")
    _exact(artifacts, ARTIFACT_FIELDS, "session artifacts")
    if artifacts.get("render_status") not in {"complete", "failed"}:
        raise StudyError("artifacts.render_status must be complete or failed")
    revisions = artifacts.get("source_revisions")
    if not isinstance(revisions, list) or any(not _is_sha256(value) for value in revisions):
        raise StudyError("source_revisions must contain only SHA-256 values")
    if all(change["completed"] for change in changes) and len(set(revisions)) < 4:
        raise StudyError("Three completed changes require four distinct source revisions")
    screenshots = artifacts.get("screenshots")
    if not isinstance(screenshots, list):
        raise StudyError("artifacts.screenshots must be an array")
    observed_screenshots: set[tuple[str, int]] = set()
    for screenshot in screenshots:
        if not isinstance(screenshot, dict):
            raise StudyError("Screenshot evidence must be an object")
        _exact(screenshot, SCREENSHOT_FIELDS, "screenshot evidence")
        target = screenshot.get("target")
        viewport = screenshot.get("viewport")
        pair = (target, viewport)
        if (
            target not in protocol["human_study"]["task_contract"]["required_targets"]
            or viewport not in protocol["human_study"]["task_contract"]["required_viewports"]
            or pair in observed_screenshots
        ):
            raise StudyError("Screenshot target/viewport is unsupported or duplicated")
        observed_screenshots.add(pair)
        if verify_artifacts:
            _safe_file(root, screenshot, "screenshot evidence", fields=SCREENSHOT_FIELDS)
    required_pairs = {
        (target, viewport)
        for target in protocol["human_study"]["task_contract"]["required_targets"]
        for viewport in protocol["human_study"]["task_contract"]["required_viewports"]
    }
    if artifacts["render_status"] == "complete" and observed_screenshots != required_pairs:
        raise StudyError("Complete render evidence must cover every required target and viewport")
    if artifacts["render_status"] == "failed" and screenshots:
        raise StudyError("Failed render evidence must not substitute partial screenshots")
    mechanical = artifacts.get("mechanical_evidence")
    if mechanical is not None:
        if not isinstance(mechanical, dict):
            raise StudyError("mechanical_evidence must be null or one file reference")
        if verify_artifacts:
            _safe_file(root, mechanical, "mechanical evidence")

    attestation = record.get("facilitator_attestation")
    if not isinstance(attestation, dict):
        raise StudyError("facilitator_attestation must be an object")
    _exact(attestation, FACILITATOR_FIELDS, "facilitator attestation")
    if any(type(value) is not bool for value in attestation.values()):
        raise StudyError("Facilitator attestation values must be booleans")
    if not all(attestation.values()):
        raise StudyError("Completed sessions require every facilitator protocol attestation")
    return dict(record)


def _read_records(directory: Path) -> dict[str, dict[str, Any]]:
    if not directory.is_dir():
        return {}
    records: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        record = _read_object(path)
        key = path.stem
        if key in records:
            raise StudyError(f"Duplicate record id {key}")
        records[key] = record
    return records


def _analysis_sessions(study: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    records = _read_records(study["root"] / "sessions")
    plan = study["plan"]
    human = study["protocol"]["human_study"]
    minimum = human["participant_primary_slots_per_arm"]
    selected: dict[str, list[dict[str, Any]]] = {arm: [] for arm in study["protocol"]["arms"]}
    used: set[str] = set()
    errors: list[str] = []
    slots_by_arm = {
        arm: sorted(
            [slot for slot in plan["participant_slots"] if slot["arm"] == arm],
            key=lambda slot: slot["arm_sequence"],
        )
        for arm in selected
    }
    for arm, slots in slots_by_arm.items():
        primary = [slot for slot in slots if slot["cohort"] == "primary"]
        reserve = [slot for slot in slots if slot["cohort"] == "reserve"]
        reserve_index = 0
        for primary_slot in primary:
            active_slot = primary_slot
            while True:
                record = records.get(active_slot["slot_id"])
                if record is None:
                    errors.append(f"missing session record {active_slot['slot_id']}")
                    break
                try:
                    validated = validate_session_record(record, study=study)
                except StudyError as exc:
                    errors.append(f"{active_slot['slot_id']}: {exc}")
                    break
                used.add(active_slot["slot_id"])
                if validated["status"] == "completed":
                    selected[arm].append(validated)
                    break
                if reserve_index >= len(reserve):
                    errors.append(f"{arm} exhausted preregistered reserve participant slots")
                    break
                active_slot = reserve[reserve_index]
                reserve_index += 1
        if len(selected[arm]) != minimum:
            errors.append(f"{arm} has {len(selected[arm])}/{minimum} analyzable sessions")
    known = {slot["slot_id"] for slot in plan["participant_slots"]}
    unknown = sorted(set(records) - known)
    if unknown:
        errors.append(f"unknown participant records: {unknown}")
    unused = sorted(set(records) - used)
    if unused:
        errors.append(f"reserve records were collected without preregistered activation: {unused}")
    return selected, errors


def _strip_png_metadata(source: bytes) -> bytes:
    if not source.startswith(PNG_SIGNATURE):
        raise StudyError("Blinded screenshots must be valid PNG files")
    output = bytearray(PNG_SIGNATURE)
    position = len(PNG_SIGNATURE)
    seen_ihdr = False
    seen_iend = False
    while position < len(source):
        if position + 12 > len(source):
            raise StudyError("PNG chunk is truncated")
        length = struct.unpack(">I", source[position : position + 4])[0]
        end = position + 12 + length
        if end > len(source):
            raise StudyError("PNG chunk length exceeds the file")
        chunk_type = source[position + 4 : position + 8]
        chunk_data = source[position + 8 : position + 8 + length]
        expected_crc = struct.unpack(">I", source[position + 8 + length : end])[0]
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
            raise StudyError("PNG chunk CRC is invalid")
        if chunk_type == b"IHDR":
            seen_ihdr = True
        if chunk_type == b"IEND":
            seen_iend = True
        critical = bool(chunk_type) and (chunk_type[0] & 0x20) == 0
        if critical or chunk_type == b"tRNS":
            output.extend(source[position:end])
        position = end
        if seen_iend:
            break
    if not seen_ihdr or not seen_iend or position != len(source):
        raise StudyError("PNG is missing canonical IHDR/IEND boundaries")
    return bytes(output)


def _tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(value for value in root.rglob("*") if value.is_file()):
        relative = str(path.relative_to(root)).replace("\\", "/")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _packet_html(packet: dict[str, Any]) -> str:
    articles: list[str] = []
    for candidate in packet["candidates"]:
        label = html.escape(candidate["label"])
        if candidate["render_status"] == "failed":
            body = "<p class=missing>No reviewable product was produced for this candidate.</p>"
        else:
            images = "".join(
                f'<figure><img src="{html.escape(image["path"])}" '
                f'alt="Candidate {label}, {html.escape(image["target"])}, '
                f'{image["viewport"]} pixel viewport"><figcaption>'
                f'{html.escape(image["target"].title())} · {image["viewport"]}px</figcaption></figure>'
                for image in candidate["images"]
            )
            body = f"<div class=images>{images}</div>"
        articles.append(f"<article><h2>Candidate {label}</h2>{body}</article>")
    return (
        "<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport "
        "content='width=device-width,initial-scale=1'><title>Blinded product review</title>"
        "<style>body{font:16px/1.5 system-ui;max-width:1200px;margin:0 auto;padding:32px;color:#171717}"
        "header{max-width:760px}.candidates{display:grid;gap:32px}.images{display:grid;gap:16px;"
        "grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr))}article{border-top:1px solid #ddd}"
        "img{display:block;width:100%;height:auto;border:1px solid #bbb;background:white}figcaption{margin-top:6px}"
        ".missing{padding:32px;border:1px solid #bbb;background:#f7f7f7}</style><body><header>"
        "<h1>Which product is better?</h1><p>Judge the delivered interface—not the presumed tool. "
        "Compare hierarchy, interaction clarity, responsive behavior, static/React consistency, and visual finish. "
        "Review every image before recording A, B, or tie.</p></header><main class=candidates>"
        + "".join(articles)
        + "</main></body></html>"
    )


def build_review_packets(study_root: str | Path, protocol_path: str | Path = DEFAULT_PROTOCOL) -> dict[str, Any]:
    study = load_study(study_root, protocol_path)
    selected, errors = _analysis_sessions(study)
    if errors:
        raise StudyError("Cannot blind incomplete participant evidence: " + "; ".join(errors))
    destination = study["root"] / "review-packets"
    if destination.exists():
        raise StudyError("review-packets already exists; never overwrite a packet reviewers may have seen")
    temporary = Path(tempfile.mkdtemp(prefix=".review-packets-", dir=study["root"]))
    packet_key: list[dict[str, Any]] = []
    preference_viewports = study["protocol"]["human_study"]["task_contract"]["preference_viewports"]
    try:
        for mapping in study["blinding_key"]["reviewer_mappings"]:
            reviewer_slot = mapping["reviewer_slot"]
            packet_dir = temporary / reviewer_slot
            packet_dir.mkdir()
            candidates: list[dict[str, Any]] = []
            key_candidates: dict[str, Any] = {}
            for label in ("A", "B"):
                candidate_key = mapping["candidates"][label]
                arm = candidate_key["arm"]
                position = candidate_key["analysis_position"]
                session = selected[arm][position - 1]
                artifacts = session["artifacts"]
                images: list[dict[str, Any]] = []
                if artifacts["render_status"] == "complete":
                    screenshots = {
                        (item["target"], item["viewport"]): item for item in artifacts["screenshots"]
                    }
                    for target in study["protocol"]["human_study"]["task_contract"]["required_targets"]:
                        for viewport in preference_viewports:
                            source_ref = screenshots[(target, viewport)]
                            source_path = _safe_file(
                                study["root"],
                                source_ref,
                                "packet screenshot",
                                fields=SCREENSHOT_FIELDS,
                            )
                            name = f"candidate-{label.lower()}-{target}-{viewport}.png"
                            blinded = _strip_png_metadata(source_path.read_bytes())
                            _write_atomic(packet_dir / name, blinded)
                            images.append(
                                {
                                    "target": target,
                                    "viewport": viewport,
                                    "path": name,
                                    "sha256": _sha256_bytes(blinded),
                                }
                            )
                candidates.append(
                    {
                        "label": label,
                        "render_status": artifacts["render_status"],
                        "images": images,
                    }
                )
                key_candidates[label] = {"arm": arm, "session_slot": session["slot_id"]}
            packet = {
                "schema_version": STUDY_SCHEMA_VERSION,
                "study_id": study["plan"]["study_id"],
                "reviewer_slot": reviewer_slot,
                "blinded": True,
                "candidates": candidates,
            }
            _write_json(packet_dir / "packet.json", packet)
            _write_atomic(packet_dir / "index.html", _packet_html(packet).encode("utf-8"))
            packet_key.append(
                {
                    "reviewer_slot": reviewer_slot,
                    "packet_sha256": _tree_sha256(packet_dir),
                    "candidates": key_candidates,
                }
            )
        temporary.replace(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    analysis_binding = {
        arm: [record["slot_id"] for record in records] for arm, records in selected.items()
    }
    key = {
        "schema_version": STUDY_SCHEMA_VERSION,
        "study_id": study["plan"]["study_id"],
        "study_plan_sha256": study["lock"]["study_plan_sha256"],
        "analysis_sessions": analysis_binding,
        "analysis_sessions_sha256": _sha256_bytes(_canonical_bytes(analysis_binding)),
        "packets": packet_key,
    }
    _write_json(study["root"] / "review-packet-key.json", key)
    (study["root"] / "review-packet-key.json").chmod(0o600)
    return key


def validate_preference_record(record: Mapping[str, Any], *, study: dict[str, Any]) -> dict[str, Any]:
    _exact(record, PREFERENCE_FIELDS, "preference record")
    if record.get("schema_version") != STUDY_SCHEMA_VERSION or record.get("study_id") != study["plan"]["study_id"]:
        raise StudyError("Preference schema or study id is not bound to this study")
    reviewer_slot = record.get("reviewer_slot")
    slots = {item["reviewer_slot"]: item for item in study["plan"]["reviewer_slots"]}
    if reviewer_slot not in slots:
        raise StudyError("Preference reviewer_slot is not preregistered")
    status = record.get("status")
    if status not in {"completed", "excluded"}:
        raise StudyError("Preference status must be completed or excluded")
    if status == "excluded":
        if record.get("exclusion_reason") not in study["protocol"]["human_study"][
            "allowed_reviewer_exclusion_reasons"
        ]:
            raise StudyError("Reviewer exclusion reason was not preregistered")
        return dict(record)
    if record.get("exclusion_reason") is not None:
        raise StudyError("Completed preference cannot declare an exclusion")
    packet_key_path = study["root"] / "review-packet-key.json"
    if not packet_key_path.is_file():
        raise StudyError("Review packet key is missing")
    packet_key = _read_object(packet_key_path)
    packet_matches = [item for item in packet_key.get("packets", []) if item.get("reviewer_slot") == reviewer_slot]
    if len(packet_matches) != 1 or record.get("packet_sha256") != packet_matches[0].get("packet_sha256"):
        raise StudyError("Preference is not bound to the exact blinded packet")
    packet_dir = study["root"] / "review-packets" / str(reviewer_slot)
    if not packet_dir.is_dir() or _tree_sha256(packet_dir) != record["packet_sha256"]:
        raise StudyError("Blinded packet bytes changed after review")
    if record.get("selection") not in {"A", "B", "tie"}:
        raise StudyError("Preference selection must be A, B, or tie")
    ratings = record.get("ratings")
    if not isinstance(ratings, dict):
        raise StudyError("Preference ratings must be an object")
    _exact(ratings, PREFERENCE_RATING_FIELDS, "preference ratings")
    for label, value in ratings.items():
        _bounded_int(value, f"preference rating {label}", minimum=1, maximum=5)
    reasons = record.get("reason_codes")
    allowed = study["protocol"]["human_study"]["preference_reason_codes"]
    if (
        not isinstance(reasons, list)
        or not 1 <= len(reasons) <= 3
        or len(set(reasons)) != len(reasons)
        or any(value not in allowed for value in reasons)
    ):
        raise StudyError("Preference reason_codes must contain one to three unique allowed values")
    attestation = record.get("reviewer_attestation")
    if not isinstance(attestation, dict):
        raise StudyError("reviewer_attestation must be an object")
    _exact(attestation, REVIEWER_ATTESTATION_FIELDS, "reviewer attestation")
    if attestation != {"no_arm_identity_seen": True, "reviewed_both_targets": True}:
        raise StudyError("Completed preference requires both blinding attestations")
    return dict(record)


def _analysis_preferences(study: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    records = _read_records(study["root"] / "preferences")
    slots = study["plan"]["reviewer_slots"]
    primary = [slot for slot in slots if slot["cohort"] == "primary"]
    reserve = {
        slot["reserve_for"]: slot for slot in slots if slot["cohort"] == "reserve"
    }
    selected: list[dict[str, Any]] = []
    used: set[str] = set()
    errors: list[str] = []
    for primary_slot in primary:
        active = primary_slot
        while True:
            slot_id = active["reviewer_slot"]
            record = records.get(slot_id)
            if record is None:
                errors.append(f"missing preference record {slot_id}")
                break
            try:
                validated = validate_preference_record(record, study=study)
            except StudyError as exc:
                errors.append(f"{slot_id}: {exc}")
                break
            used.add(slot_id)
            if validated["status"] == "completed":
                selected.append(validated)
                break
            replacement = reserve.get(primary_slot["reviewer_slot"])
            if replacement is None or active["cohort"] == "reserve":
                errors.append(f"no preregistered reserve for {primary_slot['reviewer_slot']}")
                break
            active = replacement
    minimum = study["protocol"]["human_study"]["blinded_comparisons"]
    if len(selected) != minimum:
        errors.append(f"blinded preferences have {len(selected)}/{minimum} analyzable comparisons")
    known = {slot["reviewer_slot"] for slot in slots}
    unknown = sorted(set(records) - known)
    if unknown:
        errors.append(f"unknown reviewer records: {unknown}")
    unused = sorted(set(records) - used)
    if unused:
        errors.append(f"reserve reviewer records were collected without activation: {unused}")
    return selected, errors


def _wilson_interval(successes: int, total: int, confidence: float) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    z = statistics.NormalDist().inv_cdf(0.5 + confidence / 2)
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * ((proportion * (1 - proportion) / total + z * z / (4 * total * total)) ** 0.5)
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def _rate(values: Iterable[bool]) -> tuple[int, int, float | None]:
    outcomes = list(values)
    passed = sum(outcomes)
    return passed, len(outcomes), passed / len(outcomes) if outcomes else None


def _session_metrics(records: list[dict[str, Any]], protocol: dict[str, Any], arm: str) -> dict[str, Any]:
    gates = protocol["human_gates"]
    checked_answer = "source_artifact_and_declared_contract_health"
    unproven_answer = "human_desirability_visual_parity_and_production_behavior"
    metrics = {
        "first_value_within_one_minute": _rate(
            record["timing_ms"]["first_value"] < 60_000 for record in records
        ),
        "first_value_desirable": _rate(
            record["desirability_rating"] >= gates["minimum_desirability_rating"] for record in records
        ),
        "three_change_completion": _rate(
            all(change["completed"] for change in record["changes"])
            and sum(change["stable_criterion_regressions"] for change in record["changes"]) == 0
            for record in records
        ),
        "health_comprehension": _rate(
            record["health_comprehension"]
            == {"checked_scope": checked_answer, "unproven_scope": unproven_answer}
            for record in records
        ),
        "private_handoff_within_five_minutes": _rate(
            record["private_handoff"]["completed"]
            and record["private_handoff"]["environment"] == "production_canary"
            and record["private_handoff"]["elapsed_ms"] < 300_000
            for record in records
        ),
        "runtime_clean": _rate(not record["runtime_failures"] for record in records),
    }
    if arm == "viewspec-studio":
        metrics["generated_output_untouched"] = _rate(
            record["generated_output_edits"] == 0 for record in records
        )
    return {
        name: {"passed": value[0], "total": value[1], "rate": value[2]}
        for name, value in metrics.items()
    }


def _canary_status(study: dict[str, Any]) -> tuple[bool, list[str]]:
    path = study["root"] / "production-canary-evidence.json"
    if not path.is_file():
        return False, ["production-canary-evidence.json is missing"]
    try:
        result = evaluate_canary(path)
    except CanaryError as exc:
        return False, [str(exc)]
    return result["passed"] is True, []


def summarize_study(study_root: str | Path, protocol_path: str | Path = DEFAULT_PROTOCOL) -> dict[str, Any]:
    study = load_study(study_root, protocol_path)
    sessions, session_errors = _analysis_sessions(study)
    preferences, preference_errors = _analysis_preferences(study)
    integrity_errors = [*session_errors, *preference_errors]
    gate_names = (
        "data_integrity",
        "minimum_sessions",
        "first_value_within_one_minute",
        "first_value_desirable",
        "three_change_completion",
        "health_comprehension",
        "private_handoff_within_five_minutes",
        "generated_output_untouched",
        "minimum_blinded_comparisons",
        "blinded_preference_rate",
        "blinded_preference_confidence_above_chance",
        "production_private_review_canary",
    )
    if integrity_errors:
        return {
            "schema_version": STUDY_SCHEMA_VERSION,
            "study_id": study["plan"]["study_id"],
            "study_plan_sha256": study["lock"]["study_plan_sha256"],
            "analysis_locked": True,
            "arm_metrics": None,
            "preference": None,
            "production_canary": {"passed": False, "errors": ["not checked before analysis lock"]},
            "integrity_errors": integrity_errors,
            "gates": {name: False for name in gate_names},
            "human_gate_pass": False,
            "full_product_pass": False,
            "full_product_status": "human_data_incomplete_or_invalid_analysis_remains_blinded",
        }
    packet_key_path = study["root"] / "review-packet-key.json"
    packet_key = _read_object(packet_key_path) if packet_key_path.is_file() else {"packets": []}
    packet_by_slot = {item["reviewer_slot"]: item for item in packet_key.get("packets", [])}
    preferred = 0
    ties = 0
    for preference in preferences:
        selection = preference["selection"]
        if selection == "tie":
            ties += 1
            continue
        packet = packet_by_slot.get(preference["reviewer_slot"])
        if packet and packet["candidates"][selection]["arm"] == "viewspec-studio":
            preferred += 1
    confidence = study["protocol"]["human_gates"]["preference_confidence_level"]
    lower, upper = _wilson_interval(preferred, len(preferences), confidence)
    preference_rate = preferred / len(preferences) if preferences else None
    arm_metrics = {
        arm: _session_metrics(records, study["protocol"], arm) for arm, records in sessions.items()
    }
    gates = study["protocol"]["human_gates"]
    primary = arm_metrics.get("viewspec-studio", {})
    canary_pass, canary_errors = _canary_status(study)
    gate_results = {
        "data_integrity": not integrity_errors,
        "minimum_sessions": all(len(value) >= gates["minimum_sessions_per_arm"] for value in sessions.values()),
        "first_value_within_one_minute": primary.get("first_value_within_one_minute", {}).get("rate")
        is not None
        and primary["first_value_within_one_minute"]["rate"]
        >= gates["minimum_first_value_within_one_minute_rate"],
        "first_value_desirable": primary.get("first_value_desirable", {}).get("rate") is not None
        and primary["first_value_desirable"]["rate"] >= gates["minimum_first_value_desirable_rate"],
        "three_change_completion": primary.get("three_change_completion", {}).get("rate") is not None
        and primary["three_change_completion"]["rate"] >= gates["minimum_three_change_completion_rate"],
        "health_comprehension": primary.get("health_comprehension", {}).get("rate") is not None
        and primary["health_comprehension"]["rate"] >= gates["minimum_health_comprehension_rate"],
        "private_handoff_within_five_minutes": primary.get(
            "private_handoff_within_five_minutes", {}
        ).get("rate")
        is not None
        and primary["private_handoff_within_five_minutes"]["rate"]
        >= gates["minimum_private_handoff_within_five_minutes_rate"],
        "generated_output_untouched": primary.get("generated_output_untouched", {}).get("rate") == 1.0,
        "minimum_blinded_comparisons": len(preferences) >= gates["minimum_blinded_comparisons"],
        "blinded_preference_rate": preference_rate is not None
        and preference_rate >= gates["minimum_blinded_preference_rate"],
        "blinded_preference_confidence_above_chance": lower is not None
        and lower > gates["preference_chance_rate"],
        "production_private_review_canary": canary_pass,
    }
    human_gate_pass = all(gate_results.values())
    return {
        "schema_version": STUDY_SCHEMA_VERSION,
        "study_id": study["plan"]["study_id"],
        "study_plan_sha256": study["lock"]["study_plan_sha256"],
        "analysis_locked": False,
        "arm_metrics": arm_metrics,
        "preference": {
            "viewspec_preferred": preferred,
            "ties": ties,
            "total": len(preferences),
            "rate": preference_rate,
            "confidence_level": confidence,
            "wilson_interval": {"lower": lower, "upper": upper},
            "chance_rate": gates["preference_chance_rate"],
        },
        "production_canary": {"passed": canary_pass, "errors": canary_errors},
        "integrity_errors": integrity_errors,
        "gates": gate_results,
        "human_gate_pass": human_gate_pass,
        "full_product_pass": False,
        "full_product_status": (
            "human_gate_passed_but_requires_combined_mechanical_and_assurance_audit"
            if human_gate_pass
            else "human_value_evidence_incomplete_or_below_threshold"
        ),
    }


def _session_template(study: dict[str, Any], slot_id: str) -> dict[str, Any]:
    slot = _slot(study["plan"], slot_id)
    changes = study["protocol"]["human_study"]["task_contract"]["change_step_ids"]
    return {
        "schema_version": STUDY_SCHEMA_VERSION,
        "study_id": study["plan"]["study_id"],
        "slot_id": slot_id,
        "arm": slot["arm"],
        "status": "completed",
        "exclusion_reason": None,
        "eligibility": {
            "consent_recorded": True,
            "new_to_viewspec": True,
            "recent_coding_agent_ui_use": True,
        },
        "timing_ms": {"first_value": 0, "three_changes": 0, "private_handoff": 0},
        "desirability_rating": 1,
        "changes": [
            {"id": change_id, "completed": False, "elapsed_ms": None, "stable_criterion_regressions": 0}
            for change_id in changes
        ],
        "health_comprehension": {"checked_scope": "", "unproven_scope": ""},
        "private_handoff": {
            "completed": False,
            "environment": "not_available",
            "elapsed_ms": None,
            "receipt_sha256": None,
        },
        "generated_output_edits": None if slot["arm"] == "code-first" else 0,
        "runtime_failures": [],
        "artifacts": {
            "render_status": "failed",
            "source_revisions": [],
            "screenshots": [],
            "mechanical_evidence": None,
        },
        "facilitator_attestation": {
            "artifact_capture_complete": False,
            "no_coaching_beyond_script": False,
            "product_only_capture": False,
            "timings_observed": False,
        },
    }


def _preference_template(study: dict[str, Any], reviewer_slot: str) -> dict[str, Any]:
    key_path = study["root"] / "review-packet-key.json"
    packet_sha = ""
    if key_path.is_file():
        key = _read_object(key_path)
        matches = [item for item in key.get("packets", []) if item.get("reviewer_slot") == reviewer_slot]
        if len(matches) == 1:
            packet_sha = matches[0]["packet_sha256"]
    return {
        "schema_version": STUDY_SCHEMA_VERSION,
        "study_id": study["plan"]["study_id"],
        "reviewer_slot": reviewer_slot,
        "status": "completed",
        "exclusion_reason": None,
        "packet_sha256": packet_sha,
        "selection": "tie",
        "ratings": {"A": 1, "B": 1},
        "reason_codes": ["content_hierarchy"],
        "reviewer_attestation": {"no_arm_identity_seen": False, "reviewed_both_targets": False},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize = subparsers.add_parser("init")
    initialize.add_argument("--out", type=Path, required=True)
    packets = subparsers.add_parser("build-review-packets")
    packets.add_argument("--study", type=Path, required=True)
    session_template = subparsers.add_parser("session-template")
    session_template.add_argument("--study", type=Path, required=True)
    session_template.add_argument("--slot", required=True)
    preference_template = subparsers.add_parser("preference-template")
    preference_template.add_argument("--study", type=Path, required=True)
    preference_template.add_argument("--slot", required=True)
    summarize = subparsers.add_parser("summarize")
    summarize.add_argument("--study", type=Path, required=True)
    summarize.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.command == "init":
        result = initialize_study(args.protocol, args.out)
        print(_canonical_bytes(result).decode("utf-8"), end="")
        return 0
    study = load_study(args.study, args.protocol)
    if args.command == "build-review-packets":
        result = build_review_packets(args.study, args.protocol)
        print(_canonical_bytes(result).decode("utf-8"), end="")
        return 0
    if args.command == "session-template":
        print(_canonical_bytes(_session_template(study, args.slot)).decode("utf-8"), end="")
        return 0
    if args.command == "preference-template":
        print(_canonical_bytes(_preference_template(study, args.slot)).decode("utf-8"), end="")
        return 0
    report = summarize_study(args.study, args.protocol)
    if args.out is not None:
        _write_json(args.out, report)
    print(_canonical_bytes(report).decode("utf-8"), end="")
    return 0 if report["human_gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
