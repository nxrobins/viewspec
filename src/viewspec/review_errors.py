"""Stable Review V0 failure constructors shared by bounded runtime phases."""

from __future__ import annotations

from viewspec.review_contract import ReviewContractError


_ERROR_DEFAULTS: dict[str, tuple[int, int, str]] = {
    "REVIEW_BROWSER_HANDSHAKE_TIMEOUT": (409, 2, "Reload the current frame and complete its handshake within 5 seconds."),
    "REVIEW_CHECK_TIMEOUT": (504, 2, "Reduce the artifact or repair the checker so check completes within 10 seconds."),
    "REVIEW_COMPACTION_FAILED": (507, 1, "Preserve the old journal and retry compaction after restoring local storage."),
    "REVIEW_COMPILE_TIMEOUT": (504, 2, "Reduce semantic source so compilation completes within 30 seconds."),
    "REVIEW_CONTEXT_RESET": (409, 2, "Use the declared default route and scroll position (0, 0)."),
    "REVIEW_DESIGN_INVALID": (422, 2, "Fix the captured DESIGN.md and retry the newest source generation."),
    "REVIEW_DIFF_TIMEOUT": (504, 2, "Reduce the semantic change set so diff completes within 10 seconds."),
    "REVIEW_ERROR_REPORT_TOO_LARGE": (422, 2, "Reduce the bounded source error projection."),
    "REVIEW_EXTERNAL_REFERENCE_FORBIDDEN": (422, 2, "Remove every remote runtime reference and recompile locally."),
    "REVIEW_INSTRUMENTATION_VIOLATION": (409, 2, "Reload the unmodified checked artifact with the packaged Review SDK."),
    "REVIEW_JOURNAL_INVALID": (500, 2, "Treat the session as corrupt and open a new bounded review."),
    "REVIEW_LOG_POLICY_VIOLATION": (500, 1, "Remove sensitive or unknown fields before writing the closed Review log record."),
    "REVIEW_LOG_WRITE_FAILED": (507, 1, "Restore private local log storage before retrying the mutation."),
    "REVIEW_REVISION_NOT_PROMOTED": (409, 2, "Continue serving the durable last-good revision and retry the candidate."),
    "REVIEW_SECURITY_POLICY_FAILED": (409, 2, "Disable the frame and reopen a fresh checked Review session."),
    "REVIEW_SNAPSHOT_TIMEOUT": (504, 2, "Move inputs to a supported local filesystem and retry the 5-second snapshot."),
    "REVIEW_VALIDATE_TIMEOUT": (504, 2, "Reduce semantic source so validation completes within 5 seconds."),
    "REVIEW_VERIFICATION_TIMEOUT": (504, 2, "Retry canonical viewport verification within its 180-second deadline."),
}


def make_review_error(code: str, message: str) -> ReviewContractError:
    try:
        status, cli_exit, fix = _ERROR_DEFAULTS[code]
    except KeyError as exc:
        raise ValueError(f"unknown Review error code: {code}") from exc
    return ReviewContractError(code, message, fix, http_status=status, cli_exit=cli_exit)


__all__ = ["make_review_error"]
