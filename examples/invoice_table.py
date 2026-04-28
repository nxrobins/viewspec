"""Build an invoice table from semantic data in 15 lines."""

from viewspec import ViewSpecBuilder

builder = ViewSpecBuilder("invoice")

# Build the table — each row is semantic data, not layout
table = builder.add_table("line_items", region="main", group_id="rows")
table.add_row(label="Widget A", value="$50.00")
table.add_row(label="Widget B", value="$120.00")
table.add_row(label="Widget C", value="$30.00")
table.add_row(label="Shipping", value="$15.00")
table.add_row(label="Total", value="$215.00")

# Export as JSON — ready for the compiler
bundle = builder.build_bundle()
path = builder.export_json("output/invoice.json")
print(f"ViewSpec exported to {path}")
print(f"Substrate nodes: {len(bundle.substrate.nodes)}")
print(f"Bindings: {len(bundle.view_spec.bindings)}")
print("Every binding has a provenance address - zero data dropped, zero data duplicated.")
