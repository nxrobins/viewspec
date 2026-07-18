from __future__ import annotations

import json
from pathlib import Path

from viewspec import cli
from viewspec.intent_patch import INTENT_PATCH_CONTRACT_PROFILE, preview_intent_patch, source_sha256
from viewspec.intent_tools import starter_intent_payload


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
                        "value": "Converged Dashboard",
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


def test_patch_preview_cli_writes_candidate_without_mutating_source(tmp_path: Path, capsys) -> None:
    source_path, patch_path = _write_inputs(tmp_path)
    original = source_path.read_text(encoding="utf-8")
    candidate_path = tmp_path / "candidate.intent.json"

    exit_code = cli.main(
        [
            "patch-preview",
            str(source_path),
            str(patch_path),
            "--candidate-out",
            str(candidate_path),
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["preview"]["approval_token"].startswith("vapprove_")
    assert payload["paths"]["candidate"] == str(candidate_path)
    assert json.loads(candidate_path.read_text(encoding="utf-8"))["substrate"]["nodes"]["starter_dashboard"]["attrs"]["title"] == "Converged Dashboard"
    assert source_path.read_text(encoding="utf-8") == original


def test_patch_apply_cli_requires_exact_approval_and_returns_receipt(tmp_path: Path, capsys) -> None:
    source_path, patch_path = _write_inputs(tmp_path)
    source = source_path.read_text(encoding="utf-8")
    preview = preview_intent_patch(source, patch_path.read_text(encoding="utf-8"))

    assert cli.main(["patch-apply", str(source_path), str(patch_path), "--approval", "bad", "--json"]) == 2
    assert "PATCH_APPROVAL_INVALID" in capsys.readouterr().err

    assert (
        cli.main(
            [
                "patch-apply",
                str(source_path),
                str(patch_path),
                "--approval",
                preview.approval_token,
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["receipt"]["status"] == "applied"
    assert Path(payload["paths"]["receipt"]).is_file()
    assert json.loads(source_path.read_text(encoding="utf-8"))["substrate"]["nodes"]["starter_dashboard"]["attrs"]["title"] == "Converged Dashboard"
