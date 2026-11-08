"""The "agent" lift profile keeps form controls, names them, groups them into forms, and keeps no secrets."""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from viewspec.raw_html import compile_html, lift_html


def _controls(html: str) -> dict[str, dict]:
    """Controls keyed by accessible name."""
    return {control.name: control.to_json() for control in lift_html(html, profile="agent").controls}


def test_compile_profile_is_the_default_and_has_no_controls():
    html = '<form><label for="q">Query</label><input id="q" name="q"><button>Go</button></form>'
    default = lift_html(html)
    explicit = lift_html(html, profile="compile")

    assert default.to_json() == explicit.to_json()
    assert "controls" not in default.to_json()
    assert default.controls == () and default.forms == () and default.form_tool_schemas() == []
    assert {node.tag for node in default.nodes}.isdisjoint({"form", "input", "label"})


def test_compile_html_does_not_change_with_the_agent_profile():
    html = '<form action="/s"><input name="q" value="x"><button type="submit">Go</button></form>'
    before = compile_html(html)
    lift_html(html, profile="agent")
    after = compile_html(html)

    assert before.html == after.html
    assert "<input" not in after.html and "<form" not in after.html
    assert 'type="button"' in after.html


def test_unknown_profile_is_refused():
    with pytest.raises(ValueError, match="profile"):
        lift_html("<p>x</p>", profile="browser")


@pytest.mark.parametrize(
    ("html", "name"),
    [
        ('<label for="e">Email address</label><input id="e" type="email">', "Email address"),
        ('<label>Full name <input type="text"></label>', "Full name"),
        ('<span id="a">Card</span><span id="b">holder</span><input aria-labelledby="a b">', "Card holder"),
        ('<input aria-label="City" placeholder="e.g. Paris">', "City"),
        ('<input placeholder="Search docs">', "Search docs"),
        ("<label>Size <select><option>S</option><option>M</option></select></label>", "Size"),
        ('<input type="submit" value="Send it">', "Send it"),
        ('<input type="submit">', "Submit"),
        ('<a href="/cart"><span>Cart</span> <b>(2)</b></a>', "Cart (2)"),
        ('<button aria-label="Close dialog"><svg></svg></button>', "Close dialog"),
        ('<a href="/home"><img src="/logo.png" alt="Home page"></a>', "Home page"),
    ],
)
def test_accessible_names(html, name):
    assert name in _controls(html)


def test_select_options_follow_implied_end_tags():
    html = """
    <select name="size" required>
      <option value="s">Small
      <option value="m" selected>Medium
      <optgroup label="Big" disabled><option value="l">Large<option value="xl">Extra large</optgroup>
    </select>
    """
    [control] = lift_html(html, profile="agent").controls

    assert control.control == "select" and control.required
    assert [(o.value, o.label, o.selected, o.disabled) for o in control.options] == [
        ("s", "Small", False, False),
        ("m", "Medium", True, False),
        ("l", "Large", False, True),
        ("xl", "Extra large", False, True),
    ]
    assert control.value == "m"


def test_field_state_is_read_from_attributes():
    controls = _controls(
        """
        <label><input type="checkbox" name="news" checked> News</label>
        <label><input type="checkbox" name="ads"> Ads</label>
        <label>Notes <textarea name="notes">Leave at door</textarea></label>
        <label>Qty <input type="number" name="qty" value="3" min="1" disabled></label>
        <div role="switch" aria-checked="true" aria-label="Dark mode" tabindex="0"></div>
        """
    )

    assert controls["News"]["checked"] is True
    assert controls["Ads"]["checked"] is False
    assert controls["Notes"]["value"] == "Leave at door"
    assert controls["Qty"]["value"] == "3" and controls["Qty"]["disabled"] is True
    assert controls["Dark mode"]["control"] == "switch" and controls["Dark mode"]["checked"] is True


def test_forms_group_controls_and_find_submit_buttons():
    html = """
    <form id="login" action="/session" method="post">
      <h2>Sign in</h2>
      <input name="user" aria-label="User" required>
      <button type="button">Show help</button>
      <button>Sign in</button>
    </form>
    <input name="remember" type="checkbox" form="login" aria-label="Remember me">
    <input name="loose" aria-label="Not in a form">
    """
    lift = lift_html(html, profile="agent")
    [form] = lift.forms
    by_name = {control.name: control for control in lift.controls}

    assert form.name == "login" and form.method == "POST" and form.action == "/session"
    assert set(form.control_ids) == {by_name[name].node_id for name in ("User", "Show help", "Sign in", "Remember me")}
    assert form.submit_ids == (by_name["Sign in"].node_id,)
    assert by_name["Show help"].submits is False
    assert by_name["Not in a form"].form_id is None


@pytest.mark.parametrize(
    ("html", "name"),
    [
        ('<form aria-label="Find"><button>Go</button></form>', "Find"),
        ("<form><fieldset><legend>Shipping</legend></fieldset><button>Go</button></form>", "Shipping"),
        ('<form action="/search"><input name="q"><button>Search now</button></form>', "Search now"),
        ('<form action="/api/subscribe?x=1"><input name="e"></form>', "subscribe"),
        ("<form><input name='e'></form>", "form"),
    ],
)
def test_form_names(html, name):
    [form] = lift_html(html, profile="agent").forms
    assert form.name == name


def test_aria_radiogroup_names_its_radios():
    lift = lift_html(
        """
        <form><div role="radiogroup" aria-labelledby="g"><span id="g">Shipping speed</span>
          <label><input type="radio" name="ship" value="std"> Standard</label></div></form>
        """,
        profile="agent",
    )
    [control] = lift.controls
    assert control.group == "Shipping speed" and control.name == "Standard"
    assert lift.form_tool_schemas()[0]["input_schema"]["properties"]["ship"]["description"] == "Shipping speed"


def test_disabled_fieldset_disables_its_fields():
    controls = _controls('<form><fieldset disabled><input aria-label="Locked"></fieldset></form>')
    assert controls["Locked"]["disabled"] is True


def test_form_tool_schema_types_fields():
    html = """
    <form aria-label="Checkout" action="/pay" method="post">
      <label>Email <input type="email" name="email" required></label>
      <label>Quantity <input type="number" name="qty" value="1"></label>
      <label>Gift wrap <input type="checkbox" name="gift"></label>
      <fieldset><legend>Speed</legend>
        <label><input type="radio" name="speed" value="std" checked> Standard</label>
        <label><input type="radio" name="speed" value="fast"> Fast</label>
      </fieldset>
      <label>Extras <select name="extras" multiple><option>Bag</option><option>Card</option></select></label>
      <label>Coupon <input name="coupon" disabled></label>
      <button type="submit">Pay</button>
    </form>
    """
    [schema] = lift_html(html, profile="agent").form_tool_schemas()
    props = schema["input_schema"]["properties"]

    assert schema["name"] == "checkout" and schema["x-method"] == "POST" and schema["x-action"] == "/pay"
    assert len(schema["x-submit-node-ids"]) == 1
    assert props["email"]["type"] == "string" and props["email"]["format"] == "email"
    assert props["qty"] == {**props["qty"], "type": "number", "default": "1"}
    assert props["gift"]["type"] == "boolean" and props["gift"]["default"] is False
    assert props["speed"]["enum"] == ["std", "fast"] and props["speed"]["default"] == "std"
    assert props["speed"]["description"] == "Speed"
    assert set(props["speed"]["x-node-ids"]) == {"std", "fast"}
    assert props["extras"]["type"] == "array" and props["extras"]["items"]["enum"] == ["Bag", "Card"]
    assert "coupon" not in props
    assert schema["input_schema"]["required"] == ["email"]


def test_aria_widgets_and_snapshot_hints_are_controls():
    controls = _controls(
        """
        <div role="button" tabindex="0">Load more</div>
        <div tabindex="0">Focusable card</div>
        <span data-lift-clickable="true">Export</span>
        <div role="tab">Settings</div>
        <div contenteditable="true" aria-label="Comment"></div>
        """
    )

    assert controls["Load more"]["control"] == "button"
    assert controls["Focusable card"]["control"] == "focusable"
    assert controls["Export"]["control"] == "clickable"
    assert controls["Settings"]["control"] == "tab"
    assert controls["Comment"]["kind"] == "field" and controls["Comment"]["control"] == "textbox"


def test_hidden_subtrees_are_left_out():
    controls = _controls(
        """
        <div hidden><button>Hidden attr</button></div>
        <div aria-hidden="true"><button>Aria hidden</button></div>
        <div data-lift-hidden="true"><a href="/x">Snapshot hidden</a></div>
        <div data-lift-hidden="false"><button>Shown</button></div>
        """
    )

    assert set(controls) == {"Shown"}


def test_hidden_unwrapped_elements_drop_their_content():
    controls = _controls(
        """
        <dialog data-lift-hidden="true"><button>Confirm</button></dialog>
        <my-menu hidden><a href="/a">Secret menu</a></my-menu>
        <my-panel><button>Visible</button></my-panel>
        <after-void data-lift-hidden="true"/><button>After</button>
        """
    )

    assert set(controls) == {"Visible", "After"}


def test_nested_controls_count_once():
    lift = lift_html('<a href="/p"><button>Buy</button></a>', profile="agent")
    assert [(control.control, control.name) for control in lift.controls] == [("link", "Buy")]


def test_content_roles_skip_control_text():
    lift = lift_html(
        "<h1>Order</h1><p>$10.00</p><select><option>Price</option></select><button>Pay</button>", profile="agent"
    )
    roles = {(role.role, role.text) for role in lift.roles}

    assert ("heading", "Order") in roles and ("value", "$10.00") in roles
    assert ("action", "Pay") in roles and ("field", "") in roles
    assert not any(text == "Price" for _, text in roles)


def test_viewspec_action_ids_are_kept():
    lift = lift_html(
        '<button data-action-id="refund" data-action-kind="submit" data-ir-id="a1">Refund</button>', profile="agent"
    )
    [control] = lift.controls
    assert control.action_id == "refund"
    assert lift.nodes[1].attrs["data-action-kind"] == "submit"


def test_unsafe_markup_is_still_removed():
    lift = lift_html(
        """
        <form action="javascript:alert(1)"><input name="a" onfocus="steal()" style="x"></form>
        <a href="javascript:alert(1)">Bad</a><script>alert(1)</script>
        <button formaction="https://evil.example">Go</button>
        """,
        profile="agent",
    )
    payload = json.dumps(lift.to_json())

    assert "javascript" not in payload and "steal" not in payload and "evil.example" not in payload
    assert "alert" not in payload
    assert lift.forms[0].action is None
    codes = {item["code"] for item in lift.diagnostics}
    assert {"HTML_URL_STRIPPED", "HTML_ATTR_STRIPPED", "HTML_TAG_STRIPPED"} <= codes


@pytest.mark.parametrize(
    "field",
    [
        '<input type="password" value="{secret}">',
        '<input type="PASSWORD" value="{secret}">',
        '<input name="api_token" value="{secret}">',
        '<input id="cardNumber" value="{secret}">',
        '<input autocomplete="cc-number" value="{secret}">',
        '<input autocomplete="section-a one-time-code" value="{secret}">',
        '<input type="hidden" name="csrf" value="{secret}">',
        '<textarea name="client_secret">{secret}</textarea>',
    ],
)
def test_secret_values_are_never_kept(field):
    secret = "s3cr3t-Value-91"
    lift = lift_html(f"<form>{field}</form>", profile="agent")

    assert secret not in json.dumps(lift.to_json())
    assert secret not in json.dumps(lift.form_tool_schemas())
    codes = {item["code"] for item in lift.diagnostics}
    assert codes & {"HTML_VALUE_REDACTED", "HTML_HIDDEN_INPUT_DROPPED"}


@settings(max_examples=60, deadline=None)
@given(
    secret=st.text(alphabet=st.characters(min_codepoint=48, max_codepoint=122), min_size=12, max_size=40),
    wrapper=st.sampled_from(["{f}", "<label>Pass {f}</label>", "<div><form>{f}</form></div>", "<p>{f}</p>"]),
)
def test_password_values_never_leak(secret, wrapper):
    field = f'<input type="password" name="p" value="{secret}">'
    lift = lift_html(wrapper.format(f=field), profile="agent")

    assert secret not in json.dumps(lift.to_json())


def test_agent_lift_ids_and_fingerprint_are_deterministic():
    html = '<form><label>A <input name="a" value="1"></label><button>Go</button></form>'
    first = lift_html(html, profile="agent")
    second = lift_html(html.replace('value="1"', 'value="2"'), profile="agent")

    assert [c.node_id for c in first.controls] == [c.node_id for c in second.controls]
    assert first.topology_fingerprint.hex == second.topology_fingerprint.hex
    assert first.source_hash != second.source_hash
