# MCP Apps

The `mcp-app` target turns an `IntentBundle` into an [MCP Apps](https://github.com/modelcontextprotocol/ext-apps)
`ui://` resource: one self-contained HTML document that a host renders in a sandboxed iframe, so a
Python MCP tool can show a table, dashboard, or form instead of raw JSON. You need no JavaScript
build, and the document loads nothing from any other domain.

## Compile

```python
from viewspec import ViewSpecBuilder, compile_mcp_app

builder = ViewSpecBuilder("incident_queue", root_attrs={"title": "Incident queue"})
table = builder.add_table("incidents", region="main", group_id="rows")
table.add_row(id="inc402", label="INC-402", value="Database replica lag (14s)")
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
```

`compile_mcp_app()` returns an `MCPAppArtifact` once `check` has passed the document, and raises a
`ViewSpecError` otherwise:

| Field | Value |
|---|---|
| `uri` | The `ui://` resource URI you passed. |
| `mime_type` | `text/html;profile=mcp-app` |
| `html` | The self-contained document. |
| `csp_meta` | The resource `_meta`, which declares no external domains. |
| `tool_meta` | The tool `_meta`, under both `ui.resourceUri` and the deprecated `ui/resourceUri`. |
| `size_bytes` | The document's size, at most 120,000 bytes. |

From the command line, the same target writes `index.html` and `mcp_app.json` beside the usual
manifest and diagnostics:

```bash
viewspec compile viewspec.intent.json --target mcp-app --uri ui://ops/incident-queue \
  --action-tool refresh=refresh_incidents --out dist/
```

## Publish on FastMCP

`register_mcp_app()` needs the `agents` extra. It publishes the resource once and
returns a decorator for the tools linked to it:

```python
from mcp.server.fastmcp import FastMCP
from viewspec.mcp_apps import register_mcp_app

server = FastMCP("incident-console")
tool = register_mcp_app(server, app)

@tool()
def show_incidents() -> dict:
    """Open the incident queue."""
    return {"viewspecBindings": {"inc402_value": "Database replica lag (14s)"}}

@tool(visibility=["app"])
def refresh_incidents(note_value: str = "") -> dict:
    """Refresh the queue. Only the view can call this tool."""
    return {"viewspecBindings": {"inc402_value": "Replica lag cleared"}}
```

[`examples/mcp_app_incident_console.py`](../examples/mcp_app_incident_console.py) is this server in
full, ready to run over stdio.

## Actions

| Action | Message to the host | What happens |
|---|---|---|
| Named in `action_tools` | `tools/call` | Calls the tool. The payload bindings are its arguments, keyed by binding id. |
| `select`, unmapped | `ui/update-model-context` | Tells the model, without starting a turn. |
| Any other, unmapped | `ui/message` | Posts the label and payload to the chat as the user. |

While an action runs its button is disabled. If it fails, the button gets `data-mcp-state="error"`
and a red outline, and the error goes to the host log.

## Updating the view

The host fetches a resource once and may cache it, so the compiled document is a template. New data
arrives from a tool: return `viewspecBindings` in `structuredContent`, mapping binding ids to new
text. FastMCP does this for a tool that returns a `dict`. The view applies it from the result of the
tool that opened it and from every `tools/call` response.

Binding ids come from the builder: `add_row(id="inc402", ...)` gives `inc402_label` and
`inc402_value`, and `add_text_input("note", ...)` gives `note_value`. Values reach the page as text,
never as markup. A result with `isError` changes nothing.

## What is checked

`viewspec check` treats an artifact as an MCP App when its manifest says `"target": "mcp-app"`. On
top of the checks for every HTML artifact, it requires the bridge and action scripts to match the
pinned copies byte for byte, and the document to fit the ceiling. `MCP_APP_BRIDGE_MISMATCH` and
`MCP_APP_TOO_LARGE` report a failure. The bridge is also inert when a page has no host, so the
document still opens directly in a browser.

The bridge's wire format follows the types in the `@modelcontextprotocol/ext-apps` SDK. Where the
specification's prose differs, as it does for `ui/message` content, the SDK wins, because hosts
validate against it. `tests/test_mcp_app_host_e2e.py` runs compiled apps against that SDK's
`AppBridge` in Chromium, in a sandboxed iframe under the specification's default CSP.

## Known limits

- **The shape is fixed at compile time.** A tool result can change the text of a row, but it cannot
  add or remove one. Compile for the most rows you will show, or use a view whose shape is stable.
- **About 90 table rows fit.** A row costs roughly 1.2 KB, and the 120,000 byte ceiling is
  deliberately conservative. The specification sets no limit of its own.
- **`check` is the only gate.** `viewspec prove` runs its browser checks on `react_tailwind_tsx`
  artifacts, so it reports `indeterminate` for this target, as it does for `html-tailwind`. There is
  no multi-viewport geometry or Pretext proof of an MCP App yet.
- **Tested against the SDK, not against each product.** The host test uses the official SDK host.
  Try your app in the hosts you target, since each sets its own sizing and policies.
- **Host theming is ignored.** The view keeps its own light theme and profile, and does not read the
  host's theme or CSS variables.
- **IntentBundle only.** An `AppBundle` has routes and screens, which a single resource cannot hold,
  and raw HTML import stays on `html-tailwind`.
