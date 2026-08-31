"""Exercise the emitted frame clients with a deterministic browser clock."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from viewspec.review_server import _frame_sdk
from viewspec.studio_review_http import _HOSTED_FRAME_CLIENT


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
@pytest.mark.parametrize("surface", ["hosted", "local"])
@pytest.mark.parametrize("case", ["delayed_initial", "wrong_initial_route", "duplicate_screen", "timeout_terminal", "replay_route", "replay_timeout", "payload_rejected"])
def test_frame_waits_for_committed_screen(surface: str, case: str) -> None:
    script = _HOSTED_FRAME_CLIENT if surface == "hosted" else _frame_sdk("test-nonce", surface_target="react-tailwind-app").decode()
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("review_frame_readiness.mjs"))],
        input=json.dumps({"script": script, "surface": surface, "case": case}),
        text=True, capture_output=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
