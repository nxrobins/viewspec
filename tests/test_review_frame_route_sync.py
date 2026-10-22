"""Record the route-sync causes the served Review frames post, in the pinned Chromium.

Studio relays a frame's ``navigation`` context to the other comparison frame, so a restore that a
frame reports as a navigation sends the other frame back to the route it just left.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from viewspec.app_starters import starter_app_bundle
from viewspec.review_compile import STUDIO_COMPARE_TARGET
from viewspec.review_runtime import ReviewRuntime
from viewspec.review_server import ReviewServer
from viewspec.studio_review_verify import STUDIO_REVIEW_DEPENDENCY_SEED_ENV

ROOT = Path(__file__).resolve().parents[1]
_NODE = shutil.which("node")
# CI installs this seed with `npm ci --ignore-scripts` and its Chromium in the main test job.
_SEED = ROOT / "src" / "viewspec" / "host_verify_template" / "node_modules"
_PLAYWRIGHT = _SEED / "playwright" / "index.mjs"
_DRIVER = Path(__file__).with_name("review_frame_route_sync.mjs")


def _pinned_chromium_executable() -> Path | None:
    """Ask the pinned Playwright module where its Chromium must live; None when it cannot be asked."""
    if not _NODE or not _PLAYWRIGHT.is_file():
        return None
    probe = subprocess.run(
        [
            _NODE,
            "--input-type=module",
            "-e",
            "import {pathToFileURL} from 'node:url';"
            "const {chromium} = await import(pathToFileURL(process.argv[1]).href);"
            "process.stdout.write(chromium.executablePath());",
            str(_PLAYWRIGHT),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if probe.returncode != 0 or not probe.stdout.strip():
        return None
    return Path(probe.stdout.strip())


_CHROMIUM = _pinned_chromium_executable()
pytestmark = pytest.mark.skipif(
    _CHROMIUM is None or not _CHROMIUM.is_file() or not (_SEED / ".bin" / "vite").exists(),
    reason=(
        "Node, the host-verify dependency seed and its pinned Chromium are required "
        "(npm ci --ignore-scripts, then npx --no-install playwright install chromium, "
        "in src/viewspec/host_verify_template)"
    ),
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(scope="module")
def observed(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, list[dict[str, str]]]]:
    """Serve the real checked static and React frames and drive each one through the same steps."""
    workspace = tmp_path_factory.mktemp("route-sync")
    source = workspace / "viewspec.app.json"
    source.write_text(json.dumps(starter_app_bundle("internal_tool"), sort_keys=True), encoding="utf-8")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(STUDIO_REVIEW_DEPENDENCY_SEED_ENV, str(_SEED))
        runtime = ReviewRuntime.open(
            source,
            state_root=workspace / "state",
            target=STUDIO_COMPARE_TARGET,
            allow_install=True,
        )
    server = ReviewServer(runtime, port=_free_port())
    server.start()
    try:
        result = subprocess.run(
            [_NODE or "node", str(_DRIVER)],
            input=json.dumps(
                {
                    "playwright": str(_PLAYWRIGHT),
                    "origin": server.origin,
                    "nonce": server.frame_nonce,
                    "frames": {
                        "html-tailwind-app": server.origin + server.frame_path("static/index.html"),
                        "react-tailwind-app": server.origin + server.frame_path("react/index.html"),
                    },
                }
            ),
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
    finally:
        server.stop()
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _causes(contexts: list[dict[str, str]]) -> list[tuple[str, str]]:
    return [(item["cause"], item["route"]) for item in contexts]


@pytest.mark.parametrize("surface", ["html-tailwind-app", "react-tailwind-app"])
def test_studio_restore_is_reported_only_as_a_restore(observed, surface: str) -> None:
    # The hash-routed static surface used to report the fragment change behind a restore through
    # popstate as well, as a navigation that could still carry the route shown before the restore.
    contexts = _causes(observed[surface]["restore"])
    assert contexts, "the restore posted no context"
    assert {cause for cause, _ in contexts} == {"restore"}, contexts
    assert contexts[-1] == ("restore", "/incident")
    assert observed[surface]["errors"] == []


@pytest.mark.parametrize("surface", ["html-tailwind-app", "react-tailwind-app"])
def test_reviewer_navigation_is_reported_once(observed, surface: str) -> None:
    assert _causes(observed[surface]["navigation"]) == [("navigation", "/")]
    assert _causes(observed[surface]["back"]) == [("navigation", "/incident")]
    assert observed[surface]["errors"] == []
