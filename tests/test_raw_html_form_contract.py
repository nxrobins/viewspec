"""A form's desired state survives no-op planning and typed normalization."""

import pytest

from viewspec.raw_html import lift_html as _lift_html
from viewspec.raw_html_actions import AgentActionError, FormPlan, plan_form_call


def lift_html(html):
    return _lift_html(html, profile="agent")


def test_plan_retains_noop_and_omitted_intent_separately_from_steps():
    lift = lift_html("""<form aria-label="Edit"><input name="token" type="hidden" value="protocol">
        <input aria-label="Name" name="name" value="Old"><button name="yes" type="button" role="checkbox" aria-checked="true">Yes</button>
        <button>Save</button></form>""")
    plan = plan_form_call(lift, "edit", {"yes": True}, submit=False)
    assert isinstance(plan, FormPlan) and plan == ()
    assert [(f.key, f.kind, f.preserve, f.expected) for f in plan.intents] == [
        ("name", "text", True, None),
        ("yes", "boolean", False, True),
    ]


def test_color_schema_and_plan_share_explicit_srgb_hex_contract():
    lift = lift_html('<form aria-label="Edit"><input type="color" name="color"></form>')
    schema = lift.form_tool_schemas()[0]["input_schema"]["properties"]["color"]
    assert schema["type"] == "string" and schema["pattern"] == "^#[0-9a-fA-F]{6}$"
    plan = plan_form_call(lift, "edit", {"color": "#2878B5"}, submit=False)
    assert plan[0].value == plan.intents[0].expected == "#2878b5"
    for value in ["red", "#abc", "#abcd1234", "invalid", "", None]:
        with pytest.raises(AgentActionError):
            plan_form_call(lift, "edit", {"color": value})


def test_date_pair_keeps_both_endpoints_and_secret_intent_is_not_in_repr():
    lift = lift_html("""<form aria-label="Edit"><label>Start date<input name="start" readonly></label>
        <label>End date<input name="end" readonly></label><input type="password" name="password"></form>""")
    plan = plan_form_call(lift, "edit", {"start": "2026-12-01", "end": "2026-12-18", "password": "private-test-value"})
    assert [(f.kind, f.expected) for f in plan.intents[:2]] == [("date", "2026-12-01"), ("date", "2026-12-18")]
    assert "private-test-value" not in repr(plan.intents)


def test_contract_protocol_words_survive_secret_collisions(monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "experiments/browser-agent-probe"))
    from boundary import PrivacyRedactor

    privacy = PrivacyRedactor(mode="heuristic")
    privacy.remember(["matched", "withheld", "contract"])
    result = privacy.clean(
        {"contract": [{"index": 1, "status": "matched"}], "submission": "withheld", "message": "matched"}
    )
    assert result["contract"][0]["status"] == "matched"
    assert result["submission"] == "withheld"
    assert result["message"] == "[redacted]"
