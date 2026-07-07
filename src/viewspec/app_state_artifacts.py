"""AppBundle V3 state reducer artifact helpers."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from viewspec.app_errors import AppBundleProofFailure
from viewspec.app_validation import APP_BUNDLE_STATE_SCHEMA_VERSION, APP_BUNDLE_VISIBILITY_SCHEMA_VERSION
from viewspec.local_tools import atomic_write, file_hash
from viewspec.state_ir import APP_STATE_MAX_MANIFEST_BYTES, APP_STATE_MAX_REDUCER_BYTES, INTERACTIVE_STATE_PROFILE

APP_STATE_REDUCER = "state_reducer.ts"
APP_STATE_MANIFEST = "state_manifest.json"


def _write_state_artifacts(
    payload: dict[str, Any],
    output_dir: Path,
    *,
    generate_reducer: Callable[[dict[str, Any]], str],
    check_conformance: Callable[..., dict[str, Any]],
    build_manifest: Callable[..., dict[str, Any]],
) -> dict[str, Any] | None:
    if payload.get("schema_version") not in {APP_BUNDLE_STATE_SCHEMA_VERSION, APP_BUNDLE_VISIBILITY_SCHEMA_VERSION}:
        return None
    reducer_path = output_dir / APP_STATE_REDUCER
    manifest_path = output_dir / APP_STATE_MANIFEST
    try:
        reducer = generate_reducer(payload)
        reducer_bytes = len(reducer.encode("utf-8"))
        if reducer_bytes > APP_STATE_MAX_REDUCER_BYTES:
            raise AppBundleProofFailure(
                "APP_STATE_REDUCER_LIMIT_EXCEEDED",
                f"Generated state reducer is {reducer_bytes} bytes; limit is {APP_STATE_MAX_REDUCER_BYTES}.",
                "Reduce AppBundle V3 state, mutation, or selector declarations.",
            )
        atomic_write(reducer_path, reducer)
        reducer_hash = file_hash(reducer_path)
        conformance = check_conformance(payload, reducer_source=reducer)
        if not conformance.get("ok"):
            errors = conformance.get("errors") if isinstance(conformance.get("errors"), list) else []
            first = errors[0] if errors and isinstance(errors[0], dict) else {}
            message = first.get("message") or "Generated reducer diverged from the Python state interpreter."
            if first.get("code") == "APP_STATE_REDUCER_NODE_UNAVAILABLE":
                # A missing Node.js runtime is an environment prerequisite, not a state-contract
                # bug -- surface the actionable code/fix instead of "fix your state contract".
                raise AppBundleProofFailure(
                    "APP_STATE_REDUCER_NODE_UNAVAILABLE",
                    str(message),
                    str(first.get("fix") or "Install Node.js (>=18) on PATH for V3 reducer conformance, or use a V1/V2 AppBundle."),
                )
            raise AppBundleProofFailure(
                "APP_STATE_REDUCER_CONFORMANCE_FAILED",
                str(message),
                "Fix the AppBundle V3 state contract or generated reducer semantics and retry.",
            )
        manifest = build_manifest(payload, reducer_hash=reducer_hash, conformance_report=conformance)
        replay = manifest.get("replay") if isinstance(manifest.get("replay"), dict) else {}
        if replay and not replay.get("ok"):
            raise AppBundleProofFailure(
                "APP_STATE_REPLAY_ASSERTION_FAILED",
                "State replay assertions failed.",
                "Fix state_replay_assertions or the referenced mutation operations.",
            )
        _write_bounded_json(
            manifest_path,
            manifest,
            limit=APP_STATE_MAX_MANIFEST_BYTES,
            code="APP_STATE_MANIFEST_WRITE_FAILED",
        )
    except AppBundleProofFailure:
        raise
    except Exception as exc:
        raise AppBundleProofFailure(
            "APP_STATE_REDUCER_WRITE_FAILED",
            f"Failed to write state reducer artifacts: {exc}",
            "Fix the AppBundle V3 state contract and retry.",
        ) from exc
    manifest_hash = file_hash(manifest_path)
    return {
        "reducer_path": reducer_path,
        "manifest_path": manifest_path,
        "reducer_hash": reducer_hash,
        "manifest_hash": manifest_hash,
        "manifest_summary": {
            "profile": INTERACTIVE_STATE_PROFILE,
            "reducer_hash": reducer_hash,
            "manifest_hash": manifest_hash,
            "state_count": len(payload.get("state", [])) if isinstance(payload.get("state"), list) else 0,
            "mutation_count": len(payload.get("mutations", [])) if isinstance(payload.get("mutations"), list) else 0,
            "selector_count": len(payload.get("selectors", [])) if isinstance(payload.get("selectors"), list) else 0,
            "replay_ok": bool(manifest.get("replay", {}).get("ok")) if isinstance(manifest.get("replay"), dict) else True,
            "contract_hash": manifest.get("contract_hash"),
            "reducer_conformance": _state_conformance_status(conformance),
            # v4-only keys: the v3 summary shape stays byte-stable.
            **(_visibility_summary(payload, manifest) if payload.get("schema_version") == APP_BUNDLE_VISIBILITY_SCHEMA_VERSION else {}),
        },
        "replay": manifest.get("replay") if isinstance(manifest.get("replay"), dict) else None,
        "contract_hash": manifest.get("contract_hash"),
        "conformance": conformance,
    }


def _visibility_summary(payload: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """v4 visibility facts. SC-V2: visibility_replay_ok is true|false ONLY when at least one
    replay assertion declares expect_visibility; otherwise null — reported, never claimed."""
    rules = payload.get("visibility", []) if isinstance(payload.get("visibility"), list) else []
    assertions = (
        payload.get("state_replay_assertions", [])
        if isinstance(payload.get("state_replay_assertions"), list)
        else []
    )
    asserted = any(
        isinstance(item, dict) and isinstance(item.get("expect_visibility"), dict) and item["expect_visibility"]
        for item in assertions
    )
    visibility_replay_ok: bool | None = None
    if asserted:
        replay = manifest.get("replay") if isinstance(manifest.get("replay"), dict) else {}
        entries = replay.get("assertions") if isinstance(replay.get("assertions"), list) else []
        visibility_replay_ok = all(
            entry.get("visibility_matches", True) for entry in entries if isinstance(entry, dict)
        )
    initial_hidden = _initial_hidden_count(payload)
    return {
        "visibility_rule_count": len(rules),
        "initial_hidden_count": initial_hidden,
        "visibility_replay_ok": visibility_replay_ok,
    }


def _initial_hidden_count(payload: dict[str, Any]) -> int | None:
    from viewspec.state_ir import initial_visibility, validate_state_ir

    state_ir, issues = validate_state_ir(payload)
    if state_ir is None or issues:
        return None
    return sum(1 for visible in initial_visibility(payload, state_ir).values() if not visible)


def _state_conformance_status(report: object) -> str | None:
    if not isinstance(report, dict):
        return None
    if report.get("ok") is True:
        return "passed"
    if report.get("ok") is False:
        return "failed"
    return None


def _write_bounded_json(path: Path, payload: dict[str, Any], *, limit: int, code: str) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    size = len(text.encode("utf-8"))
    if size > limit:
        raise AppBundleProofFailure(
            code,
            f"JSON artifact {path.name} is {size} bytes; limit is {limit}.",
            "Reduce the AppBundle size and retry.",
        )
    atomic_write(path, text)


__all__ = [
    "APP_STATE_MANIFEST",
    "APP_STATE_REDUCER",
    "_state_conformance_status",
    "_write_state_artifacts",
]
