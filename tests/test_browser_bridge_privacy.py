"""Benign privacy regressions at the browser bridge's public output boundary."""

from __future__ import annotations

import base64
import importlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

PROBE = Path(__file__).resolve().parents[1] / "experiments" / "browser-agent-probe"
VALUE = "sample-private-value-824"


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(PROBE))
    return SimpleNamespace(
        **{name: importlib.import_module(name) for name in ("boundary", "agent_session", "mcp_browser_server")}
    )


def session_for(modules, source: str, *, mode: str = "strict"):
    session = object.__new__(modules.agent_session.BrowserSession)
    session.privacy = modules.boundary.PrivacyRedactor(mode)
    session._readings = {}
    session._reading_seq = 0
    session._observation = None
    snap = {
        "html": source,
        "url": "https://shop.example/form",
        "title": "Account",
        "interactive": [],
        "actionIds": 0,
        "docId": "document-a",
        "navigationId": 1,
        "recorder": {"notes": [], "seq": 0},
        "activity": {"mutations": 0, "requests": 0, "pending": []},
        "secrets": [],
    }
    session._call = lambda cmd, **kwargs: snap if cmd == "snapshot" else {}
    return session


def test_strict_is_the_declared_default_and_heuristic_is_explicit(modules):
    default = modules.boundary.Boundary.build()
    assert default.private == "strict"
    assert "arbitrary secrets in prose" in default.privacy_description()
    assert modules.boundary.Boundary.build(private="heuristic").private == "heuristic"
    with pytest.raises(modules.boundary.BoundaryError):
        modules.boundary.Boundary.build(private="off")


def test_projection_covers_nested_keys_urls_and_common_encodings(modules):
    privacy = modules.boundary.PrivacyRedactor("heuristic")
    privacy.remember([VALUE])
    item = {
        VALUE: [VALUE, quote(VALUE), base64.b64encode(VALUE.encode()).decode()],
        "url": "https://shop.example/?token=sample-url-credential&sort=asc",
        "message": "sample-url-credential",
        "handle": 12,
        "count": 4,
    }
    public = privacy.clean(item)
    assert VALUE not in json.dumps(public)
    assert "sample-url-credential" not in json.dumps(public)
    assert "sort=asc" in public["url"]
    assert public["handle"] == 12 and public["count"] == 4
    assert VALUE in item, "projection must not mutate planning data"


def test_strict_policy_withholds_even_ordinary_url_parameter_values(modules):
    privacy = modules.boundary.PrivacyRedactor()
    result = privacy.clean({"url": "https://shop.example/?q=ordinary-search#tab=account", "handle": 2})
    assert "ordinary-search" not in result["url"] and "account" not in result["url"]
    assert result["handle"] == 2


def test_strict_hides_opaque_query_and_fragment_across_outputs(modules):
    privacy = modules.boundary.PrivacyRedactor()
    value = {"summary": "ticket-842 opened", "url": "https://shop.example/cart?handle-100#ticket-842"}
    result = privacy.clean(value)
    assert result["url"] == "https://shop.example/cart?[redacted]#[redacted]"
    assert result["summary"] == "[redacted] opened"


def test_private_value_budget_refuses_instead_of_truncating(modules, monkeypatch):
    monkeypatch.setattr(modules.boundary, "MAX_PRIVATE_VALUES", 1)
    privacy = modules.boundary.PrivacyRedactor()
    privacy.remember(["first-private-value"])
    with pytest.raises(modules.boundary.BoundaryError, match="Privacy value limit"):
        privacy.remember(["second-private-value"])
    with pytest.raises(modules.boundary.PrivacyLimit):
        privacy.clean("first-private-value")


def test_short_native_value_is_hidden_without_damaging_keys_or_handles(modules):
    session = session_for(modules, '<label>Count<input name="count" value="1"></label><button>Continue</button>')
    public = session.observe(locate=False)
    assert public.view.startswith("[1] text 'Count' = '[redacted]'")
    assert 'value="1"' not in public.html
    assert public.handles[1].value == "[redacted]"
    session.privacy.remember(["view"])
    result = session.privacy.clean({"view": "view", "observation_id": 1, "form_fields": {"view": "view"}})
    assert result["view"] == "[redacted]" and result["observation_id"] == 1
    assert result["form_fields"] == {"[redacted]": "[redacted]"}


def test_native_value_is_hidden_before_preview_and_raw_plan_is_preserved(modules):
    value = "sample long phrase " * 12
    session = session_for(modules, f'<label>Notes<input name="notes" value="{value}"></label><button>Continue</button>')
    public = session.observe(locate=False)
    assert value[:70] not in public.view
    assert "[redacted]" in public.view
    assert public.secrets == []
    raw = session._resolve_observation(public)
    assert raw.handles[1].value == value.strip()
    assert raw.handles[2].name == "Continue"
    assert public.handles.keys() == raw.handles.keys()


def test_heuristic_keeps_ordinary_text_but_known_values_remain_private(modules):
    session = session_for(
        modules, '<label>Notes<input name="notes" value="Quarterly report"></label>', mode="heuristic"
    )
    assert "Quarterly report" in session.observe(locate=False).view
    session.privacy.remember(["Quarterly report"])
    assert "Quarterly report" not in session.observe(locate=False).view


def test_page_false_marker_does_not_declassify_strict_field(modules):
    session = session_for(modules, f'<input name="notes" data-lift-secret="false" value="{VALUE}">')
    assert VALUE not in session.observe(locate=False).view


def test_projected_reading_resolves_to_real_names_for_actions(modules):
    session = session_for(modules, f'<input name="notes" value="{VALUE}"><button>{VALUE}</button>')
    public = session.observe(locate=False)
    assert VALUE not in public.view
    assert session._resolve_observation(public).handles[2].name == VALUE
    assert public.handles[2].name == "[redacted]"


def test_private_schema_keys_receive_distinct_callable_aliases(modules):
    first, second = "sample_private_one", "sample_private_two"
    session = session_for(modules, f'<form aria-label="Example"><input name="{first}"><input name="{second}"></form>')
    raw = session._observe(locate=False)
    tools = modules.mcp_browser_server.BrowserTools()
    tools.privacy = session.privacy
    tools.privacy.remember([first, second])
    [(original, shown, aliases)] = tools._schema_pairs(raw)
    assert len(shown["input_schema"]["properties"]) == 2
    assert set(aliases.values()) == {first, second}
    public_arguments = dict.fromkeys(aliases, "ordinary supplied value")
    tool, arguments = tools._resolve_public_call(raw, shown["name"], public_arguments)
    assert tool == original["name"] and set(arguments) == {first, second}


def test_public_journal_omits_raw_bodies_and_retains_request_state(modules):
    session = session_for(modules, "<p>Account</p>")
    session.privacy.remember([VALUE])
    session._records = lambda: {
        "writes": [
            {
                "method": "POST",
                "url": f"https://shop.example/?token={VALUE}",
                "state": "blocked",
                "post_data": "ordinary=sample",
                "post_data_b64": "b3JkaW5hcnk9c2FtcGxl",
            }
        ],
        "passed_writes": [
            {"method": "PUT", "url": "https://shop.example/save", "state": "passed", "post_data": "ordinary=sample"}
        ],
        "navigations": [],
    }
    public = session.records()
    assert "post_data" not in json.dumps(public)
    assert VALUE not in json.dumps(public)
    assert public["writes"][0]["state"] == "blocked"
    assert public["passed_writes"][0]["state"] == "passed"
    assert public["writes"][0]["body_bytes"] == len("ordinary=sample")


def test_mcp_paging_filters_the_projected_view(modules):
    session = session_for(modules, f'<label>Notes<input name="notes" value="{VALUE}"></label><button>Continue</button>')
    raw = session._observe(locate=False)
    tools = modules.mcp_browser_server.BrowserTools()
    tools.privacy = session.privacy
    tools._session = session
    result, _ = tools._view_data(raw, 1, find=VALUE)
    assert result["view_page"]["matches"] == 0
    assert VALUE not in result["view"]


@pytest.mark.parametrize("mode", ["strict", "heuristic"])
def test_mcp_prose_is_redacted_before_paging_and_find(modules, mode):
    source = (
        f'<h1>Reading</h1><input type="password" value="{VALUE}">'
        f"<p>Private echo: <strong>{VALUE}</strong>. Public ending: amber fox.</p>"
    )
    session = session_for(modules, source, mode=mode)
    raw = session._observe(locate=False)
    tools = modules.mcp_browser_server.BrowserTools()
    tools.privacy, tools._session = session.privacy, session
    found, _ = tools._view_data(raw, 1, find="amber fox")
    assert found["view_page"]["matches"] == 1
    assert "Private echo: [redacted]. Public ending: amber fox." in found["view"]
    assert VALUE not in json.dumps(found)
    hidden, _ = tools._view_data(raw, 1, find=VALUE)
    assert hidden["view_page"]["matches"] == 0 and not hidden["view"]
    last, _ = tools._view_data(raw, 1, offset=2, limit=1)
    assert last["view"] == "text Private echo: [redacted]. Public ending: amber fox."


def test_mcp_exception_uses_the_same_projection(modules):
    tools = modules.mcp_browser_server.BrowserTools()
    tools.privacy.remember([VALUE])

    def fails(self):
        raise ValueError(f"Unable to read {VALUE}")

    result = modules.mcp_browser_server._sealed(fails)(tools)
    assert result["ok"] is False
    assert VALUE not in json.dumps(result)


def test_heuristic_write_visibility_is_scoped_to_the_current_action(modules):
    from viewspec.raw_html import lift_html

    session = session_for(modules, '<input name="note" value="Visible earlier">', mode="heuristic")
    before = session._observe(locate=False)
    current = replace(
        before,
        doc_id="document-b",
        navigation_id=2,
        view="value Done",
        lines=[],
        handles={},
        lift=lift_html("<p>Done</p>", profile="agent"),
    )
    session._observe = lambda: current
    body = "note=Visible+earlier&other=Invisible+new+value&choice=Chosen+by+agent"
    session._records = lambda: {
        "writes": [
            {
                "method": "POST",
                "url": "https://shop.example/save",
                "state": "blocked",
                "post_data": body,
                "content_type": "application/x-www-form-urlencoded",
            }
        ],
        "navigations": [],
        "dialogs": [],
        "popups": [],
    }
    tools = modules.mcp_browser_server.BrowserTools(boundary=modules.boundary.Boundary.build(private="heuristic"))
    tools.privacy, tools._session = session.privacy, session
    tools._write_source, tools._said_for_write = before, "Chosen by agent"
    public = tools._observe_data(since=before)
    assert public["blocked_writes"][0]["form_fields"] == {
        "note": "Visible earlier",
        "other": "[not shown]",
        "choice": "Chosen by agent",
    }
    # A later observation may compare the old reading, but it cannot authorize old field names.
    tools._reported["writes"] = 0
    later = tools._observe_data(since=before)
    assert later["blocked_writes"][0]["form_fields"]["note"] == "[not shown]"


def test_diff_redacts_complete_value_before_its_preview(modules):
    value = "sample-long-diff-value-" * 12
    session = session_for(modules, f'<label>Notes<input name="notes" value="{value}"></label>')
    after = session._observe(locate=False)
    before = replace(after, title="Before")
    result = modules.agent_session.changes(before, after, session.privacy)
    assert value[:70] not in json.dumps(result)
    assert session._resolve_observation(after).handles[1].value == value


@pytest.mark.parametrize("media_type", ["application/json", "text/plain", "application/octet-stream", None])
def test_non_form_body_with_equals_is_size_only(modules, media_type):
    tools = modules.mcp_browser_server.BrowserTools(boundary=modules.boundary.Boundary.build(private="heuristic"))
    body = '{"note":"fixture-private=value"}'
    result = tools._redact_write(
        {"method": "POST", "url": "https://example.test/save", "post_data": body, "content_type": media_type}
    )
    assert result == {"method": "POST", "url": "https://example.test/save", "body_bytes": len(body)}


def test_fixed_policy_states_survive_a_matching_field_value(modules):
    privacy = modules.boundary.PrivacyRedactor()
    privacy.remember(["blocked", "safe"])
    result = privacy.clean({"outcome": {"status": "blocked", "retry": "safe"}, "state": "blocked", "view": "blocked"})
    assert result == {"outcome": {"status": "blocked", "retry": "safe"}, "state": "blocked", "view": "[redacted]"}


def test_direct_session_rejects_foreign_reading_with_colliding_number(modules):
    session = session_for(modules, "<button>Next</button>")
    valid = session.observe(locate=False)
    with pytest.raises(modules.agent_session.BridgeError, match="stale-view"):
        session._resolve_observation(replace(valid, doc_id="foreign-document"))


@pytest.mark.parametrize(
    "href",
    [
        "/build/images/icons.svg#down",
        "https://cdn.example/icons.svg#down",
        "icons.svg#down",
        "#down",
        "/icons.svg?version=asset-482#down",
        "/icons.svg#%64own",
    ],
)
@pytest.mark.parametrize("attribute", ["href", "xlink:href"])
def test_svg_symbol_fragment_does_not_become_a_prose_secret(modules, href, attribute):
    title = "My PC is downstairs, and my TV is upstairs."
    source = f'<h1>{title}</h1><svg><use {attribute}="{href}"/></svg><p>Searching downstairs.</p>'
    session = session_for(modules, source)
    public = session.observe(locate=False)
    assert title in public.view and "Searching downstairs." in public.view
    assert title in public.html
    tools = modules.mcp_browser_server.BrowserTools()
    tools.privacy, tools._session = session.privacy, session
    raw = session._resolve_observation(public)
    found, _ = tools._view_data(raw, 1, find="downstairs", limit=1)
    assert found["view_page"]["matches"] == 1 and title in found["view"]
    second, _ = tools._view_data(raw, 1, find="downstairs", offset=1, limit=1)
    assert "Searching downstairs." in second["view"]
    assert session.privacy.clean(title) == title, "projection must not relearn the public symbol"


@pytest.mark.parametrize("credential_source", ["operator", "password", "marked", "query", "navigation", "svg-query"])
@pytest.mark.parametrize("mode", ["strict", "heuristic"])
def test_svg_reference_cannot_declassify_a_known_credential(modules, credential_source, mode):
    value = "known-credential-482"
    source = f'<svg><use href="/icons.svg#{value}"/></svg><p>Echo: {value}</p>'
    if credential_source in {"password", "marked"}:
        attrs = 'type="password"' if credential_source == "password" else 'data-lift-secret="true"'
        source += f'<input {attrs} value="{value}">'
    elif credential_source == "query":
        source += f'<a href="/account?token={value}">Account</a>'
    elif credential_source == "svg-query":
        source += f'<svg><use href="/icons.svg?token={value}#down"/></svg>'
    session = session_for(modules, source, mode=mode)
    if credential_source == "operator":
        session.privacy.remember([value])
    if credential_source == "navigation":
        # Bare fragments are private in strict mode; heuristic mode recognizes named credentials.
        url = (
            f"https://shop.example/icons.svg#{value}"
            if mode == "strict"
            else f"https://shop.example/icons.svg#token={value}"
        )
        session.privacy.learn({"url": url})
    public = session.observe(locate=False)
    assert value not in public.view and value not in public.html
    assert session.privacy.clean(f"Echo: {value}") == "Echo: [redacted]"


@pytest.mark.parametrize(
    "source",
    [
        '<a href="/icons.svg#down">Account</a>',
        '<use href="/icons.svg#down"></use>',
        '<svg><a href="/icons.svg#down">Account</a></svg>',
        '<svg><use href="/account#down"/></svg>',
        '<svg><use href="/icons.svg#token=down"/></svg>',
        '<svg><use href="/icons.svg#public" data-account="/account#down"/></svg>',
    ],
)
def test_svg_exception_does_not_cover_other_urls_or_parameters(modules, source):
    privacy = modules.boundary.PrivacyRedactor()
    privacy.learn(source)
    assert privacy.clean("downstairs") == "[redacted]stairs"


def test_svg_fragment_seen_later_as_credential_is_still_learned(modules):
    privacy = modules.boundary.PrivacyRedactor()
    privacy.learn('<svg><use href="/icons.svg#down"/></svg>')
    assert privacy.clean("downstairs") == "downstairs"
    privacy.learn({"url": "https://shop.example/account#down"})
    assert privacy.clean("downstairs") == "[redacted]stairs"


@pytest.mark.parametrize(
    "source",
    [
        "<SVG>\n<USE title='literal href=\"/account#real-ticket-482\"' XLINK:HREF='/icons.svg#down'/></SVG>",
        "<svg><use href=/icons.svg#down ></use></svg>",
        '<svg><svg><use href="/icons.svg#down"/></svg><use href="/icons.svg#search"/></svg>',
    ],
)
def test_svg_markup_forms_keep_public_words_and_other_attributes_private(modules, source):
    privacy = modules.boundary.PrivacyRedactor()
    privacy.learn(source)
    shown = privacy.project_html(source)
    assert privacy.clean("downstairs research") == "downstairs research"
    assert "real-ticket-482" not in shown
    if "real-ticket-482" in source:
        assert privacy.clean("real-ticket-482") == "[redacted]"


def test_malformed_markup_does_not_hide_credential_sources(modules):
    privacy = modules.boundary.PrivacyRedactor()
    privacy.learn('<svg><use href="/icons.svg#down"/><![bad]> /account?token=real-ticket-482')
    assert privacy.clean("real-ticket-482") == "[redacted]"
