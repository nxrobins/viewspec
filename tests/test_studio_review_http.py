from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from urllib.parse import parse_qs, urlsplit

import pytest

from viewspec.app_starters import starter_app_bundle
import viewspec.review_compile as review_compile
from viewspec.review_compile import STUDIO_COMPARE_TARGET
from viewspec.review_runtime import ReviewRuntime
import viewspec.studio_review_http as review_http
from viewspec.studio_review_http import (
    ReviewHTTPRequest,
    STUDIO_REVIEW_COOKIE_NAME,
    STUDIO_REVIEW_MEDIA_TYPE,
    StudioReviewHTTPAdapter,
)
from viewspec.studio_review_service import StudioReviewService
from viewspec.studio_share import prepare_studio_share


_REAL_SUBPROCESS_RUN = review_compile.subprocess.run
_SIGNING_KEY = b"test-only-studio-review-http-signing-key"
_ORIGIN = "https://review.viewspec.test"


def _fake_react_npm(command, *, cwd, **kwargs):
    if tuple(command[:3]) == ("npm", "run", "build"):
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


def _passing_verifier(_package: Path, envelope: dict[str, object]) -> dict[str, object]:
    revision = envelope["revision"]
    totals = envelope["totals"]
    assert isinstance(revision, dict) and isinstance(totals, dict)
    return {
        "schema_version": 1,
        "status": "passed",
        "verifier_id": "test-sandbox-v1",
        "package_id": envelope["package_id"],
        "source_sha256": revision["source_sha256"],
        "artifact_set_sha256": revision["artifact_set_sha256"],
        "root_manifest_sha256": revision["root_manifest_sha256"],
        "inspection_sha256": revision["inspection_sha256"],
        "target_artifact_sets": revision["target_artifact_sets"],
        "rebuild": {
            "evidence_sha256": "b" * 64,
            "expected_inventory_sha256": revision["artifact_set_sha256"],
            "observed_inventory_sha256": revision["artifact_set_sha256"],
            "source_only_request": True,
            "install_used": False,
            "lifecycle_hooks_disabled": True,
            "uploaded_artifacts_executed": False,
        },
        "sandbox": {
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
        },
    }


@pytest.fixture
def private_review(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "viewspec.app.json"
    source.write_text(json.dumps(starter_app_bundle("internal_tool"), sort_keys=True), encoding="utf-8")
    state = project / "review-state"
    monkeypatch.setattr("viewspec.review_compile.subprocess.run", _fake_react_npm)
    ReviewRuntime.open(source, state_root=state, target=STUDIO_COMPARE_TARGET, allow_install=True)
    prepared = prepare_studio_share(source, state_root=state, cwd=project)
    archive = Path(prepared["paths"]["upload_archive"])
    service = StudioReviewService(
        tmp_path / "service",
        signing_key=_SIGNING_KEY,
        verifier=_passing_verifier,
        clock=lambda: 1_800_000_000,
    )
    adapter = StudioReviewHTTPAdapter(
        service,
        public_origin=_ORIGIN,
        authorize_upload=lambda headers: headers.get("authorization") == "Bearer upload-test",
    )
    return adapter, service, archive


def _request(
    adapter: StudioReviewHTTPAdapter,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    scheme: str = "https",
):
    return adapter.handle(
        ReviewHTTPRequest(method=method, path=path, headers=headers or {}, body=body, scheme=scheme)
    )


def _headers(response) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for name, value in response.headers:
        values.setdefault(name.lower(), []).append(value)
    return values


def _create(adapter: StudioReviewHTTPAdapter, archive: Path, *, key: str = "create-session-0001"):
    return _request(
        adapter,
        "POST",
        "/v1/reviews",
        headers={
            "Authorization": "Bearer upload-test",
            "Content-Type": STUDIO_REVIEW_MEDIA_TYPE,
            "Idempotency-Key": key,
            "X-ViewSpec-Disclosure-Accepted": "true",
            "X-ViewSpec-Expiry-Seconds": "3600",
        },
        body=archive.read_bytes(),
    )


def _exchange(adapter: StudioReviewHTTPAdapter, link: str) -> tuple[str, dict[str, object]]:
    parsed = urlsplit(link)
    capability = parse_qs(parsed.fragment)["cap"][0]
    response = _request(
        adapter,
        "POST",
        f"{parsed.path}exchange",
        headers={"Content-Type": "application/json", "Origin": _ORIGIN},
        body=json.dumps({"capability": capability}).encode(),
    )
    assert response.status == 200
    cookie_header = _headers(response)["set-cookie"][0]
    return cookie_header.split(";", 1)[0], response.json()


def _comment_context(revision: dict[str, object]) -> dict[str, object]:
    session = revision["session"]
    screens = revision["screens"]
    routes = revision["routes"]
    assert isinstance(session, dict) and isinstance(screens, list) and isinstance(routes, list)
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


def test_create_requires_https_upload_identity_and_deliberate_disclosure(private_review) -> None:
    adapter, _service, archive = private_review
    unauthorized = _request(
        adapter,
        "POST",
        "/v1/reviews",
        headers={"Content-Type": STUDIO_REVIEW_MEDIA_TYPE},
        body=archive.read_bytes(),
    )
    assert unauthorized.status == 401
    assert unauthorized.json()["error"]["code"] == "STUDIO_REVIEW_UPLOAD_UNAUTHORIZED"

    insecure = _request(adapter, "GET", "/review/vsr_AAAAAAAAAAAAAAAAAAAAAAAA", scheme="http")
    assert insecure.status == 400
    assert insecure.json()["error"]["code"] == "STUDIO_REVIEW_HTTPS_REQUIRED"

    response = _create(adapter, archive)
    assert response.status == 201
    created = response.json()
    assert created["ingress"] == {
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "archive_bytes": archive.stat().st_size,
    }
    for role in ("owner", "reviewer"):
        parsed = urlsplit(created["links"][role])
        assert parsed.scheme == "https" and parsed.netloc == "review.viewspec.test"
        assert parsed.path == f"/review/{created['session']['id']}/"
        assert parse_qs(parsed.fragment)["cap"][0].startswith("vsc_")
        assert "vsc_" not in parsed.path
    assert "fragment_capabilities" not in created
    assert created["links"]["transport"] == "url_fragment_one_time_exchange"


def test_shell_is_generic_private_and_non_discoverable(private_review) -> None:
    adapter, _service, _archive = private_review
    redirect = _request(adapter, "GET", "/review/vsr_AAAAAAAAAAAAAAAAAAAAAAAA")
    assert redirect.status == 308
    assert _headers(redirect)["location"] == ["/review/vsr_AAAAAAAAAAAAAAAAAAAAAAAA/"]
    known_shape = "/review/vsr_AAAAAAAAAAAAAAAAAAAAAAAA/"
    other_shape = "/review/vsr_BBBBBBBBBBBBBBBBBBBBBBBB/"
    first = _request(adapter, "GET", known_shape)
    second = _request(adapter, "GET", other_shape)
    assert first.status == second.status == 200
    assert first.body == second.body
    headers = _headers(first)
    assert headers["cache-control"] == ["private, no-store"]
    assert headers["x-robots-tag"] == ["noindex, noarchive"]
    assert headers["referrer-policy"] == ["no-referrer"]
    assert headers["strict-transport-security"] == ["max-age=31536000; includeSubDomains"]
    assert headers["cross-origin-resource-policy"] == ["same-origin"]
    assert "camera=()" in headers["permissions-policy"][0]
    assert "frame-ancestors 'none'" in headers["content-security-policy"][0]

    for asset_name, expected_type in (
        ("client.js", "text/javascript; charset=utf-8"),
        ("client.css", "text/css; charset=utf-8"),
    ):
        asset = _request(adapter, "GET", f"{known_shape}{asset_name}")
        asset_headers = _headers(asset)
        assert asset.status == 200
        assert asset_headers["content-type"] == [expected_type]
        assert asset_headers["cache-control"] == ["private, no-store"]
        assert asset_headers["cross-origin-resource-policy"] == ["same-origin"]
        assert asset_headers["x-content-type-options"] == ["nosniff"]
        assert "frame-ancestors 'none'" in asset_headers["content-security-policy"][0]
        assert asset_headers["cross-origin-opener-policy"] == ["same-origin"]
        assert asset_headers["x-frame-options"] == ["DENY"]


def test_fragment_exchange_is_one_time_and_mints_only_a_scoped_hardened_cookie(private_review) -> None:
    adapter, service, archive = private_review
    created = _create(adapter, archive).json()
    reviewer_link = created["links"]["reviewer"]
    cookie, exchanged = _exchange(adapter, reviewer_link)
    set_cookie = _headers(
        _request(
            adapter,
            "POST",
            f"{urlsplit(created['links']['owner']).path}exchange",
            headers={"Content-Type": "application/json", "Origin": _ORIGIN},
            body=json.dumps(
                {"capability": parse_qs(urlsplit(created["links"]["owner"]).fragment)["cap"][0]}
            ).encode(),
        )
    )["set-cookie"][0]
    assert exchanged["role"] == "reviewer"
    assert "cookie_value" not in exchanged
    assert cookie.startswith(f"{STUDIO_REVIEW_COOKIE_NAME}=vss_")
    assert "; Secure; HttpOnly; SameSite=Strict" in set_cookie
    assert f"Path=/review/{created['session']['id']}/" in set_cookie
    assert "Max-Age=3600" in set_cookie

    parsed = urlsplit(reviewer_link)
    capability = parse_qs(parsed.fragment)["cap"][0]
    reused = _request(
        adapter,
        "POST",
        f"{parsed.path}exchange",
        headers={"Content-Type": "application/json", "Origin": _ORIGIN},
        body=json.dumps({"capability": capability}).encode(),
    )
    assert reused.status == 404
    persisted = service.database.read_bytes()
    assert capability.encode() not in persisted
    assert cookie.partition("=")[2].encode() not in persisted


def test_revision_and_exact_artifact_reads_never_expose_source(private_review) -> None:
    adapter, _service, archive = private_review
    created = _create(adapter, archive).json()
    session_path = urlsplit(created["links"]["reviewer"]).path.rstrip("/")
    cookie, _ = _exchange(adapter, created["links"]["reviewer"])
    revision_response = _request(adapter, "GET", f"{session_path}/revision", headers={"Cookie": cookie})
    assert revision_response.status == 200
    revision = revision_response.json()
    assert revision["role"] == "reviewer"
    assert revision["inspection"]["policy"]["production_data"] == "not_claimed"
    assert all(item["path"].startswith("artifacts/") for item in revision["artifacts"])

    artifact = _request(
        adapter,
        "GET",
        f"{session_path}/artifact/static/index.html",
        headers={"Cookie": cookie},
    )
    assert artifact.status == 200
    artifact_headers = _headers(artifact)
    assert artifact_headers["etag"][0].startswith('"sha256-')
    source_metadata = next(item for item in revision["artifacts"] if item["path"] == "artifacts/static/index.html")
    assert artifact_headers["x-viewspec-source-artifact-sha256"] == [source_metadata["sha256"]]
    assert artifact_headers["x-viewspec-presentation-derivation"] == ["hosted-review-frame-v1"]
    assert hashlib.sha256(artifact.body).hexdigest() in artifact_headers["etag"][0]
    assert b'id="viewspec-hosted-review-bootstrap"' in artifact.body
    assert b'id="viewspec-hosted-review-sdk"' in artifact.body
    csp = artifact_headers["content-security-policy"][0]
    assert "connect-src 'none'" in csp and "sandbox allow-scripts" in csp

    source = _request(
        adapter,
        "GET",
        f"{session_path}/artifact/source/viewspec.app.json",
        headers={"Cookie": cookie},
    )
    assert source.status == 404
    assert source.json()["error"]["code"] == "STUDIO_REVIEW_ARTIFACT_FORBIDDEN"
    traversal = _request(
        adapter,
        "GET",
        f"{session_path}/artifact/static%2F..%2Fsource/viewspec.app.json",
        headers={"Cookie": cookie},
    )
    assert traversal.status == 400


def test_review_comment_and_owner_approval_keep_roles_and_origin_separate(private_review) -> None:
    adapter, _service, archive = private_review
    created = _create(adapter, archive).json()
    session_path = urlsplit(created["links"]["owner"]).path.rstrip("/")
    owner_cookie, _ = _exchange(adapter, created["links"]["owner"])
    reviewer_cookie, _ = _exchange(adapter, created["links"]["reviewer"])
    revision = _request(adapter, "GET", f"{session_path}/revision", headers={"Cookie": reviewer_cookie}).json()
    context = _comment_context(revision)

    missing_origin = _request(
        adapter,
        "POST",
        f"{session_path}/comments",
        headers={
            "Content-Type": "application/json",
            "Cookie": reviewer_cookie,
            "Idempotency-Key": "comment-request-0001",
        },
        body=json.dumps({"body": "Make the priority easier to scan.", "context": context}).encode(),
    )
    assert missing_origin.status == 403
    assert missing_origin.json()["error"]["code"] == "STUDIO_REVIEW_ORIGIN_FORBIDDEN"

    comment = _request(
        adapter,
        "POST",
        f"{session_path}/comments",
        headers={
            "Content-Type": "application/json",
            "Cookie": reviewer_cookie,
            "Idempotency-Key": "comment-request-0001",
            "Origin": _ORIGIN,
        },
        body=json.dumps({"body": "Make the priority easier to scan.", "context": context}).encode(),
    )
    assert comment.status == 201
    assert comment.json()["status"] == "acknowledged"

    reviewer_approval = _request(
        adapter,
        "POST",
        f"{session_path}/approval",
        headers={
            "Content-Type": "application/json",
            "Cookie": reviewer_cookie,
            "Idempotency-Key": "approve-request-0001",
            "Origin": _ORIGIN,
        },
        body=json.dumps(
            {"revision_identity_sha256": revision["session"]["revision_identity_sha256"]}
        ).encode(),
    )
    assert reviewer_approval.status == 403
    assert reviewer_approval.json()["error"]["code"] == "STUDIO_REVIEW_ROLE_FORBIDDEN"

    approval = _request(
        adapter,
        "POST",
        f"{session_path}/approval",
        headers={
            "Content-Type": "application/json",
            "Cookie": owner_cookie,
            "Idempotency-Key": "approve-request-0001",
            "Origin": _ORIGIN,
        },
        body=json.dumps(
            {"revision_identity_sha256": revision["session"]["revision_identity_sha256"]}
        ).encode(),
    )
    assert approval.status == 201
    assert approval.json()["status"] == "approved"


def test_reviewer_rotation_uses_canonical_session_path_and_revokes_old_access(private_review) -> None:
    adapter, _service, archive = private_review
    created = _create(adapter, archive).json()
    session_path = urlsplit(created["links"]["owner"]).path.rstrip("/")
    owner_cookie, _ = _exchange(adapter, created["links"]["owner"])
    reviewer_cookie, _ = _exchange(adapter, created["links"]["reviewer"])
    rotated = _request(
        adapter,
        "POST",
        f"{session_path}/lifecycle",
        headers={
            "Content-Type": "application/json",
            "Cookie": owner_cookie,
            "Idempotency-Key": "rotate-request-0001",
            "Origin": _ORIGIN,
        },
        body=b'{"action":"rotate_reviewer"}',
    )
    assert rotated.status == 200
    reviewer_url = rotated.json()["reviewer_url"]
    assert urlsplit(reviewer_url).path == f"{session_path}/"
    assert parse_qs(urlsplit(reviewer_url).fragment)["cap"][0].startswith("vsc_")
    denied = _request(adapter, "GET", f"{session_path}/revision", headers={"Cookie": reviewer_cookie})
    assert denied.status == 404


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_private_review_client_is_syntax_valid_and_removes_fragment_before_exchange() -> None:
    for script in (
        review_http._REVIEW_CLIENT,
        review_http._HOSTED_FRAME_BOOTSTRAP,
        review_http._HOSTED_FRAME_CLIENT,
    ):
        result = subprocess.run(
            ["node", "--check", "-"],
            input=script,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    client = review_http._REVIEW_CLIENT
    assert client.index("history.replaceState") < client.index("request('./exchange'")
    assert "localStorage" not in client and "sessionStorage" not in client
    assert 'title="Static product"' in client and 'title="React product"' in client
    assert all(f'data-width="{width}"' in client for width in (390, 768, 1440))
    assert "Production data is not claimed" in client
    assert "viewspec-hosted-selected" in review_http._HOSTED_FRAME_CLIENT
    assert "viewspec-hosted-replay-result" in review_http._HOSTED_FRAME_CLIENT
    assert "app.innerHTML = `<header>" in client
    assert "${review.role}" not in client.split("app.innerHTML =", 1)[1].split("`", 2)[1]


def test_malformed_transport_inputs_fail_closed_without_reflection(private_review) -> None:
    adapter, _service, _archive = private_review
    query = _request(adapter, "GET", "/review/vsr_AAAAAAAAAAAAAAAAAAAAAAAA?cap=secret-canary")
    assert query.status == 400
    assert b"secret-canary" not in query.body
    duplicate_cookie = _request(
        adapter,
        "GET",
        "/review/vsr_AAAAAAAAAAAAAAAAAAAAAAAA/revision",
        headers={"Cookie": f"{STUDIO_REVIEW_COOKIE_NAME}=one; {STUDIO_REVIEW_COOKIE_NAME}=two"},
    )
    assert duplicate_cookie.status == 404
    assert duplicate_cookie.json()["error"]["code"] == "STUDIO_REVIEW_ACCESS_DENIED"
    control_header = _request(
        adapter,
        "GET",
        "/review/vsr_AAAAAAAAAAAAAAAAAAAAAAAA/",
        headers={"X-Test": "unsafe\tvalue"},
    )
    assert control_header.status == 400
    assert control_header.json()["error"]["code"] == "STUDIO_REVIEW_HTTP_INVALID"


def test_upload_and_extraction_use_only_owned_staging(private_review, monkeypatch) -> None:
    import viewspec.studio_review_service as service_module
    adapter, service, archive = private_review
    original = service_module.materialize_studio_share_archive
    seen = []
    def materialize(path, directory):
        assert Path(path).parent == service.ingress
        assert Path(directory).parent == service.ingress
        seen.append(True)
        return original(path, directory)
    monkeypatch.setattr(service_module, "materialize_studio_share_archive", materialize)
    assert _create(adapter, archive).status == 201
    assert seen == [True] and not list(service.ingress.iterdir())
    assert not list(service.root.glob(".upload-*")) and not list(service.root.glob(".ingress-*"))
    assert service.verify_storage()["session_count"] == 1
