from __future__ import annotations

import hashlib
import http.client
import json
import re
import shutil
import socket
import subprocess

import pytest

from viewspec.converge_sessions import start_convergence_session, submit_convergence_patch
from viewspec.intent_patch import IntentPatchContext, source_sha256
from viewspec.intent_tools import starter_intent_payload
from viewspec.review_contract import ReviewContractError
from viewspec.review_runtime import ReviewRuntime
from viewspec.review_server import ReviewServer, _json_object


def _runtime(tmp_path, *, convergence_state_root=None) -> ReviewRuntime:
    source = tmp_path / "viewspec.intent.json"
    source.write_text(json.dumps(starter_intent_payload(), sort_keys=True), encoding="utf-8")
    return ReviewRuntime.open(
        source,
        state_root=tmp_path / "state",
        convergence_state_root=convergence_state_root,
    )


def _server(runtime: ReviewRuntime, **kwargs) -> ReviewServer:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return ReviewServer(runtime, port=port, **kwargs)


def _request(port: int, method: str, path: str, *, headers=None, body: bytes | None = None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    content = response.read()
    result = response.status, dict(response.getheaders()), content
    connection.close()
    return result


def _bootstrap(server: ReviewServer) -> tuple[str, str]:
    status, headers, _ = _request(server.port, "GET", server.bootstrap_path)
    assert status == 303
    return headers["Set-Cookie"].split(";", 1)[0], headers["Location"]


def _browser_headers(server: ReviewServer, cookie: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Cookie": cookie,
        "Origin": server.origin,
        "Sec-Fetch-Site": "same-origin",
        "X-ViewSpec-Frame-Nonce": server.frame_nonce,
    }


def _handshake(server: ReviewServer, cookie: str) -> None:
    frame_status, _, _ = _request(server.port, "GET", server.frame_path("index.html"))
    assert frame_status == 200
    endpoint = f"/r/{server.runtime.session.review_id}/api/v1/handshake"
    status, _, payload = _request(
        server.port,
        "POST",
        endpoint,
        headers=_browser_headers(server, cookie),
        body=b"{}",
    )
    assert status == 200, payload


def _event_payload(runtime: ReviewRuntime) -> bytes:
    manifest = json.loads(runtime.built.artifact_dir.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))
    dom_id = next(iter(manifest["nodes"]))
    return json.dumps(
        {
            "kind": "note",
            "body": "Tighten this.",
            "screen_id": None,
            "dom_ancestors": [dom_id],
            "page_level": False,
            "client_provenance": {"ir_id": "forged"},
            "context": {
                "route": None,
                "screen_id": None,
                "viewport": {"name": "desktop", "width": 1440, "height": 1000},
                "selected_text": None,
                "control_values": {},
                "visibility": "visible",
                "evidence_refs": [],
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _pending_convergence(runtime: ReviewRuntime, *, state_root=None) -> tuple[str, str]:
    source = runtime.configuration.source_path
    text = open(source, encoding="utf-8").read()
    evidence_refs = ("review:vrw_server:batch_server", "review_event:event_server")
    context = IntentPatchContext(
        origin="review_batch",
        source_kind="intent_bundle",
        base_source_sha256=source_sha256(text),
        contract_profile="local_v1",
        evidence_refs=evidence_refs,
        requests=(
            {
                "request_id": "event_server",
                "kind": "change_request",
                "instruction": "Show revenue as a badge.",
                "screen_id": None,
                "source_ref": "ir:binding_revenue_value",
                "binding_id": "revenue_value",
                "action_id": None,
                "intent_refs": ["viewspec:binding:revenue_value"],
                "content_refs": ["node:revenue#attr:value"],
            },
        ),
    )
    start_convergence_session(source, context, state_root=state_root)
    session = submit_convergence_patch(
        source,
        {
            "schema_version": 1,
            "contract_profile": "local_v1",
            "source_kind": "intent_bundle",
            "base_source_sha256": source_sha256(text),
            "operations": [
                {
                    "op": "set_binding_presentation",
                    "binding_id": "revenue_value",
                    "old_value": "value",
                    "value": "badge",
                }
            ],
            "evidence_refs": list(evidence_refs),
        },
        state_root=state_root,
    )
    assert session.pending_preview is not None
    return session.pending_preview.preview_id, text


def test_server_refuses_every_nonliteral_loopback_bind(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    for host in ("localhost", "0.0.0.0", "::1"):
        with pytest.raises(ReviewContractError) as raised:
            ReviewServer(runtime, host=host)
        assert raised.value.code == "REVIEW_NON_LOOPBACK_FORBIDDEN"


def test_bootstrap_is_single_use_and_sets_a_scoped_strict_cookie(tmp_path) -> None:
    server = _server(_runtime(tmp_path))
    server.start()
    try:
        status, headers, _ = _request(server.port, "GET", server.bootstrap_path)
        assert status == 303
        cookie = headers["Set-Cookie"]
        assert "HttpOnly" in cookie
        assert "SameSite=Strict" in cookie
        assert f"Path=/r/{server.runtime.session.review_id}/" in cookie
        assert "Domain=" not in cookie
        assert headers["Cache-Control"] == "no-store"
        assert headers["Referrer-Policy"] == "no-referrer"

        repeated, _, payload = _request(server.port, "GET", server.bootstrap_path)
        assert repeated == 403
        assert json.loads(payload)["error"]["code"] == "REVIEW_CAPABILITY_INVALID"
    finally:
        server.stop()


def test_third_simultaneous_browser_request_fails_fast_without_queueing(tmp_path) -> None:
    server = _server(_runtime(tmp_path))
    server.start()
    server._browser_connection_slots.acquire()  # noqa: SLF001 - deterministic capacity probe
    server._browser_connection_slots.acquire()  # noqa: SLF001 - deterministic capacity probe
    try:
        status, headers, payload = _request(server.port, "GET", server.bootstrap_path)
        assert status == 503
        assert headers["Retry-After"] == "1"
        assert json.loads(payload)["error"]["code"] == "REVIEW_SERVER_BUSY"
    finally:
        server._browser_connection_slots.release()  # noqa: SLF001
        server._browser_connection_slots.release()  # noqa: SLF001
        server.stop()


def test_browser_mutation_requires_exact_origin_cookie_nonce_and_rebuilds_target(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    server = _server(runtime)
    server.start()
    try:
        cookie, _ = _bootstrap(server)
        _handshake(server, cookie)
        body = _event_payload(runtime)
        endpoint = f"/r/{runtime.session.review_id}/api/v1/events"
        base_headers = {
            **_browser_headers(server, cookie),
            "Idempotency-Key": "0" * 32,
        }

        forbidden, _, payload = _request(
            server.port,
            "POST",
            endpoint,
            headers={**base_headers, "Origin": "http://localhost"},
            body=body,
        )
        assert forbidden == 403
        assert json.loads(payload)["error"]["code"] == "REVIEW_REQUEST_FORBIDDEN"
        assert runtime.session.events == ()

        stale, _, payload = _request(
            server.port,
            "POST",
            endpoint,
            headers={**base_headers, "X-ViewSpec-Frame-Nonce": "f" * 32},
            body=body,
        )
        assert stale == 409
        assert json.loads(payload)["error"]["code"] == "REVIEW_REVISION_MISMATCH"
        assert runtime.session.events == ()

        accepted, _, payload = _request(server.port, "POST", endpoint, headers=base_headers, body=body)
        assert accepted == 201
        event = json.loads(payload)["event"]
        assert event["target"]["ir_id"] != "forged"
        assert len(runtime.session.events) == 1
    finally:
        server.stop()


def test_oversized_mutation_is_rejected_before_event_parsing(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    server = _server(runtime)
    server.start()
    try:
        cookie, _ = _bootstrap(server)
        endpoint = f"/r/{runtime.session.review_id}/api/v1/events"
        body = b"{" + b"x" * (256 * 1024)
        status, _, payload = _request(
            server.port,
            "POST",
            endpoint,
            headers={
                **_browser_headers(server, cookie),
                "Idempotency-Key": "1" * 32,
            },
            body=body,
        )
        assert status == 413
        assert json.loads(payload)["error"]["code"] == "REVIEW_REQUEST_TOO_LARGE"
        assert runtime.session.events == ()
    finally:
        server.stop()


def test_frame_serving_checks_allowlist_hash_and_never_changes_stored_html(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    stored = runtime.built.artifact_dir.joinpath("index.html")
    before = hashlib.sha256(stored.read_bytes()).hexdigest()
    server = _server(runtime)
    server.start()
    try:
        status, headers, payload = _request(server.port, "GET", server.frame_path("index.html"))
        assert status == 200
        assert b"viewspec-review-sdk" in payload
        assert "sandbox" not in headers.get("Content-Security-Policy", "")
        assert hashlib.sha256(stored.read_bytes()).hexdigest() == before

        traversal, _, error = _request(server.port, "GET", server.frame_path("%2e%2e/session.json"))
        assert traversal == 404
        assert json.loads(error)["error"]["code"] == "REVIEW_ARTIFACT_NOT_FOUND"

        stored.write_bytes(stored.read_bytes() + b"tamper")
        changed, _, error = _request(server.port, "GET", server.frame_path("index.html"))
        assert changed == 404
        assert json.loads(error)["error"]["code"] == "REVIEW_ARTIFACT_NOT_FOUND"
    finally:
        server.stop()


def test_generated_review_chrome_and_frame_sdk_are_valid_javascript(tmp_path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable for generated browser-script syntax checking")
    server = _server(_runtime(tmp_path))
    chrome = server._chrome_response().body.decode("utf-8")  # noqa: SLF001
    frame_status = server._serve_frame(server.frame_path("index.html"))  # noqa: SLF001
    scripts = re.findall(r"<script[^>]*>([\s\S]*?)</script>", chrome + frame_status.body.decode("utf-8"))

    assert len(scripts) >= 2
    for script in scripts:
        result = subprocess.run((node, "--check", "-"), input=script, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
    server.stop()


def test_review_chrome_exposes_proof_not_authority_and_approves_exact_preview(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    preview_id, original = _pending_convergence(runtime)
    server = _server(runtime)
    chrome = server._chrome_response().body.decode("utf-8")  # noqa: SLF001
    assert "Convergence proposal" in chrome
    assert "Approve proposal" in chrome
    assert "Reject proposal" in chrome
    server.start()
    try:
        cookie, _ = _bootstrap(server)
        _handshake(server, cookie)
        root = f"/r/{runtime.session.review_id}/api/v1"
        status, _, content = _request(
            server.port,
            "GET",
            f"{root}/session",
            headers={"Cookie": cookie},
        )
        assert status == 200
        browser = json.loads(content)
        pending = browser["review"]["convergence"]["pending_preview"]
        assert pending["preview_id"] == preview_id
        assert pending["progress_certificate"]["mode"] == "human_review"
        assert "approval_token" not in content.decode("utf-8")
        assert "intent_approval_token" not in content.decode("utf-8")

        forbidden, _, content = _request(
            server.port,
            "POST",
            f"{root}/convergence/approve",
            headers={**_browser_headers(server, cookie), "Origin": "http://localhost"},
            body=json.dumps({"preview_id": preview_id}).encode(),
        )
        assert forbidden == 403
        assert json.loads(content)["error"]["code"] == "REVIEW_REQUEST_FORBIDDEN"
        assert open(runtime.configuration.source_path, encoding="utf-8").read() == original

        accepted, _, content = _request(
            server.port,
            "POST",
            f"{root}/convergence/approve",
            headers=_browser_headers(server, cookie),
            body=json.dumps({"preview_id": preview_id}).encode(),
        )
        assert accepted == 200, content
        assert json.loads(content)["convergence"]["status"] == "applied"
        source_payload = json.loads(open(runtime.configuration.source_path, encoding="utf-8").read())
        assert source_payload["view_spec"]["bindings"][1]["present_as"] == "badge"
    finally:
        server.stop()


def test_review_uses_configured_custom_convergence_state_root(tmp_path) -> None:
    convergence_state = tmp_path / "custom-convergence-state"
    runtime = _runtime(tmp_path, convergence_state_root=convergence_state)
    preview_id, _ = _pending_convergence(runtime, state_root=convergence_state)
    server = _server(runtime)
    server.start()
    try:
        cookie, _ = _bootstrap(server)
        _handshake(server, cookie)
        root = f"/r/{runtime.session.review_id}/api/v1"
        status, _, content = _request(
            server.port,
            "GET",
            f"{root}/session",
            headers={"Cookie": cookie},
        )
        assert status == 200
        assert json.loads(content)["review"]["convergence"]["pending_preview"]["preview_id"] == preview_id

        accepted, _, content = _request(
            server.port,
            "POST",
            f"{root}/convergence/approve",
            headers=_browser_headers(server, cookie),
            body=json.dumps({"preview_id": preview_id}).encode(),
        )
        assert accepted == 200, content
        assert json.loads(content)["convergence"]["status"] == "applied"
    finally:
        server.stop()


def test_review_chrome_rejects_exact_preview_without_mutating_source(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    preview_id, original = _pending_convergence(runtime)
    server = _server(runtime)
    server.start()
    try:
        cookie, _ = _bootstrap(server)
        _handshake(server, cookie)
        endpoint = f"/r/{runtime.session.review_id}/api/v1/convergence/reject"
        status, _, content = _request(
            server.port,
            "POST",
            endpoint,
            headers=_browser_headers(server, cookie),
            body=json.dumps({"preview_id": preview_id}).encode(),
        )
        assert status == 200, content
        assert json.loads(content)["convergence"]["status"] == "rejected"
        assert open(runtime.configuration.source_path, encoding="utf-8").read() == original
    finally:
        server.stop()


def test_agent_poll_capability_preserves_at_least_once_ack_semantics(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    server = _server(runtime)
    server.start()
    try:
        cookie, _ = _bootstrap(server)
        _handshake(server, cookie)
        event_body = _event_payload(runtime)
        endpoint = f"/r/{runtime.session.review_id}/api/v1/events"
        accepted, _, _ = _request(
            server.port,
            "POST",
            endpoint,
            headers={
                **_browser_headers(server, cookie),
                "Idempotency-Key": "2" * 32,
            },
            body=event_body,
        )
        assert accepted == 201
        runtime.semantic_diff = {"status": "available", "entries": ["x" * (256 * 1024)]}
        poll_body = b'{"ack_batch_id":null,"agent_reply":null,"timeout_ms":1}'
        forbidden, _, _ = _request(
            server.port,
            "POST",
            "/internal/v1/poll",
            headers={"Content-Type": "application/json"},
            body=poll_body,
        )
        assert forbidden == 403

        headers = {"Content-Type": "application/json", "X-ViewSpec-Agent-Capability": server.agent_token}
        first_status, _, first_bytes = _request(
            server.port,
            "POST",
            "/internal/v1/poll",
            headers=headers,
            body=poll_body,
        )
        repeated_status, _, repeated_bytes = _request(
            server.port,
            "POST",
            "/internal/v1/poll",
            headers=headers,
            body=poll_body,
        )
        assert first_status == repeated_status == 200
        first = json.loads(first_bytes)
        repeated = json.loads(repeated_bytes)
        assert first["batch"] == repeated["batch"]
        assert first["semantic_diff"] == {"status": "deferred"}

        ack = json.dumps(
            {"ack_batch_id": first["batch"]["batch_id"], "agent_reply": "Captured.", "timeout_ms": 1},
            separators=(",", ":"),
        ).encode()
        ack_status, _, ack_bytes = _request(
            server.port,
            "POST",
            "/internal/v1/poll",
            headers=headers,
            body=ack,
        )
        assert ack_status == 200
        assert json.loads(ack_bytes)["status"] == "timeout"
        assert runtime.session.agent_replies == ("Captured.",)
    finally:
        server.stop()


def test_browser_events_require_a_current_revision_handshake(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    server = _server(runtime)
    server.start()
    try:
        cookie, _ = _bootstrap(server)
        endpoint = f"/r/{runtime.session.review_id}/api/v1/events"
        headers = {**_browser_headers(server, cookie), "Idempotency-Key": "3" * 32}

        rejected, _, payload = _request(server.port, "POST", endpoint, headers=headers, body=_event_payload(runtime))
        assert rejected == 409
        assert json.loads(payload)["error"]["code"] == "REVIEW_BROWSER_HANDSHAKE_TIMEOUT"
        assert runtime.session.events == ()

        _handshake(server, cookie)
        accepted, _, _ = _request(server.port, "POST", endpoint, headers=headers, body=_event_payload(runtime))
        assert accepted == 201
    finally:
        server.stop()


def test_ended_server_becomes_auto_exitable_after_five_idle_seconds(tmp_path) -> None:
    now = [100.0]
    runtime = _runtime(tmp_path)
    server = _server(runtime, clock=lambda: now[0])
    runtime.session.end(actor="agent")

    assert server.should_auto_exit is False
    now[0] += 5.0
    assert server.should_auto_exit is True


def test_active_server_becomes_suspendable_after_thirty_idle_minutes(tmp_path) -> None:
    now = [100.0]
    server = _server(_runtime(tmp_path), clock=lambda: now[0])

    assert server.should_suspend is False
    now[0] += 30 * 60
    assert server.should_suspend is True


def test_browser_send_and_end_is_atomic_retryable_and_invalidates_normal_cookie_use(tmp_path) -> None:
    runtime = _runtime(tmp_path)
    server = _server(runtime)
    server.start()
    try:
        cookie, review_path = _bootstrap(server)
        _handshake(server, cookie)
        payload = json.loads(_event_payload(runtime))
        payload["actor"] = "human"
        body = json.dumps(payload, separators=(",", ":")).encode()
        endpoint = f"/r/{runtime.session.review_id}/api/v1/end"
        headers = {**_browser_headers(server, cookie), "Idempotency-Key": "4" * 32}

        first, _, first_body = _request(server.port, "POST", endpoint, headers=headers, body=body)
        repeated, _, repeated_body = _request(server.port, "POST", endpoint, headers=headers, body=body)

        assert first == repeated == 200
        assert json.loads(first_body)["event"] == json.loads(repeated_body)["event"]
        assert [record["type"] for record in runtime.session.journal.records()].count("event_and_end") == 1
        assert runtime.session.ended_by == "human"

        denied, _, _ = _request(server.port, "GET", review_path, headers={"Cookie": cookie})
        assert denied == 403
    finally:
        server.stop()


@pytest.mark.parametrize(
    "body",
    (
        b'{"value":1.5}',
        b'{"value":9223372036854775808}',
        json.dumps({"value": list(range(257))}).encode(),
        (b'{"value":' + b'[' * 17 + b'0' + b']' * 17 + b'}'),
    ),
)
def test_json_parser_rejects_values_outside_physical_shape_bounds(body) -> None:
    with pytest.raises(ReviewContractError) as raised:
        _json_object(body)
    assert raised.value.code == "REVIEW_REQUEST_INVALID"
    assert raised.value.http_status == 400
