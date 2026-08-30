from __future__ import annotations

import json
from pathlib import Path
import re
import socket

import pytest

from viewspec import cli
from viewspec.app_starters import starter_app_bundle
from viewspec.intent_tools import starter_intent_payload
from viewspec.review_cli import end_review
from viewspec.review_contract import ReviewContractError
from viewspec.review_runtime import ReviewRuntime
from viewspec.review_compile import STUDIO_COMPARE_TARGET
from viewspec.review_server import ReviewServer
from viewspec.studio import open_studio, resolve_studio_source


def _ready_review() -> dict[str, object]:
    return {
        "schema_version": 1,
        "ok": True,
        "summary": "ViewSpec review is ready.",
        "diagnostics": [],
        "external_refs": [],
        "paths": {},
        "errors": [],
        "next_actions": [],
        "metadata": {"sdk_version": "0.3.0b7", "network_calls": "loopback_only"},
        "review": {
            "review_id": "vrw_" + "0" * 32,
            "status": "active",
            "source_kind": "intent_bundle",
            "target": "html-tailwind",
            "revision": 1,
            "check_status": "passed",
            "verification_status": "not_run",
            "url": "http://127.0.0.1:4388/open/token",
        },
    }


def _write_intent(path: Path) -> Path:
    path.write_text(json.dumps(starter_intent_payload(), sort_keys=True), encoding="utf-8")
    return path


def _write_app(path: Path) -> Path:
    path.write_text(json.dumps(starter_app_bundle(), sort_keys=True), encoding="utf-8")
    return path


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_studio_discovers_exactly_one_canonical_source(tmp_path):
    intent = _write_intent(tmp_path / "viewspec.intent.json")

    assert resolve_studio_source(None, cwd=tmp_path) == intent
    assert resolve_studio_source("custom.json", cwd=tmp_path) == Path("custom.json")

    (tmp_path / "viewspec.app.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ReviewContractError) as ambiguous:
        resolve_studio_source(None, cwd=tmp_path)
    assert ambiguous.value.code == "STUDIO_SOURCE_AMBIGUOUS"
    assert ambiguous.value.fix == "Name the interface you want to open: viewspec studio SOURCE."


def test_studio_missing_source_has_one_product_level_next_action(tmp_path):
    with pytest.raises(ReviewContractError) as missing:
        resolve_studio_source(None, cwd=tmp_path)

    assert missing.value.code == "STUDIO_SOURCE_NOT_FOUND"
    assert "Ask your agent to create one semantic source" in missing.value.fix


def test_studio_readiness_contract_hides_machinery_behind_one_loop(tmp_path, monkeypatch):
    source = _write_intent(tmp_path / "viewspec.intent.json")
    called: dict[str, object] = {}

    def fake_open_review(opened_source, **kwargs):
        called.update({"source": opened_source, **kwargs})
        return _ready_review()

    monkeypatch.setattr("viewspec.studio.open_review", fake_open_review)
    payload = open_studio(None, cwd=tmp_path, no_open=True)

    assert called["source"] == source
    assert called["no_open"] is True
    assert payload["summary"] == "ViewSpec Studio is ready."
    assert payload["studio"] == {
        "experience_version": 1,
        "status": "ready",
        "source_name": "viewspec.intent.json",
        "source_kind": "intent_bundle",
        "target": "html-tailwind",
        "revision": 1,
        "primary_loop": ["preview", "comment", "approve"],
        "primary_action": "comment",
        "ready_ms": payload["studio"]["ready_ms"],
        "confidence": {
            "state": "checked",
            "artifact_check": "passed",
            "viewport_verification": "not_run",
            "network": "loopback_only",
        },
    }
    assert isinstance(payload["studio"]["ready_ms"], int)
    assert payload["next_actions"][0].startswith("Preview")
    assert str(tmp_path) not in json.dumps(payload["studio"])


def test_studio_comparison_is_app_only_explicit_and_reports_honest_scope(tmp_path, monkeypatch):
    intent = _write_intent(tmp_path / "viewspec.intent.json")
    with pytest.raises(ReviewContractError) as missing_install:
        open_studio(intent, compare=True, no_open=True)
    assert missing_install.value.code == "STUDIO_COMPARISON_INSTALL_REQUIRED"

    with pytest.raises(ReviewContractError) as wrong_source:
        open_studio(intent, compare=True, install=True, no_open=True)
    assert wrong_source.value.code == "STUDIO_COMPARISON_REQUIRES_APP"

    intent.unlink()
    app = _write_app(tmp_path / "viewspec.app.json")
    called: dict[str, object] = {}
    ready = _ready_review()
    ready["review"].update(
        {
            "source_kind": "app_bundle",
            "target": STUDIO_COMPARE_TARGET,
            "inspection": {
                "coherence_status": "browser_observed",
                "coherence_contract": "semantic_geometry_v1",
                "state_status": "not_declared",
                "replay_count": 0,
                "resource_status": "not_declared",
                "resource_assertion_count": 0,
                "production_data": "not_claimed",
            },
        }
    )

    def fake_open_review(opened_source, **kwargs):
        called.update({"source": opened_source, **kwargs})
        return ready

    monkeypatch.setattr("viewspec.studio.open_review", fake_open_review)
    payload = open_studio(app, compare=True, install=True, no_open=True)

    assert called["target"] == STUDIO_COMPARE_TARGET
    assert called["install"] is True
    assert payload["studio"]["comparison"] == {
        "status": "ready",
        "targets": ["html-tailwind-app", "react-tailwind-app"],
        "synchronized": ["viewport", "route", "semantic_identity"],
        "visual_parity": "not_proven",
        "dependency_install": "explicit_opt_in",
        "inspection": ready["review"]["inspection"],
    }

    with pytest.raises(ReviewContractError) as conflicting:
        open_studio(app, compare=True, install=True, target="html-tailwind-app", no_open=True)
    assert conflicting.value.code == "STUDIO_COMPARISON_INVALID"


def test_studio_private_share_is_explicit_comparison_only_and_reports_network_scope(tmp_path, monkeypatch) -> None:
    app = _write_app(tmp_path / "viewspec.app.json")
    with pytest.raises(ReviewContractError) as missing_comparison:
        open_studio(app, share=True, no_open=True)
    assert missing_comparison.value.code == "STUDIO_SHARE_COMPARISON_REQUIRED"

    with pytest.raises(ReviewContractError) as unscoped_reference:
        open_studio(app, share_reference="reference.png", no_open=True)
    assert unscoped_reference.value.code == "STUDIO_SHARE_REFERENCE_INVALID"

    called: dict[str, object] = {}
    ready = _ready_review()
    ready["review"].update(
        {
            "source_kind": "app_bundle",
            "target": STUDIO_COMPARE_TARGET,
            "share": {
                "status": "available",
                "deployment_sha256": "a" * 64,
                "expires_at_epoch_s": 2_100_000_000,
            },
        }
    )

    def fake_open_review(opened_source, **kwargs):
        called.update({"source": opened_source, **kwargs})
        return ready

    monkeypatch.setattr("viewspec.studio.open_review", fake_open_review)
    payload = open_studio(
        app,
        compare=True,
        install=True,
        share=True,
        share_reference="reference.png",
        no_open=True,
    )
    assert called["studio_share"] is True
    assert called["studio_share_reference"] == "reference.png"
    assert payload["studio"]["confidence"]["network"] == "private_review_opt_in"
    assert payload["studio"]["private_review"] == ready["review"]["share"]
    assert any("exact disclosure" in action for action in payload["next_actions"])


def test_studio_cli_is_the_single_human_facing_entry_point(monkeypatch, capsys):
    monkeypatch.setattr(cli, "open_studio", lambda *args, **kwargs: {**_ready_review(), "studio": {
        "ready_ms": 412,
    }})

    assert cli.main(["studio", "viewspec.intent.json", "--no-open"]) == 0
    output = capsys.readouterr().out
    assert "ViewSpec Studio ready in 412 ms." in output
    assert "Preview → Comment → Approve" in output
    assert "compile" not in output.lower()
    assert "hash" not in output.lower()


def test_studio_chrome_prioritizes_product_loop_and_quiet_confidence(tmp_path):
    source = _write_intent(tmp_path / "viewspec.intent.json")
    runtime = ReviewRuntime.open(source, state_root=tmp_path / "state")
    server = ReviewServer(runtime, port=_available_port())
    try:
        chrome = server._chrome_response().body.decode("utf-8")  # noqa: SLF001
    finally:
        server.stop()

    assert "<title>ViewSpec Studio</title>" in chrome
    assert "Point. Ask. Approve." in chrome
    assert "Preview mode · interactions are live" in chrome
    assert "id=agent-presence class=agent-presence data-status='not_connected' aria-live=polite>Agent not connected" in chrome
    assert "<span class=revision>Requests: <strong id=queued>0</strong>" in chrome
    assert "Request saved locally · waiting for agent" in chrome
    assert "Request delivered · agent working" in chrome
    assert "queued++" not in chrome
    assert ">Details</button>" in chrome
    assert ">Comment</button>" in chrome
    assert "id=studio-panel class=panel aria-hidden=true inert" in chrome
    assert "panel.setAttribute('inert','')" in chrome
    assert "if(e.key==='Escape'&&panelOpen)" in chrome
    assert "history.scrollRestoration='manual'" in chrome
    assert "const lockCanvasOrigin=()=>" in chrome
    assert "canvas.addEventListener('scroll',()=>{if(canvasOriginLocked" in chrome
    assert "canvas.addEventListener('wheel',()=>{canvasOriginLocked=false;}" in chrome
    assert "Proof, requests, and decisions appear here when they matter." in chrome
    assert "Studio workflow" not in chrome
    assert "Desktop · 1440" in chrome
    assert "Tablet · 768" in chrome
    assert "Mobile · 390" in chrome
    assert "iframe{display:block;box-sizing:content-box" in chrome
    assert "data-studio-proof='not_run'" in chrome
    assert "Artifact checked" in chrome
    assert "Generated output remains immutable" in chrome
    iframe = re.search(r"<iframe id=artifact[^>]+>", chrome)
    assert iframe is not None
    assert "data-src='/frame/" in iframe.group(0)
    assert not re.search(r"(?<!data-)src=", iframe.group(0))
    assert chrome.index("addEventListener('message'") < chrome.rindex("frames.forEach(item=>{item.src=item.dataset.src")
    assert "approval_token" not in chrome
    assert "intent_approval_token" not in chrome


def test_clean_studio_journey_reaches_checked_ready_state_within_one_minute(tmp_path):
    source = _write_intent(tmp_path / "viewspec.intent.json")
    state_root = tmp_path / "studio-state"
    payload = open_studio(
        source,
        port=_available_port(),
        state_root=state_root,
        no_open=True,
    )
    try:
        assert payload["studio"]["ready_ms"] < 60_000
        assert payload["studio"]["confidence"] == {
            "state": "checked",
            "artifact_check": "passed",
            "viewport_verification": "not_run",
            "network": "loopback_only",
        }
        assert payload["review"]["status"] == "active"
        assert payload["review"]["revision"] == 1
    finally:
        end_review(source, state_root=state_root)
