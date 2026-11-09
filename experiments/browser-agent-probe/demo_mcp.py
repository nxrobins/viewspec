"""Drive mcp_browser_server.py as an MCP client over stdio, the way an agent host does, with checks.

It serves the fixtures on 127.0.0.1, starts the server with ``--allow-origin`` set to that server
and ``--upload-dir`` set to a temporary folder, and then uses only the three MCP tools: observe,
act, and call. Every check is an assertion; the script exits 1 on the first failure. Secret values
and file contents are never printed.

Usage:
    python experiments/browser-agent-probe/demo_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from demo_agent import serve
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = Path(__file__).resolve().parent / "mcp_browser_server.py"
PASSWORD = "S3cret-Pass-99"
CARD = "4242424242424242"
CV = b"%PDF-1.4 CV-CONTENT-7781"  # These bytes must never come back in a result.
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


async def run(base: str, folder: Path) -> None:
    # Like a host's MCP config: pass where Playwright keeps its browsers, if that is not the default.
    # NODE_PATH is left out on purpose: the bridge must find Playwright without it.
    env = {key: os.environ[key] for key in ("PLAYWRIGHT_BROWSERS_PATH",) if key in os.environ}
    uploads = folder / "uploads"
    uploads.mkdir()
    (uploads / "cv.pdf").write_bytes(CV)
    (folder / "private.txt").write_text("outside the upload folder")
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER), "--allow-origin", base, "--upload-dir", str(uploads)],
        env=env,
    )
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

        print("\n== infinite scroll ==")
        feed = await tool(session, "observe", url=f"{base}/feed.html")
        check(feed["view"].count("button 'Like'") == 20, "the feed shows 20 posts at first")
        more = await tool(session, "act", action="scroll")
        print("\n".join(f"  ~~  {line}" for line in more["changes"]["lines"][:3]))
        check(more["scroll"]["loaded"] and more["scroll"]["scrolls"] == 1, "one scroll of the page loads more")
        check("added [41] button 'Like'" in more["changes"]["lines"], "and the changes name the new controls")
        until = await tool(session, "act", action="scroll", wait_for={"text": "Post 75"}, timeout_ms=15000)
        check(until["scroll"]["met"] is True, "scrolling until 'Post 75' shows works")
        found = await tool(session, "observe", observation_id=until["observation_id"], find="Post 75")
        like = await tool(session, "act", handle=handle(found, "Like"), action="click")
        check(like["ok"] and like["steps"][0]["resolved_by"] == "ref", "and its Like button can be clicked by handle")
        end = await tool(session, "act", action="scroll", wait_for={"text": "Post 999"}, timeout_ms=15000)
        check(
            end["scroll"]["met"] is False and end["scroll"]["end_reached"] and "end of the list" in end["summary"],
            "scrolling for a post that does not exist stops at the end of the list",
        )
        check("message status 'You have reached the end'" in end["changes"]["lines"], "and the page says so")
        check("'Comments'" not in end["view"], "after 100 posts, the comments pane is past the first slice")
        where = await tool(session, "observe", observation_id=end["observation_id"], find="Comments")
        pane = await tool(
            session,
            "act",
            handle=handle(where, "Comments"),
            action="scroll",
            wait_for={"text": "Comment 35"},
            timeout_ms=15000,
        )
        check(pane["scroll"]["met"] and pane["scroll"]["area"] == "Comments", "a handle scrolls its own pane")
        already = await tool(session, "act", action="scroll", wait_for={"text": "News feed"})
        check(
            already["scroll"]["scrolls"] == 0 and "no scroll was needed" in already["summary"],
            "no scroll when it shows",
        )
        sideways = await tool(session, "act", action="scroll", value="sideways")
        check(
            not sideways["ok"] and sideways["errors"][0]["code"] == "AGENT_ACTION_VALUE_INVALID",
            "a bad direction is refused",
        )
        nothing = await tool(session, "act", action="click")
        check(not nothing["ok"] and nothing["errors"][0]["code"] == "BROWSER_HANDLE_REQUIRED", "click needs a handle")

        print("\n== dates ==")
        booking = await tool(session, "observe", url=f"{base}/booking.html")
        [book] = booking["form_tools"]
        checkout = book["input_schema"]["properties"]["checkout"]
        check(
            checkout["format"] == "date" and "picked from its calendar" in checkout["description"],
            "the form tool says check-out is a date picked from a calendar",
        )
        later = await tool(session, "act", handle=handle(booking, "Check-out"), action="select", value="2027-02-14")
        check(later["ok"] and later["steps"][0]["chosen"] == "2027-02-14", "select picks a date from the calendar")
        check("'Check-out' = '14/02/2027'" in later["view"], "and the field shows it in its own format")
        past = await tool(session, "act", handle=handle(later, "Check-out"), action="select", value="2026-11-05")
        check(not past["ok"] and past["errors"][0]["code"] == "BROWSER_DATE_UNAVAILABLE", "a disabled day is refused")
        unclear = await tool(session, "act", handle=handle(later, "Check-in"), action="select", value="11/12/2026")
        check(
            not unclear["ok"] and "two dates" in unclear["summary"] and "Nothing was done" in unclear["summary"],
            "an ambiguous date is refused before the browser acts",
        )
        booked = await tool(
            session,
            "call",
            tool="book_a_stay",
            arguments={"checkin": "Dec 1, 2026", "checkout": "2026-12-05", "dob": "15 June 1985", "guests": 3},
        )
        fields = booked["blocked_writes"][0]["form_fields"]
        check(
            fields == {"checkin": "2026-12-01", "checkout": "05/12/2026", "dob": "06/15/1985", "guests": "3"},
            "a form call fills and picks every date",
        )

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

        print("\n== uploads ==")
        page = await tool(session, "observe", url=f"{base}/upload.html")
        [form] = page["form_tools"]
        resume = form["input_schema"]["properties"]["resume"]
        check(
            resume["type"] == "array" and resume["maxItems"] == 1 and "accepts .pdf" in resume["description"],
            "the form tool takes the CV as a list of one file, and says which types",
        )
        sent = await tool(session, "act", handle=handle(page, "Résumé"), action="upload", files=[{"path": "cv.pdf"}])
        check(sent["ok"] and sent["steps"][0]["verified"], "a file from the upload folder is set on the file input")
        check(
            sent["steps"][0]["step"]["files"] == [{"name": "cv.pdf", "mime_type": "application/pdf", "size": len(CV)}],
            "the step shows the file's name, type, and size",
        )
        check(
            sent["changes"]["lines"] == ["changed [2] file 'Résumé': value '' -> 'cv.pdf'"],
            "and changes show the file name in the field",
        )
        for path, code in (
            ("../private.txt", "BROWSER_UPLOAD_NOT_ALLOWED"),
            (str(folder / "private.txt"), "BROWSER_UPLOAD_NOT_ALLOWED"),
            ("missing.pdf", "BROWSER_UPLOAD_NOT_FOUND"),
        ):
            refused = await tool(session, "act", handle=handle(page, "Résumé"), action="upload", files=[{"path": path}])
            check(
                not refused["ok"] and refused["errors"][0]["code"] == code,
                f"{path!r} is refused: {code}",
            )
        typed = await tool(
            session, "act", handle=handle(page, "Résumé"), action="upload", files=[{"name": "cv.exe", "content": "MZ"}]
        )
        check(
            not typed["ok"] and typed["errors"][0]["code"] == "AGENT_ACTION_VALUE_INVALID",
            "a file of a type the input does not accept is refused before the browser acts",
        )
        photo = {"name": "dot.png", "content": "iVBORw0KGgo=", "encoding": "base64"}
        added = await tool(
            session,
            "act",
            handle=handle(page, "Add photos"),
            action="upload",
            files=[photo, {**photo, "name": "b.png"}],
        )
        check(
            added["ok"] and added["changes"]["lines"] == ["message status '2 photos added'"],
            "a button that opens a file chooser takes two inline photos",
        )
        wrong = await tool(
            session,
            "act",
            handle=handle(page, "Add photos"),
            action="upload",
            files=[{"name": "notes.txt", "content": "x"}],
        )
        check(
            not wrong["ok"] and wrong["errors"][0]["code"] == "BROWSER_UPLOAD_REFUSED",
            "the chooser refuses a text file: BROWSER_UPLOAD_REFUSED",
        )
        mixed = await tool(session, "call", tool="apply", arguments={"resume": [{"path": "cv.pdf"}, {"name": "b.pdf"}]})
        problems = {(error["argument"], error["problem"]) for error in mixed["errors"]}
        check(
            not mixed["ok"] and problems == {("name", "missing"), ("resume", "invalid")},
            "a form call with a path file still reports every problem at once",
        )
        applied = await tool(
            session, "call", tool="apply", arguments={"name": "Bo Diaz", "resume": [{"path": "cv.pdf"}]}
        )
        fields = applied["blocked_writes"][0]["form_fields"]
        print(f"  ..  {fields}")
        check(
            fields
            == {
                "name": "Bo Diaz",
                "resume": {"file": "cv.pdf", "bytes": len(CV), "type": "application/pdf"},
                "photos": [
                    {"file": "dot.png", "bytes": 8, "type": "image/png"},
                    {"file": "b.png", "bytes": 8, "type": "image/png"},
                ],
            },
            "the blocked form post shows each file's name, size, and type",
        )
        check(
            all(b"CV-CONTENT" not in json.dumps(item).encode() for item in (sent, applied)),
            "no result holds the bytes of a file",
        )

        print("\n== date ranges ==")
        stay = await tool(session, "observe", url=f"{base}/stay.html")
        tools = {item["name"]: item["input_schema"]["properties"] for item in stay["form_tools"]}
        check(
            "the start of a date range that ends at 'checkout'" in tools["find_a_stay"]["checkin"]["description"]
            and tools["hire_a_car"]["rental"]["required"] == ["start", "end"],
            "the form tools describe the pair, and the one-field range as start and end",
        )
        both = await tool(
            session,
            "act",
            handle=handle(stay, "Check-in"),
            action="select",
            value={"start": "2026-11-20", "end": "2026-11-23"},
        )
        print("\n".join(f"  ~~  {line}" for line in both["changes"]["lines"]))
        check(
            both["ok"]
            and both["steps"][0]["step"]["op"] == "choose_date_range"
            and both["steps"][0]["step"]["end"]["name"] == "Check-out"
            and both["steps"][0]["verified"],
            "one act picks check-in and check-out",
        )
        check(len(both["changes"]["lines"]) == 2, "and its changes name both fields")
        backwards = await tool(
            session, "act", handle=handle(stay, "Rental dates"), action="select", value="2026-11-22/2026-11-20"
        )
        check(
            not backwards["ok"] and backwards["errors"][0]["code"] == "AGENT_ACTION_VALUE_INVALID",
            "a range that ends before it starts is refused",
        )
        past = await tool(
            session, "act", handle=handle(stay, "Check-in"), action="select", value=["2026-11-01", "2026-11-03"]
        )
        check(
            not past["ok"] and past["errors"][0]["code"] == "BROWSER_DATE_UNAVAILABLE",
            "a range that starts on a past day is BROWSER_DATE_UNAVAILABLE",
        )
        hired = await tool(
            session, "call", tool="hire_a_car", arguments={"rental": {"start": "2026-11-20", "end": "2026-11-22"}}
        )
        fields = hired["blocked_writes"][0]["form_fields"]
        print(f"  ..  {fields}")
        check(fields == {"rental": "20/11/2026 \u2013 22/11/2026"}, "a form call picks a one-field range")


def first_failure(error: BaseException) -> BaseException:
    """The first exception inside the task groups that the MCP client wraps failures in."""

    while isinstance(error, BaseExceptionGroup):
        error = error.exceptions[0]
    return error


def main() -> int:
    server, base = serve()
    try:
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(run(base, Path(folder)))
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
