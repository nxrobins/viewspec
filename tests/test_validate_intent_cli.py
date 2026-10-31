from __future__ import annotations

import json
import subprocess
import sys

import pytest
from hypothesis import given
from hypothesis import strategies as st

from viewspec import ViewSpecBuilder
from viewspec.aesthetics import profile_style_facts
from viewspec.agent import _strict_json_loads
from viewspec.cli import main as cli_main
from viewspec.intent_tools import STARTER_INTENT_KINDS, validate_intent_file


def _valid_bundle_json() -> dict:
    builder = ViewSpecBuilder("validate_cli")
    dashboard = builder.add_dashboard("cards", region="main", group_id="cards")
    dashboard.add_card(label="Revenue", value="$12", id="revenue")
    return builder.build_bundle().to_json()


def _profile_workspace_bundle_json(profile: str) -> dict:
    builder = ViewSpecBuilder(
        "validate_cli_profile_workspace",
        root_attrs={"title": "Profile Workspace"},
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
        eyebrow="Operations",
        title="Profile workspace",
        description="Check output should expose manifest layout facts.",
        region="north",
        group_id="intro",
    )
    dashboard = builder.add_dashboard("numbers", region="focus", group_id="metrics")
    dashboard.add_card(label="Open", value="4", id="open")
    dashboard.add_card(label="Blocked", value="1", id="blocked")
    dashboard.add_card(label="Ready", value="9", id="ready")
    detail = builder.add_detail("identity", region="assist", group_id="details")
    detail.add_field(label="Manifest", value="checked", id="manifest")
    return builder.build_bundle().to_json()


def test_validate_intent_valid_bundle_exits_zero_and_returns_json(tmp_path, capsys):
    path = tmp_path / "viewspec.intent.json"
    path.write_text(json.dumps(_valid_bundle_json()), encoding="utf-8")

    assert cli_main(["check", str(path), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload == {
        "schema_version": 1,
        "ok": True,
        "compile_check": "passed",
        "issues": [],
        "repair_checklist": [],
        "correction_prompt": None,
    }


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("", "INVALID_JSON"),
        ("not json", "INVALID_JSON"),
        ("```json\n{}\n```", "INVALID_JSON"),
        (json.dumps([]), "INVALID_PAYLOAD"),
        (json.dumps({"id": "root", "primitive": "stack", "children": []}), "COMPOSITION_IR_INPUT"),
    ],
)
def test_validate_intent_rejects_non_intent_payloads(tmp_path, capsys, text, code):
    path = tmp_path / "bad.json"
    path.write_text(text, encoding="utf-8")

    assert cli_main(["check", str(path), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["schema_version"] == 1
    assert payload["ok"] is False
    assert payload["compile_check"] == "failed"
    assert any(issue["code"] == code for issue in payload["issues"])
    assert all(issue["suggestion"] for issue in payload["issues"])
    assert payload["correction_prompt"]
    assert payload["repair_checklist"]
    assert any("Regenerate the full IntentBundle" in item for item in payload["repair_checklist"])
    assert "Output strict JSON only" in payload["correction_prompt"]
    assert "Do not patch fragments" in payload["correction_prompt"]


@pytest.mark.parametrize(
    ("text", "expected_message"),
    [
        ('{"substrate":{},"substrate":{},"view_spec":{}}', "duplicate object key 'substrate'"),
        (lambda: json.dumps(_valid_bundle_json()).replace('"$12"', "NaN", 1), "non-standard JSON constant 'NaN'"),
        (
            lambda: json.dumps(_valid_bundle_json()).replace('"$12"', "Infinity", 1),
            "non-standard JSON constant 'Infinity'",
        ),
    ],
)
def test_validate_intent_rejects_non_strict_json(tmp_path, capsys, text, expected_message):
    payload_text = text() if callable(text) else text
    path = tmp_path / "bad.json"
    path.write_text(payload_text, encoding="utf-8")

    assert cli_main(["check", str(path), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert payload["issues"][0]["code"] == "INVALID_JSON"
    assert expected_message in payload["issues"][0]["message"]
    assert payload["repair_checklist"]
    assert payload["correction_prompt"]


@given(
    key=st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=30),
    first=st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=30)),
    second=st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=30)),
)
def test_strict_json_rejects_arbitrary_duplicate_keys_instead_of_last_wins(key, first, second):
    text = "{" + json.dumps(key) + ":" + json.dumps(first) + "," + json.dumps(key) + ":" + json.dumps(second) + "}"

    with pytest.raises(ValueError, match="duplicate object key"):
        _strict_json_loads(text)


def test_validate_intent_no_compile_check_returns_skipped(tmp_path, capsys):
    path = tmp_path / "viewspec.intent.json"
    path.write_text(json.dumps(_valid_bundle_json()), encoding="utf-8")

    assert cli_main(["check", str(path), "--no-compile-check", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["compile_check"] == "skipped"
    assert payload["repair_checklist"] == []


def test_check_reports_fields_outside_the_local_contract(tmp_path, capsys):
    extended = _valid_bundle_json()
    extended["view_spec"]["inputs"] = [{"id": "phase_filter"}]
    path = tmp_path / "extended.intent.json"
    path.write_text(json.dumps(extended), encoding="utf-8")

    assert cli_main(["check", str(path), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert any(
        issue["code"] == "UNKNOWN_FIELD" and issue["path"] == "$.view_spec.inputs" for issue in payload["issues"]
    )


def test_validate_intent_missing_file_returns_json_payload(tmp_path, capsys):
    path = tmp_path / "missing.intent.json"

    assert cli_main(["check", str(path), "--json"]) == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert captured.err == ""
    assert payload["schema_version"] == 1
    assert payload["ok"] is False
    assert payload["compile_check"] == "failed"
    assert payload["issues"][0]["code"] == "INTENT_FILE_NOT_FOUND"
    assert payload["correction_prompt"]


def test_validate_intent_process_exit_codes_match_public_contract(tmp_path):
    valid = tmp_path / "valid.intent.json"
    valid.write_text(json.dumps(_valid_bundle_json()), encoding="utf-8")
    invalid = tmp_path / "invalid.intent.json"
    invalid.write_text(json.dumps([]), encoding="utf-8")
    missing = tmp_path / "missing.intent.json"

    valid_code, valid_stdout, valid_stderr = _run_validate_intent_process(tmp_path, "valid", valid)
    invalid_code, invalid_stdout, invalid_stderr = _run_validate_intent_process(tmp_path, "invalid", invalid)
    missing_code, missing_stdout, missing_stderr = _run_validate_intent_process(tmp_path, "missing", missing)

    assert valid_code == 0
    assert json.loads(valid_stdout)["ok"] is True
    assert valid_stderr == ""
    assert invalid_code == 2
    assert json.loads(invalid_stdout)["ok"] is False
    assert invalid_stderr == ""
    assert missing_code == 2
    assert json.loads(missing_stdout)["issues"][0]["code"] == "INTENT_FILE_NOT_FOUND"
    assert missing_stderr == ""


def _run_validate_intent_process(tmp_path, name: str, path) -> tuple[int, str, str]:
    stdout_path = tmp_path / f"{name}.stdout"
    stderr_path = tmp_path / f"{name}.stderr"
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        result = subprocess.run(
            [sys.executable, "-m", "viewspec.cli", "check", str(path), "--json"],
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            check=False,
        )
    return (
        result.returncode,
        stdout_path.read_text(encoding="utf-8"),
        stderr_path.read_text(encoding="utf-8"),
    )


def test_validate_intent_does_not_mutate_file(tmp_path):
    path = tmp_path / "viewspec.intent.json"
    original = json.dumps(_valid_bundle_json(), indent=2, sort_keys=True)
    path.write_text(original, encoding="utf-8")

    assert cli_main(["check", str(path), "--json"]) == 0

    assert path.read_text(encoding="utf-8") == original


def test_compile_invalid_intent_returns_correction_payload_without_writes(tmp_path, capsys):
    path = tmp_path / "viewspec.intent.json"
    path.write_text(json.dumps([]), encoding="utf-8")
    out_dir = tmp_path / "dist"

    assert cli_main(["compile", str(path), "--out", str(out_dir)]) == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    assert "IntentBundle validation failed" in captured.err
    assert "INVALID_PAYLOAD" in captured.err
    assert "correction_prompt" in captured.err
    assert not out_dir.exists()


def test_compile_invalid_intent_reports_validation_before_missing_design(tmp_path, capsys):
    path = tmp_path / "viewspec.intent.json"
    path.write_text(json.dumps([]), encoding="utf-8")
    out_dir = tmp_path / "dist"
    missing_design = tmp_path / "missing-DESIGN.md"

    assert cli_main(["compile", str(path), "--design", str(missing_design), "--out", str(out_dir)]) == 2
    captured = capsys.readouterr()

    assert "IntentBundle validation failed" in captured.err
    assert "INVALID_PAYLOAD" in captured.err
    assert "missing-DESIGN.md" not in captured.err
    assert not out_dir.exists()


def test_check_human_output_prints_manifest_summary_for_aesthetic_artifact(tmp_path, capsys):
    style_facts = profile_style_facts("aesthetic.editorial_product")
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(
        json.dumps(_profile_workspace_bundle_json("aesthetic.editorial_product")),
        encoding="utf-8",
    )
    out_dir = tmp_path / "react-tailwind-dist"

    assert cli_main(["compile", str(intent_path), "--target", "react-tailwind-tsx", "--out", str(out_dir)]) == 0
    capsys.readouterr()

    assert cli_main(["check", str(out_dir)]) == 0
    output = capsys.readouterr().out

    assert output.startswith("ok\n")
    assert "manifest: kind=intent_bundle_compile emitter=react_tailwind_tsx artifact=ViewSpecView.tsx nodes=" in output
    assert "aesthetic_profile: aesthetic.editorial_product" in output
    assert (
        "aesthetic_style: "
        f"profile=aesthetic.editorial_product "
        f"changed_tokens={style_facts['changed_token_count']} "
        f"categories={style_facts['category_count']} "
        f"declarations={style_facts['declaration_count']}"
    ) in output
    assert "aesthetic_layout:\n" in output
    assert "  content_grid: columns=2 nodes=1 profile=aesthetic.editorial_product" in output
    assert "  metric_grid: columns=1 nodes=1 profile=aesthetic.editorial_product" in output


def test_check_human_output_prints_aesthetic_span_layout_summary(tmp_path, capsys):
    style_facts = profile_style_facts("aesthetic.premium_saas")
    intent_path = tmp_path / "viewspec.intent.json"
    intent_path.write_text(
        json.dumps(_profile_workspace_bundle_json("aesthetic.premium_saas")),
        encoding="utf-8",
    )
    out_dir = tmp_path / "react-tailwind-dist"

    assert cli_main(["compile", str(intent_path), "--target", "react-tailwind-tsx", "--out", str(out_dir)]) == 0
    capsys.readouterr()

    assert cli_main(["check", str(out_dir)]) == 0
    output = capsys.readouterr().out

    assert "aesthetic_profile: aesthetic.premium_saas" in output
    assert (
        "aesthetic_style: "
        f"profile=aesthetic.premium_saas "
        f"changed_tokens={style_facts['changed_token_count']} "
        f"categories={style_facts['category_count']} "
        f"declarations={style_facts['declaration_count']}"
    ) in output
    assert "  content_grid: columns=2 nodes=1 profile=aesthetic.premium_saas" in output
    assert "  metric_card: layout_emphasis=featured span_columns=2 nodes=1 profile=aesthetic.premium_saas" in output
    assert "  metric_card: columns=unknown" not in output
    assert "  metric_grid: columns=2 nodes=1 profile=aesthetic.premium_saas" in output


@pytest.mark.parametrize("kind", STARTER_INTENT_KINDS)
def test_init_intent_writes_valid_starter_bundle(tmp_path, capsys, kind):
    path = tmp_path / f"{kind}.intent.json"

    assert cli_main(["init", "--out", str(path), "--kind", kind]) == 0
    assert capsys.readouterr().out.strip() == str(path)

    validation = validate_intent_file(path)
    assert validation["ok"] is True
    assert validation["compile_check"] == "passed"

    html_out = tmp_path / f"{kind}-html"
    react_out = tmp_path / f"{kind}-react"
    assert cli_main(["compile", str(path), "--out", str(html_out)]) == 0
    capsys.readouterr()
    assert cli_main(["check", str(html_out), "--json"]) == 0
    capsys.readouterr()
    assert cli_main(["compile", str(path), "--target", "react-tsx", "--out", str(react_out)]) == 0
    capsys.readouterr()
    assert cli_main(["check", str(react_out), "--json"]) == 0
    capsys.readouterr()


def test_init_intent_refuses_overwrite_without_force(tmp_path):
    path = tmp_path / "viewspec.intent.json"
    path.write_text("{}", encoding="utf-8")

    assert cli_main(["init", "--out", str(path)]) == 2
    assert path.read_text(encoding="utf-8") == "{}"

    assert cli_main(["init", "--out", str(path), "--force"]) == 0
    assert validate_intent_file(path)["ok"] is True
