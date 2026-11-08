"""Measure how well the ViewSpec HTML lift describes a live page for a browser agent.

For each target, the probe:

1. Renders the page in Chromium (render_pages.js) and records the server HTML, the
   rendered DOM, the Playwright ARIA snapshot, and the visible interactive elements.
2. Runs ``viewspec.raw_html.lift_html`` on the server HTML and on the rendered DOM.
3. Builds a compact text "page view" from the lift.
4. Compares sizes (approximate tokens), action coverage, and fingerprint stability.

This is an experiment. It is not part of the ViewSpec package or its guarantees.

Usage:
    python experiments/browser-agent-probe/probe.py URL_OR_FILE [...] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from viewspec.raw_html import HtmlLiftResult, LiftNode, lift_html

HERE = Path(__file__).resolve().parent
RENDERER = HERE / "render_pages.js"
VIEW_GROUP_MIN = 3
TEXT_PREVIEW = 70


def approx_tokens(text: str) -> int:
    """Approximate tokens as characters / 4. Good enough to compare sizes, not to bill."""
    return (len(text) + 3) // 4


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def to_target(raw: str) -> str:
    if re.match(r"^[a-z]+://", raw):
        return raw
    path = Path(raw).resolve()
    if not path.exists():
        raise SystemExit(f"Target not found: {raw}")
    return path.as_uri()


def render(targets: list[str]) -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["node", str(RENDERER), *targets], capture_output=True, text=True, check=False, timeout=600
    )
    if completed.returncode != 0:
        raise SystemExit(f"render_pages.js failed:\n{completed.stderr}")
    return [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]


def descendant_text(node: LiftNode, by_id: dict[str, LiftNode]) -> str:
    parts = [node.text]
    for child_id in node.child_ids:
        parts.append(descendant_text(by_id[child_id], by_id))
    return " ".join(part for part in parts if part).strip()


def page_view(lift: HtmlLiftResult) -> tuple[str, list[str]]:
    """Return a compact agent-facing view of the lift and the names of the actions in it.

    The view lists headings, actions (with a short handle the agent can refer to), values,
    and repeated groups. Actions use descendant text, so ``<a><span>Buy</span></a>`` keeps
    its name. The lift's own role pass uses only the node's direct text.
    """

    by_id = {node.id: node for node in lift.nodes}
    group_of: dict[str, tuple[str, ...]] = {}
    for group in lift.group_candidates:
        if len(group) >= VIEW_GROUP_MIN:
            for member in group:
                group_of[member] = group
    roles = {role.node_id: role for role in lift.roles}
    lines: list[str] = []
    action_names: list[str] = []
    handle = 0

    for node in lift.nodes:
        group = group_of.get(node.id)
        if group and group[0] == node.id:
            first = descendant_text(node, by_id)[:TEXT_PREVIEW]
            lines.append(f"group {len(group)}x <{node.tag}>  first: {first!r}")
        if node.tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            text = descendant_text(node, by_id)
            if text:
                lines.append(f"{node.tag.upper()} {text[:TEXT_PREVIEW]}")
        elif node.tag in {"a", "button"}:
            name = descendant_text(node, by_id) or node.attrs.get("aria-label") or node.attrs.get("title") or ""
            if not name:
                continue
            handle += 1
            action_names.append(name)
            target = f" -> {node.attrs['href']}" if node.tag == "a" and "href" in node.attrs else ""
            kind = "link" if node.tag == "a" else "button"
            lines.append(f"[{handle}] {kind} {name[:TEXT_PREVIEW]!r}{target}")
        elif node.id in roles and roles[node.id].role == "value":
            lines.append(f"value {roles[node.id].text}")
    return "\n".join(lines), action_names


def coverage(interactive: list[dict[str, str]], names: list[str]) -> dict[str, Any]:
    """Share of visible interactive elements whose name appears among ``names``."""

    known = {normalize(name) for name in names if normalize(name)}
    by_kind: dict[str, list[int]] = {}
    missed: list[str] = []
    for item in interactive:
        hit = bool(item["name"]) and normalize(item["name"]) in known
        tally = by_kind.setdefault(item["kind"], [0, 0])
        tally[0] += int(hit)
        tally[1] += 1
        if not hit:
            missed.append(f"{item['kind']} {item['name']!r}")
    total = len(interactive)
    found = sum(hit for hit, _ in by_kind.values())
    return {
        "found": found,
        "total": total,
        "ratio": round(found / total, 3) if total else None,
        "by_kind": {kind: f"{hit}/{count}" for kind, (hit, count) in sorted(by_kind.items())},
        "missed_examples": missed[:15],
    }


def analyze(page: dict[str, Any], out_dir: Path | None, index: int) -> dict[str, Any]:
    if not page["ok"]:
        return {"target": page["target"], "ok": False, "error": page["error"]}

    static_lift = lift_html(page["static_html"])
    rendered_lift = lift_html(page["rendered_html"])
    reload_lift = lift_html(page["rendered_html_reload"])
    view, view_actions = page_view(rendered_lift)
    lift_actions = [role.text for role in rendered_lift.roles if role.role == "action"]
    lift_json = json.dumps(rendered_lift.to_json(), separators=(",", ":"))
    interactive = page["interactive"]

    report = {
        "target": page["target"],
        "ok": True,
        "approx_tokens": {
            "rendered_html": approx_tokens(page["rendered_html"]),
            "aria_snapshot": approx_tokens(page["aria_snapshot"]),
            "lift_json": approx_tokens(lift_json),
            "page_view": approx_tokens(view),
        },
        "nodes": {"static_lift": len(static_lift.nodes), "rendered_lift": len(rendered_lift.nodes)},
        "visible_interactive": len(interactive),
        "interactive_kinds": dict(Counter(item["kind"] for item in interactive).most_common()),
        "coverage_lift_roles": coverage(interactive, lift_actions),
        "coverage_page_view": coverage(interactive, view_actions),
        "fingerprint": {
            "stable_across_reload": rendered_lift.topology_fingerprint.hex == reload_lift.topology_fingerprint.hex,
            "static_equals_rendered": static_lift.topology_fingerprint.hex == rendered_lift.topology_fingerprint.hex,
        },
        "groups": len(rendered_lift.group_candidates),
        "viewspec_action_ids": page["viewspec_action_ids"],
        "lift_diagnostics": dict(Counter(item["code"] for item in rendered_lift.diagnostics)),
    }

    if out_dir is not None:
        page_dir = out_dir / f"{index:02d}-{slug(page['target'])}"
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "page_view.txt").write_text(view + "\n", encoding="utf-8")
        (page_dir / "aria_snapshot.yaml").write_text(page["aria_snapshot"] + "\n", encoding="utf-8")
        (page_dir / "lift.json").write_text(json.dumps(rendered_lift.to_json(), indent=2), encoding="utf-8")
        (page_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def slug(target: str) -> str:
    tail = target.rstrip("/").rsplit("/", 1)[-1] or target
    return re.sub(r"[^A-Za-z0-9]+", "-", tail).strip("-")[:40] or "page"


def summary_table(reports: list[dict[str, Any]]) -> str:
    header = (
        "| page | html tok | aria tok | view tok | interactive | lift-role cov | view cov "
        "| fp stable | static=rendered |\n|---|---:|---:|---:|---:|---:|---:|---|---|"
    )
    rows = [header]
    for report in reports:
        name = slug(report["target"])
        if not report["ok"]:
            rows.append(f"| {name} | error: {report['error'][:60]} |||||||")
            continue
        tokens = report["approx_tokens"]
        rows.append(
            f"| {name} | {tokens['rendered_html']} | {tokens['aria_snapshot']} | {tokens['page_view']} "
            f"| {report['visible_interactive']} "
            f"| {report['coverage_lift_roles']['found']}/{report['coverage_lift_roles']['total']} "
            f"| {report['coverage_page_view']['found']}/{report['coverage_page_view']['total']} "
            f"| {report['fingerprint']['stable_across_reload']} | {report['fingerprint']['static_equals_rendered']} |"
        )
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="+", help="http(s) URLs or local HTML files")
    parser.add_argument("--out", type=Path, help="write per-page views, snapshots, and reports here")
    args = parser.parse_args(argv)

    pages = render([to_target(target) for target in args.targets])
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
    reports = [analyze(page, args.out, index) for index, page in enumerate(pages, start=1)]
    if args.out is not None:
        (args.out / "summary.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")
    print(summary_table(reports))
    for report in reports:
        if report["ok"]:
            missed = report["coverage_page_view"]["missed_examples"]
            if missed:
                print(f"\n{slug(report['target'])}: not in page view: " + "; ".join(missed[:6]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
