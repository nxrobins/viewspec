"""MCP-safe adapters for ViewSpec Converge Sessions V1."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from viewspec.converge_sessions import (
    ConvergeError,
    ConvergenceSession,
    approve_convergence_preview,
    get_convergence_status,
    reject_convergence_preview,
    start_convergence_session,
    submit_convergence_patch,
)
from viewspec.intent_patch import IntentPatchContext
from viewspec.local_tools import (
    LocalToolError,
    path_policy_metadata,
    resolve_cwd,
    resolve_local_path,
    tool_error_response,
    tool_response,
)
from viewspec.verification import VerificationResult


def _failure(exc: Exception, *, metadata: dict[str, Any]) -> dict[str, Any]:
    if isinstance(exc, (ConvergeError, LocalToolError)):
        return tool_error_response(exc.code, exc.message, exc.fix, metadata=metadata)
    return tool_error_response(
        "CONVERGE_TOOL_FAILED",
        str(exc),
        "Fix the bounded convergence inputs and retry.",
        metadata=metadata,
    )


def _context(value: Mapping[str, Any]) -> IntentPatchContext:
    try:
        return IntentPatchContext(
            origin=value.get("origin"),
            source_kind=value.get("source_kind"),
            base_source_sha256=value.get("base_source_sha256"),
            contract_profile=value.get("contract_profile"),
            evidence_refs=tuple(value.get("evidence_refs", [])),
            requests=tuple(value.get("requests", [])),
        )
    except Exception as exc:
        raise ConvergeError(
            "CONVERGE_CONTEXT_INVALID",
            f"IntentPatch proposal context is invalid: {exc}",
            "Use build_intent_patch_context and pass its exact context object.",
        ) from exc


def _session_json(session: ConvergenceSession) -> dict[str, Any]:
    """Return agent-readable state without either source-write authority token."""

    payload = session.to_json(include_approval_token=False)
    pending = payload.get("pending_preview")
    if isinstance(pending, dict):
        pending.pop("intent_approval_token", None)
        pending["approval"] = {
            "required": True,
            "channel": "viewspec_review_or_explicit_operator",
        }
    return payload


def _next_actions(session: ConvergenceSession) -> tuple[str, ...]:
    if session.status == "awaiting_proposal":
        return (
            "Author one IntentPatch using only task.legal_operations and exact task evidence refs.",
            "Submit the bounded patch; do not edit source directly.",
        )
    if session.status == "awaiting_approval":
        return (
            "Ask the human to inspect the semantic diff and comparative evidence in ViewSpec Review.",
            "Wait for explicit approval or rejection; the agent must not self-authorize source mutation.",
        )
    if session.status in {"applied", "conformant"}:
        return ("Retain the convergence and IntentPatch receipts as audit evidence.",)
    if session.status == "full_revision_required":
        return ("Propose a separately reviewed full semantic source revision; never escape through arbitrary JSON patching.",)
    return ("Stop this bounded session and report its terminal_reason.",)


def _resolve_state_root(
    state_dir: str | Path | None,
    *,
    root: Path,
    allow_outside_cwd: bool,
) -> Path | None:
    if state_dir is None:
        return None
    return resolve_local_path(
        state_dir,
        cwd=root,
        allow_outside_cwd=allow_outside_cwd,
    )


def start_convergence_session_tool(
    source: str | Path,
    context: Mapping[str, Any],
    *,
    baseline_result: Mapping[str, Any] | None = None,
    state_dir: str | Path | None = None,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    """Start a session from one validated proposal context."""

    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        metadata = path_policy_metadata(root, allow_outside_cwd)
        source_path = resolve_local_path(
            source,
            cwd=root,
            allow_outside_cwd=allow_outside_cwd,
            must_exist=True,
        )
        state_root = _resolve_state_root(
            state_dir,
            root=root,
            allow_outside_cwd=allow_outside_cwd,
        )
        baseline = (
            VerificationResult.from_json(baseline_result)
            if baseline_result is not None
            else None
        )
        session = start_convergence_session(
            source_path,
            _context(context),
            baseline_result=baseline,
            state_root=state_root,
        )
        return tool_response(
            True,
            "Started a source-bound convergence session; source was not changed.",
            paths={"source": str(source_path)},
            next_actions=_next_actions(session),
            metadata={**metadata, "authority": "proposal_only"},
            data={"convergence": _session_json(session)},
        )
    except Exception as exc:
        return _failure(exc, metadata=path_policy_metadata(root, allow_outside_cwd))


def submit_convergence_patch_tool(
    source: str | Path,
    patch: Mapping[str, Any],
    *,
    state_dir: str | Path | None = None,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    """Submit one patch while withholding all source-write authority from the agent response."""

    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        metadata = path_policy_metadata(root, allow_outside_cwd)
        source_path = resolve_local_path(source, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        state_root = _resolve_state_root(state_dir, root=root, allow_outside_cwd=allow_outside_cwd)
        session = submit_convergence_patch(source_path, patch, state_root=state_root)
        summary = (
            "Convergence proposal is proved and waiting for explicit human approval."
            if session.status == "awaiting_approval"
            else f"Convergence proposal ended with status {session.status}."
        )
        return tool_response(
            True,
            summary,
            paths={"source": str(source_path)},
            next_actions=_next_actions(session),
            metadata={**metadata, "authority": "withheld_from_agent"},
            data={"convergence": _session_json(session)},
        )
    except Exception as exc:
        return _failure(exc, metadata=path_policy_metadata(root, allow_outside_cwd))


def convergence_status_tool(
    source: str | Path,
    *,
    state_dir: str | Path | None = None,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        metadata = path_policy_metadata(root, allow_outside_cwd)
        source_path = resolve_local_path(source, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        state_root = _resolve_state_root(state_dir, root=root, allow_outside_cwd=allow_outside_cwd)
        session = get_convergence_status(source_path, state_root=state_root)
        return tool_response(
            True,
            f"Convergence session status is {session.status}.",
            paths={"source": str(source_path)},
            next_actions=_next_actions(session),
            metadata={**metadata, "authority": "withheld_from_agent"},
            data={"convergence": _session_json(session)},
        )
    except Exception as exc:
        return _failure(exc, metadata=path_policy_metadata(root, allow_outside_cwd))


def approve_convergence_preview_tool(
    source: str | Path,
    approval_token: str,
    *,
    state_dir: str | Path | None = None,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    """Apply authority explicitly supplied by a human operator; this tool never discovers it."""

    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        metadata = path_policy_metadata(root, allow_outside_cwd)
        source_path = resolve_local_path(source, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        state_root = _resolve_state_root(state_dir, root=root, allow_outside_cwd=allow_outside_cwd)
        session = approve_convergence_preview(
            source_path,
            approval_token,
            state_root=state_root,
        )
        return tool_response(
            True,
            f"Explicitly approved convergence proposal finished with status {session.status}.",
            paths={"source": str(source_path)},
            next_actions=_next_actions(session),
            metadata={**metadata, "authority": "explicit_operator_input"},
            data={"convergence": _session_json(session)},
        )
    except Exception as exc:
        return _failure(exc, metadata=path_policy_metadata(root, allow_outside_cwd))


def reject_convergence_preview_tool(
    source: str | Path,
    preview_id: str,
    *,
    state_dir: str | Path | None = None,
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
) -> dict[str, Any]:
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        metadata = path_policy_metadata(root, allow_outside_cwd)
        source_path = resolve_local_path(source, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        state_root = _resolve_state_root(state_dir, root=root, allow_outside_cwd=allow_outside_cwd)
        session = reject_convergence_preview(source_path, preview_id, state_root=state_root)
        return tool_response(
            True,
            "Rejected the exact convergence proposal; source was not changed.",
            paths={"source": str(source_path)},
            next_actions=_next_actions(session),
            metadata={**metadata, "authority": "explicit_operator_input"},
            data={"convergence": _session_json(session)},
        )
    except Exception as exc:
        return _failure(exc, metadata=path_policy_metadata(root, allow_outside_cwd))


__all__ = [
    "approve_convergence_preview_tool",
    "convergence_status_tool",
    "reject_convergence_preview_tool",
    "start_convergence_session_tool",
    "submit_convergence_patch_tool",
]
