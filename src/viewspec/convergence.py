"""Bounded agent orchestration for compile-until-conformant workflows."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Callable

from viewspec.repair import VerificationRepairPlan
from viewspec.verification import RetryLineage, VerificationResult


CONVERGENCE_SCHEMA_VERSION = 1
CONVERGENCE_RUN_ID_RE = re.compile(r"^vcg_[0-9a-f]{32}$")
CONVERGENCE_STATUSES = frozenset({"conformant", "exhausted", "stalled"})
MAX_CONVERGENCE_ATTEMPTS = 10

VerifyAttempt = Callable[[dict, RetryLineage], VerificationResult]
RepairAttempt = Callable[[dict, VerificationRepairPlan], dict]


def _canonical(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("AppBundle must be finite JSON data") from exc


def _json_copy(value: dict) -> dict:
    return json.loads(_canonical(value))


def _input_sha256(bundle: dict) -> str:
    if not isinstance(bundle, dict):
        raise TypeError("AppBundle must be a JSON object")
    return hashlib.sha256(_canonical(bundle)).hexdigest()


@dataclass(frozen=True)
class ConvergenceAttempt:
    attempt: int
    input_sha256: str
    result: VerificationResult
    repair_plan: VerificationRepairPlan

    def __post_init__(self) -> None:
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("convergence attempt must be a positive integer")
        if not re.fullmatch(r"[0-9a-f]{64}", self.input_sha256):
            raise ValueError("convergence input_sha256 is invalid")
        if self.result.lineage.attempt != self.attempt:
            raise ValueError("convergence result lineage does not match its attempt")
        if self.repair_plan.previous_verification_id != self.result.verification_id:
            raise ValueError("convergence repair plan does not match its result")
        if self.repair_plan.previous_result_sha256 != self.result.result_sha256:
            raise ValueError("convergence repair plan hash does not match its result")

    def to_json(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "input_sha256": self.input_sha256,
            "result": self.result.to_json(),
            "repair_plan": self.repair_plan.to_json(),
        }


def _run_id(
    status: str,
    max_attempts: int,
    attempts: tuple[ConvergenceAttempt, ...],
) -> str:
    material = {
        "status": status,
        "max_attempts": max_attempts,
        "attempts": [
            {
                "attempt": item.attempt,
                "input_sha256": item.input_sha256,
                "result_sha256": item.result.result_sha256,
                "repair_plan_id": item.repair_plan.repair_plan_id,
            }
            for item in attempts
        ],
        "type": "viewspec_convergence_v1",
    }
    return f"vcg_{hashlib.sha256(_canonical(material)).hexdigest()[:32]}"


@dataclass(frozen=True)
class ConvergenceRun:
    run_id: str
    status: str
    max_attempts: int
    attempts: tuple[ConvergenceAttempt, ...]
    schema_version: int = CONVERGENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONVERGENCE_SCHEMA_VERSION:
            raise ValueError("Unsupported convergence schema_version")
        if self.status not in CONVERGENCE_STATUSES:
            raise ValueError("convergence status is invalid")
        if (
            type(self.max_attempts) is not int
            or not 1 <= self.max_attempts <= MAX_CONVERGENCE_ATTEMPTS
        ):
            raise ValueError(
                f"max_attempts must be between 1 and {MAX_CONVERGENCE_ATTEMPTS}"
            )
        if not self.attempts or len(self.attempts) > self.max_attempts:
            raise ValueError("convergence run has an invalid attempt count")
        if tuple(item.attempt for item in self.attempts) != tuple(
            range(1, len(self.attempts) + 1)
        ):
            raise ValueError("convergence attempts must be contiguous")
        for previous, current in zip(self.attempts, self.attempts[1:]):
            if current.result.lineage.parent_verification_id != previous.result.verification_id:
                raise ValueError("convergence attempt lineage is not contiguous")
        if self.status == "conformant" and self.final_result.status != "conformant":
            raise ValueError("conformant convergence must end in a conformant result")
        if self.status != "conformant" and self.final_result.status == "conformant":
            raise ValueError("nonconformant convergence cannot contain a conformant final result")
        expected_id = _run_id(self.status, self.max_attempts, self.attempts)
        if self.run_id != expected_id or not CONVERGENCE_RUN_ID_RE.fullmatch(self.run_id):
            raise ValueError("convergence run_id does not match its attempts")

    @property
    def final_result(self) -> VerificationResult:
        return self.attempts[-1].result

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "status": self.status,
            "max_attempts": self.max_attempts,
            "attempts": [item.to_json() for item in self.attempts],
        }


def _finish(
    status: str,
    max_attempts: int,
    attempts: list[ConvergenceAttempt],
) -> ConvergenceRun:
    items = tuple(attempts)
    return ConvergenceRun(
        run_id=_run_id(status, max_attempts, items),
        status=status,
        max_attempts=max_attempts,
        attempts=items,
    )


def run_until_conformant(
    app_bundle: dict,
    *,
    verify_attempt: VerifyAttempt,
    repair_attempt: RepairAttempt,
    max_attempts: int = 3,
) -> ConvergenceRun:
    """Verify, repair, and retry one AppBundle through a bounded agent loop."""
    if (
        type(max_attempts) is not int
        or not 1 <= max_attempts <= MAX_CONVERGENCE_ATTEMPTS
    ):
        raise ValueError(
            f"max_attempts must be between 1 and {MAX_CONVERGENCE_ATTEMPTS}"
        )
    current = _json_copy(app_bundle)
    lineage = RetryLineage.root()
    attempts: list[ConvergenceAttempt] = []
    plan_sha256: str | None = None

    for attempt_number in range(1, max_attempts + 1):
        input_sha = _input_sha256(current)
        result = verify_attempt(_json_copy(current), lineage)
        if not isinstance(result, VerificationResult):
            raise TypeError("verify_attempt must return a VerificationResult")
        if result.lineage != lineage:
            raise ValueError("verification result lineage changed during convergence")
        if plan_sha256 is None:
            plan_sha256 = result.plan.plan_sha256
        elif result.plan.plan_sha256 != plan_sha256:
            raise ValueError("verification plan changed during convergence")
        repair_plan = VerificationRepairPlan.from_result(result)
        attempts.append(
            ConvergenceAttempt(
                attempt=attempt_number,
                input_sha256=input_sha,
                result=result,
                repair_plan=repair_plan,
            )
        )
        if repair_plan.disposition == "done":
            return _finish("conformant", max_attempts, attempts)
        if attempt_number == max_attempts:
            return _finish("exhausted", max_attempts, attempts)
        if repair_plan.disposition == "retry":
            lineage = repair_plan.next_lineage
            continue

        repaired = repair_attempt(_json_copy(current), repair_plan)
        if not isinstance(repaired, dict):
            raise TypeError("repair_attempt must return an AppBundle JSON object")
        repaired = _json_copy(repaired)
        if _input_sha256(repaired) == input_sha:
            return _finish("stalled", max_attempts, attempts)
        current = repaired
        lineage = repair_plan.next_lineage

    raise AssertionError("bounded convergence loop did not terminate")


__all__ = [
    "CONVERGENCE_SCHEMA_VERSION",
    "ConvergenceAttempt",
    "ConvergenceRun",
    "MAX_CONVERGENCE_ATTEMPTS",
    "run_until_conformant",
]
