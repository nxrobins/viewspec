from __future__ import annotations

import sqlite3

import pytest

from viewspec.studio_review_internal import (
    STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
    STUDIO_REVIEW_INTERNAL_SIGNATURE_HEADER,
    StudioReviewInternalAuth,
    StudioReviewInternalAuthError,
    StudioReviewInternalNonceStore,
)
from viewspec.studio_review_http import STUDIO_REVIEW_MEDIA_TYPE


_SECRET = b"api-to-review-test-secret-material-0001"
_NOW = 1_800_000_000
_REQUEST_HEADERS = {
    "content-type": STUDIO_REVIEW_MEDIA_TYPE,
    "idempotency-key": "internal-review-create-0001",
    "x-viewspec-disclosure-accepted": "true",
    "x-viewspec-expiry-seconds": "3600",
}


def _auth(tmp_path, name: str, *, secret: bytes = _SECRET, now: int = _NOW, nonces=()) -> StudioReviewInternalAuth:
    values = iter(nonces)
    return StudioReviewInternalAuth(
        secret,
        nonce_store=StudioReviewInternalNonceStore(tmp_path / f"{name}.sqlite3"),
        clock=lambda: now,
        nonce_factory=(lambda: next(values)) if nonces else None,
    )


def test_internal_request_and_response_bind_exact_bytes_and_request_nonce(tmp_path) -> None:
    client = _auth(
        tmp_path,
        "client",
        nonces=("1" * 32, "2" * 32),
    )
    service = _auth(
        tmp_path,
        "service",
        nonces=("3" * 32, "4" * 32),
    )
    body = b"deterministic-review-archive"
    request_headers = client.sign_request(
        method="POST",
        path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
        headers=_REQUEST_HEADERS,
        body=body,
    )

    verified = service.verify_request(
        method="POST",
        path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
        headers=request_headers,
        body=body,
    )
    assert verified.request_nonce == "1" * 32
    assert verified.forwarded_headers == _REQUEST_HEADERS
    assert _SECRET.decode("ascii") not in str(request_headers)

    response_body = b'{"ok":true}'
    response_headers = {
        "content-type": "application/json",
        **service.sign_response(
            status=201,
            path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
            content_type="application/json",
            body=response_body,
            request_nonce=verified.request_nonce,
        ),
    }
    client.verify_response(
        status=201,
        path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
        headers=response_headers,
        body=response_body,
        request_nonce=verified.request_nonce,
    )


@pytest.mark.parametrize(
    ("mutation", "expected_body"),
    [
        ("body", b"changed"),
        ("header", b"archive"),
        ("signature", b"archive"),
    ],
)
def test_internal_request_rejects_tampered_body_header_or_signature(tmp_path, mutation, expected_body) -> None:
    client = _auth(tmp_path, f"client-{mutation}", nonces=("a" * 32,))
    service = _auth(tmp_path, f"service-{mutation}")
    signed = client.sign_request(
        method="POST",
        path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
        headers=_REQUEST_HEADERS,
        body=b"archive",
    )
    if mutation == "header":
        signed["idempotency-key"] = "different-request"
    elif mutation == "signature":
        signed[STUDIO_REVIEW_INTERNAL_SIGNATURE_HEADER] = "hmac-sha256:" + ("0" * 64)

    with pytest.raises(StudioReviewInternalAuthError):
        service.verify_request(
            method="POST",
            path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
            headers=signed,
            body=expected_body,
        )


def test_internal_request_rejects_wrong_secret_path_method_and_stale_time(tmp_path) -> None:
    client = _auth(tmp_path, "client", nonces=("b" * 32,))
    signed = client.sign_request(
        method="POST",
        path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
        headers=_REQUEST_HEADERS,
        body=b"archive",
    )

    checks = (
        (_auth(tmp_path, "wrong-secret", secret=b"wrong-review-secret-material-000000"), "POST", STUDIO_REVIEW_INTERNAL_INGRESS_PATH),
        (_auth(tmp_path, "wrong-path"), "POST", "/internal/v1/other"),
        (_auth(tmp_path, "wrong-method"), "PUT", STUDIO_REVIEW_INTERNAL_INGRESS_PATH),
        (_auth(tmp_path, "stale", now=_NOW + 61), "POST", STUDIO_REVIEW_INTERNAL_INGRESS_PATH),
    )
    for service, method, path in checks:
        with pytest.raises(StudioReviewInternalAuthError):
            service.verify_request(method=method, path=path, headers=signed, body=b"archive")


def test_internal_request_replay_stays_rejected_after_authenticator_restart(tmp_path) -> None:
    client = _auth(tmp_path, "client", nonces=("c" * 32,))
    database = tmp_path / "service.sqlite3"
    signed = client.sign_request(
        method="POST",
        path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
        headers=_REQUEST_HEADERS,
        body=b"archive",
    )
    first = StudioReviewInternalAuth(
        _SECRET,
        nonce_store=StudioReviewInternalNonceStore(database),
        clock=lambda: _NOW,
    )
    first.verify_request(
        method="POST",
        path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
        headers=signed,
        body=b"archive",
    )

    restarted = StudioReviewInternalAuth(
        _SECRET,
        nonce_store=StudioReviewInternalNonceStore(database),
        clock=lambda: _NOW,
    )
    with pytest.raises(StudioReviewInternalAuthError):
        restarted.verify_request(
            method="POST",
            path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
            headers=signed,
            body=b"archive",
        )

    with sqlite3.connect(database) as connection:
        retained = connection.execute(
            "SELECT direction, nonce, expires_at FROM studio_review_internal_nonces"
        ).fetchall()
    assert retained == [("request", "c" * 32, _NOW + 60)]


def test_internal_response_rejects_wrong_request_nonce_body_and_replay(tmp_path) -> None:
    client = _auth(tmp_path, "client")
    service = _auth(tmp_path, "service", nonces=("d" * 32,))
    response_body = b'{"ok":true}'
    request_nonce = "e" * 32
    signed = {
        "content-type": "application/json",
        **service.sign_response(
            status=201,
            path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
            content_type="application/json",
            body=response_body,
            request_nonce=request_nonce,
        ),
    }

    with pytest.raises(StudioReviewInternalAuthError):
        client.verify_response(
            status=201,
            path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
            headers=signed,
            body=response_body,
            request_nonce="f" * 32,
        )
    with pytest.raises(StudioReviewInternalAuthError):
        client.verify_response(
            status=201,
            path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
            headers=signed,
            body=b'{"ok":false}',
            request_nonce=request_nonce,
        )

    client.verify_response(
        status=201,
        path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
        headers=signed,
        body=response_body,
        request_nonce=request_nonce,
    )
    with pytest.raises(StudioReviewInternalAuthError):
        client.verify_response(
            status=201,
            path=STUDIO_REVIEW_INTERNAL_INGRESS_PATH,
            headers=signed,
            body=response_body,
            request_nonce=request_nonce,
        )


def test_internal_auth_requires_independent_bounded_secret_and_durable_store(tmp_path) -> None:
    store = StudioReviewInternalNonceStore(tmp_path / "nonces.sqlite3")
    with pytest.raises(ValueError):
        StudioReviewInternalAuth(b"short", nonce_store=store)
    with pytest.raises(ValueError):
        StudioReviewInternalAuth(_SECRET, nonce_store=store, max_clock_skew_seconds=0)
    with pytest.raises(TypeError):
        StudioReviewInternalAuth(_SECRET, nonce_store=object())  # type: ignore[arg-type]
