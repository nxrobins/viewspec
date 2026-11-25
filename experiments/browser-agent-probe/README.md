# Browser agent probe

An experiment. Can the ViewSpec agent lift give a browser agent a small, complete model of a live
page, and can the agent act on what it reads?

This folder is not part of the ViewSpec package. The pieces it uses from the package are tested
there:

- `lift_html(..., profile="agent")` reads controls and forms (`tests/test_raw_html_agent_lift.py`).
- `viewspec.raw_html_actions` plans actions and form tool calls (`tests/test_raw_html_agent_actions.py`).

See [docs/local-html-wedge.md](../../docs/local-html-wedge.md#agent-lift-profile).

The bridge and the MCP server run in CI on every pull request, in Chromium on Python 3.11: the
`browser_bridge` job of `.github/workflows/ci.yml` runs `demo_agent.py`, `demo_mcp.py`, and the
agent tasks in `tests/test_browser_bridge_e2e.py`. See [Checked in CI](#checked-in-ci). The two
evals that open real sites (`apg_eval.py`, `real_sites_eval.py`) and `probe.py` are run by hand.

For the reproducible three-tool comparison and the revised Books evidence/output contracts,
see [comparison/README.md](comparison/README.md).

## The loop

```text
             observe                                act
 browser ──────────────► snapshot ──► lift ──► view, tools ──► plan ──► steps ──► browser
 (bridge.js)  composed tree   agent profile  handles [n]      checked   by ref, or by
              + live state    controls, forms                 in Python checked identity
              + data-lift-ref
```

| File | What it does |
|---|---|
| `lift_inpage.js` | In-page library. Gives every element a ref (`e17`) that stays the same while the element is in the page. Snapshots the composed tree (open shadow roots, slots, same-origin iframes) with live form state, and never copies a secret value. Lists the visible controls (the ground truth). Proposes CSS candidates for an element. Keeps a note of every write into a message region, and counts DOM mutations (the recorder). |
| `bridge.js` | A Playwright session driven over stdio with JSON lines. Verifies locators, runs steps, and enforces the operator’s origin, request and session policies. Its write journal distinguishes passed, blocked and simulated requests. Reports the dialogs a page raises, the tabs it opens, and the requests it has in flight. |
| `agent_session.py` | `BrowserSession`: `open`, `observe`, `act(handle, op, value)`, `call(tool, arguments)`, `wait(condition, since, timeout_ms)`, `scroll(step, until, timeout_ms)`, `blocked_writes()`, `blocked_navigations()`. `BrowserSession(download_dir=...)` names the folder for downloads. `changes(before, after)`: what changed between two observations. |
| `action_outcome.py` | The rule for what an action did: `succeeded`, `blocked`, `failed`, or `uncertain`, why, and whether a retry could do it twice. It imports only the standard library, and `tests/test_browser_bridge_outcome.py` checks it without a browser. See [Outcomes](#outcomes). |
| `page_view.py` | The compact text view, with a numbered handle for each control, and slices of it (`view_page`: offset and limit, or `find`). An action's ARIA state shows on its line: `pressed`, `selected`, `expanded` or `collapsed`. |
| `mcp_browser_server.py` | **The MCP server**: `observe`, `act`, and `call` as MCP tools over stdio. See [MCP tools](#mcp-tools). |
| `probe.py` | Measures coverage, names, locators, size, and secret leaks on any set of pages. |
| `apg_eval.py` | Opens every W3C ARIA Authoring Practices example page and acts once on one control of each kind inside the example, as an agent would; reports which widget patterns the lift can operate. |
| `real_sites_eval.py` | Re-runs the real-site tasks that found the lift's bugs (Wikipedia, python.org, gov.uk, react-select, flatpickr, jQuery UI, httpbin, and more), one browser per case. |
| `demo_agent.py` | A scripted agent that uses `BrowserSession` directly, with 125 checks on the fixtures. |
| `demo_mcp.py` | An MCP client that starts the server over stdio and uses only the three tools, with 100 checks. |

### Locators

Normal observations capture each control and form's identity and blocked state in one page call.
They do not generate selectors, scan the document for candidate matches, or ask Playwright to
verify every control. At action time, the bridge checks the saved ref and identity; if the element
was replaced, it searches for a unique visible successor with that identity. Route, item, ambiguity,
and current actionability checks still apply. Paging and find reuse the saved reading.

`BrowserSession.observe(locate=True)` explicitly prepares exportable selectors for diagnostics.
`probe.py` uses this mode, then calls `confirm_locators()` to verify them in Playwright. Each
selector must match exactly the element its ref names. The diagnostic pass tries, in order:

1. a stable attribute: `#id` (not a generated-looking id), `data-testid` and its kin,
   `data-action-id`, `name` (with `value` for a radio), `aria-label`, `title`, `placeholder`, `href`;
2. a content anchor for repeated items: `li:has-text("Arc lamp") button`;
3. role and name: `getByRole("button", { name: "Refresh", exact: true })`;
4. exact text, for a clickable element with no role: `getByText("Export", { exact: true })`;
5. a structural path, as the last resort.

Each part runs inside its frame chain, for example
`page.frameLocator("iframe[title=\"Card details\"]").locator("input[name=\"card\"]")`. Playwright CSS
crosses open shadow roots, so a control in a web component needs no special path.

The page checks kinds 1 and 2 itself, for all controls in one call: it queries the document and every
open shadow root as Playwright CSS does, and counts `:has-text()` matches by text. Playwright checks
only the rest (role and name, exact text, a path that crosses a shadow root), one by one. This
diagnostic work can be expensive on pages with many repeated controls. A prepared locator is used
only when a ref has
gone stale, and then only kinds 1 and 2, and only if the one element it finds has the control's
identity (see below), so a locator that Playwright reads differently fails safely. Kinds 3 to 5 say
what a control is called or where it was, not which one it is, so they are never followed after a
re-render. The probe asks Playwright to confirm every page-checked locator; on the fixtures it
confirms 787 of 787.

### Acting

`act` and `call` plan in Python first (`plan_control_action`, `plan_form_call`), so a bad request fails
before the browser does anything. Then each step finds its element, and it must be the control the
agent read, not whatever sits where that control was.

**Identity.** A ref names a DOM element, and a structural path names a position. Neither says which
product an "Add to cart" button is for: a page can write another product into the same elements, or
build its list again in another order. So at each observation the page also records every control's
identity:

- its **tag** and its **accessible name**;
- the **items** around it, up to three, innermost first: each ancestor that is one of several of a
  kind (a list item, a table row, an article, a section, a fieldset, a form, a custom element, a
  `listitem` or `row` role, or a `<div>` or `<span>` next to one with the same first class), with
  the text that tells it from its neighbours. That text is the item's first piece of text that
  neither neighbour has, its heading first: `{"tag": "li", "label": "Globe lamp"}`.

The same in-page code reads the identity and checks it before a step, so the two readings name
things the same way. A step then finds its element:

- **by ref**, the exact element the agent read, while it still reads the same and sits in the same
  items. Its name is compared without its numbers, so a button that counts ("Saved items (1)",
  "Resend code (59s)") stays usable; an item's label is compared exactly, so "Order 1042" is never
  "Order 1043". An element that reads as something else is refused
  (`drift: the control read as "Add to cart" now reads "Added"`).
- **by css**, when the element is gone and selectors were explicitly prepared: a stable attribute
  or a content anchor that finds exactly one element, and that element has the identity.
- **by name**, otherwise: the one visible element with that tag, that name, and those items, wherever
  the page drew it. This is how a step follows its item through a list that was sorted, whether the
  page built new elements or wrote the products into the old ones.

A step is refused, and nothing is done, when the identity fits no element or more than one:

| Refusal | When | Code |
|---|---|---|
| `ambiguous: 2 controls on the page are "Next page", and nothing tells them apart` | several elements have the identity; the bridge does not pick one | `BROWSER_TARGET_AMBIGUOUS` |
| `drift: "Add to cart" in "Globe lamp" is gone; the page shows "Add to cart" in "Arc lamp", "Clamp lamp"` | the control shows, but not in the item it was read in | `BROWSER_TARGET_DRIFT` |
| `stale: "Export" is no longer in the page` | nothing with that tag and name shows | `BROWSER_TARGET_STALE` |

The error comes with the view of the page as it is now, so the agent picks the control again there.

**Readable is not usable.** A control can be on the page, and in the view, when a person could not
use it. The page says so for every control at each observation, and again before a step runs:

- an ancestor is `inert`, or a modal `<dialog>` is open and the control is not in it;
- layers cover the whole viewport (a dialog and its backdrop) and the control is not in one of them;
- for a step that needs the pointer: something is drawn over the middle of the control (a layer it
  is not part of, or an element at least four times its size, such as an open menu), or the control
  has `pointer-events: none`. Typing into a field is not stopped by these, since a person can reach
  a field with the Tab key.

In the view such a control keeps its line and its handle and ends with `blocked`, and the view opens
with what is in the way:

```text
blocked 19 controls: covered by dialog "Weekly offers"
blocked 1 control: inert: the page has switched this part off
H1 Lamps
[1] button 'Sort by price' blocked
...
[21] button 'Subscribe'
[22] button 'No thanks'
```

A step on a blocked control is refused at once (`BROWSER_TARGET_BLOCKED`,
`blocked: "Add to cart" is covered by dialog "Weekly offers"`), where it used to wait five seconds
for a click that could not land. Before the check the control is scrolled into view, so a sticky
header over it does not count; in the view, where nothing is scrolled, a fixed or sticky bar over a
control is not reported either. When Playwright itself gives up on a click because something small
took it, the error names that element.

The bridge checks each fill, check, and select in the page and returns only true or false, so a
secret value never comes back. If a step navigates the page before the plan is done, the session
stops and says how many steps did not run.

Secret fields show `secret empty`, `secret filled`, or `secret unknown`; their form schema carries
the same state as `x-value-state`. This exposes neither the value nor its length. The snapshot
derives occupancy from live fields, including JavaScript prefill, and overwrites page-authored
state hints. Static lifts without a live state hint report `unknown`, which does not mean empty.
Clearing a secret produces a `value_state 'filled' -> 'empty'` change and a verified fill step.

Omitted form arguments preserve their live semantic field values. Hidden protocol fields (such as
CSRF tokens) remain app-owned and may change. To leave an editable
text/password field blank, explicitly pass an empty string, for example
`call(tool="checkout", arguments={"new_password": ""}, submit=false)`, or
`act(handle=10, action="fill", value="")` with the current password handle. An omitted required
secret must have known `filled` state; `unknown` cannot satisfy that precheck. A form call keeps requested states even when no action is needed. It captures omitted semantic
fields privately before editing, stops on failed state verification, and checks the entire contract
before submission and through the submitter's browser events. A mismatch fails the operation;
unknown state withholds automatic submission and reports uncertainty. `submit=false` also checks
the final field contract. `progress.contract` lists field statuses by one-based intent index (form
argument order, with one entry per radio option); `progress.submission` distinguishes `withheld`
from `attempted`. Existing step progress and passed/blocked write counts remain available.

For a generic single-choice combobox, one visible, explicitly selected option establishes the
selection even when the list's coverage is partial; an input's display must also match that option.
Reselecting it needs no option activation,
and an omitted field can preserve that evidence without a previous agent selection. Input text
alone remains a query: prefilled text, hidden popup rows, conflicting selections, and create or
unknown actions do not establish a selected value. If an omitted field has only that evidence,
the entire form call stops before editing, including `submit=false`. Select the intended value
explicitly to establish its state, or use individual actions when a whole-form contract cannot
be established. Losing selection evidence during later edits still withholds submission.
A prefix query still needs option activation to commit the full value. A selected marker cannot
verify different query text, or hide query drift caused by editing another field.

This is a UI consistency contract. Earlier autosaves remain real effects; the bridge neither rolls
back nor automatically replays a batch. Custom JavaScript request payloads and server acceptance
still require application evidence. Event guards are removed on completion or failure.

Editable custom dates use typed entry when their visible placeholder establishes numeric order;
readonly calendars retain UI interaction. Ambiguous numeric dates cannot prove success. Color
fields accept six-digit sRGB hex (`#rrggbb`), normalized to lowercase before filling; other CSS
colors, alpha and wider color-space controls are outside this initial contract.
For a two-field calendar range, the bridge continues in the start picker when the end field's
`aria-controls` identifies that same still-open picker. It activates independent end fields
separately; proximity alone does not prove a shared picker. Both endpoints must verify before
submission, including widgets that fill both fields only after the second day is chosen.

A form call checks each argument the way the browser's constraint validation will when the form
is sent (an email's format, `minlength`, `pattern`, `min`, `max`, `step`, a required field left
empty), and refuses the call with the reason before the browser acts. A single `fill` is not
checked, as a person can type anything into a field. When a click or submit then tries to send a
form the browser refuses, the browser says why only in a tooltip that is not in the DOM; the
bridge records the `invalid` events it fires and the step reports them: `invalid: [{"name":
"Email", "message": "Please include an '@' in the email address."}]`, and the form was not sent.

Each observation records the bridge's top-level navigation generation. Every step, including form
steps and scrolling, checks that generation before acting. A new document or a same-document
navigation (`pushState`, `replaceState`, hash navigation, or history traversal) expires earlier
observations (`BROWSER_OBSERVATION_STALE`), even if the page reuses the same button or returns to
the original URL. Observe again before acting on the new route. Ordinary rerenders without a
navigation keep the observation usable, subject to the control's identity and blocked checks.

### Choosing from a custom list

A custom combobox shows its options only when it is open, often only after you type, and often only
after a server answers. `act(handle, "select", "Japan")` on a custom combobox or ARIA listbox plans a
`choose` step, and the bridge:

1. reads the shared choice contract: query, candidates, operation and selection evidence;
2. refreshes editable discovery through keyboard input, even if an earlier `fill` left the same
   text in the box (some widgets search only on key presses), or opens a select-only list;
3. waits for the option, waking on DOM changes, up to 5 s;
4. resolves an unambiguous supported candidate by name or prefix, binds its full meaning and
   identity, and rechecks owner, query generation, popup and meaning before and during activation;
5. verifies selection independently of the search query. `chosen` names the activated candidate;
   `selection` records operation, changed state and selected values. Persistence remains unverified.

Candidate metadata preserves the full name and description alongside the short label, and a kind:
`select`, `navigate`, `create`, or `unknown`. Create and unknown candidates retain explicit action
handles and are excluded from form examples. They are never an implicit fallback for `select`.
Generic ARIA options with unexplained qualifiers require explicit activation; ARIA alone cannot
prove the effects of arbitrary page code. Query equality alone cannot verify a selection.

The bounded `vikunja-labels` adapter recognizes the label widget's surrounding structure, owned
results, loading indicator, create rows and removable selected chips. Its scalar select adds one
existing label and preserves other memberships; repeating an already selected label does not
activate it again. It does not claim whole-set replacement semantics from the popup's single-choice
ARIA role. Both `act` and form `call` use this same execution and verification path. A form stops
before submission when selection is unknown, mismatched, or unexpectedly removes an earlier item.
Such a failure can follow real effects: `may_repeat` warns that retrying could duplicate them.
Verification stays bound to the original adapter, operation, mode and commit behavior. Temporary
loss of ownership during popup retirement is unknown until the contract returns; the selected set
and verification result are read together, without falling back to a weaker generic match.

Explicit nested controls keep independent handles, such as Edit Label inside a filter link.
Decorative children and inferred clickable wrappers still collapse into the action they name.
This preserves normal UI readback without eager locator preparation.

A read-only combobox (`aria-readonly`, as react-select without search and Ant Design mark theirs) is
not typed in: ARIA says it is "not editable, but otherwise operable", so the bridge clicks it to open
its list. When the input itself is too small to hit (react-select draws a 1 px, transparent one), the
click goes to the widget around it. Such widgets keep the input empty and show the choice in a box
beside it; the lift reads that box as the combobox's value, and the step is verified against it.

A choice that loads a new page (a search suggestion that runs the search at once, as on GOV.UK) is
reported as chosen and navigated, not verified. The steps planned after it do not run: a form call
stops there and names them (`BROWSER_PLAN_INTERRUPTED`), and the agent observes the new page.

A plain text or search field may offer suggestions once typed into: Wikipedia's search box is a
plain field until it has focus, then a combobox. `select` on such a field plans a `choose` too; the
bridge types the value and picks the suggestion that shows, or refuses when no list opens. An option
that shows a description under its title preserves both lines; its first line supports discovery
while a link supplies the navigation action. When the page replaces the field while it is typed
into (a typeahead that mounts over the search box), the bridge follows it to the new element by its
identity, and goes on.

If two or more options match, or none shows, the step is refused and the current UI remains
available to inspect. Neither text restoration nor Escape is a generic rollback: either can run
page code, and Gitea saves pending selections when its menu closes.

Observation and execution share a live choice descriptor: owner/popup, cardinality, visible
options, selected-state knowledge, coverage and apply behavior. Popup ownership groups controls;
only represented options are suppressed. Residual links, buttons, prose and search inputs remain
visible. Search inputs inside a choice widget are not additional form arguments. An unsupported
open popup is distinguished from an empty list and omitted from typed form arguments.

Known multiple-selection widgets take an **array with the exact desired set**, including `[]` to
clear; omission preserves state. Scalar requests on these widgets are refused with an array hint,
rather than silently changing their meaning. The bridge checks coverage and selected state before
removing anything, validates the whole requested set, and clicks only the necessary changes.
Repeating a satisfied set does not toggle options. Generic ARIA multiple lists need declared
set-size evidence for complete coverage; filtered or unknown sets cannot authorize replacement.

The tested `gitea-sidebar` adapter recognizes the issue sidebar's explicit selection mode,
checkmarks and nested items; arbitrary `.selected` or `.checked` classes are not interpreted as
selection state. It applies the known close action and verifies the resulting visible selection,
including sidebar replacement. Single-choice ARIA widgets retain their existing string interface.
Every internal interaction rechecks the observation's route, owner identity, option ownership,
disabled state and pointer reachability.

Choice results include `selection.changed`, the selected names, and a `completion` of `ui`,
`form_pending`, or `closed`. `persistence: unverified` distinguishes UI verification from a saved
business result. Normal transport-policy outcomes still take precedence. A failed choice does
not automatically submit the rest of a form. `progress` reports applied/verified/failed/not-run
fields and whether submission was attempted. No atomicity or rollback is promised.

After an action, navigation may interrupt the following read. The session retries interrupted
snapshots and rejects target identities captured from a different document or route. Recovery
is bounded and read-only: it never repeats a click, submission or form plan. An explicit wait
continues checking its original condition within its elapsed-time budget.

If the page still cannot be read, `BROWSER_OBSERVATION_FAILED` retains completed `steps` and
`progress`, with an uncertain outcome and `retry: may_repeat`. `progress.writes` counts recorded
write dispositions (`passed`, `blocked`, `blocked-and-simulated`); a passed request is transport
evidence, not proof that an order was persisted. A blocked write still takes precedence. Observe the resulting
page to establish the business result before deciding whether another action is needed.

### Dates

`act(handle, "select", "Nov 20, 2026")` on a date field:

- **Native** `<input type="date">` (and `month`, `time`, `datetime-local`, `week`): the package puts the
  value in the input's format and checks `min` and `max`, then the bridge fills it.
- **Custom** (a text field named or shaped like a date, often read-only): a `choose_date` step. The
  bridge opens the calendar (a click on the field, or on a button beside it named for a date, a
  calendar, or choosing), finds its day cells, reads which month it shows (from its month and year
  lists, its title, or its days' full-date labels), goes to the right month (with the lists, or with its
  next and previous buttons), and clicks the day. Days of the months around it, which repeat numbers
  at the start and end of the grid, are skipped. A disabled day is refused. The step checks that the
  field shows the date, in whatever order of day, month, and year it uses.

If the calendar cannot reach the date, or the day is disabled, the step is refused and the calendar it
opened is closed again. A numeric date that can be read two ways ("11/12/2026") is refused before the
browser acts. The field's calendar is the one it names in `aria-controls`; else one that was not shown
before the field was clicked (a popup appended to the end of the page, as flatpickr's is); else one
drawn next to the field. A calendar that is always on the page somewhere else (an inline example) is
not taken for it. Month arrows with no name, a bare span around an icon as flatpickr draws them, are
found by their class ("next", "prev") or a data attribute. A field that nothing labels is named by the
text written beside it (`Date: <input>`, `<input> Remember me`), when it is a short, label-like phrase,
and an id or name such as `datepicker` marks it as a date field. A calendar that redraws itself when
its year list changes (jQuery UI replaces its month list too) is read again between the two choices.

**Date ranges.** The lift marks a field that holds a whole range (`date range`: its name says
"dates" or "date range", or its placeholder shows two dates) and a pair of fields that hold the start
and the end (`(range start)`, `(range end)`: the next date field in the same form, with paired names
such as check-in and check-out, arrival and departure, start and end, or from and to; or both name the
same calendar in `aria-controls`). `select` on such a field takes a range: `{"start", "end"}`, a list of
two dates, or text such as "2026-11-20/2026-11-23" or "Nov 20, 2026 to Nov 23, 2026". The end must not
be before the start, and each day is checked against its field's `min` and `max`.

- **A pair of custom fields, or one range field:** one `choose_date_range` step. The bridge picks the
  start day as for one date. If the calendar stays open (a range picker that waits for the end), it
  picks the end day there; else it opens the end field's calendar. The step is verified when both
  fields show their dates, or the one field shows both.
- **A pair with a native date field:** a `fill` for the native field, and a pick for the other.

A calendar can show several months side by side, each under its own title ("November 2026",
"December 2026"). The bridge reads each month's days apart, so day 28 of the second month is not
mistaken for day 28 of the first. A form call that sets both fields of a pair plans them as one range.

### Uploads

`act(handle, "upload", files)` gives files to a file input. A file is `UploadFile(name, data,
mime_type)`; over MCP it is `{"name", "content"}` (text, or base64 with `"encoding": "base64"`), or
`{"path"}` inside the upload folder.

- **A file input:** the package checks its `accept` (`.pdf`, `image/*`, `text/csv`) and `multiple`
  before the browser acts, then the bridge sets the files. An empty list clears the input. The view
  shows `[2] file 'Résumé' required accepts .pdf,.doc,.docx`, and after an upload, the file names.
- **A button whose file input is hidden** ("Add photos", as many sites do): the bridge clicks it,
  waits for the file chooser it opens, checks the chooser's `accept` and `multiple`, and sets the
  files. A control that opens no chooser is refused.

The step is verified when the input holds exactly the files that were sent. A form tool takes a file
field as a list of files (`maxItems: 1` when the input takes one); `call` uploads them in the field's
place in the form. Each file is at most 10 MB, and the files of one step at most 25 MB together.

File contents are never in a step, a log, or a result: a step shows each file's name, media type, and
size. A blocked form post shows a file as `{"file": "cv.pdf", "bytes": 24, "type": "application/pdf"}`.

### Downloads

`act(handle, "download")` clicks a link or button and saves the file that the click downloads. The
view marks a link with a `download` attribute: `[1] link 'Invoice INV-1042' -> files/invoice-1042.pdf
download`. A plain link to a file the browser does not show, and a button whose script builds the
file (a CSV export), download too.

- Files are saved only in the folder that whoever runs the browser names (`BrowserSession(download_dir=...)`,
  or `--download-dir` for the MCP server). Without one, a download step is refused.
- The file name is the one the page suggests, made safe: no folders, no control characters, and no
  leading dot (`../../.profile` is saved as `_.._.profile`, in the folder). A name that is already
  there gets ` (1)`, ` (2)`, ...; no file is overwritten. A file over 50 MB is not kept.
- The step result gives `file`, `path`, `bytes`, `sha256`, `type`, the name the page suggested, and
  the URL. It never holds the contents.
- A download that any other step starts (a `click` on "Export orders") is not saved. The step result
  names it in `unsaved_downloads`, so the agent can do it again with `download`.
- A download step on a control that starts no download within 4 seconds is refused.

### Scrolling

A feed or a catalog with infinite scroll loads more items only when you scroll to its end, often
after a server call. `scroll(plan_scroll(direction, control), until=..., timeout_ms=...)`:

- scrolls the page, or with a control, the area that scrolls for it (the nearest ancestor that can
  scroll, crossing shadow roots): to the `end`, about one screen `down` or `up`, or to the `top`;
- waits after each scroll for what loads: it wakes on DOM changes until the area holds more elements
  than before, or 2.5 s pass. A loading indicator (a live region that says "Loading…", a progress
  bar, an `aria-busy` subtree) is not content, so a page that shows one first and adds its items
  later is waited for;
- without `until`, scrolls once and says whether it `moved`, whether more `loaded`, and whether the area
  is `at_end`;
- with `until` (a `ChangeCondition`), scrolls again and again until the page shows it, the list ends
  (a scroll at the end loads nothing new: `end_reached`), or the timeout ends. If the page already
  shows it, it does not scroll.

On the feed fixture, one scroll loads 20 more posts in about 0.5 s; `until "Post 75"` takes 2 scrolls;
a search for a post that does not exist stops at the end of the feed (100 posts) with "You have
reached the end"; and the comments pane scrolls on its own until "Comment 35" shows.

### Changes

After an action, the agent needs to know what it did. `changes(before, after)` diffs two
observations with the package's `diff_lifts` and returns short lines, with the handles of the new
view:

```text
message status 'Arc lamp added to cart'
changed [7] button 'Added': name 'Add to cart' -> 'Added'; disabled false -> true
text + value '1'
text - value '0'
```

Messages come first: they are where a page says "Email is required" or "Added to cart". Then come
changed controls (field by field), controls that appeared (`added [11] text 'Promo code'` when a
`<details>` opens) or went away, forms, and page text that appeared or went away. A secret field's
value never appears: a card number fill shows `no change`. If the page loaded a new document, the
result is `new_document: true` and one line naming the new page, because a diff would list the whole
page.

Three kinds of change leave both readings with the same text and the same controls. They are
reported too:

```text
message status 'Saved' (shown again)
message status 'Link copied' (shown, now gone)
address /outcomes.html -> /outcomes.html#notes
reordered 3x <tr>: 'Ana 36', 'Bo 41', 'Cy 29'
```

- **The same items in another order.** A sort that moves a table's rows adds no text and removes
  none. `diff_lifts` compares the order of siblings with the same tag under each parent, by what each
  item says, and reports the items that both readings show when their order differs. An item that
  was added, removed, or rewritten is not part of a reorder; the text lines have it. A list that the
  page built again from new elements still compares, because an item is its text.
- **A message shown again, or shown and taken away.** A status line that says "Saved" a second
  time reads the same before and after, and a toast that lasts 300 ms may be gone before the page
  is read. The page keeps a note of every write into a message region (`role="alert"`, `status`,
  `log`, `alertdialog`, or `aria-live`), of a region that is added already holding text, and of one
  that was hidden and now shows. `changes` reports a note as `(shown again)` when the message was
  already on the page, and as `(shown, now gone)` when it is not on the page any more. A page that
  draws itself again (takes a region out and puts one like it back in one go) has not said
  anything, and is not reported.
- **A new address in the same document.** A `#hash` link or a route that a script pushes changes
  the address, and perhaps the title, and nothing else.

What a widget says while the bridge itself drives it is left out: a calendar's month title as
`select` pages through it, "5 results" as it types into a combobox, "Loading…" during a scroll. That
is how the step works, not the page's answer. A message such a step leaves on the page is reported
like any other.

### Waiting

Right after an action, a page that answers after a delay (a server call, a timer) has not changed
yet, so the diff says `no change`. `wait(condition, since, timeout_ms)` waits for a change that the
agent names, with the package's `ChangeCondition`:

| `wait_for` | Waits until |
|---|---|
| `{"change": "any"}` | anything in `changes`: the diff, a message shown again or shown and gone, a new address |
| `{"change": "message"}` | a new message (alert, status, live region), or one shown again or shown and gone |
| `{"change": "navigation"}` | a new document |
| `{"text": "Order placed"}` | the text is on the page |
| `{"gone": "Loading"}` | the text is no longer on the page |
| `{"control": "Apply", "enabled": true}` | the control is on the page, and enabled (or disabled) |

It does not read the page on a timer. After each reading that does not meet the condition, the
bridge watches the DOM with a `MutationObserver` on the document, every open shadow root, and every
same-origin iframe, and wakes on the first change (once the DOM is quiet for 100 ms, or 500 ms after
the first change) or on a navigation. Then the page is read again. On the fixtures, a link that
shows 600 ms after a click is found in about 700 ms with 2 readings. The longest wait is 30 s.

Two changes are no DOM change, and wake a wait all the same:

- **A script that sets only a property.** `input.value = "Lisbon"` and `box.checked = true` change
  no attribute and no element. While it waits, the bridge hashes what every `input`, `textarea`, and
  `select` holds, every 100 ms, and wakes when the hash differs. Only the hash is kept, so no value
  is held, a secret one included. On the fixture, a lookup that answers 400 ms after the click by
  setting two properties is found in about 520 ms; before, the wait ran to its timeout.
- **A change between the reading and the wait.** The page is read, the condition is not met, and
  only then does the wait begin. A change in that gap used to be missed until the next one. The
  wait now compares the page with what the last reading saw (the recorder's mutation count and the
  field hash), and wakes at once when they differ.

### Outcomes

After an action, an agent must decide whether to go on, wait, or try again. A view and a list of
changes do not settle that: a click that changed nothing may have failed, may still be working, or
may have done something that does not show. Every `act` and `call` result therefore carries
`outcome`:

```json
{"status": "uncertain", "page": "busy", "retry": "may_repeat",
 "reason": "click 'Publish' ran, and the view shows no answer. 1 request it started is still in flight: wait for the answer (wait_for) before you repeat it."}
```

`status` is about whether the action took effect, not about whether the page's answer was good
news. "Card declined" is an answer.

| `status` | Means | When |
|---|---|---|
| `succeeded` | The bridge saw the effect. | What the agent waited for showed; the page navigated; every step was checked on its control (`verified`); or the page answered: `changes` is not empty, a file was saved, a tab opened, an alert was raised. |
| `blocked` | Policy stopped a write. | The write journal takes precedence over a navigation, a matching wait or a success message. Other writes in the same action may have passed. |
| `failed` | It did not take effect. | The request was refused (`ok` is false); the browser would not send the form (`invalid`); a control does not hold what was set (`verified` is false); or the origin allowlist stopped the navigation. |
| `uncertain` | It ran, and nothing shows whether it worked. | A click with no answer; a dismissed question with no other evidence of an effect; a wait that was not met; a step that stopped part-way (a timeout, a plan the page interrupted by navigating). |

The rules are tried in order. A blocked write takes precedence. The bridge next checks for a refusal, a form the browser would
not send, a navigation the allowlist stopped, a control that does not hold its value. Then what the
agent waited for, which is its own test of success: a wait that was met is `succeeded`, and one that
was not met is `uncertain` whatever else the page did.
Last come the other signs of an effect. `reason` is one line, and names the evidence: the first line
of `changes`, the question the page asked, the field the browser refused.

A dismissed `confirm` or `prompt` never proves that an action was canceled. An order may already
have completed before the page asks whether to email a receipt. An observed effect still counts
as `succeeded` / `not_needed`. Without other evidence, dismissal is `uncertain` / `may_repeat`,
even when a field holds what the step set: its handler may have acted too. A refusal after a
dismissed question also cannot make repeating the request `safe`.

One kind of change is not a sign of an effect: a field that now holds what a step set, when the
step was checked on it. After `call` fills in a form and submits it, `changes` lists the new
values. They say what the call typed, not what the submit did, so they do not make the outcome
`succeeded`; a message, a request that went out, or a navigation does.

`retry` says whether repeating the same request could do the thing twice:

| `retry` | Means |
|---|---|
| `not_needed` | It succeeded. |
| `safe` | Nothing happened, or the action only sets a state (`fill`, `check`, `uncheck`, `select`, `scroll`): doing it again changes nothing more. Deal with the reason first. |
| `may_repeat` | A click, a submit, a choice from a list, an upload, or a download ran, or may have. Repeating it may act twice: look at the page first. |

When `status` is `uncertain`, `page` says what the page did from the action to the reading, so the
agent can choose between waiting and trying again:

| `page` | Means | What to do |
|---|---|---|
| `busy` | A request the action started is still in flight. | Wait: `wait_for`, or `observe(since=..., wait_for=...)`. |
| `reacted` | The DOM changed or a request finished, and the view shows nothing of it; or the page changed, and not in the way the agent waited for. | Read the view. |
| `quiet` | No DOM change and no request. | The action may have done nothing. |

The facts behind this are on each step. `reaction` counts what the page did during the step: DOM
`mutations` (from the recorder), `requests` started, and how many of those are still `pending`.
`dialogs` lists the boxes the page raised, with the question: `{"type": "confirm", "message": "Delete
this draft?", "answer": "dismissed"}`. Playwright dismisses a dialog nobody listens for, so a
cancelled delete used to look like a click that did nothing. The bridge still dismisses every box
(and lets a page that asks before it unloads go), and now says so. `opened` lists the address of
each tab the page opened; the tab is closed, not followed, and the agent stays on its page. A box or
a tab that comes later, while the bridge waits or between two tool calls, is listed beside `steps`.

On the fixture `outcomes.html`, through the MCP tools:

| Action | `changes` | `outcome` |
|---|---|---|
| Sort the table (the rows move) | `reordered 3x <tr>: 'Ana 36', 'Bo 41', 'Cy 29'` | `succeeded` |
| Save, a second time | `message status 'Saved' (shown again)` | `succeeded` |
| Copy link (a toast, gone in 300 ms), read after it has gone | `message status 'Link copied' (shown, now gone)` | `succeeded` |
| Look up (two properties set 400 ms later), `wait_for {"change": "any"}` | both fields, after about 520 ms | `succeeded` |
| Jump to notes (a `#hash` link) | `address /outcomes.html -> /outcomes.html#notes` | `succeeded` |
| Refresh badge (a button with no handler) | `no change` | `uncertain`, `quiet` |
| Delete draft (the page asks with `confirm()`) | `no change` | `uncertain`, `may_repeat`, with the question |
| Help (a link with `target="_blank"`) | `no change` | `succeeded`, with the tab's address |
| Publish (the server takes over a second), no wait | `no change` | `uncertain`, `busy` |
| Publish, `wait_for {"change": "message"}` | `message status 'Published'` | `succeeded` |

### Long pages

A long page gives a long view: the 300-product catalog fixture has hundreds of controls and over a
thousand lines. `view_page(lines, offset, limit, find)` returns a slice:

- **By offset.** `limit` lines from `offset`. A slice that starts in the middle of the page opens with
  `context`: the headings it sits under, outermost first, and the form of its first control:
  `["H1 Lamps and lighting", "H2 Floor lamps", "H3 Prism floor lamp 039"]`.
- **By text.** `find` keeps the lines that contain some text (ignoring case and runs of whitespace).
  A matching heading brings the lines of its section (up to 30), so `find="lamp 250"` returns the
  product with its price, link, and Add to cart button. Any other matching line comes after the
  heading it sits under. Lines that are not next to each other are split by `…`.

Each line knows the heading of its own section (`section`, `article`, `li`, `main`, `form`, and the
like), so a heading does not claim the content of a section next to it. Handles are those of the whole
view: a handle from any slice, or from `find`, works with `act`.

Ordinary paragraphs appear as `text ...`, including short descriptions and inline emphasis in their
original word order. Their wording is complete rather than preview-truncated; `limit` counts lines,
so one long paragraph can still make a long line. Repeated paragraph groups keep their text lines.
Paging and `find` include these lines after privacy projection, with the same heading context as
controls. Hidden text and text already represented by a control, message or table row are omitted
from ordinary text lines. Inline links keep their wording in a paragraph and their separate handle.
Inline numbers keep their wording and their separate `value` line, including page counters.

## MCP tools

`mcp_browser_server.py` serves three tools over stdio:

| Tool | Arguments | Does |
|---|---|---|
| `observe` | `url` or `since` (both optional), `wait_for`, `timeout_ms`, `offset`, `limit`, `find`; or `observation_id` with `offset`, `limit`, `find` | Opens `url` if given, then reads the page. Returns `view` (one line per message, heading, value, table row, paragraph, and control; each control has a handle such as `[7]`), `view_page` (which lines these are), `form_tools` (complete typed tools for forms represented in this slice), `observation_id`, `url`, and `title`. With `since` (an earlier `observation_id`), it also returns `changes` since that reading, for a page that updates by itself. With `observation_id`, it pages through that kept reading without reading the page again. |
| `act` | `action` (`click`, `fill`, `check`, `uncheck`, `select`, `scroll`, `upload`, `download`), `handle`, `value`, `files`, `observation_id`, `wait_for`, `timeout_ms` | Does one thing to one control, waits for `wait_for` if given, then reads the page again. `upload` gives `files` to a file input, or to the file chooser a button opens. `download` clicks and saves the file it downloads. `select` on a date range field, or on either field of a pair, takes a range and picks both days. `scroll` needs no handle: it scrolls the page, or the pane of the control with the handle; `value` is the direction, and `wait_for` means "keep scrolling until it shows". The result has `scroll`: `scrolls`, `moved`, `loaded`, `at_end`, `end_reached`, `met`. |
| `call` | `tool`, `arguments`, `submit`, `observation_id`, `wait_for`, `timeout_ms` | Fills in a form with typed arguments (files too) and submits it, waits for `wait_for` if given, then reads the page again. |

The view in every result is a slice: 200 lines by default, at most 1000 (`limit`). `view_page` gives
`offset`, `returned`, `total`, `next_offset`, and for a slice in the middle of the page, `context`.
The summary says how to get more: `Showing lines 1-200 of 1308. More: observe(observation_id=14,
offset=200).` `observe(observation_id=14, find="lamp 250")` searches that reading. A small page fits
in one slice, so nothing changes for it.

`form_tools` follows the slice too: it includes only forms whose form header or control lines appear
in `view` or `view_page.context`. A control still brings its complete form schema when its header is
on an earlier page. Empty pages and searches without matches return no schemas. Use paging or `find`
to discover other forms; tool names, private field aliases, and handles stay stable within a reading,
and a previously seen form remains callable after paging elsewhere. This bounds the number of schemas
by the forms represented in the returned lines, not a byte limit: one large form still has a complete
schema. No input fields are truncated.

Every result uses the `viewspec mcp` result format: `ok`, `summary`, `errors` (each with `code`,
`message`, and `fix`), and `next_actions`. `act` and `call` also return `steps` (with secret values
as `[redacted]`, `chosen` for a choice from a list, and `invalid` for a click the browser refused:
the fields that failed their constraints and why), `navigated`, the new view, and `changes` (see [Changes](#changes)), so the agent
needs no extra `observe`. With `wait_for`, the result also has `wait`: what it waited for, `met`,
and `waited_ms`. A wait that is not met is not an error: the action is done, `ok` stays true, and the
summary says the change did not show, so the agent does not repeat an action that may have worked.

`act` and `call` results also carry `outcome`: `status` (`succeeded`, `blocked`, `failed`, or `uncertain`),
`reason`, `retry` (`not_needed`, `safe`, or `may_repeat`), and, when uncertain, `page` (`busy`,
`reacted`, or `quiet`). See [Outcomes](#outcomes). `ok` says that the request was accepted and run;
`outcome` says what came of it, so `ok` can be true and the outcome `failed` (the browser would not
send the form). A refused request has an outcome too, and when some steps of a plan ran before the
refusal, `steps` lists them. Each step has `reaction` (`mutations`, `requests`, `pending`), and,
when there are any, `dialogs` (the boxes the page raised and how they were answered) and `opened`
(the tabs it opened). `observe` results list the `dialogs` and `opened` tabs that came since the
last result.
A `wait_for` that is not valid is refused before the browser does anything (`AGENT_WAIT_INVALID`). When writes
are blocked, `blocked_writes` lists each stopped request with its form fields; secret fields are
`[redacted]`, a field that no view listed is `[not shown]`, and a file is its name, size, and type.
`blocked_navigations` lists each navigation that was stopped, with the reason. `blocked_requests`
lists what else the boundary stopped, one entry for each host and reason: a script, an image, or a
frame for a host that is not listed, and a connection the gate refused. A request that a tab the
page opened made, not the page itself, has `new_tab: true` in the first two lists, and is reported
in the result of the step that opened the tab. The result of `observe(url=...)` has `limits`: the
server's limits in plain sentences.

A request that the page cannot take is refused before the browser does anything, and the result
says so. A refused form call names each problem: `{"argument": "ship", "problem": "invalid", ...}`.
Other codes: `BROWSER_NO_PAGE`, `BROWSER_INVALID_REQUEST`, `BROWSER_HANDLE_REQUIRED`, `BROWSER_HANDLE_UNKNOWN`,
`BROWSER_OPTION_AMBIGUOUS`, `BROWSER_OPTION_NOT_FOUND`, `BROWSER_DATE_UNAVAILABLE`, `BROWSER_DATE_NOT_PICKED`,
`BROWSER_UPLOAD_NOT_ALLOWED` (a path outside the upload folder, or no folder), `BROWSER_UPLOAD_NOT_FOUND`,
`BROWSER_UPLOAD_TOO_LARGE`, `BROWSER_UPLOAD_REFUSED` (the chooser refused the files, or no chooser
opened), `BROWSER_DOWNLOAD_NOT_ALLOWED` (no download folder), `BROWSER_DOWNLOAD_NONE` (the click
started no download), `BROWSER_DOWNLOAD_TOO_LARGE`, `BROWSER_DOWNLOAD_FAILED`,
`BROWSER_OBSERVATION_UNKNOWN` (the server
keeps the last 8 observations), `BROWSER_OBSERVATION_STALE`, `BROWSER_TARGET_DRIFT` (the control is
not what the view showed, or not where), `BROWSER_TARGET_AMBIGUOUS` (several controls could be the
one the view showed), `BROWSER_TARGET_BLOCKED` (the control is covered or switched off),
`BROWSER_TARGET_STALE`, `BROWSER_PLAN_INTERRUPTED`, `BROWSER_ORIGIN_NOT_ALLOWED`,
`BROWSER_ADDRESS_NOT_ALLOWED` (not an `http` or `https` page, or an address a rule denies),
`BROWSER_ADDRESS_PRIVATE` (this computer or a private network, in an open session),
`BROWSER_SECRET_REFUSED` (no secret of that name, or the field is not a secret field),
`BROWSER_TIMEOUT`, and `BROWSER_ERROR`.

### Browser boundary

The operator sets the policy at startup. Opening a page returns the effective limits. A trial
requires an explicit `--allow-origin`. Use `--public-read` to browse public HTTP(S) origins
without operator credentials or write permission. Public browsing refuses private addresses.
Python callers can pass `BrowserSession(boundary=Boundary.build(...))`. The default direct
session uses the public browsing policy.

| Boundary | Default and owner | Observable result |
|---|---|---|
| Network | The browser’s proxy resolves and pins destinations. The request policy checks methods, origins and redirect targets. `--allow-host` grants subresource access only. | Refused documents and connections appear in `blocked_navigations` or `blocked_requests`. |
| Writes | HTTP writes, method overrides, WebSockets and EventSource require operator permission. For example, `--allow-write "POST https://shop.example/cart"` authorizes one endpoint. | The action reports per-step `writes`, plus newly observed `passed_writes` and `blocked_writes`. A passed entry means policy permitted dispatch. Application completion needs separate evidence. |
| Reads | `--read-policy auto` requires explicit read patterns for every origin-scoped trial. Supply the document and asset paths with repeated `--allow-read` options. Public read mode trusts ordinary GET, HEAD and OPTIONS by default. | Unclassified requests in explicit mode are refused. `--mutation-url` marks a known state-changing read. A `--deny-url` rule takes priority. |
| Page response | A blocked HTTP write receives 403 and `X-ViewSpec-Blocked: 1`. A document gets HTML. A JSON request gets a JSON object with `blocked`, `state`, and `sent`. The stub echoes a valid requesting origin for CORS. | `--simulate-writes` gives the page a synthetic 200 and records `blocked-and-simulated`. Both blocked states produce `outcome.status=blocked` and `retry=may_repeat`. |
| Privacy | `--private strict` withholds native text-entry values and blocked form values. `--private heuristic` opts into field recognition. Both honor explicit secret markers and scrub known credential values across results, including URLs and errors. | Strict MCP results and direct session records expose only the body size. Heuristic mode can report visible form fields when the request has a supported form content type. Short values receive field-level protection. Arbitrary secrets in prose and transformations of private values can escape recognition. |
| Identity | Opens in one session reuse the browser context. Operators may supply a Playwright `--storage-state` file, or `--header Authorization=ENV_NAME` and `--cookie session=ENV_NAME`. These options require one exact origin and no extra hosts. | The browser retains cookies and local storage between opens. `--save-storage-state FILE` writes host-only cookies and local storage to a new owner-readable file on close. Input files keep their original contents. |

SVG `<use href="…">` and `<use xlink:href="…">` references inside SVG markup do not make a
bare symbol ID a session-wide secret. For example, `/icons.svg#down` no longer redacts
“downstairs” in unrelated public text. The exception applies only to symbol references in
that element context, not navigation URLs or ordinary links ending in `.svg`. Query and named
fragment parameters keep their existing protection. Projected SVG references mask the symbol
fragment; a value learned from a private field, credential URL or operator secret is still
redacted everywhere, even when also used as a symbol ID.

Use narrow read rules on applications where a GET can change state. A broad read rule grants
every matching URL, including query strings, and the browser cannot infer the server’s semantics.
`--secret NAME` lets an agent fill a recognized secret field by naming an environment variable.
Direct opening accepts HTTP(S) URL spellings without embedded credentials. Subframes are restricted to the current top document’s exact origin or inline frame content.
Workers and popup network access are disabled. HTML documents pass through a Chromium response check before
execution with a 10 MiB and 10 second limit. Unsupported documents can fail to load.

`--confirm dismiss` is the default. On a listed origin `--confirm accept` answers JavaScript
confirmation boxes affirmatively. The step reports the actual answer. JavaScript prompts are
dismissed. `--upload-dir` and `--download-dir` select the operator’s upload and download folders.


This is not `viewspec mcp`. The package's server makes no network calls; this one drives a browser
that loads the pages the agent opens. That is why it lives in this folder, not in the package.

### Add it to an agent host

The Python that runs the server needs `viewspec` (and its `agents` extra, for `mcp`); Node needs
Playwright with Chromium. MCP hosts often start servers with a small environment. The bridge finds a
global Playwright install without `NODE_PATH`, but if Chromium is not in Playwright's default folder,
pass `PLAYWRIGHT_BROWSERS_PATH`.

Claude Code:

```bash
claude mcp add viewspec-browser \
  -e PLAYWRIGHT_BROWSERS_PATH=/path/to/pw-browsers \
  -- python /path/to/viewspec/experiments/browser-agent-probe/mcp_browser_server.py \
  --allow-origin http://localhost:3000 --allow-read "http://localhost:3000/*"
```

Or in a project's `.mcp.json`:

```json
{
  "mcpServers": {
    "viewspec-browser": {
      "command": "python",
      "args": [
        "experiments/browser-agent-probe/mcp_browser_server.py",
        "--allow-origin",
        "http://localhost:3000",
        "--allow-read",
        "http://localhost:3000/*"
      ],
      "env": { "PLAYWRIGHT_BROWSERS_PATH": "/path/to/pw-browsers" }
    }
  }
}
```

A session then looks like this:

```text
observe(url="http://localhost:3000/checkout")
  view: ... [4] button 'Increase quantity' ... form 'Checkout' POST /orders tool=checkout ...
act(handle=4, action="click")
  steps: [click 'Increase quantity' by ref]
  changes: ["changed [3] text 'Quantity': value '2' -> '3'"]
act(handle=3, action="click", wait_for={"text": "Download CSV"})
  summary: Did 1 step(s): click 'Export'. Waited 706 ms for 'Download CSV' on the page: it showed.
  changes: ["message status 'Export ready'", "added [7] link 'Download CSV'"]
call(tool="checkout", arguments={"email": "bo@example.com", "ship": "express", "terms": true},
     wait_for={"change": "navigation"})
  steps: 4  navigated: true  wait: {met: true}  changes: {new_document: true}
  blocked_writes: [POST /orders {email, ship, terms, ...}]
```

## Run it

You need the package with its `agents` extra (`pip install -e ".[agents]"`), Node, and Playwright
for Node with Chromium. The folder pins the Playwright that CI uses:

```bash
(cd experiments/browser-agent-probe && npm ci --ignore-scripts && npx playwright install chromium)
```

```bash
python experiments/browser-agent-probe/demo_agent.py
python experiments/browser-agent-probe/demo_mcp.py
python experiments/browser-agent-probe/probe.py experiments/browser-agent-probe/fixtures/*.html https://example.com --out .probe-out
python experiments/browser-agent-probe/apg_eval.py --out .apg-eval          # the W3C ARIA Authoring Practices examples
python experiments/browser-agent-probe/real_sites_eval.py --out .real-sites # the real-site tasks that found the bugs
```

```python
from agent_session import BrowserSession

with BrowserSession() as session:  # block_writes=True by default
    session.open("https://example.com/checkout")
    view = session.observe()
    print(view.view)  # [5] email 'Email' required ...
    print(view.lift.form_tool_schemas())  # one tool per form
    session.act(4, "click")
    session.call("checkout", {"email": "bo@example.com", "ship": "express", "terms": True})
    print(session.blocked_writes())  # the form post, stopped
```

## Checked in CI

`demo_agent.py` and `demo_mcp.py` check one capability at a time. `tests/test_browser_bridge_e2e.py`
checks whole tasks. Each one gives an agent a goal a person would ask for, and passes only when the
page ends up where the goal says:

| Task | It is completed when |
|---|---|
| Order three lamps with a promo code, a card, and a new password | the stopped post to `/orders` holds the order, with the password redacted |
| Place an order after a refused form call and a bad email | nothing was sent until the request was right, and then it was sent once |
| Add one product of 300 to the cart | the page says that product was added, and its button reads "Added" |
| Add two products whose buttons have the same name | the cart count is 2, and both products are named in turn |
| Open post 75 of an infinite feed | the browser is on `/post/75` |
| Find a stay, then hire a car for the same days | both stopped posts hold the dates, each as its own picker writes them |
| Apply for a job with a CV and two photos | the stopped multipart post names the three files and their sizes |
| Save an invoice and an export of the orders | the download folder holds exactly those two files, byte for byte |
| Refresh a page that redraws itself, then export | the export link shows, reached from the first reading of the page |
| Open an article from search suggestions | the browser is on that article, not the two with longer titles |
| Sort a list (the page builds it again), then add one product | the cart holds that product, though another now sits where it was |
| Sort a list (the page reuses its elements), then add one product | the cart holds that product, though its button now belongs to another |
| Sort, then go to the next page, with two "Next page" buttons | the first step is refused as ambiguous and turns no page; the second turns one |
| Save a product, then press a button whose label counts the saves | the button is still the same control after its label changed |
| Add one product when the step is sent twice | the second click, on a button that now reads "Added", is refused; the cart holds one |
| Add a product while a dialog covers the page | the view marks 19 controls blocked and names the dialog; the step is refused until it is declined |
| Check out once the cart holds something | "Checkout" and "Place order" are marked blocked and refused, then usable |
| Switch product routes while reusing the button and form (six navigation variants) | old clicks, form calls, and scrolls are refused without changing the cart or form; a fresh view acts on the current product |
| Sort a table by name, then by age | each result names the rows in their new order, though no text and no control changed |
| Save a draft twice, then copy its link | the second "Saved" is reported as shown again and meets a wait for a message; the toast is reported after it has gone |
| Look up a postcode whose answer sets two properties | the wait ends when the page answers (under 4 s of a 10 s limit), and the changes name both fields; a `#hash` link then reports the new address |
| Delete a draft, press a button that does nothing, open the help | the delete is `uncertain` / `may_repeat` with the question the page asked; the button is `uncertain` and `quiet`; the help is `succeeded` with the tab's address, and the agent is still on its page |
| Publish while the server takes 1.5 s, then publish again | the first result is `uncertain` and `busy`; a wait shows the answer; the second answer, the same word again, meets its wait only once the server has answered |
| Order through a form that posts into a new tab | the stopped post is in the result of the call itself, marked `new_tab`, with the order's fields; the page stays |
| Open a link that leads to another origin in a new tab | the navigation is stopped and listed, marked `new_tab`; the outcome is `failed`; the agent is still on its page |
| Open a page on this computer, and a file, in a session that lists no origins | each is refused, with the reason |
| Add to the cart, order, and delete the account, where one rule allows one write | the site receives the one POST the rule names, and nothing else |
| Sign in with a password the agent names and never holds | the site receives the password; no result holds it, not even when the page prints it |
| Start the server with limits it cannot keep (six cases) | it does not start, and says why |
| Place an order, then dismiss its optional receipt dialog (click/form call, confirm/prompt) | the local server receives exactly one order; the observed completion stays `succeeded` / `not_needed`, so retry advice never duplicates it |

The agent is scripted, because the test suite never calls a model, but it is held to what a model
would have. It starts `mcp_browser_server.py` over stdio as an agent host does, uses only `observe`,
`act`, and `call`, and takes every handle from the view text of a result. Each task gets a server
and a browser of its own. The site is a judge too: in the usual tasks, writes are blocked and one
origin is allowed, so a task fails if the site receives a write, or a request under another name
(`localhost` for `127.0.0.1`). The four receipt-dialog regressions explicitly allow writes to the
local fixture server and assert that it receives exactly one order. One rule is shared by every
task (`unmet`): what must hold for every tool call on the way to the outcome. Part of it is what
each result says the action did: an `act` or
`call` that ran must have the outcome `succeeded`, and a refused one `failed`, unless the task
expects another. So every task checks the outcome rule, not only the five written for it.

```bash
VIEWSPEC_RUN_BROWSER_BRIDGE_E2E=1 python -m pytest -q -m e2e tests/test_browser_bridge_e2e.py
```

Without the variable the tasks skip. CI sets it, and `tests/test_ci_config.py` checks that the job
sets the variable the suite reads, so the tasks cannot be skipped there unseen.

## Results on the fixtures

`probe.py`:

| page | aria tok | view tok | interactive | compile cov | agent cov | same name | located | confirmed | forms | fp stable | leaks |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| booking | 133 | 81 | 8 | 1/8 | 8/8 | 8/8 | 8/8 | 8/8 | 1 | True | 0 |
| catalog | 13544 | 7819 | 675 | 649/675 | 675/675 | 675/675 | 675/675 | 675/675 | 2 | True | 0 |
| checkout | 245 | 170 | 17 | 1/17 | 17/17 | 17/17 | 18/18 | 18/18 | 1 | True | 0 |
| downloads | 296 | 110 | 5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 0 | True | 0 |
| feed | 860 | 310 | 41 | 40/41 | 41/41 | 41/41 | 41/41 | 41/41 | 0 | True | 0 |
| market | 235 | 206 | 20 | 20/20 | 20/20 | 20/20 | 20/20 | 20/20 | 0 | True | 0 |
| search | 51 | 34 | 2 | 1/2 | 2/2 | 2/2 | 2/2 | 2/2 | 1 | True | 0 |
| shop | 254 | 152 | 14 | 8/14 | 14/14 | 14/14 | 14/14 | 14/14 | 1 | True | 0 |
| spa | 138 | 64 | 5 | 3/5 | 5/5 | 5/5 | 6/6 | 6/6 | 0 | True | 0 |
| stay | 141 | 120 | 9 | 3/9 | 9/9 | 9/9 | 9/9 | 9/9 | 3 | True | 0 |
| upload | 57 | 49 | 4 | 2/4 | 4/4 | 4/4 | 4/4 | 4/4 | 1 | True | 0 |
| viewspec_orders | 98 | 43 | 3 | 2/3 | 3/3 | 3/3 | 3/3 | 3/3 | 0 | True | 0 |

- **agent cov** matches by element (ref), not by name: 803 of 803 visible controls. **same name**: the
  lift names all 803 as the browser does.
- **located**: every control has a locator (the agent lift also finds 2 pointer-only clickables).
  Kinds on the four small fixtures, for 41 controls and 2 forms: 30 stable attribute, 4 content
  anchor, 4 role and name, 4 exact text, and 1 structural path (a form with no name). On the catalog:
  375 stable attribute, 300 content anchor (`li:has-text("Arc desk lamp 001") button`), and 2 role and
  name.
- **confirmed**: Playwright finds exactly the right element with every locator, including the ones the
  page checked by itself.
- **view tok** is the whole view. The MCP tools send it in slices: the first 200 lines of the catalog
  are about 1200 tokens.
- **leaks**: live secret values (password, card number, CVC) found in the snapshot, lift, view, form
  schemas, or locators: 0. The Playwright ARIA snapshot of `checkout` shows the typed password.

`demo_agent.py` (125 checks, all pass):

- **checkout:** a click inside the web component's shadow DOM changes the quantity from 2 to 3. Fills
  inside the payment iframe are verified, and the next snapshot does not contain the card number. A
  bad `checkout` call is refused with all three problems and does nothing in the browser. A good call
  runs 6 verified steps, and the blocked post holds the agent's values, the terms checkbox that sits
  outside the `<form>` element, the password (never printed), and the hidden CSRF field. A call with
  the email "bo" is refused before the browser acts ("'bo' is not an email address"); a fill of "bo"
  and a click on Place order are blocked by the browser, and the step says so: Email, "Please include
  an '@' in the email address".
- **spa:** after the page replaces every element, steps from the old view still land (by CSS and by
  name). A saved locator that is made to find another control is not followed: the click reaches the
  right one by its identity. A control whose item is made to be gone is refused as drift.
- **viewspec_orders:** the agent clicks the ViewSpec action `#refund`, found as `#dom-action_refund`.
- **changes:** after each checkout and shop action, the diff names exactly what changed: the
  quantity 2 to 3, `no change` after the card fill, the promo field added when its `<details>`
  opens, a new document after the submit, the status message and the renamed, disabled button
  after "Add to cart", and the new product after "Load more". A click on a search suggestion loads the
  results page: the step is reported as chosen and navigated, not verified, and a form call stops
  there and names the submit step that did not run.
- **waiting:** without a wait, the delayed Export seems to do nothing. A wait for "Download CSV" is
  met in about 700 ms with 2 readings, and the diff shows the message and the new link. A wait that
  is not met ends at its timeout. On the checkout promo code, a wait for a message shows "Code not
  valid", and a wait for the Apply button to be enabled again shows "Code SAVE10 applied". A wait for
  a new page after the submit is met at once.
- **choosing:** on the country combobox, whose options come 300 ms after each keystroke, "Jap"
  chooses Japan and the diff shows the new value; "S" (Spain, Sweden, Switzerland) and "Atlantis" are
  refused, and the current search stays visible without automatic rollback. The pickup city combobox filters only on key presses, as the
  W3C APG examples do; "Lis" chooses Lisbon. The colour combobox is built like react-select without
  search: a read-only input a person cannot see, the choice shown in the box beside it, and an
  announcement in a live region; its value reads "Pick a colour", a click on the box opens the list,
  "Pur" chooses Purple, and the diff shows the announcement and the new value. On the click-to-open
  gift wrap combobox, "box" chooses Box. The search fixture is built like Wikipedia's search: a plain
  search field until it has focus, then a combobox the page mounts in its place (with no id) whose
  suggestions show a title over a description. "Alan Turing" on the plain field types, follows the
  field to its replacement, chooses the article with that title (not the Institute or the law), and
  opens it; "Alan" is refused with the three titles, and the typed text is removed.
  A form call that chooses Kenya and Paper sends both.
- **scrolling:** one scroll of the feed loads 20 more posts, and the diff names the new controls; a
  scroll until "Post 75" stops once it shows; a scroll for "Post 999" stops at the end of the feed
  (100 posts, "You have reached the end"); the comments pane scrolls on its own until "Comment 35"
  shows; a scroll to the top moves the page and loads nothing.
- **dates:** "Nov 20, 2026" fills the native check-in field as 2026-11-20; a day before its `min` and
  "11/12/2026" are refused before the browser acts. The check-out calendar, opened by its button,
  picks 23 November, then moves three months ahead for 14 February; a past (disabled) day is refused
  and its calendar is closed again. The birth date calendar uses its month and year lists for
  7 March 1990. A form call fills the native date and picks both calendar dates. The delivery date
  picker is built like flatpickr, with bare-icon month arrows, a month list, and a year number input:
  it moves a month ahead for 25 December, across the year for 14 February, and refuses a past day. The
  field that nothing labels (`<p>Date: <input>`, as jQuery UI's demo has) is named "Date" by the text
  before it, which makes it a date field; its calendar, built like jQuery UI's (Prev and Next links
  named by their title, a month title with a non-breaking space), picks 3 March 2027.
- **uploads:** a PDF set on the résumé input is verified, and the diff shows `value '' -> 'cv.pdf'`;
  an `.exe` file and two files for the one-file input are refused before the browser acts. "Add
  photos" opens the chooser of a hidden input, which takes two images, and the page says "2 photos
  added"; a text file is refused by the chooser's `accept`. A form call sends the name and a CV, and
  the blocked multipart post holds the name, the CV, and both photos, as names and sizes.
- **ranges:** "Nov 20, 2026 to Nov 23, 2026" on Check-in picks both days in the shared two-month
  picker, in one verified step, and the diff shows both fields. A range from 28 December (in the second
  month shown) to 3 January picks 28 December, not 28 November. A range that ends before it starts is
  refused before the browser acts; a past start day is refused, and its calendar is closed again. The
  one-field "Rental dates" range shows `20/11/2026 – 02/12/2026`, and one date is refused for it. A
  range on the native From and To fields fills both. A form call with check-out before check-in is
  refused and names check-out; a good call picks the pair in one step, and the post holds both dates.
- **downloads:** the invoice is saved in a temporary download folder with its size, SHA-256, and type,
  and the saved bytes are the bytes the server sent. A second copy is saved as `invoice-1042 (1).pdf`,
  and the first is not overwritten. A plain link to a CSV is saved and the page stays where it was. A
  suggested name with a path (`../../.profile`) is saved inside the folder. The "Export orders" file,
  which the page builds by script, is saved with the table's rows. A plain click on "Export orders"
  names the download and saves nothing. A link that downloads nothing is reported.

`demo_mcp.py` (100 checks, all pass) runs the same kind of tasks through the MCP server, as a host
would, with `--allow-origin` set to the local fixture server and without `NODE_PATH`. It also checks
that the tool schemas carry no internal node ids, that a blocked post shows the password field as
`[redacted]` and no result contains the password or the card number, that acting from a view of a
page that has navigated away is refused, that opening another origin is refused, and that a link to
another origin leaves the agent on the page it was on. It checks `changes` in `act` and `call`
results, that `observe(since=...)` combines the changes of two actions, that `act` with `wait_for`
returns the delayed link, that a bad `wait_for` is refused before anything is done, and that an
`observe` wait that is not met still returns `ok`. On the catalog it checks that the view comes in
200-line slices, that paging a kept reading does not read the page again, that a later slice opens
with its headings, that all slices together hold each handle once, that `find="lamp 250"` returns the
product and its button (handle 534), that a click on that handle works and its changes name it, and
that a slice is at most 1000 lines. On checkout it checks that the form tool says country is
picked from a list, that `select` chooses Sweden from "Swe", that "S" is refused as ambiguous and the
field keeps Sweden, and that the gift wrap combobox chooses Paper. On the feed it checks that
`act(action="scroll")` loads more and names the new controls, that a scroll until "Post 75" works and
that post's Like button can then be clicked, that a scroll for a post that does not exist stops at the
end, that `find` reaches the comments pane past the first slice and its handle scrolls that pane, that
no scroll happens when the text already shows, and that a bad direction and a click without a handle
are refused. On the booking page it checks the date field's form tool, a date picked three months
ahead, a disabled day refused, an ambiguous date refused, and a form call that fills and picks every
date. On the upload page, with `--upload-dir` set to a temporary folder, it checks a file uploaded by
path, that `../private.txt`, an absolute path, and a missing file are refused, that a wrong type is
refused before the browser acts, two inline photos for the "Add photos" button, a text file refused by
the chooser, a form call that uploads by path, and that no result holds the bytes of a file. On the
stay page it checks that the form tools describe the pair and the one-field range, that one `act` with
`{"start", "end"}` picks both days and its changes name both fields, that a range that ends before it
starts is refused, that a range from a past day is `BROWSER_DATE_UNAVAILABLE`, and a form call that
picks a one-field range. With `--download-dir` set to a temporary folder, it checks that a download
is saved there, that the summary names the file and its size and no result holds its contents, that a
plain click that starts a download says it was not saved, that a link that downloads nothing is
`BROWSER_DOWNLOAD_NONE`, and that the folder holds only the file the agent asked for.

The fixtures are small and hand-made. Run the probe on real sites before you draw conclusions.

## Results on the W3C ARIA Authoring Practices examples

The ARIA Authoring Practices Guide publishes a reference implementation of each widget pattern:
tabs, menus and menu bars, trees and tree grids, five comboboxes, listboxes, sliders, switches,
dialogs, date pickers, disclosures, carousels, grids, and more. Its 61 example pages
(`https://www.w3.org/WAI/ARIA/apg/example-index/`) are the standard the lift is measured against.

`probe.py` on all 61 pages, 9 November 2026:

| Measure | Result |
|---|---:|
| Visible interactive elements | 3,551 |
| Found by the agent lift, by element | 3,532 (99.5%) |
| Named as the browser names them | 3,456, plus 61 that differ only in the page template's note, which the browser truncates (99.6%) |
| Locators found, and confirmed by Playwright | 3,548 of 3,548 |
| Live secret values leaked | 0 |
| Fingerprint stable across a reload | 61 of 61 pages |
| Size of the view against the ARIA snapshot | 107k tokens against 381k (28%) |

The 19 elements not found are of two kinds. Eleven are controls drawn inside inline SVG (the
seek, temperature, and two-thumb sliders; the five rating radios), which the sanitizer strips with
the SVG; the lift cannot see them. Eight are elements the probe counts and the lift drops on
purpose: the feed's focusable articles, a tree grid's row, two radio groups that hold the focus for
their radios (`aria-activedescendant`), and a hidden "reload needed" link; the controls inside each
are listed in their place.

One action on one control of each kind inside each example (`apg_eval.py`),
re-opening the page before each action: 58 of 64 actions did what they should, and the step
verified or the diff showed the change. Every tab (4), tree item (3), listbox (4), radio (3), switch
(3), menu item (2), combobox (2), text box (8), and 26 of 29 buttons. Of the six that did not: two
were clicks on a grid cell and a tab panel, which focus and change nothing; two were a checkbox and
a button inside a card collapsed with `overflow: hidden`, which the lift takes for visible and the
browser cannot click (the step waits 5 s and refuses); two changed only the order of a table's rows
or a grid's cells, which the diff could not see. The sort button is now seen through the column
header's `aria-sort`; the grid button is not. The collapsed card is `inert`, so its controls are now
marked `blocked` in the view (nine on that page), and a step on one is refused at once with that
reason instead of after 5 s.

The same run on 16 November 2026, on the bridge before and after it reported reorders and repeated
messages: 60 of 62 actions on each, with the same result for every action, and the same lines in
`changes` for 63 of the 66 controls tried. The three that differ are the cases above that the diff
could not see. The sortable table's "First Name" and the data grid's "Date" now show the rows in
their new order, beside the header's `aria-sort` (`reordered 4x <tr>: 'Sara James Cardinal, Inc.
457 First St.…', ...`). A carousel's tab now also shows the slide title that its live region held
on the way (`(shown, now gone)`).

What the run changed in the lift: `aria-labelledby` lets a referenced element contribute its own
`aria-label`; content that is `aria-hidden` gives no name, so a `title` does; a date combobox is a
date field; controls carry `pressed`, `selected`, `expanded`, and `sort`; and `fill` on a
select-only `div` combobox is refused before the browser acts.

## Known limits

- Closed shadow roots and cross-origin iframes cannot be read from the page.
- A canvas-drawn UI has no DOM to lift.
- Names follow the main rules of accessible name computation, not all of them. A field that nothing
  labels is named by the short text written beside it, which the browser's accessibility tree does
  not do; the in-page library names it the same way, so the drift guard still agrees.
- A ref lasts while its element is in the page. After a navigation, observe again.
- Top-level history and hash navigations expire observations. A page that replaces a standalone
  product without any browser navigation is still distinguished only by the control's tag, name,
  and surrounding items; the bridge does not infer application routes from arbitrary DOM changes.
- An item is told apart by one piece of text. If that text changes by itself between the view and
  the step (a countdown that comes first in a card with no heading), the step is refused as drift,
  again and again. Two items with the same text throughout cannot be told apart; a step on a control
  in one of them is refused as ambiguous once its element is gone.
- `blocked` is about what a pointer can reach. A dialog marked `aria-modal` with no backdrop leaves
  the page behind it usable by a pointer, so its controls are not marked. A fixed or sticky bar over
  a control is only found when a step scrolls the control into view.
- `choose` types the value key by key, so a list that filters as you type reacts to each key and
  may ask its server once per key. Options must have `role="option"`; a list that renders only
  the options in view (a virtual list) shows only those.
- The value of an empty combobox input is the text of the smallest box around it, which may be its
  placeholder ("Select..."). The search for that box stops at a form, a list item, a table cell,
  or a section, so a combobox placed directly in one of those shows no value until it is typed in.
- Server-side GET semantics and privacy coverage depend on the operator’s rules. See the
  [browser boundary](#browser-boundary) for the read, output, and storage contracts.
- Pages that require a service worker, a popup login, or a cross-origin embedded application
  may need a different workflow. Local same-origin and inline frames support ordinary controls.
- `find` matches the text of view lines, which shorten long text to 70 characters.
- A kept reading is a snapshot. Paging through it does not show changes made since; observe again.
- A scroll uses `scrollTo`, which fires scroll events and moves what `IntersectionObserver` sees. A page
  that loads more only on wheel or touch events needs another way.
- Text order follows the page's layout only in a browser snapshot, which marks elements whose layout
  differs from their tag's default (`data-lift-display`). `lift_html` on static HTML joins text by tag
  alone, so a span styled as a block runs into the text around it there.
- A spinner with no ARIA role is an element like any other, so a page that shows one first and adds
  its items later may report `loaded` before they arrive. A scroll that loads nothing takes the
  whole wait.
- `loaded` counts elements. A list that fills the elements it has with new text, or a virtual list that
  recycles its elements, does not count as loaded, so a scroll `until` may stop at the end of the
  viewport before the text shows.
- A form call checks a field's `pattern` only when it is short and has no quantified group
  (`(a+)+` can make Python's `re` run without bound); other patterns are left to the browser, which
  reports its refusal in the step's `invalid` list.
- A list that removes the items it scrolls past (a virtual list) changes the handles of the items
  after them; the diff shows the removed and added controls.
- `choose_date` reads month names in English. A calendar that shows only day numbers needs a month
  title or month and year lists. A calendar that shows several months needs a title for each.
- A field whose calendar is always on the page far from it (an inline picker elsewhere) is not matched
  to that calendar; the step says that no calendar opened.
- A range picker whose field says nothing about a range until one is chosen (flatpickr's
  `mode: "range"`) is read as a single date field: `select` picks one day and leaves the calendar
  waiting for the second.
- After a click that opens a calendar or moves it a month, the bridge waits for the next DOM change;
  a calendar that changed during the click itself makes it wait the whole 1.5 s. Each month moved
  costs about that.
- A pair of date fields is found only when the end field is the next date field after the start, in
  the same form. Other pairs are two fields, each with its own date.
- `choose_date_range` on one field needs a calendar that stays open after the first day. On a pair with
  two calendars, if the start field's calendar stays open after a pick, the bridge picks the end day in
  it too, and the step is not verified.
- A diff matches controls by ref, then by unique name, then by place. A control that the page
  re-rendered and also renamed and moved is reported as removed and added.
- The page's notes of its messages begin with the first reading of a document, and the last 48 are
  kept. A page that writes more than that between two readings loses the oldest.
- A page that writes the same words into its status line on every action is reported as showing
  them again every time. So is a page that takes a message region out and puts one like it back in
  two goes (after an `await`), which is a redraw and not a new message.
- What a widget says and takes back while the bridge drives it (`select` on a custom list or a
  date, `scroll`) is not reported, so a toast that answers such a step and is gone before the page
  is read is missed. A message it leaves on the page is reported.
- A reorder is found among siblings with the same tag under one parent, by their text. A card moved
  to another column, or grid cells that swap between two rows, change parents and are not reported
  as a reorder (the text of both rows changes, which the diff does show). Items that differ only in
  a picture cannot be told apart.
- Every dialog is dismissed: `confirm` answers no and `prompt` answers nothing. The agent is told
  what the page asked and cannot answer yes, so an action behind a `confirm` cannot be completed.
- A tab the page opens is reported by its address and closed. The agent cannot read it. Its
  requests are checked like the page's own, so a write from it is stopped and a navigation to
  another origin is stopped. A step waits up to 2 s for the request of a tab it opened, so that its
  own result lists what was stopped; a tab that takes longer is still stopped, and is listed in a
  later result.
- In a sealed session, a redirect to an origin that is not listed is refused by the gate. The tab
  shows the gate's own page for a moment; the bridge lists the navigation, goes back, and the page
  the agent was on is loaded again, so what was typed into it may be gone. In an open session there
  is no list: a redirect to any public address is followed.
- `succeeded` means that the bridge saw an effect, not that it was the one the agent wanted. A
  page that changes by itself (a clock, a ticker) or shows a tooltip when the pointer arrives
  counts as an answer. Pass `wait_for` to name the effect.
- `quiet` covers the time from the action to the reading, which is often under 100 ms. A handler
  that answers after a timer, with no request in between, reads as `quiet` at first. `busy` counts
  HTTP requests that began after the action; a message over a WebSocket is not one.
- `upload` sets files the way a file chooser does. A drop zone that takes files only by drag and drop,
  or a page that reads files another way, is not reached. A chooser that only a keyboard event opens
  is not either.
- Many pages send a file as soon as it is chosen, before the form is sent. With writes blocked, that
  request is stopped and listed in `blocked_writes`, so the page may show an upload error.
- With writes blocked, a download that a form post starts (an export form sent by POST) is stopped
  like any other write. A download from another origin is stopped by the origin allowlist.
- A download must start within 4 seconds of the click. A page that prepares a file for longer (a
  "your export is being prepared" message) needs a `wait_for` first, then a download of the link it
  shows.
- A wait wakes on DOM changes, navigations, and a change in what a native field holds. A custom
  widget that keeps its state only in script, and shows it on a canvas or not at all, does not wake
  it. A change in a secret field wakes the wait, and the reading that follows shows nothing, since
  the lift never holds a secret value.

## Next steps

1. Add the MCP server to an agent host, and try it on real sites.
2. Let `observe` list the days a calendar offers (the free nights of a hotel), so the agent can choose
   among them.
3. Let the agent read a downloaded text file (a CSV export) in slices, as it pages through a view.
