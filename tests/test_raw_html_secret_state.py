"""Secret occupancy is observable without treating redaction as an empty value."""

import json

import pytest

from tests.test_browser_bridge_view import _page_view
from viewspec.raw_html import lift_html
from viewspec.raw_html_actions import AgentActionError, plan_form_call
from viewspec.raw_html_diff import diff_lifts


def _lift(state=None, *, required=False):
    hint = f'data-lift-value-state="{state}"' if state is not None else ""
    return lift_html(
        f'<form aria-label="Account"><label>Password <input type="password" name="pw" '
        f'value="synthetic-password-826" {hint} {"required" if required else ""}>'
        "</label><button>Save</button></form>",
        profile="agent",
    )


@pytest.mark.parametrize(
    "hint, expected",
    [(None, "unknown"), ("invalid", "unknown"), ("unknown", "unknown"), ("empty", "empty"), ("filled", "filled")],
)
def test_secret_state_survives_redaction_in_the_public_interfaces(hint, expected):
    lift = _lift(hint)
    control = lift.controls[0]
    assert control.value_state == expected
    assert control.to_json()["value_state"] == expected
    schema = lift.form_tool_schemas()[0]["input_schema"]["properties"]["pw"]
    assert schema["writeOnly"] and schema["x-value-state"] == expected
    assert "default" not in schema
    lines, _ = _page_view().view_lines(lift)
    assert f"secret {expected}" in next(line.text for line in lines if line.kind == "control")
    assert "synthetic-password-826" not in json.dumps([lift.to_json(), lift.form_tool_schemas()])


def test_secret_state_changes_are_reported_without_secret_values():
    change = diff_lifts(_lift("filled"), _lift("empty"))
    assert len(change.changed) == 1
    assert change.changed[0].fields == {"value_state": ("filled", "empty")}
    assert "value_state 'filled' -> 'empty'" in change.lines()[0]
    assert "synthetic-password-826" not in json.dumps(change.to_json())


def test_ordinary_fields_do_not_repeat_a_value_change_as_an_occupancy_change():
    before = lift_html('<input name="city" value="" data-lift-value-state="empty">', profile="agent")
    after = lift_html('<input name="city" value="Lisbon" data-lift-value-state="filled">', profile="agent")
    [change] = diff_lifts(before, after).changed
    assert change.fields == {"value": ("", "Lisbon")}


@pytest.mark.parametrize("state", ["empty", "filled", "unknown"])
def test_omission_preserves_an_optional_secret_and_empty_string_plans_a_clear(state):
    lift = _lift(state)
    assert plan_form_call(lift, "account", {}, submit=False) == ()
    [step] = plan_form_call(lift, "account", {"pw": ""}, submit=False)
    assert step.op == "fill" and step.value == "" and step.secret


@pytest.mark.parametrize("state", ["empty", "filled", "unknown"])
def test_required_secret_omission_needs_known_filled_state(state):
    lift = _lift(state, required=True)
    if state == "filled":
        [step] = plan_form_call(lift, "account", {})
        assert step.op == "click"
    else:
        with pytest.raises(AgentActionError) as error:
            plan_form_call(lift, "account", {})
        assert error.value.code == "AGENT_FORM_ARGUMENTS_INVALID"
