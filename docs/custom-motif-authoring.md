# Custom Motif Authoring

ViewSpec has two custom motif surfaces with different contracts.

Hosted custom motifs are schema-driven compiler contracts. A motif definition names accepted slots, source kinds, style tokens, and prompt guidance. The hosted compiler validates the slot contract, then lowers the motif into standard CompositionIR primitives.

Local motif plugins are Python-side compiler extensions. They are registered in an explicit `MotifRegistry`, run in-process, and return portable CompositionIR through the `MotifPlugin` ABI. Local plugins are deterministic extension points only: there is no dynamic discovery, filesystem loading, remote fetch, sandbox boundary, or untrusted plugin execution story in the local V1 contract.

```python
from viewspec import (
    MotifPlugin,
    MotifPluginFixture,
    MotifPluginManifest,
    MotifPluginSlot,
    check_motif_plugin,
    create_motif_registry,
    compile,
)

candlestick_plugin = MotifPlugin(
    kinds=("financial_candlestick_chart",),
    build=build_candlestick_chart,
    manifest=MotifPluginManifest(
        plugin_id="enterprise.financial_candlestick_chart",
        version="1.0.0",
        kinds=("financial_candlestick_chart",),
        input_slots=(
            MotifPluginSlot("open", present_as=("value",)),
            MotifPluginSlot("high", present_as=("value",)),
            MotifPluginSlot("low", present_as=("value",)),
            MotifPluginSlot("close", present_as=("value",)),
        ),
        output_guarantees=("deterministic_ir",),
    ),
)

enterprise_registry = create_motif_registry(
    candlestick_plugin,
    MotifPlugin(kinds=("medical_dicom_viewer",), build=build_dicom_viewer),
)

report = check_motif_plugin(
    candlestick_plugin,
    fixtures=(MotifPluginFixture(id="candlestick.basic", bundle=fixture_bundle),),
)
assert report.ok, report.issues

ast = compile(bundle, motif_registry=enterprise_registry)
```

For one-off local experiments, callers may also pass `compile(..., motif_plugins=(plugin,))`; manifestless plugins still compile when explicitly passed. Reusable or public local plugins should include a `MotifPluginManifest`, pass `check_motif_plugin()`, and be installed through a named registry assembled once and reused.

`MotifRegistry.describe()` returns JSON-safe metadata for built-in kinds, custom kinds, and manifest-backed custom plugins. It is intended for CI checks, debugging, and plugin inventory, not dynamic discovery.

Launch tiering for hosted custom motifs:

- Free: no custom motifs.
- Pro: up to 5 custom motif instances per compile.
- Enterprise: unlimited custom motifs plus organization sharing.

Emitters do not need custom motif code paths because compiler output remains portable CompositionIR.

Massive streaming and highly dynamic data applications are a future contract profile. They are not a relaxation of the local V1 agent caps, which remain intentionally bounded for deterministic validation and proof.
