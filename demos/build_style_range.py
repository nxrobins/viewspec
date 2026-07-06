"""Build the Style Range specimen wall.

One IntentBundle, compiled through every aesthetic profile, laid out all at once — the
north-star gallery, but real: each specimen is honest compiler output (not a mockup), the eight
tiles share one semantic hash, and each carries a distinct style-projection hash. Reuses the
product-surface compile path from ``build_aesthetic_profiles`` so the specimens match the
one-at-a-time explorer served at ``/aesthetic-profiles/``.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from build_aesthetic_profiles import compile_profiles
from seo_metadata import demo_head_metadata
from viewspec import AESTHETIC_PROFILE_TOKENS
from viewspec.emitters.html_tailwind import OFFLINE_EMITTER_CSS

ROOT = Path(__file__).resolve().parents[1]

# Shell CSS as a plain string (NOT an f-string) so its literal braces need no escaping. Every
# shell class is prefixed `sr-` so none collide with the emitter's `.vs-*` classes (SC-3). The
# palette matches the light-gallery-showroom system used by the aesthetic-profiles page.
SHELL_CSS = """
    :root {
      --bg: #f6f8fb;
      --panel: #ffffff;
      --line: #d8e0e8;
      --line-strong: #b8c4d1;
      --ink: #101820;
      --muted: #5c6978;
      --accent: #0f766e;
      --accent-soft: #dff6f2;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
      line-height: 1.55;
      -webkit-font-smoothing: antialiased;
    }
    .sr-page { max-width: 1180px; margin: 0 auto; padding: 40px 24px 72px; }
    a { color: var(--accent); }

    .sr-hero { border-bottom: 1px solid var(--line); padding-bottom: 30px; margin-bottom: 34px; }
    .sr-eyebrow {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.72rem;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: var(--accent);
      margin: 0 0 12px;
    }
    .sr-hero h1 {
      font-size: clamp(1.9rem, 4vw, 2.9rem);
      line-height: 1.05;
      letter-spacing: -0.02em;
      margin: 0 0 14px;
      text-wrap: balance;
    }
    .sr-lead { color: var(--muted); max-width: 68ch; margin: 0 0 22px; font-size: 1.02rem; }
    .sr-lead b { color: var(--ink); font-weight: 650; }

    .sr-proof {
      list-style: none;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      padding: 0;
      margin: 0 0 20px;
    }
    .sr-proof li {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--panel);
      padding: 14px 16px;
      display: flex;
      flex-direction: column;
      gap: 3px;
    }
    .sr-proof strong { font-size: 1.5rem; line-height: 1; font-variant-numeric: tabular-nums; }
    .sr-proof span { color: var(--muted); font-size: 0.78rem; }
    .sr-proof code {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.72rem;
      color: var(--accent);
    }
    .sr-explore {
      display: inline-block;
      font-weight: 600;
      text-decoration: none;
      border-bottom: 1px solid currentColor;
      padding-bottom: 1px;
    }

    .sr-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(min(100%, 460px), 1fr));
      gap: 22px;
    }
    .sr-tile {
      border: 1px solid var(--line);
      border-radius: 16px;
      background: var(--panel);
      overflow: hidden;
      display: flex;
      flex-direction: column;
      min-width: 0;
    }
    .sr-tile-head {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 12px;
      padding: 16px 18px 4px;
    }
    .sr-tile-id { min-width: 0; }
    .sr-token {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.72rem;
      color: var(--accent);
      display: block;
    }
    .sr-tile-head h2 { font-size: 1.12rem; margin: 2px 0 0; letter-spacing: -0.01em; }
    .sr-hash {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.68rem;
      color: var(--muted);
      white-space: nowrap;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 9px;
      flex: none;
    }
    .sr-note { color: var(--muted); font-size: 0.86rem; margin: 6px 18px 14px; }
    .sr-stage {
      height: 340px;
      overflow: hidden;
      border-top: 1px solid var(--line);
      /* No background here: the compiled artifact root carries its own ground (SC-3). */
    }
    .sr-stage > * { height: 100%; overflow: hidden; }
    .sr-tile-foot {
      border-top: 1px solid var(--line);
      padding: 10px 18px;
      font-size: 0.76rem;
      color: var(--muted);
      display: flex;
      justify-content: space-between;
      gap: 10px;
    }
    .sr-tile-foot a { text-decoration: none; font-weight: 600; white-space: nowrap; }

    .sr-contract {
      margin-top: 40px;
      border-top: 1px solid var(--line);
      padding-top: 26px;
    }
    .sr-contract h2 { font-size: 1.15rem; margin: 0 0 10px; }
    .sr-contract p { color: var(--muted); max-width: 92ch; margin: 0 0 10px; line-height: 1.7; }
    .sr-contract code {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.82em;
      color: var(--ink);
    }

    @media (max-width: 720px) {
      .sr-proof { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .sr-stage { height: 300px; }
    }
"""


def _assert_honest(profiles: dict[str, dict[str, Any]]) -> None:
    """SC-2: exactly eight tiles, one shared semantic hash, eight distinct style hashes."""
    if len(profiles) != len(AESTHETIC_PROFILE_TOKENS):
        raise RuntimeError(
            f"Style range expects {len(AESTHETIC_PROFILE_TOKENS)} profiles, got {len(profiles)}."
        )
    semantic_hashes = {data["semanticHash"] for data in profiles.values()}
    if len(semantic_hashes) != 1:
        raise RuntimeError(
            f"Style range specimens must share one semantic hash; found {len(semantic_hashes)}."
        )
    style_hashes: dict[str, str] = {}
    for profile, data in profiles.items():
        digest = data["styleProjectionHash"]
        if digest in style_hashes:
            raise RuntimeError(
                f"Style range needs distinct style projections; {profile} collides with "
                f"{style_hashes[digest]} at {digest[:12]}."
            )
        style_hashes[digest] = profile


def specimen_tiles(profiles: dict[str, dict[str, Any]]) -> str:
    tiles = []
    for profile, data in profiles.items():
        tiles.append(
            f"""      <article class="sr-tile" data-profile-token="{html.escape(profile)}">
        <header class="sr-tile-head">
          <div class="sr-tile-id">
            <span class="sr-token">{html.escape(profile)}</span>
            <h2>{html.escape(data["profileLabel"])}</h2>
          </div>
          <span class="sr-hash" title="style projection hash">{html.escape(data["styleProjectionHash"][:10])}</span>
        </header>
        <p class="sr-note">{html.escape(data["profileNote"])}</p>
        <div class="sr-stage" role="img" aria-label="{html.escape(data["profileLabel"])} compiled artifact">
          {data["fragment"]}
        </div>
        <footer class="sr-tile-foot">
          <span>Clipped preview &middot; {html.escape(data["styleSignature"])}</span>
          <a href="../aesthetic-profiles/">open full &rarr;</a>
        </footer>
      </article>"""
        )
    return "\n".join(tiles)


def build_page(profiles: dict[str, dict[str, Any]]) -> str:
    _assert_honest(profiles)
    first = next(iter(profiles.values()))
    semantic_short = html.escape(first["semanticHash"][:10])
    # One continuous string (no source line breaks) so the rendered paragraph text stays
    # contiguous in the raw HTML that the seo contract greps.
    lead_html = (
        "Every specimen below is the <b>same</b> IntentBundle compiled through a different "
        "view-level aesthetic profile. Same generated semantic ids, same provenance — only "
        "the governed style projection and bounded layout metadata change. This is real compiler "
        "output, not a mockup: eight distinct design languages from one semantic graph."
    )
    tiles = specimen_tiles(profiles)
    profile_tokens = ", ".join(AESTHETIC_PROFILE_TOKENS)
    head_meta = demo_head_metadata(
        title="ViewSpec Demo - The Style Range, All at Once",
        description=(
            "One ViewSpec IntentBundle compiled through eight deterministic aesthetic profiles, "
            "shown side by side as honest compiler output with one shared semantic hash and eight "
            "distinct style projections."
        ),
        canonical_path="style-range",
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ViewSpec Demo - The Style Range, All at Once</title>
{head_meta}
  <link rel="icon" href="data:,">
  <script src="../shared/nav.js" defer></script>
  <style>
{OFFLINE_EMITTER_CSS}
  </style>
  <style data-demo-shell-css="true">
{SHELL_CSS}
  </style>
</head>
<body>
  <main class="sr-page">
    <section class="sr-hero" data-presentation-contract="specimen-wall">
      <p class="sr-eyebrow">One intent, eight grounds</p>
      <h1>The whole style range, compiled at once.</h1>
      <p class="sr-lead">{lead_html}</p>
      <ul class="sr-proof" aria-label="Style range invariants">
        <li><strong>1</strong><span>shared IntentBundle</span></li>
        <li><strong>1</strong><span>semantic hash <code>{semantic_short}</code></span></li>
        <li><strong>{len(profiles)}</strong><span>distinct style hashes</span></li>
        <li><strong>0</strong><span>shell overrides</span></li>
      </ul>
      <a class="sr-explore" href="../aesthetic-profiles/">Explore one at a time, with full provenance &rarr;</a>
    </section>

    <section class="sr-grid" aria-label="Compiled aesthetic profile specimens">
{tiles}
    </section>

    <section class="sr-contract">
      <h2>Why every specimen is honest</h2>
      <p>All {len(profiles)} tiles compile from one IntentBundle (<code>{profile_tokens}</code>)
        through the public local path. The demo asserts, at build time, that they share exactly one
        semantic hash and that all {len(profiles)} style-projection hashes are distinct — the page
        cannot build if two looks collapse or a specimen goes missing.</p>
      <p>The gallery frame styles the page <em>around</em> the artifacts; it never touches generated
        artifact internals or overrides the emitted profile output. Each tile shows a clipped
        preview — open the explorer for the full artifact, style delta, and layout signature.</p>
    </section>
  </main>
</body>
</html>
"""


def main() -> None:
    profiles = compile_profiles()
    output_path = ROOT / "demos" / "style-range" / "index.html"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_page(profiles), encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
