from __future__ import annotations

import http.client
import json
from pathlib import Path
import socket
import time
from urllib.parse import urlsplit

import pytest

from viewspec.intent_tools import starter_intent_payload
from viewspec.review_cli import end_review
from viewspec.review_contract import ReviewContractError
from viewspec.studio import open_studio
from viewspec.studio_creation import StudioCreationError, prepare_studio_creation
from viewspec.studio_creation_room import (
    StudioCreationHandoff,
    StudioCreationRoomController,
    StudioCreationRoomServer,
)


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _request(
    port: int,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        body = response.read()
        return response.status, {key.lower(): value for key, value in response.getheaders()}, body
    finally:
        connection.close()


def test_creation_room_controller_exposes_truthful_failure_retry_and_checked_handoff(
    tmp_path,
    monkeypatch,
) -> None:
    prepared = prepare_studio_creation(brief="Build a calm field dispatch product", cwd=tmp_path)
    candidate = Path(prepared["paths"]["candidate"])
    now = [100.0]
    attempts: list[bytes] = []
    handoffs: list[Path] = []

    def fake_accept(_task, *, cwd):
        assert Path(cwd) == tmp_path
        attempts.append(candidate.read_bytes())
        if len(attempts) == 1:
            raise StudioCreationError(
                "STUDIO_CREATION_CANDIDATE_INVALID",
                f"The candidate at {candidate} is incomplete.",
                f"Rewrite {candidate}.",
            )
        source = tmp_path / "viewspec.app.json"
        source.write_bytes(candidate.read_bytes())
        return {
            "creation": {
                "source_sha256": "a" * 64,
                "candidate_validation": "passed",
                "artifact_check": "passed",
            }
        }

    def fake_handoff(source, accepted):
        handoffs.append(source)
        assert accepted["creation"]["artifact_check"] == "passed"
        return StudioCreationHandoff(
            url="http://127.0.0.1:4399/open/review-token",
            source_sha256="a" * 64,
            review={"target": "html-tailwind-app", "revision": 1},
        )

    monkeypatch.setattr("viewspec.studio_creation_room.accept_studio_creation", fake_accept)
    controller = StudioCreationRoomController(
        cwd=tmp_path,
        handoff=fake_handoff,
        clock=lambda: now[0],
    )
    assert controller.status()["headline"] == "Waiting for agent"

    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_bytes(b'{"candidate":"broken"}')
    controller.poll_once()
    now[0] += 0.3
    controller.poll_once()
    failed = controller.status()
    assert failed["headline"] == "Candidate needs one fix"
    assert failed["checks"] == {"candidate_validation": "failed", "artifact_check": "not_run"}
    assert failed["error"] == {
        "code": "STUDIO_CREATION_CANDIDATE_INVALID",
        "message": "Studio could not check this candidate as the required product source.",
        "fix": (
            "Ask the agent to update the task-bound candidate and save it again. "
            "Studio will retry automatically."
        ),
    }
    assert not (tmp_path / "viewspec.app.json").exists()

    candidate.write_bytes(b'{"candidate":"healthy-and-different"}')
    controller.poll_once()
    now[0] += 0.3
    controller.poll_once()
    assert controller.status()["headline"] == "Checking candidate"
    now[0] += 0.4
    controller.poll_once()
    checked = controller.status()
    assert checked["headline"] == "Checked product ready"
    assert checked["checks"] == {"candidate_validation": "passed", "artifact_check": "passed"}
    assert checked["stage_history"] == [
        "waiting",
        "checking",
        "needs_fix",
        "waiting",
        "checking",
        "checked",
    ]
    assert checked["handoff_ready"] is True
    assert handoffs == [tmp_path / "viewspec.app.json"]
    assert controller.consume_handoff() == "http://127.0.0.1:4399/open/review-token"
    assert controller.consume_handoff() is None


def test_creation_room_browser_projection_is_capability_scoped_and_path_free(tmp_path) -> None:
    brief = "Build a product for field crews without exposing implementation machinery."
    prepare_studio_creation(brief=brief, cwd=tmp_path)
    controller = StudioCreationRoomController(
        cwd=tmp_path,
        handoff=lambda _source, _accepted: pytest.fail("handoff should not run"),
    )
    server = StudioCreationRoomServer(controller, port=_available_port())
    server.start()
    try:
        status, _, _ = _request(server.port, "GET", "/v1/status")
        assert status == 404
        status, _, _ = _request(server.port, "GET", "/", headers={"Host": "attacker.example"})
        assert status == 404

        status, headers, _ = _request(server.port, "GET", server.bootstrap_url.removeprefix(server.origin))
        assert status == 303
        assert headers["location"] == "/"
        cookie = headers["set-cookie"].split(";", 1)[0]

        status, headers, body = _request(server.port, "GET", "/", headers={"Cookie": cookie})
        assert status == 200
        assert headers["cache-control"] == "no-store"
        assert "default-src 'none'" in headers["content-security-policy"]
        text = body.decode("utf-8")
        assert "Waiting for agent" in text
        assert str(tmp_path) not in text
        assert server.agent_token not in text

        status, _, body = _request(server.port, "GET", "/v1/status", headers={"Cookie": cookie})
        assert status == 200
        projection = json.loads(body)
        assert set(projection) == {
            "brief",
            "checks",
            "detail",
            "elapsed_ms",
            "handoff_ready",
            "headline",
            "network_calls",
            "ok",
            "reference",
            "schema_version",
            "source_kind",
            "stage",
            "stage_history",
        }
        assert projection["brief"] == brief
        serialized = json.dumps(projection)
        assert str(tmp_path) not in serialized
        assert ".viewspec" not in serialized
        assert "candidate_path" not in serialized
        assert "candidate_schema" not in serialized
        assert "task_id" not in serialized
        assert "capability" not in serialized
        assert "sha256" not in serialized

        status, _, body = _request(
            server.port,
            "GET",
            "/internal/v1/status",
            headers={"X-ViewSpec-Agent-Capability": server.agent_token},
        )
        assert status == 200
        assert json.loads(body)["stage"] == "waiting"
    finally:
        server.stop()


def test_one_studio_command_reuses_room_checks_candidate_and_hands_same_tab_to_product(tmp_path) -> None:
    port = _available_port()
    state_root = tmp_path / "studio-state"
    brief = "Build a calm field dispatch dashboard for overdue jobs and crew availability."
    first = open_studio(
        brief=brief,
        kind="view",
        cwd=tmp_path,
        port=port,
        state_root=state_root,
        no_open=True,
    )
    repeated = open_studio(
        brief=brief,
        kind="view",
        cwd=tmp_path,
        port=port,
        state_root=state_root,
        no_open=True,
    )
    assert first["studio"]["status"] == "creating"
    assert repeated["studio"]["status"] == "creating"
    assert repeated["creation"]["port"] == first["creation"]["port"] == port
    assert repeated["creation"]["url"] != first["creation"]["url"]

    with pytest.raises(ReviewContractError) as conflict:
        open_studio(
            brief=brief,
            kind="view",
            cwd=tmp_path,
            port=port,
            state_root=state_root,
            verify=True,
            no_open=True,
        )
    assert conflict.value.code == "REVIEW_SESSION_CONFIGURATION_CONFLICT"

    room = urlsplit(str(repeated["creation"]["url"]))
    status, headers, _ = _request(port, "GET", room.path)
    assert status == 303
    room_cookie = headers["set-cookie"].split(";", 1)[0]
    status, _, body = _request(port, "GET", "/", headers={"Cookie": room_cookie})
    assert status == 200
    assert "ViewSpec Studio · First creation" in body.decode("utf-8")

    candidate_path = Path(repeated["paths"]["candidate"])
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text("{}\n", encoding="utf-8")

    deadline = time.monotonic() + 30
    failed_projection: dict[str, object] = {}
    while time.monotonic() < deadline:
        status, _, body = _request(port, "GET", "/v1/status", headers={"Cookie": room_cookie})
        assert status == 200
        failed_projection = json.loads(body)
        if failed_projection["stage"] == "needs_fix":
            break
        time.sleep(0.1)
    assert failed_projection["headline"] == "Candidate needs one fix"
    assert failed_projection["error"]["code"] == "STUDIO_CREATION_CANDIDATE_INVALID"
    assert str(tmp_path) not in json.dumps(failed_projection)
    assert not (tmp_path / "viewspec.intent.json").exists()

    candidate = starter_intent_payload("dashboard")
    candidate["substrate"]["nodes"]["starter_dashboard"]["attrs"]["title"] = "Field Dispatch"
    candidate["substrate"]["nodes"]["revenue"]["attrs"] = {"label": "Open jobs", "value": "18"}
    candidate_path.write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    headlines: list[str] = []
    deadline = time.monotonic() + 30
    projection: dict[str, object] = {}
    while time.monotonic() < deadline:
        status, _, body = _request(port, "GET", "/v1/status", headers={"Cookie": room_cookie})
        assert status == 200
        projection = json.loads(body)
        headline = str(projection["headline"])
        if not headlines or headlines[-1] != headline:
            headlines.append(headline)
        if projection["handoff_ready"] is True:
            break
        time.sleep(0.1)
    assert projection["handoff_ready"] is True
    assert "Checking candidate" in headlines
    assert headlines[-1] == "Checked product ready"
    assert projection["checks"] == {"candidate_validation": "passed", "artifact_check": "passed"}

    status, headers, _ = _request(port, "GET", "/continue", headers={"Cookie": room_cookie})
    assert status == 303
    review_url = urlsplit(headers["location"])
    status, review_headers, _ = _request(int(review_url.port), "GET", review_url.path)
    assert status == 303
    review_cookie = review_headers["set-cookie"].split(";", 1)[0]
    review_root = review_headers["location"]
    status, _, body = _request(int(review_url.port), "GET", review_root, headers={"Cookie": review_cookie})
    assert status == 200
    assert "<title>ViewSpec Studio</title>" in body.decode("utf-8")

    source = tmp_path / "viewspec.intent.json"
    receipt = json.loads(
        next((tmp_path / ".viewspec/studio-creation").glob("*/room-transition.json")).read_text(encoding="utf-8")
    )
    assert receipt["status"] == "checked"
    assert receipt["candidate_to_checked_ms"] < 60_000
    assert receipt["review_port"] == review_url.port
    assert source.is_file()

    resumed = open_studio(
        brief=brief,
        kind="view",
        cwd=tmp_path,
        port=port,
        state_root=state_root,
        no_open=True,
    )
    assert resumed["studio"]["status"] == "ready"
    assert resumed["review"]["port"] == review_url.port
    assert resumed["prepared_creation"]["task_action"] == "accepted"
    end_review(source, state_root=state_root)
