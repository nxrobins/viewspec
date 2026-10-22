from __future__ import annotations

import json
import re
from collections.abc import Iterator
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlparse

from viewspec._canonical import canonical_json_ascii
from viewspec.aesthetics import (
    AESTHETIC_PROFILE_LAYOUT_ROLES,
    AESTHETIC_PROFILE_TOKENS,
    is_aesthetic_profile_token,
    profile_layout_props,
)
from viewspec.compiler_errors import LocalToolContractError
from viewspec.emitters.html_tailwind import ACTION_EVENT_SCRIPT
from viewspec.emitters.react_tailwind_tsx import (
    GRID_CLASS_BY_COLUMNS,
    LAYOUT_EMPHASIS_CLASS_BY_VALUE,
    RECIPE_BY_KEY,
    TAILWIND_AESTHETIC_RECIPE_OVERLAYS,
    TAILWIND_APP_V1_APP_ROLE_CONTRACTS,
    TAILWIND_MAX_ACTIONS,
    TAILWIND_MAX_ARTIFACT_BYTES,
    TAILWIND_MAX_CLASS_TOKENS,
    TAILWIND_MAX_IR_NODES,
    TAILWIND_MAX_RECIPES,
    TAILWIND_PARITY_CLASS_TOKENS,
    TAILWIND_RECIPE_PACK,
    TAILWIND_RECIPE_REGISTRY_VERSION,
    CompilerConstraintError,
    resolve_manifest_recipe_metadata,
    tailwind_recipe_registry_digest,
)
from viewspec.emitters.react_tsx import react_tsx_manifest_node_markers
from viewspec.error_codes import ERROR_CODES
from viewspec.local_tools_constants import (
    ACTION_TARGET_REF_RE,
    ACTIVE_OR_AUTOFETCH_TAGS,
    ACTIVE_STRUCTURAL_TAGS,
    CANONICAL_CONTENT_REF_RE,
    DIAGNOSTIC_SEVERITIES,
    EMITTER_ARTIFACT_FILES,
    EXPECTED_MANIFEST_ENVELOPES,
    EXTERNAL_REF_POLICIES,
    HASH_RE,
    KNOWN_EMITTERS,
    MAX_SCANNED_TAG_BLOCKS,
    REACT_TSX_ACTION_REQUIRED_MARKERS,
    REACT_TSX_FORBIDDEN_SURFACES,
    REACT_TSX_REQUIRED_MARKERS,
    REACT_TSX_REQUIRED_MARKERS_BY_EMITTER,
    REMOTE_AUTOFETCH_ATTRS,
    REMOTE_HREF_AUTOFETCH_TAGS,
    SAFE_ID_RE,
    SEMANTIC_ACTION_KEYS,
    SEMANTIC_DIGEST_KEYS,
    SEMANTIC_DIGEST_MAX_PROJECTION_BYTES,
    SEMANTIC_DIGEST_VERSION,
    SEMANTIC_NODE_KEYS,
    SEMANTIC_PROJECTION_KEYS,
    STATEFUL_COLLECTION_ACTION_KINDS,
    TEXT_PROP_PRIMITIVES,
    UNSAFE_URL_SCHEMES,
    URL_SCHEME_POLICY_ATTRS,
    VIEWSPEC_INTENT_REF_RE,
    VOID_HTML_TAGS,
)
from viewspec.local_tools_hash import bytes_hash, file_hash
from viewspec.local_tools_io import looks_absolute_path_arg
from viewspec.manifest_summary import manifest_root_aesthetic_profile, summarize_intent_manifest
from viewspec.raw_html import ALLOWED_TAGS as RAW_HTML_ALLOWED_TAGS
from viewspec.raw_html import (
    DATA_IMAGE_SRC_PREFIXES,
    MANIFEST_SCHEMA_VERSION,
    MAX_HTML_INPUT_BYTES,
    collapse_url_obfuscation,
)
from viewspec.raw_html import SAFE_ATTRS_BY_TAG as RAW_HTML_SAFE_ATTRS_BY_TAG
from viewspec.raw_html import SAFE_GLOBAL_ATTRS as RAW_HTML_SAFE_GLOBAL_ATTRS
from viewspec.raw_html import URL_ATTRS as RAW_HTML_URL_ATTRS
from viewspec.raw_html import _is_external_http_url as raw_html_is_external_http_url
from viewspec.raw_html import _normalize_url_for_policy as raw_html_normalize_url
from viewspec.raw_html import _safe_url as raw_html_safe_url


def check_artifact_dir(path: str | Path) -> dict[str, Any]:
    artifact_path = Path(path)
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = artifact_path / "provenance_manifest.json"
    html_path = artifact_path / "index.html"
    diagnostics_path = artifact_path / "diagnostics.json"
    manifest: dict[str, Any] = {}

    if not manifest_path.exists():
        errors.append("missing provenance_manifest.json")
    else:
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"invalid provenance_manifest.json: {exc}")
        else:
            if isinstance(loaded, dict):
                manifest = loaded
            else:
                errors.append("provenance_manifest.json must be an object")

    required = {
        "version",
        "manifest_schema_version",
        "kind",
        "sdk_version",
        "source_name",
        "raw_source_hash",
        "source_hash",
        "design_hash",
        "artifact_hash",
        "command",
        "command_args",
        "policy_version",
        "guarantees",
        "nodes",
        "diagnostics",
        "external_refs",
    }
    for key in sorted(required - set(manifest)):
        errors.append(f"manifest missing {key}")
    if manifest.get("version") != 1:
        errors.append("manifest version must be 1")
    if manifest.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append(f"manifest_schema_version must be {MANIFEST_SCHEMA_VERSION}")
    _validate_manifest_envelope(manifest, errors)
    for key in ("raw_source_hash", "source_hash", "artifact_hash"):
        value = manifest.get(key)
        if value is not None and (not isinstance(value, str) or not HASH_RE.match(value)):
            errors.append(f"manifest {key} must be a sha256 hex string")
    design_hash = manifest.get("design_hash")
    if design_hash is not None and (not isinstance(design_hash, str) or not HASH_RE.match(design_hash)):
        errors.append("manifest design_hash must be null or a sha256 hex string")
    if not isinstance(manifest.get("command_args"), list) or not all(
        isinstance(item, str) for item in manifest.get("command_args", [])
    ):
        errors.append("manifest command_args must be a list of strings")
    else:
        absolute_args = [item for item in manifest["command_args"] if looks_absolute_path_arg(item)]
        if absolute_args:
            errors.append(f"manifest command_args must not contain absolute paths: {absolute_args}")

    guarantees = manifest.get("guarantees")
    if isinstance(guarantees, dict):
        for key in ("sdk_network_calls", "artifact_autofetch_network", "network_calls"):
            if guarantees.get(key) != "none":
                errors.append(f"manifest guarantees.{key} must be 'none'")
    else:
        errors.append("manifest guarantees must be an object")

    _validate_manifest_diagnostics(manifest.get("diagnostics"), errors)
    _validate_manifest_external_refs(manifest.get("external_refs"), errors)
    if "design" in manifest:
        _validate_manifest_design(manifest.get("design"), manifest.get("design_hash"), errors)
    _validate_manifest_nodes(manifest, errors)
    artifact_file = _validate_manifest_artifact_file(manifest, errors)

    emitter = manifest.get("emitter")
    if artifact_file == "ViewSpecView.tsx":
        if html_path.exists():
            errors.append(f"{emitter or 'react_tsx'} artifact directory must not contain index.html")
        react_path = artifact_path / artifact_file
        if react_path.exists():
            tsx = react_path.read_text(encoding="utf-8")
            artifact_hash = file_hash(react_path)
            if manifest.get("artifact_hash") and manifest.get("artifact_hash") != artifact_hash:
                errors.append("artifact_hash does not match ViewSpecView.tsx")
            errors.extend(
                _validate_react_tsx_source(
                    tsx,
                    has_action_nodes=_manifest_has_action_nodes(manifest),
                    emitter=emitter if isinstance(emitter, str) else "react_tsx",
                )
            )
            errors.extend(_validate_react_tsx_manifest_links(tsx, manifest))
            errors.extend(
                _validate_stateful_collection_artifact(
                    tsx, manifest, emitter=emitter if isinstance(emitter, str) else "react_tsx"
                )
            )
            errors.extend(
                _validate_intent_semantic_digest(
                    tsx,
                    manifest,
                    emitter=emitter if isinstance(emitter, str) else "react_tsx",
                    diagnostics_path=diagnostics_path,
                )
            )
            if emitter == "react_tailwind_tsx":
                errors.extend(_validate_react_tailwind_tsx_artifact(tsx, manifest, react_path))
            else:
                errors.extend(_validate_no_tailwind_scope_leak(manifest))
        else:
            errors.append("missing ViewSpecView.tsx")
    elif html_path.exists() and html_path.stat().st_size > MAX_HTML_INPUT_BYTES:
        errors.append(f"index.html is larger than the {MAX_HTML_INPUT_BYTES} byte check limit")
    elif html_path.exists():
        html = html_path.read_text(encoding="utf-8")
        artifact_hash = file_hash(html_path)
        if manifest.get("artifact_hash") and manifest.get("artifact_hash") != artifact_hash:
            errors.append("artifact_hash does not match index.html")
        errors.extend(_validate_no_autofetch_surfaces(html))
        # Linear block scan (S-3): one pass, never re-scanned from every unclosed opener. A
        # `<script` opener with no closer is rejected outright instead of being ignored.
        script_scan = find_tag_blocks(html, "script")
        scripts = [html[start:end] for start, _, _, end in script_scan.blocks]
        if script_scan.unclosed_content_start is not None:
            errors.append(_UNCLOSED_SCRIPT_ERROR)
        if script_scan.truncated and _SCAN_LIMIT_ERROR not in errors:
            errors.append(_SCAN_LIMIT_ERROR)
        if manifest.get("kind") == "raw_html_compile" and scripts:
            errors.append("index.html contains an active raw-HTML surface")
        elif scripts and [script.strip() for script in scripts] != [ACTION_EVENT_SCRIPT]:
            errors.append("index.html contains an unknown inline script")
        elif scripts and not _manifest_has_action_nodes(manifest):
            errors.append("index.html contains an action runtime script without action nodes")
        if manifest.get("kind") == "intent_bundle_compile":
            errors.extend(_validate_no_tailwind_scope_leak(manifest))
        errors.extend(_validate_manifest_dom_links(manifest, html))
        errors.extend(
            _validate_stateful_collection_artifact(
                html, manifest, emitter=emitter if isinstance(emitter, str) else "html_tailwind"
            )
        )
        errors.extend(
            _validate_intent_semantic_digest(
                html,
                manifest,
                emitter=emitter if isinstance(emitter, str) else "html_tailwind",
                diagnostics_path=diagnostics_path,
            )
        )
    else:
        errors.append("missing index.html")

    if diagnostics_path.exists():
        try:
            diagnostics_file = json.loads(diagnostics_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"invalid diagnostics.json: {exc}")
        else:
            if manifest.get("diagnostics") != diagnostics_file:
                warnings.append("diagnostics.json differs from manifest diagnostics")
    else:
        errors.append("missing diagnostics.json")

    return {
        "ok": not errors,
        "errors": errors,
        "issues": [check_issue_entry(item) for item in errors],
        "warnings": warnings,
        "manifest_summary": summarize_intent_manifest(manifest_path),
    }


DEFAULT_CHECK_FIX = "Re-run viewspec compile from the source, or fix the tampered artifact."

# Fixes for the rejection classes `check` raises with a code prefix. Any other error string is
# surfaced under the generic CHECK_FAILED code with the caller's default fix.
_CHECK_ISSUE_FIXES = {
    "CHECK_INLINE_EVENT_HANDLER": "Remove every on* attribute from index.html, or re-run viewspec compile from the source.",
    "CHECK_UNSAFE_URL_SCHEME": (
        "Replace javascript:, vbscript:, and data: URLs (other than raster image data URLs on <img src>) "
        "with safe links, or re-run viewspec compile from the source."
    ),
    "CHECK_UNCLOSED_SCRIPT": "Remove the unclosed <script tag from index.html, or re-run viewspec compile from the source.",
    "CHECK_SCAN_LIMIT_EXCEEDED": (
        "Reduce index.html to the compiler-emitted style and script blocks, or re-run viewspec compile from the source."
    ),
    "CHECK_RAW_HTML_DOM_UNGOVERNED": (
        "Re-run viewspec compile from the source HTML; a raw-HTML artifact may only contain the document "
        "wrapper plus manifest-governed elements whose attributes match the manifest and the sanitizer allowlist."
    ),
}

_CHECK_ISSUE_PATH_HINTS = (
    ("index.html", "index.html"),
    ("DOM", "index.html"),
    ("ViewSpecView.tsx", "ViewSpecView.tsx"),
    ("diagnostics.json", "diagnostics.json"),
)


def check_issue_entry(message: str, *, default_fix: str = DEFAULT_CHECK_FIX) -> dict[str, str]:
    """Shape one `check_artifact_dir` error string as {code, path, message, fix}.

    Error strings that start with a registered code (``CODE: detail``) surface under that code;
    everything else is the generic CHECK_FAILED. The path names the artifact file the message is about.
    """
    code = "CHECK_FAILED"
    text = message
    prefix, separator, remainder = message.partition(": ")
    if separator and remainder and prefix in ERROR_CODES:
        code = prefix
        text = remainder
    path = "provenance_manifest.json"
    for needle, candidate in _CHECK_ISSUE_PATH_HINTS:
        if needle in message:
            path = candidate
            break
    return {"code": code, "path": path, "message": text, "fix": _CHECK_ISSUE_FIXES.get(code, default_fix)}


def build_intent_semantic_digest(
    manifest: dict[str, Any],
    artifact_text: str,
    *,
    emitter: str,
    diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the versioned semantic digest for a wrapped IntentBundle artifact."""
    manifest_projection = _semantic_manifest_projection(manifest, emitter=emitter)
    source_projection = _semantic_source_projection(
        artifact_text,
        emitter=emitter,
        diagnostics=diagnostics if diagnostics is not None else manifest.get("diagnostics", []),
        manifest_nodes=manifest.get("nodes"),
    )
    _assert_semantic_projection_size(manifest_projection, "manifest_projection")
    _assert_semantic_projection_size(source_projection, "source_projection")
    digest_payload = {
        "version": SEMANTIC_DIGEST_VERSION,
        "manifest_projection": manifest_projection,
        "source_projection": source_projection,
    }
    return {
        **digest_payload,
        "digest": bytes_hash(_stable_semantic_json(digest_payload).encode("utf-8")),
    }


def _validate_manifest_envelope(manifest: dict[str, Any], errors: list[str]) -> None:
    kind = manifest.get("kind")
    if not isinstance(kind, str):
        errors.append("manifest kind must be a string")
        return
    expected = EXPECTED_MANIFEST_ENVELOPES.get(kind)
    if expected is None:
        allowed = ", ".join(sorted(EXPECTED_MANIFEST_ENVELOPES))
        errors.append(f"manifest kind must be one of: {allowed}")
        return
    for field in ("command", "policy_version"):
        if manifest.get(field) != expected[field]:
            errors.append(f"manifest {field} must be {expected[field]} for {kind}")
    guarantees = manifest.get("guarantees")
    if isinstance(guarantees, dict) and guarantees.get("decompilation") != expected["decompilation"]:
        errors.append(f"manifest guarantees.decompilation must be {expected['decompilation']} for {kind}")


def _validate_manifest_artifact_file(manifest: dict[str, Any], errors: list[str]) -> str:
    kind = manifest.get("kind")
    emitter = manifest.get("emitter")
    artifact_file = manifest.get("artifact_file")

    if emitter is not None:
        if not isinstance(emitter, str):
            errors.append("manifest emitter must be a string")
            emitter = None
        elif emitter not in KNOWN_EMITTERS:
            allowed = ", ".join(sorted(KNOWN_EMITTERS))
            errors.append(f"manifest emitter must be one of: {allowed}")
            emitter = None
    if artifact_file is not None:
        if not isinstance(artifact_file, str) or not artifact_file:
            errors.append("manifest artifact_file must be a non-empty string")
            artifact_file = None
        elif artifact_file in {".", ".."} or "/" in artifact_file or "\\" in artifact_file or ":" in artifact_file:
            errors.append("manifest artifact_file must be a simple file name")
            artifact_file = None

    if kind == "raw_html_compile":
        if emitter is not None:
            errors.append("raw_html_compile manifest must not declare an emitter")
        if artifact_file is not None and artifact_file != "index.html":
            errors.append("manifest artifact_file must be index.html for raw_html_compile")
        return "index.html"

    if kind == "intent_bundle_compile":
        resolved_emitter = emitter or "html_tailwind"
        expected = EMITTER_ARTIFACT_FILES.get(resolved_emitter, "index.html")
        if artifact_file is None and resolved_emitter in {"react_tailwind_tsx", "react_tsx"}:
            errors.append(f"manifest artifact_file must be ViewSpecView.tsx for {resolved_emitter} emitter")
        elif artifact_file is not None and artifact_file != expected:
            errors.append(f"manifest artifact_file must be {expected} for {resolved_emitter} emitter")
        return expected

    return "index.html"


def _validate_react_tsx_source(
    tsx: str,
    *,
    has_action_nodes: bool = False,
    emitter: str = "react_tsx",
) -> list[str]:
    errors: list[str] = []
    required_markers = REACT_TSX_REQUIRED_MARKERS_BY_EMITTER.get(emitter, REACT_TSX_REQUIRED_MARKERS)
    for marker, message in required_markers.items():
        if marker not in tsx:
            errors.append(message)
    if has_action_nodes:
        for marker, message in REACT_TSX_ACTION_REQUIRED_MARKERS.items():
            if marker not in tsx:
                errors.append(message)
    code = _strip_tsx_literals_and_comments(tsx)
    for pattern, message in REACT_TSX_FORBIDDEN_SURFACES:
        if pattern.search(code):
            if emitter == "react_tailwind_tsx":
                errors.append(f"TAILWIND_ACTIVE_SURFACE_FORBIDDEN: {message}")
            else:
                errors.append(message)
    return errors


def _validate_react_tsx_manifest_links(tsx: str, manifest: dict[str, Any]) -> list[str]:
    nodes = manifest.get("nodes")
    if not isinstance(nodes, dict):
        return []
    errors: list[str] = []
    for dom_id, entry in sorted(nodes.items()):
        if not isinstance(dom_id, str) or not isinstance(entry, dict):
            continue
        for name, marker in react_tsx_manifest_node_markers(dom_id, entry).items():
            if marker not in tsx:
                errors.append(f"ViewSpecView.tsx missing {name} for manifest node {dom_id}")
    return errors


def _validate_react_tailwind_tsx_artifact(tsx: str, manifest: dict[str, Any], artifact_path: Path) -> list[str]:
    errors: list[str] = []
    nodes = manifest.get("nodes")
    if not isinstance(nodes, dict):
        return errors
    errors.extend(_validate_react_tailwind_static_source(tsx, artifact_path))
    errors.extend(_validate_react_tailwind_manifest(tsx, nodes, manifest))
    errors.extend(_validate_react_tailwind_class_inventory(tsx, nodes))
    errors.extend(_validate_react_tailwind_semantic_markers(tsx, nodes))
    errors.extend(_validate_react_tailwind_limits(tsx, nodes))
    return errors


def _validate_react_tailwind_static_source(tsx: str, artifact_path: Path) -> list[str]:
    errors: list[str] = []
    code = _strip_tsx_literals_and_comments(tsx)
    if re.search(r"\bclassName\s*=\s*{", code):
        errors.append("TAILWIND_DYNAMIC_CLASS: ViewSpecView.tsx contains computed className")
    if re.search(r"\bstyle\s*=", code):
        errors.append("TAILWIND_INLINE_STYLE_FORBIDDEN: ViewSpecView.tsx contains a style prop")
    if "`" in code or re.search(r"\.join\s*\(|\bclsx\s*\(|\bclassnames\s*\(", code):
        errors.append("TAILWIND_DYNAMIC_CLASS: ViewSpecView.tsx contains runtime class construction")
    imports = re.findall(r"^import\s+.+;$", tsx, flags=re.MULTILINE)
    if imports != ['import * as React from "react";']:
        errors.append("TAILWIND_TSX_INVALID: ViewSpecView.tsx imports only React and approved local types")
    if artifact_path.stat().st_size > TAILWIND_MAX_ARTIFACT_BYTES:
        errors.append("TAILWIND_LIMIT_EXCEEDED_ARTIFACT_BYTES: ViewSpecView.tsx exceeds 256 KiB")
    return errors


def _validate_react_tailwind_manifest(tsx: str, nodes: dict[str, Any], manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    allowed_app_roles = set(TAILWIND_APP_V1_APP_ROLE_CONTRACTS)
    allowed_class_tokens = _tailwind_allowed_class_tokens()
    parent_by_dom_id = _tsx_semantic_parent_map(tsx)
    aesthetic_profile = manifest_root_aesthetic_profile(nodes)
    if aesthetic_profile not in (None, *AESTHETIC_PROFILE_TOKENS):
        errors.append("AESTHETIC_PROFILE_UNKNOWN: manifest root declares an unknown aesthetic profile")
        aesthetic_profile = None
    recipe_keys: set[str] = set()
    class_tokens: set[str] = set()
    app_roles: set[str] = set()
    app_role_sources: set[str] = set()
    for dom_id, entry in sorted(nodes.items()):
        if not isinstance(entry, dict):
            continue
        props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
        if any(key in props for key in ("app_role", "app_role_source", "recipe_key", "recipe_pack")):
            errors.append(f"APP_ROLE_LEXICAL_SOURCE: manifest node {dom_id} props contain Tailwind app-role metadata")
        parent_entry = None
        parent_dom_id = parent_by_dom_id.get(dom_id)
        if parent_dom_id is not None and isinstance(nodes.get(parent_dom_id), dict):
            parent_entry = nodes[parent_dom_id]
        try:
            expected = resolve_manifest_recipe_metadata(entry, parent_entry, aesthetic_profile=aesthetic_profile)
        except CompilerConstraintError as exc:
            errors.append(str(exc))
            expected = {
                "app_role": None,
                "app_role_source": None,
                "recipe_pack": TAILWIND_RECIPE_PACK,
                "recipe_key": None,
                "classes": [],
            }
        if entry.get("recipe_pack") != expected["recipe_pack"]:
            errors.append(
                f"APP_ROLE_DERIVATION_MISMATCH: manifest node {dom_id} recipe_pack does not match recomputed Tailwind recipe"
            )
        recipe_key = entry.get("recipe_key")
        if not isinstance(recipe_key, str) or recipe_key not in RECIPE_BY_KEY:
            errors.append(f"TAILWIND_RECIPE_CONFLICT: manifest node {dom_id} has an unknown recipe_key")
        elif recipe_key != expected["recipe_key"]:
            errors.append(
                f"APP_ROLE_DERIVATION_MISMATCH: manifest node {dom_id} recipe_key does not match recomputed Tailwind recipe"
            )
        else:
            recipe_keys.add(recipe_key)
        app_role = entry.get("app_role")
        app_role_source = entry.get("app_role_source")
        if app_role is not None and (not isinstance(app_role, str) or app_role not in allowed_app_roles):
            errors.append(f"APP_ROLE_UNDECLARED_CONTRACT: manifest node {dom_id} has an undeclared app_role")
        if expected["app_role"] is None:
            if app_role is not None or app_role_source is not None:
                errors.append(
                    f"APP_ROLE_UNDECLARED_SIGNAL: manifest node {dom_id} declares app-role metadata without a structural rule"
                )
        else:
            if app_role != expected["app_role"] or app_role_source != expected["app_role_source"]:
                errors.append(
                    f"APP_ROLE_DERIVATION_MISMATCH: manifest node {dom_id} app_role/app_role_source does not match recomputed rule"
                )
            if isinstance(app_role, str):
                app_roles.add(app_role)
            if isinstance(app_role_source, str):
                app_role_sources.add(app_role_source)
        classes = entry.get("classes")
        if not _is_string_list(classes):
            errors.append(f"TAILWIND_INVENTORY_MISMATCH: manifest node {dom_id}.classes must be a list of strings")
            continue
        if classes != expected["classes"]:
            errors.append(f"TAILWIND_INVENTORY_MISMATCH: manifest node {dom_id}.classes do not match recomputed recipe")
        for class_name in classes:
            if class_name not in allowed_class_tokens:
                errors.append(
                    f"TAILWIND_UNSAFE_CLASS_SOURCE: manifest node {dom_id} class {class_name} is not registry-owned"
                )
            class_tokens.add(class_name)
    inventory = manifest.get("tailwind_recipe_inventory")
    if not isinstance(inventory, dict):
        errors.append("TAILWIND_INVENTORY_MISMATCH: manifest missing tailwind_recipe_inventory")
    else:
        expected_inventory = {
            "recipe_pack": TAILWIND_RECIPE_PACK,
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
        extra_recipes = (
            sorted(set(inventory.get("recipes", [])) - recipe_keys)
            if isinstance(inventory.get("recipes"), list)
            else []
        )
        extra_rule_ids = (
            sorted(set(inventory.get("app_role_sources", [])) - app_role_sources)
            if isinstance(inventory.get("app_role_sources"), list)
            else []
        )
        if extra_recipes or extra_rule_ids:
            errors.append(
                "TAILWIND_RECIPE_UNREACHABLE: tailwind_recipe_inventory declares recipe/rule entries not reached by manifest nodes"
            )
        if inventory.get("recipe_registry_digest") != expected_inventory["recipe_registry_digest"]:
            errors.append(
                "TAILWIND_RECIPE_REGISTRY_DIGEST_MISMATCH: tailwind_recipe_inventory.recipe_registry_digest does not match registry"
            )
        for key, expected_value in expected_inventory.items():
            if inventory.get(key) != expected_value:
                errors.append(
                    f"TAILWIND_INVENTORY_MISMATCH: tailwind_recipe_inventory.{key} does not match manifest nodes"
                )
    if len(recipe_keys) > TAILWIND_MAX_RECIPES:
        errors.append("TAILWIND_LIMIT_EXCEEDED_RECIPES: react-tailwind-tsx artifact uses more than 96 recipes")
    if len(class_tokens) > TAILWIND_MAX_CLASS_TOKENS:
        errors.append(
            "TAILWIND_LIMIT_EXCEEDED_CLASS_TOKENS: react-tailwind-tsx artifact uses more than 512 class tokens"
        )
    errors.extend(_validate_tailwind_generic_fallback(nodes))
    return errors


def _validate_react_tailwind_class_inventory(tsx: str, nodes: dict[str, Any]) -> list[str]:
    source_tokens = _tailwind_source_class_tokens(tsx)
    manifest_tokens = {
        class_name
        for entry in nodes.values()
        if isinstance(entry, dict)
        for class_name in entry.get("classes", [])
        if isinstance(class_name, str)
    }
    if source_tokens != manifest_tokens:
        return ["TAILWIND_INVENTORY_MISMATCH: source class inventory does not match manifest class inventory"]
    return []


def _validate_react_tailwind_semantic_markers(tsx: str, nodes: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    manifest_actions: set[str] = set()
    source_actions = set(re.findall(r"data-action-id=\{\"([A-Za-z0-9_.-]+)\"\}", tsx))
    for dom_id, entry in sorted(nodes.items()):
        if not isinstance(entry, dict):
            continue
        props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
        action_id = props.get("action_id")
        if isinstance(action_id, str) and action_id:
            manifest_actions.add(action_id)
        primitive = entry.get("primitive")
        text = props.get("state_text_initial") if isinstance(props.get("state_text_id"), str) else props.get("text")
        if primitive in TEXT_PROP_PRIMITIVES and isinstance(text, str) and text:
            if _tsx_text_marker(text) not in tsx:
                errors.append(f"TAILWIND_SEMANTIC_DRIFT: ViewSpecView.tsx missing text for manifest node {dom_id}")
        elif primitive == "button":
            label = props.get("label")
            if isinstance(label, str) and label and _tsx_text_marker(label) not in tsx:
                errors.append(f"TAILWIND_SEMANTIC_DRIFT: ViewSpecView.tsx missing label for manifest node {dom_id}")
    if source_actions != manifest_actions:
        errors.append("TAILWIND_SEMANTIC_DRIFT: source action IDs do not match manifest action IDs")
    return errors


def _validate_react_tailwind_limits(tsx: str, nodes: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if len(nodes) > TAILWIND_MAX_IR_NODES:
        errors.append("TAILWIND_LIMIT_EXCEEDED_NODES: react-tailwind-tsx artifact exceeds 600 IR nodes")
    action_count = 0
    for entry in nodes.values():
        if not isinstance(entry, dict):
            continue
        props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
        if entry.get("primitive") == "button" and props.get("action_id"):
            action_count += 1
    if action_count > TAILWIND_MAX_ACTIONS:
        errors.append("TAILWIND_LIMIT_EXCEEDED_ACTIONS: react-tailwind-tsx artifact exceeds 128 actions")
    if len(tsx.encode("utf-8")) > TAILWIND_MAX_ARTIFACT_BYTES:
        errors.append("TAILWIND_LIMIT_EXCEEDED_ARTIFACT_BYTES: react-tailwind-tsx artifact exceeds 256 KiB")
    return errors


def _validate_no_tailwind_scope_leak(manifest: dict[str, Any]) -> list[str]:
    if manifest.get("emitter") == "react_tailwind_tsx":
        return []
    nodes = manifest.get("nodes")
    if not isinstance(nodes, dict):
        return []
    tailwind_keys = {"app_role", "recipe_key", "recipe_pack"}
    for entry in nodes.values():
        if isinstance(entry, dict) and any(key in entry for key in tailwind_keys):
            return [
                "TAILWIND_PLANNER_SCOPE_LEAK: Tailwind-only role or recipe metadata leaked into a non-Tailwind target"
            ]
    if "tailwind_recipe_inventory" in manifest:
        return ["TAILWIND_PLANNER_SCOPE_LEAK: Tailwind recipe inventory leaked into a non-Tailwind target"]
    return []


def _validate_tailwind_generic_fallback(nodes: dict[str, Any]) -> list[str]:
    role_bearing = []
    generic = []
    for entry in nodes.values():
        if not isinstance(entry, dict):
            continue
        props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
        if any(
            props.get(prop) is not None
            for prop in (
                "detail_role",
                "empty_state_role",
                "hero_role",
                "layout_role",
                "motif_kind",
                "product_role",
                "state_motif_role",
                "state_role",
                "table_cell_role",
            )
        ):
            role_bearing.append(entry)
            if entry.get("recipe_key", "").startswith("primitive:"):
                generic.append(entry)
    if role_bearing and (len(generic) / len(role_bearing)) > 0.25:
        return [
            "TAILWIND_GENERIC_FALLBACK_EXCEEDED: more than 25% of role-bearing nodes used generic Tailwind fallback"
        ]
    return []


def _tailwind_allowed_class_tokens() -> set[str]:
    tokens = set(TAILWIND_PARITY_CLASS_TOKENS)
    tokens.update(token for classes in RECIPE_BY_KEY.values() for token in classes.split())
    for overlay in TAILWIND_AESTHETIC_RECIPE_OVERLAYS.values():
        for classes in overlay.values():
            tokens.update(classes.split())
    for classes in GRID_CLASS_BY_COLUMNS.values():
        tokens.update(classes.split())
    for classes in LAYOUT_EMPHASIS_CLASS_BY_VALUE.values():
        tokens.update(classes.split())
    return tokens


def _tailwind_source_class_tokens(tsx: str) -> set[str]:
    tokens: set[str] = set()
    for value in re.findall(r'className="([^"]*)"', tsx):
        tokens.update(value.split())
    return tokens


def _tsx_text_marker(value: str) -> str:
    return (
        json.dumps(str(value), ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _strip_tsx_literals_and_comments(source: str) -> str:
    output: list[str] = []
    index = 0
    state: str | None = None
    escaped = False
    while index < len(source):
        char = source[index]
        peek = source[index : index + 2]
        if state in {"'", '"', "`"}:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == state:
                state = None
            output.append("\n" if char == "\n" else " ")
            index += 1
            continue
        if state == "//":
            if char == "\n":
                state = None
                output.append("\n")
            else:
                output.append(" ")
            index += 1
            continue
        if state == "/*":
            if peek == "*/":
                state = None
                output.append("  ")
                index += 2
            else:
                output.append("\n" if char == "\n" else " ")
                index += 1
            continue
        if peek in {"//", "/*"}:
            state = peek
            output.append("  ")
            index += 2
            continue
        if char in {"'", '"', "`"}:
            state = char
            output.append(" ")
            index += 1
            continue
        output.append(char)
        index += 1
    return "".join(output)


# ASCII-escaped canonical form, shared through viewspec._canonical (see its docstring).
_stable_semantic_json = canonical_json_ascii


def _assert_semantic_projection_size(projection: dict[str, Any], label: str) -> None:
    size = len(_stable_semantic_json(projection).encode("utf-8"))
    if size > SEMANTIC_DIGEST_MAX_PROJECTION_BYTES:
        raise LocalToolContractError("SEMANTIC_DIGEST_FIELD_FORBIDDEN", f"{label} exceeds 128 KiB")


def _diagnostic_codes(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(item["code"])
        for item in value
        if isinstance(item, dict) and isinstance(item.get("code"), str) and item.get("code")
    ]


def _semantic_manifest_projection(manifest: dict[str, Any], *, emitter: str) -> dict[str, Any]:
    nodes = manifest.get("nodes")
    semantic_nodes: list[dict[str, Any]] = []
    node_order: list[str] = []
    action_order: list[str] = []
    if isinstance(nodes, dict):
        for dom_id, entry in nodes.items():
            if not isinstance(dom_id, str) or not isinstance(entry, dict):
                continue
            semantic_node = _semantic_manifest_node(dom_id, entry, emitter=emitter)
            semantic_nodes.append(semantic_node)
            node_order.append(dom_id)
            action = semantic_node["action"]
            if isinstance(action, dict) and action.get("id"):
                action_order.append(str(action["id"]))
    return {
        "version": SEMANTIC_DIGEST_VERSION,
        "node_order": node_order,
        "action_order": action_order,
        "diagnostic_codes": _diagnostic_codes(manifest.get("diagnostics")),
        "nodes": semantic_nodes,
    }


def _semantic_projection_uses_stateful_collections(projection: Any) -> bool:
    if not isinstance(projection, dict):
        return False
    nodes = projection.get("nodes")
    if not isinstance(nodes, list):
        return False
    for node in nodes:
        if not isinstance(node, dict):
            continue
        action = node.get("action")
        if isinstance(action, dict) and action.get("kind") in STATEFUL_COLLECTION_ACTION_KINDS:
            return True
        if node.get("accessibility_label") in {"Loading state", "Error state"}:
            return True
    return False


def _semantic_manifest_node(dom_id: str, entry: dict[str, Any], *, emitter: str) -> dict[str, Any]:
    props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
    primitive = str(entry.get("primitive") or "")
    action = None
    action_id = props.get("action_id")
    if primitive == "button" or action_id:
        action = {
            "id": str(action_id or ""),
            "kind": str(props.get("action_kind") or ""),
            "target_ref": str(props.get("target_ref") or ""),
            "payload_bindings": [str(item) for item in props.get("payload_bindings", []) if isinstance(item, str)],
        }
    ir_id = str(entry.get("ir_id") or "")
    return {
        "dom_id": dom_id,
        "ir_id": ir_id,
        "primitive": primitive,
        "tag": _semantic_tag_for_manifest_node(entry),
        "visible_text": _normalize_visible_text(_semantic_visible_text_from_props(primitive, props, emitter=emitter)),
        "binding_id": props.get("binding_id") if isinstance(props.get("binding_id"), str) else None,
        "action": action,
        "accessibility_label": _semantic_accessibility_label(primitive, props, ir_id=ir_id),
    }


def _semantic_tag_for_manifest_node(entry: dict[str, Any]) -> str:
    primitive = entry.get("primitive")
    props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
    if primitive == "root":
        return "div" if props.get("semantic_context") == "embedded_screen" else "main"
    if props.get("motif_kind") == "table" and primitive == "stack":
        return "table"
    if props.get("motif_kind") == "table" and primitive == "cluster":
        return "tr"
    if props.get("motif_kind") == "detail" and primitive == "stack":
        return "dl"
    if props.get("detail_role") == "term":
        return "dt"
    if props.get("detail_role") == "description":
        return "dd"
    if props.get("motif_kind") == "empty_state" and primitive == "surface":
        return "section"
    if props.get("empty_state_role") == "title":
        return "h2"
    if props.get("empty_state_role") == "description":
        return "p"
    if props.get("motif_kind") in {"loading_state", "error_state"} and primitive == "surface":
        return "section"
    if props.get("state_motif_role") == "title":
        return "h2"
    if props.get("state_motif_role") == "description":
        return "p"
    if props.get("motif_kind") == "hero" and primitive == "surface":
        return "header"
    if props.get("hero_role") == "title":
        return "h1"
    if props.get("hero_role") in {"description", "eyebrow"}:
        return "p"
    if props.get("table_cell_role") == "row_header":
        return "th"
    if props.get("table_cell_role") == "cell":
        return "td"
    if props.get("motif_kind") == "list" and primitive == "stack":
        return "ul"
    if props.get("motif_kind") == "list" and primitive == "surface":
        return "li"
    if props.get("motif_kind") == "form" and primitive == "stack":
        return "section"
    if primitive == "rule":
        return "hr"
    if primitive == "button":
        return "button"
    if primitive == "input":
        return "input"
    return "div"


def _semantic_visible_text_from_props(primitive: str, props: dict[str, Any], *, emitter: str) -> str:
    if isinstance(props.get("state_text_id"), str):
        return str(props.get("state_text_initial") or "")
    if primitive == "image_slot":
        return str(props.get("alt", "image slot"))
    if primitive == "svg":
        return str(props.get("label", "vector slot"))
    if primitive == "button":
        return str(props.get("text", props.get("label", "Action")))
    if primitive == "error_boundary":
        code = str(props.get("diagnostic_code", "COMPILER_ERROR"))
        message = str(props.get("message", "Compiler diagnostic"))
        return f"{code}: {message}" if emitter in {"react_tsx", "react_tailwind_tsx"} else ""
    if primitive in TEXT_PROP_PRIMITIVES and "text" in props:
        return str(props["text"])
    return ""


def _semantic_accessibility_label(primitive: str, props: dict[str, Any], *, ir_id: str) -> str | None:
    if primitive == "input":
        return str(props.get("aria_label", props.get("binding_id", "input")))
    if primitive == "image_slot":
        return str(props.get("alt", "image slot"))
    if primitive == "svg":
        return str(props.get("label", "vector slot"))
    if props.get("motif_kind") == "form" and primitive == "stack":
        return str(props.get("label", ir_id))
    if props.get("motif_kind") == "empty_state" and primitive == "surface":
        return str(props.get("aria_label", "Empty state"))
    if props.get("motif_kind") == "loading_state" and primitive == "surface":
        return str(props.get("aria_label", "Loading state"))
    if props.get("motif_kind") == "error_state" and primitive == "surface":
        return str(props.get("aria_label", "Error state"))
    if props.get("motif_kind") == "hero" and primitive == "surface":
        return str(props.get("aria_label", "Hero"))
    return None


def _semantic_source_projection(
    artifact_text: str,
    *,
    emitter: str,
    diagnostics: Any,
    manifest_nodes: Any,
) -> dict[str, Any]:
    primitive_by_dom_id: dict[str, str] = {}
    if isinstance(manifest_nodes, dict):
        primitive_by_dom_id = {
            dom_id: str(entry.get("primitive") or "")
            for dom_id, entry in manifest_nodes.items()
            if isinstance(dom_id, str) and isinstance(entry, dict)
        }
    if emitter == "html_tailwind":
        nodes = _semantic_html_source_nodes(artifact_text, primitive_by_dom_id=primitive_by_dom_id)
    elif emitter in {"react_tsx", "react_tailwind_tsx"}:
        nodes = _semantic_tsx_source_nodes(artifact_text, primitive_by_dom_id=primitive_by_dom_id)
    else:
        nodes = []
    action_order = [
        str(node["action"]["id"]) for node in nodes if isinstance(node.get("action"), dict) and node["action"].get("id")
    ]
    return {
        "version": SEMANTIC_DIGEST_VERSION,
        "node_order": [str(node["dom_id"]) for node in nodes],
        "action_order": action_order,
        "diagnostic_codes": _diagnostic_codes(diagnostics),
        "nodes": nodes,
    }


def _semantic_html_source_nodes(html: str, *, primitive_by_dom_id: dict[str, str]) -> list[dict[str, Any]]:
    parser = _SemanticHtmlProjectionParser(primitive_by_dom_id)
    parser.feed(html)
    parser.close()
    return parser.semantic_nodes()


class _SemanticHtmlProjectionParser(HTMLParser):
    def __init__(self, primitive_by_dom_id: dict[str, str]) -> None:
        super().__init__(convert_charrefs=True)
        self.primitive_by_dom_id = primitive_by_dom_id
        self.nodes: list[dict[str, Any]] = []
        self.node_by_dom_id: dict[str, dict[str, Any]] = {}
        self.stack: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_tag(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_tag(tag, attrs, self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() not in VOID_HTML_TAGS and self.stack:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        if not self.stack:
            return
        dom_id = self.stack[-1]
        if dom_id is not None and data.strip():
            self.node_by_dom_id[dom_id].setdefault("_text_parts", []).append(data)

    def _handle_tag(self, tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool) -> None:
        attr_map = {name.lower(): value or "" for name, value in attrs}
        dom_id = attr_map.get("id")
        ir_id = attr_map.get("data-ir-id")
        semantic_dom_id = dom_id if dom_id and ir_id else None
        if semantic_dom_id is not None:
            node = {
                "dom_id": semantic_dom_id,
                "ir_id": ir_id,
                "primitive": self.primitive_by_dom_id.get(semantic_dom_id, ""),
                "tag": tag.lower(),
                "visible_text": "",
                "binding_id": attr_map.get("data-binding-id") or None,
                "action": _semantic_action_from_attrs(attr_map),
                "accessibility_label": attr_map.get("aria-label") or None,
            }
            self.nodes.append(node)
            self.node_by_dom_id[semantic_dom_id] = node
        if not self_closing and tag.lower() not in VOID_HTML_TAGS:
            self.stack.append(
                semantic_dom_id if semantic_dom_id is not None else (self.stack[-1] if self.stack else None)
            )

    def semantic_nodes(self) -> list[dict[str, Any]]:
        for node in self.nodes:
            parts = node.pop("_text_parts", [])
            node["visible_text"] = _normalize_visible_text(" ".join(str(part) for part in parts))
        return self.nodes


def _semantic_action_from_attrs(attrs: dict[str, str]) -> dict[str, Any] | None:
    action_id = attrs.get("data-action-id")
    if not action_id:
        return None
    payload_bindings: list[str] = []
    raw_payload = attrs.get("data-payload-bindings", "[]")
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError:
        parsed = []
    if isinstance(parsed, list):
        payload_bindings = [str(item) for item in parsed if isinstance(item, str)]
    return {
        "id": action_id,
        "kind": attrs.get("data-action-kind", ""),
        "target_ref": attrs.get("data-action-target-ref", ""),
        "payload_bindings": payload_bindings,
    }


def _semantic_tsx_source_nodes(tsx: str, *, primitive_by_dom_id: dict[str, str]) -> list[dict[str, Any]]:
    render_lines = _tsx_render_block_lines(tsx)
    if render_lines is None:
        raise LocalToolContractError("TAILWIND_TSX_SHAPE_UNSUPPORTED", "ViewSpecView.tsx render shape is unsupported")
    nodes: list[dict[str, Any]] = []
    stack: list[str | None] = []
    for raw_line in render_lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("</"):
            if stack:
                stack.pop()
            continue
        if line in {"<tbody>", "</tbody>"}:
            continue
        if not line.startswith("<"):
            continue
        if "id={" not in line:
            raise LocalToolContractError(
                "TAILWIND_TSX_SHAPE_UNSUPPORTED", "ViewSpecView.tsx contains an unsupported JSX node"
            )
        tag_match = re.match(r"<([A-Za-z][A-Za-z0-9]*)\b", line)
        if tag_match is None:
            raise LocalToolContractError(
                "TAILWIND_TSX_SHAPE_UNSUPPORTED", "ViewSpecView.tsx contains an unsupported JSX tag"
            )
        tag = tag_match.group(1).lower()
        attrs = _semantic_tsx_attrs(line)
        dom_id = attrs.get("id")
        ir_id = attrs.get("data-ir-id")
        if not dom_id or not ir_id:
            raise LocalToolContractError(
                "TAILWIND_TSX_SHAPE_UNSUPPORTED", "ViewSpecView.tsx node is missing semantic identity attrs"
            )
        node = {
            "dom_id": dom_id,
            "ir_id": ir_id,
            "primitive": primitive_by_dom_id.get(dom_id, ""),
            "tag": tag,
            "visible_text": _normalize_visible_text(_semantic_tsx_inner_text(line, tag)),
            "binding_id": attrs.get("data-binding-id"),
            "action": _semantic_tsx_action(attrs),
            "accessibility_label": attrs.get("aria-label"),
        }
        nodes.append(node)
        if not line.endswith("/>") and f"</{tag}>" not in line:
            stack.append(dom_id)
    return nodes


def _tsx_semantic_parent_map(tsx: str) -> dict[str, str | None]:
    render_lines = _tsx_render_block_lines(tsx)
    if render_lines is None:
        return {}
    parent_by_dom_id: dict[str, str | None] = {}
    stack: list[str] = []
    for raw_line in render_lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("</"):
            if stack:
                stack.pop()
            continue
        if line in {"<tbody>", "</tbody>"} or not line.startswith("<"):
            continue
        attrs = _semantic_tsx_attrs(line) if "id={" in line else {}
        dom_id = attrs.get("id")
        if dom_id:
            parent_by_dom_id[dom_id] = stack[-1] if stack else None
            tag_match = re.match(r"<([A-Za-z][A-Za-z0-9]*)\b", line)
            tag = tag_match.group(1).lower() if tag_match is not None else ""
            if not line.endswith("/>") and tag and f"</{tag}>" not in line:
                stack.append(dom_id)
    return parent_by_dom_id


def _html_semantic_parent_map(html: str) -> dict[str, str | None]:
    parser = _SemanticHtmlParentParser()
    parser.feed(html)
    parser.close()
    return parser.parent_by_dom_id


class _SemanticHtmlParentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parent_by_dom_id: dict[str, str | None] = {}
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_tag(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._handle_tag(tag, attrs, self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() not in VOID_HTML_TAGS and self.stack:
            self.stack.pop()

    def _handle_tag(self, tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool) -> None:
        attr_map = {name.lower(): value or "" for name, value in attrs}
        dom_id = attr_map.get("id")
        ir_id = attr_map.get("data-ir-id")
        semantic_dom_id = dom_id if dom_id and ir_id else None
        if semantic_dom_id is not None:
            self.parent_by_dom_id[semantic_dom_id] = self.stack[-1] if self.stack else None
        if not self_closing and tag.lower() not in VOID_HTML_TAGS:
            self.stack.append(semantic_dom_id or (self.stack[-1] if self.stack else ""))


def _validate_stateful_collection_artifact(artifact_text: str, manifest: dict[str, Any], *, emitter: str) -> list[str]:
    nodes = manifest.get("nodes")
    if not isinstance(nodes, dict):
        return []
    primitive_by_dom_id = {
        dom_id: str(entry.get("primitive") or "")
        for dom_id, entry in nodes.items()
        if isinstance(dom_id, str) and isinstance(entry, dict)
    }
    try:
        if emitter == "html_tailwind":
            source_nodes = _semantic_html_source_nodes(artifact_text, primitive_by_dom_id=primitive_by_dom_id)
            parent_by_dom_id = _html_semantic_parent_map(artifact_text)
        elif emitter in {"react_tsx", "react_tailwind_tsx"}:
            source_nodes = _semantic_tsx_source_nodes(artifact_text, primitive_by_dom_id=primitive_by_dom_id)
            parent_by_dom_id = _tsx_semantic_parent_map(artifact_text)
        else:
            return []
    except ValueError as exc:
        return [str(exc)]
    source_order = [str(node["dom_id"]) for node in source_nodes]
    siblings_by_parent: dict[str | None, list[str]] = {}
    for dom_id in source_order:
        siblings_by_parent.setdefault(parent_by_dom_id.get(dom_id), []).append(dom_id)
    collection_bar_by_motif: dict[str, str] = {}
    errors: list[str] = []
    for dom_id, entry in nodes.items():
        if not isinstance(dom_id, str) or not isinstance(entry, dict):
            continue
        props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
        if props.get("layout_strategy") != "collection_action_bar_v1":
            continue
        ir_id = str(entry.get("ir_id") or "")
        prefix = "planner_"
        suffix = "_collection_actions"
        if not (ir_id.startswith(prefix) and ir_id.endswith(suffix)):
            errors.append(
                "COLLECTION_ACTION_BAR_PLACEMENT_INVALID: action bar id must use planner_<motif_id>_collection_actions."
            )
            continue
        motif_id = ir_id[len(prefix) : -len(suffix)]
        previous = collection_bar_by_motif.setdefault(motif_id, dom_id)
        if previous != dom_id:
            errors.append(f"COLLECTION_ACTION_BAR_DUPLICATE: collection motif {motif_id} has more than one action bar.")
            continue
        wrapper_dom_id = f"dom-motif_{motif_id}"
        wrapper_entry = nodes.get(wrapper_dom_id)
        wrapper_props = (
            wrapper_entry.get("props")
            if isinstance(wrapper_entry, dict) and isinstance(wrapper_entry.get("props"), dict)
            else {}
        )
        if wrapper_props.get("motif_kind") not in {"table", "list"}:
            errors.append(
                f"COLLECTION_ACTION_TARGET_INVALID: action bar {dom_id} does not target a table or list motif."
            )
            continue
        parent = parent_by_dom_id.get(wrapper_dom_id)
        if parent_by_dom_id.get(dom_id) != parent:
            errors.append(
                f"COLLECTION_ACTION_BAR_PLACEMENT_INVALID: action bar {dom_id} is not a sibling of {wrapper_dom_id}."
            )
            continue
        siblings = siblings_by_parent.get(parent, [])
        try:
            wrapper_index = siblings.index(wrapper_dom_id)
        except ValueError:
            errors.append(
                f"COLLECTION_ACTION_BAR_PLACEMENT_INVALID: collection wrapper {wrapper_dom_id} is missing from source order."
            )
            continue
        if wrapper_index == 0 or siblings[wrapper_index - 1] != dom_id:
            errors.append(
                f"COLLECTION_ACTION_BAR_PLACEMENT_INVALID: action bar {dom_id} must be the direct previous sibling of {wrapper_dom_id}."
            )
    return errors


def _tsx_render_block_lines(tsx: str) -> list[str] | None:
    lines = tsx.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == "return (":
            start = index + 1
            break
    if start is None:
        return None
    for end in range(start, len(lines)):
        if lines[end].strip() == ");":
            return lines[start:end]
    return None


def _semantic_tsx_attrs(line: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for name, encoded in re.findall(r'([A-Za-z][-A-Za-z0-9]*)=\{("(?:\\.|[^"\\])*")\}', line):
        try:
            attrs[name] = str(json.loads(encoded))
        except json.JSONDecodeError as exc:
            raise LocalToolContractError(
                "TAILWIND_TSX_SHAPE_UNSUPPORTED", "ViewSpecView.tsx contains an unsupported attr literal"
            ) from exc
    for name, value in re.findall(r'([A-Za-z][-A-Za-z0-9]*)="([^"]*)"', line):
        attrs.setdefault(name, value)
    return attrs


def _semantic_tsx_action(attrs: dict[str, str]) -> dict[str, Any] | None:
    action_id = attrs.get("data-action-id")
    if not action_id:
        return None
    payload_bindings: list[str] = []
    raw_payload = attrs.get("data-payload-bindings", "[]")
    try:
        parsed = json.loads(raw_payload)
    except json.JSONDecodeError:
        parsed = []
    if isinstance(parsed, list):
        payload_bindings = [str(item) for item in parsed if isinstance(item, str)]
    return {
        "id": action_id,
        "kind": attrs.get("data-action-kind", ""),
        "target_ref": attrs.get("data-action-target-ref", ""),
        "payload_bindings": payload_bindings,
    }


def _semantic_tsx_inner_text(line: str, tag: str) -> str:
    if f"</{tag}>" not in line:
        return ""
    direct = re.search(r'>\{("(?:\\.|[^"\\])*")\}</' + re.escape(tag) + r">", line)
    if direct is not None:
        try:
            return str(json.loads(direct.group(1)))
        except json.JSONDecodeError:
            return ""
    rendered = re.search(
        r'>\{renderValue\(data\["(?:\\.|[^"\\])*"\],\s*("(?:\\.|[^"\\])*")\)\}</' + re.escape(tag) + r">",
        line,
    )
    if rendered is not None:
        try:
            return str(json.loads(rendered.group(1)))
        except json.JSONDecodeError:
            return ""
    projected = re.search(
        r'>\{renderValue\(stateText\["(?:\\.|[^"\\])*"\],\s*("(?:\\.|[^"\\])*")\)\}</' + re.escape(tag) + r">",
        line,
    )
    if projected is not None:
        try:
            return str(json.loads(projected.group(1)))
        except json.JSONDecodeError:
            return ""
    return ""


def _normalize_visible_text(value: str) -> str:
    return " ".join(str(value).split())


def _validate_intent_semantic_digest(
    artifact_text: str,
    manifest: dict[str, Any],
    *,
    emitter: str,
    diagnostics_path: Path,
) -> list[str]:
    if manifest.get("kind") != "intent_bundle_compile":
        return []
    semantic_digest = manifest.get("semantic_digest")
    if not isinstance(semantic_digest, dict):
        return ["SEMANTIC_DIGEST_MISSING: manifest missing semantic_digest"]
    errors: list[str] = []
    errors.extend(_validate_semantic_digest_shape(semantic_digest))
    if errors:
        return errors
    diagnostics: list[dict[str, Any]] = []
    if diagnostics_path.exists():
        try:
            loaded = json.loads(diagnostics_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            loaded = []
        if isinstance(loaded, list):
            diagnostics = [item for item in loaded if isinstance(item, dict)]
    try:
        recomputed = build_intent_semantic_digest(
            manifest,
            artifact_text,
            emitter=emitter,
            diagnostics=diagnostics,
        )
    except ValueError as exc:
        return [str(exc)]
    for key in ("manifest_projection", "source_projection", "digest"):
        if semantic_digest.get(key) != recomputed[key]:
            errors.append(f"SEMANTIC_DIGEST_MISMATCH: semantic_digest.{key} does not match current artifact")
    if recomputed["manifest_projection"] != recomputed["source_projection"]:
        errors.append("SEMANTIC_DIGEST_MISMATCH: manifest_projection does not match source_projection")
        if _semantic_projection_uses_stateful_collections(
            recomputed["manifest_projection"]
        ) or _semantic_projection_uses_stateful_collections(recomputed["source_projection"]):
            errors.append(
                "STATEFUL_COLLECTIONS_EMITTER_PARITY_FAILED: manifest_projection does not match source_projection for stateful collection semantics"
            )
    return errors


def _validate_semantic_digest_shape(value: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if _contains_semantic_digest_key(value.get("manifest_projection")) or _contains_semantic_digest_key(
        value.get("source_projection")
    ):
        return ["SEMANTIC_DIGEST_CIRCULAR: semantic_digest projections must not contain semantic_digest"]
    extra = sorted(set(value) - SEMANTIC_DIGEST_KEYS)
    missing = sorted(SEMANTIC_DIGEST_KEYS - set(value))
    if extra:
        errors.append(
            f"SEMANTIC_DIGEST_FIELD_FORBIDDEN: semantic_digest contains forbidden field(s): {', '.join(extra)}"
        )
    if missing:
        errors.append(f"SEMANTIC_DIGEST_FIELD_FORBIDDEN: semantic_digest missing field(s): {', '.join(missing)}")
    if value.get("version") != SEMANTIC_DIGEST_VERSION:
        errors.append(f"SEMANTIC_DIGEST_FIELD_FORBIDDEN: semantic_digest.version must be {SEMANTIC_DIGEST_VERSION}")
    if not isinstance(value.get("digest"), str) or not HASH_RE.match(str(value.get("digest"))):
        errors.append("SEMANTIC_DIGEST_FIELD_FORBIDDEN: semantic_digest.digest must be a sha256 hex string")
    for key in ("manifest_projection", "source_projection"):
        projection = value.get(key)
        if not isinstance(projection, dict):
            errors.append(f"SEMANTIC_DIGEST_FIELD_FORBIDDEN: semantic_digest.{key} must be an object")
            continue
        errors.extend(_validate_semantic_projection_shape(projection, key))
        try:
            _assert_semantic_projection_size(projection, key)
        except ValueError as exc:
            errors.append(str(exc))
    return errors


def _contains_semantic_digest_key(value: Any) -> bool:
    if isinstance(value, dict):
        return "semantic_digest" in value or any(_contains_semantic_digest_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_semantic_digest_key(item) for item in value)
    return False


def _validate_semantic_projection_shape(projection: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    extra = sorted(set(projection) - SEMANTIC_PROJECTION_KEYS)
    missing = sorted(SEMANTIC_PROJECTION_KEYS - set(projection))
    if extra:
        errors.append(f"SEMANTIC_DIGEST_FIELD_FORBIDDEN: {label} contains forbidden field(s): {', '.join(extra)}")
    if missing:
        errors.append(f"SEMANTIC_DIGEST_FIELD_FORBIDDEN: {label} missing field(s): {', '.join(missing)}")
    if projection.get("version") != SEMANTIC_DIGEST_VERSION:
        errors.append(f"SEMANTIC_DIGEST_FIELD_FORBIDDEN: {label}.version must be {SEMANTIC_DIGEST_VERSION}")
    for key in ("node_order", "action_order", "diagnostic_codes"):
        if not _is_string_list(projection.get(key)):
            errors.append(f"SEMANTIC_DIGEST_FIELD_FORBIDDEN: {label}.{key} must be a list of strings")
    nodes = projection.get("nodes")
    if not isinstance(nodes, list):
        errors.append(f"SEMANTIC_DIGEST_FIELD_FORBIDDEN: {label}.nodes must be a list")
        return errors
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"SEMANTIC_DIGEST_FIELD_FORBIDDEN: {label}.nodes[{index}] must be an object")
            continue
        node_extra = sorted(set(node) - SEMANTIC_NODE_KEYS)
        if node_extra:
            errors.append(
                f"SEMANTIC_DIGEST_FIELD_FORBIDDEN: {label}.nodes[{index}] contains forbidden field(s): {', '.join(node_extra)}"
            )
        for key in ("dom_id", "ir_id", "primitive", "tag", "visible_text"):
            if not isinstance(node.get(key), str):
                errors.append(f"SEMANTIC_DIGEST_FIELD_FORBIDDEN: {label}.nodes[{index}].{key} must be a string")
        for key in ("binding_id", "accessibility_label"):
            if node.get(key) is not None and not isinstance(node.get(key), str):
                errors.append(f"SEMANTIC_DIGEST_FIELD_FORBIDDEN: {label}.nodes[{index}].{key} must be null or string")
        action = node.get("action")
        if action is not None:
            if not isinstance(action, dict):
                errors.append(f"SEMANTIC_DIGEST_FIELD_FORBIDDEN: {label}.nodes[{index}].action must be null or object")
            else:
                action_extra = sorted(set(action) - SEMANTIC_ACTION_KEYS)
                if action_extra:
                    errors.append(
                        f"SEMANTIC_DIGEST_FIELD_FORBIDDEN: {label}.nodes[{index}].action contains forbidden field(s): {', '.join(action_extra)}"
                    )
                for key in ("id", "kind", "target_ref"):
                    if not isinstance(action.get(key), str):
                        errors.append(
                            f"SEMANTIC_DIGEST_FIELD_FORBIDDEN: {label}.nodes[{index}].action.{key} must be a string"
                        )
                if not _is_string_list(action.get("payload_bindings")):
                    errors.append(
                        f"SEMANTIC_DIGEST_FIELD_FORBIDDEN: {label}.nodes[{index}].action.payload_bindings must be a list of strings"
                    )
    return errors


def _validate_manifest_diagnostics(value: Any, errors: list[str], *, label: str = "manifest diagnostics") -> None:
    if not isinstance(value, list):
        errors.append(f"{label} must be a list")
        return
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not {"severity", "code", "message"}.issubset(item):
            errors.append(f"{label}[{index}] must contain severity, code, and message")
            continue
        if item["severity"] not in DIAGNOSTIC_SEVERITIES:
            errors.append(f"{label}[{index}].severity must be one of: error, info, warning")
        for key in ("code", "message"):
            if not isinstance(item[key], str) or not item[key]:
                errors.append(f"{label}[{index}].{key} must be a non-empty string")
        for key in ("node_id", "path"):
            if key in item and not isinstance(item[key], str):
                errors.append(f"{label}[{index}].{key} must be a string")


def _validate_manifest_external_refs(value: Any, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append("manifest external_refs must be a list")
        return
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not {"kind", "attr", "url", "behavior"}.issubset(item):
            errors.append(f"manifest external_refs[{index}] must contain kind, attr, url, and behavior")
            continue
        kind = item["kind"]
        attr = item["attr"]
        behavior = item["behavior"]
        if (kind, attr, behavior) not in EXTERNAL_REF_POLICIES:
            errors.append(f"manifest external_refs[{index}] must use an allowed inert external-ref policy")
        url = item["url"]
        if not isinstance(url, str) or not _is_remote_http_url(url):
            errors.append(f"manifest external_refs[{index}].url must be an http(s) URL")


def _validate_manifest_design(value: Any, design_hash: Any, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("manifest design must be an object")
        return
    required = {
        "name",
        "design_hash",
        "lint_summary",
        "findings",
        "applied_tokens",
        "ignored_tokens",
        "dropped_tokens",
        "mode_defaults",
    }
    for key in sorted(required - set(value)):
        errors.append(f"manifest design missing {key}")
    if not isinstance(value.get("name"), str):
        errors.append("manifest design.name must be a string")
    nested_hash = value.get("design_hash")
    if not isinstance(nested_hash, str) or not HASH_RE.match(nested_hash):
        errors.append("manifest design.design_hash must be a sha256 hex string")
    elif design_hash != nested_hash:
        errors.append("manifest design.design_hash must match manifest design_hash")
    summary = value.get("lint_summary")
    if not isinstance(summary, dict):
        errors.append("manifest design.lint_summary must be an object")
    else:
        for key in ("errors", "warnings", "info"):
            if not isinstance(summary.get(key), int) or isinstance(summary.get(key), bool) or summary.get(key) < 0:
                errors.append(f"manifest design.lint_summary.{key} must be a non-negative integer")
    findings = value.get("findings")
    if not isinstance(findings, list):
        errors.append("manifest design.findings must be a list")
    else:
        _validate_manifest_diagnostics(findings, errors, label="manifest design.findings")
    applied_tokens = value.get("applied_tokens")
    if not isinstance(applied_tokens, dict) or not all(
        isinstance(key, str) and _is_string_list(item) for key, item in applied_tokens.items()
    ):
        errors.append("manifest design.applied_tokens must be an object of string arrays")
    for key in ("ignored_tokens", "dropped_tokens", "mode_defaults"):
        if not _is_string_list(value.get(key)):
            errors.append(f"manifest design.{key} must be a list of strings")


def _validate_manifest_nodes(manifest: dict[str, Any], errors: list[str]) -> None:
    nodes = manifest.get("nodes")
    if not isinstance(nodes, dict):
        errors.append("manifest nodes must be an object")
        return
    kind = manifest.get("kind")
    root_aesthetic_profile = manifest_root_aesthetic_profile(nodes) if kind == "intent_bundle_compile" else None
    for node_id, entry in sorted(nodes.items()):
        if not node_id or not SAFE_ID_RE.match(node_id):
            errors.append(f"manifest nodes.{node_id} key must be a safe id")
        if not isinstance(entry, dict):
            errors.append(f"manifest nodes.{node_id} must be an object")
            continue
        if kind == "intent_bundle_compile":
            _validate_intent_manifest_node(node_id, entry, root_aesthetic_profile, errors)
        elif kind == "raw_html_compile":
            _validate_raw_html_manifest_node(node_id, entry, errors)


def _validate_intent_manifest_node(
    node_id: str,
    entry: dict[str, Any],
    root_aesthetic_profile: str | None,
    errors: list[str],
) -> None:
    ir_id = entry.get("ir_id")
    if not isinstance(ir_id, str) or not ir_id:
        errors.append(f"manifest nodes.{node_id}.ir_id must be a non-empty string")
    elif not SAFE_ID_RE.match(ir_id):
        errors.append(f"manifest nodes.{node_id}.ir_id must be a safe id")
    primitive = entry.get("primitive")
    if not isinstance(primitive, str) or not primitive:
        errors.append(f"manifest nodes.{node_id}.primitive must be a non-empty string")
    for key in ("content_refs", "intent_refs", "style_tokens"):
        if not _is_string_list(entry.get(key)):
            errors.append(f"manifest nodes.{node_id}.{key} must be a list of strings")
    content_refs = entry.get("content_refs")
    if _is_string_list(content_refs):
        if any(not CANONICAL_CONTENT_REF_RE.match(item) for item in content_refs):
            errors.append(f"manifest nodes.{node_id}.content_refs must contain only canonical content refs")
    intent_refs = entry.get("intent_refs")
    if _is_string_list(intent_refs):
        if not intent_refs:
            errors.append(f"manifest nodes.{node_id}.intent_refs must not be empty")
        if any(not VIEWSPEC_INTENT_REF_RE.match(item) for item in intent_refs):
            errors.append(f"manifest nodes.{node_id}.intent_refs must contain only ViewSpec intent refs")
    if not isinstance(entry.get("props"), dict):
        errors.append(f"manifest nodes.{node_id}.props must be an object")
        return
    props = entry["props"]
    binding_id = props.get("binding_id")
    if binding_id is not None:
        if not isinstance(binding_id, str) or not binding_id:
            errors.append(f"manifest nodes.{node_id}.props.binding_id must be a non-empty string")
        elif not SAFE_ID_RE.match(binding_id):
            errors.append(f"manifest nodes.{node_id}.props.binding_id must be a safe id")
        else:
            if _is_string_list(intent_refs) and f"viewspec:binding:{binding_id}" not in intent_refs:
                errors.append(f"manifest nodes.{node_id}.intent_refs must include viewspec:binding:{binding_id}")
            if _is_string_list(content_refs) and not content_refs:
                errors.append(f"manifest nodes.{node_id}.content_refs must not be empty for binding_id {binding_id}")
    action_id = props.get("action_id")
    if entry.get("primitive") == "button" or action_id is not None:
        if not isinstance(action_id, str) or not action_id:
            errors.append(f"manifest nodes.{node_id}.props.action_id must be a non-empty string")
        elif not SAFE_ID_RE.match(action_id):
            errors.append(f"manifest nodes.{node_id}.props.action_id must be a safe id")
        elif _is_string_list(intent_refs) and f"viewspec:action:{action_id}" not in intent_refs:
            errors.append(f"manifest nodes.{node_id}.intent_refs must include viewspec:action:{action_id}")
        action_kind = props.get("action_kind")
        if not isinstance(action_kind, str) or not action_kind:
            errors.append(f"manifest nodes.{node_id}.props.action_kind must be a non-empty string")
        payload_bindings = props.get("payload_bindings")
        if not _is_string_list(payload_bindings):
            errors.append(f"manifest nodes.{node_id}.props.payload_bindings must be a list of strings")
        elif any(not SAFE_ID_RE.match(item) for item in payload_bindings):
            errors.append(f"manifest nodes.{node_id}.props.payload_bindings must contain only safe ids")
        target_ref = props.get("target_ref")
        if target_ref not in (None, "") and (
            not isinstance(target_ref, str) or not ACTION_TARGET_REF_RE.match(target_ref)
        ):
            errors.append(
                f"manifest nodes.{node_id}.props.target_ref must be region:id, binding:id, motif:id, or view:id"
            )
    detail_role = props.get("detail_role")
    if detail_role is not None and detail_role not in {"term", "description"}:
        errors.append(f"manifest nodes.{node_id}.props.detail_role must be term or description")
    empty_state_role = props.get("empty_state_role")
    if empty_state_role is not None and empty_state_role not in {"title", "description", "detail"}:
        errors.append(f"manifest nodes.{node_id}.props.empty_state_role must be title, description, or detail")
    state_motif_role = props.get("state_motif_role")
    if state_motif_role is not None and state_motif_role not in {"title", "description", "detail"}:
        errors.append(f"manifest nodes.{node_id}.props.state_motif_role must be title, description, or detail")
    state_role = props.get("state_role")
    if state_role is not None and state_role not in {"loading", "error"}:
        errors.append(f"manifest nodes.{node_id}.props.state_role must be loading or error")
    hero_role = props.get("hero_role")
    if hero_role is not None and hero_role not in {"eyebrow", "title", "description", "detail"}:
        errors.append(f"manifest nodes.{node_id}.props.hero_role must be eyebrow, title, description, or detail")
    _validate_aesthetic_profile_manifest_node(node_id, entry, props, errors)
    _validate_aesthetic_layout_manifest_node(node_id, entry, props, root_aesthetic_profile, errors)


def _validate_aesthetic_profile_manifest_node(
    node_id: str,
    entry: dict[str, Any],
    props: dict[str, Any],
    errors: list[str],
) -> None:
    style_tokens = entry.get("style_tokens")
    profile_tokens = (
        [token for token in style_tokens if is_aesthetic_profile_token(token)] if _is_string_list(style_tokens) else []
    )
    profile = props.get("aesthetic_profile")
    if profile is None and not profile_tokens:
        return
    if entry.get("primitive") != "root":
        errors.append(
            f"AESTHETIC_PROFILE_TARGET_INVALID: manifest node {node_id} aesthetic profile must be on the root node"
        )
    if profile is not None and (not isinstance(profile, str) or profile not in AESTHETIC_PROFILE_TOKENS):
        errors.append(f"AESTHETIC_PROFILE_UNKNOWN: manifest node {node_id} declares unknown aesthetic_profile")
    if len(profile_tokens) > 1:
        errors.append(f"AESTHETIC_PROFILE_MULTIPLE: manifest node {node_id} declares multiple aesthetic style tokens")
    if profile_tokens and any(token not in AESTHETIC_PROFILE_TOKENS for token in profile_tokens):
        errors.append(f"AESTHETIC_PROFILE_UNKNOWN: manifest node {node_id} uses unknown aesthetic style token")
    if isinstance(profile, str) and profile_tokens and profile_tokens[0] != profile:
        errors.append(
            f"AESTHETIC_PROFILE_TARGET_INVALID: manifest node {node_id} aesthetic_profile does not match root style token"
        )


def _validate_aesthetic_layout_manifest_node(
    node_id: str,
    entry: dict[str, Any],
    props: dict[str, Any],
    root_aesthetic_profile: str | None,
    errors: list[str],
) -> None:
    layout_profile = props.get("aesthetic_layout_profile")
    if layout_profile is None:
        return
    if not isinstance(layout_profile, str) or layout_profile not in AESTHETIC_PROFILE_TOKENS:
        errors.append(
            f"AESTHETIC_PROFILE_LAYOUT_UNKNOWN: manifest node {node_id} declares unknown aesthetic_layout_profile"
        )
        return
    if root_aesthetic_profile != layout_profile:
        errors.append(
            f"AESTHETIC_PROFILE_LAYOUT_MISMATCH: manifest node {node_id} aesthetic_layout_profile does not match root profile"
        )
        return

    product_role = props.get("product_role")
    expected = profile_layout_props(layout_profile).get(product_role) if isinstance(product_role, str) else None
    if product_role not in AESTHETIC_PROFILE_LAYOUT_ROLES or expected is None:
        errors.append(
            f"AESTHETIC_PROFILE_LAYOUT_TARGET_INVALID: manifest node {node_id} targets unsupported aesthetic layout role"
        )
        return
    if "columns" in expected:
        columns = props.get("columns")
        if entry.get("primitive") != "grid":
            errors.append(
                f"AESTHETIC_PROFILE_LAYOUT_TARGET_INVALID: manifest node {node_id} layout columns must target a grid"
            )
        if not isinstance(columns, int) or isinstance(columns, bool) or columns != expected["columns"]:
            errors.append(
                f"AESTHETIC_PROFILE_LAYOUT_MISMATCH: manifest node {node_id} columns do not match aesthetic layout profile"
            )
    if "span_columns" in expected:
        span_columns = props.get("span_columns")
        if entry.get("primitive") != "surface" or product_role != "metric_card":
            errors.append(
                f"AESTHETIC_PROFILE_LAYOUT_TARGET_INVALID: manifest node {node_id} span columns must target a metric card surface"
            )
        if (
            not isinstance(span_columns, int)
            or isinstance(span_columns, bool)
            or span_columns != expected["span_columns"]
        ):
            errors.append(
                f"AESTHETIC_PROFILE_LAYOUT_MISMATCH: manifest node {node_id} span_columns do not match aesthetic layout profile"
            )
    if "layout_emphasis" in expected:
        layout_emphasis = props.get("layout_emphasis")
        if entry.get("primitive") != "surface" or product_role != "metric_card":
            errors.append(
                f"AESTHETIC_PROFILE_LAYOUT_TARGET_INVALID: manifest node {node_id} layout emphasis must target a metric card surface"
            )
        if layout_emphasis != expected["layout_emphasis"]:
            errors.append(
                f"AESTHETIC_PROFILE_LAYOUT_MISMATCH: manifest node {node_id} layout_emphasis does not match aesthetic layout profile"
            )


def _validate_raw_html_manifest_node(node_id: str, entry: dict[str, Any], errors: list[str]) -> None:
    if not isinstance(entry.get("tag"), str) or not entry.get("tag"):
        errors.append(f"manifest nodes.{node_id}.tag must be a non-empty string")
    attrs = entry.get("attrs")
    if not isinstance(attrs, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in attrs.items()
    ):
        errors.append(f"manifest nodes.{node_id}.attrs must be an object of strings")
    if not isinstance(entry.get("text"), str):
        errors.append(f"manifest nodes.{node_id}.text must be a string")


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_remote_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def _contains_remote_http_reference(value: str) -> bool:
    # Browsers resolve backslashes (incl. %5c) AND strip control whitespace
    # (tab/LF/CR) when parsing URLs, so "/\\evil.com" or "https:/<tab>/evil.com"
    # beacon cross-origin despite containing no literal "//". Collapse both the
    # same way the compiler-side URL policy does (shared helper), so the two
    # sanitizers cannot drift on obfuscation handling.
    collapsed = collapse_url_obfuscation(value)
    return bool(re.search(r"(?i)(?:https?:)?//", collapsed))


# Beacon backstop. The HTMLParser probe below drops a tag a browser's error-recovery
# parser still acts on: an unterminated final tag at EOF (`<iframe src="//evil"` with no
# closing `>`), and `<base>` re-rooting relative URLs. Tag markers run on the raw html so
# `\b` tolerates inter-attribute whitespace; the remote-URL scan runs on the
# obfuscation-collapsed html (shared helper) so whitespace/backslash/%5c can't hide a
# scheme. The URL scan is ATTRIBUTE-SCOPED (never a raw `http` substring) so a rendered
# URL in visible text is not mistaken for a beacon.
_BEACON_FORBIDDEN_TAG_RE = re.compile(r"(?i)<(?:iframe|embed|object|base|link)\b")
# Applied to each `<meta` tag region from the linear region scan, never as `<meta\b[^>]*...`
# over the whole document (that shape re-scans to EOF from every unterminated opener).
_META_REFRESH_ATTR_RE = re.compile(r"(?i)http-equiv\s*=\s*[\"']?\s*refresh")
# Only AUTO-FETCHING attributes -- NOT href/xlink:href, which are navigation (a plain
# <a href="https://...">) and are governed per-tag by the probe + external_refs policy;
# a flat scan can't see the owning tag, so including href would reject legitimate links.
_BEACON_REMOTE_URL_ATTR_RE = re.compile(
    r"(?i)(?:src|action|formaction|poster|srcset|background|manifest|data|ping)"
    r"\s*=\s*[\"']?\s*(?:https?:)?//"
)

_AUTOFETCH_SURFACE_ERROR = "index.html contains an active or auto-fetching surface"
_FORM_SURFACE_ERROR = "index.html contains an active form surface"
_REMOTE_URL_ATTR_ERROR = "index.html contains an auto-fetching remote URL attribute"
_INLINE_EVENT_HANDLER_ERROR = "CHECK_INLINE_EVENT_HANDLER: index.html contains an inline event handler attribute"
_UNSAFE_URL_SCHEME_ERROR = (
    "CHECK_UNSAFE_URL_SCHEME: index.html contains a javascript:, vbscript:, or data: URL attribute"
)
_UNCLOSED_SCRIPT_ERROR = "CHECK_UNCLOSED_SCRIPT: index.html contains an unclosed script tag"
_SCAN_LIMIT_ERROR = (
    f"CHECK_SCAN_LIMIT_EXCEEDED: index.html contains more than {MAX_SCANNED_TAG_BLOCKS} style or script blocks"
)


class TagBlockScan(NamedTuple):
    """Result of `find_tag_blocks`.

    `blocks` holds (start, content_start, content_end, end) offsets per closed block;
    `unclosed_content_start` is where the content of a never-closed block begins (None when
    every block closes); `truncated` is set when more than the limit of blocks were present.
    """

    blocks: tuple[tuple[int, int, int, int], ...]
    unclosed_content_start: int | None
    truncated: bool


_TAG_SCAN_PATTERNS: dict[str, tuple[re.Pattern[str], re.Pattern[str]]] = {}


def _tag_scan_patterns(tag: str) -> tuple[re.Pattern[str], re.Pattern[str]]:
    patterns = _TAG_SCAN_PATTERNS.get(tag)
    if patterns is None:
        escaped = re.escape(tag)
        # An end tag name ends at whitespace, "/", or ">" in the HTML tokenizer, so `</style>`
        # and `</style >` both close a block while `</styles>` or `</style` at EOF do not.
        patterns = (re.compile(rf"(?i)<{escaped}\b"), re.compile(rf"(?i)</{escaped}(?=[\s/>])"))
        _TAG_SCAN_PATTERNS[tag] = patterns
    return patterns


def find_tag_blocks(html: str, tag: str, *, limit: int = MAX_SCANNED_TAG_BLOCKS) -> TagBlockScan:
    """Locate `<tag ...>content</tag>` blocks in one linear pass.

    Every search resumes where the previous one stopped, so unclosed openers cost one scan to
    EOF in total instead of one per opener (the quadratic shape of
    `<tag\\b[^>]*>[\\s\\S]*?</tag>` that S-3 measured at ~4x per doubling). A block whose
    opener or closer never terminates is reported through `unclosed_content_start`; a browser
    treats the rest of the document as that block's content, so callers check that tail too.
    More than `limit` closed blocks sets `truncated` so callers can fail closed.
    """
    opener_re, closer_re = _tag_scan_patterns(tag)
    blocks: list[tuple[int, int, int, int]] = []
    position = 0
    unclosed_content_start: int | None = None
    truncated = False
    while True:
        opener = opener_re.search(html, position)
        if opener is None:
            break
        opener_end = html.find(">", opener.end())
        if opener_end < 0:
            unclosed_content_start = opener.end()
            break
        content_start = opener_end + 1
        closer = closer_re.search(html, content_start)
        closer_end = html.find(">", closer.end()) if closer is not None else -1
        if closer is None or closer_end < 0:
            unclosed_content_start = content_start
            break
        if len(blocks) >= limit:
            truncated = True
            break
        blocks.append((opener.start(), content_start, closer.start(), closer_end + 1))
        position = closer_end + 1
    return TagBlockScan(tuple(blocks), unclosed_content_start, truncated)


def iter_tag_regions(html: str, tag: str) -> Iterator[tuple[int, str]]:
    """Yield (opener_start, attribute_text) for each `<tag` opener in one linear pass.

    The attribute text runs to the first ">" after the opener. An opener with no ">" at all
    yields the rest of the document once and ends the scan, since no later "<" can start a
    separate tag inside it.
    """
    opener_re, _closer_re = _tag_scan_patterns(tag)
    position = 0
    while True:
        opener = opener_re.search(html, position)
        if opener is None:
            return
        end = html.find(">", opener.end())
        if end < 0:
            yield opener.start(), html[opener.end() :]
            return
        yield opener.start(), html[opener.end() : end]
        position = end + 1


# CSS escapes the tokenizer decodes before an ident is recognised: `\75rl(`, `\75 rl(` and
# `u\rl(` are all `url(`; `@\69mport` is `@import`. Decoded before any name matching.
_CSS_HEX_ESCAPE_RE = re.compile(r"\\([0-9a-fA-F]{1,6})[ \t\n\r\f]?")
_CSS_CHAR_ESCAPE_RE = re.compile(r"\\(.)", re.DOTALL)
# Matched against normalised CSS (see `normalize_css_text`): escapes decoded, comments removed,
# every ASCII control/whitespace character stripped, backslashes collapsed to "/", lower-cased.
# Covers the @import rule, every URL-loading function, and any remote scheme or scheme-relative
# authority anywhere in the text, so an unlisted function cannot smuggle a fetch.
UNSAFE_CSS_REFERENCE_RE = re.compile(
    r"@import|url\(|image-set\(|-webkit-image-set\(|image\(|src\(|expression\(|javascript:|vbscript:|https?:|//"
)


def _decode_css_hex_escape(match: re.Match[str]) -> str:
    code_point = int(match.group(1), 16)
    if code_point == 0 or code_point > 0x10FFFF or 0xD800 <= code_point <= 0xDFFF:
        return "�"
    return chr(code_point)


def _strip_css_comments(css: str) -> str:
    """Remove well-formed `/* ... */` comments in one pass; an unterminated `/*` is kept."""
    pieces: list[str] = []
    position = 0
    while True:
        start = css.find("/*", position)
        end = css.find("*/", start + 2) if start >= 0 else -1
        if start < 0 or end < 0:
            pieces.append(css[position:])
            return "".join(pieces)
        pieces.append(css[position:start])
        position = end + 2


def normalize_css_text(css: str) -> tuple[str, str]:
    """Return the two browser-equivalent views of a CSS fragment used for policy matching.

    Both have CSS escapes decoded, control and whitespace characters removed, backslashes
    collapsed to "/" (shared URL helper) and ASCII lower-casing; the second also has `/* */`
    comments removed. Callers match both so the gate fails closed either way: a comment must
    not split a function name (`url/**/(`), and a comment opener inside a CSS string must not
    hide a live `url(` that follows it.
    """
    decoded = _CSS_CHAR_ESCAPE_RE.sub(r"\1", _CSS_HEX_ESCAPE_RE.sub(_decode_css_hex_escape, css))
    with_comments = collapse_url_obfuscation(decoded).lower()
    without_comments = collapse_url_obfuscation(_strip_css_comments(decoded)).lower()
    return with_comments, without_comments


def css_has_unsafe_reference(css: str, pattern: re.Pattern[str] = UNSAFE_CSS_REFERENCE_RE) -> bool:
    """True when a CSS fragment carries an @import, a URL-loading function, or a remote scheme."""
    return any(pattern.search(variant) for variant in normalize_css_text(css))


_URL_SCHEME_PREFIX_RE = re.compile(r"^([a-z][a-z0-9+.\-]*):")


def _url_attribute_has_unsafe_scheme(tag_name: str, attr_name: str, attr_value: str) -> bool:
    """javascript:, vbscript:, or data: after the decoding a browser applies before scheme detection.

    Entity decoding happens upstream (HTMLParser, or `unescape` on the raw tail); here every
    ASCII control and whitespace character is removed and backslashes collapse, so
    `java\\tscript:`, `\\x01javascript:` and `JAVA\\nSCRIPT:` all resolve. The only inert data:
    URLs are the raster image forms the raw-HTML sanitizer keeps on <img src>.
    """
    candidates = attr_value.split(",") if attr_name == "srcset" else (attr_value,)
    for candidate in candidates:
        normalized = collapse_url_obfuscation(candidate).lower()
        match = _URL_SCHEME_PREFIX_RE.match(normalized)
        if match is None:
            continue
        scheme = match.group(1)
        if scheme in UNSAFE_URL_SCHEMES:
            return True
        if scheme == "data" and not (
            tag_name == "img" and attr_name == "src" and normalized.startswith(DATA_IMAGE_SRC_PREFIXES)
        ):
            return True
    return False


def _attribute_surface_errors(tag_name: str, attrs: list[tuple[str, str]]) -> list[str]:
    """Attribute policy for one start tag: (lower-cased name, entity-decoded value) pairs.

    Every pair is checked, including duplicates, since browsers keep the first occurrence and
    HTMLParser reports all of them.
    """
    errors: list[str] = []
    for attr_name, attr_value in attrs:
        if attr_name.startswith("on"):
            errors.append(_INLINE_EVENT_HANDLER_ERROR)
        if attr_name == "style" and css_has_unsafe_reference(attr_value):
            errors.append(_AUTOFETCH_SURFACE_ERROR)
        if attr_name in URL_SCHEME_POLICY_ATTRS and _url_attribute_has_unsafe_scheme(tag_name, attr_name, attr_value):
            errors.append(_UNSAFE_URL_SCHEME_ERROR)
        if attr_name in REMOTE_AUTOFETCH_ATTRS and _contains_remote_http_reference(attr_value):
            errors.append(_REMOTE_URL_ATTR_ERROR)
        if (
            tag_name in REMOTE_HREF_AUTOFETCH_TAGS
            and attr_name in {"href", "xlink:href"}
            and _contains_remote_http_reference(attr_value)
        ):
            errors.append(_REMOTE_URL_ATTR_ERROR)
    return errors


def _unfinished_tag_surface_errors(html: str) -> list[str]:
    """Apply the start-tag attribute policy to one browser-recoverable final tag.

    HTMLParser drops an unterminated final tag; this walks its attributes without scanning
    attribute text as markup and runs the same handler, URL-scheme, CSS and remote-URL policy.
    """
    start = html.rfind("<")
    if start < 0 or ">" in html[start:]:
        return []
    fragment = html[start + 1 :]
    if not fragment or not fragment[0].isalpha():
        return []
    position = 0
    while position < len(fragment) and not fragment[position].isspace() and fragment[position] not in "/>":
        position += 1
    tag_name = fragment[:position].lower()
    attrs: list[tuple[str, str]] = []
    while position < len(fragment):
        while position < len(fragment) and (fragment[position].isspace() or fragment[position] == "/"):
            position += 1
        name_start = position
        while position < len(fragment) and not fragment[position].isspace() and fragment[position] not in "=/>":
            position += 1
        if name_start == position:
            position += 1
            continue
        name = fragment[name_start:position].lower()
        while position < len(fragment) and fragment[position].isspace():
            position += 1
        value = ""
        if position < len(fragment) and fragment[position] == "=":
            position += 1
            while position < len(fragment) and fragment[position].isspace():
                position += 1
            if position < len(fragment) and fragment[position] in "\"'":
                quote = fragment[position]
                position += 1
                value_start = position
                while position < len(fragment) and fragment[position] != quote:
                    position += 1
                value = fragment[value_start:position]
                if position < len(fragment):
                    position += 1
            else:
                value_start = position
                while position < len(fragment) and not fragment[position].isspace() and fragment[position] not in "/>":
                    position += 1
                value = fragment[value_start:position]
        attrs.append((name, unescape(value)))
    return _attribute_surface_errors(tag_name, attrs)


def _meta_refresh_present(html: str) -> bool:
    return any(_META_REFRESH_ATTR_RE.search(attrs) for _start, attrs in iter_tag_regions(html, "meta"))


def _style_blocks_have_unsafe_css(html: str, scan: TagBlockScan) -> bool:
    """Check every `<style>` block, including one whose closer never arrives (content runs to EOF)."""
    for _start, content_start, content_end, _end in scan.blocks:
        if css_has_unsafe_reference(html[content_start:content_end]):
            return True
    return scan.unclosed_content_start is not None and css_has_unsafe_reference(html[scan.unclosed_content_start :])


def _validate_no_autofetch_surfaces(html: str) -> list[str]:
    probe = _AutofetchSurfaceProbe()
    probe.feed(html)
    probe.close()
    errors = list(probe.errors)
    collapsed = collapse_url_obfuscation(html)
    style_scan = find_tag_blocks(html, "style")
    backstops: list[str] = []
    if _BEACON_FORBIDDEN_TAG_RE.search(html) or _meta_refresh_present(html):
        backstops.append(_AUTOFETCH_SURFACE_ERROR)
    if _BEACON_REMOTE_URL_ATTR_RE.search(collapsed):
        backstops.append(_REMOTE_URL_ATTR_ERROR)
    if _style_blocks_have_unsafe_css(html, style_scan):
        backstops.append(_AUTOFETCH_SURFACE_ERROR)
    if style_scan.truncated:
        backstops.append(_SCAN_LIMIT_ERROR)
    backstops.extend(_unfinished_tag_surface_errors(html))
    for message in backstops:
        if message not in errors:
            errors.append(message)
    return errors


class _AutofetchSurfaceProbe(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self._style_depth = 0

    def _append_once(self, message: str) -> None:
        if message not in self.errors:
            self.errors.append(message)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.lower()
        pairs = [(name.lower(), value or "") for name, value in attrs]
        if tag_name == "style":
            self._style_depth += 1
        if tag_name in ACTIVE_OR_AUTOFETCH_TAGS:
            self._append_once(_AUTOFETCH_SURFACE_ERROR)
        if tag_name in ACTIVE_STRUCTURAL_TAGS:
            self._append_once(_FORM_SURFACE_ERROR)
        if tag_name == "meta" and any(name == "http-equiv" and value.lower() == "refresh" for name, value in pairs):
            self._append_once(_AUTOFETCH_SURFACE_ERROR)
        for message in _attribute_surface_errors(tag_name, pairs):
            self._append_once(message)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "style" and self._style_depth:
            self._style_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._style_depth and css_has_unsafe_reference(data):
            self._append_once(_AUTOFETCH_SURFACE_ERROR)


class _ArtifactDomProbe(HTMLParser):
    def __init__(self, *, record_ungoverned: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: dict[str, dict[str, str]] = {}
        self.node_ids: dict[str, dict[str, str]] = {}
        self.binding_ids: dict[str, str] = {}
        self.action_ids: dict[str, str] = {}
        self.data_ir_by_dom_id: dict[str, str] = {}
        self.data_ir_without_dom_id: list[str] = []
        self.text_by_node_id: dict[str, list[str]] = {}
        self._node_stack: list[str | None] = []
        self.errors: list[str] = []
        # Raw-HTML artifacts: every element without a data-viewspec-node-id, as (tag, sorted attrs),
        # so the validator can require them to be exactly the fixed document wrapper.
        self._record_ungoverned = record_ungoverned
        self.ungoverned_elements: list[tuple[str, tuple[tuple[str, str], ...]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.lower()
        attr_map = {name.lower(): value or "" for name, value in attrs}
        attr_map["__tag__"] = tag_name
        dom_id = attr_map.get("id")
        node_id = attr_map.get("data-viewspec-node-id")
        ir_id = attr_map.get("data-ir-id")
        binding_id = attr_map.get("data-binding-id")
        action_id = attr_map.get("data-action-id")

        if self._record_ungoverned and not node_id:
            self.ungoverned_elements.append(
                (tag_name, tuple(sorted((name, value) for name, value in attr_map.items() if name != "__tag__")))
            )
        if dom_id:
            if dom_id in self.ids:
                self.errors.append(f"index.html contains duplicate id {dom_id}")
            else:
                self.ids[dom_id] = attr_map
        if node_id:
            if node_id in self.node_ids:
                self.errors.append(f"index.html contains duplicate data-viewspec-node-id {node_id}")
            else:
                self.node_ids[node_id] = attr_map
        if ir_id:
            if dom_id:
                self.data_ir_by_dom_id[dom_id] = ir_id
            else:
                self.data_ir_without_dom_id.append(ir_id)
        if binding_id:
            if binding_id in self.binding_ids:
                self.errors.append(f"index.html contains duplicate data-binding-id {binding_id}")
            else:
                self.binding_ids[binding_id] = dom_id or ""
        if action_id:
            if action_id in self.action_ids:
                self.errors.append(f"index.html contains duplicate data-action-id {action_id}")
            else:
                self.action_ids[action_id] = dom_id or ""
        if tag_name not in VOID_HTML_TAGS:
            self._node_stack.append(node_id or dom_id or None)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        tag_name = tag.lower()
        if tag_name not in VOID_HTML_TAGS and self._node_stack:
            self._node_stack.pop()

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() not in VOID_HTML_TAGS and self._node_stack:
            self._node_stack.pop()

    def handle_data(self, data: str) -> None:
        if not self._node_stack:
            return
        node_id = self._node_stack[-1]
        if node_id is not None:
            self.text_by_node_id.setdefault(node_id, []).append(data)


def _validate_manifest_dom_links(manifest: dict[str, Any], html: str) -> list[str]:
    nodes = manifest.get("nodes")
    if not isinstance(nodes, dict):
        return []
    kind = manifest.get("kind")
    probe = _ArtifactDomProbe(record_ungoverned=kind == "raw_html_compile")
    probe.feed(html)
    probe.close()

    errors = list(probe.errors)
    if kind == "intent_bundle_compile":
        errors.extend(_validate_intent_dom_links(nodes, probe))
    elif kind == "raw_html_compile":
        errors.extend(_validate_raw_html_dom_links(nodes, probe, manifest))
    return errors


def _manifest_has_action_nodes(manifest: dict[str, Any]) -> bool:
    nodes = manifest.get("nodes")
    if not isinstance(nodes, dict):
        return False
    for entry in nodes.values():
        if not isinstance(entry, dict):
            continue
        props = entry.get("props")
        if entry.get("primitive") == "button" and isinstance(props, dict) and props.get("action_id"):
            return True
    return False


def _validate_intent_dom_links(nodes: dict[str, Any], probe: _ArtifactDomProbe) -> list[str]:
    errors: list[str] = []
    manifest_binding_ids: dict[str, str] = {}
    manifest_action_ids: dict[str, str] = {}
    for ir_id in probe.data_ir_without_dom_id:
        errors.append(f"DOM element with data-ir-id {ir_id} is missing an id")
    for dom_id, entry in sorted(nodes.items()):
        if not isinstance(entry, dict):
            errors.append(f"manifest nodes.{dom_id} must be an object")
            continue
        props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
        binding_id = props.get("binding_id")
        if isinstance(binding_id, str) and binding_id:
            previous = manifest_binding_ids.setdefault(binding_id, dom_id)
            if previous != dom_id:
                errors.append(f"manifest nodes contain duplicate binding_id {binding_id}: {previous}, {dom_id}")
        action_id = props.get("action_id")
        if isinstance(action_id, str) and action_id:
            previous = manifest_action_ids.setdefault(action_id, dom_id)
            if previous != dom_id:
                errors.append(f"manifest nodes contain duplicate action_id {action_id}: {previous}, {dom_id}")
        attrs = probe.ids.get(dom_id)
        if attrs is None:
            errors.append(f"manifest node {dom_id} has no matching DOM id")
            continue
        ir_id = entry.get("ir_id")
        if isinstance(ir_id, str) and ir_id and attrs.get("data-ir-id") != ir_id:
            errors.append(f"manifest node {dom_id} ir_id does not match DOM data-ir-id")
        _compare_json_list_attr(errors, attrs, entry, dom_id, "data-content-refs", "content_refs")
        _compare_json_list_attr(errors, attrs, entry, dom_id, "data-intent-refs", "intent_refs")
        _compare_json_list_attr(errors, attrs, entry, dom_id, "data-style-tokens", "style_tokens")
        _validate_intent_semantic_attrs(errors, attrs, entry, dom_id, probe)
    for dom_id in sorted(probe.data_ir_by_dom_id):
        if dom_id not in nodes:
            errors.append(f"DOM element {dom_id} with data-ir-id is missing from manifest nodes")
    return errors


_RAW_HTML_UNGOVERNED_PREFIX = "CHECK_RAW_HTML_DOM_UNGOVERNED"


def _validate_raw_html_dom_links(
    nodes: dict[str, Any], probe: _ArtifactDomProbe, manifest: dict[str, Any]
) -> list[str]:
    errors: list[str] = []
    for node_id, entry in sorted(nodes.items()):
        if not isinstance(entry, dict):
            errors.append(f"manifest nodes.{node_id} must be an object")
            continue
        _validate_raw_html_manifest_node_policy(node_id, entry, errors)
        attrs = probe.node_ids.get(node_id)
        if attrs is None:
            errors.append(f"manifest node {node_id} has no matching data-viewspec-node-id")
            continue
        _validate_raw_html_dom_node(errors, node_id, entry, attrs, probe)
    for node_id in sorted(probe.node_ids):
        if node_id not in nodes:
            errors.append(f"DOM element {node_id} with data-viewspec-node-id is missing from manifest nodes")
    errors.extend(_validate_raw_html_document_wrapper(probe, manifest))
    return errors


def _raw_html_document_wrapper(source_hash: str) -> list[tuple[str, tuple[tuple[str, str], ...]]]:
    """The fixed elements `viewspec.raw_html.compile_html` writes around the governed body."""
    return sorted(
        [
            ("html", (("lang", "en"),)),
            ("head", ()),
            ("meta", (("charset", "utf-8"),)),
            ("meta", (("content", "width=device-width, initial-scale=1"), ("name", "viewport"))),
            ("title", ()),
            ("style", ()),
            ("body", ()),
            ("div", (("class", "vs-artifact"), ("data-source-hash", source_hash), ("data-viewspec-root", "true"))),
        ]
    )


def _validate_raw_html_document_wrapper(probe: _ArtifactDomProbe, manifest: dict[str, Any]) -> list[str]:
    """Fail closed: every raw-HTML DOM element carries a manifest node id or is the fixed wrapper.

    The manifest nodes govern the sanitized body; anything else a tampered index.html adds
    (an extra <p onclick>, a second <style>, an <svg>) has no manifest entry and is rejected here
    even before its attributes are judged.
    """
    source_hash = manifest.get("source_hash")
    expected = _raw_html_document_wrapper(source_hash if isinstance(source_hash, str) else "")
    actual = sorted(probe.ungoverned_elements)
    if actual == expected:
        return []
    remaining = list(expected)
    extra_tags: set[str] = set()
    for element in actual:
        if element in remaining:
            remaining.remove(element)
        else:
            extra_tags.add(element[0])
    if extra_tags:
        shown = sorted(extra_tags)
        listed = ", ".join(f"<{tag}>" for tag in shown[:8])
        if len(shown) > 8:
            listed += f" (+{len(shown) - 8} more)"
        return [f"{_RAW_HTML_UNGOVERNED_PREFIX}: index.html contains DOM elements without a manifest node id: {listed}"]
    return [f"{_RAW_HTML_UNGOVERNED_PREFIX}: index.html document wrapper does not match the raw-HTML compile template"]


def _validate_raw_html_manifest_node_policy(node_id: str, entry: dict[str, Any], errors: list[str]) -> None:
    """Hold the (unsigned) manifest's nodes to the raw-HTML sanitizer allowlist.

    DOM attributes must equal the manifest's, so the manifest itself must only name tags,
    attributes and URLs the sanitizer could have produced; otherwise a tampered manifest would
    legitimise whatever the tampered DOM mirrors.
    """
    tag = entry.get("tag")
    attrs = entry.get("attrs")
    if not isinstance(tag, str) or not isinstance(attrs, dict):
        return  # shape errors are reported by _validate_raw_html_manifest_node
    if tag not in RAW_HTML_ALLOWED_TAGS:
        errors.append(
            f"{_RAW_HTML_UNGOVERNED_PREFIX}: manifest node {node_id} tag <{tag}> is outside the raw-HTML allowlist"
        )
    allowed = RAW_HTML_SAFE_GLOBAL_ATTRS | RAW_HTML_SAFE_ATTRS_BY_TAG.get(tag, set())
    for name, value in sorted(attrs.items()):
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        if name == "data-viewspec-external-src" and tag == "img":
            if not raw_html_is_external_http_url(raw_html_normalize_url(value)):
                errors.append(
                    f"{_RAW_HTML_UNGOVERNED_PREFIX}: manifest node {node_id} attr {name} URL violates the raw-HTML URL policy"
                )
            continue
        if name not in allowed and not name.startswith("aria-"):
            errors.append(
                f"{_RAW_HTML_UNGOVERNED_PREFIX}: manifest node {node_id} attr {name} is outside the raw-HTML allowlist"
            )
            continue
        if name in RAW_HTML_URL_ATTRS:
            normalized = raw_html_normalize_url(value)
            inert_remote_image = tag == "img" and name == "src" and raw_html_is_external_http_url(normalized)
            if inert_remote_image or not raw_html_safe_url(normalized, attr=name):
                errors.append(
                    f"{_RAW_HTML_UNGOVERNED_PREFIX}: manifest node {node_id} attr {name} URL violates the raw-HTML URL policy"
                )


def _validate_raw_html_dom_node(
    errors: list[str],
    node_id: str,
    entry: dict[str, Any],
    attrs: dict[str, str],
    probe: _ArtifactDomProbe,
) -> None:
    manifest_tag = entry.get("tag")
    manifest_attrs = entry.get("attrs")
    if not isinstance(manifest_tag, str) or not isinstance(manifest_attrs, dict):
        return
    external_image_url = manifest_attrs.get("data-viewspec-external-src")
    expected_tag = "a" if manifest_tag == "img" and isinstance(external_image_url, str) else manifest_tag
    if attrs.get("__tag__") != expected_tag:
        errors.append(f"manifest node {node_id} tag does not match DOM tag")
    dom_attrs = {name: value for name, value in attrs.items() if name not in {"__tag__", "data-viewspec-node-id"}}
    if isinstance(external_image_url, str):
        if attrs.get("href") != external_image_url:
            errors.append(f"manifest node {node_id} external image href does not match manifest attrs")
        expected_link_attrs = {"class": "vs-external-image", "href": external_image_url, "rel": "noopener noreferrer"}
        if dom_attrs != expected_link_attrs:
            errors.append(
                f"{_RAW_HTML_UNGOVERNED_PREFIX}: manifest node {node_id} external image link attributes do not match the raw-HTML template"
            )
        return
    for attr_name, attr_value in sorted(manifest_attrs.items()):
        if not isinstance(attr_name, str) or not isinstance(attr_value, str):
            continue
        if attrs.get(attr_name) != attr_value:
            errors.append(f"manifest node {node_id} attr {attr_name} does not match DOM")
    extra_attrs = sorted(name for name in dom_attrs if name not in manifest_attrs)
    if extra_attrs:
        listed = ", ".join(extra_attrs[:8])
        if len(extra_attrs) > 8:
            listed += f" (+{len(extra_attrs) - 8} more)"
        errors.append(
            f"{_RAW_HTML_UNGOVERNED_PREFIX}: manifest node {node_id} carries DOM attributes missing from manifest attrs: {listed}"
        )
    manifest_text = entry.get("text")
    if isinstance(manifest_text, str):
        dom_text = _collapse_artifact_text(" ".join(probe.text_by_node_id.get(node_id, [])))[:160]
        if dom_text != manifest_text:
            errors.append(f"manifest node {node_id} text does not match DOM text")


def _compare_json_list_attr(
    errors: list[str],
    attrs: dict[str, str],
    entry: dict[str, Any],
    dom_id: str,
    attr_name: str,
    manifest_key: str,
) -> None:
    attr_value = attrs.get(attr_name)
    if attr_value is None:
        errors.append(f"DOM element {dom_id} missing {attr_name}")
        return
    try:
        parsed = json.loads(attr_value)
    except json.JSONDecodeError:
        errors.append(f"DOM element {dom_id} has invalid {attr_name} JSON")
        return
    manifest_value = entry.get(manifest_key)
    if parsed != manifest_value:
        errors.append(f"manifest node {dom_id} {manifest_key} does not match DOM {attr_name}")


def _collapse_artifact_text(value: str) -> str:
    return " ".join(value.split())


def _validate_intent_semantic_attrs(
    errors: list[str],
    attrs: dict[str, str],
    entry: dict[str, Any],
    dom_id: str,
    probe: _ArtifactDomProbe,
) -> None:
    primitive = entry.get("primitive")
    props = entry.get("props") if isinstance(entry.get("props"), dict) else {}
    tag = attrs.get("__tag__")
    _validate_intent_visible_text(errors, primitive, props, dom_id, probe)
    if props.get("binding_id") is not None and attrs.get("data-binding-id") != str(props["binding_id"]):
        errors.append(f"DOM element {dom_id} data-binding-id does not match manifest props")
    profile = props.get("aesthetic_profile")
    if isinstance(profile, str) and attrs.get("data-aesthetic-profile") != profile:
        errors.append(f"DOM element {dom_id} data-aesthetic-profile does not match manifest props")
    if primitive == "button":
        if tag != "button":
            errors.append(f"manifest node {dom_id} button primitive must render as <button>")
        if attrs.get("type") != "button":
            errors.append(f'DOM element {dom_id} button missing type="button"')
        if props.get("action_id") and attrs.get("data-action-id") != str(props["action_id"]):
            errors.append(f"DOM element {dom_id} data-action-id does not match manifest props")
        if props.get("action_kind") and attrs.get("data-action-kind") != str(props["action_kind"]):
            errors.append(f"DOM element {dom_id} data-action-kind does not match manifest props")
        if attrs.get("data-action-target-ref", "") != str(props.get("target_ref", "")):
            errors.append(f"DOM element {dom_id} data-action-target-ref does not match manifest props")
        _compare_json_attr(errors, attrs, props, dom_id, "data-payload-bindings", "payload_bindings")
        if props.get("action_kind") in STATEFUL_COLLECTION_ACTION_KINDS:
            try:
                parsed_payload_bindings = json.loads(attrs.get("data-payload-bindings", "null"))
            except json.JSONDecodeError:
                parsed_payload_bindings = None
            if parsed_payload_bindings != props.get("payload_bindings", []):
                errors.append(
                    f"STATEFUL_COLLECTIONS_ACTION_PAYLOAD_MISMATCH: DOM element {dom_id} data-payload-bindings does not match manifest props"
                )
    elif primitive == "input":
        if tag != "input":
            errors.append(f"manifest node {dom_id} input primitive must render as <input>")
        if attrs.get("type") != "text":
            errors.append(f'DOM element {dom_id} input missing type="text"')
        if attrs.get("value") != str(props.get("value", "")):
            errors.append(f"DOM element {dom_id} input value does not match manifest props")
        expected_label = str(props.get("aria_label", props.get("binding_id", "input")))
        if attrs.get("aria-label") != expected_label:
            errors.append(f"DOM element {dom_id} input aria-label does not match manifest props")
    elif primitive == "image_slot":
        _validate_image_like_attrs(errors, attrs, props, dom_id, "image_slot", "alt")
    elif primitive == "svg":
        _validate_image_like_attrs(errors, attrs, props, dom_id, "svg", "label")
    elif primitive == "error_boundary" and attrs.get("role") != "alert":
        errors.append(f'DOM element {dom_id} error_boundary missing role="alert"')

    if props.get("motif_kind") == "list" and primitive == "stack" and tag != "ul":
        errors.append(f"manifest node {dom_id} list stack must render as <ul>")
    if props.get("motif_kind") == "list" and primitive == "surface" and tag != "li":
        errors.append(f"manifest node {dom_id} list surface must render as <li>")
    if props.get("motif_kind") == "form" and primitive == "stack":
        if tag != "section":
            errors.append(f"manifest node {dom_id} form stack must render as <section>")
        if attrs.get("role") != "form":
            errors.append(f'DOM element {dom_id} form stack missing role="form"')
    if props.get("motif_kind") == "form" and primitive == "surface":
        if attrs.get("role") != "group":
            errors.append(f'DOM element {dom_id} form field missing role="group"')
    if props.get("motif_kind") == "detail" and primitive == "stack" and tag != "dl":
        errors.append(f"manifest node {dom_id} detail stack must render as <dl>")
    if props.get("motif_kind") == "detail" and primitive == "cluster" and tag != "div":
        errors.append(f"manifest node {dom_id} detail row must render as <div>")
    detail_role = props.get("detail_role")
    if detail_role == "term":
        if tag != "dt":
            errors.append(f"manifest node {dom_id} detail term must render as <dt>")
    elif detail_role == "description":
        if tag != "dd":
            errors.append(f"manifest node {dom_id} detail description must render as <dd>")
    elif detail_role is not None:
        errors.append(f"manifest node {dom_id} detail_role must be term or description")
    if props.get("motif_kind") == "empty_state" and primitive == "surface":
        if tag != "section":
            errors.append(f"manifest node {dom_id} empty_state surface must render as <section>")
        if attrs.get("aria-label") != str(props.get("aria_label", "Empty state")):
            errors.append(f"DOM element {dom_id} empty_state surface aria-label does not match manifest props")
    empty_state_role = props.get("empty_state_role")
    if empty_state_role == "title":
        if tag != "h2":
            errors.append(f"manifest node {dom_id} empty_state title must render as <h2>")
    elif empty_state_role == "description":
        if tag != "p":
            errors.append(f"manifest node {dom_id} empty_state description must render as <p>")
    elif empty_state_role is not None and empty_state_role != "detail":
        errors.append(f"manifest node {dom_id} empty_state_role must be title, description, or detail")
    if props.get("motif_kind") == "loading_state" and primitive == "surface":
        if tag != "section":
            errors.append(f"manifest node {dom_id} loading_state surface must render as <section>")
        if attrs.get("role") != "status":
            errors.append(f'DOM element {dom_id} loading_state surface missing role="status"')
        if attrs.get("aria-busy") != "true":
            errors.append(f'DOM element {dom_id} loading_state surface missing aria-busy="true"')
        if attrs.get("aria-label") != str(props.get("aria_label", "Loading state")):
            errors.append(f"DOM element {dom_id} loading_state surface aria-label does not match manifest props")
    if props.get("motif_kind") == "error_state" and primitive == "surface":
        if tag != "section":
            errors.append(f"manifest node {dom_id} error_state surface must render as <section>")
        if attrs.get("role") != "alert":
            errors.append(f'DOM element {dom_id} error_state surface missing role="alert"')
        if attrs.get("aria-label") != str(props.get("aria_label", "Error state")):
            errors.append(f"DOM element {dom_id} error_state surface aria-label does not match manifest props")
    state_motif_role = props.get("state_motif_role")
    if state_motif_role == "title":
        if tag != "h2":
            errors.append(f"manifest node {dom_id} state title must render as <h2>")
    elif state_motif_role == "description":
        if tag != "p":
            errors.append(f"manifest node {dom_id} state description must render as <p>")
    elif state_motif_role is not None and state_motif_role != "detail":
        errors.append(f"manifest node {dom_id} state_motif_role must be title, description, or detail")
    if props.get("motif_kind") == "hero" and primitive == "surface":
        if tag != "header":
            errors.append(f"manifest node {dom_id} hero surface must render as <header>")
        if attrs.get("aria-label") != str(props.get("aria_label", "Hero")):
            errors.append(f"DOM element {dom_id} hero surface aria-label does not match manifest props")
    hero_role = props.get("hero_role")
    if hero_role == "title":
        if tag != "h1":
            errors.append(f"manifest node {dom_id} hero title must render as <h1>")
    elif hero_role in {"description", "eyebrow"}:
        if tag != "p":
            errors.append(f"manifest node {dom_id} hero {hero_role} must render as <p>")
    elif hero_role is not None and hero_role != "detail":
        errors.append(f"manifest node {dom_id} hero_role must be eyebrow, title, description, or detail")
    if props.get("motif_kind") == "table" and primitive == "stack" and tag != "table":
        errors.append(f"manifest node {dom_id} table stack must render as <table>")
    if props.get("motif_kind") == "table" and primitive == "cluster" and tag != "tr":
        errors.append(f"manifest node {dom_id} table row must render as <tr>")
    table_cell_role = props.get("table_cell_role")
    if table_cell_role == "row_header":
        if tag != "th":
            errors.append(f"manifest node {dom_id} table row_header must render as <th>")
        if attrs.get("scope") != "row":
            errors.append(f'DOM element {dom_id} table row_header missing scope="row"')
    elif table_cell_role == "cell":
        if tag != "td":
            errors.append(f"manifest node {dom_id} table cell must render as <td>")
    elif table_cell_role is not None:
        errors.append(f"manifest node {dom_id} table_cell_role must be row_header or cell")


def _validate_intent_visible_text(
    errors: list[str],
    primitive: Any,
    props: dict[str, Any],
    dom_id: str,
    probe: _ArtifactDomProbe,
) -> None:
    dom_text = _collapse_artifact_text(" ".join(probe.text_by_node_id.get(dom_id, [])))
    if primitive in TEXT_PROP_PRIMITIVES and isinstance(props.get("state_text_id"), str):
        if dom_text != _collapse_artifact_text(str(props.get("state_text_initial") or "")):
            errors.append(f"DOM element {dom_id} text does not match manifest props")
    elif primitive in TEXT_PROP_PRIMITIVES and "text" in props:
        if dom_text != str(props["text"]):
            errors.append(f"DOM element {dom_id} text does not match manifest props")
    elif primitive == "button" and "label" in props:
        if dom_text != str(props["label"]):
            errors.append(f"DOM element {dom_id} text does not match manifest props")


def _validate_image_like_attrs(
    errors: list[str],
    attrs: dict[str, str],
    props: dict[str, Any],
    dom_id: str,
    primitive: str,
    label_key: str,
) -> None:
    if attrs.get("role") != "img":
        errors.append(f'DOM element {dom_id} {primitive} missing role="img"')
    label = str(props.get(label_key, primitive.replace("_", " ")))
    if attrs.get("aria-label") != label:
        errors.append(f"DOM element {dom_id} {primitive} aria-label does not match manifest props")


def _compare_json_attr(
    errors: list[str],
    attrs: dict[str, str],
    props: dict[str, Any],
    dom_id: str,
    attr_name: str,
    props_key: str,
) -> None:
    attr_value = attrs.get(attr_name)
    if attr_value is None:
        errors.append(f"DOM element {dom_id} missing {attr_name}")
        return
    try:
        parsed = json.loads(attr_value)
    except json.JSONDecodeError:
        errors.append(f"DOM element {dom_id} has invalid {attr_name} JSON")
        return
    if parsed != props.get(props_key, []):
        errors.append(f"DOM element {dom_id} {attr_name} does not match manifest props")
