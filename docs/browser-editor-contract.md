# Browser editor contract, first slice

The experimental browser bridge distinguishes an editable document from a string field. `observe`
shows its mode (`plain`, `rich`, `source`, or `unknown`), codec, coverage, opaque revision, and supported
operations. Form properties expose the same descriptor in `x-editor`. Native inputs and textareas
retain their existing string interface.

```json
{
  "action": "edit",
  "handle": 58,
  "value": {
    "base_revision": "editor-<revision from the current view>",
    "operations": [{"op": "append_paragraph", "text": "Bring the printed site map."}]
  }
}
```

`append_paragraph` adds one nonempty paragraph containing literal text, without line separators. It
preserves the preceding supported document semantics. It is offered only when the final block is a
supported, nonempty paragraph with a safe insertion point. List/code/empty tails require another
interaction. Each request currently accepts one operation, up to 10,000 text characters.

`replace_text` replaces a supported plain/source editor or an empty rich editor. Empty rich
replacement currently accepts one paragraph; multiline text needs a supported plain/source surface. It does not import
Markdown or HTML. Legacy `fill` on those surfaces uses this same operation; filling a nonempty rich
document with a string is refused. Form arguments use the typed object above, with the same planner
and verifier as `act`. There is no automatic editor-mode toggle.

The native editing path validates document/route identity, editor revision and mode before focusing,
after focus, and before input. Paragraph insertion uses a native Enter key and native text input.
After Enter, the bridge checks that the preceding blocks remain intact and the caret is in the new
empty paragraph before inserting text. It checks the full intended result again after settling.
Verification is about editor state, **not whether the application persisted it**. Save and reread the
saved result separately.

## Supported semantics and limits

The versioned `html-dom-v1` codec covers a conservative DOM vocabulary: paragraphs, headings, simple
lists, code/preformatted blocks, hard breaks, standard inline marks, and link destinations and
attributes. Unrecognized rich content, styled inline islands, nested list layouts, embeds, known
partial/virtualized markers, and documents beyond 1,000 nodes or 20,000 characters across retained text and semantic attributes yield unknown
coverage. Reading unsupported blocks does not imply an available editing operation.

`mbe-dom-v1` additionally reads the observed Memos block renderer's task items, live checkbox state,
tags, and source-line containers. Source lines preserve blank lines using their actual logical line
boundaries. This adapter is detected from public DOM structure, not the URL or a presumed framework.
It reads no hidden application state and makes no application API calls. Mode names do not establish
an import language.

“Complete” means the bounded mounted DOM is covered by this codec. It cannot prove that an application
has no unmounted content or detect arbitrary stylesheet-driven semantics. It is not a universal
rich-text document serializer. New editor families and virtualized editors need explicit adapters
and evidence before claiming support. Composition in progress is unknown state.

Page-authored internal descriptors are stripped and regenerated. Document baselines stay inside the
browser; only opaque session revisions and compact capabilities leave it. Form schemas contain no
duplicate full-document default. Existing privacy policies apply to previews, supplied text and
results. Private or unsupported content is never treated as an empty document. Occupancy comes from the
same reader: unknown coverage yields unknown occupancy, and a structured document can be filled
even when it has no text. Requests known to exceed the reader limits are refused before input.

## Form preservation and partial effects

Omitting an editor preserves the supported document semantics and mode. Removing bold, changing a
link, replacing a paragraph with a heading, changing root inline styles, or switching mode cannot
pass as unchanged just because the text is the same. Unknown coverage withholds a guarded submit.
The baseline also binds the live editor node and observed revision.

Nested checklist controls remain actionable with `check`/`uncheck`. The editor owns their document
state, so those children are excluded from form arguments. After a separate checkbox action, use
the returned observation and its new editor revision for the next edit. This slice does not accept
conflicting parent/child edits in one form call.

This is not an atomic transaction across page handlers. Input, focus, and checkbox actions can
trigger autosaves, and an application may replace a control after saving. A late mismatch retains
write evidence and partial progress. Appending is not idempotent; uncertain or failed edits are
never classified safe to repeat merely because they resemble text entry. Inspect the new state
before retrying; the bridge does not roll back or silently retry.

The browser regressions exercise `act` and `call` against a server-side request journal, including
semantic omission drift, native paragraph insertion, source lines, nested controls, privacy,
unsupported content, focus/route/composition drift and autosave mismatch. Run:

```sh
VIEWSPEC_RUN_BROWSER_BRIDGE_E2E=1 python -m pytest tests/test_browser_editor_contract.py -q
```
