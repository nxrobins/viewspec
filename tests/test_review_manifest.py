from __future__ import annotations

import json

from hypothesis import given, strategies as st
import pytest

from viewspec.review_contract import ReviewContractError
from viewspec.review_manifest import MAX_ANCESTOR_DEPTH, MAX_MANIFEST_NODES, ReviewManifestIndex


def _manifest(nodes: dict) -> bytes:
    return json.dumps({"manifest_schema_version": 1, "nodes": nodes}, sort_keys=True).encode("utf-8")


def _node(ir_id: str, *, primitive: str = "text", intent_refs=None, content_refs=None, props=None) -> dict:
    return {
        "ir_id": ir_id,
        "primitive": primitive,
        "intent_refs": list([f"viewspec:binding:{ir_id}"] if intent_refs is None else intent_refs),
        "content_refs": list([f"node:{ir_id}#attr:value"] if content_refs is None else content_refs),
        "props": dict(props or {}),
    }


def test_manifest_reconstructs_target_and_ignores_client_provenance() -> None:
    index = ReviewManifestIndex.from_bytes(
        _manifest(
            {
                "dom-summary": _node(
                    "summary",
                    primitive="surface",
                    intent_refs=["viewspec:motif:summary"],
                    content_refs=[],
                    props={"binding_id": "summary_binding", "action_id": "open_summary"},
                )
            }
        ),
        screen_id="incident",
    )

    target = index.target_for_dom_id("dom-summary")

    assert target.source_ref == "screen:incident/ir:summary"
    assert target.intent_refs == ("viewspec:motif:summary",)
    assert target.content_refs == ()
    assert target.binding_id == "summary_binding"
    assert target.action_id == "open_summary"
    assert target.provenance_manifest_sha256 == index.manifest_sha256


def test_manifest_rejects_forged_dom_identity() -> None:
    index = ReviewManifestIndex.from_bytes(_manifest({"dom-summary": _node("summary")}), screen_id=None)

    with pytest.raises(ReviewContractError) as raised:
        index.target_for_dom_id("dom-forged")

    assert raised.value.code == "REVIEW_TARGET_NOT_IN_MANIFEST"


def test_manifest_rejects_duplicate_ir_identity() -> None:
    with pytest.raises(ReviewContractError) as raised:
        ReviewManifestIndex.from_bytes(
            _manifest({"dom-first": _node("same"), "dom-second": _node("same")}),
            screen_id=None,
        )

    assert raised.value.code == "REVIEW_MANIFEST_AMBIGUOUS"


def test_manifest_node_limit_is_inclusive_and_limit_plus_one_fails() -> None:
    at_limit = {f"dom-{index}": _node(f"node_{index}", content_refs=[]) for index in range(MAX_MANIFEST_NODES)}
    assert len(ReviewManifestIndex.from_bytes(_manifest(at_limit), screen_id=None)) == MAX_MANIFEST_NODES

    at_limit["dom-overflow"] = _node("overflow", content_refs=[])
    with pytest.raises(ReviewContractError) as raised:
        ReviewManifestIndex.from_bytes(_manifest(at_limit), screen_id=None)

    assert raised.value.code == "REVIEW_MANIFEST_AMBIGUOUS"


@given(position=st.integers(min_value=0, max_value=MAX_ANCESTOR_DEPTH - 1))
def test_ancestor_resolution_uses_first_manifest_backed_node(position: int) -> None:
    index = ReviewManifestIndex.from_bytes(_manifest({"dom-known": _node("known")}), screen_id=None)
    candidates = tuple([f"dom-child-{item}" for item in range(position)] + ["dom-known"])

    target = index.resolve_dom_ancestors(candidates)

    assert target.dom_id == "dom-known"
    assert target.target_resolution == ("exact" if position == 0 else "ancestor")


def test_ancestor_resolution_never_walks_beyond_32_nodes() -> None:
    index = ReviewManifestIndex.from_bytes(_manifest({"dom-known": _node("known")}), screen_id=None)
    candidates = tuple(f"dom-child-{index}" for index in range(MAX_ANCESTOR_DEPTH)) + ("dom-known",)

    with pytest.raises(ReviewContractError) as raised:
        index.resolve_dom_ancestors(candidates)

    assert raised.value.code == "REVIEW_TARGET_UNSUPPORTED"


def test_cross_revision_meaning_reuse_is_rejected() -> None:
    previous = ReviewManifestIndex.from_bytes(
        _manifest({"dom-summary": _node("summary", primitive="surface", intent_refs=["viewspec:motif:summary"])}),
        screen_id=None,
    )
    changed = ReviewManifestIndex.from_bytes(
        _manifest({"dom-summary": _node("summary", primitive="button", intent_refs=["viewspec:action:summary"])}),
        screen_id=None,
    )

    with pytest.raises(ReviewContractError) as raised:
        changed.assert_identity_compatible(previous)

    assert raised.value.code == "REVIEW_MANIFEST_AMBIGUOUS"
