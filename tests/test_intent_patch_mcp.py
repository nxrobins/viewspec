from __future__ import annotations

import json
from pathlib import Path

from viewspec.intent_patch import INTENT_PATCH_CONTRACT_PROFILE, source_sha256
from viewspec.intent_patch_tools import (
    apply_intent_patch_file_tool,
    intent_patch_context_tool,
    preview_intent_patch_file_tool,
)
from viewspec.intent_tools import starter_intent_payload
from viewspec.repair import VerificationRepairPlan
from viewspec.verification import VerificationDiagnostic, VerificationPlan, VerificationResult


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    source_path = tmp_path / "viewspec.intent.json"
    patch_path = tmp_path / "change.intentpatch.json"
    source = json.dumps(starter_intent_payload("dashboard"), indent=2, sort_keys=True) + "\n"
    source_path.write_text(source, encoding="utf-8")
    patch_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "contract_profile": INTENT_PATCH_CONTRACT_PROFILE,
                "source_kind": "intent_bundle",
                "base_source_sha256": source_sha256(source),
                "operations": [
                    {
                        "op": "replace_semantic_attr",
                        "node_id": "starter_dashboard",
                        "attr": "title",
                        "old_value": "Starter Dashboard",
                        "value": "MCP Dashboard",
                    }
                ],
                "evidence_refs": [],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return source_path, patch_path


def test_patch_mcp_tools_preview_then_exact_apply(tmp_path: Path) -> None:
    source_path, patch_path = _write_inputs(tmp_path)
    candidate_path = tmp_path / "candidate.intent.json"

    previewed = preview_intent_patch_file_tool(
        source_path.name,
        patch_path.name,
        candidate_out=candidate_path.name,
        cwd=tmp_path,
    )

    assert previewed["ok"] is True
    assert previewed["preview"]["approval_token"].startswith("vapprove_")
    assert previewed["paths"]["candidate"] == str(candidate_path)
    assert candidate_path.is_file()

    rejected = apply_intent_patch_file_tool(
        source_path.name,
        patch_path.name,
        approval_token="bad",
        cwd=tmp_path,
    )
    assert rejected["ok"] is False
    assert rejected["errors"][0]["code"] == "PATCH_APPROVAL_INVALID"

    applied = apply_intent_patch_file_tool(
        source_path.name,
        patch_path.name,
        approval_token=previewed["preview"]["approval_token"],
        cwd=tmp_path,
    )
    assert applied["ok"] is True
    assert applied["receipt"]["status"] == "applied"


def test_patch_mcp_tools_reject_paths_outside_cwd(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source_path, patch_path = _write_inputs(tmp_path)

    result = preview_intent_patch_file_tool(source_path, patch_path, cwd=root)

    assert result["ok"] is False
    assert result["errors"][0]["code"] == "PATH_OUTSIDE_CWD"


def test_patch_mcp_context_tool_converts_verifier_repairs_without_authority() -> None:
    result = VerificationResult.create(
        artifact_sha256="a" * 64,
        plan=VerificationPlan.default(),
        complete=True,
        diagnostics=(
            VerificationDiagnostic(
                code="VERIFY_LAYOUT_OVERFLOW",
                severity="error",
                message="Queue overflows.",
                fix="Use a bounded grid.",
                source_ref="screen:queue/ir:region_main",
                viewport="mobile",
            ),
        ),
    )
    plan = VerificationRepairPlan.from_result(result)

    context = intent_patch_context_tool(
        repair_plan=plan.to_json(),
        source_kind="app_bundle",
        base_source_sha256="b" * 64,
    )

    assert context["ok"] is True
    assert context["context"]["origin"] == "verification_repair_plan"
    assert context["context"]["requests"][0]["code"] == "VERIFY_LAYOUT_OVERFLOW"
    assert "approval_token" not in context["context"]
