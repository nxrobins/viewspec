from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import sys
from types import SimpleNamespace

from hypothesis import given, strategies as st
import pytest

import viewspec.hosted_app_bundles as hosted_app_bundles
from viewspec import (
    AppBundleBuildContractError,
    AppBundleBuildResponse,
    CompilerAPIError,
    compile_app_remote,
    starter_app_bundle,
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _encoded(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _key_and_receipt(payload: dict) -> tuple[dict, dict]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b"hosted-app-test").digest())
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = f"vsk_{hashlib.sha256(public_bytes).hexdigest()[:16]}"
    key = {"algorithm": "ed25519", "key_id": key_id, "public_key": _encoded(public_bytes)}
    receipt = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "payload": payload,
        "signature": _encoded(private_key.sign(_canonical(payload))),
    }
    return key, receipt


def _file(path: str, role: str, content: str) -> dict:
    encoded = content.encode()
    return {
        "path": path,
        "role": role,
        "content_type": "application/json" if path.endswith(".json") else "text/typescript; charset=utf-8",
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "bytes": len(encoded),
        "content": content,
    }


def _payload(bundle: dict) -> tuple[dict, dict]:
    source = _file("src/ViewSpecApp.tsx", "source", "export function ViewSpecApp() { return null }\n")
    input_hash = hashlib.sha256(_canonical(bundle)).hexdigest()
    manifest_payload = {
        "schema_version": 1,
        "target": "react-tailwind-app",
        "input_sha256": input_hash,
        "app_schema_version": bundle["schema_version"],
        "sdk_version": "0.3.0b3",
        "files": [
            {
                "bytes": source["bytes"],
                "path": source["path"],
                "role": source["role"],
                "sha256": source["sha256"],
            }
        ],
    }
    manifest = _file("hosted_app_manifest.json", "manifest", json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n")
    files = [manifest, source]
    artifact_set_hash = hashlib.sha256(
        _canonical([{"path": item["path"], "sha256": item["sha256"]} for item in files])
    ).hexdigest()
    material = {
        "schema_version": 1,
        "target": "react-tailwind-app",
        "input_sha256": input_hash,
        "artifact_set_sha256": artifact_set_hash,
    }
    build_id = f"vab_{hashlib.sha256(_canonical(material)).hexdigest()[:32]}"
    receipt_payload = {
        "receipt_type": "viewspec_app_build_v1",
        "build_id": build_id,
        "target": "react-tailwind-app",
        "input_sha256": input_hash,
        "artifact_set_sha256": artifact_set_hash,
        "manifest_sha256": manifest["sha256"],
        "app_schema_version": bundle["schema_version"],
        "sdk_version": "0.3.0b3",
        "issued_at": "2026-07-12T12:00:00Z",
    }
    key, receipt = _key_and_receipt(receipt_payload)
    return {
        "schema_version": 1,
        "build_id": build_id,
        "target": "react-tailwind-app",
        "app_schema_version": bundle["schema_version"],
        "sdk_version": "0.3.0b3",
        "input_sha256": input_hash,
        "artifact_set_sha256": artifact_set_hash,
        "files": files,
        "provenance": {
            "manifest_file": manifest["path"],
            "sha256": manifest["sha256"],
            "entry_count": 1,
        },
        "usage": {"tier": "pro", "usage": 1, "limit": 10_000},
        "receipt": receipt,
    }, key


class FakeResponse:
    def __init__(self, status_code: int, payload: object, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _install_httpx(monkeypatch, build_response: FakeResponse, key_response: FakeResponse):
    calls = []

    class HTTPError(Exception):
        pass

    def post(*args, **kwargs):
        calls.append(("post", args, kwargs))
        return build_response

    def get(*args, **kwargs):
        calls.append(("get", args, kwargs))
        return key_response

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=post, get=get, HTTPError=HTTPError))
    return calls


def test_hosted_app_response_verifies_and_materializes(tmp_path) -> None:
    bundle = starter_app_bundle()
    payload, key = _payload(bundle)

    response = AppBundleBuildResponse.from_json(payload, expected_input=bundle, receipt_public_key=key)
    output = response.write_to(tmp_path / "app")

    assert response.build_id.startswith("vab_")
    assert output.joinpath("src/ViewSpecApp.tsx").is_file()
    assert output.joinpath("hosted_app_manifest.json").is_file()


def test_hosted_app_response_file_count_is_bounded(monkeypatch) -> None:
    bundle = starter_app_bundle()
    payload, key = _payload(bundle)
    monkeypatch.setattr(hosted_app_bundles, "MAX_APP_BUILD_FILES", 1)

    with pytest.raises(AppBundleBuildContractError, match="too many files"):
        AppBundleBuildResponse.from_json(payload, expected_input=bundle, receipt_public_key=key)


@pytest.mark.parametrize(
    "mutation",
    ["content", "path", "manifest", "artifact_set", "build_id", "input_hash", "receipt_payload", "signature"],
)
def test_hosted_app_response_rejects_any_tampered_identity(mutation: str) -> None:
    bundle = starter_app_bundle()
    payload, key = _payload(bundle)
    payload = deepcopy(payload)
    if mutation == "content":
        payload["files"][1]["content"] += "tampered"
    elif mutation == "path":
        payload["files"][1]["path"] = "../ViewSpecApp.tsx"
    elif mutation == "manifest":
        payload["provenance"]["sha256"] = "0" * 64
    elif mutation == "artifact_set":
        payload["artifact_set_sha256"] = "0" * 64
    elif mutation == "build_id":
        payload["build_id"] = "vab_" + "0" * 32
    elif mutation == "input_hash":
        payload["input_sha256"] = "0" * 64
    elif mutation == "receipt_payload":
        payload["receipt"]["payload"]["build_id"] = "vab_tampered"
    else:
        payload["receipt"]["signature"] = "not-base64"

    with pytest.raises(AppBundleBuildContractError):
        AppBundleBuildResponse.from_json(payload, expected_input=bundle, receipt_public_key=key)


@given(prefix=st.sampled_from(["../", "/", "./", "src/../", "src\\"]), leaf=st.text(min_size=1, max_size=24))
def test_hosted_app_paths_reject_traversal_and_noncanonical_forms(prefix: str, leaf: str) -> None:
    bundle = starter_app_bundle()
    payload, key = _payload(bundle)
    payload["files"][1]["path"] = prefix + leaf

    with pytest.raises(AppBundleBuildContractError):
        AppBundleBuildResponse.from_json(payload, expected_input=bundle, receipt_public_key=key)


def test_remote_app_helper_posts_fetches_key_and_returns_verified_build(monkeypatch) -> None:
    bundle = starter_app_bundle()
    payload, key = _payload(bundle)
    calls = _install_httpx(monkeypatch, FakeResponse(200, payload), FakeResponse(200, key))

    response = compile_app_remote(bundle, api_key="secret")

    assert response.build_id == payload["build_id"]
    assert calls[0][0:2] == ("post", ("https://api.viewspec.dev/v1/app-bundles/build",))
    assert calls[1][0:2] == ("get", ("https://api.viewspec.dev/v1/receipt-key",))
    assert calls[0][2]["json"] == {"target": "react-tailwind-app", "app_bundle": bundle}


def test_remote_app_helper_preserves_structured_errors(monkeypatch) -> None:
    _install_httpx(
        monkeypatch,
        FakeResponse(422, {"error": {"code": "APP_BUNDLE_INVALID", "message": "invalid", "path": "$.app_bundle"}}),
        FakeResponse(500, {}),
    )

    with pytest.raises(CompilerAPIError) as raised:
        compile_app_remote(starter_app_bundle())

    assert raised.value.code == "APP_BUNDLE_INVALID"
    assert raised.value.path == "$.app_bundle"
