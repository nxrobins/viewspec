"""Durable, approval-gated convergence sessions for ViewSpec semantic sources."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import time
from typing import Any, Callable, Mapping

from viewspec.agent import SUPPORTED_AGENT_REGION_LAYOUTS, SUPPORTED_AGENT_STYLE_TOKENS
from viewspec.intent_patch import (
    APP_BUNDLE_MAX_BYTES,
    INTENT_PATCH_CONTRACT_PROFILE,
    IntentPatch,
    IntentPatchContext,
    IntentPatchError,
    IntentPatchPreview,
    _durable_write_bytes,
    _read_bounded_utf8_file,
    _strict_json_loads,
    _validate_source_text,
    apply_intent_patch_file,
    parse_intent_patch,
    patch_context_from_repair_plan,
    preview_intent_patch,
    source_sha256,
)
from viewspec.repair import VerificationRepairPlan
from viewspec.types import PRESENT_AS_TO_PRIMITIVE
from viewspec.verification import (
    RetryLineage,
    VerificationDiagnostic,
    VerificationPlan,
    VerificationResult,
)


CONVERGE_SESSION_SCHEMA_VERSION = 1
CONVERGE_MAX_ATTEMPTS = 3
CONVERGE_MAX_SECONDS = 10 * 60
CONVERGE_MAX_STATE_BYTES = 1024 * 1024
CONVERGE_LOCK_TIMEOUT_SECONDS = 2.0
CONVERGE_SESSION_ID_RE = re.compile(r"^vcgs_[0-9a-f]{32}$")
CONVERGE_PREVIEW_ID_RE = re.compile(r"^vcpv_[0-9a-f]{32}$")
CONVERGE_TASK_ID_RE = re.compile(r"^vctask_[0-9a-f]{32}$")
CONVERGE_APPROVAL_RE = re.compile(r"^vcapprove_[0-9a-f]{64}$")
CONVERGE_CERTIFICATE_ID_RE = re.compile(r"^vcert_[0-9a-f]{32}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

CONVERGE_STATUSES = frozenset(
    {
        "awaiting_proposal",
        "awaiting_approval",
        "applied",
        "conformant",
        "stalled",
        "exhausted",
        "full_revision_required",
        "rejected",
    }
)
_ACTIVE_STATUSES = frozenset({"awaiting_proposal", "awaiting_approval"})

CONVERGENCE_TASK_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://viewspec.dev/converge-task.schema.json",
    "title": "ViewSpec Convergence Authoring Task V1",
    "description": "A source-bound menu of the only semantic operations an agent may propose in one convergence step.",
    "type": "object",
    "required": [
        "schema_version",
        "task_id",
        "source_kind",
        "base_source_sha256",
        "requests",
        "targets",
        "allowed_target_keys",
    ],
    "additionalProperties": False,
    "properties": {
        "schema_version": {"const": 1},
        "task_id": {"type": "string", "pattern": r"^vctask_[0-9a-f]{32}$"},
        "source_kind": {"enum": ["intent_bundle", "app_bundle"]},
        "base_source_sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
        "requests": {
            "type": "array",
            "minItems": 1,
            "maxItems": 63,
            "items": {"type": "object"},
        },
        "targets": {
            "type": "array",
            "maxItems": 128,
            "items": {"$ref": "#/$defs/target"},
        },
        "allowed_target_keys": {
            "type": "array",
            "maxItems": 128,
            "uniqueItems": True,
            "items": {"$ref": "#/$defs/target_key"},
        },
    },
    "$defs": {
        "target_key": {
            "type": "array",
            "minItems": 3,
            "maxItems": 6,
            "items": {"type": "string", "minLength": 1, "maxLength": 128},
        },
        "operation": {
            "type": "object",
            "required": ["op", "fixed_fields", "replacement_field", "target_key"],
            "additionalProperties": False,
            "properties": {
                "op": {"type": "string", "minLength": 1, "maxLength": 64},
                "fixed_fields": {"type": "object"},
                "replacement_field": {"type": "string", "minLength": 1, "maxLength": 64},
                "target_key": {"$ref": "#/$defs/target_key"},
                "allowed_values": {"type": "array", "maxItems": 64},
            },
        },
        "target": {
            "type": "object",
            "required": ["target_id", "kind", "screen_id", "source_fragment", "legal_operations"],
            "additionalProperties": False,
            "properties": {
                "target_id": {"type": "string", "minLength": 1, "maxLength": 128},
                "kind": {"enum": ["binding", "region", "semantic_node", "style", "visibility"]},
                "screen_id": {"type": ["string", "null"]},
                "source_fragment": {"type": "object"},
                "legal_operations": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 64,
                    "items": {"$ref": "#/$defs/operation"},
                },
            },
        },
    },
}

CandidateVerifier = Callable[[str, str, VerificationPlan, RetryLineage], VerificationResult]


class ConvergeError(ValueError):
    """Stable fail-closed error for Converge Sessions V1."""

    def __init__(self, code: str, message: str, fix: str, *, cli_exit: int = 2) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix
        self.cli_exit = cli_exit

    def to_json(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "fix": self.fix}


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConvergeError(
            "CONVERGE_STATE_INVALID",
            f"Convergence state is not finite JSON: {exc}",
            "Use only bounded finite JSON values.",
            cli_exit=1,
        ) from exc


def _identity(prefix: str, domain: str, material: object, *, length: int = 32) -> str:
    digest = hashlib.sha256(_canonical({"type": domain, "material": material})).hexdigest()
    return f"{prefix}{digest[:length]}"


@dataclass(frozen=True, slots=True, order=True)
class VerificationObligation:
    """Stable verifier failure identity that excludes mutable prose and evidence paths."""

    obligation_id: str
    code: str
    source_ref: str | None
    viewport: str | None
    severity: str

    @classmethod
    def from_diagnostic(cls, diagnostic: VerificationDiagnostic) -> VerificationObligation:
        material = {
            "code": diagnostic.code,
            "source_ref": diagnostic.source_ref,
            "viewport": diagnostic.viewport,
            "severity": diagnostic.severity,
        }
        return cls(
            obligation_id=_identity(
                "vob_",
                "viewspec_verification_obligation_v1",
                material,
            ),
            code=diagnostic.code,
            source_ref=diagnostic.source_ref,
            viewport=diagnostic.viewport,
            severity=diagnostic.severity,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "code": self.code,
            "source_ref": self.source_ref,
            "viewport": self.viewport,
            "severity": self.severity,
        }

    @classmethod
    def from_json(cls, value: object) -> VerificationObligation:
        if not isinstance(value, Mapping):
            raise ValueError("verification obligation must be an object")
        diagnostic = VerificationDiagnostic(
            code=value.get("code"),
            severity=value.get("severity"),
            message="Stored convergence obligation.",
            fix="Use the stored convergence task.",
            source_ref=value.get("source_ref"),
            viewport=value.get("viewport"),
        )
        expected = cls.from_diagnostic(diagnostic)
        if value.get("obligation_id") != expected.obligation_id:
            raise ValueError("verification obligation identity mismatch")
        return expected


def _error_obligations(result: VerificationResult) -> tuple[VerificationObligation, ...]:
    return tuple(
        sorted(
            (
                VerificationObligation.from_diagnostic(item)
                for item in result.diagnostics
                if item.severity == "error"
            ),
            key=lambda item: item.obligation_id,
        )
    )


@dataclass(frozen=True, slots=True)
class ProgressCertificate:
    """Set-wise proof that one complete verification strictly improves another."""

    certificate_id: str
    mode: str
    accepted: bool
    reason: str
    plan_sha256: str | None
    baseline_result_sha256: str | None
    candidate_result_sha256: str | None
    fixed_obligations: tuple[VerificationObligation, ...] = ()
    remaining_obligations: tuple[VerificationObligation, ...] = ()
    introduced_obligations: tuple[VerificationObligation, ...] = ()

    @classmethod
    def human_review(cls, preview: IntentPatchPreview) -> ProgressCertificate:
        material = {
            "mode": "human_review",
            "accepted": True,
            "reason": "explicit_human_approval_required",
            "preview_id": preview.preview_id,
            "candidate_source_sha256": preview.candidate_source_sha256,
        }
        return cls(
            certificate_id=_identity(
                "vcert_",
                "viewspec_progress_certificate_v1",
                material,
            ),
            mode="human_review",
            accepted=True,
            reason="explicit_human_approval_required",
            plan_sha256=None,
            baseline_result_sha256=None,
            candidate_result_sha256=None,
        )

    @classmethod
    def compare(
        cls,
        baseline: VerificationResult,
        candidate: VerificationResult,
    ) -> ProgressCertificate:
        if not isinstance(baseline, VerificationResult) or not isinstance(candidate, VerificationResult):
            raise TypeError("progress comparison requires VerificationResult values")
        baseline_items = _error_obligations(baseline)
        candidate_items = _error_obligations(candidate)
        baseline_by_id = {item.obligation_id: item for item in baseline_items}
        candidate_by_id = {item.obligation_id: item for item in candidate_items}
        fixed_ids = set(baseline_by_id) - set(candidate_by_id)
        remaining_ids = set(baseline_by_id) & set(candidate_by_id)
        introduced_ids = set(candidate_by_id) - set(baseline_by_id)

        if baseline.plan.plan_sha256 != candidate.plan.plan_sha256:
            accepted = False
            reason = "verification_plan_changed"
        elif not candidate.complete or candidate.status == "indeterminate":
            accepted = False
            reason = "candidate_indeterminate"
        elif introduced_ids:
            accepted = False
            reason = "introduced_error"
        elif not fixed_ids:
            accepted = False
            reason = "no_strict_progress"
        else:
            accepted = True
            reason = "strict_progress"
        material = {
            "mode": "verification",
            "accepted": accepted,
            "reason": reason,
            "plan_sha256": baseline.plan.plan_sha256,
            "baseline_result_sha256": baseline.result_sha256,
            "candidate_result_sha256": candidate.result_sha256,
            "fixed": sorted(fixed_ids),
            "remaining": sorted(remaining_ids),
            "introduced": sorted(introduced_ids),
        }
        return cls(
            certificate_id=_identity(
                "vcert_",
                "viewspec_progress_certificate_v1",
                material,
            ),
            mode="verification",
            accepted=accepted,
            reason=reason,
            plan_sha256=baseline.plan.plan_sha256,
            baseline_result_sha256=baseline.result_sha256,
            candidate_result_sha256=candidate.result_sha256,
            fixed_obligations=tuple(baseline_by_id[item] for item in sorted(fixed_ids)),
            remaining_obligations=tuple(baseline_by_id[item] for item in sorted(remaining_ids)),
            introduced_obligations=tuple(candidate_by_id[item] for item in sorted(introduced_ids)),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "certificate_id": self.certificate_id,
            "mode": self.mode,
            "accepted": self.accepted,
            "reason": self.reason,
            "plan_sha256": self.plan_sha256,
            "baseline_result_sha256": self.baseline_result_sha256,
            "candidate_result_sha256": self.candidate_result_sha256,
            "fixed_obligations": [item.to_json() for item in self.fixed_obligations],
            "remaining_obligations": [item.to_json() for item in self.remaining_obligations],
            "introduced_obligations": [item.to_json() for item in self.introduced_obligations],
        }

    @classmethod
    def from_json(cls, value: object) -> ProgressCertificate:
        if not isinstance(value, Mapping) or value.get("schema_version") != 1:
            raise ValueError("progress certificate is invalid")
        accepted = value.get("accepted")
        if type(accepted) is not bool:
            raise ValueError("progress certificate accepted must be boolean")
        certificate = cls(
            certificate_id=str(value.get("certificate_id")),
            mode=str(value.get("mode")),
            accepted=accepted,
            reason=str(value.get("reason")),
            plan_sha256=value.get("plan_sha256"),
            baseline_result_sha256=value.get("baseline_result_sha256"),
            candidate_result_sha256=value.get("candidate_result_sha256"),
            fixed_obligations=tuple(
                VerificationObligation.from_json(item)
                for item in value.get("fixed_obligations", [])
            ),
            remaining_obligations=tuple(
                VerificationObligation.from_json(item)
                for item in value.get("remaining_obligations", [])
            ),
            introduced_obligations=tuple(
                VerificationObligation.from_json(item)
                for item in value.get("introduced_obligations", [])
            ),
        )
        if CONVERGE_CERTIFICATE_ID_RE.fullmatch(certificate.certificate_id) is None:
            raise ValueError("progress certificate id is invalid")
        if certificate.mode not in {"human_review", "verification"}:
            raise ValueError("progress certificate mode is invalid")
        if certificate.mode == "verification":
            material = {
                "mode": certificate.mode,
                "accepted": certificate.accepted,
                "reason": certificate.reason,
                "plan_sha256": certificate.plan_sha256,
                "baseline_result_sha256": certificate.baseline_result_sha256,
                "candidate_result_sha256": certificate.candidate_result_sha256,
                "fixed": sorted(item.obligation_id for item in certificate.fixed_obligations),
                "remaining": sorted(item.obligation_id for item in certificate.remaining_obligations),
                "introduced": sorted(item.obligation_id for item in certificate.introduced_obligations),
            }
            expected = _identity("vcert_", "viewspec_progress_certificate_v1", material)
            if certificate.certificate_id != expected:
                raise ValueError("progress certificate identity mismatch")
        return certificate


@dataclass(frozen=True, slots=True)
class ConvergenceTarget:
    target_id: str
    kind: str
    screen_id: str | None
    source_fragment: dict[str, Any]
    legal_operations: tuple[dict[str, Any], ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "kind": self.kind,
            "screen_id": self.screen_id,
            "source_fragment": json.loads(_canonical(self.source_fragment)),
            "legal_operations": [json.loads(_canonical(item)) for item in self.legal_operations],
        }

    @classmethod
    def from_json(cls, value: object) -> ConvergenceTarget:
        if not isinstance(value, Mapping):
            raise ValueError("convergence target must be an object")
        fragment = value.get("source_fragment")
        operations = value.get("legal_operations")
        if not isinstance(fragment, dict) or not isinstance(operations, list):
            raise ValueError("convergence target payload is invalid")
        return cls(
            target_id=str(value.get("target_id")),
            kind=str(value.get("kind")),
            screen_id=value.get("screen_id"),
            source_fragment=json.loads(_canonical(fragment)),
            legal_operations=tuple(json.loads(_canonical(item)) for item in operations),
        )


@dataclass(frozen=True, slots=True)
class ConvergenceAuthoringTask:
    task_id: str
    source_kind: str
    base_source_sha256: str
    requests: tuple[dict[str, Any], ...]
    targets: tuple[ConvergenceTarget, ...]
    allowed_target_keys: tuple[tuple[str, ...], ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "task_id": self.task_id,
            "source_kind": self.source_kind,
            "base_source_sha256": self.base_source_sha256,
            "requests": [json.loads(_canonical(item)) for item in self.requests],
            "targets": [item.to_json() for item in self.targets],
            "allowed_target_keys": [list(item) for item in self.allowed_target_keys],
        }

    @classmethod
    def from_json(cls, value: object) -> ConvergenceAuthoringTask:
        if not isinstance(value, Mapping) or value.get("schema_version") != 1:
            raise ValueError("convergence task is invalid")
        task = cls(
            task_id=str(value.get("task_id")),
            source_kind=str(value.get("source_kind")),
            base_source_sha256=str(value.get("base_source_sha256")),
            requests=tuple(json.loads(_canonical(item)) for item in value.get("requests", [])),
            targets=tuple(ConvergenceTarget.from_json(item) for item in value.get("targets", [])),
            allowed_target_keys=tuple(tuple(str(part) for part in item) for item in value.get("allowed_target_keys", [])),
        )
        material = {
            "source_kind": task.source_kind,
            "base_source_sha256": task.base_source_sha256,
            "requests": list(task.requests),
            "targets": [item.to_json() for item in task.targets],
            "allowed_target_keys": [list(item) for item in task.allowed_target_keys],
        }
        if (
            CONVERGE_TASK_ID_RE.fullmatch(task.task_id) is None
            or task.task_id != _identity("vctask_", "viewspec_convergence_authoring_task_v1", material)
        ):
            raise ValueError("convergence task identity mismatch")
        if task.source_kind not in {"intent_bundle", "app_bundle"} or SHA256_RE.fullmatch(task.base_source_sha256) is None:
            raise ValueError("convergence task source identity is invalid")
        target_keys = {
            tuple(operation["target_key"])
            for target in task.targets
            for operation in target.legal_operations
        }
        if tuple(sorted(target_keys)) != task.allowed_target_keys:
            raise ValueError("convergence task target-key index mismatch")
        return task


def _fixed_fields(screen_id: str | None, values: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(values)
    if screen_id is not None:
        result = {"screen_id": screen_id, **result}
    return result


def _legal_operation(
    op: str,
    fixed_fields: Mapping[str, Any],
    replacement_field: str,
    target_key: tuple[str, ...],
    *,
    allowed_values: tuple[Any, ...] | None = None,
) -> dict[str, Any]:
    return {
        "op": op,
        "fixed_fields": json.loads(_canonical(fixed_fields)),
        "replacement_field": replacement_field,
        "target_key": list(target_key),
        **({"allowed_values": list(allowed_values)} if allowed_values is not None else {}),
    }


def _intent_for_screen(payload: dict[str, Any], source_kind: str, screen_id: str | None) -> dict[str, Any] | None:
    if source_kind == "intent_bundle":
        return payload
    screens = payload.get("screens")
    if not isinstance(screens, list) or not isinstance(screen_id, str):
        return None
    matches = [item for item in screens if isinstance(item, dict) and item.get("id") == screen_id]
    if len(matches) != 1 or not isinstance(matches[0].get("intent_bundle"), dict):
        return None
    return matches[0]["intent_bundle"]


def _item_by_id(collection: object, target_id: str) -> dict[str, Any] | None:
    if not isinstance(collection, list):
        return None
    matches = [item for item in collection if isinstance(item, dict) and item.get("id") == target_id]
    return matches[0] if len(matches) == 1 else None


def _source_candidates(request: Mapping[str, Any]) -> dict[str, set[str]]:
    candidates: dict[str, set[str]] = {
        "binding": set(),
        "region": set(),
        "style": set(),
        "node": set(),
        "visibility": set(),
    }
    binding_id = request.get("binding_id")
    if isinstance(binding_id, str):
        candidates["binding"].add(binding_id)
    for ref in request.get("intent_refs", []):
        if not isinstance(ref, str) or not ref.startswith("viewspec:"):
            continue
        parts = ref.split(":", 2)
        if len(parts) == 3 and parts[1] in candidates:
            candidates[parts[1]].add(parts[2])
    for ref in request.get("content_refs", []):
        if isinstance(ref, str) and ref.startswith("node:") and "#attr:" in ref:
            candidates["node"].add(ref[5:].split("#attr:", 1)[0])
    source_ref = request.get("source_ref")
    if isinstance(source_ref, str) and "/ir:" in source_ref:
        ir_id = source_ref.rsplit("/ir:", 1)[1]
    elif isinstance(source_ref, str) and source_ref.startswith("ir:"):
        ir_id = source_ref[3:]
    else:
        ir_id = None
    if ir_id:
        for kind, prefix in (("binding", "binding_"), ("region", "region_"), ("style", "style_")):
            if ir_id.startswith(prefix):
                candidates[kind].add(ir_id[len(prefix) :])
        candidates["binding"].add(ir_id)
        candidates["region"].add(ir_id)
        candidates["style"].add(ir_id)
        candidates["node"].add(ir_id)
    return candidates


def _build_authoring_task(
    payload: dict[str, Any],
    context: IntentPatchContext,
) -> ConvergenceAuthoringTask:
    target_map: dict[tuple[str | None, str, str], ConvergenceTarget] = {}
    allowed_keys: set[tuple[str, ...]] = set()

    def add_target(
        *,
        screen_id: str | None,
        kind: str,
        target_id: str,
        fragment: dict[str, Any],
        operations: list[dict[str, Any]],
    ) -> None:
        if not operations:
            return
        key = (screen_id, kind, target_id)
        existing = target_map.get(key)
        merged = list(existing.legal_operations) if existing is not None else []
        by_key = {tuple(item["target_key"]): item for item in merged}
        for operation in operations:
            by_key[tuple(operation["target_key"])] = operation
            allowed_keys.add(tuple(operation["target_key"]))
        target_map[key] = ConvergenceTarget(
            target_id=target_id,
            kind=kind,
            screen_id=screen_id,
            source_fragment=json.loads(_canonical(fragment)),
            legal_operations=tuple(by_key[item] for item in sorted(by_key)),
        )

    for request in context.requests:
        screen_id = request.get("screen_id") if context.source_kind == "app_bundle" else None
        if context.source_kind == "app_bundle" and not isinstance(screen_id, str):
            source_ref = request.get("source_ref")
            if isinstance(source_ref, str) and source_ref.startswith("screen:"):
                screen_id = source_ref[7:].split("/", 1)[0]
        intent = _intent_for_screen(payload, context.source_kind, screen_id)
        if intent is None:
            continue
        view = intent.get("view_spec") if isinstance(intent.get("view_spec"), dict) else {}
        substrate = intent.get("substrate") if isinstance(intent.get("substrate"), dict) else {}
        candidates = _source_candidates(request)
        node_attrs: dict[str, set[str]] = {}
        for content_ref in request.get("content_refs", []):
            if isinstance(content_ref, str) and content_ref.startswith("node:") and "#attr:" in content_ref:
                node_id, attr = content_ref[5:].split("#attr:", 1)
                node_attrs.setdefault(node_id, set()).add(attr)

        for binding_id in sorted(candidates["binding"]):
            binding = _item_by_id(view.get("bindings"), binding_id)
            if binding is None or not isinstance(binding.get("present_as"), str):
                continue
            scope = screen_id or "root"
            target_key = ("intent", scope, "binding_presentation", binding_id)
            add_target(
                screen_id=screen_id,
                kind="binding",
                target_id=binding_id,
                fragment=binding,
                operations=[
                    _legal_operation(
                        "set_binding_presentation",
                        _fixed_fields(
                            screen_id,
                            {"binding_id": binding_id, "old_value": binding["present_as"]},
                        ),
                        "value",
                        target_key,
                        allowed_values=tuple(sorted(PRESENT_AS_TO_PRIMITIVE)),
                    )
                ],
            )
            address = binding.get("address")
            if isinstance(address, str) and address.startswith("node:") and "#attr:" in address:
                node_id, attr = address[5:].split("#attr:", 1)
                candidates["node"].add(node_id)
                node_attrs.setdefault(node_id, set()).add(attr)

        for region_id in sorted(candidates["region"]):
            region = _item_by_id(view.get("regions"), region_id)
            if region is None:
                continue
            scope = screen_id or "root"
            operations: list[dict[str, Any]] = []
            layout = region.get("layout")
            if isinstance(layout, str):
                operations.append(
                    _legal_operation(
                        "set_region_layout",
                        _fixed_fields(screen_id, {"region_id": region_id, "old_value": layout}),
                        "value",
                        ("intent", scope, "region_layout", region_id),
                        allowed_values=tuple(SUPPORTED_AGENT_REGION_LAYOUTS),
                    )
                )
            parent = region.get("parent_region")
            if region_id != "root" and isinstance(parent, str) and parent:
                parents = tuple(
                    sorted(
                        str(item["id"])
                        for item in view.get("regions", [])
                        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"] != region_id
                    )
                )
                operations.append(
                    _legal_operation(
                        "move_region",
                        _fixed_fields(screen_id, {"region_id": region_id, "old_parent_id": parent}),
                        "parent_id",
                        ("intent", scope, "region_parent", region_id),
                        allowed_values=parents,
                    )
                )
            children = [
                str(item["id"])
                for item in view.get("regions", [])
                if isinstance(item, dict) and item.get("parent_region") == region_id and isinstance(item.get("id"), str)
            ]
            if len(children) >= 2:
                operations.append(
                    _legal_operation(
                        "reorder_region_children",
                        _fixed_fields(screen_id, {"region_id": region_id, "old_children": children}),
                        "children",
                        ("intent", scope, "region_children", region_id),
                        allowed_values=tuple(children),
                    )
                )
            add_target(
                screen_id=screen_id,
                kind="region",
                target_id=region_id,
                fragment=region,
                operations=operations,
            )

        for style_id in sorted(candidates["style"]):
            style = _item_by_id(view.get("styles"), style_id)
            if style is None or style_id == "aesthetic_profile" or not isinstance(style.get("token"), str):
                continue
            scope = screen_id or "root"
            add_target(
                screen_id=screen_id,
                kind="style",
                target_id=style_id,
                fragment=style,
                operations=[
                    _legal_operation(
                        "set_style_token",
                        _fixed_fields(screen_id, {"style_id": style_id, "old_value": style["token"]}),
                        "value",
                        ("intent", scope, "style_token", style_id),
                        allowed_values=tuple(sorted(SUPPORTED_AGENT_STYLE_TOKENS)),
                    )
                ],
            )

        nodes = substrate.get("nodes") if isinstance(substrate.get("nodes"), dict) else {}
        for node_id in sorted(candidates["node"]):
            node = nodes.get(node_id)
            attrs = node.get("attrs") if isinstance(node, dict) and isinstance(node.get("attrs"), dict) else None
            if attrs is None:
                continue
            scope = screen_id or "root"
            operations = []
            for attr, old_value in sorted(attrs.items()):
                if node_id in node_attrs and attr not in node_attrs[node_id]:
                    continue
                if old_value is None or isinstance(old_value, (str, int, float, bool)):
                    operations.append(
                        _legal_operation(
                            "replace_semantic_attr",
                            _fixed_fields(
                                screen_id,
                                {"node_id": node_id, "attr": attr, "old_value": old_value},
                            ),
                            "value",
                            ("intent", scope, "semantic_attr", node_id, attr),
                        )
                    )
            add_target(
                screen_id=screen_id,
                kind="semantic_node",
                target_id=node_id,
                fragment=node,
                operations=operations,
            )

    targets = tuple(target_map[key] for key in sorted(target_map, key=lambda item: (item[0] or "", item[1], item[2])))
    if len(targets) > 128 or len(allowed_keys) > 128:
        raise ConvergeError(
            "CONVERGE_TASK_TOO_LARGE",
            "Convergence authoring task exceeds 128 semantic targets.",
            "Split the evidence into a smaller Review batch or repair plan.",
        )
    task_material = {
        "source_kind": context.source_kind,
        "base_source_sha256": context.base_source_sha256,
        "requests": list(context.requests),
        "targets": [item.to_json() for item in targets],
        "allowed_target_keys": [list(item) for item in sorted(allowed_keys)],
    }
    task = ConvergenceAuthoringTask(
        task_id=_identity("vctask_", "viewspec_convergence_authoring_task_v1", task_material),
        source_kind=context.source_kind,
        base_source_sha256=context.base_source_sha256,
        requests=context.requests,
        targets=targets,
        allowed_target_keys=tuple(sorted(allowed_keys)),
    )
    if len(_canonical(task.to_json())) > 128 * 1024:
        raise ConvergeError(
            "CONVERGE_TASK_TOO_LARGE",
            "Convergence authoring task exceeds 128 KiB.",
            "Split the evidence into a smaller Review batch or repair plan.",
        )
    return task


def starter_convergence_task_payload() -> dict[str, Any]:
    """Return one deterministic published example of the agent's bounded proposal menu."""

    from viewspec.intent_tools import starter_intent_payload

    payload = starter_intent_payload("dashboard")
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    context = IntentPatchContext(
        origin="review_batch",
        source_kind="intent_bundle",
        base_source_sha256=source_sha256(text),
        contract_profile=INTENT_PATCH_CONTRACT_PROFILE,
        evidence_refs=("review:vrw_example:batch_example", "review_event:event_example"),
        requests=(
            {
                "request_id": "event_example",
                "kind": "change_request",
                "instruction": "Show the revenue value as a badge.",
                "screen_id": None,
                "source_ref": "ir:binding_revenue_value",
                "binding_id": "revenue_value",
                "action_id": None,
                "intent_refs": ["viewspec:binding:revenue_value"],
                "content_refs": ["node:revenue#attr:value"],
            },
        ),
    )
    return _build_authoring_task(payload, context).to_json()


@dataclass(frozen=True, slots=True)
class ConvergencePreview:
    preview_id: str
    attempt: int
    patch: IntentPatch
    intent_preview_id: str
    intent_approval_token: str
    base_source_sha256: str
    candidate_source_sha256: str
    semantic_diff: dict[str, Any]
    compile_check: dict[str, Any]
    progress_certificate: ProgressCertificate
    candidate_result: VerificationResult | None
    approval_token: str

    def to_json(self, *, include_approval_token: bool = True) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "preview_id": self.preview_id,
            "attempt": self.attempt,
            "patch": self.patch.to_json(),
            "intent_preview_id": self.intent_preview_id,
            "intent_approval_token": self.intent_approval_token,
            "base_source_sha256": self.base_source_sha256,
            "candidate_source_sha256": self.candidate_source_sha256,
            "semantic_diff": json.loads(_canonical(self.semantic_diff)),
            "compile_check": json.loads(_canonical(self.compile_check)),
            "progress_certificate": self.progress_certificate.to_json(),
            "candidate_result": self.candidate_result.to_json() if self.candidate_result else None,
            **({"approval_token": self.approval_token} if include_approval_token else {}),
        }

    @classmethod
    def from_json(cls, value: object) -> ConvergencePreview:
        if not isinstance(value, Mapping) or value.get("schema_version") != 1:
            raise ValueError("convergence preview is invalid")
        result = value.get("candidate_result")
        preview = cls(
            preview_id=str(value.get("preview_id")),
            attempt=int(value.get("attempt")),
            patch=parse_intent_patch(value.get("patch")),
            intent_preview_id=str(value.get("intent_preview_id")),
            intent_approval_token=str(value.get("intent_approval_token")),
            base_source_sha256=str(value.get("base_source_sha256")),
            candidate_source_sha256=str(value.get("candidate_source_sha256")),
            semantic_diff=json.loads(_canonical(value.get("semantic_diff"))),
            compile_check=json.loads(_canonical(value.get("compile_check"))),
            progress_certificate=ProgressCertificate.from_json(value.get("progress_certificate")),
            candidate_result=VerificationResult.from_json(result) if isinstance(result, Mapping) else None,
            approval_token=str(value.get("approval_token")),
        )
        if CONVERGE_PREVIEW_ID_RE.fullmatch(preview.preview_id) is None:
            raise ValueError("convergence preview id is invalid")
        if CONVERGE_APPROVAL_RE.fullmatch(preview.approval_token) is None:
            raise ValueError("convergence approval token is invalid")
        return preview


@dataclass(frozen=True, slots=True)
class ConvergenceAttempt:
    attempt: int
    base_source_sha256: str
    candidate_source_sha256: str
    patch: IntentPatch
    preview_id: str
    progress_certificate: ProgressCertificate
    candidate_result: VerificationResult | None
    receipt: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "base_source_sha256": self.base_source_sha256,
            "candidate_source_sha256": self.candidate_source_sha256,
            "patch": self.patch.to_json(),
            "preview_id": self.preview_id,
            "progress_certificate": self.progress_certificate.to_json(),
            "candidate_result": self.candidate_result.to_json() if self.candidate_result else None,
            "receipt": json.loads(_canonical(self.receipt)) if self.receipt else None,
        }

    @classmethod
    def from_json(cls, value: object) -> ConvergenceAttempt:
        if not isinstance(value, Mapping):
            raise ValueError("convergence attempt is invalid")
        result = value.get("candidate_result")
        receipt = value.get("receipt")
        return cls(
            attempt=int(value.get("attempt")),
            base_source_sha256=str(value.get("base_source_sha256")),
            candidate_source_sha256=str(value.get("candidate_source_sha256")),
            patch=parse_intent_patch(value.get("patch")),
            preview_id=str(value.get("preview_id")),
            progress_certificate=ProgressCertificate.from_json(value.get("progress_certificate")),
            candidate_result=VerificationResult.from_json(result) if isinstance(result, Mapping) else None,
            receipt=json.loads(_canonical(receipt)) if isinstance(receipt, Mapping) else None,
        )


def _context_from_json(value: object) -> IntentPatchContext:
    if not isinstance(value, Mapping):
        raise ValueError("convergence context is invalid")
    return IntentPatchContext(
        origin=value.get("origin"),
        source_kind=value.get("source_kind"),
        base_source_sha256=value.get("base_source_sha256"),
        contract_profile=value.get("contract_profile"),
        evidence_refs=tuple(value.get("evidence_refs", [])),
        requests=tuple(value.get("requests", [])),
    )


@dataclass(frozen=True, slots=True)
class ConvergenceSession:
    session_id: str
    status: str
    mode: str
    source_path_sha256: str
    source_file: str
    created_at: float
    expires_at: float
    current_source_sha256: str
    context: IntentPatchContext
    task: ConvergenceAuthoringTask | None
    baseline_result: VerificationResult | None
    attempts: tuple[ConvergenceAttempt, ...] = ()
    pending_preview: ConvergencePreview | None = None
    seen_source_hashes: tuple[str, ...] = ()
    terminal_reason: str | None = None
    schema_version: int = CONVERGE_SESSION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONVERGE_SESSION_SCHEMA_VERSION:
            raise ValueError("unsupported convergence session schema version")
        if CONVERGE_SESSION_ID_RE.fullmatch(self.session_id) is None:
            raise ValueError("convergence session id is invalid")
        if self.status not in CONVERGE_STATUSES:
            raise ValueError("convergence session status is invalid")
        if self.mode not in {"review", "verification"}:
            raise ValueError("convergence session mode is invalid")
        if not all(
            SHA256_RE.fullmatch(value) is not None
            for value in (self.source_path_sha256, self.current_source_sha256, *self.seen_source_hashes)
        ):
            raise ValueError("convergence session hash is invalid")
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (self.created_at, self.expires_at)):
            raise ValueError("convergence timestamps are invalid")
        if self.expires_at - self.created_at != CONVERGE_MAX_SECONDS:
            raise ValueError("convergence deadline does not match the V1 limit")
        if not 0 <= len(self.attempts) <= CONVERGE_MAX_ATTEMPTS:
            raise ValueError("convergence attempt count is invalid")
        if tuple(item.attempt for item in self.attempts) != tuple(range(1, len(self.attempts) + 1)):
            raise ValueError("convergence attempts must be contiguous")
        if len(set(self.seen_source_hashes)) != len(self.seen_source_hashes):
            raise ValueError("convergence source hash history must be unique")
        if self.current_source_sha256 not in self.seen_source_hashes:
            raise ValueError("current convergence source hash is absent from history")
        if self.status == "awaiting_approval" and self.pending_preview is None:
            raise ValueError("awaiting approval requires a pending preview")
        if self.status != "awaiting_approval" and self.pending_preview is not None:
            raise ValueError("only awaiting approval may retain a pending preview")
        if self.mode == "verification" and self.baseline_result is None and self.status in _ACTIVE_STATUSES:
            raise ValueError("verification convergence requires a baseline result")
        if (
            self.status in {*_ACTIVE_STATUSES, "full_revision_required"}
            and self.task is not None
            and self.task.base_source_sha256 != self.current_source_sha256
        ):
            raise ValueError("convergence task does not match current source")
        if self.status in _ACTIVE_STATUSES and self.context.base_source_sha256 != self.current_source_sha256:
            raise ValueError("convergence context does not match current source")
        if self.pending_preview is not None:
            preview = self.pending_preview
            if not self.attempts or preview.attempt != len(self.attempts):
                raise ValueError("convergence preview attempt is invalid")
            attempt = self.attempts[-1]
            if (
                attempt.preview_id != preview.preview_id
                or attempt.patch != preview.patch
                or attempt.base_source_sha256 != preview.base_source_sha256
                or attempt.candidate_source_sha256 != preview.candidate_source_sha256
                or preview.base_source_sha256 != self.current_source_sha256
            ):
                raise ValueError("convergence pending preview does not match attempt history")
            preview_material = {
                "session_id": self.session_id,
                "attempt": preview.attempt,
                "intent_preview_id": preview.intent_preview_id,
                "candidate_source_sha256": preview.candidate_source_sha256,
                "progress_certificate_id": preview.progress_certificate.certificate_id,
            }
            if preview.preview_id != _identity("vcpv_", "viewspec_convergence_preview_v1", preview_material):
                raise ValueError("convergence preview identity mismatch")
            if preview.progress_certificate.mode == "human_review":
                material = {
                    "mode": "human_review",
                    "accepted": True,
                    "reason": "explicit_human_approval_required",
                    "preview_id": preview.intent_preview_id,
                    "candidate_source_sha256": preview.candidate_source_sha256,
                }
                expected = _identity("vcert_", "viewspec_progress_certificate_v1", material)
                if preview.progress_certificate.certificate_id != expected:
                    raise ValueError("human-review progress certificate identity mismatch")

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    def to_json(self, *, include_approval_token: bool = True) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "status": self.status,
            "mode": self.mode,
            "source_path_sha256": self.source_path_sha256,
            "source_file": self.source_file,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "current_source_sha256": self.current_source_sha256,
            "context": self.context.to_json(),
            "task": self.task.to_json() if self.task else None,
            "baseline_result": self.baseline_result.to_json() if self.baseline_result else None,
            "attempts": [item.to_json() for item in self.attempts],
            "pending_preview": (
                self.pending_preview.to_json(include_approval_token=include_approval_token)
                if self.pending_preview
                else None
            ),
            "seen_source_hashes": list(self.seen_source_hashes),
            "terminal_reason": self.terminal_reason,
            "limits": {
                "max_attempts": CONVERGE_MAX_ATTEMPTS,
                "max_seconds": CONVERGE_MAX_SECONDS,
                "max_state_bytes": CONVERGE_MAX_STATE_BYTES,
            },
        }

    @classmethod
    def from_json(cls, value: object) -> ConvergenceSession:
        if not isinstance(value, Mapping) or value.get("schema_version") != CONVERGE_SESSION_SCHEMA_VERSION:
            raise ValueError("convergence session is invalid")
        task = value.get("task")
        baseline = value.get("baseline_result")
        pending = value.get("pending_preview")
        return cls(
            session_id=str(value.get("session_id")),
            status=str(value.get("status")),
            mode=str(value.get("mode")),
            source_path_sha256=str(value.get("source_path_sha256")),
            source_file=str(value.get("source_file")),
            created_at=float(value.get("created_at")),
            expires_at=float(value.get("expires_at")),
            current_source_sha256=str(value.get("current_source_sha256")),
            context=_context_from_json(value.get("context")),
            task=ConvergenceAuthoringTask.from_json(task) if isinstance(task, Mapping) else None,
            baseline_result=VerificationResult.from_json(baseline) if isinstance(baseline, Mapping) else None,
            attempts=tuple(ConvergenceAttempt.from_json(item) for item in value.get("attempts", [])),
            pending_preview=ConvergencePreview.from_json(pending) if isinstance(pending, Mapping) else None,
            seen_source_hashes=tuple(value.get("seen_source_hashes", [])),
            terminal_reason=value.get("terminal_reason"),
        )


def _replace_session(session: ConvergenceSession, **changes: Any) -> ConvergenceSession:
    values = {
        "session_id": session.session_id,
        "status": session.status,
        "mode": session.mode,
        "source_path_sha256": session.source_path_sha256,
        "source_file": session.source_file,
        "created_at": session.created_at,
        "expires_at": session.expires_at,
        "current_source_sha256": session.current_source_sha256,
        "context": session.context,
        "task": session.task,
        "baseline_result": session.baseline_result,
        "attempts": session.attempts,
        "pending_preview": session.pending_preview,
        "seen_source_hashes": session.seen_source_hashes,
        "terminal_reason": session.terminal_reason,
    }
    values.update(changes)
    return ConvergenceSession(**values)


def _state_root(source: Path, state_root: str | Path | None) -> Path:
    if state_root is None:
        return source.parent / ".viewspec" / "converge-sessions"
    root = Path(os.path.abspath(Path(state_root).expanduser()))
    if root.is_symlink():
        raise ConvergeError(
            "CONVERGE_STATE_UNSAFE",
            "Convergence state root must be a private non-symlink directory.",
            "Choose a local owner-controlled state directory.",
            cli_exit=1,
        )
    return root


def _canonical_source(source_path: str | Path) -> Path:
    candidate = Path(os.path.abspath(Path(source_path).expanduser()))
    if candidate.is_symlink():
        raise ConvergeError(
            "CONVERGE_PATH_INVALID",
            "Convergence source must be a regular, non-symlink file.",
            "Use one local ViewSpec semantic source file.",
        )
    try:
        source = candidate.resolve(strict=True)
        value = source.lstat()
    except OSError as exc:
        raise ConvergeError(
            "CONVERGE_PATH_INVALID",
            f"Convergence source is unavailable: {exc}",
            "Use one readable local ViewSpec semantic source file.",
        ) from exc
    if not stat.S_ISREG(value.st_mode):
        raise ConvergeError(
            "CONVERGE_PATH_INVALID",
            "Convergence source must be a regular, non-symlink file.",
            "Use one local ViewSpec semantic source file.",
        )
    return source


def _state_paths(source: Path, state_root: str | Path | None) -> tuple[Path, Path, Path]:
    resolved = source.resolve(strict=True)
    source_path_sha = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()
    root = _state_root(resolved, state_root)
    return root, root / f"{source_path_sha}.json", root / f".{source_path_sha}.lock"


def _assert_state_root(root: Path) -> None:
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ConvergeError(
            "CONVERGE_STATE_UNSAFE",
            "Convergence state root must be a private non-symlink directory.",
            "Choose a local owner-controlled state directory.",
            cli_exit=1,
        )
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError as exc:
        raise ConvergeError(
            "CONVERGE_STATE_UNSAFE",
            f"Cannot secure convergence state root: {exc}",
            "Choose an owner-controlled writable directory.",
            cli_exit=1,
        ) from exc


@contextmanager
def _session_lock(source: Path, state_root: str | Path | None):
    root, _, lock_path = _state_paths(source, state_root)
    _assert_state_root(root)
    if lock_path.is_symlink():
        raise ConvergeError(
            "CONVERGE_STATE_UNSAFE",
            "Convergence lock cannot be a symlink.",
            "Remove the unsafe lock and retry.",
            cli_exit=1,
        )
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ConvergeError(
            "CONVERGE_STATE_UNSAFE",
            f"Cannot open convergence lock: {exc}",
            "Repair the private state directory and retry.",
            cli_exit=1,
        ) from exc
    acquired = False
    deadline = time.monotonic() + CONVERGE_LOCK_TIMEOUT_SECONDS
    try:
        if os.name == "nt":  # pragma: no cover - Windows CI
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            while time.monotonic() < deadline:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError:
                    time.sleep(0.01)
        else:
            import fcntl

            while time.monotonic() < deadline:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    time.sleep(0.01)
        if not acquired:
            raise ConvergeError(
                "CONVERGE_LOCK_TIMEOUT",
                "Another convergence operation held the source session lock for two seconds.",
                "Retry after the active operation finishes.",
                cli_exit=1,
            )
        yield
    finally:
        if acquired:
            if os.name == "nt":  # pragma: no cover - Windows CI
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _write_state(source: Path, state_root: str | Path | None, session: ConvergenceSession) -> Path:
    root, state_path, _ = _state_paths(source, state_root)
    _assert_state_root(root)
    payload = session.to_json(include_approval_token=True)
    payload_bytes = _canonical(payload)
    envelope = {
        "payload": payload,
        "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
    }
    encoded = json.dumps(envelope, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    if len(encoded) > CONVERGE_MAX_STATE_BYTES:
        raise ConvergeError(
            "CONVERGE_STATE_TOO_LARGE",
            "Convergence session state exceeds 1 MiB.",
            "End this bounded session and split the repair evidence.",
            cli_exit=1,
        )
    _durable_write_bytes(state_path, encoded)
    state_path.chmod(0o600)
    return state_path


def _read_state(source: Path, state_root: str | Path | None) -> ConvergenceSession:
    _, state_path, _ = _state_paths(source, state_root)
    if state_path.is_symlink() or not state_path.is_file():
        raise ConvergeError(
            "CONVERGE_SESSION_NOT_FOUND",
            "No convergence session exists for this source.",
            "Start a source-bound convergence session first.",
        )
    value = state_path.lstat()
    if not stat.S_ISREG(value.st_mode) or value.st_size > CONVERGE_MAX_STATE_BYTES:
        raise ConvergeError(
            "CONVERGE_STATE_INVALID",
            "Convergence state file is not regular or exceeds 1 MiB.",
            "Inspect and archive the malformed state file.",
            cli_exit=1,
        )
    try:
        envelope = _strict_json_loads(
            state_path.read_text(encoding="utf-8"),
            code="CONVERGE_STATE_INVALID",
            noun="convergence state",
        )
        if not isinstance(envelope, Mapping) or set(envelope) != {"payload", "payload_sha256"}:
            raise ValueError("state envelope fields are invalid")
        payload = envelope["payload"]
        if envelope["payload_sha256"] != hashlib.sha256(_canonical(payload)).hexdigest():
            raise ValueError("state checksum mismatch")
        session = ConvergenceSession.from_json(payload)
    except ConvergeError:
        raise
    except IntentPatchError as exc:
        raise ConvergeError(
            "CONVERGE_STATE_INVALID",
            exc.message,
            "Inspect and archive the malformed state file before retrying.",
            cli_exit=1,
        ) from exc
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise ConvergeError(
            "CONVERGE_STATE_INVALID",
            f"Convergence state is invalid: {exc}",
            "Inspect and archive the malformed state file before retrying.",
            cli_exit=1,
        ) from exc
    expected_path_hash = hashlib.sha256(str(source.resolve(strict=True)).encode("utf-8")).hexdigest()
    if session.source_path_sha256 != expected_path_hash or session.source_file != source.name:
        raise ConvergeError(
            "CONVERGE_STATE_INVALID",
            "Convergence state does not belong to this source path.",
            "Use the exact source that started the session.",
            cli_exit=1,
        )
    return session


def _now(clock: Callable[[], float] | None) -> float:
    value = (clock or time.time)()
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError("clock must return numeric seconds")
    return float(value)


def _expire_if_needed(
    source: Path,
    state_root: str | Path | None,
    session: ConvergenceSession,
    now: float,
) -> ConvergenceSession:
    if session.status in _ACTIVE_STATUSES and now > session.expires_at:
        session = _replace_session(
            session,
            status="exhausted",
            pending_preview=None,
            terminal_reason="deadline_exceeded",
        )
        _write_state(source, state_root, session)
    return session


def _read_source(source: Path, expected_kind: str | None = None) -> tuple[str, dict[str, Any], str]:
    text = _read_bounded_utf8_file(
        source,
        maximum=APP_BUNDLE_MAX_BYTES,
        too_large_code="CONVERGE_SOURCE_TOO_LARGE",
        noun="ViewSpec convergence source",
        changed_code="CONVERGE_SOURCE_CHANGED",
    )
    if expected_kind is None:
        raw = _strict_json_loads(text, code="CONVERGE_SOURCE_INVALID", noun="ViewSpec source")
        if not isinstance(raw, dict):
            raise ConvergeError(
                "CONVERGE_SOURCE_INVALID",
                "ViewSpec convergence source root must be an object.",
                "Use one validated IntentBundle or AppBundle.",
            )
        if {"app", "screens", "routes"}.issubset(raw):
            expected_kind = "app_bundle"
        elif {"substrate", "view_spec"}.issubset(raw):
            expected_kind = "intent_bundle"
        else:
            raise ConvergeError(
                "CONVERGE_SOURCE_INVALID",
                "Source does not identify one IntentBundle or AppBundle.",
                "Use one validated local_v1 semantic source.",
            )
    try:
        payload = _validate_source_text(text, expected_kind)
    except IntentPatchError as exc:
        raise ConvergeError(
            "CONVERGE_SOURCE_INVALID",
            exc.message,
            exc.fix,
            cli_exit=exc.cli_exit,
        ) from exc
    return text, payload, source_sha256(text)


def _ensure_source_hash(source: Path, source_kind: str, expected: str) -> tuple[str, dict[str, Any]]:
    text, payload, actual = _read_source(source, source_kind)
    if actual != expected:
        raise ConvergeError(
            "CONVERGE_SOURCE_CHANGED",
            "Source bytes changed outside the active convergence session.",
            "Start a new session from the current source; convergence never rebases automatically.",
        )
    return text, payload


def start_convergence_session(
    source_path: str | Path,
    context: IntentPatchContext,
    *,
    baseline_result: VerificationResult | None = None,
    state_root: str | Path | None = None,
    clock: Callable[[], float] | None = None,
) -> ConvergenceSession:
    """Start one source-bound convergence session without mutating source."""

    source = _canonical_source(source_path)
    if not isinstance(context, IntentPatchContext):
        raise TypeError("context must be an IntentPatchContext")
    now = _now(clock)
    with _session_lock(source, state_root):
        _, state_path, _ = _state_paths(source, state_root)
        if state_path.exists():
            existing = _read_state(source, state_root)
            existing = _expire_if_needed(source, state_root, existing, now)
            if existing.status in _ACTIVE_STATUSES:
                raise ConvergeError(
                    "CONVERGE_SESSION_ACTIVE",
                    "An active convergence session already owns this source.",
                    "Finish, reject, or let the bounded session expire before starting another.",
                )
        text, payload, current_hash = _read_source(source, context.source_kind)
        if context.contract_profile != INTENT_PATCH_CONTRACT_PROFILE:
            raise ConvergeError(
                "CONVERGE_PROFILE_UNSUPPORTED",
                "Convergence context must use local_v1.",
                "Regenerate evidence with the current local contract.",
            )
        if current_hash != context.base_source_sha256:
            raise ConvergeError(
                "CONVERGE_SOURCE_CHANGED",
                "Convergence evidence does not match the exact current source bytes.",
                "Regenerate Review or verifier evidence from the current source.",
            )
        mode = "review" if context.origin == "review_batch" else "verification"
        if mode == "verification":
            if not isinstance(baseline_result, VerificationResult):
                raise ConvergeError(
                    "CONVERGE_BASELINE_REQUIRED",
                    "Verifier-driven convergence requires its complete baseline VerificationResult.",
                    "Pass the exact result that produced this repair plan.",
                )
            if not baseline_result.complete or baseline_result.status != "nonconformant":
                raise ConvergeError(
                    "CONVERGE_BASELINE_INVALID",
                    "Verifier-driven convergence requires one complete nonconformant baseline.",
                    "Retry indeterminate verification or skip convergence for a conformant result.",
                )
            expected_plan = VerificationRepairPlan.from_result(baseline_result)
            if f"verify:{expected_plan.repair_plan_id}" not in context.evidence_refs:
                raise ConvergeError(
                    "CONVERGE_BASELINE_INVALID",
                    "Convergence context does not match the supplied baseline result.",
                    "Use the exact repair plan derived from the baseline VerificationResult.",
                )
        elif baseline_result is not None:
            raise ConvergeError(
                "CONVERGE_BASELINE_INVALID",
                "Human Review convergence does not accept a verifier baseline implicitly.",
                "Start from either one Review batch or one verification repair plan.",
            )
        task = _build_authoring_task(payload, context)
        status = "awaiting_proposal" if task.targets else "full_revision_required"
        session = ConvergenceSession(
            session_id=f"vcgs_{secrets.token_hex(16)}",
            status=status,
            mode=mode,
            source_path_sha256=hashlib.sha256(str(source).encode("utf-8")).hexdigest(),
            source_file=source.name,
            created_at=now,
            expires_at=now + CONVERGE_MAX_SECONDS,
            current_source_sha256=current_hash,
            context=context,
            task=task,
            baseline_result=baseline_result,
            seen_source_hashes=(current_hash,),
            terminal_reason=None if task.targets else "no_legal_intent_patch_target",
        )
        _write_state(source, state_root, session)
        return session


def _default_candidate_verifier(
    candidate_text: str,
    source_kind: str,
    plan: VerificationPlan,
    lineage: RetryLineage,
) -> VerificationResult:
    """Compile through the checked Review boundary and run the canonical local verifier."""

    from viewspec.local_verify import verify_local_artifact
    from viewspec.review_compile import (
        GenerationGate,
        build_review_revision,
        capture_source_snapshot,
    )

    with tempfile.TemporaryDirectory(prefix="viewspec-converge-verify-") as directory:
        root = Path(directory)
        source = root / ("viewspec.intent.json" if source_kind == "intent_bundle" else "viewspec.app.json")
        source.write_text(candidate_text, encoding="utf-8")
        gate = GenerationGate()
        generation = gate.observe()
        snapshot = capture_source_snapshot(source)
        target = "html-tailwind" if source_kind == "intent_bundle" else "html-tailwind-app"
        built = build_review_revision(
            snapshot,
            session_dir=root / "review",
            revision_number=1,
            generation=generation,
            gate=gate,
            target=target,
        )
        try:
            raw = verify_local_artifact(
                built.artifact_dir,
                plan=plan,
                evidence_dir=root / "evidence",
                install=False,
            )
            return VerificationResult.create(
                artifact_sha256=built.revision.artifact_set_sha256,
                plan=plan,
                complete=raw.complete,
                diagnostics=raw.diagnostics,
                evidence=raw.evidence,
                lineage=lineage,
            )
        except Exception as exc:
            diagnostic = VerificationDiagnostic(
                code="VERIFY_BROWSER_EXECUTION_FAILED",
                severity="warning",
                message=(str(exc) or "Canonical convergence verification was unavailable.")[:2048],
                fix="Repair the local verifier environment and retry the same candidate.",
            )
            return VerificationResult.create(
                artifact_sha256=built.revision.artifact_set_sha256,
                plan=plan,
                complete=False,
                diagnostics=(diagnostic,),
                lineage=lineage,
            )


def _run_candidate_verifier(
    verifier: CandidateVerifier | None,
    candidate_text: str,
    source_kind: str,
    baseline: VerificationResult,
) -> VerificationResult:
    lineage = baseline.lineage.next_attempt(baseline.verification_id)
    result = (verifier or _default_candidate_verifier)(
        candidate_text,
        source_kind,
        baseline.plan,
        lineage,
    )
    if not isinstance(result, VerificationResult):
        raise ConvergeError(
            "CONVERGE_VERIFIER_INVALID",
            "Candidate verifier did not return a VerificationResult.",
            "Use the canonical bounded ViewSpec verification contract.",
            cli_exit=1,
        )
    if result.lineage != lineage:
        raise ConvergeError(
            "CONVERGE_VERIFIER_INVALID",
            "Candidate verification lineage does not descend from the exact baseline.",
            "Preserve the supplied retry lineage without modification.",
            cli_exit=1,
        )
    return result


def _convert_patch_error(exc: IntentPatchError) -> ConvergeError:
    return ConvergeError(exc.code, exc.message, exc.fix, cli_exit=exc.cli_exit)


def submit_convergence_patch(
    source_path: str | Path,
    patch_value: str | Mapping[str, Any] | IntentPatch,
    *,
    verifier: CandidateVerifier | None = None,
    state_root: str | Path | None = None,
    clock: Callable[[], float] | None = None,
) -> ConvergenceSession:
    """Preview one legal proposal and persist either approval state or a terminal rejection."""

    source = _canonical_source(source_path)
    now = _now(clock)
    with _session_lock(source, state_root):
        session = _expire_if_needed(source, state_root, _read_state(source, state_root), now)
        if session.status == "exhausted" and session.terminal_reason == "deadline_exceeded":
            return session
        if session.status != "awaiting_proposal" or session.task is None:
            raise ConvergeError(
                "CONVERGE_STATE_CONFLICT",
                f"Session status {session.status!r} cannot accept a proposal.",
                "Follow the session's next legal action.",
            )
        if session.attempt_count >= CONVERGE_MAX_ATTEMPTS:
            exhausted = _replace_session(
                session,
                status="exhausted",
                terminal_reason="attempt_limit",
            )
            _write_state(source, state_root, exhausted)
            return exhausted
        source_text, _ = _ensure_source_hash(
            source,
            session.context.source_kind,
            session.current_source_sha256,
        )
        try:
            patch = parse_intent_patch(patch_value)
        except IntentPatchError as exc:
            raise _convert_patch_error(exc) from exc
        if patch.source_kind != session.context.source_kind or patch.base_source_sha256 != session.current_source_sha256:
            raise ConvergeError(
                "CONVERGE_SOURCE_CHANGED",
                "Patch is not bound to the session's exact current source.",
                "Use the task base_source_sha256 without rebasing.",
            )
        if set(patch.evidence_refs) != set(session.context.evidence_refs):
            raise ConvergeError(
                "CONVERGE_EVIDENCE_MISMATCH",
                "Patch evidence refs do not exactly match the active authoring task.",
                "Copy the task evidence refs exactly; do not omit or substitute authority.",
            )
        allowed = set(session.task.allowed_target_keys)
        outside = [operation.target_key for operation in patch.operations if operation.target_key not in allowed]
        if outside:
            raise ConvergeError(
                "CONVERGE_TARGET_OUTSIDE_TASK",
                "Patch writes a semantic target outside the bounded authoring task.",
                "Use only the legal operation templates returned by this session.",
            )
        try:
            intent_preview = preview_intent_patch(source_text, patch)
        except IntentPatchError as exc:
            raise _convert_patch_error(exc) from exc
        if intent_preview.candidate_source_sha256 in session.seen_source_hashes:
            stalled = _replace_session(
                session,
                status="stalled",
                terminal_reason="candidate_cycle",
            )
            _write_state(source, state_root, stalled)
            return stalled

        attempt_number = session.attempt_count + 1
        candidate_result: VerificationResult | None = None
        if session.mode == "verification":
            assert session.baseline_result is not None
            candidate_result = _run_candidate_verifier(
                verifier,
                intent_preview.candidate_text,
                session.context.source_kind,
                session.baseline_result,
            )
            certificate = ProgressCertificate.compare(session.baseline_result, candidate_result)
        else:
            certificate = ProgressCertificate.human_review(intent_preview)
        preview_material = {
            "session_id": session.session_id,
            "attempt": attempt_number,
            "intent_preview_id": intent_preview.preview_id,
            "candidate_source_sha256": intent_preview.candidate_source_sha256,
            "progress_certificate_id": certificate.certificate_id,
        }
        preview_id = _identity(
            "vcpv_",
            "viewspec_convergence_preview_v1",
            preview_material,
        )
        approval_token = f"vcapprove_{secrets.token_hex(32)}"
        pending = ConvergencePreview(
            preview_id=preview_id,
            attempt=attempt_number,
            patch=patch,
            intent_preview_id=intent_preview.preview_id,
            intent_approval_token=intent_preview.approval_token,
            base_source_sha256=intent_preview.base_source_sha256,
            candidate_source_sha256=intent_preview.candidate_source_sha256,
            semantic_diff=intent_preview.semantic_diff,
            compile_check=intent_preview.compile_check,
            progress_certificate=certificate,
            candidate_result=candidate_result,
            approval_token=approval_token,
        )
        attempt = ConvergenceAttempt(
            attempt=attempt_number,
            base_source_sha256=intent_preview.base_source_sha256,
            candidate_source_sha256=intent_preview.candidate_source_sha256,
            patch=patch,
            preview_id=preview_id,
            progress_certificate=certificate,
            candidate_result=candidate_result,
        )
        history = (*session.seen_source_hashes, intent_preview.candidate_source_sha256)
        if not certificate.accepted:
            stalled = _replace_session(
                session,
                status="stalled",
                attempts=(*session.attempts, attempt),
                seen_source_hashes=history,
                terminal_reason=certificate.reason,
            )
            _write_state(source, state_root, stalled)
            return stalled
        awaiting = _replace_session(
            session,
            status="awaiting_approval",
            attempts=(*session.attempts, attempt),
            pending_preview=pending,
            seen_source_hashes=history,
            terminal_reason=None,
        )
        _write_state(source, state_root, awaiting)
        return awaiting


def _patch_file_for_preview(
    source: Path,
    state_root: str | Path | None,
    preview: ConvergencePreview,
) -> Path:
    root, _, _ = _state_paths(source, state_root)
    path = root / f"{preview.preview_id}.intentpatch.json"
    content = json.dumps(preview.patch.to_json(), ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            raise ConvergeError(
                "CONVERGE_STATE_INVALID",
                "Stored convergence patch file conflicts with its approved preview.",
                "Inspect the private session state before retrying.",
                cli_exit=1,
            )
    else:
        _durable_write_bytes(path, content)
        path.chmod(0o600)
    return path


def _same_verification_obligations(left: VerificationResult, right: VerificationResult) -> bool:
    return (
        left.plan.plan_sha256 == right.plan.plan_sha256
        and left.complete == right.complete
        and left.status == right.status
        and _error_obligations(left) == _error_obligations(right)
    )


def approve_convergence_preview(
    source_path: str | Path,
    approval_token: str,
    *,
    verifier: CandidateVerifier | None = None,
    state_root: str | Path | None = None,
    clock: Callable[[], float] | None = None,
) -> ConvergenceSession:
    """Apply the exact progress-bound preview and reconcile its post-apply verification."""

    source = _canonical_source(source_path)
    now = _now(clock)
    with _session_lock(source, state_root):
        session = _expire_if_needed(source, state_root, _read_state(source, state_root), now)
        if session.status == "exhausted" and session.terminal_reason == "deadline_exceeded":
            raise ConvergeError(
                "CONVERGE_SESSION_EXPIRED",
                "Convergence approval expired after ten minutes.",
                "Start a new session from current source and evidence.",
            )
        preview = session.pending_preview
        if session.status != "awaiting_approval" or preview is None:
            raise ConvergeError(
                "CONVERGE_STATE_CONFLICT",
                f"Session status {session.status!r} has no approvable preview.",
                "Submit one bounded proposal first.",
            )
        if (
            not isinstance(approval_token, str)
            or CONVERGE_APPROVAL_RE.fullmatch(approval_token) is None
            or approval_token != preview.approval_token
        ):
            raise ConvergeError(
                "CONVERGE_APPROVAL_INVALID",
                "Approval does not authorize this exact preview and progress certificate.",
                "Approve the current before/after proposal; stale or substituted authority is rejected.",
            )
        _ensure_source_hash(source, session.context.source_kind, preview.base_source_sha256)
        patch_file = _patch_file_for_preview(source, state_root, preview)
        try:
            receipt = apply_intent_patch_file(
                source,
                patch_file,
                approval_token=preview.intent_approval_token,
            )
        except IntentPatchError as exc:
            raise _convert_patch_error(exc) from exc
        if receipt.candidate_source_sha256 != preview.candidate_source_sha256:
            raise ConvergeError(
                "CONVERGE_APPLY_MISMATCH",
                "IntentPatch receipt does not match the approved convergence candidate.",
                "Stop and inspect the source and receipts; do not continue automatically.",
                cli_exit=1,
            )
        attempts = list(session.attempts)
        attempts[-1] = ConvergenceAttempt(
            attempt=attempts[-1].attempt,
            base_source_sha256=attempts[-1].base_source_sha256,
            candidate_source_sha256=attempts[-1].candidate_source_sha256,
            patch=attempts[-1].patch,
            preview_id=attempts[-1].preview_id,
            progress_certificate=attempts[-1].progress_certificate,
            candidate_result=attempts[-1].candidate_result,
            receipt=receipt.to_json(),
        )
        applied = _replace_session(
            session,
            status="applied",
            current_source_sha256=preview.candidate_source_sha256,
            attempts=tuple(attempts),
            pending_preview=None,
            terminal_reason=None,
        )
        _write_state(source, state_root, applied)
        if session.mode == "review":
            return applied

        assert session.baseline_result is not None
        assert preview.candidate_result is not None
        source_text, payload = _ensure_source_hash(
            source,
            session.context.source_kind,
            preview.candidate_source_sha256,
        )
        reverified = _run_candidate_verifier(
            verifier,
            source_text,
            session.context.source_kind,
            session.baseline_result,
        )
        if not _same_verification_obligations(preview.candidate_result, reverified):
            stalled = _replace_session(
                applied,
                status="stalled",
                terminal_reason="post_apply_verification_drift",
            )
            _write_state(source, state_root, stalled)
            return stalled
        if reverified.status == "conformant":
            conformant = _replace_session(
                applied,
                status="conformant",
                baseline_result=reverified,
                task=None,
                terminal_reason="all_obligations_satisfied",
            )
            _write_state(source, state_root, conformant)
            return conformant
        if len(attempts) >= CONVERGE_MAX_ATTEMPTS:
            exhausted = _replace_session(
                applied,
                status="exhausted",
                baseline_result=reverified,
                task=None,
                terminal_reason="attempt_limit",
            )
            _write_state(source, state_root, exhausted)
            return exhausted
        repair_plan = VerificationRepairPlan.from_result(reverified)
        if repair_plan.disposition != "repair":
            stalled = _replace_session(
                applied,
                status="stalled",
                baseline_result=reverified,
                task=None,
                terminal_reason="post_apply_verification_indeterminate",
            )
            _write_state(source, state_root, stalled)
            return stalled
        context = patch_context_from_repair_plan(
            repair_plan,
            source_kind=session.context.source_kind,
            base_source_sha256=preview.candidate_source_sha256,
        )
        task = _build_authoring_task(payload, context)
        continued = _replace_session(
            applied,
            status="awaiting_proposal" if task.targets else "full_revision_required",
            context=context,
            task=task,
            baseline_result=reverified,
            terminal_reason=None if task.targets else "no_legal_intent_patch_target",
        )
        _write_state(source, state_root, continued)
        return continued


def reject_convergence_preview(
    source_path: str | Path,
    preview_id: str,
    *,
    state_root: str | Path | None = None,
    clock: Callable[[], float] | None = None,
) -> ConvergenceSession:
    """Reject one exact pending proposal without writing source."""

    source = _canonical_source(source_path)
    now = _now(clock)
    with _session_lock(source, state_root):
        session = _expire_if_needed(source, state_root, _read_state(source, state_root), now)
        preview = session.pending_preview
        if session.status != "awaiting_approval" or preview is None or preview.preview_id != preview_id:
            raise ConvergeError(
                "CONVERGE_PREVIEW_INVALID",
                "Rejected preview does not match the exact current proposal.",
                "Reject only the proposal currently shown in Review.",
            )
        _ensure_source_hash(source, session.context.source_kind, preview.base_source_sha256)
        rejected = _replace_session(
            session,
            status="rejected",
            pending_preview=None,
            terminal_reason="human_rejected",
        )
        _write_state(source, state_root, rejected)
        return rejected


def get_convergence_status(
    source_path: str | Path,
    *,
    state_root: str | Path | None = None,
    clock: Callable[[], float] | None = None,
) -> ConvergenceSession:
    """Read one checksum-validated convergence session and enforce its deadline."""

    source = _canonical_source(source_path)
    now = _now(clock)
    with _session_lock(source, state_root):
        return _expire_if_needed(source, state_root, _read_state(source, state_root), now)


__all__ = [
    "CONVERGE_MAX_ATTEMPTS",
    "CONVERGE_MAX_SECONDS",
    "CONVERGE_SESSION_SCHEMA_VERSION",
    "CONVERGENCE_TASK_JSON_SCHEMA",
    "CandidateVerifier",
    "ConvergeError",
    "ConvergenceAttempt",
    "ConvergenceAuthoringTask",
    "ConvergencePreview",
    "ConvergenceSession",
    "ConvergenceTarget",
    "ProgressCertificate",
    "VerificationObligation",
    "approve_convergence_preview",
    "get_convergence_status",
    "reject_convergence_preview",
    "start_convergence_session",
    "starter_convergence_task_payload",
    "submit_convergence_patch",
]
