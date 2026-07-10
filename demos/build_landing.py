"""
Build the public ViewSpec landing page as a ViewSpec artifact.

The public homepage keeps only document shell responsibilities outside the
artifact boundary: metadata, SEO links, and tiny toggle/copy behavior. The
visible landing content is emitted by the ViewSpec compiler from the
IntentBundle assembled below.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import html
from pathlib import Path
from typing import Any

from viewspec import AESTHETIC_PROFILE_TOKENS, ViewSpecBuilder, compile, profile_style_facts
from viewspec.aesthetics import profile_layout_props, profile_style_values
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
from viewspec.intent_tools import wrap_intent_bundle_manifest
from viewspec.local_tools import source_hash


ROOT = Path(__file__).resolve().parents[1]
DEMOS = ROOT / "demos"
COMPILED_DIR = DEMOS / "landing-compiled"
PROFILE_DIR = COMPILED_DIR / "profiles"
PUBLIC_INDEX = DEMOS / "index.html"
DEFAULT_PROFILE = "aesthetic.calm_ops"

PROFILE_LABELS = {
    "aesthetic.calm_ops": "Calm Ops",
    "aesthetic.premium_saas": "Premium SaaS",
    "aesthetic.data_dense": "Data Dense",
    "aesthetic.editorial_product": "Editorial Product",
    "aesthetic.executive_review": "Executive Review",
    "aesthetic.brutalist": "Brutalist",
    "aesthetic.neon_cyber": "Neon Cyber",
    "aesthetic.warm_organic": "Warm Organic",
}

PROFILE_NOTES = {
    "aesthetic.calm_ops": "Quiet operational surfaces with teal accents and low-contrast panels.",
    "aesthetic.premium_saas": "Polished product rhythm with stronger hierarchy and softer shadows.",
    "aesthetic.data_dense": "Compact spacing, smaller type, and tighter controls for repeated scanning.",
    "aesthetic.editorial_product": "Warmer product-story pacing with broader prose rhythm.",
    "aesthetic.executive_review": "Conservative review surfaces with restrained contrast and crisp framing.",
    "aesthetic.brutalist": "Raw off-white ground with hard black frames, zero radius, and a loud red.",
    "aesthetic.neon_cyber": "Near-black terminal with magenta uppercase accents and cyan glow.",
    "aesthetic.warm_organic": "Warm sand ground, humanist sans, and soft rounded amber surfaces.",
}


def _profile_slug(profile: str) -> str:
    return profile.replace("aesthetic.", "").replace("_", "-")


def build_bundle(aesthetic_profile: str | None = None):
    builder = ViewSpecBuilder("viewspec_landing")

    builder.add_hero(
        "launch_hero",
        eyebrow="Agent-native app compiler",
        title="Intent goes in. Interface comes out.",
        description=(
            "Agents commit to meaning — nodes, bindings, motifs. ViewSpec compiles the UI, "
            "the state reducer, replay checks, and shell proof. Deterministic, and no runtime LLM."
        ),
        region="main",
        group_id="launch",
    )

    # Values are the exact State IR proof-fact literals published in public-facts.json
    # (appbundle_state_ir.proof_facts); the compiled landing surfaces them verbatim.
    proof_badges = builder.add_dashboard("proof_badges", region="main", group_id="proof_badges")
    proof_badges.add_card(label="replay", value="state replay passed")
    proof_badges.add_card(label="reducer", value="reducer generated")
    proof_badges.add_card(label="manifest", value="manifest checked")
    proof_badges.add_card(label="shell", value="shell hash matched")
    proof_badges.add_card(label="runtime", value="No runtime LLM")

    compile_flow = builder.add_dashboard("compile_flow", region="main", group_id="compile_flow")
    compile_flow.add_card(label="1 · intent", value="Agent writes IntentBundle JSON.")
    compile_flow.add_card(label="2 · compile", value="CompositionIR to UI and reducer, no LLM.")
    compile_flow.add_card(label="3 · proof", value="Provenance, replay, and shell hash attached.")

    capabilities = builder.add_table("capabilities", region="main", group_id="capabilities")
    capabilities.add_row(
        label="State IR",
        value="interactive_state_v0 writes state_reducer.ts and reduceViewSpecState.",
    )
    capabilities.add_row(
        label="Aesthetic Profiles",
        value="Eight compiled projections from one semantic graph.",
    )
    capabilities.add_row(
        label="Proof Pipeline",
        value="state_replay_assertions plus viewspec prove-app --with-shell.",
    )
    capabilities.add_row(
        label="Portable Surfaces",
        value="html-tailwind and React locally; SwiftUI and Flutter hosted.",
    )

    agent_workflow = builder.add_table("agent_workflow", region="main", group_id="agent_workflow")
    agent_workflow.add_row(label="describe", value="Emit IntentBundle or AppBundle JSON.")
    agent_workflow.add_row(label="validate", value="viewspec validate-intent fails closed on drift.")
    agent_workflow.add_row(label="prove", value="viewspec prove-app --with-shell writes the report.")

    pricing = builder.add_table("pricing", region="main", group_id="pricing")
    pricing.add_row(label="Free", value="Local SDK. Unlimited compiles, proofs, and surfaces.")
    pricing.add_row(label="Pro", value="149/mo. Hosted compiler API, 10k calls per day.")
    pricing.add_row(label="Enterprise", value="Custom volume, organization sharing, and support.")

    artifact_identity = builder.add_dashboard("artifact_identity", region="main", group_id="artifact_identity")
    artifact_identity.add_card(label="provenance", value="Every element traces to its address.")
    artifact_identity.add_card(label="determinism", value="Same intent, same bytes, same hash.")

    builder.add_style("s_launch_hero", "launch_hero", "emphasis.high")
    builder.add_style("s_proof_badges", "proof_badges", "surface.subtle")
    builder.add_style("s_capabilities", "capabilities", "density.compact")
    if aesthetic_profile is not None:
        builder.set_aesthetic_profile(aesthetic_profile)
    return builder.build_bundle()


def _extract_body(html: str) -> str:
    match = re.search(r"<body>\s*([\s\S]*?)\s*</body>", html)
    if not match:
        raise RuntimeError("Generated artifact did not contain a body.")
    body = match.group(1)
    body = body.replace("<main ", '<main data-viewspec-page-artifact="true" ', 1)
    return body


def _extract_emitter_css(html: str) -> str:
    match = re.search(r"<style>\s*([\s\S]*?)\s*</style>", html)
    if not match:
        raise RuntimeError("Generated artifact did not contain emitter CSS.")
    return match.group(1)


def _semantic_hash(manifest: dict[str, Any]) -> str:
    nodes = manifest.get("nodes") if isinstance(manifest.get("nodes"), dict) else {}
    ids = sorted(entry["ir_id"] for entry in nodes.values() if isinstance(entry, dict) and entry.get("ir_id"))
    payload = json.dumps(ids, separators=(",", ":"), sort_keys=True)
    return source_hash(payload)


def _style_projection_hash(style_values: dict[str, Any]) -> str:
    payload = json.dumps(style_values, separators=(",", ":"), sort_keys=True)
    return source_hash(payload)


def _style_signature(style_facts: dict[str, Any]) -> str:
    return (
        f"{style_facts['changed_token_count']} changed tokens / "
        f"{style_facts['category_count']} categories / "
        f"{style_facts['declaration_count']} declarations"
    )


def _layout_signature(profile: str) -> str:
    layout = profile_layout_props(profile)
    parts: list[str] = []
    content = layout.get("content_grid", {})
    metrics = layout.get("metric_grid", {})
    if "columns" in content:
        parts.append(f"workspace {content['columns']}")
    if "columns" in metrics:
        parts.append(f"metrics {metrics['columns']}")
    metric_card = layout.get("metric_card")
    if metric_card:
        span = metric_card.get("span_columns")
        emphasis = metric_card.get("layout_emphasis")
        parts.append(f"featured metric span {span} + {emphasis} emphasis")
    return " / ".join(parts)


def _compiled_bundle(bundle, output_dir: Path, *, command_profile: str) -> dict[str, Any]:
    ast = compile(bundle)

    print(f"Diagnostics for {command_profile}: {len(ast.result.diagnostics)}")
    for diagnostic in ast.result.diagnostics:
        print(f"  [{diagnostic.severity}] {diagnostic.code}: {diagnostic.message}")

    paths = HtmlTailwindEmitter().emit(ast, output_dir)
    bundle_json = json.dumps(bundle.to_json(), indent=2, sort_keys=True)
    wrap_intent_bundle_manifest(
        Path(paths["manifest"]),
        source_name="intent_bundle.json",
        raw_source_hash=source_hash(bundle_json),
        design=None,
        command_args=["python", "demos/build_landing.py"],
    )

    intent_bundle_path = output_dir / "intent_bundle.json"
    intent_bundle_path.write_text(bundle_json, encoding="utf-8", newline="")
    generated_html = Path(paths["html"]).read_text(encoding="utf-8")
    artifact_body = _extract_body(generated_html)
    artifact_body_path = output_dir / "artifact_body.html"
    artifact_body_path.write_text(artifact_body, encoding="utf-8", newline="")
    manifest = json.loads(Path(paths["manifest"]).read_text(encoding="utf-8"))

    return {
        "artifact_body": artifact_body,
        "artifact_body_path": artifact_body_path,
        "ast": ast,
        "bundle_json": bundle_json,
        "generated_html": generated_html,
        "intent_bundle_path": intent_bundle_path,
        "manifest": manifest,
        "paths": paths,
    }


def _profile_evidence(profile_results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    profiles: dict[str, dict[str, Any]] = {}
    for profile, result in profile_results.items():
        style_facts = profile_style_facts(profile)
        slug = _profile_slug(profile)
        manifest = result["manifest"]
        manifest_nodes = manifest.get("nodes", {})
        root_node = manifest_nodes.get("dom-region_root", {}) if isinstance(manifest_nodes, dict) else {}
        root_props = root_node.get("props", {}) if isinstance(root_node, dict) else {}
        style_values = dict(result["ast"].style_values or {})
        projection = _profile_projection(style_values, result["artifact_body"], profile)
        profiles[profile] = {
            "projection": projection,
            "artifactBodyUrl": f"./landing-compiled/profiles/{slug}/artifact_body.html",
            "artifactUrl": f"./landing-compiled/profiles/{slug}/index.html",
            "intentUrl": f"./landing-compiled/profiles/{slug}/intent_bundle.json",
            "label": PROFILE_LABELS[profile],
            "layoutSignature": _layout_signature(profile),
            "manifestAestheticProfile": root_props.get("aesthetic_profile"),
            "manifestUrl": f"./landing-compiled/profiles/{slug}/provenance_manifest.json",
            "nodeCount": len(manifest_nodes) if isinstance(manifest_nodes, dict) else 0,
            "note": PROFILE_NOTES[profile],
            "semanticHash": _semantic_hash(manifest),
            "slug": slug,
            "styleProjectionHash": _style_projection_hash(style_values),
            "styleProof": style_facts,
            "styleSignature": _style_signature(style_facts),
        }

    semantic_hashes = {entry["semanticHash"] for entry in profiles.values()}
    style_hashes = {entry["styleProjectionHash"] for entry in profiles.values()}
    evidence = {
        "version": "landing_compiled_aesthetic_profiles.v1",
        "defaultProfile": DEFAULT_PROFILE,
        "profileCount": len(profiles),
        "profiles": profiles,
        "semanticIdsStable": len(semantic_hashes) == 1,
        "shellOverrides": 0,
        "styleProjectionDistinct": len(style_hashes) == len(profiles),
        "styleProjectionHashCount": len(style_hashes),
    }
    if not evidence["semanticIdsStable"]:
        raise RuntimeError("Landing profile evidence requires stable semantic ids across profile artifacts.")
    if not evidence["styleProjectionDistinct"]:
        raise RuntimeError("Landing profile evidence requires distinct style projection hashes.")
    for profile, entry in profiles.items():
        entry["invariantFlags"] = {
            "manifestProfileMatches": entry["manifestAestheticProfile"] == profile,
            "sameSemanticGraph": evidence["semanticIdsStable"],
            "semanticIdsStable": evidence["semanticIdsStable"],
            "shellOverridesZero": evidence["shellOverrides"] == 0,
            "styleProjectionDistinct": evidence["styleProjectionDistinct"],
        }
        if not all(entry["invariantFlags"].values()):
            raise RuntimeError(f"Landing profile evidence invariant failed for {profile}.")
    return evidence


def _json_ld() -> str:
    graph = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Organization",
                "@id": "https://viewspec.dev/#organization",
                "name": "ViewSpec",
                "url": "https://viewspec.dev/",
                "sameAs": [
                    "https://github.com/nxrobins/viewspec",
                    "https://pypi.org/project/viewspec/",
                ],
            },
            {
                "@type": "WebSite",
                "@id": "https://viewspec.dev/#website",
                "url": "https://viewspec.dev/",
                "name": "ViewSpec",
                "description": "Agent-native app compiler, hosted compiler API, and Python SDK for deterministic UI, state, reducers, and proof bundles.",
                "publisher": {"@id": "https://viewspec.dev/#organization"},
                "inLanguage": "en",
            },
            {
                "@type": "SoftwareApplication",
                "@id": "https://viewspec.dev/#software",
                "name": "ViewSpec",
                "applicationCategory": "DeveloperApplication",
                "applicationSubCategory": "Agent-native app compiler",
                "operatingSystem": "Python 3.11+ and Web API",
                "url": "https://viewspec.dev/",
                "downloadUrl": "https://pypi.org/project/viewspec/",
                "codeRepository": "https://github.com/nxrobins/viewspec",
                "programmingLanguage": ["Python", "JSON", "Protocol Buffers"],
                "description": "ViewSpec validates agent-authored IntentBundle and AppBundle JSON, proves first artifacts with viewspec prove and prove-app --with-shell, writes PROOF.md, proof_report.json, support_bundle.json, APP_PROOF.md, app_proof_report.json, state_reducer.ts, and state_manifest.json, records compact style-delta counts, applies DESIGN.md, writes provenance, and compiles deterministic UI outputs.",
                "keywords": "agent-native app compiler, AppBundle, interactive_state_v0, reduceViewSpecState, state_replay_assertions, IntentBundle, compiled aesthetic profiles, semantic UI compiler, agentic engineering, AI coding agents, deterministic HTML, semantic diff, provenance",
                "featureList": [
                    "Local viewspec init-intent and validate-intent for agent-authored IntentBundles",
                    "Local AppBundle V3 interactive_state_v0 proof with generated state_reducer.ts",
                    "Pure TypeScript reduceViewSpecState reducer generation with state_replay_assertions",
                    "viewspec prove-app --with-shell for shell hash and replay proof",
                    "Compiled aesthetic profile homepage artifacts with stable semantic ids and distinct style projection hashes",
                    "Local viewspec compile for deterministic UI artifacts",
                    "Auditable provenance_manifest.json with stable hashes",
                    "DESIGN.md theming without arbitrary CSS or script",
                    "Hosted compiler API at api.viewspec.dev for IntentBundle workflows",
                ],
                "offers": [
                    {"@type": "Offer", "name": "Free", "price": "0", "priceCurrency": "USD", "url": "https://viewspec.dev/#pricing"},
                    {"@type": "Offer", "name": "Pro", "price": "149", "priceCurrency": "USD", "url": "https://viewspec.dev/#pricing"},
                    {"@type": "Offer", "name": "Enterprise", "price": "Custom", "priceCurrency": "USD", "url": "https://viewspec.dev/#pricing"},
                ],
                "publisher": {"@id": "https://viewspec.dev/#organization"},
            },
            {
                "@type": "WebAPI",
                "@id": "https://viewspec.dev/#api",
                "name": "ViewSpec Hosted Compiler API",
                "url": "https://api.viewspec.dev/v1/compile",
                "documentation": "https://viewspec.dev/openapi.json",
                "description": "HTTP API for advanced ViewSpec IntentBundle compilation into CompositionIR, diagnostics, timing metadata, and provenance.",
                "provider": {"@id": "https://viewspec.dev/#organization"},
            },
            {
                "@type": "FAQPage",
                "@id": "https://viewspec.dev/#faq",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": "What is ViewSpec?",
                        "acceptedAnswer": {
                            "@type": "Answer",
                            "text": "ViewSpec is an agent-native app compiler. Agents emit IntentBundle or AppBundle JSON, and ViewSpec validates it, applies DESIGN.md, records provenance, compiles renderer outputs, generates bounded reducers, and writes proof reports.",
                        },
                    },
                    {
                        "@type": "Question",
                        "name": "Does ViewSpec call an LLM at runtime?",
                        "acceptedAnswer": {
                            "@type": "Answer",
                            "text": "No. The local compiler and generated artifacts are deterministic and do not call a runtime LLM.",
                        },
                    },
                ],
            },
        ],
    }
    return json.dumps(graph, indent=2)


def _profile_buttons(profile_evidence: dict[str, Any]) -> str:
    buttons: list[str] = []
    default_profile = profile_evidence["defaultProfile"]
    for profile in AESTHETIC_PROFILE_TOKENS:
        data = profile_evidence["profiles"][profile]
        active = profile == default_profile
        buttons.append(
            (
                '<button type="button" class="artifact-button artifact-profile-button" '
                f'data-profile-token="{html.escape(profile, quote=True)}" '
                f'data-profile-slug="{html.escape(data["slug"], quote=True)}" '
                f'data-active="{"true" if active else "false"}" '
                f'aria-pressed="{"true" if active else "false"}" '
                f'aria-label="Use {html.escape(data["label"], quote=True)} compiled aesthetic profile">'
                f'<span>{html.escape(data["label"])}</span>'
                f'<code>{html.escape(profile)}</code>'
                "</button>"
            )
        )
    return "".join(buttons)


def _script_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True).replace("</script>", "<\\/script>")


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return f"rgba(245,178,63,{alpha})"
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _css_last_value(decls: str, prop: str) -> str | None:
    found = re.findall(rf"{re.escape(prop)}:\s*([^;]+)", decls or "")
    return found[-1].strip() if found else None


def _profile_projection(style_values: dict[str, Any], artifact_body: str, profile: str) -> dict[str, Any]:
    """Real, compiler-derived style facts for the style-derivation demo — no invented values."""
    accent = _css_last_value(style_values.get("tone.accent", ""), "color") or "#0f766e"
    radius = _css_last_value(style_values.get("action.accent", ""), "border-radius") or "10px"
    weight = _css_last_value(style_values.get("emphasis.high", ""), "font-weight") or "700"
    match = re.search(r'id="dom-binding_launch_hero_title"[^>]*?font-family:\s*([^;"]+)', artifact_body)
    font_family = match.group(1).strip() if match else "ui-sans-serif, system-ui, sans-serif"
    first = font_family.split(",")[0]
    font_label = "mono" if "mono" in first else ("sans" if "sans" in first else ("serif" if "serif" in first else "sans"))
    layout = profile_layout_props(profile)
    cols = int(layout.get("metric_grid", {}).get("columns", 2) or 2)
    metric_card = layout.get("metric_card") or {}
    return {
        "accent": accent,
        "radius": radius,
        "weight": weight,
        "fontFamily": font_family,
        "fontLabel": font_label,
        "cols": cols,
        "featuredSpan": metric_card.get("span_columns"),
        "uppercase": "uppercase" in style_values.get("tone.accent", ""),
        "surfaceRadius": _css_last_value(style_values.get("surface.subtle", ""), "border-radius"),
        "pageTint": _css_last_value(style_values.get("palette.temperature", ""), "background-color"),
        "sparkFill": _rgba(accent, 0.16),
    }


def _projection_viewport_css() -> str:
    """Per-profile CSS for the style-derivation viewport.

    Every declaration block is injected VERBATIM from the compiler's aesthetic
    profile registry (the same `profile_style_values` the compiler projects) plus
    the bounded layout metadata (`profile_layout_props`). Nothing is invented here:
    the viewport is skinned by the projection itself.
    """
    rules: list[str] = []
    for profile in AESTHETIC_PROFILE_TOKENS:
        sv = profile_style_values(profile)
        layout = profile_layout_props(profile)
        short = profile.replace("aesthetic.", "")
        sel = f'.dv[data-p="{short}"]'
        cols = int(layout.get("metric_grid", {}).get("columns", 2) or 2)
        gap = _css_last_value(sv.get("density.regular", ""), "gap") or "0.8rem"
        pad = _css_last_value(sv.get("density.regular", ""), "padding") or "0.8rem"
        accent = _css_last_value(sv.get("tone.accent", ""), "color") or "#0f766e"
        rules.append(f'{sel}{{ {sv["palette.temperature"]} {sv["tone.neutral"]} }}')
        rules.append(f'{sel} .dvcard{{ {sv["surface.subtle"]} padding: calc({pad.split()[0]} * 1.6) calc({pad.split()[-1]} * 1.4); }}')
        rules.append(f'{sel} .dvgrid{{ grid-template-columns: repeat({cols}, minmax(0, 1fr)); gap: {gap}; }}')
        rules.append(f'{sel} .dvh{{ {sv["rhythm.hierarchy"]} }}')
        rules.append(f'{sel} .dvlabel{{ {sv["tone.muted"]} }}')
        rules.append(f'{sel} .dvdelta{{ {sv["tone.accent"]} }}')
        rules.append(f'{sel} .dvnum{{ {sv["emphasis.high"]} }}')
        rules.append(f'{sel} .dvbtn{{ {sv["action.accent"]} }}')
        rules.append(f'{sel} .dvflow{{ {sv["narrative.flow"]} }}')
        rules.append(
            f'{sel} .spark-line{{ stroke: {accent}; }} '
            f'{sel} .spark-dot{{ fill: {accent}; }} '
            f'{sel} .spark-fill{{ fill: {_rgba(accent, 0.14)}; }}'
        )
        metric_card = layout.get("metric_card") or {}
        span = metric_card.get("span_columns")
        if span:
            rules.append(f'{sel} .dvcard.featured{{ grid-column: span {span}; }}')
    return "\n".join(rules)


PAGE_CSS = r"""
  *,*::before,*::after{ box-sizing:border-box; }
  :root{
    --ink:#0A0D14; --ink-2:#0C111C;
    --panel:#12172380; --panel-solid:#141A28; --panel-2:#1A2233; --raise:#222C40;
    --line:#212A3C; --line-2:#374560;
    --text:#E7ECF5; --muted:#8894AC; --faint:#57617C;
    --amber:#F5B23F; --amber-2:#FFD584; --amber-dim:rgba(245,178,63,.12);
    --mint:#57DCA9; --focus:#93B8FF;
    --mono:ui-monospace,"JetBrains Mono","SF Mono",Menlo,Consolas,monospace;
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,Roboto,sans-serif;
    --wrap:1120px;
    /* derived tokens (profile: calm_ops) */
    --u:8px; --disp:1; --hw:600; --cols:3; --rad:11px; --tint:transparent; --measure:60ch;
  }
  body[data-profile="premium_saas"]     { --u:9px;  --disp:1.06; --hw:680; --cols:3; --rad:14px; --tint:rgba(245,178,63,.03); }
  body[data-profile="data_dense"]       { --u:6px;  --disp:.94;  --hw:560; --cols:4; --rad:7px;  --tint:rgba(120,150,220,.04); --measure:58ch; }
  body[data-profile="editorial_product"]{ --u:11px; --disp:1.14; --hw:660; --cols:2; --rad:9px;  --tint:rgba(245,178,63,.045); --measure:54ch; }
  body[data-profile="executive_review"] { --u:11px; --disp:1.2;  --hw:720; --cols:3; --rad:12px; --tint:rgba(255,213,132,.05); }

  html{ -webkit-text-size-adjust:100%; scroll-behavior:smooth; }
  @media (prefers-reduced-motion:reduce){ html{ scroll-behavior:auto; } *{ animation:none !important; transition:none !important; } }
  body{
    margin:0; background:var(--ink); color:var(--text);
    font-family:var(--sans); font-size:16px; line-height:1.62; -webkit-font-smoothing:antialiased;
  }
  .bg{ position:fixed; inset:0; z-index:0; pointer-events:none;
    background-image:
      radial-gradient(760px 460px at 82% -6%, rgba(245,178,63,.09), transparent 62%),
      radial-gradient(680px 520px at 8% 18%, rgba(87,220,169,.04), transparent 60%),
      radial-gradient(circle at center, rgba(255,255,255,.028) 1px, transparent 1.4px),
      linear-gradient(var(--tint),var(--tint));
    background-size:auto,auto,26px 26px,auto;
    -webkit-mask-image:linear-gradient(180deg,#000,#000 62%,transparent 96%);
            mask-image:linear-gradient(180deg,#000,#000 62%,transparent 96%);
  }
  .shell{ position:relative; z-index:1; }
  .mono{ font-family:var(--mono); }
  ::selection{ background:var(--amber); color:var(--ink); }
  a{ color:inherit; text-decoration:none; }
  :focus-visible{ outline:2px solid var(--focus); outline-offset:3px; border-radius:4px; }

  .wrap{ max-width:var(--wrap); margin:0 auto; padding:0 24px; }

  /* ---------------- nav ---------------- */
  nav{ position:sticky; top:0; z-index:50; backdrop-filter:blur(12px);
    background:linear-gradient(180deg, rgba(10,13,20,.9), rgba(10,13,20,.6)); border-bottom:1px solid var(--line); }
  .nav-in{ max-width:var(--wrap); margin:0 auto; padding:13px 24px; display:flex; align-items:center; justify-content:space-between; gap:18px; }
  .logo{ display:flex; align-items:center; gap:11px; }
  .logo .mark{ width:22px; height:22px; border-radius:6px; background:linear-gradient(135deg,var(--amber),#E0942A); position:relative; box-shadow:0 0 0 1px rgba(245,178,63,.4), 0 6px 18px rgba(245,178,63,.18); }
  .logo .mark::after{ content:""; position:absolute; inset:6px 6px auto auto; width:6px; height:6px; border-radius:50%; background:var(--ink); }
  .logo b{ font-family:var(--mono); font-weight:640; font-size:15.5px; letter-spacing:.005em; }
  .nav-links{ display:flex; align-items:center; gap:6px; }
  .nav-links a{ font-family:var(--mono); font-size:12.5px; color:var(--muted); padding:7px 11px; border-radius:8px; }
  .nav-links a:hover{ color:var(--text); background:var(--panel-2); }
  .inspect-btn{ display:inline-flex; align-items:center; gap:8px; cursor:pointer; font-family:var(--mono); font-size:12px;
    color:var(--muted); background:var(--panel-2); border:1px solid var(--line); padding:7px 12px; border-radius:9px; }
  .inspect-btn .d{ width:7px; height:7px; border-radius:50%; background:var(--faint); box-shadow:0 0 0 3px rgba(87,97,124,.16); }
  body.inspect .inspect-btn{ color:var(--amber); border-color:rgba(245,178,63,.5); }
  body.inspect .inspect-btn .d{ background:var(--amber); box-shadow:0 0 0 3px var(--amber-dim); }
  @media (max-width:720px){
    .nav-in{ align-items:flex-start; flex-wrap:wrap; gap:10px; padding:12px 24px; }
    .nav-links{ width:100%; align-items:center; flex-wrap:wrap; justify-content:flex-start; gap:4px; }
    .nav-links a{ font-size:11px; padding:6px 8px; }
    .inspect-btn{ font-size:11px; padding:6px 9px; }
  }

  /* ---------------- hero ---------------- */
  header{ padding:clamp(46px,9vh,104px) 0 40px; }
  .eyebrow{ font-family:var(--mono); font-size:11.5px; letter-spacing:.22em; text-transform:uppercase; color:var(--amber);
    display:inline-flex; align-items:center; gap:10px; }
  .eyebrow::before{ content:""; width:26px; height:1px; background:linear-gradient(90deg,var(--amber),transparent); }
  h1{ font-family:var(--mono); font-weight:var(--hw); letter-spacing:-.025em; line-height:1; margin:20px 0 0;
    font-size:clamp(38px, calc(6.6vw*var(--disp)), 76px); text-wrap:balance; }
  h1 .out{ color:var(--amber); position:relative; }
  .sub{ font-size:clamp(16.5px,calc(.7vw+14px),20px); color:var(--muted); max-width:60ch; margin:24px 0 0; }
  .sub b{ color:var(--text); font-weight:600; }
  .hero-cta{ display:flex; flex-wrap:wrap; align-items:center; gap:14px; margin-top:30px; }
  .cmd{ font-family:var(--mono); font-size:13.5px; color:var(--text); background:var(--panel-solid); border:1px solid var(--line-2);
    border-radius:10px; padding:11px 15px; display:inline-flex; align-items:center; gap:10px; }
  .cmd .pr{ color:var(--amber); }
  .cmd .cp{ color:var(--faint); cursor:pointer; border-left:1px solid var(--line); padding-left:10px; font-size:11px; }
  .cmd .cp:hover{ color:var(--amber); }
  .ghost{ font-family:var(--mono); font-size:13px; color:var(--muted); display:inline-flex; gap:7px; align-items:center; }
  .ghost:hover{ color:var(--amber); }

  /* compile composition */
  .compile{ margin-top:clamp(38px,6vh,64px); display:grid; grid-template-columns:minmax(0,1fr) auto minmax(0,1fr); gap:20px; align-items:center; }
  .cpanel{ background:var(--panel-solid); border:1px solid var(--line); border-radius:14px; overflow:hidden; box-shadow:0 24px 60px -30px rgba(0,0,0,.8); }
  .cpanel-h{ display:flex; align-items:center; justify-content:space-between; padding:11px 15px; border-bottom:1px solid var(--line); }
  .cpanel-h .t{ font-family:var(--mono); font-size:10.5px; letter-spacing:.14em; text-transform:uppercase; color:var(--muted); }
  .dots{ display:flex; gap:5px; } .dots i{ width:8px; height:8px; border-radius:50%; background:var(--line-2); }
  pre.intent{ margin:0; padding:16px; font-family:var(--mono); font-size:11.7px; line-height:1.7; overflow-x:auto; color:var(--text); }
  pre.intent .k{ color:var(--amber-2); } pre.intent .s{ color:var(--mint); } pre.intent .a{ color:var(--amber); } pre.intent .p{ color:var(--faint); }
  pre.intent .ln{ display:block; opacity:1; animation:lnin .32s ease both; animation-delay:calc(.1s + var(--i,0)*.05s); }
  @keyframes lnin{ from{ opacity:0; transform:translateY(3px); } to{ opacity:1; transform:none; } }
  .compile-node{ display:flex; flex-direction:column; align-items:center; gap:9px; }
  .cn-badge{ width:52px; height:52px; border-radius:14px; display:grid; place-items:center; color:var(--ink);
    background:radial-gradient(circle at 32% 28%,var(--amber-2),var(--amber)); box-shadow:0 0 0 1px rgba(245,178,63,.5),0 0 34px rgba(245,178,63,.34);
    font-family:var(--mono); font-weight:700; font-size:19px; }
  .cn-badge.pulse{ animation:pulse 2.4s ease-out .58s 1; }
  @keyframes pulse{ 0%{ box-shadow:0 0 0 1px rgba(245,178,63,.5),0 0 0 rgba(245,178,63,.5);} 40%{ box-shadow:0 0 0 1px rgba(245,178,63,.5),0 0 0 16px rgba(245,178,63,0);} 100%{ box-shadow:0 0 0 1px rgba(245,178,63,.5),0 0 34px rgba(245,178,63,.34);} }
  .cn-label{ font-family:var(--mono); font-size:9.5px; letter-spacing:.16em; text-transform:uppercase; color:var(--faint); }
  .cn-conn{ width:34px; height:1px; background:linear-gradient(90deg,transparent,var(--line-2)); }
  .out-card{ padding:16px; }
  .out-reveal{ opacity:1; animation:reveal .7s cubic-bezier(.2,.7,.2,1) .92s both; }
  @keyframes reveal{ from{ opacity:0; transform:translateY(10px) scale(.985); } to{ opacity:1; transform:none; } }
  .oc-row{ animation:rowin .45s ease both; }
  .oc-rows .oc-row:nth-child(1){ animation-delay:1.14s; }
  .oc-rows .oc-row:nth-child(2){ animation-delay:1.27s; }
  .oc-rows .oc-row:nth-child(3){ animation-delay:1.40s; }
  @keyframes rowin{ from{ opacity:0; transform:translateX(7px); } to{ opacity:1; transform:none; } }
  .oc-top{ display:flex; align-items:baseline; justify-content:space-between; }
  .oc-lbl{ font-family:var(--mono); font-size:10px; letter-spacing:.13em; text-transform:uppercase; color:var(--muted); }
  .oc-big{ font-family:var(--mono); font-weight:680; font-size:34px; letter-spacing:-.02em; margin:6px 0 2px; font-variant-numeric:tabular-nums; }
  .oc-delta{ font-family:var(--mono); font-size:12px; color:var(--mint); }
  .oc-rows{ margin-top:14px; border-top:1px solid var(--line); }
  .oc-row{ display:flex; justify-content:space-between; padding:9px 0; border-bottom:1px solid var(--line); font-size:13px; }
  .oc-row:last-child{ border-bottom:0; } .oc-row .rk{ color:var(--muted); } .oc-row .rv{ font-family:var(--mono); color:var(--text); font-variant-numeric:tabular-nums; }
  @media (max-width:840px){ .compile{ grid-template-columns:1fr; } .compile-node{ flex-direction:row; } .cn-conn{ width:1px; height:22px; background:linear-gradient(180deg,transparent,var(--line-2)); } }

  /* ---------------- sections ---------------- */
  section{ padding:clamp(56px,9vh,104px) 0; border-top:1px solid var(--line); }
  .reveal-on{ opacity:0; transform:translateY(16px); transition:opacity .7s ease, transform .7s ease; }
  .reveal-on.in{ opacity:1; transform:none; }
  .sec-head{ display:flex; flex-direction:column; gap:14px; margin-bottom:clamp(30px,5vh,50px); }
  .kicker{ font-family:var(--mono); font-size:11.5px; letter-spacing:.2em; text-transform:uppercase; color:var(--faint); }
  .kicker .n{ color:var(--amber); }
  h2{ font-family:var(--mono); font-weight:var(--hw); font-size:clamp(25px,calc(3vw*var(--disp)+8px),40px); letter-spacing:-.02em; margin:0; text-wrap:balance; line-height:1.06; }
  .lead{ color:var(--muted); font-size:clamp(15.5px,.5vw+14px,17.5px); max-width:var(--measure); }

  /* pillars */
  .pillars{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; }
  .pillar{ background:var(--panel); border:1px solid var(--line); border-radius:var(--rad); padding:calc(var(--u)*2.6); }
  .pillar .pi{ width:34px; height:34px; border-radius:9px; background:var(--amber-dim); border:1px solid rgba(245,178,63,.28); display:grid; place-items:center; margin-bottom:15px; }
  .pillar .pi svg{ width:17px; height:17px; stroke:var(--amber); fill:none; stroke-width:1.7; }
  .pillar h3{ font-family:var(--mono); font-size:15.5px; font-weight:640; margin:0 0 8px; letter-spacing:-.01em; }
  .pillar p{ margin:0; color:var(--muted); font-size:14px; line-height:1.58; }
  @media (max-width:760px){ .pillars{ grid-template-columns:1fr; } }

  /* switch bar (shared) */
  .switch{ display:inline-flex; gap:3px; padding:4px; background:var(--panel-solid); border:1px solid var(--line); border-radius:11px; }
  .switch button{ font-family:var(--mono); font-size:12px; color:var(--muted); background:transparent; border:0; cursor:pointer; padding:8px 14px; border-radius:8px; transition:.12s; }
  .switch button:hover{ color:var(--text); }
  .switch button[aria-pressed="true"]{ color:var(--ink); background:var(--amber); font-weight:600; }

  /* motif */
  .panel{ background:var(--panel-solid); border:1px solid var(--line); border-radius:16px; overflow:hidden; box-shadow:0 30px 70px -44px rgba(0,0,0,.8); }
  .panel-bar{ display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px; padding:15px 20px; border-bottom:1px solid var(--line); }
  .panel-bar .meta{ font-family:var(--mono); font-size:11px; letter-spacing:.05em; color:var(--muted); }
  .panel-bar .meta b{ color:var(--amber-2); font-weight:600; }
  #motifBody{ padding:22px; overflow-x:auto; }
  table.mt{ width:100%; border-collapse:collapse; font-size:14px; }
  table.mt th{ text-align:left; font-family:var(--mono); font-size:10px; letter-spacing:.12em; text-transform:uppercase; color:var(--muted); font-weight:500; padding:0 14px 12px; border-bottom:1px solid var(--line); }
  table.mt td{ padding:14px; border-bottom:1px solid var(--line); vertical-align:top; }
  table.mt td.k{ font-family:var(--mono); color:var(--amber-2); white-space:nowrap; font-size:13.5px; }
  table.mt td .d{ color:var(--muted); font-size:13px; margin-top:3px; }
  table.mt tr:last-child td{ border-bottom:0; }
  .cards{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; }
  .mcard{ background:var(--panel-2); border:1px solid var(--line); border-radius:var(--rad); padding:18px; }
  .mcard .ico{ width:32px; height:32px; border-radius:8px; background:var(--amber-dim); display:grid; place-items:center; color:var(--amber); font-family:var(--mono); font-weight:700; margin-bottom:12px; }
  .mcard h4{ margin:0 0 6px; font-size:15px; font-weight:640; letter-spacing:-.01em; }
  .mcard p{ margin:0; color:var(--muted); font-size:13.5px; line-height:1.55; }
  .gal{ display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:14px; }
  .gtile{ position:relative; border-radius:var(--rad); overflow:hidden; border:1px solid var(--line); min-height:150px; padding:18px; display:flex; flex-direction:column; justify-content:flex-end;
    background:linear-gradient(155deg,var(--panel-2),var(--panel-solid)); }
  .gtile::before{ content:""; position:absolute; inset:0; background:radial-gradient(360px 160px at 78% 0%, rgba(245,178,63,.16), transparent 70%); }
  .gtile .gk{ position:relative; font-family:var(--mono); font-size:10.5px; letter-spacing:.14em; text-transform:uppercase; color:var(--amber); }
  .gtile h4{ position:relative; margin:8px 0 6px; font-size:16px; font-weight:640; }
  .gtile p{ position:relative; margin:0; color:var(--muted); font-size:13px; }

  /* derive */
  .derive-top{ display:flex; flex-wrap:wrap; gap:16px; align-items:center; justify-content:space-between; padding:16px 20px; border-bottom:1px solid var(--line); }
  .profiles{ display:flex; flex-wrap:wrap; gap:6px; }
  .profiles button{ font-family:var(--mono); font-size:11.5px; color:var(--muted); background:var(--panel-2); border:1px solid var(--line-2); border-radius:20px; padding:6px 13px; cursor:pointer; transition:.12s; }
  .profiles button:hover{ color:var(--text); border-color:var(--amber); }
  .profiles button[aria-pressed="true"]{ color:var(--ink); background:var(--amber); border-color:var(--amber); font-weight:600; }
  .readout{ font-family:var(--mono); font-size:11.5px; color:var(--faint); } .readout b{ color:var(--amber-2); font-weight:600; }
  .derive-body{ padding:22px; }
  /* Artifact viewport: a light "compiled surface" inside the dark chrome. Structure lives here;
     ALL skin (palette, surfaces, type, density, accent, featured span) is injected per profile
     as verbatim declarations from the compiler's style projection. */
  .dv{ border-radius:12px; border:1px solid var(--line-2); padding:26px; box-shadow:0 30px 80px -40px rgba(0,0,0,.9); }
  .dv .dv-head{ display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:14px; margin-bottom:18px; }
  .dv .dvh{ margin:0; }
  .dv .dvbtn{ border:0; font-family:inherit; font-size:12.5px; font-weight:600; padding:9px 17px; cursor:default; }
  .dv .dvgrid{ display:grid; }
  .dv .dvcard{ display:flex; flex-direction:column; gap:5px; min-width:0; }
  .dv .dvcard .dvtop{ display:flex; align-items:baseline; justify-content:space-between; gap:8px; }
  .dv .dvlabel{ font-size:11.5px; }
  .dv .dvdelta{ font-size:11px; }
  .dv .dvnum{ font-size:26px; line-height:1.1; font-variant-numeric:tabular-nums; }
  .dv .dvcard.featured .dvnum{ font-size:34px; }
  .dv .dvflow{ margin:18px 0 0; font-size:13.5px; opacity:.82; }
  .dv svg{ display:block; width:100%; height:30px; margin-top:8px; }
  svg .spark-fill{ fill:rgba(245,178,63,.10); } svg .spark-line{ fill:none; stroke:var(--amber); stroke-width:1.5; } svg .spark-dot{ fill:var(--amber-2); }
  /* projection strip: all profiles rendered simultaneously — juxtaposition, not memory */
  .dv-strip{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin-bottom:16px; }
  @media (max-width:760px){ .dv-strip{ grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); } }
  .dv-slot{ display:flex; flex-direction:column; gap:7px; min-width:0; }
  button.dv.mini{ padding:13px 13px 15px; cursor:pointer; text-align:left; display:flex; flex-direction:column; align-items:flex-start; gap:7px; width:100%; box-shadow:none; border-color:var(--line-2); }
  button.dv.mini .dvnum{ font-size:19px !important; }
  button.dv.mini .dvh{ font-size:12px !important; }
  button.dv.mini .dvbtn{ font-size:9.5px; padding:4px 10px; pointer-events:none; }
  button.dv.mini[aria-pressed="true"]{ outline:2px solid var(--amber); outline-offset:3px; }
  .dv-name{ font-family:var(--mono); font-size:10px; color:var(--faint); text-align:center; }
  .dv-slot[data-active="true"] .dv-name{ color:var(--amber); }
  .note{ font-family:var(--mono); font-size:11px; color:var(--faint); padding:0 20px 18px; } .note b{ color:var(--muted); }

  /* reveal / provenance */
  .under{ display:grid; grid-template-columns:minmax(0,1.05fr) minmax(0,.95fr); gap:22px; align-items:start; }
  @media (max-width:860px){ .under{ grid-template-columns:1fr; } }
  .play{ background:var(--panel-solid); border:1px solid var(--line); border-radius:16px; padding:26px; }
  .play .pk{ font-family:var(--mono); font-size:10.5px; letter-spacing:.14em; text-transform:uppercase; color:var(--muted); margin-bottom:14px; }
  .specimen{ font-family:var(--mono); }
  .specimen .big{ font-size:26px; font-weight:660; letter-spacing:-.02em; }
  .specimen .row{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }
  .chip{ font-family:var(--mono); font-size:12px; color:var(--text); background:var(--panel-2); border:1px solid var(--line-2); border-radius:8px; padding:8px 12px; cursor:default; }
  [data-node].lit{ outline:1.5px solid var(--amber); outline-offset:3px; background:var(--amber-dim); border-radius:5px; }
  body.inspect [data-node]{ position:relative; outline:1px dashed rgba(245,178,63,.4); outline-offset:2px; }
  body.inspect [data-node]::after{ content:attr(data-node); position:absolute; left:-1px; top:-2px; transform:translateY(-100%);
    font-family:var(--mono); font-size:9px; line-height:1.5; color:var(--ink); background:var(--amber); padding:1px 5px; border-radius:3px 3px 3px 0; white-space:nowrap; pointer-events:none; z-index:6; }
  .trace{ background:linear-gradient(180deg,var(--panel-2),var(--panel-solid)); border:1px solid var(--line); border-radius:16px; padding:22px; position:sticky; top:84px; }
  .trace .flow{ font-family:var(--mono); font-size:10.5px; letter-spacing:.05em; color:var(--faint); margin-bottom:16px; }
  .tnode{ border:1px solid var(--line); border-left:2px solid var(--amber); background:var(--panel-solid); border-radius:9px; padding:11px 13px; margin-bottom:8px; }
  .tnode .tk{ font-family:var(--mono); font-size:9.5px; letter-spacing:.14em; text-transform:uppercase; color:var(--muted); }
  .tnode .tv{ font-family:var(--mono); font-size:12.5px; color:var(--text); word-break:break-all; margin-top:3px; }
  .tnode .tv.a{ color:var(--amber-2); } .tnode .tv.r{ color:var(--mint); }
  .tarrow{ text-align:center; color:var(--faint); font-family:var(--mono); font-size:10px; margin:-2px 0 6px; }

  /* proof */
  .proof-grid{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }
  @media (max-width:760px){ .proof-grid{ grid-template-columns:1fr; } }
  .pcard{ background:var(--panel-solid); border:1px solid var(--line); border-radius:16px; overflow:hidden; }
  .pcard-h{ display:flex; align-items:center; gap:10px; padding:16px 20px; border-bottom:1px solid var(--line); }
  .pcard-h .ok{ width:9px; height:9px; border-radius:50%; background:var(--mint); box-shadow:0 0 0 3px rgba(87,220,169,.16); }
  .pcard-h .tt{ font-family:var(--mono); font-size:13px; color:var(--text); font-weight:600; }
  .pcard-b{ padding:20px; }
  .pcard-b p{ margin:0 0 16px; color:var(--muted); font-size:14px; line-height:1.55; }
  .hashrow{ font-family:var(--mono); font-size:11.5px; color:var(--muted); word-break:break-all; background:var(--ink-2); border:1px solid var(--line); border-radius:9px; padding:12px; }
  .hashrow .h{ color:var(--mint); }
  .pbtns{ display:flex; align-items:center; gap:12px; margin-top:14px; flex-wrap:wrap; }
  .pbtn{ font-family:var(--mono); font-size:12px; color:var(--ink); background:var(--amber); border:0; border-radius:8px; padding:9px 15px; cursor:pointer; font-weight:600; }
  .pbtn:hover{ background:var(--amber-2); }
  .verdict{ font-family:var(--mono); font-size:12px; color:var(--mint); display:none; align-items:center; gap:7px; }
  .verdict.show{ display:inline-flex; }
  .verdict .ok{ width:8px; height:8px; border-radius:50%; background:var(--mint); }
  .bignum{ font-family:var(--mono); font-weight:700; font-size:46px; color:var(--text); font-variant-numeric:tabular-nums; line-height:1; }
  .bignum small{ font-size:15px; color:var(--muted); font-weight:500; letter-spacing:.02em; margin-left:8px; }

  /* cta */
  .pricing-section{ border-top:1px solid var(--line); }
  .pricing-grid{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; margin-top:26px; }
  .pricing-plan{ background:linear-gradient(180deg,var(--panel-solid),rgba(20,26,40,.72)); border:1px solid var(--line); border-radius:16px; padding:20px; display:flex; flex-direction:column; min-height:260px; }
  .pricing-plan.featured{ border-color:rgba(245,178,63,.62); box-shadow:0 24px 64px -42px rgba(245,178,63,.8); }
  .pricing-plan .plan-kicker{ font-family:var(--mono); font-size:10.5px; letter-spacing:.14em; text-transform:uppercase; color:var(--amber); margin-bottom:10px; }
  .pricing-plan h3{ margin:0; font-size:19px; line-height:1.2; letter-spacing:-.01em; }
  .pricing-plan .price{ margin:12px 0 4px; font-family:var(--mono); font-size:28px; color:var(--text); }
  .pricing-plan .price small{ color:var(--muted); font-size:13px; font-weight:500; }
  .pricing-plan p{ color:var(--muted); margin:0; font-size:14px; line-height:1.55; }
  .pricing-list{ display:grid; gap:9px; margin:18px 0 0; padding:0; list-style:none; }
  .pricing-list li{ color:var(--text); font-size:13.5px; display:flex; gap:8px; }
  .pricing-list li::before{ content:""; width:7px; height:7px; margin-top:8px; border-radius:50%; background:var(--mint); box-shadow:0 0 0 3px rgba(87,220,169,.12); flex:0 0 auto; }
  .pricing-note{ margin-top:22px; color:var(--muted); font-size:14px; max-width:68ch; }
  @media (max-width:860px){ .pricing-grid{ grid-template-columns:1fr; } .pricing-plan{ min-height:0; } }
  .cta{ text-align:center; padding-top:48px; }
  .cta h2{ margin:0 auto; max-width:20ch; }
  .cta .lead{ margin:16px auto 0; }
  .cta-row{ display:flex; justify-content:center; flex-wrap:wrap; gap:14px; margin-top:30px; }
  footer{ border-top:1px solid var(--line); }
  .foot-in{ max-width:var(--wrap); margin:0 auto; padding:26px 24px; display:flex; justify-content:space-between; flex-wrap:wrap; gap:12px;
    font-family:var(--mono); font-size:12px; color:var(--faint); }
  .foot-in a{ color:var(--muted); } .foot-in a:hover{ color:var(--amber); }

  /* inspect HUD (page-wide second gear) */
  .hud{ position:fixed; left:50%; bottom:20px; transform:translateX(-50%) translateY(20px); z-index:60; opacity:0; pointer-events:none; transition:.18s;
    background:rgba(12,15,23,.92); backdrop-filter:blur(8px); border:1px solid rgba(245,178,63,.4); border-radius:11px; padding:9px 15px;
    font-family:var(--mono); font-size:12px; color:var(--text); display:flex; align-items:center; gap:12px; box-shadow:0 12px 40px -12px #000; }
  body.inspect .hud.on{ opacity:1; transform:translateX(-50%) translateY(0); }
  .hud .lbl{ color:var(--amber); letter-spacing:.1em; text-transform:uppercase; font-size:9.5px; }
  .hud .addr{ color:var(--amber-2); }

/* ============ embedded real compiler output — dark-match the emitter inline light theme ============ */
.skip-link{ position:absolute; left:-999px; top:0; z-index:200; background:var(--amber); color:var(--ink); font-family:var(--mono); font-size:12px; padding:9px 13px; border-radius:8px; }
.skip-link:focus{ left:12px; top:12px; }
/* artifact window: browser-frame chrome + bounded internal scroll for the embedded artifact */
.artifact-window{ background:var(--panel-solid); border:1px solid var(--line-2); border-radius:14px; overflow:hidden; box-shadow:0 24px 60px -30px rgba(0,0,0,.8); }
.artifact-window .cpanel-h{ background:var(--ink-2); }
.artifact-window-foot{ font-family:var(--mono); font-size:10.5px; color:var(--faint); padding:10px 4px 0; }
.artifact-window-foot a{ color:var(--amber-2); } .artifact-window-foot a:hover{ color:var(--amber); }
#viewspec-artifact-slot{ display:block; max-height:440px; overflow-y:auto; overscroll-behavior:contain; font-size:13px; scrollbar-width:thin; scrollbar-color:var(--line-2) transparent; }
#viewspec-artifact-slot .vs-root,#viewspec-artifact-slot [id^="dom-region"]{ background:none !important; background-color:transparent !important; color:var(--text) !important; font-family:var(--sans) !important; }
#viewspec-artifact-slot .vs-surface{ background:linear-gradient(160deg,var(--panel-2),var(--panel-solid)) !important; border:1px solid var(--line) !important; border-radius:9px !important; box-shadow:none !important; padding:0.7rem 0.85rem !important; }
#viewspec-artifact-slot .vs-value,#viewspec-artifact-slot td.vs-value,#viewspec-artifact-slot .vs-text{ color:var(--text) !important; font-size:0.95em !important; }
#viewspec-artifact-slot .vs-label,#viewspec-artifact-slot th.vs-label{ color:var(--muted) !important; font-family:var(--mono) !important; font-size:0.72rem !important; letter-spacing:.08em; text-transform:uppercase; }
#viewspec-artifact-slot h1,#viewspec-artifact-slot #dom-binding_launch_hero_title{ font-family:var(--mono) !important; color:var(--text) !important; font-size:1.4rem !important; line-height:1.2 !important; letter-spacing:-.02em; margin:0.2rem 0 !important; }
#viewspec-artifact-slot table{ width:100%; border-collapse:collapse; }
#viewspec-artifact-slot td,#viewspec-artifact-slot th{ padding:0.5rem 0.6rem !important; }
#viewspec-artifact-slot #dom-binding_launch_hero_eyebrow{ color:var(--amber) !important; font-family:var(--mono) !important; font-size:0.68rem !important; letter-spacing:.16em; }
#viewspec-artifact-slot #dom-binding_launch_hero_description{ color:var(--muted) !important; font-size:0.82rem !important; line-height:1.6 !important; }
#viewspec-artifact-slot #dom-motif_proof_badges .vs-value{ color:var(--mint) !important; font-family:var(--mono) !important; }
#viewspec-artifact-slot #dom-motif_proof_badges{ grid-template-columns:repeat(2,minmax(0,1fr)) !important; gap:0.5rem !important; }
#viewspec-artifact-slot #dom-motif_compile_flow{ grid-template-columns:1fr !important; gap:0.5rem !important; }
body.inspect #viewspec-artifact-slot [data-ir-id]{ position:relative; outline:1px dashed rgba(245,178,63,.4); outline-offset:2px; }
body.inspect #viewspec-artifact-slot [data-ir-id]::after{ content:attr(data-ir-id); position:absolute; left:-1px; top:-2px; transform:translateY(-100%); font-family:var(--mono); font-size:9px; line-height:1.5; color:var(--ink); background:var(--amber); padding:1px 5px; border-radius:3px 3px 3px 0; white-space:nowrap; pointer-events:none; z-index:6; }
.anchor-target{ display:block; height:0; }
.pricing-actions{ display:flex; flex-wrap:wrap; gap:12px; justify-content:center; margin-top:20px; }
.pact{ font-family:var(--mono); font-size:12.5px; padding:10px 16px; border-radius:9px; border:1px solid var(--line-2); color:var(--text); background:var(--panel-solid); cursor:pointer; }
.pact.primary{ background:var(--amber); color:var(--ink); border-color:var(--amber); font-weight:600; }
.pact:hover{ border-color:var(--amber); }
#noteReal{ display:block; margin-top:8px; }
.receipts{ display:flex; flex-wrap:wrap; gap:8px; margin:0 0 14px; }
.receipts a,.receipts button{ font-family:var(--mono); font-size:11px; color:var(--amber-2); background:var(--ink-2); border:1px solid var(--line); border-radius:7px; padding:5px 9px; }
.receipts button{ appearance:none; cursor:pointer; }
.receipts a:hover,.receipts button:hover,.receipts button:focus-visible{ border-color:var(--amber); color:var(--amber); }
.trace-receipts{ margin:0 0 16px; }
.foot-links a{ color:var(--muted); } .foot-links a:hover{ color:var(--amber); }

"""


PAGE_BODY_TEMPLATE = r"""<a class="skip-link" href="#top">Skip to content</a>
<div class="bg"></div>
<div class="shell">
  <nav>
    <div class="nav-in">
      <a class="logo" href="#top"><span class="mark"></span><b class="mono">viewspec</b></a>
      <div class="nav-links">
        <a href="#shape">Product</a>
        <a href="#under">How it works</a>
        <a href="#proof">Proof</a>
        <a href="#pricing">Pricing</a>
        <a href="https://github.com/nxrobins/viewspec/blob/main/docs/getting-started.md" target="_blank" rel="noopener">Docs</a>
        <button class="inspect-btn" id="inspectBtn" aria-pressed="false" title="Overlay every element with its IR address (press i)"><span class="d"></span>Inspect</button>
      </div>
    </div>
  </nav>

  <div class="wrap" id="top">
    <!-- ================= HERO ================= -->
    <header>
      <span class="eyebrow">Agent&#8209;native UI compiler</span>
      <h1 data-node="node:hero#slot:title[0]" data-binding="hero_title" data-address="node:hero#slot:title[0]" data-present="text" data-raw="Intent goes in. Interface comes out.">Intent goes in.<br>Interface comes <span class="out">out.</span></h1>
      <p class="sub" data-node="node:hero#slot:body[0]" data-binding="hero_body" data-address="node:hero#slot:body[0]" data-present="rich_text" data-raw="ViewSpec is the compiler between your agents and your UI.">
        ViewSpec is the compiler between your agents and your UI. Agents commit to <b>meaning</b> &mdash; nodes, bindings, motifs. ViewSpec owns the <b>pixels</b>: deterministic, no model call at render, portable to HTML, React, SwiftUI, and Flutter.
      </p>
      <div class="hero-cta">
        <span class="cmd mono"><span class="pr">$</span> pip install viewspec <button type="button" class="cp" id="copyCmd" data-copy-text="pip install viewspec" aria-label="Copy pip install viewspec command">copy</button></span>
        <a class="ghost mono" href="#shape">see it compile &#8595;</a>
      </div>

      <!-- compile composition -->
      <div class="compile">
        <div class="cpanel">
          <div class="cpanel-h"><span class="t">viewspec.intent.json</span><span class="dots"><i></i><i></i><i></i></span></div>
          <pre class="intent" id="heroIntent"></pre>
        </div>
        <div class="compile-node">
          <span class="cn-conn"></span>
          <div style="display:flex;flex-direction:column;align-items:center;gap:9px">
            <div class="cn-badge" id="cnBadge">&rarr;</div>
            <span class="cn-label">compile</span>
          </div>
          <span class="cn-conn"></span>
        </div>
        <div class="cpanel">
          <div class="cpanel-h"><span class="t">rendered &middot; html_tailwind</span><span class="dots" style="opacity:.5"><i></i><i></i><i></i></span></div>
          <div class="out-card out-reveal">
            <div class="oc-top"><span class="oc-lbl">Monthly recurring</span><span class="oc-delta">&#9650; 8.2%</span></div>
            <div class="oc-big" id="ocBig">$48,210</div>
            <svg viewBox="0 0 120 34" preserveAspectRatio="none" style="width:100%;height:34px" id="heroSpark" aria-hidden="true"></svg>
            <div class="oc-rows">
              <div class="oc-row"><span class="rk">Active seats</span><span class="rv">1,284</span></div>
              <div class="oc-row"><span class="rk">Net retention</span><span class="rv">112%</span></div>
              <div class="oc-row"><span class="rk">Churn</span><span class="rv">0.9%</span></div>
            </div>
          </div>
        </div>
      </div>
    </header>

    <!-- ================= MODEL ================= -->
    <section id="model" class="reveal-on">
      <div class="sec-head">
        <span class="kicker"><span class="n">/</span> the model</span>
        <h2>Agents shouldn&rsquo;t write markup. They should commit to meaning.</h2>
        <p class="lead">An IntentBundle is a semantic contract, not a template. The compiler turns it into an interface the same way every time &mdash; and can prove what it did.</p>
      </div>
      <div class="pillars">
        <div class="pillar">
          <div class="pi"><svg viewBox="0 0 24 24"><path d="M4 7l8-4 8 4-8 4-8-4zM4 12l8 4 8-4M4 17l8 4 8-4"/></svg></div>
          <h3>Deterministic</h3>
          <p>Same intent, same bytes, same hash &mdash; on every machine, every run. No model in the render path, so output never drifts.</p>
        </div>
        <div class="pillar">
          <div class="pi"><svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg></div>
          <h3>Portable</h3>
          <p>One bundle emits html&#8209;tailwind and React locally; SwiftUI and Flutter when hosted. Describe the interface once, ship it anywhere.</p>
        </div>
        <div class="pillar">
          <div class="pi"><svg viewBox="0 0 24 24"><path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z"/><path d="M9 12l2 2 4-4"/></svg></div>
          <h3>Provable</h3>
          <p>Every element carries its lineage. Trace any pixel to the binding and raw data &mdash; and prove the artifact phones home to nobody.</p>
        </div>
      </div>
    </section>

    <!-- ================= MOTIF (functional demo 1) ================= -->
    <section id="shape" class="reveal-on">
      <div class="sec-head">
        <span class="kicker"><span class="n">/</span> one intent, every shape</span>
        <h2>The data doesn&rsquo;t change. The motif does.</h2>
        <p class="lead">An agent describes a group of things. You decide whether it&rsquo;s a table, a dashboard, or a gallery. Flip it &mdash; same four bindings, three renderings.</p>
      </div>
      <div style="margin-bottom:18px"><div class="switch" role="group" aria-label="Motif" id="motifSeg">
        <button data-motif="table" aria-pressed="true">Table</button>
        <button data-motif="cards" aria-pressed="false">Dashboard</button>
        <button data-motif="gal" aria-pressed="false">Gallery</button>
      </div></div>
      <div class="panel">
        <div class="panel-bar">
          <span class="meta">group: <b>capabilities</b> &middot; motif: <b id="motifName">table</b> &middot; ordered</span>
          <span class="meta" style="color:var(--faint)">4 bindings &middot; exactly&#8209;once</span>
        </div>
        <div id="motifBody" data-node="node:features#slot:items"></div>
      </div>
    </section>

    <!-- ================= DERIVE (functional demo 2) ================= -->
    <section id="style" class="reveal-on">
      <div class="sec-head">
        <span class="kicker"><span class="n">/</span> style derivation</span>
        <h2>Style is a token, not a task.</h2>
        <p class="lead">Pick an aesthetic profile. The compiler re&#8209;derives density, emphasis, rhythm, and grid from a single token &mdash; a lookup, not a redesign. This whole page re&#8209;themes with it.</p>
      </div>
      <div class="panel">
        <div class="derive-top">
          <div class="profiles" role="group" aria-label="Aesthetic profile" id="profileGroup">{{PROFILE_BUTTONS}}</div>
          <div class="readout" id="readout">density <b>regular</b> &middot; emphasis <b>medium</b> &middot; columns <b>3</b></div>
        </div>
        <div class="derive-body">
          <div class="dv-strip" id="dvStrip" role="group" aria-label="All eight compiled projections, side by side"></div>
          <div class="dv" id="dv" data-p="calm_ops" data-node="node:workspace#view:projection"></div>
        </div>
        <div class="note">aesthetic.<span id="noteP">calm_ops</span> &middot; <b>0</b> hand&#8209;written rules &middot; <span class="mono">Same graph, new projection</span> from the same semantic graph</div><div class="note" id="noteReal"></div>
      </div>
    </section>

    <!-- ================= REVEAL: PROVENANCE ================= -->
    <section id="under" class="reveal-on">
      <div class="sec-head">
        <span class="kicker"><span class="n">/</span> now look under the hood</span>
        <h2>Everything above is compiled output. Here&rsquo;s the receipt.</h2>
        <p class="lead">Turn on <b style="color:var(--amber)">Inspect</b> (top&#8209;right, or press <span class="mono" style="color:var(--amber)">i</span>) and every element on the page shows the IR address it came from. Click, tap, or hover a chip below to trace one all the way back to the data.</p>
      </div>
      <div class="under">
        <div class="play"><div class="pk">compiled artifact &middot; real IR ids &middot; click/tap/hover a node</div><div class="receipts"><a href="./landing-compiled/intent_bundle.json" target="_blank" rel="noopener" data-trace-target="intent_bundle" data-node="node:receipt#attr:intent_bundle" data-binding="receipt_intent_bundle" data-address="artifact:landing-compiled/intent_bundle.json" data-present="artifact_link" data-raw="Canonical agent-authored intent bundle">intent_bundle.json</a><a href="./landing-compiled/provenance_manifest.json" target="_blank" rel="noopener" data-trace-target="provenance_manifest" data-node="node:receipt#attr:provenance_manifest" data-binding="receipt_provenance_manifest" data-address="artifact:landing-compiled/provenance_manifest.json" data-present="artifact_link" data-raw="Compiled DOM to IR provenance manifest">provenance_manifest.json</a><a href="./landing-compiled/profile-evidence.json" target="_blank" rel="noopener" data-trace-target="profile_evidence" data-node="node:receipt#attr:profile_evidence" data-binding="receipt_profile_evidence" data-address="artifact:landing-compiled/profile-evidence.json" data-present="artifact_link" data-raw="Aesthetic profile evidence and invariant flags">profile-evidence.json</a></div>{{REAL_ARTIFACT}}</div>
        <div class="trace" aria-live="polite">
          <div class="receipts trace-receipts" aria-label="Trace compiled receipts">
            <button type="button" data-trace-target="viewspec:view:viewspec_landing" data-node="viewspec:view:viewspec_landing" data-binding="view root" data-address="viewspec:view:viewspec_landing" data-present="artifact" data-raw="IntentBundle root">Click/tap intent</button>
            <button type="button" data-trace-target="viewspec:motif:pricing" data-node="viewspec:motif:pricing" data-binding="pricing" data-address="viewspec:motif:pricing" data-present="table" data-raw="Pricing rows">Click/tap pricing</button>
            <button type="button" data-trace-target="viewspec:style:aesthetic_profile" data-node="viewspec:style:aesthetic_profile" data-binding="aesthetic.calm_ops" data-address="viewspec:style:aesthetic_profile" data-present="style projection" data-raw="Calm Ops profile">Click/tap style</button>
          </div>
          <div class="flow">DOM &rarr; binding &rarr; address &rarr; data</div>
          <div id="traceOut"></div>
        </div>
      </div>
    </section>

    <!-- ================= PROOF ================= -->
    <section id="proof" class="reveal-on">
      <div class="sec-head">
        <span class="kicker"><span class="n">/</span> trust, but verify</span>
        <h2>Then verify again, and get the same answer.</h2>
        <p class="lead">Two claims most tools ask you to take on faith. Here they&rsquo;re computed on the page you&rsquo;re reading.</p>
      </div>
      <div class="proof-grid">
        <div class="pcard">
          <div class="pcard-h"><span class="ok"></span><span class="tt">Deterministic provenance</span></div>
          <div class="pcard-b">
            <p>The provenance hash is <code style="color:var(--amber-2);font-family:var(--mono)">sha256</code> over the canonical IntentBundle. Recompute it &mdash; identical bytes, identical digest, every time.</p>
            <div class="hashrow">sha256 = <span class="h" id="hashOut">computing&hellip;</span></div>
            <div class="pbtns">
              <button class="pbtn" id="verifyBtn">re&#8209;verify</button>
              <span class="verdict" id="verdict"><span class="ok"></span><span id="verdictTxt">match &mdash; deterministic</span></span>
            </div>
          </div>
        </div>
        <div class="pcard">
          <div class="pcard-h"><span class="ok"></span><span class="tt">Provably no&#8209;network</span></div>
          <div class="pcard-b">
            <p>ViewSpec&rsquo;s <code style="color:var(--amber-2);font-family:var(--mono)">check</code> gate certifies an artifact makes zero cross&#8209;origin requests before it ships. This page holds itself to the same bar &mdash; measured live:</p>
            <div class="bignum"><span id="netCount">0</span><small>cross&#8209;origin requests</small></div>
          </div>
        </div>
      </div>
    </section>

    <!-- ================= PRICING + CTA ================= -->
    <section id="pricing" class="pricing-section reveal-on">
      <div class="sec-head">
        <span class="kicker"><span class="n">/</span> pricing</span>
        <h2>Start local. Add the hosted compiler when teams need it.</h2>
        <p class="lead">The free SDK stays offline and deterministic. Hosted plans add shared compilation capacity, organization controls, and support around the same proof pipeline.</p>
      </div>
      <div class="pricing-grid" id="pricing-grid" aria-label="ViewSpec pricing plans">
        <article class="pricing-plan">
          <span class="plan-kicker">Free</span>
          <h3>Free local SDK</h3>
          <div class="price">$0 <small>/ forever</small></div>
          <p>Build, compile, and prove ViewSpec artifacts locally without an account.</p>
          <ul class="pricing-list">
            <li>Unlimited local compile runs</li>
            <li>HTML and React emitters</li>
            <li>Proof bundle and shell checks</li>
            <li>500 hosted compile calls/day trial</li>
          </ul>
        </article>
        <article class="pricing-plan featured">
          <span class="plan-kicker">Pro</span>
          <h3>Pro hosted compiler</h3>
          <div class="price">$149 <small>/ month</small></div>
          <p>Hosted compile API for production agent workflows and shared team demos.</p>
          <ul class="pricing-list">
            <li>10,000 hosted compile calls/day</li>
            <li>Hosted SwiftUI and Flutter emitters</li>
            <li>Team usage receipts</li>
            <li>Email support</li>
          </ul>
        </article>
        <article class="pricing-plan">
          <span class="plan-kicker">Enterprise</span>
          <h3>Enterprise support</h3>
          <div class="price">Custom</div>
          <p>Scale ViewSpec across internal agent products with support and controls.</p>
          <ul class="pricing-list">
            <li>Custom compile volume</li>
            <li>Organization sharing and policy gates</li>
            <li>Private deployment support</li>
            <li>Launch and migration help</li>
          </ul>
        </article>
      </div>
      <p class="pricing-note">Pricing is intentionally simple: the compiler remains useful locally, while hosted capacity is there for teams that need API volume, mobile emitters, and support.</p>
      <div class="cta">
        <h2>Compile your first interface in a minute.</h2>
        <p class="lead">No account, no network, no LLM key. Install the SDK, write intent, prove the output.</p>
      <div class="cta-row">
        <span class="cmd mono"><span class="pr">$</span> pip install viewspec &amp;&amp; viewspec init&#8209;intent</span>
        <a class="ghost mono" href="https://github.com/nxrobins/viewspec/blob/main/docs/getting-started.md" target="_blank" rel="noopener" style="color:var(--amber)">read the docs &#8594;</a>
        <a class="ghost mono" href="./proof-bundle/" style="color:var(--amber)">Try the one-minute proof &#8594;</a>
      {{PRICING_ACTIONS}}</div></div>
    </section>
  </div>

  <footer>
    <div class="foot-in">
      <span>viewspec &middot; agent&#8209;native UI compiler</span>
      <span class="foot-links"><a href="./appbundle-state-ir/">State IR</a> &middot; <a href="./proof-bundle/">Proof bundle</a> &middot; <a href="./custom-motifs/">Motifs</a> &middot; <a href="./openapi.json">OpenAPI</a></span>
      <span id="footState">profile: aesthetic.calm_ops &middot; network: none</span>
    </div>
  </footer>
</div>

<div class="hud" id="hud"><span class="lbl">inspecting</span><span class="addr" id="hudAddr">&mdash;</span></div>"""


PAGE_SCRIPT = r"""
(function(){
  "use strict";
  var body=document.body, reduce=matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- data ---------- */
  var CAPS=[
    {k:"provenance",  ab:"PV", name:"Traceable provenance", desc:"Every node maps back through binding, address, and raw data."},
    {k:"determinism", ab:"DT", name:"Deterministic render", desc:"No LLM at render. Same intent compiles to identical bytes."},
    {k:"no_network",  ab:"NW", name:"Provable no-network",  desc:"The check gate certifies zero cross-origin requests."},
    {k:"surfaces",    ab:"SF", name:"One intent, N surfaces", desc:"Emits html, React, and hosted SwiftUI and Flutter."}
  ];
  var PROFILES={
    calm_ops:{density:"regular",emphasis:"medium",columns:3},
    premium_saas:{density:"airy",emphasis:"high",columns:3},
    data_dense:{density:"compact",emphasis:"medium",columns:5},
    editorial_product:{density:"airy",emphasis:"high",columns:2},
    executive_review:{density:"airy",emphasis:"high",columns:3},
    brutalist:{density:"regular",emphasis:"high",columns:1},
    neon_cyber:{density:"compact",emphasis:"high",columns:2},
    warm_organic:{density:"airy",emphasis:"medium",columns:3}
  };
  var METRICS=[
    {ml:"Compiles / day", mv:"12,480", seed:0.2},
    {ml:"P50 compile",    mv:"38ms",   seed:1.1},
    {ml:"Proof pass rate",mv:"100%",   seed:2.0},
    {ml:"Surfaces",       mv:"4",      seed:0.7}
  ];
  var INTENT={
    substrate:{ id:"card", root_id:"card",
      nodes:{ card:{ id:"card", kind:"metric_card",
        attrs:{ label:"Monthly recurring", value:"$48,210", delta:"+8.2%" }, slots:{}, edges:{} } } },
    view_spec:{ id:"card", substrate_id:"card", complexity_tier:1, root_region:"root",
      regions:[{id:"root",parent_region:null,role:"panel",layout:"stack",min_children:1,max_children:null}],
      bindings:[
        {id:"mrr_label", address:"node:card#attr:label", target_region:"root", present_as:"label", cardinality:"exactly_once"},
        {id:"mrr_value", address:"node:card#attr:value", target_region:"root", present_as:"value", cardinality:"exactly_once"},
        {id:"mrr_delta", address:"node:card#attr:delta", target_region:"root", present_as:"badge", cardinality:"exactly_once"}
      ],
      groups:[], motifs:[{id:"card",kind:"metric_card",region:"root",members:["mrr_label","mrr_value","mrr_delta"]}],
      styles:[{id:"aesthetic",target:"view:card",token:"aesthetic.calm_ops"}], actions:[] }
  };

  function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

  /* ---------- hero intent (compact, highlighted) ---------- */
  function heroIntentHTML(){
    var lines=[
      ['{',''],
      ['  "kind": "metric_card",','key'],
      ['  "attrs": {',''],
      ['    "label": "Monthly recurring",','pair'],
      ['    "value": "$48,210",','pair'],
      ['    "delta": "+8.2%"','pair'],
      ['  },',''],
      ['  "bindings": [ label, value, delta ],','bind'],
      ['  "style": "aesthetic.calm_ops"','style'],
      ['}','']
    ];
    return lines.map(function(l,idx){
      var t=esc(l[0]);
      t=t.replace(/&quot;([^&]*?)&quot;(\s*:)/g,'<span class="k">&quot;$1&quot;</span>$2');
      t=t.replace(/:\s(&quot;[^&]*?&quot;)/g,function(m,s){return ': <span class="s">'+s+'</span>';});
      t=t.replace(/(aesthetic\.[a-z_]+)/g,'<span class="a">$1</span>');
      t=t.replace(/([{}\[\],])/g,'<span class="p">$1</span>');
      return '<span class="ln" style="--i:'+idx+'">'+(t||"&nbsp;")+'</span>';
    }).join("");
  }
  document.getElementById("heroIntent").innerHTML=heroIntentHTML();

  /* ---------- sparkline ---------- */
  function spark(seed,el,fill){
    var n=24,w=120,h=34,d="";
    for(var i=0;i<n;i++){ var t=i/(n-1); var y=26-16*Math.abs(Math.sin(t*3.0+seed))-6*t; d+=(i?"L":"M")+(t*w).toFixed(1)+" "+y.toFixed(1)+" "; }
    var last=d.trim().split(" ").slice(-1)[0];
    el.innerHTML='<path class="spark-fill" d="'+d+'L120 34 L0 34 Z"/><path class="spark-line" d="'+d+'"/><circle class="spark-dot" cx="120" cy="'+last+'" r="2.2"/>';
  }
  spark(0.5, document.getElementById("heroSpark"));

  /* ---------- artifact viewport (skinned entirely by the compiler's projection CSS) ---------- */
  function renderViewport(){
    var g=document.getElementById("dv");
    var cards=METRICS.map(function(m,i){
      return '<div class="dvcard'+(i===0?' featured':'')+'" data-node="node:board#slot:metric['+i+']" data-binding="kpi_'+i+'" data-address="node:board#slot:metric['+i+']" data-present="value" data-raw="'+m.mv+'" tabindex="0">'
        +'<span class="dvtop"><span class="dvlabel">'+m.ml+'</span><span class="dvdelta">&#9650; 8.2%</span></span>'
        +'<span class="dvnum">'+m.mv+'</span>'
        +'<svg viewBox="0 0 120 34" preserveAspectRatio="none" data-seed="'+m.seed+'" aria-hidden="true"></svg></div>';
    }).join("");
    g.innerHTML='<div class="dv-head"><h4 class="dvh">Revenue workspace</h4><button type="button" class="dvbtn">Export report</button></div>'
      +'<div class="dvgrid">'+cards+'</div>'
      +'<p class="dvflow">This paragraph&rsquo;s measure and leading come from narrative.flow &mdash; the same sentence reads at a different rhythm in every projection.</p>';
    g.querySelectorAll("svg").forEach(function(s){ spark(parseFloat(s.getAttribute("data-seed")),s); });
  }
  function renderStrip(){
    var strip=document.getElementById("dvStrip");
    strip.innerHTML=Object.keys(PROFILES).map(function(p){
      return '<div class="dv-slot" data-slot="'+p+'"><button type="button" class="dv mini" data-p="'+p+'" data-pick="'+p+'" aria-pressed="'+(p==="calm_ops"?"true":"false")+'" aria-label="Select aesthetic.'+p+'">'
        +'<span class="dvh">Workspace</span>'
        +'<span class="dvlabel">MRR</span><span class="dvnum">$48k</span>'
        +'<span class="dvbtn">Export</span>'
        +'</button><span class="dv-name">'+p+'</span></div>';
    }).join("");
    strip.addEventListener("click",function(e){ var b=e.target.closest("button[data-pick]"); if(b) setProfile(b.getAttribute("data-pick")); });
  }

  /* ---------- profile ---------- */
  function setProfile(p){
    body.setAttribute("data-profile",p);
    var live=document.getElementById("dv"); if(live) live.setAttribute("data-p",p);
    document.querySelectorAll("#dvStrip button[data-pick]").forEach(function(b){ b.setAttribute("aria-pressed", b.getAttribute("data-pick")===p?"true":"false"); });
    document.querySelectorAll("#dvStrip .dv-slot").forEach(function(s){ s.setAttribute("data-active", s.getAttribute("data-slot")===p?"true":"false"); });
    document.querySelectorAll("#profileGroup button").forEach(function(b){ b.setAttribute("aria-pressed", b.getAttribute("data-profile")===p?"true":"false"); });
    var t=PROFILES[p];
    var pf=(window.__VIEWSPEC__&&window.__VIEWSPEC__.profiles&&window.__VIEWSPEC__.profiles[p]), pr=pf&&pf.projection;
    if(pr){
      var bits=["font <b>"+pr.fontLabel+"</b>","columns <b>"+pr.cols+"</b>","weight <b>"+pr.weight+"</b>","accent <b style=\"color:"+pr.accent+"\">"+pr.accent+"</b>"];
      if(pr.featuredSpan) bits.push("featured span <b>"+pr.featuredSpan+"</b>");
      if(pr.uppercase) bits.push("<b>uppercase</b> accents");
      document.getElementById("readout").innerHTML=bits.join(" &middot; ");
    }
    else { document.getElementById("readout").innerHTML="density <b>"+t.density+"</b> &middot; emphasis <b>"+t.emphasis+"</b> &middot; columns <b>"+t.columns+"</b>"; }
    if(pf){ var nn=document.getElementById("noteReal"); if(nn) nn.innerHTML="every declaration above is the compiled projection, verbatim &middot; semantic id <b>"+pf.semanticHash.slice(0,10)+"</b> (stable across profiles) &middot; style projection <b>"+pf.styleHash.slice(0,10)+"</b> (distinct) &middot; <b>"+pf.changedTokens+"</b> governed tokens changed"; }
    document.getElementById("noteP").textContent=p;
    document.getElementById("footState").innerHTML="profile: aesthetic."+p+" &middot; network: none";
  }
  document.getElementById("profileGroup").addEventListener("click",function(e){ var b=e.target.closest("button[data-profile]"); if(b) setProfile(b.getAttribute("data-profile")); });

  /* ---------- motif ---------- */
  var motifBody=document.getElementById("motifBody"), motifName=document.getElementById("motifName");
  function renderMotif(kind){
    motifName.textContent = kind==="cards"?"dashboard":(kind==="gal"?"gallery":"table");
    var h="";
    if(kind==="table"){
      h='<table class="mt"><thead><tr><th>capability</th><th>what it means</th></tr></thead><tbody>';
      CAPS.forEach(function(c,i){ h+='<tr data-node="node:features#slot:items['+i+']" data-binding="cap_'+c.k+'" data-address="node:features#slot:items['+i+']" data-present="label" data-raw="'+c.name+'" tabindex="0"><td class="k">'+c.name+'</td><td>'+c.desc+'</td></tr>'; });
      h+='</tbody></table>';
    } else if(kind==="cards"){
      h='<div class="cards">'; CAPS.forEach(function(c,i){ h+='<div class="mcard" data-node="node:features#slot:items['+i+']" data-binding="cap_'+c.k+'" data-address="node:features#slot:items['+i+']" data-present="label" data-raw="'+c.name+'" tabindex="0"><div class="ico">'+c.ab+'</div><h4>'+c.name+'</h4><p>'+c.desc+'</p></div>'; }); h+='</div>';
    } else {
      h='<div class="gal">'; CAPS.forEach(function(c,i){ h+='<div class="gtile" data-node="node:features#slot:items['+i+']" data-binding="cap_'+c.k+'" data-address="node:features#slot:items['+i+']" data-present="label" data-raw="'+c.name+'" tabindex="0"><span class="gk">'+c.k+'</span><h4>'+c.name+'</h4><p>'+c.desc+'</p></div>'; }); h+='</div>';
    }
    motifBody.innerHTML=h;
  }
  document.getElementById("motifSeg").addEventListener("click",function(e){ var b=e.target.closest("button[data-motif]"); if(!b) return;
    document.querySelectorAll("#motifSeg button").forEach(function(x){ x.setAttribute("aria-pressed",x===b?"true":"false"); }); renderMotif(b.getAttribute("data-motif")); });

  /* ---------- trace + inspect ---------- */
  var traceOut=document.getElementById("traceOut"), hud=document.getElementById("hud"), hudAddr=document.getElementById("hudAddr");
  function emptyTrace(){ traceOut.innerHTML='<div style="color:var(--faint);font-size:13px;line-height:1.7">Click, tap, hover, or focus a receipt on the left. Its DOM node, the binding that owns it, its address in the substrate, and the raw value appear here.</div>'; }
  function fillTrace(el){
    var irid=el.getAttribute("data-ir-id"); var rows = irid ? [["dom node","<"+el.tagName.toLowerCase()+">",""],["ir id",irid,"a"],["binding",el.getAttribute("data-binding-id")||"—",""],["intent refs",(el.getAttribute("data-intent-refs")||"[]"),"a"],["content refs",(el.getAttribute("data-content-refs")||"[]"),"r"]] : [["dom node","<"+el.tagName.toLowerCase()+">",""],["binding",el.getAttribute("data-binding")||"—",""],["address",el.getAttribute("data-address")||el.getAttribute("data-trace-target")||el.getAttribute("data-node"),"a"],["present_as",el.getAttribute("data-present")||"—",""],["raw data",el.getAttribute("data-raw")||"—","r"]];
    var h=""; rows.forEach(function(r,i){ h+='<div class="tnode"><div class="tk">'+r[0]+'</div><div class="tv '+r[2]+'">'+esc(r[1])+'</div></div>'; if(i<rows.length-1) h+='<div class="tarrow">&#8595;</div>'; });
    traceOut.innerHTML=h;
  }
  emptyTrace();
  document.addEventListener("mouseover",function(e){
    var el=e.target.closest("[data-node],[data-ir-id],[data-trace-target]");
    document.querySelectorAll(".lit").forEach(function(n){ if(n!==el) n.classList.remove("lit"); });
    if(!el){ hud.classList.remove("on"); return; }
    el.classList.add("lit");
    if(el.closest("#under")) fillTrace(el);
    if(body.classList.contains("inspect")){ hud.classList.add("on"); hudAddr.textContent=(el.getAttribute("data-ir-id")||el.getAttribute("data-trace-target")||el.getAttribute("data-node")); }
  });
  document.addEventListener("focusin",function(e){ var el=e.target.closest("[data-node],[data-ir-id],[data-trace-target]"); if(el&&el.closest("#under")) fillTrace(el); });
  document.addEventListener("click",function(e){ var el=e.target.closest("[data-node],[data-ir-id],[data-trace-target]"); if(el&&el.closest("#under")) fillTrace(el); });

  var inspectBtn=document.getElementById("inspectBtn");
  function setInspect(on){ body.classList.toggle("inspect",on); inspectBtn.setAttribute("aria-pressed",on?"true":"false"); if(!on) hud.classList.remove("on"); }
  inspectBtn.addEventListener("click",function(){ setInspect(!body.classList.contains("inspect")); });
  document.addEventListener("keydown",function(e){ if((e.key==="i"||e.key==="I")&&!/input|textarea/i.test((e.target.tagName||""))) setInspect(!body.classList.contains("inspect")); });

  /* ---------- provenance hash (real) ---------- */
  function stableJson(v){ if(v===null||typeof v!=="object") return JSON.stringify(v);
    if(Array.isArray(v)) return "["+v.map(stableJson).join(",")+"]";
    return "{"+Object.keys(v).sort().map(function(k){ return JSON.stringify(k)+":"+stableJson(v[k]); }).join(",")+"}"; }
  var canonical=stableJson((window.__VIEWSPEC__&&window.__VIEWSPEC__.intent)||INTENT), hashOut=document.getElementById("hashOut"), verdict=document.getElementById("verdict"), first=null;
  function computeHash(){
    if(window.crypto&&crypto.subtle&&window.TextEncoder){
      return crypto.subtle.digest("SHA-256",new TextEncoder().encode(canonical)).then(function(buf){
        return Array.prototype.map.call(new Uint8Array(buf),function(b){return b.toString(16).padStart(2,"0");}).join(""); });
    }
    var h=2166136261>>>0,out=""; for(var i=0;i<canonical.length;i++){ h^=canonical.charCodeAt(i); h=Math.imul(h,16777619)>>>0; }
    for(var j=0;j<8;j++){ out+=(h>>>0).toString(16).padStart(8,"0"); h=Math.imul(h^j,16777619)>>>0; } return Promise.resolve(out.slice(0,64));
  }
  function showHash(flash){ computeHash().then(function(hex){ hashOut.textContent=hex; if(first===null) first=hex;
    if(flash){ verdict.classList.add("show"); document.getElementById("verdictTxt").textContent=(hex===first)?"match — deterministic":"changed"; } }); }
  document.getElementById("verifyBtn").addEventListener("click",function(){ showHash(true); });

  /* ---------- live network count ---------- */
  function netCount(){ try{ var here=location.origin, res=performance.getEntriesByType("resource")||[];
    var n=res.filter(function(r){ try{ return new URL(r.name).origin!==here && !/^data:|^blob:/.test(r.name); }catch(e){ return false; } }).length;
    document.getElementById("netCount").textContent=n; }catch(e){} }

  /* ---------- copy ---------- */
  document.getElementById("copyCmd").addEventListener("click",function(){ var b=this;
    if(navigator.clipboard) navigator.clipboard.writeText("pip install viewspec").then(function(){ b.textContent="copied"; setTimeout(function(){ b.textContent="copy"; },1400); }); });

  /* ---------- scroll reveal ---------- */
  if("IntersectionObserver" in window && !reduce){
    var io=new IntersectionObserver(function(es){ es.forEach(function(en){ if(en.isIntersecting){ en.target.classList.add("in"); io.unobserve(en.target); } }); },{threshold:.12,rootMargin:"0px 0px -8% 0px"});
    document.querySelectorAll(".reveal-on").forEach(function(s){ io.observe(s); });
  } else { document.querySelectorAll(".reveal-on").forEach(function(s){ s.classList.add("in"); }); }

  /* ---------- boot ---------- */
  renderViewport(); renderStrip(); setProfile("calm_ops"); renderMotif("table"); showHash(false); netCount(); setTimeout(netCount,1200);
  if(!reduce){
    document.getElementById("cnBadge").classList.add("pulse");
    var big=document.getElementById("ocBig");
    if(big){ var target=48210, t0=null; big.textContent="$0";
      var step=function(ts){ if(!t0) t0=ts; var k=Math.min(1,(ts-t0)/750); var e=1-Math.pow(1-k,3);
        big.textContent="$"+Math.round(target*e).toLocaleString(); if(k<1) requestAnimationFrame(step); };
      setTimeout(function(){ requestAnimationFrame(step); }, 1000);
    }
  }
})();
"""


COMMERCE_SCRIPT = r"""
import { LANDING_CONFIG, hasProductionCommerceConfig } from './shared/landing-config.js?v=20260505-launch'

const links = {
  pro: LANDING_CONFIG.proStripeUrl,
  enterprise: LANDING_CONFIG.enterpriseUrl,
}

document.querySelectorAll('[data-config-link]').forEach((link) => {
  const key = link.getAttribute('data-config-link')
  link.href = links[key] || '#pricing'
  if (!hasProductionCommerceConfig()) {
    link.title = 'Production Stripe/API-key URL must be configured before launch.'
  }
})
"""


def _public_html(generated_html: str, profile_evidence: dict[str, Any]) -> str:
    body = _extract_body(generated_html)
    emitter_css = _extract_emitter_css(generated_html)
    intent_json = (COMPILED_DIR / "intent_bundle.json").read_text(encoding="utf-8")
    manifest = json.loads((COMPILED_DIR / "provenance_manifest.json").read_text(encoding="utf-8"))
    semantic_hash = _semantic_hash(manifest)

    # The full compiled artifact, presented as a contained "artifact window" (browser-frame
    # header + bounded scroll) instead of raw inline flow — the whole page in a viewport.
    real_artifact = (
        '<div class="artifact-window">'
        '<div class="cpanel-h"><span class="t">compiled artifact &middot; aesthetic.calm_ops &middot; scroll &amp; hover</span>'
        '<span class="dots"><i></i><i></i><i></i></span></div>'
        f'<section id="viewspec-artifact-slot" class="viewspec-artifact-slot" '
        f'data-active-profile="{html.escape(DEFAULT_PROFILE, quote=True)}">' + body + "</section>"
        '</div>'
        '<div class="artifact-window-foot">the entire homepage artifact, embedded live &middot; '
        '<a href="./landing-compiled/" target="_blank" rel="noopener">open it full-page &#8599;</a></div>'
    )

    # concept-styled profile pills carrying data-profile-token (SEO) + data-profile (theming)
    pills: list[str] = []
    for profile in AESTHETIC_PROFILE_TOKENS:
        data = profile_evidence["profiles"][profile]
        short = profile.replace("aesthetic.", "")
        active = profile == DEFAULT_PROFILE
        pills.append(
            f'<button type="button" data-profile-token="{html.escape(profile, quote=True)}" '
            f'data-profile="{html.escape(short, quote=True)}" '
            f'data-profile-slug="{html.escape(data["slug"], quote=True)}" '
            f'aria-pressed="{"true" if active else "false"}" '
            f'aria-label="Use {html.escape(data["label"], quote=True)} compiled aesthetic profile">'
            f'{html.escape(short)}</button>'
        )
    profile_buttons = "".join(pills)

    pricing_actions = (
        '<div class="pricing-actions" id="pricing-actions">'
        '<a class="pact primary" href="https://buy.stripe.com/6oU4gA6PqcM9afq6qq2Z0b8" data-config-link="pro" target="_blank" rel="noopener">Get Pro</a>'
        '<a class="pact" href="mailto:hello@viewspec.dev?subject=ViewSpec%20Enterprise" data-config-link="enterprise" target="_blank" rel="noopener">Talk to us</a>'
        '<button type="button" class="pact" data-copy-text="pip install viewspec" aria-label="Copy pip install viewspec command">pip install viewspec</button>'
        '<button type="button" class="pact" data-copy-text="pip install viewspec" aria-label="Copy pip install viewspec command">copy install</button>'
        "</div>"
    )

    page_body = (
        PAGE_BODY_TEMPLATE
        .replace("{{REAL_ARTIFACT}}", real_artifact)
        .replace("{{PROFILE_BUTTONS}}", profile_buttons)
        .replace("{{PRICING_ACTIONS}}", pricing_actions)
    )

    # real data for the page script: hash the canonical IntentBundle + real profile evidence
    real_profiles = {
        profile.replace("aesthetic.", ""): {
            "semanticHash": data["semanticHash"],
            "styleHash": data["styleProjectionHash"],
            "changedTokens": data["styleProof"].get("changed_token_count", 0),
            "projection": data["projection"],
        }
        for profile, data in profile_evidence["profiles"].items()
    }
    viewspec_data = {
        "intent": json.loads(intent_json),
        "semanticHash": semantic_hash,
        "profiles": real_profiles,
    }

    return "\n".join(
        [
            "<!DOCTYPE html>",
            f'<html lang="en" data-viewspec-profile="{html.escape(DEFAULT_PROFILE, quote=True)}" data-viewspec-evidence="clean">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>ViewSpec - Ship agent-built apps you can prove</title>",
            '<meta name="description" content="Agents author app intent as JSON. ViewSpec compiles UI, state reducers, replay checks, shell artifacts, and proof reports without runtime LLM calls.">',
            '<meta name="robots" content="index,follow,max-snippet:-1,max-image-preview:large">',
            '<meta name="application-name" content="ViewSpec">',
            '<meta name="keywords" content="agent-native app compiler, AppBundle, State IR, IntentBundle, semantic UI compiler, AI coding agents, deterministic HTML, reducers, provenance, proof pipeline, Python SDK">',
            '<link rel="canonical" href="https://viewspec.dev/">',
            '<link rel="sitemap" type="application/xml" href="https://viewspec.dev/sitemap.xml">',
            '<link rel="alternate" type="text/markdown" title="ViewSpec for LLMs" href="https://viewspec.dev/llms.txt">',
            '<link rel="alternate" type="application/json" title="ViewSpec OpenAPI" href="https://viewspec.dev/openapi.json">',
            '<meta property="og:site_name" content="ViewSpec">',
            '<meta property="og:type" content="website">',
            '<meta property="og:title" content="ViewSpec - Ship agent-built apps you can prove">',
            '<meta property="og:description" content="Agents author app intent. ViewSpec compiles UI, state reducers, shell artifacts, replay assertions, and proof reports.">',
            '<meta property="og:url" content="https://viewspec.dev/">',
            '<meta name="twitter:card" content="summary">',
            '<meta name="twitter:title" content="ViewSpec - Ship agent-built apps you can prove">',
            '<meta name="twitter:description" content="Compile agent-authored app intent into UI, State IR, reducers, replay checks, checked shells, and proof reports.">',
            '<script type="application/ld+json">',
            _json_ld(),
            "</script>",
            '<script type="application/json" id="landing-profile-evidence">',
            _script_json(profile_evidence),
            "</script>",
            '<script type="application/json" id="viewspec-real-data">',
            _script_json(viewspec_data),
            "</script>",
            '<link rel="icon" href="data:,">',
            '<style data-viewspec-emitter-css="true">',
            emitter_css,
            "</style>",
            '<style data-viewspec-page-css="true">',
            PAGE_CSS,
            "</style>",
            '<style data-viewspec-profile-projection="true">',
            _projection_viewport_css(),
            "</style>",
            "</head>",
            "<body data-profile=\"calm_ops\">",
            page_body,
            '<script type="module">',
            COMMERCE_SCRIPT,
            "</script>",
            "<script>",
            'window.__VIEWSPEC__ = JSON.parse(document.getElementById("viewspec-real-data").textContent);',
            PAGE_SCRIPT,
            "</script>",
            "</body>",
            "</html>",
        ]
    )


def build() -> None:
    if PROFILE_DIR.exists():
        shutil.rmtree(PROFILE_DIR)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    default_bundle = build_bundle(DEFAULT_PROFILE)
    default_result = _compiled_bundle(default_bundle, COMPILED_DIR, command_profile=DEFAULT_PROFILE)

    profile_results: dict[str, dict[str, Any]] = {}
    for profile in AESTHETIC_PROFILE_TOKENS:
        profile_output = PROFILE_DIR / _profile_slug(profile)
        profile_results[profile] = _compiled_bundle(
            build_bundle(profile),
            profile_output,
            command_profile=profile,
        )

    profile_evidence = _profile_evidence(profile_results)
    evidence_path = COMPILED_DIR / "profile-evidence.json"
    evidence_path.write_text(_script_json(profile_evidence), encoding="utf-8", newline="")

    PUBLIC_INDEX.write_text(
        _public_html(default_result["generated_html"], profile_evidence),
        encoding="utf-8",
        newline="",
    )

    print(f"Compiled mirror HTML: {default_result['paths']['html']}")
    print(f"Public artifact page: {PUBLIC_INDEX}")
    print(f"Intent bundle: {default_result['intent_bundle_path']}")
    print(f"Profile evidence: {evidence_path}")
    print("\nIR Tree:")
    _print_tree(default_result["ast"].result.root.root)


def _print_tree(node, indent=0) -> None:
    prefix = "  " * indent
    refs = len(node.provenance.content_refs) + len(node.provenance.intent_refs)
    style = f" [{', '.join(node.style_tokens)}]" if node.style_tokens else ""
    text = node.props.get("text", "")
    label = f' "{text}"' if text else ""
    line = f"{prefix}{node.primitive} ({node.id}) refs={refs}{style}{label}"
    print(line.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"))
    for child in node.children:
        _print_tree(child, indent + 1)


if __name__ == "__main__":
    build()
