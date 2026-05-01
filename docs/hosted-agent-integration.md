# Hosted Agent Integration

Agents should emit IntentBundle JSON and never CompositionIR. CompositionIR is compiler output.

For launch, the public SDK remains V1/reference-focused. Hosted compiler capabilities include projections, input bindings, rule bindings, custom motif libraries, React TSX, SwiftUI, and Flutter emitters.

Use these public entry points:

- System prompt: `https://viewspec.dev/agent-system-prompt.txt`
- JSON schema: `https://viewspec.dev/agent-intent-bundle.schema.json`
- Hosted OpenAPI: `https://viewspec.dev/openapi.json`
- Full LLM context: `https://viewspec.dev/llms-full.txt`
