import json
import importlib

from viewspec import profile_style_facts
from viewspec.cli import main as cli_main
from viewspec.intent_tools import init_intent_file
from viewspec.local_tools import check_artifact_dir, file_hash
from viewspec.prove import prove, prove_tool
from viewspec.sdk.builder import ViewSpecBuilder


def _profile_workspace_bundle(profile: str):
    builder = ViewSpecBuilder(
        "profile_proof",
        root_attrs={"title": "Profile Proof"},
        default_main_region=False,
        root_min_children=2,
    )
    builder.set_aesthetic_profile(profile)
    builder.add_region("north", parent_region="root", role="banner", layout="stack", min_children=1)
    builder.add_region("canvas", parent_region="root", role="application", layout="grid", min_children=2)
    builder.add_region("focus", parent_region="canvas", role="primary", layout="stack", min_children=1)
    builder.add_region("assist", parent_region="canvas", role="complementary", layout="stack", min_children=1)
    builder.add_hero(
        "intro",
        eyebrow="Proof",
        title="Profile proof workspace",
        description="Manifest summary should expose profile layout.",
        region="north",
        group_id="intro",
    )
    dashboard = builder.add_dashboard("numbers", region="focus", group_id="metrics")
    dashboard.add_card(label="Open", value="4", id="open")
    dashboard.add_card(label="Blocked", value="1", id="blocked")
    dashboard.add_card(label="Ready", value="9", id="ready")
    detail = builder.add_detail("identity", region="assist", group_id="details")
    detail.add_field(label="Manifest", value="checked", id="manifest")
    return builder.build_bundle()


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
    assert report["manifest_summary"]["available"] is True
    assert report["manifest_summary"]["emitter"] == "html_tailwind"
    assert report["manifest_summary"]["node_count"] > 0
    assert out_dir.joinpath("viewspec.intent.json").exists()
    assert out_dir.joinpath("DESIGN.md").exists()
    assert out_dir.joinpath("proof_report.json").exists()
    assert out_dir.joinpath("PROOF.md").exists()
    assert out_dir.joinpath("support_bundle.json").exists()
    assert report["paths"]["proof_summary"] == str(out_dir / "PROOF.md")
    assert report["paths"]["support_bundle"] == str(out_dir / "support_bundle.json")
    assert check_artifact_dir(out_dir / "artifact")["ok"] is True
    assert json.loads(out_dir.joinpath("proof_report.json").read_text(encoding="utf-8")) == report
    proof_text = out_dir.joinpath("PROOF.md").read_text(encoding="utf-8")
    assert "Status: **PASSED**" in proof_text
    assert "Proof level: `source_artifact`" in proof_text
    assert "Target: `html-tailwind`" in proof_text
    assert "Artifact SHA-256:" in proof_text
    assert "Manifest SHA-256:" in proof_text
    assert "Proof report SHA-256:" in proof_text
    assert "Manifest Summary" in proof_text
    assert "Emitter: `html_tailwind`" in proof_text
    assert "Redacted support bundle path:" in proof_text
    assert "Network/install policy: `none`" in proof_text
    assert "pixel-perfect visual regression" in proof_text
    support_text = out_dir.joinpath("support_bundle.json").read_text(encoding="utf-8")
    support = json.loads(support_text)
    assert support["kind"] == "viewspec_proof_support_bundle"
    assert support["ok"] is True
    assert support["privacy"]["contains_raw_intent"] is False
    assert support["privacy"]["contains_absolute_paths"] is False
    assert support["proof_report_hash"] == file_hash(out_dir / "proof_report.json")
    assert support["manifest_summary"]["available"] is True
    assert support["manifest_summary"]["emitter"] == "html_tailwind"
    assert support["paths"]["proof_dir_name"] == "proof"
    assert str(tmp_path) not in support_text


def test_prove_reports_aesthetic_profile_layout_summary(tmp_path):
    style_facts = profile_style_facts("aesthetic.data_dense")
    intent = tmp_path / "profile.intent.json"
    intent.write_text(json.dumps(_profile_workspace_bundle("aesthetic.data_dense").to_json(), indent=2), encoding="utf-8")
    out_dir = tmp_path / "profile-proof"

    report = prove(intent_path=intent, out_dir=out_dir, cwd=tmp_path)

    assert report["ok"] is True
    summary = report["manifest_summary"]
    assert summary["aesthetic_profile"] == "aesthetic.data_dense"
    assert summary["aesthetic_style"]["changed_token_count"] == style_facts["changed_token_count"]
    assert summary["aesthetic_style"]["category_count"] == style_facts["category_count"]
    assert summary["aesthetic_style"]["declaration_count"] == style_facts["declaration_count"]
    assert "changed_tokens" not in summary["aesthetic_style"]
    assert summary["aesthetic_layout"]["content_grid"] == {
        "profile": "aesthetic.data_dense",
        "columns": 3,
        "node_count": 1,
    }
    assert summary["aesthetic_layout"]["metric_grid"] == {
        "profile": "aesthetic.data_dense",
        "columns": 3,
        "node_count": 1,
    }
    proof_text = out_dir.joinpath("PROOF.md").read_text(encoding="utf-8")
    assert "Aesthetic profile: `aesthetic.data_dense`" in proof_text
    assert (
        "Aesthetic style delta: profile `aesthetic.data_dense`, "
        f"changed_tokens `{style_facts['changed_token_count']}`, "
        f"categories `{style_facts['category_count']}`, "
        f"declarations `{style_facts['declaration_count']}`"
    ) in proof_text
    assert "Aesthetic layout `content_grid`: profile `aesthetic.data_dense`, columns `3`, nodes `1`" in proof_text
    support = json.loads(out_dir.joinpath("support_bundle.json").read_text(encoding="utf-8"))
    assert support["manifest_summary"]["aesthetic_style"]["category_count"] == style_facts["category_count"]
    assert support["manifest_summary"]["aesthetic_layout"]["metric_grid"]["columns"] == 3


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
    assert payload["paths"]["support_bundle"].endswith("support_bundle.json")
    assert payload["artifact_hash"]
    assert payload["manifest_hash"]


def test_prove_cli_human_output_prints_manifest_summary(tmp_path, capsys):
    out_dir = tmp_path / "proof"

    assert cli_main(["prove", "--out", str(out_dir)]) == 0
    output = capsys.readouterr().out

    assert output.startswith("ok\n")
    assert "proof_level: source_artifact" in output
    assert "proof_summary:" in output
    assert "support_bundle:" in output
    assert "manifest: kind=intent_bundle_compile emitter=html_tailwind artifact=index.html nodes=" in output


def test_prove_cli_react_tailwind_human_output_prints_host_assertions(tmp_path, monkeypatch, capsys):
    def fake_verify_host_artifact_dir(*_args, **_kwargs):
        return {
            "ok": True,
            "assertions": {
                "action_count": 2,
                "aesthetic_layout_assertion_count": 2,
                "aesthetic_profile_assertion_count": 1,
                "dom_count": 8,
                "grid_column_assertion_count": 1,
                "payload_binding_count": 2,
                "style_assertion_count": 6,
            },
            "assertion_requirements": {
                "aesthetic_layout_assertion_count": 2,
                "aesthetic_profile_assertion_count": 1,
                "dom_count": 1,
                "grid_span_assertion_count": 0,
                "style_assertion_count": 4,
            },
            "errors": [],
        }

    prove_module = importlib.import_module("viewspec.prove")
    monkeypatch.setattr(prove_module, "verify_host_artifact_dir", fake_verify_host_artifact_dir)

    assert cli_main(["prove", "--out", str(tmp_path / "proof"), "--target", "react-tailwind-tsx"]) == 0
    output = capsys.readouterr().out

    assert output.startswith("ok\n")
    assert "proof_level: react_tailwind_reference_host" in output
    assert "manifest: kind=intent_bundle_compile emitter=react_tailwind_tsx artifact=ViewSpecView.tsx nodes=" in output
    assert "host_verification: passed" in output
    assert "host_assertions:\n" in output
    assert "  action_count: 2" in output
    assert "  aesthetic_layout_assertion_count: 2" in output
    assert "  aesthetic_profile_assertion_count: 1" in output
    assert "  dom_count: 8" in output
    assert "  grid_column_assertion_count: 1" in output
    assert "  payload_binding_count: 2" in output
    assert "  style_assertion_count: 6" in output
    assert "host_assertion_requirements:\n" in output
    assert "  dom_count: 1" in output
    assert "  style_assertion_count: 4" in output


def test_prove_existing_output_requires_force(tmp_path):
    out_dir = tmp_path / "proof"
    out_dir.mkdir()

    blocked = prove(out_dir=out_dir, cwd=tmp_path)

    assert blocked["ok"] is False
    assert blocked["errors"][0]["code"] == "PROVE_OUTPUT_EXISTS"
    assert out_dir.joinpath("proof_report.json").exists()
    assert out_dir.joinpath("PROOF.md").exists()
    assert out_dir.joinpath("support_bundle.json").exists()
    proof_text = out_dir.joinpath("PROOF.md").read_text(encoding="utf-8")
    assert "Status: **FAILED**" in proof_text
    assert "PROVE_OUTPUT_EXISTS" in proof_text
    support_text = out_dir.joinpath("support_bundle.json").read_text(encoding="utf-8")
    assert "PROVE_OUTPUT_EXISTS" in support_text
    assert str(tmp_path) not in support_text
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


def test_prove_support_bundle_generation_failure_returns_exact_code(tmp_path, monkeypatch):
    prove_module = importlib.import_module("viewspec.prove")
    monkeypatch.setattr(prove_module, "_render_support_bundle", lambda *_args, **_kwargs: "x" * (17 * 1024))

    report = prove(out_dir=tmp_path / "proof", cwd=tmp_path)

    assert report["ok"] is False
    assert report["checks"]["support_bundle"] == "failed"
    assert report["errors"][-1]["code"] == "PROVE_SUPPORT_BUNDLE_WRITE_FAILED"
    assert tmp_path.joinpath("proof/proof_report.json").exists()
    assert tmp_path.joinpath("proof/PROOF.md").exists()
    assert not tmp_path.joinpath("proof/support_bundle.json").exists()
    proof_text = tmp_path.joinpath("proof/PROOF.md").read_text(encoding="utf-8")
    assert "PROVE_SUPPORT_BUNDLE_WRITE_FAILED" in proof_text


def test_prove_support_bundle_rejects_full_path_content(tmp_path, monkeypatch):
    prove_module = importlib.import_module("viewspec.prove")

    def leaking_support_bundle(report, **_kwargs):
        return json.dumps({"leak": report["paths"]["report"]})

    monkeypatch.setattr(prove_module, "_render_support_bundle", leaking_support_bundle)

    report = prove(out_dir=tmp_path / "proof", cwd=tmp_path)

    assert report["ok"] is False
    assert report["checks"]["support_bundle"] == "failed"
    assert report["errors"][-1]["code"] == "PROVE_SUPPORT_BUNDLE_CONTENT_FORBIDDEN"
    assert tmp_path.joinpath("proof/proof_report.json").exists()
    assert tmp_path.joinpath("proof/PROOF.md").exists()
    assert not tmp_path.joinpath("proof/support_bundle.json").exists()


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
    assert result["paths"]["support_bundle"].endswith("support_bundle.json")
    assert result["metadata"]["checks"]["artifact_check"] == "passed"
    assert result["metadata"]["checks"]["host_verify"] == "not_applicable"
    assert result["metadata"]["manifest_summary"]["available"] is True
    assert result["metadata"]["manifest_summary"]["emitter"] == "html_tailwind"
    assert result["metadata"]["host_verification"] is None
    identity = result["metadata"]["proof_identity"]
    assert identity["artifact_hash"] == result["proof_report"]["artifact_hash"]
    assert identity["manifest_hash"] == result["proof_report"]["manifest_hash"]
    assert identity["proof_report_hash"] == file_hash(tmp_path / "proof/proof_report.json")
    assert identity["proof_summary_hash"] == file_hash(tmp_path / "proof/PROOF.md")
    assert identity["support_bundle_hash"] == file_hash(tmp_path / "proof/support_bundle.json")


def test_prove_tool_metadata_exposes_react_tailwind_host_summary(tmp_path, monkeypatch):
    def fake_verify_host_artifact_dir(*_args, **_kwargs):
        return {
            "ok": True,
            "assertions": {
                "action_count": 1,
                "aesthetic_layout_assertion_count": 2,
                "aesthetic_profile_assertion_count": 1,
                "dom_count": 5,
                "grid_column_assertion_count": 1,
                "payload_binding_count": 1,
                "style_assertion_count": 6,
            },
            "assertion_requirements": {
                "aesthetic_layout_assertion_count": 2,
                "aesthetic_profile_assertion_count": 1,
                "dom_count": 1,
                "grid_span_assertion_count": 0,
                "style_assertion_count": 4,
            },
            "errors": [],
        }

    prove_module = importlib.import_module("viewspec.prove")
    monkeypatch.setattr(prove_module, "verify_host_artifact_dir", fake_verify_host_artifact_dir)

    result = prove_tool(out_dir="react-proof", target="react-tailwind-tsx", cwd=tmp_path)

    assert result["ok"] is True
    assert result["metadata"]["checks"]["host_verify"] == "passed"
    assert result["metadata"]["manifest_summary"]["emitter"] == "react_tailwind_tsx"
    assert result["metadata"]["manifest_summary"]["artifact_file"] == "ViewSpecView.tsx"
    assert result["metadata"]["proof_identity"]["artifact_hash"] == result["proof_report"]["artifact_hash"]
    assert result["metadata"]["proof_identity"]["manifest_hash"] == result["proof_report"]["manifest_hash"]
    assert result["metadata"]["proof_identity"]["proof_report_hash"] == file_hash(tmp_path / "react-proof/proof_report.json")
    assert result["metadata"]["host_verification"] == {
        "ok": True,
        "assertions": {
            "action_count": 1,
            "aesthetic_layout_assertion_count": 2,
            "aesthetic_profile_assertion_count": 1,
            "dom_count": 5,
            "grid_column_assertion_count": 1,
            "payload_binding_count": 1,
            "style_assertion_count": 6,
        },
        "assertion_requirements": {
            "aesthetic_layout_assertion_count": 2,
            "aesthetic_profile_assertion_count": 1,
            "dom_count": 1,
            "grid_span_assertion_count": 0,
            "style_assertion_count": 4,
        },
        "error_codes": [],
    }
    assert result["proof_report"]["host_report"]["assertions"]["style_assertion_count"] == 6
