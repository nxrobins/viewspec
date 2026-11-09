# Substrate

The substrate is the data of an IntentBundle: what each thing is and how things relate. It holds no
layout, style, or visual intent. The view decides what appears and where.

`substrate` has three keys. `id` names it, and `view_spec.substrate_id` repeats that id. `root_id`
names the root node. `nodes` maps each node id to its node.

| Node key | Holds |
|---|---|
| `id` | The node id, the same as its key in `nodes`. |
| `kind` | A word for what the node is, such as `metric` or `invoice`. |
| `attrs` | Named values. A value may be any JSON value. |
| `slots` | Named lists of literal values or node ids, as `{"items": {"values": ["a", "b"]}}`. |
| `edges` | Named lists of node ids, in the same shape. Each id must be a declared node. |

Ids and keys use only letters, digits, underscore, dot, and dash. A string value renders as itself.
Any other value renders as compact JSON text: `0.031` renders as `0.031`, `100.0` as `100`, and
`["Ann", "Bob"]` as `["Ann","Bob"]`. `viewspec check` rejects an integer above 2^53 in magnitude
with `INTEGER_OUT_OF_SAFE_RANGE`. Write such an identifier as a string.

## Example

This report has a root node and two metrics. The `metrics` edge links the report to them. The view
binds each metric's label and value, so four values appear. The edge and the report title do not.

```json viewspec
{
  "schema_version": 1,
  "substrate": {
    "id": "q3_report_substrate",
    "root_id": "q3_report",
    "nodes": {
      "q3_report": {"id": "q3_report", "kind": "report", "attrs": {"title": "Q3 report"}, "slots": {},
                    "edges": {"metrics": {"values": ["revenue", "churn"]}}},
      "revenue": {"id": "revenue", "kind": "metric", "attrs": {"label": "Revenue", "value": "$2.4M"}, "slots": {}, "edges": {}},
      "churn": {"id": "churn", "kind": "metric", "attrs": {"label": "Churn", "value": 0.031}, "slots": {}, "edges": {}}
    }
  },
  "view_spec": {
    "id": "q3_report",
    "substrate_id": "q3_report_substrate",
    "complexity_tier": 1,
    "root_region": "root",
    "regions": [
      {"id": "root", "parent_region": "", "role": "root", "layout": "stack", "min_children": 1, "max_children": null},
      {"id": "main", "parent_region": "root", "role": "main", "layout": "grid", "min_children": 1, "max_children": null}
    ],
    "bindings": [
      {"id": "revenue_label", "address": "node:revenue#attr:label", "target_region": "main", "present_as": "label", "cardinality": "exactly_once"},
      {"id": "revenue_value", "address": "node:revenue#attr:value", "target_region": "main", "present_as": "value", "cardinality": "exactly_once"},
      {"id": "churn_label", "address": "node:churn#attr:label", "target_region": "main", "present_as": "label", "cardinality": "exactly_once"},
      {"id": "churn_value", "address": "node:churn#attr:value", "target_region": "main", "present_as": "value", "cardinality": "exactly_once"}
    ],
    "groups": [],
    "motifs": [{"id": "kpis", "kind": "dashboard", "region": "main", "members": ["revenue_label", "revenue_value", "churn_label", "churn_value"]}],
    "styles": [],
    "actions": []
  }
}
```

## What it does not do

- A node does not appear by itself. Only a [binding](binding.md) puts a value on screen.
- The substrate does not order or place anything. [Regions](region.md) and [motifs](motif.md) do.

## In the compact form

The [compact form](compact-form.md) has no `substrate` key. Normalization makes one node for each
item and one for each hero or state motif. The root node takes the view `id` as its id and `title`
as its `title` attribute. The substrate id is `<id>_substrate`.
