from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest

from viewspec import (
    ArtifactContractError,
    ArtifactResponse,
    CompilerAPIError,
    ViewSpecBuilder,
    compile_artifact_remote,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: object, text: str = "", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


def _bundle():
    builder = ViewSpecBuilder("artifact_remote")
    table = builder.add_table("rows", region="main", group_id="rows")
    table.add_row(label="Alpha", value=1)
    return builder.build_bundle()


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _artifact_payload(bundle: dict, target: str = "react-tsx") -> dict:
    content = "export function ViewSpecView() { return null }\n"
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    files = [
        {
            "path": "ViewSpecView.tsx",
            "role": "tsx",
            "content_type": "text/typescript; charset=utf-8",
            "sha256": content_hash,
            "bytes": len(content.encode()),
            "content": content,
        }
    ]
    input_hash = hashlib.sha256(_canonical(bundle)).hexdigest()
    artifact_set_hash = hashlib.sha256(
        _canonical([{"path": "ViewSpecView.tsx", "sha256": content_hash}])
    ).hexdigest()
    material = {
        "schema_version": 1,
        "target": target,
        "input_sha256": input_hash,
        "artifact_set_sha256": artifact_set_hash,
    }
    compiler_material = {
        "profile": "hosted_extended_v1",
        "source_revision": "abc123",
        "api_contract_version": "1.0.0",
        "public_sdk_version": "0.3.0b3",
        "intent_contract_sha256": "1" * 64,
    }
    return {
        "schema_version": 1,
        "build_id": f"vsb_{hashlib.sha256(_canonical(material)).hexdigest()[:32]}",
        "target": target,
        "input_sha256": input_hash,
        "artifact_set_sha256": artifact_set_hash,
        "files": files,
        "provenance": {"manifest_file": None, "sha256": None, "entry_count": 0},
        "diagnostics": [],
        "usage": {"tier": "pro", "usage": 1, "limit": 10_000},
        "compiler": {
            "build_id": f"vsc_{hashlib.sha256(_canonical(compiler_material)).hexdigest()[:32]}",
            **compiler_material,
        },
    }


def _artifact_payload_with_provenance(bundle: dict) -> dict:
    payload = _artifact_payload(bundle)
    manifest = json.dumps({"node_a": {"intent_refs": ["viewspec:binding:a"]}}, sort_keys=True)
    diagnostics = json.dumps([{"severity": "warning", "code": "EXAMPLE"}], sort_keys=True)
    for path, role, content in (
        ("provenance_manifest.json", "manifest", manifest),
        ("diagnostics.json", "diagnostics", diagnostics),
    ):
        encoded = content.encode()
        payload["files"].append(
            {
                "path": path,
                "role": role,
                "content_type": "application/json",
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "bytes": len(encoded),
                "content": content,
            }
        )
    payload["diagnostics"] = json.loads(diagnostics)
    payload["provenance"] = {
        "manifest_file": "provenance_manifest.json",
        "sha256": next(file["sha256"] for file in payload["files"] if file["role"] == "manifest"),
        "entry_count": 1,
    }
    artifact_index = sorted(
        ({"path": file["path"], "sha256": file["sha256"]} for file in payload["files"]),
        key=lambda item: item["path"],
    )
    payload["artifact_set_sha256"] = hashlib.sha256(_canonical(artifact_index)).hexdigest()
    material = {
        "schema_version": 1,
        "target": payload["target"],
        "input_sha256": payload["input_sha256"],
        "artifact_set_sha256": payload["artifact_set_sha256"],
    }
    payload["build_id"] = f"vsb_{hashlib.sha256(_canonical(material)).hexdigest()[:32]}"
    return payload


def _install_httpx(monkeypatch, response: FakeResponse):
    calls = []

    class HTTPError(Exception):
        pass

    def post(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return response

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=post, HTTPError=HTTPError))
    return calls


def test_remote_artifact_helper_posts_and_verifies_response(monkeypatch) -> None:
    bundle = _bundle()
    payload = _artifact_payload(bundle.to_json())
    calls = _install_httpx(monkeypatch, FakeResponse(200, payload))

    response = compile_artifact_remote(bundle, "react-tsx", api_key="secret")

    assert isinstance(response, ArtifactResponse)
    assert response.files[0].path == "ViewSpecView.tsx"
    assert response.compiler.profile == "hosted_extended_v1"
    assert response.compiler.source_revision == "abc123"
    assert response.files[0].content.startswith("export function")
    assert calls[0]["args"][0] == "https://api.viewspec.dev/v1/artifacts"
    assert calls[0]["kwargs"]["headers"]["Authorization"] == "Bearer secret"
    assert calls[0]["kwargs"]["json"] == {"target": "react-tsx", "bundle": bundle.to_json()}


@pytest.mark.parametrize("mutation", ["content", "path", "artifact_set", "build_id", "input_hash"])
def test_remote_artifact_helper_rejects_tampered_contract(monkeypatch, mutation: str) -> None:
    bundle = _bundle()
    payload = deepcopy(_artifact_payload(bundle.to_json()))
    if mutation == "content":
        payload["files"][0]["content"] += "tampered"
    elif mutation == "path":
        payload["files"][0]["path"] = "../ViewSpecView.tsx"
    elif mutation == "artifact_set":
        payload["artifact_set_sha256"] = "0" * 64
    elif mutation == "build_id":
        payload["build_id"] = "vsb_" + "0" * 32
    else:
        payload["input_sha256"] = "0" * 64
    _install_httpx(monkeypatch, FakeResponse(200, payload))

    with pytest.raises(ArtifactContractError):
        compile_artifact_remote(bundle, "react-tsx", api_key="secret")


def test_remote_artifact_helper_surfaces_api_error(monkeypatch) -> None:
    _install_httpx(
        monkeypatch,
        FakeResponse(403, {"error": {"code": "paid_plan_required", "message": "Paid plan required"}}),
    )

    with pytest.raises(CompilerAPIError, match="Paid plan required"):
        compile_artifact_remote(_bundle(), "react-tsx")


def test_remote_artifact_helper_preserves_structured_error_metadata(monkeypatch) -> None:
    _install_httpx(
        monkeypatch,
        FakeResponse(
            403,
            {
                "error": {"code": "paid_plan_required", "message": "Paid plan required", "path": "$.target"},
                "request_id": "req_artifact_123",
            },
            headers={"Retry-After": "60"},
        ),
    )

    with pytest.raises(CompilerAPIError) as raised:
        compile_artifact_remote(_bundle(), "react-tsx")

    assert raised.value.code == "paid_plan_required"
    assert raised.value.path == "$.target"
    assert raised.value.status_code == 403
    assert raised.value.request_id == "req_artifact_123"
    assert raised.value.retry_after == "60"


@pytest.mark.parametrize(
    "mutation",
    ["manifest_file", "manifest_hash", "manifest_count", "manifest_role", "diagnostics"],
)
def test_artifact_provenance_metadata_must_match_verified_files(mutation: str) -> None:
    bundle = _bundle().to_json()
    payload = deepcopy(_artifact_payload_with_provenance(bundle))
    if mutation == "manifest_file":
        payload["provenance"]["manifest_file"] = "missing.json"
    elif mutation == "manifest_hash":
        payload["provenance"]["sha256"] = "0" * 64
    elif mutation == "manifest_count":
        payload["provenance"]["entry_count"] = 2
    elif mutation == "manifest_role":
        next(file for file in payload["files"] if file["path"] == "provenance_manifest.json")["role"] = "source"
    else:
        payload["diagnostics"] = []

    with pytest.raises(ArtifactContractError):
        ArtifactResponse.from_json(payload, expected_input=bundle, expected_target="react-tsx")


def test_artifact_provenance_metadata_accepts_exact_verified_files() -> None:
    bundle = _bundle().to_json()

    response = ArtifactResponse.from_json(
        _artifact_payload_with_provenance(bundle),
        expected_input=bundle,
        expected_target="react-tsx",
    )

    assert response.provenance.manifest_file == "provenance_manifest.json"
    assert response.provenance.entry_count == 1


@pytest.mark.parametrize("field", ["build_id", "profile", "source_revision", "intent_contract_sha256"])
def test_artifact_compiler_identity_is_required_and_typed(field: str) -> None:
    bundle = _bundle().to_json()
    payload = _artifact_payload(bundle)
    payload["compiler"][field] = ""

    with pytest.raises(ArtifactContractError, match="compiler identity"):
        ArtifactResponse.from_json(payload, expected_input=bundle, expected_target="react-tsx")
