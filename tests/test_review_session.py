from __future__ import annotations

import json
from pathlib import Path
import tempfile

from hypothesis import given, settings, strategies as st
import pytest

from viewspec.review_contract import ReviewContext, ReviewContractError, ReviewRevision, ReviewTarget, ReviewViewport
from viewspec.review_session import ReviewJournal, ReviewSession, ReviewStateLock


def _revision(number: int = 1, *, source_hash: str = "a") -> ReviewRevision:
    return ReviewRevision(
        number=number,
        source_kind="intent_bundle",
        source_sha256=source_hash * 64,
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
        ir_id="summary",
        source_ref="ir:summary",
        dom_id="dom-summary",
        binding_id=None,
        action_id=None,
        intent_refs=("viewspec:motif:summary",),
        content_refs=(),
        provenance_manifest_sha256="d" * 64,
        target_resolution="exact",
    )


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


@given(records=st.lists(st.dictionaries(st.text(min_size=1, max_size=8), st.integers()), max_size=20))
def test_journal_round_trips_canonical_records(records) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "events.vrj"
        journal = ReviewJournal(path)
        for record in records:
            journal.append(record)

        assert ReviewJournal(path).records() == tuple(records)


def test_journal_rejects_a_malformed_tail_without_salvage(tmp_path) -> None:
    path = tmp_path / "events.vrj"
    journal = ReviewJournal(path)
    journal.append({"type": "event", "value": 1})
    with path.open("ab") as handle:
        handle.write(b"malformed")

    with pytest.raises(ReviewContractError) as raised:
        ReviewJournal(path)

    assert raised.value.code == "REVIEW_JOURNAL_INVALID"


def test_state_lock_allows_exactly_one_process_writer(tmp_path) -> None:
    first = ReviewStateLock(tmp_path / "session.lock")
    second = ReviewStateLock(tmp_path / "session.lock")
    first.acquire(timeout_seconds=0.01)
    try:
        with pytest.raises(ReviewContractError) as raised:
            second.acquire(timeout_seconds=0.01)
        assert raised.value.code == "REVIEW_STATE_LOCKED"
    finally:
        first.release()
    second.acquire(timeout_seconds=0.01)
    second.release()


def test_event_submission_is_idempotent_and_conflicting_reuse_fails(tmp_path) -> None:
    session = ReviewSession(tmp_path / "session", revision=_revision())

    first = session.submit_event(
        idempotency_key="0" * 32,
        kind="note",
        body="Make this denser.",
        target=_target(),
        context=_context(),
    )
    repeated = session.submit_event(
        idempotency_key="0" * 32,
        kind="note",
        body="Make this denser.",
        target=_target(),
        context=_context(),
    )

    assert repeated == first
    assert len(session.events) == 1

    with pytest.raises(ReviewContractError) as raised:
        session.submit_event(
            idempotency_key="0" * 32,
            kind="note",
            body="Different bytes.",
            target=_target(),
            context=_context(),
        )

    assert raised.value.code == "REVIEW_IDEMPOTENCY_CONFLICT"
    assert len(session.events) == 1


def test_third_event_id_collision_fails_without_timestamp_fallback(tmp_path, monkeypatch) -> None:
    session = ReviewSession(tmp_path / "session", revision=_revision())
    first = session.submit_event(
        idempotency_key="c" * 32,
        kind="note",
        body="First.",
        target=_target(),
        context=_context(),
    )
    monkeypatch.setattr("viewspec.review_session.new_event_id", lambda: first.event_id)
    with pytest.raises(ReviewContractError) as raised:
        session.submit_event(
            idempotency_key="d" * 32,
            kind="note",
            body="Second.",
            target=_target(),
            context=_context(),
        )
    assert raised.value.code == "REVIEW_ENTROPY_UNAVAILABLE"
    assert len(session.events) == 1


def test_poll_redelivers_byte_identical_batch_until_acknowledged(tmp_path) -> None:
    session = ReviewSession(tmp_path / "session", revision=_revision())
    session.submit_event(
        idempotency_key="1" * 32,
        kind="change_request",
        body="Move this up.",
        target=_target(),
        context=_context(),
    )

    first = session.poll()
    repeated = session.poll()

    assert first is not None
    assert repeated is not None
    assert repeated.to_json() == first.to_json()
    assert session.cursor == 0

    assert session.poll(ack_batch_id=first.batch_id, agent_reply="Done.") is None
    assert session.cursor == 1
    assert session.agent_replies == ("Done.",)


def test_acknowledgement_and_reply_survive_restart_without_duplication(tmp_path) -> None:
    path = tmp_path / "session"
    session = ReviewSession(path, revision=_revision())
    session.submit_event(
        idempotency_key="2" * 32,
        kind="question",
        body="Should this be compact?",
        target=_target(),
        context=_context(),
    )
    batch = session.poll()
    assert batch is not None
    session.poll(ack_batch_id=batch.batch_id, agent_reply="Yes.")

    recovered = ReviewSession(path, revision=_revision())
    assert recovered.cursor == 1
    assert recovered.agent_replies == ("Yes.",)
    assert recovered.poll(ack_batch_id=batch.batch_id, agent_reply="Yes.") is None
    assert recovered.agent_replies == ("Yes.",)

    with pytest.raises(ReviewContractError) as raised:
        recovered.poll(ack_batch_id=batch.batch_id, agent_reply="No.")

    assert raised.value.code == "REVIEW_ACK_INVALID"


def test_acknowledgement_threshold_compacts_and_preserves_exact_recovery(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("viewspec.review_session.COMPACTION_ACKNOWLEDGEMENTS", 1)
    path = tmp_path / "session"
    session = ReviewSession(path, revision=_revision())
    original = session.submit_event(
        idempotency_key="a" * 32,
        kind="note",
        body="Persist through compaction.",
        target=_target(),
        context=_context(),
    )
    batch = session.poll()
    assert batch is not None
    assert session.poll(ack_batch_id=batch.batch_id, agent_reply="Captured.") is None

    record_types = [record["type"] for record in session.journal.records()]
    assert "compaction_marker" in record_types
    assert "acknowledgement_checkpoint" in record_types
    assert "batch_issued" not in record_types

    recovered = ReviewSession(path, revision=_revision())
    assert recovered.cursor == 1
    assert recovered.agent_replies == ("Captured.",)
    repeated = recovered.submit_event(
        idempotency_key="a" * 32,
        kind="note",
        body="Persist through compaction.",
        target=_target(),
        context=_context(),
    )
    assert repeated.event_id == original.event_id


def test_compaction_failure_keeps_old_journal_and_fails_next_poll(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("viewspec.review_session.COMPACTION_ACKNOWLEDGEMENTS", 1)
    path = tmp_path / "session"
    session = ReviewSession(path, revision=_revision())
    session.submit_event(
        idempotency_key="b" * 32,
        kind="note",
        body="Do not lose this.",
        target=_target(),
        context=_context(),
    )
    batch = session.poll()
    assert batch is not None
    before = path.joinpath("events.vrj").read_bytes()

    def fail_compaction(*args, **kwargs):
        del args, kwargs
        raise ReviewContractError(
            "REVIEW_COMPACTION_FAILED",
            "Injected compaction failure.",
            "Keep the old journal.",
            http_status=507,
        )

    monkeypatch.setattr(session.journal, "compact", fail_compaction)
    assert session.poll(ack_batch_id=batch.batch_id) is None
    assert path.joinpath("events.vrj").read_bytes().startswith(before)
    assert session.compaction_failure is not None

    with pytest.raises(ReviewContractError) as raised:
        session.poll()
    assert raised.value.code == "REVIEW_COMPACTION_FAILED"


def test_out_of_order_ack_does_not_advance_delivery(tmp_path) -> None:
    session = ReviewSession(tmp_path / "session", revision=_revision())
    session.submit_event(
        idempotency_key="3" * 32,
        kind="note",
        body="One.",
        target=_target(),
        context=_context(),
    )
    batch = session.poll()
    assert batch is not None

    with pytest.raises(ReviewContractError) as raised:
        session.poll(ack_batch_id="vrb_" + "f" * 32)

    assert raised.value.code == "REVIEW_ACK_INVALID"
    assert session.cursor == 0
    assert session.poll().batch_id == batch.batch_id


def test_human_end_is_durable_and_rejects_later_events(tmp_path) -> None:
    path = tmp_path / "session"
    session = ReviewSession(path, revision=_revision())
    session.end(actor="human", idempotency_key="4" * 32)

    recovered = ReviewSession(path, revision=_revision())
    assert recovered.ended_by == "human"

    with pytest.raises(ReviewContractError) as raised:
        recovered.submit_event(
            idempotency_key="5" * 32,
            kind="note",
            body="Too late.",
            target=_target(),
            context=_context(),
        )

    assert raised.value.code == "REVIEW_SESSION_ENDED"


def test_final_event_and_human_end_are_one_idempotent_durable_frame(tmp_path) -> None:
    path = tmp_path / "session"
    session = ReviewSession(path, revision=_revision())
    before = len(session.journal.records())

    event = session.submit_event_and_end(
        idempotency_key="e" * 32,
        kind="approval",
        body="Ship this revision.",
        target=_target(),
        context=_context(),
    )
    repeated = session.submit_event_and_end(
        idempotency_key="e" * 32,
        kind="approval",
        body="Ship this revision.",
        target=_target(),
        context=_context(),
    )

    assert repeated.event_id == event.event_id
    assert len(session.journal.records()) == before + 1
    assert session.journal.records()[-1]["type"] == "event_and_end"
    assert session.ended_by == "human"

    recovered = ReviewSession(path, revision=_revision())
    assert recovered.ended_by == "human"
    assert [item.event_id for item in recovered.events] == [event.event_id]


def test_reopen_is_a_durable_transition_that_allows_new_feedback(tmp_path) -> None:
    path = tmp_path / "session"
    session = ReviewSession(path, revision=_revision())
    session.end(actor="agent")
    session.reopen()
    session.submit_event(
        idempotency_key="a" * 32,
        kind="note",
        body="Resumed.",
        target=_target(),
        context=_context(),
    )

    recovered = ReviewSession(path, revision=_revision())
    assert recovered.ended_by is None
    assert [event.body for event in recovered.events] == ["Resumed."]


def test_revision_promotion_preserves_old_event_identity_and_survives_restart(tmp_path) -> None:
    path = tmp_path / "session"
    session = ReviewSession(path, revision=_revision())
    old = session.submit_event(
        idempotency_key="6" * 32,
        kind="note",
        body="Before.",
        target=_target(),
        context=_context(),
    )
    second = _revision(2, source_hash="e")

    session.promote_revision(second)
    new = session.submit_event(
        idempotency_key="7" * 32,
        kind="note",
        body="After.",
        target=_target(),
        context=_context(),
    )

    assert old.revision.number == 1
    assert new.revision == second
    assert session.revision == second
    recovered = ReviewSession(path, revision=second)
    assert recovered.revision == second
    assert [event.revision.number for event in recovered.events] == [1, 2]


@given(next_number=st.integers(min_value=1, max_value=10).filter(lambda value: value != 2))
def test_revision_promotion_rejects_nonconsecutive_numbers(next_number: int) -> None:
    with tempfile.TemporaryDirectory() as directory:
        session = ReviewSession(Path(directory) / "session", revision=_revision())
        with pytest.raises(ReviewContractError) as raised:
            session.promote_revision(_revision(next_number, source_hash="e"))
        assert raised.value.code == "REVIEW_REVISION_IDENTITY_MISMATCH"
        assert session.revision.number == 1


def test_restart_rejects_a_revision_other_than_the_durable_current_head(tmp_path) -> None:
    path = tmp_path / "session"
    session = ReviewSession(path, revision=_revision())
    session.promote_revision(_revision(2, source_hash="e"))

    with pytest.raises(ReviewContractError) as raised:
        ReviewSession(path, revision=_revision())

    assert raised.value.code == "REVIEW_SESSION_CONFIGURATION_CONFLICT"


def test_poll_splits_contiguous_feedback_at_revision_boundaries(tmp_path) -> None:
    session = ReviewSession(tmp_path / "session", revision=_revision())
    session.submit_event(
        idempotency_key="8" * 32,
        kind="note",
        body="Old head.",
        target=_target(),
        context=_context(),
    )
    session.promote_revision(_revision(2, source_hash="e"))
    session.submit_event(
        idempotency_key="9" * 32,
        kind="note",
        body="New head.",
        target=_target(),
        context=_context(),
    )

    old_batch = session.poll()
    assert old_batch is not None
    assert [event.sequence for event in old_batch.events] == [1]
    assert old_batch.revision.number == 1
    new_batch = session.poll(ack_batch_id=old_batch.batch_id)
    assert new_batch is not None
    assert [event.sequence for event in new_batch.events] == [2]
    assert new_batch.revision.number == 2


@settings(max_examples=30, deadline=None)
@given(operations=st.lists(st.sampled_from(("submit", "poll", "ack", "restart")), min_size=1, max_size=30))
def test_session_state_machine_preserves_sequence_and_at_least_once_delivery(operations) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "session"
        session = ReviewSession(path, revision=_revision())
        next_key = 0
        acknowledged = 0

        for operation in operations:
            if operation == "submit" and next_key < 32:
                session.submit_event(
                    idempotency_key=f"{next_key:032x}",
                    kind="note",
                    body=json.dumps({"index": next_key}),
                    target=_target(),
                    context=_context(),
                )
                next_key += 1
            elif operation == "poll":
                batch = session.poll()
                if batch is not None:
                    assert [event.sequence for event in batch.events] == list(
                        range(acknowledged + 1, acknowledged + len(batch.events) + 1)
                    )
            elif operation == "ack" and session.outstanding_batch is not None:
                batch = session.outstanding_batch
                acknowledged = batch.last_sequence
                session.poll(ack_batch_id=batch.batch_id)
            elif operation == "restart":
                session = ReviewSession(path, revision=_revision())

            assert [event.sequence for event in session.events] == list(range(1, len(session.events) + 1))
            assert len({event.event_id for event in session.events}) == len(session.events)
            assert session.cursor == acknowledged
