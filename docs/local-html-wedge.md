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

- `plan_control_action(control, op, value)` takes `click`, `fill`, `check`, `uncheck`, `select`,
  `upload` (see [File uploads](#file-uploads)), or `download` (see [Downloads](#downloads)).
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
- `plan_scroll(direction, control=None)` plans a scroll of the page, or of the scrollable area that
  holds `control`: `end` (where an infinite list loads more), `down`, `up`, or `top`. Any other
  direction is `AGENT_ACTION_VALUE_INVALID`.
- A step of a secret field has `secret: true`, and `step.to_json()` shows its value as `[redacted]`.

The planner is pure Python: it makes no network calls and drives no browser.

### Comboboxes and listboxes

A custom combobox (`role="combobox"`) owns the list its `aria-controls` or `aria-owns` names, or the
list inside it. The list is not a field of its own: its visible `role="option"` items are the
combobox's `options`, each with its `ref`, and the control's `expanded` says whether the list is
open (from `aria-expanded`, or from the list's visibility). A closed list has no options. An ARIA
`role="listbox"` has its options too, and its `value` is the names of the selected ones. Listboxes,
comboboxes, textboxes, sliders, and spin buttons are named by their labels, never by their content.

`plan_control_action(control, "select", "Japan")` on a custom combobox or listbox plans a `choose`
step: a driver opens the list if needed (typing the value into an editable combobox, or clicking the
control), waits for the option, and clicks the one whose name is the value, or else the one whose
name starts with it. A listbox shows all its options, so its value must name one of them. In a form
tool, a combobox field is a string described as "pick an option from its list", with the options
the page shows as `examples`; a listbox field is an `enum`.

### Date fields

A control has `date: true` when it holds a date: a native `date`, `datetime-local`, `month`, `time`,
or `week` input, or a text field whose name, id, or label says so (`date`, `dob`, `birth…`,
`check-in`, `check-out`, `arrival`, `departure`, `deadline`, `due`), or whose placeholder shows a
date's shape (`DD/MM/YYYY`). Controls also carry `minimum` and `maximum` (from `min` and `max`) and
`popup` (from `aria-haspopup`).

`viewspec.raw_html_actions.parse_date()` reads `YYYY-MM-DD` (or with `/` or `.`), "20 November
2026", "Nov 20, 2026", and "Friday, November 20, 2026". It reads a numeric day-month-year date only
when there is one way to read it: "20/11/2026" is 20 November, and "11/12/2026" is refused as
ambiguous. `date_value(control, value)` puts a value in the field's own format (`YYYY-MM-DD`,
`YYYY-MM`, `HH:MM`, `YYYY-MM-DDTHH:MM`, or `YYYY-Www`) and checks `min` and `max`.

`select` (or `fill`) on a native date field plans a `fill` in that format. `select` on a custom date
field plans a `choose_date` step with a `YYYY-MM-DD` value, for a driver to pick from the field's
calendar; a read-only custom date field takes `select` but not `fill`. In a form tool, a date field
is a string with `format: date` (or `date-time`, `time`), a description that gives its shape and its
limits, and for a custom field, "picked from its calendar". Number fields get `minimum` and
`maximum`. `viewspec.raw_html_agent.hidden_ids(nodes)` returns the nodes the user cannot see.

### Date ranges

A date field can hold part of a range. `date_range` is `"range"` for a text field that holds a whole
range: its name, id, or label says "dates" or "date range", or its placeholder shows two dates
(`DD/MM/YYYY - DD/MM/YYYY`). It is `"start"` and `"end"` for a pair of fields, and `range_with` gives
the node id of the other field. A pair is a date field and the next date field after it in the same
form, when their names pair up (check-in and check-out, arrival and departure, depart and return,
start and end, from and to, pick-up and drop-off) or both name the same calendar in `aria-controls`.
Native `date` inputs and custom text date fields can pair; time, month, and week inputs do not.

`viewspec.raw_html_actions.parse_date_range()` reads `{"start", "end"}`, a list of two dates, or text
with two dates: `2026-11-20/2026-11-23`, "Nov 20, 2026 to Nov 23, 2026", or "20/11/2026 - 23/11/2026".
Each date is read by `parse_date()`. The end may be the start, but not before it.

`plan_date_range(start, end, value)` plans a range on one range field (`end` is `None`) or on a pair:

- One range field, or a pair of custom fields: one `choose_date_range` step whose value is
  `START/END`. For a pair, the step's `end_node_id`, `end_ref`, and `end_name` name the end field, and
  `to_json()` shows them as `end`. A driver picks both days, in one calendar when it stays open.
- A pair with a native date field: a step for each field (`fill` for a native one).
- Each day is checked against its field's `min` and `max`; a disabled field is `AGENT_ACTION_DISABLED`.

`plan_control_action(control, "select", value, lift=lift)` with a range on either field of a pair plans
the same steps; it needs `lift` to find the other field. One date on a pair field still sets only that
field. On a range field, `select` needs a range. In a form tool, a range field is an object with
`start` and `end`, and the fields of a pair say which field holds the other end. A form call that sets
both fields of a pair checks that the end is not before the start and plans them as one range.

### File uploads

A file input is a control with `control: "file"`, its `accept` (such as `.pdf,.docx` or `image/*`),
and `multiple`. Its value is the names of the files it holds, which a snapshot gives in
`data-lift-files`; file contents are never in a lift.

`viewspec.raw_html_actions.UploadFile(name, data, mime_type)` is a file to upload. Its `repr` and
`to_json()` show the name, media type, and size, never the bytes. `UploadFile.from_json()` reads
`{"name", "content"}`, with text content, or base64 content and `"encoding": "base64"`. It refuses a
`path`: only the driver of the browser knows which folder files may come from, so it reads them.

`plan_upload(control, files)` plans an `upload` step:

- On a file input, it checks `accept` (by extension, by `type/*`, or by exact type) and `multiple`
  before the browser acts. An empty list clears the input.
- On a button (an "Add photos" button whose file input is hidden), it plans the step and the driver
  checks the file chooser that the button opens.
- Every file needs a plain file name (no `/`, `\`, or NUL), is at most 10 MB, and the files of one step
  are at most 25 MB together. A disabled control is `AGENT_ACTION_DISABLED`; a control that takes no
  files is `AGENT_ACTION_UNSUPPORTED`.

`plan_control_action(control, "upload", files)` does the same. In a form tool, a file field is an
array of file objects (`maxItems: 1` unless the input takes several), and its description says which
types it accepts. A form call plans its upload in the field's place among the other steps.

### Downloads

The agent profile keeps a link's `download` attribute, and a link control carries it as `download`:
the file name it suggests, `""` when the attribute has no value, or `None` when there is none.

`viewspec.raw_html_actions.plan_download(control)` plans a `download` step: click the link or button,
and save the file the click downloads. The step names no folder and no file name. Whoever runs the
browser chooses the folder, and the driver makes a safe file name from the one the page suggests. A
field is `AGENT_ACTION_UNSUPPORTED`, and a disabled control is `AGENT_ACTION_DISABLED`.
`plan_control_action(control, "download")` does the same.

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

### Waiting for a change

A page that answers after a delay (a server call, a timer) shows nothing right after the action.
`viewspec.raw_html_diff.ChangeCondition` says what change to wait for, and checks it on lifts:

```python
from viewspec.raw_html_diff import ChangeCondition

condition = ChangeCondition.from_json({"text": "Order placed"})
condition.met(before, after)  # True once the page shows it
```

| Condition | Met when |
|---|---|
| `{"change": "any"}` | `diff_lifts(before, after)` is not empty |
| `{"change": "message"}` | a new message shows |
| `{"change": "navigation"}` | a new document loads; lifts cannot show this, so the caller decides it and `met()` returns False |
| `{"text": "..."}` | the text is on the page, ignoring case and runs of whitespace |
| `{"gone": "..."}` | the text is no longer on the page |
| `{"control": "...", "enabled": true}` | a control with that name (ignoring case) is on the page; with `enabled`, it is also enabled (or disabled) |

`text` and `gone` search all the text the user can see
(`viewspec.raw_html_agent.visible_text(lift.nodes)`), even across tags, plus control names and
values that are not secret. Any other shape is `AGENT_WAIT_INVALID`. The condition does no waiting
itself; a browser driver reads the page again as it changes and asks `met()` each time.
`experiments/browser-agent-probe/` has a Playwright session that runs these steps.

### Input

The input must be the rendered DOM, not the server HTML, for pages that build their content with
script. `outerHTML` shows the values a page loaded with, not what the user typed, so copy the live
state into attributes first. `experiments/browser-agent-probe/render_pages.js` shows one way to do
this. It also flattens open shadow roots and same-origin iframes into the snapshot.
