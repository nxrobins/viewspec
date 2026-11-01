"""An interactive incident console that renders inside an MCP host.

    pip install "viewspec[agents]"
    python examples/mcp_app_incident_console.py

The server speaks MCP over stdio. Add it to a host that supports MCP Apps, then ask for the incident
queue: `show_incidents` opens the view, and its Refresh button calls `refresh_incidents`.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from viewspec import ViewSpecBuilder, compile_mcp_app
from viewspec.mcp_apps import register_mcp_app

builder = ViewSpecBuilder("incident_queue", root_attrs={"title": "Incident queue"})
table = builder.add_table("incidents", region="main", group_id="rows")
table.add_row(id="inc402", label="INC-402", value="Loading")
table.add_row(id="inc403", label="INC-403", value="Loading")
note = builder.add_text_input("note", label="Note", region="main")
builder.add_collection_action(
    "refresh", kind="filter", label="Refresh", collection_id="incidents", payload_bindings=[note]
)
app = compile_mcp_app(
    builder.build_bundle(),
    uri="ui://ops/incident-queue",
    profile="aesthetic.calm_ops",
    action_tools={"refresh": "refresh_incidents"},
)

server = FastMCP("incident-console")
tool = register_mcp_app(server, app)


@tool()
def show_incidents() -> dict[str, Any]:
    """Open the incident queue."""
    return {"viewspecBindings": {"inc402_value": "Database replica lag (14s)", "inc403_value": "Queue depth rising"}}


@tool(visibility=["app"])
def refresh_incidents(note_value: str = "") -> dict[str, Any]:
    """Refresh the queue. Only the view can call this tool."""
    return {"viewspecBindings": {"inc402_value": f"Replica lag cleared ({note_value or 'no note'})"}}


if __name__ == "__main__":
    server.run()
