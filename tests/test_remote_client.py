from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from viewspec import (
    ASTBundle,
    CompilerAPIError,
    ViewSpecBuilder,
    compile,
    compile_auto,
    compile_remote,
    compile_remote_response,
)


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _bundle():
    builder = ViewSpecBuilder("remote")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="1")
    return builder.build_bundle()


def _install_fake_httpx(monkeypatch, response=None, exc=None):
    calls = []

    class HTTPError(Exception):
        pass

    def post(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        if exc:
            raise exc
        return response

    fake_httpx = SimpleNamespace(post=post, HTTPError=HTTPError)
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    return calls, HTTPError


def test_compile_remote_posts_to_canonical_origin(monkeypatch):
    bundle = _bundle()
    ast = compile(bundle)
    calls, _ = _install_fake_httpx(monkeypatch, FakeResponse(200, {"ast": ast.to_json()}))

    restored = compile_remote(bundle, api_key="secret")

    assert isinstance(restored, ASTBundle)
    assert restored.title == ast.title
    assert calls[0]["args"][0] == "https://api.viewspec.dev/v1/compile"
    assert calls[0]["kwargs"]["headers"]["Authorization"] == "Bearer secret"
    assert calls[0]["kwargs"]["json"] == bundle.to_json()


def test_compile_remote_posts_design_payload_at_root(monkeypatch, tmp_path):
    builder = ViewSpecBuilder("remote_design")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="1")
    design_path = tmp_path / "DESIGN.md"
    design_path.write_text("name: Acme\ncolor.primary: #FFFFFF\n", encoding="utf-8")
    request = builder.attach_design(design_path).build_compile_request()
    ast = compile(request.bundle)
    calls, _ = _install_fake_httpx(monkeypatch, FakeResponse(200, {"ast": ast.to_json()}))

    restored = compile_remote(request)

    assert restored.title == ast.title
    posted = calls[0]["kwargs"]["json"]
    assert posted["substrate"] == request.bundle.to_json()["substrate"]
    assert posted["view_spec"] == request.bundle.to_json()["view_spec"]
    assert posted["design"] == {
        "format": "design.md",
        "content": "name: Acme\ncolor.primary: #FFFFFF\n",
        "lint": True,
    }


def test_compile_remote_response_exposes_design_metadata(monkeypatch):
    bundle = _bundle()
    ast = compile(bundle)
    payload = {
        "ast": ast.to_json(),
        "meta": {
            "compile_ms": 2.5,
            "design": {
                "name": "Acme",
                "lint_summary": {"errors": 1, "warnings": 2, "info": 3},
                "findings": [
                    {
                        "severity": "warning",
                        "code": "IGNORED_COLOR",
                        "path": "tokens.color.bad",
                        "message": "Only exact sRGB hex colors are accepted.",
                    }
                ],
                "applied_tokens": {"color.primary": "#FFFFFF"},
                "inferred_hints": {"tone": "neutral"},
                "ignored_tokens": ["color.bad"],
                "dropped_tokens": ["color.loop"],
            },
        },
    }
    _install_fake_httpx(monkeypatch, FakeResponse(200, payload))

    response = compile_remote_response(bundle)

    assert response.ast.title == ast.title
    assert response.meta.raw["compile_ms"] == 2.5
    assert response.meta.design is not None
    assert response.meta.design.name == "Acme"
    assert response.meta.design.lint_summary == {"errors": 1, "warnings": 2, "info": 3}
    assert response.meta.design.findings[0].code == "IGNORED_COLOR"
    assert response.meta.design.dropped_tokens == ["color.loop"]


def test_compile_auto_skips_local_when_design_is_attached(monkeypatch):
    bundle = _bundle()
    ast = compile(bundle)
    request = ViewSpecBuilder("auto_design").attach_design("name: Acme\n", is_path=False).build_compile_request()
    calls, _ = _install_fake_httpx(monkeypatch, FakeResponse(200, {"ast": ast.to_json()}))

    restored = compile_auto(request)

    assert restored.title == ast.title
    assert len(calls) == 1
    assert calls[0]["kwargs"]["json"]["design"]["content"] == "name: Acme\n"


def test_compile_remote_import_error_guides_remote_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "httpx", None)

    with pytest.raises(ImportError, match=r"viewspec\[remote\]"):
        compile_remote(_bundle())


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (FakeResponse(401, {"message": "nope"}), "Invalid API key"),
        (FakeResponse(429, {"message": "slow down"}), "Rate limit exceeded: slow down"),
        (FakeResponse(500, {"error": "server"}, text="server"), r"HTTP 500"),
        (FakeResponse(200, ValueError("bad json")), "not valid JSON"),
        (FakeResponse(200, []), "not an object"),
        (FakeResponse(200, {"ok": True}), "missing ast"),
        (FakeResponse(200, {"ast": {"not": "an ast"}}), "ast was invalid"),
    ],
)
def test_compile_remote_errors_are_stable(monkeypatch, response, message):
    _install_fake_httpx(monkeypatch, response)

    with pytest.raises(CompilerAPIError, match=message):
        compile_remote(_bundle())


def test_compile_remote_wraps_network_errors(monkeypatch):
    class HTTPError(Exception):
        pass

    def post(*args, **kwargs):
        raise HTTPError("connection refused")

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(post=post, HTTPError=HTTPError))

    with pytest.raises(CompilerAPIError, match="request failed"):
        compile_remote(_bundle())
