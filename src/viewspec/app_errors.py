"""Shared AppBundle proof errors."""

from __future__ import annotations


class AppBundleProofFailure(ValueError):
    """Stable-code AppBundle proof failure."""

    def __init__(self, code: str, message: str, fix: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.fix = fix


def _normalize_proof_errors(errors: object) -> list[dict[str, str]]:
    if not isinstance(errors, list) or not errors:
        return []
    normalized: list[dict[str, str]] = []
    for item in errors:
        if isinstance(item, dict):
            normalized.append(
                {
                    "code": str(item.get("code") or "APP_PROOF_FAILED"),
                    "message": str(item.get("message") or "App proof failed."),
                    "fix": str(item.get("fix") or "Inspect app_proof_report.json and retry."),
                    **({"screen_id": str(item["screen_id"])} if item.get("screen_id") else {}),
                }
            )
        else:
            normalized.append({"code": "APP_PROOF_FAILED", "message": str(item), "fix": "Inspect app_proof_report.json and retry."})
    return normalized


__all__ = ["AppBundleProofFailure", "_normalize_proof_errors"]
