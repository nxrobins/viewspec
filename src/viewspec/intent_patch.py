"""Proof-carrying, id-addressed semantic patch transactions for ViewSpec sources."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Any, Mapping

from viewspec.aesthetics import AESTHETIC_PROFILE_TOKENS
from viewspec.agent import SUPPORTED_AGENT_REGION_LAYOUTS, SUPPORTED_AGENT_STYLE_TOKENS
from viewspec.app_bundle import compile_app, diff_app_text
from viewspec.app_validation import APP_BUNDLE_MAX_BYTES, validate_app_text
from viewspec.intent_tools import (
    compile_intent_bundle_file_tool,
    diff_intent_text,
    starter_intent_payload,
    validate_intent_text,
)
from viewspec.repair import VerificationRepairPlan
from viewspec.review_contract import ReviewBatch
from viewspec.types import PRESENT_AS_TO_PRIMITIVE


INTENT_PATCH_SCHEMA_VERSION = 1
INTENT_PATCH_CONTRACT_PROFILE = "local_v1"
INTENT_PATCH_MAX_BYTES = 64 * 1024
INTENT_PATCH_MAX_OPERATIONS = 64
INTENT_PATCH_MAX_EVIDENCE_REFS = 64
INTENT_PATCH_MAX_EVIDENCE_REF_BYTES = 256
INTENT_PATCH_MAX_STRING_BYTES = 8 * 1024
INTENT_PATCH_MAX_CONTEXT_REQUESTS = 63
INTENT_PATCH_RECEIPT_DIR = ".viewspec/patch-receipts"
INTENT_PATCH_MAX_RECEIPT_BYTES = 256 * 1024
INTENT_PATCH_LOCK_TIMEOUT_SECONDS = 5.0
INTENT_PATCH_SOURCE_KINDS = frozenset({"intent_bundle", "app_bundle"})
INTENT_PATCH_OPERATION_KINDS = frozenset(
    {
        "set_aesthetic_profile",
        "set_style_token",
        "set_region_layout",
        "move_region",
        "reorder_region_children",
        "set_binding_presentation",
        "replace_semantic_attr",
        "replace_fixture_scalar",
        "set_visibility_condition",
    }
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
_EVIDENCE_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,256}$")
_PATCH_ID_RE = re.compile(r"^vpatch_[0-9a-f]{32}$")
_PREVIEW_ID_RE = re.compile(r"^vpv_[0-9a-f]{32}$")
_APPROVAL_RE = re.compile(r"^vapprove_[0-9a-f]{64}$")

_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "contract_profile",
        "source_kind",
        "base_source_sha256",
        "operations",
        "evidence_refs",
    }
)
_INTENT_OPERATION_KINDS = frozenset(
    {
        "set_aesthetic_profile",
        "set_style_token",
        "set_region_layout",
        "move_region",
        "reorder_region_children",
        "set_binding_presentation",
        "replace_semantic_attr",
    }
)
_OPERATION_FIELDS: Mapping[str, frozenset[str]] = {
    "set_aesthetic_profile": frozenset({"op", "screen_id", "old_value", "value"}),
    "set_style_token": frozenset({"op", "screen_id", "style_id", "old_value", "value"}),
    "set_region_layout": frozenset({"op", "screen_id", "region_id", "old_value", "value"}),
    "move_region": frozenset({"op", "screen_id", "region_id", "old_parent_id", "parent_id"}),
    "reorder_region_children": frozenset({"op", "screen_id", "region_id", "old_children", "children"}),
    "set_binding_presentation": frozenset({"op", "screen_id", "binding_id", "old_value", "value"}),
    "replace_semantic_attr": frozenset({"op", "screen_id", "node_id", "attr", "old_value", "value"}),
    "replace_fixture_scalar": frozenset({"op", "resource_id", "record_id", "field", "old_value", "value"}),
    "set_visibility_condition": frozenset({"op", "visibility_id", "old_value", "value"}),
}


def _operation_schema(
    op: str,
    properties: Mapping[str, Any],
    *,
    screen_scoped: bool = False,
) -> dict[str, Any]:
    merged = {"op": {"const": op}, **dict(properties)}
    if screen_scoped:
        merged["screen_id"] = {"$ref": "#/$defs/id"}
    return {
        "type": "object",
        "required": ["op", *properties],
        "additionalProperties": False,
        "properties": merged,
    }


_OLD_NEW_SCALAR = {
    "old_value": {"$ref": "#/$defs/scalar"},
    "value": {"$ref": "#/$defs/scalar"},
}
_OLD_NEW_TOKEN = {
    "old_value": {"type": "string", "enum": sorted(SUPPORTED_AGENT_STYLE_TOKENS)},
    "value": {"type": "string", "enum": sorted(SUPPORTED_AGENT_STYLE_TOKENS)},
}
_CHILD_IDS = {
    "type": "array",
    "maxItems": 32,
    "uniqueItems": True,
    "items": {"$ref": "#/$defs/id"},
}

INTENT_PATCH_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://viewspec.dev/intent-patch.schema.json",
    "title": "ViewSpec IntentPatch V1",
    "description": "Closed, source-bound semantic changes for a validated IntentBundle or AppBundle.",
    "type": "object",
    "required": [
        "schema_version",
        "contract_profile",
        "source_kind",
        "base_source_sha256",
        "operations",
        "evidence_refs",
    ],
    "additionalProperties": False,
    "properties": {
        "schema_version": {"type": "integer", "const": INTENT_PATCH_SCHEMA_VERSION},
        "contract_profile": {"type": "string", "const": INTENT_PATCH_CONTRACT_PROFILE},
        "source_kind": {"type": "string", "enum": sorted(INTENT_PATCH_SOURCE_KINDS)},
        "base_source_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "operations": {
            "type": "array",
            "minItems": 1,
            "maxItems": INTENT_PATCH_MAX_OPERATIONS,
            "items": {
                "oneOf": [
                    {"$ref": f"#/$defs/{name}"}
                    for name in sorted(INTENT_PATCH_OPERATION_KINDS)
                ]
            },
        },
        "evidence_refs": {
            "type": "array",
            "maxItems": INTENT_PATCH_MAX_EVIDENCE_REFS,
            "uniqueItems": True,
            "items": {
                "type": "string",
                "maxLength": INTENT_PATCH_MAX_EVIDENCE_REF_BYTES,
                "pattern": r"^(?!.*\.\.)[A-Za-z0-9_.:/-]+$",
            },
        },
    },
    "allOf": [
        {
            "if": {"properties": {"source_kind": {"const": "intent_bundle"}}},
            "then": {
                "properties": {
                    "operations": {
                        "items": {"not": {"required": ["screen_id"]}},
                    }
                }
            },
            "else": {
                "properties": {
                    "operations": {
                        "items": {
                            "if": {
                                "properties": {"op": {"enum": sorted(_INTENT_OPERATION_KINDS)}},
                                "required": ["op"],
                            },
                            "then": {"required": ["screen_id"]},
                        }
                    }
                }
            },
        }
    ],
    "$defs": {
        "id": {"type": "string", "minLength": 1, "maxLength": 128, "pattern": r"^[A-Za-z0-9_.-]+$"},
        "field": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "pattern": r"^[A-Za-z_][A-Za-z0-9_.-]*$",
        },
        "scalar": {"type": ["string", "number", "boolean", "null"]},
        "set_aesthetic_profile": _operation_schema(
            "set_aesthetic_profile",
            {
                "old_value": {"type": ["string", "null"], "enum": [None, *sorted(AESTHETIC_PROFILE_TOKENS)]},
                "value": {"type": ["string", "null"], "enum": [None, *sorted(AESTHETIC_PROFILE_TOKENS)]},
            },
            screen_scoped=True,
        ),
        "set_style_token": _operation_schema(
            "set_style_token",
            {"style_id": {"$ref": "#/$defs/id"}, **_OLD_NEW_TOKEN},
            screen_scoped=True,
        ),
        "set_region_layout": _operation_schema(
            "set_region_layout",
            {
                "region_id": {"$ref": "#/$defs/id"},
                "old_value": {"type": "string", "enum": sorted(SUPPORTED_AGENT_REGION_LAYOUTS)},
                "value": {"type": "string", "enum": sorted(SUPPORTED_AGENT_REGION_LAYOUTS)},
            },
            screen_scoped=True,
        ),
        "move_region": _operation_schema(
            "move_region",
            {
                "region_id": {"$ref": "#/$defs/id"},
                "old_parent_id": {"$ref": "#/$defs/id"},
                "parent_id": {"$ref": "#/$defs/id"},
            },
            screen_scoped=True,
        ),
        "reorder_region_children": _operation_schema(
            "reorder_region_children",
            {
                "region_id": {"$ref": "#/$defs/id"},
                "old_children": _CHILD_IDS,
                "children": _CHILD_IDS,
            },
            screen_scoped=True,
        ),
        "set_binding_presentation": _operation_schema(
            "set_binding_presentation",
            {
                "binding_id": {"$ref": "#/$defs/id"},
                "old_value": {"type": "string", "enum": sorted(PRESENT_AS_TO_PRIMITIVE)},
                "value": {"type": "string", "enum": sorted(PRESENT_AS_TO_PRIMITIVE)},
            },
            screen_scoped=True,
        ),
        "replace_semantic_attr": _operation_schema(
            "replace_semantic_attr",
            {
                "node_id": {"$ref": "#/$defs/id"},
                "attr": {"$ref": "#/$defs/field"},
                **_OLD_NEW_SCALAR,
            },
            screen_scoped=True,
        ),
        "replace_fixture_scalar": _operation_schema(
            "replace_fixture_scalar",
            {
                "resource_id": {"$ref": "#/$defs/id"},
                "record_id": {"$ref": "#/$defs/id"},
                "field": {"$ref": "#/$defs/field"},
                **_OLD_NEW_SCALAR,
            },
        ),
        "set_visibility_condition": _operation_schema(
            "set_visibility_condition",
            {
                "visibility_id": {"$ref": "#/$defs/id"},
                "old_value": {"type": "object"},
                "value": {"type": "object"},
            },
        ),
    },
    "x-viewspec-invariants": [
        "The UTF-8 document is capped at 65536 bytes; JSON Schema character limits are supplemented by runtime byte limits.",
        "base_source_sha256 must match the exact current source bytes; stale patches are never rebased.",
        "Every operation targets one existing stable id and carries an exact old-value precondition.",
        "Every operation must change its target; duplicate semantic targets, field creation, and field deletion are rejected.",
        "Preview validates, semantic-diffs, and compile-checks the full candidate before producing an approval token.",
        "Apply re-previews under a source lock and accepts only the exact current preview approval token.",
    ],
}


class IntentPatchError(ValueError):
    """Stable fail-closed error for patch contract and transaction failures."""

    def __init__(self, code: str, message: str, fix: str, *, cli_exit: int = 2) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix
        self.cli_exit = cli_exit

    def to_json(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "fix": self.fix}


@dataclass(frozen=True)
class IntentPatchContext:
    """Bounded, source-bound evidence for proposing (never applying) a patch."""

    origin: str
    source_kind: str
    base_source_sha256: str
    contract_profile: str
    evidence_refs: tuple[str, ...]
    requests: tuple[dict[str, Any], ...]

    def __post_init__(self) -> None:
        if self.origin not in {"review_batch", "verification_repair_plan"}:
            raise IntentPatchError(
                "PATCH_CONTEXT_INVALID",
                "Patch context origin is unsupported.",
                "Use a validated ReviewBatch or VerificationRepairPlan.",
            )
        _validate_source_identity(
            source_kind=self.source_kind,
            base_source_sha256=self.base_source_sha256,
            contract_profile=self.contract_profile,
        )
        if not self.requests:
            raise IntentPatchError(
                "PATCH_CONTEXT_EMPTY",
                "Patch context contains no actionable requests.",
                "Provide a Review change_request or a verification repair plan.",
            )
        if len(self.requests) > INTENT_PATCH_MAX_CONTEXT_REQUESTS:
            raise IntentPatchError(
                "PATCH_OPERATION_LIMIT_EXCEEDED",
                f"Patch context contains more than {INTENT_PATCH_MAX_CONTEXT_REQUESTS} requests.",
                "Split the evidence into smaller source-bound patch proposals.",
            )
        refs = tuple(self.evidence_refs)
        if len(refs) > INTENT_PATCH_MAX_EVIDENCE_REFS or len(set(refs)) != len(refs):
            raise IntentPatchError(
                "PATCH_EVIDENCE_INVALID",
                "Patch context evidence refs must be unique and bounded.",
                f"Use at most {INTENT_PATCH_MAX_EVIDENCE_REFS} unique evidence refs.",
            )
        for ref in refs:
            if not isinstance(ref, str) or _EVIDENCE_RE.fullmatch(ref) is None:
                raise IntentPatchError(
                    "PATCH_EVIDENCE_INVALID",
                    "Patch context contains an invalid evidence ref.",
                    "Use stable evidence refs containing only letters, digits, dot, underscore, colon, slash, or dash.",
                )
        canonical_requests: list[dict[str, Any]] = []
        for request in self.requests:
            if not isinstance(request, Mapping):
                raise IntentPatchError(
                    "PATCH_CONTEXT_INVALID",
                    "Patch context requests must be objects.",
                    "Regenerate context from a validated evidence contract.",
                )
            canonical_requests.append(
                json.loads(_canonical_json_bytes(dict(request)).decode("utf-8"))
            )
        object.__setattr__(self, "evidence_refs", refs)
        object.__setattr__(self, "requests", tuple(canonical_requests))
        if len(_canonical_json_bytes(self.to_json())) > INTENT_PATCH_MAX_BYTES:
            raise IntentPatchError(
                "PATCH_TOO_LARGE",
                f"Patch context exceeds {INTENT_PATCH_MAX_BYTES} bytes.",
                "Split the evidence into smaller source-bound patch proposals.",
            )

    def to_json(self) -> dict[str, Any]:
        return {
            "origin": self.origin,
            "source_kind": self.source_kind,
            "base_source_sha256": self.base_source_sha256,
            "contract_profile": self.contract_profile,
            "evidence_refs": list(self.evidence_refs),
            "requests": [json.loads(_canonical_json_bytes(item).decode("utf-8")) for item in self.requests],
        }


def _validate_source_identity(
    *,
    source_kind: object,
    base_source_sha256: object,
    contract_profile: object,
) -> None:
    if contract_profile != INTENT_PATCH_CONTRACT_PROFILE:
        raise IntentPatchError(
            "PATCH_PROFILE_UNSUPPORTED",
            f"Unsupported patch contract profile {contract_profile!r}.",
            f"Use contract_profile {INTENT_PATCH_CONTRACT_PROFILE!r}.",
        )
    if source_kind not in INTENT_PATCH_SOURCE_KINDS:
        raise IntentPatchError(
            "PATCH_SOURCE_KIND_UNSUPPORTED",
            f"Unsupported patch source kind {source_kind!r}.",
            "Use intent_bundle or app_bundle.",
        )
    if not isinstance(base_source_sha256, str) or _SHA256_RE.fullmatch(base_source_sha256) is None:
        raise IntentPatchError(
            "PATCH_BASE_HASH_INVALID",
            "Patch base source hash must be a lowercase SHA-256 digest.",
            "Hash the exact UTF-8 source bytes used to propose the patch.",
        )


def patch_context_from_review_batch(batch: ReviewBatch) -> IntentPatchContext:
    """Convert bounded human change requests into non-authoritative patch evidence."""

    if not isinstance(batch, ReviewBatch):
        raise IntentPatchError(
            "PATCH_CONTEXT_INVALID",
            "Review patch context requires a validated ReviewBatch.",
            "Parse and validate the Review V0 batch before creating patch context.",
        )
    _validate_source_identity(
        source_kind=batch.revision.source_kind,
        base_source_sha256=batch.revision.source_sha256,
        contract_profile=batch.revision.contract_profile,
    )
    actionable = tuple(event for event in batch.events if event.kind == "change_request")
    requests = tuple(
        {
            "request_id": event.event_id,
            "kind": event.kind,
            "instruction": event.body,
            "screen_id": event.target.screen_id,
            "source_ref": event.target.source_ref,
            "binding_id": event.target.binding_id,
            "action_id": event.target.action_id,
            "intent_refs": list(event.target.intent_refs),
            "content_refs": list(event.target.content_refs),
        }
        for event in actionable
    )
    return IntentPatchContext(
        origin="review_batch",
        source_kind=batch.revision.source_kind,
        base_source_sha256=batch.revision.source_sha256,
        contract_profile=batch.revision.contract_profile,
        evidence_refs=(
            f"review:{batch.review_id}:{batch.batch_id}",
            *(f"review_event:{event.event_id}" for event in actionable),
        ),
        requests=requests,
    )


def patch_context_from_repair_plan(
    plan: VerificationRepairPlan,
    *,
    source_kind: str,
    base_source_sha256: str,
) -> IntentPatchContext:
    """Convert deterministic verifier repairs into non-authoritative patch evidence."""

    if not isinstance(plan, VerificationRepairPlan):
        raise IntentPatchError(
            "PATCH_CONTEXT_INVALID",
            "Verification patch context requires a validated VerificationRepairPlan.",
            "Parse and validate the repair plan before creating patch context.",
        )
    _validate_source_identity(
        source_kind=source_kind,
        base_source_sha256=base_source_sha256,
        contract_profile=INTENT_PATCH_CONTRACT_PROFILE,
    )
    if plan.disposition != "repair" or not plan.directives:
        raise IntentPatchError(
            "PATCH_CONTEXT_EMPTY",
            "Only verification repair directives can propose source changes.",
            "Do not create a patch for done or retry dispositions.",
        )
    requests = tuple(
        {
            "request_id": directive.repair_id,
            "code": directive.code,
            "instruction": directive.instruction,
            "source_ref": directive.source_path.to_text() if directive.source_path else None,
            "viewports": list(directive.viewports),
            "evidence_refs": list(directive.evidence_refs),
        }
        for directive in plan.directives
    )
    return IntentPatchContext(
        origin="verification_repair_plan",
        source_kind=source_kind,
        base_source_sha256=base_source_sha256,
        contract_profile=INTENT_PATCH_CONTRACT_PROFILE,
        evidence_refs=(
            f"verify:{plan.repair_plan_id}",
            *(f"verify_repair:{directive.repair_id}" for directive in plan.directives),
        ),
        requests=requests,
    )


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise IntentPatchError(
            "PATCH_VALUE_INVALID",
            f"Patch value is not finite canonical JSON: {exc}",
            "Use only bounded JSON values without NaN or Infinity.",
        ) from exc


def _canonical_json_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def source_sha256(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _read_bounded_utf8_file(
    path: Path,
    *,
    maximum: int,
    too_large_code: str,
    noun: str,
    changed_code: str = "PATCH_PATH_INVALID",
) -> str:
    descriptor: int | None = None
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise IntentPatchError(
                "PATCH_PATH_INVALID",
                f"{noun} must be a regular, non-symlink file.",
                "Use a local regular file.",
            )
        if before.st_size > maximum:
            raise IntentPatchError(
                too_large_code,
                f"{noun} is {before.st_size} bytes; limit is {maximum}.",
                "Reduce the input before retrying.",
            )
        chunks: list[bytes] = []
        total = 0
        while total <= maximum:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        data = b"".join(chunks)
        if len(data) > maximum:
            raise IntentPatchError(
                too_large_code,
                f"{noun} exceeds {maximum} bytes.",
                "Reduce the input before retrying.",
            )
        after = os.fstat(descriptor)
        current = path.stat(follow_symlinks=False)
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        identity_current = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns)
        if identity_after != identity_before or identity_current != identity_after:
            raise IntentPatchError(
                changed_code,
                f"{noun} changed while it was being read.",
                "Retry from one stable local file snapshot.",
            )
        return data.decode("utf-8")
    except IntentPatchError:
        raise
    except (OSError, UnicodeError) as exc:
        raise IntentPatchError(
            "PATCH_PATH_INVALID",
            f"Cannot read {noun}: {exc}",
            "Fix the local file path and UTF-8 encoding.",
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _assert_source_hash(path: Path, expected_hash: str) -> None:
    current = _read_bounded_utf8_file(
        path,
        maximum=APP_BUNDLE_MAX_BYTES,
        too_large_code="PATCH_SOURCE_TOO_LARGE",
        noun="ViewSpec source",
        changed_code="PATCH_BASE_CHANGED",
    )
    if source_sha256(current) != expected_hash:
        raise IntentPatchError(
            "PATCH_BASE_CHANGED",
            "Source bytes changed during the patch transaction.",
            "Preserve the concurrent edit and regenerate the patch from the current source.",
        )


def starter_intent_patch_payload() -> dict[str, Any]:
    """Return a valid patch for the exported dashboard IntentBundle example bytes."""

    source = json.dumps(starter_intent_payload("dashboard"), indent=2, sort_keys=True) + "\n"
    return {
        "schema_version": INTENT_PATCH_SCHEMA_VERSION,
        "contract_profile": INTENT_PATCH_CONTRACT_PROFILE,
        "source_kind": "intent_bundle",
        "base_source_sha256": source_sha256(source),
        "operations": [
            {
                "op": "replace_semantic_attr",
                "node_id": "starter_dashboard",
                "attr": "title",
                "old_value": "Starter Dashboard",
                "value": "Operations Dashboard",
            }
        ],
        "evidence_refs": [],
    }


def preview_intent_patch_file(
    source_path: str | Path,
    patch_path: str | Path,
    *,
    verify: bool = False,
    install: bool = False,
) -> IntentPatchPreview:
    """Read bounded regular files and preview one patch without mutating source."""

    source = Path(source_path)
    patch = Path(patch_path)
    source_text = _read_bounded_utf8_file(
        source,
        maximum=APP_BUNDLE_MAX_BYTES,
        too_large_code="PATCH_SOURCE_TOO_LARGE",
        noun="ViewSpec source",
        changed_code="PATCH_BASE_CHANGED",
    )
    patch_text = _read_bounded_utf8_file(
        patch,
        maximum=INTENT_PATCH_MAX_BYTES,
        too_large_code="PATCH_TOO_LARGE",
        noun="IntentPatch",
    )
    return preview_intent_patch(
        source_text,
        patch_text,
        verify=verify,
        install=install,
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r}")


def _strict_json_loads(text: str, *, code: str, noun: str) -> Any:
    try:
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (TypeError, ValueError) as exc:
        raise IntentPatchError(
            code,
            f"{noun} must be strict JSON: {exc}",
            f"Regenerate {noun} without duplicate keys, comments, fences, NaN, or Infinity.",
        ) from exc


def _mapping(value: object, *, code: str, noun: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IntentPatchError(code, f"{noun} must be an object.", f"Use the documented {noun} object shape.")
    return dict(value)


def _exact_fields(payload: Mapping[str, Any], allowed: frozenset[str], *, noun: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise IntentPatchError(
            "PATCH_FIELD_UNKNOWN",
            f"{noun} contains unknown field {unknown[0]!r}.",
            "Remove unknown fields; IntentPatch V1 uses a closed schema.",
        )
    missing = sorted(allowed - {"screen_id"} - set(payload))
    if missing:
        raise IntentPatchError(
            "PATCH_FIELD_REQUIRED",
            f"{noun} is missing required field {missing[0]!r}.",
            "Supply every required IntentPatch V1 field explicitly.",
        )


def _bounded_id(value: object, name: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise IntentPatchError(
            "PATCH_TARGET_INVALID",
            f"{name} must be a stable 1-128 character ViewSpec id.",
            f"Use the exact manifest-backed {name} without selectors or array indexes.",
        )
    return value


def _bounded_field(value: object, name: str) -> str:
    if not isinstance(value, str) or _FIELD_RE.fullmatch(value) is None:
        raise IntentPatchError(
            "PATCH_TARGET_INVALID",
            f"{name} must be a stable semantic field name.",
            "Use an exact JSON field name, not a path, selector, or expression.",
        )
    return value


def _json_scalar(value: object, name: str) -> object:
    if isinstance(value, (dict, list)):
        raise IntentPatchError(
            "PATCH_VALUE_INVALID",
            f"{name} must be a JSON scalar.",
            "Use a string, finite number, boolean, or null.",
        )
    if isinstance(value, str) and len(value.encode("utf-8")) > INTENT_PATCH_MAX_STRING_BYTES:
        raise IntentPatchError(
            "PATCH_VALUE_INVALID",
            f"{name} exceeds {INTENT_PATCH_MAX_STRING_BYTES} UTF-8 bytes.",
            "Shorten the scalar patch value.",
        )
    _canonical_json_bytes(value)
    return value


def _json_equal(left: object, right: object) -> bool:
    return _canonical_json_bytes(left) == _canonical_json_bytes(right)


@dataclass(frozen=True, slots=True)
class IntentPatchOperation:
    op: str
    _payload_json: str
    target_key: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return json.loads(self._payload_json)

    def inverse_json(self) -> dict[str, Any]:
        payload = self.to_json()
        if self.op in {
            "set_aesthetic_profile",
            "set_style_token",
            "set_region_layout",
            "set_binding_presentation",
            "replace_semantic_attr",
            "replace_fixture_scalar",
            "set_visibility_condition",
        }:
            payload["old_value"], payload["value"] = payload["value"], payload["old_value"]
        elif self.op == "move_region":
            payload["old_parent_id"], payload["parent_id"] = payload["parent_id"], payload["old_parent_id"]
        elif self.op == "reorder_region_children":
            payload["old_children"], payload["children"] = payload["children"], payload["old_children"]
        else:  # pragma: no cover - construction is closed above
            raise AssertionError(f"unsupported operation {self.op}")
        return payload


@dataclass(frozen=True, slots=True)
class IntentPatch:
    patch_id: str
    source_kind: str
    contract_profile: str
    base_source_sha256: str
    operations: tuple[IntentPatchOperation, ...]
    evidence_refs: tuple[str, ...]
    schema_version: int = INTENT_PATCH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if _PATCH_ID_RE.fullmatch(self.patch_id) is None:
            raise IntentPatchError("PATCH_ID_INVALID", "Patch id is invalid.", "Recompute the patch id from canonical patch bytes.")

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "contract_profile": self.contract_profile,
            "source_kind": self.source_kind,
            "base_source_sha256": self.base_source_sha256,
            "operations": [operation.to_json() for operation in self.operations],
            "evidence_refs": list(self.evidence_refs),
        }


def _operation_target_key(payload: Mapping[str, Any], source_kind: str) -> tuple[str, ...]:
    op = str(payload["op"])
    if op in _INTENT_OPERATION_KINDS:
        scope = str(payload.get("screen_id") or "root")
        prefix = ("intent", scope)
        if op == "set_aesthetic_profile":
            return (*prefix, "aesthetic_profile")
        if op == "set_style_token":
            return (*prefix, "style_token", str(payload["style_id"]))
        if op == "set_region_layout":
            return (*prefix, "region_layout", str(payload["region_id"]))
        if op == "move_region":
            return (*prefix, "region_parent", str(payload["region_id"]))
        if op == "reorder_region_children":
            return (*prefix, "region_children", str(payload["region_id"]))
        if op == "set_binding_presentation":
            return (*prefix, "binding_presentation", str(payload["binding_id"]))
        return (*prefix, "semantic_attr", str(payload["node_id"]), str(payload["attr"]))
    if op == "replace_fixture_scalar":
        return ("app", "fixture_scalar", str(payload["resource_id"]), str(payload["record_id"]), str(payload["field"]))
    return ("app", "visibility_condition", str(payload["visibility_id"]))


def _parse_operation(value: object, *, source_kind: str) -> IntentPatchOperation:
    payload = _mapping(value, code="PATCH_OPERATION_INVALID", noun="patch operation")
    op = payload.get("op")
    if not isinstance(op, str) or op not in INTENT_PATCH_OPERATION_KINDS:
        raise IntentPatchError(
            "PATCH_OPERATION_UNSUPPORTED",
            f"Unsupported patch operation {op!r}.",
            "Use one of the nine closed IntentPatch V1 operations.",
        )
    _exact_fields(payload, _OPERATION_FIELDS[op], noun=f"{op} operation")

    screen_id = payload.get("screen_id")
    if op in _INTENT_OPERATION_KINDS:
        if source_kind == "app_bundle":
            payload["screen_id"] = _bounded_id(screen_id, "screen_id")
        elif screen_id is not None:
            raise IntentPatchError(
                "PATCH_TARGET_INVALID",
                "IntentBundle operations cannot declare screen_id.",
                "Remove screen_id when patching a top-level IntentBundle.",
            )
        else:
            payload.pop("screen_id", None)
    elif screen_id is not None:
        raise IntentPatchError(
            "PATCH_FIELD_UNKNOWN",
            f"{op} does not accept screen_id.",
            "Address app-level resources and visibility rules by their stable ids.",
        )

    if op == "set_aesthetic_profile":
        old = payload["old_value"]
        new = payload["value"]
        if old is not None and old not in AESTHETIC_PROFILE_TOKENS:
            raise IntentPatchError("PATCH_VALUE_INVALID", "old aesthetic profile is unsupported.", "Use the exact current profile or null.")
        if new is not None and new not in AESTHETIC_PROFILE_TOKENS:
            raise IntentPatchError("PATCH_VALUE_INVALID", "new aesthetic profile is unsupported.", "Use a published aesthetic profile or null.")
    elif op == "set_style_token":
        _bounded_id(payload["style_id"], "style_id")
        if payload["style_id"] == "aesthetic_profile":
            raise IntentPatchError(
                "PATCH_OPERATION_INVALID",
                "aesthetic_profile must use set_aesthetic_profile.",
                "Use the profile-specific operation so profile uniqueness stays explicit.",
            )
        if payload["old_value"] not in SUPPORTED_AGENT_STYLE_TOKENS or payload["value"] not in SUPPORTED_AGENT_STYLE_TOKENS:
            raise IntentPatchError("PATCH_VALUE_INVALID", "Style token is outside the local_v1 vocabulary.", "Use a supported local style token.")
    elif op == "set_region_layout":
        _bounded_id(payload["region_id"], "region_id")
        if payload["old_value"] not in SUPPORTED_AGENT_REGION_LAYOUTS or payload["value"] not in SUPPORTED_AGENT_REGION_LAYOUTS:
            raise IntentPatchError("PATCH_VALUE_INVALID", "Region layout is outside the local_v1 vocabulary.", "Use stack, grid, or cluster.")
    elif op == "move_region":
        _bounded_id(payload["region_id"], "region_id")
        _bounded_id(payload["old_parent_id"], "old_parent_id")
        _bounded_id(payload["parent_id"], "parent_id")
        if payload["region_id"] == "root":
            raise IntentPatchError("PATCH_TARGET_INVALID", "The root region cannot be moved.", "Move a non-root region.")
    elif op == "reorder_region_children":
        _bounded_id(payload["region_id"], "region_id")
        for field in ("old_children", "children"):
            children = payload[field]
            if not isinstance(children, list) or len(children) > 32 or any(not isinstance(item, str) for item in children):
                raise IntentPatchError("PATCH_VALUE_INVALID", f"{field} must be an array of at most 32 region ids.", "Use the exact child region ids.")
            payload[field] = [_bounded_id(item, "child region id") for item in children]
            if len(set(payload[field])) != len(payload[field]):
                raise IntentPatchError("PATCH_VALUE_INVALID", f"{field} contains duplicate region ids.", "List each child exactly once.")
        if set(payload["old_children"]) != set(payload["children"]):
            raise IntentPatchError("PATCH_VALUE_INVALID", "Region reordering cannot add or remove children.", "Use identical child-id sets in old_children and children.")
    elif op == "set_binding_presentation":
        _bounded_id(payload["binding_id"], "binding_id")
        allowed = set(PRESENT_AS_TO_PRIMITIVE)
        if payload["old_value"] not in allowed or payload["value"] not in allowed:
            raise IntentPatchError("PATCH_VALUE_INVALID", "Binding presentation is outside the local_v1 vocabulary.", "Use a supported present_as value.")
    elif op == "replace_semantic_attr":
        _bounded_id(payload["node_id"], "node_id")
        _bounded_field(payload["attr"], "attr")
        _json_scalar(payload["old_value"], "old_value")
        _json_scalar(payload["value"], "value")
    elif op == "replace_fixture_scalar":
        if source_kind != "app_bundle":
            raise IntentPatchError("PATCH_OPERATION_INVALID", "Fixture operations require an AppBundle.", "Set source_kind to app_bundle and target a declared fixture resource.")
        _bounded_id(payload["resource_id"], "resource_id")
        _bounded_id(payload["record_id"], "record_id")
        _bounded_field(payload["field"], "field")
        _json_scalar(payload["old_value"], "old_value")
        _json_scalar(payload["value"], "value")
    elif op == "set_visibility_condition":
        if source_kind != "app_bundle":
            raise IntentPatchError("PATCH_OPERATION_INVALID", "Visibility operations require an AppBundle.", "Set source_kind to app_bundle and target a declared visibility rule.")
        _bounded_id(payload["visibility_id"], "visibility_id")
        if not isinstance(payload["old_value"], dict) or not isinstance(payload["value"], dict):
            raise IntentPatchError("PATCH_VALUE_INVALID", "Visibility conditions must be JSON objects.", "Use a valid bounded AppBundle visibility condition.")
        if len(_canonical_json_bytes(payload["old_value"])) > INTENT_PATCH_MAX_STRING_BYTES or len(_canonical_json_bytes(payload["value"])) > INTENT_PATCH_MAX_STRING_BYTES:
            raise IntentPatchError("PATCH_VALUE_INVALID", "Visibility condition is too large.", "Use a bounded declarative condition.")

    if op == "move_region":
        old_value, new_value = payload["old_parent_id"], payload["parent_id"]
    elif op == "reorder_region_children":
        old_value, new_value = payload["old_children"], payload["children"]
    else:
        old_value, new_value = payload["old_value"], payload["value"]
    if _json_equal(old_value, new_value):
        raise IntentPatchError(
            "PATCH_NO_EFFECT",
            f"{op} does not change its target value.",
            "Remove no-op operations; every approved operation must produce one semantic change.",
        )

    canonical = _canonical_json_bytes(payload).decode("utf-8")
    return IntentPatchOperation(op=op, _payload_json=canonical, target_key=_operation_target_key(payload, source_kind))


def parse_intent_patch(value: str | Mapping[str, Any] | IntentPatch) -> IntentPatch:
    if isinstance(value, IntentPatch):
        return value
    if isinstance(value, str):
        if len(value.encode("utf-8")) > INTENT_PATCH_MAX_BYTES:
            raise IntentPatchError(
                "PATCH_TOO_LARGE",
                f"IntentPatch exceeds {INTENT_PATCH_MAX_BYTES} UTF-8 bytes.",
                "Reduce the patch to at most 64 operations and 64 KiB.",
            )
        raw = _strict_json_loads(value, code="PATCH_INVALID_JSON", noun="IntentPatch")
    elif isinstance(value, Mapping):
        encoded = _canonical_json_bytes(value)
        if len(encoded) > INTENT_PATCH_MAX_BYTES:
            raise IntentPatchError("PATCH_TOO_LARGE", "IntentPatch exceeds 64 KiB.", "Split the patch into smaller approved transactions.")
        raw = _strict_json_loads(encoded.decode("utf-8"), code="PATCH_INVALID_JSON", noun="IntentPatch")
    else:
        raise IntentPatchError("PATCH_INVALID_JSON", "IntentPatch must be JSON text or an object.", "Pass a strict IntentPatch V1 object.")
    payload = _mapping(raw, code="PATCH_INVALID_JSON", noun="IntentPatch")
    _exact_fields(payload, _TOP_LEVEL_FIELDS, noun="IntentPatch")
    if payload["schema_version"] != INTENT_PATCH_SCHEMA_VERSION:
        raise IntentPatchError("PATCH_SCHEMA_UNSUPPORTED", "Unsupported IntentPatch schema_version.", "Use schema_version 1.")
    if payload["contract_profile"] != INTENT_PATCH_CONTRACT_PROFILE:
        raise IntentPatchError("PATCH_PROFILE_UNSUPPORTED", "Unsupported IntentPatch contract_profile.", "Use local_v1.")
    source_kind = payload["source_kind"]
    if source_kind not in INTENT_PATCH_SOURCE_KINDS:
        raise IntentPatchError("PATCH_SOURCE_KIND_UNSUPPORTED", "Unsupported IntentPatch source_kind.", "Use intent_bundle or app_bundle.")
    base_hash = payload["base_source_sha256"]
    if not isinstance(base_hash, str) or _SHA256_RE.fullmatch(base_hash) is None:
        raise IntentPatchError("PATCH_BASE_HASH_INVALID", "base_source_sha256 is invalid.", "Hash the exact UTF-8 source bytes with SHA-256.")
    raw_operations = payload["operations"]
    if not isinstance(raw_operations, list) or not 1 <= len(raw_operations) <= INTENT_PATCH_MAX_OPERATIONS:
        raise IntentPatchError(
            "PATCH_OPERATION_LIMIT_EXCEEDED",
            f"IntentPatch requires 1 through {INTENT_PATCH_MAX_OPERATIONS} operations.",
            "Split larger changes into separately approved transactions.",
        )
    operations = tuple(_parse_operation(item, source_kind=source_kind) for item in raw_operations)
    target_keys = [operation.target_key for operation in operations]
    if len(set(target_keys)) != len(target_keys):
        raise IntentPatchError(
            "PATCH_TARGET_CONFLICT",
            "IntentPatch writes the same semantic field more than once.",
            "Collapse conflicting writes into one operation with one old-value precondition.",
        )
    raw_refs = payload["evidence_refs"]
    if not isinstance(raw_refs, list) or len(raw_refs) > INTENT_PATCH_MAX_EVIDENCE_REFS:
        raise IntentPatchError("PATCH_EVIDENCE_INVALID", "evidence_refs must contain at most 64 values.", "Use bounded Review or verifier evidence identities.")
    evidence_refs: list[str] = []
    for item in raw_refs:
        if (
            not isinstance(item, str)
            or len(item.encode("utf-8")) > INTENT_PATCH_MAX_EVIDENCE_REF_BYTES
            or _EVIDENCE_RE.fullmatch(item) is None
            or ".." in item
        ):
            raise IntentPatchError("PATCH_EVIDENCE_INVALID", "Patch evidence reference is invalid.", "Use a bounded canonical Review or verifier identity.")
        evidence_refs.append(item)
    if len(set(evidence_refs)) != len(evidence_refs):
        raise IntentPatchError("PATCH_EVIDENCE_INVALID", "Patch evidence references must be unique.", "Deduplicate evidence references.")
    canonical_payload = {
        "schema_version": INTENT_PATCH_SCHEMA_VERSION,
        "contract_profile": INTENT_PATCH_CONTRACT_PROFILE,
        "source_kind": source_kind,
        "base_source_sha256": base_hash,
        "operations": [operation.to_json() for operation in operations],
        "evidence_refs": evidence_refs,
    }
    patch_id = f"vpatch_{_canonical_json_sha256({'type': 'viewspec_intent_patch_v1', 'patch': canonical_payload})[:32]}"
    return IntentPatch(
        patch_id=patch_id,
        source_kind=source_kind,
        contract_profile=INTENT_PATCH_CONTRACT_PROFILE,
        base_source_sha256=base_hash,
        operations=operations,
        evidence_refs=tuple(evidence_refs),
    )


def _validate_source_text(text: str, source_kind: str) -> dict[str, Any]:
    size = len(text.encode("utf-8"))
    maximum = 256 * 1024 if source_kind == "intent_bundle" else APP_BUNDLE_MAX_BYTES
    if size > maximum:
        raise IntentPatchError("PATCH_SOURCE_TOO_LARGE", f"Source is {size} bytes; limit is {maximum}.", "Split the source before patching it.")
    payload = _strict_json_loads(text, code="PATCH_SOURCE_INVALID", noun="ViewSpec source")
    if not isinstance(payload, dict):
        raise IntentPatchError("PATCH_SOURCE_INVALID", "ViewSpec source root must be an object.", "Use an IntentBundle or AppBundle object.")
    detected = "app_bundle" if {"app", "screens", "routes"}.issubset(payload) else "intent_bundle" if {"substrate", "view_spec"}.issubset(payload) else None
    if detected != source_kind:
        raise IntentPatchError(
            "PATCH_SOURCE_KIND_MISMATCH",
            f"Patch declares {source_kind}, but source shape is {detected or 'unknown'}.",
            "Regenerate the patch for the exact source kind.",
        )
    validation = validate_intent_text(text, compile_check=True) if source_kind == "intent_bundle" else validate_app_text(text, compile_check=True)
    if not validation.get("ok"):
        issues = validation.get("issues") if isinstance(validation.get("issues"), list) else []
        first = issues[0] if issues and isinstance(issues[0], dict) else {}
        raise IntentPatchError(
            "PATCH_SOURCE_INVALID",
            f"Base source failed validation: {first.get('code') or 'unknown validation error'}.",
            str(first.get("suggestion") or "Fix the source before applying a semantic patch."),
        )
    return payload


def _find_one(items: object, target_id: str, *, noun: str) -> dict[str, Any]:
    if not isinstance(items, list):
        raise IntentPatchError("PATCH_TARGET_MISSING", f"Source has no {noun} collection.", "Patch only declared stable ids.")
    matches = [item for item in items if isinstance(item, dict) and item.get("id") == target_id]
    if len(matches) != 1:
        code = "PATCH_TARGET_AMBIGUOUS" if len(matches) > 1 else "PATCH_TARGET_MISSING"
        raise IntentPatchError(code, f"Expected exactly one {noun} {target_id!r}; found {len(matches)}.", "Regenerate the patch from the current validated source.")
    return matches[0]


def _intent_payload(payload: dict[str, Any], operation: IntentPatchOperation, source_kind: str) -> dict[str, Any]:
    op = operation.to_json()
    if source_kind == "intent_bundle":
        return payload
    screen = _find_one(payload.get("screens"), str(op.get("screen_id")), noun="screen")
    intent = screen.get("intent_bundle")
    if not isinstance(intent, dict):
        raise IntentPatchError("PATCH_TARGET_MISSING", "Target screen has no IntentBundle.", "Choose a screen with a declared intent_bundle.")
    return intent


def _require_old(actual: object, expected: object, *, target: tuple[str, ...]) -> None:
    if not _json_equal(actual, expected):
        raise IntentPatchError(
            "PATCH_PRECONDITION_FAILED",
            f"Old-value precondition failed for {'/'.join(target)}.",
            "Re-read the current source and regenerate the patch; never overwrite a changed value.",
        )


def _apply_operation(payload: dict[str, Any], operation: IntentPatchOperation, source_kind: str) -> None:
    op = operation.to_json()
    kind = operation.op
    if kind in _INTENT_OPERATION_KINDS:
        intent = _intent_payload(payload, operation, source_kind)
        view = intent.get("view_spec")
        substrate = intent.get("substrate")
        if not isinstance(view, dict) or not isinstance(substrate, dict):
            raise IntentPatchError("PATCH_TARGET_MISSING", "Target IntentBundle is incomplete.", "Patch a validated IntentBundle.")
        if kind == "set_aesthetic_profile":
            styles = view.get("styles")
            if not isinstance(styles, list):
                raise IntentPatchError("PATCH_TARGET_MISSING", "IntentBundle has no styles array.", "Patch a validated IntentBundle.")
            matches = [item for item in styles if isinstance(item, dict) and (item.get("id") == "aesthetic_profile" or str(item.get("token", "")).startswith("aesthetic."))]
            if len(matches) > 1:
                raise IntentPatchError("PATCH_TARGET_AMBIGUOUS", "IntentBundle contains multiple aesthetic profiles.", "Fix the invalid source before patching.")
            actual = matches[0].get("token") if matches else None
            _require_old(actual, op["old_value"], target=operation.target_key)
            if op["value"] is None:
                styles[:] = [item for item in styles if item not in matches]
            elif matches:
                matches[0]["token"] = op["value"]
            else:
                styles.append({"id": "aesthetic_profile", "target": f"view:{view.get('id')}", "token": op["value"]})
            return
        if kind == "set_style_token":
            style = _find_one(view.get("styles"), op["style_id"], noun="style")
            _require_old(style.get("token"), op["old_value"], target=operation.target_key)
            style["token"] = op["value"]
            return
        if kind in {"set_region_layout", "move_region"}:
            region = _find_one(view.get("regions"), op["region_id"], noun="region")
            field = "layout" if kind == "set_region_layout" else "parent_region"
            old_field = "old_value" if kind == "set_region_layout" else "old_parent_id"
            value_field = "value" if kind == "set_region_layout" else "parent_id"
            _require_old(region.get(field), op[old_field], target=operation.target_key)
            region[field] = op[value_field]
            return
        if kind == "reorder_region_children":
            regions = view.get("regions")
            if not isinstance(regions, list):
                raise IntentPatchError("PATCH_TARGET_MISSING", "IntentBundle has no regions array.", "Patch a validated IntentBundle.")
            actual_children = [str(item.get("id")) for item in regions if isinstance(item, dict) and item.get("parent_region") == op["region_id"]]
            _require_old(actual_children, op["old_children"], target=operation.target_key)
            children_by_id = {str(item.get("id")): item for item in regions if isinstance(item, dict) and item.get("parent_region") == op["region_id"]}
            ordered = iter([children_by_id[item] for item in op["children"]])
            regions[:] = [next(ordered) if isinstance(item, dict) and item.get("parent_region") == op["region_id"] else item for item in regions]
            return
        if kind == "set_binding_presentation":
            binding = _find_one(view.get("bindings"), op["binding_id"], noun="binding")
            _require_old(binding.get("present_as"), op["old_value"], target=operation.target_key)
            binding["present_as"] = op["value"]
            return
        nodes = substrate.get("nodes")
        if not isinstance(nodes, dict) or op["node_id"] not in nodes or not isinstance(nodes[op["node_id"]], dict):
            raise IntentPatchError("PATCH_TARGET_MISSING", f"Semantic node {op['node_id']!r} is missing.", "Use a stable node id from the current source.")
        attrs = nodes[op["node_id"]].get("attrs")
        if not isinstance(attrs, dict):
            raise IntentPatchError("PATCH_TARGET_MISSING", "Semantic node has no attrs object.", "Patch a declared semantic attribute.")
        if op["attr"] not in attrs:
            raise IntentPatchError(
                "PATCH_TARGET_MISSING",
                f"Semantic attribute {op['attr']!r} is not declared.",
                "IntentPatch V1 can replace existing scalar attributes but cannot add or delete fields.",
            )
        actual = attrs.get(op["attr"])
        _require_old(actual, op["old_value"], target=operation.target_key)
        attrs[op["attr"]] = op["value"]
        return

    if kind == "replace_fixture_scalar":
        resource = _find_one(payload.get("resources"), op["resource_id"], noun="resource")
        record = _find_one(resource.get("records"), op["record_id"], noun="fixture record")
        if op["field"] not in record:
            raise IntentPatchError(
                "PATCH_TARGET_MISSING",
                f"Fixture field {op['field']!r} is not declared.",
                "IntentPatch V1 can replace existing scalar fixture fields but cannot add or delete fields.",
            )
        _require_old(record.get(op["field"]), op["old_value"], target=operation.target_key)
        record[op["field"]] = op["value"]
        return
    visibility = _find_one(payload.get("visibility"), op["visibility_id"], noun="visibility rule")
    _require_old(visibility.get("when"), op["old_value"], target=operation.target_key)
    visibility["when"] = op["value"]


def _candidate_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"


def _compile_check_candidate(candidate_text: str, source_kind: str) -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="viewspec-patch-preview-") as directory:
        root = Path(directory)
        source_name = "viewspec.intent.json" if source_kind == "intent_bundle" else "viewspec.app.json"
        source = root / source_name
        source.write_text(candidate_text, encoding="utf-8")
        output = root / "artifact"
        if source_kind == "intent_bundle":
            result = compile_intent_bundle_file_tool(source, output, target="html-tailwind", cwd=root, allow_outside_cwd=False)
            ok = result.get("ok") is True
            target = "html-tailwind"
        else:
            result = compile_app(source, out_dir=output, target="html-tailwind-app", force=True, cwd=root)
            ok = result.get("ok") is True
            target = "html-tailwind-app"
        if not ok:
            errors = result.get("errors") if isinstance(result.get("errors"), list) else []
            first = errors[0] if errors and isinstance(errors[0], dict) else {}
            raise IntentPatchError(
                "PATCH_COMPILE_FAILED",
                f"Candidate compile/check failed: {first.get('code') or 'unknown error'}.",
                str(first.get("fix") or "Fix the semantic patch and preview it again."),
            )
        return {"status": "passed", "target": target, "artifact_check": "passed"}


def _inverse_patch(patch: IntentPatch, candidate_hash: str) -> IntentPatch:
    payload = {
        "schema_version": INTENT_PATCH_SCHEMA_VERSION,
        "contract_profile": patch.contract_profile,
        "source_kind": patch.source_kind,
        "base_source_sha256": candidate_hash,
        "operations": [operation.inverse_json() for operation in reversed(patch.operations)],
        "evidence_refs": [f"patch:{patch.patch_id}"],
    }
    return parse_intent_patch(payload)


@dataclass(frozen=True, slots=True)
class IntentPatchPreview:
    preview_id: str
    patch: IntentPatch
    base_source_sha256: str
    candidate_source_sha256: str
    candidate_text: str
    semantic_diff: dict[str, Any]
    compile_check: dict[str, str]
    verification: dict[str, Any]
    inverse_patch: IntentPatch
    approval_token: str

    def to_json(self, *, include_candidate: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": 1,
            "preview_id": self.preview_id,
            "patch_id": self.patch.patch_id,
            "source_kind": self.patch.source_kind,
            "base_source_sha256": self.base_source_sha256,
            "candidate_source_sha256": self.candidate_source_sha256,
            "semantic_diff": self.semantic_diff,
            "compile_check": self.compile_check,
            "verification": self.verification,
            "inverse_patch": self.inverse_patch.to_json(),
            "approval_token": self.approval_token,
        }
        if include_candidate:
            result["candidate_source"] = self.candidate_text
        return result


def preview_intent_patch(
    source_text: str,
    patch_value: str | Mapping[str, Any] | IntentPatch,
    *,
    verify: bool = False,
    install: bool = False,
) -> IntentPatchPreview:
    patch = parse_intent_patch(patch_value)
    actual_hash = source_sha256(source_text)
    if actual_hash != patch.base_source_sha256:
        raise IntentPatchError(
            "PATCH_BASE_CHANGED",
            "Source bytes no longer match base_source_sha256.",
            "Re-read the source and regenerate the patch; stale patches are never rebased automatically.",
        )
    base_payload = _validate_source_text(source_text, patch.source_kind)
    candidate_payload = json.loads(json.dumps(base_payload, ensure_ascii=False, allow_nan=False))
    for operation in patch.operations:
        _apply_operation(candidate_payload, operation, patch.source_kind)
    candidate_text = _candidate_text(candidate_payload)
    candidate_hash = source_sha256(candidate_text)
    if _canonical_json_bytes(base_payload) == _canonical_json_bytes(candidate_payload):
        raise IntentPatchError("PATCH_NO_EFFECT", "IntentPatch produces no semantic change.", "Remove no-op operations or choose a different value.")
    _validate_source_text(candidate_text, patch.source_kind)
    semantic_diff = diff_intent_text(source_text, candidate_text, compile_check=True) if patch.source_kind == "intent_bundle" else diff_app_text(source_text, candidate_text, compile_check=True)
    if semantic_diff.get("ok") is not True:
        raise IntentPatchError("PATCH_DIFF_FAILED", "Candidate semantic diff failed.", "Fix the candidate source and retry preview.")
    compile_check = _compile_check_candidate(candidate_text, patch.source_kind)
    verification: dict[str, Any] = {"status": "not_run"}
    if verify:
        verification = _verify_candidate(candidate_text, patch.source_kind, install=install)
        if verification.get("status") != "conformant":
            raise IntentPatchError(
                "PATCH_VERIFICATION_FAILED",
                f"Candidate verification is {verification.get('status')}.",
                "Repair the candidate until verification is conformant before applying this verified preview.",
            )
    inverse = _inverse_patch(patch, candidate_hash)
    verification_identity = {
        key: verification[key]
        for key in ("status", "target", "verification_id", "diagnostic_codes")
        if key in verification
    }
    preview_material = {
        "type": "viewspec_intent_patch_preview_v1",
        "patch_id": patch.patch_id,
        "base_source_sha256": actual_hash,
        "candidate_source_sha256": candidate_hash,
        "semantic_diff_sha256": _canonical_json_sha256(semantic_diff),
        "compile_check": compile_check,
        # Browser evidence bytes can include runtime timings, so result_sha256 may
        # legitimately differ across identical proofs. Approval binds to the stable
        # verification identity and the exact candidate source instead.
        "verification": verification_identity,
        "inverse_patch_id": inverse.patch_id,
    }
    preview_id = f"vpv_{_canonical_json_sha256(preview_material)[:32]}"
    approval_token = f"vapprove_{_canonical_json_sha256({'type': 'viewspec_patch_approval_v1', 'preview_id': preview_id, 'candidate_source_sha256': candidate_hash})}"
    return IntentPatchPreview(
        preview_id=preview_id,
        patch=patch,
        base_source_sha256=actual_hash,
        candidate_source_sha256=candidate_hash,
        candidate_text=candidate_text,
        semantic_diff=semantic_diff,
        compile_check=compile_check,
        verification=verification,
        inverse_patch=inverse,
        approval_token=approval_token,
    )


def _verify_candidate(candidate_text: str, source_kind: str, *, install: bool) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="viewspec-patch-verify-") as directory:
        root = Path(directory)
        source = root / ("viewspec.intent.json" if source_kind == "intent_bundle" else "viewspec.app.json")
        source.write_text(candidate_text, encoding="utf-8")
        if source_kind == "app_bundle":
            from viewspec.app_bundle import prove_app

            report = prove_app(
                app_path=source,
                out_dir=root / "proof",
                target="react-tailwind-app",
                install=install,
                force=True,
                cwd=root,
            )
            status = "conformant" if report.get("ok") is True else "nonconformant"
            codes = sorted(
                {
                    str(item.get("code"))
                    for item in report.get("errors", [])
                    if isinstance(item, dict) and item.get("code")
                }
            )
            return {"status": status, "target": "react-tailwind-app", "diagnostic_codes": codes}
        artifact = root / "artifact"
        compiled = compile_intent_bundle_file_tool(source, artifact, target="react-tailwind-tsx", cwd=root)
        if compiled.get("ok") is not True:
            return {"status": "nonconformant", "target": "react-tailwind-tsx", "diagnostic_codes": ["PATCH_COMPILE_FAILED"]}
        from viewspec.local_verify import verify_local_artifact

        result = verify_local_artifact(
            artifact,
            evidence_dir=root / "evidence",
            install=install,
        )
        return {
            "status": result.status,
            "target": "react-tailwind-tsx",
            "verification_id": result.verification_id,
            "result_sha256": result.result_sha256,
            "diagnostic_codes": sorted({item.code for item in result.diagnostics}),
        }


@dataclass(frozen=True, slots=True)
class IntentPatchReceipt:
    status: str
    receipt_id: str
    preview_id: str
    patch_id: str
    approval_token: str
    base_source_sha256: str
    candidate_source_sha256: str
    inverse_patch: IntentPatch
    receipt_path: Path

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "receipt_id": self.receipt_id,
            "preview_id": self.preview_id,
            "patch_id": self.patch_id,
            "approval_token": self.approval_token,
            "base_source_sha256": self.base_source_sha256,
            "candidate_source_sha256": self.candidate_source_sha256,
            "inverse_patch": self.inverse_patch.to_json(),
        }


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def _write_receipt(path: Path, payload: Mapping[str, Any]) -> None:
    _durable_write_bytes(path, json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")


def _receipt_from_payload(path: Path, payload: Mapping[str, Any]) -> IntentPatchReceipt:
    inverse = parse_intent_patch(_mapping(payload.get("inverse_patch"), code="PATCH_RECEIPT_INVALID", noun="inverse patch"))
    return IntentPatchReceipt(
        status=str(payload.get("status")),
        receipt_id=str(payload.get("receipt_id")),
        preview_id=str(payload.get("preview_id")),
        patch_id=str(payload.get("patch_id")),
        approval_token=str(payload.get("approval_token")),
        base_source_sha256=str(payload.get("base_source_sha256")),
        candidate_source_sha256=str(payload.get("candidate_source_sha256")),
        inverse_patch=inverse,
        receipt_path=path,
    )


def _receipt_paths(source_path: Path) -> tuple[Path, list[Path]]:
    receipt_dir = source_path.parent / INTENT_PATCH_RECEIPT_DIR
    if receipt_dir.is_symlink():
        raise IntentPatchError(
            "PATCH_PATH_INVALID",
            "Patch receipt directory cannot be a symlink.",
            f"Replace {INTENT_PATCH_RECEIPT_DIR} with a private local directory.",
            cli_exit=1,
        )
    if receipt_dir.exists() and not receipt_dir.is_dir():
        raise IntentPatchError(
            "PATCH_PATH_INVALID",
            "Patch receipt path exists but is not a directory.",
            f"Replace {INTENT_PATCH_RECEIPT_DIR} with a private local directory.",
            cli_exit=1,
        )
    paths: list[Path] = []
    if receipt_dir.is_dir():
        for path in receipt_dir.iterdir():
            if path.name.startswith("vpv_") and path.name.endswith(".json"):
                paths.append(path)
                if len(paths) > 512:
                    raise IntentPatchError(
                        "PATCH_RECEIPT_LIMIT_EXCEEDED",
                        "Patch receipt directory exceeds 512 entries.",
                        "Archive old receipts before applying another patch.",
                    )
        paths.sort()
    return receipt_dir, paths


@contextmanager
def _source_transaction_lock(source_path: Path):
    """Serialize all read/preview/replace steps for one source across processes."""

    receipt_dir, _ = _receipt_paths(source_path)
    receipt_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = receipt_dir / f".{source_path.name}.lock"
    if lock_path.is_symlink():
        raise IntentPatchError(
            "PATCH_PATH_INVALID",
            "Patch transaction lock cannot be a symlink.",
            "Remove the unsafe lock and retry.",
            cli_exit=1,
        )
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise IntentPatchError(
            "PATCH_PATH_INVALID",
            f"Cannot open patch transaction lock: {exc}",
            "Repair the private receipt directory and retry.",
            cli_exit=1,
        ) from exc
    acquired = False
    deadline = time.monotonic() + INTENT_PATCH_LOCK_TIMEOUT_SECONDS
    try:
        if os.name == "nt":  # pragma: no cover - exercised on Windows CI
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            while True:
                try:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.01)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.01)
        if not acquired:
            raise IntentPatchError(
                "PATCH_LOCK_TIMEOUT",
                f"Another patch transaction held the source lock for {INTENT_PATCH_LOCK_TIMEOUT_SECONDS:g} seconds.",
                "Retry after the active transaction finishes; never apply concurrently.",
                cli_exit=1,
            )
        yield
    finally:
        if acquired:
            if os.name == "nt":  # pragma: no cover - exercised on Windows CI
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _read_receipt(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise IntentPatchError(
            "PATCH_RECEIPT_INVALID",
            "Patch receipt must be a regular, non-symlink file.",
            "Repair receipt storage before retrying.",
            cli_exit=1,
        )
    try:
        size = path.stat(follow_symlinks=False).st_size
        if size > INTENT_PATCH_MAX_RECEIPT_BYTES:
            raise IntentPatchError(
                "PATCH_RECEIPT_INVALID",
                f"Patch receipt exceeds {INTENT_PATCH_MAX_RECEIPT_BYTES} bytes.",
                "Archive the malformed receipt before retrying.",
                cli_exit=1,
            )
        payload = _strict_json_loads(
            path.read_text(encoding="utf-8"),
            code="PATCH_RECEIPT_INVALID",
            noun="patch receipt",
        )
    except IntentPatchError:
        raise
    except (OSError, UnicodeError) as exc:
        raise IntentPatchError(
            "PATCH_RECEIPT_INVALID",
            f"Cannot read patch receipt: {exc}",
            "Repair receipt storage before retrying.",
            cli_exit=1,
        ) from exc
    return _mapping(payload, code="PATCH_RECEIPT_INVALID", noun="patch receipt")


def _receipt_backup_path(source_path: Path, payload: Mapping[str, Any], receipt_path: Path) -> Path:
    preview_id = payload.get("preview_id")
    if (
        not isinstance(preview_id, str)
        or _PREVIEW_ID_RE.fullmatch(preview_id) is None
        or receipt_path.name != f"{preview_id}.json"
    ):
        raise IntentPatchError(
            "PATCH_RECEIPT_INVALID",
            "Patch receipt preview identity is invalid.",
            "Repair or archive the malformed receipt before retrying.",
            cli_exit=1,
        )
    return source_path.parent / f".{source_path.name}.{preview_id}.backup"


def _validated_receipt_hash(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise IntentPatchError(
            "PATCH_RECEIPT_INVALID",
            f"Patch receipt {field} is invalid.",
            "Repair or archive the malformed receipt before retrying.",
            cli_exit=1,
        )
    return value


def _recover_interrupted_receipts(source_path: Path) -> None:
    """Resolve only hash-provable transaction states; reject every third state."""

    _, paths = _receipt_paths(source_path)
    records: list[tuple[Path, dict[str, Any], Path]] = []
    for path in paths:
        payload = _read_receipt(path)
        if payload.get("source_file") != source_path.name:
            continue
        status = payload.get("status")
        if status not in {"prepared", "applied", "aborted"}:
            raise IntentPatchError(
                "PATCH_RECEIPT_INVALID",
                "Patch receipt status is invalid.",
                "Repair or archive the malformed receipt before retrying.",
                cli_exit=1,
            )
        records.append((path, payload, _receipt_backup_path(source_path, payload, path)))
    prepared = [record for record in records if record[1]["status"] == "prepared"]
    if len(prepared) > 1:
        raise IntentPatchError(
            "PATCH_RECOVERY_REQUIRED",
            "Multiple prepared patch transactions target the same source.",
            "Inspect the receipts and backups; retain exactly one provable transaction state.",
            cli_exit=1,
        )
    try:
        current_hash = source_sha256(source_path.read_bytes())
    except OSError as exc:
        raise IntentPatchError(
            "PATCH_RECOVERY_REQUIRED",
            f"Cannot hash source during patch recovery: {exc}",
            "Restore the source file before retrying.",
            cli_exit=1,
        ) from exc
    for path, payload, backup_path in records:
        if not backup_path.exists():
            continue
        if backup_path.is_symlink() or not backup_path.is_file():
            raise IntentPatchError(
                "PATCH_RECOVERY_REQUIRED",
                "Patch backup must be a regular, non-symlink file.",
                f"Restore {source_path.name} manually and remove the unsafe backup.",
                cli_exit=1,
            )
        base_hash = _validated_receipt_hash(payload, "base_source_sha256")
        candidate_hash = _validated_receipt_hash(payload, "candidate_source_sha256")
        try:
            backup_hash = source_sha256(backup_path.read_bytes())
        except OSError as exc:
            raise IntentPatchError(
                "PATCH_RECOVERY_REQUIRED",
                f"Cannot read patch backup: {exc}",
                "Restore the source manually before retrying.",
                cli_exit=1,
            ) from exc
        if backup_hash != base_hash or current_hash not in {base_hash, candidate_hash}:
            raise IntentPatchError(
                "PATCH_RECOVERY_REQUIRED",
                "Interrupted patch state does not match its recorded base or candidate hash.",
                "Inspect the source, receipt, and backup manually; no file was overwritten.",
                cli_exit=1,
            )
        if payload["status"] == "applied" and current_hash != candidate_hash:
            raise IntentPatchError(
                "PATCH_RECOVERY_REQUIRED",
                "Applied patch receipt conflicts with the current source hash.",
                "Inspect the source, receipt, and backup manually; no file was overwritten.",
                cli_exit=1,
            )
        if payload["status"] == "aborted" and current_hash != base_hash:
            raise IntentPatchError(
                "PATCH_RECOVERY_REQUIRED",
                "Aborted patch receipt conflicts with the current source hash.",
                "Inspect the source, receipt, and backup manually; no file was overwritten.",
                cli_exit=1,
            )
        if payload["status"] != "prepared":
            backup_path.unlink()
            _fsync_directory(source_path.parent)
    if not prepared:
        return
    path, payload, backup_path = prepared[0]
    base_hash = _validated_receipt_hash(payload, "base_source_sha256")
    candidate_hash = _validated_receipt_hash(payload, "candidate_source_sha256")
    if current_hash == candidate_hash:
        if not backup_path.is_file() or source_sha256(backup_path.read_bytes()) != base_hash:
            raise IntentPatchError(
                "PATCH_RECOVERY_REQUIRED",
                "Candidate source lacks its exact recorded base backup.",
                "Inspect the source and receipt manually; no file was overwritten.",
                cli_exit=1,
            )
        _write_receipt(path, {**payload, "status": "applied"})
        backup_path.unlink()
        _fsync_directory(source_path.parent)
        return
    if current_hash == base_hash:
        if backup_path.exists():
            if backup_path.is_symlink() or not backup_path.is_file() or source_sha256(backup_path.read_bytes()) != base_hash:
                raise IntentPatchError(
                    "PATCH_RECOVERY_REQUIRED",
                    "Prepared patch backup does not match its recorded base hash.",
                    "Inspect the source, receipt, and backup manually; no file was overwritten.",
                    cli_exit=1,
                )
            backup_path.unlink()
            _fsync_directory(source_path.parent)
        _write_receipt(path, {**payload, "status": "aborted"})
        return
    raise IntentPatchError(
        "PATCH_RECOVERY_REQUIRED",
        "Prepared patch transaction is in an unrecognized source state.",
        "Inspect the source, receipt, and backup manually; no file was overwritten.",
        cli_exit=1,
    )


def _existing_applied_receipt(source_path: Path, patch: IntentPatch, approval_token: str, current_hash: str) -> IntentPatchReceipt | None:
    _, paths = _receipt_paths(source_path)
    for path in paths:
        payload = _read_receipt(path)
        if payload.get("source_file") != source_path.name:
            continue
        if (
            payload.get("status") == "applied"
            and payload.get("patch_id") == patch.patch_id
            and payload.get("approval_token") == approval_token
            and payload.get("candidate_source_sha256") == current_hash
        ):
            return _receipt_from_payload(path, payload)
    return None


def apply_intent_patch_file(
    source_path: str | Path,
    patch_path: str | Path,
    *,
    approval_token: str,
    verify: bool = False,
    install: bool = False,
) -> IntentPatchReceipt:
    source = Path(source_path)
    patch_file = Path(patch_path)
    if source.is_symlink() or patch_file.is_symlink() or not source.is_file() or not patch_file.is_file():
        raise IntentPatchError("PATCH_PATH_INVALID", "Patch apply requires regular, non-symlink source and patch files.", "Use local regular files.")
    with _source_transaction_lock(source):
        return _apply_intent_patch_file_locked(
            source,
            patch_file,
            approval_token=approval_token,
            verify=verify,
            install=install,
        )


def _apply_intent_patch_file_locked(
    source: Path,
    patch_file: Path,
    *,
    approval_token: str,
    verify: bool,
    install: bool,
) -> IntentPatchReceipt:
    source_text = _read_bounded_utf8_file(
        source,
        maximum=APP_BUNDLE_MAX_BYTES,
        too_large_code="PATCH_SOURCE_TOO_LARGE",
        noun="ViewSpec source",
        changed_code="PATCH_BASE_CHANGED",
    )
    patch_text = _read_bounded_utf8_file(
        patch_file,
        maximum=INTENT_PATCH_MAX_BYTES,
        too_large_code="PATCH_TOO_LARGE",
        noun="IntentPatch",
    )
    patch = parse_intent_patch(patch_text)
    _recover_interrupted_receipts(source)
    source_text = _read_bounded_utf8_file(
        source,
        maximum=APP_BUNDLE_MAX_BYTES,
        too_large_code="PATCH_SOURCE_TOO_LARGE",
        noun="ViewSpec source after transaction recovery",
        changed_code="PATCH_BASE_CHANGED",
    )
    current_hash = source_sha256(source_text)
    existing = _existing_applied_receipt(source, patch, approval_token, current_hash)
    if existing is not None:
        return existing
    preview = preview_intent_patch(source_text, patch, verify=verify, install=install)
    if not isinstance(approval_token, str) or _APPROVAL_RE.fullmatch(approval_token) is None or approval_token != preview.approval_token:
        raise IntentPatchError(
            "PATCH_APPROVAL_INVALID",
            "Approval token does not authorize this exact preview.",
            "Preview the current source and pass its exact approval_token only after user approval.",
        )
    _assert_source_hash(source, preview.base_source_sha256)
    if _PREVIEW_ID_RE.fullmatch(preview.preview_id) is None:  # pragma: no cover - derived internally
        raise IntentPatchError("PATCH_PREVIEW_INVALID", "Preview id is invalid.", "Regenerate the preview.", cli_exit=1)
    receipt_dir, _ = _receipt_paths(source)
    receipt_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    receipt_path = receipt_dir / f"{preview.preview_id}.json"
    backup_path = source.parent / f".{source.name}.{preview.preview_id}.backup"
    if backup_path.exists():
        raise IntentPatchError("PATCH_RECOVERY_REQUIRED", "A patch backup already exists for this preview.", "Recover or remove the incomplete transaction before retrying.", cli_exit=1)
    receipt_id = f"vreceipt_{_canonical_json_sha256({'type': 'viewspec_patch_receipt_v1', 'preview_id': preview.preview_id, 'approval_token': approval_token})[:32]}"
    base_receipt: dict[str, Any] = {
        "schema_version": 1,
        "status": "prepared",
        "receipt_id": receipt_id,
        "preview_id": preview.preview_id,
        "patch_id": patch.patch_id,
        "approval_token": approval_token,
        "source_file": source.name,
        "base_source_sha256": preview.base_source_sha256,
        "candidate_source_sha256": preview.candidate_source_sha256,
        "semantic_diff_sha256": _canonical_json_sha256(preview.semantic_diff),
        "compile_check": preview.compile_check,
        "verification": preview.verification,
        "inverse_patch": preview.inverse_patch.to_json(),
    }
    source_replaced = False
    try:
        _write_receipt(receipt_path, base_receipt)
        _durable_write_bytes(backup_path, source_text.encode("utf-8"))
        _assert_source_hash(source, preview.base_source_sha256)
        _durable_write_bytes(source, preview.candidate_text.encode("utf-8"))
        source_replaced = True
        applied_receipt = {**base_receipt, "status": "applied"}
        _write_receipt(receipt_path, applied_receipt)
        backup_path.unlink(missing_ok=True)
        _fsync_directory(source.parent)
        return _receipt_from_payload(receipt_path, applied_receipt)
    except Exception as exc:
        rollback_error: Exception | None = None
        if source_replaced and backup_path.is_file():
            try:
                os.replace(backup_path, source)
                _fsync_directory(source.parent)
            except Exception as rollback_exc:  # pragma: no cover - catastrophic filesystem failure
                rollback_error = rollback_exc
        else:
            backup_path.unlink(missing_ok=True)
        try:
            _write_receipt(receipt_path, {**base_receipt, "status": "aborted"})
        except Exception:
            pass
        if rollback_error is not None:
            raise IntentPatchError(
                "PATCH_RECOVERY_REQUIRED",
                f"Patch apply and rollback both failed: {rollback_error}",
                f"Restore {source.name} from {backup_path.name} before any further patch operation.",
                cli_exit=1,
            ) from exc
        raise IntentPatchError(
            "PATCH_APPLY_FAILED",
            f"Patch transaction failed and source was left unchanged: {exc}",
            "Fix filesystem or receipt storage and retry the exact approved patch.",
            cli_exit=1,
        ) from exc


__all__ = [
    "INTENT_PATCH_CONTRACT_PROFILE",
    "INTENT_PATCH_JSON_SCHEMA",
    "INTENT_PATCH_MAX_CONTEXT_REQUESTS",
    "INTENT_PATCH_MAX_BYTES",
    "INTENT_PATCH_MAX_OPERATIONS",
    "INTENT_PATCH_MAX_RECEIPT_BYTES",
    "INTENT_PATCH_OPERATION_KINDS",
    "INTENT_PATCH_SCHEMA_VERSION",
    "IntentPatch",
    "IntentPatchContext",
    "IntentPatchError",
    "IntentPatchOperation",
    "IntentPatchPreview",
    "IntentPatchReceipt",
    "apply_intent_patch_file",
    "parse_intent_patch",
    "patch_context_from_repair_plan",
    "patch_context_from_review_batch",
    "preview_intent_patch",
    "preview_intent_patch_file",
    "source_sha256",
    "starter_intent_patch_payload",
]
