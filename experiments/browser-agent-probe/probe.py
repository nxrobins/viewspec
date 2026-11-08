"""Measure how well the ViewSpec agent lift describes a live page, and whether an agent can act on it.

For each target, the probe opens the page in a ``BrowserSession`` (bridge.js) and:

1. Snapshots the composed tree (shadow roots, slots, same-origin iframes) with live form state, and
   lists every visible interactive element by ref: the ground truth.
2. Lifts the snapshot with the "agent" profile, and with the "compile" profile as a baseline.
3. Verifies a locator for every control: frame chain + CSS, or role + name.
4. Compares sizes (approximate tokens), coverage, names, locators, and fingerprint stability, and
   checks that no live secret value reaches any output.

This is an experiment. It is not part of the ViewSpec package or its guarantees.

Usage:
    python experiments/browser-agent-probe/probe.py URL_OR_FILE [...] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from agent_session import BridgeError, BrowserSession, Observation

from viewspec.raw_html import lift_html


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


def count_leaks(secrets: list[str], outputs: list[str]) -> dict[str, int]:
    """Count the outputs that contain a live secret value. The secrets are never written anywhere."""

    names = ["snapshot_html", "lift_json", "page_view", "form_schemas", "locators", "aria_snapshot"]
    return {
        name: sum(1 for secret in secrets if len(secret) >= 4 and secret in output)
        for name, output in zip(names, outputs, strict=True)
    }


def ref_coverage(interactive: list[dict[str, str]], observation: Observation) -> dict[str, Any]:
    """Ground-truth elements that the agent lift found as controls (same element, by ref)."""

    controls = {control.ref: control for control in observation.lift.controls if control.ref}
    found = [item for item in interactive if item["ref"] in controls]
    named = [item for item in found if normalize(item["name"]) == normalize(controls[item["ref"]].name)]
    missed = [f"{item['kind']} {item['name']!r}" for item in interactive if item["ref"] not in controls]
    renamed = [
        f"{item['name']!r} -> {controls[item['ref']].name!r}"
        for item in found
        if normalize(item["name"]) != normalize(controls[item["ref"]].name)
    ]
    extra = [control.name for ref, control in controls.items() if ref not in {item["ref"] for item in interactive}]
    return {
        "found": len(found),
        "total": len(interactive),
        "same_name": len(named),
        "missed_examples": missed[:15],
        "renamed_examples": renamed[:15],
        "extra_controls": extra[:15],
    }


def locator_stats(observation: Observation) -> dict[str, Any]:
    """How each control can be found again: by kind of verified locator, or not at all."""

    found = [observation.locators.get(control.node_id) or {} for control in observation.lift.controls]
    kinds = Counter(locator.get("kind") or "none" for locator in found)
    unlocated = [
        f"{control.control} {control.name!r}: {locator.get('reason')}"
        for control, locator in zip(observation.lift.controls, found, strict=True)
        if not locator.get("strategy")
    ]
    return {
        "located": sum(1 for locator in found if locator.get("strategy")),
        "total": len(found),
        "by_kind": dict(kinds.most_common()),
        "unlocated_examples": unlocated[:10],
    }


def analyze(session: BrowserSession, target: str, out_dir: Path | None, index: int) -> dict[str, Any]:
    try:
        opened = session.open(target)
        observation = session.observe(collect_secrets=True)
        aria = session.aria_snapshot()
        session.reload()
        reload_lift = session.observe(locate=False).lift
    except BridgeError as error:
        return {"target": target, "ok": False, "error": str(error)}

    lift = observation.lift
    static_lift = lift_html(opened["static_html"], profile="agent")
    compile_lift = lift_html(observation.html)
    schemas = lift.form_tool_schemas()
    compile_actions = [role.text for role in compile_lift.roles if role.role == "action"]
    lift_json = json.dumps(lift.to_json(), separators=(",", ":"))
    locators_json = json.dumps(observation.locators, separators=(",", ":"))
    interactive = observation.interactive

    report = {
        "target": target,
        "ok": True,
        "approx_tokens": {
            "snapshot_html": approx_tokens(observation.html),
            "aria_snapshot": approx_tokens(aria),
            "lift_json": approx_tokens(lift_json),
            "page_view": approx_tokens(observation.view),
            "form_schemas": approx_tokens(json.dumps(schemas, separators=(",", ":"))),
        },
        "nodes": {"static_lift": len(static_lift.nodes), "rendered_lift": len(lift.nodes)},
        "visible_interactive": len(interactive),
        "interactive_kinds": dict(Counter(item["kind"] for item in interactive).most_common()),
        "coverage_compile_profile": coverage(interactive, compile_actions),
        "coverage_agent_profile": ref_coverage(interactive, observation),
        "locators": locator_stats(observation),
        "controls": dict(Counter(control.control for control in lift.controls).most_common()),
        "forms": [schema["name"] for schema in schemas],
        "fingerprint": {
            "stable_across_reload": lift.topology_fingerprint.hex == reload_lift.topology_fingerprint.hex,
            "static_equals_rendered": static_lift.topology_fingerprint.hex == lift.topology_fingerprint.hex,
        },
        "secret_fields_checked": len(observation.secrets),
        "secret_leaks": count_leaks(
            observation.secrets,
            [observation.html, lift_json, observation.view, json.dumps(schemas), locators_json, aria],
        ),
        "groups": len(lift.group_candidates),
        "viewspec_action_ids": observation.action_ids,
        "lift_diagnostics": dict(Counter(item["code"] for item in lift.diagnostics)),
    }

    if out_dir is not None:
        page_dir = out_dir / f"{index:02d}-{slug(target)}"
        page_dir.mkdir(parents=True, exist_ok=True)
        (page_dir / "page_view.txt").write_text(observation.view + "\n", encoding="utf-8")
        (page_dir / "aria_snapshot.yaml").write_text(aria + "\n", encoding="utf-8")
        (page_dir / "lift.json").write_text(json.dumps(lift.to_json(), indent=2), encoding="utf-8")
        (page_dir / "form_tools.json").write_text(json.dumps(schemas, indent=2), encoding="utf-8")
        (page_dir / "locators.json").write_text(json.dumps(observation.locators, indent=2), encoding="utf-8")
        (page_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def slug(target: str) -> str:
    tail = target.rstrip("/").rsplit("/", 1)[-1] or target
    return re.sub(r"[^A-Za-z0-9]+", "-", tail).strip("-")[:40] or "page"


def summary_table(reports: list[dict[str, Any]]) -> str:
    header = (
        "| page | aria tok | view tok | interactive | compile cov | agent cov | same name | located "
        "| forms | fp stable | leaks |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|"
    )
    rows = [header]
    for report in reports:
        name = slug(report["target"])
        if not report["ok"]:
            rows.append(f"| {name} | error: {report['error'][:60]} |||||||||")
            continue
        tokens = report["approx_tokens"]
        compiled = report["coverage_compile_profile"]
        agent = report["coverage_agent_profile"]
        located = report["locators"]
        leaks = sum(count for key, count in report["secret_leaks"].items() if key != "aria_snapshot")
        rows.append(
            f"| {name} | {tokens['aria_snapshot']} | {tokens['page_view']} | {report['visible_interactive']} "
            f"| {compiled['found']}/{compiled['total']} | {agent['found']}/{agent['total']} "
            f"| {agent['same_name']}/{agent['found']} | {located['located']}/{located['total']} "
            f"| {len(report['forms'])} | {report['fingerprint']['stable_across_reload']} | {leaks} |"
        )
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="+", help="http(s) URLs or local HTML files")
    parser.add_argument("--out", type=Path, help="write per-page views, snapshots, and reports here")
    args = parser.parse_args(argv)

    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
    with BrowserSession() as session:
        reports = [analyze(session, to_target(target), args.out, index) for index, target in enumerate(args.targets, 1)]
    if args.out is not None:
        (args.out / "summary.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")
    print(summary_table(reports))
    for report in reports:
        if not report["ok"]:
            continue
        name = slug(report["target"])
        agent = report["coverage_agent_profile"]
        for label, key in (("missed", "missed_examples"), ("named differently", "renamed_examples")):
            if agent[key]:
                print(f"\n{name}: {label}: " + "; ".join(agent[key][:6]))
        if agent["extra_controls"]:
            print(f"\n{name}: found only by the agent lift: " + "; ".join(agent["extra_controls"][:6]))
        if report["locators"]["unlocated_examples"]:
            print(f"\n{name}: no locator: " + "; ".join(report["locators"]["unlocated_examples"][:6]))
        aria_leaks = report["secret_leaks"]["aria_snapshot"]
        if aria_leaks:
            print(f"\n{name}: the ARIA snapshot shows {aria_leaks} live secret value(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
