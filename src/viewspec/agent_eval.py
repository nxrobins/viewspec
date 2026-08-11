"""Reproducible records and summaries for the ViewSpec agent-UI evaluation.

This module deliberately does not call a model or launch a browser.  The live
runner lives under ``scripts/``; the public, deterministic contracts live here
so records can be validated and summarized without network access.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
from typing import Any, Iterable, Mapping


AGENT_UI_EVAL_SCHEMA_VERSION = 1
AGENT_UI_EVAL_PROTOCOL_SCHEMA_VERSIONS = (1, 2)
AGENT_UI_EVAL_ARMS = ("code-first", "viewspec-core", "viewspec-deep")
AGENT_UI_EVAL_MODES = ("efficiency", "value_premium")
TOKEN_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _nonempty_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be non-empty canonical text")
    return value


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class AgentEvalStep:
    id: str
    prompt: str
    required_text: tuple[str, ...]
    phase: str = "evolution"
    assurance_tags: tuple[str, ...] = ()
    forbidden_text: tuple[str, ...] = ()
    required_buttons: tuple[str, ...] = ()
    text_order: tuple[str, ...] = ()
    click_button: str | None = None
    click_reveals: str | None = None
    interactions: tuple[Mapping[str, Any], ...] = ()
    text_geometry: tuple[Mapping[str, Any], ...] = ()
    unique_text: tuple[str, ...] = ()
    resources: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_json(cls, payload: Any) -> AgentEvalStep:
        data = _mapping(payload, "agent eval step")
        acceptance = _mapping(data.get("acceptance"), "agent eval step acceptance")

        def texts(key: str) -> tuple[str, ...]:
            values = acceptance.get(key, [])
            if not isinstance(values, list) or not all(isinstance(item, str) and item for item in values):
                raise ValueError(f"agent eval step acceptance.{key} must be an array of non-empty strings")
            return tuple(values)

        click = acceptance.get("click")
        click_button = None
        click_reveals = None
        if click is not None:
            click_data = _mapping(click, "agent eval step acceptance.click")
            click_button = _nonempty_text(click_data.get("button"), "click.button")
            click_reveals = _nonempty_text(click_data.get("reveals"), "click.reveals")
        raw_interactions = acceptance.get("interactions", [])
        if not isinstance(raw_interactions, list):
            raise ValueError("agent eval step acceptance.interactions must be an array")
        if click is not None and raw_interactions:
            raise ValueError("agent eval step acceptance cannot define both click and interactions")
        interactions: list[dict[str, Any]] = []
        for index, raw_interaction in enumerate(raw_interactions):
            interaction = _mapping(
                raw_interaction,
                f"agent eval step acceptance.interactions[{index}]",
            )
            button = _nonempty_text(
                interaction.get("button"),
                f"interactions[{index}].button",
            )
            raw_assertions = interaction.get("assertions")
            if raw_assertions is None:
                assertions = [
                    {
                        "kind": "visible_text",
                        "text": _nonempty_text(
                            interaction.get("reveals"),
                            f"interactions[{index}].reveals",
                        ),
                    }
                ]
                rendered: dict[str, Any] = {"button": button, "reveals": assertions[0]["text"]}
            else:
                if not isinstance(raw_assertions, list) or not raw_assertions:
                    raise ValueError(f"interactions[{index}].assertions must be a non-empty array")
                assertions = []
                for assertion_index, raw_assertion in enumerate(raw_assertions):
                    assertion = _mapping(
                        raw_assertion,
                        f"interactions[{index}].assertions[{assertion_index}]",
                    )
                    if assertion.get("kind") != "visible_text":
                        raise ValueError("interaction assertion kind must be visible_text")
                    assertions.append(
                        {
                            "kind": "visible_text",
                            "text": _nonempty_text(
                                assertion.get("text"),
                                f"interactions[{index}].assertions[{assertion_index}].text",
                            ),
                        }
                    )
                rendered = {"button": button, "assertions": assertions}
            interactions.append(rendered)
        if len({item["button"] for item in interactions}) != len(interactions):
            raise ValueError("agent eval step acceptance.interactions buttons must be unique")
        phase = data.get("phase", "evolution")
        if phase not in {"establishment", "evolution", "assurance", "leverage", "repair"}:
            raise ValueError("agent eval step phase is invalid")
        raw_tags = data.get("assurance_tags", [])
        if (
            not isinstance(raw_tags, list)
            or not all(isinstance(item, str) and item for item in raw_tags)
            or len(set(raw_tags)) != len(raw_tags)
        ):
            raise ValueError("agent eval step assurance_tags must be an array of unique non-empty strings")
        raw_geometry = acceptance.get("text_geometry", [])
        if not isinstance(raw_geometry, list):
            raise ValueError("agent eval step acceptance.text_geometry must be an array")
        text_geometry: list[dict[str, Any]] = []
        for index, raw_item in enumerate(raw_geometry):
            item = _mapping(raw_item, f"agent eval step acceptance.text_geometry[{index}]")
            text = _nonempty_text(item.get("text"), f"text_geometry[{index}].text")
            minimum_lines = item.get("minimum_lines", 1)
            if isinstance(minimum_lines, bool) or not isinstance(minimum_lines, int) or minimum_lines < 1:
                raise ValueError(f"text_geometry[{index}].minimum_lines must be a positive integer")
            maximum_lines = item.get("maximum_lines")
            if maximum_lines is not None and (
                isinstance(maximum_lines, bool)
                or not isinstance(maximum_lines, int)
                or maximum_lines < minimum_lines
            ):
                raise ValueError(
                    f"text_geometry[{index}].maximum_lines must be an integer greater than or equal to minimum_lines"
                )
            minimum_width_px = item.get("minimum_width_px")
            if minimum_width_px is not None and (
                isinstance(minimum_width_px, bool)
                or not isinstance(minimum_width_px, int)
                or minimum_width_px < 1
            ):
                raise ValueError(f"text_geometry[{index}].minimum_width_px must be a positive integer")
            viewport_width = item.get("viewport_width")
            if viewport_width is not None and (
                isinstance(viewport_width, bool) or not isinstance(viewport_width, int) or viewport_width < 1
            ):
                raise ValueError(f"text_geometry[{index}].viewport_width must be a positive integer")
            no_clip = item.get("no_clip", True)
            if not isinstance(no_clip, bool):
                raise ValueError(f"text_geometry[{index}].no_clip must be a boolean")
            no_word_fragmentation = item.get("no_word_fragmentation", False)
            if not isinstance(no_word_fragmentation, bool):
                raise ValueError(f"text_geometry[{index}].no_word_fragmentation must be a boolean")
            rendered_geometry: dict[str, Any] = {
                "text": text,
                "minimum_lines": minimum_lines,
                "no_clip": no_clip,
                **({"viewport_width": viewport_width} if viewport_width is not None else {}),
                **({"maximum_lines": maximum_lines} if maximum_lines is not None else {}),
                **({"minimum_width_px": minimum_width_px} if minimum_width_px is not None else {}),
                **(
                    {"no_word_fragmentation": no_word_fragmentation}
                    if "no_word_fragmentation" in item
                    else {}
                ),
            }
            identity = item.get("identity")
            if identity is not None:
                rendered_geometry["identity"] = _nonempty_text(
                    identity,
                    f"text_geometry[{index}].identity",
                )
            resource = item.get("resource")
            if resource is not None:
                resource_data = _mapping(resource, f"text_geometry[{index}].resource")
                if set(resource_data) != {"field", "record_id"}:
                    raise ValueError(
                        f"text_geometry[{index}].resource must contain exactly field and record_id"
                    )
                rendered_geometry["resource"] = {
                    "field": _nonempty_text(
                        resource_data.get("field"),
                        f"text_geometry[{index}].resource.field",
                    ),
                    "record_id": _nonempty_text(
                        resource_data.get("record_id"),
                        f"text_geometry[{index}].resource.record_id",
                    ),
                }
            text_geometry.append(rendered_geometry)
        required_text = texts("required_text")
        if not required_text:
            raise ValueError("agent eval step acceptance.required_text cannot be empty")
        unique_text = texts("unique_text")
        raw_resources = acceptance.get("resources", [])
        if not isinstance(raw_resources, list):
            raise ValueError("agent eval step acceptance.resources must be an array")
        resources: list[dict[str, Any]] = []
        for index, raw_resource in enumerate(raw_resources):
            resource = _mapping(raw_resource, f"agent eval step acceptance.resources[{index}]")
            count = resource.get("count", 1)
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError(f"resources[{index}].count must be a positive integer")
            resources.append(
                {
                    "identity": _nonempty_text(resource.get("identity"), f"resources[{index}].identity"),
                    "text": _nonempty_text(resource.get("text"), f"resources[{index}].text"),
                    "count": count,
                }
            )
        if len({item["identity"] for item in resources}) != len(resources):
            raise ValueError("agent eval step acceptance.resources identities must be unique")
        return cls(
            id=_nonempty_text(data.get("id"), "agent eval step id"),
            prompt=_nonempty_text(data.get("prompt"), "agent eval step prompt"),
            required_text=required_text,
            phase=phase,
            assurance_tags=tuple(raw_tags),
            forbidden_text=texts("forbidden_text"),
            required_buttons=texts("required_buttons"),
            text_order=texts("text_order"),
            click_button=click_button,
            click_reveals=click_reveals,
            interactions=tuple(interactions),
            text_geometry=tuple(text_geometry),
            unique_text=unique_text,
            resources=tuple(resources),
        )

    def to_score_spec(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "required_text": list(self.required_text),
            "forbidden_text": list(self.forbidden_text),
            "required_buttons": list(self.required_buttons),
            "text_order": list(self.text_order),
        }
        if self.click_button is not None and self.click_reveals is not None:
            payload["click"] = {"button": self.click_button, "reveals": self.click_reveals}
        if self.interactions:
            payload["interactions"] = [dict(item) for item in self.interactions]
        if self.text_geometry:
            payload["text_geometry"] = [dict(item) for item in self.text_geometry]
        if self.unique_text:
            payload["unique_text"] = list(self.unique_text)
        if self.resources:
            payload["resources"] = [dict(item) for item in self.resources]
        return payload


@dataclass(frozen=True)
class AgentEvalTask:
    id: str
    title: str
    brief: str
    reference: str
    visual_anchors: tuple[str, ...]
    steps: tuple[AgentEvalStep, ...]
    primary_heading: str | None = None

    @classmethod
    def from_json(cls, payload: Any) -> AgentEvalTask:
        data = _mapping(payload, "agent eval task")
        raw_steps = data.get("steps")
        if not isinstance(raw_steps, list) or len(raw_steps) < 2:
            raise ValueError("agent eval task requires at least two steps")
        steps = tuple(AgentEvalStep.from_json(item) for item in raw_steps)
        if len({step.id for step in steps}) != len(steps):
            raise ValueError("agent eval task step ids must be unique")
        anchors = data.get("visual_anchors")
        if not isinstance(anchors, list) or not anchors or not all(isinstance(item, str) and item for item in anchors):
            raise ValueError("agent eval task visual_anchors must be a non-empty string array")
        return cls(
            id=_nonempty_text(data.get("id"), "agent eval task id"),
            title=_nonempty_text(data.get("title"), "agent eval task title"),
            brief=_nonempty_text(data.get("brief"), "agent eval task brief"),
            reference=_nonempty_text(data.get("reference"), "agent eval task reference"),
            visual_anchors=tuple(anchors),
            steps=steps,
            primary_heading=(
                _nonempty_text(data.get("primary_heading"), "agent eval task primary_heading")
                if data.get("primary_heading") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class AgentEvalProtocol:
    schema_version: int
    id: str
    model: str
    seeds: tuple[int, ...]
    arms: tuple[str, ...]
    tasks: tuple[AgentEvalTask, ...]
    success_criteria: Mapping[str, float]
    evaluation_mode: str = "efficiency"
    primary_arm: str = "viewspec-deep"
    minimum_sessions_per_arm: int = 18
    study_design: Mapping[str, Any] | None = None
    qualification: Mapping[str, Any] | None = None

    @classmethod
    def from_json(cls, payload: Any) -> AgentEvalProtocol:
        data = _mapping(payload, "agent eval protocol")
        schema_version = data.get("schema_version")
        if schema_version not in AGENT_UI_EVAL_PROTOCOL_SCHEMA_VERSIONS:
            raise ValueError("Unsupported agent eval protocol schema_version")
        raw_arms = data.get("arms")
        if raw_arms != list(AGENT_UI_EVAL_ARMS):
            raise ValueError(f"agent eval arms must be exactly {list(AGENT_UI_EVAL_ARMS)}")
        raw_seeds = data.get("seeds")
        if not isinstance(raw_seeds, list) or not raw_seeds:
            raise ValueError("agent eval seeds must be a non-empty array")
        seeds = tuple(_nonnegative_int(item, "agent eval seed") for item in raw_seeds)
        if len(set(seeds)) != len(seeds):
            raise ValueError("agent eval seeds must be unique")
        raw_tasks = data.get("tasks")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise ValueError("agent eval tasks must be a non-empty array")
        tasks = tuple(AgentEvalTask.from_json(item) for item in raw_tasks)
        if len({task.id for task in tasks}) != len(tasks):
            raise ValueError("agent eval task ids must be unique")
        if schema_version == 2 and any(task.primary_heading is None for task in tasks):
            raise ValueError("agent eval protocol schema_version 2 tasks require primary_heading")
        raw_criteria = _mapping(data.get("success_criteria"), "agent eval success_criteria")
        evaluation_mode = data.get(
            "evaluation_mode",
            "efficiency" if schema_version == 1 else "value_premium",
        )
        if evaluation_mode not in AGENT_UI_EVAL_MODES:
            raise ValueError(f"agent eval evaluation_mode must be one of {list(AGENT_UI_EVAL_MODES)}")
        if schema_version == 1 and evaluation_mode != "efficiency":
            raise ValueError("agent eval protocol schema_version 1 requires efficiency mode")
        if schema_version == 2 and evaluation_mode != "value_premium":
            raise ValueError("agent eval protocol schema_version 2 requires value_premium mode")
        expected_criteria = (
            (
                "minimum_token_reduction",
                "minimum_iteration_token_reduction",
                "minimum_iteration_speedup",
                "minimum_regression_reduction",
                "minimum_deep_mutation_detection",
                "maximum_proof_overhead",
            )
            if evaluation_mode == "efficiency"
            else (
                "minimum_functional_acceptance",
                "minimum_layout_fidelity",
                "maximum_functional_acceptance_loss",
                "maximum_layout_fidelity_loss",
                "minimum_regression_reduction",
                "minimum_deep_proof_pass_rate",
                "minimum_mutation_detection_rate",
                "minimum_mutation_repair_rate",
                "maximum_mutation_false_positive_rate",
                "minimum_mutation_trials_per_session",
                "minimum_negative_control_trials_per_session",
                "minimum_cross_target_pass_rate",
                "minimum_cross_target_parity",
                "minimum_target_trials_per_session",
                "maximum_total_token_premium",
                "maximum_evolution_token_premium",
                "maximum_evolution_wall_time_premium",
                "maximum_proof_overhead",
            )
        )
        criteria: dict[str, float] = {}
        premium_keys = {
            "maximum_total_token_premium",
            "maximum_evolution_token_premium",
            "maximum_evolution_wall_time_premium",
        }
        count_keys = {
            "minimum_mutation_trials_per_session",
            "minimum_negative_control_trials_per_session",
            "minimum_target_trials_per_session",
        }
        unexpected_criteria = sorted(set(raw_criteria) - set(expected_criteria))
        if unexpected_criteria:
            raise ValueError(
                f"agent eval success_criteria has unexpected keys: {unexpected_criteria}"
            )
        for key in expected_criteria:
            value = raw_criteria.get(key)
            maximum = 100 if key in count_keys else (10 if key in premium_keys else 1)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= maximum:
                raise ValueError(
                    f"agent eval success_criteria.{key} must be between 0 and {maximum}"
                )
            criteria[key] = float(value)
        primary_arm = data.get("primary_arm", "viewspec-deep")
        if primary_arm not in AGENT_UI_EVAL_ARMS or primary_arm == "code-first":
            raise ValueError("agent eval primary_arm must be a ViewSpec arm")
        minimum_sessions = data.get("minimum_sessions_per_arm", 18)
        if isinstance(minimum_sessions, bool) or not isinstance(minimum_sessions, int) or minimum_sessions < 1:
            raise ValueError("agent eval minimum_sessions_per_arm must be a positive integer")
        raw_study_design = data.get("study_design")
        study_design = (
            dict(_mapping(raw_study_design, "agent eval study_design"))
            if raw_study_design is not None
            else None
        )
        raw_qualification = data.get("qualification")
        if schema_version == 1:
            if raw_qualification is not None:
                raise ValueError("agent eval protocol schema_version 1 cannot define qualification")
            qualification = None
        else:
            qualification = dict(
                _mapping(raw_qualification, "agent eval qualification")
            )
            expected_qualification_keys = {
                "max_turns_per_arm",
                "trigger",
                "feedback",
                "thread_policy",
                "cost_accounting",
            }
            if set(qualification) != expected_qualification_keys:
                raise ValueError(
                    "agent eval qualification must define the exact V2.2 contract"
                )
            max_turns = qualification.get("max_turns_per_arm")
            if (
                isinstance(max_turns, bool)
                or not isinstance(max_turns, int)
                or not 1 <= max_turns <= 5
            ):
                raise ValueError(
                    "agent eval qualification.max_turns_per_arm must be between 1 and 5"
                )
            expected_values = {
                "trigger": "post-lifecycle-ineligible-or-layout-miss",
                "feedback": "compact-evaluator-only",
                "thread_policy": "continue-lifecycle-thread",
                "cost_accounting": "total-and-post-establishment",
            }
            if any(qualification.get(key) != value for key, value in expected_values.items()):
                raise ValueError("agent eval qualification policy is invalid")
        return cls(
            schema_version=schema_version,
            id=_nonempty_text(data.get("id"), "agent eval protocol id"),
            model=_nonempty_text(data.get("model"), "agent eval protocol model"),
            seeds=seeds,
            arms=tuple(raw_arms),
            tasks=tasks,
            success_criteria=criteria,
            evaluation_mode=evaluation_mode,
            primary_arm=primary_arm,
            minimum_sessions_per_arm=minimum_sessions,
            study_design=study_design,
            qualification=qualification,
        )

    def task(self, task_id: str) -> AgentEvalTask:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise ValueError(f"Unknown agent eval task: {task_id}")

    @property
    def qualification_max_turns(self) -> int:
        if not isinstance(self.qualification, Mapping):
            return 0
        value = self.qualification.get("max_turns_per_arm")
        return value if isinstance(value, int) and not isinstance(value, bool) else 0


def load_agent_eval_protocol(path: str | Path) -> AgentEvalProtocol:
    protocol_path = Path(path)
    return AgentEvalProtocol.from_json(json.loads(protocol_path.read_text(encoding="utf-8")))


def parse_codex_jsonl(text: str) -> dict[str, Any]:
    """Parse stable Codex JSONL events while tolerating diagnostic lines."""
    thread_id: str | None = None
    messages: list[str] = []
    usage = {key: 0 for key in TOKEN_KEYS}
    completed = False
    event_count = 0
    event_types: dict[str, int] = {}
    item_types: dict[str, int] = {}
    commands: list[dict[str, Any]] = []
    file_changes: list[dict[str, str]] = []
    skill_reads: set[str] = set()
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            continue
        event_count += 1
        event_types[event["type"]] = event_types.get(event["type"], 0) + 1
        if event["type"] == "thread.started" and isinstance(event.get("thread_id"), str):
            thread_id = event["thread_id"]
        elif event["type"] == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and isinstance(item.get("type"), str):
                item_type = item["type"]
                item_types[item_type] = item_types.get(item_type, 0) + 1
                if item_type == "agent_message" and isinstance(item.get("text"), str):
                    messages.append(item["text"])
                elif item_type == "command_execution":
                    command = item.get("command") if isinstance(item.get("command"), str) else ""
                    output = item.get("aggregated_output") if isinstance(item.get("aggregated_output"), str) else ""
                    for match in re.findall(r"/[^\s\"']*/SKILL\.md", command):
                        parent = Path(match).parent.name
                        if parent:
                            skill_reads.add(parent)
                    commands.append(
                        {
                            "id": item.get("id") if isinstance(item.get("id"), str) else None,
                            "status": item.get("status") if isinstance(item.get("status"), str) else None,
                            "exit_code": item.get("exit_code") if isinstance(item.get("exit_code"), int) else None,
                            "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
                            "command_bytes": len(command.encode("utf-8")),
                            "output_bytes": len(output.encode("utf-8")),
                        }
                    )
                elif item_type == "file_change":
                    changes = item.get("changes")
                    if isinstance(changes, list):
                        for change in changes:
                            if not isinstance(change, Mapping):
                                continue
                            path = change.get("path")
                            kind = change.get("kind")
                            if isinstance(path, str):
                                file_changes.append(
                                    {
                                        "path_tail": "/".join(Path(path).parts[-3:]),
                                        "kind": str(kind or "unknown")[:64],
                                    }
                                )
        elif event["type"] == "turn.completed":
            raw_usage = event.get("usage")
            if isinstance(raw_usage, dict):
                usage = {
                    key: _nonnegative_int(raw_usage.get(key, 0), f"Codex usage.{key}")
                    for key in TOKEN_KEYS
                }
            completed = True
    command_failures = sum(
        command.get("status") != "completed" or command.get("exit_code") not in (0, None)
        for command in commands
    )
    return {
        "thread_id": thread_id,
        "agent_message": messages[-1] if messages else "",
        "usage": usage,
        "completed": completed,
        "event_count": event_count,
        "telemetry": {
            "event_types": dict(sorted(event_types.items())),
            "item_types": dict(sorted(item_types.items())),
            "command_count": len(commands),
            "command_failure_count": command_failures,
            "command_output_bytes": sum(int(command["output_bytes"]) for command in commands),
            "commands": commands,
            "file_change_count": len(file_changes),
            "file_changes": file_changes,
            "skill_reads": sorted(skill_reads),
        },
    }


def _score_fraction(score: Mapping[str, Any]) -> float:
    passed = score.get("passed")
    total = score.get("total")
    if isinstance(passed, int) and isinstance(total, int) and total > 0:
        return passed / total
    return 0.0


def _dimension_facts(score: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    dimensions = score.get("dimensions")
    if not isinstance(dimensions, Mapping):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for name, raw in dimensions.items():
        if not isinstance(name, str) or not isinstance(raw, Mapping):
            continue
        passed = raw.get("passed")
        total = raw.get("total")
        if isinstance(passed, int) and isinstance(total, int) and total > 0:
            result[name] = {"passed": passed, "total": total, "score": passed / total}
    return result


def _functional_fraction(score: Mapping[str, Any]) -> float:
    dimensions = _dimension_facts(score)
    selected = [value for name, value in dimensions.items() if name != "layout_fidelity"]
    passed = sum(value["passed"] for value in selected)
    total = sum(value["total"] for value in selected)
    return passed / total if total else 0.0


def _layout_fidelity_value(score: Mapping[str, Any]) -> float:
    viewports = score.get("viewports")
    values = [
        float(viewport["layout_fidelity"])
        for viewport in viewports
        if isinstance(viewport, Mapping)
        and isinstance(viewport.get("layout_fidelity"), (int, float))
        and not isinstance(viewport.get("layout_fidelity"), bool)
        and math.isfinite(viewport["layout_fidelity"])
    ] if isinstance(viewports, list) else []
    if values:
        return min(values)
    value = _dimension_facts(score).get("layout_fidelity", {}).get("score")
    return float(value) if isinstance(value, (int, float)) else 0.0


def _criterion_states(score: Mapping[str, Any]) -> dict[str, bool]:
    states: dict[str, bool] = {}
    viewports = score.get("viewports")
    if not isinstance(viewports, list):
        return states
    for viewport in viewports:
        if not isinstance(viewport, Mapping):
            continue
        size = viewport.get("viewport")
        width = size.get("width") if isinstance(size, Mapping) else None
        height = size.get("height") if isinstance(size, Mapping) else None
        criteria = viewport.get("criteria")
        if not isinstance(criteria, list):
            continue
        for item in criteria:
            if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                continue
            passed = item.get("passed")
            if isinstance(passed, bool):
                states[f"{width}x{height}:{item['id']}"] = passed
    return states


def _proof_summary(turns: list[Mapping[str, Any]]) -> dict[str, Any]:
    proof_count = 0
    passed_count = 0
    error_codes: list[str] = []
    analysis_statuses: dict[str, dict[str, int]] = {"freerange": {}, "pretext": {}}
    for turn in turns:
        proof = turn.get("proof")
        if not isinstance(proof, Mapping):
            continue
        proof_count += 1
        if proof.get("ok") is True:
            passed_count += 1
        errors = proof.get("errors")
        if isinstance(errors, list):
            for error in errors:
                if isinstance(error, Mapping) and isinstance(error.get("code"), str):
                    error_codes.append(error["code"])
        for key, report_key in (("freerange", "static_analysis"), ("pretext", "text_layout")):
            report = proof.get(report_key)
            if isinstance(report, Mapping) and isinstance(report.get("status"), str):
                status = report["status"]
                analysis_statuses[key][status] = analysis_statuses[key].get(status, 0) + 1
    return {
        "proof_turn_count": proof_count,
        "passed_turn_count": passed_count,
        "failed_turn_count": proof_count - passed_count,
        "error_codes": error_codes,
        "analyses": analysis_statuses,
    }


def _trial_rate(trials: list[Mapping[str, Any]], key: str) -> float | None:
    applicable = [trial for trial in trials if trial.get("applicable", True) is True]
    if not applicable:
        return None
    observed = [trial.get(key) for trial in applicable]
    if not all(isinstance(value, bool) for value in observed):
        return None
    return sum(value is True for value in observed) / len(observed)


def _value_evidence_summary(payload: Any) -> dict[str, Any]:
    if payload is None:
        payload = {}
    data = _mapping(payload, "agent eval value_evidence")
    raw_mutations = data.get("mutation_trials", [])
    raw_controls = data.get("negative_control_trials", [])
    raw_targets = data.get("target_trials", [])
    if not isinstance(raw_mutations, list) or not all(isinstance(item, Mapping) for item in raw_mutations):
        raise ValueError("agent eval value_evidence.mutation_trials must be an array of objects")
    if not isinstance(raw_controls, list) or not all(isinstance(item, Mapping) for item in raw_controls):
        raise ValueError("agent eval value_evidence.negative_control_trials must be an array of objects")
    if not isinstance(raw_targets, list) or not all(isinstance(item, Mapping) for item in raw_targets):
        raise ValueError("agent eval value_evidence.target_trials must be an array of objects")
    mutations = [dict(item) for item in raw_mutations]
    controls = [dict(item) for item in raw_controls]
    targets = [dict(item) for item in raw_targets]
    applicable_mutations = [item for item in mutations if item.get("applicable", True) is True]
    detected_mutations = [item for item in applicable_mutations if item.get("detected") is True]
    repairable_mutations = [
        item
        for item in detected_mutations
        if item.get("repair_applicable", True) is True
    ]
    repaired_values = [item.get("repaired") for item in repairable_mutations]
    repair_rate = (
        sum(value is True for value in repaired_values) / len(repaired_values)
        if repaired_values and all(isinstance(value, bool) for value in repaired_values)
        else None
    )
    applicable_targets = [item for item in targets if item.get("applicable", True) is True]
    raw_artifact_integrity = data.get("artifact_integrity", {})
    artifact_integrity = (
        dict(raw_artifact_integrity)
        if isinstance(raw_artifact_integrity, Mapping)
        else {}
    )
    missing_artifacts = artifact_integrity.get("missing", [])
    artifact_errors = artifact_integrity.get("errors", [])
    artifact_integrity_complete = (
        artifact_integrity.get("checked") is True
        and artifact_integrity.get("complete") is True
        and isinstance(artifact_integrity.get("declared_reference_count"), int)
        and not isinstance(artifact_integrity.get("declared_reference_count"), bool)
        and artifact_integrity["declared_reference_count"] > 0
        and isinstance(missing_artifacts, list)
        and not missing_artifacts
        and isinstance(artifact_errors, list)
        and not artifact_errors
    )

    def unit_interval(value: Any) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and 0 <= value <= 1
        )

    mutation_fields = {
        "id",
        "order",
        "applicable",
        "baseline_sha256",
        "mutated_sha256",
        "expected_detectors",
        "observed_detectors",
        "detected",
        "deterministic_ms",
        "commands",
        "artifacts",
    }
    control_fields = {
        "id",
        "order",
        "applicable",
        "baseline_sha256",
        "detected",
        "deterministic_ms",
        "commands",
        "artifacts",
    }
    target_fields = {
        "id",
        "applicable",
        "build",
        "functional_acceptance",
        "layout_fidelity",
        "passed",
        "parity",
        "parity_by_viewport",
        "score_artifact",
    }

    def structurally_complete(item: Mapping[str, Any], required: set[str]) -> bool:
        if item.get("applicable", True) is True:
            return required <= set(item)
        return {"id", "order", "applicable", "baseline_sha256", "invalid_reason"} <= set(item)

    def artifact_manifest_complete(item: Mapping[str, Any]) -> bool:
        if item.get("applicable", True) is not True:
            return True
        artifacts = item.get("artifacts")
        if not isinstance(artifacts, Mapping):
            return False
        root = artifacts.get("root")
        status = artifacts.get("score_status")
        if not isinstance(root, str) or not root:
            return False
        if status == "recorded":
            return isinstance(artifacts.get("score"), str) and bool(artifacts["score"])
        if status == "not_run_early_detector":
            detector_evidence = artifacts.get("detector_evidence")
            return (
                "score" not in artifacts
                and isinstance(detector_evidence, list)
                and bool(detector_evidence)
                and all(isinstance(path, str) and path for path in detector_evidence)
            )
        return False

    mutation_records_complete = all(
        structurally_complete(item, mutation_fields) for item in mutations
    )
    control_records_complete = all(
        structurally_complete(item, control_fields) for item in controls
    )
    target_records_complete = all(
        target_fields <= set(item) for item in applicable_targets
    )
    repair_records_complete = all(
        not item.get("repair_applicable")
        or {"repaired", "repaired_sha256", "repair_usage", "repair_wall_time_ms"} <= set(item)
        for item in applicable_mutations
    )
    trial_artifact_manifests_complete = all(
        artifact_manifest_complete(item) for item in [*mutations, *controls]
    )
    structural_complete = (
        bool(mutations or controls or targets)
        and mutation_records_complete
        and control_records_complete
        and target_records_complete
        and repair_records_complete
        and trial_artifact_manifests_complete
        and artifact_integrity_complete
    )

    mutation_evidence_complete = (
        bool(mutations)
        and bool(controls)
        and len(applicable_mutations) == len(mutations)
        and len([item for item in controls if item.get("applicable", True) is True]) == len(controls)
        and mutation_records_complete
        and control_records_complete
        and repair_records_complete
        and trial_artifact_manifests_complete
        and artifact_integrity_complete
    )

    def target_evidence_complete(item: Mapping[str, Any]) -> bool:
        build = item.get("build")
        parity_by_viewport = item.get("parity_by_viewport")
        return (
            item.get("applicable", True) is True
            and target_fields <= set(item)
            and isinstance(build, Mapping)
            and build.get("ok") is True
            and unit_interval(item.get("functional_acceptance"))
            and unit_interval(item.get("layout_fidelity"))
            and isinstance(item.get("passed"), bool)
            and unit_interval(item.get("parity"))
            and isinstance(parity_by_viewport, Mapping)
            and set(parity_by_viewport) == {"390", "768", "1440"}
            and all(unit_interval(value) for value in parity_by_viewport.values())
            and isinstance(item.get("score_artifact"), str)
            and bool(item["score_artifact"])
        )

    complete_targets = [item for item in applicable_targets if target_evidence_complete(item)]
    cross_target_evidence_complete = (
        bool(targets)
        and len(applicable_targets) == len(targets)
        and len(complete_targets) == len(targets)
    )
    parity_values = [float(item["parity"]) for item in complete_targets]
    substantive_complete = (
        structural_complete
        and mutation_evidence_complete
        and cross_target_evidence_complete
    )
    return {
        "structural_evidence_complete": structural_complete,
        "evidence_complete": substantive_complete,
        "artifact_integrity": {
            "checked": artifact_integrity.get("checked") is True,
            "complete": artifact_integrity_complete,
            "declared_reference_count": artifact_integrity.get(
                "declared_reference_count", 0
            ),
            "missing": missing_artifacts if isinstance(missing_artifacts, list) else [],
            "errors": artifact_errors if isinstance(artifact_errors, list) else [],
        },
        "mutation": {
            "evidence_complete": mutation_evidence_complete,
            "trial_count": len(mutations),
            "applicable_count": len(applicable_mutations),
            "detected_count": sum(item.get("detected") is True for item in applicable_mutations),
            "repair_applicable_count": len(repairable_mutations),
            "repaired_count": sum(item.get("repaired") is True for item in repairable_mutations),
            "detection_rate": _trial_rate(mutations, "detected"),
            "repair_rate": repair_rate,
            "negative_control_count": len(controls),
            "applicable_negative_control_count": len(
                [item for item in controls if item.get("applicable", True) is True]
            ),
            "false_positive_count": sum(
                item.get("detected") is True
                for item in controls
                if item.get("applicable", True) is True
            ),
            "false_positive_rate": _trial_rate(controls, "detected"),
        },
        "cross_target": {
            "evidence_complete": cross_target_evidence_complete,
            "trial_count": len(targets),
            "applicable_count": len(applicable_targets),
            "complete_count": len(complete_targets),
            "passed_count": sum(item.get("passed") is True for item in applicable_targets),
            "pass_rate": _trial_rate(targets, "passed"),
            "minimum_parity": (
                min(parity_values) if cross_target_evidence_complete else None
            ),
        },
    }


def summarize_agent_eval_session(payload: Any) -> dict[str, Any]:
    data = _mapping(payload, "agent eval session")
    if data.get("schema_version") != AGENT_UI_EVAL_SCHEMA_VERSION:
        raise ValueError("Unsupported agent eval session schema_version")
    arm = data.get("arm_id")
    if arm not in AGENT_UI_EVAL_ARMS:
        raise ValueError("agent eval session arm_id is invalid")
    raw_lifecycle_turns = data.get("turns")
    if not isinstance(raw_lifecycle_turns, list) or not raw_lifecycle_turns:
        raise ValueError("agent eval session turns must be a non-empty array")
    raw_qualification_turns = data.get("qualification_turns", [])
    if not isinstance(raw_qualification_turns, list):
        raise ValueError("agent eval session qualification_turns must be an array")
    raw_turns = [*raw_lifecycle_turns, *raw_qualification_turns]
    selected_turn: Mapping[str, Any] = _mapping(
        raw_turns[-1], "agent eval selected turn"
    )
    selected_turn_ref: dict[str, Any] | None = None
    qualification = data.get("qualification")
    if isinstance(qualification, Mapping):
        candidate_ref = qualification.get("selected_turn")
        if isinstance(candidate_ref, Mapping):
            kind = candidate_ref.get("kind")
            index = candidate_ref.get("index")
            collection = (
                raw_lifecycle_turns
                if kind == "lifecycle"
                else raw_qualification_turns
                if kind == "qualification"
                else None
            )
            if (
                not isinstance(collection, list)
                or isinstance(index, bool)
                or not isinstance(index, int)
                or not 0 <= index < len(collection)
            ):
                raise ValueError("agent eval qualification selected_turn is invalid")
            selected_turn = _mapping(collection[index], "agent eval selected turn")
            selected_turn_ref = dict(candidate_ref)
    totals = {key: 0 for key in TOKEN_KEYS}
    accepted = 0
    functionally_accepted = 0
    regressions = 0
    regression_turns = 0
    previous_states: dict[str, bool] = {}
    open_regressions: dict[str, int] = {}
    regression_repair_turns: list[int] = []
    transition_turns: list[dict[str, Any]] = []
    total_wall_ms = 0
    deterministic_ms = 0
    model_wall_ms = 0
    phase_totals: dict[str, int] = {}
    lifecycle_totals: dict[str, dict[str, int]] = {}
    tool_totals = {
        "command_count": 0,
        "command_failure_count": 0,
        "command_output_bytes": 0,
        "file_change_count": 0,
    }
    skill_reads: set[str] = set()
    source_totals = {"snapshots": 0, "bytes": 0, "lines": 0, "added_lines": 0, "removed_lines": 0}
    fractions: list[float] = []
    functional_fractions: list[float] = []
    parsed_turns: list[Mapping[str, Any]] = []
    for index, raw_turn in enumerate(raw_turns):
        turn = _mapping(raw_turn, f"agent eval turn {index}")
        parsed_turns.append(turn)
        usage = _mapping(turn.get("usage"), f"agent eval turn {index} usage")
        lifecycle_phase = turn.get("phase", "unspecified")
        if not isinstance(lifecycle_phase, str) or not lifecycle_phase:
            raise ValueError(f"agent eval turn {index} phase must be non-empty text")
        lifecycle = lifecycle_totals.setdefault(
            lifecycle_phase,
            {
                "turn_count": 0,
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "model_wall_ms": 0,
                "deterministic_ms": 0,
                "total_wall_ms": 0,
            },
        )
        lifecycle["turn_count"] += 1
        for key in TOKEN_KEYS:
            value = _nonnegative_int(usage.get(key, 0), f"agent eval turn {index} usage.{key}")
            totals[key] += value
            if key in lifecycle:
                lifecycle[key] += value
        lifecycle["total_tokens"] += (
            _nonnegative_int(usage.get("input_tokens", 0), "input_tokens")
            + _nonnegative_int(usage.get("output_tokens", 0), "output_tokens")
        )
        wall_ms = _nonnegative_int(turn.get("wall_time_ms", 0), f"agent eval turn {index} wall_time_ms")
        proof_ms = _nonnegative_int(turn.get("deterministic_ms", 0), f"agent eval turn {index} deterministic_ms")
        lifecycle["model_wall_ms"] += wall_ms
        lifecycle["deterministic_ms"] += proof_ms
        lifecycle["total_wall_ms"] += wall_ms + proof_ms
        total_wall_ms += wall_ms + proof_ms
        model_wall_ms += wall_ms
        deterministic_ms += proof_ms
        raw_phases = turn.get("phase_timings_ms")
        if isinstance(raw_phases, Mapping):
            for name, value in raw_phases.items():
                if isinstance(name, str) and isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    phase_totals[name] = phase_totals.get(name, 0) + value
        agent_telemetry = turn.get("agent_telemetry")
        if isinstance(agent_telemetry, Mapping):
            for key in tool_totals:
                value = agent_telemetry.get(key, 0)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    tool_totals[key] += value
            raw_skills = agent_telemetry.get("skill_reads")
            if isinstance(raw_skills, list):
                skill_reads.update(item for item in raw_skills if isinstance(item, str))
        source = turn.get("source")
        if isinstance(source, Mapping):
            source_totals["snapshots"] += 1
            for key in ("bytes", "lines"):
                value = source.get(key, 0)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    source_totals[key] += value
            delta = source.get("delta")
            if isinstance(delta, Mapping):
                for key in ("added_lines", "removed_lines"):
                    value = delta.get(key, 0)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        source_totals[key] += value
        score = _mapping(turn.get("score", {}), f"agent eval turn {index} score")
        fraction = _score_fraction(score)
        fractions.append(fraction)
        functional_fraction = _functional_fraction(score)
        functional_fractions.append(functional_fraction)
        if score.get("ok") is True:
            accepted += 1
        if functional_fraction >= 1.0:
            functionally_accepted += 1
        current_states = _criterion_states(score)
        lost = [key for key, passed in previous_states.items() if passed and current_states.get(key) is False]
        newly_passed = [key for key, passed in current_states.items() if passed and key not in previous_states]
        recovered = [key for key, passed in current_states.items() if passed and previous_states.get(key) is False]
        transition_turns.append(
            {
                "step_id": turn.get("step_id"),
                "phase": lifecycle_phase,
                "lost": sorted(lost),
                "newly_passed": sorted(newly_passed),
                "recovered": sorted(recovered),
            }
        )
        regressions += len(lost)
        if lost:
            regression_turns += 1
        for key in lost:
            open_regressions.setdefault(key, index)
        for key in recovered:
            started = open_regressions.pop(key, None)
            if started is not None:
                regression_repair_turns.append(index - started)
        previous_states = current_states
    raw_value_evidence = data.get("value_evidence")
    value_evidence = (
        _mapping(raw_value_evidence, "agent eval value_evidence")
        if raw_value_evidence is not None
        else {}
    )
    raw_repair_turns = value_evidence.get("repair_turns", [])
    if not isinstance(raw_repair_turns, list):
        raise ValueError("agent eval value_evidence.repair_turns must be an array")
    repair_totals = {key: 0 for key in TOKEN_KEYS}
    repair_model_wall_ms = 0
    repair_deterministic_ms = 0
    repair_activity = lifecycle_totals.setdefault(
        "repair_trials",
        {
            "turn_count": 0,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "model_wall_ms": 0,
            "deterministic_ms": 0,
            "total_wall_ms": 0,
        },
    )
    for index, raw_repair in enumerate(raw_repair_turns):
        repair = _mapping(raw_repair, f"agent eval repair turn {index}")
        usage = _mapping(repair.get("usage", {}), f"agent eval repair turn {index} usage")
        repair_activity["turn_count"] += 1
        for key in TOKEN_KEYS:
            value = _nonnegative_int(
                usage.get(key, 0),
                f"agent eval repair turn {index} usage.{key}",
            )
            totals[key] += value
            repair_totals[key] += value
            if key in repair_activity:
                repair_activity[key] += value
        repair_tokens = (
            _nonnegative_int(usage.get("input_tokens", 0), "repair input_tokens")
            + _nonnegative_int(usage.get("output_tokens", 0), "repair output_tokens")
        )
        repair_activity["total_tokens"] += repair_tokens
        repair_wall = _nonnegative_int(
            repair.get("wall_time_ms", 0),
            f"agent eval repair turn {index} wall_time_ms",
        )
        repair_deterministic = _nonnegative_int(
            repair.get("deterministic_ms", 0),
            f"agent eval repair turn {index} deterministic_ms",
        )
        repair_model_wall_ms += repair_wall
        repair_deterministic_ms += repair_deterministic
        repair_activity["model_wall_ms"] += repair_wall
        repair_activity["deterministic_ms"] += repair_deterministic
        repair_activity["total_wall_ms"] += repair_wall + repair_deterministic
        telemetry = repair.get("agent_telemetry")
        if isinstance(telemetry, Mapping):
            for key in tool_totals:
                value = telemetry.get(key, 0)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    tool_totals[key] += value
            raw_skills = telemetry.get("skill_reads")
            if isinstance(raw_skills, list):
                skill_reads.update(item for item in raw_skills if isinstance(item, str))
    deterministic_overhead_ms = _nonnegative_int(
        value_evidence.get("deterministic_overhead_ms", 0),
        "agent eval value_evidence.deterministic_overhead_ms",
    )
    if deterministic_overhead_ms < repair_deterministic_ms:
        raise ValueError("value evidence deterministic overhead omits repair verification")
    model_wall_ms += repair_model_wall_ms
    deterministic_ms += deterministic_overhead_ms
    total_wall_ms += repair_model_wall_ms + deterministic_overhead_ms
    uncached = max(0, totals["input_tokens"] - totals["cached_input_tokens"])
    billable = uncached + totals["cache_write_input_tokens"]
    repair_token_count = repair_totals["input_tokens"] + repair_totals["output_tokens"]
    iteration_tokens = sum(
        _nonnegative_int(_mapping(turn.get("usage"), "turn usage").get("input_tokens", 0), "input_tokens")
        + _nonnegative_int(_mapping(turn.get("usage"), "turn usage").get("output_tokens", 0), "output_tokens")
        for turn in raw_turns[1:]
    ) + repair_token_count
    iteration_model_wall_ms = sum(
        _nonnegative_int(turn.get("wall_time_ms", 0), "iteration wall_time_ms")
        for turn in parsed_turns[1:]
    ) + repair_model_wall_ms
    iteration_deterministic_ms = sum(
        _nonnegative_int(turn.get("deterministic_ms", 0), "iteration deterministic_ms")
        for turn in parsed_turns[1:]
    ) + deterministic_overhead_ms
    selected_score = _mapping(selected_turn.get("score", {}), "final selected score")
    final_dimensions = _dimension_facts(selected_score)
    final_layout_fidelity = _layout_fidelity_value(
        selected_score
    )
    evolution_phases = {
        name: values for name, values in lifecycle_totals.items() if name != "establishment"
    }
    evolution_tokens = sum(
        values["total_tokens"]
        for name, values in evolution_phases.items()
        if name != "repair_trials"
    ) + repair_token_count
    evolution_model_wall_ms = sum(
        values["model_wall_ms"]
        for name, values in evolution_phases.items()
        if name != "repair_trials"
    ) + repair_model_wall_ms
    evolution_deterministic_ms = sum(
        values["deterministic_ms"]
        for name, values in evolution_phases.items()
        if name != "repair_trials"
    ) + deterministic_overhead_ms
    qualification_activity = lifecycle_totals.get("qualification", {})
    qualification_tokens = qualification_activity.get("total_tokens", 0)
    qualification_model_wall_ms = qualification_activity.get("model_wall_ms", 0)
    qualification_deterministic_ms = qualification_activity.get("deterministic_ms", 0)
    selected_native_proof = _proof_summary([selected_turn])
    intermediate_native_proof = _proof_summary(
        [turn for turn in parsed_turns if turn is not selected_turn]
    )
    return {
        "schema_version": AGENT_UI_EVAL_SCHEMA_VERSION,
        "protocol_id": data.get("protocol_id"),
        "task_id": data.get("task_id"),
        "arm_id": arm,
        "seed": data.get("seed"),
        "model": data.get("model"),
        "environment": data.get("environment"),
        "turn_count": len(raw_turns),
        "lifecycle_turn_count": len(raw_lifecycle_turns),
        "qualification_turn_count": len(raw_qualification_turns),
        "selected_turn": selected_turn_ref,
        "accepted_turn_count": accepted,
        "acceptance_rate": accepted / len(raw_turns),
        "final_acceptance": _score_fraction(selected_score),
        "functionally_accepted_turn_count": functionally_accepted,
        "functional_acceptance_rate": functionally_accepted / len(raw_turns),
        "final_functional_acceptance": _functional_fraction(selected_score),
        "final_dimensions": final_dimensions,
        "final_layout_fidelity": final_layout_fidelity,
        "regression_count": regressions,
        "regression_turn_count": regression_turns,
        "durability": {
            "regression_count": regressions,
            "repaired_regression_count": len(regression_repair_turns),
            "unresolved_regression_count": len(open_regressions),
            "median_repair_turns": _median(regression_repair_turns),
        },
        "criterion_transitions": {
            "lost_count": sum(len(item["lost"]) for item in transition_turns),
            "newly_passed_count": sum(len(item["newly_passed"]) for item in transition_turns),
            "recovered_count": sum(len(item["recovered"]) for item in transition_turns),
            "turns": transition_turns,
        },
        "tokens": {
            **totals,
            "uncached_input_tokens": uncached,
            "billing_equivalent_input_tokens": billable,
            "total_tokens": totals["input_tokens"] + totals["output_tokens"],
            "iteration_tokens": iteration_tokens,
            "evolution_tokens": evolution_tokens,
            "repair_tokens": repair_token_count,
            "repair_turn_count": len(raw_repair_turns),
            "qualification_tokens": qualification_tokens,
            "tokens_per_accepted_turn": (
                (totals["input_tokens"] + totals["output_tokens"]) / accepted if accepted else None
            ),
            "tokens_per_functionally_accepted_turn": (
                (totals["input_tokens"] + totals["output_tokens"]) / functionally_accepted
                if functionally_accepted
                else None
            ),
        },
        "timing": {
            "model_wall_ms": model_wall_ms,
            "deterministic_ms": deterministic_ms,
            "total_wall_ms": total_wall_ms,
            "proof_overhead_ratio": deterministic_ms / total_wall_ms if total_wall_ms else 0.0,
            "iteration_model_wall_ms": iteration_model_wall_ms,
            "iteration_deterministic_ms": iteration_deterministic_ms,
            "iteration_total_wall_ms": iteration_model_wall_ms + iteration_deterministic_ms,
            "evolution_model_wall_ms": evolution_model_wall_ms,
            "evolution_deterministic_ms": evolution_deterministic_ms,
            "evolution_total_wall_ms": evolution_model_wall_ms + evolution_deterministic_ms,
            "repair_model_wall_ms": repair_model_wall_ms,
            "repair_deterministic_ms": repair_deterministic_ms,
            "qualification_model_wall_ms": qualification_model_wall_ms,
            "qualification_deterministic_ms": qualification_deterministic_ms,
            "value_trial_deterministic_overhead_ms": deterministic_overhead_ms,
            "phases_ms": dict(sorted(phase_totals.items())),
        },
        "lifecycle_activity": dict(sorted(lifecycle_totals.items())),
        "tool_activity": {**tool_totals, "skill_reads": sorted(skill_reads)},
        "source_activity": source_totals,
        "native_proof": _proof_summary(parsed_turns),
        "selected_native_proof": selected_native_proof,
        "intermediate_native_proof_activity": intermediate_native_proof,
        "value_evidence": _value_evidence_summary(data.get("value_evidence")),
    }


def _median(items: Iterable[float]) -> float | None:
    values = list(items)
    return statistics.median(values) if values else None


def _reduction(baseline: float | None, candidate: float | None) -> float | None:
    if baseline is None or candidate is None or baseline <= 0:
        return None
    return (baseline - candidate) / baseline


def _premium(baseline: float | None, candidate: float | None) -> float | None:
    if baseline is None or candidate is None or baseline <= 0:
        return None
    return candidate / baseline


def summarize_agent_eval_shakedown_exit(
    sessions: Iterable[Any],
    *,
    success_criteria: Mapping[str, float],
) -> dict[str, Any]:
    payloads = [_mapping(item, "agent eval shakedown session") for item in sessions]
    summaries = [summarize_agent_eval_session(item) for item in payloads]
    by_arm = {summary["arm_id"]: (payload, summary) for payload, summary in zip(payloads, summaries)}
    exact_arm_set = len(payloads) == len(AGENT_UI_EVAL_ARMS) and set(by_arm) == set(
        AGENT_UI_EVAL_ARMS
    )
    seeds = {summary.get("seed") for summary in summaries}
    models = {summary.get("model") for summary in summaries}
    protocols = {summary.get("protocol_id") for summary in summaries}
    arm_results: dict[str, Any] = {}
    for arm in ("viewspec-core", "viewspec-deep"):
        pair = by_arm.get(arm)
        if pair is None:
            arm_results[arm] = {"pass": False, "checks": {"session_present": False}}
            continue
        payload, summary = pair
        evidence = payload.get("value_evidence")
        evidence = evidence if isinstance(evidence, Mapping) else {}
        baseline = evidence.get("baseline")
        baseline = baseline if isinstance(baseline, Mapping) else {}
        mutations = evidence.get("mutation_trials")
        mutations = mutations if isinstance(mutations, list) else []
        controls = evidence.get("negative_control_trials")
        controls = controls if isinstance(controls, list) else []
        targets = evidence.get("target_trials")
        targets = targets if isinstance(targets, list) else []
        target_by_id = {
            item.get("id"): item
            for item in targets
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        }
        required_targets = [target_by_id.get("static-shell"), target_by_id.get("native-react")]
        target_builds = all(
            isinstance(item, Mapping)
            and isinstance(item.get("build"), Mapping)
            and item["build"].get("ok") is True
            for item in required_targets
        )
        target_functional = all(
            isinstance(item, Mapping)
            and item.get("functional_acceptance") == 1.0
            and item.get("passed") is True
            for item in required_targets
        )
        layout_minimum = success_criteria["minimum_layout_fidelity"]
        target_layout = all(
            isinstance(item, Mapping)
            and finite_number(item.get("layout_fidelity"))
            and item["layout_fidelity"] >= layout_minimum
            for item in required_targets
        )
        parity_minimum = success_criteria["minimum_cross_target_parity"]
        target_parity = all(
            isinstance(item, Mapping)
            and isinstance(item.get("parity_by_viewport"), Mapping)
            and set(item["parity_by_viewport"]) == {"390", "768", "1440"}
            and all(
                finite_number(value) and value >= parity_minimum
                for value in item["parity_by_viewport"].values()
            )
            for item in required_targets
        )
        selected_proof = summary["selected_native_proof"]
        selected_proof_healthy = (
            selected_proof["proof_turn_count"] == 1
            and selected_proof["passed_turn_count"] == 1
        )
        deep_integrations_healthy = True
        if arm == "viewspec-deep":
            deep_integrations_healthy = all(
                selected_proof["analyses"][name].get("passed") == 1
                for name in ("freerange", "pretext")
            )
        mutation_hashes_complete = all(
            isinstance(item, Mapping)
            and all(
                isinstance(item.get(key), str) and len(item[key]) == 64
                for key in ("baseline_sha256", "mutated_sha256")
            )
            and (
                item.get("repair_applicable") is not True
                or (
                    isinstance(item.get("repaired_sha256"), str)
                    and len(item["repaired_sha256"]) == 64
                )
            )
            for item in mutations
        )
        checks = {
            "session_present": True,
            "final_functional_acceptance": summary["final_functional_acceptance"] == 1.0,
            "final_reference_layout": (
                finite_number(summary["final_layout_fidelity"])
                and summary["final_layout_fidelity"] >= layout_minimum
            ),
            "target_count_and_identity": (
                len(targets) == 2
                and set(target_by_id) == {"static-shell", "native-react"}
            ),
            "target_builds": target_builds,
            "target_functional_acceptance": target_functional,
            "target_reference_layout": target_layout,
            "cross_target_parity_by_viewport": target_parity,
            "selected_native_proof": selected_proof_healthy,
            "deep_freerange_pretext": deep_integrations_healthy,
            "assurance_eligible": baseline.get("eligible") is True,
            "five_mutations": len(mutations) == 5,
            "two_unchanged_controls": len(controls) == 2,
            "all_expected_faults_detected": (
                len(mutations) == 5 and all(item.get("detected") is True for item in mutations)
            ),
            "all_isolated_repairs_passed": (
                len(mutations) == 5 and all(item.get("repaired") is True for item in mutations)
            ),
            "no_control_false_positives": (
                len(controls) == 2 and all(item.get("detected") is False for item in controls)
            ),
            "artifact_and_evidence_integrity": summary["value_evidence"]["evidence_complete"] is True,
            "hash_provenance": (
                isinstance(baseline.get("source_sha256"), str)
                and len(baseline["source_sha256"]) == 64
                and mutation_hashes_complete
            ),
            "cost_and_timing_accounting": (
                summary["tokens"]["repair_turn_count"] == 5
                and summary["tokens"]["repair_tokens"] > 0
                and summary["timing"]["value_trial_deterministic_overhead_ms"] > 0
                and summary["timing"]["repair_model_wall_ms"] > 0
            ),
        }
        arm_results[arm] = {"pass": all(checks.values()), "checks": checks}
    overall_checks = {
        "exact_paired_arm_set": exact_arm_set,
        "single_shared_seed": len(seeds) == 1 and None not in seeds,
        "single_shared_model": len(models) == 1 and None not in models,
        "single_shared_protocol": len(protocols) == 1 and None not in protocols,
        "core_pass": arm_results["viewspec-core"]["pass"],
        "deep_pass": arm_results["viewspec-deep"]["pass"],
    }
    return {
        "pass": all(overall_checks.values()),
        "scope": "one_seed_product_regression_exit",
        "checks": overall_checks,
        "arms": arm_results,
    }


def summarize_agent_eval_study(
    sessions: Iterable[Any],
    *,
    success_criteria: Mapping[str, float],
    minimum_sessions_per_arm: int = 18,
    evaluation_mode: str | None = None,
    primary_arm: str = "viewspec-deep",
) -> dict[str, Any]:
    mode = evaluation_mode or (
        "value_premium" if "maximum_total_token_premium" in success_criteria else "efficiency"
    )
    if mode not in AGENT_UI_EVAL_MODES:
        raise ValueError(f"agent eval evaluation_mode must be one of {list(AGENT_UI_EVAL_MODES)}")
    if primary_arm not in AGENT_UI_EVAL_ARMS or primary_arm == "code-first":
        raise ValueError("agent eval primary_arm must be a ViewSpec arm")
    session_payloads = list(sessions)
    summaries = [summarize_agent_eval_session(item) for item in session_payloads]
    by_arm: dict[str, list[dict[str, Any]]] = {arm: [] for arm in AGENT_UI_EVAL_ARMS}
    for summary in summaries:
        by_arm[summary["arm_id"]].append(summary)
    arms: dict[str, dict[str, Any]] = {}
    for arm, items in by_arm.items():
        proof_rates = [
            item["native_proof"]["passed_turn_count"] / item["native_proof"]["proof_turn_count"]
            for item in items
            if item["native_proof"]["proof_turn_count"]
        ]
        selected_proof_rates = [
            item["selected_native_proof"]["passed_turn_count"]
            / item["selected_native_proof"]["proof_turn_count"]
            for item in items
            if item["selected_native_proof"]["proof_turn_count"]
        ]
        mutation_detection_rates = [
            value
            for item in items
            if (value := item["value_evidence"]["mutation"]["detection_rate"]) is not None
        ]
        mutation_repair_rates = [
            value
            for item in items
            if (value := item["value_evidence"]["mutation"]["repair_rate"]) is not None
        ]
        mutation_false_positive_rates = [
            value
            for item in items
            if (value := item["value_evidence"]["mutation"]["false_positive_rate"]) is not None
        ]
        cross_target_pass_rates = [
            value
            for item in items
            if (value := item["value_evidence"]["cross_target"]["pass_rate"]) is not None
        ]
        cross_target_evidence_complete = bool(items) and all(
            item["value_evidence"]["cross_target"]["evidence_complete"]
            for item in items
        )
        cross_target_parities = (
            [
                item["value_evidence"]["cross_target"]["minimum_parity"]
                for item in items
            ]
            if cross_target_evidence_complete
            else []
        )
        proof_passed = sum(item["native_proof"]["passed_turn_count"] for item in items)
        proof_total = sum(item["native_proof"]["proof_turn_count"] for item in items)
        selected_proof_passed = sum(
            item["selected_native_proof"]["passed_turn_count"] for item in items
        )
        selected_proof_total = sum(
            item["selected_native_proof"]["proof_turn_count"] for item in items
        )
        intermediate_proof_failed = sum(
            item["intermediate_native_proof_activity"]["failed_turn_count"]
            for item in items
        )
        intermediate_proof_total = sum(
            item["intermediate_native_proof_activity"]["proof_turn_count"]
            for item in items
        )
        mutation_applicable = sum(
            item["value_evidence"]["mutation"]["applicable_count"] for item in items
        )
        mutation_detected = sum(
            item["value_evidence"]["mutation"]["detected_count"] for item in items
        )
        mutation_repaired = sum(
            item["value_evidence"]["mutation"]["repaired_count"] for item in items
        )
        mutation_repair_applicable = sum(
            item["value_evidence"]["mutation"]["repair_applicable_count"] for item in items
        )
        negative_controls = sum(
            item["value_evidence"]["mutation"]["applicable_negative_control_count"]
            for item in items
        )
        false_positives = sum(
            item["value_evidence"]["mutation"]["false_positive_count"] for item in items
        )
        target_applicable = sum(
            item["value_evidence"]["cross_target"]["applicable_count"] for item in items
        )
        target_passed = sum(
            item["value_evidence"]["cross_target"]["passed_count"] for item in items
        )
        arms[arm] = {
            "session_count": len(items),
            "median_final_acceptance": _median(item["final_acceptance"] for item in items),
            "median_final_functional_acceptance": _median(
                item["final_functional_acceptance"] for item in items
            ),
            "median_final_layout_fidelity": _median(
                item["final_layout_fidelity"] for item in items
            ),
            "median_total_tokens": _median(item["tokens"]["total_tokens"] for item in items),
            "median_iteration_tokens": _median(item["tokens"]["iteration_tokens"] for item in items),
            "median_evolution_tokens": _median(item["tokens"]["evolution_tokens"] for item in items),
            "median_total_wall_ms": _median(item["timing"]["total_wall_ms"] for item in items),
            "median_iteration_wall_ms": _median(
                item["timing"]["iteration_total_wall_ms"] for item in items
            ),
            "median_evolution_wall_ms": _median(
                item["timing"]["evolution_total_wall_ms"] for item in items
            ),
            "median_regressions": _median(item["regression_count"] for item in items),
            "median_unresolved_regressions": _median(
                item["durability"]["unresolved_regression_count"] for item in items
            ),
            "median_regression_repair_turns": _median(
                item["durability"]["median_repair_turns"]
                for item in items
                if item["durability"]["median_repair_turns"] is not None
            ),
            "median_proof_overhead_ratio": _median(item["timing"]["proof_overhead_ratio"] for item in items),
            "median_native_proof_pass_rate": _median(proof_rates),
            "pooled_native_proof_pass_rate": proof_passed / proof_total if proof_total else None,
            "median_selected_native_proof_pass_rate": _median(selected_proof_rates),
            "pooled_selected_native_proof_pass_rate": (
                selected_proof_passed / selected_proof_total
                if selected_proof_total
                else None
            ),
            "pooled_intermediate_native_proof_detection_rate": (
                intermediate_proof_failed / intermediate_proof_total
                if intermediate_proof_total
                else None
            ),
            "median_mutation_detection_rate": _median(mutation_detection_rates),
            "median_mutation_repair_rate": _median(mutation_repair_rates),
            "median_mutation_false_positive_rate": _median(mutation_false_positive_rates),
            "pooled_mutation_detection_rate": (
                mutation_detected / mutation_applicable if mutation_applicable else None
            ),
            "pooled_mutation_repair_rate": (
                mutation_repaired / mutation_repair_applicable
                if mutation_repair_applicable
                else None
            ),
            "evidence_complete": all(
                item["value_evidence"]["evidence_complete"] for item in items
            ) if items else False,
            "structural_evidence_complete": all(
                item["value_evidence"]["structural_evidence_complete"] for item in items
            ) if items else False,
            "mutation_evidence_complete": all(
                item["value_evidence"]["mutation"]["evidence_complete"] for item in items
            ) if items else False,
            "cross_target_evidence_complete": cross_target_evidence_complete,
            "pooled_mutation_false_positive_rate": (
                false_positives / negative_controls if negative_controls else None
            ),
            "minimum_mutation_trials_per_session": (
                min(
                    item["value_evidence"]["mutation"]["applicable_count"]
                    for item in items
                )
                if items
                else None
            ),
            "minimum_negative_control_trials_per_session": (
                min(
                    item["value_evidence"]["mutation"]["applicable_negative_control_count"]
                    for item in items
                )
                if items
                else None
            ),
            "median_cross_target_pass_rate": _median(cross_target_pass_rates),
            "median_cross_target_minimum_parity": _median(cross_target_parities),
            "pooled_cross_target_pass_rate": (
                target_passed / target_applicable if target_applicable else None
            ),
            "minimum_cross_target_parity": (
                min(cross_target_parities) if cross_target_parities else None
            ),
            "minimum_target_trials_per_session": (
                min(
                    item["value_evidence"]["cross_target"]["complete_count"]
                    for item in items
                )
                if items
                else None
            ),
            "median_command_count": _median(item["tool_activity"]["command_count"] for item in items),
            "median_command_output_bytes": _median(
                item["tool_activity"]["command_output_bytes"] for item in items
            ),
            "median_source_added_lines": _median(item["source_activity"]["added_lines"] for item in items),
        }
    baseline = arms["code-first"]
    comparisons: dict[str, Any] = {}
    for arm in ("viewspec-core", "viewspec-deep"):
        candidate = arms[arm]
        comparisons[arm] = {
            "acceptance_delta": (
                candidate["median_final_acceptance"] - baseline["median_final_acceptance"]
                if candidate["median_final_acceptance"] is not None
                and baseline["median_final_acceptance"] is not None
                else None
            ),
            "token_reduction": _reduction(baseline["median_total_tokens"], candidate["median_total_tokens"]),
            "iteration_token_reduction": _reduction(
                baseline["median_iteration_tokens"], candidate["median_iteration_tokens"]
            ),
            "evolution_token_reduction": _reduction(
                baseline["median_evolution_tokens"], candidate["median_evolution_tokens"]
            ),
            "iteration_speedup": _reduction(baseline["median_total_wall_ms"], candidate["median_total_wall_ms"]),
            "regression_reduction": _reduction(baseline["median_regressions"], candidate["median_regressions"]),
            "total_token_premium": _premium(
                baseline["median_total_tokens"], candidate["median_total_tokens"]
            ),
            "evolution_token_premium": _premium(
                baseline["median_evolution_tokens"], candidate["median_evolution_tokens"]
            ),
            "evolution_wall_time_premium": _premium(
                baseline["median_evolution_wall_ms"], candidate["median_evolution_wall_ms"]
            ),
        }
        comparisons[arm]["iteration_speedup"] = _reduction(
            baseline["median_iteration_wall_ms"], candidate["median_iteration_wall_ms"]
        )
        comparisons[arm]["functional_acceptance_delta"] = (
            candidate["median_final_functional_acceptance"] - baseline["median_final_functional_acceptance"]
            if candidate["median_final_functional_acceptance"] is not None
            and baseline["median_final_functional_acceptance"] is not None
            else None
        )
        comparisons[arm]["layout_fidelity_delta"] = (
            candidate["median_final_layout_fidelity"] - baseline["median_final_layout_fidelity"]
            if candidate["median_final_layout_fidelity"] is not None
            and baseline["median_final_layout_fidelity"] is not None
            else None
        )
    enough = all(arms[arm]["session_count"] > 0 for arm in AGENT_UI_EVAL_ARMS)
    full_sample = all(
        arms[arm]["session_count"] >= minimum_sessions_per_arm for arm in AGENT_UI_EVAL_ARMS
    )
    primary = comparisons[primary_arm]
    primary_summary = arms[primary_arm]
    provenance: list[tuple[str, str, bool, str]] = []
    provenance_complete = True
    for summary in summaries:
        environment = summary.get("environment")
        controls = environment.get("controls") if isinstance(environment, Mapping) else None
        versions = environment.get("versions") if isinstance(environment, Mapping) else None
        inputs = environment.get("inputs") if isinstance(environment, Mapping) else None
        protocol_input = inputs.get("protocol") if isinstance(inputs, Mapping) else None
        model = summary.get("model")
        codex_version = versions.get("codex") if isinstance(versions, Mapping) else None
        ignored = controls.get("ignore_user_config") if isinstance(controls, Mapping) else None
        protocol_hash = protocol_input.get("sha256") if isinstance(protocol_input, Mapping) else None
        if (
            not isinstance(model, str)
            or not model
            or not isinstance(codex_version, str)
            or not isinstance(ignored, bool)
            or not isinstance(protocol_hash, str)
            or len(protocol_hash) != 64
        ):
            provenance_complete = False
            continue
        provenance.append((model, codex_version, ignored, protocol_hash))
    provenance_consistent = provenance_complete and len(set(provenance)) == 1
    if mode == "efficiency":
        gate_values: dict[str, bool | None] = {
            "provenance_complete": provenance_complete,
            "provenance_consistent": provenance_consistent,
            "acceptance_not_lower": primary["acceptance_delta"] is not None
            and primary["acceptance_delta"] >= 0,
            "total_tokens": primary["token_reduction"] is not None
            and primary["token_reduction"] >= success_criteria["minimum_token_reduction"],
            "iteration_tokens": primary["iteration_token_reduction"] is not None
            and primary["iteration_token_reduction"]
            >= success_criteria["minimum_iteration_token_reduction"],
            "iteration_speed": primary["iteration_speedup"] is not None
            and primary["iteration_speedup"] >= success_criteria["minimum_iteration_speedup"],
            "regressions": primary["regression_reduction"] is not None
            and primary["regression_reduction"] >= success_criteria["minimum_regression_reduction"],
            "proof_overhead": primary_summary["median_native_proof_pass_rate"] == 1.0
            and primary_summary["median_proof_overhead_ratio"] is not None
            and primary_summary["median_proof_overhead_ratio"]
            <= success_criteria["maximum_proof_overhead"],
            "deep_mutation_detection": None,
        }
        note = (
            "Mutation detection was not run in this pilot. A pilot can validate instrumentation and expose "
            "product failures but cannot support population-level product claims."
        )
    else:
        mutation_detection = primary_summary["pooled_mutation_detection_rate"]
        mutation_repair = primary_summary["pooled_mutation_repair_rate"]
        mutation_false_positive = primary_summary["pooled_mutation_false_positive_rate"]
        cross_target_pass = primary_summary["pooled_cross_target_pass_rate"]
        cross_target_parity = primary_summary["minimum_cross_target_parity"]
        gate_values = {
            "provenance_complete": provenance_complete,
            "provenance_consistent": provenance_consistent,
            "functional_quality": (
                primary_summary["median_final_functional_acceptance"] is not None
                and primary_summary["median_final_functional_acceptance"]
                >= success_criteria["minimum_functional_acceptance"]
                and primary["functional_acceptance_delta"] is not None
                and primary["functional_acceptance_delta"]
                >= -success_criteria["maximum_functional_acceptance_loss"]
            ),
            "visual_quality": (
                primary_summary["median_final_layout_fidelity"] is not None
                and primary_summary["median_final_layout_fidelity"]
                >= success_criteria["minimum_layout_fidelity"]
                and primary["layout_fidelity_delta"] is not None
                and primary["layout_fidelity_delta"]
                >= -success_criteria["maximum_layout_fidelity_loss"]
            ),
            "regression_prevention": (
                primary["regression_reduction"] is not None
                and primary["regression_reduction"]
                >= success_criteria["minimum_regression_reduction"]
            ),
            "native_proof_health": (
                primary_summary["pooled_selected_native_proof_pass_rate"] is not None
                and primary_summary["pooled_selected_native_proof_pass_rate"]
                >= success_criteria["minimum_deep_proof_pass_rate"]
            ),
            "assurance_evidence_complete": (
                primary_summary["mutation_evidence_complete"]
                and
                primary_summary["minimum_mutation_trials_per_session"] is not None
                and primary_summary["minimum_mutation_trials_per_session"]
                >= success_criteria["minimum_mutation_trials_per_session"]
                and primary_summary["minimum_negative_control_trials_per_session"] is not None
                and primary_summary["minimum_negative_control_trials_per_session"]
                >= success_criteria["minimum_negative_control_trials_per_session"]
            ),
            "mutation_detection": (
                None
                if mutation_detection is None
                else mutation_detection >= success_criteria["minimum_mutation_detection_rate"]
            ),
            "mutation_repair": (
                None
                if mutation_repair is None
                else mutation_repair >= success_criteria["minimum_mutation_repair_rate"]
            ),
            "mutation_false_positives": (
                None
                if mutation_false_positive is None
                else mutation_false_positive
                <= success_criteria["maximum_mutation_false_positive_rate"]
            ),
            "cross_target_pass": (
                None
                if cross_target_pass is None
                else cross_target_pass >= success_criteria["minimum_cross_target_pass_rate"]
            ),
            "cross_target_parity": (
                None
                if cross_target_parity is None
                else cross_target_parity >= success_criteria["minimum_cross_target_parity"]
            ),
            "cross_target_evidence_complete": (
                primary_summary["cross_target_evidence_complete"]
                and primary_summary["minimum_target_trials_per_session"] is not None
                and primary_summary["minimum_target_trials_per_session"]
                >= success_criteria["minimum_target_trials_per_session"]
            ),
            "total_token_premium": (
                primary["total_token_premium"] is not None
                and primary["total_token_premium"]
                <= success_criteria["maximum_total_token_premium"]
            ),
            "evolution_token_premium": (
                primary["evolution_token_premium"] is not None
                and primary["evolution_token_premium"]
                <= success_criteria["maximum_evolution_token_premium"]
            ),
            "evolution_wall_time_premium": (
                primary["evolution_wall_time_premium"] is not None
                and primary["evolution_wall_time_premium"]
                <= success_criteria["maximum_evolution_wall_time_premium"]
            ),
            "proof_overhead": (
                primary_summary["median_proof_overhead_ratio"] is not None
                and primary_summary["median_proof_overhead_ratio"]
                <= success_criteria["maximum_proof_overhead"]
            ),
        }
        note = (
            "Value-premium studies require quality parity, assurance evidence, cross-target evidence, and bounded "
            "cost premiums. Missing mutation or target trials remain unevaluated and prevent a pass."
        )
    result = {
        "schema_version": AGENT_UI_EVAL_SCHEMA_VERSION,
        "evaluation_mode": mode,
        "primary_arm": primary_arm,
        "session_count": len(summaries),
        "arms": arms,
        "comparisons": comparisons,
        "gates": {
            "status": "full_study" if full_sample else ("pilot_only" if enough else "inconclusive"),
            "sample_size_met": full_sample,
            "minimum_sessions_per_arm": minimum_sessions_per_arm,
            "pass": full_sample and all(value is True for value in gate_values.values()),
            "results": gate_values,
            "note": note,
        },
        "sessions": summaries,
    }
    if mode == "value_premium":
        result["shakedown_exit"] = summarize_agent_eval_shakedown_exit(
            session_payloads,
            success_criteria=success_criteria,
        )
    return result


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


__all__ = [
    "AGENT_UI_EVAL_ARMS",
    "AGENT_UI_EVAL_MODES",
    "AGENT_UI_EVAL_PROTOCOL_SCHEMA_VERSIONS",
    "AGENT_UI_EVAL_SCHEMA_VERSION",
    "AgentEvalProtocol",
    "AgentEvalStep",
    "AgentEvalTask",
    "canonical_json",
    "load_agent_eval_protocol",
    "parse_codex_jsonl",
    "summarize_agent_eval_shakedown_exit",
    "summarize_agent_eval_session",
    "summarize_agent_eval_study",
]
