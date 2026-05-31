from __future__ import annotations

import socket

import pytest

from viewspec.cli import main as cli_main
from viewspec.local_tools import (
    MCP_RESULT_SCHEMA_VERSION,
    check_artifact_tool,
    compile_html_file_tool,
    diff_html_files_tool,
    init_design_tool,
    lift_html_file_tool,
)
from viewspec.mcp_server import MCP_INSTALL_HINT, MissingMCPDependency


def assert_tool_schema(payload: dict) -> None:
    assert payload["schema_version"] == MCP_RESULT_SCHEMA_VERSION
    assert isinstance(payload["ok"], bool)
    assert isinstance(payload["summary"], str)
    assert isinstance(payload["diagnostics"], list)
    assert isinstance(payload["external_refs"], list)
    assert isinstance(payload["paths"], dict)
    assert isinstance(payload["next_actions"], list)
    assert isinstance(payload["errors"], list)


def test_mcp_missing_dependency_cli_hint(monkeypatch, capsys):
    import viewspec.cli as cli

    def missing_mcp(**kwargs):
        raise MissingMCPDependency(MCP_INSTALL_HINT)

    monkeypatch.setattr(cli, "run_mcp_server", missing_mcp)

    assert cli_main(["mcp"]) == 2
    assert MCP_INSTALL_HINT in capsys.readouterr().err


def test_doctor_agents_reports_missing_optional_mcp(capsys):
    exit_code = cli_main(["doctor", "--agents"])
    output = capsys.readouterr().out

    assert '"mcp_dependency"' in output
    assert "viewspec[agents]" in output
    if exit_code == 2:
        assert '"mcp_dependency": false' in output.lower()


def test_local_tool_wrappers_compile_check_lift_and_diff(tmp_path):
    html = tmp_path / "report.html"
    newer = tmp_path / "report-new.html"
    html.write_text("<h1>Report</h1><p>$1</p>", encoding="utf-8")
    newer.write_text("<h1>Report Updated</h1><p>$2</p>", encoding="utf-8")

    design = init_design_tool("DESIGN.md", cwd=tmp_path)
    assert_tool_schema(design)
    assert design["ok"] is True

    compiled = compile_html_file_tool("report.html", "dist", design_path="DESIGN.md", include_lift=True, cwd=tmp_path)
    assert_tool_schema(compiled)
    assert compiled["ok"] is True
    assert (tmp_path / "dist/index.html").exists()
    assert (tmp_path / "dist/lift.json").exists()

    checked = check_artifact_tool("dist", cwd=tmp_path)
    assert_tool_schema(checked)
    assert checked["ok"] is True

    lifted = lift_html_file_tool("report.html", "lift.json", cwd=tmp_path)
    assert_tool_schema(lifted)
    assert lifted["ok"] is True
    assert (tmp_path / "lift.json").exists()

    diffed = diff_html_files_tool("report.html", "report-new.html", cwd=tmp_path)
    assert_tool_schema(diffed)
    assert diffed["ok"] is True
    assert diffed["diff"]["basis"] == "lift_v1"


def test_mcp_path_sandbox_rejects_urls_and_outside_paths(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.html"
    outside.write_text("<h1>Outside</h1>", encoding="utf-8")

    outside_result = compile_html_file_tool(outside, "dist", cwd=root)
    assert_tool_schema(outside_result)
    assert outside_result["ok"] is False
    assert outside_result["errors"][0]["code"] == "PATH_OUTSIDE_CWD"

    url_result = lift_html_file_tool("file:///tmp/report.html", cwd=root)
    assert_tool_schema(url_result)
    assert url_result["ok"] is False
    assert url_result["errors"][0]["code"] == "INVALID_PATH"

    absolute = root.anchor + "viewspec-outside-test.html"
    absolute_result = lift_html_file_tool(absolute, cwd=root)
    assert_tool_schema(absolute_result)
    assert absolute_result["ok"] is False
    assert absolute_result["errors"][0]["code"] in {"PATH_OUTSIDE_CWD", "INVALID_PATH"}


def test_mcp_allow_outside_cwd_is_reflected_in_metadata(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.html"
    outside.write_text("<h1>Outside</h1>", encoding="utf-8")

    result = lift_html_file_tool(outside, cwd=root, allow_outside_cwd=True)

    assert_tool_schema(result)
    assert result["ok"] is True
    assert result["metadata"]["allow_outside_cwd"] is True


def test_mcp_symlink_escape_is_rejected_when_supported(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.html"
    outside.write_text("<h1>Outside</h1>", encoding="utf-8")
    link = root / "link.html"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not available in this environment")

    result = lift_html_file_tool("link.html", cwd=root)

    assert_tool_schema(result)
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "PATH_OUTSIDE_CWD"


def test_mcp_tools_make_no_socket_calls(tmp_path, monkeypatch):
    def fail_socket(*args, **kwargs):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket, "create_connection", fail_socket)
    monkeypatch.setattr(socket.socket, "connect", fail_socket)

    html = tmp_path / "report.html"
    html.write_text("<h1>Report</h1>", encoding="utf-8")

    assert compile_html_file_tool("report.html", "dist", cwd=tmp_path)["ok"] is True
    assert check_artifact_tool("dist", cwd=tmp_path)["ok"] is True
    assert lift_html_file_tool("report.html", cwd=tmp_path)["ok"] is True
