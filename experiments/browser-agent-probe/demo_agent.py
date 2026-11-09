"""A scripted agent that reads pages with the ViewSpec agent lift and acts on them, with checks.

It serves the fixtures on 127.0.0.1, then:

1. checkout: clicks a button inside a web component's shadow DOM, fills card fields inside an
   iframe, makes a bad form tool call (refused before the browser does anything), then a good one,
   and checks the form post that the session blocked.
2. spa: lets the page replace every element, then acts from the old view; the saved locators find
   the new elements. A simulated layout change shows the drift guard refusing a wrong target.
3. viewspec_orders: clicks a ViewSpec action by its data-action-id.
4. shop: checks what each action changed: a status message, a changed button, a new product.
5. waiting: pages that answer after a delay. Without a wait the agent sees no change; with
   ``BrowserSession.wait`` it sees the answer, without polling on a timer.
6. choosing: custom comboboxes. One fetches its options as you type; one opens on a click.
7. scrolling: an infinite feed and a pane that each load more as they are scrolled.
8. dates: a native date field, and two custom date pickers (one with month buttons and plain day
   numbers, one with month and year lists).
9. uploads: a file input, and an "Add photos" button whose file input is hidden. Wrong types and
   too many files are refused; the blocked form post holds the file names and sizes.
10. ranges: a range picker that shows two months and fills check-in and check-out, one field that
    holds a whole range, and two native date fields, each set by one range.
11. downloads: links and a script-built export, saved in a temporary download folder under safe
    names, never over another file; a plain click that starts a download saves nothing.

After each action in checkout and shop, the script also checks the diff between the reading before
and the reading after (``agent_session.changes``).

Every check is an assertion. The script prints a transcript and exits 1 on the first failure.
Secret values are never printed.

Usage:
    python experiments/browser-agent-probe/demo_agent.py
"""

from __future__ import annotations

import functools
import hashlib
import http.server
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs

from agent_session import BridgeError, BrowserSession, StepResult, changes, request_fields

from viewspec.raw_html_actions import AgentActionError, UploadFile, plan_control_action, plan_scroll
from viewspec.raw_html_diff import ChangeCondition

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PASSWORD = "S3cret-Pass-99"
CARD = "4242424242424242"
CHECKS: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    CHECKS.append(message)
    print(f"  ok  {message}")


def show(results: list[StepResult]) -> None:
    for result in results:
        step = result.step.to_json()
        value = f" {step['value']!r}" if "value" in step else f" {step['values']}" if "values" in step else ""
        if "files" in step:
            value = f" {[file['name'] for file in step['files']]}"
        flags = [f"by {result.resolved_by}"]
        if result.verified is not None:
            flags.append("verified" if result.verified else "NOT VERIFIED")
        if result.navigated:
            flags.append(f"navigated to {result.url}")
        print(f"  ->  {step['op']} {step['name']!r}{value}  ({', '.join(flags)})")


def handle_named(session: BrowserSession, name: str) -> int:
    observation = session.observation or session.observe()
    return next(handle for handle, control in observation.handles.items() if control.name == name)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


def serve() -> tuple[http.server.ThreadingHTTPServer, str]:
    handler = functools.partial(QuietHandler, directory=str(FIXTURES))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def checkout(session: BrowserSession, base: str) -> None:
    print("\n== checkout: shadow DOM, iframe, form tool ==")
    session.open(f"{base}/checkout.html")
    view = session.observe()
    print("\n".join(f"     {line}" for line in view.view.splitlines()))

    quantity = handle_named(session, "Quantity")
    check(view.handles[quantity].value == "2", "the page shows quantity 2")
    show(session.act(handle_named(session, "Increase quantity"), "click"))
    after = session.observe()
    check(after.handles[quantity].value == "3", "a click inside the shadow DOM made it 3")
    diff = changes(view, after)["lines"]
    print("\n".join(f"  ~~  {line}" for line in diff))
    check(diff == [f"changed [{quantity}] text 'Quantity': value '2' -> '3'"], "the diff shows that one change only")

    card = session.act(handle_named(session, "Card number"), "fill", CARD)
    cvc = session.act(handle_named(session, "CVC"), "fill", "9876")
    show(card + cvc)
    check(all(result.verified for result in card + cvc), "the card fields inside the iframe hold the typed values")
    filled = session.observe()
    check(CARD not in filled.html, "the next snapshot does not contain the card number")
    check(changes(after, filled)["lines"] == ["no change"], "the diff does not show a change to a secret value")

    show(session.act(handle_named(session, "Have a promo code?"), "click"))
    opened = session.observe()
    diff = changes(filled, opened)["lines"]
    print("\n".join(f"  ~~  {line}" for line in diff))
    check(
        f"added [{handle_named(session, 'Promo code')}] text 'Promo code'" in diff,
        "opening the <details> shows the promo field as added, with its new handle",
    )

    try:
        session.call("checkout", {"ship": "overnight", "colour": "red"})
        check(False, "a bad tool call is refused")
    except AgentActionError as error:
        for item in error.details:
            print(f"  !!  {item['argument']}: {item['problem']}: {item['message']}")
        problems = {(item["argument"], item["problem"]) for item in error.details}
        check(
            problems == {("ship", "invalid"), ("colour", "unknown"), ("terms", "missing")},
            "a bad tool call is refused with every problem listed",
        )
    check(session.blocked_writes() == [], "the refused call did nothing in the browser")

    # The browser checks an email field's format when the form is sent, and says why it refused only
    # in a tooltip that never reaches the page.
    try:
        session.call("checkout", {"email": "bo", "ship": "express", "terms": True})
        check(False, "a form call with a bad email is refused")
    except AgentActionError as error:
        [problem] = [item for item in error.details if item["argument"] == "email"]
        print(f"  !!  {problem['argument']}: {problem['problem']}: {problem['message']}")
        check(
            "is not an email address" in problem["message"],
            "a form call with a bad email is refused before the browser acts, and says why",
        )
    session.act(handle_named(session, "Email"), "fill", "bo")
    [clicked] = session.act(handle_named(session, "Place order"), "click")
    show([clicked])
    refused = [f"{item['name']}: {item['message']}" for item in clicked.invalid]
    print(f"  ..  the browser refused: {refused}")
    check(
        not clicked.navigated and clicked.invalid[0]["name"] == "Email" and "@" in clicked.invalid[0]["message"],
        "a click on Place order with a bad email is blocked by the browser, and the step names the field and the reason",
    )

    arguments = {
        "email": "bo@example.com",
        "ship": "express",
        "country": "Japan",
        "new_password": PASSWORD,
        "terms": True,
    }
    before_submit = session.observe()
    results = session.call("checkout", arguments)
    show(results)
    check(all(result.verified is not False for result in results), "every step that can be verified was")
    check(results[-1].navigated, "the submit click sent the form")

    [write] = session.blocked_writes()
    body = {key: values[0] for key, values in parse_qs(write["post_data"] or "").items()}
    shown = {key: ("[redacted]" if key == "new_password" else value) for key, value in body.items()}
    print(f"  ..  blocked {write['method']} {write['url']}: {shown}")
    check(write["method"] == "POST" and write["url"].endswith("/orders"), "the post went to /orders and was blocked")
    check(
        body.get("email") == "bo@example.com" and body.get("ship") == "express" and body.get("country") == "Japan",
        "the post holds the email, the shipping speed, and the country the agent chose",
    )
    check(body.get("terms") == "on", "the terms checkbox outside the <form> element was sent with it")
    check(body.get("new_password") == PASSWORD, "the password reached the form (and was never printed)")
    check(body.get("csrf") == "csrf-token-123456", "the browser sent the hidden CSRF field the agent never saw")
    blocked = session.observe()
    check(blocked.lift.roles[0].text == "Write blocked", "the agent now sees the blocked-write page")
    check(changes(before_submit, blocked)["new_document"], "the diff says the page is a new document")
    navigated = session.wait(ChangeCondition.from_json({"change": "navigation"}), before_submit, timeout_ms=3000)
    check(navigated.met and navigated.readings == 1, "a wait for a new page is met at once, in one reading")


def waiting(session: BrowserSession, base: str) -> None:
    print("\n== waiting: pages that answer after a delay ==")
    session.open(f"{base}/spa.html")
    before = session.observe()
    show(session.act(handle_named(session, "Export"), "click"))
    check(changes(before, session.observe())["lines"] == ["no change"], "without a wait, Export seems to do nothing")

    session.open(f"{base}/spa.html")
    before = session.observe()
    show(session.act(handle_named(session, "Export"), "click"))
    waited = session.wait(ChangeCondition.from_json({"text": "Download CSV"}), before, timeout_ms=5000)
    after = session.observe()
    diff = changes(before, after)["lines"]
    print(f"  ..  waited {waited.waited_ms} ms in {waited.readings} readings")
    print("\n".join(f"  ~~  {line}" for line in diff))
    check(waited.met and waited.waited_ms >= 400, "a wait for 'Download CSV' is met once the page shows it")
    check(waited.readings <= 4, "the page was read only when it changed, not on a timer")
    link = handle_named(session, "Download CSV")
    check(diff == ["message status 'Export ready'", f"added [{link}] link 'Download CSV'"], "and the diff shows it")

    never = session.wait(ChangeCondition.from_json({"text": "Never shown"}), after, timeout_ms=800)
    print(f"  ..  waited {never.waited_ms} ms in {never.readings} readings")
    check(not never.met and never.waited_ms >= 800, "a wait that is not met ends at its timeout")

    session.open(f"{base}/checkout.html")
    session.observe()
    session.act(handle_named(session, "Have a promo code?"), "click")
    session.act(handle_named(session, "Promo code"), "fill", "nope")
    before = session.observe()
    show(session.act(handle_named(session, "Apply"), "click"))
    waited = session.wait(ChangeCondition.from_json({"change": "message"}), before, timeout_ms=5000)
    diff = changes(before, session.observe())["lines"]
    print("\n".join(f"  ~~  {line}" for line in diff))
    check(waited.met and diff[0] == "message alert 'Code not valid'", "a wait for a message shows the alert")

    session.act(handle_named(session, "Promo code"), "fill", "save10")
    before = session.observe()
    show(session.act(handle_named(session, "Apply"), "click"))
    checking = session.observe()
    check(
        any(
            line.endswith("name 'Apply' -> 'Checking…'; disabled false -> true")
            for line in changes(before, checking)["lines"]
        ),
        "right after the click, the button says Checking… and is disabled",
    )
    waited = session.wait(ChangeCondition.from_json({"control": "Apply", "enabled": True}), before, timeout_ms=5000)
    diff = changes(before, session.observe())["lines"]
    print("\n".join(f"  ~~  {line}" for line in diff))
    check(waited.met, "a wait for the Apply button to be enabled again is met")
    check("message status 'Code SAVE10 applied: 10% off'" in diff, "and the diff shows the code was applied")


def shop(session: BrowserSession, base: str) -> None:
    print("\n== shop: what each action changed ==")
    session.open(f"{base}/shop.html")
    before = session.observe()
    show(session.act(handle_named(session, "Add to cart"), "click"))
    after = session.observe()
    diff = changes(before, after)["lines"]
    print("\n".join(f"  ~~  {line}" for line in diff))
    check(diff[0] == "message status 'Arc lamp added to cart'", "the status message comes first")
    added = handle_named(session, "Added")
    check(
        f"changed [{added}] button 'Added': name 'Add to cart' -> 'Added'; disabled false -> true" in diff,
        "the clicked button is shown renamed and disabled, with its handle",
    )
    check("text + value '1'" in diff and "text - value '0'" in diff, "the cart count went from 0 to 1")

    show(session.act(handle_named(session, "Load more"), "click"))
    more = session.observe()
    diff = changes(after, more)["lines"]
    print("\n".join(f"  ~~  {line}" for line in diff))
    old_refs = {control.ref for control in after.lift.controls}
    [new_button] = [handle for handle, control in more.handles.items() if control.ref not in old_refs]
    check(
        diff
        == [
            f"added [{new_button}] button 'Add to cart'",
            "text + heading 'Desk organizer'",
            "text + value '$22.00'",
        ],
        "Load more shows one new product and its button, with the button's new handle",
    )

    check(changes(more, session.observe())["empty"], "reading the page again shows no change")

    # The search box suggests products, as gov.uk's does, and a click on a suggestion runs the search.
    session.open(f"{base}/shop.html")
    [tool] = [schema["name"] for schema in session.observe().lift.form_tool_schemas()]
    searched = session.act(handle_named(session, "Search products"), "select", "Arc")
    show(searched)
    check(
        searched[0].step.op == "choose"
        and searched[0].chosen == "Arc lamp"
        and searched[0].navigated
        and searched[0].verified is None,
        "a suggestion that loads the results page is reported as chosen and navigated, not verified",
    )
    check("/search?q=Arc+lamp" in session.observe().url, "and the agent is on the results page")

    session.open(f"{base}/shop.html")
    session.observe()
    try:
        session.call(tool, {"q": "Clamp lamp"})
        check(False, "a form call whose suggestion loads the results page stops")
    except BridgeError as error:
        print(f"  !!  {error}")
        check(
            "navigated after step 1 of 2 (choose 'Search products')" in str(error)
            and "1 step(s) did not run" in str(error),
            "a form call whose suggestion loads the results page stops and names the submit step that did not run",
        )
    check("/search?q=Clamp+lamp" in session.observe().url, "the search ran all the same")


def spa(session: BrowserSession, base: str) -> None:
    print("\n== spa: the page replaces every element ==")
    session.open(f"{base}/spa.html")
    old = session.observe()
    refresh = handle_named(session, "Refresh")
    show(session.act(refresh, "click"))

    filter_handle = next(handle for handle, control in old.handles.items() if control.name == "Filter metrics")
    fill = session.run(plan_control_action(old.handles[filter_handle], "fill", "signups"), observation=old)
    show(fill)
    check(fill[0].resolved_by != "ref", "the old ref is gone, so a saved locator found the new element")
    check(fill[0].verified is True, "and the fill landed")

    again = session.run(plan_control_action(old.handles[refresh], "click"), observation=old)
    show(again)
    clock = [node.text for node in session.observe().lift.nodes if "refresh" in node.text]
    check(bool(clock) and clock[0].endswith("(refresh 2)"), "a click from the old view reached the new Refresh button")

    # Simulate a layout change: the saved locator for Refresh now finds the Export control.
    locator = old.locators[old.handles[refresh].node_id]
    old.locators[old.handles[refresh].node_id] = {**locator, "css": "#root > main > div > div", "tag": "div"}
    try:
        session.run(plan_control_action(old.handles[refresh], "click"), observation=old)
        check(False, "the drift guard refuses a changed target")
    except BridgeError as error:
        print(f"  !!  {error}")
        check("drift" in str(error), "the drift guard refuses to click a control with another name")


def viewspec_page(session: BrowserSession, base: str) -> None:
    print("\n== viewspec_orders: a page ViewSpec compiled ==")
    session.open(f"{base}/viewspec_orders.html")
    view = session.observe()
    refund = next(handle for handle, control in view.handles.items() if control.action_id == "refund")
    locator = view.locators[view.handles[refund].node_id]
    print(f"  ..  [{refund}] {view.handles[refund].name!r} #refund -> {locator['playwright']}")
    check(view.handles[refund].action_id == "refund", "the lift reads the ViewSpec action id")
    results = session.act(refund, "click")
    show(results)
    check(results[0].resolved_by == "ref", "the click used the exact element")


def choosing(session: BrowserSession, base: str) -> None:
    print("\n== choosing: custom comboboxes ==")
    session.open(f"{base}/checkout.html")
    before = session.observe()
    country = handle_named(session, "Country")
    picked = session.act(country, "select", "Jap")
    show(picked)
    check(picked[0].step.op == "choose" and picked[0].chosen == "Japan", "'Jap' chose Japan from the list")
    check(picked[0].verified is True, "and the combobox shows it")
    after = session.observe()
    check(
        changes(before, after)["lines"] == [f"changed [{country}] combobox 'Country': value '' -> 'Japan'"],
        "the diff shows the new value",
    )

    for value, words in (("S", "more than one option matches"), ("Atlantis", "no option named")):
        try:
            session.act(country, "select", value)
            check(False, f"{value!r} is refused")
        except BridgeError as error:
            print(f"  !!  {error}")
            check(words in str(error), f"{value!r} is refused: {words}")
        check(session.observe().handles[country].value == "Japan", "and the field still holds Japan")

    city = handle_named(session, "Pickup city")
    before = session.observe()
    lisbon = session.act(city, "select", "Lis")
    show(lisbon)
    check(lisbon[0].chosen == "Lisbon" and lisbon[0].verified, "a combobox that filters on key presses chose Lisbon")
    check(
        changes(before, session.observe())["lines"]
        == [f"changed [{city}] combobox 'Pickup city': value '' -> 'Lisbon'"],
        "the diff shows Lisbon",
    )

    colour = handle_named(session, "Colour")
    before = session.observe()
    check(
        before.handles[colour].readonly and before.handles[colour].value == "Pick a colour",
        "a read-only combobox shows the text beside its unreachable input as its value",
    )
    purple = session.act(colour, "select", "Pur")
    show(purple)
    check(purple[0].chosen == "Purple" and purple[0].verified, "a click on its box opened the list and chose Purple")
    check(
        changes(before, session.observe())["lines"]
        == [
            "message live 'option Purple, selected.'",
            f"changed [{colour}] combobox 'Colour': value 'Pick a colour' -> 'Purple'",
        ],
        "the diff shows the announcement and the value beside the input",
    )

    wrap = handle_named(session, "Gift wrap")
    before = session.observe()
    boxed = session.act(wrap, "select", "box")
    show(boxed)
    check(boxed[0].chosen == "Box" and boxed[0].verified, "a click-to-open combobox chose Box")
    check(
        changes(before, session.observe())["lines"]
        == [f"changed [{wrap}] combobox 'Gift wrap': value 'None' -> 'Box'"],
        "the diff shows Box",
    )

    results = session.call("checkout", {"country": "Kenya", "gift_wrap": "Paper", "terms": True})
    show(results)
    [write] = session.blocked_writes()
    body = {key: values[0] for key, values in parse_qs(write["post_data"] or "").items()}
    check(body.get("country") == "Kenya" and body.get("wrap") == "Paper", "a form call chose both and sent them")

    # Like Wikipedia's search: a plain search field until it has focus, then a combobox whose
    # suggestions show a title over a description and open the article when clicked.
    session.open(f"{base}/search.html")
    view = session.observe()
    box = handle_named(session, "Search Wikipedia")
    check(
        view.handles[box].control == "search" and not view.handles[box].options, "the search field is no combobox yet"
    )
    picked = session.act(box, "select", "Alan Turing")
    show(picked)
    check(
        picked[0].step.op == "choose" and picked[0].chosen == "Alan Turing" and picked[0].navigated,
        "'Alan Turing' chose the article with that title, not the Institute or the law, and opened it",
    )
    check(session.observe().url.endswith("/wiki/Alan_Turing"), "the agent is on the article")
    session.open(f"{base}/search.html")
    box = handle_named(session, "Search Wikipedia")
    try:
        session.act(box, "select", "Alan")
        check(False, "'Alan' is refused")
    except BridgeError as error:
        print(f"  !!  {error}")
        check(
            "Alan Turing, Alan Turing Institute, Alan Turing law" in str(error)
            and session.observe().handles[box].value == "",
            "'Alan' is refused with the three titles, and the typed text is removed",
        )


def scrolling(session: BrowserSession, base: str) -> None:
    print("\n== scrolling: infinite lists ==")
    session.open(f"{base}/feed.html")
    before = session.observe()
    likes = lambda observation: sum(1 for c in observation.lift.controls if c.name == "Like")  # noqa: E731
    check(likes(before) == 20, "the feed starts with 20 posts")
    once = session.scroll(plan_scroll("end"))
    print(f"  ..  {once.to_json()}")
    after = session.observe()
    check(once.loaded and likes(after) == 40, "one scroll to the end loads 20 more posts")
    check(changes(before, after)["lines"][0].startswith("added [41] button 'Like'"), "and the diff names them")

    until = session.scroll(plan_scroll("end"), until=ChangeCondition.from_json({"text": "Post 75"}), timeout_ms=15000)
    print(f"  ..  {until.to_json()}")
    check(until.met is True and until.scrolls >= 1, "scrolling until 'Post 75' stops once it shows")
    end = session.scroll(plan_scroll("end"), until=ChangeCondition.from_json({"text": "Post 999"}), timeout_ms=15000)
    print(f"  ..  {end.to_json()}")
    final = session.observe()
    check(end.met is False and end.end_reached and likes(final) == 100, "the search stops at the end: 100 posts")
    check("You have reached the end" in [m.text for m in final.lift.messages], "and the page says so")

    pane = final.handles[handle_named(session, "Comments")]
    side = session.scroll(
        plan_scroll("end", pane), until=ChangeCondition.from_json({"text": "Comment 35"}), timeout_ms=15000
    )
    print(f"  ..  {side.to_json()}")
    check(side.met is True and not side.page, "a pane scrolls on its own until 'Comment 35' shows")
    top = session.scroll(plan_scroll("top"))
    check(top.moved and not top.loaded, "scrolling to the top moves the page and loads nothing")


def dates(session: BrowserSession, base: str) -> None:
    print("\n== dates: native fields and custom date pickers ==")
    session.open(f"{base}/booking.html")
    view = session.observe()
    check("[2] text 'Check-out' read-only date" in view.view, "the view marks the custom date field")

    def pick(name: str, value: str) -> list[StepResult]:
        before = session.observe()
        results = session.act(handle_named(session, name), "select", value)
        show(results)
        print("\n".join(f"  ~~  {line}" for line in changes(before, session.observe())["lines"]))
        return results

    native = pick("Check-in", "Nov 20, 2026")
    check(native[0].step.op == "fill" and native[0].step.value == "2026-11-20", "a native date field is filled as ISO")
    for name, value, words in (
        ("Check-in", "2026-11-01", "before the earliest"),
        ("Check-in", "11/12/2026", "two dates"),
    ):
        try:
            session.act(handle_named(session, name), "select", value)
            check(False, f"{value!r} is refused")
        except AgentActionError as error:
            check(words in error.message, f"{value!r} is refused before the browser acts: {words}")

    soon = pick("Check-out", "2026-11-23")
    check(
        soon[0].step.op == "choose_date" and soon[0].verified,
        "the check-out calendar, opened by its button, picks a day",
    )
    check(
        session.observe().handles[handle_named(session, "Check-out")].value == "23/11/2026",
        "the field shows 23/11/2026",
    )
    later = pick("Check-out", "2027-02-14")
    check(later[0].verified, "it moves three months ahead and picks 14 February")
    try:
        session.act(handle_named(session, "Check-out"), "select", "2026-11-05")
        check(False, "a past day is refused")
    except BridgeError as error:
        print(f"  !!  {error}")
        check("not available" in str(error), "a past (disabled) day is refused")
    open_days = [c.name for c in session.observe().lift.controls if c.name.isdigit()]
    check(open_days == [], "and the calendar it opened is closed again")
    born = pick("Date of birth", "March 7, 1990")
    check(born[0].verified, "the birth date calendar uses its month and year lists")
    check(
        session.observe().handles[handle_named(session, "Date of birth")].value == "03/07/1990",
        "the field shows 03/07/1990",
    )

    results = session.call(
        "book_a_stay", {"checkin": "2026-12-01", "checkout": "December 5, 2026", "dob": "1985-06-15", "guests": 3}
    )
    show(results)
    [write] = session.blocked_writes()
    body = {key: values[0] for key, values in parse_qs(write["post_data"] or "").items()}
    print(f"  ..  blocked {write['method']} {write['url']}: {body}")
    check(
        body == {"checkin": "2026-12-01", "checkout": "05/12/2026", "dob": "06/15/1985", "guests": "3"},
        "a form call fills the native date and picks both calendar dates",
    )

    # A picker built like flatpickr: month arrows that are bare icons, a month list, a year number input.
    session.open(f"{base}/booking.html")
    session.observe()
    delivery = handle_named(session, "Delivery date")
    picked = session.act(delivery, "select", "Dec 25, 2026")
    show(picked)
    check(
        picked[0].verified and session.observe().handles[delivery].value == "2026-12-25",
        "a calendar whose month arrows are bare icons moves a month ahead and picks 25 December",
    )
    later = session.act(delivery, "select", "Feb 14, 2027")
    show(later)
    check(
        later[0].verified and session.observe().handles[delivery].value == "2027-02-14",
        "and moves across the year to 14 February",
    )
    try:
        session.act(delivery, "select", "2026-11-05")
        check(False, "a past day is refused in it")
    except BridgeError as error:
        print(f"  !!  {error}")
        check(
            "not available" in str(error) and session.observe().handles[delivery].value == "2027-02-14",
            "a past day is refused in it, and the field keeps its date",
        )

    # A field with no label, as jQuery UI's demo has, with a calendar built like jQuery UI's.
    session.open(f"{base}/booking.html")
    view = session.observe()
    date = handle_named(session, "Date")
    check(
        view.handles[date].date, "a field with no label is named by the text before it, and that makes it a date field"
    )
    picked = session.act(date, "select", "Mar 3, 2027")
    show(picked)
    check(
        picked[0].verified and session.observe().handles[date].value == "03/03/2027",
        "its Prev and Next links and its month title (with a non-breaking space) are read, and 3 March is picked",
    )


def uploads(session: BrowserSession, base: str) -> None:
    print("\n== uploads: a file input, and a button that opens a hidden one ==")
    session.open(f"{base}/upload.html")
    view = session.observe()
    check("file 'Résumé' required accepts .pdf,.doc,.docx" in view.view, "the view shows the file input and its types")
    pdf = UploadFile.named("cv.pdf", b"%PDF-1.4 a short CV")

    before = session.observe()
    results = session.act(handle_named(session, "Résumé"), "upload", [pdf])
    show(results)
    lines = changes(before, session.observe())["lines"]
    print("\n".join(f"  ~~  {line}" for line in lines))
    check(results[0].verified, "the CV is set on the file input, and the browser shows it")
    check(any("'' -> 'cv.pdf'" in line for line in lines), "the diff shows the file name in the field")
    for files, words in (
        ([UploadFile.named("cv.exe", b"MZ")], "not a type"),
        ([pdf, pdf], "takes one file"),
    ):
        try:
            session.act(handle_named(session, "Résumé"), "upload", files)
            check(False, f"{words} is refused")
        except AgentActionError as error:
            check(words in error.message, f"the planner refuses it before the browser acts: {words}")

    photos = [UploadFile.named("front.png", b"\x89PNG front"), UploadFile.named("back.jpg", b"\xff\xd8 back")]
    before = session.observe()
    results = session.act(handle_named(session, "Add photos"), "upload", photos)
    show(results)
    after = session.observe()
    print("\n".join(f"  ~~  {line}" for line in changes(before, after)["lines"]))
    check(
        results[0].verified and results[0].chosen == "front.png, back.jpg",
        "the button opens a file chooser, which takes both photos",
    )
    check(any(m.text == "2 photos added" for m in after.lift.messages), "the page says 2 photos added")
    try:
        session.act(handle_named(session, "Add photos"), "upload", [UploadFile.named("notes.txt", b"x")])
        check(False, "a text file is refused for photos")
    except BridgeError as error:
        print(f"  !!  {error}")
        check("not a type the chooser accepts" in str(error), "the chooser's accept refuses a text file")

    show(session.call("apply", {"name": "Bo Diaz", "resume": [{"name": "cv-2026.pdf", "content": "%PDF-1.7"}]}))
    [write] = session.blocked_writes()
    fields = request_fields(write)
    print(f"  ..  blocked {write['method']} {write['url']}: {fields}")
    check(
        fields
        == {
            "name": "Bo Diaz",
            "resume": {"file": "cv-2026.pdf", "bytes": 8, "type": "application/pdf"},
            "photos": [
                {"file": "front.png", "bytes": 10, "type": "image/png"},
                {"file": "back.jpg", "bytes": 7, "type": "image/jpeg"},
            ],
        },
        "the form post (blocked) holds the name, the CV and both photos",
    )


def ranges(session: BrowserSession, base: str) -> None:
    print("\n== ranges: a two-month range picker, a one-field range, and a native pair ==")
    session.open(f"{base}/stay.html")
    view = session.observe()
    check(
        "[1] text 'Check-in' read-only date (range start)" in view.view
        and "[2] text 'Check-out' read-only date (range end)" in view.view
        and "[5] text 'Rental dates' read-only date range" in view.view
        and "[7] date 'From' (range start)" in view.view,
        "the view marks the range fields and the pairs",
    )

    def pick(name: str, value: object) -> tuple[list[StepResult], list[str]]:
        before = session.observe()
        results = session.act(handle_named(session, name), "select", value)
        show(results)
        lines = changes(before, session.observe())["lines"]
        print("\n".join(f"  ~~  {line}" for line in lines))
        return results, lines

    stay, lines = pick("Check-in", "Nov 20, 2026 to Nov 23, 2026")
    check(
        len(stay) == 1 and stay[0].step.op == "choose_date_range" and stay[0].verified,
        "one step picks check-in and check-out in the shared calendar",
    )
    check(
        lines
        == [
            "changed [1] text 'Check-in': value '' -> '20 Nov 2026'",
            "changed [2] text 'Check-out': value '' -> '23 Nov 2026'",
        ],
        "and the diff shows both fields",
    )
    later, _ = pick("Check-out", ["2026-12-28", "2027-01-03"])
    fields = session.observe().handles
    check(
        later[0].verified and (fields[1].value, fields[2].value) == ("28 Dec 2026", "3 Jan 2027"),
        "a range from the second month shown into the next year: 28 Dec, not 28 Nov",
    )
    try:
        session.act(handle_named(session, "Check-in"), "select", "2026-11-23/2026-11-20")
        check(False, "a range that ends before it starts is refused")
    except AgentActionError as error:
        check(
            "before it starts" in error.message, "a range that ends before it starts is refused before the browser acts"
        )
    try:
        session.act(handle_named(session, "Check-in"), "select", "2026-11-05/2026-11-08")
        check(False, "a past start day is refused")
    except BridgeError as error:
        print(f"  !!  {error}")
        check("not available" in str(error), "a past (disabled) start day is refused")
    open_days = [c.name for c in session.observe().lift.controls if c.name.isdigit()]
    check(open_days == [], "and the calendar it opened is closed again")

    rental, lines = pick("Rental dates", "Nov 20, 2026 \u2013 Dec 2, 2026")
    check(
        rental[0].verified and lines == ["changed [5] text 'Rental dates': value '' -> '20/11/2026 \u2013 02/12/2026'"],
        "one field takes a whole range, across two months",
    )
    try:
        session.act(handle_named(session, "Rental dates"), "select", "2026-11-20")
        check(False, "one date is refused for a range field")
    except AgentActionError as error:
        check("a start and an end" in error.message, "one date is refused for a range field")

    native, _ = pick("From", "2026-10-01/2026-10-31")
    check(
        [(r.step.op, r.step.name, r.verified) for r in native] == [("fill", "From", True), ("fill", "To", True)],
        "a range on two native date fields fills both",
    )

    try:
        session.call("find_a_stay", {"checkin": "2026-12-04", "checkout": "2026-12-03"})
        check(False, "a form call with check-out before check-in is refused")
    except AgentActionError as error:
        check(
            [item["argument"] for item in error.details] == ["checkout"],
            "a form call with check-out before check-in is refused, and names check-out",
        )
    results = session.call("find_a_stay", {"checkin": "2026-12-04", "checkout": "December 6, 2026", "guests": 3})
    show(results)
    check(
        [r.step.op for r in results] == ["choose_date_range", "fill", "click"], "a form call picks the pair in one step"
    )
    [write] = session.blocked_writes()
    body = request_fields(write)
    print(f"  ..  blocked {write['method']} {write['url']}: {body}")
    check(body == {"checkin": "4 Dec 2026", "checkout": "6 Dec 2026", "guests": "3"}, "and the post holds both dates")


def downloads(session: BrowserSession, base: str) -> None:
    print("\n== downloads: links, a script-built export, and a plain click ==")
    folder = session.download_dir
    assert folder is not None
    session.open(f"{base}/downloads.html")
    view = session.observe()
    check(
        "[1] link 'Invoice INV-1042' -> files/invoice-1042.pdf download" in view.view
        and "download '../../.profile'" in view.view,
        "the view marks the links that download",
    )

    def fetch(name: str) -> dict[str, object]:
        [result] = session.act(handle_named(session, name), "download")
        saved = result.download or {}
        print(f"  ->  download {name!r}: { ({k: v for k, v in saved.items() if k != 'path'}) }")
        return saved

    invoice = fetch("Invoice INV-1042")
    original = (FIXTURES / "files" / "invoice-1042.pdf").read_bytes()
    check(
        invoice["file"] == "invoice-1042.pdf"
        and invoice["bytes"] == len(original)
        and invoice["sha256"] == hashlib.sha256(original).hexdigest()
        and invoice["type"] == "application/pdf",
        "the invoice is saved, with its size, SHA-256, and type",
    )
    check((folder / "invoice-1042.pdf").read_bytes() == original, "the saved file is the file the server sent")
    again = fetch("Invoice INV-1042")
    check(
        again["file"] == "invoice-1042 (1).pdf" and (folder / "invoice-1042.pdf").read_bytes() == original,
        "a second copy gets a new name; the first is not overwritten",
    )
    prices = fetch("Price list (CSV)")
    check(
        prices["file"] == "price-list.csv" and session.observe().url.endswith("/downloads.html"),
        "a plain link to a file is saved, and the page stays where it was",
    )
    notes = fetch("Setup notes")
    saved = Path(str(notes["path"]))
    check(
        saved.parent == folder and not saved.name.startswith(".") and "/" not in str(notes["file"]),
        f"a suggested name with a path ('../../.profile') is saved inside the folder as {notes['file']!r}",
    )
    export = fetch("Export orders")
    check(
        export["file"] == "orders-2026-11.csv"
        and (folder / "orders-2026-11.csv").read_text().startswith("Order,Date,Total\n1042,2026-11-02,49.00"),
        "a file the page builds by script is saved, with the table's rows",
    )
    before = sorted(path.name for path in folder.iterdir())
    [clicked] = session.act(handle_named(session, "Export orders"), "click")
    check(
        clicked.unsaved_downloads == ("orders-2026-11.csv",) and sorted(p.name for p in folder.iterdir()) == before,
        "a plain click that starts a download names it and saves nothing",
    )
    try:
        session.act(handle_named(session, "Terms of service"), "download")
        check(False, "a link that downloads nothing is reported")
    except BridgeError as error:
        print(f"  !!  {error}")
        check("no download started" in str(error), "a link that downloads nothing is reported")


def main() -> int:
    server, base = serve()
    try:
        with tempfile.TemporaryDirectory() as folder, BrowserSession(download_dir=folder) as session:
            checkout(session, base)
            spa(session, base)
            viewspec_page(session, base)
            shop(session, base)
            waiting(session, base)
            choosing(session, base)
            scrolling(session, base)
            dates(session, base)
            uploads(session, base)
            ranges(session, base)
            downloads(session, base)
    except AssertionError as error:
        print(f"\nFAILED: {error}")
        return 1
    finally:
        server.shutdown()
    print(f"\nAll {len(CHECKS)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
