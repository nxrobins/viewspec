"""Typed predicates execute against observed state, not text descriptions of implementation."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "experiments/browser-agent-probe"))
from feedback import ScopedCondition, evaluate

from viewspec.raw_html_actions import AgentActionError


def sample(**attrs):
    return {"state": "observed", "attributes": attrs, "name": "Save", "messages": ["Not saved"], "texts": []}


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"target": True, "state": {"disabled": True}},
        {"target": 1, "state": {"disabled": "false"}},
        {"target": 1, "state": {}},
        {"target": 1, "state": {"password": "secret"}},
        {"target": 1, "text_equals": "x", "source": "anywhere"},
        {"any_of": []},
        {"all_of": [{"target": 1, "state": {"disabled": True}}], "extra": 1},
    ],
)
def test_invalid_conditions_refused(payload):
    with pytest.raises(AgentActionError):
        ScopedCondition.from_json(payload)


def test_initial_truth_is_distinct_from_transition_and_target_is_exact():
    before = {1: sample(disabled=True), 2: sample(disabled=False)}
    state = ScopedCondition.from_json({"target": 1, "state": {"disabled": True}})
    assert evaluate(state, before, before) == {"target": 1, "state": "already_true", "met": True, "initially_met": True}
    transition = ScopedCondition.from_json(state.spec | {"transition": True})
    assert not evaluate(transition, before, before)["met"]
    other = ScopedCondition.from_json({"target": 2, "state": {"disabled": True}})
    assert not evaluate(other, before, before)["met"]
    assert evaluate(other, before | {2: sample(disabled=True)}, before)["met"]


def test_exact_message_does_not_match_negative_text_or_unknown_coverage():
    condition = ScopedCondition.from_json({"target": 1, "text_equals": "Saved", "source": "message"})
    before = {1: sample()}
    assert not evaluate(condition, before, before)["met"]
    assert evaluate(condition, {1: sample() | {"truncated": True}}, before)["state"] == "unknown"
    assert evaluate(condition, {1: sample() | {"messages": [" Saved  "]}}, before)["met"]
    assert not evaluate(condition, {1: sample() | {"messages": ["saved"]}}, before)["met"]


def test_groups_keep_unknown_and_false_separate():
    cond = ScopedCondition.from_json(
        {
            "all_of": [
                {"target": 1, "state": {"disabled": True}},
                {"target": 2, "state": {"visible": False}},
            ]
        }
    )
    before = {1: sample(disabled=False), 2: sample(visible=True)}
    assert evaluate(cond, {1: sample(disabled=True), 2: {"state": "scope_gone"}}, before)["met"]
    assert evaluate(cond, {1: sample(disabled=True), 2: {"state": "scope_changed"}}, before)["state"] == "unknown"


def test_nested_condition_budget_is_bounded():
    cond = {"target": 1, "state": {"disabled": True}}
    for _ in range(5):
        cond = {"any_of": [cond]}
    with pytest.raises(AgentActionError):
        ScopedCondition.from_json(cond)
