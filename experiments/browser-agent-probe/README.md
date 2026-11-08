# Browser agent probe

An experiment. It asks one question: can the ViewSpec HTML lift give a browser agent a smaller and
still complete model of a live page?

The probe is not part of the ViewSpec package and has no CI. The lift profile it uses,
`lift_html(..., profile="agent")`, is part of the package and is tested in
`tests/test_raw_html_agent_lift.py`. See [docs/local-html-wedge.md](../../docs/local-html-wedge.md#agent-lift-profile).

## Run it

You need the package (`pip install -e .`), Node, and Playwright for Node with Chromium.

```bash
python experiments/browser-agent-probe/probe.py \
  experiments/browser-agent-probe/fixtures/*.html \
  https://example.com \
  --out .probe-out
```

A target is a local HTML file or an `http(s)` URL. For each target the probe:

1. Renders the page in Chromium. `render_pages.js` walks the **composed tree**: open shadow roots
   in place of their hosts, slots filled with their assigned nodes, and same-origin iframes inline.
   It copies that tree with the **live form state** in attributes, marks subtrees the user cannot
   see (`data-lift-hidden`) and elements that only look clickable (`data-lift-clickable`), and never
   copies a secret value. The same walk lists each visible interactive element with its accessible
   name; this list is the ground truth.
2. Lifts the snapshot with the `"agent"` profile, and with the `"compile"` profile as a baseline.
3. Builds a compact page view and a tool schema for each form.
4. Checks that no live secret value (password, card number, CVC) appears in any output.

With `--out`, it writes `page_view.txt`, `form_tools.json`, `aria_snapshot.yaml`, `lift.json`, and
`report.json` for each page, and `summary.json` for all pages.

## Columns

| Column | Meaning |
|---|---|
| html / aria / view tok | Approximate tokens (characters / 4) of the snapshot, the Playwright ARIA snapshot, and the page view. |
| interactive | Visible interactive elements in the ground truth. |
| compile cov | How many of those have an `action` role in a `"compile"` profile lift. A match is by normalized name. |
| agent cov | How many of those are among the `"agent"` profile controls. |
| forms | Forms found. Each one has a tool schema in `form_tools.json`. |
| fp stable | The topology fingerprint is the same after a reload. |
| static=rendered | The fingerprint of the server HTML equals that of the rendered page. `False` means script built or changed the page. |

## Results on the fixtures

| page | html tok | aria tok | view tok | interactive | compile cov | agent cov | forms | fp stable | static=rendered |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| shop | 289 | 232 | 133 | 13 | 7/13 | 13/13 | 1 | True | True |
| spa | 211 | 132 | 64 | 5 | 3/5 | 5/5 | 0 | True | False |
| viewspec_orders | 1539 | 98 | 43 | 3 | 2/3 | 3/3 | 0 | True | True |
| checkout | 573 | 208 | 133 | 14 | 1/14 | 14/14 | 1 | True | False |

`checkout.html` is the hard case. It has a web component with its controls in a shadow root and a
slot; a same-origin payment iframe with a card number and CVC; an ARIA radio group and combobox; a
checkbox that joins the form through `form="checkout"`; a closed `<details>` and a closed `<dialog>`;
a hidden CSRF input; and typed values, including a password. Its page view:

```text
[1] link 'Lamp Co home' -> /
H1 Checkout
H2 Arc lamp
[2] button 'Decrease quantity'
[3] text 'Quantity' = '2'
[4] button 'Increase quantity'
form 'Checkout' POST /orders
[5] email 'Email' = 'ana@example.com' required
[6] radio 'Standard (free)' [x]
[7] radio 'Express ($9)' [ ]
[8] combobox 'Country'
[9] password 'Create a password'
[10] disclosure 'Have a promo code?'
[11] button 'Place order' (submits)
[12] checkbox 'I accept the terms' [ ] required
[13] text 'Card number'
[14] text 'CVC'
[15] clickable 'Save cart for later'
```

and its form becomes a tool with `email` (required), `ship` (`standard` | `express`, described as
"Shipping speed"), `country`, `new_password` (write-only), and `terms` (boolean, required).

What the fixtures show:

- **The agent profile finds every control.** 35 of 35 across the four pages; the compile profile
  finds 13. It reaches controls in shadow DOM and in the iframe, reads typed values, and leaves out
  the closed dialog, the promo field inside the closed `<details>`, and a `display: none` panel.
- **No secret leaks from the lift.** The live password, card number, and CVC appear in none of the
  snapshot, lift, page view, or form schemas, and the hidden CSRF value is dropped.
- **The Playwright ARIA snapshot does leak.** It prints the typed password as
  `textbox "Create a password": <value>`. It also does not enter the iframe, so it has no card fields.
- **The page view is smaller than the ARIA snapshot** on these pages (133 against 208 tokens on
  `checkout`) and says more: field state, required flags, form membership, submit buttons, and
  ViewSpec action ids (`#refund`).
- **The fingerprint ignores text.** A changing timestamp or typed value does not change it, so it can
  tell an agent "this is the same kind of page as before".

The fixtures are small and hand-made. Run the probe on real sites before you draw conclusions.

## Known limits

- Closed shadow roots and cross-origin iframes cannot be read from the page.
- A canvas-drawn UI has no DOM to lift.
- Names follow the main rules of accessible name computation, not all of them.
- Node ids come from the tree path. A page that inserts nodes above a control changes that
  control's id. Use the fingerprint and the name to find it again.

## Next steps

1. Run the probe on real sites, and measure task success (not only size) against the ARIA snapshot.
2. Map each node id back to a selector, so an agent can act on what it reads.
3. Diff two lifts after an action to show the agent what changed.

`fixtures/viewspec_orders.html` is compiled from `fixtures/viewspec_orders.intent.json` with
`viewspec compile`.
