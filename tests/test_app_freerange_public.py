from __future__ import annotations

import json

import viewspec.app_bundle as app_bundle
from viewspec.app_tools import prove_app_tool


def test_public_prove_app_forwards_freerange_without_changing_default(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_pipeline(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "errors": []}

    monkeypatch.setattr(app_bundle._app_pipeline, "prove_app", fake_pipeline)

    assert app_bundle.prove_app(app_path="app.json")["ok"] is True
    assert app_bundle.prove_app(app_path="app.json", freerange=True)["ok"] is True

    assert calls[0]["freerange"] is False
    assert calls[1]["freerange"] is True


def test_prove_app_tool_forwards_and_surfaces_static_analysis(tmp_path) -> None:
    app_path = tmp_path / "viewspec.app.json"
    app_path.write_text(json.dumps({}), encoding="utf-8")
    captured: dict[str, object] = {}
    static_analysis = {
        "status": "passed",
        "engine": {"name": "freerange", "version": "0.0.1"},
        "coverage": {"required": 1, "fully_analyzed": 1, "partial": 0, "unsupported": 0},
        "audit_transcript_hash": "a" * 64,
    }

    def fake_prove(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "target": "react-tailwind-app",
            "proof_level": "react_app_reference_host",
            "policy": {"network_calls": "none"},
            "paths": {},
            "route_assertions": {},
            "screens": [],
            "app": {},
            "static_analysis": static_analysis,
            "errors": [],
        }

    result = prove_app_tool(
        app_path=app_path,
        out_dir=tmp_path / "proof",
        target="react-tailwind-app",
        freerange=True,
        cwd=tmp_path,
        _prove_app=fake_prove,
    )

    assert result["ok"] is True
    assert captured["freerange"] is True
    assert result["metadata"]["freerange_requested"] is True
    assert result["metadata"]["static_analysis"] == static_analysis
