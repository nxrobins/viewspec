from __future__ import annotations

import json

import pytest

from viewspec import cli
from viewspec.intent_tools import starter_intent_payload
from viewspec.review_cli import open_review, review_status
from viewspec.review_contract import ReviewContractError
from viewspec.review_runtime import ReviewRuntime


def _ready() -> dict[str, object]:
    return {
        "schema_version": 1,
        "ok": True,
        "summary": "ViewSpec review is ready.",
        "diagnostics": [],
        "external_refs": [],
        "paths": {},
        "errors": [],
        "next_actions": ["Open.", "Poll."],
        "metadata": {"sdk_version": "0.3.0b4", "network_calls": "loopback_only"},
        "review": {
            "review_id": "vrw_" + "0" * 32,
            "status": "active",
            "source_kind": "intent_bundle",
            "target": "html-tailwind",
            "revision": 1,
            "queued_events": 0,
            "url": "http://127.0.0.1:4388/open/token",
        },
    }


def test_review_cli_routes_all_bounded_options_and_prints_json(monkeypatch, capsys) -> None:
    called = {}

    def fake_open(source, **kwargs):
        called.update({"source": source, **kwargs})
        return _ready()

    monkeypatch.setattr(cli, "open_review", fake_open)
    exit_code = cli.main(
        [
            "review",
            "viewspec.intent.json",
            "--target",
            "html-tailwind",
            "--port",
            "4388",
            "--state-dir",
            ".review-state",
            "--no-open",
            "--json",
        ]
    )

    assert exit_code == 0
    assert called["source"] == "viewspec.intent.json"
    assert called["port"] == 4388
    assert called["no_open"] is True
    assert json.loads(capsys.readouterr().out)["review"]["revision"] == 1


def test_review_contract_errors_keep_stable_code_and_exit(monkeypatch, capsys) -> None:
    def fail(*args, **kwargs):
        del args, kwargs
        raise ReviewContractError(
            "REVIEW_REQUEST_INVALID",
            "Bad timeout.",
            "Use 1 through 55000.",
            cli_exit=2,
        )

    monkeypatch.setattr(cli, "poll_review", fail)
    assert cli.main(["review-poll", "viewspec.intent.json", "--timeout-ms", "0"]) == 2
    error = capsys.readouterr().err
    assert "REVIEW_REQUEST_INVALID" in error
    assert "Use 1 through 55000" in error


def test_review_status_without_source_lists_offline_retained_sessions_without_paths(tmp_path) -> None:
    state_root = tmp_path / "state"
    for name in ("first.intent.json", "second.intent.json"):
        source = tmp_path / name
        source.write_text(json.dumps(starter_intent_payload()), encoding="utf-8")
        ReviewRuntime.open(source, state_root=state_root)

    result = review_status(None, state_root=state_root)

    assert len(result["reviews"]) == 2
    assert all(review["status"] == "suspended" for review in result["reviews"])
    assert str(tmp_path) not in json.dumps(result)


def test_public_review_rejects_ephemeral_or_privileged_ports_before_startup() -> None:
    for port in (0, 1023, 65536):
        with pytest.raises(ReviewContractError) as raised:
            open_review("unused.intent.json", port=port, no_open=True)
        assert raised.value.code == "REVIEW_PORT_UNAVAILABLE"
