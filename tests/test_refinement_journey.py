from __future__ import annotations

import importlib
import json
from pathlib import Path

from viewspec.cli import main as cli_main
from viewspec.intent_patch import (
    INTENT_PATCH_CONTRACT_PROFILE,
    apply_intent_patch_file,
    preview_intent_patch,
    source_sha256,
)
from viewspec.local_tools import check_artifact_dir


def _passing_host_report(*_args, **_kwargs):
    return {
        "ok": True,
        "assertions": {
            "action_count": 0,
            "aesthetic_layout_assertion_count": 0,
            "aesthetic_profile_assertion_count": 0,
            "dom_count": 8,
            "grid_column_assertion_count": 1,
            "payload_binding_count": 0,
            "style_assertion_count": 6,
        },
        "assertion_requirements": {
            "aesthetic_layout_assertion_count": 0,
            "aesthetic_profile_assertion_count": 0,
            "dom_count": 1,
            "grid_span_assertion_count": 0,
            "style_assertion_count": 4,
        },
        "errors": [],
    }


def test_clean_workspace_first_proof_and_bounded_second_revision(tmp_path, monkeypatch, capsys):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    prove_module = importlib.import_module("viewspec.prove")
    monkeypatch.setattr(prove_module, "verify_host_artifact_dir", _passing_host_report)

    intent_path = Path("viewspec.intent.json")
    proof_dir = Path(".viewspec-proof")
    first_run_commands = 0

    assert cli_main(["init-intent", "--out", str(intent_path), "--kind", "dashboard"]) == 0
    first_run_commands += 1
    capsys.readouterr()

    authored = json.loads(intent_path.read_text(encoding="utf-8"))
    root_id = authored["substrate"]["root_id"]
    authored["substrate"]["nodes"][root_id]["attrs"]["title"] = "Deployment Health"
    intent_path.write_text(json.dumps(authored, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert cli_main(["validate-intent", str(intent_path), "--json"]) == 0
    first_run_commands += 1
    validation = json.loads(capsys.readouterr().out)
    assert validation["ok"] is True
    assert validation["compile_check"] == "passed"

    assert cli_main(
        [
            "prove",
            "--intent",
            str(intent_path),
            "--target",
            "react-tailwind-tsx",
            "--out",
            str(proof_dir),
            "--json",
        ]
    ) == 0
    first_run_commands += 1
    first_report = json.loads(capsys.readouterr().out)

    assert first_run_commands == 3
    assert first_report["ok"] is True
    assert first_report["proof_level"] == "react_tailwind_reference_host"
    assert first_report["checks"]["compile"] == "passed"
    assert first_report["checks"]["artifact_check"] == "passed"
    assert first_report["checks"]["host_verify"] == "passed"
    assert check_artifact_dir(proof_dir / "artifact")["ok"] is True
    for relative in (
        "artifact/ViewSpecView.tsx",
        "artifact/provenance_manifest.json",
        "proof_report.json",
        "PROOF.md",
        "support_bundle.json",
    ):
        assert proof_dir.joinpath(relative).is_file()

    before_revision = intent_path.read_text(encoding="utf-8")
    old_intent_path = Path("first-proof.intent.json")
    old_intent_path.write_text(before_revision, encoding="utf-8")
    patch = {
        "schema_version": 1,
        "contract_profile": INTENT_PATCH_CONTRACT_PROFILE,
        "source_kind": "intent_bundle",
        "base_source_sha256": source_sha256(before_revision),
        "operations": [
            {
                "op": "replace_semantic_attr",
                "node_id": root_id,
                "attr": "title",
                "old_value": "Deployment Health",
                "value": "Release Health",
            }
        ],
        "evidence_refs": ["proof:first-run"],
    }
    preview = preview_intent_patch(before_revision, patch)
    assert preview.compile_check["status"] == "passed"
    assert preview.semantic_diff["changes"]["substrate_nodes"]["changed"] == [root_id]
    for section, changes in preview.semantic_diff["changes"].items():
        if section != "substrate_nodes":
            assert changes == {"added": [], "removed": [], "changed": []}

    patch_path = Path("release-health.intentpatch.json")
    patch_path.write_text(json.dumps(patch, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt = apply_intent_patch_file(
        intent_path,
        patch_path,
        approval_token=preview.approval_token,
    )
    assert receipt.status == "applied"
    assert receipt.receipt_path.is_file()
    assert source_sha256(intent_path.read_text(encoding="utf-8")) == preview.candidate_source_sha256

    assert cli_main(["diff-intent", str(old_intent_path), str(intent_path), "--json"]) == 0
    semantic_diff = json.loads(capsys.readouterr().out)
    assert semantic_diff["ok"] is True
    assert semantic_diff["changes"]["substrate_nodes"]["changed"] == [root_id]

    assert cli_main(
        [
            "prove",
            "--intent",
            str(intent_path),
            "--target",
            "react-tailwind-tsx",
            "--out",
            str(proof_dir),
            "--force",
            "--json",
        ]
    ) == 0
    second_report = json.loads(capsys.readouterr().out)

    assert second_report["ok"] is True
    assert second_report["checks"]["host_verify"] == "passed"
    assert second_report["artifact_hash"] != first_report["artifact_hash"]
    assert "Release Health" in (proof_dir / "artifact/ViewSpecView.tsx").read_text(encoding="utf-8")
    assert check_artifact_dir(proof_dir / "artifact")["ok"] is True
