"""AppBundle per-screen proof helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from viewspec.app_errors import _normalize_proof_errors
from viewspec.app_paths import _assert_under_proof_root
from viewspec.local_tools import atomic_write, check_artifact_dir, file_hash
from viewspec.manifest_summary import summarize_intent_manifest


def _prove_app_screens(
    payload: dict[str, Any],
    output_dir: Path,
    *,
    design_path: Path | None,
    root: Path,
    strict_design: bool,
    target: str,
) -> list[dict[str, Any]]:
    screen_reports: list[dict[str, Any]] = []
    screens = payload.get("screens") if isinstance(payload.get("screens"), list) else []
    for screen in screens:
        screen_id = str(screen["id"])
        screen_dir = output_dir / "screens" / screen_id
        artifact_dir = screen_dir / "artifact"
        _assert_under_proof_root(screen_dir, output_dir)
        intent_path = screen_dir / "viewspec.intent.json"
        intent_text = json.dumps(screen["intent_bundle"], indent=2, sort_keys=True) + "\n"
        atomic_write(intent_path, intent_text)
        compiled = _compile_screen(
            intent_path,
            artifact_dir,
            design_path=design_path,
            strict_design=strict_design,
            target=target,
            root=root,
        )
        errors = _normalize_proof_errors(compiled.get("errors")) if not compiled.get("ok") else []
        manifest_path = artifact_dir / "provenance_manifest.json"
        diagnostics_path = artifact_dir / "diagnostics.json"
        artifact_path = artifact_dir / "index.html"
        check = check_artifact_dir(artifact_dir) if artifact_dir.exists() else {"ok": False, "errors": ["artifact directory missing"], "manifest_summary": None}
        if not check.get("ok") and not errors:
            errors = [
                {
                    "code": "APP_PROOF_SCREEN_CHECK_FAILED",
                    "message": str(item),
                    "fix": "Fix the embedded screen IntentBundle and retry prove-app.",
                }
                for item in check.get("errors", [])
            ]
        manifest_summary = summarize_intent_manifest(manifest_path) if manifest_path.exists() else None
        if not errors and (not isinstance(manifest_summary, dict) or manifest_summary.get("available") is not True):
            errors.append(
                {
                    "code": "APP_PROOF_MANIFEST_SUMMARY_FAILED",
                    "message": f"Screen {screen_id} manifest summary unavailable.",
                    "fix": "Regenerate the screen artifact from a valid embedded IntentBundle.",
                }
            )
        screen_reports.append(
            {
                "id": screen_id,
                "title": screen.get("title"),
                "validation_status": "passed" if not errors else "failed",
                "compile_status": "passed" if compiled.get("ok") else "failed",
                "check_status": "passed" if check.get("ok") else "failed",
                "artifact_hash": file_hash(artifact_path) if artifact_path.exists() and not errors else None,
                "manifest_hash": file_hash(manifest_path) if manifest_path.exists() and not errors else None,
                "manifest_summary": manifest_summary,
                "paths": {
                    "intent": str(intent_path),
                    "artifact_dir": str(artifact_dir),
                    "artifact": str(artifact_path),
                    "manifest": str(manifest_path),
                    "diagnostics": str(diagnostics_path),
                },
                "errors": [
                    {
                        **error,
                        "screen_id": screen_id,
                    }
                    for error in errors
                ],
            }
        )
        if errors:
            break
    return screen_reports


def _compile_screen(
    intent_path: Path,
    artifact_dir: Path,
    *,
    design_path: Path | None,
    strict_design: bool,
    target: str,
    root: Path,
) -> dict[str, Any]:
    from viewspec.intent_tools import compile_intent_bundle_file_tool

    return compile_intent_bundle_file_tool(
        intent_path,
        artifact_dir,
        design_path=design_path,
        strict_design=strict_design,
        target=target,
        cwd=root,
        allow_outside_cwd=True,
    )


__all__ = ["_prove_app_screens"]
