"""Shared SEO metadata for generated demo pages."""

from __future__ import annotations

import json
from html import escape


def demo_head_metadata(*, title: str, description: str, canonical_path: str) -> str:
    canonical = f"https://viewspec.dev/{canonical_path.strip('/')}/"
    json_ld = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "WebPage",
            "description": description,
            "name": title,
            "url": canonical,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return "\n".join(
        [
            f'  <meta name="description" content="{escape(description, quote=True)}">',
            '  <meta name="robots" content="index,follow,max-snippet:-1,max-image-preview:large">',
            f'  <link rel="canonical" href="{escape(canonical, quote=True)}">',
            '  <link rel="alternate" type="text/markdown" title="ViewSpec for LLMs" href="https://viewspec.dev/llms.txt">',
            '  <link rel="service" type="application/json" title="ViewSpec OpenAPI" href="https://viewspec.dev/openapi.json">',
            f"  <script type=\"application/ld+json\">{json_ld}</script>",
        ]
    )
