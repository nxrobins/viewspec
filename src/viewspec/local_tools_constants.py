from __future__ import annotations

from viewspec.raw_html import RAW_HTML_POLICY_VERSION
import re

MCP_RESULT_SCHEMA_VERSION = 1

INTENT_BUNDLE_POLICY_VERSION = "viewspec-intent-bundle@1"

SEMANTIC_DIGEST_VERSION = "semantic_digest.v1"

SEMANTIC_DIGEST_MAX_PROJECTION_BYTES = 128 * 1024

HASH_RE = re.compile(r"^[0-9a-f]{64}$")

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

CANONICAL_CONTENT_REF_RE = re.compile(r"^node:[A-Za-z0-9_.-]+(?:#(?:attr|slot|edge):[A-Za-z0-9_.-]+(?:\[[0-9]+\])?)?$")

VIEWSPEC_INTENT_REF_RE = re.compile(r"^viewspec:(view|region|binding|group|motif|style|action):[A-Za-z0-9_.-]+$")

ACTION_TARGET_REF_RE = re.compile(r"^(region|binding|motif|view):[A-Za-z0-9_.-]+$")

ABSOLUTE_PATH_ARG_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|[\\/]{1,2})")

DIAGNOSTIC_SEVERITIES = {"error", "info", "warning"}

MCP_RESERVED_RESULT_KEYS = {
    "diagnostics",
    "errors",
    "external_refs",
    "metadata",
    "next_actions",
    "ok",
    "paths",
    "schema_version",
    "summary",
}

EXTERNAL_REF_POLICIES = {
    ("image", "src", "inert_placeholder"),
    ("link", "href", "user_click"),
}

KNOWN_EMITTERS = {"html_tailwind", "react_tailwind_tsx", "react_tsx"}

STATEFUL_COLLECTION_ACTION_KINDS = {"search", "filter", "sort", "paginate", "bulk_action"}

EMITTER_ARTIFACT_FILES = {
    "html_tailwind": "index.html",
    "react_tailwind_tsx": "ViewSpecView.tsx",
    "react_tsx": "ViewSpecView.tsx",
}

REACT_TSX_REQUIRED_MARKERS_BY_EMITTER = {
    "react_tsx": {
        '"use client";': "ViewSpecView.tsx missing client component directive",
        'source: "viewspec-react-tsx"': "ViewSpecView.tsx missing React action source marker",
        "export function ViewSpecView": "ViewSpecView.tsx missing ViewSpecView export",
        "const collectPayloadValues = (payloadBindings: string[]): Record<string, unknown> =>": (
            "ViewSpecView.tsx missing action payload collection"
        ),
    },
    "react_tailwind_tsx": {
        '"use client";': "ViewSpecView.tsx missing client component directive",
        'source: "viewspec-react-tailwind-tsx"': "ViewSpecView.tsx missing React Tailwind action source marker",
        "export function ViewSpecView": "ViewSpecView.tsx missing ViewSpecView export",
        "const collectPayloadValues = (payloadBindings: string[]): Record<string, unknown> =>": (
            "ViewSpecView.tsx missing action payload collection"
        ),
    },
}

REACT_TSX_REQUIRED_MARKERS = {
    '"use client";': "ViewSpecView.tsx missing client component directive",
    'source: "viewspec-react-tsx"': "ViewSpecView.tsx missing React action source marker",
    "export function ViewSpecView": "ViewSpecView.tsx missing ViewSpecView export",
    "const collectPayloadValues = (payloadBindings: string[]): Record<string, unknown> =>": (
        "ViewSpecView.tsx missing action payload collection"
    ),
}

REACT_TSX_ACTION_REQUIRED_MARKERS = {
    "const payloadValues = collectPayloadValues(payloadBindings);": "ViewSpecView.tsx missing action payload dispatch",
    "assertPayloadBounds(": "ViewSpecView.tsx missing collection action payload bounds",
}

REACT_TSX_FORBIDDEN_SURFACES = (
    (re.compile(r"\bdangerouslySetInnerHTML\b"), "ViewSpecView.tsx contains dangerouslySetInnerHTML"),
    (re.compile(r"\bfetch\s*\("), "ViewSpecView.tsx contains fetch()"),
    (re.compile(r"\bXMLHttpRequest\b"), "ViewSpecView.tsx contains XMLHttpRequest"),
    (re.compile(r"\bWebSocket\b"), "ViewSpecView.tsx contains WebSocket"),
    (re.compile(r"\bEventSource\b"), "ViewSpecView.tsx contains EventSource"),
    (re.compile(r"\bnavigator\.sendBeacon\b"), "ViewSpecView.tsx contains navigator.sendBeacon"),
    (re.compile(r"\bimport\s*\("), "ViewSpecView.tsx contains dynamic import"),
    (re.compile(r"\beval\s*\("), "ViewSpecView.tsx contains eval()"),
    (re.compile(r"\bnew\s+Function\s*\("), "ViewSpecView.tsx contains new Function()"),
    (re.compile(r"(?i)<script\b"), "ViewSpecView.tsx contains a script tag"),
)

REMOTE_AUTOFETCH_ATTRS = {"action", "background", "formaction", "manifest", "poster", "src", "srcset"}

REMOTE_HREF_AUTOFETCH_TAGS = {"image", "use", "base"}

ACTIVE_OR_AUTOFETCH_TAGS = {"embed", "iframe", "link", "object"}

ACTIVE_STRUCTURAL_TAGS = {"form"}

VOID_HTML_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

TEXT_PROP_PRIMITIVES = {"badge", "button", "label", "text", "value"}

SEMANTIC_DIGEST_KEYS = {"version", "manifest_projection", "source_projection", "digest"}

SEMANTIC_PROJECTION_KEYS = {"version", "node_order", "action_order", "diagnostic_codes", "nodes"}

SEMANTIC_NODE_KEYS = {
    "accessibility_label",
    "action",
    "binding_id",
    "dom_id",
    "ir_id",
    "primitive",
    "tag",
    "visible_text",
}

SEMANTIC_ACTION_KEYS = {"id", "kind", "payload_bindings", "target_ref"}

EXPECTED_MANIFEST_ENVELOPES = {
    "raw_html_compile": {
        "command": "compile_html",
        "policy_version": RAW_HTML_POLICY_VERSION,
        "decompilation": "not_claimed",
    },
    "intent_bundle_compile": {
        "command": "compile",
        "policy_version": INTENT_BUNDLE_POLICY_VERSION,
        "decompilation": "not_applicable",
    },
}

STARTER_DESIGN = """---
name: Agent Output
colors:
  primary: "#111827"
  secondary: "#4B5563"
  surface: "#FFFFFF"
  background: "#F8FAFC"
  accent: "#0F766E"
typography:
  body:
    fontFamily: Inter
    fontSize: 16px
    lineHeight: 1.6
  heading:
    fontFamily: Inter
    fontWeight: 760
    letterSpacing: -0.02em
spacing:
  md: 16px
rounded:
  md: 10px
---
"""
