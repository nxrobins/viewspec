"""A scripted agent that reads pages with the ViewSpec agent lift and acts on them, with checks.

It serves the fixtures on 127.0.0.1, then:

1. checkout: clicks a button inside a web component's shadow DOM, fills card fields inside an
   iframe, makes a bad form tool call (refused before the browser does anything), then a good one,
   and checks the form post that the session blocked.
2. spa: lets the page replace every element, then acts from the old view; the saved locators find
   the new elements. A simulated layout change shows the drift guard refusing a wrong target.
3. viewspec_orders: clicks a ViewSpec action by its data-action-id.

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

from agent_session import BridgeError, BrowserSession, StepResult

from viewspec.raw_html_actions import AgentActionError, plan_control_action

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
    check(session.observe().handles[quantity].value == "3", "a click inside the shadow DOM made it 3")

    card = session.act(handle_named(session, "Card number"), "fill", CARD)
    cvc = session.act(handle_named(session, "CVC"), "fill", "9876")
    show(card + cvc)
    check(all(result.verified for result in card + cvc), "the card fields inside the iframe hold the typed values")
    check(CARD not in session.observe().html, "the next snapshot does not contain the card number")

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
    check(session.observe().lift.roles[0].text == "Write blocked", "the agent now sees the blocked-write page")


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
    except AssertionError as error:
        print(f"\nFAILED: {error}")
        return 1
    finally:
        server.shutdown()
    print(f"\nAll {len(CHECKS)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
