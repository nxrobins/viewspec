"""The MCP Apps target: the document, its metadata, the check gate, and FastMCP registration."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import re
import runpy
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from viewspec import ERROR_CODES, ViewSpecBuilder, ViewSpecError, check, compile_mcp_app
from viewspec import cli as viewspec_cli
from viewspec.compiler_errors import LocalToolContractError
from viewspec.emitters.html_tailwind import (
    ACTION_EVENT_SCRIPT,
    MCP_APP_BRIDGE_SCRIPT,
    HtmlTailwindEmitter,
    _render_node,
)
from viewspec.intent_tools import compile_intent_bundle_file_tool
from viewspec.local_tools import MCP_APP_MAX_BYTES
from viewspec.mcp_apps import MCP_APP_MIME_TYPE, MCPAppArtifact, register_mcp_app
from viewspec.types import IntentBundle

ROOT = Path(__file__).resolve().parents[1]
URI = "ui://ops/incident-queue"
# Changing the bridge changes what hosts receive: run tests/test_mcp_app_host_e2e.py against the
# ext-apps SDK before updating this digest.
BRIDGE_SHA256 = "b17dd21308b859954bcfca5f2927a94e1d4260463901333b345debec2de172e6"


def _builder(rows: int = 2, *, actions: bool = True) -> ViewSpecBuilder:
    builder = ViewSpecBuilder("incident_queue", root_attrs={"title": "Incident queue"})
    table = builder.add_table("incidents", region="main", group_id="rows")
    for index in range(rows):
        table.add_row(id=f"inc{index}", label=f"INC-{index}", value=f"Replica lag ({index}s)")
    query = builder.add_text_input("query", label="Filter", region="main")
    if actions:
        builder.add_collection_action(
            "refresh", kind="filter", label="Refresh", collection_id="incidents", payload_bindings=[query]
        )
    return builder


def _write_intent(path: Path, builder: ViewSpecBuilder | None = None) -> Path:
    path.write_text(json.dumps((builder or _builder()).build_bundle().to_json()), encoding="utf-8")
    return path


def _compile_dir(tmp_path: Path, *extra: str) -> Path:
    source = _write_intent(tmp_path / "viewspec.intent.json")
    out = tmp_path / "dist"
    assert viewspec_cli.main(["compile", str(source), "--target", "mcp-app", "--out", str(out), *extra]) == 0
    return out


def _codes(report: dict[str, Any]) -> set[str]:
    return {issue["code"] for issue in report["issues"]}


def test_artifact_matches_the_publishing_contract():
    app = compile_mcp_app(_builder().build_bundle(), uri=URI)

    assert isinstance(app, MCPAppArtifact)
    assert app.uri == URI
    assert app.mime_type == MCP_APP_MIME_TYPE == "text/html;profile=mcp-app"
    assert app.html.startswith("<!DOCTYPE html>")
    assert app.csp_meta == {"ui": {"csp": {"connectDomains": [], "resourceDomains": []}}}
    assert app.tool_meta == {"ui": {"resourceUri": URI}, "ui/resourceUri": URI}
    assert app.size_bytes == len(app.html.encode("utf-8")) < MCP_APP_MAX_BYTES


def test_metadata_is_a_fresh_copy_each_time():
    app = compile_mcp_app(_builder().build_bundle(), uri=URI)
    app.tool_meta["ui"]["visibility"] = ["app"]
    assert "visibility" not in app.tool_meta["ui"]


def test_document_loads_nothing_from_any_other_domain():
    html = compile_mcp_app(_builder().build_bundle(), uri=URI).html

    assert not re.search(r"https?://", html)
    assert "<script src" not in html
    assert "url(" not in html


def test_document_carries_the_pinned_scripts_in_order():
    html = compile_mcp_app(_builder().build_bundle(), uri=URI).html

    assert html.index(ACTION_EVENT_SCRIPT) < html.index(MCP_APP_BRIDGE_SCRIPT)
    assert hashlib.sha256(MCP_APP_BRIDGE_SCRIPT.encode("utf-8")).hexdigest() == BRIDGE_SHA256


def test_view_without_actions_carries_the_action_runtime_for_a_later_render():
    builder = ViewSpecBuilder("kpi", root_attrs={"title": "KPIs"})
    builder.add_dashboard("cards", region="main", group_id="group").add_card(label="Open", value="12")

    html = compile_mcp_app(builder.build_bundle(), uri=URI).html

    assert MCP_APP_BRIDGE_SCRIPT in html
    assert ACTION_EVENT_SCRIPT in html


def test_action_tools_reach_the_document_as_inert_data():
    html = compile_mcp_app(_builder().build_bundle(), uri=URI, action_tools={"refresh": "refresh_incidents"}).html

    assert '<meta name="viewspec-mcp-tools" content="{&quot;refresh&quot;: &quot;refresh_incidents&quot;}">' in html


def test_plain_html_tailwind_output_is_untouched(tmp_path: Path):
    from viewspec import compile as compile_bundle

    paths = HtmlTailwindEmitter().emit(compile_bundle(_builder().build_bundle()), tmp_path)
    html = Path(paths["html"]).read_text(encoding="utf-8")

    assert "ui/initialize" not in html
    assert "viewspec-mcp-tools" not in html
    assert "min-height: 0" not in html


def test_profile_replaces_the_bundle_profile_and_leaves_the_bundle_alone():
    builder = _builder()
    builder.set_aesthetic_profile("aesthetic.neon_cyber")
    bundle = builder.build_bundle()

    html = compile_mcp_app(bundle, uri=URI, profile="aesthetic.calm_ops").html

    assert 'data-aesthetic-profile="aesthetic.calm_ops"' in html
    assert "aesthetic.neon_cyber" not in html
    assert [style.token for style in bundle.view_spec.styles if style.id == "aesthetic_profile"] == [
        "aesthetic.neon_cyber"
    ]


def test_unknown_profile_is_refused():
    with pytest.raises(LocalToolContractError) as caught:
        compile_mcp_app(_builder().build_bundle(), uri=URI, profile="aesthetic.nope")
    assert caught.value.code == "MCP_APP_INVALID_PROFILE"


@pytest.mark.parametrize("uri", ["https://ops/view", "ui://", "ui:// spaced", "UI://ops/view", "ui://ops/a b", ""])
def test_bad_uri_is_refused(uri: str):
    with pytest.raises(LocalToolContractError) as caught:
        compile_mcp_app(_builder().build_bundle(), uri=uri)
    assert caught.value.code == "MCP_APP_INVALID_URI"


def test_action_tools_must_name_an_action_in_the_view():
    with pytest.raises(LocalToolContractError) as caught:
        compile_mcp_app(_builder().build_bundle(), uri=URI, action_tools={"refreshh": "tool"})
    assert caught.value.code == "MCP_APP_UNKNOWN_ACTION"
    assert "refresh" in caught.value.message


@pytest.mark.parametrize("tool", ["", "has space", 'quote"', "x" * 129])
def test_action_tools_must_be_valid_tool_names(tool: str):
    with pytest.raises(LocalToolContractError) as caught:
        compile_mcp_app(_builder().build_bundle(), uri=URI, action_tools={"refresh": tool})
    assert caught.value.code == "MCP_APP_INVALID_TOOL"


def test_document_over_the_ceiling_is_refused():
    with pytest.raises(LocalToolContractError) as caught:
        compile_mcp_app(_builder(rows=120).build_bundle(), uri=URI)
    assert caught.value.code == "MCP_APP_TOO_LARGE"
    assert str(MCP_APP_MAX_BYTES) in caught.value.message


def test_refusals_are_viewspec_errors_with_registered_codes():
    with pytest.raises(ViewSpecError) as caught:
        compile_mcp_app(_builder().build_bundle(), uri="nope")
    assert caught.value.code in ERROR_CODES


def test_cli_writes_a_checked_app_with_its_metadata(tmp_path: Path):
    out = _compile_dir(tmp_path, "--uri", URI, "--action-tool", "refresh=refresh_incidents")

    assert sorted(path.name for path in out.iterdir()) == [
        "diagnostics.json",
        "index.html",
        "mcp_app.json",
        "provenance_manifest.json",
    ]
    metadata = json.loads((out / "mcp_app.json").read_text(encoding="utf-8"))
    assert metadata["uri"] == URI
    assert metadata["mime_type"] == MCP_APP_MIME_TYPE
    assert metadata["size_bytes"] == (out / "index.html").stat().st_size
    assert json.loads((out / "provenance_manifest.json").read_text(encoding="utf-8"))["target"] == "mcp-app"
    assert check(out)["ok"]


def test_cli_defaults_the_uri_to_the_view_id(tmp_path: Path):
    out = _compile_dir(tmp_path)
    assert json.loads((out / "mcp_app.json").read_text(encoding="utf-8"))["uri"] == "ui://viewspec/incident_queue"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--target", "mcp-app", "--action-tool", "no_separator"], "ACTION=TOOL"),
        (["--uri", URI], "apply to --target mcp-app"),
        (["--target", "react-tsx", "--action-tool", "refresh=tool"], "apply to --target mcp-app"),
    ],
)
def test_cli_refuses_misused_flags(tmp_path: Path, capsys: pytest.CaptureFixture[str], args: list[str], message: str):
    source = _write_intent(tmp_path / "viewspec.intent.json")

    assert viewspec_cli.main(["compile", str(source), "--out", str(tmp_path / "dist"), *args]) != 0
    assert message in capsys.readouterr().err


def test_cli_keeps_mcp_app_to_intent_bundles(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    source = tmp_path / "viewspec.app.json"
    assert viewspec_cli.main(["init", "app", "--out", str(source)]) == 0
    capsys.readouterr()

    assert viewspec_cli.main(["compile", str(source), "--target", "mcp-app", "--out", str(tmp_path / "dist")]) != 0
    assert "IntentBundle" in capsys.readouterr().err


def test_compile_tool_builds_the_target(tmp_path: Path):
    source = _write_intent(tmp_path / "viewspec.intent.json")

    report = compile_intent_bundle_file_tool(
        source,
        tmp_path / "dist",
        target="mcp-app",
        uri=URI,
        action_tools={"refresh": "refresh_incidents"},
        cwd=tmp_path,
    )

    assert report["ok"], report
    assert json.loads(Path(report["paths"]["mcp_app"]).read_text(encoding="utf-8"))["uri"] == URI


def test_compile_tool_reports_a_uri_on_another_target(tmp_path: Path):
    source = _write_intent(tmp_path / "viewspec.intent.json")

    report = compile_intent_bundle_file_tool(source, tmp_path / "dist", uri=URI, cwd=tmp_path)

    assert not report["ok"]
    assert "apply to target='mcp-app'" in json.dumps(report)


def test_check_rejects_a_changed_bridge(tmp_path: Path):
    out = _compile_dir(tmp_path)
    html = out / "index.html"
    html.write_text(
        html.read_text(encoding="utf-8").replace("ui/initialize", "ui/evil", 1),
        encoding="utf-8",
    )

    assert "MCP_APP_BRIDGE_MISMATCH" in _codes(check(out))


def test_check_rejects_a_removed_bridge(tmp_path: Path):
    out = _compile_dir(tmp_path)
    html = out / "index.html"
    html.write_text(html.read_text(encoding="utf-8").replace(MCP_APP_BRIDGE_SCRIPT, ""), encoding="utf-8")

    assert "MCP_APP_BRIDGE_MISMATCH" in _codes(check(out))


def test_check_rejects_the_bridge_on_a_plain_artifact(tmp_path: Path):
    source = _write_intent(tmp_path / "viewspec.intent.json")
    out = tmp_path / "dist"
    assert viewspec_cli.main(["compile", str(source), "--out", str(out)]) == 0
    html = out / "index.html"
    html.write_text(
        html.read_text(encoding="utf-8").replace("</body>", f"{MCP_APP_BRIDGE_SCRIPT}\n</body>"),
        encoding="utf-8",
    )

    report = check(out)

    assert not report["ok"]
    assert "index.html contains an unknown inline script" in report["errors"]


def test_check_rejects_an_unknown_manifest_target(tmp_path: Path):
    out = _compile_dir(tmp_path)
    manifest_path = out / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["target"] = "elsewhere"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = check(out)

    assert not report["ok"]
    assert any("manifest target must be mcp-app" in error for error in report["errors"])


def test_check_measures_an_oversized_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    out = _compile_dir(tmp_path)
    monkeypatch.setattr("viewspec.local_tools_validators.MCP_APP_MAX_BYTES", 100)

    assert "MCP_APP_TOO_LARGE" in _codes(check(out))


class _Elements(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: set[str] = set()
        self.attributes: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.add(tag)
        self.attributes.update(name for name, _ in attrs)


def _bridge_allow_list() -> tuple[set[str], re.Pattern[str]]:
    tags = re.search(r"rootTags = new Set\('([^']+)'", MCP_APP_BRIDGE_SCRIPT)
    attributes = re.search(r"rootAttributes = /(.+)/;", MCP_APP_BRIDGE_SCRIPT)
    assert tags and attributes
    return set(tags[1].split()), re.compile(attributes[1])


def test_bridge_allows_everything_the_emitter_source_can_write():
    source = inspect.getsource(_render_node)
    allowed_tags, allowed_attributes = _bridge_allow_list()

    tags = {
        *re.findall(r'\btag = "(\w+)"', source),
        *re.findall(r'\btag = .* else "(\w+)"', source),
        *re.findall(r"<(\w+)[ >]", source),
    }
    attributes = {*re.findall(r"""['"]([a-z][a-z-]*)=""", source), *re.findall(r'"((?:aria|data)-[a-z-]+)"', source)}

    assert {"main", "tbody", "tr", "hr", "button", "input"} <= tags
    assert {"id", "class", "role", "data-ir-id", "data-resource-id", "aria-label"} <= attributes
    assert tags <= allowed_tags
    assert [name for name in attributes if not allowed_attributes.fullmatch(name)] == []


@pytest.mark.parametrize(
    "fixture", sorted((ROOT / "conformance" / "verification" / "fixtures").glob("*.json")), ids=lambda path: path.stem
)
def test_bridge_allows_the_root_of_every_conformance_fixture(fixture: Path):
    bundle = IntentBundle.from_json(json.loads(fixture.read_text(encoding="utf-8")))
    elements = _Elements()
    elements.feed(compile_mcp_app(bundle, uri=URI).root)
    allowed_tags, allowed_attributes = _bridge_allow_list()

    assert elements.tags <= allowed_tags
    assert [name for name in elements.attributes if not allowed_attributes.fullmatch(name)] == []


@pytest.mark.parametrize("rows", [0, 1, 2, 5])
def test_root_is_the_whole_compiled_view_and_nothing_else(rows: int):
    app = compile_mcp_app(_builder(rows=rows).build_bundle(), uri=URI)

    assert app.root.startswith('<main id="') and 'class="vs-root' in app.root.partition(">")[0]
    assert app.root.endswith("</main>")
    assert app.html.count(app.root) == 1
    assert "<script" not in app.root
    assert app.root.count("<tr ") == rows


@pytest.mark.parametrize("rows", [0, 1, 5])
def test_render_changes_the_row_count_through_the_compiler(rows: int):
    pytest.importorskip("mcp.types")
    app = compile_mcp_app(_builder().build_bundle(), uri=URI, action_tools={"refresh": "refresh_incidents"})

    result = app.render(_builder(rows=rows).build_bundle())

    assert result.structuredContent == {
        "viewspecRoot": compile_mcp_app(_builder(rows=rows).build_bundle(), uri=URI).root
    }
    assert result.structuredContent["viewspecRoot"].count("<tr ") == rows
    assert not result.isError


def test_render_keeps_the_root_out_of_what_the_model_reads():
    pytest.importorskip("mcp.types")
    app = compile_mcp_app(_builder().build_bundle(), uri=URI)

    named = app.render(_builder().build_bundle(), text="2 open incidents.")
    default = app.render(_builder().build_bundle())

    assert [block.text for block in named.content] == ["2 open incidents."]
    assert [block.text for block in default.content] == ["Rendered the incident_queue view."]


def test_render_keeps_the_apps_profile():
    pytest.importorskip("mcp.types")
    app = compile_mcp_app(_builder().build_bundle(), uri=URI, profile="aesthetic.calm_ops")

    root = app.render(_builder(rows=5).build_bundle()).structuredContent["viewspecRoot"]

    assert 'data-aesthetic-profile="aesthetic.calm_ops"' in root


def test_render_can_drop_an_action_the_app_maps():
    pytest.importorskip("mcp.types")
    app = compile_mcp_app(_builder().build_bundle(), uri=URI, action_tools={"refresh": "refresh_incidents"})

    root = app.render(_builder(actions=False).build_bundle()).structuredContent["viewspecRoot"]

    assert "data-action-id" not in root


def test_render_writes_tool_data_as_text():
    pytest.importorskip("mcp.types")
    hostile = "<img src=x onerror=\"document.title='pwned'\">"
    builder = ViewSpecBuilder("incident_queue", root_attrs={"title": "Incident queue"})
    builder.add_table("incidents", region="main", group_id="rows").add_row(id="inc0", label=hostile, value=hostile)
    app = compile_mcp_app(_builder().build_bundle(), uri=URI)

    root = app.render(builder.build_bundle()).structuredContent["viewspecRoot"]

    elements = _Elements()
    elements.feed(root)
    assert "img" not in elements.tags
    assert "onerror" not in elements.attributes
    assert "&lt;img" in root


def test_render_is_held_to_the_document_ceiling():
    pytest.importorskip("mcp.types")
    app = compile_mcp_app(_builder().build_bundle(), uri=URI)

    with pytest.raises(LocalToolContractError) as caught:
        app.render(_builder(rows=120).build_bundle())

    assert caught.value.code == "MCP_APP_TOO_LARGE"


def _in_memory_client(server: Any) -> Any:
    memory = pytest.importorskip("mcp.shared.memory")
    return memory.create_connected_server_and_client_session(server._mcp_server)


def test_register_mcp_app_publishes_the_resource_and_links_its_tools():
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    app = compile_mcp_app(_builder().build_bundle(), uri=URI, action_tools={"refresh": "refresh_incidents"})
    server = fastmcp.FastMCP("test")
    tool = register_mcp_app(server, app)

    @tool()
    def show_incidents() -> dict[str, Any]:
        return {"viewspecBindings": {"inc0_value": "fresh"}}

    @tool(visibility=["app"])
    def refresh_incidents(query_value: str = "") -> dict[str, Any]:
        return {"viewspecBindings": {"inc0_value": query_value}}

    async def run() -> tuple[Any, Any, Any]:
        async with _in_memory_client(server) as session:
            read = await session.read_resource(URI)
            tools = await session.list_tools()
            called = await session.call_tool("refresh_incidents", {"query_value": "typed"})
            return read, tools, called

    read, tools, called = asyncio.run(run())

    content = read.contents[0]
    assert content.mimeType == MCP_APP_MIME_TYPE
    assert content.text == app.html
    assert content.meta == app.csp_meta
    meta_by_tool = {item.name: item.meta for item in tools.tools}
    assert meta_by_tool["show_incidents"] == app.tool_meta
    assert meta_by_tool["refresh_incidents"]["ui"] == {"resourceUri": URI, "visibility": ["app"]}
    assert called.structuredContent == {"viewspecBindings": {"inc0_value": "typed"}}


def test_render_reaches_the_host_through_fastmcp_with_the_root_kept_from_the_model():
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    app = compile_mcp_app(_builder().build_bundle(), uri=URI, action_tools={"refresh": "refresh_incidents"})
    server = fastmcp.FastMCP("test")
    tool = register_mcp_app(server, app)

    @tool()
    def show_incidents() -> Any:
        return app.render(_builder(rows=5).build_bundle(), text="5 open incidents.")

    async def run() -> Any:
        async with _in_memory_client(server) as session:
            return await session.call_tool("show_incidents", {})

    called = asyncio.run(run())

    assert called.structuredContent["viewspecRoot"].count("<tr ") == 5
    assert [block.text for block in called.content] == ["5 open incidents."]


def test_a_render_leaves_the_published_document_alone():
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    app = compile_mcp_app(_builder().build_bundle(), uri=URI)
    published = app.html
    server = fastmcp.FastMCP("test")
    tool = register_mcp_app(server, app)

    @tool()
    def show_incidents() -> Any:
        return app.render(_builder(rows=5).build_bundle())

    async def run() -> Any:
        async with _in_memory_client(server) as session:
            await session.call_tool("show_incidents", {})
            return await session.read_resource(URI)

    assert asyncio.run(run()).contents[0].text == published == app.html


def test_every_server_process_serves_the_same_view():
    """A host can start a configured server twice and reach either process."""
    pytest.importorskip("mcp.types")
    script = (
        "import hashlib, runpy\n"
        "example = runpy.run_path('examples/mcp_app_incident_console.py', run_name='example')\n"
        "root = example['app'].render(example['queue'](example['INCIDENTS'])).structuredContent['viewspecRoot']\n"
        "print(hashlib.sha256((example['app'].html + root).encode()).hexdigest())\n"
    )

    def process(seed: str) -> str:
        env = {**os.environ, "PYTHONHASHSEED": seed}
        return subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True, check=True
        ).stdout

    assert process("1") == process("2")


def test_flagship_example_document_makes_sense_with_no_result():
    example = runpy.run_path(str(ROOT / "examples" / "mcp_app_incident_console.py"), run_name="example")
    app = example["app"]

    assert app.root.count("<tr ") == 0
    assert "Refresh to load the queue" in app.root
    assert 'data-action-id="refresh"' in app.root
    assert "refresh_incidents" in app.html


def test_flagship_example_returns_a_queue_as_long_as_its_data():
    fastmcp = pytest.importorskip("mcp.server.fastmcp")
    example = runpy.run_path(str(ROOT / "examples" / "mcp_app_incident_console.py"), run_name="example")
    app, server, incidents = example["app"], example["server"], example["INCIDENTS"]
    assert isinstance(server, fastmcp.FastMCP)

    async def run() -> list[Any]:
        async with _in_memory_client(server) as session:
            return [
                await session.call_tool("show_incidents", {}),
                await session.call_tool("refresh_incidents", {"filter_value": "replica"}),
                await session.call_tool("refresh_incidents", {"filter_value": "no such incident"}),
            ]

    opened, filtered, empty = [result.structuredContent["viewspecRoot"] for result in asyncio.run(run())]

    assert opened.count("<tr ") == len(incidents) > filtered.count("<tr ") == 1
    assert empty.count("<tr ") == 0
    assert "No incidents match" in empty
    assert 'data-binding-id="filter_value"' in app.html
    assert all('data-action-id="refresh"' in root for root in (app.html, opened, filtered, empty))
