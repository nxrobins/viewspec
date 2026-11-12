"""Messages in the agent lift, and diffs between two agent lifts of the same page."""

from __future__ import annotations

import json

import pytest

from viewspec.raw_html import lift_html
from viewspec.raw_html_diff import diff_lifts


def _lift(html: str):
    return lift_html(html, profile="agent")


def test_messages_come_from_alert_status_and_live_regions():
    lift = _lift(
        """
        <form><input name="e" aria-label="Email"><p role="alert">Email is <b>required</b></p></form>
        <div aria-live="polite"><span>Saved</span></div>
        <div role="status"></div>
        <div role="status" hidden>Old news</div>
        <ul role="log"><li>Step 1 done</li></ul>
        """
    )

    assert [(m.kind, m.text) for m in lift.messages] == [
        ("alert", "Email is required"),
        ("live", "Saved"),
        ("log", "Step 1 done"),
    ]
    assert not any(role.text in {"Saved", "Step 1 done"} for role in lift.roles)
    assert lift.to_json()["messages"][0] == {
        "node_id": lift.messages[0].node_id,
        "kind": "alert",
        "text": "Email is required",
    }
    assert "messages" not in lift_html("<p role='alert'>x</p>").to_json()


BEFORE = """
<h1>Cart</h1><p><span>$49.00</span></p>
<label>Qty <input name="q" value="1" data-lift-ref="e1"></label>
<button data-lift-ref="e2">Add</button><p role="status"></p>
<details><summary data-lift-ref="e3">Promo</summary>
  <div hidden><input name="promo" aria-label="Code" data-lift-ref="e4"></div></details>
<a href="/old" data-lift-ref="e5">Old deals</a>
"""
AFTER = """
<h1>Cart</h1><p><span>$98.00</span></p>
<label>Qty <input name="q" value="2" data-lift-ref="e1"></label>
<button data-lift-ref="e2" disabled>Added</button><p role="status">Arc lamp added</p>
<details open><summary data-lift-ref="e3">Promo</summary>
  <div><input name="promo" aria-label="Code" data-lift-ref="e4"></div></details>
"""


def test_diff_reports_state_controls_text_and_messages():
    diff = diff_lifts(_lift(BEFORE), _lift(AFTER))

    assert diff.lines() == [
        "message status 'Arc lamp added'",
        "changed text 'Qty': value '1' -> '2'",
        "changed button 'Added': name 'Add' -> 'Added'; disabled false -> true",
        "added text 'Code'",
        "removed link 'Old deals'",
        "text + value '$98.00'",
        "text - value '$49.00'",
    ]
    assert not diff.empty and diff.unchanged == 1  # The summary did not change.
    payload = diff.to_json()
    assert payload["changed"][1]["fields"]["disabled"] == {"before": False, "after": True}
    assert payload["added"][0]["ref"] == "e4"
    assert payload["messages"] == [{"kind": "status", "text": "Arc lamp added"}]


def test_lines_use_handles_of_the_new_view_and_stay_short():
    after = _lift(AFTER)
    handles = {control.node_id: index for index, control in enumerate(after.controls, start=1)}
    lines = diff_lifts(_lift(BEFORE), after).lines(handles.get)

    assert "changed [1] text 'Qty': value '1' -> '2'" in lines
    assert "added [4] text 'Code'" in lines
    assert diff_lifts(_lift(BEFORE), after).lines(limit=2)[-1] == "... 5 more"


def test_same_page_gives_an_empty_diff():
    diff = diff_lifts(_lift(BEFORE), _lift(BEFORE))
    assert diff.empty and diff.same_layout and diff.lines() == ["no change"]
    assert diff.unchanged == len(_lift(BEFORE).controls)


def test_refs_match_controls_that_swapped_places():
    before = _lift(
        '<ul><li><button data-lift-ref="e1">Add to cart</button></li>'
        '<li><button data-lift-ref="e2">Add to cart</button></li></ul>'
    )
    after = _lift(
        '<ul><li><button data-lift-ref="e2" disabled>Add to cart</button></li>'
        '<li><button data-lift-ref="e1">Add to cart</button></li></ul>'
    )
    [change] = diff_lifts(before, after).changed

    assert change.after.ref == "e2" and change.fields == {"disabled": (False, True)}


def test_without_refs_controls_match_by_unique_name_then_by_place():
    before = _lift('<button>Save</button><button>Go</button><button>Cancel</button><input aria-label="Note" value="a">')
    # A re-render with no refs: Save and Cancel swapped places, and Go, still second, became Next.
    after = _lift(
        '<button>Cancel</button><button>Next</button><button>Save</button><input aria-label="Note" value="b">'
    )
    diff = diff_lifts(before, after)

    by_name = {change.after.name: change.fields for change in diff.changed}
    assert by_name == {"Next": {"name": ("Go", "Next")}, "Note": {"value": ("a", "b")}}
    assert diff.added == () and diff.removed == () and diff.unchanged == 2

    # Renamed and moved: no pass can match it, so it is reported as removed and added.
    moved = diff_lifts(before, _lift("<button>Next</button><button>Save</button><button>Cancel</button>"))
    assert [c.name for c in moved.removed] == ["Go", "Note"] and [c.name for c in moved.added] == ["Next"]


def test_secret_values_never_appear_in_a_diff():
    before = _lift('<input name="api_token" value="first-secret-1" data-lift-ref="e1">')
    after = _lift('<input name="api_token" value="second-secret-2" data-lift-ref="e1">')
    diff = diff_lifts(before, after)
    text = json.dumps(diff.to_json()) + "\n".join(diff.lines())

    assert diff.empty
    assert "first-secret-1" not in text and "second-secret-2" not in text


def test_select_option_changes_are_reported():
    before = _lift(
        '<select aria-label="Size" data-lift-ref="e1"><option>S</option><option selected>M</option></select>'
    )
    after = _lift('<select aria-label="Size" data-lift-ref="e1"><option selected>S</option><option>M</option></select>')
    [change] = diff_lifts(before, after).changed

    assert change.fields["value"] == ("M", "S")
    assert change.fields["options"] == (["S", "M (selected)"], ["S (selected)", "M"])


def test_forms_that_appear_or_go_away_are_named():
    before = _lift('<form aria-label="Search"><input name="q"></form>')
    after = _lift('<form aria-label="Sign in"><input name="user"></form>')
    diff = diff_lifts(before, after)

    assert diff.forms_added == ("sign_in",) and diff.forms_removed == ("search",)


def test_a_message_shown_again_is_not_new():
    before = _lift('<p role="alert">Required</p>')
    assert diff_lifts(before, _lift('<p role="alert">Required</p>')).messages == ()
    twice = _lift('<p role="alert">Required</p><p role="alert">Required</p>')
    assert diff_lifts(before, twice).messages == (("alert", "Required"),)


def test_diff_needs_agent_lifts():
    with pytest.raises(ValueError, match="profile='agent'"):
        diff_lifts(lift_html("<p>x</p>"), _lift("<p>x</p>"))


# ---- Waiting for a change --------------------------------------------------------------------------

from viewspec.raw_html_actions import AgentActionError  # noqa: E402
from viewspec.raw_html_diff import ChangeCondition  # noqa: E402

WAIT_BEFORE = '<button>Apply</button><p role="status"></p><p>Loading…</p><input aria-label="Code" value="SAVE10">'
WAIT_AFTER = (
    '<button disabled>Checking</button><p role="status">Code SAVE10 applied</p><input aria-label="Code" value="SAVE10">'
)


@pytest.mark.parametrize(
    ("payload", "met"),
    [
        ({"change": "any"}, True),
        ({"change": "message"}, True),
        ({"change": "navigation"}, False),  # Lifts cannot show a new document; the caller decides.
        ({"text": "code save10 APPLIED"}, True),
        ({"text": "Order placed"}, False),
        ({"gone": "Loading"}, True),
        ({"gone": "Checking"}, False),
        ({"control": "checking"}, True),
        ({"control": "Checking", "enabled": False}, True),
        ({"control": "Checking", "enabled": True}, False),
        ({"control": "Apply"}, False),
    ],
)
def test_change_conditions_are_checked_on_the_lift(payload, met):
    condition = ChangeCondition.from_json(payload)
    assert condition.met(_lift(WAIT_BEFORE), _lift(WAIT_AFTER)) is met
    assert condition.to_json() == payload


def test_no_change_meets_only_the_conditions_already_true():
    same = _lift(WAIT_BEFORE)
    assert not ChangeCondition.from_json({"change": "any"}).met(same, same)
    assert not ChangeCondition.from_json({"change": "message"}).met(same, same)
    assert ChangeCondition.from_json({"text": "Loading"}).met(same, same)


def test_text_conditions_search_all_visible_text_across_tags():
    page = _lift("<p>Email is <b>required</b></p><div hidden>Secret plan</div><p>Loading…</p>")

    assert ChangeCondition.from_json({"text": "email is   REQUIRED"}).met(page, page)
    assert not ChangeCondition.from_json({"gone": "Loading"}).met(page, page)  # No role, but visible.
    assert ChangeCondition.from_json({"gone": "Secret plan"}).met(page, page)  # Hidden text is gone.


def test_secret_values_are_not_page_text():
    before = _lift('<input type="password" aria-label="Password" value="hunter2-long">')
    assert not ChangeCondition.from_json({"text": "hunter2"}).met(before, before)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"text": "a", "gone": "b"},
        {"change": "soon"},
        {"text": "  "},
        {"text": 5},
        {"enabled": True},
        {"text": "a", "enabled": True},
        {"control": "a", "enabled": "yes"},
        {"control": "a", "timeout": 5},
    ],
)
def test_bad_wait_conditions_are_refused(payload):
    with pytest.raises(AgentActionError) as caught:
        ChangeCondition.from_json(payload)
    assert caught.value.code == "AGENT_WAIT_INVALID"


def test_conditions_describe_themselves():
    assert [
        ChangeCondition.from_json(p).describe()
        for p in (
            {"change": "any"},
            {"change": "message"},
            {"change": "navigation"},
            {"text": "Saved"},
            {"gone": "Loading"},
            {"control": "Pay", "enabled": True},
        )
    ] == [
        "any change",
        "a new message",
        "a new page",
        "'Saved' on the page",
        "'Loading' gone from the page",
        "control 'Pay' enabled",
    ]


def test_hidden_ids_cover_every_way_to_hide():
    from viewspec.raw_html_agent import hidden_ids

    lift = _lift(
        '<div hidden><p>a</p></div><div aria-hidden="true"><p>b</p></div>'
        '<div data-lift-hidden="true"><p>c</p></div><p>d</p>'
    )
    hidden_text = {node.text for node in lift.nodes if node.id in hidden_ids(lift.nodes)}
    assert hidden_text >= {"a", "b", "c"} and "d" not in hidden_text


def test_controls_that_match_by_ref_pair_in_order_when_many_share_a_name():
    def page(label: str):
        items = "".join(f'<button data-lift-ref="e{index}">{label}</button>' for index in range(300))
        return _lift(f"<div>{items}</div>")

    diff = diff_lifts(page("Buy"), page("Add"))
    assert len(diff.changed) == 300 and not diff.added and not diff.removed
    assert [change.after.ref for change in diff.changed] == [f"e{index}" for index in range(300)]
