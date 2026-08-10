"""Deterministic value-trial contracts for the V2 agent UI evaluation."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import re
from typing import Any, Mapping


VALUE_TRIAL_SCHEMA_VERSION = 1
TRIAL_CLASSES = (
    "action-state-transition",
    "numeric-invariant",
    "visibility-contract",
    "resource-binding",
    "text-layout",
)
STABLE_HOOKS = (
    "action-show-guide",
    "action-record-review",
    "action-pause-intake",
    "panel-escalation-guide",
    "panel-review-count",
    "panel-intake-paused",
    "job-j205-title",
    "job-j207",
)
VIEWSPEC_IDENTITIES = {
    "action-show-guide": ("action", "show_escalation_guide"),
    "action-record-review": ("action", "record_review"),
    "action-pause-intake": ("action", "pause_intake"),
    "panel-escalation-guide": ("visibility", "show_escalation_panel"),
    "panel-review-count": ("visibility", "show_review_count"),
    "panel-intake-paused": ("visibility", "show_intake_paused"),
    "job-j205-title": ("record", "J-205"),
    "job-j207": ("record", "J-207"),
}


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def seeded_trial_order(seed: int, trial_ids: list[str]) -> list[str]:
    """Return the preregistered deterministic order without touching global RNG."""
    result = list(trial_ids)
    random.Random(f"viewspec-value-trials:{seed}").shuffle(result)
    return result


def seeded_arm_order(seed: int, arms: tuple[str, ...]) -> list[str]:
    result = list(arms)
    random.Random(f"viewspec-pair-arms:{seed}").shuffle(result)
    return result


def load_mutation_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_mutation_manifest(payload)
    return payload


def validate_mutation_manifest(payload: Any) -> None:
    if not isinstance(payload, Mapping):
        raise ValueError("mutation manifest must be an object")
    if payload.get("schema_version") != VALUE_TRIAL_SCHEMA_VERSION:
        raise ValueError("unsupported mutation manifest schema_version")
    if payload.get("task_id") != "field-dispatch-lifecycle":
        raise ValueError("mutation manifest task_id is invalid")
    hooks = payload.get("stable_hooks")
    if not isinstance(hooks, list) or tuple(hooks) != STABLE_HOOKS:
        raise ValueError("mutation manifest stable_hooks must match the V2 contract")
    identity_contract = payload.get("identity_contract")
    expected_contract = {
        "code-first": {hook: f'data-eval-id="{hook}"' for hook in STABLE_HOOKS},
        "viewspec": {
            hook: {"kind": kind, "id": identity}
            for hook, (kind, identity) in VIEWSPEC_IDENTITIES.items()
        },
    }
    if identity_contract != expected_contract:
        raise ValueError("mutation manifest identity_contract does not match the V2 mapping")
    mutations = payload.get("mutations")
    controls = payload.get("controls")
    if not isinstance(mutations, list) or len(mutations) != 5:
        raise ValueError("mutation manifest must define exactly five mutations")
    if not isinstance(controls, list) or len(controls) != 2:
        raise ValueError("mutation manifest must define exactly two controls")
    ids: list[str] = []
    classes: list[str] = []
    for index, trial in enumerate([*mutations, *controls]):
        if not isinstance(trial, Mapping):
            raise ValueError(f"mutation manifest trial {index} must be an object")
        trial_id = trial.get("id")
        if not isinstance(trial_id, str) or not trial_id:
            raise ValueError(f"mutation manifest trial {index} id is invalid")
        ids.append(trial_id)
        kind = trial.get("kind")
        expected_kind = "mutation" if index < 5 else "control"
        if kind != expected_kind:
            raise ValueError(f"mutation manifest trial {trial_id} kind must be {expected_kind}")
        if kind == "mutation":
            trial_class = trial.get("class")
            if trial_class not in TRIAL_CLASSES:
                raise ValueError(f"mutation manifest trial {trial_id} class is invalid")
            if not isinstance(trial.get("observable_failure"), str) or not trial["observable_failure"]:
                raise ValueError(f"mutation manifest trial {trial_id} observable_failure is invalid")
            classes.append(trial_class)
            detectors = trial.get("expected_detectors")
            if (
                not isinstance(detectors, Mapping)
                or set(detectors) != {"code-first", "viewspec-core", "viewspec-deep"}
                or not all(
                    isinstance(value, list)
                    and value
                    and all(isinstance(item, str) and item for item in value)
                    for value in detectors.values()
                )
            ):
                raise ValueError(f"mutation manifest trial {trial_id} detectors are incomplete")
    if len(set(ids)) != len(ids):
        raise ValueError("mutation manifest trial ids must be unique")
    if tuple(classes) != TRIAL_CLASSES:
        raise ValueError("mutation manifest must cover each mutation class once in order")
    claimed = payload.get("manifest_sha256")
    if not isinstance(claimed, str) or len(claimed) != 64:
        raise ValueError("mutation manifest manifest_sha256 is invalid")
    unsigned = dict(payload)
    unsigned.pop("manifest_sha256", None)
    if claimed != sha256_bytes(canonical_bytes(unsigned)):
        raise ValueError("mutation manifest hash does not match its contents")


def validate_stable_hooks(arm: str, sources: Mapping[str, str]) -> dict[str, Any]:
    if arm == "code-first":
        target_sources = {
            "static": sources.get("submission/index.html", ""),
            "react": "\n".join(
                text
                for path, text in sources.items()
                if path.startswith("submission/react/src/")
            ),
        }
        target_counts = {
            target: {
                hook: len(
                    re.findall(
                        rf"""data-eval-id\s*=\s*["']{re.escape(hook)}["']""",
                        text,
                    )
                )
                for hook in STABLE_HOOKS
            }
            for target, text in target_sources.items()
        }
        errors = [
            f'{target}:{hook} expected data-eval-id="{hook}" exactly once; found {count}'
            for target, counts in target_counts.items()
            for hook, count in counts.items()
            if count != 1
        ]
        return {
            "ok": not errors,
            "counts": target_counts,
            "expected": {
                target: {hook: f'data-eval-id="{hook}"' for hook in STABLE_HOOKS}
                for target in target_sources
            },
            "errors": errors,
        }
    else:
        raw = sources.get("viewspec.app.json")
        if raw is None:
            return {"ok": False, "counts": {}, "errors": ["viewspec.app.json is missing"]}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            return {
                "ok": False,
                "counts": {},
                "errors": [f"viewspec.app.json is invalid JSON: {exc}"],
            }
        counts = {}
        for hook, (kind, identity) in VIEWSPEC_IDENTITIES.items():
            if kind == "action":
                counts[hook] = _action_definition_count(payload, identity)
            elif kind == "visibility":
                counts[hook] = _collection_id_count(payload.get("visibility"), identity)
            else:
                counts[hook] = _record_id_count(payload, identity)
    errors = []
    for hook, count in counts.items():
        if count == 1:
            continue
        kind, identity = VIEWSPEC_IDENTITIES[hook]
        errors.append(
            f"{hook} expected {kind} id '{identity}' exactly once; found {count}. "
            "Visible labels and alternate IDs do not satisfy this identity contract."
        )
    discovered = {
        "action_ids": _action_definition_ids(payload),
        "visibility_ids": sorted(
            str(item.get("id"))
            for item in payload.get("visibility", [])
            if isinstance(item, Mapping) and isinstance(item.get("id"), str)
        ),
        "record_ids": sorted(
            str(record.get("id"))
            for resource in payload.get("resources", [])
            if isinstance(resource, Mapping)
            for record in resource.get("records", [])
            if isinstance(record, Mapping) and isinstance(record.get("id"), str)
        ),
    }
    return {
        "ok": not errors,
        "counts": counts,
        "expected": {
            hook: {"kind": kind, "id": identity}
            for hook, (kind, identity) in VIEWSPEC_IDENTITIES.items()
        },
        "discovered": discovered,
        "errors": errors,
    }


def _action_definition_ids(payload: Any) -> list[str]:
    identities: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            actions = value.get("actions")
            if isinstance(actions, list):
                identities.extend(
                    str(item.get("id"))
                    for item in actions
                    if isinstance(item, Mapping) and isinstance(item.get("id"), str)
                )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload.get("screens") if isinstance(payload, Mapping) else payload)
    return sorted(identities)


def _action_definition_count(payload: Any, identity: str) -> int:
    count = 0

    def visit(value: Any) -> None:
        nonlocal count
        if isinstance(value, Mapping):
            actions = value.get("actions")
            if isinstance(actions, list):
                count += sum(
                    isinstance(item, Mapping) and item.get("id") == identity
                    for item in actions
                )
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload.get("screens") if isinstance(payload, Mapping) else payload)
    return count


def _collection_id_count(value: Any, identity: str) -> int:
    if not isinstance(value, list):
        return 0
    return sum(isinstance(item, Mapping) and item.get("id") == identity for item in value)


def _record_id_count(payload: Any, identity: str) -> int:
    if not isinstance(payload, Mapping):
        return 0
    count = 0
    for resource in payload.get("resources", []):
        if not isinstance(resource, Mapping):
            continue
        for record in resource.get("records", []):
            if isinstance(record, Mapping) and record.get("id") == identity:
                count += 1
    return count


def apply_value_trial(
    *,
    arm: str,
    trial_id: str,
    sources: Mapping[str, str],
) -> tuple[dict[str, str], dict[str, Any]]:
    """Apply one exact operator to a healthy in-memory source snapshot."""
    result = dict(sources)
    before_hash = source_snapshot_hash(sources)
    if trial_id.startswith("control-"):
        return result, {
            "changed_file_count": 0,
            "baseline_sha256": before_hash,
            "mutated_sha256": before_hash,
            "operator": "identity",
        }
    if arm == "code-first":
        path = "submission/index.html"
        if path not in result:
            raise ValueError("code-first trial requires submission/index.html")
        marker = (
            "<!-- "
            + hashlib.sha256(f"viewspec-v2-fault:{trial_id}".encode()).hexdigest()[:16]
            + " -->"
        )
        if marker in result[path]:
            raise ValueError(f"trial {trial_id} was already applied")
        scripts = {
            "break-escalation-action": _intercept_script(
                marker, "action-show-guide"
            ),
            "corrupt-reviewed-count": _intercept_script(
                marker,
                "action-record-review",
                replacement_panel="panel-review-count",
                replacement_text="Review count: 2",
            ),
            "break-escalation-visibility": (
                f"\n{marker}\n<style>[data-eval-id=\"panel-escalation-guide\"]"
                "{display:none!important}</style>\n"
            ),
            "duplicate-j207-resource": _dom_mutation_script(
                marker,
                """const n=document.querySelector('[data-eval-id="job-j207"]');"""
                """if(n){n.after(n.cloneNode(true));}""",
            ),
            "break-j205-mobile-geometry": _dom_mutation_script(
                marker,
                """const n=document.querySelector('[data-eval-id="job-j205-title"]');"""
                """if(n){n.textContent+='__unbroken_eval_suffix_"""
                + "X" * 96
                + """';}""",
            ),
        }
        if trial_id not in scripts:
            raise ValueError(f"unknown value trial: {trial_id}")
        result[path] = _append_before_body(result[path], scripts[trial_id])
        operator = "code-first-dom-hook"
    else:
        path = "viewspec.app.json"
        if path not in result:
            raise ValueError("ViewSpec trial requires viewspec.app.json")
        payload = json.loads(result[path])
        payload = _mutate_app_bundle(payload, trial_id)
        result[path] = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        operator = "appbundle-semantic-id"
    after_hash = source_snapshot_hash(result)
    if after_hash == before_hash:
        raise ValueError(f"trial {trial_id} was a no-op")
    changed = [path for path in result if result[path] != sources.get(path)]
    if len(changed) != 1:
        raise ValueError(f"trial {trial_id} must change exactly one source file")
    return result, {
        "changed_file_count": 1,
        "changed_path": changed[0],
        "baseline_sha256": before_hash,
        "mutated_sha256": after_hash,
        "operator": operator,
    }


def _intercept_script(
    marker: str,
    hook: str,
    *,
    replacement_panel: str | None = None,
    replacement_text: str | None = None,
) -> str:
    replacement = ""
    if replacement_panel is not None and replacement_text is not None:
        replacement = (
            f"const p=document.querySelector('[data-eval-id=\"{replacement_panel}\"]');"
            f"if(p){{p.hidden=false;p.removeAttribute('hidden');p.textContent={json.dumps(replacement_text)};}}"
        )
    return _dom_mutation_script(
        marker,
        f"""const n=document.querySelector('[data-eval-id="{hook}"]');"""
        """if(n){n.addEventListener('click',e=>{e.preventDefault();"""
        f"""e.stopImmediatePropagation();{replacement}}},true);}}""",
    )


def _dom_mutation_script(marker: str, body: str) -> str:
    return (
        f"\n{marker}\n<script>document.addEventListener('DOMContentLoaded',()=>{{"
        f"{body}}});</script>\n"
    )


def _append_before_body(text: str, addition: str) -> str:
    match = re.search(r"</body\s*>", text, flags=re.IGNORECASE)
    return text[: match.start()] + addition + text[match.start() :] if match else text + addition


def _mutate_app_bundle(payload: dict[str, Any], trial_id: str) -> dict[str, Any]:
    result = deepcopy(payload)
    if trial_id == "break-escalation-action":
        mutation = _one_by_id(result.get("mutations"), "reveal_escalation_guide", "mutation")
        mutation["trigger"]["action_id"] = "broken_escalation_action"
    elif trial_id == "corrupt-reviewed-count":
        mutation = _one_by_id(result.get("mutations"), "increment_reviewed_count", "mutation")
        operations = [
            item
            for item in mutation.get("ops", [])
            if isinstance(item, dict) and item.get("op") == "increment"
        ]
        if len(operations) != 1:
            raise ValueError("increment_reviewed_count must contain exactly one increment")
        operations[0]["amount"] = 2
    elif trial_id == "break-escalation-visibility":
        visibility = _one_by_id(result.get("visibility"), "show_escalation_panel", "visibility")
        when = visibility.get("when")
        if not isinstance(when, dict) or when.get("is") != "truthy":
            raise ValueError("show_escalation_panel must use a truthy condition")
        when["is"] = "falsy"
    elif trial_id == "duplicate-j207-resource":
        records = _resource_records(result, "J-207")
        records.append(deepcopy(_resource_record(result, "J-207")))
    elif trial_id == "break-j205-mobile-geometry":
        record = _resource_record(result, "J-205")
        title = record.get("title")
        if not isinstance(title, str) or not title:
            raise ValueError("J-205 must have a non-empty title field")
        record["title"] = title + "__unbroken_eval_suffix_" + ("X" * 96)
    else:
        raise ValueError(f"unknown value trial: {trial_id}")
    return result


def _one_by_id(value: Any, identity: str, noun: str) -> dict[str, Any]:
    matches = [
        item
        for item in value if isinstance(item, dict) and item.get("id") == identity
    ] if isinstance(value, list) else []
    if len(matches) != 1:
        raise ValueError(f"{noun} {identity} count was {len(matches)}, expected 1")
    return matches[0]


def _resource_record(payload: Mapping[str, Any], identity: str) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for resource in payload.get("resources", []):
        if isinstance(resource, Mapping):
            matches.extend(
                item
                for item in resource.get("records", [])
                if isinstance(item, dict) and item.get("id") == identity
            )
    if len(matches) != 1:
        raise ValueError(f"resource record {identity} count was {len(matches)}, expected 1")
    return matches[0]


def _resource_records(payload: Mapping[str, Any], identity: str) -> list[dict[str, Any]]:
    owners: list[list[dict[str, Any]]] = []
    for resource in payload.get("resources", []):
        if not isinstance(resource, Mapping):
            continue
        records = resource.get("records")
        if isinstance(records, list) and any(
            isinstance(item, Mapping) and item.get("id") == identity
            for item in records
        ):
            owners.append(records)
    if len(owners) != 1:
        raise ValueError(f"resource record {identity} owner count was {len(owners)}, expected 1")
    return owners[0]


def source_snapshot_hash(sources: Mapping[str, str]) -> str:
    digest = hashlib.sha256()
    for path, text in sorted(sources.items()):
        digest.update(path.encode() + b"\0" + text.encode() + b"\0")
    return digest.hexdigest()


def checkpoint_envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(payload)
    body.pop("checkpoint_sha256", None)
    body["checkpoint_sha256"] = sha256_bytes(canonical_bytes(body))
    return body


def validate_checkpoint(
    payload: Any,
    *,
    protocol_sha256: str,
    model: str,
    source_sha256: str,
    product_tree_sha256: str | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("checkpoint must be an object")
    body = dict(payload)
    claimed = body.pop("checkpoint_sha256", None)
    if claimed != sha256_bytes(canonical_bytes(body)):
        raise ValueError("checkpoint hash mismatch")
    expected = {
        "protocol_sha256": protocol_sha256,
        "model": model,
        "source_sha256": source_sha256,
    }
    if product_tree_sha256 is not None:
        expected["product_tree_sha256"] = product_tree_sha256
    for key, value in expected.items():
        if body.get(key) != value:
            raise ValueError(f"checkpoint {key} mismatch")
    return dict(payload)
