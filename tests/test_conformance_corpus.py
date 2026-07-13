from __future__ import annotations

import json

from hypothesis import given, strategies as st
import pytest

from viewspec.conformance import ConformanceCorpus, load_conformance_corpus


def _case(case_id: str, source: str = "demos/example.json") -> dict:
    return {
        "id": case_id,
        "kind": "intent",
        "source": source,
        "expected_status": "conformant",
        "required_evidence_roles": ["accessibility", "dom", "log", "screenshot"],
    }


def test_public_verification_corpus_has_unique_executable_semantic_cases():
    corpus = load_conformance_corpus("conformance/verification/corpus.json")

    assert [case.id for case in corpus.cases] == [
        "app-detail",
        "app-queue",
        "data-dense-dashboard",
        "interactive-form",
        "landing-intent",
    ]
    assert all(case.source_path.is_file() for case in corpus.cases)
    assert all(case.expected_status == "conformant" for case in corpus.cases)
    assert {case.screen_id for case in corpus.cases if case.kind == "app_screen"} == {
        "detail",
        "queue",
    }


@given(st.permutations(("charlie", "alpha", "bravo")))
def test_corpus_case_order_is_canonical(case_ids):
    corpus = ConformanceCorpus.from_json(
        {"schema_version": 1, "cases": [_case(case_id) for case_id in case_ids]},
        root=".",
        require_sources=False,
    )

    assert [case.id for case in corpus.cases] == ["alpha", "bravo", "charlie"]


@pytest.mark.parametrize(
    "source",
    ["../secret.json", "/tmp/secret.json", "demos/../secret.json", "demos\\secret.json"],
)
def test_corpus_rejects_unsafe_source_paths(source):
    with pytest.raises(ValueError, match="source"):
        ConformanceCorpus.from_json(
            {"schema_version": 1, "cases": [_case("unsafe", source)]},
            root=".",
            require_sources=False,
        )


def test_corpus_rejects_ambiguous_app_screen_contract():
    payload = _case("ambiguous")
    payload["kind"] = "app_screen"

    with pytest.raises(ValueError, match="screen_id"):
        ConformanceCorpus.from_json(
            {"schema_version": 1, "cases": [payload]},
            root=".",
            require_sources=False,
        )


def test_corpus_manifest_round_trips_canonically():
    corpus = load_conformance_corpus("conformance/verification/corpus.json")

    reparsed = ConformanceCorpus.from_json(
        json.loads(json.dumps(corpus.to_json())),
        root=corpus.root,
    )

    assert reparsed == corpus
