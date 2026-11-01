"""Opt-in proof that a compiled MCP App works against the ext-apps SDK's own host.

tests/mcp-app-host drives the real ``AppBridge`` in Chromium: the handshake, tool results that change
the row count, every action route, failures, sizing, spoofed messages, injected markup and the
spec's default CSP. Set ``VIEWSPEC_RUN_MCP_HOST_E2E=1`` after ``npm ci --ignore-scripts`` in that
directory. Its SDK version is bumped by hand, because that version is what this test proves the
bridge against.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from viewspec import ViewSpecBuilder, compile_mcp_app
from viewspec.mcp_apps import MCPAppArtifact
from viewspec.types import IntentBundle

E2E_OPT_IN = "VIEWSPEC_RUN_MCP_HOST_E2E"
HOST_DIR = Path(__file__).parent / "mcp-app-host"


INCIDENTS = [
    ("INC-402", "Database replica lag (14s)"),
    ("INC-403", "Queue depth rising"),
    ("INC-404", "Disk pressure on worker-7"),
    ("INC-405", "Certificate expires in 3 days"),
    ("INC-406", "Elevated 5xx rate"),
]
HOSTILE = "<img src=x onerror=\"window.pwned='compiled'\">"


def _console_bundle(incidents: list[tuple[str, str]], *, actions: bool = True) -> IntentBundle:
    builder = ViewSpecBuilder("console", root_attrs={"title": "Incident console"})
    table = builder.add_table("incidents", region="main", group_id="rows")
    for number, (label, value) in enumerate(incidents, start=1):
        table.add_row(id=f"inc{number}", label=label, value=value)
    query = builder.add_text_input("query", label="Filter", region="main")
    if actions:
        collection = {"collection_id": "incidents", "payload_bindings": [query]}
        builder.add_collection_action("refresh", kind="filter", label="Refresh", **collection)
        builder.add_collection_action("boom", kind="sort", label="Sort", **collection)
        builder.add_collection_action("explode", kind="paginate", label="Next", **collection)
        builder.add_action("approve", "submit", "Approve", payload_bindings=[query])
        builder.add_action("pick", "select", "Pick", payload_bindings=[query])
    return builder.build_bundle()


def _console_app() -> MCPAppArtifact:
    return compile_mcp_app(
        _console_bundle(INCIDENTS[:2]),
        uri="ui://ops/console",
        profile="aesthetic.calm_ops",
        action_tools={"refresh": "refresh_incidents", "boom": "failing_tool", "explode": "rpc_error_tool"},
    )


def _kpi_bundle(*, action: bool = False) -> IntentBundle:
    builder = ViewSpecBuilder("kpi", root_attrs={"title": "KPIs"})
    builder.add_dashboard("cards", region="main", group_id="group").add_card(id="c1", label="Open", value="12")
    if action:
        builder.add_action("pick", "select", "Pick")
    return builder.build_bundle()


def _renders(console: MCPAppArtifact, kpi: MCPAppArtifact) -> dict[str, Any]:
    """Real `render()` results, as a host would receive them, for run.mjs to send through the bridge."""

    def result(app: MCPAppArtifact, bundle: IntentBundle) -> dict[str, Any]:
        return app.render(bundle).model_dump(mode="json", by_alias=True, exclude_none=True)

    return {
        "five": result(console, _console_bundle(INCIDENTS)),
        "one": result(console, _console_bundle(INCIDENTS[:1])),
        "no_actions": result(console, _console_bundle(INCIDENTS[:1], actions=False)),
        "hostile": result(console, _console_bundle([(HOSTILE, HOSTILE)])),
        "first_action": result(kpi, _kpi_bundle(action=True)),
    }


@pytest.mark.e2e
@pytest.mark.skipif(os.environ.get(E2E_OPT_IN) != "1", reason=f"set {E2E_OPT_IN}=1 to run the real SDK host")
def test_compiled_app_works_against_the_sdk_host(tmp_path: Path):
    console = _console_app()
    kpi = compile_mcp_app(_kpi_bundle(), uri="ui://ops/kpi")
    (tmp_path / "view.html").write_text(console.html, encoding="utf-8")
    (tmp_path / "view_noactions.html").write_text(kpi.html, encoding="utf-8")
    (tmp_path / "renders.json").write_text(json.dumps(_renders(console, kpi)), encoding="utf-8")

    result = subprocess.run(
        ["node", "run.mjs", str(tmp_path)],
        cwd=HOST_DIR,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
