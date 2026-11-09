# Browser agent probe

An experiment. Can the ViewSpec agent lift give a browser agent a small, complete model of a live
page, and can the agent act on what it reads?

This folder is not part of the ViewSpec package and has no CI. The pieces it uses from the package
are tested there:

- `lift_html(..., profile="agent")` reads controls and forms (`tests/test_raw_html_agent_lift.py`).
- `viewspec.raw_html_actions` plans actions and form tool calls (`tests/test_raw_html_agent_actions.py`).

See [docs/local-html-wedge.md](../../docs/local-html-wedge.md#agent-lift-profile).

## The loop

```text
             observe                                act
 browser ──────────────► snapshot ──► lift ──► view, tools ──► plan ──► steps ──► browser
 (bridge.js)  composed tree   agent profile  handles [n]      checked   by ref, or by
              + live state    controls, forms                 in Python verified locator
              + data-lift-ref
```

| File | What it does |
|---|---|
| `lift_inpage.js` | In-page library. Gives every element a ref (`e17`) that stays the same while the element is in the page. Snapshots the composed tree (open shadow roots, slots, same-origin iframes) with live form state, and never copies a secret value. Lists the visible controls (the ground truth). Proposes CSS candidates for an element. |
| `bridge.js` | A Playwright session driven over stdio with JSON lines. Verifies locators, runs steps, and by default **blocks writes**: a form post or any other non-GET request is recorded and answered with a stub page. With an origin allowlist, it also stops navigations to other origins. |
| `agent_session.py` | `BrowserSession`: `open`, `observe`, `act(handle, op, value)`, `call(tool, arguments)`, `wait(condition, since, timeout_ms)`, `scroll(step, until, timeout_ms)`, `blocked_writes()`, `blocked_navigations()`. `changes(before, after)`: what changed between two observations. |
| `page_view.py` | The compact text view, with a numbered handle for each control, and slices of it (`view_page`: offset and limit, or `find`). |
| `mcp_browser_server.py` | **The MCP server**: `observe`, `act`, and `call` as MCP tools over stdio. See [MCP tools](#mcp-tools). |
| `probe.py` | Measures coverage, names, locators, size, and secret leaks on any set of pages. |
| `demo_agent.py` | A scripted agent that uses `BrowserSession` directly, with 91 checks on the fixtures. |
| `demo_mcp.py` | An MCP client that starts the server over stdio and uses only the three tools, with 92 checks. |

### Locators

At each observation, the bridge finds a locator for every control: it must match exactly one
element, and that element must be the one the ref names. It tries, in order:

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
only the rest (role and name, exact text, a path that crosses a shadow root), one by one. On the
675-control catalog this takes a reading from 9.7 s to 0.9 s. A locator is used only when a ref has
gone stale, and then Playwright must find exactly one element with the same tag and name, so a
locator that Playwright reads differently fails safely. The probe asks Playwright to confirm every
page-checked locator; on the fixtures it confirms 761 of 761.

### Acting

`act` and `call` plan in Python first (`plan_control_action`, `plan_form_call`), so a bad request fails
before the browser does anything. Then each step finds its element:

- **by ref** while the element is still in the page: the exact element the agent read;
- **by its saved locator** when the page has replaced the element (a re-render). The element that the
  locator finds must have the same tag and the same accessible name. If it does not, the step is
  refused (`drift: the locator now finds "Export", not "Refresh"`), so a changed page cannot redirect
  a click.

The bridge checks each fill, check, and select in the page and returns only true or false, so a
secret value never comes back. If a step navigates the page before the plan is done, the session
stops and says how many steps did not run.

Each document gets a random id when the in-page library is installed. A step planned from a view of
an earlier document (the page has navigated since) is refused (`stale-view`): a control with the same
name on the new page is a different control. A hash change or a `pushState` route change keeps the
document, so steps from a single-page app's earlier view still run.

### Choosing from a custom list

A custom combobox shows its options only when it is open, often only after you type, and often only
after a server answers. `act(handle, "select", "Japan")` on a custom combobox or ARIA listbox plans a
`choose` step, and the bridge:

1. clicks the option at once if the list already shows it;
2. otherwise opens the list: it types the value into an editable combobox (most filter as you type),
   or clicks a select-only one;
3. waits for the option, waking on DOM changes, up to 5 s;
4. clicks the option whose name is the value (ignoring case), or else the one option whose name starts
   with it, and checks that the combobox now shows it. `chosen` names the option.

If two or more options match, or none shows, the step is refused with the names the list showed, the
typed text is put back, and the list is closed with Escape:
`choose: more than one option matches "S": Spain, Sweden, Switzerland; the typed text was removed`.
In a form tool call, a combobox field is chosen the same way.

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
browser acts. When more than one calendar is open, the one the field names in `aria-controls`, or else
the one nearest the field, is used.

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

### Scrolling

A feed or a catalog with infinite scroll loads more items only when you scroll to its end, often
after a server call. `scroll(plan_scroll(direction, control), until=..., timeout_ms=...)`:

- scrolls the page, or with a control, the area that scrolls for it (the nearest ancestor that can
  scroll, crossing shadow roots): to the `end`, about one screen `down` or `up`, or to the `top`;
- waits after each scroll for what loads, waking on DOM changes (up to 2.5 s);
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

### Waiting

Right after an action, a page that answers after a delay (a server call, a timer) has not changed
yet, so the diff says `no change`. `wait(condition, since, timeout_ms)` waits for a change that the
agent names, with the package's `ChangeCondition`:

| `wait_for` | Waits until |
|---|---|
| `{"change": "any"}` | anything in the diff |
| `{"change": "message"}` | a new message (alert, status, live region) |
| `{"change": "navigation"}` | a new document |
| `{"text": "Order placed"}` | the text is on the page |
| `{"gone": "Loading"}` | the text is no longer on the page |
| `{"control": "Apply", "enabled": true}` | the control is on the page, and enabled (or disabled) |

It does not poll on a timer. After each reading that does not meet the condition, the bridge
watches the DOM with a `MutationObserver` on the document, every open shadow root, and every
same-origin iframe, and wakes on the first change (once the DOM is quiet for 100 ms, or 500 ms after
the first change) or on a navigation. Then the page is read again. On the fixtures, a link that
shows 600 ms after a click is found in about 700 ms with 2 readings. The longest wait is 30 s.

### Long pages

A long page gives a long view: the 300-product catalog fixture has 1308 lines and 675 controls, about
7800 tokens (its ARIA snapshot is about 13500). `view_page(lines, offset, limit, find)` returns a
slice:

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

## MCP tools

`mcp_browser_server.py` serves three tools over stdio:

| Tool | Arguments | Does |
|---|---|---|
| `observe` | `url` or `since` (both optional), `wait_for`, `timeout_ms`, `offset`, `limit`, `find`; or `observation_id` with `offset`, `limit`, `find` | Opens `url` if given, then reads the page. Returns `view` (one line per message, heading, value, table row, and control; each control has a handle such as `[7]`), `view_page` (which lines these are), `form_tools` (one typed tool per form), `observation_id`, `url`, and `title`. With `since` (an earlier `observation_id`), it also returns `changes` since that reading, for a page that updates by itself. With `observation_id`, it pages through that kept reading without reading the page again. |
| `act` | `action` (`click`, `fill`, `check`, `uncheck`, `select`, `scroll`, `upload`), `handle`, `value`, `files`, `observation_id`, `wait_for`, `timeout_ms` | Does one thing to one control, waits for `wait_for` if given, then reads the page again. `upload` gives `files` to a file input, or to the file chooser a button opens. `select` on a date range field, or on either field of a pair, takes a range and picks both days. `scroll` needs no handle: it scrolls the page, or the pane of the control with the handle; `value` is the direction, and `wait_for` means "keep scrolling until it shows". The result has `scroll`: `scrolls`, `moved`, `loaded`, `at_end`, `end_reached`, `met`. |
| `call` | `tool`, `arguments`, `submit`, `observation_id`, `wait_for`, `timeout_ms` | Fills in a form with typed arguments (files too) and submits it, waits for `wait_for` if given, then reads the page again. |

The view in every result is a slice: 200 lines by default, at most 1000 (`limit`). `view_page` gives
`offset`, `returned`, `total`, `next_offset`, and for a slice in the middle of the page, `context`.
The summary says how to get more: `Showing lines 1-200 of 1308. More: observe(observation_id=14,
offset=200).` `observe(observation_id=14, find="lamp 250")` searches that reading. A small page fits
in one slice, so nothing changes for it.

Every result uses the `viewspec mcp` result format: `ok`, `summary`, `errors` (each with `code`,
`message`, and `fix`), and `next_actions`. `act` and `call` also return `steps` (with secret values
as `[redacted]`, and `chosen` for a choice from a list), `navigated`, the new view, and `changes` (see [Changes](#changes)), so the agent
needs no extra `observe`. With `wait_for`, the result also has `wait`: what it waited for, `met`,
and `waited_ms`. A wait that is not met is not an error: the action is done, `ok` stays true, and the
summary says the change did not show, so the agent does not repeat an action that may have worked.
A `wait_for` that is not valid is refused before the browser does anything (`AGENT_WAIT_INVALID`). When writes
are blocked, `blocked_writes` lists each stopped request with its form fields; secret fields are
`[redacted]`, and a file is its name, size, and type. `blocked_navigations` lists each navigation the
origin allowlist stopped.

A request that the page cannot take is refused before the browser does anything, and the result
says so. A refused form call names each problem: `{"argument": "ship", "problem": "invalid", ...}`.
Other codes: `BROWSER_NO_PAGE`, `BROWSER_INVALID_REQUEST`, `BROWSER_HANDLE_REQUIRED`, `BROWSER_HANDLE_UNKNOWN`,
`BROWSER_OPTION_AMBIGUOUS`, `BROWSER_OPTION_NOT_FOUND`, `BROWSER_DATE_UNAVAILABLE`, `BROWSER_DATE_NOT_PICKED`,
`BROWSER_UPLOAD_NOT_ALLOWED` (a path outside the upload folder, or no folder), `BROWSER_UPLOAD_NOT_FOUND`,
`BROWSER_UPLOAD_TOO_LARGE`, `BROWSER_UPLOAD_REFUSED` (the chooser refused the files, or no chooser
opened), `BROWSER_OBSERVATION_UNKNOWN` (the server
keeps the last 8 observations), `BROWSER_OBSERVATION_STALE`, `BROWSER_TARGET_DRIFT`,
`BROWSER_TARGET_STALE`, `BROWSER_PLAN_INTERRUPTED`, `BROWSER_ORIGIN_NOT_ALLOWED`, `BROWSER_TIMEOUT`,
and `BROWSER_ERROR`.

**Safety is set by whoever starts the server, never by the agent:**

- Writes are blocked unless the server is started with `--allow-writes`. `act` and `call` then carry
  `destructiveHint: false`.
- `--allow-origin URL` (repeatable) limits the pages the agent can open, and stops a click or a
  script from taking it to another origin. The stopped navigation is answered with HTTP 204, so the
  page stays where it was.
- `--upload-dir DIR` is the only folder the agent may upload files from by path. Without it, the agent
  can send file contents only. A path that leads out of the folder (`../`, an absolute path, or a
  link) is refused.
- No result contains a secret value or the contents of a file.

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
  --allow-origin http://localhost:3000
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
        "http://localhost:3000"
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

You need the package (`pip install -e .`), Node, and Playwright for Node with Chromium.

```bash
python experiments/browser-agent-probe/demo_agent.py
python experiments/browser-agent-probe/demo_mcp.py
python experiments/browser-agent-probe/probe.py experiments/browser-agent-probe/fixtures/*.html https://example.com --out .probe-out
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

## Results on the fixtures

`probe.py`:

| page | aria tok | view tok | interactive | compile cov | agent cov | same name | located | confirmed | forms | fp stable | leaks |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| booking | 99 | 66 | 6 | 1/6 | 6/6 | 6/6 | 6/6 | 6/6 | 1 | True | 0 |
| catalog | 13544 | 7819 | 675 | 649/675 | 675/675 | 675/675 | 675/675 | 675/675 | 2 | True | 0 |
| checkout | 222 | 151 | 15 | 1/15 | 15/15 | 15/15 | 16/16 | 16/16 | 1 | True | 0 |
| feed | 860 | 310 | 41 | 40/41 | 41/41 | 41/41 | 41/41 | 41/41 | 0 | True | 0 |
| shop | 255 | 152 | 14 | 8/14 | 14/14 | 14/14 | 14/14 | 14/14 | 1 | True | 0 |
| spa | 138 | 64 | 5 | 3/5 | 5/5 | 5/5 | 6/6 | 6/6 | 0 | True | 0 |
| stay | 141 | 120 | 9 | 3/9 | 9/9 | 9/9 | 9/9 | 9/9 | 3 | True | 0 |
| upload | 57 | 49 | 4 | 2/4 | 4/4 | 4/4 | 4/4 | 4/4 | 1 | True | 0 |
| viewspec_orders | 98 | 43 | 3 | 2/3 | 3/3 | 3/3 | 3/3 | 3/3 | 0 | True | 0 |

- **agent cov** matches by element (ref), not by name: 772 of 772 visible controls. **same name**: the
  lift names all 772 as the browser does.
- **located**: every control has a locator (the agent lift also finds 2 pointer-only clickables).
  Kinds on the four small fixtures, for 39 controls and 2 forms: 28 stable attribute, 4 content
  anchor, 4 role and name, 4 exact text, and 1 structural path (a form with no name). On the catalog:
  375 stable attribute, 300 content anchor (`li:has-text("Arc desk lamp 001") button`), and 2 role and
  name.
- **confirmed**: Playwright finds exactly the right element with every locator, including the ones the
  page checked by itself.
- **view tok** is the whole view. The MCP tools send it in slices: the first 200 lines of the catalog
  are about 1200 tokens.
- **leaks**: live secret values (password, card number, CVC) found in the snapshot, lift, view, form
  schemas, or locators: 0. The Playwright ARIA snapshot of `checkout` shows the typed password.

`demo_agent.py` (91 checks, all pass):

- **checkout:** a click inside the web component's shadow DOM changes the quantity from 2 to 3. Fills
  inside the payment iframe are verified, and the next snapshot does not contain the card number. A
  bad `checkout` call is refused with all three problems and does nothing in the browser. A good call
  runs 6 verified steps, and the blocked post holds the agent's values, the terms checkbox that sits
  outside the `<form>` element, the password (never printed), and the hidden CSRF field.
- **spa:** after the page replaces every element, steps from the old view still land (by CSS and by
  role). A simulated layout change is refused by the drift guard.
- **viewspec_orders:** the agent clicks the ViewSpec action `#refund`, found as `#dom-action_refund`.
- **changes:** after each checkout and shop action, the diff names exactly what changed: the
  quantity 2 to 3, `no change` after the card fill, the promo field added when its `<details>`
  opens, a new document after the submit, the status message and the renamed, disabled button
  after "Add to cart", and the new product after "Load more".
- **waiting:** without a wait, the delayed Export seems to do nothing. A wait for "Download CSV" is
  met in about 700 ms with 2 readings, and the diff shows the message and the new link. A wait that
  is not met ends at its timeout. On the checkout promo code, a wait for a message shows "Code not
  valid", and a wait for the Apply button to be enabled again shows "Code SAVE10 applied". A wait for
  a new page after the submit is met at once.
- **choosing:** on the country combobox, whose options come 300 ms after each keystroke, "Jap"
  chooses Japan and the diff shows the new value; "S" (Spain, Sweden, Switzerland) and "Atlantis" are
  refused, and the field still holds Japan. On the click-to-open gift wrap combobox, "box" chooses Box.
  A form call that chooses Kenya and Paper sends both.
- **scrolling:** one scroll of the feed loads 20 more posts, and the diff names the new controls; a
  scroll until "Post 75" stops once it shows; a scroll for "Post 999" stops at the end of the feed
  (100 posts, "You have reached the end"); the comments pane scrolls on its own until "Comment 35"
  shows; a scroll to the top moves the page and loads nothing.
- **dates:** "Nov 20, 2026" fills the native check-in field as 2026-11-20; a day before its `min` and
  "11/12/2026" are refused before the browser acts. The check-out calendar, opened by its button,
  picks 23 November, then moves three months ahead for 14 February; a past (disabled) day is refused
  and its calendar is closed again. The birth date calendar uses its month and year lists for
  7 March 1990. A form call fills the native date and picks both calendar dates.
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

`demo_mcp.py` (92 checks, all pass) runs the same kind of tasks through the MCP server, as a host
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
picks a one-field range.

The fixtures are small and hand-made. Run the probe on real sites before you draw conclusions.

## Known limits

- Closed shadow roots and cross-origin iframes cannot be read from the page.
- A canvas-drawn UI has no DOM to lift.
- Names follow the main rules of accessible name computation, not all of them.
- A ref lasts while its element is in the page. After a navigation, observe again.
- The drift guard checks the tag and the name. Two items with the same name (two "Add to cart"
  buttons) can still swap places after a re-render; content-anchored locators make this less likely,
  not impossible.
- `choose` types with `fill`, which sends an `input` event. A combobox that reacts only to key
  presses needs a click to open instead. Options must have `role="option"`; a list that renders only
  the options in view (a virtual list) shows only those.
- `block_writes` stops HTTP requests that are not GET, HEAD, or OPTIONS. A page that changes data with
  a GET request, or over a WebSocket, is not stopped.
- The origin allowlist checks top-level navigations. Requests for scripts, images, and data from
  other origins still load, as the page needs them.
- `find` matches the text of view lines, which shorten long text to 70 characters.
- A kept reading is a snapshot. Paging through it does not show changes made since; observe again.
- A scroll uses `scrollTo`, which fires scroll events and moves what `IntersectionObserver` sees. A page
  that loads more only on wheel or touch events needs another way.
- A list that removes the items it scrolls past (a virtual list) changes the handles of the items
  after them; the diff shows the removed and added controls.
- `choose_date` reads month names in English. A calendar that shows only day numbers needs a month
  title or month and year lists. A calendar that shows several months needs a title for each.
- A pair of date fields is found only when the end field is the next date field after the start, in
  the same form. Other pairs are two fields, each with its own date.
- `choose_date_range` on one field needs a calendar that stays open after the first day. On a pair with
  two calendars, if the start field's calendar stays open after a pick, the bridge picks the end day in
  it too, and the step is not verified.
- A diff matches controls by ref, then by unique name, then by place. A control that the page
  re-rendered and also renamed and moved is reported as removed and added.
- A message that the page shows again with the same text is not reported as new.
- `upload` sets files the way a file chooser does. A drop zone that takes files only by drag and drop,
  or a page that reads files another way, is not reached. A chooser that only a keyboard event opens
  is not either.
- Many pages send a file as soon as it is chosen, before the form is sent. With writes blocked, that
  request is stopped and listed in `blocked_writes`, so the page may show an upload error.
- A wait wakes on DOM changes and navigations. A change that only sets a property (text typed into a
  field by script) does not wake it; the next DOM change or the timeout does.

## Next steps

1. Add the MCP server to an agent host, and try it on real sites.
2. Let the agent download a file a page offers, into a folder the operator names.
3. Let `observe` list the days a calendar offers (the free nights of a hotel), so the agent can choose
   among them.
