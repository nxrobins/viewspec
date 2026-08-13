"""Build the public, static Proof Explorer from retained refinement evidence."""

from __future__ import annotations

import hashlib
import json
import shutil
import struct
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "conformance" / "refinement"
DEFAULT_OUTPUT = ROOT / "demos" / "proof-explorer"

VIEWPORTS = (
    {"id": "mobile", "label": "Mobile", "width": 390, "height": 844},
    {"id": "tablet", "label": "Tablet", "width": 768, "height": 1024},
    {"id": "desktop", "label": "Desktop", "width": 1440, "height": 1000},
)

CASE_COPY = {
    "app-detail": ("Incident detail", "A focused record surface with one clear operational action."),
    "app-queue": ("Incident queue", "Comparable incident rows with a selected-item action."),
    "collection-states": ("Collection states", "Empty, loading, and refresh states that remain distinct."),
    "data-dense-dashboard": ("Data-dense dashboard", "Summary, direction, and priority signals in one scan."),
    "dense-operational-console": ("Operational console", "Failure-first hierarchy with dense recovery context."),
    "interactive-form": ("Interactive form", "Labelled fields and a clear submit path before interaction."),
    "landing-intent": ("Product landing page", "A complete product narrative compiled from semantic intent."),
    "multi-step-workflow": ("Multi-step workflow", "A staged flow with visible progress and next actions."),
    "outcome-states": ("Outcome states", "Success and failure outcomes with useful recovery paths."),
    "settings": ("Settings", "Grouped configuration with readable controls at every viewport."),
}


def _load(name: str) -> dict[str, Any]:
    return json.loads((SOURCE / name).read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError(f"{path} is not a PNG")
    return struct.unpack(">II", data[16:24])


def _source_identity(name: str) -> dict[str, Any]:
    path = SOURCE / name
    return {
        "path": f"conformance/refinement/{name}",
        "sha256": _sha256(path),
    }


def build(output_dir: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    verification = _load("verification-report-v2.json")
    scorecard = _load("scorecard-v2.json")
    corrections = _load("correction-proof-v1.json")
    gate_status = _load("gate-status-v1.json")
    negative_corpus = _load("negative-corpus.json")

    verification_by_id = {case["id"]: case for case in verification["cases"]}
    scorecard_by_id = {case["id"]: case for case in scorecard["cases"]}
    corrections_by_id = {case["case_id"]: case for case in corrections["cases"]}
    case_ids = tuple(scorecard_by_id)
    if not (
        set(case_ids)
        == set(verification_by_id)
        == set(corrections_by_id)
        == set(CASE_COPY)
    ):
        raise ValueError("Proof Explorer source case sets do not match")
    if verification["case_count"] != len(case_ids) or not verification["ok"]:
        raise ValueError("Proof Explorer requires a complete healthy verification report")
    if corrections["case_count"] != len(case_ids) or not corrections["ok"]:
        raise ValueError("Proof Explorer requires a complete healthy correction proof")
    if gate_status["status"] != "passed" or any(gate["status"] != "passed" for gate in gate_status["gates"]):
        raise ValueError("Proof Explorer requires every declared refinement gate to pass")

    screenshots_dir = output_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    cases: list[dict[str, Any]] = []

    for case_id in case_ids:
        verified = verification_by_id[case_id]
        scored = scorecard_by_id[case_id]
        corrected = corrections_by_id[case_id]
        if not (verified["ok"] and scored["pass"] and corrected["semantic_diff_ok"]):
            raise ValueError(f"Proof Explorer case {case_id!r} is not healthy")
        if not (
            verified["artifact_sha256"]
            == scored["artifact_sha256"]
            and verified["verification_id"] == scored["verification_id"]
        ):
            raise ValueError(f"Proof Explorer case {case_id!r} has evidence identity drift")

        evidence_by_path = {item["path"]: item for item in verified["evidence"]}
        screenshots: dict[str, dict[str, Any]] = {}
        case_screenshot_dir = screenshots_dir / case_id
        case_screenshot_dir.mkdir(parents=True, exist_ok=True)
        for viewport in VIEWPORTS:
            viewport_id = str(viewport["id"])
            evidence_item = evidence_by_path[f"evidence/{viewport_id}.png"]
            source_image = SOURCE / "evidence-v2" / case_id / f"{viewport_id}.png"
            if _sha256(source_image) != evidence_item["sha256"]:
                raise ValueError(f"Proof Explorer screenshot hash drift: {case_id}/{viewport_id}")
            image_width, image_height = _png_size(source_image)
            if image_width != viewport["width"] or image_height < viewport["height"]:
                raise ValueError(f"Proof Explorer screenshot viewport drift: {case_id}/{viewport_id}")
            destination = case_screenshot_dir / source_image.name
            shutil.copyfile(source_image, destination)
            screenshots[viewport_id] = {
                "path": f"screenshots/{case_id}/{viewport_id}.png",
                "sha256": evidence_item["sha256"],
                "bytes": evidence_item["bytes"],
                "width": image_width,
                "height": image_height,
                "capture_kind": (
                    "viewport" if image_height == viewport["height"] else "full_page"
                ),
            }

        inverse_operations = corrected["receipt"]["inverse_patch"]["operations"]
        if len(inverse_operations) != 1:
            raise ValueError(f"Proof Explorer case {case_id!r} must have exactly one correction operation")
        inverse = inverse_operations[0]
        label, description = CASE_COPY[case_id]
        cases.append(
            {
                "id": case_id,
                "label": label,
                "description": description,
                "status": verified["actual_status"],
                "review": scored["review"],
                "scores": scored["scores"],
                "mean_score": scored["mean"],
                "critical_issues": scored["critical_issues"],
                "artifacts": {
                    "source_sha256": verified["source_sha256"],
                    "intent_sha256": verified["intent_sha256"],
                    "artifact_sha256": verified["artifact_sha256"],
                    "plan_sha256": verified["plan_sha256"],
                    "verification_id": verified["verification_id"],
                },
                "evidence": {
                    "roles": verified["evidence_roles"],
                    "item_count": len(verified["evidence"]),
                    "screenshots": screenshots,
                },
                "correction": {
                    "source_kind": corrected["source_kind"],
                    "operation": inverse["op"],
                    "target": corrected["target"],
                    "before": inverse["value"],
                    "after": inverse["old_value"],
                    "base_source_sha256": corrected["base_source_sha256"],
                    "candidate_source_sha256": corrected["candidate_source_sha256"],
                    "patch_id": corrected["patch_id"],
                    "preview_id": corrected["preview_id"],
                    "receipt_id": corrected["receipt"]["receipt_id"],
                    "receipt_status": corrected["receipt"]["status"],
                    "compile_status": corrected["compile_check"]["status"],
                    "verification_status": corrected["verification"]["status"],
                    "verification_target": corrected["verification"]["target"],
                },
            }
        )

    contract = {
        "schema_version": 1,
        "kind": "viewspec_public_proof_explorer",
        "scope": (
            "Retained fixed-corpus evidence for supported brief families. This is not certification "
            "of arbitrary briefs, host applications, accessibility, or pixel-perfect fidelity."
        ),
        "sources": [
            _source_identity("verification-report-v2.json"),
            _source_identity("scorecard-v2.json"),
            _source_identity("correction-proof-v1.json"),
            _source_identity("gate-status-v1.json"),
            _source_identity("negative-corpus.json"),
        ],
        "summary": {
            "case_count": len(cases),
            "conformant_count": sum(case["status"] == "conformant" for case in cases),
            "first_compile_pass_count": scorecard["summary"]["first_compile_pass_count"],
            "critical_issue_count": scorecard["summary"]["critical_issue_count"],
            "verified_correction_count": scorecard["correction_proof"]["verified_preview_count"],
            "applied_receipt_count": scorecard["correction_proof"]["applied_receipt_count"],
            "passed_gate_count": len(gate_status["gates"]),
            "negative_control_count": len(negative_corpus["cases"]),
        },
        "viewports": list(VIEWPORTS),
        "quality_dimensions": scorecard["rubric"]["dimensions"],
        "gates": gate_status["gates"],
        "negative_controls": negative_corpus["cases"],
        "cases": cases,
    }
    (output_dir / "proof-data.json").write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return contract


if __name__ == "__main__":
    result = build()
    print(
        f"Built Proof Explorer data: {result['summary']['case_count']} cases, "
        f"{result['summary']['passed_gate_count']} gates, "
        f"{result['summary']['negative_control_count']} negative controls."
    )
