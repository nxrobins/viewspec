"""Build a side-by-side comparison from semantic data."""

from viewspec import ViewSpecBuilder

builder = ViewSpecBuilder("pricing_comparison")

# Same data, different motif — comparison instead of table
comparison = builder.add_comparison("plans", region="main", group_id="tiers")
comparison.add_item(label="Starter", value="$9/mo — 1 seat, 10GB storage, email support")
comparison.add_item(label="Pro", value="$29/mo — 5 seats, 100GB storage, priority support")
comparison.add_item(label="Enterprise", value="Custom — unlimited seats, unlimited storage, dedicated CSM")

# Export
path = builder.export_json("output/comparison.json")
print(f"ViewSpec exported to {path}")
print("Same data could be rendered as a table by changing the motif kind.")
