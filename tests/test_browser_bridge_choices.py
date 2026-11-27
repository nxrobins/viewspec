"""Choice contracts through the real bridge; the fixture server judges persisted effects."""

from __future__ import annotations

import os
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.test_browser_bridge_e2e import BODIES, PROBE, RECEIVED, _QuietHandler, handle
from tests.test_browser_bridge_e2e import site as site

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get("VIEWSPEC_RUN_BROWSER_BRIDGE_E2E") != "1", reason="opt-in browser checks"),
]


@pytest.fixture
def browser(site, monkeypatch):
    monkeypatch.syspath_prepend(str(PROBE))
    from boundary import Boundary
    from mcp_browser_server import BrowserTools

    tools = BrowserTools(
        boundary=Boundary.build(allow_origin=(site,), allow_read=(site + "/*",), allow_writes=True, private="heuristic")
    )
    RECEIVED.clear()
    BODIES.clear()
    try:
        yield tools
    finally:
        tools.close()


def select(browser, page, values):
    return browser.act(
        action="select", handle=handle(page, "Labels"), value=values, observation_id=page["observation_id"]
    )


def test_custom_choices_batch_and_idempotent_repeat(browser, site):
    page = browser.observe(url=site + "/custom_choices.html")
    result = browser.call(
        tool="issue",
        arguments={"title": "Audit", "labels": ["operations", "urgent"]},
        submit=False,
        observation_id=page["observation_id"],
    )
    assert result["ok"], result
    assert result["steps"][-1]["verified"] is True
    assert result["steps"][-1]["selection"]["completion"] == "form_pending"
    assert "Clicks 2" in result["view"]
    repeated = select(browser, result, ["urgent", "operations"])
    assert repeated["ok"], repeated
    assert repeated["steps"][0]["selection"]["changed"] is False
    assert "Clicks 2" in repeated["view"]
    done = browser.call(tool="issue", arguments={}, submit=True, observation_id=repeated["observation_id"])
    assert done["ok"], done
    assert parse_qs(BODIES["/choice-created"]) == {"title": ["Audit"], "labels": ["operations,urgent"]}


def test_clear_and_omission_preserve_exact_semantics(browser, site):
    page = browser.observe(url=site + "/custom_choices.html")
    selected = select(browser, page, ["operations"])
    kept = browser.call(
        tool="issue", arguments={"title": "Kept"}, submit=False, observation_id=selected["observation_id"]
    )
    assert "= 'operations'" in kept["view"]
    cleared = select(browser, kept, [])
    assert cleared["ok"], cleared
    assert cleared["steps"][0]["selection"]["selected"] == []
    assert "Clicks 2" in cleared["view"]


def test_close_applies_once_and_survives_sidebar_replacement(browser, site):
    page = browser.observe(url=site + "/custom_choices.html?save")
    selected = select(browser, page, ["operations"])
    assert selected["ok"], selected
    assert BODIES["/choice-save"] == "operations"
    repeated = select(browser, selected, ["operations"])
    assert repeated["ok"], repeated
    assert RECEIVED.count("POST /choice-save") == 1


@pytest.mark.parametrize("values", [["locked"], ["red", "blue"], ["missing"]])
def test_invalid_requested_set_does_not_partially_apply(browser, site, values):
    page = browser.observe(url=site + "/custom_choices.html?save")
    refused = select(browser, page, ["operations", *values])
    assert not refused["ok"], refused
    assert "Clicks 0" in refused["view"]
    assert not RECEIVED


@pytest.mark.parametrize("query", ["route", "cover", "partial", "duplicate", "portal-swap"])
def test_unknown_or_changed_targets_refuse_without_side_effect(browser, site, query):
    page = browser.observe(url=site + "/custom_choices.html?" + query)
    refused = select(browser, page, ["operations"])
    assert not refused["ok"], refused
    assert not RECEIVED
    assert "Clicks 0" in refused.get("view", ""), refused


def test_route_change_after_first_option_stops_the_rest_of_the_batch(browser, site):
    page = browser.observe(url=site + "/custom_choices.html?route-on-option")
    refused = browser.call(
        tool="issue",
        arguments={"title": "Changed", "labels": ["operations", "urgent"]},
        submit=True,
        observation_id=page["observation_id"],
    )
    assert not refused["ok"], refused
    assert "Clicks 1" in refused["view"]
    assert refused["progress"]["submission"] == "withheld"
    assert not RECEIVED


def test_unverified_choice_stops_batch_before_submit(browser, site):
    page = browser.observe(url=site + "/custom_choices.html?ignore")
    refused = browser.call(
        tool="issue",
        arguments={"title": "Changed", "labels": ["operations"]},
        submit=True,
        observation_id=page["observation_id"],
    )
    assert not refused["ok"], refused
    assert refused["progress"]["submission"] == "withheld"
    assert [f["status"] for f in refused["progress"]["fields"]] == ["verified", "failed", "not_run"]
    assert not RECEIVED


def test_failed_lookup_does_not_close_and_commit_a_staged_edit(browser, site):
    page = browser.observe(url=site + "/custom_choices.html?save")
    selected = select(browser, page, ["operations"])
    opened = browser.act(action="click", handle=handle(selected, "Labels"), observation_id=selected["observation_id"])
    cleared = browser.act(
        action="click", handle=handle(opened, "Clear labels"), observation_id=opened["observation_id"]
    )
    refused = select(browser, cleared, ["missing"])
    assert not refused["ok"]
    assert refused["progress"]["fields"][0]["selection"]["rollback"] == "not_attempted"
    assert "Filter Label" in refused["view"]
    assert BODIES["/choice-save"] == "operations"
    assert RECEIVED.count("POST /choice-save") == 1


@pytest.mark.parametrize(
    ("name", "value"), [("Country", "Jap"), ("Pickup city", "Lis"), ("Colour", "Pur"), ("Gift wrap", "box")]
)
def test_existing_custom_choice_patterns_still_verify(browser, site, name, value):
    page = browser.observe(url=site + "/checkout.html")
    chosen = browser.act(action="select", handle=handle(page, name), value=value, observation_id=page["observation_id"])
    assert chosen["ok"], chosen
    assert chosen["steps"][0]["verified"] is True


def test_delayed_replaced_typeahead_still_navigates(browser, site):
    page = browser.observe(url=site + "/search.html")
    chosen = browser.act(
        action="select",
        handle=handle(page, "Search Wikipedia"),
        value="Alan Turing",
        observation_id=page["observation_id"],
    )
    assert chosen["ok"], chosen
    assert chosen["url"].endswith("/wiki/Alan_Turing")


@pytest.mark.parametrize("submit", [False, True])
def test_form_preparation_prevents_native_suggestion_submit(browser, site, monkeypatch, submit):
    searches = []
    original = _QuietHandler.do_GET

    def record_search(handler):
        if urlsplit(handler.path).path == "/search":
            searches.append(handler.path)
        original(handler)

    monkeypatch.setattr(_QuietHandler, "do_GET", record_search)
    page = browser.observe(url=site + "/shop.html")
    refused = browser.call(
        tool=page["form_tools"][0]["name"],
        arguments={"q": "Clamp lamp"},
        submit=submit,
        observation_id=page["observation_id"],
    )
    assert not refused["ok"], refused
    assert refused["errors"][0]["code"] == "BROWSER_FORM_UNKNOWN"
    assert refused["progress"]["submission"] == ("withheld" if submit else "not_requested")
    assert refused["progress"]["preparation"]["prevented_submissions"] == 1
    assert refused["progress"]["preparation"]["native_submissions"] == 0
    assert refused["url"].endswith("/shop.html")
    assert not searches

    chosen = browser.act(
        action="select",
        handle=handle(refused, "Search products"),
        value="Clamp lamp",
        observation_id=refused["observation_id"],
    )
    assert chosen["ok"] and chosen["navigated"], chosen
    assert searches == ["/search?q=Clamp+lamp&sort=Price"]


def test_multi_choice_defaults_are_arrays(browser, site):
    page = browser.observe(url=site + "/custom_choices.html")
    selected = select(browser, page, ["operations"])
    field = selected["form_tools"][0]["input_schema"]["properties"]["labels"]
    assert field["default"] == ["operations"]


def test_live_unsupported_popup_keeps_manual_controls_and_prose(browser, site):
    page = browser.observe(url=site + "/custom_choices.html?unsupported")
    opened = browser.act(action="click", handle=handle(page, "Labels"), observation_id=page["observation_id"])
    assert "choices: unsupported" in opened["view"]
    assert "Choose all labels needed" in opened["view"]
    assert "Filter Label" in opened["view"] and "Label help" in opened["view"]
    assert set(opened["form_tools"][0]["input_schema"]["properties"]) == {"title"}
    picked = browser.act(action="click", handle=handle(opened, "operations"), observation_id=opened["observation_id"])
    assert picked["ok"] and "Clicks 1" in picked["view"]


def test_page_authored_descriptor_cannot_replace_live_choice_facts(browser, site):
    page = browser.observe(url=site + "/custom_choices.html?spoof")
    assert "Forged" not in page["view"]
    assert page["form_tools"][0]["input_schema"]["properties"]["labels"]["type"] == "array"
    picked = select(browser, page, ["operations"])
    assert picked["ok"] and picked["steps"][0]["verified"] is True


def test_nested_edit_control_opens_saved_color_without_following_parent_link(browser, site):
    page = browser.observe(url=site + "/choice_actions.html")
    edited = browser.act(action="click", handle=handle(page, "Edit Label"), observation_id=page["observation_id"])
    assert edited["ok"], edited
    assert "#c65d21" in edited["view"]
    assert edited["url"].endswith("/choice_actions.html")
    assert not RECEIVED


def test_fill_then_select_refreshes_search_and_adds_existing_without_creating(browser, site):
    page = browser.observe(url=site + "/choice_actions.html")
    filled = browser.act(
        action="fill", handle=handle(page, "Labels"), value="Equipment review", observation_id=page["observation_id"]
    )
    assert "[create]" in filled["view"] and "Add this as new label" in filled["view"]
    chosen = select(browser, filled, "Equipment review")
    assert chosen["ok"], chosen
    assert chosen["steps"][0]["selection"]["selected"] == ["Existing", "Equipment review"]
    assert chosen["steps"][0]["selection"]["operation"] == "add"
    assert BODIES["/choice-attach"] == "existing"
    assert "POST /choice-create" not in RECEIVED
    repeated = select(browser, chosen, "Equipment review")
    assert repeated["ok"] and not repeated["steps"][0]["selection"]["changed"]
    assert RECEIVED.count("POST /choice-attach") == 1


def test_form_call_uses_the_same_add_contract_and_verifies_before_submit(browser, site):
    page = browser.observe(url=site + "/choice_actions.html")
    result = browser.call(
        tool="assignment", arguments={"labels": "Equipment review"}, submit=True, observation_id=page["observation_id"]
    )
    assert result["ok"], result
    assert BODIES["/choice-form"] == "Existing,Equipment review"
    assert RECEIVED.count("POST /choice-attach") == 1 and "POST /choice-create" not in RECEIVED


@pytest.mark.parametrize(
    "query", ["missing", "missing&localized", "duplicate", "generic", "change-meaning", "change-query"]
)
def test_unsafe_or_ambiguous_candidate_never_activates(browser, site, query):
    page = browser.observe(url=site + "/choice_actions.html?" + query)
    refused = select(browser, page, "Equipment review")
    assert not refused["ok"], refused
    assert not RECEIVED
    assert "Activations 0" in refused["view"]


def test_create_action_stays_available_for_explicit_activation(browser, site):
    page = browser.observe(url=site + "/choice_actions.html?missing")
    refused = select(browser, page, "Equipment review")
    assert not refused["ok"]
    created = browser.act(
        action="click",
        handle=handle(refused, "Equipment review Add this as new label"),
        observation_id=refused["observation_id"],
    )
    assert created["ok"], created
    assert RECEIVED.count("POST /choice-create") == 1


@pytest.mark.parametrize("query", ["noop", "replace"])
def test_unverified_or_destructive_choice_stops_form_and_warns_about_prior_effects(browser, site, query):
    page = browser.observe(url=site + "/choice_actions.html?" + query)
    refused = browser.call(
        tool="assignment", arguments={"labels": "Equipment review"}, submit=True, observation_id=page["observation_id"]
    )
    assert not refused["ok"], refused
    assert RECEIVED.count("POST /choice-attach") == 1
    assert "POST /choice-form" not in RECEIVED and "POST /choice-create" not in RECEIVED
    assert refused["outcome"]["retry"] == "may_repeat"
    assert "before another mutation" in refused["next_actions"][0]


@pytest.mark.parametrize("query", ["retiring&empty", "retiring"])
def test_retiring_popup_keeps_verification_bound_to_the_original_contract(browser, site, query):
    page = browser.observe(url=site + "/choice_actions.html?" + query)
    chosen = select(browser, page, "Equipment review")
    assert chosen["ok"], chosen
    step = chosen["steps"][0]
    assert step["verified"] is True
    expected = ["Equipment review"] if "empty" in query else ["Existing", "Equipment review"]
    assert step["selection"]["selected"] == expected
    assert RECEIVED.count("POST /choice-attach") == 1 and "POST /choice-create" not in RECEIVED


def test_losing_the_contract_after_activation_reports_unknown_not_a_generic_match(browser, site):
    page = browser.observe(url=site + "/choice_actions.html?empty&contract-loss")
    refused = select(browser, page, "Equipment review")
    assert not refused["ok"], refused
    assert refused["steps"][0]["verified"] is None
    assert refused["errors"][0]["code"] == "BROWSER_FORM_UNKNOWN"
    assert refused["outcome"]["retry"] == "may_repeat"
    assert RECEIVED.count("POST /choice-attach") == 1 and "POST /choice-create" not in RECEIVED
