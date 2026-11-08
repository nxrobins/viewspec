# Browser agent probe

An experiment. It asks one question: can the ViewSpec HTML lift (`viewspec.raw_html.lift_html`)
give a browser agent a smaller and still useful model of a live page?

This folder is not part of the ViewSpec package. It makes no claims for the compiler and has no CI.

## Run it

You need the package (`pip install -e .`), Node, and Playwright for Node with Chromium.

```bash
python experiments/browser-agent-probe/probe.py \
  experiments/browser-agent-probe/fixtures/shop.html \
  experiments/browser-agent-probe/fixtures/spa.html \
  experiments/browser-agent-probe/fixtures/viewspec_orders.html \
  https://example.com \
  --out .probe-out
```

A target is a local HTML file or an `http(s)` URL. For each target the probe:

1. Renders the page in Chromium (`render_pages.js`). It records the server HTML, the rendered DOM,
   the Playwright ARIA snapshot, and each visible interactive element with its accessible name.
2. Runs the lift on the server HTML and on the rendered DOM. Then it loads the page again and lifts it
   again.
3. Builds a compact text page view from the lift: headings, numbered actions, values, and repeated
   groups.
4. Prints one table row for each page.

With `--out`, it writes `page_view.txt`, `aria_snapshot.yaml`, `lift.json`, and `report.json` for each
page, and `summary.json` for all pages.

## Columns

| Column | Meaning |
|---|---|
| html tok / aria tok / view tok | Approximate tokens (characters / 4) of the rendered DOM, the ARIA snapshot, and the page view. |
| interactive | Visible interactive elements that the browser finds (links, buttons, inputs, selects, ARIA roles, `tabindex`, `onclick`). |
| lift-role cov | How many of those elements have a match among the lift's own `action` roles. A match is by normalized name. |
| view cov | The same, for the page view. The view uses descendant text and `aria-label`, so `<a><span>Deals</span></a>` keeps its name. |
| fp stable | The topology fingerprint is the same after a reload. |
| static=rendered | The fingerprint of the server HTML equals the fingerprint of the rendered DOM. `False` means JavaScript built the page. |

## First results (fixtures only)

| page | html tok | aria tok | view tok | interactive | lift-role cov | view cov | fp stable | static=rendered |
|---|---:|---:|---:|---:|---:|---:|---|---|
| shop | 285 | 232 | 98 | 13 | 7/13 | 9/13 | True | True |
| spa | 367 | 127 | 42 | 5 | 3/5 | 3/5 | True | False |
| viewspec_orders | 3828 | 98 | 34 | 3 | 2/3 | 2/3 | True | True |

The fixtures are small, so the token numbers do not predict real sites. Run the probe on real pages
before you decide anything. What the fixtures show:

- **Form controls are lost.** The sanitizer removes `input`, `select`, and `textarea`, and it unwraps
  `form` and `label`. No page view includes a text box, a select, or a checkbox. This is the largest
  gap for an agent that must fill in forms.
- **Script-made controls are lost.** A `div` with `tabindex` or `role="button"` is not an action in the
  lift. (The ARIA snapshot also shows the `tabindex` div only as text.)
- **The lift's own action roles use direct text only.** A link whose text is in a child `span`, or whose
  name is only `aria-label`, has no `action` role. The page view fixes this with descendant text.
- **Rendering is required.** On `spa`, the server HTML has no content, so the lift must run on the
  rendered DOM.
- **The fingerprint ignores text.** `spa` shows a new timestamp on each load, but the fingerprint stays
  the same. This makes it useful to see "same page shape" for plan caching.
- **ViewSpec pages are already agent-ready.** `viewspec_orders` has `data-action-id` and
  `data-action-kind` on each action. An agent can read these directly. It needs no guessing.

## Next steps, if the results on real pages are good

1. Lift a browser-side DOM walk instead of the sanitized HTML string. Keep inputs, selects, labels, ARIA
   roles, visibility, and enabled state.
2. Give each action a stable handle that maps back to a selector or a `data-viewspec-node-id`.
3. Show table rows and list items as rows, not only as values.
4. Measure task success, not only size, against an ARIA snapshot baseline.

`fixtures/viewspec_orders.html` is compiled from `fixtures/viewspec_orders.intent.json` with
`viewspec compile`.
