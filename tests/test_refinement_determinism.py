from __future__ import annotations

import json
from pathlib import Path

from viewspec.conformance import ConformanceCase, load_conformance_corpus
from viewspec.intent_tools import compile_intent_bundle_file_tool


def _intent_text(case: ConformanceCase) -> str:
    source_text = case.source_path.read_text(encoding="utf-8")
    if case.kind == "intent":
        return source_text
    payload = json.loads(source_text)
    screen = next(item for item in payload["screens"] if item["id"] == case.screen_id)
    return json.dumps(screen["intent_bundle"], indent=2, sort_keys=True) + "\n"


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_fixed_refinement_corpus_compiles_byte_identically_with_traceable_manifest_nodes(tmp_path):
    corpus = load_conformance_corpus("conformance/verification/corpus.json")

    for case in corpus.cases:
        source_path = tmp_path / "sources" / f"{case.id}.intent.json"
        source_path.parent.mkdir(exist_ok=True)
        source_path.write_text(_intent_text(case), encoding="utf-8")
        first = tmp_path / "first" / case.id
        second = tmp_path / "second" / case.id

        first_result = compile_intent_bundle_file_tool(
            source_path,
            first,
            target="react-tailwind-tsx",
            cwd=tmp_path,
        )
        second_result = compile_intent_bundle_file_tool(
            source_path,
            second,
            target="react-tailwind-tsx",
            cwd=tmp_path,
        )

        assert first_result["ok"] is True
        assert second_result["ok"] is True
        assert _files(first) == _files(second)
        manifest = json.loads((first / "provenance_manifest.json").read_text(encoding="utf-8"))
        assert manifest["artifact_hash"]
        assert manifest["source_hash"]
        assert manifest["semantic_digest"]
        assert manifest["nodes"]
        assert all(node["intent_refs"] for node in manifest["nodes"].values())
        assert all(
            ref.startswith("node:")
            for node in manifest["nodes"].values()
            for ref in node["content_refs"]
        )
