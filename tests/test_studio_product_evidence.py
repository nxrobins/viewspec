from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.check_studio_product_evidence import PROTOCOL, evaluate


def _evidence() -> dict[str, object]:
    changes = []
    operations = (
        ("set_binding_presentation", "queue/status", "value", "badge"),
        ("replace_fixture_scalar", "jobs/j-2/status", "queued", "ready"),
        ("replace_semantic_attr", "queue/title", "Jobs", "Priority jobs"),
    )
    for index, (operation, target, before, after) in enumerate(operations, start=1):
        change = {
            "index": index,
            "operation": operation,
            "target": target,
            "before": before,
            "after": after,
            "source_sha256": str(index) * 64,
            "proposal_ms": 500,
            "approval_to_revision_ms": 2500,
            "revision": index + 1,
            "viewports": [390, 768, 1440],
            "targets": ["html-tailwind-app", "react-tailwind-app"],
        }
        if index == 1:
            change.update(
                {
                    "coherence_detector": "Status sits 24 px farther right in React.",
                    "coherence_recovered": True,
                }
            )
        changes.append(change)
    return {
        "schema_version": 1,
        "status": "passed",
        "journey": "brief-to-three-approved-semantic-changes",
        "initial_source_sha256": "0" * 64,
        "final_source_sha256": "3" * 64,
        "creation_ready_ms": 3000,
        "studio_journey_ms": 12000,
        "change_count": 3,
        "changes": changes,
        "generated_output_edits": 0,
        "static_react_target_pass_rate": 1,
        "responsive_viewports": [390, 768, 1440],
        "human_desirability": "not_measured",
        "private_review": "separately_proven",
        "runtime_failures": [],
    }


def _write(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "studio-product-journey-evidence.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_mechanical_product_evidence_passes_without_overclaiming_full_product(tmp_path: Path) -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    result = evaluate(_write(tmp_path, _evidence()), protocol)

    assert result["mechanical_pass"] is True
    assert result["full_product_pass"] is False
    assert result["full_product_status"] == "awaiting_blinded_human_study_and_production_private_review_canary"
    assert all(result["checks"].values())


@pytest.mark.parametrize(
    ("mutate", "failed_check"),
    [
        (lambda value: value.update({"creation_ready_ms": 60000}), "creation_under_one_minute"),
        (lambda value: value.update({"change_count": 2}), "three_approved_changes"),
        (lambda value: value.update({"generated_output_edits": 1}), "generated_output_untouched"),
        (lambda value: value.update({"runtime_failures": ["console error"]}), "runtime_clean"),
        (lambda value: value.update({"journey": "partial-demo"}), "reported_pass"),
        (lambda value: value.update({"studio_journey_ms": 180000}), "bounded_turn_timings"),
        (
            lambda value: value["changes"][0].update({"operation": "edit_generated_css"}),
            "meaningful_semantic_deltas",
        ),
        (
            lambda value: value["changes"][1].update({"revision": 2}),
            "three_approved_changes",
        ),
        (
            lambda value: value["changes"][1].update({"viewports": [390, 1440]}),
            "responsive_target_coherence",
        ),
        (
            lambda value: value["changes"][2].update({"source_sha256": "2" * 64}),
            "source_revision_continuity",
        ),
    ],
)
def test_mechanical_product_evidence_rejects_incomplete_or_regressed_journeys(
    tmp_path: Path,
    mutate,
    failed_check: str,
) -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    payload = _evidence()
    mutate(payload)

    result = evaluate(_write(tmp_path, payload), protocol)

    assert result["mechanical_pass"] is False
    assert result["checks"][failed_check] is False


def test_product_evidence_shape_is_closed(tmp_path: Path) -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    payload = _evidence()
    payload["claim"] = "desirable"

    with pytest.raises(ValueError, match="unsupported evidence shape"):
        evaluate(_write(tmp_path, payload), protocol)


def test_product_change_evidence_shape_is_closed(tmp_path: Path) -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    payload = _evidence()
    payload["changes"][0]["unknown"] = True

    with pytest.raises(ValueError, match="unsupported change record"):
        evaluate(_write(tmp_path, payload), protocol)


def test_product_change_coherence_record_is_complete(tmp_path: Path) -> None:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    payload = _evidence()
    payload["changes"][0].pop("coherence_recovered")

    with pytest.raises(ValueError, match="incomplete coherence record"):
        evaluate(_write(tmp_path, payload), protocol)
