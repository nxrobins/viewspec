"""High-level Review V0 orchestration over immutable compile and durable session contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shutil
import stat
from typing import Mapping

from viewspec._version import __version__
from viewspec.review_compile import (
    BuiltReviewRevision,
    GenerationGate,
    ReviewSourceSnapshot,
    bounded_review_operation,
    build_review_revision,
    capture_source_snapshot,
    compute_review_semantic_diff,
    load_review_revision,
    load_review_semantic_diff,
)
from viewspec.review_contract import ReviewContext, ReviewContractError, ReviewEvent, canonical_json_bytes
from viewspec.review_session import ReviewSession, read_current_revision
from viewspec.verification import VerificationResult


REVIEW_CONFIG_SCHEMA_VERSION = 1
MAX_CONFIG_BYTES = 64 * 1024
MAX_RETAINED_SESSIONS = 64
MAX_ARTIFACT_REVISIONS = 8
MAX_VERIFICATION_EVIDENCE_SETS = 2
_EMPTY_PLUGIN_REGISTRY_SHA256 = hashlib.sha256(b"viewspec.review.plugins.v0\x00").hexdigest()


def default_review_state_root() -> Path:
    configured = os.environ.get("VIEWSPEC_REVIEW_STATE_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".viewspec" / "review"


def review_session_dir(source_path: str | Path, state_root: str | Path) -> Path:
    source = Path(os.path.abspath(Path(source_path).expanduser()))
    root = Path(os.path.abspath(Path(state_root).expanduser()))
    session_key = hashlib.sha256(b"viewspec.review.source.v1\x00" + str(source).encode("utf-8")).hexdigest()[:32]
    return root / "sessions" / session_key


@dataclass(frozen=True, slots=True)
class ReviewRuntimeConfiguration:
    source_path: str
    design_path: str | None
    source_kind: str
    target: str
    convergence_state_root: str | None = None
    compiler_version: str = __version__
    contract_profile: str = "local_v1"
    plugin_registry_sha256: str = _EMPTY_PLUGIN_REGISTRY_SHA256
    requested_port: int = 4388
    verification_plan_sha256: str | None = None
    allow_install: bool = False
    schema_version: int = REVIEW_CONFIG_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != REVIEW_CONFIG_SCHEMA_VERSION:
            raise _configuration_conflict("Review configuration schema is unsupported.")
        if self.source_kind not in {"intent_bundle", "app_bundle"}:
            raise _configuration_conflict("Review configuration source kind is unsupported.")
        if not isinstance(self.source_path, str) or not self.source_path:
            raise _configuration_conflict("Review configuration source path is missing.")
        if self.design_path is not None and not isinstance(self.design_path, str):
            raise _configuration_conflict("Review configuration design path is invalid.")
        if self.convergence_state_root is not None and (
            not isinstance(self.convergence_state_root, str)
            or not os.path.isabs(self.convergence_state_root)
        ):
            raise _configuration_conflict("Review convergence state root must be an absolute path.")
        if type(self.requested_port) is not int or not 1024 <= self.requested_port <= 65535:
            raise _configuration_conflict("Review configuration port is outside 1024 through 65535.")
        if type(self.allow_install) is not bool:
            raise _configuration_conflict("Review dependency-install consent must be a boolean.")

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_path": self.source_path,
            "design_path": self.design_path,
            "source_kind": self.source_kind,
            "target": self.target,
            "convergence_state_root": self.convergence_state_root,
            "compiler_version": self.compiler_version,
            "contract_profile": self.contract_profile,
            "plugin_registry_sha256": self.plugin_registry_sha256,
            "requested_port": self.requested_port,
            "verification_plan_sha256": self.verification_plan_sha256,
            "allow_install": self.allow_install,
        }

    @classmethod
    def from_json(cls, value: object) -> ReviewRuntimeConfiguration:
        if not isinstance(value, dict):
            raise _configuration_conflict("Stored Review configuration is not an object.")
        allowed = {
            "schema_version",
            "source_path",
            "design_path",
            "source_kind",
            "target",
            "convergence_state_root",
            "compiler_version",
            "contract_profile",
            "plugin_registry_sha256",
            "requested_port",
            "verification_plan_sha256",
            "allow_install",
        }
        legacy_allowed = allowed - {"convergence_state_root"}
        fields = set(value)
        if fields != allowed and fields != legacy_allowed:
            raise _configuration_conflict("Stored Review configuration fields do not match V0.")
        return cls(**{**value, "convergence_state_root": value.get("convergence_state_root")})


class ReviewRuntime:
    """One active local Review session and its exact checked current revision."""

    def __init__(
        self,
        *,
        configuration: ReviewRuntimeConfiguration,
        state_root: Path,
        session_dir: Path,
        session: ReviewSession,
        built: BuiltReviewRevision,
        gate: GenerationGate,
    ) -> None:
        self.configuration = configuration
        self.state_root = state_root
        self.session_dir = session_dir
        self.session = session
        self.built = built
        self._gate = gate
        self.last_source_failure: dict[str, object] | None = None
        self._route_screens = _load_route_screens(built)
        self.semantic_diff = load_review_semantic_diff(built)
        self.verification = _load_verification(built)

    @classmethod
    def resume(
        cls,
        source_path: str | Path,
        *,
        state_root: str | Path,
    ) -> ReviewRuntime:
        source = Path(os.path.abspath(Path(source_path).expanduser()))
        root = Path(os.path.abspath(Path(state_root).expanduser()))
        session_dir = review_session_dir(source, root)
        config_path = session_dir / "session.json"
        if not config_path.is_file() or config_path.is_symlink():
            raise ReviewContractError(
                "REVIEW_SESSION_NOT_FOUND",
                "No Review session exists for this canonical source path.",
                "Run viewspec review SOURCE before polling or ending it.",
                http_status=404,
            )
        config = _read_configuration(config_path)
        if config.source_path != str(source):
            raise _configuration_conflict("Stored Review source identity does not match the requested canonical path.")
        current = read_current_revision(session_dir)
        built = load_review_revision(session_dir, current.number)
        session = ReviewSession(session_dir, revision=current)
        return cls(
            configuration=config,
            state_root=root,
            session_dir=session_dir,
            session=session,
            built=built,
            gate=GenerationGate(),
        )

    @classmethod
    @bounded_review_operation("REVIEW_COMPILE_TIMEOUT", 60)
    def open(
        cls,
        source_path: str | Path,
        *,
        state_root: str | Path,
        convergence_state_root: str | Path | None = None,
        target: str | None = None,
        design_path: str | Path | None = None,
        requested_port: int = 4388,
        verification_plan_sha256: str | None = None,
        allow_install: bool = False,
        reopen: bool = False,
    ) -> ReviewRuntime:
        snapshot = capture_source_snapshot(source_path, design_path=design_path)
        selected_target = target or ("html-tailwind" if snapshot.source_kind == "intent_bundle" else "html-tailwind-app")
        root = Path(os.path.abspath(Path(state_root).expanduser()))
        convergence_root = (
            str(Path(os.path.abspath(Path(convergence_state_root).expanduser())))
            if convergence_state_root is not None
            else None
        )
        _ensure_private_directory(root)
        sessions_dir = root / "sessions"
        _ensure_private_directory(sessions_dir)
        session_dir = review_session_dir(snapshot.source_path, root)
        config = ReviewRuntimeConfiguration(
            source_path=str(snapshot.source_path),
            design_path=str(snapshot.design_path) if snapshot.design_path is not None else None,
            source_kind=snapshot.source_kind,
            target=selected_target,
            convergence_state_root=convergence_root,
            requested_port=requested_port,
            verification_plan_sha256=verification_plan_sha256,
            allow_install=allow_install if selected_target == "react-tailwind-app" else False,
        )
        config_path = session_dir / "session.json"
        gate = GenerationGate()
        if config_path.exists():
            stored = _read_configuration(config_path)
            if stored != config:
                raise _configuration_conflict("Existing Review session configuration does not match this invocation.")
            current = read_current_revision(session_dir)
            built = load_review_revision(session_dir, current.number)
            session = ReviewSession(session_dir, revision=current)
            runtime = cls(
                configuration=config,
                state_root=root,
                session_dir=session_dir,
                session=session,
                built=built,
                gate=gate,
            )
            if session.ended_by == "human" and not reopen:
                raise ReviewContractError(
                    "REVIEW_SESSION_ENDED_BY_HUMAN",
                    "The reviewer ended this Review session.",
                    "Pass --reopen only after the user explicitly asks to reopen it.",
                    http_status=409,
                )
            if session.ended_by is not None:
                session.reopen()
            if snapshot.source_sha256 != current.source_sha256 or snapshot.design_sha256 != current.design_sha256:
                runtime._rebuild_from_snapshot(snapshot)
            return runtime

        retained_sessions = sum(1 for path in sessions_dir.iterdir() if path.is_dir() and (path / "session.json").is_file())
        if retained_sessions >= MAX_RETAINED_SESSIONS:
            raise ReviewContractError(
                "REVIEW_SESSION_LIMIT_EXCEEDED",
                f"Review state already retains {MAX_RETAINED_SESSIONS} sessions.",
                "Purge an ended retained session before opening another.",
                cli_exit=2,
            )
        if session_dir.exists() and any(path.name != ".writer.lock" for path in session_dir.iterdir()):
            raise ReviewContractError(
                "REVIEW_JOURNAL_INVALID",
                "Review session directory exists without a complete private configuration.",
                "Inspect or remove the incomplete private session before retrying.",
                http_status=500,
            )
        _ensure_private_directory(session_dir)
        generation = gate.observe()
        built = build_review_revision(
            snapshot,
            session_dir=session_dir,
            revision_number=1,
            generation=generation,
            gate=gate,
            target=selected_target,
            allow_install=config.allow_install,
        )
        session = ReviewSession(session_dir, revision=built.revision)
        _write_configuration(config_path, config)
        return cls(
            configuration=config,
            state_root=root,
            session_dir=session_dir,
            session=session,
            built=built,
            gate=gate,
        )

    @bounded_review_operation("REVIEW_COMPILE_TIMEOUT", 60)
    def rebuild(self) -> BuiltReviewRevision:
        try:
            snapshot = capture_source_snapshot(
                self.configuration.source_path,
                design_path=self.configuration.design_path,
            )
        except ReviewContractError as exc:
            self.last_source_failure = {
                "candidate_source_sha256": None,
                "code": exc.code,
                "message": exc.message[:2048],
                "fix": exc.fix[:2048],
            }
            raise
        return self._rebuild_from_snapshot(snapshot)

    def _rebuild_from_snapshot(self, snapshot: ReviewSourceSnapshot) -> BuiltReviewRevision:
        generation = self._gate.observe()
        try:
            semantic_diff = compute_review_semantic_diff(
                self.built,
                snapshot,
                to_revision=self.session.revision.number + 1,
            )
            built = build_review_revision(
                snapshot,
                session_dir=self.session_dir,
                revision_number=self.session.revision.number + 1,
                generation=generation,
                gate=self._gate,
                target=self.configuration.target,
                previous_manifest_indexes=self.built.manifest_indexes,
                semantic_diff=semantic_diff,
                allow_install=self.configuration.allow_install,
            )
            self.session.promote_revision(built.revision)
        except ReviewContractError as exc:
            self.last_source_failure = {
                "candidate_source_sha256": snapshot.source_sha256,
                "code": exc.code,
                "message": exc.message[:2048],
                "fix": exc.fix[:2048],
            }
            raise
        self.built = built
        self._route_screens = _load_route_screens(built)
        self.semantic_diff = semantic_diff.to_json()
        self.verification = _load_verification(built)
        self.last_source_failure = None
        _prune_artifact_revisions(self.session_dir, keep=MAX_ARTIFACT_REVISIONS)
        return built

    def submit_browser_event(
        self,
        *,
        idempotency_key: str,
        kind: str,
        body: str,
        screen_id: str | None,
        dom_ancestors: tuple[str, ...],
        page_level: bool,
        context: ReviewContext,
        client_provenance: Mapping[str, object] | None = None,
    ) -> ReviewEvent:
        del client_provenance  # Browser provenance is intentionally never consulted.
        if not isinstance(context, ReviewContext) or not isinstance(dom_ancestors, tuple):
            raise ReviewContractError(
                "REVIEW_EVENT_INVALID",
                "Browser Review event shape is invalid.",
                "Send one bounded context and a light-DOM ancestor id list.",
                http_status=400,
            )
        index = self.built.manifest_indexes.get(screen_id)
        if index is None:
            raise ReviewContractError(
                "REVIEW_TARGET_NOT_IN_MANIFEST",
                "Browser screen is not present in the checked current revision.",
                "Reload the current revision and select a checked screen.",
                http_status=422,
            )
        target = index.page_target() if page_level else index.resolve_dom_ancestors(dom_ancestors)
        self._assert_context(context, screen_id=screen_id)
        self._assert_selection(context, target_dom_id=target.dom_id, screen_id=screen_id)
        return self.session.submit_event(
            idempotency_key=idempotency_key,
            kind=kind,
            body=body,
            target=target,
            context=context,
        )

    def submit_browser_event_and_end(
        self,
        *,
        idempotency_key: str,
        kind: str,
        body: str,
        screen_id: str | None,
        dom_ancestors: tuple[str, ...],
        page_level: bool,
        context: ReviewContext,
        client_provenance: Mapping[str, object] | None = None,
    ) -> ReviewEvent:
        del client_provenance
        if not isinstance(context, ReviewContext) or not isinstance(dom_ancestors, tuple):
            raise ReviewContractError(
                "REVIEW_EVENT_INVALID",
                "Final browser Review event shape is invalid.",
                "Send one bounded final context and light-DOM ancestor id list.",
                http_status=400,
            )
        index = self.built.manifest_indexes.get(screen_id)
        if index is None:
            raise ReviewContractError(
                "REVIEW_TARGET_NOT_IN_MANIFEST",
                "Final browser target is not present in the checked current revision.",
                "Reload the current revision and select a checked target.",
                http_status=422,
            )
        target = index.page_target() if page_level else index.resolve_dom_ancestors(dom_ancestors)
        self._assert_context(context, screen_id=screen_id)
        self._assert_selection(context, target_dom_id=target.dom_id, screen_id=screen_id)
        return self.session.submit_event_and_end(
            idempotency_key=idempotency_key,
            kind=kind,
            body=body,
            target=target,
            context=context,
        )

    def record_verification(self, result: VerificationResult) -> None:
        if not isinstance(result, VerificationResult):
            raise TypeError("result must be a VerificationResult")
        if result.artifact_sha256 != self.built.revision.artifact_set_sha256:
            raise ReviewContractError(
                "REVIEW_VERIFICATION_STALE",
                "Verification artifact identity does not match the displayed Review revision.",
                "Run verification again against the exact current artifact set.",
                http_status=409,
            )
        if self.configuration.verification_plan_sha256 not in {None, result.plan.plan_sha256}:
            raise ReviewContractError(
                "REVIEW_VERIFICATION_STALE",
                "Verification plan identity does not match the Review session configuration.",
                "Use the exact configured canonical viewport verification plan.",
                http_status=409,
            )
        result_json = result.to_json()
        diagnostics = result_json.get("diagnostics", [])
        diagnostic_bytes = 0
        if not isinstance(diagnostics, list) or len(diagnostics) > 64:
            raise _verification_failed("Verification projection contains more than 64 diagnostics.")
        for diagnostic in diagnostics:
            encoded = canonical_json_bytes(diagnostic)
            if len(encoded) > 2 * 1024:
                raise _verification_failed("One verification diagnostic exceeds 2 KiB.")
            diagnostic_bytes += len(encoded)
        if diagnostic_bytes > 96 * 1024:
            raise _verification_failed("Verification diagnostics exceed 96 KiB aggregate.")
        payload = {
            "schema_version": 1,
            "revision": self.built.revision.to_json(),
            "result_sha256": result.result_sha256,
            "result": result_json,
        }
        directory = self.built.revision_dir / "verification"
        _ensure_private_directory(directory)
        _write_atomic_private_json(directory / "result.json", payload)
        self.verification = _verification_projection(result)

    def status(self) -> dict[str, object]:
        return {
            "review_id": self.session.review_id,
            "status": "ended" if self.session.ended_by is not None else "active",
            "ended_by": self.session.ended_by,
            "source_kind": self.built.revision.source_kind,
            "target": self.built.revision.target,
            "revision": self.built.revision.number,
            "check_status": "passed",
            "verification_status": self.verification["status"],
            "verification": self.verification,
            "queued_events": len(self.session.events) - self.session.cursor,
            "source_failure": self.last_source_failure,
            "semantic_diff": self.semantic_diff,
            "compaction_failure": self.session.compaction_failure,
        }

    @property
    def routes(self) -> tuple[str, ...]:
        """Return the current checked route names for the browser chrome."""

        return tuple(sorted(self._route_screens))

    def _assert_context(self, context: ReviewContext, *, screen_id: str | None) -> None:
        if context.control_values:
            raise ReviewContractError(
                "REVIEW_CONTEXT_FORBIDDEN",
                "V0 source contracts declare no review_safe controls, so control capture is forbidden.",
                "Submit the event without control values until semantic source explicitly supports review_safe controls.",
                http_status=422,
            )
        if context.screen_id != screen_id:
            raise ReviewContractError(
                "REVIEW_CONTEXT_FORBIDDEN",
                "Browser context screen does not match the server-resolved target screen.",
                "Reload the current screen and submit one internally consistent context.",
                http_status=422,
            )
        if context.route is not None and self._route_screens.get(context.route) != screen_id:
            raise ReviewContractError(
                "REVIEW_CONTEXT_FORBIDDEN",
                "Browser route does not resolve to the target screen in the checked AppBundle.",
                "Use the exact current checked route or omit route context.",
                http_status=422,
            )

    def _assert_selection(self, context: ReviewContext, *, target_dom_id: str | None, screen_id: str | None) -> None:
        selected = context.selected_text
        if selected is None:
            return
        if target_dom_id is None:
            raise ReviewContractError(
                "REVIEW_SELECTION_UNSUPPORTED",
                "Selected text requires one manifest-backed source-node target.",
                "Select text inside a compiler-owned element instead of a page target.",
                http_status=422,
            )
        if screen_id is None:
            html_path = self.built.artifact_dir / "index.html"
        else:
            html_path = self.built.artifact_dir / "screens" / screen_id / "artifact" / "index.html"
        parser = _ElementTextParser(target_dom_id)
        try:
            parser.feed(html_path.read_text(encoding="utf-8"))
            parser.close()
        except (OSError, UnicodeDecodeError) as exc:
            raise ReviewContractError(
                "REVIEW_REVISION_IDENTITY_MISMATCH",
                "Could not revalidate selected text against the promoted artifact.",
                "Reload a complete checked revision before annotating.",
                http_status=500,
                cli_exit=1,
            ) from exc
        visible = "".join(parser.text)
        matches = []
        start = 0
        while True:
            index = visible.find(selected.quote, start)
            if index < 0:
                break
            before = visible[:index]
            after = visible[index + len(selected.quote) :]
            if before.endswith(selected.prefix) and after.startswith(selected.suffix):
                matches.append(index)
            start = index + max(1, len(selected.quote))
        if not parser.found or not matches:
            raise ReviewContractError(
                "REVIEW_SELECTION_UNSUPPORTED",
                "Selected text is not proven inside the server-resolved manifest node.",
                "Reload the current frame and select visible text within one compiler-owned node.",
                http_status=422,
            )


def _load_route_screens(built: BuiltReviewRevision) -> dict[str, str]:
    if built.revision.source_kind != "app_bundle":
        return {}
    manifest_name = "review_manifest.json" if built.revision.target == "react-tailwind-app" else "shell_manifest.json"
    try:
        payload = json.loads(built.artifact_dir.joinpath(manifest_name).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewContractError(
            "REVIEW_REVISION_IDENTITY_MISMATCH",
            f"Could not load checked AppBundle routes: {exc}",
            "Do not serve the revision; rebuild a complete checked AppBundle.",
            http_status=500,
            cli_exit=1,
        ) from exc
    routes = payload.get("routes") if isinstance(payload, dict) else None
    if not isinstance(routes, list):
        raise ReviewContractError(
            "REVIEW_MANIFEST_AMBIGUOUS",
            "Checked AppBundle shell has no canonical routes array.",
            "Recompile the AppBundle with the current compiler.",
            http_status=422,
        )
    result: dict[str, str] = {}
    for route in routes:
        if not isinstance(route, dict) or not isinstance(route.get("path"), str) or not isinstance(route.get("screenId"), str):
            raise ReviewContractError(
                "REVIEW_MANIFEST_AMBIGUOUS",
                "Checked AppBundle shell contains an invalid route mapping.",
                "Recompile the AppBundle with canonical route identities.",
                http_status=422,
            )
        path = route["path"]
        if path in result:
            raise ReviewContractError(
                "REVIEW_MANIFEST_AMBIGUOUS",
                "Checked AppBundle shell repeats a route path.",
                "Recompile an AppBundle with unique routes.",
                http_status=422,
            )
        result[path] = route["screenId"]
    return result


def _load_verification(built: BuiltReviewRevision) -> dict[str, object]:
    path = built.revision_dir / "verification" / "result.json"
    if not path.exists():
        return {
            "status": "not_run",
            "verification_id": None,
            "result_sha256": None,
            "diagnostics": [],
            "evidence_refs": [],
        }
    try:
        raw = path.read_bytes()
        if len(raw) > 256 * 1024:
            raise ValueError("verification record exceeds 256 KiB")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("verification record root is not an object")
        revision = payload.get("revision")
        if revision != built.revision.to_json():
            raise ValueError("verification revision identity differs")
        result = VerificationResult.from_json(payload.get("result"))
        if (
            result.artifact_sha256 != built.revision.artifact_set_sha256
            or payload.get("result_sha256") != result.result_sha256
        ):
            raise ValueError("verification result identity differs")
        return _verification_projection(result)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise ReviewContractError(
            "REVIEW_VERIFICATION_STALE",
            f"Stored verification no longer matches the current Review revision: {exc}",
            "Discard the stale result and rerun verification.",
            http_status=409,
        ) from exc


def _verification_projection(result: VerificationResult) -> dict[str, object]:
    refs = sorted({ref for diagnostic in result.diagnostics for ref in diagnostic.evidence_refs})
    return {
        "status": result.status,
        "verification_id": result.verification_id,
        "result_sha256": result.result_sha256,
        "plan_sha256": result.plan.plan_sha256,
        "diagnostics": [item.to_json() for item in result.diagnostics],
        "evidence_refs": refs,
    }


def _verification_failed(message: str) -> ReviewContractError:
    return ReviewContractError(
        "REVIEW_VERIFICATION_FAILED",
        message,
        "Reduce or rerun the bounded canonical viewport verification.",
        http_status=422,
    )


class _ElementTextParser(HTMLParser):
    _VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, dom_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self.dom_id = dom_id
        self.depth = 0
        self.found = False
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.depth:
            if tag not in self._VOID_TAGS:
                self.depth += 1
            return
        if dict(attrs).get("id") == self.dom_id:
            self.found = True
            if tag not in self._VOID_TAGS:
                self.depth = 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del tag, attrs

    def handle_endtag(self, tag: str) -> None:
        del tag
        if self.depth:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.text.append(data)


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    value = path.lstat()
    owner = os.geteuid() if hasattr(os, "geteuid") else value.st_uid
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode) or value.st_uid != owner:
        raise ReviewContractError(
            "REVIEW_FILESYSTEM_UNSAFE",
            "Review state path is not an owner-controlled regular directory.",
            "Choose a private local state directory owned by the current user.",
            cli_exit=2,
        )
    path.chmod(0o700)


def _prune_artifact_revisions(session_dir: Path, *, keep: int) -> None:
    revisions_dir = session_dir / "revisions"
    numbered: list[tuple[int, Path]] = []
    for path in revisions_dir.iterdir():
        value = path.lstat()
        if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode) or not path.name.isdigit():
            raise ReviewContractError(
                "REVIEW_FILESYSTEM_UNSAFE",
                "Review revision store contains an unsafe or unexpected entry.",
                "Inspect the private state directory before continuing.",
                cli_exit=2,
            )
        numbered.append((int(path.name), path))
    ordered = sorted(numbered)
    for _, path in ordered[:-keep]:
        shutil.rmtree(path)
    for _, path in ordered[-keep:-MAX_VERIFICATION_EVIDENCE_SETS]:
        verification = path / "verification"
        if verification.exists():
            if verification.is_symlink() or not verification.is_dir():
                raise ReviewContractError(
                    "REVIEW_FILESYSTEM_UNSAFE",
                    "Retained verification state is not a private directory.",
                    "Inspect the private Review revision store.",
                    cli_exit=2,
                )
            shutil.rmtree(verification)


def _read_configuration(path: Path) -> ReviewRuntimeConfiguration:
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or stat.S_ISLNK(value.st_mode) or value.st_size > MAX_CONFIG_BYTES:
        raise _configuration_conflict("Stored Review configuration file is unsafe or oversized.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _configuration_conflict(f"Stored Review configuration is invalid: {exc}") from exc
    return ReviewRuntimeConfiguration.from_json(payload)


def _write_configuration(path: Path, config: ReviewRuntimeConfiguration) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        content = canonical_json_bytes(config.to_json())
        offset = 0
        while offset < len(content):
            offset += os.write(descriptor, content[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise ReviewContractError(
            "REVIEW_REVISION_WRITE_FAILED",
            f"Could not durably write the private Review configuration: {exc}",
            "Restore writable private state storage and retry.",
            http_status=507,
            cli_exit=1,
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _write_atomic_private_json(path: Path, payload: dict[str, object]) -> None:
    content = canonical_json_bytes(payload)
    if len(content) > 256 * 1024:
        raise _verification_failed("Verification result record exceeds 256 KiB.")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        offset = 0
        while offset < len(content):
            offset += os.write(descriptor, content[offset:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as exc:
        raise ReviewContractError(
            "REVIEW_REVISION_WRITE_FAILED",
            f"Could not persist the bounded verification result: {exc}",
            "Restore writable private Review storage and retry verification.",
            http_status=507,
            cli_exit=1,
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _configuration_conflict(message: str) -> ReviewContractError:
    return ReviewContractError(
        "REVIEW_SESSION_CONFIGURATION_CONFLICT",
        message,
        "Resume with the exact existing configuration or choose an explicit new state directory.",
        http_status=409,
    )


__all__ = [
    "ReviewRuntime",
    "ReviewRuntimeConfiguration",
    "default_review_state_root",
    "review_session_dir",
]
