"""Shared AppBundle proof errors."""

from __future__ import annotations


class AppBundleProofFailure(ValueError):
    """Stable-code AppBundle proof failure."""

    def __init__(self, code: str, message: str, fix: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.fix = fix


__all__ = ["AppBundleProofFailure"]
