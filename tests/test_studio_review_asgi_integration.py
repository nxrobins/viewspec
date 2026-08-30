from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from viewspec.app_starters import starter_app_bundle
import viewspec.review_compile as review_compile
from viewspec.review_compile import STUDIO_COMPARE_TARGET
from viewspec.review_runtime import ReviewRuntime
from viewspec.studio_review_asgi import StudioReviewASGIApp
from viewspec.studio_review_http import STUDIO_REVIEW_MEDIA_TYPE, StudioReviewHTTPAdapter
from viewspec.studio_review_internal import (
    STUDIO_REVIEW_INTERNAL_AUTHENTICATED_HEADER,
    STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
    STUDIO_REVIEW_INTERNAL_NONCE_HEADER,
    StudioReviewInternalAuth,
    StudioReviewInternalNonceStore,
    authorize_internal_studio_review_upload,
)
from viewspec.studio_review_service import StudioReviewService
from viewspec.studio_review_verify import (
    STUDIO_REVIEW_DEPENDENCY_SEED_ENV,
    bind_studio_review_sandbox_attestation,
    rebuild_studio_review_package,
    studio_review_rebuild_evidence_sha256,
)
from viewspec.studio_share import prepare_studio_share


_REAL_SUBPROCESS_RUN = review_compile.subprocess.run
_ORIGIN = "https://review.viewspec.test"


def _fake_react_npm(command, *, cwd, **kwargs):
    if tuple(command[:3]) == ("npm", "run", "build") or Path(command[0]).name == "vite":
        runtime = Path(cwd) / "runtime-dist"
        assets = runtime / "assets"
        assets.mkdir(parents=True)
        assets.joinpath("main.js").write_text(
            "document.getElementById('root').textContent='React ready';",
            encoding="utf-8",
        )
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


def _test_sandbox_verifier(package: Path, envelope: dict[str, object]) -> dict[str, object]:
    totals = envelope["totals"]
    assert isinstance(totals, dict)
    rebuild = rebuild_studio_review_package(package, envelope)
    attestation = {
        "schema_version": 1,
        "kind": "studio_review_sandbox_attestation",
        "status": "passed",
        "runner_id": "test-asgi-isolated-runner-v1",
        "rebuild_evidence_sha256": studio_review_rebuild_evidence_sha256(rebuild),
        "network": "denied",
        "lifecycle_hooks": "disabled",
        "arbitrary_commands": "disabled",
        "limits": {
            "cpu_seconds": 20,
            "memory_bytes": 256 * 1024 * 1024,
            "wall_seconds": 90,
            "file_count": totals["file_count"],
            "byte_count": totals["bytes"],
        },
    }
    return bind_studio_review_sandbox_attestation(rebuild, attestation, envelope=envelope)


def _prepared_archive(tmp_path: Path, monkeypatch) -> Path:
    source = tmp_path / "project/viewspec.app.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(starter_app_bundle("internal_tool"), sort_keys=True), encoding="utf-8")
    dependency_seed = tmp_path / "trusted-node-modules"
    dependency_seed.mkdir()
    dependency_seed.joinpath(".package-lock.json").write_text(
        '{"lockfileVersion":3,"name":"viewspec-studio-review","packages":{}}',
        encoding="utf-8",
    )
    monkeypatch.setenv(STUDIO_REVIEW_DEPENDENCY_SEED_ENV, str(dependency_seed))
    state = tmp_path / "review-state"
    monkeypatch.setattr(review_compile.subprocess, "run", _fake_react_npm)
    ReviewRuntime.open(source, state_root=state, target=STUDIO_COMPARE_TARGET, allow_install=True)
    prepared = prepare_studio_share(source, state_root=state, cwd=tmp_path)
    return Path(prepared["paths"]["upload_archive"])


def _review_adapter(tmp_path: Path) -> StudioReviewHTTPAdapter:
    service = StudioReviewService(
        tmp_path / "service",
        signing_key=b"test-only-asgi-review-signing-key-material",
        verifier=_test_sandbox_verifier,
        clock=lambda: 1_800_000_000,
    )
    return StudioReviewHTTPAdapter(
        service,
        public_origin=_ORIGIN,
        authorize_upload=authorize_internal_studio_review_upload,
    )


def _context(revision: dict[str, object]) -> dict[str, object]:
    session = revision["session"]
    routes = revision["routes"]
    screens = revision["screens"]
    assert isinstance(session, dict) and isinstance(routes, list) and isinstance(screens, list)
    screen = screens[0]
    assert isinstance(screen, dict)
    route = next(item for item in routes if item["screen_id"] == screen["id"])
    targets = screen["targets"]
    assert isinstance(targets, dict)
    return {
        "revision_identity_sha256": session["revision_identity_sha256"],
        "route": route["path"],
        "screen_id": screen["id"],
        "semantic_identity_sha256": screen["semantic_identity_sha256"],
        "viewport_width": 390,
        "target": {"kind": "node", "id": targets["node"][0]},
        "replay_evidence_ref": None,
    }


def test_default_direct_create_cannot_spoof_internal_auth_marker(tmp_path, monkeypatch) -> None:
    archive = _prepared_archive(tmp_path, monkeypatch)
    internal_secret = b"test-only-api-to-review-hmac-key-material"
    internal_service = StudioReviewInternalAuth(
        internal_secret,
        nonce_store=StudioReviewInternalNonceStore(tmp_path / "review-internal-nonces.sqlite3"),
        clock=lambda: 1_800_000_000,
    )
    app = StudioReviewASGIApp(_review_adapter(tmp_path), internal_auth=internal_service)

    async def journey() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=_ORIGIN) as owner:
            response = await owner.post(
                "/v1/reviews",
                headers={
                    "Content-Type": STUDIO_REVIEW_MEDIA_TYPE,
                    "Idempotency-Key": "asgi-forged-create-0001",
                    "X-ViewSpec-Disclosure-Accepted": "true",
                    "X-ViewSpec-Expiry-Seconds": "3600",
                    STUDIO_REVIEW_INTERNAL_AUTHENTICATED_HEADER: "true",
                },
                content=archive.read_bytes(),
            )
            assert response.status_code == 401
            assert "vsr_" not in response.text

    asyncio.run(journey())


def test_real_asgi_adapter_completes_private_comment_and_owner_approval(tmp_path, monkeypatch) -> None:
    archive = _prepared_archive(tmp_path, monkeypatch)
    adapter = _review_adapter(tmp_path)
    internal_secret = b"test-only-api-to-review-hmac-key-material"
    internal_client = StudioReviewInternalAuth(
        internal_secret,
        nonce_store=StudioReviewInternalNonceStore(tmp_path / "api-internal-nonces.sqlite3"),
        clock=lambda: 1_800_000_000,
        nonce_factory=lambda: "1" * 32,
    )
    internal_service = StudioReviewInternalAuth(
        internal_secret,
        nonce_store=StudioReviewInternalNonceStore(tmp_path / "review-internal-nonces.sqlite3"),
        clock=lambda: 1_800_000_000,
        nonce_factory=lambda: "2" * 32,
    )
    app = StudioReviewASGIApp(
        adapter,
        internal_auth=internal_service,
        allow_direct_create=False,
    )

    async def journey() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=_ORIGIN) as owner:
            archive_bytes = archive.read_bytes()
            creation_headers = {
                "Content-Type": STUDIO_REVIEW_MEDIA_TYPE,
                "Idempotency-Key": "asgi-create-session-0001",
                "X-ViewSpec-Disclosure-Accepted": "true",
                "X-ViewSpec-Expiry-Seconds": "3600",
            }
            signed_headers = internal_client.sign_request(
                method="POST",
                path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
                headers=creation_headers,
                body=archive_bytes,
            )
            direct = await owner.post(
                "/v1/reviews",
                headers=creation_headers,
                content=archive_bytes,
            )
            assert direct.status_code == 404
            response = await owner.post(
                STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
                headers=signed_headers,
                content=archive_bytes,
            )
            assert response.status_code == 201
            internal_client.verify_response(
                status=response.status_code,
                path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
                headers=dict(response.headers),
                body=response.content,
                request_nonce=signed_headers[STUDIO_REVIEW_INTERNAL_NONCE_HEADER],
            )
            assert "x-viewspec-internal" not in response.text.lower()
            assert internal_secret.decode() not in response.text
            created = response.json()
            session_id = created["session"]["id"]
            owner_capability = parse_qs(urlsplit(created["links"]["owner"]).fragment)["cap"][0]
            reviewer_capability = parse_qs(urlsplit(created["links"]["reviewer"]).fragment)["cap"][0]

            exchanged = await owner.post(
                f"/review/{session_id}/exchange",
                headers={"Origin": _ORIGIN},
                json={"capability": owner_capability},
            )
            assert exchanged.status_code == 200

            async with httpx.AsyncClient(transport=transport, base_url=_ORIGIN) as reviewer:
                exchanged = await reviewer.post(
                    f"/review/{session_id}/exchange",
                    headers={"Origin": _ORIGIN},
                    json={"capability": reviewer_capability},
                )
                assert exchanged.status_code == 200
                revision_response = await reviewer.get(f"/review/{session_id}/revision")
                assert revision_response.status_code == 200
                revision = revision_response.json()
                commented = await reviewer.post(
                    f"/review/{session_id}/comments",
                    headers={"Origin": _ORIGIN, "Idempotency-Key": "asgi-comment-0001"},
                    json={"body": "Make the primary action calmer.", "context": _context(revision)},
                )
                assert commented.status_code == 201
                assert commented.json()["comment"]["body"] == "Make the primary action calmer."

            approved = await owner.post(
                f"/review/{session_id}/approval",
                headers={"Origin": _ORIGIN, "Idempotency-Key": "asgi-approval-0001"},
                json={"revision_identity_sha256": created["session"]["revision_identity_sha256"]},
            )
            assert approved.status_code == 201
            assert approved.json()["approval"]["revision_identity_sha256"] == created["session"][
                "revision_identity_sha256"
            ]

    asyncio.run(journey())
