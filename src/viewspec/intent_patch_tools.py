"""MCP-safe file adapters for proof-carrying IntentPatch transactions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from viewspec.intent_patch import (
    IntentPatchError,
    apply_intent_patch_file,
    patch_context_from_repair_plan,
    patch_context_from_review_batch,
    preview_intent_patch_file,
)
from viewspec.local_tools import (
    LocalToolError,
    atomic_write,
    path_policy_metadata,
    resolve_cwd,
    resolve_local_path,
    tool_error_response,
    tool_response,
)
from viewspec.repair import VerificationRepairPlan
from viewspec.review_contract import ReviewBatch


def _failure(exc: Exception, *, metadata: dict[str, Any]) -> dict[str, Any]:
    if isinstance(exc, (IntentPatchError, LocalToolError)):
        return tool_error_response(exc.code, exc.message, exc.fix, metadata=metadata)
    return tool_error_response(
        "PATCH_TOOL_FAILED",
        str(exc),
        "Fix the local patch inputs and retry.",
        metadata=metadata,
    )


def preview_intent_patch_file_tool(
    source_path: str | Path,
    patch_path: str | Path,
    *,
    candidate_out: str | Path | None = None,
    verify: bool = False,
    install: bool = False,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    """Preview one bounded patch without mutating its source."""

    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        metadata = path_policy_metadata(root, allow_outside_cwd)
        source = resolve_local_path(
            source_path,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
            must_exist=True,
        )
        patch = resolve_local_path(
            patch_path,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
            must_exist=True,
        )
        candidate = (
            resolve_local_path(
                candidate_out,
                cwd=root,
                allow_outside_cwd=allow_outside_cwd,
            )
            if candidate_out is not None
            else None
        )
        if candidate is not None and candidate in {source, patch}:
            raise IntentPatchError(
                "PATCH_PATH_INVALID",
                "Candidate output cannot overwrite the source or patch file.",
                "Choose a separate candidate output path.",
            )
        preview = preview_intent_patch_file(
            source,
            patch,
            verify=verify,
            install=install,
        )
        if candidate is not None:
            atomic_write(candidate, preview.candidate_text)
        return tool_response(
            True,
            "IntentPatch preview is valid; source was not changed.",
            paths={
                "source": str(source),
                "patch": str(patch),
                **({"candidate": str(candidate)} if candidate is not None else {}),
            },
            next_actions=(
                "Inspect semantic_diff and candidate output.",
                "Apply only with this exact approval_token while the source remains unchanged.",
            ),
            metadata=metadata,
            data={"preview": preview.to_json()},
        )
    except Exception as exc:
        return _failure(exc, metadata=path_policy_metadata(root, allow_outside_cwd))


def apply_intent_patch_file_tool(
    source_path: str | Path,
    patch_path: str | Path,
    *,
    approval_token: str,
    verify: bool = False,
    install: bool = False,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    """Apply one exact approved patch through the bounded path sandbox."""

    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        metadata = path_policy_metadata(root, allow_outside_cwd)
        source = resolve_local_path(
            source_path,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
            must_exist=True,
        )
        patch = resolve_local_path(
            patch_path,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
            must_exist=True,
        )
        receipt = apply_intent_patch_file(
            source,
            patch,
            approval_token=approval_token,
            verify=verify,
            install=install,
        )
        return tool_response(
            True,
            "IntentPatch applied atomically; inverse patch is recorded in the receipt.",
            paths={
                "source": str(source),
                "patch": str(patch),
                "receipt": str(receipt.receipt_path),
            },
            next_actions=("Retain the receipt to audit or explicitly apply its source-bound inverse patch.",),
            metadata=metadata,
            data={"receipt": receipt.to_json()},
        )
    except Exception as exc:
        return _failure(exc, metadata=path_policy_metadata(root, allow_outside_cwd))


def intent_patch_context_tool(
    *,
    review_batch: dict[str, Any] | None = None,
    repair_plan: dict[str, Any] | None = None,
    source_kind: str | None = None,
    base_source_sha256: str | None = None,
) -> dict[str, Any]:
    """Convert exactly one validated evidence contract into non-authoritative patch context."""

    metadata = {"network_calls": "none", "authority": "proposal_only"}
    try:
        if (review_batch is None) == (repair_plan is None):
            raise IntentPatchError(
                "PATCH_CONTEXT_INVALID",
                "Provide exactly one of review_batch or repair_plan.",
                "Convert one evidence contract at a time.",
            )
        if review_batch is not None:
            try:
                context = patch_context_from_review_batch(ReviewBatch.from_json(review_batch))
            except IntentPatchError:
                raise
            except (TypeError, ValueError) as exc:
                raise IntentPatchError(
                    "PATCH_CONTEXT_INVALID",
                    f"ReviewBatch is invalid: {exc}",
                    "Pass one complete validated Review V0 batch.",
                ) from exc
        else:
            try:
                plan = VerificationRepairPlan.from_json(repair_plan)
            except (TypeError, ValueError) as exc:
                raise IntentPatchError(
                    "PATCH_CONTEXT_INVALID",
                    f"VerificationRepairPlan is invalid: {exc}",
                    "Pass one complete validated verification repair plan.",
                ) from exc
            context = patch_context_from_repair_plan(
                plan,
                source_kind=source_kind,
                base_source_sha256=base_source_sha256,
            )
        return tool_response(
            True,
            "Created source-bound patch proposal context; no approval or source mutation occurred.",
            next_actions=(
                "Author only closed IntentPatch V1 operations from this evidence.",
                "Preview and obtain explicit approval before apply.",
            ),
            metadata=metadata,
            data={"context": context.to_json()},
        )
    except Exception as exc:
        return _failure(exc, metadata=metadata)


__all__ = [
    "apply_intent_patch_file_tool",
    "intent_patch_context_tool",
    "preview_intent_patch_file_tool",
]
