"""The rule every browser-bridge agent task shares, checked without a browser.

``tests/test_browser_bridge_e2e.py`` runs whole agent tasks in Chromium, and only when it is
switched on. Each task ends with ``unmet(run) == []``, so a rule that can never fail would pass
every task. These tests run in the default suite and pin the two ends of the rule: a run the tasks
depend on counts as completed, and a refusal the task did not expect does not.
"""

from __future__ import annotations

from typing import Any

from tests.test_browser_bridge_e2e import Run, Turn, unmet


def _turn(ok: bool, *, refusal_expected: bool = False, **result: Any) -> Turn:
    errors = [] if ok else [{"code": "BROWSER_TARGET_DRIFT", "message": "drift", "fix": "Observe again."}]
    return Turn(
        "act",
        {"action": "click", "handle": 4},
        {"ok": ok, "summary": "s", "errors": errors, **result},
        refusal_expected,
    )


def test_a_run_like_the_ones_the_tasks_make_counts_as_completed() -> None:
    # What the tasks do on the way: a click (the bridge cannot verify a click, so it says None), a
    # fill it verified, a wait that was met, and one request the task sent to be refused.
    steps = [{"step": {"op": "click"}, "verified": None}, {"step": {"op": "fill"}, "verified": True}]
    run = Run(
        "goal",
        ("S3cret-Pass-99",),
        [
            _turn(True, steps=steps, wait={"met": True}),
            _turn(False, refusal_expected=True),
            _turn(True, blocked_writes=[{"form_fields": {"new_password": "[redacted]"}}]),
        ],
    )

    assert unmet(run) == []


def test_a_refusal_the_task_did_not_expect_is_a_reason() -> None:
    run = Run("goal", (), [_turn(True), _turn(False)])

    assert unmet(run)
