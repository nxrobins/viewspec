from __future__ import annotations

from dataclasses import replace

from hypothesis import given, strategies as st
import pytest

from viewspec.review_contract import (
    MAX_BODY_BYTES,
    MAX_CONTEXT_CONTROLS,
    MAX_CONTROL_VALUE_BYTES,
    MAX_EVENT_BYTES,
    ReviewBatch,
    ReviewContext,
    ReviewContractError,
    ReviewEvent,
    ReviewRevision,
    ReviewSelectedText,
    ReviewTarget,
    ReviewViewport,
    canonical_json_bytes,
    new_batch_id,
    new_event_id,
    new_review_id,
)


def _revision(number: int = 1, fill: str = "a") -> ReviewRevision:
    return ReviewRevision(
        number=number,
        source_kind="intent_bundle",
        source_sha256=fill * 64,
        design_sha256=None,
        target="html-tailwind",
        artifact_set_sha256="b" * 64,
        root_manifest_kind="provenance_manifest",
        root_manifest_sha256="c" * 64,
        compiler_version="0.3.0b4",
        contract_profile="local_v1",
    )


def _target() -> ReviewTarget:
    return ReviewTarget(
        kind="source_node",
        screen_id=None,
        ir_id="motif_summary",
        source_ref="ir:motif_summary",
        dom_id="dom-motif_summary",
        binding_id=None,
        action_id=None,
        intent_refs=("viewspec:motif:summary",),
        content_refs=("node:summary#attr:value",),
        provenance_manifest_sha256="d" * 64,
        target_resolution="exact",
    )


def _context() -> ReviewContext:
    return ReviewContext(
        route="/incident",
        screen_id=None,
        viewport=ReviewViewport.canonical("desktop"),
        selected_text=None,
        control_values=(),
        visibility="visible",
        evidence_refs=(),
    )


def _event(sequence: int = 1, *, revision: ReviewRevision | None = None) -> ReviewEvent:
    return ReviewEvent(
        event_id=new_event_id(),
        sequence=sequence,
        actor="human",
        kind="note",
        body="Tighten this layout.",
        revision=revision or _revision(),
        target=_target(),
        context=_context(),
    )


def test_review_ids_are_128_bit_prefixed_and_unique() -> None:
    review_ids = {new_review_id() for _ in range(256)}
    event_ids = {new_event_id() for _ in range(256)}
    batch_ids = {new_batch_id() for _ in range(256)}

    assert len(review_ids) == len(event_ids) == len(batch_ids) == 256
    assert all(value.startswith("vrw_") and len(value) == 36 for value in review_ids)
    assert all(value.startswith("vre_") and len(value) == 36 for value in event_ids)
    assert all(value.startswith("vrb_") and len(value) == 36 for value in batch_ids)


def test_entropy_failure_has_no_predictable_id_fallback(monkeypatch) -> None:
    def unavailable(size: int) -> str:
        del size
        raise OSError("entropy unavailable")

    monkeypatch.setattr("viewspec.review_contract.secrets.token_hex", unavailable)
    with pytest.raises(ReviewContractError) as raised:
        new_event_id()
    assert raised.value.code == "REVIEW_ENTROPY_UNAVAILABLE"


@given(body=st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=MAX_BODY_BYTES))
def test_event_body_accepts_every_valid_ascii_value(body: str) -> None:
    event = replace(_event(), body=body)

    assert event.body == body
    assert len(canonical_json_bytes(event.to_json())) <= MAX_EVENT_BYTES


def test_event_body_limit_is_inclusive_and_limit_plus_one_fails() -> None:
    assert replace(_event(), body="x" * MAX_BODY_BYTES).body

    with pytest.raises(ReviewContractError) as raised:
        replace(_event(), body="x" * (MAX_BODY_BYTES + 1))

    assert raised.value.code == "REVIEW_EVENT_TOO_LARGE"


def test_revision_rejects_non_hash_identity() -> None:
    with pytest.raises(ReviewContractError) as raised:
        replace(_revision(), source_sha256="not-a-hash")

    assert raised.value.code == "REVIEW_REVISION_IDENTITY_MISMATCH"


def test_context_control_limits_are_enforced_before_event_serialization() -> None:
    controls = tuple((f"field_{index}", "x" * MAX_CONTROL_VALUE_BYTES) for index in range(MAX_CONTEXT_CONTROLS))
    context = replace(_context(), control_values=controls)
    assert len(context.control_values) == MAX_CONTEXT_CONTROLS

    with pytest.raises(ReviewContractError) as raised:
        replace(context, control_values=controls + (("overflow", "x"),))

    assert raised.value.code == "REVIEW_CONTEXT_FORBIDDEN"


def test_selected_text_must_be_nonempty_visible_text() -> None:
    with pytest.raises(ReviewContractError) as raised:
        ReviewSelectedText.create("")
    assert raised.value.code == "REVIEW_SELECTION_UNSUPPORTED"


def test_target_refs_are_bounded_and_manifest_hash_is_required() -> None:
    with pytest.raises(ReviewContractError) as raised:
        replace(_target(), intent_refs=tuple(f"viewspec:motif:item_{index}" for index in range(33)))

    assert raised.value.code == "REVIEW_TARGET_LIMIT_EXCEEDED"

    with pytest.raises(ReviewContractError) as raised:
        replace(_target(), provenance_manifest_sha256="")

    assert raised.value.code == "REVIEW_TARGET_INVALID"


def test_batch_requires_one_revision_and_is_byte_bounded() -> None:
    first = _event(1)
    second = _event(2, revision=_revision(2, "e"))

    with pytest.raises(ReviewContractError) as raised:
        ReviewBatch.create((first, second))

    assert raised.value.code == "REVIEW_REVISION_IDENTITY_MISMATCH"


def test_batch_round_trip_is_canonical() -> None:
    batch = ReviewBatch.create((_event(1), _event(2)))

    assert ReviewBatch.from_json(batch.to_json()) == batch
    assert canonical_json_bytes(batch.to_json()) == canonical_json_bytes(ReviewBatch.from_json(batch.to_json()).to_json())
