# Compact form

The compact form is an IntentBundle written as the Python builder's calls, in JSON. Write it by
default. For the starter dashboard it is about a sixth the size of the canonical bundle.
`viewspec check`, `compile`, `prove`, and `studio` take a compact file. They normalize it into the
canonical bundle, then run the canonical validator and compiler on it unchanged.

`"format": "compact"` marks the form, and `id` names the view. The other root keys are optional:
`schema_version`, `title`, `aesthetic`, `root`, `regions`, `motifs`, `inputs`, `styles`, and
`actions`. Each motif has an `id`, a `kind`, an optional `region` and `group`, and its items:

| Kind | Key | Item |
|---|---|---|
| `table` | `rows` | `{id?, label, value?, values?}` |
| `dashboard` | `cards` | `{id?, label, value, present_as?}` |
| `detail` | `fields` | `{id?, label, value?, values?}` |
| `form` | `fields` | `{id?, label, value=""}` |
| `list` | `items` | `{id?, label, value?, description?, values?}` |
| `comparison` | `items` | `{id?, label, value}` |
| `outline` | `items` | `{id?, label}` |
| `hero` | none | `eyebrow`, `title`, and `description` on the motif |
| `empty_state`, `loading_state`, `error_state` | none | `title` and `description` on the motif |

An item without an `id` gets `<motif>_row_<n>`, or `_card_`, `_field_`, `_item_`, or `_branch_`.
Its bindings are `<node>_label`, `<node>_value`, and `<node>_<key>` for each key in `values`. An
action takes either a `target`, such as `motif:intro`, or a `collection`, the id of a table or list
motif. Its `payload` lists field or input ids. [Agent Integration](../agent-integration.md#compact-form)
has the full key reference.

## Example

```json viewspec
{
  "schema_version": 1,
  "format": "compact",
  "id": "billing",
  "title": "Billing",
  "aesthetic": "aesthetic.calm_ops",
  "motifs": [
    {"id": "intro", "kind": "hero", "eyebrow": "Finance", "title": "Billing overview"},
    {"id": "kpis", "kind": "dashboard", "cards": [
      {"id": "mrr", "label": "MRR", "value": "$48.2K"},
      {"id": "overdue", "label": "Overdue", "value": "3", "present_as": "badge"}
    ]},
    {"id": "invoices", "kind": "table", "rows": [
      {"id": "inv_2041", "label": "Invoice 2041", "value": "$4,200", "values": {"status": "Overdue"}},
      {"id": "inv_2042", "label": "Invoice 2042", "value": "$8,500", "values": {"status": "Paid"}}
    ]}
  ],
  "inputs": [{"id": "query", "label": "Search invoices"}],
  "styles": [{"target": "motif:kpis", "token": "density.compact"}],
  "actions": [{"id": "find", "kind": "search", "label": "Search", "collection": "invoices", "payload": ["query"]}]
}
```

## How a compact file is checked

1. `--patch` applies to the compact file as written. Its pointers name the compact document, such as
   `/motifs/1/cards/1/present_as`.
2. The compact checks run. Their issues carry `COMPACT_*` codes and compact paths. An integer with
   magnitude above 2^53 in a title, eyebrow, label, value, description, or `values` entry fails
   here, with `INTEGER_OUT_OF_SAFE_RANGE`, because normalization stores numbers as 64-bit doubles
   and would round it. Write such identifiers as JSON strings.
3. Normalization builds the canonical bundle with `ViewSpecBuilder`.
4. The lossless fixes and the canonical validator run on that bundle. Their `fixes` and `issues`
   carry canonical paths: a `tag` alias on the `overdue` card is fixed at
   `$.view_spec.bindings[5].present_as`.

`check --json` reports `source_format: "compact"`. `--canonical-out` writes the canonical bundle
that those paths name. Save the example as `billing.compact.json`, then run:

```bash
viewspec check billing.compact.json --json --canonical-out billing.intent.json
```

## What it does not express

- A `bulk_action`. It needs a hand-declared selection binding, so write that bundle in the canonical
  form.
- Substrate edges and slots, or bindings you name yourself. Normalization derives every node and
  binding.
- An AppBundle screen. Screens take canonical IntentBundles.
