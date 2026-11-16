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


# ---- The same items in another order -----------------------------------------------------------------


def _table(*rows: str, refs: bool = True) -> str:
    """A table of people, one "Name Age" row each. With ``refs``, a person's row keeps its snapshot
    ref wherever it stands, as a row does when a sort moves it."""

    ref = {"Bo": "e11", "Ana": "e12", "Cy": "e13", "Di": "e14"}
    cells = ""
    for row in rows:
        name, age = row.split()
        attr = f' data-lift-ref="{ref[name]}"' if refs else ""
        cells += f"<tr{attr}><td>{name}</td><td>{age}</td></tr>"
    return f'<h1>Team</h1><table><tbody data-lift-ref="e10">{cells}</tbody></table>'


def test_a_sort_that_only_moves_rows_is_reported():
    before = _lift(_table("Bo 41", "Ana 36", "Cy 29"))
    after = _lift(_table("Ana 36", "Bo 41", "Cy 29"))
    diff = diff_lifts(before, after)

    assert diff.lines() == ["reordered 3x <tr>: 'Ana 36', 'Bo 41', 'Cy 29'"]
    assert not diff.empty and not diff.text_added and not diff.text_removed
    [reorder] = diff.to_json()["reordered"]
    assert reorder["tag"] == "tr" and reorder["before"] == ["Bo 41", "Ana 36", "Cy 29"]
    assert ChangeCondition.from_json({"change": "any"}).met(before, after)
    assert not ChangeCondition.from_json({"change": "message"}).met(before, after)
    assert diff_lifts(after, after).reordered == ()


def test_a_list_the_page_built_again_is_compared_by_what_its_items_say():
    # No refs at all, as in a list rebuilt from new elements: the parent is found by its place.
    before = _lift(_table("Bo 41", "Ana 36", "Cy 29", refs=False))
    after = _lift(_table("Cy 29", "Ana 36", "Bo 41", refs=False))

    assert diff_lifts(before, after).lines() == ["reordered 3x <tr>: 'Cy 29', 'Ana 36', 'Bo 41'"]


def test_an_item_added_removed_or_rewritten_is_not_a_reorder():
    before = _lift(_table("Bo 41", "Ana 36", "Cy 29"))

    added = diff_lifts(before, _lift(_table("Di 52", "Bo 41", "Ana 36", "Cy 29")))
    removed = diff_lifts(before, _lift(_table("Bo 41", "Cy 29")))
    rewritten = diff_lifts(before, _lift(_table("Bo 42", "Ana 36", "Cy 29")))
    assert added.reordered == () and removed.reordered == () and rewritten.reordered == ()
    assert not added.empty and not removed.empty and not rewritten.empty  # The text diff has them.

    # Added and moved at once: the rows both lifts show are compared, the new one is left out.
    both = diff_lifts(before, _lift(_table("Di 52", "Cy 29", "Bo 41", "Ana 36")))
    assert [(r.items, r.was) for r in both.reordered] == [(("Cy 29", "Bo 41", "Ana 36"), ("Bo 41", "Ana 36", "Cy 29"))]


def test_copies_of_one_text_are_compared_only_when_none_came_or_went():
    def listing(*items: str):
        return _lift("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")

    # Two rows read the same. With both still there, the row between them can be seen to move.
    moved = diff_lifts(listing("Ana", "Sold out", "Sold out"), listing("Sold out", "Sold out", "Ana"))
    assert moved.lines() == ["reordered 3x <li>: 'Sold out', 'Sold out', 'Ana'"]
    # One copy went away. Whether the first or the second, nothing says, so it is not called a move.
    fewer = diff_lifts(listing("Sold out", "Ana", "Sold out"), listing("Ana", "Sold out"))
    assert fewer.reordered == ()


def test_only_the_outermost_reorder_is_reported():
    def board(*columns: tuple[str, str, str]) -> str:
        cards = "".join(
            f"<section><h2>{name}</h2><ul><li>{a}</li><li>{b}</li></ul></section>" for name, a, b in columns
        )
        return f"<main>{cards}</main>"

    before = _lift(board(("Todo", "Write", "Test"), ("Done", "Plan", "Ship")))
    # The two columns swap places, so each list now sits where the other was.
    after = _lift(board(("Done", "Plan", "Ship"), ("Todo", "Write", "Test")))

    assert diff_lifts(before, after).lines() == ["reordered 2x <section>: 'Done Plan Ship', 'Todo Write Test'"]


def test_hidden_items_and_options_are_not_reordered_items():
    before = _lift(
        "<ul><li>Ana</li><li hidden>Zed</li><li>Bo</li></ul>"
        '<select aria-label="Size" data-lift-ref="e1"><option>S</option><option>M</option></select>'
    )
    after = _lift(
        "<ul><li hidden>Zed</li><li>Ana</li><li>Bo</li></ul>"
        '<select aria-label="Size" data-lift-ref="e1"><option>M</option><option>S</option></select>'
    )
    diff = diff_lifts(before, after)

    # The hidden item moved, which no user can see. The options moved, and the control says so.
    assert diff.reordered == ()
    assert [change.fields["options"] for change in diff.changed] == [(["S", "M"], ["M", "S"])]


def test_a_long_reorder_line_names_a_few_items_and_counts_the_rest():
    def listing(order: list[int]):
        return _lift("<ol>" + "".join(f"<li>Item {n} {'x' * 60}</li>" for n in order) + "</ol>")

    [line] = diff_lifts(listing(list(range(10))), listing(list(reversed(range(10))))).lines()

    assert line.startswith("reordered 10x <li>: 'Item 9 xxx") and line.endswith("…', … 4 more")
    assert line.count("'Item ") == 6 and len(line) < 320


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
