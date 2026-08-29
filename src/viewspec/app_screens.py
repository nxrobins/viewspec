"""AppBundle per-screen proof helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from viewspec.app_errors import _normalize_proof_errors
from viewspec.app_paths import _assert_under_proof_root
from viewspec.app_resource_repeat import resource_binding_address
from viewspec.app_state_text import check_screen_state_text_bake, screen_state_text_overlays
from viewspec.app_visibility import check_screen_visibility_bake, screen_visibility_overlays
from viewspec.local_tools import atomic_write, check_artifact_dir, file_hash
from viewspec.manifest_summary import summarize_intent_manifest
from viewspec.state_ir import validate_state_ir


def _visibility_overlays_for(payload: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    if payload.get("schema_version") != 4:
        return {}
    state_ir, issues = validate_state_ir(payload)
    if state_ir is None or issues:
        # Upstream validation already failed the bundle; screens will not compile cleanly anyway.
        return {}
    return screen_visibility_overlays(payload, state_ir)


def _state_text_overlays_for(payload: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    if payload.get("schema_version") != 4 or "state_text" not in payload:
        return {}
    state_ir, issues = validate_state_ir(payload)
    if state_ir is None or issues:
        return {}
    return screen_state_text_overlays(payload, state_ir)


def _screen_ir_overlays_for(payload: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    """Return compiler-owned overlays for IntentBundles embedded inside an AppBundle.

    A standalone IntentBundle owns its page landmark and therefore emits a ``main`` root.
    An AppBundle shell owns that landmark, so every embedded screen root is explicitly
    rendered as a neutral container. Visibility overlays are merged into the same bounded
    compile seam.
    """
    overlays = {
        screen_id: {node_id: dict(props) for node_id, props in screen_overlay.items()}
        for screen_id, screen_overlay in _visibility_overlays_for(payload).items()
    }
    for screen_id, screen_overlay in _state_text_overlays_for(payload).items():
        target_overlay = overlays.setdefault(screen_id, {})
        for node_id, props in screen_overlay.items():
            target_overlay.setdefault(node_id, {}).update(props)
    screens = payload.get("screens") if isinstance(payload.get("screens"), list) else []
    for screen in screens:
        if not isinstance(screen, dict):
            continue
        screen_id = screen.get("id")
        intent_bundle = screen.get("intent_bundle")
        view_spec = intent_bundle.get("view_spec") if isinstance(intent_bundle, dict) else None
        root_region = view_spec.get("root_region") if isinstance(view_spec, dict) else None
        if not isinstance(screen_id, str) or not isinstance(root_region, str):
            continue
        root_props = overlays.setdefault(screen_id, {}).setdefault(f"region_{root_region}", {})
        root_props["semantic_context"] = "embedded_screen"
        _add_resource_identity_overlays(screen, overlays[screen_id])
    return overlays


def _add_resource_identity_overlays(screen: dict[str, Any], overlay: dict[str, dict[str, Any]]) -> None:
    intent_bundle = screen.get("intent_bundle")
    view_spec = intent_bundle.get("view_spec") if isinstance(intent_bundle, dict) else None
    if not isinstance(view_spec, dict):
        return
    bindings = view_spec.get("bindings") if isinstance(view_spec.get("bindings"), list) else []
    bindings_by_address: dict[str, list[str]] = {}
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        binding_id = binding.get("id")
        address = binding.get("address")
        if isinstance(binding_id, str) and isinstance(address, str):
            bindings_by_address.setdefault(address, []).append(binding_id)
    motifs = view_spec.get("motifs") if isinstance(view_spec.get("motifs"), list) else []
    motif_members = {
        str(motif.get("id")): {item for item in motif.get("members", []) if isinstance(item, str)}
        for motif in motifs
        if isinstance(motif, dict) and isinstance(motif.get("id"), str)
    }
    resource_views = screen.get("resource_views") if isinstance(screen.get("resource_views"), list) else []
    for resource_view in resource_views:
        if not isinstance(resource_view, dict):
            continue
        view_id = resource_view.get("id")
        resource_id = resource_view.get("resource_id")
        target_motif_id = resource_view.get("target_motif_id")
        if not all(isinstance(item, str) and item for item in (view_id, resource_id, target_motif_id)):
            continue
        members = motif_members.get(str(target_motif_id), set())
        for record_id in resource_view.get("record_ids", []):
            if not isinstance(record_id, str):
                continue
            for field in resource_view.get("fields", []):
                if not isinstance(field, str):
                    continue
                address = resource_binding_address(resource_view, record_id, field)
                for binding_id in bindings_by_address.get(address, []):
                    if binding_id not in members:
                        continue
                    node_props = overlay.setdefault(f"binding_{binding_id}", {})
                    identity = {
                        "record_id": record_id,
                        "resource_field": field,
                        "resource_id": str(resource_id),
                        "resource_view_id": str(view_id),
                    }
                    existing = {key: node_props.get(key) for key in identity if key in node_props}
                    if existing and existing != {key: value for key, value in identity.items() if key in existing}:
                        # Leave an invalid sentinel for the bounded overlay validator. A single
                        # binding cannot claim two canonical resource identities.
                        node_props["resource_id"] = ""
                        continue
                    node_props.update(identity)


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
    visibility_overlays = _visibility_overlays_for(payload)
    state_text_overlays = _state_text_overlays_for(payload)
    screen_ir_overlays = _screen_ir_overlays_for(payload)
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
            ir_props_overlay=screen_ir_overlays.get(screen_id),
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
        if not errors and screen_id in visibility_overlays and manifest_path.exists():
            # SC-V1: the artifact's baked markers must equal the initial_visibility-derived overlay.
            try:
                screen_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                screen_manifest = {}
            for mismatch in check_screen_visibility_bake(screen_manifest, visibility_overlays[screen_id]):
                errors.append(
                    {
                        "code": "APP_VISIBILITY_BAKE_MISMATCH",
                        "message": f"Screen {screen_id} visibility bake mismatch: {mismatch}.",
                        "fix": "Recompile the AppBundle; baked visibility markers must match initial_visibility exactly.",
                    }
                )
        if not errors and screen_id in state_text_overlays and manifest_path.exists():
            try:
                screen_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                screen_manifest = {}
            for mismatch in check_screen_state_text_bake(screen_manifest, state_text_overlays[screen_id]):
                errors.append(
                    {
                        "code": "APP_STATE_TEXT_BAKE_MISMATCH",
                        "message": f"Screen {screen_id} state text bake mismatch: {mismatch}.",
                        "fix": "Recompile the AppBundle; state text markers and initial values must match the state contract exactly.",
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
    ir_props_overlay: dict[str, dict[str, Any]] | None = None,
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
        ir_props_overlay=ir_props_overlay,
    )


__all__ = [
    "_prove_app_screens",
    "_screen_ir_overlays_for",
    "_state_text_overlays_for",
    "_visibility_overlays_for",
]
