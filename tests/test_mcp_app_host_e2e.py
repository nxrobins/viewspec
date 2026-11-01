"""Opt-in proof that a compiled MCP App works against the ext-apps SDK's own host.

tests/mcp-app-host drives the real ``AppBridge`` in Chromium: the handshake, tool results, every
action route, failures, sizing, spoofed messages and the spec's default CSP. Set
``VIEWSPEC_RUN_MCP_HOST_E2E=1`` after ``npm ci --ignore-scripts`` in that directory. Its SDK version is
bumped by hand, because that version is what this test proves the bridge against.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from viewspec import ViewSpecBuilder, compile_mcp_app

E2E_OPT_IN = "VIEWSPEC_RUN_MCP_HOST_E2E"
HOST_DIR = Path(__file__).parent / "mcp-app-host"


def _console_view() -> str:
    builder = ViewSpecBuilder("console", root_attrs={"title": "Incident console"})
    table = builder.add_table("incidents", region="main", group_id="rows")
    table.add_row(id="inc1", label="INC-402", value="Database replica lag (14s)")
    table.add_row(id="inc2", label="INC-403", value="Queue depth rising")
    query = builder.add_text_input("query", label="Filter", region="main")
    collection = {"collection_id": "incidents", "payload_bindings": [query]}
    builder.add_collection_action("refresh", kind="filter", label="Refresh", **collection)
    builder.add_collection_action("boom", kind="sort", label="Sort", **collection)
    builder.add_collection_action("explode", kind="paginate", label="Next", **collection)
    builder.add_action("approve", "submit", "Approve", payload_bindings=[query])
    builder.add_action("pick", "select", "Pick", payload_bindings=[query])
    return compile_mcp_app(
        builder.build_bundle(),
        uri="ui://ops/console",
        profile="aesthetic.calm_ops",
        action_tools={"refresh": "refresh_incidents", "boom": "failing_tool", "explode": "rpc_error_tool"},
    ).html


def _view_without_actions() -> str:
    builder = ViewSpecBuilder("kpi", root_attrs={"title": "KPIs"})
    builder.add_dashboard("cards", region="main", group_id="group").add_card(id="c1", label="Open", value="12")
    return compile_mcp_app(builder.build_bundle(), uri="ui://ops/kpi").html


@pytest.mark.e2e
@pytest.mark.skipif(os.environ.get(E2E_OPT_IN) != "1", reason=f"set {E2E_OPT_IN}=1 to run the real SDK host")
def test_compiled_app_works_against_the_sdk_host(tmp_path: Path):
    (tmp_path / "view.html").write_text(_console_view(), encoding="utf-8")
    (tmp_path / "view_noactions.html").write_text(_view_without_actions(), encoding="utf-8")

    result = subprocess.run(
        ["node", "run.mjs", str(tmp_path)],
        cwd=HOST_DIR,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
