#!/usr/bin/env python3
"""Validate retained Studio journey evidence against the mechanical product gate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any


PROTOCOL = Path(__file__).resolve().parents[1] / "conformance/studio-product-v1/protocol.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_FIELDS = {
    "schema_version",
    "status",
    "journey",
    "initial_source_sha256",
    "final_source_sha256",
    "creation_ready_ms",
    "studio_journey_ms",
    "change_count",
    "changes",
    "generated_output_edits",
    "static_react_target_pass_rate",
    "responsive_viewports",
    "human_desirability",
    "private_review",
    "runtime_failures",
}
CHANGE_FIELDS = {
    "index",
    "operation",
    "target",
    "before",
    "after",
    "source_sha256",
    "proposal_ms",
    "approval_to_revision_ms",
    "revision",
    "viewports",
    "targets",
}


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _discover(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted(root.glob("**/studio-product-journey-evidence.json"))


def evaluate(path: Path, protocol: dict[str, Any]) -> dict[str, Any]:
    evidence = _read_object(path)
    gates = protocol["mechanical_gates"]
    if set(evidence) != EVIDENCE_FIELDS or evidence.get("schema_version") != 1:
        raise ValueError(f"{path} has an unsupported evidence shape")
    changes = evidence.get("changes")
    if not isinstance(changes, list):
        raise ValueError(f"{path} changes must be an array")
    for change in changes:
        if not isinstance(change, dict) or set(change) != CHANGE_FIELDS:
            raise ValueError(f"{path} contains an unsupported change record")
    required_viewports = gates["required_viewports"]
    required_targets = gates["required_targets"]
    hashes = [evidence.get("initial_source_sha256")]
    hashes.extend(change.get("source_sha256") for change in changes)
    checks = {
        "reported_pass": evidence.get("status") == "passed" and evidence.get("journey") == gates["journey_id"],
        "creation_under_one_minute": type(evidence.get("creation_ready_ms")) is int
        and 0 <= evidence["creation_ready_ms"] < gates["maximum_creation_ready_ms"],
        "three_approved_changes": evidence.get("change_count") == len(changes)
        and len(changes) >= gates["minimum_approved_changes"]
        and [change.get("index") for change in changes] == list(range(1, len(changes) + 1))
        and [change.get("revision") for change in changes] == list(range(2, len(changes) + 2)),
        "responsive_target_coherence": evidence.get("responsive_viewports") == required_viewports
        and all(change.get("viewports") == required_viewports for change in changes)
        and all(change.get("targets") == required_targets for change in changes)
        and evidence.get("static_react_target_pass_rate") == gates["minimum_target_pass_rate"],
        "meaningful_semantic_deltas": all(
            change.get("operation") in gates["allowed_change_operations"]
            and isinstance(change.get("target"), str)
            and bool(change["target"].strip())
            and change.get("before") != change.get("after")
            for change in changes
        ),
        "bounded_turn_timings": type(evidence.get("studio_journey_ms")) is int
        and 0 <= evidence["studio_journey_ms"] < gates["maximum_three_change_journey_ms"]
        and all(
            type(change.get(field)) is int and 0 <= change[field] < gates["maximum_change_turn_ms"]
            for change in changes
            for field in ("proposal_ms", "approval_to_revision_ms")
        ),
        "source_revision_continuity": all(isinstance(value, str) and SHA256_RE.fullmatch(value) for value in hashes)
        and len(set(hashes)) >= gates["minimum_distinct_source_revisions"]
        and evidence.get("final_source_sha256") == hashes[-1],
        "generated_output_untouched": evidence.get("generated_output_edits")
        == gates["maximum_generated_output_edits"],
        "runtime_clean": isinstance(evidence.get("runtime_failures"), list)
        and len(evidence["runtime_failures"]) <= gates["maximum_runtime_failures"],
        "human_scope_honest": evidence.get("human_desirability") == "not_measured",
        "private_review_scope_honest": evidence.get("private_review") == "separately_proven",
    }
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "checks": checks,
        "mechanical_pass": all(checks.values()),
        "full_product_pass": False,
        "full_product_status": "awaiting_blinded_human_study_and_production_private_review_canary",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path, help="Evidence file or test-results root.")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    protocol = _read_object(PROTOCOL)
    paths = _discover(args.evidence)
    if not paths:
        raise SystemExit("No Studio product-journey evidence found.")
    results = [evaluate(path, protocol) for path in paths]
    report = {
        "schema_version": 1,
        "protocol_id": protocol["id"],
        "mechanical_pass": all(result["mechanical_pass"] for result in results),
        "full_product_pass": False,
        "evidence": results,
    }
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["mechanical_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
