from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from viewspec import cli
import viewspec.studio_creation as studio_creation
from viewspec.app_starters import starter_app_bundle
from viewspec.intent_tools import starter_intent_payload
from viewspec.studio_creation import (
    STUDIO_CREATION_TASK_DEFAULT,
    StudioCreationError,
    accept_studio_creation,
    prepare_studio_creation,
)


_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _write_reference(root: Path) -> Path:
    path = root / "reference.png"
    path.write_bytes(_ONE_PIXEL_PNG)
    return path


def _custom_intent() -> dict:
    payload = starter_intent_payload("dashboard")
    payload["substrate"]["nodes"]["starter_dashboard"]["attrs"]["title"] = "Field Dispatch"
    payload["substrate"]["nodes"]["revenue"]["attrs"] = {"label": "Open jobs", "value": "18"}
    payload["substrate"]["nodes"]["weekly_trend"]["attrs"] = {
        "label": "Response time",
        "value": "12 min",
    }
    payload["substrate"]["nodes"]["priority"]["attrs"] = {
        "label": "Priority",
        "value": "J-205 overdue",
    }
    payload["substrate"]["nodes"]["users"]["attrs"] = {"label": "Crews online", "value": "7"}
    return payload


def _custom_app() -> dict:
    payload = starter_app_bundle("internal_tool")
    payload["app"]["id"] = "field_dispatch"
    payload["app"]["title"] = "Field Dispatch"
    payload["screens"][0]["title"] = "Active jobs"
    payload["screens"][0]["intent_bundle"]["substrate"]["nodes"]["incident_queue"]["attrs"][
        "title"
    ] = "Active jobs"
    payload["screens"][1]["title"] = "Job detail"
    payload["screens"][1]["intent_bundle"]["substrate"]["nodes"]["incident_detail"]["attrs"][
        "title"
    ] = "Job detail"
    return payload


def _write_candidate(root: Path, payload: dict, *, kind: str) -> Path:
    suffix = "app" if kind == "app" else "intent"
    path = root / f".viewspec/studio-candidate.{suffix}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_creation_task_is_deterministic_hash_bound_local_and_source_free(tmp_path) -> None:
    brief = tmp_path / "brief.md"
    brief.write_text("Field crews need a fast dispatch console.\r\nPrioritize overdue jobs.\r\n", encoding="utf-8")
    reference = _write_reference(tmp_path)

    first = prepare_studio_creation(
        brief_file=brief.name,
        reference=reference.name,
        kind="app",
        cwd=tmp_path,
    )
    repeated = prepare_studio_creation(
        brief_file=brief.name,
        reference=reference.name,
        kind="app",
        cwd=tmp_path,
    )

    creation = first["creation"]
    assert creation["task_action"] == "create"
    assert repeated["creation"]["task_action"] == "unchanged"
    assert creation["task_id"] == repeated["creation"]["task_id"]
    assert creation["brief"] == "Field crews need a fast dispatch console.\nPrioritize overdue jobs."
    assert creation["reference"] == {
        "path": "reference.png",
        "sha256": creation["reference"]["sha256"],
        "bytes": len(_ONE_PIXEL_PNG),
        "media_type": "image/png",
        "width": 1,
        "height": 1,
    }
    assert creation["source_path"] == "viewspec.app.json"
    assert creation["candidate_path"] == ".viewspec/studio-candidate.app.json"
    assert creation["acceptance"] == {
        "artifact_check": "required",
        "candidate_validation": "required",
        "generated_output_editable": False,
        "network_calls": "none",
        "reference_fidelity": "not_proven",
        "starter_copy": "forbidden",
    }
    assert first["metadata"] == {
        "sdk_version": first["metadata"]["sdk_version"],
        "network_calls": "none",
        "reference_uploaded": False,
    }
    assert not (tmp_path / "viewspec.app.json").exists()
    assert not (tmp_path / "viewspec.intent.json").exists()


@pytest.mark.parametrize(
    ("kwargs", "code"),
    (
        ({}, "STUDIO_CREATION_BRIEF_INVALID"),
        ({"brief": "one", "brief_file": "brief.md"}, "STUDIO_CREATION_BRIEF_INVALID"),
        ({"brief": "\x00"}, "STUDIO_CREATION_BRIEF_INVALID"),
        ({"brief": "x" * (32 * 1024 + 1)}, "STUDIO_CREATION_BRIEF_TOO_LARGE"),
        ({"brief": "Build a product", "kind": "site"}, "STUDIO_CREATION_KIND_INVALID"),
    ),
)
def test_creation_task_rejects_invalid_brief_or_kind(tmp_path, kwargs, code) -> None:
    (tmp_path / "brief.md").write_text("brief", encoding="utf-8")
    with pytest.raises(StudioCreationError) as raised:
        prepare_studio_creation(cwd=tmp_path, **kwargs)
    assert raised.value.code == code
    assert not (tmp_path / STUDIO_CREATION_TASK_DEFAULT).exists()


def test_creation_task_rejects_invalid_or_oversized_reference(tmp_path) -> None:
    invalid = tmp_path / "reference.png"
    invalid.write_bytes(b"not an image")
    with pytest.raises(StudioCreationError) as raised:
        prepare_studio_creation(brief="Build a dispatch console", reference=invalid.name, cwd=tmp_path)
    assert raised.value.code == "STUDIO_CREATION_REFERENCE_INVALID"

    invalid.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
    with pytest.raises(StudioCreationError) as raised:
        prepare_studio_creation(brief="Build a dispatch console", reference=invalid.name, cwd=tmp_path)
    assert raised.value.code == "STUDIO_CREATION_REFERENCE_TOO_LARGE"


def test_creation_never_overwrites_existing_semantic_source(tmp_path) -> None:
    (tmp_path / "viewspec.intent.json").write_text("{}", encoding="utf-8")
    with pytest.raises(StudioCreationError) as raised:
        prepare_studio_creation(brief="Build a dispatch console", cwd=tmp_path)
    assert raised.value.code == "STUDIO_CREATION_SOURCE_EXISTS"


@pytest.mark.parametrize(
    "task_out",
    ("viewspec.app.json", ".viewspec/studio-candidate.app.json"),
)
def test_creation_task_rejects_reserved_source_and_candidate_paths(tmp_path, task_out) -> None:
    with pytest.raises(StudioCreationError) as raised:
        prepare_studio_creation(
            brief="Build a dispatch console",
            kind="app",
            task_out=task_out,
            cwd=tmp_path,
        )
    assert raised.value.code == "STUDIO_CREATION_PATH_INVALID"
    assert not (tmp_path / task_out).exists()


def test_creation_rejects_broken_canonical_source_symlink(tmp_path) -> None:
    (tmp_path / "viewspec.app.json").symlink_to("missing-app.json")
    with pytest.raises(StudioCreationError) as raised:
        prepare_studio_creation(brief="Build a dispatch console", cwd=tmp_path)
    assert raised.value.code == "STUDIO_CREATION_SOURCE_EXISTS"


def test_accept_rejects_unchanged_starter_without_publishing_source(tmp_path) -> None:
    prepare_studio_creation(brief="Build a field dispatch dashboard", kind="view", cwd=tmp_path)
    _write_candidate(tmp_path, starter_intent_payload("dashboard"), kind="view")

    with pytest.raises(StudioCreationError) as raised:
        accept_studio_creation(cwd=tmp_path)
    assert raised.value.code == "STUDIO_CREATION_STARTER_FORBIDDEN"
    assert not (tmp_path / "viewspec.intent.json").exists()
    assert not (tmp_path / ".viewspec/studio-creation").exists()


def test_accept_rejects_tampered_task_and_changed_reference(tmp_path) -> None:
    reference = _write_reference(tmp_path)
    prepared = prepare_studio_creation(
        brief="Build a field dispatch dashboard",
        reference=reference.name,
        kind="view",
        cwd=tmp_path,
    )
    _write_candidate(tmp_path, _custom_intent(), kind="view")
    task_path = Path(prepared["paths"]["task"])
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task["brief"] = "Substituted brief"
    task_path.write_text(json.dumps(task), encoding="utf-8")
    with pytest.raises(StudioCreationError) as raised:
        accept_studio_creation(cwd=tmp_path)
    assert raised.value.code == "STUDIO_CREATION_TASK_INVALID"
    assert not (tmp_path / "viewspec.intent.json").exists()

    task_path.unlink()
    prepare_studio_creation(
        brief="Build a field dispatch dashboard",
        reference=reference.name,
        kind="view",
        cwd=tmp_path,
    )
    reference.write_bytes(_ONE_PIXEL_PNG + b"changed")
    with pytest.raises(StudioCreationError) as raised:
        accept_studio_creation(cwd=tmp_path)
    assert raised.value.code in {"STUDIO_CREATION_REFERENCE_CHANGED", "STUDIO_CREATION_REFERENCE_INVALID"}
    assert not (tmp_path / "viewspec.intent.json").exists()


def test_accept_proves_then_publishes_intent_source_with_retained_evidence(tmp_path) -> None:
    prepared = prepare_studio_creation(brief="Build a field dispatch dashboard", kind="view", cwd=tmp_path)
    candidate = _write_candidate(tmp_path, _custom_intent(), kind="view")
    candidate_bytes = candidate.read_bytes()

    accepted = accept_studio_creation(cwd=tmp_path)

    assert accepted["ok"] is True
    assert accepted["creation"] == {
        "schema_version": 1,
        "task_id": prepared["creation"]["task_id"],
        "status": "source_ready",
        "source_kind": "intent_bundle",
        "source_name": "viewspec.intent.json",
        "source_sha256": accepted["creation"]["source_sha256"],
        "brief_sha256": prepared["creation"]["brief_sha256"],
        "reference_sha256": None,
        "candidate_validation": "passed",
        "artifact_check": "passed",
        "proof_ok": True,
        "network_calls": "none",
        "reference_fidelity": "not_proven",
    }
    assert (tmp_path / "viewspec.intent.json").read_bytes() == candidate_bytes
    assert Path(accepted["paths"]["task"]).is_file()
    assert Path(accepted["paths"]["candidate"]).read_bytes() == candidate_bytes
    assert Path(accepted["paths"]["captured_candidate"]).read_bytes() == candidate_bytes
    assert accepted["creation"]["source_sha256"] in Path(accepted["paths"]["captured_candidate"]).name
    proof = Path(accepted["paths"]["proof"])
    assert json.loads((proof / "proof_report.json").read_text(encoding="utf-8"))["ok"] is True
    assert (proof / "artifact/provenance_manifest.json").is_file()

    with pytest.raises(StudioCreationError) as raised:
        accept_studio_creation(cwd=tmp_path)
    assert raised.value.code == "STUDIO_CREATION_SOURCE_EXISTS"


def test_accept_proves_the_immutable_capture_and_publishes_nothing_on_failure(
    tmp_path,
    monkeypatch,
) -> None:
    prepare_studio_creation(brief="Build a field dispatch dashboard", kind="view", cwd=tmp_path)
    candidate = _write_candidate(tmp_path, _custom_intent(), kind="view")
    candidate_bytes = candidate.read_bytes()
    observed: dict[str, Path] = {}

    def fail_proof(*, candidate_path, **_kwargs):
        observed["candidate_path"] = candidate_path
        assert candidate_path != candidate
        assert candidate_path.read_bytes() == candidate_bytes
        return {
            "ok": False,
            "errors": [{"code": "TEST_PROOF_FAILURE", "message": "Injected proof failure."}],
        }

    monkeypatch.setattr(studio_creation, "_prove_creation_candidate", fail_proof)
    with pytest.raises(StudioCreationError) as raised:
        accept_studio_creation(cwd=tmp_path)

    assert raised.value.code == "STUDIO_CREATION_PROOF_FAILED"
    assert "canonical source was not published" in raised.value.fix
    assert not (tmp_path / "viewspec.intent.json").exists()
    assert observed["candidate_path"].is_file()


def test_accept_proves_then_publishes_app_source(tmp_path) -> None:
    prepare_studio_creation(brief="Build the field dispatch application", kind="app", cwd=tmp_path)
    candidate = _write_candidate(tmp_path, _custom_app(), kind="app")

    accepted = accept_studio_creation(cwd=tmp_path)

    assert accepted["creation"]["source_kind"] == "app_bundle"
    assert accepted["creation"]["source_name"] == "viewspec.app.json"
    assert (tmp_path / "viewspec.app.json").read_bytes() == candidate.read_bytes()
    proof = Path(accepted["paths"]["proof"])
    assert json.loads((proof / "app_proof_report.json").read_text(encoding="utf-8"))["ok"] is True


def test_studio_creation_cli_exposes_agent_handoff_without_compiler_vocabulary(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert cli.main(["studio-create", "--brief", "Build a field dispatch dashboard", "--kind", "view"]) == 0
    output = capsys.readouterr().out
    assert "Studio creation task ready for your agent." in output
    assert "studio-candidate.intent.json" in output
    assert "compiler" not in output.lower()

    _write_candidate(tmp_path, _custom_intent(), kind="view")
    assert cli.main(["studio-accept", "--json"]) == 0
    accepted = json.loads(capsys.readouterr().out)
    assert accepted["creation"]["status"] == "source_ready"
    assert accepted["next_actions"] == ["Run viewspec studio to enter Preview → Comment → Approve."]
