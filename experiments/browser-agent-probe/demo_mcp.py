"""Drive mcp_browser_server.py as an MCP client over stdio, the way an agent host does, with checks.

It serves the fixtures on 127.0.0.1, starts the server with ``--allow-origin`` set to that server,
and then uses only the three MCP tools: observe, act, and call. Every check is an assertion; the
script exits 1 on the first failure. Secret values are never printed.

Usage:
    python experiments/browser-agent-probe/demo_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from demo_agent import serve
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent / "mcp_browser_server.py"
PASSWORD = "S3cret-Pass-99"
CARD = "4242424242424242"
CHECKS: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    CHECKS.append(message)
    print(f"  ok  {message}")


def handle(result: dict[str, Any], name: str) -> int:
    match = re.search(rf"^\[(\d+)\] \S+ {re.escape(repr(name))}", result["view"], re.MULTILINE)
    assert match, f"no control named {name!r} in the view"
    return int(match.group(1))


async def tool(session: ClientSession, name: str, **arguments: Any) -> dict[str, Any]:
    reply = await session.call_tool(name, arguments)
    result = reply.structuredContent or json.loads(reply.content[0].text)  # type: ignore[union-attr]
    status = "ok" if result["ok"] else "FAILED " + ", ".join(error["code"] for error in result["errors"])
    shown = ", ".join(f"{key}={value!r}" for key, value in arguments.items() if key != "arguments")
    print(f"  ->  {name}({shown}) {status}: {result['summary']}")
    return result


async def run(base: str) -> None:
    # Like a host's MCP config: pass where Playwright keeps its browsers, if that is not the default.
    # NODE_PATH is left out on purpose: the bridge must find Playwright without it.
    env = {key: os.environ[key] for key in ("PLAYWRIGHT_BROWSERS_PATH",) if key in os.environ}
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER), "--allow-origin", base], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        print("\n== the server ==")
        listed = {item.name: item for item in (await session.list_tools()).tools}
        check(set(listed) == {"observe", "act", "call"}, "the server offers observe, act, and call")
        check(
            all(listed[name].annotations.destructiveHint is False for name in ("act", "call")),
            "with writes blocked, act and call say they change nothing outside the browser",
        )

        print("\n== checkout ==")
        first = await tool(session, "observe", url=f"{base}/checkout.html")
        print("\n".join(f"     {line}" for line in first["view"].splitlines()[:6]) + "\n     ...")
        [form] = first["form_tools"]
        check(form["name"] == "checkout", "the form is offered as the tool 'checkout'")
        check("x-node-id" not in json.dumps(form), "the tool schema has no internal node ids")

        more = await tool(session, "act", handle=handle(first, "Increase quantity"), action="click")
        check("'Quantity' = '3'" in more["view"], "a click in the shadow DOM shows quantity 3 in the new view")
        check(
            more["changes"]["lines"] == ["changed [3] text 'Quantity': value '2' -> '3'"],
            "and changes names that one change",
        )

        card = await tool(session, "act", handle=handle(more, "Card number"), action="fill", value=CARD)
        check(
            card["ok"] and card["steps"][0]["step"]["value"] == "[redacted]", "the card number fill is logged redacted"
        )
        check(CARD not in json.dumps(card), "the card number appears nowhere in the result")
        check(card["changes"]["lines"] == ["no change"], "changes never shows a secret value")

        wrong = await tool(session, "act", handle=handle(card, "Email"), action="check")
        check(
            not wrong["ok"] and wrong["errors"][0]["code"] == "AGENT_ACTION_UNSUPPORTED",
            "checking a text box is refused",
        )
        check("Nothing was done" in wrong["summary"], "and the result says nothing was done")

        bad = await tool(session, "call", tool="checkout", arguments={"ship": "overnight", "colour": "red"})
        problems = {(error["argument"], error["problem"]) for error in bad["errors"]}
        check(
            not bad["ok"] and problems == {("ship", "invalid"), ("colour", "unknown"), ("terms", "missing")},
            "a bad form call is refused with each problem named by argument",
        )

        before = card["observation_id"]
        arguments = {
            "email": "bo@example.com",
            "ship": "express",
            "country": "Japan",
            "new_password": PASSWORD,
            "terms": True,
        }
        done = await tool(session, "call", tool="checkout", arguments=arguments, wait_for={"change": "navigation"})
        check(done["ok"] and done["navigated"], "the form call ran and the submit navigated")
        check(done["wait"]["met"] and done["wait"]["for"] == "a new page", "and the wait for a new page was met")
        check(len(done["steps"]) == 6, "it ran 6 steps")
        [write] = done["blocked_writes"]
        fields = write["form_fields"]
        print(f"  ..  blocked {write['method']} {write['url']}: {fields}")
        check(fields["new_password"] == "[redacted]", "the blocked post shows the password field redacted")
        check(fields["email"] == "bo@example.com" and fields["terms"] == "on", "and the other fields in clear")
        check(PASSWORD not in json.dumps(done), "the password appears nowhere in the result")
        check("Write blocked" in done["view"], "the new view is the blocked-write page")
        check(done["changes"]["new_document"], "changes says the form led to a new page")

        stale = await tool(session, "act", handle=1, action="click", observation_id=before)
        check(
            not stale["ok"] and stale["errors"][0]["code"] == "BROWSER_OBSERVATION_STALE",
            "acting from a view of the page that is gone is refused",
        )

        print("\n== origins ==")
        away = await tool(session, "observe", url="https://brand.example/")
        check(not away["ok"] and away["errors"][0]["code"] == "BROWSER_ORIGIN_NOT_ALLOWED", "another origin is refused")
        shop = await tool(session, "observe", url=f"{base}/shop.html")
        click = await tool(session, "act", handle=handle(shop, "Brand site"), action="click")
        check(click["ok"] and not click["navigated"], "a link to another origin does not navigate")
        check(
            click.get("blocked_navigations", [{}])[0].get("url") == "https://brand.example/about",
            "and the blocked navigation is reported",
        )
        check(click["url"].endswith("/shop.html"), "the agent is still on the shop page")

        print("\n== changes ==")
        start = click["observation_id"]
        cart = await tool(session, "act", handle=handle(click, "Add to cart"), action="click")
        print("\n".join(f"  ~~  {line}" for line in cart["changes"]["lines"]))
        check(
            cart["changes"]["lines"][0] == "message status 'Arc lamp added to cart'", "the status message comes first"
        )
        loaded = await tool(session, "act", handle=handle(cart, "Load more"), action="click")
        check("text + heading 'Desk organizer'" in loaded["changes"]["lines"], "Load more shows the new product")
        since = await tool(session, "observe", since=start)
        print("\n".join(f"  ~~  {line}" for line in since["changes"]["lines"]))
        check(
            "message status 'Arc lamp added to cart'" in since["changes"]["lines"]
            and "text + heading 'Desk organizer'" in since["changes"]["lines"],
            "observe(since=...) shows both actions' changes together",
        )
        mixed = await tool(session, "observe", url=f"{base}/shop.html", since=start)
        check(not mixed["ok"] and mixed["errors"][0]["code"] == "BROWSER_INVALID_REQUEST", "url with since is refused")

        print("\n== waiting ==")
        spa = await tool(session, "observe", url=f"{base}/spa.html")
        bad = await tool(
            session, "act", handle=handle(spa, "Export"), action="click", wait_for={"text": "a", "gone": "b"}
        )
        check(not bad["ok"] and bad["errors"][0]["code"] == "AGENT_WAIT_INVALID", "a bad wait_for is refused")
        check("Nothing was done" in bad["summary"], "before the browser does anything")
        export = await tool(
            session, "act", handle=handle(spa, "Export"), action="click", wait_for={"text": "Download CSV"}
        )
        print("\n".join(f"  ~~  {line}" for line in export["changes"]["lines"]))
        check(export["wait"]["met"] and export["wait"]["waited_ms"] >= 400, "act waited for the delayed link")
        check(
            export["changes"]["lines"]
            == ["message status 'Export ready'", f"added [{handle(export, 'Download CSV')}] link 'Download CSV'"],
            "and its changes show the message and the new link",
        )
        late = await tool(session, "observe", wait_for={"text": "Never shown"}, timeout_ms=600)
        check(late["ok"] and not late["wait"]["met"], "a wait that is not met is not an error")
        check("did not show" in late["summary"], "the summary says the change did not show")

        print("\n== choosing ==")
        shop = await tool(session, "observe", url=f"{base}/checkout.html")
        tools = {item["name"]: item for item in shop["form_tools"]}
        check(
            "pick an option" in tools["checkout"]["input_schema"]["properties"]["country"]["description"],
            "the form tool says country is picked from a list",
        )
        swe = await tool(session, "act", handle=handle(shop, "Country"), action="select", value="Swe")
        check(swe["ok"] and swe["steps"][0]["chosen"] == "Sweden", "select chose Sweden from the delayed list")
        check("'Country' = 'Sweden'" in swe["view"], "and the new view shows it")
        many = await tool(session, "act", handle=handle(swe, "Country"), action="select", value="S")
        check(
            not many["ok"]
            and many["errors"][0]["code"] == "BROWSER_OPTION_AMBIGUOUS"
            and "Switzerland" in many["summary"],
            "an ambiguous choice is refused, with the options it matched",
        )
        check("'Country' = 'Sweden'" in many["view"], "and the field keeps its value")
        wrap = await tool(session, "act", handle=handle(swe, "Gift wrap"), action="select", value="Paper")
        check(wrap["ok"] and "'Gift wrap' = 'Paper'" in wrap["view"], "a click-to-open combobox chose Paper")

        print("\n== long pages ==")
        catalog = await tool(session, "observe", url=f"{base}/catalog.html")
        reading = catalog["observation_id"]
        page = catalog["view_page"]
        print(f"  ..  {page}")
        check(page["total"] > 1000 and page["returned"] == 200, "a long view comes as its first 200 lines")
        check(len(catalog["view"].splitlines()) == 200, "and the view holds exactly those lines")
        check(
            f"observe(observation_id={reading}, offset=200)" in catalog["summary"], "the summary says how to get more"
        )

        second = await tool(session, "observe", observation_id=reading, offset=200)
        check(second["observation_id"] == reading, "paging a kept reading does not read the page again")
        print(f"  ..  context: {second['view_page'].get('context')}")
        check(
            second["view_page"]["context"][:2] == ["H1 Lamps and lighting", "H2 Floor lamps"],
            "the next slice starts with the headings it sits under",
        )

        handles: list[int] = []
        offset: int | None = 0
        while offset is not None:
            part = await tool(session, "observe", observation_id=reading, offset=offset, limit=1000)
            handles += [int(found) for found in re.findall(r"^\[(\d+)\]", part["view"], re.MULTILINE)]
            offset = part["view_page"]["next_offset"]
        check(
            handles == list(range(1, len(handles) + 1)),
            f"all slices together hold handles 1 to {len(handles)}, once each",
        )

        found = await tool(session, "observe", observation_id=reading, find="lamp 250")
        print("\n".join(f"     {line}" for line in found["view"].splitlines()))
        add = handle(found, "Add to cart")
        check(
            add > 200 and found["view_page"]["matches"] == 1,
            "find shows one product, deep in the page, with its button",
        )
        bought = await tool(
            session, "act", handle=add, action="click", observation_id=reading, wait_for={"change": "message"}
        )
        print("\n".join(f"  ~~  {line}" for line in bought["changes"]["lines"]))
        check(
            bought["changes"]["lines"][0] == "message status 'Kindle vintage lamp 250 added to cart'"
            and f"changed [{add}] button 'Added': name 'Add to cart' -> 'Added'; disabled false -> true"
            in bought["changes"]["lines"],
            "acting on a handle from a found line works, and the changes name it",
        )
        check(bought["view_page"]["returned"] == 200, "the view in an act result is a slice too")

        none = await tool(session, "observe", observation_id=reading, find="no such lamp")
        check(none["view"] == "" and "No line contains" in none["summary"], "find with no match says so")
        wide = await tool(session, "observe", observation_id=reading, limit=5000)
        check(wide["view_page"]["returned"] == 1000, "a slice is at most 1000 lines")
        mixed = await tool(session, "observe", observation_id=reading, url=f"{base}/catalog.html")
        check(
            not mixed["ok"] and mixed["errors"][0]["code"] == "BROWSER_INVALID_REQUEST",
            "observation_id with url is refused",
        )

        print("\n== viewspec_orders ==")
        orders = await tool(session, "observe", url=f"{base}/viewspec_orders.html")
        check("#refund" in orders["view"], "the view shows the ViewSpec action id")
        refund = await tool(session, "act", handle=handle(orders, "Refund order"), action="click")
        check(refund["ok"] and refund["steps"][0]["resolved_by"] == "ref", "the click used the exact element")


def first_failure(error: BaseException) -> BaseException:
    """The first exception inside the task groups that the MCP client wraps failures in."""

    while isinstance(error, BaseExceptionGroup):
        error = error.exceptions[0]
    return error


def main() -> int:
    server, base = serve()
    try:
        asyncio.run(run(base))
    except (AssertionError, BaseExceptionGroup) as error:
        failure = first_failure(error)
        print(f"\nFAILED: {type(failure).__name__}: {failure}")
        return 1
    finally:
        server.shutdown()
    print(f"\nAll {len(CHECKS)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
