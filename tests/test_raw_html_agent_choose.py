"""Custom ARIA comboboxes and listboxes: their options, and choosing one."""

from __future__ import annotations

import json
from html import escape

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
        "name": "France",
        "kind": "select",
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


def test_unsupported_popup_preserves_links_filter_and_prose_without_form_arguments():
    lift = _lift("""<form aria-label="Issue"><input name="title" aria-label="Title">
      <div role="combobox" aria-label="Labels" aria-expanded="true" aria-controls="labels">
        <div role="listbox" id="labels"><p>Choose labels for this issue.</p>
          <input placeholder="Filter Label"><a href="?labels=1">operations</a>
          <button type="button">Clear labels</button></div></div></form>""")
    controls = _by_name(lift)
    assert {"Labels", "Filter Label", "operations", "Clear labels"} <= controls.keys()
    assert controls["Labels"].choice.state == "unsupported"
    assert controls["Filter Label"].choice_owner == controls["Labels"].node_id
    assert any("Choose labels" in role.text for role in lift.roles)
    assert set(lift.form_tool_schemas()[0]["input_schema"]["properties"]) == {"title"}
    with pytest.raises(AgentActionError, match="visible controls"):
        plan_control_action(controls["Labels"], "select", "operations")


def test_partially_represented_popup_preserves_residual_actions():
    lift = _lift("""<div role="combobox" aria-label="Labels" aria-expanded="true" aria-controls="labels"></div>
      <div role="listbox" id="labels"><div role="option">operations</div>
        <input placeholder="Filter"><a href="/help">Label help</a></div>""")
    controls = _by_name(lift)
    assert set(controls) == {"Labels", "Filter", "Label help"}
    assert [o.label for o in controls["Labels"].options] == ["operations"]


@pytest.mark.parametrize(
    "hint",
    ['{"version":1,"mode":[]}', '{"version":1,"mode":"single","options":[{"ref":[],"label":"wrong"}]}', "broken"],
)
def test_malformed_choice_hints_do_not_hide_real_options_or_crash(hint):
    lift = _lift(f"""<div role="listbox" aria-label="Labels" data-lift-choice='{hint}'>
        <div role="option">operations</div><a href="/help">Help</a></div>""")
    assert [o.label for o in _by_name(lift)["Labels"].options] == ["operations"]
    assert "Help" in _by_name(lift)


def test_custom_multi_choice_uses_exact_arrays_and_explicit_empty_set():
    lift = _lift("""<form aria-label="Issue"><div role="listbox" aria-label="Labels" aria-multiselectable="true">
      <div role="option" aria-selected="true">operations</div><div role="option" aria-selected="false">urgent</div>
      </div></form>""")
    labels = _by_name(lift)["Labels"]
    assert labels.multiple
    assert plan_control_action(labels, "select", ["operations", "urgent"])[0].values == ("operations", "urgent")
    assert plan_control_action(labels, "select", [])[0].to_json()["values"] == []
    with pytest.raises(AgentActionError, match="array"):
        plan_control_action(labels, "select", "operations")
    with pytest.raises(AgentActionError, match="duplicate"):
        plan_control_action(labels, "select", ["operations", "OPERATIONS"])
    schema = lift.form_tool_schemas()[0]["input_schema"]["properties"]["labels"]
    assert schema["type"] == "array" and schema["items"]["type"] == "string"
    assert schema["default"] == ["operations"]
    assert plan_form_call(lift, lift.form_tool_schemas()[0]["name"], {}, submit=False) == ()


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


def test_a_select_only_combobox_cannot_be_typed_into():
    # The W3C APG select-only combobox is a div: fill is refused before the browser acts.
    [combo] = [
        control
        for control in _lift(
            '<div role="combobox" aria-label="Fruit" tabindex="0" aria-expanded="false">Choose</div>'
        ).controls
        if control.control == "combobox"
    ]
    with pytest.raises(AgentActionError) as caught:
        plan_control_action(combo, "fill", "Ban")
    assert caught.value.code == "AGENT_ACTION_UNSUPPORTED" and "use select" in caught.value.message


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


def test_action_qualifier_survives_static_extraction_and_is_not_a_form_example():
    lift = _lift(
        '<form aria-label="Labels"><input role="combobox" aria-label="Label" aria-controls="opts">'
        '<div role="listbox" id="opts"><button role="option"><div>Equipment review</div>'
        "<div>Add this as new label</div></button></div></form>"
    )
    combo = _by_name(lift)["Label"]
    assert combo.options[0].label == "Equipment review"
    assert combo.options[0].name == "Equipment review Add this as new label"
    assert combo.options[0].kind == "unknown"
    assert "Equipment review Add this as new label" in _by_name(lift)
    assert "examples" not in lift.form_tool_schemas()[0]["input_schema"]["properties"]["label"]


def _live_contract(**changes):
    contract = {
        "version": 2,
        "adapter": "vikunja-labels",
        "mode": "single",
        "state": "available",
        "coverage": "partial",
        "commit": "immediate",
        "expanded": True,
        "selection_known": True,
        "selected": ["Existing"],
        "operation": "add",
        "query": {"text": "Equipment review", "generation": 2},
        "options": [
            {
                "ref": "e2",
                "label": "Equipment review",
                "name": "Equipment review Add as new label",
                "description": "",
                "kind": "create",
                "selected": None,
            }
        ],
    }
    contract.update(changes)
    return _lift(
        '<form aria-label="Labels"><input role="combobox" aria-label="Label" aria-controls="opts" '
        'value="Equipment review" data-lift-choice="' + escape(json.dumps(contract), quote=True) + '">'
        '<div role="listbox" id="opts"><button role="option" data-lift-ref="e2">'
        "<div>Equipment review</div><div>Add as new label</div></button></div></form>"
    )


def test_live_choice_distinguishes_query_selection_add_and_explicit_creation():
    lift = _live_contract()
    control = _by_name(lift)["Label"]
    assert control.choice.operation == "add"
    assert control.choice.query == "Equipment review" and control.value == "Existing"
    assert control.to_json()["options"][0]["kind"] == "create"
    assert "Equipment review Add as new label" in _by_name(lift)
    field = lift.form_tool_schemas()[0]["input_schema"]["properties"]["label"]
    assert "examples" not in field and "add one existing" in field["description"]
    assert "default" not in field  # The displayed membership set is not an argument to add one item.


@pytest.mark.parametrize(
    "changes",
    [
        {"operation": []},
        {"version": []},
        {"query": []},
        {"options": [{"ref": "e2", "label": "Equipment review", "kind": []}]},
    ],
)
def test_malformed_new_contract_falls_back_without_hiding_popup_actions(changes):
    lift = _live_contract(**changes)
    assert "Equipment review Add as new label" in _by_name(lift)


def test_option_meaning_change_with_same_short_label_appears_in_diff():
    before = _live_contract()
    after = _live_contract(
        options=[
            {
                "ref": "e2",
                "label": "Equipment review",
                "name": "Equipment review Select existing",
                "description": "",
                "kind": "select",
                "selected": False,
            }
        ]
    )
    change = next(c for c in diff_lifts(before, after).changed if c.after.name == "Label")
    assert change.fields["options"] == (
        ["Equipment review Add as new label [create]"],
        ["Equipment review Select existing"],
    )
