from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from viewspec.app_starters import starter_app_bundle
import viewspec.review_compile as review_compile
from viewspec.review_compile import STUDIO_COMPARE_TARGET
from viewspec.review_contract import ReviewContractError, canonical_json_bytes
from viewspec.review_runtime import ReviewRuntime
from viewspec.node_runtime import materialize_prebuilt_node_modules
from viewspec.studio_review_verify import (
    STUDIO_REVIEW_DEPENDENCY_SEED_ENV,
    StudioReviewVerificationError,
    bind_studio_review_sandbox_attestation,
    make_studio_review_rebuild_request,
    rebuild_studio_review_package,
    rebuild_studio_review_request,
    studio_review_rebuild_evidence_sha256,
)
from viewspec.studio_review_worker import run_studio_review_worker
from viewspec.studio_share import load_studio_share_package, prepare_studio_share


_REAL_SUBPROCESS_RUN = review_compile.subprocess.run


def _fake_react_npm(command, *, cwd, **kwargs):
    if tuple(command[:3]) == ("npm", "run", "build") or Path(command[0]).name == "vite":
        _write_fake_runtime(Path(cwd), marker="ready")
        return object()
    if tuple(command[:2]) == ("npm", "ci"):
        return object()
    return _REAL_SUBPROCESS_RUN(command, cwd=cwd, **kwargs)


def _write_fake_runtime(root: Path, *, marker: str) -> None:
    runtime = root / "runtime-dist"
    assets = runtime / "assets"
    assets.mkdir(parents=True)
    assets.joinpath("main.js").write_text(
        f"document.getElementById('root').textContent='{marker}';",
        encoding="utf-8",
    )
    assets.joinpath("main.css").write_text("body{margin:0}", encoding="utf-8")
    runtime.joinpath("index.html").write_text(
        '<!doctype html><html><head><link rel="stylesheet" crossorigin href="./assets/main.css"></head>'
        '<body><div id="root"></div><script type="module" crossorigin src="./assets/main.js"></script></body></html>',
        encoding="utf-8",
    )


def _package(tmp_path: Path, monkeypatch) -> tuple[Path, dict[str, object]]:
    source = tmp_path / "project/viewspec.app.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(starter_app_bundle("internal_tool"), sort_keys=True), encoding="utf-8")
    state = tmp_path / "review-state"
    monkeypatch.setattr(review_compile.subprocess, "run", _fake_react_npm)
    ReviewRuntime.open(source, state_root=state, target=STUDIO_COMPARE_TARGET, allow_install=True)
    prepared = prepare_studio_share(source, state_root=state, cwd=tmp_path)
    package = Path(prepared["paths"]["package"])
    return package, load_studio_share_package(package)


def _seed(tmp_path: Path) -> Path:
    seed = tmp_path / "trusted-node-modules"
    seed.mkdir()
    seed.joinpath(".package-lock.json").write_text(
        '{"lockfileVersion":3,"name":"viewspec-studio-review","packages":{}}',
        encoding="utf-8",
    )
    return seed


def _attestation(evidence: dict[str, object], envelope: dict[str, object]) -> dict[str, object]:
    totals = envelope["totals"]
    assert isinstance(totals, dict)
    return {
        "schema_version": 1,
        "kind": "studio_review_sandbox_attestation",
        "status": "passed",
        "runner_id": "test-isolated-runner-v1",
        "rebuild_evidence_sha256": studio_review_rebuild_evidence_sha256(evidence),
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
    }


def test_exact_rebuild_uses_only_prebuilt_seed_and_binds_real_runner_attestation(tmp_path, monkeypatch) -> None:
    package, envelope = _package(tmp_path, monkeypatch)
    seed = _seed(tmp_path)
    commands: list[tuple[str, ...]] = []

    def rebuild_command(command, *, cwd, **kwargs):
        commands.append(tuple(command))
        assert tuple(command[:2]) != ("npm", "ci")
        if Path(command[0]).name == "vite":
            assert Path(cwd, "node_modules/.package-lock.json").exists()
            _write_fake_runtime(Path(cwd), marker="ready")
            return object()
        return _REAL_SUBPROCESS_RUN(command, cwd=cwd, **kwargs)

    monkeypatch.setenv(STUDIO_REVIEW_DEPENDENCY_SEED_ENV, str(seed))
    monkeypatch.setattr(review_compile.subprocess, "run", rebuild_command)
    evidence = rebuild_studio_review_package(package, envelope)

    assert commands and all(command[:2] != ("npm", "ci") for command in commands)
    assert [Path(command[0]).name for command in commands] == ["vite"]
    assert evidence["status"] == "passed"
    assert evidence["package_id"] == package.name
    assert evidence["artifact_set_sha256"] == envelope["revision"]["artifact_set_sha256"]
    assert evidence["dependency_seed"]["install_command_invoked"] is False
    assert "path" not in evidence["dependency_seed"]
    assert evidence["execution_policy"]["sandbox_attestation"] == "required_separately"

    verification = bind_studio_review_sandbox_attestation(
        evidence,
        _attestation(evidence, envelope),
        envelope=envelope,
    )
    assert verification["status"] == "passed"
    assert verification["sandbox"]["network"] == "denied"
    assert verification["package_id"] == package.name
    assert verification["rebuild"] == {
        "evidence_sha256": studio_review_rebuild_evidence_sha256(evidence),
        "expected_inventory_sha256": envelope["revision"]["artifact_set_sha256"],
        "observed_inventory_sha256": envelope["revision"]["artifact_set_sha256"],
        "source_only_request": True,
        "install_used": False,
        "lifecycle_hooks_disabled": True,
        "uploaded_artifacts_executed": False,
    }


def test_rebuild_has_no_install_fallback_and_requires_normal_locked_seed(tmp_path, monkeypatch) -> None:
    package, envelope = _package(tmp_path, monkeypatch)
    monkeypatch.delenv(STUDIO_REVIEW_DEPENDENCY_SEED_ENV, raising=False)
    with pytest.raises(StudioReviewVerificationError, match="install fallback is forbidden"):
        rebuild_studio_review_package(package, envelope)

    relative = Path("relative-node-modules")
    monkeypatch.setenv(STUDIO_REVIEW_DEPENDENCY_SEED_ENV, str(relative))
    with pytest.raises(StudioReviewVerificationError, match="absolute normal directory"):
        rebuild_studio_review_package(package, envelope)

    seed = tmp_path / "seed-without-lock"
    seed.mkdir()
    monkeypatch.setenv(STUDIO_REVIEW_DEPENDENCY_SEED_ENV, str(seed))
    with pytest.raises(StudioReviewVerificationError, match="no retained install lock"):
        rebuild_studio_review_package(package, envelope)

    actual = _seed(tmp_path)
    link = tmp_path / "linked-seed"
    link.symlink_to(actual, target_is_directory=True)
    monkeypatch.setenv(STUDIO_REVIEW_DEPENDENCY_SEED_ENV, str(link))
    with pytest.raises(StudioReviewVerificationError, match="absolute normal directory"):
        rebuild_studio_review_package(package, envelope)


def test_rebuild_rejects_envelope_mismatch_and_source_artifact_divergence(tmp_path, monkeypatch) -> None:
    package, envelope = _package(tmp_path, monkeypatch)
    seed = _seed(tmp_path)
    monkeypatch.setenv(STUDIO_REVIEW_DEPENDENCY_SEED_ENV, str(seed))

    mismatched_envelope = copy.deepcopy(envelope)
    mismatched_envelope["status"] = "changed"
    with pytest.raises(StudioReviewVerificationError, match="does not match"):
        rebuild_studio_review_package(package, mismatched_envelope)

    def divergent_build(command, *, cwd, **kwargs):
        assert tuple(command[:2]) != ("npm", "ci")
        if Path(command[0]).name == "vite":
            _write_fake_runtime(Path(cwd), marker="different")
            return object()
        return _REAL_SUBPROCESS_RUN(command, cwd=cwd, **kwargs)

    monkeypatch.setattr(review_compile.subprocess, "run", divergent_build)
    with pytest.raises(StudioReviewVerificationError, match="differs|did not reproduce"):
        rebuild_studio_review_package(package, envelope)


def test_attestation_binding_rejects_forgery_unbounded_limits_and_mutated_evidence(tmp_path, monkeypatch) -> None:
    package, envelope = _package(tmp_path, monkeypatch)
    monkeypatch.setenv(STUDIO_REVIEW_DEPENDENCY_SEED_ENV, str(_seed(tmp_path)))
    evidence = rebuild_studio_review_package(package, envelope)
    valid = _attestation(evidence, envelope)

    for mutate in (
        lambda item: item.update(network="allowed"),
        lambda item: item.update(rebuild_evidence_sha256="0" * 64),
        lambda item: item["limits"].update(cpu_seconds=31),
        lambda item: item["limits"].update(file_count=1),
    ):
        changed = copy.deepcopy(valid)
        mutate(changed)
        with pytest.raises(StudioReviewVerificationError):
            bind_studio_review_sandbox_attestation(evidence, changed, envelope=envelope)

    changed_evidence = copy.deepcopy(evidence)
    changed_evidence["execution_policy"]["fixed_build_command_only"] = False
    with pytest.raises(StudioReviewVerificationError):
        bind_studio_review_sandbox_attestation(changed_evidence, valid, envelope=envelope)


def test_rebuild_rejects_dependency_lock_change_during_execution(tmp_path, monkeypatch) -> None:
    package, envelope = _package(tmp_path, monkeypatch)
    seed = _seed(tmp_path)
    monkeypatch.setenv(STUDIO_REVIEW_DEPENDENCY_SEED_ENV, str(seed))

    def mutate_lock(command, *, cwd, **kwargs):
        if Path(command[0]).name == "vite":
            Path(cwd, "node_modules/.package-lock.json").write_text("changed", encoding="utf-8")
            _write_fake_runtime(Path(cwd), marker="ready")
            return object()
        return _REAL_SUBPROCESS_RUN(command, cwd=cwd, **kwargs)

    monkeypatch.setattr(review_compile.subprocess, "run", mutate_lock)
    with pytest.raises(StudioReviewVerificationError, match="install lock changed"):
        rebuild_studio_review_package(package, envelope)


def test_artifact_free_worker_request_is_bounded_exact_and_fail_closed(tmp_path, monkeypatch) -> None:
    package, envelope = _package(tmp_path, monkeypatch)
    monkeypatch.setenv(STUDIO_REVIEW_DEPENDENCY_SEED_ENV, str(_seed(tmp_path)))
    request = make_studio_review_rebuild_request(package, envelope)

    assert set(request) == {
        "schema_version",
        "kind",
        "envelope",
        "semantic_source_base64",
        "design_system_base64",
    }
    assert request["kind"] == "studio_review_rebuild_request"
    assert request["design_system_base64"] is None
    assert "checked_artifact_base64" not in request
    assert rebuild_studio_review_request(request)["package_id"] == package.name

    status, response = run_studio_review_worker(canonical_json_bytes(request))
    payload = json.loads(response)
    assert status == 0
    assert payload["ok"] is True
    assert payload["evidence"]["package_id"] == package.name

    corrupt_source = copy.deepcopy(request)
    corrupt_source["semantic_source_base64"] = "e30"
    with pytest.raises(StudioReviewVerificationError, match="semantic source"):
        rebuild_studio_review_request(corrupt_source)
    status, response = run_studio_review_worker(canonical_json_bytes(corrupt_source))
    assert status == 1
    assert json.loads(response) == {
        "schema_version": 1,
        "ok": False,
        "error": {
            "code": "STUDIO_REVIEW_VERIFICATION_FAILED",
            "message": "Isolated Studio review rebuild failed closed.",
        },
    }

    corrupt_envelope = copy.deepcopy(request)
    corrupt_envelope["envelope"]["package_id"] = "0" * 64
    with pytest.raises(StudioReviewVerificationError, match="envelope identity"):
        rebuild_studio_review_request(corrupt_envelope)

    status, response = run_studio_review_worker(b"{" + (b"x" * (4 * 1024 * 1024)))
    assert status == 1
    assert json.loads(response)["error"]["code"] == "STUDIO_REVIEW_VERIFICATION_FAILED"


def test_review_compile_rejects_unsafe_configured_seed_before_build(tmp_path, monkeypatch) -> None:
    source = tmp_path / "viewspec.app.json"
    source.write_text(json.dumps(starter_app_bundle("internal_tool"), sort_keys=True), encoding="utf-8")
    monkeypatch.setenv(STUDIO_REVIEW_DEPENDENCY_SEED_ENV, str(tmp_path / "missing"))
    called: list[tuple[str, ...]] = []

    def no_command(command, *, cwd, **kwargs):
        called.append(tuple(command))
        return _REAL_SUBPROCESS_RUN(command, cwd=cwd, **kwargs)

    monkeypatch.setattr(review_compile.subprocess, "run", no_command)
    with pytest.raises(ReviewContractError) as failed:
        ReviewRuntime.open(
            source,
            state_root=tmp_path / "state",
            target=STUDIO_COMPARE_TARGET,
            allow_install=True,
        )
    assert failed.value.code == "REVIEW_COMPILE_FAILED"
    assert called == []


@pytest.mark.parametrize("worker_seed_path", [
    "conformance/agent-ui-v2/react-dependencies/node_modules",
    "src/viewspec/host_verify_template/node_modules",
])
def test_real_pinned_dependency_seed_reproduces_exact_static_react_package(tmp_path, monkeypatch, worker_seed_path) -> None:
    seed = Path(__file__).parents[1] / "conformance/agent-ui-v2/react-dependencies/node_modules"
    worker_seed = Path(__file__).parents[1] / worker_seed_path
    if any(not item.joinpath(".package-lock.json").is_file() or not item.joinpath(".bin/vite").exists() for item in (seed, worker_seed)):
        pytest.skip("pinned local React dependency seed is not installed")
    source = tmp_path / "viewspec.app.json"
    source.write_text(json.dumps(starter_app_bundle("internal_tool"), sort_keys=True), encoding="utf-8")
    state = tmp_path / "review-state"

    def local_locked_build(command, *, cwd, **kwargs):
        if tuple(command[:2]) == ("npm", "ci"):
            materialize_prebuilt_node_modules(Path(cwd) / "node_modules", seed)
            return object()
        return _REAL_SUBPROCESS_RUN(command, cwd=cwd, **kwargs)

    monkeypatch.delenv(STUDIO_REVIEW_DEPENDENCY_SEED_ENV, raising=False)
    monkeypatch.setattr(review_compile.subprocess, "run", local_locked_build)
    runtime = ReviewRuntime.open(source, state_root=state, target=STUDIO_COMPARE_TARGET, allow_install=True)
    prepared = prepare_studio_share(source, state_root=state, cwd=tmp_path)
    package = Path(prepared["paths"]["package"])
    envelope = load_studio_share_package(package)
    monkeypatch.setenv(STUDIO_REVIEW_DEPENDENCY_SEED_ENV, str(worker_seed))
    monkeypatch.setattr(review_compile.subprocess, "run", _REAL_SUBPROCESS_RUN)
    evidence = rebuild_studio_review_package(package, envelope)

    assert evidence["artifact_set_sha256"] == runtime.built.revision.artifact_set_sha256
    assert evidence["artifact_inventory"]["file_count"] > 1
    assert evidence["dependency_seed"]["install_command_invoked"] is False
