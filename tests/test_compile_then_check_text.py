"""C-11: ``check`` accepts the HTML the compiler wrote for text with ordinary whitespace.

The html-tailwind and mcp-app checks collapsed the whitespace in an element's visible text and then
compared it with the raw text in the manifest. A value with two spaces, a leading or trailing space,
a newline, a tab or a no-break space passed ``check`` on the source and compiled, and the compiled
output then failed ``check``; ``compile_mcp_app()`` raised on it.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from html import escape
from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from viewspec import ViewSpecBuilder, check, compile, compile_mcp_app
from viewspec import cli as viewspec_cli
from viewspec.intent_tools import compile_intent_bundle_file_tool
from viewspec.local_tools_validators import _ArtifactDomProbe, _validate_intent_visible_text
from viewspec.types import IRNode

TARGETS = ("html-tailwind", "mcp-app", "react-tsx", "react-tailwind-tsx")

# The values the audit ran: two spaces, a leading space, a newline, a no-break space and a tab.
AUDIT_VALUES = {
    "two-spaces": "Ada  Lovelace",
    "leading-space": " queued",
    "newline": "Replica lag:\n14s and rising",
    "no-break-space": "Latency 1\u00a0200 ms",
    "tab": "tab\there",
}

# Letters, numbers, punctuation, symbols and space separators (Zs holds U+00A0 and U+3000), plus a
# newline and a tab. Whitespace is also drawn as chunks of its own, so runs of it and leading or
# trailing whitespace are common: an alphabet of one_of(...) would merge into a single character set.
# NUL, U+0085 and carriage returns stay out: they fail check for other reasons (audit finding C-34).
_WHITESPACE = st.sampled_from([" ", "\u00a0", "\u3000", "\n", "\t"])
_CHARACTERS = st.characters(categories=["L", "N", "P", "S", "Zs"], include_characters="\n\t")
TEXT = st.lists(st.one_of(_WHITESPACE, st.text(_CHARACTERS, min_size=1, max_size=4)), min_size=1, max_size=8).map(
    "".join
)


def _builder(text: str) -> ViewSpecBuilder:
    """A view that shows ``text`` as a table value, a row label, a hero title, a badge and a button label."""
    builder = ViewSpecBuilder("text_parity", root_attrs={"title": "Text parity"})
    builder.add_hero("intro", title=text, region="main", group_id="message")
    builder.add_table("rows", region="main", group_id="row_group").add_row(id="r1", label=text, value=text)
    builder.add_node("status", "status", attrs={"state": text})
    builder.bind_attr("status_badge", "status", "state", region="main", present_as="badge")
    builder.add_action("open", "navigate", text, target_region="main", target_ref="motif:intro")
    return builder


def _walk(node: IRNode) -> Iterator[IRNode]:
    yield node
    for child in node.children:
        yield from _walk(child)


def _visible_text_errors(primitive: str, props: dict[str, str], html: str) -> list[str]:
    probe = _ArtifactDomProbe()
    probe.feed(html)
    probe.close()
    errors: list[str] = []
    _validate_intent_visible_text(errors, primitive, props, "dom-action_open", probe)
    return errors


def test_the_view_carries_the_text_in_every_role():
    ast = compile(_builder("Ada  Lovelace").build_bundle())

    carriers = [node for node in _walk(ast.result.root.root) if node.props.get("text") == "Ada  Lovelace"]

    assert sorted(node.primitive for node in carriers) == ["badge", "button", "label", "value", "value"]
    assert {node.props.get("hero_role") for node in carriers} >= {"title"}
    assert {node.props.get("table_cell_role") for node in carriers} >= {"row_header", "cell"}


@settings(max_examples=100, deadline=None)
@given(text=TEXT)
def test_text_that_passes_check_compiles_and_passes_check_on_every_target(text: str):
    with tempfile.TemporaryDirectory(prefix="viewspec-text-") as scratch:
        work = Path(scratch)
        source = _builder(text).export_json(work / "bundle.json")
        assume(check(source)["ok"])
        for target in TARGETS:
            out = work / target
            compiled = compile_intent_bundle_file_tool(source, out, target=target, cwd=work)
            assert compiled["ok"] is True, (target, compiled.get("errors"))
            checked = check(out)
            assert checked["ok"] is True, (target, checked["errors"])


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("text", AUDIT_VALUES.values(), ids=AUDIT_VALUES.keys())
def test_audit_values_pass_check_then_compile_then_check_of_the_output(tmp_path: Path, text: str, target: str):
    source = _builder(text).export_json(tmp_path / "bundle.json")
    out = tmp_path / "out"

    assert viewspec_cli.main(["check", str(source)]) == 0
    assert viewspec_cli.main(["compile", str(source), "--target", target, "--out", str(out)]) == 0
    assert viewspec_cli.main(["check", str(out)]) == 0


def test_compile_mcp_app_accepts_a_tool_value_with_two_spaces():
    builder = ViewSpecBuilder("incident_queue", root_attrs={"title": "Incident queue"})
    builder.add_table("incidents", region="main", group_id="rows").add_row(
        id="inc0", label="INC-0", value="Database replica lag  (14s)"
    )

    app = compile_mcp_app(builder.build_bundle(), uri="ui://ops/incident-queue")

    assert ">Database replica lag  (14s)<" in app.html


@pytest.mark.parametrize("key", ["text", "label"])
@pytest.mark.parametrize("text", AUDIT_VALUES.values(), ids=AUDIT_VALUES.keys())
def test_button_text_and_label_compare_with_whitespace_collapsed_on_both_sides(key: str, text: str):
    html = f'<button id="dom-action_open" type="button">{escape(text)}</button>'

    assert _visible_text_errors("button", {key: text}, html) == []


@pytest.mark.parametrize("key", ["text", "label"])
def test_text_that_differs_in_more_than_whitespace_still_fails(key: str):
    html = '<button id="dom-action_open" type="button">Ada  Byron</button>'

    assert _visible_text_errors("button", {key: "Ada  Lovelace"}, html) == [
        "DOM element dom-action_open text does not match manifest props"
    ]
