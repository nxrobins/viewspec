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
    def __init__(self, status_code: int, payload: object, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

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
    }


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
