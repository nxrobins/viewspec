from __future__ import annotations

import json
from pathlib import Path

from viewspec import cli
from viewspec.intent_patch import IntentPatchContext, source_sha256
from viewspec.intent_tools import starter_intent_payload


def _write_inputs(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    source_path = tmp_path / "viewspec.intent.json"
    context_path = tmp_path / "converge-context.json"
    patch_path = tmp_path / "converge-patch.json"
    source = json.dumps(starter_intent_payload("dashboard"), indent=2, sort_keys=True) + "\n"
    source_path.write_text(source, encoding="utf-8")
    context = IntentPatchContext(
        origin="review_batch",
        source_kind="intent_bundle",
        base_source_sha256=source_sha256(source),
        contract_profile="local_v1",
        evidence_refs=("review:vrw_cli:batch_cli", "review_event:event_cli"),
        requests=(
            {
                "request_id": "event_cli",
                "kind": "change_request",
                "instruction": "Show revenue as a badge.",
                "screen_id": None,
                "source_ref": "ir:binding_revenue_value",
                "binding_id": "revenue_value",
                "action_id": None,
                "intent_refs": ["viewspec:binding:revenue_value"],
                "content_refs": ["node:revenue#attr:value"],
            },
        ),
    ).to_json()
    context_path.write_text(json.dumps(context, indent=2, sort_keys=True), encoding="utf-8")
    patch_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "contract_profile": "local_v1",
                "source_kind": "intent_bundle",
                "base_source_sha256": source_sha256(source),
                "operations": [
                    {
                        "op": "set_binding_presentation",
                        "binding_id": "revenue_value",
                        "old_value": "value",
                        "value": "badge",
                    }
                ],
                "evidence_refs": context["evidence_refs"],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return source_path, context_path, patch_path, source


def test_converge_cli_keeps_authority_hidden_by_default_and_applies_exact_preview(
    tmp_path: Path, capsys
) -> None:
    source_path, context_path, patch_path, source = _write_inputs(tmp_path)
    state = tmp_path / "state"

    assert cli.main(
        [
            "converge-start",
            str(source_path),
            str(context_path),
            "--state-dir",
            str(state),
            "--json",
        ]
    ) == 0
    started = json.loads(capsys.readouterr().out)
    assert started["convergence"]["status"] == "awaiting_proposal"

    assert cli.main(
        [
            "converge-submit",
            str(source_path),
            str(patch_path),
            "--state-dir",
            str(state),
            "--json",
        ]
    ) == 0
    submitted = json.loads(capsys.readouterr().out)
    pending = submitted["convergence"]["pending_preview"]
    assert submitted["convergence"]["status"] == "awaiting_approval"
    assert "approval_token" not in pending
    assert "intent_approval_token" not in pending
    assert source_path.read_text(encoding="utf-8") == source

    assert cli.main(
        [
            "converge-status",
            str(source_path),
            "--state-dir",
            str(state),
            "--show-authority",
            "--json",
        ]
    ) == 0
    expert = json.loads(capsys.readouterr().out)
    expert_pending = expert["convergence"]["pending_preview"]
    assert expert_pending["approval_token"].startswith("vcapprove_")
    assert "intent_approval_token" not in expert_pending

    assert cli.main(
        [
            "converge-approve",
            str(source_path),
            "--approval",
            "bad",
            "--state-dir",
            str(state),
            "--json",
        ]
    ) == 2
    assert "CONVERGE_APPROVAL_INVALID" in capsys.readouterr().err
    assert source_path.read_text(encoding="utf-8") == source

    assert cli.main(
        [
            "converge-approve",
            str(source_path),
            "--approval",
            expert_pending["approval_token"],
            "--state-dir",
            str(state),
            "--json",
        ]
    ) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["convergence"]["status"] == "applied"
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    assert payload["view_spec"]["bindings"][1]["present_as"] == "badge"


def test_converge_cli_rejects_exact_preview_without_mutating_source(tmp_path: Path, capsys) -> None:
    source_path, context_path, patch_path, source = _write_inputs(tmp_path)
    state = tmp_path / "state"
    common = ["--state-dir", str(state), "--json"]

    assert cli.main(["converge-start", str(source_path), str(context_path), *common]) == 0
    capsys.readouterr()
    assert cli.main(["converge-submit", str(source_path), str(patch_path), *common]) == 0
    submitted = json.loads(capsys.readouterr().out)
    preview_id = submitted["convergence"]["pending_preview"]["preview_id"]

    assert cli.main(
        ["converge-reject", str(source_path), "--preview", preview_id, *common]
    ) == 0
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["convergence"]["status"] == "rejected"
    assert source_path.read_text(encoding="utf-8") == source


def test_converge_cli_uses_duplicate_key_rejecting_json_parser(tmp_path: Path, capsys) -> None:
    source_path, context_path, _patch_path, _source = _write_inputs(tmp_path)
    raw = context_path.read_text(encoding="utf-8")
    context_path.write_text(raw.replace('{\n  "base_source_sha256"', '{\n  "schema_version": 1,\n  "schema_version": 1,\n  "base_source_sha256"'), encoding="utf-8")

    assert cli.main(["converge-start", str(source_path), str(context_path), "--json"]) == 2
    assert "CONVERGE_CONTEXT_INVALID" in capsys.readouterr().err
