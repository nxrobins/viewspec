from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import zipfile

import pytest

from viewspec import cli
from viewspec.app_starters import starter_app_bundle
import viewspec.review_compile as review_compile
from viewspec.review_compile import STUDIO_COMPARE_TARGET
from viewspec.review_contract import canonical_json_bytes
from viewspec.review_runtime import ReviewRuntime
from viewspec.studio_share import (
    StudioShareError,
    load_studio_share_archive,
    load_studio_share_package,
    materialize_studio_share_archive,
    prepare_studio_share,
)


_REAL_SUBPROCESS_RUN = review_compile.subprocess.run
_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


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


def _write_app(path: Path, *, secret: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = starter_app_bundle("internal_tool")
    if secret is not None:
        payload["screens"][0]["intent_bundle"]["substrate"]["nodes"]["incident_queue"]["attrs"]["title"] = secret
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _checked_comparison(tmp_path: Path, monkeypatch, *, secret: str | None = None) -> tuple[Path, Path, ReviewRuntime]:
    source = _write_app(tmp_path / "viewspec.app.json", secret=secret)
    state = tmp_path / "review-state"
    monkeypatch.setattr("viewspec.review_compile.subprocess.run", _fake_react_npm)
    runtime = ReviewRuntime.open(
        source,
        state_root=state,
        target=STUDIO_COMPARE_TARGET,
        allow_install=True,
    )
    return source, state, runtime


def test_share_prepare_is_content_addressed_exact_disclosed_and_network_free(tmp_path, monkeypatch) -> None:
    source, state, runtime = _checked_comparison(tmp_path, monkeypatch)
    reference = tmp_path / "reference.png"
    reference.write_bytes(_ONE_PIXEL_PNG)

    first = prepare_studio_share(
        source.name,
        reference=reference.name,
        state_root=state,
        cwd=tmp_path,
    )
    repeated = prepare_studio_share(
        source.name,
        reference=reference.name,
        state_root=state,
        cwd=tmp_path,
    )

    assert first["ok"] is True
    assert first["summary"] == "Private review package is prepared locally; nothing was uploaded."
    assert first["external_refs"] == []
    assert first["metadata"]["network_calls"] == "none"
    assert first["share"]["package_action"] == "create"
    assert repeated["share"]["package_action"] == "unchanged"
    assert first["share"]["archive_action"] == "create"
    assert repeated["share"]["archive_action"] == "unchanged"
    assert repeated["share"]["archive_sha256"] == first["share"]["archive_sha256"]
    assert repeated["share"]["package_id"] == first["share"]["package_id"]
    assert first["share"]["confirmation_required"] is True
    assert first["share"]["upload_performed"] is False
    assert first["share"]["capability_created"] is False

    package = Path(first["paths"]["package"])
    envelope = load_studio_share_package(package)
    assert envelope["package_id"] == package.name
    assert envelope["revision"]["source_sha256"] == runtime.built.revision.source_sha256
    assert envelope["revision"]["artifact_set_sha256"] == runtime.built.revision.artifact_set_sha256
    assert envelope["revision"]["target"] == STUDIO_COMPARE_TARGET
    assert envelope["policy"] == {
        "network_calls": "none",
        "upload_performed": False,
        "capability_created": False,
        "confirmation_required": True,
        "confirmation_accepted": False,
        "existing_local_comments": "excluded",
        "future_remote_comments": "disclosed_if_service_is_created",
        "production_data": "not_claimed",
        "secret_detection": "bounded_pattern_scan_not_certification",
        "visual_parity": "not_proven",
    }
    roles = [entry["role"] for entry in envelope["files"]]
    assert roles.count("semantic_source") == 1
    assert roles.count("reference_image") == 1
    assert roles.count("checked_artifact") > 1
    assert "design_system" not in roles
    assert (package / "payload/source/viewspec.app.json").read_bytes() == runtime.built.revision_dir.joinpath(
        "source.json"
    ).read_bytes()
    assert (package / "payload/reference/reference.png").read_bytes() == _ONE_PIXEL_PNG
    serialized = json.dumps(envelope, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert "absolute local paths" in serialized
    disclosure = Path(first["paths"]["disclosure"]).read_text(encoding="utf-8")
    assert "nothing uploaded" in disclosure
    assert "no review link or capability created" in disclosure
    assert "future remote review comments" in disclosure
    assert "existing local Review comments and journal" in disclosure
    assert stat_mode(package) == 0o700
    assert stat_mode(package / "envelope.json") == 0o600


def test_share_transport_archive_is_deterministic_and_strictly_materializes(tmp_path, monkeypatch) -> None:
    source, state, _ = _checked_comparison(tmp_path, monkeypatch)
    first = prepare_studio_share(source, state_root=state, cwd=tmp_path)
    repeated = prepare_studio_share(source, state_root=state, cwd=tmp_path)
    archive = Path(first["paths"]["upload_archive"])
    package = Path(first["paths"]["package"])

    assert archive == package.with_suffix(".vsreview")
    assert archive == Path(repeated["paths"]["upload_archive"])
    assert stat_mode(archive) == 0o600
    assert load_studio_share_archive(archive) == load_studio_share_package(package)
    materialized = materialize_studio_share_archive(archive, tmp_path / "ingress")
    assert materialized.name == package.name
    assert load_studio_share_package(materialized) == load_studio_share_package(package)


def test_share_transport_archive_rejects_tampering_and_traversal(tmp_path, monkeypatch) -> None:
    source, state, _ = _checked_comparison(tmp_path, monkeypatch)
    prepared = prepare_studio_share(source, state_root=state, cwd=tmp_path)
    archive = Path(prepared["paths"]["upload_archive"])

    tampered = tmp_path / "tampered.vsreview"
    traversal = tmp_path / "traversal.vsreview"
    with zipfile.ZipFile(archive) as original:
        entries = [(info, original.read(info)) for info in original.infolist()]

    with zipfile.ZipFile(tampered, "w", compression=zipfile.ZIP_STORED) as rewritten:
        changed = False
        for original_info, content in entries:
            info = _canonical_zip_info(original_info.filename)
            if not changed and original_info.filename.startswith("payload/artifacts/"):
                content += b"tampered"
                changed = True
            rewritten.writestr(info, content)
    with pytest.raises(StudioShareError) as changed_archive:
        load_studio_share_archive(tampered)
    assert changed_archive.value.code == "STUDIO_SHARE_PACKAGE_INVALID"

    with zipfile.ZipFile(traversal, "w", compression=zipfile.ZIP_STORED) as rewritten:
        for original_info, content in entries:
            rewritten.writestr(_canonical_zip_info(original_info.filename), content)
        rewritten.writestr(_canonical_zip_info("../outside.txt"), b"escape")
    with pytest.raises(StudioShareError) as escaped:
        materialize_studio_share_archive(traversal, tmp_path / "escaped-ingress")
    assert escaped.value.code == "STUDIO_SHARE_PACKAGE_INVALID"
    assert not (tmp_path / "outside.txt").exists()


def test_share_prepare_includes_only_the_exact_checked_design(tmp_path, monkeypatch) -> None:
    source = _write_app(tmp_path / "viewspec.app.json")
    design = tmp_path / "DESIGN.md"
    design.write_text("---\nname: Field Dispatch\n---\n", encoding="utf-8")
    state = tmp_path / "review-state"
    monkeypatch.setattr("viewspec.review_compile.subprocess.run", _fake_react_npm)
    runtime = ReviewRuntime.open(
        source,
        design_path=design,
        state_root=state,
        target=STUDIO_COMPARE_TARGET,
        allow_install=True,
    )

    result = prepare_studio_share(source, state_root=state, cwd=tmp_path)
    package = Path(result["paths"]["package"])
    envelope = load_studio_share_package(package)
    design_entry = next(item for item in envelope["files"] if item["role"] == "design_system")
    assert design_entry["sha256"] == runtime.built.revision.design_sha256
    assert (package / "payload/design/DESIGN.md").read_text(encoding="utf-8") == design.read_text(encoding="utf-8")


def test_share_prepare_rejects_unchecked_stale_or_sensitive_source(tmp_path, monkeypatch) -> None:
    source = _write_app(tmp_path / "viewspec.app.json")
    with pytest.raises(StudioShareError) as missing:
        prepare_studio_share(source, state_root=tmp_path / "missing-state", cwd=tmp_path)
    assert missing.value.code == "STUDIO_SHARE_REVIEW_NOT_READY"

    source, state, _ = _checked_comparison(tmp_path / "stale", monkeypatch)
    payload = json.loads(source.read_text(encoding="utf-8"))
    payload["app"]["title"] = "Changed after review"
    source.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    with pytest.raises(StudioShareError) as stale:
        prepare_studio_share(source, state_root=state, cwd=source.parent)
    assert stale.value.code == "STUDIO_SHARE_REVISION_STALE"

    sensitive_root = tmp_path / "sensitive"
    source, state, _ = _checked_comparison(
        sensitive_root,
        monkeypatch,
        secret="sk-proj-abcdefghijklmnopqrstuvwxyz123456",
    )
    with pytest.raises(StudioShareError) as sensitive:
        prepare_studio_share(source, state_root=state, cwd=sensitive_root)
    assert sensitive.value.code == "STUDIO_SHARE_SENSITIVE_PATTERN"
    assert not list((sensitive_root / ".viewspec/studio-share").glob("[0-9a-f]" * 64))


def test_share_prepare_rejects_changed_immutable_source_copy(tmp_path, monkeypatch) -> None:
    source, state, runtime = _checked_comparison(tmp_path, monkeypatch)
    retained = runtime.built.revision_dir / "source.json"
    retained.chmod(0o600)
    retained.write_bytes(retained.read_bytes() + b" ")

    with pytest.raises(StudioShareError) as changed:
        prepare_studio_share(source, state_root=state, cwd=tmp_path)
    assert changed.value.code == "STUDIO_SHARE_REVISION_INVALID"


def test_share_prepare_requires_comparison_and_rejects_reference_or_output_escape(tmp_path, monkeypatch) -> None:
    source = _write_app(tmp_path / "viewspec.app.json")
    state = tmp_path / "review-state"
    ReviewRuntime.open(source, state_root=state, target="html-tailwind-app")
    with pytest.raises(StudioShareError) as wrong_target:
        prepare_studio_share(source, state_root=state, cwd=tmp_path)
    assert wrong_target.value.code == "STUDIO_SHARE_COMPARISON_REQUIRED"

    source, state, _ = _checked_comparison(tmp_path / "comparison", monkeypatch)
    invalid_reference = source.parent / "reference.png"
    invalid_reference.write_text("not an image", encoding="utf-8")
    with pytest.raises(StudioShareError) as bad_reference:
        prepare_studio_share(source, reference=invalid_reference, state_root=state, cwd=source.parent)
    assert bad_reference.value.code == "STUDIO_SHARE_REFERENCE_INVALID"

    with pytest.raises(StudioShareError) as escaped:
        prepare_studio_share(source, state_root=state, out_root="../outside", cwd=source.parent)
    assert escaped.value.code == "STUDIO_SHARE_PATH_INVALID"


def test_share_package_revalidation_rejects_tampering_and_extra_files(tmp_path, monkeypatch) -> None:
    source, state, _ = _checked_comparison(tmp_path, monkeypatch)
    result = prepare_studio_share(source, state_root=state, cwd=tmp_path)
    package = Path(result["paths"]["package"])
    target = next(path for path in (package / "payload/artifacts").rglob("*") if path.is_file())
    target.chmod(0o600)
    target.write_bytes(target.read_bytes() + b"tampered")
    with pytest.raises(StudioShareError) as tampered:
        load_studio_share_package(package)
    assert tampered.value.code == "STUDIO_SHARE_PACKAGE_INVALID"

    clean_root = tmp_path / "clean"
    source, state, _ = _checked_comparison(clean_root, monkeypatch)
    result = prepare_studio_share(source, state_root=state, cwd=clean_root)
    package = Path(result["paths"]["package"])
    extra = package / "payload/unlisted.txt"
    extra.write_text("not in envelope", encoding="utf-8")
    with pytest.raises(StudioShareError) as unlisted:
        load_studio_share_package(package)
    assert unlisted.value.code == "STUDIO_SHARE_PACKAGE_INVALID"


def test_share_package_rejects_self_consistent_policy_rewrite(tmp_path, monkeypatch) -> None:
    source, state, _ = _checked_comparison(tmp_path, monkeypatch)
    result = prepare_studio_share(source, state_root=state, cwd=tmp_path)
    package = Path(result["paths"]["package"])
    envelope_path = package / "envelope.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    envelope["policy"]["upload_performed"] = True
    basis = {key: value for key, value in envelope.items() if key != "package_id"}
    rewritten_id = hashlib.sha256(canonical_json_bytes(basis)).hexdigest()
    envelope["package_id"] = rewritten_id
    envelope_path.chmod(0o600)
    envelope_path.write_bytes(canonical_json_bytes(envelope))
    rewritten = package.with_name(rewritten_id)
    package.rename(rewritten)

    with pytest.raises(StudioShareError) as policy:
        load_studio_share_package(rewritten)
    assert policy.value.code == "STUDIO_SHARE_PACKAGE_INVALID"


def test_studio_share_cli_says_exactly_that_no_upload_or_link_exists(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "prepare_studio_share",
        lambda *args, **kwargs: {
            "paths": {
                "disclosure": ".viewspec/studio-share/hash/share-disclosure.md",
                "upload_archive": ".viewspec/studio-share/hash.vsreview",
            },
        },
    )

    assert cli.main(["studio-share-prepare", "viewspec.app.json"]) == 0
    output = capsys.readouterr().out
    assert "prepared locally" in output
    assert "Nothing was uploaded" in output
    assert "hash.vsreview" in output
    assert "No review link or capability was created" in output


def _canonical_zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    return info


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777
