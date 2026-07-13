"""Deterministic repair instructions derived from verification results."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
from typing import Any, Mapping

from viewspec.verification import (
    RetryLineage,
    SHA256_RE,
    VERIFICATION_ID_RE,
    VerificationDiagnostic,
    VerificationResult,
)


REPAIR_SCHEMA_VERSION = 1
REPAIR_PLAN_ID_RE = re.compile(r"^vrp_[0-9a-f]{32}$")
REPAIR_ID_RE = re.compile(r"^vfix_[0-9a-f]{32}$")
SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
ALLOWED_DISPOSITIONS = frozenset({"done", "repair", "retry"})
MAX_REPAIR_DIRECTIVES = 256


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _text(value: Any, name: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or "\0" in value:
        raise ValueError(f"{name} must be non-empty trimmed text")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return value


def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _evidence_path(value: Any) -> str:
    candidate = _text(value, "repair evidence path", maximum=512)
    path = PurePosixPath(candidate)
    if (
        "\\" in candidate
        or path.is_absolute()
        or path.as_posix() != candidate
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("repair evidence path must be canonical and relative")
    return candidate


@dataclass(frozen=True)
class SourceNodePath:
    """Canonical source address for one local or AppBundle screen node."""

    screen_id: str | None
    ir_id: str | None

    def __post_init__(self) -> None:
        if self.screen_id is None and self.ir_id is None:
            raise ValueError("source node path requires a screen or IR node")
        if self.screen_id is not None and not SOURCE_ID_RE.fullmatch(self.screen_id):
            raise ValueError("source node path has an invalid screen id")
        if self.ir_id is not None and not SOURCE_ID_RE.fullmatch(self.ir_id):
            raise ValueError("source node path has an invalid IR id")

    @classmethod
    def from_text(cls, value: Any) -> SourceNodePath:
        text = _text(value, "source node path", maximum=512)
        parts = text.split("/")
        if len(parts) == 1 and parts[0].startswith("ir:"):
            return cls(None, parts[0][3:])
        if len(parts) == 1 and parts[0].startswith("screen:"):
            return cls(parts[0][7:], None)
        if (
            len(parts) == 2
            and parts[0].startswith("screen:")
            and parts[1].startswith("ir:")
        ):
            return cls(parts[0][7:], parts[1][3:])
        raise ValueError("source node path must be ir:<id> or screen:<id>[/ir:<id>]")

    def to_text(self) -> str:
        if self.screen_id is None:
            return f"ir:{self.ir_id}"
        if self.ir_id is None:
            return f"screen:{self.screen_id}"
        return f"screen:{self.screen_id}/ir:{self.ir_id}"


def _directive_identity(code: str, source_path: SourceNodePath | None) -> tuple[str, str]:
    material = {
        "code": code,
        "source_path": source_path.to_text() if source_path else None,
        "type": "viewspec_repair_directive_v1",
    }
    digest = hashlib.sha256(_canonical(material)).hexdigest()
    return f"vfix_{digest[:32]}", digest


@dataclass(frozen=True)
class RepairDirective:
    """One source-scoped repair that may recur across verification attempts."""

    repair_id: str
    recurrence_fingerprint: str
    code: str
    instruction: str
    source_path: SourceNodePath | None
    viewports: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.repair_id, str) or not REPAIR_ID_RE.fullmatch(self.repair_id):
            raise ValueError("repair_id is invalid")
        _sha(self.recurrence_fingerprint, "recurrence_fingerprint")
        code = _text(self.code, "repair code", maximum=96)
        if not code.startswith("VERIFY_") or not re.fullmatch(r"[A-Z0-9_]+", code):
            raise ValueError("repair code must use the VERIFY_* namespace")
        _text(self.instruction, "repair instruction")
        expected_id, expected_fingerprint = _directive_identity(code, self.source_path)
        if self.repair_id != expected_id or self.recurrence_fingerprint != expected_fingerprint:
            raise ValueError("repair directive identity does not match its code and source path")
        viewports = tuple(self.viewports)
        if len(set(viewports)) != len(viewports):
            raise ValueError("repair directive viewports must be unique")
        for viewport in viewports:
            _text(viewport, "repair viewport", maximum=64)
        refs = tuple(sorted({_evidence_path(item) for item in self.evidence_refs}))
        object.__setattr__(self, "viewports", viewports)
        object.__setattr__(self, "evidence_refs", refs)

    def to_json(self) -> dict[str, Any]:
        return {
            "repair_id": self.repair_id,
            "recurrence_fingerprint": self.recurrence_fingerprint,
            "code": self.code,
            "instruction": self.instruction,
            "source_path": self.source_path.to_text() if self.source_path else None,
            "viewports": list(self.viewports),
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_json(cls, payload: Any) -> RepairDirective:
        data = _mapping(payload, "repair directive")
        raw_viewports = data.get("viewports")
        raw_refs = data.get("evidence_refs")
        if not isinstance(raw_viewports, list) or not all(
            isinstance(item, str) for item in raw_viewports
        ):
            raise ValueError("repair directive viewports must be an array of strings")
        if not isinstance(raw_refs, list) or not all(isinstance(item, str) for item in raw_refs):
            raise ValueError("repair directive evidence_refs must be an array of paths")
        raw_source = data.get("source_path")
        return cls(
            repair_id=data.get("repair_id"),
            recurrence_fingerprint=data.get("recurrence_fingerprint"),
            code=data.get("code"),
            instruction=data.get("instruction"),
            source_path=SourceNodePath.from_text(raw_source) if raw_source is not None else None,
            viewports=tuple(raw_viewports),
            evidence_refs=tuple(raw_refs),
        )


def _repair_plan_id(previous_result_sha256: str) -> str:
    material = {
        "previous_result_sha256": _sha(previous_result_sha256, "previous_result_sha256"),
        "type": "viewspec_repair_plan_v1",
    }
    return f"vrp_{hashlib.sha256(_canonical(material)).hexdigest()[:32]}"


def _viewport_order(result: VerificationResult) -> dict[str, int]:
    return {viewport.name: index for index, viewport in enumerate(result.plan.viewports)}


def _group_directives(result: VerificationResult) -> tuple[RepairDirective, ...]:
    grouped: dict[tuple[str, str | None], list[VerificationDiagnostic]] = {}
    for diagnostic in result.diagnostics:
        if diagnostic.severity != "error":
            continue
        source = (
            SourceNodePath.from_text(diagnostic.source_ref)
            if diagnostic.source_ref is not None
            else None
        )
        grouped.setdefault(
            (diagnostic.code, source.to_text() if source else None),
            [],
        ).append(diagnostic)

    viewport_order = _viewport_order(result)
    directives = []
    for (code, source_text), diagnostics in grouped.items():
        source = SourceNodePath.from_text(source_text) if source_text else None
        raw_viewports = {item.viewport for item in diagnostics if item.viewport is not None}
        unknown = sorted(raw_viewports - set(viewport_order))
        if unknown:
            raise ValueError(f"repair diagnostic references unknown viewport: {unknown[0]}")
        viewports = tuple(sorted(raw_viewports, key=viewport_order.__getitem__))
        evidence_refs = tuple(
            sorted({ref for item in diagnostics for ref in item.evidence_refs})
        )
        instructions = sorted({item.fix for item in diagnostics})
        repair_id, fingerprint = _directive_identity(code, source)
        directives.append(
            RepairDirective(
                repair_id=repair_id,
                recurrence_fingerprint=fingerprint,
                code=code,
                instruction=instructions[0],
                source_path=source,
                viewports=viewports,
                evidence_refs=evidence_refs,
            )
        )
    return tuple(sorted(directives, key=lambda item: item.repair_id))


@dataclass(frozen=True)
class VerificationRepairPlan:
    """Canonical next action for a verified artifact."""

    repair_plan_id: str
    disposition: str
    previous_verification_id: str
    previous_result_sha256: str
    next_lineage: RetryLineage | None
    directives: tuple[RepairDirective, ...] = ()
    retry_reason_codes: tuple[str, ...] = ()
    schema_version: int = REPAIR_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REPAIR_SCHEMA_VERSION:
            raise ValueError("Unsupported repair plan schema_version")
        if not isinstance(self.repair_plan_id, str) or not REPAIR_PLAN_ID_RE.fullmatch(
            self.repair_plan_id
        ):
            raise ValueError("repair_plan_id is invalid")
        if self.repair_plan_id != _repair_plan_id(self.previous_result_sha256):
            raise ValueError("repair_plan_id does not match previous_result_sha256")
        if not isinstance(self.previous_verification_id, str) or not VERIFICATION_ID_RE.fullmatch(
            self.previous_verification_id
        ):
            raise ValueError("previous_verification_id is invalid")
        if self.disposition not in ALLOWED_DISPOSITIONS:
            raise ValueError("repair disposition is invalid")
        directives = tuple(sorted(self.directives, key=lambda item: item.repair_id))
        if len(directives) > MAX_REPAIR_DIRECTIVES:
            raise ValueError("repair plan contains too many directives")
        if len({item.repair_id for item in directives}) != len(directives):
            raise ValueError("repair directive ids must be unique")
        retry_codes = tuple(sorted(set(self.retry_reason_codes)))
        for code in retry_codes:
            if not code.startswith("VERIFY_") or not re.fullmatch(r"[A-Z0-9_]+", code):
                raise ValueError("retry reason codes must use the VERIFY_* namespace")
        if self.disposition == "done":
            if self.next_lineage is not None or directives or retry_codes:
                raise ValueError("done repair plans cannot include next actions")
        else:
            if self.next_lineage is None:
                raise ValueError("repair and retry plans require next lineage")
            if self.next_lineage.parent_verification_id != self.previous_verification_id:
                raise ValueError("repair next lineage must descend from the previous verification")
        if self.disposition == "repair" and (not directives or retry_codes):
            raise ValueError("repair disposition requires directives and no retry reasons")
        if self.disposition == "retry" and (directives or not retry_codes):
            raise ValueError("retry disposition requires retry reasons and no directives")
        object.__setattr__(self, "directives", directives)
        object.__setattr__(self, "retry_reason_codes", retry_codes)

    @classmethod
    def from_result(cls, result: VerificationResult) -> VerificationRepairPlan:
        if not isinstance(result, VerificationResult):
            raise TypeError("result must be a VerificationResult")
        if result.status == "conformant":
            disposition = "done"
            next_lineage = None
            directives: tuple[RepairDirective, ...] = ()
            retry_codes: tuple[str, ...] = ()
        elif result.status == "indeterminate":
            disposition = "retry"
            next_lineage = result.lineage.next_attempt(result.verification_id)
            directives = ()
            retry_codes = tuple(sorted({item.code for item in result.diagnostics}))
            if not retry_codes:
                retry_codes = ("VERIFY_RESULT_INDETERMINATE",)
        else:
            disposition = "repair"
            next_lineage = result.lineage.next_attempt(result.verification_id)
            directives = _group_directives(result)
            retry_codes = ()
        return cls(
            repair_plan_id=_repair_plan_id(result.result_sha256),
            disposition=disposition,
            previous_verification_id=result.verification_id,
            previous_result_sha256=result.result_sha256,
            next_lineage=next_lineage,
            directives=directives,
            retry_reason_codes=retry_codes,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "repair_plan_id": self.repair_plan_id,
            "disposition": self.disposition,
            "previous_verification_id": self.previous_verification_id,
            "previous_result_sha256": self.previous_result_sha256,
            "next_lineage": self.next_lineage.to_json() if self.next_lineage else None,
            "directives": [item.to_json() for item in self.directives],
            "retry_reason_codes": list(self.retry_reason_codes),
        }

    @classmethod
    def from_json(cls, payload: Any) -> VerificationRepairPlan:
        data = _mapping(payload, "verification repair plan")
        raw_directives = data.get("directives")
        raw_codes = data.get("retry_reason_codes")
        if not isinstance(raw_directives, list):
            raise ValueError("repair directives must be an array")
        if not isinstance(raw_codes, list) or not all(isinstance(item, str) for item in raw_codes):
            raise ValueError("retry_reason_codes must be an array of strings")
        raw_lineage = data.get("next_lineage")
        return cls(
            schema_version=data.get("schema_version"),
            repair_plan_id=data.get("repair_plan_id"),
            disposition=data.get("disposition"),
            previous_verification_id=data.get("previous_verification_id"),
            previous_result_sha256=data.get("previous_result_sha256"),
            next_lineage=RetryLineage.from_json(raw_lineage) if raw_lineage is not None else None,
            directives=tuple(RepairDirective.from_json(item) for item in raw_directives),
            retry_reason_codes=tuple(raw_codes),
        )


__all__ = [
    "REPAIR_SCHEMA_VERSION",
    "RepairDirective",
    "SourceNodePath",
    "VerificationRepairPlan",
]
