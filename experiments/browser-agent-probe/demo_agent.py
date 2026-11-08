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

After each action in checkout and shop, the script also checks the diff between the reading before
and the reading after (``agent_session.changes``).

Every check is an assertion. The script prints a transcript and exits 1 on the first failure.
Secret values are never printed.

Usage:
    python experiments/browser-agent-probe/demo_agent.py
"""

from __future__ import annotations

import functools
import http.server
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs

from agent_session import BridgeError, BrowserSession, StepResult, changes

from viewspec.raw_html_actions import AgentActionError, plan_control_action
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


def main() -> int:
    server, base = serve()
    try:
        with BrowserSession() as session:
            checkout(session, base)
            spa(session, base)
            viewspec_page(session, base)
            shop(session, base)
            waiting(session, base)
    except AssertionError as error:
        print(f"\nFAILED: {error}")
        return 1
    finally:
        server.shutdown()
    print(f"\nAll {len(CHECKS)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
