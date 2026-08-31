"""The standalone rebuild worker emits one fixed envelope on operational failure."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from viewspec import studio_review_worker as worker


FAILURE = {
    "schema_version": 1,
    "ok": False,
    "error": {
        "code": "STUDIO_REVIEW_VERIFICATION_FAILED",
        "message": "Isolated Studio review rebuild failed closed.",
    },
}
PRIVATE_MARKER = "synthetic-private-worker-fixture-7f901b"


def test_library_worker_does_not_propagate_unexpected_exception_content(monkeypatch):
    def broken_rebuild(_request):
        raise RuntimeError(PRIVATE_MARKER)

    monkeypatch.setattr(worker, "rebuild_studio_review_request", broken_rebuild)
    status, response = worker.run_studio_review_worker(b"{}")
    assert status == 1
    assert json.loads(response) == FAILURE
    assert PRIVATE_MARKER.encode() not in response


@pytest.mark.parametrize("case", ["expected", "runtime", "recursion", "io", "encoding", "serialization", "deep_json", "stdin_failure", "stdout_closed", "success"])
def test_real_worker_process_has_one_content_free_failure_envelope(case):
    script = r'''
import os, sys
from viewspec import studio_review_worker as worker
case, marker = sys.argv[1:]
def rebuild(_request):
    if case == "expected":
        raise worker.StudioReviewVerificationError(marker)
    if case == "runtime":
        raise RuntimeError(marker)
    if case == "recursion":
        raise RecursionError(marker)
    if case == "io":
        raise OSError(marker)
    if case == "encoding":
        return {"value": "\ud800"}
    if case == "serialization":
        return {"value": object()}
    return {"source_sha256": "a" * 64}
if case != "deep_json":
    worker.rebuild_studio_review_request = rebuild
if case == "stdin_failure":
    class BrokenInput:
        @property
        def buffer(self):
            return self
        def read(self, _limit):
            raise OSError(marker)
    sys.stdin = BrokenInput()
if case == "stdout_closed":
    os.close(1)
raise SystemExit(worker.main())
'''
    content = b'{"nested":' * 1400 + b"null" + b"}" * 1400 if case == "deep_json" else b"{}"
    result = subprocess.run(
        [sys.executable, "-c", script, case, PRIVATE_MARKER], input=content,
        capture_output=True, timeout=10, check=False,
        env={"PATH": os.defpath, "PYTHONPATH": str(Path(__file__).parents[1] / "src"), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == (0 if case == "success" else 1)
    assert result.stderr == b"", "Worker diagnostics must not expose exception content or traceback paths."
    assert PRIVATE_MARKER.encode() not in result.stdout
    if case == "stdout_closed":
        assert result.stdout == b""
        return
    assert result.stdout.count(b"\n") == 1
    expected = {"schema_version": 1, "ok": True, "evidence": {"source_sha256": "a" * 64}} if case == "success" else FAILURE
    assert json.loads(result.stdout) == expected


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit])
def test_worker_preserves_process_control_exceptions(monkeypatch, exception):
    def interrupted(_request):
        raise exception()
    monkeypatch.setattr(worker, "rebuild_studio_review_request", interrupted)
    with pytest.raises(exception):
        worker.run_studio_review_worker(b"{}")
