# Binding

A binding puts one substrate value on screen. It names the value, the region it goes to, and the role
it plays there. A value with no binding does not appear.

| Key | Holds |
|---|---|
| `id` | The binding id. Motifs, groups, styles, and actions refer to it. |
| `address` | The value's address, such as `node:<id>#attr:<name>`. A slot is `#slot:<name>` or `#slot:<name>[<n>]`, and an edge is `#edge:<name>`. |
| `target_region` | The id of the [region](region.md) it goes to. |
| `present_as` | Its role: `label`, `value`, `text`, `rich_text`, `badge`, `input`, `image_slot`, or `rule`. |
| `cardinality` | `exactly_once`, the only supported value. |

`present_as` is a semantic role, not an element or a component. `input` declares a local text input.

The compiler routes each valid binding exactly once. When two bindings conflict, the first one wins
and the compiler reports a diagnostic. It never drops or copies a value silently.

Every bound element in the compiled output carries `data-binding-id`, and `data-content-refs` holds
its address. `provenance_manifest.json` records the same. `viewspec check dist/` fails when the two
disagree.

## Example

An invoice record with two fields, shown by a `detail` motif. The status renders as a badge.

```json viewspec
{
  "schema_version": 1,
  "substrate": {
    "id": "invoice_substrate",
    "root_id": "invoice",
    "nodes": {
      "invoice": {"id": "invoice", "kind": "invoice", "attrs": {"title": "Invoice 2041"}, "slots": {}, "edges": {}},
      "status": {"id": "status", "kind": "field", "attrs": {"label": "Status", "value": "Overdue"}, "slots": {}, "edges": {}},
      "total": {"id": "total", "kind": "field", "attrs": {"label": "Total", "value": "$4,200"}, "slots": {}, "edges": {}}
    }
  },
  "view_spec": {
    "id": "invoice",
    "substrate_id": "invoice_substrate",
    "complexity_tier": 1,
    "root_region": "root",
    "regions": [
      {"id": "root", "parent_region": "", "role": "root", "layout": "stack", "min_children": 1, "max_children": null},
      {"id": "main", "parent_region": "root", "role": "main", "layout": "stack", "min_children": 1, "max_children": null}
    ],
    "bindings": [
      {"id": "status_label", "address": "node:status#attr:label", "target_region": "main", "present_as": "label", "cardinality": "exactly_once"},
      {"id": "status_value", "address": "node:status#attr:value", "target_region": "main", "present_as": "badge", "cardinality": "exactly_once"},
      {"id": "total_label", "address": "node:total#attr:label", "target_region": "main", "present_as": "label", "cardinality": "exactly_once"},
      {"id": "total_value", "address": "node:total#attr:value", "target_region": "main", "present_as": "value", "cardinality": "exactly_once"}
    ],
    "groups": [],
    "motifs": [{"id": "record", "kind": "detail", "region": "main", "members": ["status_label", "status_value", "total_label", "total_value"]}],
    "styles": [],
    "actions": []
  }
}
```

## What it does not do

- A binding does not compute, convert, or format a value. It shows the value as the substrate holds it.
- A binding carries no style. A style targets it as `binding:<id>`.

## In the compact form

The [compact form](compact-form.md) has no `bindings` key. An item such as
`{"id": "total", "label": "Total", "value": "$4,200"}` gives the bindings `total_label` and
`total_value`. Each key in an item's `values` object gives one more binding, `<node>_<key>`.
