"""Opinionated product entry point for the local ViewSpec creation loop."""

from __future__ import annotations

from pathlib import Path
import time

from viewspec.review_cli import DEFAULT_REVIEW_PORT, open_review
from viewspec.review_compile import STUDIO_COMPARE_TARGET, capture_source_snapshot
from viewspec.review_contract import ReviewContractError
from viewspec.studio_creation import STUDIO_CREATION_TASK_DEFAULT, prepare_studio_creation
from viewspec.studio_creation_cli import accepted_creation_handoff_port, open_studio_creation_room


STUDIO_EXPERIENCE_VERSION = 1
STUDIO_SOURCE_NAMES = ("viewspec.app.json", "viewspec.intent.json")


def resolve_studio_source(
    source: str | Path | None,
    *,
    cwd: str | Path | None = None,
) -> Path:
    """Resolve one explicit source or the only canonical source in the workspace."""

    if source is not None:
        return Path(source)
    root = Path(cwd) if cwd is not None else Path.cwd()
    candidates = [root / name for name in STUDIO_SOURCE_NAMES if (root / name).is_file()]
    if not candidates:
        raise ReviewContractError(
            "STUDIO_SOURCE_NOT_FOUND",
            "ViewSpec Studio could not find viewspec.app.json or viewspec.intent.json.",
            "Give your agent a brief and open one room: viewspec studio --brief-file product-brief.md.",
            cli_exit=2,
        )
    if len(candidates) > 1:
        raise ReviewContractError(
            "STUDIO_SOURCE_AMBIGUOUS",
            "ViewSpec Studio found both viewspec.app.json and viewspec.intent.json.",
            "Name the interface you want to open: viewspec studio SOURCE.",
            cli_exit=2,
        )
    return candidates[0]


def open_studio(
    source: str | Path | None = None,
    *,
    cwd: str | Path | None = None,
    design: str | Path | None = None,
    target: str | None = None,
    port: int = DEFAULT_REVIEW_PORT,
    state_root: str | Path | None = None,
    convergence_state_root: str | Path | None = None,
    reopen: bool = False,
    no_open: bool = False,
    verify: bool = False,
    install: bool = False,
    compare: bool = False,
    share: bool = False,
    share_reference: str | Path | None = None,
    brief: str | None = None,
    brief_file: str | Path | None = None,
    reference: str | Path | None = None,
    kind: str = "app",
    task_out: str | Path = STUDIO_CREATION_TASK_DEFAULT,
) -> dict[str, object]:
    """Open the checked Preview → Comment → Approve experience."""

    started = time.perf_counter_ns()
    creation_requested = brief is not None or brief_file is not None or reference is not None
    if creation_requested:
        if source is not None:
            raise ReviewContractError(
                "STUDIO_CREATION_OPTIONS_INVALID",
                "Studio cannot combine an explicit semantic source with a first-creation brief.",
                "Open the existing source, or omit SOURCE and create from the brief.",
                cli_exit=2,
            )
        if share or share_reference is not None:
            raise ReviewContractError(
                "STUDIO_CREATION_OPTIONS_INVALID",
                "A first-creation room cannot create a private review before its product is checked.",
                "Open the local creation room first; choose Share only from a checked comparison.",
                cli_exit=2,
            )
        prepared = prepare_studio_creation(
            brief=brief,
            brief_file=brief_file,
            reference=reference,
            kind=kind,
            task_out=task_out,
            cwd=cwd,
        )
        creation = prepared.get("creation")
        paths = prepared.get("paths")
        if not isinstance(creation, dict) or not isinstance(paths, dict) or not isinstance(paths.get("task"), str):
            raise ReviewContractError(
                "STUDIO_CREATION_ROOM_FAILED",
                "Studio did not receive a complete deterministic creation task.",
                "Retry the exact local brief.",
                cli_exit=1,
            )
        source_kind = str(creation.get("source_kind"))
        selected_target = _creation_target(
            source_kind=source_kind,
            target=target,
            verify=verify,
            install=install,
            compare=compare,
        )
        if creation.get("task_action") == "accepted":
            resume_port = accepted_creation_handoff_port(
                paths["task"],
                cwd=cwd,
                design=design,
                target=selected_target,
                requested_port=port,
                state_root=state_root,
                convergence_state_root=convergence_state_root,
                verify=verify,
                install=install,
                expected_source_sha256=str(creation["source_sha256"]),
            )
            resumed = open_studio(
                paths.get("source"),
                cwd=cwd,
                design=design,
                target=target,
                port=resume_port or port,
                state_root=state_root,
                convergence_state_root=convergence_state_root,
                reopen=reopen,
                no_open=no_open,
                verify=verify,
                install=install,
                compare=compare,
            )
            resumed["prepared_creation"] = creation
            return resumed
        payload = open_studio_creation_room(
            paths["task"],
            cwd=cwd,
            design=design,
            target=selected_target,
            port=port,
            state_root=state_root,
            convergence_state_root=convergence_state_root,
            no_open=no_open,
            verify=verify,
            install=install,
        )
        payload["prepared_creation"] = creation
        return payload
    resolved = resolve_studio_source(source, cwd=cwd)
    if share_reference is not None and not share:
        raise ReviewContractError(
            "STUDIO_SHARE_REFERENCE_INVALID",
            "A private-review reference is meaningful only when Share is explicitly enabled.",
            "Add --share or remove --share-reference.",
            cli_exit=2,
        )
    if share and not compare:
        raise ReviewContractError(
            "STUDIO_SHARE_COMPARISON_REQUIRED",
            "Private Share requires one checked static/React AppBundle comparison.",
            "Use viewspec studio --compare --install --share.",
            cli_exit=2,
        )
    selected_target = target
    if compare:
        if target is not None or verify:
            raise ReviewContractError(
                "STUDIO_COMPARISON_INVALID",
                "Studio comparison owns both targets and cannot combine with --target or --verify.",
                "Remove --target and --verify; use viewspec studio --compare --install.",
                cli_exit=2,
            )
        if not install:
            raise ReviewContractError(
                "STUDIO_COMPARISON_INSTALL_REQUIRED",
                "Studio comparison requires the exact locked React runtime dependencies.",
                "Pass --install to authorize the bounded npm ci --ignore-scripts build flow.",
                cli_exit=2,
            )
        snapshot = capture_source_snapshot(resolved, design_path=design)
        if snapshot.source_kind != "app_bundle":
            raise ReviewContractError(
                "STUDIO_COMPARISON_REQUIRES_APP",
                "Static/React Studio comparison requires one AppBundle product source.",
                "Create or open viewspec.app.json; one-screen IntentBundle comparison is not yet supported.",
                cli_exit=2,
            )
        selected_target = STUDIO_COMPARE_TARGET
    payload = open_review(
        resolved,
        design=design,
        target=selected_target,
        port=port,
        state_root=state_root,
        convergence_state_root=convergence_state_root,
        reopen=reopen,
        no_open=no_open,
        verify=verify,
        install=install,
        studio_share=share,
        studio_share_reference=share_reference,
    )
    review = payload["review"]
    if not isinstance(review, dict):
        raise ReviewContractError(
            "REVIEW_SERVER_START_FAILED",
            "ViewSpec Studio did not receive a complete checked revision.",
            "Restart the local Studio session.",
            cli_exit=1,
        )
    verification_status = str(review.get("verification_status", "not_run"))
    ready_ms = max(0, round((time.perf_counter_ns() - started) / 1_000_000))
    studio = {
        "experience_version": STUDIO_EXPERIENCE_VERSION,
        "status": "ready",
        "source_name": resolved.name,
        "source_kind": review.get("source_kind"),
        "target": review.get("target"),
        "revision": review.get("revision"),
        "primary_loop": ["preview", "comment", "approve"],
        "primary_action": "comment",
        "ready_ms": ready_ms,
        "confidence": {
            "state": "verified" if verification_status == "conformant" else "checked",
            "artifact_check": review.get("check_status"),
            "viewport_verification": verification_status,
            "network": "private_review_opt_in" if share else "loopback_only",
        },
    }
    if compare:
        studio["comparison"] = {
            "status": "ready",
            "targets": ["html-tailwind-app", "react-tailwind-app"],
            "synchronized": ["viewport", "route", "semantic_identity"],
            "visual_parity": "not_proven",
            "dependency_install": "explicit_opt_in",
            "inspection": review.get("inspection"),
        }
    if share:
        studio["private_review"] = review.get("share")
    return {
        **payload,
        "summary": "ViewSpec Studio is ready.",
        "studio": studio,
        "next_actions": [
            "Preview the interface in the local Studio canvas.",
            "Choose Comment, point at the result, and describe one desired change.",
            *( ["Choose Share to inspect the exact disclosure before creating a private link."] if share else [] ),
            "Keep this task running so your agent can receive the feedback by semantic identity.",
        ],
    }


def _creation_target(
    *,
    source_kind: str,
    target: str | None,
    verify: bool,
    install: bool,
    compare: bool,
) -> str | None:
    if not compare:
        return target
    if target is not None or verify:
        raise ReviewContractError(
            "STUDIO_COMPARISON_INVALID",
            "Studio comparison owns both targets and cannot combine with --target or --verify.",
            "Remove --target and --verify; use viewspec studio --compare --install.",
            cli_exit=2,
        )
    if not install:
        raise ReviewContractError(
            "STUDIO_COMPARISON_INSTALL_REQUIRED",
            "Studio comparison requires the exact locked React runtime dependencies.",
            "Pass --install to authorize the bounded npm ci --ignore-scripts build flow.",
            cli_exit=2,
        )
    if source_kind != "app_bundle":
        raise ReviewContractError(
            "STUDIO_COMPARISON_REQUIRES_APP",
            "Static/React Studio comparison requires one AppBundle product source.",
            "Use --kind app for a product; one-screen IntentBundle comparison is not yet supported.",
            cli_exit=2,
        )
    return STUDIO_COMPARE_TARGET


__all__ = [
    "STUDIO_EXPERIENCE_VERSION",
    "STUDIO_SOURCE_NAMES",
    "open_studio",
    "resolve_studio_source",
]
