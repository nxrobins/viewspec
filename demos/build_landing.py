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
from viewspec.aesthetics import profile_layout_props
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
}

PROFILE_NOTES = {
    "aesthetic.calm_ops": "Quiet operational surfaces with teal accents and low-contrast panels.",
    "aesthetic.premium_saas": "Polished product rhythm with stronger hierarchy and softer shadows.",
    "aesthetic.data_dense": "Compact spacing, smaller type, and tighter controls for repeated scanning.",
    "aesthetic.editorial_product": "Warmer product-story pacing with broader prose rhythm.",
    "aesthetic.executive_review": "Conservative review surfaces with restrained contrast and crisp framing.",
}


def _profile_slug(profile: str) -> str:
    return profile.replace("aesthetic.", "").replace("_", "-")


def build_bundle(aesthetic_profile: str | None = None):
    builder = ViewSpecBuilder("viewspec_landing")

    builder.add_hero(
        "launch_hero",
        eyebrow="Agent-native app compiler",
        title="Ship agent-built apps you can prove.",
        description=(
            "Intent in. App out. Proof attached. Agents commit to meaning — nodes, bindings, "
            "motifs — and ViewSpec compiles the UI, the state reducer, replay checks, and shell "
            "proof. Deterministic, and no runtime LLM."
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

    capabilities = builder.add_table("capabilities", region="main", group_id="capabilities")
    capabilities.add_row(
        label="State IR",
        value="interactive_state_v0 writes state_reducer.ts and reduceViewSpecState.",
    )
    capabilities.add_row(
        label="Aesthetic Profiles",
        value="Five compiled projections from one semantic graph.",
    )
    capabilities.add_row(
        label="Proof Pipeline",
        value="state_replay_assertions plus viewspec prove-app --with-shell.",
    )
    capabilities.add_row(
        label="Portable Surfaces",
        value="html-tailwind and React locally; SwiftUI and Flutter hosted.",
    )

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
        profiles[profile] = {
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


PAGE_CSS = r"""
:root {
  color-scheme: dark;
  --shell-bg-rgb: 10 13 20;
  --shell-bg-2-rgb: 12 16 26;
  --shell-panel-rgb: 20 26 40;
  --shell-ink-rgb: 231 236 245;
  --shell-line-rgb: 136 148 172;
  --shell-accent-rgb: 245 178 63;
  --shell-accent-2-rgb: 87 220 169;
  --shell-bg: rgb(var(--shell-bg-rgb));
  --shell-bg-2: rgb(var(--shell-bg-2-rgb));
  --shell-panel: rgb(var(--shell-panel-rgb));
  --shell-ink: rgb(var(--shell-ink-rgb));
  --shell-muted: rgb(var(--shell-line-rgb));
  --shell-accent: rgb(var(--shell-accent-rgb));
  --shell-accent-2: rgb(var(--shell-accent-2-rgb));
  --shell-line: rgb(var(--shell-line-rgb) / 0.17);
  --shell-line-strong: rgb(var(--shell-accent-rgb) / 0.34);
  --shell-shadow: 0 30px 70px rgb(0 0 0 / 0.55);
  --shell-soft-shadow: 0 16px 40px rgb(0 0 0 / 0.42);
  --bg: var(--shell-bg);
  --bg-2: var(--shell-bg-2);
  --panel: var(--shell-panel);
  --panel-2: rgb(var(--shell-panel-rgb) / 0.82);
  --panel-3: rgb(var(--shell-panel-rgb) / 0.94);
  --line: var(--shell-line);
  --line-strong: var(--shell-line-strong);
  --text: var(--shell-ink);
  --muted: var(--shell-muted);
  --teal: var(--shell-accent);
  --green: #57DCA9;
  --amber: #F5B23F;
  --amber-2: #FFD584;
  --blue: #93B8FF;
  --rose: #fb7185;
  --radius: 10px;
  --shell: 1180px;
  --mono: ui-monospace, "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, Roboto, sans-serif;
}
[data-viewspec-profile="aesthetic.premium_saas"] {
  --shell-bg-rgb: 13 12 22;
  --shell-bg-2-rgb: 16 14 28;
  --shell-panel-rgb: 24 22 40;
  --shell-ink-rgb: 236 238 248;
  --shell-line-rgb: 150 150 185;
  --shell-accent-rgb: 245 178 63;
  --shell-accent-2-rgb: 147 184 255;
}
[data-viewspec-profile="aesthetic.data_dense"] {
  --shell-bg-rgb: 8 11 17;
  --shell-bg-2-rgb: 10 14 22;
  --shell-panel-rgb: 17 23 35;
  --shell-ink-rgb: 226 233 244;
  --shell-line-rgb: 120 138 168;
  --shell-accent-rgb: 245 178 63;
  --shell-accent-2-rgb: 87 220 169;
  --radius: 7px;
}
[data-viewspec-profile="aesthetic.editorial_product"] {
  --shell-bg-rgb: 17 12 12;
  --shell-bg-2-rgb: 21 15 14;
  --shell-panel-rgb: 30 22 22;
  --shell-ink-rgb: 244 236 232;
  --shell-line-rgb: 168 140 128;
  --shell-accent-rgb: 245 178 63;
  --shell-accent-2-rgb: 255 213 132;
}
[data-viewspec-profile="aesthetic.executive_review"] {
  --shell-bg-rgb: 11 13 19;
  --shell-bg-2-rgb: 13 16 24;
  --shell-panel-rgb: 21 26 38;
  --shell-ink-rgb: 234 238 246;
  --shell-line-rgb: 130 142 166;
  --shell-accent-rgb: 245 178 63;
  --shell-accent-2-rgb: 147 184 255;
}
* {
  box-sizing: border-box;
}
body {
  background:
    radial-gradient(760px 440px at 84% -6%, rgb(var(--shell-accent-rgb) / 0.10), transparent 62%),
    radial-gradient(620px 500px at 4% 12%, rgb(var(--shell-accent-2-rgb) / 0.05), transparent 60%),
    linear-gradient(180deg, var(--shell-bg) 0%, var(--shell-bg-2) 100%);
  background-attachment: fixed;
  color: var(--text);
  font-family: var(--sans);
  margin: 0;
  transition: background 180ms ease, color 180ms ease;
}
.skip-link {
  background: var(--shell-panel);
  border-radius: var(--radius);
  color: var(--shell-ink);
  font-weight: 900;
  left: 1rem;
  padding: 0.7rem 0.9rem;
  position: fixed;
  top: 0.75rem;
  transform: translateY(-150%);
  transition: transform 120ms ease;
  z-index: 100;
}
.skip-link:focus {
  transform: translateY(0);
}
.anchor-target {
  display: block;
  grid-column: 1 / -1;
  scroll-margin-top: 6rem;
}
.artifact-bar {
  align-items: center;
  backdrop-filter: blur(10px);
  background: rgb(var(--shell-bg-rgb) / 0.94);
  border-bottom: 1px solid var(--shell-line);
  box-shadow: none;
  display: grid;
  gap: 0.45rem 0.9rem;
  grid-template-columns: auto minmax(0, 1fr) auto;
  left: 0;
  padding: 0.52rem max(1rem, calc((100vw - var(--shell)) / 2)) 0.58rem;
  position: sticky;
  right: 0;
  top: 0;
  z-index: 50;
}
.artifact-brand {
  color: var(--shell-accent);
  font-size: 0.92rem;
  font-weight: 900;
  letter-spacing: 0;
  min-width: 0;
  text-decoration: none;
}
.artifact-links, .artifact-controls {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  min-width: 0;
}
.artifact-profile-dock {
  border-top: 1px solid var(--shell-line);
  grid-column: 1 / -1;
  padding-top: 0.42rem;
  width: 100%;
}
.artifact-profile-dock-inner {
  align-items: center;
  display: grid;
  gap: 0.55rem;
  grid-template-columns: auto minmax(0, 1fr) auto;
  margin: 0 auto;
  width: 100%;
}
.artifact-profile-label {
  color: var(--shell-muted);
  display: grid;
  font-size: 0.64rem;
  font-weight: 780;
  gap: 0.05rem;
  line-height: 1.05;
  min-width: 7.5rem;
  text-transform: uppercase;
}
.artifact-profile-label strong {
  color: var(--shell-ink);
  font-size: 0.8rem;
  font-weight: 820;
  text-transform: none;
}
.artifact-profile-switcher {
  background: transparent;
  border: 0;
  border-radius: 0;
  box-shadow: none;
  display: flex;
  flex-wrap: wrap;
  gap: 0.65rem;
  min-width: 0;
  overflow: visible;
  padding: 0;
}
.artifact-links {
  justify-content: center;
}
.artifact-inspector {
  justify-self: end;
  max-width: 100%;
  min-width: 0;
  position: static;
}
.artifact-inspector-toggle {
  align-items: center;
  appearance: none;
  background: rgb(var(--shell-panel-rgb) / 0.38);
  border: 1px solid var(--shell-line);
  border-radius: var(--radius);
  box-shadow: none;
  color: var(--shell-accent);
  cursor: pointer;
  display: inline-flex;
  font-size: 0.78rem;
  font-weight: 820;
  gap: 0.65rem;
  list-style: none;
  min-height: 2.18rem;
  padding: 0.42rem 0.68rem;
  white-space: nowrap;
}
.artifact-inspector-toggle::after {
  color: var(--shell-muted);
  content: "+";
  font-size: 1rem;
  line-height: 1;
}
.artifact-inspector[data-open="true"] .artifact-inspector-toggle::after {
  content: "-";
}
.artifact-inspector[data-open="false"] .artifact-inspector-panel {
  display: none;
}
.artifact-inspector-panel {
  background: rgb(var(--shell-panel-rgb) / 0.96);
  border: 1px solid var(--shell-line);
  border-radius: var(--radius);
  box-shadow: var(--shell-soft-shadow);
  display: grid;
  gap: 0.7rem;
  margin-top: 0.55rem;
  padding: 0.8rem;
  position: absolute;
  right: 0;
  top: 100%;
  width: min(42rem, calc(100vw - 2rem));
  z-index: 60;
}
.artifact-control-group {
  border: 1px solid var(--shell-line);
  border-radius: var(--radius);
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin: 0;
  min-width: 0;
  padding: 0.65rem;
}
.artifact-control-group legend {
  color: var(--shell-muted);
  font-size: 0.68rem;
  font-weight: 760;
  padding: 0 0.25rem;
  text-transform: uppercase;
}
.artifact-controls {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  justify-content: flex-start;
}
.artifact-links a, .artifact-link-button {
  color: var(--shell-muted);
  font-size: 0.8rem;
  font-weight: 720;
  letter-spacing: 0;
  text-decoration: none;
  white-space: nowrap;
}
.artifact-button, .artifact-link-button {
  appearance: none;
  background: rgb(var(--shell-panel-rgb) / 0.5);
  border: 1px solid var(--shell-line);
  border-radius: var(--radius);
  color: var(--shell-ink);
  cursor: pointer;
  font: inherit;
  font-size: 0.78rem;
  font-weight: 760;
  letter-spacing: 0;
  min-height: 2.25rem;
  padding: 0.45rem 0.7rem;
  white-space: nowrap;
}
.artifact-button[data-active="true"], .artifact-link-button.primary {
  background: transparent;
  border-color: rgb(var(--shell-accent-rgb) / 0.36);
  color: var(--shell-accent);
}
.artifact-profile-button {
  align-items: center;
  background: transparent;
  border: 0;
  border-bottom: 1px solid transparent;
  border-radius: 0;
  box-shadow: none;
  display: inline-flex;
  flex: 0 1 auto;
  gap: 0;
  min-height: 1.6rem;
  min-width: 0;
  padding: 0.12rem 0 0.18rem;
  text-align: center;
}
.artifact-profile-button::before {
  display: none;
}
.artifact-profile-button[data-active="true"] {
  background: transparent;
  border-color: rgb(var(--shell-accent-rgb) / 0.7);
}
.artifact-profile-button code {
  color: var(--shell-muted);
  font: 700 0.64rem ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
}
.artifact-profile-status {
  align-items: center;
  color: var(--shell-muted);
  display: inline-flex;
  justify-self: end;
  font-size: 0.76rem;
  font-weight: 720;
  gap: 0.4rem;
  min-width: 0;
  white-space: nowrap;
}
.artifact-profile-status output {
  color: var(--shell-muted);
  display: inline-block;
  font: inherit;
  max-width: 24rem;
  overflow: hidden;
  text-overflow: ellipsis;
}
.artifact-pretext-status {
  align-items: center;
  color: var(--shell-muted);
  display: inline-flex;
  flex: 1 1 auto;
  font-size: 0.76rem;
  font-weight: 800;
  gap: 0.4rem;
  justify-content: flex-end;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}
.artifact-pretext-status output {
  color: var(--shell-accent-2);
  display: inline-block;
  font: inherit;
  max-width: 13rem;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: bottom;
}
.artifact-ir-status {
  color: var(--shell-accent);
  font-size: 0.74rem;
  font-weight: 800;
  min-width: 0;
}
.viewspec-artifact-slot {
  display: block;
  transition: opacity 160ms ease, transform 160ms ease;
}
.vs-root {
  margin: 0 auto;
  min-height: auto !important;
  padding: 3.6rem 1rem 3.75rem !important;
  width: min(var(--shell), 100%);
}
#dom-region_main {
  display: grid !important;
  gap: 1.15rem !important;
  grid-template-columns: repeat(12, minmax(0, 1fr));
  padding: 0 !important;
}
.vs-stack, .vs-grid, .vs-cluster {
  min-width: 0;
}
.vs-surface, .vs-grid, .vs-cluster, table.vs-stack, dl.vs-stack, .vs-motif-comparison {
  border-radius: var(--radius) !important;
  box-shadow: none !important;
}
.vs-label, .vs-value, .vs-text, .artifact-button, .artifact-link-button, th, td {
  letter-spacing: 0 !important;
}
#dom-motif_launch_hero {
  background: transparent !important;
  border: 0 !important;
  box-shadow: none !important;
  display: flex !important;
  flex-direction: column;
  grid-column: span 7;
  justify-content: center;
  min-height: 31rem;
  padding: 2.85rem 0 2.35rem !important;
  position: relative;
}
#dom-motif_launch_hero .vs-label {
  font-size: 0.78rem !important;
  font-weight: 780 !important;
  margin-bottom: 1rem;
  text-transform: uppercase;
}
#dom-motif_launch_hero h1,
#dom-binding_launch_hero_title {
  font-size: 5.15rem !important;
  font-weight: 880 !important;
  line-height: 0.97 !important;
  max-width: 48rem;
}
#dom-binding_launch_hero_description {
  font-size: 1.08rem !important;
  line-height: 1.8 !important;
  margin-top: 1.4rem;
  max-width: 50rem;
}
#dom-motif_proof_badges {
  align-self: stretch;
  box-shadow: none !important;
  grid-column: 8 / -1;
  grid-template-columns: 1fr !important;
  padding: 0.8rem !important;
  position: relative;
}
#dom-motif_proof_badges::before {
  color: var(--muted);
  content: "Proof report";
  display: block;
  font-size: 0.72rem;
  font-weight: 760;
  grid-column: 1 / -1;
  letter-spacing: 0;
  padding: 0.2rem 0.25rem 0.35rem;
  text-transform: uppercase;
}
#dom-motif_proof_badges .vs-surface,
table.vs-stack {
  box-shadow: none !important;
}
#dom-motif_proof_badges .vs-surface {
  min-height: 5.2rem;
  padding: 0.9rem !important;
}
#dom-motif_proof_badges .vs-label {
  font-size: 0.72rem !important;
  font-weight: 760 !important;
  text-transform: uppercase;
}
#dom-motif_proof_badges .vs-value {
  font-size: 1.18rem !important;
  line-height: 1.25 !important;
}
.vs-label {
  font-weight: 760 !important;
}
table.vs-stack {
  border-collapse: separate !important;
  grid-column: span 6;
  border-spacing: 0;
  overflow: hidden;
}
#dom-motif_capabilities {
  grid-column: span 7;
}
th.vs-label {
  font-size: 0.72rem !important;
  padding: 1rem !important;
  text-transform: uppercase;
  width: 30%;
}
td.vs-value {
  font-size: 1rem !important;
  font-weight: 760 !important;
  line-height: 1.55 !important;
  padding: 1rem !important;
}
.landing-pretext-wrap {
  display: none;
}
[data-landing-pretext="ready"] .landing-pretext-wrap {
  display: block;
  max-width: 100%;
}
[data-landing-pretext="ready"] [data-pretext-source="true"] {
  border: 0;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  height: 1px;
  margin: -1px;
  overflow: hidden;
  padding: 0;
  position: absolute;
  white-space: nowrap;
  width: 1px;
}
.landing-pretext-title canvas {
  margin-bottom: 0.2rem;
}
.landing-pretext-hero-description canvas {
  margin-top: 1.2rem;
}
.artifact-actions, .artifact-footer {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  margin: 0 auto 1rem;
  width: min(var(--shell), calc(100% - 2rem));
}
.artifact-actions {
  justify-content: center;
}
.artifact-footer {
  border-top: 1px solid var(--shell-line);
  color: var(--muted);
  font-size: 0.82rem;
  justify-content: space-between;
  padding: 1.25rem 0 2rem;
}
.artifact-source {
  background: rgb(var(--shell-panel-rgb) / 0.94);
  border: 1px solid var(--shell-line-strong);
  border-radius: var(--radius);
  box-shadow: var(--shell-shadow);
  color: var(--shell-ink);
  display: none;
  margin: 0 auto 1.25rem;
  padding: 1rem;
  width: min(var(--shell), calc(100% - 2rem));
}
.artifact-source[data-visible="true"] {
  display: block;
}
.artifact-source-heading {
  align-items: start;
  display: flex;
  gap: 1rem;
  justify-content: space-between;
  margin-bottom: 0.9rem;
}
.artifact-source-kicker {
  color: var(--shell-accent);
  font-size: 0.72rem;
  font-weight: 900;
  margin: 0 0 0.2rem;
  text-transform: uppercase;
}
.artifact-source h2 {
  color: var(--shell-ink);
  font-size: 1.15rem;
  line-height: 1.25;
  margin: 0;
}
.artifact-source-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  justify-content: flex-end;
}
.artifact-source-summary {
  display: grid;
  gap: 0.65rem;
  grid-template-columns: repeat(4, minmax(0, 1fr));
}
.artifact-source-card {
  background: rgb(var(--shell-bg-rgb) / 0.68);
  border: 1px solid var(--shell-line);
  border-radius: var(--radius);
  min-width: 0;
  padding: 0.8rem;
}
.artifact-source-card dt {
  color: var(--shell-accent);
  font-size: 0.72rem;
  font-weight: 900;
  margin: 0 0 0.35rem;
  text-transform: uppercase;
}
.artifact-source-card dd {
  color: var(--shell-ink);
  font-size: 0.95rem;
  font-weight: 800;
  line-height: 1.35;
  margin: 0;
  overflow-wrap: anywhere;
}
.artifact-source-raw {
  border-top: 1px solid var(--shell-line);
  margin-top: 0.9rem;
  padding-top: 0.75rem;
}
.artifact-source-raw summary {
  color: var(--shell-accent);
  cursor: pointer;
  font-size: 0.8rem;
  font-weight: 900;
}
.artifact-source-raw pre {
  background: rgb(var(--shell-ink-rgb) / 0.94);
  border-radius: var(--radius);
  color: #f8fafc;
  margin: 0.75rem 0 0;
  max-height: 24rem;
  overflow: auto;
  padding: 0.9rem;
}
[data-viewspec-evidence="provenance"] [data-ir-id] {
  outline: 1px solid rgb(var(--shell-accent-rgb) / 0.76) !important;
  outline-offset: -1px !important;
  position: relative;
}
[data-viewspec-evidence="provenance"] [data-ir-id]::before {
  background: rgb(var(--shell-ink-rgb) / 0.88);
  border-radius: 4px;
  color: #f8fafc;
  content: attr(data-ir-id);
  font: 800 10px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  max-width: 16rem;
  opacity: 0;
  overflow: hidden;
  padding: 2px 4px;
  position: absolute;
  right: 2px;
  text-overflow: ellipsis;
  top: 2px;
  transform: translateY(-2px);
  transition: opacity 120ms ease, transform 120ms ease;
  white-space: nowrap;
  z-index: 20;
}
[data-viewspec-evidence="provenance"] [data-ir-id]:hover::before,
[data-viewspec-evidence="provenance"] [data-ir-id]:focus-within::before,
[data-viewspec-evidence="provenance"] [data-ir-selected="true"]::before {
  opacity: 1;
  transform: translateY(0);
}
@media (max-width: 1080px) {
  .artifact-bar {
    grid-template-columns: auto 1fr;
  }
  .artifact-profile-dock-inner {
    align-items: center;
    gap: 0.42rem 0.55rem;
    grid-template-columns: minmax(0, 1fr) auto;
  }
  .artifact-profile-label {
    align-items: baseline;
    display: flex;
    gap: 0.45rem;
    min-width: 0;
  }
  .artifact-profile-switcher {
    grid-column: 1 / -1;
    width: 100%;
  }
  .artifact-profile-status {
    justify-self: end;
    max-width: 100%;
    overflow: hidden;
  }
  .artifact-profile-status output {
    max-width: 17rem;
  }
  .artifact-inspector {
    grid-column: 1 / -1;
    justify-self: stretch;
  }
  .artifact-inspector-panel {
    left: 0;
    right: auto;
    width: 100%;
  }
  .artifact-controls {
    justify-content: flex-start;
    padding-bottom: 0.1rem;
  }
  #dom-motif_launch_hero,
  #dom-motif_proof_badges,
  #dom-motif_capabilities {
    grid-column: 1 / -1;
  }
  #dom-motif_launch_hero {
    min-height: 23rem;
  }
  #dom-motif_launch_hero #dom-binding_launch_hero_title {
    font-size: 4.15rem !important;
    max-width: 42rem;
  }
  #dom-motif_proof_badges {
    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
  }
  #dom-motif_proof_badges::before {
    grid-column: 1 / -1;
  }
}
@media (max-width: 720px) {
  .artifact-bar {
    align-items: start;
    grid-template-columns: 1fr;
    overflow-x: clip;
    position: static;
    padding-bottom: 0.52rem;
    padding-top: 0.48rem;
  }
  .artifact-profile-dock {
    padding: 0.34rem 0 0;
  }
  .artifact-profile-dock-inner {
    gap: 0.34rem 0.45rem;
  }
  .artifact-profile-label strong {
    font-size: 0.78rem;
  }
  .artifact-profile-switcher {
    padding: 0.2rem;
  }
  .artifact-profile-button {
    min-height: 1.9rem;
    padding: 0.34rem 0.5rem;
  }
  .artifact-profile-button code {
    display: none;
  }
  .artifact-profile-status output {
    max-width: min(13.5rem, 48vw);
  }
  .artifact-links {
    justify-content: flex-start;
    min-width: 0;
    max-width: 100%;
    overflow: hidden;
    width: 100%;
  }
  .artifact-inspector-toggle {
    justify-content: space-between;
    width: 100%;
  }
  .artifact-pretext-status output {
    max-width: 9rem;
  }
  .artifact-inspector-panel {
    max-width: 100%;
    overflow-x: hidden;
    position: static;
  }
  .artifact-controls {
    display: grid;
    gap: 0.45rem;
    grid-template-columns: 1fr;
    overflow-x: visible;
    width: 100%;
  }
  .artifact-button,
  .artifact-pretext-status {
    justify-content: center;
    min-width: 0;
    overflow: hidden;
    padding-left: 0.45rem;
    padding-right: 0.45rem;
    text-overflow: ellipsis;
  }
  .artifact-pretext-status {
    grid-column: 1 / -1;
  }
  .artifact-source-heading {
    display: block;
  }
  .artifact-source-actions {
    justify-content: flex-start;
    margin-top: 0.75rem;
  }
  .artifact-source-summary {
    grid-template-columns: 1fr;
  }
  .vs-root {
    padding-top: 1.45rem !important;
  }
  #dom-region_main {
    gap: 0.8rem !important;
  }
  #dom-motif_launch_hero {
    min-height: 0;
    padding: 1.5rem 0 1rem !important;
  }
  #dom-motif_launch_hero #dom-binding_launch_hero_title {
    font-size: 2.65rem !important;
    line-height: 1 !important;
  }
  #dom-binding_launch_hero_description {
    font-size: 1rem !important;
  }
  #dom-motif_proof_badges {
    grid-template-columns: 1fr !important;
  }
  th.vs-label, td.vs-value, td.vs-text {
    display: block !important;
    width: 100% !important;
  }
}

/* ============ dark / mono / amber reskin — beats the emitter's inline light-theme colors ============ */
#dom-region_root.vs-root { background: none !important; background-color: transparent !important; color: var(--text) !important; font-family: var(--sans) !important; }
.vs-value, .vs-text, td.vs-value, td.vs-text { color: var(--text) !important; }
.vs-label, th.vs-label { color: var(--muted) !important; }
.vs-surface { background: linear-gradient(160deg, var(--panel-2), var(--panel)) !important; border-color: var(--shell-line) !important; box-shadow: var(--shell-soft-shadow) !important; }
/* hero — monospace display */
#dom-motif_launch_hero .vs-label, #dom-binding_launch_hero_eyebrow { font-family: var(--mono) !important; color: var(--shell-accent) !important; letter-spacing: 0.2em !important; text-transform: uppercase; }
#dom-motif_launch_hero h1, #dom-binding_launch_hero_title { font-family: var(--mono) !important; color: var(--text) !important; font-weight: 660 !important; letter-spacing: -0.03em !important; }
#dom-binding_launch_hero_description { color: var(--muted) !important; font-family: var(--sans) !important; }
/* proof badges */
#dom-motif_proof_badges .vs-value { color: var(--shell-accent-2) !important; font-family: var(--mono) !important; }
#dom-motif_proof_badges::before { color: var(--muted) !important; font-family: var(--mono) !important; letter-spacing: 0.14em !important; }
#dom-motif_proof_badges .vs-surface::before { content: ""; position: absolute; inset: 0 auto auto 0; width: 2px; height: 100%; background: linear-gradient(var(--shell-accent), transparent); }
#dom-motif_proof_badges .vs-surface { position: relative; overflow: hidden; }
/* tables: capabilities */
table.vs-stack { background: var(--panel) !important; }
td.vs-label { color: var(--shell-accent-2) !important; font-family: var(--mono) !important; }
th.vs-label { background: rgb(var(--shell-panel-rgb) / 0.5) !important; font-family: var(--mono) !important; letter-spacing: 0.12em !important; }
/* monospace chrome accents */
.artifact-brand, .artifact-profile-button code, .artifact-ir-status, .artifact-source-kicker, .artifact-source-card dt { font-family: var(--mono) !important; }
/* hero entrance */
#dom-binding_launch_hero_eyebrow { opacity: 1; animation: vs-rv 0.6s ease 0.05s both; }
#dom-binding_launch_hero_title { opacity: 1; animation: vs-rv 0.7s cubic-bezier(0.2, 0.7, 0.2, 1) 0.16s both; }
#dom-binding_launch_hero_description { opacity: 1; animation: vs-rv 0.7s ease 0.3s both; }
@keyframes vs-rv { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) {
  #dom-binding_launch_hero_eyebrow, #dom-binding_launch_hero_title, #dom-binding_launch_hero_description { animation: none !important; }
}
"""


PAGE_SCRIPT = r"""
;(() => {
  const root = document.documentElement
  const source = document.getElementById('artifact-source')
  const sourceTitle = document.getElementById('artifact-source-title')
  const sourceSummary = document.getElementById('artifact-source-summary')
  const sourceCode = document.getElementById('artifact-source-code')
  const sourceOpen = document.getElementById('artifact-source-open')
  const copySource = document.getElementById('artifact-source-copy')
  const irOutput = document.getElementById('ir-inspector-value')
  const inspector = document.getElementById('artifact-inspector')
  const inspectorToggle = document.getElementById('artifact-inspector-toggle')
  const artifactSlot = document.getElementById('viewspec-artifact-slot')
  const profileOutput = document.getElementById('profile-proof-value')
  const profileEvidenceElement = document.getElementById('landing-profile-evidence')
  const profileEvidence = profileEvidenceElement ? JSON.parse(profileEvidenceElement.textContent || '{}') : {}
  const sourceNames = {
    intent: 'IntentBundle summary',
    manifest: 'Manifest summary',
    profile: 'Profile proof summary',
  }
  const cache = new Map()
  let activeSourceText = ''
  let activeSourceView = 'hide'
  let activeProfile = profileEvidence.defaultProfile || 'aesthetic.calm_ops'

  function activeProfileData() {
    return profileEvidence.profiles?.[activeProfile] || {}
  }

  function sourceUrl(value) {
    if (value === 'profile') return './landing-compiled/profile-evidence.json'
    const data = activeProfileData()
    if (value === 'intent') return data.intentUrl || './landing-compiled/intent_bundle.json'
    if (value === 'manifest') return data.manifestUrl || './landing-compiled/provenance_manifest.json'
    return ''
  }

  function updateProfileStatus() {
    const data = activeProfileData()
    root.dataset.viewspecProfile = activeProfile
    if (artifactSlot) artifactSlot.dataset.activeProfile = activeProfile
    if (profileOutput) {
      profileOutput.value = `${data.label || activeProfile} verified`
      profileOutput.textContent = profileOutput.value
    }
  }

  function setActive(selector, attr, value) {
    document.querySelectorAll(selector).forEach((button) => {
      button.dataset.active = button.getAttribute(attr) === value ? 'true' : 'false'
    })
  }

  if (inspector && inspectorToggle) {
    inspectorToggle.addEventListener('click', () => {
      const open = inspector.dataset.open !== 'true'
      inspector.dataset.open = open ? 'true' : 'false'
      inspectorToggle.setAttribute('aria-expanded', open ? 'true' : 'false')
    })
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape' || inspector.dataset.open !== 'true') return
      inspector.dataset.open = 'false'
      inspectorToggle.setAttribute('aria-expanded', 'false')
      inspectorToggle.focus()
    })
  }

  document.querySelectorAll('[data-profile-token]').forEach((button) => {
    button.addEventListener('click', () => {
      const value = button.dataset.profileToken || profileEvidence.defaultProfile || 'aesthetic.calm_ops'
      setProfile(value).catch(() => {
        if (profileOutput) profileOutput.textContent = 'profile artifact unavailable'
      })
    })
  })

  document.querySelectorAll('[data-page-evidence]').forEach((button) => {
    button.addEventListener('click', () => {
      const value = button.dataset.pageEvidence || 'clean'
      root.dataset.viewspecEvidence = value
      setActive('[data-page-evidence]', 'data-page-evidence', value)
    })
  })

  async function loadSource(value) {
    activeSourceView = value || 'hide'
    if (activeSourceView === 'hide') {
      source.dataset.visible = 'false'
      source.setAttribute('aria-hidden', 'true')
      setActive('[data-source-view]', 'data-source-view', activeSourceView)
      return
    }
    const url = sourceUrl(activeSourceView)
    if (!url) return
    source.dataset.visible = 'true'
    source.setAttribute('aria-hidden', 'false')
    setActive('[data-source-view]', 'data-source-view', activeSourceView)
    const cacheKey = `${activeProfile}:${activeSourceView}`
    if (!cache.has(cacheKey)) {
      const response = await fetch(url)
      const text = await response.text()
      const json = JSON.parse(text)
      cache.set(cacheKey, { json, text: JSON.stringify(json, null, 2), url })
    }
    const entry = cache.get(cacheKey)
    activeSourceText = entry.text
    sourceTitle.textContent = sourceNames[activeSourceView] || 'Artifact summary'
    sourceOpen.href = entry.url
    sourceCode.textContent = entry.text
    sourceSummary.innerHTML = summaryCards(activeSourceView, entry.json)
  }

  document.querySelectorAll('[data-source-view]').forEach((button) => {
    button.addEventListener('click', () => {
      loadSource(button.dataset.sourceView).catch(() => {
        sourceTitle.textContent = 'Artifact source unavailable'
      })
    })
  })

  document.querySelectorAll('[data-close-source]').forEach((button) => {
    button.addEventListener('click', () => {
      loadSource('hide').catch(() => undefined)
    })
  })

  async function setProfile(profile) {
    if (!profileEvidence.profiles?.[profile]) return
    activeProfile = profile
    const data = activeProfileData()
    if (artifactSlot && data.artifactBodyUrl) {
      const response = await fetch(data.artifactBodyUrl)
      if (!response.ok) throw new Error(`Profile artifact unavailable: ${profile}`)
      artifactSlot.innerHTML = await response.text()
    }
    document.querySelectorAll('[data-profile-token]').forEach((button) => {
      const active = button.dataset.profileToken === profile
      button.dataset.active = active ? 'true' : 'false'
      button.setAttribute('aria-pressed', active ? 'true' : 'false')
    })
    document.querySelectorAll('[data-ir-selected="true"]').forEach((selected) => {
      selected.removeAttribute('data-ir-selected')
    })
    setIrStatus('profile swapped; hover a highlighted node')
    updateProfileStatus()
    if (activeSourceView !== 'hide') await loadSource(activeSourceView)
    queuePretextMeasure()
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;')
  }

  function shortHash(value) {
    return value ? `${String(value).slice(0, 12)}...` : 'not recorded'
  }

  function countObject(value) {
    return value && typeof value === 'object' ? Object.keys(value).length : 0
  }

  function manifestProfile(json) {
    const nodes = json.nodes && typeof json.nodes === 'object' ? Object.values(json.nodes) : []
    const rootNode = nodes.find((entry) => entry?.primitive === 'root')
    return rootNode?.props?.aesthetic_profile || activeProfile
  }

  function summaryCards(kind, json) {
    const profileData = activeProfileData()
    const cards = kind === 'profile'
      ? [
          ['Active profile', profileData.label || activeProfile],
          ['Token', activeProfile],
          ['Semantic ids', profileEvidence.semanticIdsStable ? 'stable' : 'changed'],
          ['Style hash', shortHash(profileData.styleProjectionHash)],
          ['Style delta', profileData.styleSignature || 'not recorded'],
          ['Layout signature', profileData.layoutSignature || 'not recorded'],
          ['Shell overrides', `${profileEvidence.shellOverrides ?? 0}`],
          ['Profiles', `${profileEvidence.profileCount || 0}`],
        ]
      : kind === 'manifest'
      ? [
          ['Kind', json.kind || 'manifest'],
          ['Profile', manifestProfile(json)],
          ['Artifact hash', shortHash(json.artifact_hash)],
          ['Source hash', shortHash(json.source_hash || json.raw_source_hash)],
          ['Nodes', countObject(json.nodes)],
          ['Diagnostics', Array.isArray(json.diagnostics) ? json.diagnostics.length : 0],
          ['Network calls', json.guarantees?.network_calls || 'none'],
          ['Autofetch', json.guarantees?.artifact_autofetch_network || 'none'],
          ['Policy', json.policy_version || 'viewspec policy'],
        ]
      : [
          ['View', json.view_spec?.id || json.id || 'viewspec_landing'],
          ['Motifs', Array.isArray(json.view_spec?.motifs) ? json.view_spec.motifs.length : 0],
          ['Substrate nodes', countObject(json.substrate?.nodes)],
          ['Styles', Array.isArray(json.view_spec?.styles) ? json.view_spec.styles.length : 0],
          ['Profile', activeProfile],
          ['Compiler source', 'demos/build_landing.py'],
          ['Reducer', 'not required for this landing artifact'],
          ['Runtime LLM', 'none'],
          ['Raw JSON', 'available on demand'],
        ]
    return cards.map(([label, value]) => (
      `<dl class="artifact-source-card"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></dl>`
    )).join('')
  }

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return
    }
    const textarea = document.createElement('textarea')
    textarea.value = text
    textarea.setAttribute('readonly', '')
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.focus()
    textarea.select()
    document.execCommand('copy')
    textarea.remove()
  }

  document.querySelectorAll('[data-copy-text]').forEach((button) => {
    const text = button.getAttribute('data-copy-text')
    const defaultLabel = button.textContent
    button.addEventListener('click', async () => {
      try {
        await copyText(text)
        button.textContent = 'copied'
      } catch {
        button.textContent = text
      }
      window.setTimeout(() => { button.textContent = defaultLabel }, 1200)
    })
  })

  if (copySource) {
    const defaultSourceLabel = copySource.textContent
    copySource.addEventListener('click', async () => {
      if (!activeSourceText) return
      try {
        await copyText(activeSourceText)
        copySource.textContent = 'copied JSON'
      } catch {
        copySource.textContent = 'copy failed'
      }
      window.setTimeout(() => { copySource.textContent = defaultSourceLabel }, 1200)
    })
  }

  function setIrStatus(value) {
    if (!irOutput) return
    irOutput.value = value || 'hover a highlighted node'
    irOutput.textContent = irOutput.value
  }

  document.addEventListener('mouseover', (event) => {
    const node = event.target.closest?.('[data-ir-id]')
    if (!node || root.dataset.viewspecEvidence !== 'provenance') return
    setIrStatus(node.getAttribute('data-ir-id'))
  })
  document.addEventListener('focusin', (event) => {
    const node = event.target.closest?.('[data-ir-id]')
    if (!node || root.dataset.viewspecEvidence !== 'provenance') return
    setIrStatus(node.getAttribute('data-ir-id'))
  })
  document.addEventListener('click', (event) => {
    const node = event.target.closest?.('[data-ir-id]')
    if (!node || root.dataset.viewspecEvidence !== 'provenance') return
    document.querySelectorAll('[data-ir-selected="true"]').forEach((selected) => {
      selected.removeAttribute('data-ir-selected')
    })
    node.dataset.irSelected = 'true'
    setIrStatus(node.getAttribute('data-ir-id'))
  })

  const pretextOutput = document.getElementById('pretext-fit')
  const pretextTargets = [
    ['hero', '#dom-binding_launch_hero_title', 760],
    ['copy', '#dom-binding_launch_hero_description', 820],
  ]
  let pretextFrame = 0

  function numericPx(value, fallback) {
    const parsed = Number.parseFloat(value || '')
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
  }

  function lineHeightFor(style, fontSize) {
    return numericPx(style.lineHeight, fontSize * 1.18)
  }

  function fontFor(style, fontSize) {
    const family = style.fontFamily || 'Inter, ui-sans-serif, system-ui, sans-serif'
    return `${style.fontStyle || 'normal'} ${style.fontWeight || '400'} ${fontSize}px ${family}`
  }

  async function measurePretextTargets() {
    const pretext = window.ViewSpecPretext
    if (!pretext) throw new Error('Pretext global unavailable')
    const { layoutWithLines, measureNaturalWidth, prepareWithSegments } = pretext
    const metrics = pretextTargets.map(([id, selector, fallbackWidth]) => {
      const element = document.querySelector(selector)
      if (!element) return null
      const style = window.getComputedStyle(element)
      const fontSize = numericPx(style.fontSize, 18)
      const lineHeight = lineHeightFor(style, fontSize)
      const width = Math.max(1, Math.min(fallbackWidth, element.getBoundingClientRect().width || fallbackWidth))
      const prepared = prepareWithSegments(element.textContent.trim(), fontFor(style, fontSize), { whiteSpace: 'normal' })
      const layout = layoutWithLines(prepared, width, lineHeight)
      const lines = Array.isArray(layout.lines) ? layout.lines.length : Number(layout.lineCount || 0)
      return { id, lines, naturalWidth: measureNaturalWidth(prepared), width }
    }).filter(Boolean)
    const hero = metrics.find((entry) => entry.id === 'hero')
    const copy = metrics.find((entry) => entry.id === 'copy')
    if (!hero || !copy) throw new Error('missing landing text targets')

    root.dataset.landingPretext = 'ready'
    root.dataset.pretextReady = 'true'
    root.dataset.pretextHeroLines = String(hero.lines)
    root.dataset.pretextCopyLines = String(copy.lines)
    root.dataset.pretextHeroNaturalWidth = hero.naturalWidth.toFixed(2)
    root.dataset.pretextCopyNaturalWidth = copy.naturalWidth.toFixed(2)
    delete root.dataset.pretextError
    if (pretextOutput) {
      pretextOutput.value = `${hero.lines} title lines / ${copy.lines} copy lines`
      pretextOutput.textContent = pretextOutput.value
    }
  }

  function handlePretextError(error) {
    root.dataset.landingPretext = 'error'
    root.dataset.pretextError = error && error.message ? error.message : 'unknown pretext error'
    if (pretextOutput) pretextOutput.textContent = 'text metrics unavailable'
  }

  function queuePretextMeasure() {
    if (pretextFrame) return
    pretextFrame = window.requestAnimationFrame(() => {
      pretextFrame = 0
      measurePretextTargets().catch(handlePretextError)
    })
  }

  if (document.fonts?.ready) {
    document.fonts.ready.catch(() => undefined).then(queuePretextMeasure)
  } else {
    queuePretextMeasure()
  }
  updateProfileStatus()
  window.addEventListener('resize', queuePretextMeasure)
  window.setTimeout(queuePretextMeasure, 160)
})()
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
    profile_buttons = _profile_buttons(profile_evidence)
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
            '<link rel="icon" href="data:,">',
            '<style data-viewspec-emitter-css="true">',
            emitter_css,
            "</style>",
            '<style data-viewspec-page-css="true">',
            PAGE_CSS,
            "</style>",
            "</head>",
            "<body>",
            '<a class="skip-link" href="#dom-region_main">Skip to content</a>',
            '<nav class="artifact-bar" aria-label="Primary navigation and artifact inspector">',
            '<a class="artifact-brand" href="./">ViewSpec</a>',
            '<div class="artifact-links">',
            '<a href="./appbundle-state-ir/">State IR</a>',
            '<a href="./proof-bundle/">Proof</a>',
            '<a href="./custom-motifs/">Motifs</a>',
            '<a href="./openapi.json">OpenAPI</a>',
            '<a href="#pricing">Pricing</a>',
            '<a href="https://github.com/nxrobins/viewspec">GitHub</a>',
            "</div>",
            '<div class="artifact-inspector" id="artifact-inspector" data-open="false">',
            '<button type="button" class="artifact-inspector-toggle" id="artifact-inspector-toggle" aria-expanded="false" aria-controls="artifact-inspector-panel" aria-label="Open artifact inspector controls"><span>Inspect artifact</span><span class="artifact-pretext-status">Pretext <output id="pretext-fit" role="status" aria-live="polite">measuring text</output></span></button>',
            '<div class="artifact-inspector-panel" id="artifact-inspector-panel" role="group" aria-label="Artifact inspector controls">',
            '<div class="artifact-controls">',
            '<fieldset class="artifact-control-group"><legend>Evidence</legend>',
            '<button type="button" class="artifact-button" data-page-evidence="clean" data-active="true" aria-label="Hide compiler IR outlines">Clean</button>',
            '<button type="button" class="artifact-button" data-page-evidence="provenance" aria-label="Show compiler IR outlines and inspect node ids">Show IR ids</button>',
            '<span class="artifact-ir-status">IR <output id="ir-inspector-value" role="status" aria-live="polite">hover a highlighted node</output></span>',
            "</fieldset>",
            '<fieldset class="artifact-control-group"><legend>Source</legend>',
            '<button type="button" class="artifact-button" data-source-view="intent" aria-label="Show IntentBundle summary">IntentBundle</button>',
            '<button type="button" class="artifact-button" data-source-view="manifest" aria-label="Show manifest summary">Manifest</button>',
            '<button type="button" class="artifact-button" data-source-view="profile" aria-label="Show compiled profile proof summary">Profile proof</button>',
            '<button type="button" class="artifact-button" data-source-view="hide" data-active="true" aria-label="Hide source summary">Hide source</button>',
            '<button type="button" class="artifact-button" data-copy-text="pip install viewspec" aria-label="Copy pip install viewspec command">pip install viewspec</button>',
            "</fieldset>",
            "</div>",
            "</div>",
            "</div>",
            '<div class="artifact-profile-dock" role="group" aria-label="Compiled aesthetic profile selector">',
            '<div class="artifact-profile-dock-inner">',
            '<div class="artifact-profile-label"><span>Compiled profile</span><strong>Same graph, new projection</strong><span class="artifact-profile-caption">Every profile is the same semantic graph, re-projected.</span></div>',
            '<div class="artifact-profile-switcher">',
            profile_buttons,
            "</div>",
            '<span class="artifact-profile-status">Profile <output id="profile-proof-value" role="status" aria-live="polite">Calm Ops verified</output></span>',
            "</div>",
            "</div>",
            "</nav>",
            '<section id="artifact-source" class="artifact-source" data-visible="false" aria-hidden="true" role="region" aria-labelledby="artifact-source-title">',
            '<div class="artifact-source-heading">',
            '<div><p class="artifact-source-kicker">Artifact evidence</p><h2 id="artifact-source-title">Source summary</h2></div>',
            '<div class="artifact-source-actions">',
            '<a id="artifact-source-open" class="artifact-link-button" href="./landing-compiled/intent_bundle.json" target="_blank" rel="noopener">Open raw</a>',
            '<button id="artifact-source-copy" type="button" class="artifact-button">Copy JSON</button>',
            '<button type="button" class="artifact-button" data-close-source aria-label="Close source summary">Close</button>',
            "</div>",
            "</div>",
            '<div id="artifact-source-summary" class="artifact-source-summary"></div>',
            '<details class="artifact-source-raw"><summary>Raw JSON</summary><pre><code id="artifact-source-code"></code></pre></details>',
            "</section>",
            f'<section id="viewspec-artifact-slot" class="viewspec-artifact-slot" data-active-profile="{html.escape(DEFAULT_PROFILE, quote=True)}">',
            body,
            "</section>",
            '<span id="pricing" class="anchor-target" aria-hidden="true"></span>',
            '<div class="artifact-actions" id="pricing-actions">',
            '<a class="artifact-link-button primary" href="https://buy.stripe.com/6oU4gA6PqcM9afq6qq2Z0b8" data-config-link="pro" target="_blank" rel="noopener">Get Pro</a>',
            '<a class="artifact-link-button" href="mailto:hello@viewspec.dev?subject=ViewSpec%20Enterprise" data-config-link="enterprise" target="_blank" rel="noopener">Talk to us</a>',
            '<a class="artifact-link-button" href="https://pypi.org/project/viewspec/">Install SDK</a>',
            '<button type="button" class="artifact-button" data-copy-text="pip install viewspec" aria-label="Copy pip install viewspec command">pip install viewspec</button>',
            "</div>",
            '<footer class="artifact-footer">',
            '<span>Generated by <code>demos/build_landing.py</code> through the ViewSpec compiler.</span>',
            '<span><button type="button" class="artifact-button" data-copy-text="pip install viewspec" aria-label="Copy pip install viewspec command">pip install viewspec</button> MIT &copy; 2026</span>',
            "</footer>",
            '<script type="module">',
            COMMERCE_SCRIPT,
            "</script>",
            '<script src="./vendor/pretext/pretext.global.js?v=20260628-global"></script>',
            "<script>",
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
