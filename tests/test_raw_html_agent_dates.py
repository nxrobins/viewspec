"""Date fields: which fields hold dates, how date values are read, and how a date is picked."""

from __future__ import annotations

import datetime

import pytest

from viewspec.raw_html import lift_html
from viewspec.raw_html_actions import AgentActionError, parse_date, plan_control_action, plan_form_call


def _control(html: str):
    [control] = lift_html(html, profile="agent").controls
    return control


@pytest.mark.parametrize(
    ("text", "day"),
    [
        ("2026-11-20", (2026, 11, 20)),
        ("2026/1/5", (2026, 1, 5)),
        ("2026.11.20", (2026, 11, 20)),
        ("20/11/2026", (2026, 11, 20)),
        ("11/20/2026", (2026, 11, 20)),
        ("7.7.2026", (2026, 7, 7)),
        ("20 November 2026", (2026, 11, 20)),
        ("20th Nov 2026", (2026, 11, 20)),
        ("Nov 20, 2026", (2026, 11, 20)),
        ("November 20th, 2026", (2026, 11, 20)),
        ("Friday, November 20, 2026", (2026, 11, 20)),
        ("sept. 3 2026", (2026, 9, 3)),
        ("  2026-02-28 ", (2026, 2, 28)),
    ],
)
def test_dates_are_read_in_common_forms(text, day):
    assert parse_date(text) == datetime.date(*day)


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("11/12/2026", "two dates"),
        ("2026-02-30", "not a real date"),
        ("next friday", "not a date"),
        ("20 Novembre 2026", "not a date"),
        ("", "Expected a date"),
        (20261120, "Expected a date"),
    ],
)
def test_unclear_dates_are_refused(text, words):
    with pytest.raises(AgentActionError, match=words) as caught:
        parse_date(text)
    assert caught.value.code == "AGENT_ACTION_VALUE_INVALID"


@pytest.mark.parametrize(
    ("html", "date"),
    [
        ('<input type="date" aria-label="Day">', True),
        ('<input type="time" aria-label="At">', True),
        ('<input name="check_in" aria-label="Arrive">', True),
        ('<input name="x" aria-label="Date of birth">', True),
        ('<input name="x" aria-label="When" placeholder="DD/MM/YYYY">', True),
        ('<input name="x" aria-label="When" placeholder="yyyy-mm-dd">', True),
        ('<input name="update" aria-label="Update note">', False),
        ('<input name="email" aria-label="Email">', False),
        ('<textarea aria-label="Due notes"></textarea>', False),
    ],
)
def test_date_fields_are_found(html, date):
    assert _control(html).date is date


@pytest.mark.parametrize(
    ("html", "value", "filled"),
    [
        ('<input type="date" aria-label="Day">', "Nov 20, 2026", "2026-11-20"),
        ('<input type="month" aria-label="M">', "November 2026", "2026-11"),
        ('<input type="month" aria-label="M">', "2026-3", "2026-03"),
        ('<input type="time" aria-label="T">', "2:05 pm", "14:05"),
        ('<input type="time" aria-label="T">', "12:30 am", "00:30"),
        ('<input type="time" aria-label="T">', "09:15:30", "09:15:30"),
        ('<input type="datetime-local" aria-label="D">', "20/11/2026 18:30", "2026-11-20T18:30"),
        ('<input type="week" aria-label="W">', "2026-w07", "2026-W07"),
    ],
)
def test_native_date_fields_are_filled_in_their_own_format(html, value, filled):
    control = _control(html)
    for op in ("select", "fill"):
        [step] = plan_control_action(control, op, value)
        assert (step.op, step.value) == ("fill", filled)


def test_native_limits_are_checked():
    control = _control('<input type="date" aria-label="Day" min="2026-11-10" max="2026-12-31">')
    assert plan_control_action(control, "select", "2026-11-10")[0].value == "2026-11-10"
    for value, words in (("2026-11-09", "before the earliest"), ("2027-01-01", "after the latest")):
        with pytest.raises(AgentActionError, match=words):
            plan_control_action(control, "select", value)
    with pytest.raises(AgentActionError, match="not a time"):
        plan_control_action(_control('<input type="time" aria-label="T">'), "fill", "25:00")


def test_a_custom_date_field_plans_choose_date():
    control = _control(
        '<input name="checkout" readonly placeholder="DD/MM/YYYY" aria-label="Check-out" data-lift-ref="e4">'
    )
    [step] = plan_control_action(control, "select", "20 November 2026")
    assert step.to_json() == {
        "op": "choose_date",
        "node_id": control.node_id,
        "ref": "e4",
        "name": "Check-out",
        "value": "2026-11-20",
    }
    with pytest.raises(AgentActionError, match="read-only"):
        plan_control_action(control, "fill", "20/11/2026")  # Typing is not allowed; the calendar is.


def test_form_tools_describe_date_fields():
    lift = lift_html(
        """
        <form aria-label="Book">
          <label>Check-in <input type="date" name="checkin" min="2026-11-10" required></label>
          <label>Check-out <input name="checkout" readonly placeholder="DD/MM/YYYY" value="21/11/2026"></label>
          <label>Guests <input type="number" name="guests" min="1" max="8"></label>
          <button>Book</button>
        </form>
        """,
        profile="agent",
    )
    [schema] = lift.form_tool_schemas()
    props = schema["input_schema"]["properties"]

    assert props["checkin"]["format"] == "date" and "(YYYY-MM-DD, from 2026-11-10)" in props["checkin"]["description"]
    assert props["checkout"]["format"] == "date" and "picked from its calendar" in props["checkout"]["description"]
    assert "default" not in props["checkout"]  # The page shows 21/11/2026, not the tool's format.
    assert (props["guests"]["minimum"], props["guests"]["maximum"]) == (1, 8)
    steps = plan_form_call(lift, "book", {"checkin": "Nov 20, 2026", "checkout": "2026-11-23"}, submit=False)
    assert [(step.op, step.value) for step in steps] == [("fill", "2026-11-20"), ("choose_date", "2026-11-23")]
    with pytest.raises(AgentActionError) as caught:
        plan_form_call(lift, "book", {"checkin": "11/12/2026"})
    assert caught.value.details[0]["problem"] == "invalid" and "two dates" in caught.value.details[0]["message"]
