"""The browser bridge's rule for what an action did, checked without a browser.

After ``act`` or ``call`` an agent must decide whether to go on, wait, or try again. The bridge's
``action_outcome`` module reads the result and says ``succeeded``, ``failed``, or ``uncertain``, with
a reason and whether a retry could do the thing twice. It also finds what two readings of a page
cannot show: a message the page showed again, and one that came and went in between.

Whether the browser reports the facts needs a browser, and ``tests/test_browser_bridge_e2e.py``
checks that when it is switched on. These tests run in the default suite and pin the rule itself.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from types import ModuleType
from typing import Any

import pytest

from tests.test_browser_bridge_e2e import PROBE


def _rule() -> ModuleType:
    """The experiment's ``action_outcome`` module, loaded by path: the experiment is not a package."""

    name = "browser_bridge_action_outcome"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, PROBE / "action_outcome.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _step(op: str = "click", name: str = "Save", verified: bool | None = None, **extra: Any) -> dict[str, Any]:
    return {"step": {"op": op, "name": name}, "verified": verified, "navigated": False, **extra}


def _result(*steps: dict[str, Any], lines: tuple[str, ...] = (), **extra: Any) -> dict[str, Any]:
    changes = {"new_document": False, "empty": not lines, "lines": list(lines) or ["no change"]}
    return {"ok": True, "summary": "s", "steps": list(steps), "navigated": False, "changes": changes, **extra}


@pytest.mark.parametrize(
    "states,expected",
    [
        (["matched", "mismatch"], "mismatch"),
        (["matched", "unknown"], "unknown"),
        (["matched", "not_run"], "unknown"),
        (["matched", "matched"], "matched"),
    ],
)
def test_final_field_contract_takes_precedence_over_individual_step_checks(states, expected):
    result = _result(
        _step("fill", verified=True),
        progress={"contract": [{"index": index, "status": state} for index, state in enumerate(states, 1)]},
    )
    assert _rule().action_evidence(result)["control_verification"] == expected


def test_noop_preparation_still_has_verified_field_evidence():
    result = _result(progress={"contract": [{"index": 1, "status": "matched"}]})
    evidence = _rule().action_evidence(result)
    assert evidence["execution"] == "not_dispatched"
    assert evidence["control_verification"] == "matched"
    assert evidence["completion"]["state"] == "unknown"


def _refusal(code: str, message: str = "why", *steps: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": False, "summary": message, "errors": [{"code": code, "message": message}]}
    if steps:
        result["steps"] = list(steps)
    return result


QUIET = {"mutations": 0, "requests": 0, "pending": 0, "watched_ms": 70}
BUSY = {"mutations": 0, "requests": 1, "pending": 1, "watched_ms": 70}
REACTED = {"mutations": 4, "requests": 1, "pending": 0, "watched_ms": 70}
CONFIRM = {"type": "confirm", "message": "Delete this draft?", "answer": "dismissed"}
MET = {"for": "a new message", "met": True, "waited_ms": 40, "timeout_ms": 5000}
UNMET = {"for": "'Order placed' on the page", "met": False, "waited_ms": 5000, "timeout_ms": 5000}


def _outcome(result: dict[str, Any], activity: dict[str, Any] | None = None) -> tuple[str, str, str | None]:
    outcome = _rule().outcome_of(result, activity)
    assert set(outcome) <= {"status", "reason", "retry", "page"}
    assert outcome["reason"] and "\n" not in outcome["reason"]
    return outcome["status"], outcome["retry"], outcome.get("page")


# ---- failed: it did not take effect ------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    ["AGENT_FORM_ARGUMENTS_INVALID", "BROWSER_HANDLE_UNKNOWN", "BROWSER_TARGET_BLOCKED", "BROWSER_OBSERVATION_STALE"],
)
def test_a_request_refused_before_it_ran_failed_and_is_safe_to_repeat(code: str) -> None:
    assert _outcome(_refusal(code)) == ("failed", "safe", None)
    assert "why" in _rule().outcome_of(_refusal(code, "why it was refused"))["reason"]


def test_a_refusal_after_a_click_ran_may_repeat_the_click() -> None:
    # A form call: the fields were filled, a button was clicked, and then a step was refused.
    ran = (_step("fill", "Email", True), _step("click", "Add a line"))

    assert _outcome(_refusal("BROWSER_TARGET_DRIFT", "drift", ran[0])) == ("failed", "safe", None)
    assert _outcome(_refusal("BROWSER_TARGET_DRIFT", "drift", *ran)) == ("failed", "may_repeat", None)


def test_a_step_that_ran_and_did_not_do_it_failed() -> None:
    # The list was opened and closed again: the page is as it was.
    assert _outcome(_refusal("BROWSER_OPTION_NOT_FOUND")) == ("failed", "safe", None)
    # The control was clicked, and no file came. The click may have done something else.
    assert _outcome(_refusal("BROWSER_DOWNLOAD_NONE")) == ("failed", "may_repeat", None)


@pytest.mark.parametrize("code", ["BROWSER_TIMEOUT", "BROWSER_PLAN_INTERRUPTED", "BROWSER_ERROR", "A_CODE_ADDED_LATER"])
def test_a_step_that_stopped_part_way_is_uncertain(code: str) -> None:
    assert _outcome(_refusal(code)) == ("uncertain", "may_repeat", None)


def test_a_form_the_browser_would_not_send_failed() -> None:
    refused = _step("click", "Place order", invalid=[{"name": "Email", "message": "Please include an '@'."}])
    outcome = _rule().outcome_of(_result(refused, wait=MET))

    # Even when what the agent waited for was on the page all along.
    assert (outcome["status"], outcome["retry"]) == ("failed", "safe")
    assert "Email: Please include an '@'." in outcome["reason"]


def test_a_control_that_does_not_hold_what_was_set_failed() -> None:
    assert _outcome(_result(_step("fill", "Email", False))) == ("failed", "safe", None)
    # A choice from a custom list is a click on an option: doing it again may undo it.
    assert _outcome(_result(_step("choose", "Country", False))) == ("failed", "may_repeat", None)


def test_a_navigation_the_server_stopped_failed() -> None:
    left = _result(_step("click", "Partner site"), blocked_navigations=[{"url": "https://other.example/"}])
    outcome = _rule().outcome_of(left)

    assert (outcome["status"], outcome["retry"]) == ("failed", "safe")
    assert "https://other.example/" in outcome["reason"]


def test_a_dismissed_question_cannot_prove_the_action_was_canceled() -> None:
    outcome = _rule().outcome_of(_result(_step("click", "Delete draft", dialogs=[CONFIRM])), QUIET)

    assert (outcome["status"], outcome["retry"], outcome["page"]) == ("uncertain", "may_repeat", "quiet")
    assert "'Delete this draft?' (confirm)" in outcome["reason"]
    # A box raised while the bridge waited is reported beside the steps, and counts the same.
    late = _result(_step("click", "Delete draft"), dialogs=[{**CONFIRM, "type": "prompt"}])
    assert _outcome(late, QUIET) == ("uncertain", "may_repeat", "quiet")


@pytest.mark.parametrize("kind", ["confirm", "prompt"])
def test_a_dismissed_receipt_question_does_not_override_a_completed_order(kind: str) -> None:
    dialog = {"type": kind, "message": "Also email a receipt?", "answer": "dismissed"}
    placed = _result(_step("click", "Place order", dialogs=[dialog]), lines=("message status 'Order placed'",))

    assert _outcome(placed, REACTED) == ("succeeded", "not_needed", None)
    assert "Order placed" in _rule().outcome_of(placed, REACTED)["reason"]


def test_a_dismissed_question_with_no_visible_answer_may_follow_an_order() -> None:
    placed = _result(_step("click", "Place order", dialogs=[CONFIRM]))

    # A request may have completed without leaving anything in the view, or still be in flight.
    assert _outcome(placed, REACTED) == ("uncertain", "may_repeat", "reacted")
    assert _outcome(placed, BUSY) == ("uncertain", "may_repeat", "busy")
    # A verified field value does not rule out side effects in its input/change handler either.
    typed = _result(_step("fill", "Email", True, dialogs=[CONFIRM]))
    assert _outcome(typed, QUIET) == ("uncertain", "may_repeat", "quiet")
    assert _outcome({**typed, "wait": UNMET}, BUSY) == ("uncertain", "may_repeat", "busy")


@pytest.mark.parametrize(
    "extra",
    [
        {"invalid": [{"name": "Email", "message": "Required"}]},
        {"verified": False},
    ],
)
def test_a_refused_field_after_a_dismissed_question_does_not_make_retry_safe(extra: dict[str, Any]) -> None:
    result = _result(_step("fill", "Email", dialogs=[CONFIRM], **extra))

    assert _outcome(result)[1] == "may_repeat"


def test_a_stopped_navigation_after_a_dismissed_question_does_not_make_retry_safe() -> None:
    result = _result(_step(dialogs=[CONFIRM]), blocked_navigations=[{"url": "https://other.example/"}])

    assert _outcome(result)[1] == "may_repeat"


# ---- succeeded: the bridge saw the effect ------------------------------------------------------------


def test_what_the_agent_waited_for_decides() -> None:
    assert _outcome(_result(_step(), wait=MET), QUIET) == ("succeeded", "not_needed", None)
    # The page asked, got no, and showed what the agent waited for all the same.
    assert _outcome(_result(_step(dialogs=[CONFIRM]), wait=MET))[0] == "succeeded"


@pytest.mark.parametrize("state", ["blocked", "blocked-and-simulated"])
def test_a_blocked_write_cannot_be_confirmed_by_its_own_stub(state: str) -> None:
    result = _result(
        _step("submit", "order"),
        navigated=True,
        url="https://shop.example/save",
        wait=MET,
        lines=("message status 'Saved'",),
        blocked_writes=[{"method": "POST", "url": "https://shop.example/save", "state": state}],
    )

    outcome = _rule().outcome_of(result)
    assert outcome["status"] == "blocked"
    assert outcome["retry"] == "may_repeat"
    assert "not sent" in outcome["reason"]


def test_each_kind_of_answer_counts() -> None:
    answers = [
        _result(_step(), navigated=True, url="http://shop.example/done"),
        _result(_step(), changes={"new_document": True, "empty": False, "lines": ["new page 'Done' /done"]}),
        _result(_step("fill", "Email", True), _step("check", "Terms", True)),
        _result(_step(), lines=("message status 'Saved' (shown again)",)),
        _result(_step(), lines=("reordered 3x <tr>: 'Ana 36', 'Bo 41', 'Cy 29'",)),
        _result(_step("download", "Invoice", download={"file": "invoice.pdf"})),
        _result(_step(opened=["http://shop.example/help"])),
        _result(_step(unsaved_downloads=["export.csv"])),
        _result(_step(dialogs=[{"type": "alert", "message": "Saved", "answer": "dismissed"}])),
        _result(),  # Nothing to do: the control was already in that state.
    ]

    assert [_outcome(answer, QUIET) for answer in answers] == [("succeeded", "not_needed", None)] * len(answers)
    assert "'Ana 36'" in _rule().outcome_of(answers[4])["reason"]


# ---- uncertain: it ran, and nothing shows whether it worked --------------------------------------------


def test_a_click_with_no_answer_is_uncertain_and_says_what_the_page_did() -> None:
    click = _result(_step("click", "Publish"))

    assert _outcome(click, BUSY) == ("uncertain", "may_repeat", "busy")
    assert _outcome(click, REACTED) == ("uncertain", "may_repeat", "reacted")
    assert _outcome(click, QUIET) == ("uncertain", "may_repeat", "quiet")
    assert _outcome(click) == ("uncertain", "may_repeat", None)  # The page's activity is not known.
    assert "1 request it started is still in flight" in _rule().outcome_of(click, BUSY)["reason"]
    assert "4 DOM changes, 1 request" in _rule().outcome_of(click, REACTED)["reason"]
    assert "Nothing on the page reacted in the 70 ms" in _rule().outcome_of(click, QUIET)["reason"]


def test_one_verified_step_does_not_vouch_for_the_submit_after_it() -> None:
    # The form was filled in, which the bridge checked, and submitted, which it cannot check.
    call = _result(_step("fill", "Email", True), _step("submit", "checkout"))

    assert _outcome(call, QUIET) == ("uncertain", "may_repeat", "quiet")
    # A step that only sets a state cannot do it twice, whatever came of it.
    assert _outcome(_result(_step("select", "Country")), QUIET) == ("uncertain", "safe", "quiet")


def test_a_field_that_holds_what_a_step_set_is_not_an_answer_from_the_page() -> None:
    # A form call: two fields filled in and checked, then a submit that showed nothing. The changes
    # list the fields' new values, which is what the steps did, not what the submit did.
    filled = (
        "changed [3] text 'Email': value '' -> 'bo@example.com'",
        "changed [5] select 'Country': value 'Peru' -> 'Japan'; options [Peru (selected), Japan] -> [Peru, Japan (selected)]",
    )
    steps = (_step("fill", "Email", True), _step("select", "Country", True), _step("submit", "checkout"))

    assert _outcome(_result(*steps, lines=filled), QUIET) == ("uncertain", "may_repeat", "quiet")
    # A request that went out with the submit is an answer, and the reason names it, not a field.
    sent = _result(*steps, lines=filled, blocked_writes=[{"method": "POST", "url": "http://shop.example/orders"}])
    assert _outcome(sent, QUIET)[:2] == ("blocked", "may_repeat")
    assert "POST http://shop.example/orders" in _rule().outcome_of(sent, QUIET)["reason"]
    # The page did more to a filled field than hold the value: that is the page's doing.
    more = _result(*steps, lines=("changed [3] text 'Email': value '' -> 'bo@example.com'; disabled false -> true",))
    assert _outcome(more, QUIET)[0] == "succeeded"
    # Another field changed, or a field a step set and was not checked on.
    other = _result(*steps, lines=(*filled, "changed [9] text 'Total': value '10' -> '12'"))
    assert "'Total'" in _rule().outcome_of(other, QUIET)["reason"]
    unchecked = _result(_step("fill", "Email"), _step("submit", "checkout"), lines=filled[:1])
    assert _outcome(unchecked, QUIET)[0] == "succeeded"


def test_a_wait_that_was_not_met_is_uncertain_whatever_else_the_page_did() -> None:
    silent = _result(_step("click", "Place order"), wait=UNMET)
    other = _result(_step("click", "Place order"), wait=UNMET, lines=("message alert 'Card declined'",))

    assert _outcome(silent, BUSY) == ("uncertain", "may_repeat", "busy")
    assert _outcome(silent, QUIET) == ("uncertain", "may_repeat", "quiet")
    assert _outcome(other, REACTED) == ("uncertain", "may_repeat", "reacted")
    assert "message alert 'Card declined'" in _rule().outcome_of(other, REACTED)["reason"]
    assert "did not show in 5000 ms" in _rule().outcome_of(silent, QUIET)["reason"]


def test_a_long_message_from_the_page_does_not_make_a_long_reason() -> None:
    outcome = _rule().outcome_of(_result(_step(), lines=("message alert " + "x" * 2000,)))

    assert len(outcome["reason"]) <= 300


# ---- scrolling -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scroll", "status"),
    [
        ({"scrolls": 3, "moved": True, "loaded": True, "met": True}, "succeeded"),
        ({"scrolls": 1, "moved": True, "loaded": False}, "succeeded"),
        ({"scrolls": 1, "moved": False, "loaded": False}, "failed"),
        ({"scrolls": 2, "moved": True, "loaded": True, "met": False, "end_reached": True}, "uncertain"),
        ({"scrolls": 9, "moved": True, "loaded": True, "met": False, "end_reached": False}, "uncertain"),
    ],
)
def test_a_scroll_has_an_outcome_and_is_always_safe_to_repeat(scroll: dict[str, Any], status: str) -> None:
    outcome = _rule().scroll_outcome(scroll)

    assert outcome["status"] == status and outcome["retry"] in {"not_needed", "safe"}


# ---- what two readings cannot show -----------------------------------------------------------------------


def _note(seq: int, ref: str, text: str, kind: str = "status", **extra: Any) -> dict[str, Any]:
    return {"seq": seq, "ref": ref, "kind": kind, "text": text, **extra}


def _noted(notes: list[dict[str, Any]], shown: dict[str, tuple[str, str]], **kwargs: Any) -> list[str]:
    rule = _rule()
    return rule.message_lines(rule.noted_messages(notes, since=kwargs.pop("since", 0), shown=shown, **kwargs))


def test_a_message_written_again_is_shown_again() -> None:
    # The status line said "Saved" at the earlier reading, and the page wrote "Saved" into it again.
    assert _noted([_note(4, "e9", "Saved")], {"e9": ("status", "Saved")}) == ["message status 'Saved' (shown again)"]


def test_a_message_the_diff_reports_is_not_reported_twice() -> None:
    notes = [_note(4, "e9", "Saved")]

    assert _noted(notes, {"e9": ("status", "Saved")}, new=[("status", "Saved")]) == []
    # Two regions say the same words, and only one of them is new to the diff.
    both = [_note(4, "e9", "Saved"), _note(5, "e12", "Saved")]
    shown = {"e9": ("status", "Saved"), "e12": ("status", "Saved")}
    assert _noted(both, shown, new=[("status", "Saved")]) == ["message status 'Saved' (shown again)"]


def test_a_message_that_came_and_went_is_reported_as_gone() -> None:
    # A toast: added with its text, and removed before the later reading.
    assert _noted([_note(4, "e30", "Link copied")], {}) == ["message status 'Link copied' (shown, now gone)"]
    # A status line that said "Saving…" on the way to "Saved".
    steps = [_note(4, "e9", "Saving…"), _note(5, "e9", "Saved")]
    assert _noted(steps, {"e9": ("status", "Saved")}, new=[("status", "Saved")]) == [
        "message status 'Saving…' (shown, now gone)"
    ]


def test_notes_from_before_the_earlier_reading_are_left_out() -> None:
    notes = [_note(3, "e30", "Link copied"), _note(4, "e9", "Saved")]

    assert _noted(notes, {"e9": ("status", "Saved")}, since=3) == ["message status 'Saved' (shown again)"]
    assert _noted(notes, {"e9": ("status", "Saved")}, since=4) == []


def test_an_earlier_line_of_a_log_is_not_gone() -> None:
    log = [_note(4, "e5", "Step 1 done", "log"), _note(5, "e5", "Step 1 done Step 2 done", "log")]
    shown = {"e5": ("log", "Step 1 done Step 2 done")}

    assert _noted(log, shown, new=[("log", "Step 1 done Step 2 done")]) == []


def test_what_a_widget_said_while_the_bridge_drove_it_is_not_an_answer() -> None:
    # A calendar's month title, shown and hidden again while the bridge paged through it.
    paging = [_note(4, "e40", "December 2026", "live", aside=True), _note(5, "e40", "January 2027", "live", aside=True)]

    assert _noted(paging, {}) == []
    # A message such a step leaves on the page is reported like any other.
    assert _noted(paging, {"e40": ("live", "January 2027")}) == ["message live 'January 2027' (shown again)"]


def test_words_that_another_region_shows_now_are_not_gone_and_repeats_are_named_once() -> None:
    # The page drew its status line again in a new element, a moment after it took the old one out.
    assert _noted([_note(4, "e9", "Cart: empty")], {"e77": ("status", "Cart: empty")}) == []
    twice = [_note(4, "e30", "Link copied"), _note(5, "e31", "Link copied")]
    assert _noted(twice, {}) == ["message status 'Link copied' (shown, now gone)"]


def test_a_new_address_in_the_same_document_is_a_line() -> None:
    rule = _rule()
    page = "http://127.0.0.1:8000/outcomes.html"

    assert rule.address_lines(page, page, "Team", "Team") == []
    assert rule.address_lines(page, page + "#notes") == ["address /outcomes.html -> /outcomes.html#notes"]
    assert rule.address_lines("http://a.example/x?q=1", "http://a.example/", "Old", "New") == [
        "address /x?q=1 -> /",
        "title 'Old' -> 'New'",
    ]
    assert rule.address_lines("http://a.example/x", "http://b.example/x") == [
        "address http://a.example/x -> http://b.example/x"
    ]


def test_the_rule_needs_only_the_standard_library() -> None:
    # It is loaded by path above, with nothing of the experiment or of Playwright on the path.
    tree = ast.parse((PROBE / "action_outcome.py").read_text(encoding="utf-8"))
    imported = {
        (node.module or "").split(".")[0] if isinstance(node, ast.ImportFrom) else alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }

    assert imported <= set(sys.stdlib_module_names)


def test_a_permitted_write_before_a_blocked_navigation_cannot_be_safe_to_repeat() -> None:
    result = _result(
        _step("click"),
        passed_writes=[{"method": "POST", "state": "passed"}],
        blocked_navigations=[{"url": "https://example.org/receipt"}],
    )
    assert _rule().outcome_of(result)["retry"] == "may_repeat"
