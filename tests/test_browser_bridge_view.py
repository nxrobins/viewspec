"""Readable prose, paging, search, and blocked controls in the browser bridge's page view.

The bridge asks the page which controls are blocked (a dialog covers them, or the page has switched
them off) and hands the answer to ``page_view.view_lines``. Whether the page's answer is right needs
a browser, and ``tests/test_browser_bridge_e2e.py`` checks that when it is switched on. These tests
run in the default suite and pin what the view does with the answer: a blocked control stays
readable, keeps its handle, and is told apart from one the agent can use.
"""

from __future__ import annotations

import importlib.util
import sys
from types import ModuleType

import pytest

from tests.test_browser_bridge_e2e import PROBE
from viewspec.raw_html import HtmlLiftResult, lift_html

PAGE = """
<main>
  <h1>Lamps</h1>
  <ul>
    <li><h2>Arc lamp</h2><button>Add to cart</button></li>
    <li><h2>Globe lamp</h2><button>Add to cart</button></li>
  </ul>
  <a href="/checkout">Checkout</a>
  <div role="dialog" aria-label="Weekly offers"><button>No thanks</button></div>
</main>
"""
COVERED = 'covered by dialog "Weekly offers"'
SWITCHED_OFF = "switched off: it ignores the pointer"


def _page_view() -> ModuleType:
    """The experiment's ``page_view`` module, loaded by path: the experiment is not a package."""

    name = "browser_bridge_page_view"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, PROBE / "page_view.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # Before it runs: its dataclasses look their module up by name.
        spec.loader.exec_module(module)
    return sys.modules[name]


def _lift() -> tuple[HtmlLiftResult, dict[str, str]]:
    """The page, and the node id of each of its controls by the name the view shows."""

    lift = lift_html(PAGE, profile="agent")
    names = ["Arc lamp: Add to cart", "Globe lamp: Add to cart", "Checkout", "No thanks"]
    assert [control.name for control in lift.controls] == ["Add to cart", "Add to cart", "Checkout", "No thanks"]
    return lift, {name: control.node_id for name, control in zip(names, lift.controls, strict=True)}


def test_a_blocked_control_stays_in_the_view_with_its_handle() -> None:
    lift, ids = _lift()
    blocked = {ids["Arc lamp: Add to cart"]: COVERED, ids["Globe lamp: Add to cart"]: COVERED, ids["Checkout"]: COVERED}

    plain, plain_handles = _page_view().view_lines(lift)
    lines, handles = _page_view().view_lines(lift, blocked=blocked)

    # The same controls behind the same handles: an agent can still read a blocked control.
    assert handles == plain_handles
    before = [line.text for line in plain if line.kind == "control"]
    after = [line.text for line in lines if line.kind == "control"]
    assert before == [
        "[1] button 'Add to cart'",
        "[2] button 'Add to cart'",
        "[3] link 'Checkout' -> /checkout",
        "[4] button 'No thanks'",
    ]
    # Only the last word differs, and the control the agent can use has none.
    assert after == [*(f"{line} blocked" for line in before[:3]), before[3]]


def test_the_view_opens_with_what_is_in_the_way() -> None:
    lift, ids = _lift()
    blocked = {
        ids["Checkout"]: SWITCHED_OFF,
        ids["Arc lamp: Add to cart"]: COVERED,
        ids["Globe lamp: Add to cart"]: COVERED,
    }

    lines, _ = _page_view().view_lines(lift, blocked=blocked)

    # One line per reason, the one that blocks the most controls first.
    assert [line.text for line in lines[:2]] == [
        f"blocked 2 controls: {COVERED}",
        f"blocked 1 control: {SWITCHED_OFF}",
    ]
    assert [line.kind for line in lines[:3]] == ["blocked", "blocked", "heading"]


def test_a_view_with_nothing_blocked_does_not_change() -> None:
    lift, _ = _lift()
    plain, _ = _page_view().view_lines(lift)

    assert _page_view().view_lines(lift, blocked={})[0] == plain
    assert all(line.kind != "blocked" and not line.text.endswith(" blocked") for line in plain)
    # A reason for a node that is not a control in the view (a form, say) marks nothing.
    assert _page_view().view_lines(lift, blocked={lift.nodes[0].id: COVERED})[0] == plain


def test_a_slice_of_the_view_still_says_a_control_is_blocked() -> None:
    lift, ids = _lift()
    lines, _ = _page_view().view_lines(lift, blocked={ids["Globe lamp: Add to cart"]: COVERED})

    # An agent that reads only the lines for one product must still see that its button is blocked.
    found = _page_view().view_page(lines, find="Globe lamp")

    assert "[2] button 'Add to cart' blocked" in found.lines
    assert "[1] button 'Add to cart'" not in found.lines


@pytest.mark.parametrize(
    "paragraph",
    [
        "The author named in this post is Tolkien.",
        "a short description — with punctuation!",
        "A medium description that sits between sixty and one hundred characters, with its ending intact.",
        "An extended description about reading together as a family. " * 4 + "The author is Tolkien.",
    ],
)
def test_paragraphs_are_complete_and_searchable_regardless_of_role(paragraph: str) -> None:
    view = _page_view()
    lines, _ = view.view_lines(lift_html(f"<main><h1>Reading</h1><p>{paragraph}</p></main>", profile="agent"))
    assert [line.text for line in lines] == ["H1 Reading", f"text {paragraph}"]
    found = view.view_page(lines, find=paragraph[-24:].upper())
    assert found.matches == 1
    assert found.lines == ("H1 Reading", f"text {paragraph}")


def test_inline_prose_keeps_word_order_without_repeating_its_children() -> None:
    view = _page_view()
    source = "<article><h1>Reading</h1><p>Read <strong>The <em>Hobbit</em></strong> by Tolkien.</p></article>"
    lines, _ = view.view_lines(lift_html(source, profile="agent"))
    assert [line.text for line in lines] == ["H1 Reading", "text Read The Hobbit by Tolkien."]
    found = view.view_page(lines, find="HOBBIT   by\n Tolkien")
    assert found.matches == 1
    assert "text Read The Hobbit by Tolkien." in found.lines


def test_inline_link_wording_is_readable_and_its_handle_is_preserved() -> None:
    view = _page_view()
    source = '<p>Read <a href="/book"><em>The Hobbit</em></a> by Tolkien.</p><button>Continue</button>'
    lines, handles = view.view_lines(lift_html(source, profile="agent"))
    assert [line.text for line in lines] == [
        "text Read The Hobbit by Tolkien.",
        "[1] link 'The Hobbit' -> /book",
        "[2] button 'Continue'",
    ]
    assert [control.name for control in handles.values()] == ["The Hobbit", "Continue"]
    assert view.view_page(lines, find="Hobbit by Tolkien").matches == 1


def test_inline_numbers_and_paragraphs_with_only_formatted_text_remain_complete() -> None:
    view = _page_view()
    source = "<p>Read <strong>3</strong> chapters.</p><p><em>Then discuss the story.</em></p>"
    lines, _ = view.view_lines(lift_html(source, profile="agent"))
    assert [line.text for line in lines] == ["text Read 3 chapters.", "text Then discuss the story."]
    assert view.view_page(lines, find="Read 3 chapters").matches == 1


def test_grouped_prose_can_be_paged_and_found_without_changing_handles() -> None:
    view = _page_view()
    paragraphs = [f"Entry {n}: readers recommend the tale of the amber fox." for n in range(7)]
    source = "<main><h1>Reading</h1><button>Earlier</button><article><h2>Recommendations</h2>"
    source += "".join(f"<p>{text}</p>" for text in paragraphs)
    source += "</article><button>Later</button></main>"
    lines, handles = view.view_lines(lift_html(source, profile="agent"))
    assert [c.name for c in handles.values()] == ["Earlier", "Later"]
    assert any(line.text == "group 7x <p>" for line in lines)
    assert [line.text for line in lines if line.kind == "text"] == [f"text {p}" for p in paragraphs]

    def all_pages(find: str | None = None) -> list[str]:
        offset, collected = 0, []
        while True:
            page = view.view_page(lines, offset=offset, limit=2, find=find)
            assert len(page.lines) <= 2
            collected.extend(page.lines)
            if page.next_offset is None:
                assert len(collected) == page.total
                return collected
            assert page.next_offset > offset
            offset = page.next_offset

    assert all_pages() == [line.text for line in lines]
    found = view.view_page(lines, find="AMBER   fox", limit=100)
    assert found.matches == len(paragraphs)
    assert all_pages("AMBER   fox") == list(found.lines)
    assert [text for text in found.lines if text.startswith("text ")] == [f"text {p}" for p in paragraphs]
    assert "H2 Recommendations" in found.lines
    assert view.view_page(lines, find="missing animal").matches == 0


def test_prose_does_not_expose_hidden_text_or_duplicate_control_message_and_row_text() -> None:
    view = _page_view()
    source = """
    <main><h1>Reading</h1>
      <p>Visible <span hidden>hidden-inline</span><em>words</em>.</p>
      <div hidden><p>hidden-parent</p></div><dialog data-lift-hidden="true"><p>hidden-dialog</p></dialog>
      <p data-lift-hidden="true">hidden-snapshot</p>
      <button><span>Continue</span></button>
      <div role="status"><p>Ready to read</p></div>
      <table><tr><td><p>One row</p></td></tr></table>
      <textarea aria-label="Password" data-lift-secret="true">private-field-value</textarea>
    </main>
    """
    lines, handles = view.view_lines(lift_html(source, profile="agent"))
    text = "\n".join(line.text for line in lines)
    assert "text Visible words." in text
    assert "hidden-" not in text and "private-field-value" not in text
    assert text.count("Continue") == text.count("Ready to read") == text.count("One row") == 1
    assert [c.name for c in handles.values()] == ["Continue", "Password"]
