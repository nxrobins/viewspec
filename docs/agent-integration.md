# ViewSpec Agent Integration V1

Agents generate `IntentBundle` JSON. The ViewSpec compiler generates `CompositionIR`.

Do not prompt agents to output `CompositionIR`, primitives, nested `children`, or rendered layout. That bypasses the compiler and undermines the provenance and validation model. Agents should describe the semantic substrate and declarative view intent; ViewSpec remains responsible for layout, style resolution, diagnostics, and exact provenance.

## Agent Contract

Agent output must be one strict JSON object with:

- `substrate.id`
- `substrate.root_id`
- `substrate.nodes` as a dictionary keyed by node ID
- each substrate node containing `id`, `kind`, `attrs`, `slots`, `edges`
- `view_spec.id`
- `view_spec.substrate_id`
- `view_spec.complexity_tier`
- `view_spec.root_region`
- `view_spec.regions`
- `view_spec.bindings`
- `view_spec.groups`
- `view_spec.motifs`
- `view_spec.styles`
- `view_spec.actions`

V1 supported motifs are:

- `table`
- `dashboard`
- `outline`
- `comparison`

Do not use `form`, `list`, `detail`, or `chat` in V1.

Slots and edges use protobuf JSON shape:

```json
{
  "slots": {
    "items": { "values": ["item_1", "item_2"] }
  },
  "edges": {
    "next": { "values": ["item_2"] }
  }
}
```

## SDK Validation

```python
from viewspec import agent_correction_prompt, validate_agent_intent_bundle

result = validate_agent_intent_bundle(agent_json)

if result.valid:
    bundle = result.bundle
else:
    repair_prompt = agent_correction_prompt(result)
```

`validate_agent_intent_bundle()` accepts a JSON string or a dictionary. It checks the agent contract, parses successful payloads through `IntentBundle.from_json()`, and by default compiles with the local reference compiler so diagnostics are returned in an agent-readable shape.

## Published Agent Artifacts

- System prompt: `https://viewspec.dev/agent-system-prompt.txt`
- JSON schema: `https://viewspec.dev/agent-intent-bundle.schema.json`
- Hosted compiler OpenAPI: `https://viewspec.dev/openapi.json`
- LLM summary: `https://viewspec.dev/llms.txt`
- Expanded AI context: `https://viewspec.dev/llms-full.txt`

## Minimal IntentBundle Example

```json
{
  "substrate": {
    "id": "sales_dashboard_substrate",
    "root_id": "sales_dashboard",
    "nodes": {
      "sales_dashboard": {
        "id": "sales_dashboard",
        "kind": "app",
        "attrs": { "title": "Sales dashboard" },
        "slots": {},
        "edges": {}
      },
      "revenue": {
        "id": "revenue",
        "kind": "metric",
        "attrs": { "label": "Revenue", "value": "$2.4M" },
        "slots": {},
        "edges": {}
      }
    }
  },
  "view_spec": {
    "id": "sales_dashboard",
    "substrate_id": "sales_dashboard_substrate",
    "complexity_tier": 1,
    "root_region": "root",
    "regions": [
      { "id": "root", "parent_region": "", "role": "root", "layout": "stack", "min_children": 1, "max_children": null },
      { "id": "main", "parent_region": "root", "role": "main", "layout": "grid", "min_children": 1, "max_children": null }
    ],
    "bindings": [
      { "id": "revenue_label", "address": "node:revenue#attr:label", "target_region": "main", "present_as": "label", "cardinality": "exactly_once" },
      { "id": "revenue_value", "address": "node:revenue#attr:value", "target_region": "main", "present_as": "value", "cardinality": "exactly_once" }
    ],
    "groups": [
      { "id": "metrics", "kind": "ordered", "members": ["revenue_label", "revenue_value"], "target_region": "main" }
    ],
    "motifs": [
      { "id": "kpis", "kind": "dashboard", "region": "main", "members": ["revenue_label", "revenue_value"] }
    ],
    "styles": [],
    "actions": []
  }
}
```

## Error Repair Loop

When validation fails:

1. Pass `agent_correction_prompt(result)` back to the agent.
2. Require the agent to regenerate the full IntentBundle.
3. Validate again.
4. Compile only after `result.valid` is true.

The correction prompt intentionally contains compact structured issues. It should not ask the agent to patch fragments or emit prose.


## Hosted Launch Capabilities

The public SDK agent schema remains V1 and reference-focused. The hosted compiler can accept additional launch fields documented in hosted examples: `projections`, `inputs`, `rules`, and optional `motif_library`.

Agents still generate IntentBundle JSON. They should not generate CompositionIR, React, SwiftUI, Flutter, or HTML directly unless the user explicitly asks for emitter source rather than ViewSpec intent.
