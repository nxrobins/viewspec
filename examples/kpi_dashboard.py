"""Build a KPI dashboard from semantic data."""

from viewspec import ViewSpecBuilder

builder = ViewSpecBuilder("sales_dashboard")

# Build the dashboard — cards, not divs
dashboard = builder.add_dashboard("kpis", region="main", group_id="metrics")
dashboard.add_card(label="Revenue", value="$2.4M")
dashboard.add_card(label="Customers", value="1,847")
dashboard.add_card(label="Churn", value="3.2%")
dashboard.add_card(label="MRR Growth", value="+18%")

# Style tokens — aesthetic intent, not CSS
builder.add_style("s1", "kpis_card_1_value", "emphasis.high")
builder.add_style("s2", "kpis_card_4_value", "tone.accent")

# Export
path = builder.export_json("output/dashboard.json")
print(f"ViewSpec exported to {path}")
