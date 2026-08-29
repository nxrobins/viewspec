from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from viewspec.app_starters import starter_app_bundle
import viewspec.review_compile as review_compile
from viewspec.review_compile import STUDIO_COMPARE_TARGET
from viewspec.review_runtime import ReviewRuntime
from viewspec.studio_review_service import StudioReviewService, StudioReviewServiceError
from viewspec.studio_share import prepare_studio_share


_REAL_SUBPROCESS_RUN = review_compile.subprocess.run
_SIGNING_KEY = b"test-only-studio-review-signing-key-material"


def _fake_react_npm(command, *, cwd, **kwargs):
    if tuple(command[:3]) == ("npm", "run", "build"):
        runtime = Path(cwd) / "runtime-dist"
        assets = runtime / "assets"
        assets.mkdir(parents=True)
        assets.joinpath("main.js").write_text("document.getElementById('root').textContent='ready';", encoding="utf-8")
        assets.joinpath("main.css").write_text("body{margin:0}", encoding="utf-8")
        runtime.joinpath("index.html").write_text(
            '<!doctype html><html><head><link rel="stylesheet" crossorigin href="./assets/main.css"></head>'
            '<body><div id="root"></div><script type="module" crossorigin src="./assets/main.js"></script></body></html>',
            encoding="utf-8",
        )
        return object()
    if tuple(command[:2]) == ("npm", "ci"):
        return object()
    return _REAL_SUBPROCESS_RUN(command, cwd=cwd, **kwargs)


def _prepared_package(tmp_path: Path, monkeypatch, *, app_payload: dict | None = None) -> Path:
    source = tmp_path / "viewspec.app.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(app_payload or starter_app_bundle("internal_tool"), sort_keys=True), encoding="utf-8")
    state = tmp_path / "review-state"
    monkeypatch.setattr("viewspec.review_compile.subprocess.run", _fake_react_npm)
    ReviewRuntime.open(
        source,
        state_root=state,
        target=STUDIO_COMPARE_TARGET,
        allow_install=True,
    )
    prepared = prepare_studio_share(source, state_root=state, cwd=tmp_path)
    return Path(prepared["paths"]["package"])


def _passing_verifier(_package: Path, envelope: dict[str, object]) -> dict[str, object]:
    revision = envelope["revision"]
    totals = envelope["totals"]
    assert isinstance(revision, dict)
    assert isinstance(totals, dict)
    return {
        "schema_version": 1,
        "status": "passed",
        "verifier_id": "test-sandbox-v1",
        "package_id": envelope["package_id"],
        "source_sha256": revision["source_sha256"],
        "artifact_set_sha256": revision["artifact_set_sha256"],
        "root_manifest_sha256": revision["root_manifest_sha256"],
        "inspection_sha256": revision["inspection_sha256"],
        "target_artifact_sets": revision["target_artifact_sets"],
        "rebuild": {
            "evidence_sha256": "b" * 64,
            "expected_inventory_sha256": revision["artifact_set_sha256"],
            "observed_inventory_sha256": revision["artifact_set_sha256"],
            "source_only_request": True,
            "install_used": False,
            "lifecycle_hooks_disabled": True,
            "uploaded_artifacts_executed": False,
        },
        "sandbox": {
            "network": "denied",
            "lifecycle_hooks": "disabled",
            "arbitrary_commands": "disabled",
            "limits": {
                "cpu_seconds": 20,
                "memory_bytes": 256 * 1024 * 1024,
                "wall_seconds": 90,
                "file_count": totals["file_count"],
                "byte_count": totals["bytes"],
            },
        },
    }


def _service(tmp_path: Path, *, clock=None, verifier=_passing_verifier) -> StudioReviewService:
    return StudioReviewService(
        tmp_path / "service",
        signing_key=_SIGNING_KEY,
        verifier=verifier,
        **({"clock": clock} if clock is not None else {}),
    )


def _create(service: StudioReviewService, package: Path, *, key: str = "create-session-0001") -> dict[str, object]:
    return service.create_session(
        package,
        disclosure_accepted=True,
        expires_in_seconds=3600,
        idempotency_key=key,
    )


def _capability(response: dict[str, object], role: str) -> str:
    capabilities = response["fragment_capabilities"]
    assert isinstance(capabilities, dict)
    fragment = capabilities[role]
    assert isinstance(fragment, str)
    return fragment.removeprefix("#cap=")


def _cookie(service: StudioReviewService, created: dict[str, object], role: str) -> str:
    session = created["session"]
    assert isinstance(session, dict)
    exchanged = service.exchange_capability(str(session["id"]), _capability(created, role))
    return str(exchanged["cookie_value"])


def _comment_context(read: dict[str, object], *, target_kind: str = "node") -> dict[str, object]:
    session = read["session"]
    screens = read["screens"]
    routes = read["routes"]
    assert isinstance(session, dict) and isinstance(screens, list) and isinstance(routes, list)
    screen = screens[0]
    assert isinstance(screen, dict)
    route = next(item for item in routes if item["screen_id"] == screen["id"])
    targets = screen["targets"]
    assert isinstance(targets, dict)
    return {
        "revision_identity_sha256": session["revision_identity_sha256"],
        "route": route["path"],
        "screen_id": screen["id"],
        "semantic_identity_sha256": screen["semantic_identity_sha256"],
        "viewport_width": 390,
        "target": {"kind": target_kind, "id": targets[target_kind][0]},
        "replay_evidence_ref": None,
    }


def test_session_creation_requires_disclosure_and_exact_bounded_verification(tmp_path, monkeypatch) -> None:
    package = _prepared_package(tmp_path / "project", monkeypatch)
    service = _service(tmp_path)

    with pytest.raises(StudioReviewServiceError) as disclosure:
        service.create_session(
            package,
            disclosure_accepted=False,
            expires_in_seconds=3600,
            idempotency_key="create-session-0001",
        )
    assert disclosure.value.code == "STUDIO_REVIEW_DISCLOSURE_REQUIRED"

    created = _create(service, package)
    assert created["status"] == "active"
    assert created["session"]["private"] is True
    assert created["response_policy"] == {
        "visibility": "unlisted_private",
        "robots": "noindex, noarchive",
        "cache_control": "private, no-store",
        "referrer_policy": "no-referrer",
        "capabilities_in_artifact_urls": False,
        "analytics": "disabled",
    }
    assert created["verification"]["sandbox"]["network"] == "denied"
    assert service.verify_receipt(created["receipt"]) is True
    assert created["fragment_capabilities"]["owner"].startswith("#cap=vsc_")
    assert created["fragment_capabilities"]["reviewer"].startswith("#cap=vsc_")

    repeated = _create(service, package)
    assert repeated == created
    with pytest.raises(StudioReviewServiceError) as conflict:
        service.create_session(
            package,
            disclosure_accepted=True,
            expires_in_seconds=7200,
            idempotency_key="create-session-0001",
        )
    assert conflict.value.code == "STUDIO_REVIEW_IDEMPOTENCY_CONFLICT"
    assert len([path for path in service.objects.iterdir() if not path.name.startswith(".")]) == 1


def test_failed_verification_never_creates_a_session_or_link(tmp_path, monkeypatch) -> None:
    package = _prepared_package(tmp_path / "project", monkeypatch)

    def unsafe_verifier(package_path, envelope):
        result = _passing_verifier(package_path, envelope)
        result["sandbox"]["network"] = "allowed"
        return result

    service = _service(tmp_path, verifier=unsafe_verifier)
    with pytest.raises(StudioReviewServiceError) as failed:
        _create(service, package)
    assert failed.value.code == "STUDIO_REVIEW_VERIFICATION_FAILED"
    assert not list(service.objects.iterdir())
    with sqlite3.connect(service.database) as database:
        assert database.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_single_file_archive_ingress_revalidates_before_session_creation(tmp_path, monkeypatch) -> None:
    package = _prepared_package(tmp_path / "project", monkeypatch)
    archive = package.with_suffix(".vsreview")
    service = _service(tmp_path)

    created = service.create_session_from_archive(
        archive,
        disclosure_accepted=True,
        expires_in_seconds=3600,
        idempotency_key="create-session-0001",
    )
    assert created["session"]["package_id"] == package.name
    assert created["ingress"] == {
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "archive_bytes": archive.stat().st_size,
    }
    assert not list(service.root.glob(".ingress-*"))

    invalid = tmp_path / "invalid.vsreview"
    invalid.write_bytes(b"not a review archive")
    with pytest.raises(StudioReviewServiceError) as rejected:
        service.create_session_from_archive(
            invalid,
            disclosure_accepted=True,
            expires_in_seconds=3600,
            idempotency_key="create-session-0002",
        )
    assert rejected.value.code == "STUDIO_REVIEW_PACKAGE_INVALID"


def test_capabilities_are_one_time_role_separated_and_never_persist_in_plaintext(tmp_path, monkeypatch) -> None:
    package = _prepared_package(tmp_path / "project", monkeypatch)
    service = _service(tmp_path)
    created = _create(service, package)
    session_id = created["session"]["id"]
    owner_capability = _capability(created, "owner")
    reviewer_capability = _capability(created, "reviewer")

    owner_exchange = service.exchange_capability(session_id, owner_capability)
    reviewer_exchange = service.exchange_capability(session_id, reviewer_capability)
    assert owner_exchange["role"] == "owner"
    assert reviewer_exchange["role"] == "reviewer"
    assert owner_exchange["cookie_policy"]["secure"] is True
    assert owner_exchange["cookie_policy"]["http_only"] is True
    assert owner_exchange["cookie_policy"]["same_site"] == "Strict"
    assert owner_exchange["history_action"] == "remove_fragment_immediately"
    with pytest.raises(StudioReviewServiceError) as reused:
        service.exchange_capability(session_id, owner_capability)
    assert reused.value.code == "STUDIO_REVIEW_ACCESS_DENIED"
    with pytest.raises(StudioReviewServiceError) as anonymous:
        service.read_revision("not-a-browser-session")
    assert anonymous.value.code == "STUDIO_REVIEW_ACCESS_DENIED"

    persisted = service.database.read_bytes()
    for secret in (
        owner_capability,
        reviewer_capability,
        owner_exchange["cookie_value"],
        reviewer_exchange["cookie_value"],
    ):
        assert str(secret).encode() not in persisted


def test_revision_reads_and_artifact_reads_expose_only_allowlisted_review_content(tmp_path, monkeypatch) -> None:
    package = _prepared_package(tmp_path / "project", monkeypatch)
    service = _service(tmp_path)
    created = _create(service, package)
    reviewer = _cookie(service, created, "reviewer")

    revision = service.read_revision(reviewer)
    assert revision["status"] == "active"
    assert revision["role"] == "reviewer"
    assert revision["inspection"]["policy"]["production_data"] == "not_claimed"
    assert revision["comments"] == []
    artifact_metadata = revision["artifacts"][0]
    artifact = service.read_artifact(reviewer, artifact_metadata["path"])
    assert artifact.sha256 == artifact_metadata["sha256"]
    assert len(artifact.content) == artifact_metadata["bytes"]
    with pytest.raises(StudioReviewServiceError) as source:
        service.read_artifact(reviewer, "source/viewspec.app.json")
    assert source.value.code == "STUDIO_REVIEW_ARTIFACT_FORBIDDEN"
    with pytest.raises(StudioReviewServiceError) as traversal:
        service.read_artifact(reviewer, "artifacts/../source/viewspec.app.json")
    assert traversal.value.code == "STUDIO_REVIEW_ARTIFACT_FORBIDDEN"


def test_reviewer_comment_is_source_bound_server_derived_and_idempotent(tmp_path, monkeypatch) -> None:
    package = _prepared_package(tmp_path / "project", monkeypatch)
    service = _service(tmp_path)
    created = _create(service, package)
    reviewer = _cookie(service, created, "reviewer")
    owner = _cookie(service, created, "owner")
    read = service.read_revision(reviewer)
    context = _comment_context(read)

    first = service.append_comment(
        reviewer,
        body="Make the incident priority easier to scan.",
        context=context,
        idempotency_key="comment-request-0001",
    )
    repeated = service.append_comment(
        reviewer,
        body="Make the incident priority easier to scan.",
        context=context,
        idempotency_key="comment-request-0001",
    )
    assert repeated == first
    assert first["comment"]["acknowledged"] is True
    assert first["comment"]["context"]["revision_identity_sha256"] == read["session"]["revision_identity_sha256"]
    assert service.read_revision(owner)["comments"] == [first["comment"]]

    with pytest.raises(StudioReviewServiceError) as conflict:
        service.append_comment(
            reviewer,
            body="Different request.",
            context=context,
            idempotency_key="comment-request-0001",
        )
    assert conflict.value.code == "STUDIO_REVIEW_IDEMPOTENCY_CONFLICT"
    forged = dict(context)
    forged["semantic_identity_sha256"] = "0" * 64
    with pytest.raises(StudioReviewServiceError) as invalid:
        service.append_comment(
            reviewer,
            body="Forged context.",
            context=forged,
            idempotency_key="comment-request-0002",
        )
    assert invalid.value.code == "STUDIO_REVIEW_CONTEXT_INVALID"
    with pytest.raises(StudioReviewServiceError) as wrong_role:
        service.append_comment(
            owner,
            body="Owner cannot impersonate a reviewer.",
            context=context,
            idempotency_key="comment-request-0003",
        )
    assert wrong_role.value.code == "STUDIO_REVIEW_ROLE_FORBIDDEN"
    assert "Make the incident" not in json.dumps(service.audit_events(owner))


def test_cross_session_context_and_unchecked_replay_refs_fail_closed(tmp_path, monkeypatch) -> None:
    first_package = _prepared_package(tmp_path / "first", monkeypatch)
    second_payload = starter_app_bundle("internal_tool")
    second_payload["app"]["title"] = "Second private product"
    second_package = _prepared_package(tmp_path / "second", monkeypatch, app_payload=second_payload)
    service = _service(tmp_path)
    first = _create(service, first_package, key="create-session-0001")
    second = _create(service, second_package, key="create-session-0002")
    reviewer = _cookie(service, first, "reviewer")
    foreign_context = _comment_context(service.read_revision(_cookie(service, second, "reviewer")))
    with pytest.raises(StudioReviewServiceError) as cross_session:
        service.append_comment(
            reviewer,
            body="Cross-session attempt.",
            context=foreign_context,
            idempotency_key="comment-request-0001",
        )
    assert cross_session.value.code == "STUDIO_REVIEW_CONTEXT_INVALID"

    current = service.read_revision(reviewer)
    replay_context = _comment_context(current)
    replay_context["replay_evidence_ref"] = "studio-inspection/replays/forged/checkpoints/9"
    with pytest.raises(StudioReviewServiceError) as replay:
        service.append_comment(
            reviewer,
            body="Forged replay.",
            context=replay_context,
            idempotency_key="comment-request-0002",
        )
    assert replay.value.code == "STUDIO_REVIEW_CONTEXT_INVALID"


def test_declared_replay_and_resource_evidence_are_derived_into_the_comment(tmp_path, monkeypatch) -> None:
    payload = json.loads(
        Path(
            "conformance/agent-ui-v2/fixtures/shakedown-104729-2026-08-06-v5/viewspec-core.app.json"
        ).read_text(encoding="utf-8")
    )
    package = _prepared_package(tmp_path / "project", monkeypatch, app_payload=payload)
    service = _service(tmp_path)
    created = _create(service, package)
    reviewer = _cookie(service, created, "reviewer")
    read = service.read_revision(reviewer)
    inspection = read["inspection"]
    replay_ref = inspection["state"]["replays"][0]["checkpoints"][0]["evidence_ref"]
    assertion = inspection["resources"]["views"][0]["assertions"][0]
    screen_id = inspection["resources"]["views"][0]["screen_id"]
    screen = next(item for item in read["screens"] if item["id"] == screen_id)
    route = next(item for item in read["routes"] if item["screen_id"] == screen_id)
    context = {
        "revision_identity_sha256": read["session"]["revision_identity_sha256"],
        "route": route["path"],
        "screen_id": screen_id,
        "semantic_identity_sha256": screen["semantic_identity_sha256"],
        "viewport_width": 768,
        "target": {"kind": "binding", "id": assertion["matched_binding_id"]},
        "replay_evidence_ref": replay_ref,
    }

    result = service.append_comment(
        reviewer,
        body="Keep this job identity visible after replay.",
        context=context,
        idempotency_key="comment-request-0001",
    )

    assert result["comment"]["context"]["evidence_refs"] == [
        replay_ref,
        f"studio-inspection/resources/{assertion['canonical_identity']}",
    ]


def test_only_owner_can_approve_exact_revision_and_receipts_reject_tampering(tmp_path, monkeypatch) -> None:
    package = _prepared_package(tmp_path / "project", monkeypatch)
    service = _service(tmp_path)
    created = _create(service, package)
    owner = _cookie(service, created, "owner")
    reviewer = _cookie(service, created, "reviewer")
    revision_sha256 = service.read_revision(owner)["session"]["revision_identity_sha256"]

    with pytest.raises(StudioReviewServiceError) as reviewer_denied:
        service.approve_revision(
            reviewer,
            revision_identity_sha256=revision_sha256,
            idempotency_key="approve-request-0001",
        )
    assert reviewer_denied.value.code == "STUDIO_REVIEW_ROLE_FORBIDDEN"
    with pytest.raises(StudioReviewServiceError) as stale:
        service.approve_revision(
            owner,
            revision_identity_sha256="0" * 64,
            idempotency_key="approve-request-0001",
        )
    assert stale.value.code == "STUDIO_REVIEW_REVISION_MISMATCH"

    approved = service.approve_revision(
        owner,
        revision_identity_sha256=revision_sha256,
        idempotency_key="approve-request-0001",
    )
    assert service.approve_revision(
        owner,
        revision_identity_sha256=revision_sha256,
        idempotency_key="approve-request-0001",
    ) == approved
    assert approved["approval"]["role"] == "owner"
    assert service.verify_receipt(approved["approval"]["receipt"]) is True
    tampered = json.loads(json.dumps(approved["approval"]["receipt"]))
    tampered["payload"]["revision_identity_sha256"] = "f" * 64
    assert service.verify_receipt(tampered) is False
    with pytest.raises(StudioReviewServiceError) as duplicate:
        service.approve_revision(
            owner,
            revision_identity_sha256=revision_sha256,
            idempotency_key="approve-request-0002",
        )
    assert duplicate.value.code == "STUDIO_REVIEW_ALREADY_APPROVED"


def test_rotation_revocation_expiry_and_deletion_are_immediate_and_reversible(tmp_path, monkeypatch) -> None:
    now = [1_800_000_000]
    package = _prepared_package(tmp_path / "project", monkeypatch)
    service = _service(tmp_path, clock=lambda: now[0])
    created = _create(service, package)
    owner = _cookie(service, created, "owner")
    reviewer = _cookie(service, created, "reviewer")
    session_id = created["session"]["id"]

    rotated = service.rotate_reviewer(owner, idempotency_key="rotate-reviewer-0001")
    assert service.rotate_reviewer(owner, idempotency_key="rotate-reviewer-0001") == rotated
    with pytest.raises(StudioReviewServiceError) as old_reviewer:
        service.read_revision(reviewer)
    assert old_reviewer.value.code == "STUDIO_REVIEW_ACCESS_DENIED"
    new_reviewer = service.exchange_capability(
        session_id,
        rotated["reviewer_fragment"].removeprefix("#cap="),
    )["cookie_value"]
    assert service.read_revision(new_reviewer)["status"] == "active"

    shortened = service.shorten_expiry(
        owner,
        expires_at=now[0] + 300,
        idempotency_key="shorten-expiry-0001",
    )
    assert shortened["expires_at"] == now[0] + 300
    now[0] += 301
    with pytest.raises(StudioReviewServiceError) as expired:
        service.read_revision(owner)
    assert expired.value.code == "STUDIO_REVIEW_ACCESS_DENIED"

    package = _prepared_package(tmp_path / "revoked", monkeypatch)
    revoked_created = _create(service, package, key="create-session-0002")
    revoked_owner = _cookie(service, revoked_created, "owner")
    revoked_reviewer = _cookie(service, revoked_created, "reviewer")
    revoked = service.revoke(revoked_owner, idempotency_key="revoke-session-0001")
    assert service.revoke(revoked_owner, idempotency_key="revoke-session-0001") == revoked
    for cookie in (revoked_owner, revoked_reviewer):
        with pytest.raises(StudioReviewServiceError) as denied:
            service.read_revision(cookie)
        assert denied.value.code == "STUDIO_REVIEW_ACCESS_DENIED"

    package = _prepared_package(tmp_path / "deleted", monkeypatch)
    deleted_created = _create(service, package, key="create-session-0003")
    deleted_owner = _cookie(service, deleted_created, "owner")
    deleted_reviewer = _cookie(service, deleted_created, "reviewer")
    deleted_session_id = deleted_created["session"]["id"]
    deleted = service.delete(deleted_owner, idempotency_key="delete-session-0001")
    assert service.delete(deleted_owner, idempotency_key="delete-session-0001") == deleted
    assert not (service.objects / deleted_session_id).exists()
    assert not (service.objects / f".deleted-{deleted_session_id}").exists()
    with pytest.raises(StudioReviewServiceError) as deleted_denied:
        service.read_revision(deleted_reviewer)
    assert deleted_denied.value.code == "STUDIO_REVIEW_ACCESS_DENIED"


def test_receipt_key_rotation_preserves_old_proof_and_uses_the_new_active_key(tmp_path, monkeypatch) -> None:
    package = _prepared_package(tmp_path / "project", monkeypatch)
    service_root = tmp_path / "service"
    old_receipt_key = b"test-only-old-receipt-key-material-0001"
    new_receipt_key = b"test-only-new-receipt-key-material-0002"
    original = StudioReviewService(
        service_root,
        signing_key=_SIGNING_KEY,
        receipt_signing_key=old_receipt_key,
        key_id="studio-review-old",
        verifier=_passing_verifier,
    )
    created = _create(original, package)
    old_receipt = created["receipt"]
    assert old_receipt["key_id"] == "studio-review-old"

    rotated = StudioReviewService(
        service_root,
        signing_key=_SIGNING_KEY,
        receipt_signing_key=new_receipt_key,
        receipt_verification_keys={"studio-review-old": old_receipt_key},
        key_id="studio-review-new",
        verifier=_passing_verifier,
    )
    assert rotated.verify_receipt(old_receipt) is True
    owner = _cookie(rotated, created, "owner")
    revision_sha256 = rotated.read_revision(owner)["session"]["revision_identity_sha256"]
    approved = rotated.approve_revision(
        owner,
        revision_identity_sha256=revision_sha256,
        idempotency_key="approve-request-0001",
    )
    new_receipt = approved["approval"]["receipt"]
    assert new_receipt["key_id"] == "studio-review-new"
    assert rotated.verify_receipt(new_receipt) is True
    storage = rotated.verify_storage()
    assert storage["receipt_count"] == 2
    assert storage["receipt_key_count"] == 2

    active_only = StudioReviewService(
        service_root,
        signing_key=_SIGNING_KEY,
        receipt_signing_key=new_receipt_key,
        key_id="studio-review-new",
        verifier=_passing_verifier,
    )
    assert active_only.verify_receipt(old_receipt) is False
    with pytest.raises(StudioReviewServiceError) as missing_old_key:
        active_only.verify_storage()
    assert missing_old_key.value.code == "STUDIO_REVIEW_STORAGE_FAILED"


def test_retention_is_bounded_dry_runnable_idempotent_and_aggregate_only(tmp_path, monkeypatch) -> None:
    now = [1_800_000_000]
    first_package = _prepared_package(tmp_path / "first", monkeypatch)
    second_package = _prepared_package(tmp_path / "second", monkeypatch)
    service = _service(tmp_path, clock=lambda: now[0])
    first = _create(service, first_package, key="create-session-0001")
    second = _create(service, second_package, key="create-session-0002")
    session_ids = {first["session"]["id"], second["session"]["id"]}
    now[0] += 3601

    preview = service.run_retention(dry_run=True, limit=1)
    assert preview["status"] == "dry_run"
    assert preview["has_more"] is True
    assert preview["counts"] == {
        "eligible": 1,
        "would_expire": 1,
        "sessions_expired": 0,
        "objects_deleted": 0,
    }
    assert {path.name for path in service.objects.iterdir()} == session_ids

    first_batch = service.run_retention(dry_run=False, limit=1)
    assert first_batch["counts"]["sessions_expired"] == 1
    assert first_batch["has_more"] is True
    assert len(list(service.objects.iterdir())) == 1
    second_batch = service.run_retention(dry_run=False, limit=1)
    assert second_batch["counts"]["sessions_expired"] == 1
    assert second_batch["has_more"] is False
    empty = service.run_retention(dry_run=False, limit=1)
    assert empty["counts"]["eligible"] == 0
    assert not list(service.objects.iterdir())

    telemetry = service.aggregate_telemetry(since=1_800_000_000, until=now[0])
    assert telemetry["event_counts"]["verification_passed"] == 2
    assert telemetry["event_counts"]["session_expired"] == 2
    assert telemetry["maintenance_counts"]["retention:dry_run"]["eligible"] == 1
    assert telemetry["maintenance_counts"]["retention:applied"]["sessions_expired"] == 2
    assert telemetry["session_status_counts"] == {"deleted": 2}
    serialized = json.dumps(telemetry, sort_keys=True)
    assert not any(session_id in serialized for session_id in session_ids)
    verified = service.verify_storage()
    assert verified["status"] == "passed"
    assert verified["object_count"] == 0


def test_restart_reconciles_only_provable_crash_remnants_and_retry_is_unique(tmp_path, monkeypatch) -> None:
    package = _prepared_package(tmp_path / "project", monkeypatch)
    service = _service(tmp_path)
    created = _create(service, package, key="create-session-0001")
    owner = _cookie(service, created, "owner")
    session_id = created["session"]["id"]
    original = service.objects / session_id
    tombstone = service.objects / f".deleted-{session_id}"
    original.rename(tombstone)

    orphan_id = "vsr_" + "A" * 24
    orphan = service.objects / orphan_id
    shutil.copytree(tombstone, orphan)
    candidate_id = "vsr_" + "B" * 24
    candidate = service.objects / f".candidate-{candidate_id}"
    shutil.copytree(tombstone, candidate)

    inspector = StudioReviewService(
        service.root,
        signing_key=_SIGNING_KEY,
        verifier=_passing_verifier,
        reconcile_on_startup=False,
    )
    preview = inspector.reconcile_storage(dry_run=True)
    assert preview["counts"]["deletion_rollback_completed"] == 1
    assert preview["counts"]["orphan_objects_removed"] == 1
    assert preview["counts"]["staging_removed"] == 1
    assert tombstone.exists() and orphan.exists() and candidate.exists()

    restarted = _service(tmp_path)
    assert original.is_dir()
    assert not tombstone.exists()
    assert not orphan.exists()
    assert not candidate.exists()
    assert restarted.read_revision(owner)["session"]["id"] == session_id

    retried = _create(restarted, package, key="create-session-0002")
    assert retried["session"]["id"] != session_id
    with sqlite3.connect(restarted.database) as database:
        assert database.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2
    assert restarted.verify_storage()["session_count"] == 2


def test_clean_volume_restore_revalidates_database_objects_and_receipts(tmp_path, monkeypatch) -> None:
    package = _prepared_package(tmp_path / "project", monkeypatch)
    service = _service(tmp_path / "origin")
    created = _create(service, package)
    owner = _cookie(service, created, "owner")
    revision_sha256 = service.read_revision(owner)["session"]["revision_identity_sha256"]
    service.approve_revision(
        owner,
        revision_identity_sha256=revision_sha256,
        idempotency_key="approve-request-0001",
    )
    before = service.verify_storage()

    restored_root = tmp_path / "restored" / "service"
    restored_root.parent.mkdir()
    shutil.copytree(service.root, restored_root)
    restored = StudioReviewService(
        restored_root,
        signing_key=_SIGNING_KEY,
        verifier=_passing_verifier,
    )
    after = restored.verify_storage()

    assert after["database_integrity"] == "ok"
    assert after["session_count"] == before["session_count"]
    assert after["receipt_count"] == before["receipt_count"]
    assert after["object_set_sha256"] == before["object_set_sha256"]
