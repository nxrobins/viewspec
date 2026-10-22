"""Prepared AppBundle output path containers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PreparedAppProof:
    output_dir: Path
    app_path: Path
    design_path: Path | None
    report_path: Path
    summary_path: Path
    support_path: Path


@dataclass(frozen=True)
class PreparedAppShell:
    output_dir: Path
    app_path: Path
    design_path: Path | None
    manifest_path: Path
    diagnostics_path: Path
    index_path: Path


# Backward-compatible private aliases; the un-prefixed names are canonical.
_PreparedAppProof = PreparedAppProof
_PreparedAppShell = PreparedAppShell

__all__ = ["PreparedAppProof", "PreparedAppShell"]
