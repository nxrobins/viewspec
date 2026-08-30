from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path
import zipfile

import pytest

from viewspec.app_starters import starter_app_bundle
import viewspec.review_compile as review_compile
from viewspec.review_compile import STUDIO_COMPARE_TARGET
from viewspec.review_contract import ReviewContractError, canonical_json_bytes
from viewspec.review_runtime import ReviewRuntime
from viewspec.studio_share_publish import (
    STUDIO_SHARE_RELEASE_CHECKS,
    StudioSharePublisher,
    StudioShareRelease,
    fetch_studio_share_release,
)


_REAL_SUBPROCESS_RUN = review_compile.subprocess.run
_API_ORIGIN = "https://api.viewspec.test"
_REVIEW_ORIGIN = "https://review.viewspec.test"
_NOW = 2_000_000_000


def _fake_react_npm(command, *, cwd, **kwargs):
    if tuple(command[:3]) == ("npm", "run", "build"):
        runtime = Path(cwd) / "runtime-dist"
        assets = runtime / "assets"
        assets.mkdir(parents=True)
        assets.joinpath("main.js").write_text("document.getElementById('root').textContent='ready';", encoding="utf-8")
        assets.joinpath("main.css").write_text("body{margin:0}", encoding="utf-8")
        runtime.joinpath("index.html").write_text(
            '<!doctype html><html><head><link rel="stylesheet" crossorigin href="./assets/main.css"></head>'
            '<body><div id="root"></div><script type="module" crossorigin src="./assets/main.js"></script></body></html>',
            encoding="utf-8",
        )
        return object()
    if tuple(command[:2]) == ("npm", "ci"):
        return object()
    return _REAL_SUBPROCESS_RUN(command, cwd=cwd, **kwargs)


def _signing_material():
    cryptography = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    private = cryptography.Ed25519PrivateKey.generate()
    public_bytes = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = "vsk_" + __import__("hashlib").sha256(public_bytes).hexdigest()[:16]
    public_key = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "public_key": base64.urlsafe_b64encode(public_bytes).decode().rstrip("="),
    }
    return private, public_key


def _release_payload(**overrides) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "kind": "studio_share_release",
        "status": "active",
        "api_origin": _API_ORIGIN,
        "review_origin": _REVIEW_ORIGIN,
        "deployment_sha256": "a" * 64,
        "verifier_id": "viewspec-studio-production-canary-v1",
        "run_id": "vsrcan_" + ("b" * 32),
        "report_sha256": "c" * 64,
        "checks": {name: True for name in sorted(STUDIO_SHARE_RELEASE_CHECKS)},
        "issued_at_epoch_s": _NOW - 10,
        "expires_at_epoch_s": _NOW + 600,
    }
    payload.update(overrides)
    return payload


def _signed_release(payload: dict[str, object] | None = None):
    private, public_key = _signing_material()
    value = payload or _release_payload()
    signature = private.sign(canonical_json_bytes(value))
    return {
        "algorithm": "ed25519",
        "key_id": public_key["key_id"],
        "payload": value,
        "signature": base64.urlsafe_b64encode(signature).decode().rstrip("="),
    }, public_key


def _verified_release() -> StudioShareRelease:
    receipt, public_key = _signed_release()
    return StudioShareRelease.from_signed_receipt(
        receipt,
        public_key,
        expected_api_origin=_API_ORIGIN,
        expected_review_origin=_REVIEW_ORIGIN,
        now_epoch_s=_NOW,
    )


def _checked_comparison(tmp_path: Path, monkeypatch) -> tuple[Path, Path, ReviewRuntime]:
    source = tmp_path / "viewspec.app.json"
    source.write_text(json.dumps(starter_app_bundle("internal_tool"), sort_keys=True), encoding="utf-8")
    state = tmp_path / "review-state"
    monkeypatch.setattr("viewspec.review_compile.subprocess.run", _fake_react_npm)
    runtime = ReviewRuntime.open(source, state_root=state, target=STUDIO_COMPARE_TARGET, allow_install=True)
    return source, state, runtime


def _creation_response(body: bytes) -> bytes:
    with zipfile.ZipFile(BytesIO(body)) as archive:
        envelope = json.loads(archive.read("envelope.json"))
    revision = envelope["revision"]
    session_id = "vsr_" + ("A" * 24)
    archive_sha256 = __import__("hashlib").sha256(body).hexdigest()
    payload = {
        "schema_version": 1,
        "status": "active",
        "session": {
            "id": session_id,
            "package_id": envelope["package_id"],
            "revision_identity_sha256": "d" * 64,
            "created_at": _NOW,
            "expires_at": _NOW + 3600,
            "private": True,
        },
        "verification": {
            "schema_version": 1,
            "status": "passed",
            "verifier_id": "production-sandbox-v1",
            "package_id": envelope["package_id"],
            "source_sha256": revision["source_sha256"],
            "artifact_set_sha256": revision["artifact_set_sha256"],
            "root_manifest_sha256": revision["root_manifest_sha256"],
            "inspection_sha256": revision["inspection_sha256"],
            "target_artifact_sets": revision["target_artifact_sets"],
        },
        "ingress": {"archive_sha256": archive_sha256, "archive_bytes": len(body)},
        "links": {
            "owner": f"{_REVIEW_ORIGIN}/review/{session_id}/#cap=vsc_{'o' * 24}",
            "reviewer": f"{_REVIEW_ORIGIN}/review/{session_id}/#cap=vsc_{'r' * 24}",
            "transport": "url_fragment_one_time_exchange",
        },
    }
    return canonical_json_bytes(payload)


def test_share_release_requires_current_signed_complete_production_canary() -> None:
    receipt, public_key = _signed_release()
    release = StudioShareRelease.from_signed_receipt(
        receipt,
        public_key,
        expected_api_origin=_API_ORIGIN,
        expected_review_origin=_REVIEW_ORIGIN,
        now_epoch_s=_NOW,
    )
    assert release.browser_projection() == {
        "status": "available",
        "review_origin": _REVIEW_ORIGIN,
        "deployment_sha256": "a" * 64,
        "run_id": "vsrcan_" + ("b" * 32),
        "report_sha256": "c" * 64,
        "expires_at_epoch_s": _NOW + 600,
    }
    assert "signature" not in release.browser_projection()

    tampered = json.loads(json.dumps(receipt))
    tampered["payload"]["deployment_sha256"] = "e" * 64
    with pytest.raises(ReviewContractError) as invalid:
        StudioShareRelease.from_signed_receipt(
            tampered,
            public_key,
            expected_api_origin=_API_ORIGIN,
            expected_review_origin=_REVIEW_ORIGIN,
            now_epoch_s=_NOW,
        )
    assert invalid.value.code == "STUDIO_SHARE_RELEASE_INVALID"

    expired_receipt, expired_key = _signed_release(_release_payload(expires_at_epoch_s=_NOW))
    with pytest.raises(ReviewContractError) as expired:
        StudioShareRelease.from_signed_receipt(
            expired_receipt,
            expired_key,
            expected_api_origin=_API_ORIGIN,
            expected_review_origin=_REVIEW_ORIGIN,
            now_epoch_s=_NOW,
        )
    assert expired.value.code == "STUDIO_SHARE_RELEASE_EXPIRED"

    checks = {name: True for name in STUDIO_SHARE_RELEASE_CHECKS}
    checks["zero_sensitive_leaks"] = False
    incomplete_receipt, incomplete_key = _signed_release(_release_payload(checks=checks))
    with pytest.raises(ReviewContractError) as incomplete:
        StudioShareRelease.from_signed_receipt(
            incomplete_receipt,
            incomplete_key,
            expected_api_origin=_API_ORIGIN,
            expected_review_origin=_REVIEW_ORIGIN,
            now_epoch_s=_NOW,
        )
    assert incomplete.value.code == "STUDIO_SHARE_RELEASE_INVALID"


def test_fetch_release_verifies_key_and_readiness_contract() -> None:
    receipt, public_key = _signed_release()
    calls: list[tuple[str, str]] = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url))
        assert body is None and timeout == 15.0
        if url.endswith("/v1/receipt-key"):
            assert headers == {}
            return 200, {}, canonical_json_bytes(public_key)
        assert headers == {"Authorization": "Bearer readiness-private-key"}
        return 200, {}, canonical_json_bytes({"schema_version": 1, "release": receipt})

    release = fetch_studio_share_release(
        api_key="readiness-private-key",
        api_origin=_API_ORIGIN,
        review_origin=_REVIEW_ORIGIN,
        now_epoch_s=_NOW,
        transport=transport,
    )
    assert release.run_id == "vsrcan_" + ("b" * 32)
    assert calls == [
        ("GET", f"{_API_ORIGIN}/v1/receipt-key"),
        ("GET", f"{_API_ORIGIN}/v1/studio-share-readiness"),
    ]
    assert "readiness-private-key" not in repr(release)
    assert "readiness-private-key" not in json.dumps(release.browser_projection())


@pytest.mark.parametrize("api_key", ["", None, 123, "a" * 513, "has space", "line\nbreak", "control\x00", "unicode-\u200b"])
def test_readiness_rejects_invalid_credential_before_any_network(api_key):
    calls = []
    with pytest.raises(ReviewContractError) as failure:
        fetch_studio_share_release(api_key=api_key, transport=lambda *args: calls.append(args))
    assert failure.value.code == "STUDIO_SHARE_AUTH_REQUIRED"
    assert calls == []


@pytest.mark.parametrize("status,code", [(401, "STUDIO_SHARE_AUTH_REQUIRED"), (403, "STUDIO_SHARE_NOT_ELIGIBLE")])
def test_readiness_denial_does_not_expose_credential_or_remote_details(status, code):
    _, public_key = _signed_release()
    def transport(*args):
        return status, {}, canonical_json_bytes({"error": {"code": "readiness-private-key", "message": "untrusted private detail"}})
    with pytest.raises(ReviewContractError) as failure:
        fetch_studio_share_release(api_key="readiness-private-key", public_key=public_key, transport=transport)
    assert failure.value.code == code
    assert "readiness-private-key" not in str(failure.value)
    assert "untrusted private detail" not in str(failure.value)
    assert "continue locally" in failure.value.fix


@pytest.mark.parametrize("mode", ["success", "redirect", "encoded", "oversize"])
def test_share_transport_refuses_proxy_redirect_and_unbounded_response(monkeypatch, mode):
    import httpx
    import viewspec.studio_share_publish as module
    calls = []
    real_client = httpx.Client
    closed = []
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"{}" if mode != "oversize" else b"x" * (module.STUDIO_SHARE_HTTP_MAX_RESPONSE_BYTES + 1)
        def close(self):
            closed.append(True)
    def handler(request):
        calls.append(str(request.url))
        assert request.headers["authorization"] == "Bearer private-transport-key"
        assert request.headers["accept-encoding"] == "identity"
        headers = {"location": "https://untrusted.invalid"} if mode == "redirect" else {}
        if mode == "encoded":
            headers["content-encoding"] = "gzip"
        return httpx.Response(302 if mode == "redirect" else 200, headers=headers, stream=Stream())
    def client(**kwargs):
        assert kwargs == {"trust_env": False, "follow_redirects": False}
        return real_client(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, "Client", client)
    args = ("GET", "https://api.viewspec.dev/v1/studio-share-readiness", {"Authorization": "Bearer private-transport-key"}, None, 15.0)
    if mode in {"encoded", "oversize"}:
        with pytest.raises(ReviewContractError) as failure:
            module._http_transport(*args)
        assert failure.value.code == "STUDIO_SHARE_REMOTE_INVALID"
    else:
        result = module._http_transport(*args)
        assert result[0] == (302 if mode == "redirect" else 200)
    assert calls == [args[1]] and closed


@pytest.mark.parametrize("eligible", [True, False])
def test_daemon_checks_eligibility_with_private_key_before_exposing_share(tmp_path, monkeypatch, capsys, eligible):
    import viewspec.review_daemon as daemon
    import viewspec.studio_share_publish as module
    source, state, runtime = _checked_comparison(tmp_path, monkeypatch)
    monkeypatch.setenv("VIEWSPEC_STUDIO_API_KEY", "daemon-private-key")
    monkeypatch.setattr(daemon.ReviewRuntime, "open", lambda *args, **kwargs: runtime)
    calls = []
    def fetch(**kwargs):
        calls.append(kwargs)
        if not eligible:
            raise ReviewContractError("STUDIO_SHARE_NOT_ELIGIBLE", "Private sharing is not available for this account.",
                                      "Continue locally without --share.", http_status=403, cli_exit=2)
        import time
        now = int(time.time())
        receipt, key = _signed_release(_release_payload(issued_at_epoch_s=now, expires_at_epoch_s=now + 900))
        return StudioShareRelease.from_signed_receipt(receipt, key, expected_api_origin=_API_ORIGIN,
                                                      expected_review_origin=_REVIEW_ORIGIN)
    monkeypatch.setattr(module, "fetch_studio_share_release", fetch)
    monkeypatch.setattr(daemon, "_install_signal_handlers", lambda stop: None)
    inspected = []
    def watch(runtime, server, stop, **kwargs):
        inspected.append(True)
        assert "daemon-private-key" not in json.dumps(server.status())
        for name in ("server.json", "agent-capability.json"):
            assert "daemon-private-key" not in (runtime.session_dir / name).read_text()
    monkeypatch.setattr(daemon, "_watch_sources", watch)
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    result = daemon.main(["--source", str(source), "--state-root", str(state), "--port", str(port), "--studio-share", "--install"])
    captured = capsys.readouterr().out
    assert "daemon-private-key" not in captured
    assert calls == [{"api_key": "daemon-private-key"}]
    assert result == (0 if eligible else 2), captured
    assert bool(inspected) is eligible
    assert json.loads(captured)["ok"] is eligible


def test_publisher_prepares_without_upload_then_creates_one_exact_private_link(tmp_path, monkeypatch) -> None:
    source, state, _runtime = _checked_comparison(tmp_path, monkeypatch)
    calls: list[dict[str, object]] = []

    def transport(method, url, headers, body, timeout):
        calls.append({"method": method, "url": url, "headers": dict(headers), "body": body, "timeout": timeout})
        assert isinstance(body, bytes)
        return 201, {}, _creation_response(body)

    publisher = StudioSharePublisher(
        release=_verified_release(),
        api_key="test-production-key",
        source=source,
        state_root=state,
        cwd=tmp_path,
        transport=transport,
        now_epoch_s=lambda: _NOW,
    )
    prepared = publisher.prepare()
    assert prepared["status"] == "awaiting_confirmation"
    assert prepared["upload_performed"] is False
    assert calls == []
    serialized = json.dumps(prepared, sort_keys=True)
    assert "test-production-key" not in serialized
    assert str(tmp_path) not in serialized

    with pytest.raises(ReviewContractError) as unconfirmed:
        publisher.publish(
            package_id=str(prepared["package_id"]),
            disclosure_accepted=False,
            expires_in_seconds=3600,
        )
    assert unconfirmed.value.code == "STUDIO_SHARE_DISCLOSURE_REQUIRED"
    assert calls == []

    created = publisher.publish(
        package_id=str(prepared["package_id"]),
        disclosure_accepted=True,
        expires_in_seconds=3600,
    )
    assert created["status"] == "active"
    assert created["package_id"] == prepared["package_id"]
    assert created["reviewer_url"].startswith(f"{_REVIEW_ORIGIN}/review/vsr_")
    assert created["owner_url"].startswith(f"{_REVIEW_ORIGIN}/review/vsr_")
    assert created["upload_performed"] is True
    assert "test-production-key" not in json.dumps(created, sort_keys=True)
    assert len(calls) == 1
    request = calls[0]
    assert request["method"] == "POST"
    assert request["url"] == f"{_API_ORIGIN}/v1/reviews"
    assert request["headers"]["Authorization"] == "Bearer test-production-key"
    assert request["headers"]["X-ViewSpec-Disclosure-Accepted"] == "true"
    assert request["headers"]["Idempotency-Key"].startswith("vss_")


def test_publisher_rejects_stale_confirmation_and_mismatched_remote_proof(tmp_path, monkeypatch) -> None:
    source, state, _runtime = _checked_comparison(tmp_path, monkeypatch)
    response_mutator = {"enabled": False}

    def transport(method, url, headers, body, timeout):
        assert isinstance(body, bytes)
        response = json.loads(_creation_response(body))
        if response_mutator["enabled"]:
            response["verification"]["source_sha256"] = "0" * 64
        return 201, {}, canonical_json_bytes(response)

    publisher = StudioSharePublisher(
        release=_verified_release(),
        api_key="test-production-key",
        source=source,
        state_root=state,
        cwd=tmp_path,
        transport=transport,
        now_epoch_s=lambda: _NOW,
    )
    prepared = publisher.prepare()
    with pytest.raises(ReviewContractError) as stale:
        publisher.publish(package_id="0" * 64, disclosure_accepted=True, expires_in_seconds=3600)
    assert stale.value.code == "STUDIO_SHARE_REVISION_STALE"

    response_mutator["enabled"] = True
    with pytest.raises(ReviewContractError) as mismatch:
        publisher.publish(
            package_id=str(prepared["package_id"]),
            disclosure_accepted=True,
            expires_in_seconds=3600,
        )
    assert mismatch.value.code == "STUDIO_SHARE_REMOTE_INVALID"
