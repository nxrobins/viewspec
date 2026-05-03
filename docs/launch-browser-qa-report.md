# Launch Browser QA Report

Baseline commits:

- `viewspec-api`: `f4bc310`
- `viewspec`: `5ee8e4b`

Checked on May 1, 2026 with headless Chrome/CDP against `https://viewspec.dev`.

## Workflow Status

- Public SDK Reliability: success.
- Public GitHub Pages deploy: success.
- API Fly deploy: success.

## Browser Coverage

- Landing page renders `ViewSpec - Agent-Native UI IR`, exposes the three launch demo cards, and resolves pricing CTA links.
- `/cross-platform-dashboard/` renders the Launch Operations Dashboard page and all generated source/provenance links.
- `/custom-motifs/` renders the MDL custom motif walkthrough and launch tiering copy.
- `/interactive-compose/` renders the review form and compiled payload preview.
- Generated HTML artifact renders the compiled dashboard with status table, KPIs, inputs, and submit action.

## Interaction Checks

- Selecting `Mobile` in the interactive compose phase filter displays the mobile handoff note.
- The compiled payload preview updates to include `phase_filter: "Mobile"`, `owner_email`, `include_mobile`, `launch_status`, and `kpi_blockers_value`.

## Link And Commerce Checks

- Launch artifact links returned `200`: prompt, IntentBundle, ASTBundle, React TSX, SwiftUI, Flutter.
- Pro checkout route returned `200`: `https://buy.stripe.com/7sY00i9v67cJebDd1K1oI00`.
- Enterprise fallback route returned `200`: `https://github.com/nxrobins/viewspec/issues`.
