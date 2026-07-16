from __future__ import annotations

import json

import pytest

from viewspec.intent_tools import starter_intent_payload
from viewspec.review_contract import ReviewContext, ReviewContractError, ReviewSelectedText, ReviewViewport
from viewspec.review_runtime import ReviewRuntime
from viewspec.verification import VerificationPlan, VerificationResult


def _write_intent(path, kind: str = "dashboard") -> None:
    path.write_text(json.dumps(starter_intent_payload(kind), sort_keys=True), encoding="utf-8")


def _context() -> ReviewContext:
    return ReviewContext(
        route=None,
        screen_id=None,
        viewport=ReviewViewport.canonical("desktop"),
        selected_text=None,
        control_values=(),
        visibility="visible",
        evidence_refs=(),
    )


def _first_dom_id(runtime: ReviewRuntime) -> str:
    manifest = json.loads(runtime.built.artifact_dir.joinpath("provenance_manifest.json").read_text(encoding="utf-8"))
    return next(iter(manifest["nodes"]))


def test_runtime_rebuilds_browser_target_from_checked_manifest(tmp_path) -> None:
    source = tmp_path / "viewspec.intent.json"
    _write_intent(source)
    runtime = ReviewRuntime.open(source, state_root=tmp_path / "state", target="html-tailwind")
    dom_id = _first_dom_id(runtime)

    event = runtime.submit_browser_event(
        idempotency_key="0" * 32,
        kind="note",
        body="Tighten this.",
        screen_id=None,
        dom_ancestors=(dom_id,),
        page_level=False,
        context=_context(),
        client_provenance={"ir_id": "forged", "intent_refs": ["forged"]},
    )

    expected = runtime.built.manifest_indexes[None].target_for_dom_id(dom_id)
    assert event.target == expected
    assert event.target.ir_id != "forged"


def test_runtime_rejects_unknown_target_without_journaling_subset(tmp_path) -> None:
    source = tmp_path / "viewspec.intent.json"
    _write_intent(source)
    runtime = ReviewRuntime.open(source, state_root=tmp_path / "state", target="html-tailwind")

    with pytest.raises(ReviewContractError) as raised:
        runtime.submit_browser_event(
            idempotency_key="1" * 32,
            kind="note",
            body="Unknown.",
            screen_id=None,
            dom_ancestors=("not-in-manifest",),
            page_level=False,
            context=_context(),
        )

    assert raised.value.code == "REVIEW_TARGET_UNSUPPORTED"
    assert runtime.session.events == ()


def test_runtime_rebuild_promotes_new_head_and_preserves_old_event(tmp_path) -> None:
    source = tmp_path / "viewspec.intent.json"
    _write_intent(source, "dashboard")
    runtime = ReviewRuntime.open(source, state_root=tmp_path / "state", target="html-tailwind")
    old_dom_id = _first_dom_id(runtime)
    old = runtime.submit_browser_event(
        idempotency_key="2" * 32,
        kind="note",
        body="Old revision.",
        screen_id=None,
        dom_ancestors=(old_dom_id,),
        page_level=False,
        context=_context(),
    )

    _write_intent(source, "table")
    rebuilt = runtime.rebuild()

    assert rebuilt.revision.number == 2
    assert runtime.session.revision == rebuilt.revision
    assert runtime.session.events[0] == old
    assert old.revision.number == 1
    semantic_diff = json.loads(rebuilt.revision_dir.joinpath("semantic_diff.json").read_text(encoding="utf-8"))
    assert semantic_diff["from_revision"] == 1
    assert semantic_diff["to_revision"] == 2
    assert semantic_diff["from_source_sha256"] == old.revision.source_sha256
    assert semantic_diff["to_source_sha256"] == rebuilt.revision.source_sha256
    assert runtime.status()["semantic_diff"]["status"] == "available"
    resumed = ReviewRuntime.open(source, state_root=tmp_path / "state", target="html-tailwind")
    assert resumed.session.revision.number == 2
    assert resumed.session.events[0].revision.number == 1
    assert resumed.status()["semantic_diff"]["to_revision"] == 2


def test_failed_runtime_rebuild_keeps_last_good_head(tmp_path) -> None:
    source = tmp_path / "viewspec.intent.json"
    _write_intent(source)
    runtime = ReviewRuntime.open(source, state_root=tmp_path / "state", target="html-tailwind")
    first_hash = runtime.built.revision.artifact_set_sha256
    source.write_text('{"schema_version":1,"substrate":{},"view_spec":{}}', encoding="utf-8")

    with pytest.raises(ReviewContractError) as raised:
        runtime.rebuild()

    assert raised.value.code == "REVIEW_SOURCE_INVALID"
    assert runtime.built.revision.number == 1
    assert runtime.built.revision.artifact_set_sha256 == first_hash
    assert not runtime.session.state_dir.joinpath("revisions", "2").exists()


def test_runtime_resume_requires_exact_private_configuration(tmp_path) -> None:
    source = tmp_path / "viewspec.intent.json"
    design = tmp_path / "DESIGN.md"
    _write_intent(source)
    design.write_text("# Local design\n", encoding="utf-8")
    ReviewRuntime.open(source, state_root=tmp_path / "state", target="html-tailwind")

    with pytest.raises(ReviewContractError) as raised:
        ReviewRuntime.open(source, state_root=tmp_path / "state", target="html-tailwind", design_path=design)

    assert raised.value.code == "REVIEW_SESSION_CONFIGURATION_CONFLICT"


def test_human_ended_runtime_requires_explicit_reopen(tmp_path) -> None:
    source = tmp_path / "viewspec.intent.json"
    _write_intent(source)
    runtime = ReviewRuntime.open(source, state_root=tmp_path / "state")
    runtime.session.end(actor="human", idempotency_key="f" * 32)

    with pytest.raises(ReviewContractError) as raised:
        ReviewRuntime.open(source, state_root=tmp_path / "state")
    assert raised.value.code == "REVIEW_SESSION_ENDED_BY_HUMAN"

    reopened = ReviewRuntime.open(source, state_root=tmp_path / "state", reopen=True)
    assert reopened.session.ended_by is None


def test_runtime_rejects_default_forbidden_controls_and_unproven_selection(tmp_path) -> None:
    source = tmp_path / "viewspec.intent.json"
    _write_intent(source)
    runtime = ReviewRuntime.open(source, state_root=tmp_path / "state")
    dom_id = _first_dom_id(runtime)
    forbidden_controls = ReviewContext(
        route=None,
        screen_id=None,
        viewport=ReviewViewport.canonical("desktop"),
        selected_text=None,
        control_values=(("secret", "value"),),
        visibility="visible",
        evidence_refs=(),
    )
    with pytest.raises(ReviewContractError) as raised:
        runtime.submit_browser_event(
            idempotency_key="a" * 32,
            kind="note",
            body="No controls.",
            screen_id=None,
            dom_ancestors=(dom_id,),
            page_level=False,
            context=forbidden_controls,
        )
    assert raised.value.code == "REVIEW_CONTEXT_FORBIDDEN"

    selection = ReviewContext(
        route=None,
        screen_id=None,
        viewport=ReviewViewport.canonical("desktop"),
        selected_text=ReviewSelectedText.create("text that is absent from the selected node"),
        control_values=(),
        visibility="visible",
        evidence_refs=(),
    )
    with pytest.raises(ReviewContractError) as raised:
        runtime.submit_browser_event(
            idempotency_key="b" * 32,
            kind="note",
            body="Bad selection.",
            screen_id=None,
            dom_ancestors=(dom_id,),
            page_level=False,
            context=selection,
        )
    assert raised.value.code == "REVIEW_SELECTION_UNSUPPORTED"
    assert runtime.session.events == ()


def test_runtime_retains_only_eight_latest_artifact_revisions(tmp_path) -> None:
    source = tmp_path / "viewspec.intent.json"
    _write_intent(source, "dashboard")
    runtime = ReviewRuntime.open(source, state_root=tmp_path / "state")
    kinds = ["table", "dashboard"] * 5
    for kind in kinds[:9]:
        _write_intent(source, kind)
        runtime.rebuild()

    retained = sorted(int(path.name) for path in runtime.session_dir.joinpath("revisions").iterdir())
    assert retained == list(range(3, 11))


def test_runtime_persists_only_verification_for_the_exact_current_artifact(tmp_path) -> None:
    source = tmp_path / "viewspec.intent.json"
    _write_intent(source)
    runtime = ReviewRuntime.open(source, state_root=tmp_path / "state")
    result = VerificationResult.create(
        artifact_sha256=runtime.built.revision.artifact_set_sha256,
        plan=VerificationPlan.default(),
        complete=True,
        diagnostics=(),
    )
    runtime.record_verification(result)
    assert runtime.status()["verification_status"] == "conformant"
    resumed = ReviewRuntime.resume(source, state_root=tmp_path / "state")
    assert resumed.status()["verification"]["result_sha256"] == result.result_sha256

    stale = VerificationResult.create(
        artifact_sha256="f" * 64,
        plan=VerificationPlan.default(),
        complete=True,
        diagnostics=(),
    )
    with pytest.raises(ReviewContractError) as raised:
        runtime.record_verification(stale)
    assert raised.value.code == "REVIEW_VERIFICATION_STALE"
