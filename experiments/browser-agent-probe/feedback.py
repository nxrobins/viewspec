"""Typed read-only conditions and bounded action receipts. No browser mutations live here."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Any

from viewspec.raw_html_actions import AgentActionError

BOOL_STATES = frozenset({"visible", "disabled", "checked", "expanded", "pressed"})
MAX_TARGETS = 16


def invalid(message: str) -> None:
    raise AgentActionError("AGENT_WAIT_INVALID", message)


@dataclass(frozen=True)
class ScopedCondition:
    spec: dict[str, Any]

    @classmethod
    def from_json(cls, value: Any) -> ScopedCondition:
        count = 0

        def check(node: Any, depth: int = 0) -> None:
            nonlocal count
            count += 1
            if count > 32 or depth > 3 or not isinstance(node, dict):
                invalid("A scoped condition must be an object, with at most 32 nodes and depth 3.")
            groups = set(node) & {"any_of", "all_of"}
            if groups:
                if len(node) != 1:
                    invalid("Use only one of any_of or all_of in a condition group.")
                children = node[next(iter(groups))]
                if not isinstance(children, list) or not 1 <= len(children) <= MAX_TARGETS:
                    invalid("Condition groups need 1–16 conditions.")
                for child in children:
                    check(child, depth + 1)
                return
            if set(node) - {"target", "state", "text_equals", "source", "transition"}:
                invalid("Unknown scoped condition key.")
            if type(node.get("target")) is not int or node["target"] < 1:
                invalid("A scoped condition needs a positive target handle from its source observation.")
            if ("state" in node) == ("text_equals" in node):
                invalid("Choose exactly one of state or text_equals.")
            if "transition" in node and type(node["transition"]) is not bool:
                invalid("transition must be a boolean.")
            if "state" in node:
                states = node["state"]
                if "source" in node or not isinstance(states, dict) or not states:
                    invalid("state needs public control attributes; source is only for text_equals.")
                for key, expected in states.items():
                    if key in BOOL_STATES and type(expected) is bool:
                        continue
                    if key == "value" and isinstance(expected, str) and len(expected) <= 4096:
                        continue
                    invalid(
                        "State attributes are visible/disabled/checked/expanded/pressed booleans or a public value string."
                    )
            elif (
                not isinstance(node["text_equals"], str)
                or not node["text_equals"].strip()
                or len(node["text_equals"]) > 4096
                or node.get("source", "control") not in {"control", "scope", "message"}
            ):
                invalid("text_equals needs 1–4096 characters and source control, scope, or message.")

        check(value)
        return cls(copy.deepcopy(value))

    def targets(self) -> set[int]:
        def visit(node):
            if "target" in node:
                return {node["target"]}
            return set().union(*(visit(child) for child in next(iter(node.values()))))

        return visit(self.spec)

    def to_json(self) -> dict[str, Any]:
        return copy.deepcopy(self.spec)

    def describe(self) -> str:
        return "the scoped UI condition"


def evaluate(condition: ScopedCondition, samples: dict[int, dict], baseline: dict[int, dict]) -> dict:
    """Three-valued state checks; true before dispatch is never a newly observed transition."""

    def matches(node, sample):
        state = sample.get("state")
        if state in {"target_gone", "scope_gone"}:
            return (
                False
                if "text_equals" in node
                else (node["state"] == {"visible": False} if set(node["state"]) == {"visible"} else None)
            )
        if state != "observed":
            return None
        if "state" in node:
            actual = sample.get("attributes", {})
            if any(key not in actual for key in node["state"]):
                return None
            return all(actual[key] == value for key, value in node["state"].items())
        source = node.get("source", "control")
        candidates = (
            [sample.get("name", "")]
            if source == "control"
            else sample.get("texts" if source == "scope" else "messages", [])
        )
        expected = " ".join(node["text_equals"].split())
        found = any(" ".join(text.split()) == expected for text in candidates)
        return True if found else None if sample.get("truncated") else False

    def visit(node):
        if "target" not in node:
            kind = next(iter(node))
            children = [visit(child) for child in node[kind]]
            met = all(c["met"] for c in children) if kind == "all_of" else any(c["met"] for c in children)
            unknown = any(c["state"] == "unknown" for c in children)
            return {"state": "matched" if met else "unknown" if unknown else "unmet", "met": met, "children": children}
        target = node["target"]
        before, now = matches(node, baseline.get(target, {})), matches(node, samples.get(target, {}))
        occurrence = False
        sample = samples.get(target, {})
        if node.get("source") == "message" and node.get("transition") and sample.get("state") == "observed":
            occurrence = any(
                event["seq"] > baseline.get(target, {}).get("note_seq", 0)
                and " ".join(event["text"].split()) == " ".join(node["text_equals"].split())
                for event in sample.get("message_events", [])
            )
            if occurrence:
                now = True
        met = now is True and (not node.get("transition", False) or before is False)
        state = (
            "unknown"
            if now is None or (node.get("transition") and before is None)
            else ("already_true" if before is True and now is True else "matched" if met else "unmet")
        )
        result = {"target": target, "state": state, "met": met, "initially_met": before}
        if occurrence:
            result["source"] = "message_event"
        return result

    return visit(condition.spec)


@dataclass
class ActionReceipt:
    action_id: str
    observation: Any
    bindings: list[dict]
    handles: list[int]
    mark: dict
    source_observation_id: int = 0
    condition: ScopedCondition | None = None
    baseline: dict[int, dict] = field(default_factory=dict)
    execution: str = "not_dispatched"
    superseded: bool = False
    through: dict | None = None
    last: dict = field(default_factory=dict)
    sample_sequence: int = 0
    feedback_baseline: dict[int, dict] = field(default_factory=dict)
    seen_terminal: set[int] = field(default_factory=set)
    current_samples: dict[int, dict] = field(default_factory=dict)

    def sample(self, session) -> dict:
        raw = session._call("feedback_read", bindings=self.bindings, mark=self.mark, through=self.through)
        self.sample_sequence += 1
        samples = dict(zip(self.handles, raw["samples"], strict=True))
        self.current_samples = samples
        if self.superseded:
            samples = {handle: {"state": "unknown"} for handle in self.handles}
        signals = []
        for handle, sample in samples.items():
            before = (self.feedback_baseline or self.baseline).get(handle, {})
            if sample.get("state") in {"scope_gone", "target_gone"}:
                signals.append({"kind": sample["state"], "target": handle})
            elif sample.get("state") == "observed" and before.get("state") == "observed":
                changed = {
                    key: {"before": before.get("attributes", {}).get(key), "after": value}
                    for key, value in sample.get("attributes", {}).items()
                    if value != before.get("attributes", {}).get(key)
                }
                if changed:
                    signals.append({"kind": "control_changed", "target": handle, "attributes": changed})
                if set(sample.get("messages", [])) - set(before.get("messages", [])) or any(
                    e["seq"] > before.get("note_seq", 0) for e in sample.get("message_events", [])
                ):
                    signals.append({"kind": "message", "target": handle})
        transport = raw["transport"]
        if any(
            r["state"] in {"completed", "failed"} and r["sequence"] not in self.seen_terminal
            for r in transport["requests"]
        ):
            signals.append({"kind": "transport_terminal", "association": "temporal"})
        invalid_scope = (
            self.superseded
            or transport["truncated"]
            or any(s.get("state") in {"scope_changed", "unknown"} for s in samples.values())
        )
        blocked = any(w.get("state") in {"blocked", "blocked-and-simulated"} for w in raw["dispositions"])
        self.last = {
            "version": 1,
            "action_id": self.action_id,
            "source_observation_id": self.source_observation_id,
            "targets": self.handles,
            "execution": self.execution,
            "sample_sequence": self.sample_sequence,
            "source": "feedback_sample",
            "scope_state": "superseded" if self.superseded else "unknown" if invalid_scope else "observed",
            "signals": signals,
            "transport": transport,
            "writes": raw["dispositions"],
            "persistence": "unverified",
            "blocked": blocked,
        }
        return {"raw": raw, "samples": samples, "blocked": blocked, "unknown": invalid_scope}

    def delivered(self) -> None:
        self.feedback_baseline = copy.deepcopy(self.current_samples)
        self.seen_terminal.update(
            r["sequence"]
            for r in self.last.get("transport", {}).get("requests", [])
            if r["state"] in {"completed", "failed"}
        )

    def wait(self, session, condition: ScopedCondition | None, *, watch: bool, timeout_ms: int) -> dict:
        began = time.monotonic()
        limit = max(0, min(int(timeout_ms), 30000))
        while True:
            sampled = self.sample(session)
            checked = evaluate(condition, sampled["samples"], self.baseline) if condition else None
            if watch and condition and checked["met"] and evaluate(condition, self.baseline, self.baseline)["met"]:
                checked = {**checked, "state": "already_true", "met": False}
            elapsed = int((time.monotonic() - began) * 1000)
            met = checked["met"] if checked else bool(self.last["signals"])
            reason = (
                "blocked"
                if sampled["blocked"]
                else "scope_changed"
                if sampled["unknown"]
                else "unknown"
                if checked and checked["state"] == "unknown"
                else "already_true"
                if checked and checked["state"] == "already_true" and not met
                else ("feedback" if watch and met else "matched" if met else "timed_out" if elapsed >= limit else None)
            )
            if reason:
                result = {
                    "mode": "watch" if watch else "assert",
                    "reason": reason,
                    "met": bool(met and reason not in {"blocked", "scope_changed", "unknown", "already_true"}),
                    "waited_ms": elapsed,
                    "timeout_ms": limit,
                }
                if checked:
                    result.update(condition=condition.to_json(), check=checked)
                self.last["verification"] = result
                return result
            session._call("feedback_wait", revision=sampled["raw"]["revision"], timeout_ms=limit - elapsed)
