"""Versioned corrections to the evidence and response contracts of Books tasks 68/69.

The public prompt and the format validator consume the same schema. Expected answers stay in
the evaluator's private dataset; no examples or expected values are put in the model prompt.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from jsonschema import Draft202012Validator

PROTOCOL = "books-evidence-v1"
TASK_IDS = (68, 69)
EVIDENCE = {
    "allowed": ["post title", "post body"],
    "excluded": ["comments", "linked pages", "outside knowledge"],
}


def revise_task(original: dict[str, Any]) -> dict[str, Any]:
    """Copy a source record, correcting only the two diagnosed tasks."""
    task = copy.deepcopy(original)
    if task["task_id"] not in TASK_IDS:
        return task
    scope = "Use only each post's title and body as evidence. Do not use comments, linked pages, or outside knowledge. "
    if task["task_id"] == 68:
        task["intent"] = (
            'Among the top 10 hottest posts in the "Books" forum, identify those that recommend '
            "exactly one book. For each, return the book title and its author's name exactly as "
            "written in that post's title or body. " + scope + "If the book's author is not stated "
            "there, use JSON null for author. A narrator, poster, or platform is not the book's "
            'author. Return a list of objects with exactly the keys "book" and "author".'
        )
        result_schema = {
            "type": "array",
            "uniqueItems": True,
            "items": {
                "type": "object",
                "required": ["book", "author"],
                "additionalProperties": False,
                "properties": {
                    "book": {"type": "string", "minLength": 1},
                    "author": {"anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]},
                },
            },
        }
    else:
        task["intent"] = (
            'Among the top 10 hottest posts in the "Books" forum, identify those that talk about '
            "supporting local book stores. Return the URLs or bare domain names of organizations "
            "involved, exactly as written in those posts' titles or bodies. " + scope + "A bare "
            "domain counts; do not add a scheme or expand it into a URL that is not shown. "
            "Return a deduplicated array of strings, with no enclosing objects or explanations "
            "inside retrieved_data."
        )
        result_schema = {"type": "array", "uniqueItems": True, "items": {"type": "string", "minLength": 1}}
        # The upstream expected value uses a list of acceptable variants for this one item.
        # The first is the literal domain in the post; a synthesized https URL is out of scope.
        for evaluator in task["eval"]:
            if evaluator["evaluator"] == "AgentResponseEvaluator":
                values = evaluator["expected"]["retrieved_data"]
                evaluator["expected"]["retrieved_data"] = [v[0] if isinstance(v, list) else v for v in values]
    for evaluator in task["eval"]:
        if evaluator["evaluator"] == "AgentResponseEvaluator":
            evaluator["results_schema"] = copy.deepcopy(result_schema)
    return task


def response_schema(task: dict[str, Any]) -> dict[str, Any]:
    """The complete, machine-checked JSON response contract, without reference answers."""
    [evaluator] = [entry for entry in task["eval"] if entry["evaluator"] == "AgentResponseEvaluator"]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["task_type", "status", "retrieved_data"],
        "additionalProperties": False,
        "properties": {
            "task_type": {"const": "RETRIEVE"},
            "status": {"enum": ["SUCCESS", "NOT_FOUND_ERROR"]},
            "retrieved_data": copy.deepcopy(evaluator["results_schema"]),
        },
        "allOf": [
            {
                "if": {"properties": {"status": {"const": "NOT_FOUND_ERROR"}}},
                "then": {"properties": {"retrieved_data": {"maxItems": 0}}},
                "else": {"properties": {"retrieved_data": {"minItems": 1}}},
            }
        ],
    }


def prompt_contract(task: dict[str, Any]) -> dict[str, Any]:
    """Only this public portion of the task is passed to the model, identically for every tool."""
    return {
        "protocol": PROTOCOL,
        "goal": task["intent"],
        "evidence_scope": copy.deepcopy(EVIDENCE),
        "response_schema": response_schema(task),
    }


def answer_instructions(task: dict[str, Any]) -> str:
    return (
        "For the final answer, output only a JSON object satisfying this task contract. "
        "Use NOT_FOUND_ERROR with an empty retrieved_data array only when no qualifying evidence "
        "is found. Array order is immaterial.\n" + json.dumps(prompt_contract(task), ensure_ascii=False)
    )


def schema_errors(task: dict[str, Any], response: Any) -> list[str]:
    validator = Draft202012Validator(response_schema(task))
    return [
        f"/{'/'.join(map(str, error.absolute_path))}: {error.message}"
        for error in sorted(validator.iter_errors(response), key=lambda error: str(error.absolute_path))
    ]


def validate_reference(task: dict[str, Any]) -> None:
    """Fail before a model run if the revised schema and private reference answer disagree."""
    Draft202012Validator.check_schema(response_schema(task))
    [evaluator] = [entry for entry in task["eval"] if entry["evaluator"] == "AgentResponseEvaluator"]
    expected = copy.deepcopy(evaluator["expected"])
    expected["task_type"] = expected["task_type"].upper()
    if errors := schema_errors(task, expected):
        raise ValueError("Reference response violates the task contract: " + "; ".join(errors))
