"""Shared atomic artifact-set writer and the ``.viewspec_write_failed.json`` sentinel.

Every artifact writer (the three emitters and the raw-HTML importer) writes its files one by one
with a temp-file-and-rename so a reader never sees a partial file, and records a sentinel in the
output directory when any write fails so a half-written artifact set is never mistaken for a
finished one.  The sentinel content, and the warning logged when even the sentinel cannot be
written, are the same for every writer.
"""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Iterable
from pathlib import Path

ARTIFACT_WRITE_FAILED_SENTINEL = ".viewspec_write_failed.json"
ARTIFACT_WRITE_FAILED_CODE = "ARTIFACT_WRITE_FAILED"

_LOGGER = logging.getLogger(__name__)


def write_text_atomic(path: Path, text: str) -> None:
    """Write ``text`` as UTF-8 with no newline translation, via a sibling temp file and rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(text)
        Path(temp_name).replace(path)
    except Exception:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)
        raise


def write_artifact_set(
    output_dir: Path,
    files: Iterable[tuple[Path, str]],
    *,
    logger: logging.Logger | None = None,
) -> None:
    """Write ``files`` in order; when any write fails, record the sentinel in ``output_dir`` and re-raise.

    ``files`` may be a generator: it is consumed inside the failure guard, so a producer that derives
    one file from an earlier write (the raw-HTML manifest hashes the written ``index.html``) is
    covered by the same sentinel.  ``logger`` names the logger that receives the warning when the
    sentinel itself cannot be written; it defaults to this module's logger.
    """
    try:
        for path, text in files:
            write_text_atomic(path, text)
    except Exception as exc:
        record_artifact_write_failure(output_dir, exc, logger=logger)
        raise


def record_artifact_write_failure(output_dir: Path, exc: Exception, *, logger: logging.Logger | None = None) -> None:
    """Write the ``.viewspec_write_failed.json`` sentinel for ``exc``; log a warning if that fails too."""
    sentinel_path = output_dir / ARTIFACT_WRITE_FAILED_SENTINEL
    try:
        write_text_atomic(
            sentinel_path,
            json.dumps(
                {
                    "version": 1,
                    "severity": "error",
                    "code": ARTIFACT_WRITE_FAILED_CODE,
                    "message": str(exc),
                },
                indent=2,
                sort_keys=True,
            ),
        )
    except Exception as sentinel_exc:
        (logger or _LOGGER).warning(
            "Could not record the artifact write failure in %s: %s", sentinel_path, sentinel_exc, exc_info=sentinel_exc
        )


__all__ = [
    "ARTIFACT_WRITE_FAILED_CODE",
    "ARTIFACT_WRITE_FAILED_SENTINEL",
    "record_artifact_write_failure",
    "write_artifact_set",
    "write_text_atomic",
]
