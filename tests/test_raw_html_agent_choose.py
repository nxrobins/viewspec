"""Custom ARIA comboboxes and listboxes: their options, and choosing one."""

from __future__ import annotations

import pytest

from viewspec.raw_html import lift_html
from viewspec.raw_html_actions import AgentActionError, plan_control_action, plan_form_call
from viewspec.raw_html_diff import diff_lifts

OPEN = """
<form aria-label="Ship">
  <span id="cl">Country</span>
  <input role="combobox" name="country" aria-labelledby="cl" aria-expanded="true" aria-controls="lst"
         data-lift-ref="e1">
  <ul role="listbox" id="lst">
    <li role="option" data-lift-ref="e2">France</li>
    <li role="option" aria-selected="true" data-lift-ref="e3">Japan</li>
    <li role="option" aria-disabled="true" data-lift-ref="e4">Atlantis</li>
  </ul>
  <div role="listbox" aria-label="Size"><div role="option">S</div><div role="option" aria-selected="true">M</div></div>
</form>
"""


def _lift(html: str):
    return lift_html(html, profile="agent")


def _by_name(lift):
    return {control.name: control for control in lift.controls}


def test_a_combobox_owns_the_list_it_controls():
    lift = _lift(OPEN)
    controls = _by_name(lift)
    country = controls["Country"]

    assert set(controls) == {"Country", "Size"}  # The list is not a field of its own.
    assert country.expanded is True
    assert [(o.label, o.ref, o.selected, o.disabled) for o in country.options] == [
        ("France", "e2", False, False),
        ("Japan", "e3", True, False),
        ("Atlantis", "e4", False, True),
    ]
    assert not any(role.text in {"France", "Japan"} for role in lift.roles)
    assert country.to_json()["options"][0] == {
        "value": "France",
        "label": "France",
        "selected": False,
        "disabled": False,
        "ref": "e2",
    }


def test_a_closed_list_has_no_options():
    closed = OPEN.replace('aria-expanded="true"', 'aria-expanded="false"').replace('id="lst"', 'id="lst" hidden')
    country = _by_name(_lift(closed))["Country"]
    assert country.expanded is False and country.options == ()

    # Without aria-expanded, the list's visibility says whether the combobox is open.
    no_attr = closed.replace(' aria-expanded="false"', "")
    assert _by_name(_lift(no_attr))["Country"].expanded is False
    assert _by_name(_lift(OPEN.replace(' aria-expanded="true"', "")))["Country"].expanded is True


def test_aria_owns_and_a_wrapping_combobox_work_too():
    owns = _lift(
        '<input role="combobox" aria-label="City" aria-owns="c"><div role="listbox" id="c">'
        '<div role="option">Oslo</div></div>'
    )
    assert [o.label for o in _by_name(owns)["City"].options] == ["Oslo"]

    wrapper = _lift(
        '<div role="combobox" aria-label="Gift wrap" tabindex="0">None'
        '<ul role="listbox"><li role="option">Paper</li><li role="option">Box</li></ul></div>'
    )
    wrap = _by_name(wrapper)["Gift wrap"]
    assert [o.label for o in wrap.options] == ["Paper", "Box"]
    assert wrap.value == "None"


def test_a_listbox_is_named_by_its_label_not_its_options():
    controls = _by_name(_lift(OPEN))
    assert controls["Size"].value == "M"
    unnamed = _lift('<div role="listbox"><div role="option">A</div><div role="option">B</div></div>')
    assert [c.name for c in unnamed.controls] == [""]


def test_select_on_a_combobox_plans_a_choose_step():
    country = _by_name(_lift(OPEN))["Country"]

    [step] = plan_control_action(country, "select", "  japan ")
    assert step.to_json() == {
        "op": "choose",
        "node_id": country.node_id,
        "ref": "e1",
        "name": "Country",
        "value": "japan",
    }
    [step] = plan_control_action(country, "select", ["Spain"])  # A closed or filtered list may not show it yet.
    assert step.value == "Spain"


def test_choosing_on_a_listbox_must_name_an_enabled_option():
    size = _by_name(_lift(OPEN))["Size"]
    assert plan_control_action(size, "select", "s")[0].op == "choose"
    with pytest.raises(AgentActionError) as caught:
        plan_control_action(size, "select", "XL")
    assert caught.value.code == "AGENT_ACTION_VALUE_INVALID"


@pytest.mark.parametrize(
    ("html", "value", "code"),
    [
        (OPEN, "", "AGENT_ACTION_VALUE_INVALID"),
        (OPEN, ["France", "Japan"], "AGENT_ACTION_VALUE_INVALID"),
        (OPEN, 5, "AGENT_ACTION_VALUE_INVALID"),
        (OPEN.replace('name="country"', 'name="country" disabled'), "France", "AGENT_ACTION_DISABLED"),
    ],
)
def test_bad_choices_are_refused(html, value, code):
    country = _by_name(_lift(html))["Country"]
    with pytest.raises(AgentActionError) as caught:
        plan_control_action(country, "select", value)
    assert caught.value.code == code


def test_an_option_is_named_by_its_title_line():
    # Wikipedia's suggestions show a title over a description; the snapshot marks both as lines.
    lift = _lift(
        OPEN.replace(
            '<li role="option" data-lift-ref="e2">France</li>',
            '<li role="option" data-lift-ref="e2"><span data-lift-display="block">France</span>'
            '<span data-lift-display="block">Country in Western Europe</span></li>',
        )
    )
    assert [option.label for option in _by_name(lift)["Country"].options][:2] == ["France", "Japan"]


def test_an_option_with_an_icon_above_its_title_is_named_by_the_title():
    # react-select.com's section picker: an emoji on a line of its own, then the title.
    lift = _lift(
        OPEN.replace(
            '<li role="option" data-lift-ref="e2">France</li>',
            '<li role="option" data-lift-ref="e2"><span data-lift-display="block">🎨</span>'
            '<span data-lift-display="block">CSS-in-JS styling API</span></li>',
        )
    )
    assert _by_name(lift)["Country"].options[0].label == "CSS-in-JS styling API"


def test_select_on_a_search_field_types_and_picks_a_suggestion():
    # Wikipedia's search box is a plain field until it has focus, then a combobox: the browser finds out.
    [field] = _lift('<input type="search" aria-label="Search Wikipedia">').controls
    [step] = plan_control_action(field, "select", "Alan Turing")
    assert step.op == "choose" and step.value == "Alan Turing"
    [box] = _lift('<input type="checkbox" aria-label="Agree">').controls
    with pytest.raises(AgentActionError) as caught:
        plan_control_action(box, "select", "yes")
    assert caught.value.code == "AGENT_ACTION_UNSUPPORTED"


def test_a_read_only_combobox_is_still_chosen_from():
    # ARIA: aria-readonly on a combobox means "not editable, but otherwise operable". react-select
    # without search and Ant Design mark their select-only comboboxes this way.
    country = _by_name(_lift(OPEN.replace('name="country"', 'name="country" readonly aria-readonly="true"')))["Country"]
    assert country.readonly is True
    [step] = plan_control_action(country, "select", "France")
    assert step.op == "choose" and step.value == "France"
    with pytest.raises(AgentActionError) as caught:
        plan_control_action(country, "fill", "Fra")
    assert caught.value.code == "AGENT_ACTION_READONLY"


BESIDE = """
<label for="colour">Colour</label>
<div class="box">
  <div>Purple</div>
  <input id="colour" role="combobox" readonly aria-readonly="true" aria-haspopup="true" aria-expanded="false" value="">
  <span aria-live="polite">option Purple, selected.</span>
  <button aria-label="Clear"><span>×</span></button>
</div>
"""


def test_an_input_combobox_shows_the_value_beside_it():
    # react-select and Ant Design keep the input empty and show the choice in the box around it.
    colour = _by_name(_lift(BESIDE))["Colour"]
    assert colour.value == "Purple"

    typed = _by_name(_lift(BESIDE.replace('value=""', 'value="Pur"')))["Colour"]
    assert typed.value == "Pur"  # What is typed in the input wins.


@pytest.mark.parametrize(
    "html",
    [
        '<form><p>Choose a colour:</p><input role="combobox" aria-label="Colour"></form>',
        '<li><span>Item one</span> <input role="combobox" aria-label="Colour"></li>',
        '<div><label>Colour <input role="combobox"></label></div>',
    ],
)
def test_the_text_beside_an_input_combobox_stops_at_its_widget(html):
    assert _by_name(_lift(html))["Colour"].value == ""


def test_form_tools_choose_from_lists():
    lift = _lift(OPEN)
    [schema] = lift.form_tool_schemas()
    props = schema["input_schema"]["properties"]

    assert props["country"]["type"] == "string" and props["country"]["examples"] == ["France", "Japan"]
    assert "pick an option" in props["country"]["description"]
    assert props["size"]["enum"] == ["S", "M"] and props["size"]["default"] == "M"
    steps = plan_form_call(lift, "ship", {"country": "France", "size": "S"}, submit=False)
    assert [(step.op, step.value) for step in steps] == [("choose", "France"), ("choose", "S")]


def test_opening_a_list_shows_in_a_diff():
    closed = OPEN.replace('aria-expanded="true"', 'aria-expanded="false"').replace('id="lst"', 'id="lst" hidden')
    diff = diff_lifts(_lift(closed), _lift(OPEN))
    [change] = [c for c in diff.changed if c.after.name == "Country"]

    assert change.fields["expanded"] == (False, True)
    assert change.fields["options"] == ([], ["France", "Japan (selected)", "Atlantis (disabled)"])


def test_native_selects_still_use_select():
    [control] = _lift(
        '<select aria-label="Size"><option data-lift-ref="e1">S</option><option data-lift-ref="e2">M</option></select>'
    ).controls
    assert [o.ref for o in control.options] == ["e1", "e2"] and all(o.node_id for o in control.options)
    assert plan_control_action(control, "select", "M")[0].op == "select"
