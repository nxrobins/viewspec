from __future__ import annotations

import json
from pathlib import Path

from viewspec.conformance import load_conformance_corpus
from viewspec.intent_patch import (
    INTENT_PATCH_CONTRACT_PROFILE,
    apply_intent_patch_file,
    preview_intent_patch,
    source_sha256,
)


def _corrections() -> list[dict[str, object]]:
    return json.loads(
        Path("conformance/refinement/corrections.json").read_text(encoding="utf-8")
    )["corrections"]


def _patch(source_text: str, correction: dict[str, object]) -> dict[str, object]:
    operation = {
        "op": "replace_semantic_attr",
        "node_id": correction["node_id"],
        "attr": correction["attr"],
        "old_value": correction["old_value"],
        "value": correction["value"],
    }
    if correction["source_kind"] == "app_bundle":
        operation["screen_id"] = correction["screen_id"]
    return {
        "schema_version": 1,
        "contract_profile": INTENT_PATCH_CONTRACT_PROFILE,
        "source_kind": correction["source_kind"],
        "base_source_sha256": source_sha256(source_text),
        "operations": [operation],
        "evidence_refs": [f"scorecard:{correction['case_id']}"],
    }


def _assert_only_target_node_changed(
    semantic_diff: dict[str, object], correction: dict[str, object]
) -> None:
    expected_node = correction["node_id"]
    if correction["source_kind"] == "intent_bundle":
        changes = semantic_diff["changes"]
        assert changes["substrate_nodes"]["changed"] == [expected_node]
        for section, section_changes in changes.items():
            if section == "substrate_nodes":
                assert section_changes["added"] == []
                assert section_changes["removed"] == []
                continue
            assert section_changes == {"added": [], "removed": [], "changed": []}
        return

    screen_id = correction["screen_id"]
    changes = semantic_diff["changes"]
    assert changes["screens"]["changed"] == [screen_id]
    for section, section_changes in changes.items():
        if section == "screens":
            assert section_changes["added"] == []
            assert section_changes["removed"] == []
            continue
        assert section_changes == {"added": [], "removed": [], "changed": []}
    screen_changes = semantic_diff["screen_intent_diffs"][screen_id]["changes"]
    assert screen_changes["substrate_nodes"]["changed"] == [expected_node]
    for section, section_change in screen_changes.items():
        if section == "substrate_nodes":
            assert section_change["added"] == []
            assert section_change["removed"] == []
            continue
        assert section_change == {"added": [], "removed": [], "changed": []}


def test_every_refinement_brief_has_a_bounded_semantic_correction_and_receipt(tmp_path):
    corpus = load_conformance_corpus("conformance/verification/corpus.json")
    cases = {case.id: case for case in corpus.cases}
    corrections = _corrections()

    assert [item["case_id"] for item in corrections] == sorted(cases)
    for correction in corrections:
        case_id = str(correction["case_id"])
        source_text = cases[case_id].source_path.read_text(encoding="utf-8")
        patch = _patch(source_text, correction)
        preview = preview_intent_patch(source_text, patch)

        assert preview.compile_check["status"] == "passed"
        assert preview.semantic_diff["ok"] is True
        _assert_only_target_node_changed(preview.semantic_diff, correction)

        source_path = tmp_path / f"{case_id}.source.json"
        patch_path = tmp_path / f"{case_id}.intentpatch.json"
        source_path.write_text(source_text, encoding="utf-8")
        patch_path.write_text(json.dumps(patch, sort_keys=True), encoding="utf-8")
        receipt = apply_intent_patch_file(
            source_path,
            patch_path,
            approval_token=preview.approval_token,
        )

        assert receipt.status == "applied"
        assert receipt.receipt_path.is_file()
        assert source_sha256(source_path.read_text(encoding="utf-8")) == preview.candidate_source_sha256

        candidate = json.loads(source_path.read_text(encoding="utf-8"))
        if correction["source_kind"] == "app_bundle":
            screen = next(
                item for item in candidate["screens"] if item["id"] == correction["screen_id"]
            )
            nodes = screen["intent_bundle"]["substrate"]["nodes"]
        else:
            nodes = candidate["substrate"]["nodes"]
        assert nodes[correction["node_id"]]["attrs"][correction["attr"]] == correction["value"]


def test_checked_in_correction_proof_covers_verified_previews_and_applied_receipts():
    corpus = load_conformance_corpus("conformance/verification/corpus.json")
    cases = {case.id: case for case in corpus.cases}
    corrections = _corrections()
    report = json.loads(
        Path("conformance/refinement/correction-proof-v1.json").read_text(encoding="utf-8")
    )

    assert report["ok"] is True
    assert report["case_count"] == 10
    assert [case["case_id"] for case in report["cases"]] == [
        correction["case_id"] for correction in corrections
    ]
    for case, correction in zip(report["cases"], corrections, strict=True):
        source_text = cases[case["case_id"]].source_path.read_text(encoding="utf-8")
        assert case["base_source_sha256"] == source_sha256(source_text)
        assert case["semantic_diff_ok"] is True
        assert case["compile_check"]["status"] == "passed"
        assert case["verification"]["status"] == "conformant"
        assert case["receipt"]["status"] == "applied"
        assert case["receipt"]["preview_id"] == case["preview_id"]
        assert case["receipt"]["patch_id"] == case["patch_id"]
        assert case["receipt"]["candidate_source_sha256"] == case["candidate_source_sha256"]
        assert case["target"]["node_id"] == correction["node_id"]
        assert case["target"]["attr"] == correction["attr"]
