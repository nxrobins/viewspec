"""Shared IntentBundle tools for agent-native UI workflows."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from viewspec._canonical import canonical_json_utf8
from viewspec._version import __version__
from viewspec.agent import (
    MAX_AGENT_INTENT_BYTES,
    SUPPORTED_AGENT_MOTIFS,
    AgentValidationIssue,
    AgentValidationResult,
    _strict_json_loads,
    agent_correction_prompt,
    agent_repair_checklist,
    validate_agent_intent_bundle,
)
from viewspec.compact_form import (
    COMPACT_FORMAT,
    MAX_COMPACT_BYTES,
    canonical_json_text,
    compact_too_large_issue,
    detect_compact,
    looks_like_compact_text,
    normalize_with_issues,
)
from viewspec.compiler import compile
from viewspec.design_md import DesignSystemContext, load_design_system
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
from viewspec.emitters.react_registry import (
    REACT_REGISTRY_TARGETS,
    ReactComponentRegistry,
    ReactRegistryError,
    load_react_component_registry,
)
from viewspec.emitters.react_tailwind_tsx import (
    TAILWIND_RECIPE_REGISTRY_VERSION,
    ReactTailwindTsxEmitter,
    tailwind_recipe_registry_digest,
)
from viewspec.emitters.react_tsx import ReactTsxEmitter
from viewspec.json_patch import MAX_JSON_PATCH_OPERATIONS, JsonPatchError, apply_json_patch
from viewspec.local_tools import (
    INTENT_BUNDLE_POLICY_VERSION,
    MCP_APP_TARGET,
    LocalToolError,
    atomic_write,
    build_intent_semantic_digest,
    check_artifact_dir,
    check_issue_entry,
    ensure_no_input_overwrite,
    exception_response,
    file_hash,
    path_policy_metadata,
    resolve_cwd,
    resolve_local_path,
    source_hash,
    tool_error_response,
    tool_response,
)
from viewspec.manifest_summary import summarize_intent_manifest
from viewspec.raw_html import MANIFEST_SCHEMA_VERSION
from viewspec.sdk.builder import ViewSpecBuilder
from viewspec.types import INTENT_BUNDLE_SCHEMA_VERSION, IntentBundle

BUNDLE_POLICY_VERSION = INTENT_BUNDLE_POLICY_VERSION
INTENT_RESULT_SCHEMA_VERSION = 1
INTENT_COMPILE_TARGETS = ("html-tailwind", "react-tsx", "react-tailwind-tsx")
STARTER_INTENT_KINDS = tuple(SUPPORTED_AGENT_MOTIFS)
CANONICAL_FORMAT = "canonical"
# --fixed-out keeps the author's key order of a canonical source. A compact source has no canonical
# text of its own: its fixed canonical document is what --canonical-out writes.
COMPACT_FIXED_OUT_MESSAGE = (
    "--fixed-out applies to a canonical IntentBundle source; this source is in the compact form. "
    "Use --canonical-out PATH to write its canonical document after the lossless fixes."
)
# A patch document is bounded like the source it edits: a larger patch cannot produce a source
# within the cap without removing what it adds.
MAX_INTENT_PATCH_BYTES = MAX_AGENT_INTENT_BYTES
MAX_INTENT_PATCH_OPERATIONS = MAX_JSON_PATCH_OPERATIONS
PATCH_REPAIR_SUGGESTION = (
    "Fix the JSON Patch document, or regenerate the full IntentBundle; the source file is unchanged."
)


class IntentPatchError(LocalToolError):
    """A JSON Patch that could not be read or applied to an IntentBundle source.

    ``path`` locates the failure for the issue list: ``patch`` for the document as a whole and
    ``patch[<index>]`` for one operation.
    """

    def __init__(self, code: str, message: str, fix: str = PATCH_REPAIR_SUGGESTION, *, path: str = "patch") -> None:
        super().__init__(code, message, fix)
        self.path = path


def starter_intent_bundle(kind: str = "dashboard") -> IntentBundle:
    if kind not in STARTER_INTENT_KINDS:
        raise ValueError(f"Unknown starter intent kind: {kind}")
    builder = ViewSpecBuilder(
        f"starter_{kind}",
        root_attrs={"title": f"Starter {kind.replace('_', ' ').title()}"},
    )
    if kind == "table":
        table = builder.add_table("items", region="main", group_id="rows")
        table.add_row(label="Alpha", value="Ready", id="alpha")
        table.add_row(label="Beta", value="Next", id="beta")
    elif kind == "dashboard":
        dashboard = builder.add_dashboard("metrics", region="main", group_id="cards")
        dashboard.add_card(label="Revenue", value="$12.4K", id="revenue")
        dashboard.add_card(label="Users", value="1,284", id="users")
        dashboard.add_card(label="Weekly trend", value="Revenue +12%", id="weekly_trend")
        dashboard.add_card(
            label="Priority",
            value="Activation -4%",
            id="priority",
            value_present_as="badge",
        )
    elif kind == "outline":
        outline = builder.add_outline("plan", region="main", group_id="steps")
        outline.add_branch(label="Define intent", id="define")
        outline.add_branch(label="Compile artifact", id="compile")
    elif kind == "comparison":
        comparison = builder.add_comparison("options", region="main", group_id="choices")
        comparison.add_item(label="Standard", value="Core workflow", id="standard")
        comparison.add_item(label="Pro", value="Advanced workflow", id="pro")
    elif kind == "list":
        items = builder.add_list("next_steps", region="main", group_id="steps")
        items.add_item(label="Capture user intent", description="Describe the UI without writing DOM.", id="intent")
        items.add_item(
            label="Compile artifact", description="Let ViewSpec produce checked renderer output.", id="compile"
        )
    elif kind == "form":
        form = builder.add_form("contact", region="main", group_id="fields")
        form.add_field(label="Name", value="", id="name")
        form.add_field(label="Email", value="", id="email")
        builder.add_action(
            "submit_contact",
            "submit",
            "Submit",
            target_region="main",
            target_ref="motif:contact",
            payload_bindings=["name_value", "email_value"],
        )
    elif kind == "detail":
        detail = builder.add_detail("profile", region="main", group_id="fields")
        detail.add_field(label="Owner", value="Ada Lovelace", id="owner")
        detail.add_field(label="Status", value="Ready", id="status")
        detail.add_field(label="Next step", value="Compile the checked artifact", id="next_step")
    elif kind == "empty_state":
        builder.add_empty_state(
            "no_results",
            title="No results yet",
            description="Adjust filters or create the first item.",
            region="main",
            group_id="message",
        )
    elif kind == "loading_state":
        builder.add_loading_state(
            "loading_results",
            title="Loading results",
            description="The collection is being prepared.",
            region="main",
            group_id="message",
        )
    elif kind == "error_state":
        builder.add_error_state(
            "collection_error",
            title="Unable to load results",
            description="Retry after checking the source data.",
            region="main",
            group_id="message",
        )
    elif kind == "hero":
        builder.add_hero(
            "intro",
            eyebrow="Agent-native UI",
            title="Describe intent, not DOM",
            description="ViewSpec compiles semantic UI intent into checked renderer artifacts.",
            region="main",
            group_id="message",
        )
    return builder.build_bundle()


def starter_intent_payload(kind: str = "dashboard") -> dict[str, Any]:
    """Starter IntentBundle JSON payload carrying the self-describing schema_version field."""
    return {"schema_version": INTENT_BUNDLE_SCHEMA_VERSION, **starter_intent_bundle(kind).to_json()}


def starter_compact_payload(kind: str = "dashboard") -> dict[str, Any]:
    """The starter of ``kind`` in the compact authoring form.

    ``viewspec.compact_form.normalize`` turns it into exactly ``starter_intent_payload(kind)``.
    """
    if kind not in STARTER_INTENT_KINDS:
        raise ValueError(f"Unknown starter intent kind: {kind}")
    payload: dict[str, Any] = {
        "schema_version": INTENT_BUNDLE_SCHEMA_VERSION,
        "format": COMPACT_FORMAT,
        "id": f"starter_{kind}",
        "title": f"Starter {kind.replace('_', ' ').title()}",
    }
    if kind == "table":
        motif: dict[str, Any] = {
            "id": "items",
            "kind": "table",
            "group": "rows",
            "rows": [
                {"id": "alpha", "label": "Alpha", "value": "Ready"},
                {"id": "beta", "label": "Beta", "value": "Next"},
            ],
        }
    elif kind == "dashboard":
        motif = {
            "id": "metrics",
            "kind": "dashboard",
            "group": "cards",
            "cards": [
                {"id": "revenue", "label": "Revenue", "value": "$12.4K"},
                {"id": "users", "label": "Users", "value": "1,284"},
                {"id": "weekly_trend", "label": "Weekly trend", "value": "Revenue +12%"},
                {"id": "priority", "label": "Priority", "value": "Activation -4%", "present_as": "badge"},
            ],
        }
    elif kind == "outline":
        motif = {
            "id": "plan",
            "kind": "outline",
            "group": "steps",
            "items": [{"id": "define", "label": "Define intent"}, {"id": "compile", "label": "Compile artifact"}],
        }
    elif kind == "comparison":
        motif = {
            "id": "options",
            "kind": "comparison",
            "group": "choices",
            "items": [
                {"id": "standard", "label": "Standard", "value": "Core workflow"},
                {"id": "pro", "label": "Pro", "value": "Advanced workflow"},
            ],
        }
    elif kind == "list":
        motif = {
            "id": "next_steps",
            "kind": "list",
            "group": "steps",
            "items": [
                {"id": "intent", "label": "Capture user intent", "description": "Describe the UI without writing DOM."},
                {
                    "id": "compile",
                    "label": "Compile artifact",
                    "description": "Let ViewSpec produce checked renderer output.",
                },
            ],
        }
    elif kind == "form":
        motif = {
            "id": "contact",
            "kind": "form",
            "group": "fields",
            "fields": [{"id": "name", "label": "Name", "value": ""}, {"id": "email", "label": "Email", "value": ""}],
        }
        payload["actions"] = [
            {
                "id": "submit_contact",
                "kind": "submit",
                "label": "Submit",
                "target": "motif:contact",
                "payload": ["name", "email"],
            }
        ]
    elif kind == "detail":
        motif = {
            "id": "profile",
            "kind": "detail",
            "group": "fields",
            "fields": [
                {"id": "owner", "label": "Owner", "value": "Ada Lovelace"},
                {"id": "status", "label": "Status", "value": "Ready"},
                {"id": "next_step", "label": "Next step", "value": "Compile the checked artifact"},
            ],
        }
    elif kind == "empty_state":
        motif = {
            "id": "no_results",
            "kind": "empty_state",
            "group": "message",
            "title": "No results yet",
            "description": "Adjust filters or create the first item.",
        }
    elif kind == "loading_state":
        motif = {
            "id": "loading_results",
            "kind": "loading_state",
            "group": "message",
            "title": "Loading results",
            "description": "The collection is being prepared.",
        }
    elif kind == "error_state":
        motif = {
            "id": "collection_error",
            "kind": "error_state",
            "group": "message",
            "title": "Unable to load results",
            "description": "Retry after checking the source data.",
        }
    else:
        motif = {
            "id": "intro",
            "kind": "hero",
            "group": "message",
            "eyebrow": "Agent-native UI",
            "title": "Describe intent, not DOM",
            "description": "ViewSpec compiles semantic UI intent into checked renderer artifacts.",
        }
    payload["motifs"] = [motif]
    return payload


def init_intent_file(
    path: str | Path = "viewspec.intent.json", *, kind: str = "dashboard", force: bool = False
) -> Path:
    output = Path(path)
    if output.exists() and not force:
        raise ValueError(f"{output} already exists; pass --force to overwrite")
    payload = starter_intent_payload(kind)
    atomic_write(output, json.dumps(payload, indent=2, sort_keys=True))
    return output


def init_compact_file(
    path: str | Path = "viewspec.compact.json", *, kind: str = "dashboard", force: bool = False
) -> Path:
    """Write the starter of ``kind`` in the compact form, formatted like ``init_intent_file``."""
    output = Path(path)
    if output.exists() and not force:
        raise ValueError(f"{output} already exists; pass --force to overwrite")
    atomic_write(output, canonical_json_text(starter_compact_payload(kind)))
    return output


def validate_intent_text(
    text: str, *, compile_check: bool = True, fix: bool = True, compact: bool = True
) -> dict[str, Any]:
    """Validate IntentBundle JSON text; ``fix`` applies the lossless fixes first and reports them.

    ``compact`` accepts the compact authoring form. ``compact=False`` validates the text as a
    canonical bundle only, as AppBundle screens are validated.
    """
    return prepare_intent_source(text, compile_check=compile_check, fix=fix, compact=compact).validation


def validate_intent_file(path: str | Path, *, compile_check: bool = True, fix: bool = True) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    return validate_intent_text(text, compile_check=compile_check, fix=fix)


def intent_validation_payload(result: AgentValidationResult, *, compile_check: bool) -> dict[str, Any]:
    if not compile_check:
        compile_status = "skipped"
    elif result.valid:
        compile_status = "passed"
    else:
        compile_status = "failed"
    return {
        "schema_version": INTENT_RESULT_SCHEMA_VERSION,
        "ok": result.valid,
        "compile_check": compile_status,
        "issues": [issue.to_json() for issue in result.issues],
        "fixes": [fix.to_json() for fix in result.fixes],
        "repair_checklist": [] if result.valid else agent_repair_checklist(result),
        "correction_prompt": None if result.valid else agent_correction_prompt(result),
    }


@dataclass(frozen=True)
class IntentSourcePreparation:
    """An IntentBundle source after the optional patch and the validation that followed.

    ``text`` is the source that was validated: the patched text when ``patched`` is True, else the
    input. ``source_format`` is ``canonical`` or ``compact``. ``result.document`` is the canonical
    document after the lossless fixes: the source itself, or what a compact source normalizes to.
    ``result.bundle`` is the bundle to compile when ``validation["ok"]``.
    """

    text: str
    result: AgentValidationResult
    validation: dict[str, Any]
    patched: bool
    source_format: str = CANONICAL_FORMAT

    @property
    def compact(self) -> bool:
        return self.source_format == COMPACT_FORMAT

    @property
    def canonical_text(self) -> str | None:
        """What ``--canonical-out`` writes: the canonical document after the fixes, as ``viewspec init`` formats it.

        The fixes and the canonical issues of a compact source carry paths into this document. It is
        None when the source did not parse as a JSON object or a compact source did not normalize.
        """
        if self.result.document is None:
            return None
        return canonical_json_text(self.result.document)

    @property
    def raw_source_hash(self) -> str:
        """The sha256 of the source text that was validated: as written, or after the patch."""
        return source_hash(self.text)

    @property
    def manifest_source_hash(self) -> str:
        """The manifest ``source_hash``: the source text for a canonical source, ``canonical_text`` for a compact one."""
        canonical = self.canonical_text if self.compact else None
        return source_hash(canonical) if canonical is not None else self.raw_source_hash


def prepare_intent_source(
    text: str,
    *,
    compile_check: bool = True,
    fix: bool = True,
    patch: Sequence[Any] | None = None,
    compact: bool = True,
) -> IntentSourcePreparation:
    """Patch, normalize, fix, and validate an IntentBundle source, in that order.

    ``patch`` applies to the source as written, compact or canonical, so its pointers name that
    document. A patch that cannot be applied is reported as one ``PATCH_*`` issue and the source is
    not validated further. A source that is not JSON is validated as it stands, so the report names
    the JSON problem rather than a patch failure. A compact source (``"format": "compact"``) is then
    checked and normalized: its own problems keep ``$.``-rooted compact paths and stop there. The
    canonical document, as written or normalized, gets the lossless fixes and the canonical
    validator. For a compact source their paths point into ``canonical_text``, the document
    ``--canonical-out`` writes. ``compact=False`` reads every source as a canonical bundle.
    """
    patched = False
    if patch is not None:
        try:
            document: Any = _strict_json_loads(text)
        except (ValueError, RecursionError):
            document = None
        if document is not None:
            try:
                text = apply_intent_patch(document, patch)
            except IntentPatchError as exc:
                issue = AgentValidationIssue("error", exc.code, exc.path, exc.message, exc.fix)
                result = AgentValidationResult(valid=False, bundle=None, issues=[issue])
                return IntentSourcePreparation(
                    text, result, intent_validation_payload(result, compile_check=compile_check), False
                )
            patched = True
    compact_result = _compact_source_result(text, compile_check=compile_check, fix=fix) if compact else None
    if compact_result is not None:
        validation = intent_validation_payload(compact_result, compile_check=compile_check)
        # The paths in issues and fixes name either the compact source or its canonical document;
        # source_format tells an agent that both exist.
        validation["source_format"] = COMPACT_FORMAT
        return IntentSourcePreparation(text, compact_result, validation, patched, COMPACT_FORMAT)
    result = validate_agent_intent_bundle(text, require_reference_compiler_support=compile_check, fix=fix)
    return IntentSourcePreparation(
        text, result, intent_validation_payload(result, compile_check=compile_check), patched
    )


def _compact_source_result(text: str, *, compile_check: bool, fix: bool) -> AgentValidationResult | None:
    """Validate ``text`` as a compact source; None when it is not one, so the canonical path takes it."""
    size = len(text.encode("utf-8"))
    if size > MAX_COMPACT_BYTES:
        # Too large to parse under the cap: a compact marker gets the compact code, anything else
        # the canonical validator's INTENT_TOO_LARGE.
        if not looks_like_compact_text(text):
            return None
        return AgentValidationResult(valid=False, bundle=None, issues=[compact_too_large_issue(size)])
    try:
        document = _strict_json_loads(text)
    except (ValueError, RecursionError):
        return None  # The canonical validator reports the INVALID_JSON.
    if not detect_compact(document):
        return None
    canonical, issues = normalize_with_issues(document)
    if canonical is None:
        return AgentValidationResult(valid=False, bundle=None, issues=issues)
    # The dictionary path applies the 256 KB cap to the normalized bundle as well.
    return validate_agent_intent_bundle(canonical, require_reference_compiler_support=compile_check, fix=fix)


def _is_compact_text(text: str) -> bool:
    """Whether ``text`` is a compact-form source, without validating it."""
    if len(text.encode("utf-8")) > MAX_COMPACT_BYTES:
        return looks_like_compact_text(text)
    try:
        return detect_compact(_strict_json_loads(text))
    except (ValueError, RecursionError):
        return False


def reject_compact_fixed_out(text: str, prepared: IntentSourcePreparation | None = None) -> None:
    """Raise the ``--fixed-out`` usage error for a compact source, as written or after the patch."""
    if _is_compact_text(text) or (prepared is not None and prepared.compact):
        raise ValueError(COMPACT_FIXED_OUT_MESSAGE)


def load_intent_patch(path: str | Path) -> list[Any]:
    """Read an RFC 6902 patch document from a JSON file; the operations are checked when applied."""
    source = Path(path)
    try:
        raw = source.read_bytes()
    except FileNotFoundError as exc:
        raise IntentPatchError("PATCH_INVALID", f"Patch file not found: {source}") from exc
    except OSError as exc:
        raise IntentPatchError("PATCH_INVALID", f"Could not read patch file {source}: {exc}") from exc
    if len(raw) > MAX_INTENT_PATCH_BYTES:
        raise IntentPatchError(
            "PATCH_INVALID", f"Patch file is {len(raw)} bytes; the limit is {MAX_INTENT_PATCH_BYTES} bytes."
        )
    try:
        operations = _strict_json_loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise IntentPatchError("PATCH_INVALID", f"Patch file is not strict JSON: {exc}") from exc
    if not isinstance(operations, list):
        raise IntentPatchError("PATCH_INVALID", "A JSON Patch document must be an array of operation objects.")
    return operations


def apply_intent_patch(document: Any, operations: Sequence[Any]) -> str:
    """Apply ``operations`` to a parsed IntentBundle source and return the patched source text.

    The text is the form ``--patched-out`` writes: two-space indentation and the author's key
    order, with new keys at the end of their object. It must fit the IntentBundle byte cap.
    """
    try:
        patched = apply_json_patch(document, list(operations), max_operations=MAX_INTENT_PATCH_OPERATIONS)
    except JsonPatchError as exc:
        path = "patch" if exc.index is None else f"patch[{exc.index}]"
        raise IntentPatchError(exc.code, exc.message, path=path) from exc
    try:
        text = json.dumps(patched, indent=2, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise IntentPatchError("PATCH_INVALID", f"The patched document is not JSON: {exc}") from exc
    size = len(text.encode("utf-8"))
    if size > MAX_AGENT_INTENT_BYTES:
        raise IntentPatchError(
            "PATCH_RESULT_TOO_LARGE",
            f"The patched IntentBundle is {size} bytes; the v1 local limit is {MAX_AGENT_INTENT_BYTES} bytes.",
            "Split the UI into smaller IntentBundles, or remove content in the same patch.",
        )
    return text


def fixed_intent_source_text(result: AgentValidationResult) -> str | None:
    """The validated document after lossless fixes, in the form ``--fixed-out`` writes."""
    if result.document is None:
        return None
    return json.dumps(result.document, indent=2, allow_nan=False)


def patch_target_problem(path: Path) -> str | None:
    """Why ``path`` may not be patched, or None: a patch targets an IntentBundle source file only."""
    if path.is_dir():
        return f"{path} is a directory; a patch applies to an IntentBundle source file, never to compiled output."
    if path.suffix.lower() != ".json":
        return f"{path} is not a JSON source file; a patch applies to an IntentBundle source file."
    from viewspec.api import source_kind

    if source_kind(path) == "app_bundle":
        return f"{path} is an AppBundle; a patch applies to an IntentBundle source file."
    return None


def check_intent_source(
    path: str | Path,
    *,
    compile_check: bool = True,
    fix: bool = True,
    patch: Sequence[Any] | None = None,
    patched_out: str | Path | None = None,
    fixed_out: str | Path | None = None,
    canonical_out: str | Path | None = None,
) -> dict[str, Any]:
    """The ``check`` report for an IntentBundle file, with the optional patch and output files.

    Read failures are reported as issues, not raised. ``patch`` applies to the source in memory; the
    file itself is never changed. ``patched_out`` receives the patched source once the patch applied,
    and ``fixed_out`` receives the validated document after the lossless fixes whenever the source
    parsed as a JSON object. ``canonical_out`` receives the canonical document after the fixes,
    formatted as ``viewspec init`` writes it, whenever there is one: for a compact source, once it
    normalized. The report names each written file under ``patched_out``, ``fixed_out``, and
    ``canonical_out``. ``fixed_out`` on a compact source raises ``ValueError``: use ``canonical_out``.
    """
    source = Path(path)
    if patch is not None:
        problem = patch_target_problem(source)
        if problem is not None:
            return intent_error_payload(
                "PATCH_TARGET_FORBIDDEN",
                problem,
                "Pass the IntentBundle .json file that the compiled output came from.",
                compile_check=compile_check,
                path="patch",
            )
    try:
        text = source.read_text(encoding="utf-8")
    except FileNotFoundError:
        return intent_error_payload(
            "INTENT_FILE_NOT_FOUND",
            f"Intent file not found: {source}",
            "Create viewspec.intent.json or pass the correct intent file path.",
            compile_check=compile_check,
        )
    except OSError as exc:
        return intent_error_payload(
            "INTENT_FILE_READ_ERROR",
            f"Could not read intent file {source}: {exc}",
            "Use a readable local IntentBundle JSON file.",
            compile_check=compile_check,
        )
    if fixed_out is not None:
        reject_compact_fixed_out(text)
    prepared = prepare_intent_source(text, compile_check=compile_check, fix=fix, patch=patch)
    if fixed_out is not None:
        reject_compact_fixed_out(prepared.text, prepared)
    payload = dict(prepared.validation)
    if prepared.patched and patched_out is not None:
        atomic_write(Path(patched_out), prepared.text)
        payload["patched_out"] = str(patched_out)
    fixed_text = fixed_intent_source_text(prepared.result)
    if fixed_out is not None and fixed_text is not None:
        atomic_write(Path(fixed_out), fixed_text)
        payload["fixed_out"] = str(fixed_out)
    canonical_text = prepared.canonical_text
    if canonical_out is not None and canonical_text is not None:
        atomic_write(Path(canonical_out), canonical_text)
        payload["canonical_out"] = str(canonical_out)
    return payload


def intent_error_payload(
    code: str,
    message: str,
    suggestion: str,
    *,
    compile_check: bool,
    path: str = "$",
) -> dict[str, Any]:
    issue = AgentValidationIssue("error", code, path, message, suggestion)
    return intent_validation_payload(
        AgentValidationResult(valid=False, bundle=None, issues=[issue]),
        compile_check=compile_check,
    )


def _apply_ir_props_overlay(root_node: Any, overlay: dict[str, dict[str, Any]]) -> list[str]:
    """Merge bounded props onto IR nodes by node id, between compile() and emit.

    The overlay is a closed data structure used by AppBundle compilation. Only visibility,
    bounded state-text, resource identity markers, and the compiler-owned semantic embedding
    context are permitted, so this seam
    cannot become a generic style/content side channel.
    Returns the overlay node ids that did not resolve to an IR node (fail-closed at the caller).
    """
    allowed_keys = {
        "record_id",
        "resource_field",
        "resource_id",
        "resource_view_id",
        "semantic_context",
        "state_text_id",
        "state_text_initial",
        "visibility_rule_id",
        "visibility_hidden_initial",
    }
    for node_id, props in overlay.items():
        unexpected = set(props) - allowed_keys
        if unexpected:
            raise ValueError(f"ir_props_overlay contains unsupported keys {sorted(unexpected)} for {node_id}.")
        semantic_context = props.get("semantic_context")
        if semantic_context is not None and semantic_context != "embedded_screen":
            raise ValueError(
                f"ir_props_overlay semantic_context must be 'embedded_screen'; got {semantic_context!r} for {node_id}."
            )
        for key in ("record_id", "resource_field", "resource_id", "resource_view_id"):
            if key in props and (not isinstance(props[key], str) or not props[key]):
                raise ValueError(f"ir_props_overlay {key} must be a non-empty string for {node_id}.")
        state_text_id = props.get("state_text_id")
        if state_text_id is not None and (not isinstance(state_text_id, str) or not state_text_id):
            raise ValueError(f"ir_props_overlay state_text_id must be a non-empty string for {node_id}.")
        if "state_text_initial" in props and not isinstance(props.get("state_text_initial"), str):
            raise ValueError(f"ir_props_overlay state_text_initial must be a string for {node_id}.")
    remaining = dict(overlay)
    stack = [root_node]
    while stack and remaining:
        node = stack.pop()
        props = remaining.pop(node.id, None)
        if props is not None:
            node.props.update(props)
        stack.extend(getattr(node, "children", []) or [])
    return sorted(remaining)


def compile_intent_bundle_file_tool(
    input_path: str | Path,
    out_dir: str | Path,
    *,
    design_path: str | Path | None = None,
    strict_design: bool = False,
    target: str = "html-tailwind",
    cwd: str | Path | None = None,
    allow_outside_cwd: bool = False,
    ir_props_overlay: dict[str, dict[str, Any]] | None = None,
    uri: str | None = None,
    action_tools: dict[str, str] | None = None,
    fix: bool = True,
    patch_path: str | Path | None = None,
    patched_out: str | Path | None = None,
    fixed_out: str | Path | None = None,
    registry: str | Path | ReactComponentRegistry | None = None,
) -> dict[str, Any]:
    """Compile an IntentBundle file into ``out_dir`` and check the artifact; a tool envelope report.

    ``fix`` applies the lossless fixes before validation and compiles the fixed document; the fixes
    are reported under ``fixes``. ``patch_path`` names an RFC 6902 patch file applied to the source
    in memory first. ``patched_out`` and ``fixed_out`` write those documents and are reported under
    ``paths``.

    ``registry`` is a host component registry (a ``REGISTRY.json`` path or a loaded
    :class:`ReactComponentRegistry`) for the ``react-tsx`` and ``react-tailwind-tsx`` targets; see
    :mod:`viewspec.emitters.react_registry`. It is checked before the source is read, so a bad
    registry fails before any file is written. Emission through it runs on the canonical bundle that
    the source pipeline produced, after any patch, compact normalization, and lossless fixes.
    """
    root: Path | None = None
    try:
        root = resolve_cwd(cwd)
        if (uri is not None or action_tools) and target != MCP_APP_TARGET:
            raise ValueError(f"uri and action_tools apply to target='{MCP_APP_TARGET}', not {target}.")
        if target not in (*INTENT_COMPILE_TARGETS, MCP_APP_TARGET):
            return tool_error_response(
                "COMPILE_FAILED",
                f"Unsupported IntentBundle compile target: {target}",
                "Use target='html-tailwind', target='react-tsx', target='react-tailwind-tsx', or target='mcp-app'.",
                metadata={
                    "cwd": str(root),
                    "allow_outside_cwd": allow_outside_cwd,
                    "sdk_version": __version__,
                    "network_calls": "none",
                    "target": target,
                },
            )
        component_registry = _load_component_registry(
            registry, target=target, cwd=root, allow_outside_cwd=allow_outside_cwd
        )
        source = resolve_local_path(input_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
        output = resolve_local_path(out_dir, cwd=root, allow_outside_cwd=allow_outside_cwd)
        if target in {"react-tsx", "react-tailwind-tsx"}:
            ensure_no_input_overwrite(
                source, output, ("ViewSpecView.tsx", "provenance_manifest.json", "diagnostics.json")
            )
        else:
            ensure_no_input_overwrite(source, output, ("index.html", "provenance_manifest.json", "diagnostics.json"))
        text = source.read_text(encoding="utf-8")
        patch: list[Any] | None = None
        if patch_path is not None:
            problem = patch_target_problem(source)
            if problem is not None:
                raise IntentPatchError(
                    "PATCH_TARGET_FORBIDDEN",
                    problem,
                    "Pass the IntentBundle .json file that the compiled output came from.",
                )
            patch = load_intent_patch(
                resolve_local_path(patch_path, cwd=root, allow_outside_cwd=allow_outside_cwd, must_exist=True)
            )
        if fixed_out is not None:
            reject_compact_fixed_out(text)
        prepared = prepare_intent_source(text, compile_check=True, fix=fix, patch=patch)
        if fixed_out is not None:
            reject_compact_fixed_out(prepared.text, prepared)
        validation = prepared.validation
        if not validation["ok"]:
            return tool_error_response(
                "COMPILE_FAILED",
                "IntentBundle validation failed.",
                "Regenerate viewspec.intent.json using correction_prompt, then retry compile.",
                errors=[
                    {
                        "code": issue["code"],
                        "message": issue["message"],
                        "fix": issue.get("suggestion") or "Regenerate the full IntentBundle JSON.",
                    }
                    for issue in validation["issues"]
                ],
                data={
                    "validation": validation,
                    "correction_prompt": validation["correction_prompt"],
                    "fixes": validation["fixes"],
                },
                metadata={
                    "cwd": str(root),
                    "allow_outside_cwd": allow_outside_cwd,
                    "sdk_version": __version__,
                    "network_calls": "none",
                },
            )
        design = _load_optional_design(design_path, cwd=root, allow_outside_cwd=allow_outside_cwd, strict=strict_design)
        bundle = prepared.result.bundle
        if bundle is None:  # pragma: no cover - a valid result always carries its bundle
            raise ValueError("IntentBundle validation passed without a bundle.")
        extra_paths: dict[str, str] = {}
        if prepared.patched and patched_out is not None:
            written = resolve_local_path(patched_out, cwd=root, allow_outside_cwd=allow_outside_cwd)
            atomic_write(written, prepared.text)
            extra_paths["patched_out"] = str(written)
        fixed_text = fixed_intent_source_text(prepared.result)
        if fixed_out is not None and fixed_text is not None:
            written = resolve_local_path(fixed_out, cwd=root, allow_outside_cwd=allow_outside_cwd)
            atomic_write(written, fixed_text)
            extra_paths["fixed_out"] = str(written)
        ast = compile(bundle, design=design, strict_design=strict_design)
        if ir_props_overlay:
            unresolved = _apply_ir_props_overlay(ast.result.root.root, ir_props_overlay)
            if unresolved:
                unresolved_errors = []
                for node_id in unresolved:
                    props = ir_props_overlay.get(node_id, {})
                    if isinstance(props.get("state_text_id"), str):
                        unresolved_errors.append(
                            {
                                "code": "APP_STATE_TEXT_TARGET_UNRESOLVED",
                                "message": f"State text target node {node_id} was not found in the compiled IR.",
                                "fix": "Point the state_text rule at an exactly-once text binding declared by this screen.",
                            }
                        )
                    else:
                        unresolved_errors.append(
                            {
                                "code": "APP_VISIBILITY_TARGET_UNRESOLVED",
                                "message": f"Visibility target node {node_id} was not found in the compiled IR.",
                                "fix": "Point the visibility rule at a declared region, binding, or motif id.",
                            }
                        )
                return tool_error_response(
                    "COMPILE_FAILED",
                    f"App compiler target(s) did not resolve to compiled IR nodes: {', '.join(unresolved)}.",
                    "Verify every compiler-owned target id is declared in this screen's IntentBundle.",
                    errors=unresolved_errors,
                    metadata={
                        "cwd": str(root),
                        "allow_outside_cwd": allow_outside_cwd,
                        "sdk_version": __version__,
                        "network_calls": "none",
                    },
                )
        if target == "react-tsx":
            paths = ReactTsxEmitter(component_registry=component_registry).emit(ast, output)
            artifact_path = Path(paths["tsx"])
            emitter = "react_tsx"
        elif target == "react-tailwind-tsx":
            paths = ReactTailwindTsxEmitter(component_registry=component_registry).emit(ast, output)
            artifact_path = Path(paths["tsx"])
            emitter = "react_tailwind_tsx"
        elif target == MCP_APP_TARGET:
            from viewspec.mcp_apps import default_mcp_app_uri, emit_mcp_app

            paths = emit_mcp_app(ast, output, uri=uri or default_mcp_app_uri(bundle), action_tools=action_tools)
            artifact_path = Path(paths["html"])
            emitter = "html_tailwind"
        else:
            paths = HtmlTailwindEmitter().emit(ast, output)
            artifact_path = Path(paths["html"])
            emitter = "html_tailwind"
        wrap_intent_bundle_manifest(
            Path(paths["manifest"]),
            source_name=source.name,
            raw_source_hash=prepared.raw_source_hash,
            design=design,
            command_args=_compile_command_args(
                source.name,
                design_path=design_path,
                strict_design=strict_design,
                target=target,
                patch_name=Path(patch_path).name if patch_path is not None else None,
                registry_name=None
                if registry is None or isinstance(registry, ReactComponentRegistry)
                else Path(registry).name,
            ),
            artifact_path=artifact_path,
            emitter=emitter,
            target=MCP_APP_TARGET if target == MCP_APP_TARGET else None,
            source_format=prepared.source_format,
            canonical_source_hash=prepared.manifest_source_hash,
            registry=component_registry,
        )
        paths.update(extra_paths)
        manifest_summary = summarize_intent_manifest(Path(paths["manifest"]))
        metadata = {
            "cwd": str(root),
            "allow_outside_cwd": allow_outside_cwd,
            "sdk_version": __version__,
            "network_calls": "none",
            "target": target,
            "emitter": emitter,
            "manifest_summary": manifest_summary,
        }
        if component_registry is not None:
            metadata["registry"] = component_registry.to_summary()
        checked = check_artifact_dir(output)
        if not checked["ok"]:
            return tool_error_response(
                "CHECK_FAILED",
                "Compiled IntentBundle artifact failed viewspec check.",
                "Fix the reported artifact issue and retry compile.",
                paths=paths,
                errors=[
                    check_issue_entry(
                        item, default_fix="Re-run viewspec compile after fixing the reported artifact issue."
                    )
                    for item in checked["errors"]
                ],
                metadata=metadata,
            )
        if target == MCP_APP_TARGET:
            from viewspec.mcp_apps import default_mcp_app_uri, write_mcp_app_metadata

            paths["mcp_app"] = str(write_mcp_app_metadata(output, uri or default_mcp_app_uri(bundle)))
        return tool_response(
            True,
            "Compiled and checked IntentBundle artifact.",
            paths=paths,
            next_actions=[
                "Review ViewSpecView.tsx and provenance_manifest.json."
                if target in {"react-tsx", "react-tailwind-tsx"}
                else "Review dist/index.html and provenance_manifest.json."
            ],
            metadata={**metadata, "artifact_check": "passed"},
            data={"fixes": validation["fixes"]},
        )
    except ReactRegistryError as exc:
        return tool_error_response(
            exc.code, exc.message, exc.fix, metadata=path_policy_metadata(root, allow_outside_cwd)
        )
    except Exception as exc:
        return exception_response(
            exc,
            "COMPILE_FAILED",
            "Fix the IntentBundle, DESIGN.md, or path issue and retry.",
            metadata=path_policy_metadata(root, allow_outside_cwd),
        )


def _load_component_registry(
    registry: str | Path | ReactComponentRegistry | None,
    *,
    target: str,
    cwd: Path,
    allow_outside_cwd: bool,
) -> ReactComponentRegistry | None:
    if registry is None:
        return None
    if target not in REACT_REGISTRY_TARGETS:
        raise ReactRegistryError(
            "REGISTRY_TARGET_UNSUPPORTED",
            f"A component registry applies to target react-tsx or react-tailwind-tsx, not {target}.",
            "Drop the registry, or compile with --target react-tsx or --target react-tailwind-tsx.",
        )
    if isinstance(registry, ReactComponentRegistry):
        return registry
    path = resolve_local_path(registry, cwd=cwd, allow_outside_cwd=allow_outside_cwd, must_exist=True)
    return load_react_component_registry(path)


def wrap_intent_bundle_manifest(
    manifest_path: Path,
    *,
    source_name: str | None,
    raw_source_hash: str,
    design: DesignSystemContext | None,
    command_args: list[str],
    artifact_path: Path | None = None,
    emitter: str | None = None,
    target: str | None = None,
    source_format: str = CANONICAL_FORMAT,
    canonical_source_hash: str | None = None,
    registry: ReactComponentRegistry | None = None,
) -> None:
    """Wrap the emitter manifest in the IntentBundle provenance envelope.

    ``raw_source_hash`` is the sha256 of the source text that was compiled: as written, or after the
    patch. For a canonical source it is also ``source_hash``. A compact source records, as
    ``source_hash``, the sha256 of its canonical document after the lossless fixes (exactly the bytes
    ``--canonical-out`` writes), plus ``source_format`` and ``compact_source_sha256``. A canonical
    manifest carries neither new key, so its bytes do not change.

    ``registry`` adds the ``registry`` section (``sha256``, ``module``, ``primitive_count``,
    ``primitives``) for a React artifact rendered through a host component registry; without it the
    manifest carries no such key.
    """
    from viewspec.local_tools import atomic_write

    existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = artifact_path or manifest_path.with_name("index.html")
    artifact_hash = file_hash(artifact) if artifact.exists() else None
    source_fields: dict[str, Any] = {}
    if source_format == COMPACT_FORMAT:
        source_fields = {"source_format": COMPACT_FORMAT, "compact_source_sha256": raw_source_hash}
    wrapped: dict[str, Any] = {
        "version": 1,
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "kind": "intent_bundle_compile",
        "sdk_version": __version__,
        "source_name": source_name,
        "raw_source_hash": raw_source_hash,
        "source_hash": canonical_source_hash or raw_source_hash,
        **source_fields,
        "design_hash": design.design_hash if design else None,
        "artifact_hash": artifact_hash,
        "command": "compile",
        "command_args": command_args,
        "policy_version": BUNDLE_POLICY_VERSION,
        "guarantees": {
            "sdk_network_calls": "none",
            "artifact_autofetch_network": "none",
            "network_calls": "none",
            "decompilation": "not_applicable",
        },
        "nodes": existing,
        "diagnostics": json.loads(manifest_path.with_name("diagnostics.json").read_text(encoding="utf-8")),
        "external_refs": [],
    }
    if emitter is not None:
        wrapped["emitter"] = emitter
        wrapped["artifact_file"] = artifact.name
    if target is not None:
        wrapped["target"] = target
    if emitter == "react_tailwind_tsx":
        wrapped["tailwind_recipe_inventory"] = _tailwind_recipe_inventory(existing)
    if registry is not None:
        wrapped["registry"] = registry.to_manifest()
    if design is not None:
        wrapped["design"] = design.to_meta()
    wrapped["semantic_digest"] = build_intent_semantic_digest(
        wrapped,
        artifact.read_text(encoding="utf-8") if artifact.exists() else "",
        emitter=emitter or "html_tailwind",
        diagnostics=wrapped["diagnostics"],
    )
    atomic_write(manifest_path, json.dumps(wrapped, indent=2))


def _tailwind_recipe_inventory(nodes: dict[str, Any]) -> dict[str, Any]:
    recipe_keys: set[str] = set()
    class_tokens: set[str] = set()
    app_roles: set[str] = set()
    app_role_sources: set[str] = set()
    aesthetic_profile: str | None = None
    for entry in nodes.values():
        if not isinstance(entry, dict):
            continue
        props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
        if entry.get("primitive") == "root" and isinstance(props.get("aesthetic_profile"), str):
            aesthetic_profile = props["aesthetic_profile"]
        recipe_key = entry.get("recipe_key")
        if isinstance(recipe_key, str) and recipe_key:
            recipe_keys.add(recipe_key)
        app_role = entry.get("app_role")
        if isinstance(app_role, str) and app_role:
            app_roles.add(app_role)
        app_role_source = entry.get("app_role_source")
        if isinstance(app_role_source, str) and app_role_source:
            app_role_sources.add(app_role_source)
        classes = entry.get("classes")
        if isinstance(classes, list):
            class_tokens.update(item for item in classes if isinstance(item, str) and item)
    return {
        "recipe_pack": "tailwind_app_v1",
        "registry_version": TAILWIND_RECIPE_REGISTRY_VERSION,
        "recipe_registry_digest": tailwind_recipe_registry_digest(),
        "aesthetic_profile": aesthetic_profile,
        "recipe_count": len(recipe_keys),
        "recipes": sorted(recipe_keys),
        "class_count": len(class_tokens),
        "class_tokens": sorted(class_tokens),
        "app_roles": sorted(app_roles),
        "app_role_sources": sorted(app_role_sources),
    }


def _compile_command_args(
    source_name: str,
    *,
    design_path: str | Path | None,
    strict_design: bool,
    target: str = "html-tailwind",
    patch_name: str | None = None,
    registry_name: str | None = None,
) -> list[str]:
    # --patch changes the compiled source and --registry changes the emitted bytes, so both are
    # recorded; --no-fix only changes validation and never the compiled bytes, so it is not, and
    # AppBundle screen manifests stay byte-identical. The CLI records them in the same order.
    command = ["viewspec", "compile", source_name]
    if design_path is not None:
        command.extend(["--design", Path(design_path).name])
    if strict_design:
        command.append("--strict-design")
    if target != "html-tailwind":
        command.extend(["--target", target])
    if patch_name is not None:
        command.extend(["--patch", patch_name])
    if registry_name is not None:
        command.extend(["--registry", registry_name])
    command.extend(["--out", "<out>"])
    return command


def _load_optional_design(
    design_path: str | Path | None,
    *,
    cwd: Path,
    allow_outside_cwd: bool,
    strict: bool,
) -> DesignSystemContext | None:
    if design_path is None:
        return None
    resolved = resolve_local_path(design_path, cwd=cwd, allow_outside_cwd=allow_outside_cwd, must_exist=True)
    return load_design_system(path=resolved, strict=strict)


# UTF-8 canonical form, shared through viewspec._canonical (see its docstring).
_stable_json = canonical_json_utf8


__all__ = [
    "BUNDLE_POLICY_VERSION",
    "CANONICAL_FORMAT",
    "INTENT_RESULT_SCHEMA_VERSION",
    "MAX_INTENT_PATCH_BYTES",
    "MAX_INTENT_PATCH_OPERATIONS",
    "STARTER_INTENT_KINDS",
    "IntentPatchError",
    "IntentSourcePreparation",
    "apply_intent_patch",
    "check_intent_source",
    "compile_intent_bundle_file_tool",
    "fixed_intent_source_text",
    "init_compact_file",
    "init_intent_file",
    "intent_error_payload",
    "intent_validation_payload",
    "load_intent_patch",
    "patch_target_problem",
    "prepare_intent_source",
    "reject_compact_fixed_out",
    "starter_compact_payload",
    "starter_intent_bundle",
    "summarize_intent_manifest",
    "validate_intent_file",
    "validate_intent_text",
    "wrap_intent_bundle_manifest",
]
