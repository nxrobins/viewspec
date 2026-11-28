"""Bounded presentations of already-judged, redacted browser results.

Presentation never decides execution, retry safety, or persistence. The retained result is
the source of expandable detail; handles always name an observation, including in full mode.
"""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from typing import Any

DEFAULT_RESPONSE_BYTES = 12_288
MIN_RESPONSE_BYTES = 4096
MAX_RESPONSE_BYTES = 65_536


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def result_envelope(value: dict) -> dict:
    return {
        "content": [{"type": "text", "text": compact_json(value)}],
        "structuredContent": value,
        "isError": False,
    }


def response_bytes(value: dict) -> int:
    """Both MCP channels, default-spaced UTF-8 JSON, plus 256 bytes for RPC framing."""
    return len(json.dumps(result_envelope(value), ensure_ascii=False).encode("utf-8")) + 256


def qualified(value: dict) -> dict:
    result = copy.deepcopy(value)
    observation_id = result.get("observation_id")
    if observation_id is None:
        return result

    def line(text: str) -> str:
        return re.sub(r"^(\[(\d+)\])", lambda m: f"[{observation_id}:{m[2]}]", text, flags=re.MULTILINE)

    if "view" in result:
        result["view"] = line(result["view"])
    page = result.get("view_page", {})
    if "context" in page:
        page["context"] = [line(text) for text in page["context"]]
    for key in ("form_tools", "preparation_tools"):
        for schema in result.get(key, []):
            schema["observation_id"] = observation_id
            if "submitters" in schema:
                schema["submitters"] = [
                    f"{observation_id}:{handle}" if type(handle) is int else handle for handle in schema["submitters"]
                ]
    # Changes are diagnostic prose, not an independently actionable handle namespace.
    return result


def _short(text: Any, limit: int = 240) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "… [detail omitted]"


def _counts(items: list[dict], key: str) -> dict[str, int]:
    return dict(Counter(item.get(key, "unknown") for item in items))


def _progress(value: dict) -> dict:
    result = {k: copy.deepcopy(v) for k, v in value.items() if k not in {"fields", "contract"}}
    for key in ("fields", "contract"):
        if key in value:
            result[key] = {"count": len(value[key]), "states": _counts(value[key], "status")}
    return result


def _receipt(value: dict) -> dict:
    result = copy.deepcopy(value)
    transport = result.get("transport", {})
    if "requests" in transport:
        items = transport.pop("requests")
        transport["request_count"] = len(items)
        transport["states"] = _counts(items, "state")
        transport["failed_http"] = sum(isinstance(i.get("status"), int) and i["status"] >= 400 for i in items)
    if "writes" in result:
        result["writes"] = {"count": len(value["writes"]), "states": _counts(value["writes"], "state")}
    if "signals" in result:
        result["signals"] = _counts(value["signals"], "kind")
    return result


def project(
    canonical: dict,
    *,
    mode: str,
    budget: int,
    result_id: str | None,
    focused: dict | None = None,
) -> dict:
    """Create a bounded reply without changing any action/evidence decision."""
    result = qualified(canonical)
    omitted: set[str] = set()
    summarized: set[str] = set()
    if focused is not None and mode == "focused":
        result.update(qualified(focused))
        if "view_scope" in focused:
            result.pop("view_page", None)
            omitted.add("view_page")
    presentation = {
        "version": 1,
        "mode": mode,
        "budget_bytes": budget,
        "detail_state": "retained" if result_id else "unavailable",
        "omitted": [],
        "summarized": [],
    }
    result["presentation"] = presentation
    if result_id:
        result["result_id"] = result_id
        presentation["expand"] = {"details": {"result_id": result_id}}

    def drop(key):
        if key in result:
            del result[key]
            omitted.add(key)

    def fits():
        presentation["omitted"] = sorted(omitted)
        presentation["summarized"] = sorted(summarized)
        return response_bytes(result) <= budget

    if mode != "full":
        for key in ("diagnostics", "external_refs", "paths", "next_actions", "errors"):
            if not result.get(key):
                result.pop(key, None)
        for key in ("steps", "changes", "passed_writes"):
            drop(key)
        if "progress" in result:
            result["progress"] = _progress(result["progress"])
            summarized.add("progress")
        if "action_evidence" in result:
            result["action_evidence"] = _receipt(result["action_evidence"])
            summarized.add("action_evidence")
        if mode == "receipt":
            for key in ("view", "view_page", "form_tools", "preparation_tools"):
                drop(key)

    # Counts remain visible even if individual policy records exceed the budget. A positive
    # field check or UI signal can never erase these dispositions.
    policy = {}
    for key in ("blocked_writes", "blocked_navigations", "blocked_requests", "passed_writes"):
        items = canonical.get(key, [])
        if items:
            policy[key] = sum(item.get("count", 1) for item in items) if key == "blocked_requests" else len(items)
    if policy:
        result["policy_events"] = policy
    event_keys = ("dialogs", "opened", "download", "unsaved_downloads", "invalid")
    events = [
        {"step": index + 1, **{k: step[k] for k in event_keys if step.get(k)}}
        for index, step in enumerate(canonical.get("steps", []))
        if any(step.get(k) for k in event_keys)
    ]
    event_counts = {
        k: sum(len(event[k]) if isinstance(event[k], list) else 1 for event in events if k in event)
        for k in event_keys
        if any(k in event for event in events)
    }
    if events and mode != "full":
        result["step_events"] = events
        result["event_counts"] = event_counts
    # Transient and global messages must remain discoverable even when the owner-focused
    # snapshot has replaced a page/diff. Do not reduce a negative message to a success flag.
    if mode != "full" and canonical.get("changes"):
        messages = [
            line
            for line in canonical["changes"].get("lines", [])
            if "message" in line or "(shown again)" in line or "(shown, now gone)" in line
        ]
        if messages:
            result["messages"] = messages
    if fits():
        return result

    # First remove diagnostics and duplicate descriptions, not the next actionable view.
    for key in ("steps", "changes", "limits", "passed_writes", "diagnostics", "external_refs", "paths"):
        drop(key)
    for key in ("action_evidence", "progress"):
        if key in result and key not in summarized:
            result[key] = (_receipt if key == "action_evidence" else _progress)(result[key])
            summarized.add(key)
    for key in ("blocked_requests", "blocked_navigations", "blocked_writes", "opened", "dialogs", "messages"):
        if key in result:
            result[key] = result[key][:2]
            summarized.add(key)
    if "summary" in result:
        result["summary"] = _short(result["summary"])
        summarized.add("summary")
    if "outcome" in result:
        result["outcome"]["reason"] = _short(result["outcome"].get("reason", ""))
    for key in ("wait", "feedback"):
        if key in result:
            result[key] = {
                k: v for k, v in result[key].items() if k in {"met", "mode", "reason", "waited_ms", "timeout_ms"}
            }
            summarized.add(key)
    if isinstance(result.get("evidence", {}).get("completion"), dict):
        result["evidence"]["completion"].pop("condition", None)
        summarized.add("evidence")
    if fits():
        return result

    # Never present a partial JSON Schema as a complete contract. Expansion returns the
    # exact retained schema and its observation, not an inferred schema for a newer page.
    for key in ("form_tools", "preparation_tools"):
        if key in result:
            result[key] = [
                {
                    "name": _short(schema["name"], 80),
                    "observation_id": schema.get("observation_id"),
                    "schema_coverage": "omitted",
                    "details": {"result_id": result_id, "section": key},
                }
                for schema in result[key][:4]
            ]
            summarized.add(key)
    if fits():
        return result

    # Keep complete lines only. A giant individual line is omitted, not turned into an
    # apparently complete field value. Existing page offsets are not repurposed as byte offsets.
    if "view" in result:
        lines = result["view"].splitlines()
        result["view"] = ""
        result["view_coverage"] = {"state": "partial", "retained_lines": 0, "source_lines": len(lines)}
        omitted.add("view")
        for line in lines:
            previous = result["view"]
            result["view"] = previous + ("\n" if previous else "") + line
            result["view_coverage"]["retained_lines"] += 1
            if not fits():
                result["view"] = previous
                result["view_coverage"]["retained_lines"] -= 1
                break
        if fits():
            return result

    # Oversized errors/labels/URLs can exhaust the budget too. This final bounded envelope
    # preserves disposition and conservative replay information after a dispatched action.
    outcome = canonical.get("outcome")
    evidence = canonical.get("evidence", {})
    receipt = canonical.get("action_evidence", {})
    minimal = {
        "schema_version": canonical.get("schema_version", 1),
        "ok": canonical.get("ok", False),
        "summary": "Result details exceed the response budget. Expand before deciding or repeating an action.",
        "presentation": {
            **presentation,
            "omitted": ["result_details"],
            "summarized": [],
            "coverage": "partial",
        },
        "policy_events": policy,
    }
    if event_counts:
        minimal["event_counts"] = event_counts
    if result_id:
        minimal["result_id"] = result_id
    if "observation_id" in canonical:
        minimal["observation_id"] = canonical["observation_id"]
    if outcome:
        minimal["outcome"] = {k: outcome[k] for k in ("status", "retry")}
    if evidence:
        minimal["evidence"] = {
            k: evidence[k] for k in ("execution", "control_verification", "completion") if k in evidence
        }
        if isinstance(minimal["evidence"].get("completion"), dict):
            minimal["evidence"]["completion"] = {
                k: v for k, v in minimal["evidence"]["completion"].items() if k != "condition"
            }
    if receipt:
        minimal["action_evidence"] = {
            k: receipt[k]
            for k in ("action_id", "source_observation_id", "execution", "scope_state", "persistence", "blocked")
            if k in receipt
        }
    if canonical.get("action_id"):
        minimal["action_id"] = canonical["action_id"]
    if canonical.get("progress"):
        p = canonical["progress"]
        minimal["progress"] = {
            "submission": p.get("submission", "unknown"),
            "fields": _counts(p.get("fields", []), "status"),
            "contract": _counts(p.get("contract", []), "status"),
        }
    if canonical.get("errors"):
        minimal["errors"] = [{"code": _short(e.get("code", "ERROR"), 80)} for e in canonical["errors"][:2]]
    return minimal


def detail_page(value: Any, *, result_id: str, section: str | None, offset: int, budget: int, revision: str) -> dict:
    """Expand redacted retained data in JSON character slices, including a huge single leaf."""
    text = compact_json(value)
    result = {
        "schema_version": 1,
        "ok": True,
        "summary": "Retained result detail; no new page observation or action.",
        "result_id": result_id,
        "detail": {"section": section, "encoding": "json", "offset": offset, "total": len(text), "revision": revision},
    }
    lo, hi = 0, min(len(text) - offset, budget)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        result["detail"].update(text=text[offset : offset + mid], next_offset=offset + mid)
        if response_bytes(result) <= budget:
            lo = mid
        else:
            hi = mid - 1
    result["detail"].update(
        text=text[offset : offset + lo], next_offset=offset + lo if offset + lo < len(text) else None
    )
    return result
