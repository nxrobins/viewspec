# Hosted Agent Integration

Agents should emit IntentBundle JSON and never CompositionIR. CompositionIR is compiler output.

For launch, the public SDK remains V1/reference-focused. Hosted compiler capabilities include projections, input bindings, rule bindings, custom motif libraries, React TSX, SwiftUI, and Flutter emitters.

Use these public entry points:

- System prompt: `https://viewspec.dev/agent-system-prompt.txt`
- JSON schema: `https://viewspec.dev/agent-intent-bundle.schema.json`
- Hosted OpenAPI: `https://viewspec.dev/openapi.json`
- Full LLM context: `https://viewspec.dev/llms-full.txt`

## Theming with DESIGN.md

SDK clients may send optional root-level `design` context to `/v1/compile`:

```json
{
  "substrate": {},
  "view_spec": {},
  "design": {
    "format": "design.md",
    "content": "name: Acme\ncolor.primary: #FFFFFF\n",
    "lint": true
  }
}
```

The SDK treats `content` as an opaque string. The hosted API owns parsing, linting, cycle detection, style mapping, and `meta.design` validation results.

Strict ingestion rules:

- Colors must be exact sRGB hex values such as `#FFFFFF`; `rgba()`, `#FFF`, and named CSS colors are ignored with defaults.
- React/HTML emitters can map custom `fontFamily` CSS. Flutter and SwiftUI coerce custom font families to native system defaults while preserving size, weight, and tracking.
