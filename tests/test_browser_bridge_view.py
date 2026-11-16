"""What the browser bridge's page view does with a control a person could not use.

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
