# Region

Regions are the layout tree of a view. Each binding, motif, and action names the region it belongs
to. The compiler turns each region into a container and places that content inside it.

| Key | Holds |
|---|---|
| `id` | The region id. |
| `parent_region` | The parent's id. The root region has `""`. |
| `role` | A word for the region's job, such as `header`, `main`, or `sidebar`. |
| `layout` | A layout hint: `stack`, `grid`, or `cluster`. |
| `min_children`, `max_children` | Bounds on its children. `max_children` is `null` or at least `min_children`. |

`view_spec.root_region` names the root. The regions form one tree: the root has no parent, every
other region has one chain of parents up to the root, and no chain loops. A view holds at most 32
regions.

## Example

A `header` region holds the hero, and a `main` grid holds the KPI cards.

```json viewspec
{
  "schema_version": 1,
  "substrate": {
    "id": "ops_substrate",
    "root_id": "ops",
    "nodes": {
      "ops": {"id": "ops", "kind": "page", "attrs": {"title": "Operations"}, "slots": {}, "edges": {}},
      "uptime": {"id": "uptime", "kind": "metric", "attrs": {"label": "Uptime", "value": "99.98%"}, "slots": {}, "edges": {}},
      "incidents": {"id": "incidents", "kind": "metric", "attrs": {"label": "Open incidents", "value": "3"}, "slots": {}, "edges": {}}
    }
  },
  "view_spec": {
    "id": "ops",
    "substrate_id": "ops_substrate",
    "complexity_tier": 1,
    "root_region": "root",
    "regions": [
      {"id": "root", "parent_region": "", "role": "root", "layout": "stack", "min_children": 1, "max_children": null},
      {"id": "header", "parent_region": "root", "role": "header", "layout": "stack", "min_children": 1, "max_children": 1},
      {"id": "main", "parent_region": "root", "role": "main", "layout": "grid", "min_children": 1, "max_children": null}
    ],
    "bindings": [
      {"id": "ops_title", "address": "node:ops#attr:title", "target_region": "header", "present_as": "value", "cardinality": "exactly_once"},
      {"id": "uptime_label", "address": "node:uptime#attr:label", "target_region": "main", "present_as": "label", "cardinality": "exactly_once"},
      {"id": "uptime_value", "address": "node:uptime#attr:value", "target_region": "main", "present_as": "value", "cardinality": "exactly_once"},
      {"id": "incidents_label", "address": "node:incidents#attr:label", "target_region": "main", "present_as": "label", "cardinality": "exactly_once"},
      {"id": "incidents_value", "address": "node:incidents#attr:value", "target_region": "main", "present_as": "value", "cardinality": "exactly_once"}
    ],
    "groups": [],
    "motifs": [
      {"id": "intro", "kind": "hero", "region": "header", "members": ["ops_title"]},
      {"id": "kpis", "kind": "dashboard", "region": "main", "members": ["uptime_label", "uptime_value", "incidents_label", "incidents_value"]}
    ],
    "styles": [],
    "actions": []
  }
}
```

## What it does not do

- A region sets no pixel sizes, breakpoints, or CSS. `layout` is a hint. The compiler decides
  spacing and columns, and an aesthetic profile may adjust grid columns and spans.
- A region holds no content of its own. Its content is its child regions and the bindings, motifs,
  and actions that name it.

## In the compact form

Without a `regions` key, a [compact](compact-form.md) bundle has `root` and `main`. With it, each
entry declares one region below the root, with the defaults
`{id, parent="root", role=id, layout="stack", min_children=1, max_children=null}`. A motif, input,
or action takes `region` to pick one; the default is `main`.
