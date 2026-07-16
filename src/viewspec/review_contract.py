"""Bounded public contracts for the local ViewSpec Review V0 protocol."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import secrets
from typing import Any, Mapping


REVIEW_SCHEMA_VERSION = 1

KIB = 1024
MIB = 1024 * KIB

MAX_BODY_BYTES = 8 * KIB
MAX_EVENT_BYTES = 24 * KIB
MAX_BATCH_BYTES = 240 * KIB
MAX_BATCH_EVENTS = 8
MAX_SESSION_EVENTS = 1024
MAX_UNACKNOWLEDGED_EVENTS = 256
MAX_ROUTE_BYTES = 2 * KIB
MAX_SCREEN_ID_BYTES = 128
MAX_SELECTION_BYTES = 4 * KIB
MAX_SELECTION_CONTEXT_BYTES = 512
MAX_CONTEXT_CONTROLS = 16
MAX_CONTROL_VALUE_BYTES = 256
MAX_CONTROL_VALUES_BYTES = 4 * KIB
MAX_EVIDENCE_REFS = 64
MAX_EVIDENCE_REF_BYTES = 256
MAX_EVIDENCE_REFS_BYTES = 16 * KIB
MAX_TARGET_REFS = 32
MAX_TARGET_REF_BYTES = 256
MAX_TARGET_REFS_BYTES = 8 * KIB

CANONICAL_VIEWPORTS: Mapping[str, tuple[int, int]] = {
    "mobile": (390, 844),
    "tablet": (768, 1024),
    "desktop": (1440, 1000),
}

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_IDEMPOTENCY_RE = re.compile(r"^[0-9a-f]{32}$")
_ID_RES = {
    "review": re.compile(r"^vrw_[0-9a-f]{32}$"),
    "event": re.compile(r"^vre_[0-9a-f]{32}$"),
    "batch": re.compile(r"^vrb_[0-9a-f]{32}$"),
}
_SAFE_EVIDENCE_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")


class ReviewContractError(ValueError):
    """Stable fail-closed error raised by Review contract and state code."""

    def __init__(
        self,
        code: str,
        message: str,
        fix: str,
        *,
        http_status: int | None = None,
        cli_exit: int = 2,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix
        self.http_status = http_status
        self.cli_exit = cli_exit

    def to_json(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "fix": self.fix}


def canonical_json_bytes(value: object) -> bytes:
    """Return the compact, stable UTF-8 encoding used for byte limits and hashes."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReviewContractError(
            "REVIEW_EVENT_INVALID",
            f"Review value is not canonical JSON: {exc}",
            "Send only bounded JSON values supported by the Review V0 contract.",
            http_status=400,
        ) from exc


def canonical_json_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def new_review_id() -> str:
    return _new_id("vrw_")


def new_event_id() -> str:
    return _new_id("vre_")


def new_batch_id() -> str:
    return _new_id("vrb_")


def validate_idempotency_key(value: str) -> str:
    if not isinstance(value, str) or _IDEMPOTENCY_RE.fullmatch(value) is None:
        raise ReviewContractError(
            "REVIEW_IDEMPOTENCY_REQUIRED",
            "Review mutations require a 32-lowercase-hex idempotency key.",
            "Generate a 128-bit idempotency key and retry the identical request with the same key.",
            http_status=400,
        )
    return value


def _new_id(prefix: str) -> str:
    try:
        return f"{prefix}{secrets.token_hex(16)}"
    except Exception as exc:
        raise ReviewContractError(
            "REVIEW_ENTROPY_UNAVAILABLE",
            "Operating-system cryptographic entropy is unavailable.",
            "Retry only after the operating system random generator is healthy.",
            http_status=500,
            cli_exit=1,
        ) from exc


def _validate_id(value: object, kind: str) -> str:
    if not isinstance(value, str) or _ID_RES[kind].fullmatch(value) is None:
        raise ReviewContractError(
            "REVIEW_EVENT_INVALID" if kind == "event" else "REVIEW_BATCH_TOO_LARGE",
            f"Invalid Review {kind} id.",
            f"Use the server-issued 128-bit {kind} id unchanged.",
            http_status=400,
        )
    return value


def _validate_hash(value: object, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ReviewContractError(
            "REVIEW_REVISION_IDENTITY_MISMATCH",
            f"{name} must be a 64-lowercase-hex SHA-256.",
            "Rebuild the value from the exact captured revision bytes.",
            http_status=500,
            cli_exit=1,
        )
    return value


def _bounded_text(value: object, name: str, maximum: int, code: str, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str):
        raise ReviewContractError(code, f"{name} must be text.", f"Send {name} as bounded UTF-8 text.", http_status=422)
    if len(value.encode("utf-8")) > maximum:
        raise ReviewContractError(
            code,
            f"{name} exceeds {maximum} UTF-8 bytes.",
            f"Shorten {name} to at most {maximum} UTF-8 bytes.",
            http_status=413 if code == "REVIEW_EVENT_TOO_LARGE" else 422,
        )
    return value


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReviewContractError(
            "REVIEW_EVENT_INVALID",
            f"{name} must be an object.",
            f"Send {name} using the documented Review V0 object shape.",
            http_status=400,
        )
    return value


@dataclass(frozen=True, slots=True)
class ReviewViewport:
    name: str
    width: int
    height: int

    def __post_init__(self) -> None:
        expected = CANONICAL_VIEWPORTS.get(self.name)
        if expected is None or type(self.width) is not int or type(self.height) is not int or expected != (self.width, self.height):
            raise ReviewContractError(
                "REVIEW_VIEWPORT_MISMATCH",
                "Review viewport does not match a canonical V0 viewport.",
                "Use mobile 390x844, tablet 768x1024, or desktop 1440x1000.",
                http_status=422,
            )

    @classmethod
    def canonical(cls, name: str) -> ReviewViewport:
        dimensions = CANONICAL_VIEWPORTS.get(name)
        if dimensions is None:
            return cls(name=name, width=0, height=0)
        return cls(name=name, width=dimensions[0], height=dimensions[1])

    @classmethod
    def from_json(cls, value: object) -> ReviewViewport:
        data = _mapping(value, "viewport")
        return cls(name=data.get("name"), width=data.get("width"), height=data.get("height"))

    def to_json(self) -> dict[str, object]:
        return {"name": self.name, "width": self.width, "height": self.height}


@dataclass(frozen=True, slots=True)
class ReviewSelectedText:
    quote: str
    prefix: str
    suffix: str
    sha256: str

    def __post_init__(self) -> None:
        _bounded_text(self.quote, "selected text", MAX_SELECTION_BYTES, "REVIEW_SELECTION_UNSUPPORTED")
        _bounded_text(self.prefix, "selected text prefix", MAX_SELECTION_CONTEXT_BYTES, "REVIEW_SELECTION_UNSUPPORTED")
        _bounded_text(self.suffix, "selected text suffix", MAX_SELECTION_CONTEXT_BYTES, "REVIEW_SELECTION_UNSUPPORTED")
        if not self.quote:
            raise ReviewContractError(
                "REVIEW_SELECTION_UNSUPPORTED",
                "Selected text must contain at least one visible character.",
                "Select nonempty visible text inside one manifest-backed source node.",
                http_status=422,
            )
        expected = hashlib.sha256(self.quote.encode("utf-8")).hexdigest()
        if self.sha256 != expected:
            raise ReviewContractError(
                "REVIEW_SELECTION_UNSUPPORTED",
                "Selected-text SHA-256 does not match the quote.",
                "Recompute the quote hash from its exact visible UTF-8 text.",
                http_status=422,
            )

    @classmethod
    def create(cls, quote: str, *, prefix: str = "", suffix: str = "") -> ReviewSelectedText:
        return cls(quote, prefix, suffix, hashlib.sha256(quote.encode("utf-8")).hexdigest())

    @classmethod
    def from_json(cls, value: object) -> ReviewSelectedText:
        data = _mapping(value, "selected_text")
        return cls(
            quote=data.get("quote"),
            prefix=data.get("prefix", ""),
            suffix=data.get("suffix", ""),
            sha256=data.get("sha256"),
        )

    def to_json(self) -> dict[str, str]:
        return {"quote": self.quote, "prefix": self.prefix, "suffix": self.suffix, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class ReviewContext:
    route: str | None
    screen_id: str | None
    viewport: ReviewViewport
    selected_text: ReviewSelectedText | None
    control_values: tuple[tuple[str, str], ...]
    visibility: str
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _bounded_text(self.route, "route", MAX_ROUTE_BYTES, "REVIEW_CONTEXT_FORBIDDEN", allow_none=True)
        _bounded_text(self.screen_id, "screen id", MAX_SCREEN_ID_BYTES, "REVIEW_CONTEXT_FORBIDDEN", allow_none=True)
        if not isinstance(self.viewport, ReviewViewport):
            raise ReviewContractError(
                "REVIEW_CONTEXT_FORBIDDEN", "Context viewport is invalid.", "Use one canonical Review viewport.", http_status=422
            )
        if self.selected_text is not None and not isinstance(self.selected_text, ReviewSelectedText):
            raise ReviewContractError(
                "REVIEW_CONTEXT_FORBIDDEN",
                "Context selected_text is invalid.",
                "Use the bounded selected-text contract.",
                http_status=422,
            )
        controls = tuple(self.control_values)
        if len(controls) > MAX_CONTEXT_CONTROLS:
            raise ReviewContractError(
                "REVIEW_CONTEXT_FORBIDDEN",
                f"Context contains more than {MAX_CONTEXT_CONTROLS} controls.",
                "Send only source-declared review_safe controls in the selected scope.",
                http_status=422,
            )
        normalized: list[tuple[str, str]] = []
        seen: set[str] = set()
        aggregate = 0
        for item in controls:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ReviewContractError(
                    "REVIEW_CONTEXT_FORBIDDEN", "Control context must contain key/value pairs.", "Send bounded text pairs.", http_status=422
                )
            key = _bounded_text(item[0], "control id", MAX_SCREEN_ID_BYTES, "REVIEW_CONTEXT_FORBIDDEN")
            value = _bounded_text(item[1], "control value", MAX_CONTROL_VALUE_BYTES, "REVIEW_CONTEXT_FORBIDDEN")
            assert key is not None and value is not None
            if key in seen:
                raise ReviewContractError(
                    "REVIEW_CONTEXT_FORBIDDEN", f"Duplicate control id: {key}", "Send each review-safe control once.", http_status=422
                )
            seen.add(key)
            aggregate += len(value.encode("utf-8"))
            normalized.append((key, value))
        if aggregate > MAX_CONTROL_VALUES_BYTES:
            raise ReviewContractError(
                "REVIEW_CONTEXT_FORBIDDEN",
                f"Control values exceed {MAX_CONTROL_VALUES_BYTES} aggregate UTF-8 bytes.",
                "Reduce the captured review-safe control context.",
                http_status=422,
            )
        if self.visibility not in {"visible", "hidden", "not_rendered"}:
            raise ReviewContractError(
                "REVIEW_CONTEXT_FORBIDDEN",
                "Visibility must be visible, hidden, or not_rendered.",
                "Use the server-observed visibility marker.",
                http_status=422,
            )
        refs = tuple(self.evidence_refs)
        if len(refs) > MAX_EVIDENCE_REFS:
            raise ReviewContractError(
                "REVIEW_CONTEXT_FORBIDDEN",
                f"Context contains more than {MAX_EVIDENCE_REFS} evidence refs.",
                "Reduce the verifier evidence references.",
                http_status=422,
            )
        ref_bytes = 0
        for ref in refs:
            checked = _bounded_text(ref, "evidence ref", MAX_EVIDENCE_REF_BYTES, "REVIEW_CONTEXT_FORBIDDEN")
            assert checked is not None
            if _SAFE_EVIDENCE_RE.fullmatch(checked) is None or checked.startswith("/") or ".." in checked.split("/"):
                raise ReviewContractError(
                    "REVIEW_CONTEXT_FORBIDDEN",
                    "Evidence ref is not a canonical relative path.",
                    "Use an allowlisted relative evidence path.",
                    http_status=422,
                )
            ref_bytes += len(checked.encode("utf-8"))
        if ref_bytes > MAX_EVIDENCE_REFS_BYTES:
            raise ReviewContractError(
                "REVIEW_CONTEXT_FORBIDDEN",
                f"Evidence refs exceed {MAX_EVIDENCE_REFS_BYTES} aggregate UTF-8 bytes.",
                "Reduce the verifier evidence references.",
                http_status=422,
            )
        object.__setattr__(self, "control_values", tuple(sorted(normalized)))
        object.__setattr__(self, "evidence_refs", refs)

    @classmethod
    def from_json(cls, value: object) -> ReviewContext:
        data = _mapping(value, "context")
        allowed = {"route", "screen_id", "viewport", "selected_text", "control_values", "visibility", "evidence_refs"}
        if set(data) - allowed:
            raise ReviewContractError(
                "REVIEW_CONTEXT_FORBIDDEN", "Context contains unknown fields.", "Remove fields outside the V0 context schema.", http_status=422
            )
        controls = data.get("control_values", {})
        if not isinstance(controls, Mapping):
            raise ReviewContractError(
                "REVIEW_CONTEXT_FORBIDDEN", "control_values must be an object.", "Send bounded control key/value pairs.", http_status=422
            )
        selected = data.get("selected_text")
        return cls(
            route=data.get("route"),
            screen_id=data.get("screen_id"),
            viewport=ReviewViewport.from_json(data.get("viewport")),
            selected_text=ReviewSelectedText.from_json(selected) if selected is not None else None,
            control_values=tuple((key, value) for key, value in controls.items()),
            visibility=data.get("visibility"),
            evidence_refs=tuple(data.get("evidence_refs", ())),
        )

    def to_json(self) -> dict[str, object]:
        return {
            "route": self.route,
            "screen_id": self.screen_id,
            "viewport": self.viewport.to_json(),
            "selected_text": self.selected_text.to_json() if self.selected_text is not None else None,
            "control_values": dict(self.control_values),
            "visibility": self.visibility,
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class ReviewRevision:
    number: int
    source_kind: str
    source_sha256: str
    design_sha256: str | None
    target: str
    artifact_set_sha256: str
    root_manifest_kind: str
    root_manifest_sha256: str
    compiler_version: str
    contract_profile: str

    def __post_init__(self) -> None:
        if type(self.number) is not int or not 1 <= self.number <= (2**63 - 1):
            raise ReviewContractError(
                "REVIEW_REVISION_IDENTITY_MISMATCH",
                "Revision number must be a positive 64-bit integer.",
                "Use the session-assigned monotonic revision number.",
                http_status=500,
                cli_exit=1,
            )
        if self.source_kind not in {"intent_bundle", "app_bundle"}:
            raise ReviewContractError(
                "REVIEW_SOURCE_UNSUPPORTED", "Review source kind is unsupported.", "Use an IntentBundle or AppBundle.", cli_exit=2
            )
        _validate_hash(self.source_sha256, "source_sha256")
        _validate_hash(self.design_sha256, "design_sha256", optional=True)
        _validate_hash(self.artifact_set_sha256, "artifact_set_sha256")
        _validate_hash(self.root_manifest_sha256, "root_manifest_sha256")
        _bounded_text(self.target, "target", 64, "REVIEW_REVISION_IDENTITY_MISMATCH")
        _bounded_text(self.root_manifest_kind, "root manifest kind", 64, "REVIEW_REVISION_IDENTITY_MISMATCH")
        _bounded_text(self.compiler_version, "compiler version", 64, "REVIEW_REVISION_IDENTITY_MISMATCH")
        _bounded_text(self.contract_profile, "contract profile", 64, "REVIEW_REVISION_IDENTITY_MISMATCH")

    @classmethod
    def from_json(cls, value: object) -> ReviewRevision:
        data = _mapping(value, "revision")
        return cls(
            number=data.get("number"),
            source_kind=data.get("source_kind"),
            source_sha256=data.get("source_sha256"),
            design_sha256=data.get("design_sha256"),
            target=data.get("target"),
            artifact_set_sha256=data.get("artifact_set_sha256"),
            root_manifest_kind=data.get("root_manifest_kind"),
            root_manifest_sha256=data.get("root_manifest_sha256"),
            compiler_version=data.get("compiler_version"),
            contract_profile=data.get("contract_profile"),
        )

    def to_json(self) -> dict[str, object]:
        return {
            "number": self.number,
            "source_kind": self.source_kind,
            "source_sha256": self.source_sha256,
            "design_sha256": self.design_sha256,
            "target": self.target,
            "artifact_set_sha256": self.artifact_set_sha256,
            "root_manifest_kind": self.root_manifest_kind,
            "root_manifest_sha256": self.root_manifest_sha256,
            "compiler_version": self.compiler_version,
            "contract_profile": self.contract_profile,
        }


@dataclass(frozen=True, slots=True)
class ReviewTarget:
    kind: str
    screen_id: str | None
    ir_id: str | None
    source_ref: str | None
    dom_id: str | None
    binding_id: str | None
    action_id: str | None
    intent_refs: tuple[str, ...]
    content_refs: tuple[str, ...]
    provenance_manifest_sha256: str | None
    target_resolution: str

    def __post_init__(self) -> None:
        if self.kind not in {"source_node", "page"}:
            raise ReviewContractError(
                "REVIEW_TARGET_INVALID", "Target kind must be source_node or page.", "Use a manifest-backed or page target.", http_status=422
            )
        _bounded_text(self.screen_id, "target screen id", MAX_SCREEN_ID_BYTES, "REVIEW_TARGET_INVALID", allow_none=True)
        _bounded_text(self.ir_id, "target ir id", 128, "REVIEW_TARGET_INVALID", allow_none=True)
        _bounded_text(self.source_ref, "target source ref", MAX_TARGET_REF_BYTES, "REVIEW_TARGET_INVALID", allow_none=True)
        _bounded_text(self.dom_id, "target DOM id", MAX_TARGET_REF_BYTES, "REVIEW_TARGET_INVALID", allow_none=True)
        _bounded_text(self.binding_id, "target binding id", 128, "REVIEW_TARGET_INVALID", allow_none=True)
        _bounded_text(self.action_id, "target action id", 128, "REVIEW_TARGET_INVALID", allow_none=True)
        if self.target_resolution not in {"exact", "ancestor", "page", "changed", "stale"}:
            raise ReviewContractError(
                "REVIEW_TARGET_INVALID", "Target resolution is unsupported.", "Use an exact V0 target resolution.", http_status=422
            )
        if self.kind == "source_node":
            if not self.ir_id or not self.source_ref or not self.dom_id:
                raise ReviewContractError(
                    "REVIEW_TARGET_INVALID",
                    "Source-node targets require ir_id, source_ref, and dom_id.",
                    "Rebuild the target from the checked manifest.",
                    http_status=422,
                )
            try:
                _validate_hash(self.provenance_manifest_sha256, "provenance_manifest_sha256")
            except ReviewContractError as exc:
                raise ReviewContractError(
                    "REVIEW_TARGET_INVALID",
                    "Source-node target requires a valid provenance manifest hash.",
                    "Rebuild the target from the checked manifest.",
                    http_status=422,
                ) from exc
        intents = tuple(self.intent_refs)
        contents = tuple(self.content_refs)
        if len(intents) > MAX_TARGET_REFS or len(contents) > MAX_TARGET_REFS:
            raise ReviewContractError(
                "REVIEW_TARGET_LIMIT_EXCEEDED",
                f"Target intent_refs and content_refs are capped at {MAX_TARGET_REFS} each.",
                "Reduce the manifest-backed target refs.",
                http_status=422,
            )
        total = 0
        for ref in (*intents, *contents):
            checked = _bounded_text(ref, "target ref", MAX_TARGET_REF_BYTES, "REVIEW_TARGET_LIMIT_EXCEEDED")
            assert checked is not None
            total += len(checked.encode("utf-8"))
        if total > MAX_TARGET_REFS_BYTES:
            raise ReviewContractError(
                "REVIEW_TARGET_LIMIT_EXCEEDED",
                f"Target refs exceed {MAX_TARGET_REFS_BYTES} aggregate UTF-8 bytes.",
                "Reduce the manifest-backed target refs.",
                http_status=422,
            )
        object.__setattr__(self, "intent_refs", intents)
        object.__setattr__(self, "content_refs", contents)

    @classmethod
    def from_json(cls, value: object) -> ReviewTarget:
        data = _mapping(value, "target")
        return cls(
            kind=data.get("kind"),
            screen_id=data.get("screen_id"),
            ir_id=data.get("ir_id"),
            source_ref=data.get("source_ref"),
            dom_id=data.get("dom_id"),
            binding_id=data.get("binding_id"),
            action_id=data.get("action_id"),
            intent_refs=tuple(data.get("intent_refs", ())),
            content_refs=tuple(data.get("content_refs", ())),
            provenance_manifest_sha256=data.get("provenance_manifest_sha256"),
            target_resolution=data.get("target_resolution"),
        )

    def to_json(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "screen_id": self.screen_id,
            "ir_id": self.ir_id,
            "source_ref": self.source_ref,
            "dom_id": self.dom_id,
            "binding_id": self.binding_id,
            "action_id": self.action_id,
            "intent_refs": list(self.intent_refs),
            "content_refs": list(self.content_refs),
            "provenance_manifest_sha256": self.provenance_manifest_sha256,
            "target_resolution": self.target_resolution,
        }


@dataclass(frozen=True, slots=True)
class ReviewEvent:
    event_id: str
    sequence: int
    actor: str
    kind: str
    body: str
    revision: ReviewRevision
    target: ReviewTarget
    context: ReviewContext
    schema_version: int = REVIEW_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REVIEW_SCHEMA_VERSION:
            raise ReviewContractError(
                "REVIEW_EVENT_INVALID", "Unsupported ReviewEvent schema_version.", "Use ReviewEvent schema_version 1.", http_status=400
            )
        _validate_id(self.event_id, "event")
        if type(self.sequence) is not int or not 1 <= self.sequence <= MAX_SESSION_EVENTS:
            raise ReviewContractError(
                "REVIEW_EVENT_LIMIT_EXCEEDED",
                f"Event sequence must be between 1 and {MAX_SESSION_EVENTS}.",
                "End or purge the bounded session before accepting more events.",
                http_status=429,
            )
        if self.actor != "human" or self.kind not in {"change_request", "question", "approval", "note"}:
            raise ReviewContractError(
                "REVIEW_EVENT_INVALID",
                "Review event actor or kind is unsupported.",
                "Use a human change_request, question, approval, or note.",
                http_status=422,
            )
        _bounded_text(self.body, "event body", MAX_BODY_BYTES, "REVIEW_EVENT_TOO_LARGE")
        if not isinstance(self.revision, ReviewRevision) or not isinstance(self.target, ReviewTarget) or not isinstance(self.context, ReviewContext):
            raise ReviewContractError(
                "REVIEW_EVENT_INVALID",
                "Review event revision, target, or context is invalid.",
                "Use validated Review V0 contract objects.",
                http_status=422,
            )
        if len(canonical_json_bytes(self.to_json())) > MAX_EVENT_BYTES:
            raise ReviewContractError(
                "REVIEW_EVENT_TOO_LARGE",
                f"Serialized ReviewEvent exceeds {MAX_EVENT_BYTES} bytes.",
                "Reduce feedback or context before submitting the event.",
                http_status=413,
            )

    @classmethod
    def from_json(cls, value: object) -> ReviewEvent:
        data = _mapping(value, "event")
        return cls(
            schema_version=data.get("schema_version"),
            event_id=data.get("event_id"),
            sequence=data.get("sequence"),
            actor=data.get("actor"),
            kind=data.get("kind"),
            body=data.get("body"),
            revision=ReviewRevision.from_json(data.get("revision")),
            target=ReviewTarget.from_json(data.get("target")),
            context=ReviewContext.from_json(data.get("context")),
        )

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "sequence": self.sequence,
            "actor": self.actor,
            "kind": self.kind,
            "body": self.body,
            "revision": self.revision.to_json(),
            "target": self.target.to_json(),
            "context": self.context.to_json(),
        }


@dataclass(frozen=True, slots=True)
class ReviewBatch:
    review_id: str
    batch_id: str
    status: str
    revision: ReviewRevision
    events: tuple[ReviewEvent, ...]
    first_sequence: int
    last_sequence: int
    requires_ack: bool = True
    redelivered: bool = False
    schema_version: int = REVIEW_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REVIEW_SCHEMA_VERSION:
            raise ReviewContractError(
                "REVIEW_BATCH_TOO_LARGE", "Unsupported ReviewBatch schema_version.", "Use ReviewBatch schema_version 1.", http_status=500
            )
        _validate_id(self.review_id, "review")
        _validate_id(self.batch_id, "batch")
        if self.status != "feedback" or not self.events or len(self.events) > MAX_BATCH_EVENTS:
            raise ReviewContractError(
                "REVIEW_BATCH_TOO_LARGE",
                f"Feedback batches require 1 through {MAX_BATCH_EVENTS} events.",
                "Pack a smaller complete batch without truncating an event.",
                http_status=500,
                cli_exit=1,
            )
        if type(self.requires_ack) is not bool or type(self.redelivered) is not bool:
            raise ReviewContractError(
                "REVIEW_BATCH_TOO_LARGE", "Batch delivery flags must be boolean.", "Use canonical delivery flags.", http_status=500
            )
        expected_sequences = list(range(self.first_sequence, self.last_sequence + 1))
        actual_sequences = [event.sequence for event in self.events]
        if actual_sequences != expected_sequences:
            raise ReviewContractError(
                "REVIEW_ACK_OUT_OF_ORDER",
                "Batch events must be contiguous and ordered.",
                "Issue the next contiguous events after the delivery cursor.",
                http_status=500,
                cli_exit=1,
            )
        if any(event.revision != self.revision for event in self.events):
            raise ReviewContractError(
                "REVIEW_REVISION_IDENTITY_MISMATCH",
                "A batch cannot mix events from different revisions.",
                "Defer later-revision events to a separate batch.",
                http_status=500,
                cli_exit=1,
            )
        if len(canonical_json_bytes(self.to_json())) > MAX_BATCH_BYTES:
            raise ReviewContractError(
                "REVIEW_BATCH_TOO_LARGE",
                f"Serialized ReviewBatch exceeds {MAX_BATCH_BYTES} bytes.",
                "Pack fewer complete events into the batch.",
                http_status=500,
                cli_exit=1,
            )

    @classmethod
    def create(
        cls,
        events: tuple[ReviewEvent, ...],
        *,
        review_id: str | None = None,
        batch_id: str | None = None,
    ) -> ReviewBatch:
        bounded = tuple(events)
        if not bounded:
            raise ReviewContractError(
                "REVIEW_BATCH_TOO_LARGE", "Cannot create an empty feedback batch.", "Wait until feedback is available.", http_status=500
            )
        revision = bounded[0].revision
        return cls(
            review_id=review_id or new_review_id(),
            batch_id=batch_id or new_batch_id(),
            status="feedback",
            revision=revision,
            events=bounded,
            first_sequence=bounded[0].sequence,
            last_sequence=bounded[-1].sequence,
        )

    @classmethod
    def from_json(cls, value: object) -> ReviewBatch:
        data = _mapping(value, "batch")
        delivery = _mapping(data.get("delivery"), "batch delivery")
        return cls(
            schema_version=data.get("schema_version"),
            review_id=data.get("review_id"),
            batch_id=data.get("batch_id"),
            status=data.get("status"),
            revision=ReviewRevision.from_json(data.get("revision")),
            events=tuple(ReviewEvent.from_json(item) for item in data.get("events", ())),
            first_sequence=delivery.get("first_sequence"),
            last_sequence=delivery.get("last_sequence"),
            requires_ack=delivery.get("requires_ack"),
            redelivered=delivery.get("redelivered"),
        )

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "review_id": self.review_id,
            "batch_id": self.batch_id,
            "status": self.status,
            "delivery": {
                "first_sequence": self.first_sequence,
                "last_sequence": self.last_sequence,
                "requires_ack": self.requires_ack,
                "redelivered": self.redelivered,
            },
            "revision": self.revision.to_json(),
            "events": [event.to_json() for event in self.events],
        }


__all__ = [
    "CANONICAL_VIEWPORTS",
    "MAX_BATCH_BYTES",
    "MAX_BATCH_EVENTS",
    "MAX_BODY_BYTES",
    "MAX_CONTEXT_CONTROLS",
    "MAX_CONTROL_VALUE_BYTES",
    "MAX_EVENT_BYTES",
    "MAX_SESSION_EVENTS",
    "MAX_UNACKNOWLEDGED_EVENTS",
    "ReviewBatch",
    "ReviewContext",
    "ReviewContractError",
    "ReviewEvent",
    "ReviewRevision",
    "ReviewSelectedText",
    "ReviewTarget",
    "ReviewViewport",
    "canonical_json_bytes",
    "canonical_json_sha256",
    "new_batch_id",
    "new_event_id",
    "new_review_id",
    "validate_idempotency_key",
]
