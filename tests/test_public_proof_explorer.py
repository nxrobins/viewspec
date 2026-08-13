from __future__ import annotations

import hashlib
import json
from pathlib import Path

from demos.build_proof_explorer import build


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_EXPLORER = ROOT / "demos" / "proof-explorer"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_public_proof_explorer_is_a_deterministic_projection_of_retained_evidence(tmp_path):
    generated = build(tmp_path / "proof-explorer")
    committed = json.loads(
        (PUBLIC_EXPLORER / "proof-data.json").read_text(encoding="utf-8")
    )

    assert generated == committed
    assert committed["kind"] == "viewspec_public_proof_explorer"
    assert committed["schema_version"] == 1
    assert committed["summary"] == {
        "applied_receipt_count": 10,
        "case_count": 10,
        "conformant_count": 10,
        "critical_issue_count": 0,
        "first_compile_pass_count": 10,
        "negative_control_count": 6,
        "passed_gate_count": 8,
        "verified_correction_count": 10,
    }
    assert len(committed["cases"]) == 10
    assert len(committed["gates"]) == 8
    assert len(committed["negative_controls"]) == 6
    assert "arbitrary briefs" in committed["scope"]


def test_public_proof_explorer_binds_every_image_and_source_to_its_hash():
    contract = json.loads(
        (PUBLIC_EXPLORER / "proof-data.json").read_text(encoding="utf-8")
    )

    for source in contract["sources"]:
        source_path = ROOT / source["path"]
        assert source_path.is_file()
        assert _sha256(source_path) == source["sha256"]

    viewports = {viewport["id"]: viewport for viewport in contract["viewports"]}
    for case in contract["cases"]:
        assert case["status"] == "conformant"
        assert case["critical_issues"] == []
        assert len(case["scores"]) == 5
        assert case["correction"]["receipt_status"] == "applied"
        assert case["correction"]["compile_status"] == "passed"
        assert case["correction"]["verification_status"] == "conformant"
        for viewport_id, screenshot in case["evidence"]["screenshots"].items():
            image_path = PUBLIC_EXPLORER / screenshot["path"]
            viewport = viewports[viewport_id]
            assert image_path.is_file()
            assert _sha256(image_path) == screenshot["sha256"]
            assert image_path.stat().st_size == screenshot["bytes"]
            assert screenshot["width"] == viewport["width"]
            assert screenshot["height"] >= viewport["height"]
            assert screenshot["capture_kind"] in {"viewport", "full_page"}


def test_public_proof_explorer_contract_is_bounded_and_safe_to_publish():
    contract_path = PUBLIC_EXPLORER / "proof-data.json"
    text = contract_path.read_text(encoding="utf-8")
    html = (PUBLIC_EXPLORER / "index.html").read_text(encoding="utf-8")
    script = (PUBLIC_EXPLORER / "proof-explorer.js").read_text(encoding="utf-8")

    assert contract_path.stat().st_size < 200_000
    assert "/Users/" not in text
    assert "approval_token" not in text
    assert "generated turn context" not in text.lower()
    assert "Inspect the evidence" in html
    assert "fixed-corpus evidence" in html
    assert "proof-data.json" in html
    assert "URLSearchParams" in script
    assert "aria-pressed" in script
