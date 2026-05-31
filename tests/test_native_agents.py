from __future__ import annotations

import json

from viewspec import agent_asset_readiness
from viewspec.agent import AGENT_INTENT_BUNDLE_SCHEMA, AGENT_SYSTEM_PROMPT
from viewspec.cli import main as cli_main
from viewspec.native_agents import BEGIN_MARKER, END_MARKER


def test_init_agent_creates_codex_instructions(tmp_path, capsys):
    assert cli_main(["init-agent", "--target", "codex", "--root", str(tmp_path)]) == 0
    output = json.loads(capsys.readouterr().out)
    path = tmp_path / "AGENTS.md"

    assert output["ok"] is True
    assert output["changes"] == [{"action": "create", "path": "AGENTS.md", "target": "codex"}]
    text = path.read_text(encoding="utf-8")
    assert BEGIN_MARKER in text
    assert END_MARKER in text
    assert "viewspec.intent.json" in text
    assert "viewspec validate-intent viewspec.intent.json --json" in text
    assert "viewspec compile viewspec.intent.json --design DESIGN.md --out dist/" in text
    assert "viewspec check dist/" in text
    assert "If `DESIGN.md` is missing" in text
    assert "viewspec init-design --out DESIGN.md" in text
    assert "viewspec diff-intent old.intent.json new.intent.json --json" in text
    assert "viewspec init-intent --out viewspec.intent.json" in text
    assert "viewspec export-agent-assets --out .viewspec" in text
    assert "Use raw HTML tools only when importing existing HTML" in text
    assert "Never patch or recursively compile generated `dist/index.html`" in text
    assert "Do not upload, share, call hosted APIs" in text
    assert "compile_html_file" not in text
    assert "lift_html_file" not in text


def test_init_agent_replaces_one_block_and_preserves_surrounding_content(tmp_path):
    path = tmp_path / "AGENTS.md"
    prefix = "Existing intro\r\nKeep this exact line.\r\n"
    suffix = "Existing outro.\r\n"
    old_block = f"{BEGIN_MARKER}\nold managed text\n{END_MARKER}\n"
    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(f"{prefix}{old_block}{suffix}")

    assert cli_main(["init-agent", "--target", "codex", "--root", str(tmp_path)]) == 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        updated = handle.read()

    assert updated.startswith(prefix)
    assert updated.endswith(suffix)
    assert updated.count(BEGIN_MARKER) == 1
    assert updated.count(END_MARKER) == 1
    assert "old managed text" not in updated

    before = updated
    assert cli_main(["init-agent", "--target", "codex", "--root", str(tmp_path)]) == 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        assert handle.read() == before


def test_init_agent_conflict_fails_without_partial_writes(tmp_path, capsys):
    (tmp_path / "AGENTS.md").write_text(
        f"{BEGIN_MARKER}\none\n{END_MARKER}\n{BEGIN_MARKER}\ntwo\n{END_MARKER}\n",
        encoding="utf-8",
    )

    assert cli_main(["init-agent", "--target", "all", "--root", str(tmp_path)]) == 2
    assert "MARKER_CONFLICT" in capsys.readouterr().err
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / ".cursor").exists()
    assert not (tmp_path / ".github").exists()


def test_init_agent_late_conflict_fails_without_earlier_partial_writes(tmp_path, capsys):
    claude = tmp_path / "CLAUDE.md"
    claude.write_text(
        f"{BEGIN_MARKER}\none\n{END_MARKER}\n{BEGIN_MARKER}\ntwo\n{END_MARKER}\n",
        encoding="utf-8",
    )

    assert cli_main(["init-agent", "--target", "all", "--root", str(tmp_path)]) == 2
    assert "MARKER_CONFLICT" in capsys.readouterr().err
    assert not (tmp_path / "AGENTS.md").exists()
    assert claude.exists()
    assert not (tmp_path / ".cursor").exists()
    assert not (tmp_path / ".github").exists()


def test_init_agent_dry_run_creates_no_files_or_directories(tmp_path, capsys):
    assert cli_main(["init-agent", "--target", "cursor", "--root", str(tmp_path), "--dry-run"]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["changes"] == [{"action": "create", "path": ".cursor/rules/viewspec.mdc", "target": "cursor"}]
    assert not (tmp_path / ".cursor").exists()


def test_init_agent_all_creates_all_targets(tmp_path):
    assert cli_main(["init-agent", "--target", "all", "--root", str(tmp_path)]) == 0

    assert (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / "CLAUDE.md").exists()
    assert (tmp_path / ".cursor/rules/viewspec.mdc").exists()
    assert (tmp_path / ".github/copilot-instructions.md").exists()


def test_export_agent_assets_creates_local_prompt_and_schema(tmp_path, capsys):
    out_dir = tmp_path / ".viewspec"

    assert cli_main(["export-agent-assets", "--out", str(out_dir)]) == 0
    payload = json.loads(capsys.readouterr().out)

    prompt_path = out_dir / "agent-system-prompt.txt"
    schema_path = out_dir / "agent-intent-bundle.schema.json"
    assert payload["ok"] is True
    assert payload["schema_version"] == 1
    assert {item["path"]: item["action"] for item in payload["files"]} == {
        "agent-system-prompt.txt": "create",
        "agent-intent-bundle.schema.json": "create",
    }
    assert prompt_path.read_text(encoding="utf-8") == AGENT_SYSTEM_PROMPT
    assert json.loads(schema_path.read_text(encoding="utf-8")) == AGENT_INTENT_BUNDLE_SCHEMA

    assert cli_main(["export-agent-assets", "--out", str(out_dir)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert {item["path"]: item["action"] for item in payload["files"]} == {
        "agent-system-prompt.txt": "unchanged",
        "agent-intent-bundle.schema.json": "unchanged",
    }


def test_export_agent_assets_dry_run_creates_no_files(tmp_path, capsys):
    out_dir = tmp_path / ".viewspec"

    assert cli_main(["export-agent-assets", "--out", str(out_dir), "--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert {item["path"]: item["action"] for item in payload["files"]} == {
        "agent-system-prompt.txt": "create",
        "agent-intent-bundle.schema.json": "create",
    }
    assert not out_dir.exists()


def test_export_agent_assets_refuses_conflict_without_partial_writes(tmp_path, capsys):
    out_dir = tmp_path / ".viewspec"
    out_dir.mkdir()
    (out_dir / "agent-system-prompt.txt").write_text("custom prompt\n", encoding="utf-8")

    assert cli_main(["export-agent-assets", "--out", str(out_dir)]) == 2
    assert "AGENT_ASSET_CONFLICT" in capsys.readouterr().err
    assert not (out_dir / "agent-intent-bundle.schema.json").exists()
    assert (out_dir / "agent-system-prompt.txt").read_text(encoding="utf-8") == "custom prompt\n"

    assert cli_main(["export-agent-assets", "--out", str(out_dir), "--force"]) == 0
    assert (out_dir / "agent-system-prompt.txt").read_text(encoding="utf-8") == AGENT_SYSTEM_PROMPT
    assert json.loads((out_dir / "agent-intent-bundle.schema.json").read_text(encoding="utf-8")) == AGENT_INTENT_BUNDLE_SCHEMA


def test_agent_asset_readiness_reports_local_contract_identity():
    readiness = agent_asset_readiness()

    assert readiness["ok"] is True
    assert readiness["schema_version"] == 1
    assert readiness["system_prompt_file"] == "agent-system-prompt.txt"
    assert readiness["intent_schema_file"] == "agent-intent-bundle.schema.json"
    assert readiness["intent_schema_id"] == "https://viewspec.dev/agent-intent-bundle.schema.json"
    assert readiness["export_command"] == "viewspec export-agent-assets --out .viewspec"
    assert len(readiness["system_prompt_sha256"]) == 64
    assert len(readiness["intent_schema_sha256"]) == 64


def test_export_agent_assets_rejects_file_output_path(tmp_path, capsys):
    out_path = tmp_path / ".viewspec"
    out_path.write_text("not a directory", encoding="utf-8")

    assert cli_main(["export-agent-assets", "--out", str(out_path)]) == 2
    assert "AGENT_ASSET_OUTPUT_NOT_DIRECTORY" in capsys.readouterr().err
    assert out_path.read_text(encoding="utf-8") == "not a directory"
