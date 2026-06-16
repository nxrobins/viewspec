"""Build the Same Intent, Five Art Directions demo page."""

from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
from typing import Any

from seo_metadata import demo_head_metadata
from viewspec import AESTHETIC_PROFILE_TOKENS, IntentBundle, ViewSpecBuilder, compile, profile_style_facts
from viewspec.emitters.html_tailwind import OFFLINE_EMITTER_CSS, _render_node
from viewspec.types import ASTBundle, DEFAULT_STYLE_TOKEN_VALUES

ROOT = Path(__file__).resolve().parents[1]

PROFILE_LABELS = {
    "aesthetic.calm_ops": "Calm Ops",
    "aesthetic.premium_saas": "Premium SaaS",
    "aesthetic.data_dense": "Data Dense",
    "aesthetic.editorial_product": "Editorial Product",
    "aesthetic.executive_review": "Executive Review",
}

PROFILE_NOTES = {
    "aesthetic.calm_ops": "Quiet operational surfaces with teal accents and low-contrast panels.",
    "aesthetic.premium_saas": "Polished product rhythm with stronger hierarchy and softer shadows.",
    "aesthetic.data_dense": "Compact spacing, smaller type, and tighter controls for repeated scanning.",
    "aesthetic.editorial_product": "Warmer product-story pacing with broader prose rhythm.",
    "aesthetic.executive_review": "Conservative review surfaces with restrained contrast and crisp framing.",
}

LAYOUT_PROOF_ROLES = ("content_grid", "metric_grid")
OPTIONAL_LAYOUT_PROOF_ROLES = ("metric_card",)
LAYOUT_PROOF_LABELS = {
    "content_grid": "workspace",
    "metric_grid": "metrics",
    "metric_card": "featured metric",
}


def build_bundle(profile: str) -> IntentBundle:
    builder = ViewSpecBuilder(
        "art_direction_workspace",
        root_attrs={"title": "Launch quality workspace"},
        default_main_region=False,
        root_min_children=2,
    )
    builder.set_aesthetic_profile(profile)
    builder.add_region("hero", parent_region="root", role="banner", layout="stack", min_children=1)
    builder.add_region("workspace", parent_region="root", role="application", layout="grid", min_children=2)
    builder.add_region("primary", parent_region="workspace", role="primary", layout="stack", min_children=1)
    builder.add_region("review", parent_region="workspace", role="complementary", layout="stack", min_children=1)

    builder.add_hero(
        "overview",
        eyebrow="Launch desk",
        title="Resolve the next release decision",
        description="One semantic product surface is compiled through five governed art-direction profiles.",
        region="hero",
        group_id="overview_copy",
    )

    metrics = builder.add_dashboard("quality_metrics", region="primary", group_id="quality_cards")
    metrics.add_card(label="Ship confidence", value="92%", id="confidence")
    metrics.add_card(label="Risk delta", value="-18%", id="risk_delta")
    metrics.add_card(label="Open blockers", value="4", id="blockers")
    metrics.add_card(label="Review window", value="36 hr", id="review_window")

    decisions = builder.add_detail("decision_context", region="review", group_id="decision_fields")
    decisions.add_field(label="Owner", value="Revenue Platform", id="owner")
    decisions.add_field(label="Next gate", value="Executive review", id="next_gate")
    decisions.add_field(label="Evidence", value="Provenance manifest aligned", id="evidence")

    notes = builder.add_list("review_notes", region="review", group_id="note_stack")
    notes.add_item(
        label="Scope",
        description="Profile token changes style projection and bounded layout metadata.",
        id="scope_note",
    )
    notes.add_item(label="Proof", description="Semantic ids and provenance stay stable.", id="proof_note")

    builder.add_style("page_temperature", "view:art_direction_workspace", "palette.temperature")
    builder.add_style("hero_density", "region:hero", "density.airy")
    builder.add_style("hero_surface", "motif:overview", "surface.subtle")
    builder.add_style("hero_eyebrow", "binding:overview_eyebrow", "tone.accent")
    builder.add_style("hero_title", "binding:overview_title", "rhythm.hierarchy")
    builder.add_style("hero_body", "binding:overview_description", "narrative.flow")
    builder.add_style("metrics_density", "motif:quality_metrics", "density.regular")
    builder.add_style("confidence_focus", "binding:confidence_value", "emphasis.high")
    builder.add_style("risk_accent", "binding:risk_delta_value", "tone.accent")
    builder.add_style("review_surface", "motif:decision_context", "surface.strong")
    builder.add_style("review_density", "motif:decision_context", "density.compact")
    builder.add_style("owner_muted", "binding:owner_value", "tone.muted")
    builder.add_style("notes_surface", "motif:review_notes", "surface.subtle")
    builder.add_style("notes_flow", "binding:scope_note_description", "narrative.flow")
    return builder.build_bundle()


def render_fragment(ast: ASTBundle, namespace: str) -> tuple[str, dict[str, Any]]:
    manifest: dict[str, Any] = {}
    style_values = dict(ast.style_values or DEFAULT_STYLE_TOKEN_VALUES)
    rendered = _render_node(ast.result.root.root, manifest, style_values)
    namespaced = rendered.replace('id="dom-', f'id="{namespace}-dom-')
    return namespaced, {f"{namespace}-{key}": value for key, value in manifest.items()}


def semantic_hash(manifest: dict[str, Any]) -> str:
    ids = sorted(entry["ir_id"] for entry in manifest.values() if isinstance(entry, dict) and entry.get("ir_id"))
    payload = json.dumps(ids, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def layout_proof(manifest: dict[str, Any], profile: str) -> dict[str, dict[str, Any]]:
    proof: dict[str, dict[str, Any]] = {}
    for entry in manifest.values():
        if not isinstance(entry, dict):
            continue
        props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
        role = props.get("product_role")
        if role not in {*LAYOUT_PROOF_ROLES, *OPTIONAL_LAYOUT_PROOF_ROLES}:
            continue
        if role in OPTIONAL_LAYOUT_PROOF_ROLES and props.get("aesthetic_layout_profile") is None:
            continue
        if props.get("aesthetic_layout_profile") != profile:
            raise RuntimeError(f"{profile} generated {role} without matching aesthetic layout profile metadata.")
        if role in LAYOUT_PROOF_ROLES:
            columns = props.get("columns")
            if not isinstance(columns, int):
                raise RuntimeError(f"{profile} generated {role} without integer column metadata.")
            proof[role] = {
                "columns": columns,
                "layoutStrategy": props.get("layout_strategy"),
                "profile": props.get("aesthetic_layout_profile"),
            }
        elif role == "metric_card":
            span_columns = props.get("span_columns")
            if not isinstance(span_columns, int):
                raise RuntimeError(f"{profile} generated featured metric card without integer span metadata.")
            proof[role] = {
                "profile": props.get("aesthetic_layout_profile"),
                "spanColumns": span_columns,
            }
    missing = [role for role in LAYOUT_PROOF_ROLES if role not in proof]
    if missing:
        raise RuntimeError(f"{profile} missing layout proof role(s): {', '.join(missing)}")
    ordered_roles = (*LAYOUT_PROOF_ROLES, *(role for role in OPTIONAL_LAYOUT_PROOF_ROLES if role in proof))
    return {role: proof[role] for role in ordered_roles}


def layout_signature(layout: dict[str, dict[str, Any]]) -> str:
    parts = [f"{LAYOUT_PROOF_LABELS[role]} {layout[role]['columns']}" for role in LAYOUT_PROOF_ROLES]
    if "metric_card" in layout:
        parts.append(f"{LAYOUT_PROOF_LABELS['metric_card']} span {layout['metric_card']['spanColumns']}")
    return " / ".join(parts)


def style_signature(style_facts: dict[str, Any]) -> str:
    return (
        f"{style_facts['changed_token_count']} changed tokens / "
        f"{style_facts['category_count']} categories / "
        f"{style_facts['declaration_count']} declarations"
    )


def compile_profiles() -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    expected_hash: str | None = None
    expected_ids: list[str] | None = None
    for index, profile in enumerate(AESTHETIC_PROFILE_TOKENS):
        bundle = build_bundle(profile)
        ast = compile(bundle)
        if ast.result.diagnostics:
            diagnostics = [diagnostic.to_json() for diagnostic in ast.result.diagnostics]
            raise RuntimeError(f"{profile} produced diagnostics: {json.dumps(diagnostics, indent=2)}")
        fragment, manifest = render_fragment(ast, f"profile{index}")
        ids = sorted(entry["ir_id"] for entry in manifest.values() if isinstance(entry, dict) and entry.get("ir_id"))
        digest = semantic_hash(manifest)
        if expected_hash is None:
            expected_hash = digest
            expected_ids = ids
        elif digest != expected_hash or ids != expected_ids:
            raise RuntimeError("Aesthetic profiles changed semantic ids; expected one stable IntentBundle shape.")
        layout = layout_proof(manifest, profile)
        style_facts = profile_style_facts(profile)
        profiles[profile] = {
            "fragment": fragment,
            "manifest": manifest,
            "intentJson": json.dumps(bundle.to_json(), indent=2, sort_keys=True),
            "layoutProof": layout,
            "layoutSignature": layout_signature(layout),
            "semanticHash": digest,
            "nodeCount": len(ids),
            "styleTokenCount": len(bundle.view_spec.styles),
            "styleProof": style_facts,
            "styleSignature": style_signature(style_facts),
            "profileLabel": PROFILE_LABELS[profile],
            "profileNote": PROFILE_NOTES[profile],
        }
    return profiles


def script_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True).replace("</script>", "<\\/script>")


def build_page(profiles: dict[str, dict[str, Any]]) -> str:
    first_profile = next(iter(profiles))
    cards = "\n".join(
        f"""        <button type="button" class="profile-card" data-profile-token="{html.escape(profile)}" aria-pressed="{"true" if profile == first_profile else "false"}">
          <span class="profile-token">{html.escape(profile)}</span>
          <span class="profile-name">{html.escape(data["profileLabel"])}</span>
          <span class="profile-note">{html.escape(data["profileNote"])}</span>
          <span class="stable-pill">semantic ids stable</span>
        </button>"""
        for profile, data in profiles.items()
    )
    artifact_views = "\n".join(
        f"""          <section class="artifact-view" data-profile-token="{html.escape(profile)}" aria-label="{html.escape(data["profileLabel"])} generated artifact" {"hidden" if profile != first_profile else ""}>
            {data["fragment"]}
          </section>"""
        for profile, data in profiles.items()
    )
    profile_tokens = ", ".join(AESTHETIC_PROFILE_TOKENS)
    head_meta = demo_head_metadata(
        title="ViewSpec Demo - Same Intent, Five Art Directions",
        description="Compile one ViewSpec IntentBundle through five deterministic aesthetic profile tokens while preserving semantic ids and provenance.",
        canonical_path="aesthetic-profiles",
    )
    profile_meta = {
        profile: {
            "label": data["profileLabel"],
            "note": data["profileNote"],
            "semanticHash": data["semanticHash"],
            "nodeCount": data["nodeCount"],
            "styleTokenCount": data["styleTokenCount"],
            "styleProof": data["styleProof"],
            "styleSignature": data["styleSignature"],
            "layoutProof": data["layoutProof"],
            "layoutSignature": data["layoutSignature"],
        }
        for profile, data in profiles.items()
    }
    first_intent = profiles[first_profile]["intentJson"]
    first_data = profiles[first_profile]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ViewSpec Demo - Same Intent, Five Art Directions</title>
{head_meta}
  <link rel="icon" href="data:,">
  <script src="../shared/nav.js" defer></script>
  <style data-viewspec-emitter-css="true">
{OFFLINE_EMITTER_CSS}
  </style>
  <style data-demo-shell-css="true">
    :root {{
      color-scheme: light;
      --bg: #f6f8fb;
      --paper: #ffffff;
      --paper-2: #f0f4f8;
      --line: #d8e0e8;
      --line-strong: #b8c4d1;
      --ink: #101820;
      --muted: #5c6978;
      --soft: #eef3f7;
      --accent: #0f766e;
      --accent-soft: #dff6f2;
      --gold: #a16207;
      --shell: 1240px;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      min-height: 100vh;
    }}

    .page-shell {{
      margin: 0 auto;
      padding: 24px 18px 56px;
      width: min(var(--shell), 100%);
    }}

    .hero {{
      display: grid;
      gap: 16px;
      grid-template-columns: minmax(0, 1fr) minmax(280px, 420px);
      padding: 18px 0 20px;
      align-items: end;
      border-bottom: 1px solid var(--line);
    }}

    .eyebrow,
    .profile-token,
    .stable-pill,
    dt {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      letter-spacing: 0;
    }}

    .eyebrow {{
      color: var(--accent);
      font-size: 0.78rem;
      font-weight: 900;
      text-transform: uppercase;
    }}

    h1 {{
      font-size: clamp(2rem, 4.1vw, 3.15rem);
      line-height: 1.02;
      margin: 0;
      max-width: 19ch;
    }}

    .lead {{
      color: var(--muted);
      font-size: 0.96rem;
      line-height: 1.6;
      margin: 0;
      max-width: 76ch;
    }}

    .hero-copy {{
      display: grid;
      gap: 12px;
    }}

    .hero-facts {{
      align-self: stretch;
      display: grid;
      gap: 8px;
      list-style: none;
      margin: 0;
      padding: 0;
    }}

    .hero-facts li {{
      align-items: baseline;
      background: rgba(255, 255, 255, 0.72);
      border: 1px solid var(--line);
      border-radius: 8px;
      display: flex;
      gap: 12px;
      justify-content: space-between;
      padding: 10px 12px;
    }}

    .hero-facts strong {{
      color: var(--ink);
      font-size: 0.9rem;
      font-weight: 850;
      white-space: nowrap;
    }}

    .hero-facts span {{
      color: var(--muted);
      font-size: 0.82rem;
      line-height: 1.35;
      text-align: right;
    }}

    .demo-layout {{
      display: grid;
      gap: 22px;
      grid-template-columns: minmax(235px, 292px) minmax(0, 1fr);
      margin-top: 20px;
      align-items: start;
    }}

    .profile-grid {{
      display: grid;
      gap: 9px;
      position: sticky;
      top: 18px;
    }}

    .profile-card {{
      appearance: none;
      background: rgba(255, 255, 255, 0.82);
      border: 1px solid var(--line);
      border-radius: 8px;
      color: inherit;
      cursor: pointer;
      display: grid;
      gap: 7px;
      padding: 12px;
      text-align: left;
      width: 100%;
    }}

    .profile-card:hover {{
      border-color: var(--line-strong);
      background: #ffffff;
    }}

    .profile-card[aria-pressed="true"] {{
      background: var(--accent-soft);
      border-color: rgba(15, 118, 110, 0.44);
      box-shadow: inset 3px 0 0 var(--accent);
    }}

    .profile-token,
    .profile-name,
    .profile-note,
    .stable-pill {{
      display: block;
    }}

    .profile-token {{
      color: var(--accent);
      font-size: 0.68rem;
      font-weight: 900;
    }}

    .profile-name,
    h2 {{
      font-size: 1.05rem;
      font-weight: 850;
      line-height: 1.18;
      margin: 0;
    }}

    .stable-pill {{
      border: 1px solid rgba(15, 118, 110, 0.22);
      border-radius: 999px;
      color: #0f5f58;
      font-size: 0.62rem;
      font-weight: 800;
      justify-self: start;
      padding: 5px 8px;
    }}

    .profile-note {{
      color: var(--muted);
      font-size: 0.86rem;
      line-height: 1.55;
      margin: 0;
    }}

    .artifact-stage {{
      display: grid;
      gap: 14px;
      min-width: 0;
    }}

    .stage-header {{
      align-items: start;
      display: flex;
      gap: 12px;
      justify-content: space-between;
      padding: 2px 2px 0;
    }}

    .stage-title {{
      display: grid;
      gap: 5px;
    }}

    .stage-title h2 {{
      font-size: 1.35rem;
    }}

    .stage-title p {{
      color: var(--muted);
      line-height: 1.55;
      margin: 0;
    }}

    .artifact-frame {{
      background: #ffffff;
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 22px 54px rgba(45, 55, 72, 0.14);
      max-height: min(790px, 76vh);
      min-height: min(640px, 74vh);
      overflow: auto;
      padding: 20px;
    }}

    .artifact-view[hidden] {{
      display: none;
    }}

    .profile-proof {{
      align-self: start;
      display: grid;
      gap: 8px;
      grid-template-columns: repeat(4, minmax(8rem, 1fr));
      margin: 0;
      min-width: min(100%, 560px);
    }}

    .profile-proof div {{
      background: var(--soft);
      border: 1px solid var(--line);
      border-radius: 8px;
      display: grid;
      gap: 4px;
      padding: 9px 10px;
    }}

    dt {{
      color: var(--muted);
      font-size: 0.68rem;
      font-weight: 900;
      text-transform: uppercase;
    }}

    dd {{
      color: var(--ink);
      font-size: 0.82rem;
      font-weight: 850;
      margin: 0;
    }}

    .contract-panel {{
      border-top: 1px solid var(--line);
      display: grid;
      gap: 14px;
      margin-top: 34px;
      padding: 26px 0 0;
    }}

    .contract-panel p {{
      color: var(--muted);
      line-height: 1.68;
      margin: 0;
      max-width: 94ch;
    }}

    .code-box {{
      background: #111827;
      border: 1px solid #0f172a;
      border-radius: 8px;
      color: #dbeafe;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.78rem;
      line-height: 1.6;
      max-height: 320px;
      overflow: auto;
      padding: 14px;
      white-space: pre-wrap;
    }}

    @media (max-width: 900px) {{
      .hero {{
        grid-template-columns: 1fr;
      }}

      .hero-facts {{
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }}

      .hero-facts li {{
        align-items: start;
        display: grid;
      }}

      .hero-facts span {{
        text-align: left;
      }}

      .demo-layout {{
        grid-template-columns: 1fr;
      }}

      .profile-grid {{
        grid-auto-columns: minmax(238px, 78vw);
        grid-auto-flow: column;
        overflow-x: auto;
        padding-bottom: 4px;
        position: static;
        scroll-snap-type: x proximity;
      }}

      .profile-card {{
        scroll-snap-align: start;
      }}

      .stage-header {{
        align-items: start;
        display: grid;
      }}

      .profile-proof {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
        width: 100%;
      }}

      .artifact-frame {{
        max-height: 68vh;
        min-height: 480px;
        padding: 10px;
      }}
    }}

    @media (max-width: 460px) {{
      .page-shell {{
        padding-inline: 10px;
      }}

      .hero {{
        gap: 10px;
        padding: 18px 0 14px;
      }}

      .hero-facts {{
        grid-template-columns: 1fr;
      }}

      h1 {{
        font-size: 1.8rem;
        line-height: 1.06;
        max-width: 16ch;
      }}

      .lead {{
        font-size: 0.9rem;
        line-height: 1.55;
      }}

      .token-lead {{
        display: none;
      }}

      .profile-grid {{
        gap: 8px;
        grid-auto-columns: minmax(134px, 43vw);
      }}

      .profile-card {{
        gap: 5px;
        min-height: 70px;
        padding: 10px;
      }}

      .profile-card .profile-token,
      .profile-card .profile-note,
      .profile-card .stable-pill {{
        display: none;
      }}

      .profile-name {{
        font-size: 0.9rem;
      }}

      .demo-layout,
      .artifact-stage {{
        gap: 12px;
      }}

      .stage-title h2 {{
        font-size: 1.05rem;
      }}

      #active-profile-note,
      .profile-proof {{
        display: none;
      }}

      .artifact-frame {{
        max-height: 62vh;
        min-height: 420px;
        padding: 6px;
      }}
    }}
  </style>
</head>
<body>
  <main class="page-shell">
    <section class="hero" data-presentation-contract="light-gallery-showroom">
      <div class="hero-copy">
        <p class="eyebrow">Same Intent, Five Art Directions</p>
        <h1>Beauty as a checked compiler handle.</h1>
        <p class="lead">Each generated artifact uses one explicit view-level style token. The token is deterministic art direction, not CSS, visual-regression proof, accessibility certification, or arbitrary design-system certification.</p>
        <p class="lead token-lead">Supported V1 tokens: {html.escape(profile_tokens)}.</p>
      </div>
      <ul class="hero-facts" aria-label="Aesthetic profile invariants">
        <li><strong>1 IntentBundle</strong><span>shared semantic graph</span></li>
        <li><strong>5 profiles</strong><span>governed style handles</span></li>
        <li><strong>0 shell overrides</strong><span>generated internals untouched</span></li>
      </ul>
    </section>

    <section class="demo-layout" aria-label="Generated aesthetic profile variants">
      <nav class="profile-grid" aria-label="Aesthetic profile selector">
{cards}
      </nav>

      <section class="artifact-stage" aria-live="polite">
        <div class="stage-header">
          <div class="stage-title">
            <p class="profile-token" id="active-profile-token">{html.escape(first_profile)}</p>
            <h2 id="active-profile-label">{html.escape(first_data["profileLabel"])}</h2>
            <p id="active-profile-note">{html.escape(first_data["profileNote"])}</p>
          </div>
          <dl class="profile-proof">
            <div><dt>Semantic hash</dt><dd id="active-semantic-hash">{html.escape(first_data["semanticHash"][:16])}</dd></div>
            <div><dt>IR nodes</dt><dd id="active-node-count">{first_data["nodeCount"]}</dd></div>
            <div><dt>Style refs</dt><dd id="active-style-count">{first_data["styleTokenCount"]}</dd></div>
            <div><dt>Style delta</dt><dd id="active-style-signature">{html.escape(first_data["styleSignature"])}</dd></div>
            <div><dt>Layout</dt><dd id="active-layout-signature">{html.escape(first_data["layoutSignature"])}</dd></div>
          </dl>
        </div>
        <div class="artifact-frame" aria-label="Selected generated artifact">
{artifact_views}
        </div>
      </section>
    </section>

    <section class="contract-panel">
      <h2>What stays invariant</h2>
      <p>The five cards share the same IntentBundle shape, generated semantic ids, and manifest-backed provenance. Only compiler-owned typography, spacing, surface, color, action, hierarchy, rhythm, narrative style projections, bounded grid metadata, and featured metric-card span metadata change.</p>
      <p>Demo shell CSS frames the page only. It does not style generated artifact internals or bypass the emitted profile output.</p>
      <div class="code-box" aria-label="Profile proof metadata">{html.escape(script_json(profile_meta))}</div>
      <div class="code-box" aria-label="Representative IntentBundle">{html.escape(first_intent)}</div>
    </section>
  </main>
  <script type="application/json" id="aesthetic-profile-proof">{script_json(profile_meta)}</script>
  <script>
    const profileProof = JSON.parse(document.getElementById('aesthetic-profile-proof').textContent);
    const profileButtons = Array.from(document.querySelectorAll('.profile-card'));
    const profileViews = Array.from(document.querySelectorAll('.artifact-view'));
    const activeToken = document.getElementById('active-profile-token');
    const activeLabel = document.getElementById('active-profile-label');
    const activeNote = document.getElementById('active-profile-note');
    const activeHash = document.getElementById('active-semantic-hash');
    const activeNodeCount = document.getElementById('active-node-count');
    const activeStyleCount = document.getElementById('active-style-count');
    const activeStyleSignature = document.getElementById('active-style-signature');
    const activeLayoutSignature = document.getElementById('active-layout-signature');

    function activateProfile(token) {{
      const proof = profileProof[token];
      if (!proof) return;
      for (const button of profileButtons) {{
        button.setAttribute('aria-pressed', String(button.dataset.profileToken === token));
      }}
      for (const view of profileViews) {{
        view.hidden = view.dataset.profileToken !== token;
      }}
      activeToken.textContent = token;
      activeLabel.textContent = proof.label;
      activeNote.textContent = proof.note;
      activeHash.textContent = proof.semanticHash.slice(0, 16);
      activeNodeCount.textContent = String(proof.nodeCount);
      activeStyleCount.textContent = String(proof.styleTokenCount);
      activeStyleSignature.textContent = proof.styleSignature;
      activeLayoutSignature.textContent = proof.layoutSignature;
    }}

    for (const button of profileButtons) {{
      button.addEventListener('click', () => activateProfile(button.dataset.profileToken));
    }}
  </script>
</body>
</html>
"""


def main() -> None:
    profiles = compile_profiles()
    output_dir = ROOT / "demos" / "aesthetic-profiles"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "index.html"
    output_path.write_text(build_page(profiles), encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
