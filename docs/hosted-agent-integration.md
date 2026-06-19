# Hosted Agent Integration

This page is for hosted/API integration. The local-first path is the same agent-native contract: agents emit `IntentBundle` JSON, then run `viewspec validate-intent`, `viewspec compile`, and `viewspec check`.

For hosted/API integration, agents should emit IntentBundle JSON and never CompositionIR. CompositionIR is compiler output.

For launch, the public SDK remains V1/reference-focused. Hosted compiler capabilities include projections, input bindings, rule bindings, custom motif libraries, React TSX, SwiftUI, and Flutter emitters.

Hosted examples that use those extended fields declare `contract_profile: "hosted_extended_v1"` in their artifact index. That marker means the source is still an IntentBundle, but it is not claiming to satisfy the local `viewspec validate-intent` V1 reference contract.

Use these public entry points:

- System prompt: `https://viewspec.dev/agent-system-prompt.txt`
- JSON schema: `https://viewspec.dev/agent-intent-bundle.schema.json`
- Valid starter example: `https://viewspec.dev/agent-intent-example.dashboard.json`
- AppBundle V1/V2 schema: `https://viewspec.dev/agent-app-bundle.schema.json`
- AppBundle internal-tool example: `https://viewspec.dev/agent-app-example.internal-tool.json`
- Asset manifest: `https://viewspec.dev/agent-assets.json`
- Hosted OpenAPI: `https://viewspec.dev/openapi.json`
- Full LLM context: `https://viewspec.dev/llms-full.txt`

## Theming with DESIGN.md

SDK clients may send optional root-level `design` context in the hosted `CompileRequestPayload` envelope for `/v1/compile`:

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

This is a hosted request envelope, not the local IntentBundle source contract. Local `viewspec validate-intent` intentionally rejects root `design` and `motif_library` fields so agents keep `viewspec.intent.json` portable and compiler-owned.

Strict ingestion rules:

- Colors must be exact sRGB hex values such as `#FFFFFF`; `rgba()`, `#FFF`, and named CSS colors are ignored with defaults.
- React/HTML emitters can map custom `fontFamily` CSS. In local HTML output, `typography.body` styles normal text and `typography.heading` styles prominent values through the compiler's emphasis token. Flutter and SwiftUI coerce custom font families to native system defaults while preserving size, weight, and tracking.
