"""Load a compiled ASTBundle from JSON and emit HTML + Tailwind."""

import json
import sys
from pathlib import Path

from viewspec import ASTBundle
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter

if len(sys.argv) < 2:
    print("Usage: python emit_html.py <ast_bundle.json> [output_dir]")
    sys.exit(1)

input_path = Path(sys.argv[1])
output_dir = sys.argv[2] if len(sys.argv) > 2 else "viewspec_output"

# Load the compiled AST bundle
ast_bundle = ASTBundle.from_json(json.loads(input_path.read_text()))

# Emit HTML + Tailwind with full provenance
emitter = HtmlTailwindEmitter()
paths = emitter.emit(ast_bundle, output_dir)

print("Emitted artifacts:")
for kind, path in paths.items():
    print(f"  {kind}: {path}")
print()
print("Every DOM element carries data-ir-id, data-content-refs, and data-intent-refs.")
print("Click any element → trace it back to the exact semantic address that produced it.")
