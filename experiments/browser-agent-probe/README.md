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
| `agent_session.py` | `BrowserSession`: `open`, `observe`, `act(handle, op, value)`, `call(tool, arguments)`, `wait(condition, since, timeout_ms)`, `blocked_writes()`, `blocked_navigations()`. `changes(before, after)`: what changed between two observations. |
| `page_view.py` | The compact text view, with a numbered handle for each control, and slices of it (`view_page`: offset and limit, or `find`). |
| `mcp_browser_server.py` | **The MCP server**: `observe`, `act`, and `call` as MCP tools over stdio. See [MCP tools](#mcp-tools). |
| `probe.py` | Measures coverage, names, locators, size, and secret leaks on any set of pages. |
| `demo_agent.py` | A scripted agent that uses `BrowserSession` directly, with 39 checks on the fixtures. |
| `demo_mcp.py` | An MCP client that starts the server over stdio and uses only the three tools, with 49 checks. |

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
page-checked locator; on the fixtures it confirms 713 of 713.

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
| `act` | `handle`, `action` (`click`, `fill`, `check`, `uncheck`, `select`), `value`, `observation_id`, `wait_for`, `timeout_ms` | Does one thing to one control, waits for `wait_for` if given, then reads the page again. |
| `call` | `tool`, `arguments`, `submit`, `observation_id`, `wait_for`, `timeout_ms` | Fills in a form with typed arguments and submits it, waits for `wait_for` if given, then reads the page again. |

The view in every result is a slice: 200 lines by default, at most 1000 (`limit`). `view_page` gives
`offset`, `returned`, `total`, `next_offset`, and for a slice in the middle of the page, `context`.
The summary says how to get more: `Showing lines 1-200 of 1308. More: observe(observation_id=14,
offset=200).` `observe(observation_id=14, find="lamp 250")` searches that reading. A small page fits
in one slice, so nothing changes for it.

Every result uses the `viewspec mcp` result format: `ok`, `summary`, `errors` (each with `code`,
`message`, and `fix`), and `next_actions`. `act` and `call` also return `steps` (with secret values
as `[redacted]`), `navigated`, the new view, and `changes` (see [Changes](#changes)), so the agent
needs no extra `observe`. With `wait_for`, the result also has `wait`: what it waited for, `met`,
and `waited_ms`. A wait that is not met is not an error: the action is done, `ok` stays true, and the
summary says the change did not show, so the agent does not repeat an action that may have worked.
A `wait_for` that is not valid is refused before the browser does anything (`AGENT_WAIT_INVALID`). When writes
are blocked, `blocked_writes` lists each stopped request with its form fields; secret fields are
`[redacted]`. `blocked_navigations` lists each navigation the origin allowlist stopped.

A request that the page cannot take is refused before the browser does anything, and the result
says so. A refused form call names each problem: `{"argument": "ship", "problem": "invalid", ...}`.
Other codes: `BROWSER_NO_PAGE`, `BROWSER_INVALID_REQUEST`, `BROWSER_HANDLE_UNKNOWN`, `BROWSER_OBSERVATION_UNKNOWN` (the server
keeps the last 8 observations), `BROWSER_OBSERVATION_STALE`, `BROWSER_TARGET_DRIFT`,
`BROWSER_TARGET_STALE`, `BROWSER_PLAN_INTERRUPTED`, `BROWSER_ORIGIN_NOT_ALLOWED`, `BROWSER_TIMEOUT`,
and `BROWSER_ERROR`.

**Safety is set by whoever starts the server, never by the agent:**

- Writes are blocked unless the server is started with `--allow-writes`. `act` and `call` then carry
  `destructiveHint: false`.
- `--allow-origin URL` (repeatable) limits the pages the agent can open, and stops a click or a
  script from taking it to another origin. The stopped navigation is answered with HTTP 204, so the
  page stays where it was.
- No result contains a secret value.

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
| catalog | 13544 | 7824 | 675 | 649/675 | 675/675 | 675/675 | 675/675 | 675/675 | 2 | True | 0 |
| checkout | 208 | 142 | 14 | 1/14 | 14/14 | 14/14 | 15/15 | 15/15 | 1 | True | 0 |
| shop | 255 | 152 | 14 | 8/14 | 14/14 | 14/14 | 14/14 | 14/14 | 1 | True | 0 |
| spa | 138 | 64 | 5 | 3/5 | 5/5 | 5/5 | 6/6 | 6/6 | 0 | True | 0 |
| viewspec_orders | 98 | 43 | 3 | 2/3 | 3/3 | 3/3 | 3/3 | 3/3 | 0 | True | 0 |

- **agent cov** matches by element (ref), not by name: 711 of 711 visible controls. **same name**: the
  lift names all 711 as the browser does.
- **located**: every control has a locator (the agent lift also finds 2 pointer-only clickables).
  Kinds on the four small fixtures, for 38 controls and 2 forms: 27 stable attribute, 4 content
  anchor, 4 role and name, 4 exact text, and 1 structural path (a form with no name). On the catalog:
  375 stable attribute, 300 content anchor (`li:has-text("Arc desk lamp 001") button`), and 2 role and
  name.
- **confirmed**: Playwright finds exactly the right element with every locator, including the ones the
  page checked by itself.
- **view tok** is the whole view. The MCP tools send it in slices: the first 200 lines of the catalog
  are about 1200 tokens.
- **leaks**: live secret values (password, card number, CVC) found in the snapshot, lift, view, form
  schemas, or locators: 0. The Playwright ARIA snapshot of `checkout` shows the typed password.

`demo_agent.py` (39 checks, all pass):

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

`demo_mcp.py` (49 checks, all pass) runs the same kind of tasks through the MCP server, as a host
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
that a slice is at most 1000 lines.

The fixtures are small and hand-made. Run the probe on real sites before you draw conclusions.

## Known limits

- Closed shadow roots and cross-origin iframes cannot be read from the page.
- A canvas-drawn UI has no DOM to lift.
- Names follow the main rules of accessible name computation, not all of them.
- A ref lasts while its element is in the page. After a navigation, observe again.
- The drift guard checks the tag and the name. Two items with the same name (two "Add to cart"
  buttons) can still swap places after a re-render; content-anchored locators make this less likely,
  not impossible.
- A custom ARIA combobox is filled as text. Choosing from its popup list takes `click` steps.
- `block_writes` stops HTTP requests that are not GET, HEAD, or OPTIONS. A page that changes data with
  a GET request, or over a WebSocket, is not stopped.
- The origin allowlist checks top-level navigations. Requests for scripts, images, and data from
  other origins still load, as the page needs them.
- `find` matches the text of view lines, which shorten long text to 70 characters.
- A kept reading is a snapshot. Paging through it does not show changes made since; observe again.
- A diff matches controls by ref, then by unique name, then by place. A control that the page
  re-rendered and also renamed and moved is reported as removed and added.
- A message that the page shows again with the same text is not reported as new.
- A wait wakes on DOM changes and navigations. A change that only sets a property (text typed into a
  field by script) does not wake it; the next DOM change or the timeout does.

## Next steps

1. Add the MCP server to an agent host, and try it on real sites.
2. Let an agent choose an option from a custom ARIA combobox's pop-up list in one step.
3. Let `act` scroll a page that loads more items as it scrolls (infinite scroll).
