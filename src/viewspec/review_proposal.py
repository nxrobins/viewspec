"""Read-only, source-bound renderings of pending Studio approval proposals."""

from __future__ import annotations

import secrets
import tempfile
import threading
from dataclasses import dataclass, replace
from typing import Any

from viewspec.converge_sessions import ConvergencePreview, ConvergenceSession, get_convergence_status
from viewspec.intent_patch import preview_intent_patch
from viewspec.review_compile import (
    STUDIO_COMPARE_TARGET,
    BuiltReviewRevision,
    GenerationGate,
    ReviewSourceSnapshot,
    build_review_revision,
    capture_source_snapshot,
)
from viewspec.review_contract import ReviewContractError
from viewspec.review_runtime import ReviewRuntime, _load_route_screens

_BUILD_SLOTS = threading.BoundedSemaphore(2)


def proposal_error(message: str) -> ReviewContractError:
    return ReviewContractError(
        "REVIEW_PROPOSAL_NOT_READY",
        message,
        "Compare the exact current and proposed interfaces before approving this change.",
        http_status=409,
    )


@dataclass(frozen=True)
class RenderedProposal:
    preview_id: str
    nonce: str
    baseline: BuiltReviewRevision
    candidate: BuiltReviewRevision
    routes: dict[str, str]
    directory: tempfile.TemporaryDirectory[str]

    @property
    def initial_route(self) -> str | None:
        return next(iter(self.routes), None)


class ProposalRenderer:
    """Retain one checked candidate, with at most one bounded build in flight."""

    def __init__(self, runtime: ReviewRuntime) -> None:
        self.runtime = runtime
        self._lock = threading.RLock()
        self._key: tuple[object, ...] | None = None
        self._worker: threading.Thread | None = None
        self._rendered: RenderedProposal | None = None
        self._error: str | None = None
        self._served: set[tuple[str, str]] = set()
        self._ready: set[str] = set()

    def reset(self) -> None:
        with self._lock:
            self._key = None
            self._error = None
            self._served.clear()
            self._ready.clear()
            if self._rendered is not None:
                self._rendered.directory.cleanup()
                self._rendered = None

    def request(self, session: ConvergenceSession) -> dict[str, Any]:
        pending = session.pending_preview
        if session.status != "awaiting_approval" or pending is None:
            self.reset()
            return {"status": "unavailable"}
        baseline = self.runtime.built
        snapshot = capture_source_snapshot(
            self.runtime.configuration.source_path,
            design_path=self.runtime.configuration.design_path,
        )
        if (
            snapshot.source_sha256 != pending.base_source_sha256
            or baseline.revision.source_sha256 != pending.base_source_sha256
            or snapshot.design_sha256 != baseline.revision.design_sha256
        ):
            self.reset()
            return {"status": "failed", "message": "The current interface changed. Ask for a new proposal."}
        key = (pending.preview_id, baseline.revision, pending.candidate_source_sha256)
        with self._lock:
            if key != self._key:
                self.reset()
                self._key = key
            if self._rendered is not None:
                return {"status": "ready", "preview_id": pending.preview_id, "nonce": self._rendered.nonce}
            if self._error is not None:
                return {
                    "status": "failed",
                    "message": "The proposed interface could not render. Reject it and ask for a revised change.",
                }
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._build,
                    args=(key, pending, baseline, snapshot),
                    name="viewspec-proposal-render",
                    daemon=True,
                )
                self._worker.start()
            return {"status": "building", "preview_id": pending.preview_id}

    def _build(
        self,
        key: tuple[object, ...],
        pending: ConvergencePreview,
        baseline: BuiltReviewRevision,
        snapshot: ReviewSourceSnapshot,
    ) -> None:
        directory = None
        acquired = _BUILD_SLOTS.acquire(timeout=5)
        try:
            if not acquired:
                raise proposal_error("The proposed preview build is busy.")
            preview = preview_intent_patch(snapshot.source_bytes.decode("utf-8"), pending.patch)
            if (
                preview.preview_id != pending.intent_preview_id
                or preview.candidate_source_sha256 != pending.candidate_source_sha256
                or preview.base_source_sha256 != pending.base_source_sha256
            ):
                raise proposal_error("The rendered proposal identity changed.")
            directory = tempfile.TemporaryDirectory(prefix=".proposal-", dir=self.runtime.session_dir)
            gate = GenerationGate()
            generation = gate.observe()
            candidate = build_review_revision(
                replace(
                    snapshot,
                    source_bytes=preview.candidate_text.encode("utf-8"),
                    source_sha256=preview.candidate_source_sha256,
                ),
                session_dir=directory.name,
                revision_number=1,
                generation=generation,
                gate=gate,
                target=baseline.revision.target,
                previous_manifest_indexes=baseline.manifest_indexes,
                allow_install=self.runtime.configuration.allow_install,
            )
            routes = _load_route_screens(candidate)
            if routes != _load_route_screens(baseline):
                raise proposal_error("The proposed interface changed the checked routes.")
            current = get_convergence_status(
                self.runtime.configuration.source_path,
                state_root=self.runtime.configuration.convergence_state_root,
            )
            fresh = capture_source_snapshot(
                self.runtime.configuration.source_path,
                design_path=self.runtime.configuration.design_path,
            )
            if (
                current.pending_preview is None
                or current.pending_preview.preview_id != pending.preview_id
                or fresh.source_sha256 != pending.base_source_sha256
                or fresh.design_sha256 != snapshot.design_sha256
            ):
                raise proposal_error("The proposal changed while its preview was building.")
            with self._lock:
                if self._key == key:
                    self._rendered = RenderedProposal(
                        pending.preview_id, secrets.token_hex(32), baseline, candidate, routes, directory
                    )
                    directory = None
        except Exception as error:
            with self._lock:
                if self._key == key:
                    self._error = type(error).__name__
        finally:
            if directory is not None:
                directory.cleanup()
            if acquired:
                _BUILD_SLOTS.release()

    def active(self, preview_id: str) -> RenderedProposal:
        session = get_convergence_status(
            self.runtime.configuration.source_path,
            state_root=self.runtime.configuration.convergence_state_root,
        )
        if self.request(session).get("status") != "ready":
            raise proposal_error("The current proposal has no checked rendered preview.")
        with self._lock:
            if self._rendered is None or self._rendered.preview_id != preview_id:
                raise proposal_error("This rendered preview is stale.")
            return self._rendered

    def served(self, preview_id: str, target: str, side: str) -> None:
        rendered = self.active(preview_id)
        with self._lock:
            self._assert_current(rendered)
            self._served.add((target, side))

    def confirm(self, payload: dict[str, object]) -> None:
        if set(payload) != {"preview_id", "nonce", "target", "observations"}:
            raise proposal_error("The rendered comparison handshake is invalid.")
        rendered = self.active(str(payload["preview_id"]))
        with self._lock:
            self._assert_current(rendered)
            self._confirm_locked(rendered, payload)

    def _confirm_locked(self, rendered: RenderedProposal, payload: dict[str, object]) -> None:
        target = payload["target"]
        allowed = (
            {"html-tailwind-app", "react-tailwind-app"}
            if rendered.baseline.revision.target == STUDIO_COMPARE_TARGET
            else {rendered.baseline.revision.target}
        )
        observations = payload["observations"]
        if (
            payload["nonce"] != rendered.nonce
            or not isinstance(target, str)
            or target not in allowed
            or not isinstance(observations, list)
            or len(observations) != 2
        ):
            raise proposal_error("The rendered comparison does not match the pending proposal.")
        sides = set()
        contexts = []
        for observation in observations:
            if not isinstance(observation, dict) or set(observation) != {"side", "route", "screen_id"}:
                raise proposal_error("The comparison must observe both current and proposed interfaces.")
            side = observation["side"]
            if (
                not isinstance(side, str)
                or side not in {"current", "proposed"}
                or side in sides
                or (target, side) not in self._served
            ):
                raise proposal_error("Both exact comparison frames must render before approval.")
            sides.add(side)
            route, screen = observation["route"], observation["screen_id"]
            if rendered.routes and (not isinstance(route, str) or rendered.routes.get(route) != screen):
                raise proposal_error("The rendered proposal screen is invalid.")
            if not rendered.routes and (route is not None or screen is not None):
                raise proposal_error("The one-screen comparison context is invalid.")
            contexts.append((route, screen))
        if contexts[0] != contexts[1]:
            raise proposal_error("Compare the same screen on both sides before approving.")
        self._ready.add(target)

    def require_ready(self, preview_id: str) -> None:
        rendered = self.active(preview_id)
        with self._lock:
            self._assert_current(rendered)
            if not self._ready:
                raise proposal_error("Both current and proposed interfaces must render before approval.")

    def _assert_current(self, rendered: RenderedProposal) -> None:
        if self._rendered is not rendered:
            raise proposal_error("The rendered proposal changed during comparison.")
