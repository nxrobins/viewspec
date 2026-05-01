# Custom Motif Authoring

Custom motifs are hosted compiler contracts. A motif definition names accepted slots, source kinds, style tokens, and prompt guidance. The compiler validates the slot contract, then lowers the motif into standard CompositionIR primitives.

Launch tiering:

- Free: no custom motifs.
- Pro: up to 5 custom motif instances per compile.
- Existing Scale accounts: same 5-instance compile cap as Pro.
- Enterprise: unlimited custom motifs plus organization sharing and prompt injection tooling.

Emitters do not need custom motif code paths because the compiler output remains portable CompositionIR.
