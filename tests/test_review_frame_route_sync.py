"""Record the route-sync causes the served Review frames post, in the pinned Chromium.

Studio relays a frame's ``navigation`` context to the other comparison frame when that frame is on
another route, so a frame that reports a restore as a navigation to the route it is leaving sends
the other frame back there. The local Studio page and the Private Studio review page inject
different frame clients; both are driven here.
"""

from __future__ import annotations

import base64
import hashlib
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
from viewspec.studio_review_http import ReviewHTTPRequest, StudioReviewHTTPAdapter
from viewspec.studio_review_service import HostedReviewArtifact, StudioReviewService
from viewspec.studio_review_verify import STUDIO_REVIEW_DEPENDENCY_SEED_ENV

ROOT = Path(__file__).resolve().parents[1]
_NODE = shutil.which("node")
# CI installs this seed with `npm ci --ignore-scripts` and its Chromium in the main test job.
_SEED = ROOT / "src" / "viewspec" / "host_verify_template" / "node_modules"
_PLAYWRIGHT = _SEED / "playwright" / "index.mjs"
_DRIVER = Path(__file__).with_name("review_frame_route_sync.mjs")
_HOSTED_ORIGIN = "https://review.viewspec.test"
_HOSTED_SESSION = "vsr_" + "A" * 24
_HOSTED_CHANNEL = "vsf_" + "B" * 24

Observed = dict[str, dict[str, list[dict[str, str]]]]


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


def _drive(payload: dict[str, object]) -> Observed:
    result = subprocess.run(
        [_NODE or "node", str(_DRIVER)],
        input=json.dumps({"playwright": str(_PLAYWRIGHT), **payload}),
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def runtime(tmp_path_factory: pytest.TempPathFactory) -> ReviewRuntime:
    """Build the real checked static and React frames once, for both frame clients."""
    workspace = tmp_path_factory.mktemp("route-sync")
    source = workspace / "viewspec.app.json"
    source.write_text(json.dumps(starter_app_bundle("internal_tool"), sort_keys=True), encoding="utf-8")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(STUDIO_REVIEW_DEPENDENCY_SEED_ENV, str(_SEED))
        return ReviewRuntime.open(
            source,
            state_root=workspace / "state",
            target=STUDIO_COMPARE_TARGET,
            allow_install=True,
        )


@pytest.fixture(scope="module")
def observed(runtime: ReviewRuntime) -> Observed:
    """Serve the checked frames from a ReviewServer and drive each one through the same steps."""
    server = ReviewServer(runtime, port=_free_port())
    server.start()
    try:
        return _drive(
            {
                "protocol": "local",
                "origin": server.origin,
                "nonce": server.frame_nonce,
                "frames": {
                    "html-tailwind-app": server.origin + server.frame_path("static/index.html"),
                    "react-tailwind-app": server.origin + server.frame_path("react/index.html"),
                },
            }
        )
    finally:
        server.stop()


def _unused_verifier(_package: Path, _envelope: dict[str, object]) -> dict[str, object]:
    raise AssertionError("no review session is created")


class _CheckedArtifactService(StudioReviewService):
    """Serve the checked artifacts of a built revision the way a hosted session serves them.

    A share packages the built artifact directory verbatim under ``artifacts/``, so this returns
    the bytes a session would, without creating one.
    """

    def __init__(self, root: Path, artifact_dir: Path) -> None:
        super().__init__(root, signing_key=b"test-only-route-sync-signing-key-material", verifier=_unused_verifier)
        self._artifact_dir = artifact_dir

    def read_artifact(self, browser_session: str, artifact_path: str) -> HostedReviewArtifact:
        content = (self._artifact_dir / artifact_path.removeprefix("artifacts/")).read_bytes()
        return HostedReviewArtifact(
            path=artifact_path,
            media_type="text/html",
            sha256=hashlib.sha256(content).hexdigest(),
            content=content,
        )


@pytest.fixture(scope="module")
def hosted_observed(runtime: ReviewRuntime, tmp_path_factory: pytest.TempPathFactory) -> Observed:
    """Serve the static frame as StudioReviewHTTPAdapter derives it and drive it through the same steps.

    Only the static surface: the hosted React frame is restored through an app event, not through
    its history.
    """
    service = _CheckedArtifactService(tmp_path_factory.mktemp("hosted-review"), runtime.built.artifact_dir)
    adapter = StudioReviewHTTPAdapter(service, public_origin=_HOSTED_ORIGIN, authorize_upload=lambda _headers: False)
    path = f"/review/{_HOSTED_SESSION}/artifact/static/index.html"
    response = adapter.handle(ReviewHTTPRequest(method="GET", path=path, headers={}, body=b"", scheme="https"))
    assert response.status == 200, response.body[:512]
    return _drive(
        {
            "protocol": "hosted",
            "origin": _HOSTED_ORIGIN,
            "channel": _HOSTED_CHANNEL,
            "responses": {
                path: {
                    "status": response.status,
                    "headers": dict(response.headers),
                    "body": base64.b64encode(response.body).decode("ascii"),
                }
            },
            # The review page opens each frame with its message channel and route in the fragment.
            "frames": {"html-tailwind-app": f"{_HOSTED_ORIGIN}{path}#channel={_HOSTED_CHANNEL}&route=%2F"},
        }
    )


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


def test_private_review_restore_names_no_other_route(hosted_observed) -> None:
    # The hosted client reports the fragment change behind a restore from hashchange, as a
    # navigation to the restored route, which the review page already shows and so does not relay.
    # Popstate used to report the same change first, naming the route the frame was leaving, and the
    # review page then sent the React frame back to it.
    contexts = _causes(hosted_observed["html-tailwind-app"]["restore"])
    assert "restore" in {cause for cause, _ in contexts}, contexts
    assert {route for cause, route in contexts if cause == "navigation"} <= {"/incident"}, contexts
    assert contexts[-1][1] == "/incident", contexts
    assert hosted_observed["html-tailwind-app"]["errors"] == []


def test_private_review_navigation_is_reported_once(hosted_observed) -> None:
    observed = hosted_observed["html-tailwind-app"]
    assert _causes(observed["navigation"]) == [("navigation", "/")]
    assert _causes(observed["back"]) == [("navigation", "/incident")]
    assert observed["errors"] == []
