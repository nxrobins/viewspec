from pathlib import Path
import os
import sqlite3

import pytest

from viewspec.studio_review_service import StudioReviewService, StudioReviewServiceError
import viewspec.studio_review_staging as staging


def service(root: Path, *, reconcile: bool = True) -> StudioReviewService:
    return StudioReviewService(root, signing_key=b"test-only-key" * 4, verifier=lambda *_: {}, reconcile_on_startup=reconcile)


def remnants(root: Path) -> tuple[Path, Path]:
    upload = root / "ingress" / ".upload-abcdefgh.vsreview"
    upload.write_bytes(b"incomplete upload")
    upload.chmod(0o600)
    extracted = root / "ingress" / ".ingress-abcdefgh"
    extracted.mkdir(mode=0o700)
    package = extracted / ("a" * 64)
    package.mkdir(mode=0o700)
    (package / "envelope.json").write_bytes(b"partial")
    (package / "envelope.json").chmod(0o600)
    return upload, extracted


def test_owned_remnants_support_bounded_dry_run_and_restart(tmp_path):
    current = service(tmp_path / "service")
    upload, extracted = remnants(current.root)
    with pytest.raises(StudioReviewServiceError):
        current.verify_storage()
    preview = current.reconcile_storage(dry_run=True, limit=1)
    assert preview["counts"]["staging_removed"] == 1 and preview["has_more"]
    assert upload.exists() and extracted.exists()
    first = current.reconcile_storage(dry_run=False, limit=1)
    assert first["counts"]["staging_removed"] == 1
    assert len(list(current.ingress.iterdir())) == 1
    restarted = service(current.root)
    assert not list(restarted.ingress.iterdir())
    assert restarted.verify_storage()["session_count"] == 0
    assert restarted.reconcile_storage(dry_run=False)["counts"]["staging_removed"] == 0


@pytest.mark.parametrize("case", ["legacy_upload", "legacy_directory", "unowned_object_ingress", "unknown_name", "symlink", "nested_symlink", "hardlink", "fifo", "public_file", "public_directory", "entry_limit", "byte_limit", "node_limit"])
def test_ambiguous_state_is_preserved_without_partial_cleanup(tmp_path, monkeypatch, case):
    current = service(tmp_path / "service")
    upload, extracted = remnants(current.root)
    target = tmp_path / "preserve"
    target.write_bytes(b"unrelated private material")
    target.chmod(0o600)
    if case == "legacy_upload":
        (current.root / ".upload-abcdefgh.vsreview").write_bytes(b"legacy")
    elif case == "legacy_directory":
        (current.root / ".ingress-abcdefgh").mkdir(mode=0o700)
    elif case == "unowned_object_ingress":
        (current.objects / ".ingress-abcdefgh").mkdir(mode=0o700)
    elif case == "unknown_name":
        (current.ingress / "notes").write_bytes(b"not scratch")
    elif case == "symlink":
        (current.ingress / ".upload-ijklmnop.vsreview").symlink_to(target)
    elif case == "nested_symlink":
        (extracted / "link").symlink_to(target)
    elif case == "hardlink":
        os.link(target, extracted / "link")
    elif case == "fifo":
        os.mkfifo(extracted / "pipe", 0o600)
    elif case == "public_file":
        upload.chmod(0o644)
    elif case == "public_directory":
        extracted.chmod(0o755)
    elif case == "entry_limit":
        monkeypatch.setattr(staging, "MAX_STAGING_ENTRIES", 1)
    elif case == "byte_limit":
        monkeypatch.setattr(staging, "MAX_STAGING_BYTES", 1)
    else:
        monkeypatch.setattr(staging, "MAX_STAGING_NODES", 1)
    before = (current.root / "service.sqlite3").read_bytes()
    with pytest.raises(StudioReviewServiceError):
        current.reconcile_storage(dry_run=False)
    assert upload.exists() and extracted.exists() and target.read_bytes() == b"unrelated private material"
    assert (current.root / "service.sqlite3").read_bytes() == before


def test_replaced_staging_identity_is_not_deleted(tmp_path):
    current = service(tmp_path / "service")
    upload, _ = remnants(current.root)
    planned = staging.plan_staging(current.root)
    upload.rename(tmp_path / "original")
    upload.write_bytes(b"replacement")
    upload.chmod(0o600)
    with pytest.raises(ValueError):
        staging.remove_staging(current.root, planned)
    assert upload.read_bytes() == b"replacement"


def test_staging_root_cannot_be_a_symlink_or_public_directory(tmp_path):
    root = tmp_path / "service"
    root.mkdir(mode=0o700)
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    (root / "ingress").symlink_to(other)
    with pytest.raises(ValueError):
        service(root)
    (root / "ingress").unlink()
    (root / "ingress").mkdir(mode=0o755)
    with pytest.raises(ValueError):
        service(root)


def test_staging_recovery_records_only_aggregate_counts(tmp_path):
    current = service(tmp_path / "service")
    remnants(current.root)
    service(current.root)
    with sqlite3.connect(current.database) as db:
        records = db.execute("SELECT counts_json FROM maintenance_runs ORDER BY sequence").fetchall()
    assert not any("abcdefgh" in row[0] or "partial" in row[0] for row in records)
