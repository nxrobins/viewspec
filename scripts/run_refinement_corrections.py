#!/usr/bin/env python3
"""Preview, verify, apply, and receipt the fixed core-refinement corrections."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
from typing import Any

from viewspec.conformance import load_conformance_corpus
from viewspec.intent_patch import (
    INTENT_PATCH_CONTRACT_PROFILE,
    apply_intent_patch_file,
    preview_intent_patch,
    source_sha256,
)
from viewspec.local_tools import atomic_write


def _patch_payload(source_text: str, correction: dict[str, Any]) -> dict[str, Any]:
    operation = {
        "op": "replace_semantic_attr",
        "node_id": correction["node_id"],
        "attr": correction["attr"],
        "old_value": correction["old_value"],
        "value": correction["value"],
    }
    if correction["source_kind"] == "app_bundle":
        operation["screen_id"] = correction["screen_id"]
    return {
        "schema_version": 1,
        "contract_profile": INTENT_PATCH_CONTRACT_PROFILE,
        "source_kind": correction["source_kind"],
        "base_source_sha256": source_sha256(source_text),
        "operations": [operation],
        "evidence_refs": [f"scorecard:{correction['case_id']}"],
    }


def run(
    *,
    corpus_path: str | Path,
    corrections_path: str | Path,
    install: bool,
) -> dict[str, Any]:
    corpus = load_conformance_corpus(corpus_path)
    case_by_id = {case.id: case for case in corpus.cases}
    correction_payload = json.loads(Path(corrections_path).read_text(encoding="utf-8"))
    corrections = correction_payload.get("corrections")
    if not isinstance(corrections, list):
        raise ValueError("refinement corrections must be an array")
    if [item.get("case_id") for item in corrections if isinstance(item, dict)] != sorted(case_by_id):
        raise ValueError("refinement corrections must cover the fixed corpus exactly once in canonical order")

    reports: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="viewspec-refinement-corrections-") as directory:
        workspace = Path(directory)
        for correction in corrections:
            if not isinstance(correction, dict):
                raise ValueError("refinement correction entries must be objects")
            case_id = str(correction["case_id"])
            source_text = case_by_id[case_id].source_path.read_text(encoding="utf-8")
            patch = _patch_payload(source_text, correction)
            preview = preview_intent_patch(
                source_text,
                patch,
                verify=True,
                install=install,
            )

            source_path = workspace / f"{case_id}.source.json"
            patch_path = workspace / f"{case_id}.intentpatch.json"
            atomic_write(source_path, source_text)
            atomic_write(patch_path, json.dumps(patch, indent=2, sort_keys=True) + "\n")
            receipt = apply_intent_patch_file(
                source_path,
                patch_path,
                approval_token=preview.approval_token,
                verify=True,
                install=install,
            )
            reports.append(
                {
                    "case_id": case_id,
                    "source_kind": correction["source_kind"],
                    "target": {
                        "screen_id": correction.get("screen_id"),
                        "node_id": correction["node_id"],
                        "attr": correction["attr"],
                    },
                    "base_source_sha256": preview.base_source_sha256,
                    "candidate_source_sha256": preview.candidate_source_sha256,
                    "preview_id": preview.preview_id,
                    "patch_id": preview.patch.patch_id,
                    "semantic_diff_ok": preview.semantic_diff.get("ok") is True,
                    "compile_check": preview.compile_check,
                    "verification": preview.verification,
                    "receipt": receipt.to_json(),
                }
            )

    return {
        "schema_version": 1,
        "ok": all(
            item["semantic_diff_ok"]
            and item["compile_check"].get("status") == "passed"
            and item["verification"].get("status") == "conformant"
            and item["receipt"].get("status") == "applied"
            for item in reports
        ),
        "case_count": len(reports),
        "cases": reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        default="conformance/verification/corpus.json",
        help="Fixed conformance corpus manifest.",
    )
    parser.add_argument(
        "--corrections",
        default="conformance/refinement/corrections.json",
        help="Fixed semantic correction manifest.",
    )
    parser.add_argument(
        "--output",
        default="correction-proof-report.json",
        help="Machine-readable correction proof report.",
    )
    parser.add_argument("--install", action="store_true", help="Prepare locked browser verifier dependencies.")
    args = parser.parse_args()

    report = run(
        corpus_path=args.corpus,
        corrections_path=args.corrections,
        install=args.install,
    )
    atomic_write(Path(args.output), json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
