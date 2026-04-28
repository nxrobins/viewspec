from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from viewspec import CompilerAPIError, ViewSpecBuilder, compile, compile_remote


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

    assert restored.title == ast.title
    assert calls[0]["args"][0] == "https://api.viewspec.dev/v1/compile"
    assert calls[0]["kwargs"]["headers"]["Authorization"] == "Bearer secret"
    assert calls[0]["kwargs"]["json"] == bundle.to_json()


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
