# Demo: The Invariants

## What It Proves
ViewSpec doesn't just produce correct UI — it refuses to produce incorrect UI. The three invariants (exactly-once provenance, semantic grouping, strict ordering) are enforced, visible, and unbreakable.

## Behavior

Three sections, each demonstrating one invariant being enforced. Each section has:
- A title and one-sentence explanation
- A "Valid" example (green border, renders correctly)
- A "Violation" example (red border, shows the CompilerDiagnostic)
- A toggle to flip between them

### Section 1: Exactly-Once Provenance

**Valid:** A table with 4 rows. Each binding routes exactly once. All data accounted for. A counter shows "4/4 bindings routed."

**Violation:** Same table but one binding is duplicated (appears in two rows). The compiler catches it: `CompilerDiagnostic { severity: "error", code: "DUPLICATE_ROUTING", message: "Binding 'row_2_value' routed to multiple IR nodes" }`. The duplicate element renders with a red dashed border and an error badge.

### Section 2: Semantic Grouping

**Valid:** A dashboard with 3 cards. Each card's label and value are grouped by semantic boundary (same node). Groups are visually enclosed in surfaces.

**Violation:** A label from card 1 is grouped with the value from card 2 (cross-group contamination). Diagnostic: `{ code: "GROUP_VIOLATION", message: "Binding 'card_1_label' grouped with non-sibling 'card_2_value'" }`. The mismatched elements highlight with connecting red lines.

### Section 3: Strict Ordering

**Valid:** A table where rows appear in the order they were declared in the substrate. Row 1 → Row 2 → Row 3 → Row 4.

**Violation:** Rows reordered (Row 1 → Row 3 → Row 2 → Row 4). Diagnostic: `{ code: "ORDER_VIOLATION", message: "Binding 'row_3_label' precedes 'row_2_label' but was declared after it in the substrate" }`. The out-of-order rows get numbered badges showing declared order vs rendered order.

## Implementation

### Pre-generation
Write a Python script (`demos/build_invariants.py`) that:
1. For each invariant, builds a valid IntentBundle and a deliberately broken one
2. Hand-builds correct IR trees for valid cases
3. Hand-builds broken IR trees with `CompilerDiagnostic` entries for violation cases
4. Emits both via `HtmlTailwindEmitter`
5. Wraps in `index.html` with toggle JS

### HTML Structure
```html
<section class="invariant">
  <h2>Exactly-Once Provenance</h2>
  <p>Every data binding is routed exactly once. Nothing dropped. Nothing duplicated.</p>
  
  <div class="toggle-group">
    <button data-state="valid" class="active">✓ Valid</button>
    <button data-state="violation">✗ Violation</button>
  </div>
  
  <div class="demo-container">
    <div class="state-valid active">
      <!-- valid rendered HTML with green border -->
      <div class="status-bar valid">✓ 4/4 bindings routed exactly once</div>
    </div>
    <div class="state-violation">
      <!-- violation rendered HTML with red border -->
      <div class="diagnostic">
        <span class="severity error">ERROR</span>
        <span class="code">DUPLICATE_ROUTING</span>
        <span class="message">Binding 'row_2_value' routed to multiple IR nodes</span>
      </div>
    </div>
  </div>
</section>
```

### Styling
- Valid state: thin green-500 left border on the container
- Violation state: thin red-500 left border, diagnostic card below with monospace text
- Error elements within violations: red dashed border, semi-transparent red overlay
- Status bar: full-width bar below the rendered output showing pass/fail
- Diagnostic cards styled like compiler output (dark bg, monospace, severity badge)
- Subtle shake animation on the violation elements when toggled to (150ms, 2px)

### JS (~30 lines)
Per-section toggle between valid and violation states.

## Output
`demos/invariants/index.html` — single self-contained HTML file.

## Quality Bar
- The violations must look genuinely broken, not just annotated
- The diagnostics must read like real compiler output
- The valid examples must feel solid and trustworthy
- The contrast between valid and violation should be visceral
- Someone watching should think "I want my UI to have this"
