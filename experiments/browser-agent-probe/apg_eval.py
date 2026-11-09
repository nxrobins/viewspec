"""Measure the agent lift against the W3C ARIA Authoring Practices examples.

The APG publishes a reference implementation of each ARIA widget pattern. This script opens every
example page listed in the APG example index, keeps the controls inside the example (an element
whose id is ``ex1``, ``ex2``, ...; never the page template's CodePen button), and acts once on one
control of each kind the way an agent would: check a checkbox or a switch, click a tab or a tree
item that is not yet selected, pick a listbox or combobox option, fill a text box, pick a date. A
page is opened again before each action, so one action does not change what the next one sees.

An action counts when the bridge verified it, or the page navigated, or the diff shows a change.
The report says which kinds worked, which did not, and why. Reading quality (controls found, names,
locators, leaks) is ``probe.py``'s job: run it on the same URLs.

This is an experiment. It is not part of the ViewSpec package or its guarantees.

Usage:
    python experiments/browser-agent-probe/apg_eval.py --out .apg-eval
    python experiments/browser-agent-probe/apg_eval.py --urls apg_urls.txt --limit 5 --out .apg-eval
    python experiments/browser-agent-probe/probe.py $(cat .apg-eval/apg_urls.txt) --out .apg-eval/probe
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from agent_session import BridgeError, BrowserSession, changes  # noqa: E402

from viewspec.raw_html_actions import AgentActionError  # noqa: E402
from viewspec.raw_html_diff import ChangeCondition  # noqa: E402

EXAMPLE_INDEX = "https://www.w3.org/WAI/ARIA/apg/example-index/"

# How to act on a control of each kind. A link navigates away, a slider and a spin button take keys
# the planner does not send, and a file input needs a file: those kinds are not acted on.
CLICK = {"button", "tab", "treeitem", "menuitem", "disclosure", "menuitemcheckbox", "menuitemradio", "clickable"}
CHECK = {"checkbox", "switch", "radio"}
FILL = {"text", "textbox", "searchbox", "textarea", "email", "search", "number", "tel", "url"}
CHOOSE = {"combobox", "listbox", "select"}
SKIP = {"link", "slider", "spinbutton", "submit", "reset", "image", "file", "select-multiple", "focusable"}
# Items where the first one is usually the selected one or the current page: act on another.
ITEMS = {"tab", "treeitem", "menuitem", "menuitemradio", "radio"}


def example_urls(session: BrowserSession) -> list[str]:
    """Every example page the APG example index links to."""

    session.open(EXAMPLE_INDEX)
    obs = session.observe()
    urls: list[str] = []
    for control in obs.handles.values():
        if control.control == "link" and control.href and "/patterns/" in control.href and "/examples/" in control.href:
            url = urljoin(obs.url, control.href).split("#")[0]
            if url.endswith("/") and url not in urls:
                urls.append(url)
    return urls


def example_handles(obs) -> list[int]:
    """Handles of the controls inside the example container, or else under a heading that says
    "Example". The CodePen button of the page template is not one."""

    by_id = {node.id: node for node in obs.lift.nodes}
    roots = {node.id for node in obs.lift.nodes if re.fullmatch(r"ex\d+", node.attrs.get("id", ""))}

    def inside(node_id: str) -> bool:
        node = by_id.get(node_id)
        while node is not None:
            if node.id in roots:
                return True
            node = by_id.get(node.parent_id) if node.parent_id else None
        return False

    headings = {line.node_id: line.text for line in obs.lines if line.kind == "heading"}
    handles = []
    for line in obs.lines:
        if line.kind != "control":
            continue
        control = obs.handles[line.handle]
        if "codepen" in control.name.lower():
            continue
        if roots:
            if inside(control.node_id):
                handles.append(line.handle)
        elif re.search(r"\bexample", headings.get(line.heading_id or "", ""), re.IGNORECASE):
            handles.append(line.handle)
    return handles


def pick_controls(obs, handles: list[int]) -> list[int]:
    """One control of each kind to act on, as an agent would choose it."""

    by_kind: dict[str, list[int]] = {}
    for handle in handles:
        control = obs.handles[handle]
        if control.control in SKIP or control.disabled or control.readonly:
            continue
        by_kind.setdefault(control.control, []).append(handle)
    chosen: list[int] = []
    for kind, candidates in by_kind.items():
        if kind in ITEMS:
            unselected = [h for h in candidates if not obs.handles[h].selected and not obs.handles[h].checked]
            pool = unselected or candidates
            chosen.append(pool[1] if len(pool) > 1 else pool[0])
        elif kind == "button":
            stateful = [
                h for h in candidates if obs.handles[h].pressed is not None or obs.handles[h].expanded is not None
            ]
            chosen.append((stateful or candidates)[0])
        else:
            chosen.append(candidates[0])
    return chosen


def act_on(session: BrowserSession, obs, handle: int) -> dict[str, Any]:
    control = obs.handles[handle]
    kind = control.control
    rec: dict[str, Any] = {"kind": kind, "name": control.name[:60]}
    try:
        if control.date:
            rec["action"] = "select Feb 14, 2027"
            results = session.act(handle, "select", "Feb 14, 2027")
        elif kind in CHOOSE:
            options = [o for o in control.options if not o.disabled and not o.selected]
            if not options and kind == "combobox":
                # As an agent would: open or type, read the options the lift now lists, pick the first.
                if control.tag == "input" and not control.readonly:
                    session.act(handle, "fill", "a")
                else:
                    session.act(handle, "click")
                session.wait(ChangeCondition.from_json({"change": "any"}), obs, timeout_ms=1500)
                obs = session.observe()
                suggestions = (
                    [o for o in obs.handles[handle].options if not o.disabled] if handle in obs.handles else []
                )
                if not suggestions:
                    rec["skipped"] = "opening showed no options the lift could list"
                    return rec
                rec["action"] = f"open, then select {suggestions[0].label[:30]!r}"
                results = session.act(handle, "select", suggestions[0].label)
            elif not options:
                rec["skipped"] = "no options"
                return rec
            else:
                rec["action"] = f"select {options[0].label[:30]!r}"
                results = session.act(handle, "select", options[0].label)
        elif kind in CHECK:
            if kind == "radio" and control.checked:
                rec["skipped"] = "already checked radio"
                return rec
            rec["action"] = "uncheck" if control.checked else "check"
            results = session.act(handle, rec["action"])
        elif kind in FILL:
            rec["action"] = "fill 'test'"
            results = session.act(handle, "fill", "test")
        elif kind in CLICK:
            rec["action"] = "click"
            results = session.act(handle, "click")
        else:
            rec["skipped"] = "no action for this kind"
            return rec
        step = results[0]
        rec["verified"] = step.verified
        rec["navigated"] = step.navigated
        rec["resolved_by"] = step.resolved_by
        if step.chosen:
            rec["chosen"] = step.chosen[:60]
        if not step.navigated:
            diff = changes(obs, session.observe())
            rec["changed"] = not diff["empty"]
            rec["diff"] = diff["lines"][:3]
        rec["ok"] = step.verified is not False and (
            step.navigated or rec.get("changed", False) or step.verified is True
        )
    except AgentActionError as error:
        rec["refused"] = f"{error.code}: {error.message[:120]}"
        rec["ok"] = False
    except BridgeError as error:
        rec["error"] = str(error)[:160]
        rec["ok"] = False
    return rec


def run_page(url: str) -> dict[str, Any]:
    page: dict[str, Any] = {"url": url, "pattern": url.split("/patterns/", 1)[1].rstrip("/"), "actions": []}
    with BrowserSession() as session:
        session.open(url)
        obs = session.observe()
        handles = example_handles(obs)
        page["controls_in_example"] = len(handles)
        page["kinds"] = dict(Counter(obs.handles[h].control for h in handles))
        page["unnamed"] = sum(1 for h in handles if not obs.handles[h].name)
        page["untried_kinds"] = sorted(kind for kind in page["kinds"] if kind in SKIP)
        for handle in pick_controls(obs, handles):
            kind = obs.handles[handle].control
            session.open(url)  # A fresh page for each action.
            fresh = session.observe()
            if handle not in fresh.handles or fresh.handles[handle].control != kind:
                continue
            page["actions"].append(act_on(session, fresh, handle))
    return page


def report(pages: list[dict[str, Any]]) -> str:
    tried = [a for p in pages for a in p["actions"] if "skipped" not in a]
    ok = sum(1 for a in tried if a.get("ok"))
    by_kind: dict[str, list[int]] = {}
    for action in tried:
        tally = by_kind.setdefault(action["kind"], [0, 0])
        tally[0] += int(bool(action.get("ok")))
        tally[1] += 1
    lines = [f"# APG action eval: {ok}/{len(tried)} actions succeeded on {len(pages)} pages", ""]
    lines += ["| kind | ok | tried |", "|---|---:|---:|"]
    lines += [
        f"| {kind} | {k_ok} | {k_tried} |"
        for kind, (k_ok, k_tried) in sorted(by_kind.items(), key=lambda kv: -kv[1][1])
    ]
    lines += ["", "| pattern | controls | actions ok | failures |", "|---|---:|---:|---|"]
    for page in pages:
        acts = [a for a in page["actions"] if "skipped" not in a]
        fails = [
            f"{a['kind']}: {a.get('refused') or a.get('error') or 'not verified, no change'}"
            for a in acts
            if not a.get("ok")
        ]
        error = f" ERROR {page['error']}" if page.get("error") else ""
        lines.append(
            f"| {page['pattern']} | {page.get('controls_in_example', '?')} | "
            f"{sum(1 for a in acts if a.get('ok'))}/{len(acts)} | {'; '.join(fails)[:200]}{error} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--out", type=Path, required=True, help="write apg_urls.txt, apg_eval.json, and apg_eval.md here"
    )
    parser.add_argument(
        "--urls", type=Path, help="a file of example URLs, one per line (default: read the APG example index)"
    )
    parser.add_argument("--limit", type=int, help="run at most N pages")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    if args.urls:
        urls = [line.strip() for line in args.urls.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        with BrowserSession() as session:
            urls = example_urls(session)
    if args.limit:
        urls = urls[: args.limit]
    (args.out / "apg_urls.txt").write_text("\n".join(urls) + "\n", encoding="utf-8")
    print(f"{len(urls)} example pages", flush=True)

    pages: list[dict[str, Any]] = []
    for url in urls:
        start = time.monotonic()
        try:
            page = run_page(url)
        except Exception as error:  # noqa: BLE001
            page = {
                "url": url,
                "pattern": url.split("/patterns/", 1)[1].rstrip("/"),
                "actions": [],
                "error": f"{type(error).__name__}: {str(error)[:200]}",
                "trace": traceback.format_exc()[-500:],
            }
        page["seconds"] = round(time.monotonic() - start, 1)
        acts = [a for a in page["actions"] if "skipped" not in a]
        ok = sum(1 for a in acts if a.get("ok"))
        note = f"  ERROR {page['error']}" if page.get("error") else ""
        print(
            f"{page['pattern']:55} controls={page.get('controls_in_example', '?'):>3} actions {ok}/{len(acts)}{note}",
            flush=True,
        )
        pages.append(page)
    (args.out / "apg_eval.json").write_text(json.dumps(pages, indent=2, default=str), encoding="utf-8")
    text = report(pages)
    (args.out / "apg_eval.md").write_text(text, encoding="utf-8")
    print("\n" + text.splitlines()[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
