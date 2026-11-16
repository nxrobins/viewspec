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

Set ``VIEWSPEC_RUN_BROWSER_BRIDGE_E2E=1`` after ``npm ci --ignore-scripts`` and ``npx playwright
install chromium`` in ``experiments/browser-agent-probe``. It needs the ``agents`` extra for the MCP
client, and it fails rather than skips without it. Writes stay blocked, as they are by default, so a
task that sends a form is judged by the request the bridge stopped.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import http.server
import json
import os
import re
import sys
import threading
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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

    async def use(self, tool: str, /, *, refusal_expected: bool = False, **arguments: Any) -> dict[str, Any]:
        reply = await self._session.call_tool(tool, arguments)
        result = reply.structuredContent or json.loads(reply.content[0].text)
        self.run.turns.append(Turn(tool, arguments, result, refusal_expected))
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
    done = await agent.call(tool="checkout", arguments=order, wait_for={"change": "navigation"}, timeout_ms=10000)

    assert "'Quantity' = '3'" in more["view"]
    assert "message status 'Code SAVE10 applied: 10% off'" in applied["changes"]["lines"]
    assert sent(done, "/orders") == {
        "csrf": "csrf-token-123456",
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
    stopped = await agent.act(action="click", handle=handle(typed, "Place order"))
    done = await agent.call(
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
    stay = await agent.call(
        tool="find_a_stay",
        arguments={"checkin": "2026-11-20", "checkout": "2026-11-23", "guests": 3},
        wait_for={"change": "navigation"},
        timeout_ms=10000,
    )
    # The search loaded a new page, so the car form is read again before it is used.
    await agent.observe(url=f"{agent.base}/stay.html")
    car = await agent.call(
        tool="hire_a_car",
        arguments={"rental": {"start": "2026-11-20", "end": "2026-11-23"}},
        wait_for={"change": "navigation"},
        timeout_ms=10000,
    )

    assert {"find_a_stay", "hire_a_car"} <= {tool["name"] for tool in page["form_tools"]}
    [picked] = [step for step in stay["steps"] if step["step"]["op"] == "choose_date_range"]
    assert picked["verified"]  # One step picked both days in the shared two-month calendar.
    # Each form sends its dates the way its own picker writes them.
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


# ---- running them -----------------------------------------------------------------------------------


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    """The fixture pages, served on a local port. The server allows the agent this origin only."""

    handler = functools.partial(_QuietHandler, directory=str(FIXTURES))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


async def _attempt(task: Task, base: str, folder: Path) -> Run:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    uploads, downloads = folder / "uploads", folder / "downloads"
    uploads.mkdir()
    downloads.mkdir()
    # Like a host's MCP config: only where Playwright keeps its browsers, if that is not the
    # default. NODE_PATH is left out, so the bridge loads the Playwright installed beside it.
    env = {key: os.environ[key] for key in ("PLAYWRIGHT_BROWSERS_PATH",) if key in os.environ}
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER), "--allow-origin", base, "--upload-dir", str(uploads), "--download-dir", str(downloads)],
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
    run = asyncio.run(_attempt(task, site, tmp_path))

    assert unmet(run) == []
