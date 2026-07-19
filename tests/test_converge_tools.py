from __future__ import annotations

import json
from pathlib import Path

from viewspec.converge_sessions import get_convergence_status
from viewspec.converge_tools import (
    approve_convergence_preview_tool,
    convergence_status_tool,
    reject_convergence_preview_tool,
    start_convergence_session_tool,
    submit_convergence_patch_tool,
)
from viewspec.intent_patch import IntentPatchContext, source_sha256
from viewspec.intent_tools import starter_intent_payload


def _inputs(tmp_path: Path) -> tuple[Path, str, dict, dict]:
    source = tmp_path / "viewspec.intent.json"
    text = json.dumps(starter_intent_payload("dashboard"), indent=2, sort_keys=True) + "\n"
    source.write_text(text, encoding="utf-8")
    context = IntentPatchContext(
        origin="review_batch",
        source_kind="intent_bundle",
        base_source_sha256=source_sha256(text),
        contract_profile="local_v1",
        evidence_refs=("review:vrw_tools:batch_tools", "review_event:event_tools"),
        requests=(
            {
                "request_id": "event_tools",
                "kind": "change_request",
                "instruction": "Use a badge.",
                "screen_id": None,
                "source_ref": "ir:binding_revenue_value",
                "binding_id": "revenue_value",
                "action_id": None,
                "intent_refs": ["viewspec:binding:revenue_value"],
                "content_refs": ["node:revenue#attr:value"],
            },
        ),
    ).to_json()
    patch = {
        "schema_version": 1,
        "contract_profile": "local_v1",
        "source_kind": "intent_bundle",
        "base_source_sha256": source_sha256(text),
        "operations": [
            {
                "op": "set_binding_presentation",
                "binding_id": "revenue_value",
                "old_value": "value",
                "value": "badge",
            }
        ],
        "evidence_refs": context["evidence_refs"],
    }
    return source, text, context, patch


def test_agent_tools_drive_proposal_but_withhold_both_write_tokens(tmp_path: Path) -> None:
    source, text, context, patch = _inputs(tmp_path)
    state = tmp_path / "state"

    started = start_convergence_session_tool(
        source.name,
        context,
        state_dir=state.name,
        cwd=tmp_path,
    )
    assert started["ok"] is True
    assert started["convergence"]["status"] == "awaiting_proposal"
    assert started["metadata"]["authority"] == "proposal_only"

    submitted = submit_convergence_patch_tool(
        source.name,
        patch,
        state_dir=state.name,
        cwd=tmp_path,
    )
    assert submitted["ok"] is True
    pending = submitted["convergence"]["pending_preview"]
    assert submitted["convergence"]["status"] == "awaiting_approval"
    assert "approval_token" not in pending
    assert "intent_approval_token" not in pending
    assert pending["approval"]["channel"] == "viewspec_review_or_explicit_operator"
    assert source.read_text(encoding="utf-8") == text

    status = convergence_status_tool(source.name, state_dir=state.name, cwd=tmp_path)
    assert status["ok"] is True
    assert "approval_token" not in status["convergence"]["pending_preview"]

    core = get_convergence_status(source, state_root=state)
    applied = approve_convergence_preview_tool(
        source.name,
        core.pending_preview.approval_token,
        state_dir=state.name,
        cwd=tmp_path,
    )
    assert applied["ok"] is True
    assert applied["convergence"]["status"] == "applied"
    assert json.loads(source.read_text(encoding="utf-8"))["view_spec"]["bindings"][1]["present_as"] == "badge"


def test_tools_preserve_path_sandbox_and_exact_rejection(tmp_path: Path) -> None:
    source, text, context, patch = _inputs(tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    outside = start_convergence_session_tool(source, context, cwd=root)
    assert outside["ok"] is False
    assert outside["errors"][0]["code"] == "PATH_OUTSIDE_CWD"

    state = tmp_path / "state"
    assert start_convergence_session_tool(source.name, context, state_dir=state.name, cwd=tmp_path)["ok"]
    submitted = submit_convergence_patch_tool(source.name, patch, state_dir=state.name, cwd=tmp_path)
    preview_id = submitted["convergence"]["pending_preview"]["preview_id"]
    rejected = reject_convergence_preview_tool(
        source.name,
        preview_id,
        state_dir=state.name,
        cwd=tmp_path,
    )
    assert rejected["ok"] is True
    assert rejected["convergence"]["status"] == "rejected"
    assert source.read_text(encoding="utf-8") == text
