import json
import importlib

from viewspec.cli import main as cli_main
from viewspec.intent_tools import init_intent_file
from viewspec.local_tools import check_artifact_dir
from viewspec.prove import prove, prove_tool


def test_prove_default_writes_checked_source_report(tmp_path):
    out_dir = tmp_path / "proof"

    report = prove(out_dir=out_dir, cwd=tmp_path)

    assert report["ok"] is True
    assert report["schema_version"] == 1
    assert report["proof_level"] == "source_artifact"
    assert report["target"] == "html-tailwind"
    assert report["checks"]["intent"] == "generated"
    assert report["checks"]["design"] == "generated"
    assert report["checks"]["artifact_check"] == "passed"
    assert report["checks"]["host_verify"] == "not_applicable"
    assert report["metadata"]["network_calls"] == "none"
    assert out_dir.joinpath("viewspec.intent.json").exists()
    assert out_dir.joinpath("DESIGN.md").exists()
    assert out_dir.joinpath("proof_report.json").exists()
    assert out_dir.joinpath("PROOF.md").exists()
    assert report["paths"]["proof_summary"] == str(out_dir / "PROOF.md")
    assert check_artifact_dir(out_dir / "artifact")["ok"] is True
    assert json.loads(out_dir.joinpath("proof_report.json").read_text(encoding="utf-8")) == report
    proof_text = out_dir.joinpath("PROOF.md").read_text(encoding="utf-8")
    assert "Status: **PASSED**" in proof_text
    assert "Proof level: `source_artifact`" in proof_text
    assert "Target: `html-tailwind`" in proof_text
    assert "Artifact SHA-256:" in proof_text
    assert "Manifest SHA-256:" in proof_text
    assert "Proof report SHA-256:" in proof_text
    assert "Network/install policy: `none`" in proof_text
    assert "pixel-perfect visual regression" in proof_text


def test_prove_existing_intent_cli_json(tmp_path, capsys):
    intent = tmp_path / "viewspec.intent.json"
    init_intent_file(intent)
    out_dir = tmp_path / "proof"

    assert cli_main(["prove", "--intent", str(intent), "--out", str(out_dir), "--target", "html-tailwind", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["checks"]["intent"] == "provided"
    assert payload["checks"]["compile"] == "passed"
    assert payload["paths"]["intent"] == str(intent.resolve())
    assert payload["paths"]["report"].endswith("proof_report.json")
    assert payload["paths"]["proof_summary"].endswith("PROOF.md")
    assert payload["artifact_hash"]
    assert payload["manifest_hash"]


def test_prove_existing_output_requires_force(tmp_path):
    out_dir = tmp_path / "proof"
    out_dir.mkdir()

    blocked = prove(out_dir=out_dir, cwd=tmp_path)

    assert blocked["ok"] is False
    assert blocked["errors"][0]["code"] == "PROVE_OUTPUT_EXISTS"
    assert out_dir.joinpath("proof_report.json").exists()
    assert out_dir.joinpath("PROOF.md").exists()
    proof_text = out_dir.joinpath("PROOF.md").read_text(encoding="utf-8")
    assert "Status: **FAILED**" in proof_text
    assert "PROVE_OUTPUT_EXISTS" in proof_text
    assert prove(out_dir=out_dir, cwd=tmp_path, force=True)["ok"] is True


def test_prove_rejects_unsafe_output_without_writing_report(tmp_path):
    report = prove(out_dir=tmp_path, cwd=tmp_path)

    assert report["ok"] is False
    assert report["errors"][0]["code"] == "PROVE_OUTPUT_UNSAFE"
    assert not tmp_path.joinpath("proof_report.json").exists()
    assert not tmp_path.joinpath("PROOF.md").exists()


def test_prove_react_tailwind_preserves_host_failure_code(tmp_path, monkeypatch):
    def fake_verify_host_artifact_dir(*_args, **_kwargs):
        return {
            "ok": False,
            "errors": [
                {
                    "code": "HOST_VERIFY_NODE_MODULES_MISSING",
                    "message": "Missing vite in node_modules/.bin.",
                    "fix": "Run with --install.",
                }
            ],
        }

    prove_module = importlib.import_module("viewspec.prove")
    monkeypatch.setattr(prove_module, "verify_host_artifact_dir", fake_verify_host_artifact_dir)

    report = prove(out_dir=tmp_path / "proof", target="react-tailwind-tsx", cwd=tmp_path)

    assert report["ok"] is False
    assert report["proof_level"] == "react_tailwind_reference_host"
    assert report["checks"]["artifact_check"] == "passed"
    assert report["checks"]["host_verify"] == "failed"
    assert report["errors"][0]["code"] == "HOST_VERIFY_NODE_MODULES_MISSING"
    proof_text = tmp_path.joinpath("proof/PROOF.md").read_text(encoding="utf-8")
    assert "Status: **FAILED**" in proof_text
    assert "Host Verification" in proof_text
    assert "Bounded React/Vite/Tailwind reference-host proof" not in proof_text


def test_prove_summary_generation_failure_returns_exact_code(tmp_path, monkeypatch):
    prove_module = importlib.import_module("viewspec.prove")
    monkeypatch.setattr(prove_module, "_render_proof_summary", lambda *_args, **_kwargs: "x" * (33 * 1024))

    report = prove(out_dir=tmp_path / "proof", cwd=tmp_path)

    assert report["ok"] is False
    assert report["checks"]["proof_summary"] == "failed"
    assert report["errors"][-1]["code"] == "PROVE_SUMMARY_WRITE_FAILED"
    assert tmp_path.joinpath("proof/proof_report.json").exists()
    assert not tmp_path.joinpath("proof/PROOF.md").exists()


def test_prove_tool_returns_standard_envelope_and_respects_cwd(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"

    blocked = prove_tool(out_dir=outside, cwd=tmp_path)
    assert blocked["ok"] is False
    assert blocked["errors"][0]["code"] == "PATH_OUTSIDE_CWD"

    result = prove_tool(out_dir="proof", cwd=tmp_path)
    assert result["schema_version"] == 1
    assert result["ok"] is True
    assert result["proof_report"]["ok"] is True
    assert result["proof_report"]["paths"]["proof_dir"].endswith("proof")
    assert result["paths"]["proof_summary"].endswith("PROOF.md")
