# Hosted Agent Integration

This page is for hosted/API integration. The local-first path is the same agent-native contract: agents emit `IntentBundle` JSON, then run `viewspec validate-intent`, `viewspec compile`, and `viewspec check`.

For hosted/API integration, agents should emit IntentBundle JSON and never CompositionIR. CompositionIR is compiler output.

For launch, the public SDK remains V1/reference-focused. The hosted `/v1/compile` API adds projections, input bindings, rule bindings, custom motif libraries, Level 2 derivation, and DESIGN.md context to its portable AST response. Paid callers can use `/v1/artifacts` or `compile_artifact_remote(...)` for integrity-checked HTML, React TSX, SwiftUI, and Flutter files. For multi-screen applications, `/v1/app-bundles/build` and `compile_app_remote(...)` return a checked runnable React/Tailwind project with deterministic build identity, a complete outer manifest, and an Ed25519 build receipt. The SDK verifies the published receipt key before returning or writing files.

Hosted examples that use those extended fields declare `contract_profile: "hosted_extended_v1"` in their artifact index. That marker means the source is still an IntentBundle, but it is not claiming to satisfy the local `viewspec validate-intent` V1 reference contract.

Use these public entry points:

- System prompt: `https://viewspec.dev/agent-system-prompt.txt`
- JSON schema: `https://viewspec.dev/agent-intent-bundle.schema.json`
- Valid starter example: `https://viewspec.dev/agent-intent-example.dashboard.json`
- AppBundle V1/V2/V3/V4 schema: `https://viewspec.dev/agent-app-bundle.schema.json`
- AppBundle internal-tool example: `https://viewspec.dev/agent-app-example.internal-tool.json`
- Asset manifest: `https://viewspec.dev/agent-assets.json`
- Hosted OpenAPI: `https://viewspec.dev/openapi.json`
- Full LLM context: `https://viewspec.dev/llms-full.txt`

## Hosted AppBundle Build

```python
from viewspec import compile_app_remote, starter_react_app_bundle

build = compile_app_remote(starter_react_app_bundle(), api_key="vs_pro_...")
build.write_to("generated-app")
```

The helper posts the AppBundle to the hosted compiler, verifies every returned file, checks the
service-owned manifest and deterministic build identity, fetches `/v1/receipt-key`, verifies the
Ed25519 build receipt, and only then returns a typed response. Output materialization refuses to
overwrite an existing directory.

## Hosted Verification

Hosted verification is paid and compiles the complete AppBundle before checking route/state
behavior and every screen at canonical mobile, tablet, and desktop viewports.

```python
from viewspec import starter_react_app_bundle, submit_verification_remote

job = submit_verification_remote(
    starter_react_app_bundle(),
    api_key="vs_pro_...",
)
assert job.result is not None
print(job.result.status)
print(job.repair_plan.to_json())
job.write_evidence_to("verification-evidence")
```

The client checks the request-derived job id, every evidence hash and byte count, exact result and
artifact-set equality, and the Ed25519 verification receipt before exposing a successful job.
Repair plans group stable diagnostics by source node across viewports and provide the exact next
lineage. A retry must descend from an owned, nonconformant or indeterminate parent with the same
verification plan.

## Compile Until Conformant

`compile_until_conformant_remote(...)` provides a bounded premium loop while keeping semantic
editing in the caller's agent:

```python
from viewspec import compile_until_conformant_remote, starter_react_app_bundle

def repair_app(app_bundle, repair_plan):
    # Give the semantic AppBundle and repair_plan to the caller's coding agent.
    return agent.repair_app_bundle(app_bundle, repair_plan.to_json())

run = compile_until_conformant_remote(
    starter_react_app_bundle(),
    repair_attempt=repair_app,
    max_attempts=3,
    api_key="vs_pro_...",
)
print(run.status, run.run_id)
```

The loop stops on conformance, an unchanged repair, or the configured attempt bound. It retries an
indeterminate infrastructure result without asking the agent to edit the AppBundle, rejects plan
drift, and records every signed verification result and derived repair plan in the convergence run.

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

Every successful compile includes `meta.compiler`, and paid artifact responses expose the same
identity as `response.compiler`. Record its `build_id` and `source_revision` with generated output
when exact hosted reproducibility matters. The identity also binds the contract profile, API and
public SDK versions, and the SHA-256 digest of the canonical IntentBundle boundary.

This is a hosted request envelope, not the local IntentBundle source contract. Local `viewspec validate-intent` intentionally rejects root `design` and `motif_library` fields so agents keep `viewspec.intent.json` portable and compiler-owned.

Strict ingestion rules:

- Colors must be exact sRGB hex values such as `#FFFFFF`; `rgba()`, `#FFF`, and named CSS colors are ignored with defaults.
- React/HTML emitters can map custom `fontFamily` CSS. In local HTML output, `typography.body` styles normal text and `typography.heading` styles prominent values through the compiler's emphasis token. Flutter and SwiftUI coerce custom font families to native system defaults while preserving size, weight, and tracking.
