# Local HTML Wedge

The local HTML wedge is the SDK import/fallback path for existing HTML. For new UI, agents should emit `viewspec.intent.json` and use `viewspec check` before compiling.

```bash
viewspec compile input.html --design DESIGN.md --out dist/
viewspec check dist/
```

It is intentionally not ViewSpec decompilation. The guarantee is sanitize + theme + manifest.

## Offline Contract

- `viewspec compile` performs no network calls on HTML input.
- Generated raw-HTML artifacts do not auto-fetch network resources when opened.
- External anchors remain clickable with `rel="noopener noreferrer"`.
- Remote image `src` values are replaced with inert external-image links and recorded in `external_refs`.

## Manifest v1

`provenance_manifest.json` follows the minimal schema in `docs/manifest-v1.schema.json`.

Required fields:

- `version`
- `manifest_schema_version`
- `kind`
- `sdk_version`
- `source_name`
- `raw_source_hash`
- `source_hash`
- `design_hash`
- `artifact_hash`
- `command`
- `command_args`
- `policy_version`
- `guarantees`
- `nodes`
- `diagnostics`
- `external_refs`

For raw HTML, `source_hash` is SHA-256 of the canonical lifted DOM token stream. It is not a raw file hash.
`raw_source_hash` is SHA-256 of the original input bytes interpreted as UTF-8 text. `artifact_hash` is SHA-256 of `index.html`.
`command_args` are normalized to avoid absolute paths, temp paths, and machine-local output directories.
The manifest envelope is fail-closed: `raw_html_compile` must use `command: compile_html`, policy `viewspec-raw-html-allowlist@1`, and `guarantees.decompilation: not_claimed`; `intent_bundle_compile` must use `command: compile`, policy `viewspec-intent-bundle@1`, and `guarantees.decompilation: not_applicable`.

Diagnostics have stable `severity`, `code`, and `message` fields. `node_id` and `path` may appear when the pipeline can locate the finding precisely.

`viewspec check <artifact_dir>` validates the manifest, artifact hash, diagnostics shape, manifest-to-DOM provenance links, and the no-autofetch policy.

## Agent Lift Profile

`lift_html(html, profile="agent")` is a second lift profile for agents that read and operate a
rendered page. It does not change `compile_html()`, which always uses the default `"compile"`
profile, so compiled artifacts, `source_hash`, and the sanitizer policy stay the same.

```python
from viewspec.raw_html import lift_html

lift = lift_html(rendered_dom_html, profile="agent")
lift.controls  # every visible link, button, field, and ARIA widget, with an accessible name
lift.forms  # each form with its controls and its submit controls
lift.form_tool_schemas()  # each form as a tool: a JSON Schema for its fields plus the submit node ids
```

What the agent profile adds:

- It keeps `form`, `fieldset`, `legend`, `label`, `input`, `select`, `option`, `optgroup`,
  `textarea`, and `output`, with their state attributes (`value`, `checked`, `selected`,
  `required`, `disabled`, and so on), and it keeps a button's real `type`.
- It keeps `id`, `role`, `tabindex`, `hidden`, `contenteditable`, `aria-*`, and the ViewSpec
  provenance attributes `data-action-id`, `data-action-kind`, `data-action-target-ref`,
  `data-binding-id`, and `data-ir-id`.
- It adds `action` and `field` roles. Names follow `aria-labelledby`, `aria-label`, `<label for>`,
  a wrapping `<label>`, the content, `alt`, `title`, and `placeholder`, in that order.
- It closes an open `<option>` when the next `<option>` or `<optgroup>` starts, as HTML does.
- It reads two hints that a browser-side snapshot can add: `data-lift-hidden` (the user cannot
  see this subtree) and `data-lift-clickable` (this element looks clickable but has no role).
  A subtree with `hidden`, `aria-hidden="true"`, or `data-lift-hidden` is not a control.

What it never keeps:

- The value of a hidden input (`HTML_HIDDEN_INPUT_DROPPED`).
- The value of a password field, of a field whose `autocomplete` is a password, one-time code, or
  card token, or of a field whose `name` or `id` looks like a password, token, CVC, or card number.
  The value becomes `[redacted]` (`HTML_VALUE_REDACTED`).
- Script, style, event handlers, `formaction`, and unsafe URLs, as in the compile profile. A form
  `action` must be relative or `http(s)`.

### Acting on the page

Each control and form carries `ref`, the `data-lift-ref` attribute that a browser-side snapshot
gave its element, so a browser driver can find that exact element again. Controls also carry
`tag`, `readonly`, and `secret`, and `viewspec.raw_html_agent.control_aria_role(control)` gives the
role for a role-and-name locator.

`viewspec.raw_html_actions` plans actions before anything touches a browser:

```python
from viewspec.raw_html_actions import AgentActionError, plan_control_action, plan_form_call

steps = plan_form_call(lift, "checkout", {"email": "bo@example.com", "ship": "express", "terms": True})
steps = plan_control_action(lift.control(node_id), "fill", "Japan")
for step in steps:
    print(step.to_json())  # {"op": "fill", "node_id": ..., "ref": "e20", "name": "Email", "value": ...}
```

- `plan_control_action(control, op, value)` takes `click`, `fill`, `check`, `uncheck`, or `select`.
  It refuses a disabled control (`AGENT_ACTION_DISABLED`), a read-only one (`AGENT_ACTION_READONLY`),
  an action the control cannot take, such as filling a link (`AGENT_ACTION_UNSUPPORTED`), and a value
  it cannot hold, such as an option that is not in the list (`AGENT_ACTION_VALUE_INVALID`). On an ARIA
  checkbox or switch, `check` and `uncheck` become a `click` only when the state must change.
- `plan_form_call(lift, tool_name, arguments)` checks a call of a tool from `form_tool_schemas()`. It
  reports every problem at once in `AgentActionError.details` (`AGENT_FORM_ARGUMENTS_INVALID`):
  unknown arguments, values outside the schema, and required fields that the call leaves out while
  the page shows them empty. Its steps fill the arguments in document order, then click the first
  enabled submit control, or submit the form when it has none. Tool names are unique per page
  (`find`, `find_2`); an unknown name is `AGENT_FORM_UNKNOWN`.
- A step of a secret field has `secret: true`, and `step.to_json()` shows its value as `[redacted]`.

The planner is pure Python: it makes no network calls and drives no browser.

### Messages and changes

`lift.messages` holds the text of each visible region where the page reports what happened: a
`role` of `alert`, `alertdialog`, `log`, or `status`, or an `aria-live` region (`kind` is `live`).
This is where a page says "Email is required" or "Added to cart". Message text is not repeated as
page content.

`viewspec.raw_html_diff.diff_lifts(before, after)` says what changed between two agent lifts of the
same page, for example before and after an action:

```python
from viewspec.raw_html_diff import diff_lifts

diff = diff_lifts(before, after)
print("\n".join(diff.lines()))
```

```text
message status 'Arc lamp added to cart'
changed button 'Added': name 'Add to cart' -> 'Added'; disabled false -> true
text + value '1'
text - value '0'
```

It reports new messages, controls whose state changed (field by field), controls that appeared or
went away, forms that appeared or went away, and page text (headings, values, labels, prose) that
appeared or went away. Controls are matched by `ref` (the same element), then by kind and name when
that pair is unique on both sides, then by node id and kind. A secret field's value never appears in
a diff. `lines(handle_of)` takes a function from an `after` node id to a view handle. Both lifts
should come from the same document; after a navigation, read the new page instead.
`experiments/browser-agent-probe/` has a Playwright session that runs these steps.

### Input

The input must be the rendered DOM, not the server HTML, for pages that build their content with
script. `outerHTML` shows the values a page loaded with, not what the user typed, so copy the live
state into attributes first. `experiments/browser-agent-probe/render_pages.js` shows one way to do
this. It also flattens open shadow roots and same-origin iframes into the snapshot.
