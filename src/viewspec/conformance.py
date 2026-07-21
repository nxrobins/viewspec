"""Executable public conformance corpus for the ViewSpec verifier."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
from typing import Any, Iterator, Mapping

from viewspec.intent_tools import compile_intent_bundle_file_tool
from viewspec.local_verify import verify_local_artifact
from viewspec.verification import (
    ALLOWED_EVIDENCE_ROLES,
    ALLOWED_STATUSES,
    RetryLineage,
    VerificationPlan,
)


CONFORMANCE_CORPUS_SCHEMA_VERSION = 1
ALLOWED_CASE_KINDS = frozenset({"app_screen", "intent"})
CASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,95}$")
SCREEN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
MAX_CORPUS_CASES = 64


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _safe_source(value: Any) -> str:
    if not isinstance(value, str) or not value or value.strip() != value or "\\" in value:
        raise ValueError("conformance case source must be canonical relative text")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("conformance case source must be a canonical relative path")
    return value


@dataclass(frozen=True)
class ConformanceCase:
    id: str
    kind: str
    source: str
    expected_status: str
    required_evidence_roles: tuple[str, ...]
    root: Path
    screen_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not CASE_ID_RE.fullmatch(self.id):
            raise ValueError("conformance case id is invalid")
        if self.kind not in ALLOWED_CASE_KINDS:
            raise ValueError("conformance case kind is invalid")
        object.__setattr__(self, "source", _safe_source(self.source))
        if self.expected_status not in ALLOWED_STATUSES:
            raise ValueError("conformance case expected_status is invalid")
        roles = tuple(sorted(set(self.required_evidence_roles)))
        if not roles or any(role not in ALLOWED_EVIDENCE_ROLES for role in roles):
            raise ValueError("conformance case required_evidence_roles are invalid")
        if self.kind == "app_screen":
            if not isinstance(self.screen_id, str) or not SCREEN_ID_RE.fullmatch(self.screen_id):
                raise ValueError("app_screen conformance cases require a valid screen_id")
        elif self.screen_id is not None:
            raise ValueError("intent conformance cases cannot include screen_id")
        object.__setattr__(self, "required_evidence_roles", roles)
        object.__setattr__(self, "root", self.root.resolve())

    @property
    def source_path(self) -> Path:
        path = self.root.joinpath(*PurePosixPath(self.source).parts).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("conformance case source escapes the corpus root")
        return path

    def to_json(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "kind": self.kind,
            "source": self.source,
            "expected_status": self.expected_status,
            "required_evidence_roles": list(self.required_evidence_roles),
        }
        if self.screen_id is not None:
            payload["screen_id"] = self.screen_id
        return payload


@dataclass(frozen=True)
class ConformanceCorpus:
    root: Path
    cases: tuple[ConformanceCase, ...]
    schema_version: int = CONFORMANCE_CORPUS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CONFORMANCE_CORPUS_SCHEMA_VERSION:
            raise ValueError("Unsupported conformance corpus schema_version")
        root = self.root.resolve()
        cases = tuple(sorted(self.cases, key=lambda item: item.id))
        if not cases or len(cases) > MAX_CORPUS_CASES:
            raise ValueError(
                f"conformance corpus requires between 1 and {MAX_CORPUS_CASES} cases"
            )
        if len({case.id for case in cases}) != len(cases):
            raise ValueError("conformance corpus case ids must be unique")
        if any(case.root != root for case in cases):
            raise ValueError("conformance corpus cases must share one root")
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "cases", cases)

    @classmethod
    def from_json(
        cls,
        payload: Any,
        *,
        root: str | Path,
        require_sources: bool = True,
    ) -> ConformanceCorpus:
        data = _mapping(payload, "conformance corpus")
        raw_cases = data.get("cases")
        if not isinstance(raw_cases, list):
            raise ValueError("conformance corpus cases must be an array")
        resolved_root = Path(root).resolve()
        cases = []
        for raw_case in raw_cases:
            item = _mapping(raw_case, "conformance case")
            raw_roles = item.get("required_evidence_roles")
            if not isinstance(raw_roles, list) or not all(
                isinstance(role, str) for role in raw_roles
            ):
                raise ValueError(
                    "conformance case required_evidence_roles must be an array of strings"
                )
            case = ConformanceCase(
                id=item.get("id"),
                kind=item.get("kind"),
                source=item.get("source"),
                expected_status=item.get("expected_status"),
                required_evidence_roles=tuple(raw_roles),
                root=resolved_root,
                screen_id=item.get("screen_id"),
            )
            if require_sources and not case.source_path.is_file():
                raise ValueError(f"conformance case source does not exist: {case.source}")
            cases.append(case)
        return cls(
            schema_version=data.get("schema_version"),
            root=resolved_root,
            cases=tuple(cases),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "cases": [case.to_json() for case in self.cases],
        }


def load_conformance_corpus(
    manifest_path: str | Path,
    *,
    root: str | Path | None = None,
) -> ConformanceCorpus:
    path = Path(manifest_path).resolve()
    if root is None:
        if len(path.parents) < 3:
            raise ValueError("conformance corpus manifest has no repository root")
        root = path.parents[2]
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ConformanceCorpus.from_json(payload, root=root)


def _case_intent(case: ConformanceCase, workspace: Path) -> Path:
    if case.kind == "intent":
        return case.source_path
    payload = json.loads(case.source_path.read_text(encoding="utf-8"))
    screens = payload.get("screens") if isinstance(payload, dict) else None
    if not isinstance(screens, list):
        raise ValueError(f"AppBundle corpus source has no screens array: {case.source}")
    matches = [
        screen
        for screen in screens
        if isinstance(screen, dict) and screen.get("id") == case.screen_id
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("intent_bundle"), dict):
        raise ValueError(f"AppBundle corpus screen is missing or ambiguous: {case.screen_id}")
    path = workspace / "intent.json"
    path.write_text(
        json.dumps(matches[0]["intent_bundle"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _compile_errors(compiled: Mapping[str, Any]) -> list[Any]:
    errors = compiled.get("errors")
    if isinstance(errors, list):
        return errors
    issues = compiled.get("issues")
    return issues if isinstance(issues, list) else []


def _prepare_persistent_workspace(path: Path) -> Path:
    workspace = path.resolve()
    if workspace.exists():
        if workspace.is_symlink() or not workspace.is_dir() or any(workspace.iterdir()):
            raise FileExistsError(
                f"Conformance evidence output must be an empty directory: {workspace}"
            )
    else:
        workspace.mkdir(parents=True)
    return workspace


@contextmanager
def _corpus_workspace(evidence_out: str | Path | None) -> Iterator[tuple[Path, bool]]:
    if evidence_out is not None:
        yield _prepare_persistent_workspace(Path(evidence_out)), True
        return
    with tempfile.TemporaryDirectory(prefix="viewspec-conformance-") as temp_name:
        yield Path(temp_name), False


def run_conformance_corpus(
    corpus: ConformanceCorpus,
    *,
    install: bool = False,
    evidence_out: str | Path | None = None,
) -> dict[str, Any]:
    """Compile and verify every corpus case, optionally retaining review evidence."""
    case_reports = []
    with _corpus_workspace(evidence_out) as (workspace, retained):
        for case in corpus.cases:
            case_root = workspace / case.id
            case_root.mkdir()
            intent_path = _case_intent(case, case_root)
            source_sha256 = _file_sha256(case.source_path)
            intent_sha256 = _file_sha256(intent_path)
            artifact_dir = case_root / "artifact"
            compiled = compile_intent_bundle_file_tool(
                intent_path,
                artifact_dir,
                target="react-tailwind-tsx",
                cwd=corpus.root,
                allow_outside_cwd=True,
            )
            if compiled.get("ok") is not True:
                compile_errors = _compile_errors(compiled)
                case_reports.append(
                    {
                        "id": case.id,
                        "ok": False,
                        "expected_status": case.expected_status,
                        "actual_status": "compile_failed",
                        "source_sha256": source_sha256,
                        "intent_sha256": intent_sha256,
                        "artifact_sha256": None,
                        "plan_sha256": None,
                        "verification_id": None,
                        "diagnostic_codes": sorted(
                            {
                                str(item.get("code"))
                                for item in compile_errors
                                if isinstance(item, dict) and item.get("code")
                            }
                        ),
                        "evidence_roles": [],
                        "evidence": [],
                        "evidence_dir": None,
                        "compile_errors": compile_errors,
                    }
                )
                continue
            result = verify_local_artifact(
                artifact_dir,
                plan=VerificationPlan.default(),
                evidence_dir=case_root / "evidence",
                install=install,
                lineage=RetryLineage.root(),
            )
            evidence_roles = tuple(sorted({item.role for item in result.evidence}))
            ok = (
                result.status == case.expected_status
                and set(case.required_evidence_roles).issubset(evidence_roles)
            )
            case_reports.append(
                {
                    "id": case.id,
                    "ok": ok,
                    "expected_status": case.expected_status,
                    "actual_status": result.status,
                    "source_sha256": source_sha256,
                    "intent_sha256": intent_sha256,
                    "artifact_sha256": result.artifact_sha256,
                    "plan_sha256": result.plan.plan_sha256,
                    "verification_id": result.verification_id,
                    "diagnostic_codes": sorted({item.code for item in result.diagnostics}),
                    "evidence_roles": list(evidence_roles),
                    "evidence": [item.to_json() for item in result.evidence],
                    "evidence_dir": f"{case.id}/evidence" if retained else None,
                    "compile_errors": [],
                }
            )
    return {
        "schema_version": CONFORMANCE_CORPUS_SCHEMA_VERSION,
        "ok": all(item["ok"] for item in case_reports),
        "case_count": len(case_reports),
        "evidence_root": str(workspace) if retained else None,
        "cases": case_reports,
    }


__all__ = [
    "CONFORMANCE_CORPUS_SCHEMA_VERSION",
    "ConformanceCase",
    "ConformanceCorpus",
    "load_conformance_corpus",
    "run_conformance_corpus",
]
