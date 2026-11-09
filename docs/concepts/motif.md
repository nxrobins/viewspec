# Motif

A motif turns bindings into a known UI pattern. It has an `id`, a `kind`, the `region` it sits in,
and `members`, the ids of its bindings in order. The compiler owns each kind's elements, semantics,
and layout.

| Kind | Use | Needs among its members |
|---|---|---|
| `table` | Tabular data in label-value rows | Label and value bindings |
| `dashboard` | KPI cards | Label and value bindings |
| `detail` | Read-only record, profile, or settings fields | Label and value bindings |
| `list` | Ordered narrative or task lists | At least one binding |
| `outline` | Hierarchical outlines and trees | At least one binding |
| `comparison` | Side-by-side comparisons | Bindings from two or more nodes |
| `form` | Inert local form fields and action payloads | An `input` binding |
| `hero` | Intro with eyebrow, title, and description | A `title`, `heading`, `headline`, or `label` binding |
| `empty_state` | Absence, no results, or first run | A `title`, `heading`, `headline`, or `label` binding |
| `loading_state`, `error_state` | The current state of a collection or region | Exactly one title-like binding, at most one description |

A binding is title-like when its address names an attribute or slot called `title`, `heading`,
`headline`, or `label`. A group, `{"id", "kind": "ordered", "members", "target_region"}`, keeps
bindings in source order.

## Example

An invoice page with a `hero` and a two-row `table`.

```json viewspec
{
  "schema_version": 1,
  "substrate": {
    "id": "invoice_substrate",
    "root_id": "invoice",
    "nodes": {
      "invoice": {"id": "invoice", "kind": "invoice", "attrs": {"eyebrow": "Billing", "title": "Invoice 2041"}, "slots": {}, "edges": {}},
      "audit": {"id": "audit", "kind": "line", "attrs": {"label": "Design system audit", "value": "$4,200"}, "slots": {}, "edges": {}},
      "library": {"id": "library", "kind": "line", "attrs": {"label": "Component library", "value": "$8,500"}, "slots": {}, "edges": {}}
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
      {"id": "invoice_eyebrow", "address": "node:invoice#attr:eyebrow", "target_region": "main", "present_as": "label", "cardinality": "exactly_once"},
      {"id": "invoice_title", "address": "node:invoice#attr:title", "target_region": "main", "present_as": "value", "cardinality": "exactly_once"},
      {"id": "audit_label", "address": "node:audit#attr:label", "target_region": "main", "present_as": "label", "cardinality": "exactly_once"},
      {"id": "audit_value", "address": "node:audit#attr:value", "target_region": "main", "present_as": "value", "cardinality": "exactly_once"},
      {"id": "library_label", "address": "node:library#attr:label", "target_region": "main", "present_as": "label", "cardinality": "exactly_once"},
      {"id": "library_value", "address": "node:library#attr:value", "target_region": "main", "present_as": "value", "cardinality": "exactly_once"}
    ],
    "groups": [{"id": "lines", "kind": "ordered", "members": ["audit_label", "audit_value", "library_label", "library_value"], "target_region": "main"}],
    "motifs": [
      {"id": "intro", "kind": "hero", "region": "main", "members": ["invoice_eyebrow", "invoice_title"]},
      {"id": "items", "kind": "table", "region": "main", "members": ["audit_label", "audit_value", "library_label", "library_value"]}
    ],
    "styles": [],
    "actions": []
  }
}
```

## What it does not do

- A `form` does not submit anything. It emits fields and action events, not a submitting `<form>`.
- A `table` or `list` does not search, filter, sort, or paginate. A collection action dispatches an
  event, and the host owns the data.
- A motif takes no CSS. A style token targets it as `motif:<id>`.
- The agent contract has these eleven kinds, and `viewspec check` rejects any other. A Python host
  can add a kind with a trusted [motif plugin](../custom-motif-authoring.md).

## In the compact form

Each motif is one entry in `motifs`, with its items under `rows`, `cards`, `fields`, or `items`. A
`hero` takes `eyebrow`, `title`, and `description` on the motif itself. See
[Compact form](compact-form.md).
