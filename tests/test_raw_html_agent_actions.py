"""Snapshot refs flow through the agent lift, and the planner turns agent requests into checked steps."""

from __future__ import annotations

import json

import pytest

from viewspec.raw_html import lift_html
from viewspec.raw_html_actions import AgentActionError, plan_control_action, plan_form_call, plan_scroll
from viewspec.raw_html_agent import control_aria_role

CHECKOUT = """
<form id="checkout" aria-label="Checkout" data-lift-ref="e1">
  <label>Email <input type="email" name="email" required data-lift-ref="e2"></label>
  <label>Quantity <input type="number" name="qty" value="1" data-lift-ref="e3"></label>
  <fieldset><legend>Speed</legend>
    <label><input type="radio" name="ship" value="std" checked data-lift-ref="e4"> Standard</label>
    <label><input type="radio" name="ship" value="fast" data-lift-ref="e5"> Fast</label>
  </fieldset>
  <label>Size <select name="size" data-lift-ref="e6">
    <option value="s">Small</option><option value="m">Medium</option><option value="xl" disabled>XL</option>
  </select></label>
  <label>Extras <select name="extras" multiple data-lift-ref="e7"><option>Bag</option><option>Card</option></select></label>
  <label>Password <input type="password" name="pw" required data-lift-ref="e8"></label>
  <label>Code <input name="code" value="ABC" readonly data-lift-ref="e9"></label>
  <button type="button" data-lift-ref="e10">Help</button>
  <button data-lift-ref="e11">Pay now</button>
</form>
<label><input type="checkbox" name="terms" form="checkout" required data-lift-ref="e12"> I accept</label>
"""


def _lift(html: str = CHECKOUT):
    return lift_html(html, profile="agent")


def _by_name(lift):
    return {control.name: control for control in lift.controls}


def test_refs_tags_and_flags_flow_through_the_lift():
    lift = _lift()
    controls = _by_name(lift)

    assert controls["Email"].ref == "e2" and controls["Email"].tag == "input"
    assert controls["Size"].tag == "select"
    assert controls["Password"].secret is True and controls["Email"].secret is False
    assert controls["Code"].readonly is True
    assert lift.forms[0].ref == "e1"
    assert lift.control(controls["Pay now"].node_id).ref == "e11"
    with pytest.raises(KeyError):
        lift.control("dom_missing")


def test_refs_do_not_change_node_ids_or_the_fingerprint():
    with_refs = _lift()
    plain = _lift(CHECKOUT.replace(' data-lift-ref="', ' data-x="'))

    assert [c.node_id for c in with_refs.controls] == [c.node_id for c in plain.controls]
    assert with_refs.topology_fingerprint.hex == plain.topology_fingerprint.hex


@pytest.mark.parametrize(
    ("html", "role"),
    [
        ('<a href="/x">Go</a>', "link"),
        ("<button>Go</button>", "button"),
        ('<input type="submit">', "button"),
        ('<input aria-label="Q">', "textbox"),
        ('<input type="password" aria-label="P">', "textbox"),
        ('<input type="search" aria-label="S">', "searchbox"),
        ('<input type="number" aria-label="N">', "spinbutton"),
        ('<input type="checkbox" aria-label="C">', "checkbox"),
        ('<select aria-label="S"><option>a</option></select>', "combobox"),
        ('<select multiple aria-label="S"><option>a</option></select>', "listbox"),
        ('<div role="switch" aria-label="W" aria-checked="false"></div>', "switch"),
        ('<div tabindex="0">Card</div>', None),
        ("<details><summary>More</summary></details>", None),
        ('<input type="date" aria-label="D">', None),
    ],
)
def test_control_aria_role(html, role):
    [control] = _lift(html).controls
    assert control_aria_role(control) == role


def test_form_call_plans_steps_in_document_order_and_submits():
    lift = _lift()
    [schema] = lift.form_tool_schemas()
    steps = plan_form_call(
        lift,
        schema["name"],
        {
            "terms": True,
            "email": "bo@example.com",
            "qty": 3,
            "ship": "fast",
            "size": "Medium",
            "extras": ["Card", "Bag"],
            "pw": "hunter2-long",
        },
    )

    assert [(step.op, step.ref) for step in steps] == [
        ("fill", "e2"),
        ("fill", "e3"),
        ("check", "e5"),
        ("select", "e6"),
        ("select", "e7"),
        ("fill", "e8"),
        ("check", "e12"),
        ("click", "e11"),
    ]
    assert steps[1].value == "3"
    assert steps[3].values == ("m",)
    assert steps[4].values == ("Card", "Bag")
    assert steps[5].secret is True and steps[5].value == "hunter2-long"


def test_secret_values_are_redacted_when_steps_are_logged():
    lift = _lift()
    steps = plan_form_call(lift, "checkout", {"email": "a@b.co", "pw": "hunter2-long", "terms": True}, submit=False)
    logged = json.dumps([step.to_json() for step in steps])

    assert "hunter2-long" not in logged and "[redacted]" in logged
    assert "hunter2-long" in json.dumps([step.to_json(redact=False) for step in steps])


def test_form_call_reports_every_problem_at_once():
    lift = _lift()
    with pytest.raises(AgentActionError) as caught:
        plan_form_call(
            lift,
            "checkout",
            {"ship": "overnight", "size": "XL", "qty": "many", "colour": "red", "code": "XYZ"},
        )

    error = caught.value
    assert error.code == "AGENT_FORM_ARGUMENTS_INVALID"
    problems = {(item["argument"], item["problem"]) for item in error.details}
    assert problems == {
        ("colour", "unknown"),
        ("email", "missing"),
        ("terms", "missing"),
        ("pw", "missing"),
        ("ship", "invalid"),
        ("size", "invalid"),
        ("qty", "invalid"),
        ("code", "invalid"),
    }
    assert error.to_json()["code"] == "AGENT_FORM_ARGUMENTS_INVALID"


def test_a_required_field_the_page_already_filled_may_be_left_out():
    lift = _lift(
        """
        <form aria-label="Sub"><input name="email" value="a@b.co" required>
        <input type="checkbox" name="ok" checked required><button>Go</button></form>
        """
    )
    steps = plan_form_call(lift, "sub", {})
    assert [step.op for step in steps] == ["click"]


def test_unknown_form_tool_is_refused():
    with pytest.raises(AgentActionError, match="Tools: checkout") as caught:
        plan_form_call(_lift(), "login", {})
    assert caught.value.code == "AGENT_FORM_UNKNOWN"


def test_form_without_a_submit_button_is_submitted_directly():
    lift = _lift('<form aria-label="Search" data-lift-ref="f1"><input name="q"></form>')
    [step] = plan_form_call(lift, "search", {})
    assert (step.op, step.ref, step.node_id) == ("submit", "f1", lift.forms[0].node_id)


def test_disabled_submit_buttons_are_skipped():
    lift = _lift('<form aria-label="F"><button disabled>Old</button><button>New</button></form>')
    [step] = plan_form_call(lift, "f", {})
    assert step.name == "New"

    lift = _lift('<form aria-label="F"><button disabled>Only</button></form>')
    with pytest.raises(AgentActionError) as caught:
        plan_form_call(lift, "f", {})
    assert caught.value.code == "AGENT_ACTION_DISABLED"


def test_forms_with_the_same_name_get_unique_tool_names():
    lift = _lift('<form aria-label="Find"><input name="a"></form><form aria-label="Find"><input name="b"></form>')

    assert [schema["name"] for schema in lift.form_tool_schemas()] == ["find", "find_2"]
    [step] = plan_form_call(lift, "find_2", {"b": "x"}, submit=False)
    assert step.node_id == lift.forms[1].control_ids[0]


def test_aria_widgets_click_only_when_their_state_must_change():
    controls = _by_name(
        _lift(
            """
            <div role="switch" aria-label="Dark" aria-checked="true" tabindex="0"></div>
            <div role="checkbox" aria-label="Notify" aria-checked="false" tabindex="0"></div>
            """
        )
    )

    assert plan_control_action(controls["Dark"], "check") == ()
    assert [step.op for step in plan_control_action(controls["Dark"], "uncheck")] == ["click"]
    assert [step.op for step in plan_control_action(controls["Notify"], "check")] == ["click"]


@pytest.mark.parametrize(
    ("html", "op", "value", "code"),
    [
        ('<a href="/x">Go</a>', "fill", "x", "AGENT_ACTION_UNSUPPORTED"),
        ('<input type="checkbox" aria-label="C">', "fill", "x", "AGENT_ACTION_UNSUPPORTED"),
        ('<input aria-label="T" disabled>', "click", None, "AGENT_ACTION_DISABLED"),
        ('<input aria-label="T" readonly>', "fill", "x", "AGENT_ACTION_READONLY"),
        ('<input aria-label="T">', "fill", 5, "AGENT_ACTION_VALUE_INVALID"),
        ('<input type="radio" name="r" aria-label="R">', "uncheck", None, "AGENT_ACTION_UNSUPPORTED"),
        ("<button>B</button>", "check", None, "AGENT_ACTION_UNSUPPORTED"),
        ('<select aria-label="S"><option>a</option></select>', "select", "b", "AGENT_ACTION_VALUE_INVALID"),
        ('<select aria-label="S"><option>a</option></select>', "select", ["a", "a"], "AGENT_ACTION_VALUE_INVALID"),
        ('<input aria-label="T">', "select", "a", "AGENT_ACTION_UNSUPPORTED"),
        ('<input aria-label="T">', "hover", None, "AGENT_ACTION_UNSUPPORTED"),
        ('<input aria-label="T">', "submit", None, "AGENT_ACTION_UNSUPPORTED"),
    ],
)
def test_control_actions_are_checked(html, op, value, code):
    [control] = _lift(html).controls
    with pytest.raises(AgentActionError) as caught:
        plan_control_action(control, op, value)
    assert caught.value.code == code


def test_control_actions_name_their_target():
    [control] = _lift('<button data-lift-ref="e7">Save</button>').controls
    [step] = plan_control_action(control, "click")
    assert step.to_json() == {"op": "click", "node_id": control.node_id, "ref": "e7", "name": "Save"}


def test_scrolls_name_the_page_or_the_area_of_a_control():
    page = plan_scroll()
    assert page.to_json() == {"op": "scroll", "node_id": "", "ref": None, "name": "page", "value": "end"}

    [control] = _lift('<div style="overflow:auto"><button data-lift-ref="e9">Like</button></div>').controls
    step = plan_scroll("down", control)
    assert (step.op, step.node_id, step.ref, step.name, step.value) == ("scroll", control.node_id, "e9", "Like", "down")
    # A secret field's area scrolls, but the step carries no value of the field.
    [secret] = _lift('<input type="password" aria-label="Pw" value="hunter2-long" data-lift-ref="e1">').controls
    assert plan_scroll("up", secret).to_json()["value"] == "up"


@pytest.mark.parametrize("direction", ["sideways", "", None, 5, "END"])
def test_bad_scroll_directions_are_refused(direction):
    with pytest.raises(AgentActionError) as caught:
        plan_scroll(direction)
    assert caught.value.code == "AGENT_ACTION_VALUE_INVALID"
