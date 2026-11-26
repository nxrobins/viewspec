from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_request_lifecycle_readiness_contract():
    subprocess.run(["node", "--test", str(ROOT / "tests/browser_readiness.test.cjs")], check=True, timeout=15)


@pytest.mark.parametrize("kind", ["reaction", "navigation", "verified"])
def test_effect_evidence_never_promises_application_completion(monkeypatch, kind):
    monkeypatch.syspath_prepend(str(ROOT / "experiments/browser-agent-probe"))
    from action_outcome import action_evidence

    result = {"ok": True, "steps": [{"verified": kind == "verified"}], "navigated": kind == "navigation"}
    evidence = action_evidence(result)
    assert evidence["completion"] == {"scope": "application", "state": "unknown", "requested": False}


def test_wait_is_ui_evidence_and_blocked_transport_has_priority(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "experiments/browser-agent-probe"))
    from action_outcome import action_evidence

    result = {"wait": {"met": True, "condition": {"text": "Saved"}}}
    assert action_evidence(result)["completion"] == {
        "scope": "ui_condition",
        "state": "met",
        "requested": True,
        "condition": {"text": "Saved"},
    }
    result["blocked_writes"] = [{"state": "blocked-and-simulated"}]
    assert action_evidence(result)["completion"]["state"] == "blocked"


def test_new_stream_can_keep_form_completion_pending_even_without_finite_requests(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "experiments/browser-agent-probe"))
    from action_outcome import action_evidence

    result = {
        "errors": [{"code": "BROWSER_FORM_PENDING"}],
        "progress": {"submission": "withheld"},
        "steps": [{"step": {"op": "fill"}, "verified": True}],
        "readiness": {"pending": {"finite": 0, "unknown": 0, "streams": 1}},
    }
    evidence = action_evidence(result)
    assert evidence["execution"] == "partial"
    assert evidence["completion"]["state"] == "pending"
