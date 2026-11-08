# ViewSpec next to json-render, A2UI, and MCP Apps

This page sets [ViewSpec](https://viewspec.dev/) next to [json-render](https://json-render.dev),
[A2UI](https://a2ui.org), and [MCP Apps](https://modelcontextprotocol.io/docs/extensions/apps). It
states only what the documents in [Sources](#sources) say. A cell says so when they say nothing.

| | ViewSpec | json-render | A2UI | MCP Apps |
|---|---|---|---|---|
| What it is | A local compiler and checker for UI specs. | A generative UI framework: catalogs, prompt generation, and renderers for JSON specs. | Google's Agent-to-User Interaction protocol, a message format. | An extension to the Model Context Protocol for interactive HTML UIs inside chat. |
| Where it runs | Build time, on your machine: the CLI checks, compiles, and proves the spec. An MCP server can also compile a new bundle per tool call with `app.render()`. | Run time. A renderer turns the spec into components, and can render progressively as the model streams it. | Run time. A client renders a surface from the messages it receives. | Run time. The host renders the server's `ui://` HTML document in a sandboxed iframe. |
| Who writes the spec | A coding agent writes JSON source, in the compact form by default. Python code can build the same bundle with `ViewSpecBuilder`. | A model, from a user's prompt, limited to the components in the developer's catalog. | The agent, as a flat list of components with id references. | The MCP server supplies the document. Tool results reach the view through the host. |
| What comes out | HTML, React components, an MCP Apps document, a static app shell, or a Vite React app, each with a manifest. | The developer's own components on web or mobile, and renderers for other outputs such as PDF, email, video, and images. | The client's own components, one for each component type in the message. | An interactive view inside the conversation. |
| What it checks or proves | Validates the spec, compiles it deterministically, checks the DOM against its manifest, and proves layout in Chromium on request. | The catalog limits components and actions, and Zod schemas type their props. `validateSpec` checks structure. `autoFixSpec` marks each fix lossless or lossy. | Not described in these sources. | The host's sandbox and CSP bound what a document can do. |
| What it does not do | Call a model or host anything. Its proof is not pixel-perfect visual regression, accessibility certification, or arbitrary host-app certification. | Its rendered result is "not an iframe, not markdown, not generated code". Code export is a separate package, `@json-render/codegen`. | Not described in these sources. | Limit a document's size. Each host sets its own sizing and policies. |

## Build time or run time

ViewSpec does its work before the interface runs. An agent writes the spec into the repository, and
ViewSpec compiles, checks, and proves the output there. The compiled output calls no model. To
change it, the agent edits the spec and compiles again. The one run-time path is an MCP server that
compiles a new bundle per tool call with `app.render()`, which runs the same checks first.

json-render, A2UI, and MCP Apps do their work while the interface runs. A renderer, a client, or a
host turns a spec, a message, or a document into UI at that moment.

## How they connect

- ViewSpec's `mcp-app` target compiles an IntentBundle into an MCP Apps `ui://` document. See
  [MCP Apps](mcp-apps.md).
- json-render's `@json-render/mcp` package serves json-render UIs as MCP Apps. The developer bundles
  a React app into one HTML file with Vite and passes it to `createMcpApp`.
- json-render's A2UI page shows a json-render catalog and renderer for A2UI messages. The page calls
  its examples illustrative.
- ViewSpec does not read json-render specs or A2UI messages.

## Sources

ViewSpec claims come from this repository's [README](../README.md),
[Getting Started](getting-started.md), [Agent Integration](agent-integration.md),
[Proof Bundle](proof-bundle.md), and [MCP Apps](mcp-apps.md) guide.

json-render claims come from json-render v0.22.0:
[`README.md`](https://github.com/vercel-labs/json-render/blob/v0.22.0/README.md),
[`packages/core/README.md`](https://github.com/vercel-labs/json-render/blob/v0.22.0/packages/core/README.md),
[`packages/mcp/README.md`](https://github.com/vercel-labs/json-render/blob/v0.22.0/packages/mcp/README.md),
and [`apps/web/content/docs/index.mdx`](https://github.com/vercel-labs/json-render/blob/v0.22.0/apps/web/content/docs/index.mdx).

A2UI claims come from json-render's pages about it, not from A2UI's own documentation:
[`a2ui.mdx`](https://github.com/vercel-labs/json-render/blob/v0.22.0/apps/web/content/docs/a2ui.mdx)
for the message format,
[`schemas.mdx`](https://github.com/vercel-labs/json-render/blob/v0.22.0/apps/web/content/docs/schemas.mdx)
for the protocol's name, and
[`adaptive-cards.mdx`](https://github.com/vercel-labs/json-render/blob/v0.22.0/apps/web/content/docs/adaptive-cards.mdx),
which calls it an agent-driven UI protocol.

MCP Apps claims come from json-render's `packages/mcp/README.md` and this repository's
[MCP Apps](mcp-apps.md) guide, which follows the
[`@modelcontextprotocol/ext-apps`](https://github.com/modelcontextprotocol/ext-apps) SDK.
