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
| `agent_session.py` | `BrowserSession`: `open`, `observe`, `act(handle, op, value)`, `call(tool, arguments)`, `blocked_writes()`, `blocked_navigations()`. |
| `page_view.py` | The compact text view, with a numbered handle for each control. |
| `mcp_browser_server.py` | **The MCP server**: `observe`, `act`, and `call` as MCP tools over stdio. See [MCP tools](#mcp-tools). |
| `probe.py` | Measures coverage, names, locators, size, and secret leaks on any set of pages. |
| `demo_agent.py` | A scripted agent that uses `BrowserSession` directly, with 20 checks on the fixtures. |
| `demo_mcp.py` | An MCP client that starts the server over stdio and uses only the three tools, with 23 checks. |

### Locators

At each observation, the bridge finds a locator for every control and checks it with Playwright:
it must match exactly one element, and that element must be the one the ref names. It tries, in
order:

1. a stable attribute: `#id` (not a generated-looking id), `data-testid` and its kin,
   `data-action-id`, `name` (with `value` for a radio), `aria-label`, `title`, `placeholder`, `href`;
2. a content anchor for repeated items: `li:has-text("Arc lamp") button`;
3. role and name: `getByRole("button", { name: "Refresh", exact: true })`;
4. exact text, for a clickable element with no role: `getByText("Export", { exact: true })`;
5. a structural path, as the last resort.

Each part runs inside its frame chain, for example
`page.frameLocator("iframe[title=\"Card details\"]").locator("input[name=\"card\"]")`. Playwright CSS
crosses open shadow roots, so a control in a web component needs no special path.

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

## MCP tools

`mcp_browser_server.py` serves three tools over stdio:

| Tool | Arguments | Does |
|---|---|---|
| `observe` | `url` (optional) | Opens `url` if given, then reads the page. Returns `view` (one line per heading, value, table row, and control; each control has a handle such as `[7]`), `form_tools` (one typed tool per form), `observation_id`, `url`, and `title`. |
| `act` | `handle`, `action` (`click`, `fill`, `check`, `uncheck`, `select`), `value`, `observation_id` | Does one thing to one control, then reads the page again. |
| `call` | `tool`, `arguments`, `submit`, `observation_id` | Fills in a form with typed arguments and submits it, then reads the page again. |

Every result uses the `viewspec mcp` result format: `ok`, `summary`, `errors` (each with `code`,
`message`, and `fix`), and `next_actions`. `act` and `call` also return `steps` (with secret values
as `[redacted]`), `navigated`, and the new view, so the agent needs no extra `observe`. When writes
are blocked, `blocked_writes` lists each stopped request with its form fields; secret fields are
`[redacted]`. `blocked_navigations` lists each navigation the origin allowlist stopped.

A request that the page cannot take is refused before the browser does anything, and the result
says so. A refused form call names each problem: `{"argument": "ship", "problem": "invalid", ...}`.
Other codes: `BROWSER_NO_PAGE`, `BROWSER_HANDLE_UNKNOWN`, `BROWSER_OBSERVATION_UNKNOWN` (the server
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
  steps: [click 'Increase quantity' by ref]  view: ... [3] text 'Quantity' = '3' ...
call(tool="checkout", arguments={"email": "bo@example.com", "ship": "express", "terms": true})
  steps: 4  navigated: true  blocked_writes: [POST /orders {email, ship, terms, ...}]
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

| page | aria tok | view tok | interactive | compile cov | agent cov | same name | located | forms | fp stable | leaks |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| checkout | 208 | 142 | 14 | 1/14 | 14/14 | 14/14 | 15/15 | 1 | True | 0 |
| shop | 248 | 150 | 14 | 8/14 | 14/14 | 14/14 | 14/14 | 1 | True | 0 |
| spa | 135 | 64 | 5 | 3/5 | 5/5 | 5/5 | 6/6 | 0 | True | 0 |
| viewspec_orders | 98 | 43 | 3 | 2/3 | 3/3 | 3/3 | 3/3 | 0 | True | 0 |

- **agent cov** matches by element (ref), not by name: 36 of 36 visible controls. **same name**: the
  lift names all 36 as the browser does.
- **located**: 38 of 38 controls have a verified locator (the agent lift also finds 2 pointer-only
  clickables). Kinds, for the 38 controls and the 2 forms: 27 stable attribute, 4 content anchor,
  4 role and name, 4 exact text, and 1 structural path (a form with no name).
- **leaks**: live secret values (password, card number, CVC) found in the snapshot, lift, view, form
  schemas, or locators: 0. The Playwright ARIA snapshot of `checkout` shows the typed password.

`demo_agent.py` (20 checks, all pass):

- **checkout:** a click inside the web component's shadow DOM changes the quantity from 2 to 3. Fills
  inside the payment iframe are verified, and the next snapshot does not contain the card number. A
  bad `checkout` call is refused with all three problems and does nothing in the browser. A good call
  runs 6 verified steps, and the blocked post holds the agent's values, the terms checkbox that sits
  outside the `<form>` element, the password (never printed), and the hidden CSRF field.
- **spa:** after the page replaces every element, steps from the old view still land (by CSS and by
  role). A simulated layout change is refused by the drift guard.
- **viewspec_orders:** the agent clicks the ViewSpec action `#refund`, found as `#dom-action_refund`.

`demo_mcp.py` (23 checks, all pass) runs the same kind of tasks through the MCP server, as a host
would, with `--allow-origin` set to the local fixture server and without `NODE_PATH`. It also checks
that the tool schemas carry no internal node ids, that a blocked post shows the password field as
`[redacted]` and no result contains the password or the card number, that acting from a view of a
page that has navigated away is refused, that opening another origin is refused, and that a link to
another origin leaves the agent on the page it was on.

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
- The view is not cut to a size limit. A very large page gives a long view.

## Next steps

1. Add the MCP server to an agent host, and try it on real sites.
2. Diff two observations after an action, so the agent sees what changed.
3. Let `observe` page through a long view.
