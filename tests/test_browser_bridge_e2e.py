"""Opt-in proof that an agent can finish whole tasks through the browser bridge.

``experiments/browser-agent-probe`` holds a Playwright bridge (``bridge.js``) and an MCP server
(``mcp_browser_server.py``) that serves it as three tools: observe, act, and call. Its two scripts,
``demo_agent.py`` and ``demo_mcp.py``, check one capability at a time. This file checks tasks: each
test gives an agent a goal a person would ask for, and passes only when the page ends up where the
goal says: the order that was posted, the file that was saved, the page that was opened.

The agent here is scripted, because the test suite never calls a model. It is held to what a model
would have: it starts the server the way an agent host does, uses only the three tools, and takes
every handle from the view text of a result. It never imports the experiment's code. Each task gets
a server and a browser of its own, so no task sees another's page.

The last tests are about the boundary: what a session cannot do, whatever a page tells the agent.
The fixture site is their second judge. It records what it receives under another name, so a test
fails if anything gets out. One of them does load a module of the experiment, ``boundary.py``, to
hold its list of private addresses against the list in ``bridge.js``.

Set ``VIEWSPEC_RUN_BROWSER_BRIDGE_E2E=1`` after ``npm ci --ignore-scripts`` and ``npx playwright
install chromium`` in ``experiments/browser-agent-probe``. It needs the ``agents`` extra for the MCP
client, and it fails rather than skips without it. Writes normally stay blocked, so a task that sends
a form is judged by the request the bridge stopped. The receipt-dialog regressions allow writes
to the local fixture server to count completed orders and catch duplicates caused by retry advice.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import http.server
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlsplit

import pytest

E2E_OPT_IN = "VIEWSPEC_RUN_BROWSER_BRIDGE_E2E"
ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "experiments" / "browser-agent-probe"
SERVER = PROBE / "mcp_browser_server.py"
FIXTURES = PROBE / "fixtures"

PASSWORD = "S3cret-Pass-99"
CARD = "4242424242424242"
CV = b"%PDF-1.4 CV-CONTENT-7781"
PHOTO = {"content": "iVBORw0KGgo=", "encoding": "base64"}  # Eight bytes: the PNG signature.

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get(E2E_OPT_IN) != "1", reason=f"set {E2E_OPT_IN}=1 to run the agent tasks"),
]


@dataclass
class Turn:
    """One tool call and the result the agent got back."""

    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    # The task made this call to be refused: a request the server must stop before the browser acts.
    refusal_expected: bool = False
    # What the task expects ``outcome.status`` of an act or call to be, when it is not the usual one:
    # "succeeded" for a request that ran, "failed" for one made to be refused.
    outcome_expected: str | None = None


@dataclass
class Run:
    """One agent's whole attempt at a task."""

    goal: str
    secrets: tuple[str, ...]  # Values the agent typed or uploaded that no result may hold.
    turns: list[Turn] = field(default_factory=list)


class Agent:
    """What an agent host gives a model: the three tools, and nothing else. It keeps every turn."""

    def __init__(self, session: Any, run: Run, base: str, uploads: Path, downloads: Path) -> None:
        self._session = session
        self.run = run
        self.base = base
        self.uploads = uploads
        self.downloads = downloads

    async def use(
        self, tool: str, /, *, refusal_expected: bool = False, expect: str | None = None, **arguments: Any
    ) -> dict[str, Any]:
        reply = await self._session.call_tool(tool, arguments)
        result = reply.structuredContent or json.loads(reply.content[0].text)
        self.run.turns.append(Turn(tool, arguments, result, refusal_expected, expect))
        # Printed for the captured output of a failing test. Values are left out: some are secrets.
        outcome = "ok" if result["ok"] else "refused " + ", ".join(error["code"] for error in result["errors"])
        print(f"{len(self.run.turns):>2}  {tool}({', '.join(arguments)}) {outcome}: {result['summary']}")
        return result

    async def observe(self, **arguments: Any) -> dict[str, Any]:
        return await self.use("observe", **arguments)

    async def act(self, **arguments: Any) -> dict[str, Any]:
        return await self.use("act", **arguments)

    async def call(self, **arguments: Any) -> dict[str, Any]:
        return await self.use("call", **arguments)


@dataclass(frozen=True)
class Task:
    goal: str
    play: Callable[[Agent], Awaitable[None]]
    secrets: tuple[str, ...]


TASKS: list[Task] = []


def task(goal: str, *, secrets: tuple[str, ...] = ()) -> Callable[[Callable[[Agent], Awaitable[None]]], Task]:
    def register(play: Callable[[Agent], Awaitable[None]]) -> Task:
        TASKS.append(Task(goal, play, secrets))
        return TASKS[-1]

    return register


def handle(result: dict[str, Any], name: str) -> int:
    """The handle of the first control with this name in a result's view, as an agent reads it."""

    match = re.search(rf"^\[(\d+)\] \S+ {re.escape(repr(name))}", result["view"], re.MULTILINE)
    assert match, f"no control named {name!r} in the view:\n{result['view']}"
    return int(match.group(1))


def handle_under(result: dict[str, Any], item: str, name: str) -> int:
    """The handle of the first control with this name below the text ``item`` in a result's view:
    how an agent reads "the Add to cart of Globe lamp" when every card has that button."""

    _, found, below = result["view"].partition(item)
    assert found, f"no {item!r} in the view:\n{result['view']}"
    return handle({"view": below}, name)


def codes(result: dict[str, Any]) -> list[str]:
    return [error["code"] for error in result["errors"]]


def sent(result: dict[str, Any], path: str) -> dict[str, Any]:
    """The form fields of the one request this result says the bridge stopped, a POST to ``path``."""

    [write] = result["blocked_writes"]
    assert (write["method"], urlsplit(write["url"]).path) == ("POST", path)
    return write["form_fields"]


def unmet(run: Run) -> list[str]:
    """Why this run does not count as a completed task, one reason each; empty when it does.

    Each task below checks its own outcome. This is the rule every task shares: what must hold
    for every turn on the way there.
    """

    reasons: list[str] = []
    for number, turn in enumerate(run.turns, start=1):
        where = f"turn {number} ({turn.tool})"
        # Both directions: a refusal the task did not expect, and a bad request that went through.
        if turn.result["ok"] == turn.refusal_expected:
            reasons.append(f"{where}: ok={turn.result['ok']}: {turn.result['summary']}")
        # None is not a breach: the bridge cannot verify a click, and says so.
        for step in turn.result.get("steps", []):
            if step["verified"] is False:
                reasons.append(f"{where}: step {step['step']['op']} was not verified")
        # The server keeps ok true for an unmet wait; on a fixture the wait is part of the task.
        if turn.result.get("wait", {}).get("met") is False:
            reasons.append(f"{where}: the wait was not met")
        # Every act and call says whether it took effect, and says what the task counts on: a
        # request that ran succeeded, and a refused one failed, unless the task expects otherwise.
        if turn.tool != "observe":
            wanted = turn.outcome_expected or ("failed" if turn.refusal_expected else "succeeded")
            outcome = turn.result.get("outcome") or {}
            if outcome.get("status") != wanted:
                reasons.append(f"{where}: outcome {outcome.get('status')}, not {wanted}: {outcome.get('reason')}")
        text = json.dumps(turn.result)
        reasons += [f"{where}: a secret came back" for secret in run.secrets if secret in text]
    return reasons


# ---- tasks ------------------------------------------------------------------------------------------


@task(
    "Order three Arc lamps: express to Japan, wrapped in paper, with the promo code SAVE10, a new "
    "account password, and the card on file.",
    secrets=(PASSWORD, CARD),
)
async def order_with_a_promo_code(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/checkout.html")
    more = await agent.act(action="click", handle=handle(page, "Increase quantity"))
    opened = await agent.act(action="click", handle=handle(more, "Have a promo code?"))
    typed = await agent.act(action="fill", handle=handle(opened, "Promo code"), value="SAVE10")
    applied = await agent.act(
        action="click", handle=handle(typed, "Apply"), wait_for={"text": "Code SAVE10 applied"}, timeout_ms=10000
    )
    card = await agent.act(action="fill", handle=handle(applied, "Card number"), value=CARD)
    await agent.act(action="fill", handle=handle(card, "CVC"), value="9876")
    order = {
        "email": "bo@example.com",
        "ship": "express",
        "country": "Japan",
        "gift_wrap": "Paper",
        "new_password": PASSWORD,
        "terms": True,
    }
    done = await agent.call(
        expect="blocked", tool="checkout", arguments=order, wait_for={"change": "navigation"}, timeout_ms=10000
    )

    assert "'Quantity' = '3'" in more["view"]
    assert "message status 'Code SAVE10 applied: 10% off'" in applied["changes"]["lines"]
    assert sent(done, "/orders") == {
        "csrf": "[not shown]",  # A hidden token is sent, and no view showed it, so no result does.
        "email": "bo@example.com",
        "ship": "express",
        "country": "Japan",
        "wrap": "Paper",
        "new_password": "[redacted]",
        "promo": "SAVE10",
        "terms": "on",
    }
    assert "Write blocked" in done["view"]


@task("Place the order as bo@example.com with express shipping. Fix whatever is refused on the way.")
async def recover_from_refusals(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/checkout.html")
    # A first guess at the form: a shipping speed the page does not offer, a field it does not have,
    # and no word on the terms. The server names all three before the browser does anything.
    guess = await agent.call(refusal_expected=True, tool="checkout", arguments={"ship": "overnight", "colour": "red"})
    # A bad email typed by hand, as a person can. The browser refuses to send the form and says why.
    typed = await agent.act(action="fill", handle=handle(page, "Email"), value="bo")
    stopped = await agent.act(expect="failed", action="click", handle=handle(typed, "Place order"))
    done = await agent.call(
        expect="blocked",
        tool="checkout",
        arguments={"email": "bo@example.com", "ship": "express", "terms": True},
        wait_for={"change": "navigation"},
        timeout_ms=10000,
    )

    assert {(error["argument"], error["problem"]) for error in guess["errors"]} == {
        ("ship", "invalid"),
        ("colour", "unknown"),
        ("terms", "missing"),
    }
    assert "Nothing was done" in guess["summary"]
    assert not stopped["navigated"]
    assert "Email" in {field["name"] for field in stopped["steps"][0]["invalid"]}
    assert stopped["outcome"]["retry"] == "safe" and "did not send the form" in stopped["outcome"]["reason"]
    # Nothing was sent until the request was right, and then it was sent once.
    assert all("blocked_writes" not in turn.result for turn in agent.run.turns[:-1])
    fields = sent(done, "/orders")
    assert (fields["email"], fields["ship"], fields["terms"]) == ("bo@example.com", "express", "on")


@task("Add the Kindle vintage lamp 250 to the cart. It is one of 300 products on the page.")
async def add_a_product_from_a_long_catalog(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/catalog.html")
    found = await agent.observe(observation_id=page["observation_id"], find="lamp 250")
    added = await agent.act(action="click", handle=handle(found, "Add to cart"), wait_for={"change": "message"})
    after = await agent.observe(observation_id=added["observation_id"], find="lamp 250")

    assert page["view_page"]["total"] > 1000 and "lamp 250" not in page["view"]  # Not in the first slice.
    assert found["view_page"]["matches"] == 1
    assert added["changes"]["lines"][0] == "message status 'Kindle vintage lamp 250 added to cart'"
    assert re.search(r"^\[\d+\] button 'Added' disabled$", after["view"], re.MULTILINE)
    assert "button 'Add to cart'" not in after["view"]


@task("Add the Arc lamp and the Desk organizer to the cart. The organizer shows only after Load more.")
async def add_two_products_among_same_named_buttons(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/shop.html")
    arc = await agent.observe(observation_id=page["observation_id"], find="Arc lamp")
    first = await agent.act(action="click", handle=handle(arc, "Add to cart"))
    more = await agent.act(action="click", handle=handle(first, "Load more"))
    organizer = await agent.observe(observation_id=more["observation_id"], find="Desk organizer")
    second = await agent.act(action="click", handle=handle(organizer, "Add to cart"))

    assert page["view"].count("button 'Add to cart'") == 4  # The name alone does not say which one.
    assert first["changes"]["lines"][0] == "message status 'Arc lamp added to cart'"
    assert "text + heading 'Desk organizer'" in more["changes"]["lines"]
    assert second["changes"]["lines"][0] == "message status 'Desk organizer added to cart'"
    assert "value 2" in second["view"].splitlines()  # The cart count.
    assert second["view"].count("button 'Added' disabled") == 2


@pytest.mark.parametrize("privacy", ["strict", "heuristic"])
def test_prose_paging_and_find_use_the_same_complete_private_reading(site: str, tmp_path: Path, privacy: str) -> None:
    async def play(agent: Agent) -> None:
        page = await agent.observe(url=f"{site}/reading.html", limit=2)
        observation_id = page["observation_id"]
        assert "Tolkien" not in page["view"]
        collected = page["view"].splitlines()
        while page["view_page"]["next_offset"] is not None:
            page = await agent.observe(observation_id=observation_id, offset=page["view_page"]["next_offset"], limit=2)
            assert page["observation_id"] == observation_id
            assert page["view_page"]["returned"] <= 2
            collected.extend(page["view"].splitlines())
        whole = await agent.observe(observation_id=observation_id, limit=1000)
        assert collected == whole["view"].splitlines()
        assert len(collected) == whole["view_page"]["total"]
        assert "text Read The Hobbit by Tolkien." in collected
        assert "author named at the end is Tolkien." in whole["view"]
        assert "group 3x <p>" in whole["view"]
        assert "hidden-" not in whole["view"]
        assert "text Private echo: [redacted]. Public ending." in collected

        found = await agent.observe(observation_id=observation_id, find="TOLKIEN", limit=100)
        assert found["view_page"]["matches"] == 2
        assert "H2 Recommendations" in found["view"]
        assert "Read The Hobbit by Tolkien." in found["view"]
        offset, pieces = 0, []
        while True:
            part = await agent.observe(observation_id=observation_id, find="TOLKIEN", offset=offset, limit=1)
            assert part["view_page"]["matches"] == 2
            pieces.extend(part["view"].splitlines())
            offset = part["view_page"]["next_offset"]
            if offset is None:
                break
        assert pieces == found["view"].splitlines()
        phrase = await agent.observe(observation_id=observation_id, find="HOBBIT   by\n Tolkien")
        assert phrase["view_page"]["matches"] == 1
        secret = await agent.observe(observation_id=observation_id, find="private-reading-value-824")
        assert secret["view_page"]["matches"] == 0
        shops = await agent.observe(observation_id=observation_id, find="Local shops")
        assert handle(shops, "Mark read") == handle(whole, "Mark read")
        marked = await agent.act(observation_id=observation_id, handle=handle(shops, "Mark read"), action="click")
        assert "Marked as read" in marked["view"]

    run = asyncio.run(
        _attempt(
            Task("Read, search and page through post descriptions", play, ("private-reading-value-824",)),
            site,
            tmp_path,
            flags=("--private", privacy),
        )
    )
    assert unmet(run) == []


@task("Open post 75 of the news feed. The feed shows 20 posts and loads more as it is scrolled.")
async def open_a_post_far_down_an_infinite_feed(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/feed.html")
    scrolled = await agent.act(action="scroll", wait_for={"text": "Post 75"}, timeout_ms=20000)
    found = await agent.observe(observation_id=scrolled["observation_id"], find="Post 75")
    opened = await agent.act(
        action="click", handle=handle(found, "Open"), wait_for={"change": "navigation"}, timeout_ms=10000
    )

    assert "Post 75" not in page["view"]
    assert scrolled["scroll"]["met"] is True and scrolled["scroll"]["loaded"]
    assert opened["navigated"] and urlsplit(opened["url"]).path == "/post/75"


@task("Find a stay for three guests from 20 to 23 November 2026, then hire a car for the same days.")
async def book_a_stay_and_a_car(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/stay.html")
    filled = await agent.call(
        tool="find_a_stay",
        submit=False,
        arguments={"checkin": "2026-11-20", "checkout": "2026-11-23", "guests": 3},
    )
    assert {"find_a_stay", "hire_a_car"} <= {tool["name"] for tool in page["form_tools"]}
    [picked] = [step for step in filled["steps"] if step["step"]["op"] == "choose_date_range"]
    assert picked["verified"]
    assert "20 Nov 2026" in filled["view"] and "23 Nov 2026" in filled["view"]
    stay = await agent.act(expect="blocked", action="click", handle=handle(filled, "Search"))
    await agent.observe(url=f"{agent.base}/stay.html")
    rental = await agent.call(
        tool="hire_a_car", submit=False, arguments={"rental": {"start": "2026-11-20", "end": "2026-11-23"}}
    )
    assert "20/11/2026 – 23/11/2026" in rental["view"]
    car = await agent.act(expect="blocked", action="click", handle=handle(rental, "Hire"))
    # The heuristic policy can report the formatted values that the agent actually read.
    assert sent(stay, "/search") == {"checkin": "20 Nov 2026", "checkout": "23 Nov 2026", "guests": "3"}
    assert sent(car, "/hire") == {"rental": "20/11/2026 – 23/11/2026"}


@task(
    "Apply for the job as Bo Diaz, with the CV from the upload folder and two photos.",
    secrets=("CV-CONTENT-7781",),
)
async def apply_for_a_job(agent: Agent) -> None:
    (agent.uploads / "cv.pdf").write_bytes(CV)
    page = await agent.observe(url=f"{agent.base}/upload.html")
    photos = await agent.act(
        action="upload",
        handle=handle(page, "Add photos"),
        files=[{**PHOTO, "name": "front.png"}, {**PHOTO, "name": "side.png"}],
    )
    done = await agent.call(
        expect="blocked",
        tool="apply",
        arguments={"name": "Bo Diaz", "resume": [{"path": "cv.pdf"}]},
        wait_for={"change": "navigation"},
        timeout_ms=10000,
    )

    assert photos["changes"]["lines"] == ["message status '2 photos added'"]
    assert sent(done, "/apply") == {
        "name": "Bo Diaz",
        "resume": {"file": "cv.pdf", "bytes": len(CV), "type": "application/pdf"},
        "photos": [
            {"file": "front.png", "bytes": 8, "type": "image/png"},
            {"file": "side.png", "bytes": 8, "type": "image/png"},
        ],
    }


@task("Save invoice INV-1042 and an export of the orders.", secrets=("Fixture invoice",))
async def save_the_account_documents(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/downloads.html")
    invoice = await agent.act(action="download", handle=handle(page, "Invoice INV-1042"))
    export = await agent.act(action="download", handle=handle(invoice, "Export orders"))

    original = (FIXTURES / "files" / "invoice-1042.pdf").read_bytes()
    saved = invoice["steps"][0]["download"]
    assert sorted(path.name for path in agent.downloads.iterdir()) == ["invoice-1042.pdf", "orders-2026-11.csv"]
    assert (agent.downloads / "invoice-1042.pdf").read_bytes() == original
    assert (saved["bytes"], saved["sha256"]) == (len(original), hashlib.sha256(original).hexdigest())
    assert export["steps"][0]["download"]["file"] == "orders-2026-11.csv"
    # The page builds this file by script from the table the agent read.
    rows = (agent.downloads / "orders-2026-11.csv").read_text(encoding="utf-8").splitlines()
    assert rows[0] == "Order,Date,Total" and rows[-1] == "1044,2026-11-08,19.00"


@task("Refresh the metrics, then export them. Plan both from the first reading of the page.")
async def export_after_the_page_redraws_itself(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/spa.html")
    first = page["observation_id"]
    # Refresh replaces every element, so the Export the agent read is gone. Its saved locator
    # finds the new one, and the export answers 600 ms later.
    await agent.act(action="click", handle=handle(page, "Refresh"), observation_id=first)
    export = await agent.act(
        action="click",
        handle=handle(page, "Export"),
        observation_id=first,
        wait_for={"text": "Download CSV"},
        timeout_ms=10000,
    )

    assert export["steps"][0]["resolved_by"] != "ref"
    assert "message status 'Export ready'" in export["changes"]["lines"]
    assert re.search(r"^\[\d+\] link 'Download CSV'", export["view"], re.MULTILINE)


@task("Open the article on Alan Turing from the search box, not the Institute or the law named after him.")
async def open_an_article_from_search_suggestions(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/search.html")
    opened = await agent.act(action="select", handle=handle(page, "Search Wikipedia"), value="Alan Turing")

    assert opened["steps"][0]["chosen"] == "Alan Turing"
    assert opened["navigated"] and urlsplit(opened["url"]).path == "/wiki/Alan_Turing"


# The tasks below are about what a handle means. Four cards on market.html have the same two
# buttons, so "Add to cart" names nothing: the card around the button does. A step must reach the
# control the agent read, in the item it read it in, or be refused. It must never reach another one.


@task("Sort the lamps by price, then add the Globe lamp to the cart. Plan both from the first reading.")
async def add_the_right_product_after_the_list_is_rebuilt(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/market.html")
    first = page["observation_id"]
    # Sorting builds the list again: every element is new, and Globe lamp moves from third to last.
    # The third card is Arc lamp now, so a step that goes by position buys the wrong lamp.
    await agent.act(action="click", handle=handle(page, "Sort by price"), observation_id=first)
    added = await agent.act(
        action="click", handle=handle_under(page, "Globe lamp", "Add to cart"), observation_id=first
    )

    assert added["steps"][0]["resolved_by"] != "ref"
    assert "message status 'Globe lamp added to cart'" in added["changes"]["lines"]
    assert "message status 'Cart: Globe lamp'" in added["view"]


@task("Sort the lamps by name, then add the Globe lamp to the cart. Plan both from the first reading.")
async def add_the_right_product_after_the_cards_are_reused(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/market.html")
    first = page["observation_id"]
    # This sort keeps every element and writes another product into each card. The button the agent
    # read is still on the page, and it belongs to Clamp lamp now.
    await agent.act(action="click", handle=handle(page, "Sort by name"), observation_id=first)
    added = await agent.act(
        action="click", handle=handle_under(page, "Globe lamp", "Add to cart"), observation_id=first
    )

    assert added["steps"][0]["resolved_by"] == "name"
    assert "message status 'Globe lamp added to cart'" in added["changes"]["lines"]
    assert "message status 'Cart: Globe lamp'" in added["view"]


@task("Sort the lamps by price, then go to the next page. Plan both from the first reading.")
async def choose_between_two_next_page_buttons(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/market.html")
    first = page["observation_id"]
    await agent.act(action="click", handle=handle(page, "Sort by price"), observation_id=first)
    # The page has a "Next page" above the list and one below, and it has drawn both again. Nothing
    # says which of them the agent read, so the server does not pick one.
    guess = await agent.act(
        refusal_expected=True, action="click", handle=handle(page, "Next page"), observation_id=first
    )
    turned = await agent.act(action="click", handle=handle(guess, "Next page"))

    assert codes(guess) == ["BROWSER_TARGET_AMBIGUOUS"]
    assert '2 controls on the page are "Next page"' in guess["errors"][0]["message"]
    assert re.search(r"^value 1$", guess["view"], re.MULTILINE), "the refused step turned the page"
    assert re.search(r"^value 2$", turned["view"], re.MULTILINE)


@task("Save the Globe lamp for later, then show the saved items. Plan both from the first reading.")
async def use_a_button_whose_label_counts(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/market.html")
    first = page["observation_id"]
    saved = await agent.act(action="click", handle=handle_under(page, "Globe lamp", "Save"), observation_id=first)
    # The button read "Saved items (0)" and reads "Saved items (1)" now. It is the same button: a
    # label that counts must not make a control unusable.
    shown = await agent.act(action="click", handle=handle(page, "Saved items (0)"), observation_id=first)

    assert "message status 'Globe lamp saved for later'" in saved["changes"]["lines"]
    assert re.search(r"^\[\d+\] button 'Saved items \(1\)'$", saved["view"], re.MULTILINE)
    assert shown["steps"][0]["resolved_by"] == "ref"
    assert "message status 'Showing 1 saved item'" in shown["changes"]["lines"]


@task("Add one Arc lamp to the cart. The step may be sent twice; the cart must hold one.")
async def add_once_when_the_step_is_sent_twice(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/shop.html")
    first = page["observation_id"]
    add = handle_under(page, "Arc lamp", "Add to cart")
    added = await agent.act(action="click", handle=add, observation_id=first)
    # The button the agent read as "Add to cart" reads "Added" now. It is not the control the agent
    # asked for any more, so the second click is refused at once.
    again = await agent.act(refusal_expected=True, action="click", handle=add, observation_id=first)

    assert "message status 'Arc lamp added to cart'" in added["changes"]["lines"]
    assert codes(again) == ["BROWSER_TARGET_DRIFT"]
    assert 'read as "Add to cart" now reads "Added"' in again["errors"][0]["message"]
    assert re.search(r"^value 1$", again["view"], re.MULTILINE), "the cart does not hold exactly one"


# These two are about the difference between a control the agent can read and one it can use.


@task("Add the Globe lamp to the cart. An offers dialog is open over the page: decline it.")
async def decline_a_dialog_that_covers_the_page(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/market.html")
    opened = await agent.act(action="click", handle=handle(page, "Show offers"))
    # The view lists every control, as before, and says which of them the dialog covers.
    hasty = await agent.act(
        refusal_expected=True, action="click", handle=handle_under(opened, "Globe lamp", "Add to cart")
    )
    closed = await agent.act(action="click", handle=handle(hasty, "No thanks"))
    added = await agent.act(action="click", handle=handle_under(closed, "Globe lamp", "Add to cart"))

    lines = opened["view"].splitlines()
    assert lines[0] == 'blocked 19 controls: covered by dialog "Weekly offers"'
    usable = [line for line in lines if line.startswith("[") and not line.endswith(" blocked")]
    assert [line.split("'")[1] for line in usable] == ["Subscribe", "No thanks"]
    assert codes(hasty) == ["BROWSER_TARGET_BLOCKED"]
    assert '"Add to cart" is covered by dialog "Weekly offers"' in hasty["errors"][0]["message"]
    assert "message status 'Cart: empty'" in hasty["view"], "the refused step reached the page"
    assert "message status 'Offers closed'" in closed["changes"]["lines"]
    assert "message status 'Cart: Globe lamp'" in added["view"]


@task("Check out with a Globe lamp, and place the order. The page lets you once the cart holds something.")
async def check_out_once_the_page_allows_it(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/market.html")
    # Both controls are on the page from the start, and neither can be used: the link ignores the
    # pointer, and the order summary is inert. The view says so, and so does a step that tries.
    early = await agent.act(refusal_expected=True, action="click", handle=handle(page, "Checkout"))
    added = await agent.act(action="click", handle=handle_under(early, "Globe lamp", "Add to cart"))
    started = await agent.act(action="click", handle=handle(added, "Checkout"))
    placed = await agent.act(action="click", handle=handle(started, "Place order"))

    assert page["view"].splitlines()[:2] == [
        "blocked 1 control: switched off: it ignores the pointer",
        "blocked 1 control: inert: the page has switched this part off",
    ]
    assert re.search(r"^\[\d+\] link 'Checkout' -> #checkout blocked$", page["view"], re.MULTILINE)
    assert re.search(r"^\[\d+\] button 'Place order' blocked$", page["view"], re.MULTILINE)
    assert codes(early) == ["BROWSER_TARGET_BLOCKED"]
    assert '"Checkout" is switched off' in early["errors"][0]["message"]
    assert "blocked" not in added["view"]
    assert "message status 'Checking out: Globe lamp'" in started["changes"]["lines"]
    assert "message status 'Order placed: Globe lamp'" in placed["changes"]["lines"]


# The tasks below are about what a result says an action did. On outcomes.html each control answers
# in a way that two readings of the page do not show: the same rows in another order, the same
# message again, a toast that is gone, two properties set by a script, a question in a browser
# dialog, a new tab, an answer that is still on its way. The agent must be told which it was, and
# whether the action succeeded, failed, or cannot be told, before it decides to act again.


@task("Sort the team by name, then by age, and read the order each time.")
async def read_a_table_after_it_is_sorted(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/outcomes.html")
    # Each sort moves the same three rows. No text and no control is new or gone.
    by_name = await agent.act(action="click", handle=handle(page, "Name"))
    by_age = await agent.act(action="click", handle=handle(by_name, "Age"))

    assert by_name["changes"]["lines"] == ["reordered 3x <tr>: 'Ana 36', 'Bo 41', 'Cy 29'"]
    assert by_age["changes"]["lines"] == ["reordered 3x <tr>: 'Cy 29', 'Ana 36', 'Bo 41'"]
    assert "reordered 3x <tr>" in by_name["outcome"]["reason"]
    rows = [line for line in by_age["view"].splitlines() if line.startswith("row ")]
    assert rows == ["row Name | Age", "row Cy | 29", "row Ana | 36", "row Bo | 41"]


@task("Save the draft twice, and be sure the second save happened. Then copy its link.")
async def tell_a_repeated_message_from_no_answer(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/outcomes.html")
    first = await agent.act(action="click", handle=handle(page, "Save"))
    # The status line says "Saved" already, and the page writes "Saved" into it again.
    second = await agent.act(action="click", handle=handle(first, "Save"), wait_for={"change": "message"})
    # The toast is on the page for 300 ms. The agent reads the page after it has gone.
    copied = await agent.act(action="click", handle=handle(second, "Copy link"), wait_for={"gone": "Link copied"})

    assert first["changes"]["lines"] == ["message status 'Saved'"]
    assert second["changes"]["lines"] == ["message status 'Saved' (shown again)"]
    assert "message status 'Saved'" in second["view"]
    assert copied["changes"]["lines"] == ["message status 'Link copied' (shown, now gone)"]
    assert "Link copied" not in copied["view"]


@task("Look up the city for postcode 1100, wait for the page to fill it in, then jump to the notes.")
async def wait_for_an_answer_that_sets_only_properties(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/outcomes.html")
    typed = await agent.act(action="fill", handle=handle(page, "Postcode"), value="1100")
    # The answer comes 400 ms later, as two properties a script sets: no element and no attribute
    # changes, so nothing wakes a wait that only watches the DOM.
    found = await agent.act(
        action="click", handle=handle(typed, "Look up"), wait_for={"change": "any"}, timeout_ms=10000
    )
    jumped = await agent.act(action="click", handle=handle(found, "Jump to notes"))

    assert found["wait"]["waited_ms"] < 4000, "the wait ended at its timeout, not when the page answered"
    assert [line.split("] ", 1)[1] for line in found["changes"]["lines"]] == [
        "text 'City': value '' -> 'Lisbon'",
        "checkbox 'Address verified': checked false -> true",
    ]
    assert re.search(r"^\[\d+\] checkbox 'Address verified' \[x\]$", found["view"], re.MULTILINE)
    assert jumped["changes"]["lines"] == ["address /outcomes.html -> /outcomes.html#notes"]
    assert jumped["url"].endswith("/outcomes.html#notes")


@task("Delete the draft, refresh the badge, and open the help. Say which of them did anything.")
async def tell_what_did_not_happen(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/outcomes.html")
    # The page asks "Delete this draft?" in a browser dialog, which is not in the page, and the
    # bridge dismisses it. Unreported, that reads as a click that did nothing.
    deleted = await agent.act(expect="uncertain", action="click", handle=handle(page, "Delete draft"))
    # This button has no handler at all.
    refreshed = await agent.act(expect="uncertain", action="click", handle=handle(deleted, "Refresh badge"))
    helped = await agent.act(expect="failed", action="click", handle=handle(refreshed, "Help"))

    assert deleted["steps"][0].get("dialogs") == [
        {"type": "confirm", "message": "Delete this draft?", "answer": "dismissed"}
    ]
    assert "'Delete this draft?'" in deleted["outcome"]["reason"] and deleted["outcome"]["retry"] == "may_repeat"
    assert "Draft deleted" not in deleted["view"]
    assert (refreshed["outcome"]["page"], refreshed["outcome"]["retry"]) == ("quiet", "may_repeat")
    assert refreshed["steps"][0]["reaction"] == {"mutations": 0, "requests": 0, "pending": 0}
    assert helped["blocked_navigations"][0]["url"] == f"{agent.base}/outcomes_help.html"
    assert helped["steps"][0]["opened"] == [f"{agent.base}/outcomes_help.html"]
    assert helped["url"] == page["url"] and "H1 Team" in helped["view"], "the agent left the page it was on"


@task(
    "Publish the draft, then publish it once more. The server takes over a second to answer: wait "
    "for the answer, and never press Publish again only because nothing showed."
)
async def wait_for_the_server_instead_of_acting_twice(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/outcomes.html")
    # Nothing on the page changes until the server answers. The result must say that the page is
    # still working, which is what tells an agent to wait and not to click again.
    sent = await agent.act(expect="uncertain", action="click", handle=handle(page, "Publish"))
    answered = await agent.observe(since=sent["observation_id"], wait_for={"change": "message"}, timeout_ms=10000)
    again = await agent.act(
        action="click", handle=handle(answered, "Publish"), wait_for={"change": "message"}, timeout_ms=10000
    )

    assert sent["changes"]["lines"] == ["no change"] and sent["steps"][0].get("reaction", {}).get("pending") == 1
    assert (sent["outcome"]["page"], sent["outcome"]["retry"]) == ("busy", "may_repeat")
    assert answered["changes"]["lines"] == ["message status 'Published'"]
    # The second answer is the same word in the same place. It is a message all the same.
    assert again["changes"]["lines"] == ["message status 'Published' (shown again)"]
    assert again["wait"]["waited_ms"] >= 1000, "the wait was met before the server answered"


# These two are about where a page sends things. A guard that is set on the page the agent reads
# does not see a tab that the page opens, and newtab.html sends its order, and its visitors, through
# one. Writes are blocked and one origin is allowed for every task here, so for both the site itself
# is the judge: it must receive nothing (see ``test_an_agent_completes_the_task``).


@task("Order two Arc lamps. The shop opens its receipt in a new tab.")
async def order_through_a_form_that_opens_a_new_tab(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/newtab.html")
    done = await agent.call(expect="blocked", tool="order", arguments={"quantity": 2})

    assert done.get("blocked_writes"), "the order that went out through a new tab was not stopped"
    assert sent(done, "/orders") == {"item": "Arc lamp", "quantity": "2"}
    assert done["blocked_writes"][0].get("new_tab") is True
    assert done["steps"][-1]["opened"] == [f"{agent.base}/orders"]
    # The result that reports the tab is the one that reports what was stopped in it, and its
    # reason is the request, not the quantity that the call itself typed.
    assert f"POST {agent.base}/orders" in done["outcome"]["reason"]
    assert done["url"] == page["url"] and "'Quantity' = '2'" in done["view"]


@task("Open the partner offers. This server allows the shop's own site and no other.")
async def stay_on_the_allowed_site_when_a_link_opens_a_new_tab(agent: Agent) -> None:
    page = await agent.observe(url=f"{agent.base}/newtab.html")
    left = await agent.act(expect="failed", action="click", handle=handle(page, "Partner offers"))

    partner = f"http://localhost:{urlsplit(agent.base).port}/outcomes_help.html"
    assert left.get("blocked_navigations") == [
        {"url": partner, "reason": "document origin not allowed", "new_tab": True}
    ]
    assert left["steps"][0]["opened"] == [partner]
    assert partner in left["outcome"]["reason"] and left["outcome"]["retry"] == "safe"
    assert left["url"] == page["url"] and "H1 Order" in left["view"]


# ---- running them -----------------------------------------------------------------------------------

SLOW_ANSWER_S = 1.5  # How long the server holds back slow.json, which outcomes.html asks for.
# Requests the site received: normally none, because writes are blocked and only 127.0.0.1 with
# the site's port is allowed. The receipt-dialog regressions enable writes and expect one order.
RECEIVED: list[str] = []
BODIES: dict[str, str] = {}  # The body of the latest POST the site received, by path.


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        host = self.headers.get("Host", "")
        parts = urlsplit(self.path)
        # The site under another name is "another origin". /account/ is where a GET changes data.
        if not host.startswith("127.0.0.1:") or parts.path.startswith("/account/"):
            RECEIVED.append(f"GET {self.path} as {host}")
        if parts.path == "/leave":
            # An allowed origin whose server sends the browser somewhere else.
            self.send_response(302)
            self.send_header("Location", parse_qs(parts.query)["to"][0])
            self.end_headers()
            return
        if parts.path in ("/session-start", "/session-check"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            if parts.path == "/session-start":
                self.send_header("Set-Cookie", "visit=second; HttpOnly; Path=/")
            self.end_headers()
            accepted = self.headers.get(
                "X-Trial"
            ) == "fixture-ticket" and "account=fixture-account" in self.headers.get("Cookie", "")
            visiting = "visit=second" in self.headers.get("Cookie", "")
            heading = "Authenticated" if accepted else "Anonymous"
            script = (
                "localStorage.setItem('visit','present')"
                if parts.path == "/session-start"
                else ("document.querySelector('h2').textContent = localStorage.getItem('visit') || 'empty'")
            )
            self.wfile.write(
                f"<!doctype html><h1>{heading}</h1><h2></h2><h3>{visiting}</h3><script>{script}</script>".encode()
            )
            return
        if parts.path == "/slow.json":
            time.sleep(SLOW_ANSWER_S)  # A server that takes its time: the page is still working.
        super().do_GET()

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        RECEIVED.append(f"POST {self.path}")
        BODIES[self.path] = body.decode("utf-8", "replace")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<!doctype html><title>Stored</title><p>Stored</p>")


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    """The fixture pages, served on a local port. The server allows the agent this origin only."""

    handler = functools.partial(_QuietHandler, directory=str(FIXTURES))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


async def _attempt(
    task: Task,
    base: str,
    folder: Path,
    *,
    allow_writes: bool = False,
    sealed: bool = True,
    flags: tuple[str, ...] = (),
    secrets: dict[str, str] | None = None,
) -> Run:
    """Run a task against a server of its own. ``sealed``: the server lists the site as its one
    origin. ``flags`` are more of the server's limits; ``secrets`` go into its environment."""

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    uploads, downloads = folder / "uploads", folder / "downloads"
    uploads.mkdir()
    downloads.mkdir()
    # Like a host's MCP config: only where Playwright keeps its browsers, if that is not the
    # default. NODE_PATH is left out, so the bridge loads the Playwright installed beside it.
    env = {key: os.environ[key] for key in ("PLAYWRIGHT_BROWSERS_PATH",) if key in os.environ} | (secrets or {})
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            str(SERVER),
            *(["--allow-origin", base, "--allow-read", base + "/*"] if sealed else ["--public-read"]),
            "--private",
            "heuristic",
            *flags,
            "--upload-dir",
            str(uploads),
            "--download-dir",
            str(downloads),
            *(["--allow-writes"] if allow_writes else []),
        ],
        env=env,
    )
    run = Run(task.goal, task.secrets)
    failure: AssertionError | None = None
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        try:
            await task.play(Agent(session, run, base, uploads, downloads))
        except AssertionError as error:
            # Raised again below, outside the client's task groups, where pytest can show it.
            failure = error
    if failure is not None:
        raise failure
    return run


@pytest.mark.parametrize("task", TASKS, ids=[task.play.__name__ for task in TASKS])
def test_an_agent_completes_the_task(task: Task, site: str, tmp_path: Path) -> None:
    print(f"goal: {task.goal}")
    RECEIVED.clear()
    run = asyncio.run(_attempt(task, site, tmp_path))

    assert unmet(run) == []
    assert RECEIVED == [], "a write or a request for another origin got past the bridge to the site"


@pytest.mark.parametrize("dialog", ["confirm", "prompt"])
@pytest.mark.parametrize("tool", ["act", "call"])
def test_dismissing_a_receipt_dialog_cannot_make_a_completed_order_safe_to_retry(
    dialog: str, tool: str, site: str, tmp_path: Path
) -> None:
    async def play(agent: Agent) -> None:
        page = await agent.observe(url=f"{agent.base}/order_receipt.html?dialog={dialog}")

        async def place(current: dict[str, Any]) -> dict[str, Any]:
            if tool == "call":
                return await agent.call(tool="order", arguments={"quantity": 2})
            return await agent.act(action="click", handle=handle(current, "Place order"))

        placed = await place(page)
        assert placed["steps"][-1]["dialogs"] == [
            {"type": dialog, "message": "Also email a receipt?", "answer": "dismissed"}
        ]
        assert "message status 'Order placed'" in placed["changes"]["lines"]
        # Follow the old retry advice if it returns: the server must catch the duplicate order,
        # not merely a different label in the result. All writes stay on this local fixture site.
        if placed["outcome"]["retry"] == "safe":
            await place(placed)
        assert RECEIVED == ["POST /orders"], "retry advice duplicated an already completed order"
        assert (placed["outcome"]["status"], placed["outcome"]["retry"]) == ("succeeded", "not_needed")

    RECEIVED.clear()
    run = asyncio.run(
        _attempt(Task("Place one order, without emailing a receipt.", play, ()), site, tmp_path, allow_writes=True)
    )
    assert unmet(run) == []
    assert RECEIVED == ["POST /orders"]


@pytest.mark.parametrize("route", ["push", "replace", "hash", "back", "state", "roundtrip"])
def test_old_observation_cannot_act_after_a_product_route_change(route: str, site: str, tmp_path: Path) -> None:
    async def play(agent: Agent) -> None:
        page = await agent.observe(url=f"{agent.base}/spa_product.html?route={route}")
        first = page["observation_id"]
        current_product = "Arc lamp" if route in {"back", "roundtrip"} else "Globe lamp"
        route_button = "View Arc lamp" if route == "back" else "View Globe lamp"
        changed = await agent.act(action="click", handle=handle(page, route_button))
        assert f"H1 {current_product}" in changed["view"]
        # The input, button and form are exactly the same DOM nodes on the new product route.
        # Returning to the original URL (or changing only history state) must also expire the view.
        stale_click = await agent.act(
            refusal_expected=True, action="click", handle=handle(page, "Add to cart"), observation_id=first
        )
        stale_form = await agent.call(
            refusal_expected=True, tool="order_product", arguments={"quantity": 2}, observation_id=first
        )
        stale_scroll = await agent.act(refusal_expected=True, action="scroll", value="end", observation_id=first)
        assert codes(stale_scroll) == ["BROWSER_OBSERVATION_STALE"]
        for refused in (stale_click, stale_form):
            assert codes(refused) == ["BROWSER_OBSERVATION_STALE"]
            assert "message status 'Cart: empty'" in refused["view"]
            assert "message status 'Orders: empty'" in refused["view"]
            assert "'Quantity' = '1'" in refused["view"], "the stale form call filled the new product's field"
        if route in {"state", "roundtrip"}:
            assert changed["url"] == page["url"], "this case must defeat a URL-equality guard"
        fresh = await agent.observe()
        added = await agent.act(action="click", handle=handle(fresh, "Add to cart"))
        ordered = await agent.call(tool="order_product", arguments={"quantity": 2})
        assert f"message status 'Cart: {current_product}'" in added["view"]
        assert f"message status 'Orders: {current_product} x 2'" in ordered["view"]

    case = Task("Use only a fresh observation after changing product routes", play, ())
    run = asyncio.run(_attempt(case, site, tmp_path))
    assert unmet(run) == []


@pytest.mark.parametrize("privacy", ["strict", "heuristic"])
@pytest.mark.parametrize("operation", ["omit", "call_clear", "act_clear"])
def test_checkout_secret_state_and_clearing_reach_the_server(
    site: str, tmp_path: Path, privacy: str, operation: str
) -> None:
    seeded_password = "TOPSECRET-pw-4242"

    def password_state(page: dict[str, Any], expected: str) -> None:
        line = next(line for line in page["view"].splitlines() if "password 'Create a password'" in line)
        assert f"secret {expected}" in line
        prop = page["form_tools"][0]["input_schema"]["properties"]["new_password"]
        assert prop["writeOnly"] and prop["x-value-state"] == expected
        assert "default" not in prop

    async def play(agent: Agent) -> None:
        page = await agent.observe(url=f"{site}/checkout.html")
        # The fixture sets .value in JavaScript and claims empty in its own snapshot hint.
        password_state(page, "filled")
        arguments = {
            "email": "river@example.test",
            "ship": "express",
            "country": "Spain",
            "gift_wrap": "Box",
            "terms": True,
        }
        cleared = None
        if operation == "act_clear":
            cleared = await agent.act(handle=handle(page, "Create a password"), action="fill", value="")
        if operation == "call_clear":
            arguments["new_password"] = ""
        prepared = await agent.call(tool="checkout", arguments=arguments, submit=False)
        if operation == "call_clear":
            cleared = prepared
        if cleared is not None:
            password_state(cleared, "empty")
            [step] = [item for item in cleared["steps"] if item["step"]["name"] == "Create a password"]
            assert step["verified"] is True
            assert any("value_state 'filled' -> 'empty'" in line for line in cleared["changes"]["lines"])
        else:
            assert all(item["step"]["name"] != "Create a password" for item in prepared["steps"])
        password_state(prepared, "filled" if operation == "omit" else "empty")
        await agent.act(handle=handle(prepared, "Place order"), action="click")

    RECEIVED.clear()
    BODIES.clear()
    run = asyncio.run(
        _attempt(
            Task(
                "Place an order and check whether its optional password was preserved or cleared",
                play,
                (seeded_password,),
            ),
            site,
            tmp_path,
            flags=("--private", privacy, "--allow-write", f"POST {site}/orders"),
        )
    )
    assert unmet(run) == []
    assert RECEIVED == ["POST /orders"]
    posted = parse_qs(BODIES["/orders"], keep_blank_values=True)
    assert posted["new_password"] == [seeded_password if operation == "omit" else ""]
    assert posted["email"] == ["river@example.test"]


# ---- the boundary -----------------------------------------------------------------------------------
# The tests above ask whether an agent can do its task. These ask what it cannot do, whatever the
# page tells it: reach an origin that is not listed, send a write no rule names, or read a secret.
# The site under the name localhost stands for "another origin"; RECEIVED says what reached it.

SHOP_PASSWORD = "Tr0ub4dor&3-horse"  # The "&" makes a form send it in another shape than it is typed.


def _bounded(goal: str, play: Callable[[Agent], Awaitable[None]], site: str, folder: Path, **limits: Any) -> Run:
    RECEIVED.clear()
    BODIES.clear()
    held = tuple((limits.get("secrets") or {}).values())
    run = asyncio.run(
        _attempt(Task(goal, play, (*held, *(quote_plus(value) for value in held))), site, folder, **limits)
    )
    assert unmet(run) == []
    return run


@pytest.mark.parametrize(
    ("simulate", "code", "state"),
    [
        (False, 403, "blocked"),
        (True, 200, "blocked-and-simulated"),
    ],
)
def test_page_visible_json_and_agent_write_disposition(
    site: str, tmp_path: Path, simulate: bool, code: int, state: str
) -> None:
    async def play(agent: Agent) -> None:
        page = await agent.observe(url=f"{site}/session_contract.html")
        done = await agent.act(
            expect="blocked", action="click", handle=handle(page, "Save note"), wait_for={"text": f"Response {code}"}
        )
        assert done["blocked_writes"][0]["state"] == state
        assert done["steps"][0]["writes"][0]["state"] == state
        assert done["wait"]["met"] and f"Response {code} {state} marked 1" in done["view"]
        assert done["summary"] == done["outcome"]["reason"]
        assert done["outcome"]["retry"] == "may_repeat"

    _bounded("Save a fixture note", play, site, tmp_path, flags=("--simulate-writes",) if simulate else ())
    assert RECEIVED == []


def test_strict_session_projects_values_on_each_mcp_result(site: str, tmp_path: Path) -> None:
    async def play(agent: Agent) -> None:
        reading = await agent.observe(url=f"{site}/session_contract.html?token=fixture-url-482#fixture-fragment")
        filled = await agent.act(action="fill", handle=handle(reading, "Notes"), value="fixture-revision-482")
        assert "[redacted]" in reading["url"] and "[redacted]" in filled["view"]
        assert filled["steps"][0]["verified"] is True

    task = Task(
        "Edit a private note", play, ("fixture-note-482", "fixture-url-482", "fixture-fragment", "fixture-revision-482")
    )
    assert unmet(asyncio.run(_attempt(task, site, tmp_path, flags=("--private", "strict")))) == []


def test_operator_session_persists_across_opens(site: str, tmp_path: Path) -> None:
    async def play(agent: Agent) -> None:
        first = await agent.observe(url=f"{site}/session-start")
        second = await agent.observe(url=f"{site}/session-check")
        assert "Authenticated" in first["view"] and "Authenticated" in second["view"]
        assert "present" in second["view"] and "True" in second["view"]

    state_path = tmp_path / "trial-session.json"
    _bounded(
        "Read two signed-in pages",
        play,
        site,
        tmp_path,
        flags=(
            "--header",
            "X-Trial=TRIAL_HEADER",
            "--cookie",
            "account=TRIAL_COOKIE",
            "--save-storage-state",
            str(state_path),
        ),
        secrets={"TRIAL_HEADER": "fixture-ticket", "TRIAL_COOKIE": "fixture-account"},
    )
    state = json.loads(state_path.read_text())
    assert {c["name"] for c in state["cookies"]} == {"account", "visit"}
    assert state_path.stat().st_mode & 0o777 == 0o600
    assert "fixture-ticket" not in state_path.read_text()


def test_operator_sets_confirmation_answer(site: str, tmp_path: Path) -> None:
    async def play(agent: Agent) -> None:
        page = await agent.observe(url=f"{site}/outcomes.html")
        result = await agent.act(action="click", handle=handle(page, "Delete draft"))
        assert result["steps"][0]["dialogs"][0]["answer"] == "accepted"
        assert "Draft deleted" in result["view"]

    _bounded("Confirm a fixture dialog", play, site, tmp_path, flags=("--confirm", "accept"))


def test_click_controls_in_same_origin_and_srcdoc_frames(site: str, tmp_path: Path) -> None:
    async def play(agent: Agent) -> None:
        reading = await agent.observe(url=f"{site}/session_contract.html")
        for name in ("Open embedded panel", "Open inline panel"):
            reading = await agent.act(action="click", handle=handle(reading, name))
            assert name + " opened" in reading["view"]

    _bounded("Open both embedded panels", play, site, tmp_path)


def test_an_open_session_does_not_read_this_computer(site: str, tmp_path: Path) -> None:
    async def play(agent: Agent) -> None:
        # With no origins listed the agent may read the public web. This site is on 127.0.0.1.
        local = await agent.observe(refusal_expected=True, url=f"{agent.base}/boundary.html")
        named = await agent.observe(refusal_expected=True, url=agent.base.replace("127.0.0.1", "localhost"))
        file = await agent.observe(refusal_expected=True, url="file:///etc/hosts")
        keyed = await agent.observe(refusal_expected=True, url="https://bo:hunter2@example.com/")

        assert codes(local) == codes(named) == ["BROWSER_ADDRESS_PRIVATE"]
        assert codes(file) == codes(keyed) == ["BROWSER_ADDRESS_NOT_ALLOWED"]
        assert "private network" in local["errors"][0]["message"]

    _bounded("Read a page on this computer, and a file.", play, site, tmp_path, sealed=False)
    assert RECEIVED == []


def test_only_the_writes_a_rule_names_are_sent(site: str, tmp_path: Path) -> None:
    async def play(agent: Agent) -> None:
        page = await agent.observe(url=f"{agent.base}/boundary.html")
        added = await agent.act(action="click", handle=handle(page, "Add to cart"))
        again = await agent.observe(url=f"{agent.base}/boundary.html")
        ordered = await agent.act(expect="blocked", action="click", handle=handle(again, "Place order"))
        back = await agent.observe(url=f"{agent.base}/boundary.html")
        # A GET that changes data. Its method says nothing, so the server was told its address.
        deleted = await agent.act(expect="failed", action="click", handle=handle(back, "Delete account"))

        assert f"Writes: only POST {agent.base}/cart. Every other one is stopped and listed." in page["limits"]
        assert added["navigated"] and added["title"] == "Stored" and "blocked_writes" not in added
        [stopped] = ordered["blocked_writes"]
        assert (stopped["method"], urlsplit(stopped["url"]).path) == ("POST", "/orders")
        assert [item["reason"] for item in deleted["blocked_navigations"]] == ["a rule denies this address"]
        assert deleted["url"] == f"{agent.base}/boundary.html"

    flags = ("--allow-write", f"POST {site}/cart", "--deny-url", "*/account/delete*")
    _bounded("Add a lamp to the cart, order it, and delete the account.", play, site, tmp_path, flags=flags)
    assert RECEIVED == ["POST /cart"], "the site received something other than the one write a rule allows"
    assert BODIES == {"/cart": "item=lamp"}


def test_a_secret_is_named_by_the_agent_and_never_shown_to_it(site: str, tmp_path: Path) -> None:
    async def play(agent: Agent) -> None:
        page = await agent.observe(url=f"{agent.base}/boundary.html")
        named = {"secret": "SHOP_PASSWORD"}
        # A page can tell an agent to type the password anywhere. It goes into a secret field only.
        wrong = await agent.act(refusal_expected=True, action="fill", handle=handle(page, "Note"), value=named)
        unknown = await agent.act(
            refusal_expected=True, action="fill", handle=handle(page, "Password"), value={"secret": "BANK_PIN"}
        )
        typed = await agent.act(action="fill", handle=handle(page, "Password"), value=named)
        # A faulty page writes the password where anyone can read it. The view would hold it.
        echoed = await agent.act(action="click", handle=handle(typed, "Show password"))
        sign_in = next(tool["name"] for tool in page["form_tools"] if "sign" in tool["name"])
        signed = await agent.call(tool=sign_in, arguments={"user": "bo", "password": named})

        assert codes(wrong) == codes(unknown) == ["BROWSER_SECRET_REFUSED"]
        assert typed["steps"][0]["verified"] is True
        assert "message status 'You typed: [secret SHOP_PASSWORD]'" in echoed["changes"]["lines"]
        assert "You typed: [secret SHOP_PASSWORD]" in echoed["view"]
        assert signed["navigated"] and signed["title"] == "Stored"
        assert any("secret SHOP_PASSWORD" in line for line in page["limits"])

    flags = ("--secret", "SHOP_PASSWORD", "--allow-write", f"POST {site}/login")
    _bounded("Sign in as bo.", play, site, tmp_path, flags=flags, secrets={"SHOP_PASSWORD": SHOP_PASSWORD})
    # The site got the password itself. No result did: the shared rule checks every one of them.
    received = dict(BODIES)
    assert received == {"/login": f"user=bo&password={quote_plus(SHOP_PASSWORD)}"}


def test_the_gate_and_the_policy_agree_on_which_addresses_are_private() -> None:
    """boundary.py judges the address written in a link, and the gate in bridge.js the address a name
    resolves to. They are two lists in two languages, and they must draw the same line."""

    private = ["127.0.0.1", "10.0.0.5", "172.16.0.1", "172.31.255.254", "192.168.1.1", "169.254.169.254", "100.64.0.1"]
    private += [
        "0.0.0.0",
        "192.0.2.7",
        "::1",
        "fd00::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "::ffff:7f00:1",
        "::ffff:10.0.0.5",
    ]
    # 172.66 is outside 172.16/12, and 104.20 is an ordinary address: both are example.com's.
    public = ["8.8.8.8", "104.20.23.154", "172.66.147.243", "172.32.0.1", "1.1.1.1", "2606:4700:10::6814:179a"]
    public += ["::ffff:8.8.8.8", "::ffff:808:808"]

    spec = importlib.util.spec_from_file_location("browser_bridge_boundary_e2e", PROBE / "boundary.py")
    assert spec is not None and spec.loader is not None
    boundary = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = boundary
    spec.loader.exec_module(boundary)
    by_policy = [address for address in private + public if boundary.is_private_host(address)]

    asked = json.dumps({"id": 1, "cmd": "private", "addresses": private + public})
    done = subprocess.run(
        ["node", str(PROBE / "bridge.js")], input=asked + "\n", capture_output=True, text=True, timeout=60
    )
    by_gate = json.loads(done.stdout.splitlines()[0])["result"]["private"]

    assert by_gate == private, "the gate lets through, or refuses, an address it should not"
    assert by_policy == private


@pytest.mark.parametrize(
    ("flags", "reason"),
    [
        ([], "Choose --allow-origin"),
        (["--allow-writes"], "--allow-writes needs --allow-origin"),
        (["--secret", "SHOP_PASSWORD"], "--secret needs --allow-origin"),
        (["--allow-host", "cdn.example"], "--allow-host needs --allow-origin"),
        (["--allow-origin", "https://shop.example", "--secret", "NOT_SET_ANYWHERE"], "no value for it"),
        (
            ["--allow-origin", "https://shop.example", "--allow-write", "POST https://elsewhere.example/pay"],
            "not on a listed origin",
        ),
        (["--allow-origin", "https://shop.example/account"], "must be an origin only"),
    ],
)
def test_the_server_does_not_start_with_limits_it_cannot_keep(flags: list[str], reason: str) -> None:
    env = {**os.environ, "SHOP_PASSWORD": SHOP_PASSWORD}
    done = subprocess.run(
        [sys.executable, str(SERVER), *flags],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert done.returncode == 2 and reason in done.stderr, done.stderr[-400:]
    assert SHOP_PASSWORD not in done.stderr + done.stdout


def test_large_reading_paging_find_and_action_do_not_prepare_selectors(site: str, monkeypatch) -> None:
    monkeypatch.syspath_prepend(str(PROBE))
    from agent_session import BrowserSession
    from boundary import Boundary
    from mcp_browser_server import BrowserTools

    calls: list[str] = []
    original = BrowserSession._call

    def recorded(self, cmd, **kwargs):
        # A deterministic work-budget assertion, independent of browser or CI timing. The real
        # bridge still captures identities and blocked state, and executes the selected action.
        assert cmd not in {"locate", "confirm"}, "ordinary reading eagerly prepared selectors"
        calls.append(cmd)
        return original(self, cmd, **kwargs)

    monkeypatch.setattr(BrowserSession, "_call", recorded)
    browser = BrowserTools(boundary=Boundary.build(allow_origin=(site,), allow_read=(site + "/*",)))
    try:
        page = browser.observe(url=f"{site}/locator_catalog.html", limit=1)
        assert page["ok"]
        reading = browser._history[page["observation_id"]]
        assert len(reading.lift.controls) == 1200 and len(reading.lift.forms) == 600
        assert len(reading.locators) == 1800
        assert all(target["identity"] for target in reading.locators.values())
        assert calls.count("targets") == 1
        before = list(calls)
        paged = browser.observe(observation_id=page["observation_id"], offset=1, limit=3)
        found = browser.observe(observation_id=page["observation_id"], find="Entry 599")
        assert paged["ok"] and found["ok"] and calls == before
        added = browser.act(action="click", handle=handle(found, "Add"), observation_id=page["observation_id"])
        assert added["ok"] and added["steps"][0]["resolved_by"] == "ref"
        assert "message status 'Added Entry 599'" in added["view"]
    finally:
        browser.close()


@pytest.mark.parametrize("fixture", ["market.html", "checkout.html"])
def test_identity_and_blocked_state_match_explicit_locator_diagnostics(site: str, monkeypatch, fixture: str) -> None:
    monkeypatch.syspath_prepend(str(PROBE))
    from agent_session import BrowserSession
    from boundary import Boundary

    with BrowserSession(boundary=Boundary.build(allow_origin=(site,), allow_read=(site + "/*",))) as session:
        session.open(f"{site}/{fixture}")
        ordinary = session.observe()
        diagnostic = session.observe(locate=True)
        assert ordinary.blocked == diagnostic.blocked
        assert ordinary.locators.keys() == diagnostic.locators.keys()
        assert all(target["strategy"] is None for target in ordinary.locators.values())
        for key, target in ordinary.locators.items():
            assert target["identity"] == diagnostic.locators[key]["identity"]
        confirmed = session.confirm_locators(diagnostic)
        assert confirmed and all(confirmed.values())


def test_action_checks_a_dialog_that_appeared_after_the_reading(site: str, tmp_path: Path) -> None:
    async def play(agent: Agent) -> None:
        page = await agent.observe(url=f"{agent.base}/market.html")
        first = page["observation_id"]
        add = handle_under(page, "Globe lamp", "Add to cart")
        opened = await agent.act(action="click", handle=handle(page, "Show offers"))
        refused = await agent.act(refusal_expected=True, action="click", handle=add, observation_id=first)
        assert codes(refused) == ["BROWSER_TARGET_BLOCKED"]
        assert "covered by dialog" in refused["errors"][0]["message"]
        assert "message status 'Cart: empty'" in refused["view"]
        await agent.act(action="click", handle=handle(opened, "No thanks"))
        added = await agent.act(action="click", handle=add, observation_id=first)
        assert "message status 'Cart: Globe lamp'" in added["view"]

    run = asyncio.run(_attempt(Task("Check current actionability of a saved target", play, ()), site, tmp_path))
    assert unmet(run) == []


@pytest.mark.parametrize("privacy", ["strict", "heuristic"])
def test_public_svg_fragments_preserve_prose_without_exposing_credentials(
    site: str, tmp_path: Path, privacy: str
) -> None:
    title = "My PC is downstairs, and my TV is upstairs."
    private = (
        "fixture-native-482",
        "fixture-marked-482",
        "fixture-query-482",
        "fixture-fragment-482",
        "fixture-svg-query-482",
        "fixture-operator-482",
        "fixture-session-query-482",
        "fixture-session-fragment-482",
    )

    async def play(agent: Agent) -> None:
        page = await agent.observe(
            url=f"{site}/svg_privacy.html?token=fixture-session-query-482#token=fixture-session-fragment-482",
            limit=1,
        )
        assert title in page["view"]
        observation_id = page["observation_id"]
        found = await agent.observe(observation_id=observation_id, find="downstairs", limit=1)
        assert found["view_page"]["matches"] == 1 and title in found["view"]
        following = await agent.observe(observation_id=observation_id, find="downstairs", offset=1, limit=2)
        assert "Research continues downstairs." in following["view"]
        whole = await agent.observe(observation_id=observation_id, limit=100)
        assert "Private echoes:" in whole["view"] and "[redacted]" in whole["view"]
        assert "[secret FIXTURE_TOKEN]" in whole["view"]
        hidden = await agent.observe(observation_id=observation_id, find="fixture-native-482")
        assert hidden["view_page"]["matches"] == 0
        changed = await agent.act(action="click", handle=handle(whole, "Refresh text"))
        assert "Still researching downstairs." in changed["view"] and title in changed["view"]

    run = asyncio.run(
        _attempt(
            Task("Read the public title and body while withholding all credentials", play, private),
            site,
            tmp_path,
            flags=("--private", privacy, "--secret", "FIXTURE_TOKEN"),
            secrets={"FIXTURE_TOKEN": "fixture-operator-482"},
        )
    )
    assert unmet(run) == []


@pytest.mark.parametrize("privacy", ["strict", "heuristic"])
def test_paged_form_schema_calls_the_selected_form(site: str, tmp_path: Path, privacy: str) -> None:
    async def play(agent: Agent) -> None:
        first = await agent.observe(url=f"{site}/locator_catalog.html", limit=1)
        assert first["form_tools"] == []
        reading = first["observation_id"]
        found = await agent.observe(observation_id=reading, find="Entry 599", limit=1)
        assert found["form_tools"] == []  # Only the matching heading so far.
        form = await agent.observe(observation_id=reading, find="Entry 599", offset=1, limit=1)
        [schema] = form["form_tools"]
        assert f"tool={schema['name']}" in form["view"]
        field = await agent.observe(observation_id=reading, find="Entry 599", offset=2, limit=1)
        assert field["form_tools"] == [schema]
        assert schema["input_schema"]["properties"]["quantity"]["type"] == "number"
        missing = await agent.observe(observation_id=reading, find="Missing entry")
        assert missing["form_tools"] == []
        # Paging only changes the response, never the reading's action identities or callable forms.
        called = await agent.call(tool=schema["name"], arguments={"quantity": 2}, observation_id=reading)
        assert "Added Entry 599" in called["view"]

    run = asyncio.run(
        _attempt(Task("Page to and call the last of 600 forms", play, ()), site, tmp_path, flags=("--private", privacy))
    )
    assert unmet(run) == []
