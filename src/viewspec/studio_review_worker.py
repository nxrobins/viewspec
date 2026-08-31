"""Bounded stdin/stdout worker for an isolated Studio review rebuild."""

from __future__ import annotations

import json
import sys

from viewspec.review_contract import canonical_json_bytes
from viewspec.studio_review_verify import (
    STUDIO_REVIEW_REBUILD_REQUEST_MAX_BYTES,
    StudioReviewVerificationError,
    rebuild_studio_review_request,
)


STUDIO_REVIEW_WORKER_SCHEMA_VERSION = 1
_FAILURE_RESPONSE = canonical_json_bytes({
    "schema_version": STUDIO_REVIEW_WORKER_SCHEMA_VERSION,
    "ok": False,
    "error": {
        "code": "STUDIO_REVIEW_VERIFICATION_FAILED",
        "message": "Isolated Studio review rebuild failed closed.",
    },
})


def run_studio_review_worker(input_bytes: bytes) -> tuple[int, bytes]:
    """Return one bounded machine response without leaking worker internals."""

    try:
        if not isinstance(input_bytes, bytes) or not 1 <= len(input_bytes) <= STUDIO_REVIEW_REBUILD_REQUEST_MAX_BYTES:
            raise StudioReviewVerificationError("Isolated rebuild worker input is outside its byte boundary.")
        request = json.loads(input_bytes)
        if not isinstance(request, dict):
            raise StudioReviewVerificationError("Isolated rebuild worker input is not one JSON object.")
        evidence = rebuild_studio_review_request(request)
        response = {
            "schema_version": STUDIO_REVIEW_WORKER_SCHEMA_VERSION,
            "ok": True,
            "evidence": evidence,
        }
        return 0, canonical_json_bytes(response)
    except Exception:
        # This is a machine-response boundary. Unexpected operational failures
        # must not export request-derived messages or traceback paths. Process
        # control exceptions (KeyboardInterrupt/SystemExit) still propagate.
        return 1, _FAILURE_RESPONSE


def main() -> int:
    try:
        content = sys.stdin.buffer.read(STUDIO_REVIEW_REBUILD_REQUEST_MAX_BYTES + 1)
    except Exception:
        status, response = 1, _FAILURE_RESPONSE
    else:
        status, response = run_studio_review_worker(content)
    try:
        sys.stdout.buffer.write(response + b"\n")
        sys.stdout.buffer.flush()
    except Exception:
        # There is no response channel left. Close the failed stream so Python
        # cannot print an ignored flush exception during interpreter shutdown.
        try:
            sys.stdout.close()
        except Exception:
            pass
        return 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["STUDIO_REVIEW_WORKER_SCHEMA_VERSION", "run_studio_review_worker"]
