"""Re-run the real-site tasks that found the agent lift's bugs, so a change cannot bring one back.

Each case opens one public page in a browser of its own (a dead bridge in one case cannot fail the
rest), does what an agent would, and checks the result. Writes stay blocked; no page is logged
into, nothing is downloaded. The sites change, so a failure here is a reason to look, not proof of
a regression. Writes real_sites_eval.json and prints one PASS or FAIL line per case.

This is an experiment. It is not part of the ViewSpec package or its guarantees.

Usage:
    python experiments/browser-agent-probe/real_sites_eval.py --out .real-sites-eval
    python experiments/browser-agent-probe/real_sites_eval.py --only wikipedia,flatpickr --out .real-sites-eval
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from agent_session import BridgeError, BrowserSession  # noqa: E402

from viewspec.raw_html_actions import AgentActionError, UploadFile, plan_scroll  # noqa: E402
from viewspec.raw_html_diff import ChangeCondition  # noqa: E402

Case = Callable[[BrowserSession, dict[str, Any]], None]
CASES: list[tuple[str, str, Case]] = []


def case(name: str, url: str):
    def wrap(fn: Case) -> Case:
        CASES.append((name, url, fn))
        return fn

    return wrap


def by_name(obs, name: str, *, kind: str | None = None) -> int:
    for handle, control in obs.handles.items():
        if control.name == name and (kind is None or control.kind == kind):
            return handle
    raise LookupError(f"no control named {name!r}")


def steps(results) -> list[tuple[Any, ...]]:
    return [(r.step.op, r.verified, r.chosen, r.navigated) for r in results]


# ---- The bugs the first run found, one case each ---------------------------------------------------


@case("wikipedia names in page order", "https://en.wikipedia.org/wiki/Ada_Lovelace")
def wikipedia_names(s: BrowserSession, rec: dict[str, Any]) -> None:
    obs = s.observe()
    names = {c.name for c in obs.handles.values()}
    rows = [line.text for line in obs.lines if line.kind == "row" and line.text.startswith("row Known for")]
    rec["known_for"] = rows[:1]
    rec["ok"] = "Doctor Who, series 12" in names and bool(rows) and "Mathematics, computing" in rows[0]


@case("wikipedia search suggestion", "https://en.wikipedia.org/wiki/Ada_Lovelace")
def wikipedia_search(s: BrowserSession, rec: dict[str, Any]) -> None:
    obs = s.observe()
    handle = by_name(obs, "Search Wikipedia", kind="field")
    results = s.act(handle, "select", "Alan Turing")
    rec["steps"] = steps(results)
    rec["url"] = s.observe().url
    rec["ok"] = results[0].chosen == "Alan Turing" and results[0].navigated and rec["url"].endswith("/wiki/Alan_Turing")


@case("python.org tree items keep their links", "https://www.python.org/")
def python_org(s: BrowserSession, rec: dict[str, Any]) -> None:
    links = {c.name: c.href for c in s.observe().handles.values() if c.control == "link"}
    rec["ok"] = links.get("Applications") is not None and "Getting Started" in links


@case(
    "APG combobox: wrapper gives way, key-press list",
    "https://www.w3.org/WAI/ARIA/apg/patterns/combobox/examples/combobox-autocomplete-list/",
)
def apg_combobox(s: BrowserSession, rec: dict[str, Any]) -> None:
    results = s.act(by_name(s.observe(), "State"), "select", "Calif")
    rec["steps"] = steps(results)
    try:
        s.act(by_name(s.observe(), "State"), "select", "New")
        rec["ambiguous"] = "not refused"
    except BridgeError as error:
        rec["ambiguous"] = str(error)[:140]
    rec["ok"] = (
        results[0].chosen == "California" and results[0].verified is True and "New Hampshire" in rec["ambiguous"]
    )


@case("react-select: value beside the input, read-only opens", "https://react-select.com/home")
def react_select(s: BrowserSession, rec: dict[str, Any]) -> None:
    obs = s.observe()
    combos = [h for h, c in obs.handles.items() if c.control == "combobox"]
    searchable = s.act(combos[1], "select", "Red")
    after = s.observe()
    rec["searchable"] = steps(searchable) + [after.handles[combos[1]].value]
    read_only = s.act(combos[0], "select", "CSS-in-JS styling API")
    rec["read_only"] = steps(read_only)
    rec["ok"] = searchable[0].verified is True and after.handles[combos[1]].value == "Red" and read_only[0].navigated


@case("gov.uk search suggestion submits", "https://www.gov.uk/")
def govuk(s: BrowserSession, rec: dict[str, Any]) -> None:
    obs = s.observe()
    [tool] = [t["name"] for t in obs.lift.form_tool_schemas() if "search" in t["name"]]
    try:
        s.call(tool, {"keywords": "renew passport"})
        rec["call"] = "ran to the end"
    except BridgeError as error:
        rec["call"] = str(error)[:160]
    rec["url"] = s.observe().url
    rec["ok"] = "navigated after step 1 of 2" in rec["call"] and "keywords=renew+passport" in rec["url"]


@case("flatpickr: icon arrows, the right calendar", "https://flatpickr.js.org/examples/")
def flatpickr(s: BrowserSession, rec: dict[str, Any]) -> None:
    obs = s.observe()
    handle = next(h for h, c in obs.handles.items() if c.date)
    results = s.act(handle, "select", "Feb 14, 2027")
    rec["steps"] = steps(results)
    rec["value"] = s.observe().handles[handle].value
    rec["ok"] = results[0].verified is True and rec["value"] == "2027-02-14"


@case("httpbin: bad email refused, blocked submit reported", "https://httpbin.org/forms/post")
def httpbin(s: BrowserSession, rec: dict[str, Any]) -> None:
    obs = s.observe()
    try:
        s.call("pizza_size", {"custname": "Test Agent", "custemail": "not-an-email", "size": "medium"})
        rec["call"] = "accepted"
    except AgentActionError as error:
        rec["call"] = [(p["argument"], p["message"]) for p in error.details]
    s.act(next(h for h, c in obs.handles.items() if c.control == "email"), "fill", "not-an-email")
    [clicked] = s.act(by_name(obs, "Submit order"), "click")
    rec["invalid"] = [f"{i['name']}: {i['message']}" for i in clicked.invalid]
    rec["ok"] = (
        isinstance(rec["call"], list)
        and rec["call"][0][0] == "custemail"
        and not clicked.navigated
        and bool(clicked.invalid)
        and "@" in clicked.invalid[0]["message"]
    )


@case(
    "jQuery UI: unlabelled field, nbsp title, redraws",
    "https://jqueryui.com/resources/demos/datepicker/dropdown-month-year.html",
)
def jquery_ui(s: BrowserSession, rec: dict[str, Any]) -> None:
    obs = s.observe()
    handle = next(h for h, c in obs.handles.items() if c.kind == "field")
    rec["name"], rec["date"] = obs.handles[handle].name, obs.handles[handle].date
    results = s.act(handle, "select", "Mar 3, 2027")
    rec["steps"] = steps(results)
    rec["value"] = s.observe().handles[handle].value
    rec["ok"] = rec["name"] == "Date" and rec["date"] and results[0].verified is True and rec["value"] == "03/03/2027"


@case("quotes.toscrape: scroll until loads items", "https://quotes.toscrape.com/scroll")
def quotes(s: BrowserSession, rec: dict[str, Any]) -> None:
    s.observe()
    once = s.scroll(plan_scroll("end"))
    until = s.scroll(plan_scroll("end"), until=ChangeCondition.from_json({"text": "Ayn Rand"}), timeout_ms=20000)
    rec["once"], rec["until"] = once.to_json(), until.to_json()
    rec["ok"] = once.loaded and until.met is True


@case("the-internet: checkboxes named by the text beside them", "https://the-internet.herokuapp.com/checkboxes")
def checkboxes(s: BrowserSession, rec: dict[str, Any]) -> None:
    obs = s.observe()
    rec["names"] = [c.name for c in obs.handles.values() if c.control == "checkbox"]
    results = s.act(by_name(obs, "checkbox 1"), "check")
    rec["ok"] = rec["names"] == ["checkbox 1", "checkbox 2"] and results[0].verified is True


# ---- What worked before the fixes, and must still ---------------------------------------------------


@case("the-internet: wait for a delayed element", "https://the-internet.herokuapp.com/dynamic_loading/2")
def dynamic_loading(s: BrowserSession, rec: dict[str, Any]) -> None:
    obs = s.observe()
    s.act(by_name(obs, "Start"), "click")
    waited = s.wait(ChangeCondition.from_json({"text": "Hello World!"}), obs, timeout_ms=10000)
    rec["wait"] = waited.to_json()
    rec["ok"] = waited.met


@case("the-internet: upload a file", "https://the-internet.herokuapp.com/upload")
def upload(s: BrowserSession, rec: dict[str, Any]) -> None:
    obs = s.observe()
    handle = next(h for h, c in obs.handles.items() if c.control == "file")
    results = s.act(handle, "upload", [UploadFile.named("notes.txt", b"hello\n")])
    rec["steps"] = steps(results)
    rec["ok"] = results[0].verified is True and results[0].chosen == "notes.txt"


@case("APG date picker dialog", "https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/examples/datepicker-dialog/")
def apg_datepicker(s: BrowserSession, rec: dict[str, Any]) -> None:
    obs = s.observe()
    results = s.act(next(h for h, c in obs.handles.items() if c.date), "select", "Feb 14, 2027")
    rec["steps"] = steps(results)
    rec["ok"] = results[0].verified is True


@case("books.toscrape: paginate and follow a category", "https://books.toscrape.com/")
def books(s: BrowserSession, rec: dict[str, Any]) -> None:
    first = s.act(by_name(s.observe(), "next"), "click")
    second = s.act(by_name(s.observe(), "Mystery"), "click")
    rec["url"] = s.observe().url
    rec["ok"] = first[0].navigated and second[0].navigated and "mystery" in rec["url"]


@case("hacker news: More", "https://news.ycombinator.com/")
def hacker_news(s: BrowserSession, rec: dict[str, Any]) -> None:
    results = s.act(by_name(s.observe(), "More"), "click")
    rec["url"] = s.observe().url
    rec["ok"] = results[0].navigated and "p=2" in rec["url"]


@case("the-internet: infinite scroll loads", "https://the-internet.herokuapp.com/infinite_scroll")
def infinite_scroll(s: BrowserSession, rec: dict[str, Any]) -> None:
    s.observe()
    scrolled = s.scroll(plan_scroll("end"))
    rec["scroll"] = scrolled.to_json()
    rec["ok"] = scrolled.loaded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True, help="write real_sites_eval.json here")
    parser.add_argument("--only", help="comma-separated words; run the cases whose names contain one of them")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    words = [word.strip().lower() for word in args.only.split(",")] if args.only else []
    selected = [item for item in CASES if not words or any(word in item[0].lower() for word in words)]

    results: list[dict[str, Any]] = []
    for name, url, fn in selected:
        rec: dict[str, Any] = {"case": name, "url": url, "ok": False}
        start = time.monotonic()
        try:
            with BrowserSession() as session:
                session.open(url)
                fn(session, rec)
        except (AgentActionError, BridgeError) as error:
            rec["error"] = f"{type(error).__name__}: {str(error)[:200]}"
        except Exception as error:  # noqa: BLE001
            rec["error"] = f"{type(error).__name__}: {str(error)[:200]}"
            rec["trace"] = traceback.format_exc()[-600:]
        rec["seconds"] = round(time.monotonic() - start, 1)
        note = f"  -- {rec['error']}" if rec.get("error") else ""
        print(f"{'PASS' if rec['ok'] else 'FAIL'}  {name}  ({rec['seconds']} s){note}", flush=True)
        results.append(rec)
    (args.out / "real_sites_eval.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    passed = sum(1 for r in results if r["ok"])
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
