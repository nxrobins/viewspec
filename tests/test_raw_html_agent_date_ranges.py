"""Date ranges: fields that hold a range, start and end pairs, and how a range is picked in one step."""

from __future__ import annotations

import datetime

import pytest

from viewspec.raw_html import lift_html
from viewspec.raw_html_actions import AgentActionError, parse_date_range, plan_control_action, plan_form_call

PAIR = """
<form aria-label="Stay">
  <label>Check-in <input name="checkin" readonly placeholder="DD/MM/YYYY" data-lift-ref="e1"></label>
  <label>Check-out <input name="checkout" readonly placeholder="DD/MM/YYYY" data-lift-ref="e2"></label>
  <label>Travel dates <input name="trip" placeholder="DD/MM/YYYY - DD/MM/YYYY" data-lift-ref="e3"></label>
  <label>From <input type="date" name="from" min="2026-01-01"></label>
  <label>To <input type="date" name="to"></label>
  <button>Go</button>
</form>
"""


def _controls(html: str) -> dict[str, object]:
    return {control.name: control for control in lift_html(html, profile="agent").controls}


@pytest.mark.parametrize(
    "value",
    [
        "2026-11-20/2026-11-23",
        "2026-11-20 / 2026-11-23",
        "Nov 20, 2026 to Nov 23, 2026",
        "20/11/2026 - 23/11/2026",
        "20 November 2026 – 23 November 2026",
        "Friday, November 20, 2026—Monday, November 23, 2026",
        "2026-11-20 until 2026-11-23",
        ["2026-11-20", "Nov 23, 2026"],
        ("2026-11-20", "2026-11-23"),
        {"start": "2026-11-20", "end": "2026-11-23"},
    ],
)
def test_ranges_are_read_in_common_forms(value):
    assert parse_date_range(value) == (datetime.date(2026, 11, 20), datetime.date(2026, 11, 23))


def test_a_range_may_end_on_its_first_day():
    assert parse_date_range("2026-11-20/2026-11-20") == (datetime.date(2026, 11, 20),) * 2


@pytest.mark.parametrize(
    ("value", "words"),
    [
        ("2026-11-20", "a start and an end"),
        (["2026-11-20"], "a start and an end"),
        (["2026-11-20", "2026-11-21", "2026-11-22"], "a start and an end"),
        ({"from": "2026-11-20", "to": "2026-11-23"}, "a start and an end"),
        ("2026-11-23/2026-11-20", "ends .2026-11-20. before it starts"),
        ("11/12/2026 - 13/12/2026", "two dates"),
        ("2026-02-30 to 2026-03-02", "not a real date"),
        (None, "a start and an end"),
    ],
)
def test_bad_ranges_are_refused(value, words):
    with pytest.raises(AgentActionError, match=words) as caught:
        parse_date_range(value)
    assert caught.value.code == "AGENT_ACTION_VALUE_INVALID"


def test_range_fields_and_pairs_are_found():
    controls = _controls(PAIR)
    checkin, checkout = controls["Check-in"], controls["Check-out"]
    assert (checkin.date_range, checkin.range_with) == ("start", checkout.node_id)
    assert (checkout.date_range, checkout.range_with) == ("end", checkin.node_id)
    assert (controls["Travel dates"].date_range, controls["Travel dates"].range_with) == ("range", None)
    assert (controls["From"].date_range, controls["To"].date_range) == ("start", "end")
    assert checkin.to_json()["date_range"] == "start"


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ('name="arrival_date" aria-label="Arrival"', 'name="departure_date" aria-label="Departure"'),
        ('name="depart" aria-label="Depart"', 'name="return" aria-label="Return"'),
        ('name="startDate" aria-label="Start date"', 'name="endDate" aria-label="End date"'),
        ('name="dateFrom" aria-label="Date"', 'name="dateTo" aria-label="Date"'),
        ('name="pickup_date" aria-label="Pick-up date"', 'name="dropoff_date" aria-label="Drop-off date"'),
        ('aria-label="Date" aria-controls="cal"', 'aria-label="Date" aria-controls="cal"'),
    ],
)
def test_pairs_are_found_by_their_names_or_a_shared_calendar(first, second):
    html = f'<input type="date" {first}><input placeholder="DD/MM/YYYY" {second}>'
    start, end = lift_html(html, profile="agent").controls
    assert (start.date_range, end.date_range) == ("start", "end")


@pytest.mark.parametrize(
    "html",
    [
        # Not next to each other: another date field sits between them.
        '<input type="date" name="checkin"><input type="date" name="dob"><input type="date" name="checkout">',
        # Each in its own form.
        '<form><input type="date" name="checkin"></form><form><input type="date" name="checkout"></form>',
        # The words do not belong to one pair.
        '<input type="date" name="checkin"><input type="date" name="end_date">',
        # Times and months are not days.
        '<input type="time" name="start_time"><input type="time" name="end_time">',
        # In the wrong order.
        '<input type="date" name="checkout"><input type="date" name="checkin">',
    ],
)
def test_other_date_fields_are_not_pairs(html):
    assert all(control.date_range is None for control in lift_html(html, profile="agent").controls)


def test_a_range_on_a_pair_of_custom_fields_is_one_step():
    lift = lift_html(PAIR, profile="agent")
    controls = {control.name: control for control in lift.controls}
    for field in ("Check-in", "Check-out"):  # Either field of the pair takes the range.
        [step] = plan_control_action(controls[field], "select", "Nov 20, 2026 to Nov 23, 2026", lift=lift)
        assert step.to_json() == {
            "op": "choose_date_range",
            "node_id": controls["Check-in"].node_id,
            "ref": "e1",
            "name": "Check-in",
            "value": "2026-11-20/2026-11-23",
            "end": {"node_id": controls["Check-out"].node_id, "ref": "e2", "name": "Check-out"},
        }
    [one] = plan_control_action(controls["Check-in"], "select", "2026-11-20", lift=lift)
    assert (one.op, one.value) == ("choose_date", "2026-11-20")  # One date is still one date.
    with pytest.raises(AgentActionError, match="plan a range with the page's lift"):
        plan_control_action(controls["Check-in"], "select", "2026-11-20/2026-11-23")


def test_a_range_on_a_range_field_or_native_pair():
    lift = lift_html(PAIR, profile="agent")
    controls = {control.name: control for control in lift.controls}
    [step] = plan_control_action(controls["Travel dates"], "select", ["2026-12-28", "2027-01-03"])
    assert (step.op, step.value, step.end_node_id) == ("choose_date_range", "2026-12-28/2027-01-03", None)
    with pytest.raises(AgentActionError, match="a start and an end"):
        plan_control_action(controls["Travel dates"], "select", "2026-12-28")

    steps = plan_control_action(controls["To"], "select", {"start": "2026-10-01", "end": "2026-10-31"}, lift=lift)
    assert [(step.op, step.name, step.value) for step in steps] == [
        ("fill", "From", "2026-10-01"),
        ("fill", "To", "2026-10-31"),
    ]
    with pytest.raises(AgentActionError, match="before the earliest"):
        plan_control_action(controls["From"], "select", "2025-12-30/2026-01-02", lift=lift)


def test_a_disabled_end_field_refuses_the_range():
    html = PAIR.replace('name="checkout" readonly', 'name="checkout" readonly disabled')
    lift = lift_html(html, profile="agent")
    checkin = next(control for control in lift.controls if control.name == "Check-in")
    with pytest.raises(AgentActionError) as caught:
        plan_control_action(checkin, "select", "2026-11-20/2026-11-23", lift=lift)
    assert caught.value.code == "AGENT_ACTION_DISABLED"


def test_form_tools_describe_ranges_and_pick_a_pair_in_one_step():
    lift = lift_html(PAIR, profile="agent")
    [schema] = lift.form_tool_schemas()
    props = schema["input_schema"]["properties"]
    assert "the start of a date range that ends at 'checkout'" in props["checkin"]["description"]
    assert "the end of the date range from 'checkin', not before it" in props["checkout"]["description"]
    assert props["trip"]["type"] == "object" and props["trip"]["required"] == ["start", "end"]
    assert props["trip"]["properties"]["start"] == {"type": "string", "format": "date"}

    steps = plan_form_call(
        lift,
        "stay",
        {"checkin": "2026-11-20", "checkout": "Nov 23, 2026", "trip": {"start": "2026-12-01", "end": "2026-12-05"}},
    )
    assert [(step.op, step.value) for step in steps] == [
        ("choose_date_range", "2026-11-20/2026-11-23"),
        ("choose_date_range", "2026-12-01/2026-12-05"),
        ("click", None),
    ]
    alone = plan_form_call(lift, "stay", {"checkout": "2026-11-23"}, submit=False)
    assert [(step.op, step.name) for step in alone] == [("choose_date", "Check-out")]


def test_form_calls_refuse_a_range_that_ends_before_it_starts():
    lift = lift_html(PAIR, profile="agent")
    with pytest.raises(AgentActionError) as caught:
        plan_form_call(
            lift, "stay", {"checkin": "2026-11-20", "checkout": "2026-11-19", "from": "2026-04-01", "to": "x"}
        )
    problems = {item["argument"]: item["message"] for item in caught.value.details}
    assert problems["checkout"] == "'checkout' (2026-11-19) is before 'checkin' (2026-11-20)."
    assert "not a date" in problems["to"]
