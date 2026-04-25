"""
Build the ViewSpec landing page using ViewSpec itself.

The landing page is defined as a semantic substrate, compiled through the
reference compiler, and emitted as HTML. The page IS the demo.
"""

from viewspec import ViewSpecBuilder, compile
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
import json

# ---------------------------------------------------------------------------
# Define the landing page as semantic data
# ---------------------------------------------------------------------------

builder = ViewSpecBuilder("viewspec_landing")

# -- Hero section: dashboard motif (label-value pairs as feature cards) --
hero = builder.add_dashboard("hero_features", region="main", group_id="features")
hero.add_card(label="Compilation", value="3ms deterministic — no LLM, no GPU")
hero.add_card(label="Provenance", value="Every pixel traces to its source data")
hero.add_card(label="Invariants", value="Exactly-once · Semantic grouping · Strict order")

# -- Demo cards: table motif --
demos = builder.add_table("demo_index", region="main", group_id="demos")
demos.add_row(label="Same Data, Three Motifs", value="One dataset, three visual structures. Change one parameter.")
demos.add_row(label="Provenance Inspector", value="Hover any element. Trace DOM → IR → binding → address → raw data.")
demos.add_row(label="Live Builder", value="Browse ViewSpec JSON, IR tree, and rendered output. Always in sync.")
demos.add_row(label="The Invariants", value="Watch the compiler enforce — and refuse — each guarantee.")
demos.add_row(label="15 Lines → Full UI", value="An invoice table builds itself from 15 lines of Python.")

# -- How it works: comparison motif --
how = builder.add_comparison("pipeline", region="main", group_id="steps")
how.add_item(label="Describe", value="Semantic substrate + ViewSpec. What the data means, not how it looks.")
how.add_item(label="Compile", value="Deterministic routing with provenance guarantees. 3ms. No LLM.")
how.add_item(label="Render", value="Pluggable emitters. HTML today. Canvas tomorrow. Your renderer next.")

# -- Styles --
builder.add_style("s_hero_emphasis", "hero_features", "emphasis.high")
builder.add_style("s_pipeline_subtle", "pipeline", "surface.subtle")

# ---------------------------------------------------------------------------
# Compile
# ---------------------------------------------------------------------------

bundle = builder.build_bundle()
ast = compile(bundle)

print(f"Diagnostics: {len(ast.result.diagnostics)}")
for d in ast.result.diagnostics:
    print(f"  [{d.severity}] {d.code}: {d.message}")

# ---------------------------------------------------------------------------
# Emit the raw compiled HTML (the ViewSpec output)
# ---------------------------------------------------------------------------

paths = HtmlTailwindEmitter().emit(ast, "landing-compiled")
print(f"Compiled HTML: {paths['html']}")

# ---------------------------------------------------------------------------
# Also dump the IntentBundle JSON for the live-builder demo
# ---------------------------------------------------------------------------

with open("landing-compiled/intent_bundle.json", "w") as f:
    json.dump(bundle.to_json(), f, indent=2)
print("Intent bundle: landing-compiled/intent_bundle.json")

# ---------------------------------------------------------------------------
# Print the IR tree summary
# ---------------------------------------------------------------------------

def _print_tree(node, indent=0):
    prefix = "  " * indent
    refs = len(node.provenance.content_refs) + len(node.provenance.intent_refs)
    style = f" [{', '.join(node.style_tokens)}]" if node.style_tokens else ""
    text = node.props.get("text", "")
    label = f' "{text}"' if text else ""
    print(f"{prefix}{node.primitive} ({node.id}) refs={refs}{style}{label}")
    for child in node.children:
        _print_tree(child, indent + 1)

print("\nIR Tree:")
_print_tree(ast.result.root.root)
