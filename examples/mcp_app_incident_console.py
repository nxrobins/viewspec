"""An interactive incident console that renders inside an MCP host.

    pip install "viewspec[agents]"
    python examples/mcp_app_incident_console.py

The server speaks MCP over stdio. Add it to a host that supports MCP Apps, then ask for the incident
queue: `show_incidents` opens the view, and its Refresh button calls `refresh_incidents`. The queue is
as long as its data, so each result can show a different number of rows.

The document itself holds no incidents. A host can reopen a view without running `show_incidents`
again, and data compiled in would look current when it is not, so it offers Refresh instead.
"""

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from viewspec import ViewSpecBuilder, compile_mcp_app
from viewspec.mcp_apps import register_mcp_app
from viewspec.types import IntentBundle

INCIDENTS = [
    ("inc402", "INC-402", "Database replica lag (14s)"),
    ("inc403", "INC-403", "Queue depth rising"),
    ("inc404", "INC-404", "Disk pressure on worker-7"),
]


def queue(incidents: list[tuple[str, str, str]], *, empty: str = "No incidents match") -> IntentBundle:
    """The whole view for `incidents`, compiled again for every result."""
    builder = ViewSpecBuilder("incident_queue", root_attrs={"title": "Incident queue"})
    table = builder.add_table("incidents", region="main", group_id="rows")
    for incident_id, label, summary in incidents:
        table.add_row(id=incident_id, label=label, value=summary)
    if not incidents:
        builder.add_empty_state("no_incidents", title=empty, region="main")
    filter_value = builder.add_text_input("filter", label="Filter", region="main")
    builder.add_collection_action(
        "refresh", kind="filter", label="Refresh", collection_id="incidents", payload_bindings=[filter_value]
    )
    return builder.build_bundle()


app = compile_mcp_app(
    queue([], empty="Refresh to load the queue"),
    uri="ui://ops/incident-queue",
    profile="aesthetic.calm_ops",
    action_tools={"refresh": "refresh_incidents"},
)

server = FastMCP("incident-console")
tool = register_mcp_app(server, app)


@tool()
def show_incidents() -> CallToolResult:
    """Open the incident queue."""
    return app.render(queue(INCIDENTS), text=f"{len(INCIDENTS)} open incidents.")


@tool(visibility=["app"])
def refresh_incidents(filter_value: str = "") -> CallToolResult:
    """Filter the queue. Only the view can call this tool."""
    matches = [incident for incident in INCIDENTS if filter_value.lower() in incident[2].lower()]
    return app.render(queue(matches), text=f"{len(matches)} of {len(INCIDENTS)} incidents match.")


if __name__ == "__main__":
    server.run()
