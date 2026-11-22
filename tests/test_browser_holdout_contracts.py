"""The revised task's emitted answer contract is also its executable format validator."""

from __future__ import annotations

import copy
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

COMPARISON = Path(__file__).resolve().parents[1] / "experiments" / "browser-agent-probe" / "comparison"


@pytest.fixture
def contracts(monkeypatch):
    monkeypatch.syspath_prepend(str(COMPARISON))
    return importlib.import_module("holdout_contracts")


def original(task_id):
    return {
        "task_id": task_id,
        "intent": "Retrieve from the description only.",
        "eval": [
            {
                "evaluator": "AgentResponseEvaluator",
                "results_schema": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"book": {"type": "string"}, "author": {"type": "string"}},
                    },
                },
                "expected": answer(
                    [{"book": "Private reference title", "author": None}]
                    if task_id == 68
                    else [["example.test", "https://example.test"]]
                ),
            }
        ],
    }


def answer(value, status="SUCCESS"):
    return {"task_type": "RETRIEVE", "status": status, "retrieved_data": value}


@pytest.mark.parametrize("task_id", [68, 69])
def test_reference_validates_and_original_record_is_preserved(contracts, task_id):
    source = original(task_id)
    unchanged = copy.deepcopy(source)
    task = contracts.revise_task(source)
    contracts.validate_reference(task)
    assert source == unchanged
    if task_id == 69:
        assert task["eval"][0]["expected"]["retrieved_data"] == ["example.test"]


def test_original_string_only_schema_rejects_null_author_and_revision_accepts_it(contracts):
    source = original(68)
    evaluator = source["eval"][0]
    assert not Draft202012Validator(evaluator["results_schema"]).is_valid(evaluator["expected"]["retrieved_data"])
    assert contracts.schema_errors(contracts.revise_task(source), evaluator["expected"]) == []


@pytest.mark.parametrize(
    ("task_id", "response", "valid"),
    [
        (68, answer([{"book": "A title", "author": None}]), True),
        (68, answer([{"book": "A title", "author": "An author"}]), True),
        (68, answer([{"book": "A title"}]), False),
        (68, answer([{"book": "A title", "author": ""}]), False),
        (68, answer([{"book": "A title", "author": None, "explanation": "Absent"}]), False),
        (69, answer(["example.test"]), True),
        (69, answer({"urls": ["example.test"]}), False),
        (69, answer([["example.test"]]), False),
        (69, answer(["example.test", "example.test"]), False),
        (69, answer([], "NOT_FOUND_ERROR"), True),
        (69, answer(["example.test"], "NOT_FOUND_ERROR"), False),
        (69, answer([]), False),
        (69, {**answer(["example.test"]), "explanation": "Found it"}, False),
        (69, {"retrieved_data": ["example.test"]}, False),
    ],
)
def test_emitted_schema_and_evaluator_agree(contracts, task_id, response, valid):
    task = contracts.revise_task(original(task_id))
    # Consume the actual machine-readable contract sent in the final model prompt.
    emitted = json.loads(contracts.answer_instructions(task).split("\n", 1)[1])
    validator = Draft202012Validator(emitted["response_schema"])
    assert validator.is_valid(response) == valid
    assert (not contracts.schema_errors(task, response)) == valid


def test_reference_answers_cannot_influence_the_public_contract(contracts):
    task = contracts.revise_task(original(68))
    public = contracts.answer_instructions(task)
    task["eval"][0]["expected"] = answer([{"book": "Another private title", "author": "Another author"}])
    assert contracts.answer_instructions(task) == public
    emitted = json.loads(public.split("\n", 1)[1])
    assert set(emitted) == {"protocol", "goal", "evidence_scope", "response_schema"}
    assert emitted["evidence_scope"] == {
        "allowed": ["post title", "post body"],
        "excluded": ["comments", "linked pages", "outside knowledge"],
    }


def test_preflight_rejects_reference_with_the_wrong_shape(contracts):
    task = contracts.revise_task(original(69))
    task["eval"][0]["expected"] = answer({"urls": ["example.test"]})
    with pytest.raises(ValueError, match="Reference response violates"):
        contracts.validate_reference(task)


def test_other_task_records_are_not_rewritten(contracts):
    source = original(27)
    revised = contracts.revise_task(source)
    assert revised == source and revised is not source


def test_runner_refuses_to_mix_a_new_protocol_with_existing_results(tmp_path):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("chrome", "playwright-mcp", "agent-browser"):
        (binaries / name).touch()
    data = tmp_path / "source.json"
    data.write_text("[]")
    output = tmp_path / "prior-run"
    output.mkdir()
    saved = output / "result.json"
    saved.write_text('{"protocol":"original","success":false}')
    result = subprocess.run(
        [
            sys.executable,
            str(COMPARISON / "run.py"),
            "--out",
            str(output),
            "--data",
            str(data),
            "--chrome",
            str(binaries / "chrome"),
            "--node-bin",
            str(binaries),
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 2 and "new empty output directory" in result.stderr
    assert json.loads(saved.read_text()) == {"protocol": "original", "success": False}
    assert list(output.iterdir()) == [saved]
