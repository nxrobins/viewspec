"""
ViewSpec core types and address grammar.

This module defines the public type system for ViewSpec:
- Semantic substrate (SemanticNode, SemanticSubstrate)
- View specification (ViewSpec, RegionSpec, BindingSpec, GroupSpec, MotifSpec, StyleSpec, ActionIntent)
- Composition IR (CompositionIR, IRNode, Provenance)
- Compiler output (CompilerResult, CompilerDiagnostic, ASTBundle, IntentBundle)
- Canonical address grammar and resolution utilities

All types support protobuf serialization and JSON round-tripping.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from google.protobuf import json_format, struct_pb2

from viewspec.schema import viewspec_pb2 as pb2


# ---------------------------------------------------------------------------
# Address grammar
# ---------------------------------------------------------------------------

CANONICAL_ADDRESS_RE = re.compile(
    r"^node:(?P<node_id>[a-zA-Z0-9_.-]+)"
    r"(?:(?P<suffix>"
    r"#attr:(?P<attr>[a-zA-Z0-9_.-]+)"
    r"|#slot:(?P<slot>[a-zA-Z0-9_.-]+)(?:\[(?P<slot_index>\d+)])?"
    r"|#edge:(?P<edge>[a-zA-Z0-9_.-]+)"
    r"))?$"
)

VIEWSPEC_REF_RE = re.compile(
    r"^viewspec:(?P<kind>view|region|binding|group|motif|style|action):(?P<ref_id>[a-zA-Z0-9_.-]+)$"
)

TARGET_REF_RE = re.compile(
    r"^(?P<kind>region|binding|motif|view):(?P<ref_id>[a-zA-Z0-9_.-]+)$"
)

# ---------------------------------------------------------------------------
# Primitive sets
# ---------------------------------------------------------------------------

CONTAINER_PRIMITIVES = {"root", "stack", "grid", "cluster", "surface"}
CONTENT_PRIMITIVES = {"text", "label", "value", "badge", "image_slot", "rule", "svg", "button", "error_boundary"}
VISIBLE_TEXT_PRIMITIVES = {"text", "label", "value", "badge", "button", "error_boundary"}
LAYOUT_PRIMITIVES = {"stack", "grid", "cluster"}

PRESENT_AS_TO_PRIMITIVE = {
    "text": "text",
    "label": "label",
    "value": "value",
    "badge": "badge",
    "rich_text": "text",
    "image_slot": "image_slot",
    "rule": "rule",
}

DEFAULT_STYLE_TOKEN_VALUES = {
    "emphasis.low": "font-weight: 500;",
    "emphasis.medium": "font-weight: 600;",
    "emphasis.high": "font-weight: 700; letter-spacing: -0.02em;",
    "density.compact": "gap: 0.4rem; padding: 0.4rem 0.55rem;",
    "density.regular": "gap: 0.7rem; padding: 0.6rem 0.8rem;",
    "density.airy": "gap: 1rem; padding: 0.85rem 1rem;",
    "tone.neutral": "color: #1f2937;",
    "tone.muted": "color: #5b6472;",
    "tone.accent": "color: #0f766e;",
    "surface.none": "background: transparent; border: 0;",
    "surface.subtle": "background: #f3f4f6; border: 1px solid #d1d5db; border-radius: 12px;",
    "surface.strong": "background: #e2e8f0; border: 1px solid #94a3b8; border-radius: 14px;",
    "align.start": "align-items: start; text-align: left;",
    "align.center": "align-items: center; text-align: center;",
    "align.end": "align-items: end; text-align: right;",
}

# ---------------------------------------------------------------------------
# JSON type alias
# ---------------------------------------------------------------------------

JsonDict = dict[str, Any]

# ---------------------------------------------------------------------------
# Proto helpers
# ---------------------------------------------------------------------------


def _proto_to_json_dict(message: Any) -> JsonDict:
    return json_format.MessageToDict(
        message,
        preserving_proto_field_name=True,
        always_print_fields_with_no_presence=True,
    )


def _json_to_proto(payload: Any, message: Any, type_name: str) -> Any:
    data = _coerce_json_mapping(payload, type_name)
    return json_format.ParseDict(data, message, ignore_unknown_fields=False)


def _coerce_json_mapping(payload: Any, type_name: str) -> JsonDict:
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise TypeError(f"{type_name}.from_json expects a JSON object")
    return payload


def _to_struct(value: dict[str, Any] | None) -> struct_pb2.Struct:
    message = struct_pb2.Struct()
    message.update(dict(value or {}))
    return message


def _from_struct(value: struct_pb2.Struct) -> JsonDict:
    return dict(json_format.MessageToDict(value, preserving_proto_field_name=True))


def _to_value(value: Any) -> struct_pb2.Value:
    return json_format.ParseDict(value, struct_pb2.Value())


def _from_value(value: struct_pb2.Value) -> Any:
    return json_format.MessageToDict(value, preserving_proto_field_name=True)


def _optional_string(value: str) -> str | None:
    return value or None


# ---------------------------------------------------------------------------
# Semantic Substrate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticNode:
    """A node in the semantic substrate — the raw data entity."""

    id: str
    kind: str
    attrs: dict[str, Any] = field(default_factory=dict)
    slots: dict[str, list[Any]] = field(default_factory=dict)
    edges: dict[str, list[str]] = field(default_factory=dict)

    def to_proto(self) -> pb2.SemanticNode:
        message = pb2.SemanticNode(id=self.id, kind=self.kind, attrs=_to_struct(self.attrs))
        for key, values in self.slots.items():
            message.slots[key].values.extend(_to_value(v) for v in values)
        for key, values in self.edges.items():
            message.edges[key].values.extend(str(v) for v in values)
        return message

    @classmethod
    def from_proto(cls, message: pb2.SemanticNode) -> SemanticNode:
        return cls(
            id=message.id,
            kind=message.kind,
            attrs=_from_struct(message.attrs),
            slots={k: [_from_value(v) for v in vals.values] for k, vals in message.slots.items()},
            edges={k: list(vals.values) for k, vals in message.edges.items()},
        )

    def to_json(self) -> JsonDict:
        return _proto_to_json_dict(self.to_proto())

    @classmethod
    def from_json(cls, payload: Any) -> SemanticNode:
        return cls.from_proto(_json_to_proto(payload, pb2.SemanticNode(), cls.__name__))


@dataclass(frozen=True)
class SemanticSubstrate:
    """The full semantic graph — all data entities and their relationships."""

    id: str
    root_id: str
    nodes: dict[str, SemanticNode]

    def to_proto(self) -> pb2.SemanticSubstrate:
        message = pb2.SemanticSubstrate(id=self.id, root_id=self.root_id)
        for node_id, node in self.nodes.items():
            message.nodes[node_id].CopyFrom(node.to_proto())
        return message

    @classmethod
    def from_proto(cls, message: pb2.SemanticSubstrate) -> SemanticSubstrate:
        return cls(
            id=message.id,
            root_id=message.root_id,
            nodes={nid: SemanticNode.from_proto(n) for nid, n in message.nodes.items()},
        )

    def to_json(self) -> JsonDict:
        return _proto_to_json_dict(self.to_proto())

    @classmethod
    def from_json(cls, payload: Any) -> SemanticSubstrate:
        return cls.from_proto(_json_to_proto(payload, pb2.SemanticSubstrate(), cls.__name__))


# ---------------------------------------------------------------------------
# ViewSpec components
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegionSpec:
    """A spatial region in the view — where data can be placed."""

    id: str
    parent_region: str | None
    role: str
    layout: str
    min_children: int
    max_children: int | None

    def to_proto(self) -> pb2.Region:
        return pb2.Region(
            id=self.id,
            parent_region=self.parent_region or "",
            role=self.role,
            layout=self.layout,
            min_children=self.min_children,
            max_children=_to_value(self.max_children),
        )

    @classmethod
    def from_proto(cls, message: pb2.Region) -> RegionSpec:
        max_children = _from_value(message.max_children)
        return cls(
            id=message.id,
            parent_region=_optional_string(message.parent_region),
            role=message.role,
            layout=message.layout,
            min_children=int(message.min_children),
            max_children=None if max_children is None else int(max_children),
        )

    def to_json(self) -> JsonDict:
        return _proto_to_json_dict(self.to_proto())

    @classmethod
    def from_json(cls, payload: Any) -> RegionSpec:
        data = _coerce_json_mapping(payload, cls.__name__)
        if data.get("parent_region") is None:
            data = {**data, "parent_region": ""}
        return cls.from_proto(_json_to_proto(data, pb2.Region(), cls.__name__))


@dataclass(frozen=True)
class BindingSpec:
    """A binding from a semantic address to a visual region."""

    id: str
    address: str
    target_region: str
    present_as: str
    cardinality: str

    def to_proto(self) -> pb2.Binding:
        return pb2.Binding(
            id=self.id,
            address=self.address,
            target_region=self.target_region,
            present_as=self.present_as,
            cardinality=self.cardinality,
        )

    @classmethod
    def from_proto(cls, message: pb2.Binding) -> BindingSpec:
        return cls(
            id=message.id,
            address=message.address,
            target_region=message.target_region,
            present_as=message.present_as,
            cardinality=message.cardinality,
        )

    def to_json(self) -> JsonDict:
        return _proto_to_json_dict(self.to_proto())

    @classmethod
    def from_json(cls, payload: Any) -> BindingSpec:
        return cls.from_proto(_json_to_proto(payload, pb2.Binding(), cls.__name__))


@dataclass(frozen=True)
class GroupSpec:
    """A semantic grouping of bindings."""

    id: str
    kind: str
    members: list[str]
    target_region: str | None

    def to_proto(self) -> pb2.Group:
        return pb2.Group(id=self.id, kind=self.kind, members=list(self.members), target_region=self.target_region or "")

    @classmethod
    def from_proto(cls, message: pb2.Group) -> GroupSpec:
        return cls(id=message.id, kind=message.kind, members=list(message.members), target_region=_optional_string(message.target_region))

    def to_json(self) -> JsonDict:
        return _proto_to_json_dict(self.to_proto())

    @classmethod
    def from_json(cls, payload: Any) -> GroupSpec:
        data = _coerce_json_mapping(payload, cls.__name__)
        if data.get("target_region") is None:
            data = {**data, "target_region": ""}
        return cls.from_proto(_json_to_proto(data, pb2.Group(), cls.__name__))


@dataclass(frozen=True)
class MotifSpec:
    """A visual motif hint — how the compiler should structure the data."""

    id: str
    kind: str
    region: str
    members: list[str]

    def to_proto(self) -> pb2.Motif:
        return pb2.Motif(id=self.id, kind=self.kind, region=self.region, members=list(self.members))

    @classmethod
    def from_proto(cls, message: pb2.Motif) -> MotifSpec:
        return cls(id=message.id, kind=message.kind, region=message.region, members=list(message.members))

    def to_json(self) -> JsonDict:
        return _proto_to_json_dict(self.to_proto())

    @classmethod
    def from_json(cls, payload: Any) -> MotifSpec:
        return cls.from_proto(_json_to_proto(payload, pb2.Motif(), cls.__name__))


@dataclass(frozen=True)
class StyleSpec:
    """A style token assignment."""

    id: str
    target: str
    token: str

    def to_proto(self) -> pb2.Style:
        return pb2.Style(id=self.id, target=self.target, token=self.token)

    @classmethod
    def from_proto(cls, message: pb2.Style) -> StyleSpec:
        return cls(id=message.id, target=message.target, token=message.token)

    def to_json(self) -> JsonDict:
        return _proto_to_json_dict(self.to_proto())

    @classmethod
    def from_json(cls, payload: Any) -> StyleSpec:
        return cls.from_proto(_json_to_proto(payload, pb2.Style(), cls.__name__))


@dataclass(frozen=True)
class ActionIntent:
    """An interactive action bound to a region."""

    id: str
    kind: str
    label: str
    target_region: str
    target_ref: str | None = None
    payload_bindings: list[str] = field(default_factory=list)

    def to_proto(self) -> pb2.ActionIntent:
        return pb2.ActionIntent(
            id=self.id,
            kind=self.kind,
            label=self.label,
            target_region=self.target_region,
            target_ref=self.target_ref or "",
            payload_bindings=list(self.payload_bindings),
        )

    @classmethod
    def from_proto(cls, message: pb2.ActionIntent) -> ActionIntent:
        return cls(
            id=message.id,
            kind=message.kind,
            label=message.label,
            target_region=message.target_region,
            target_ref=_optional_string(message.target_ref),
            payload_bindings=list(message.payload_bindings),
        )

    def to_json(self) -> JsonDict:
        return _proto_to_json_dict(self.to_proto())

    @classmethod
    def from_json(cls, payload: Any) -> ActionIntent:
        data = _coerce_json_mapping(payload, cls.__name__)
        if data.get("target_ref") is None:
            data = {**data, "target_ref": ""}
        return cls.from_proto(_json_to_proto(data, pb2.ActionIntent(), cls.__name__))


# ---------------------------------------------------------------------------
# ViewSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ViewSpec:
    """A complete view specification — the declarative description of UI intent."""

    id: str
    substrate_id: str
    complexity_tier: int
    root_region: str
    regions: list[RegionSpec]
    bindings: list[BindingSpec]
    groups: list[GroupSpec]
    motifs: list[MotifSpec]
    styles: list[StyleSpec]
    actions: list[ActionIntent] = field(default_factory=list)

    def to_proto(self) -> pb2.ViewSpec:
        return pb2.ViewSpec(
            id=self.id,
            substrate_id=self.substrate_id,
            complexity_tier=self.complexity_tier,
            root_region=self.root_region,
            regions=[r.to_proto() for r in self.regions],
            bindings=[b.to_proto() for b in self.bindings],
            groups=[g.to_proto() for g in self.groups],
            motifs=[m.to_proto() for m in self.motifs],
            styles=[s.to_proto() for s in self.styles],
            actions=[a.to_proto() for a in self.actions],
        )

    @classmethod
    def from_proto(cls, message: pb2.ViewSpec) -> ViewSpec:
        return cls(
            id=message.id,
            substrate_id=message.substrate_id,
            complexity_tier=int(message.complexity_tier),
            root_region=message.root_region,
            regions=[RegionSpec.from_proto(r) for r in message.regions],
            bindings=[BindingSpec.from_proto(b) for b in message.bindings],
            groups=[GroupSpec.from_proto(g) for g in message.groups],
            motifs=[MotifSpec.from_proto(m) for m in message.motifs],
            styles=[StyleSpec.from_proto(s) for s in message.styles],
            actions=[ActionIntent.from_proto(a) for a in message.actions],
        )

    def to_json(self) -> JsonDict:
        return _proto_to_json_dict(self.to_proto())

    @classmethod
    def from_json(cls, payload: Any) -> ViewSpec:
        return cls.from_proto(_json_to_proto(payload, pb2.ViewSpec(), cls.__name__))


# ---------------------------------------------------------------------------
# Composition IR (output types)
# ---------------------------------------------------------------------------


@dataclass
class Provenance:
    """Tracks which semantic addresses and intent refs produced an IR node."""

    content_refs: list[str] = field(default_factory=list)
    intent_refs: list[str] = field(default_factory=list)

    def to_proto(self) -> pb2.Provenance:
        return pb2.Provenance(content_refs=list(self.content_refs), intent_refs=list(self.intent_refs))

    @classmethod
    def from_proto(cls, message: pb2.Provenance) -> Provenance:
        return cls(content_refs=list(message.content_refs), intent_refs=list(message.intent_refs))

    def to_json(self) -> JsonDict:
        return _proto_to_json_dict(self.to_proto())

    @classmethod
    def from_json(cls, payload: Any) -> Provenance:
        return cls.from_proto(_json_to_proto(payload, pb2.Provenance(), cls.__name__))


@dataclass
class IRNode:
    """A node in the composition IR tree — the compiler's output."""

    id: str
    primitive: str
    props: dict[str, Any] = field(default_factory=dict)
    children: list[IRNode] = field(default_factory=list)
    provenance: Provenance = field(default_factory=Provenance)
    style_tokens: list[str] = field(default_factory=list)

    def to_proto(self) -> pb2.IRNode:
        return pb2.IRNode(
            id=self.id,
            primitive=self.primitive,
            props=_to_struct(self.props),
            children=[c.to_proto() for c in self.children],
            provenance=self.provenance.to_proto(),
            style_tokens=list(self.style_tokens),
        )

    @classmethod
    def from_proto(cls, message: pb2.IRNode) -> IRNode:
        return cls(
            id=message.id,
            primitive=message.primitive,
            props=_from_struct(message.props),
            children=[cls.from_proto(c) for c in message.children],
            provenance=Provenance.from_proto(message.provenance),
            style_tokens=list(message.style_tokens),
        )

    def to_json(self) -> JsonDict:
        return _proto_to_json_dict(self.to_proto())

    @classmethod
    def from_json(cls, payload: Any) -> IRNode:
        return cls.from_proto(_json_to_proto(payload, pb2.IRNode(), cls.__name__))


@dataclass
class CompositionIR:
    """The root of a composition IR tree."""

    root: IRNode

    def to_proto(self) -> pb2.CompositionIR:
        return pb2.CompositionIR(root=self.root.to_proto())

    @classmethod
    def from_proto(cls, message: pb2.CompositionIR) -> CompositionIR:
        return cls(root=IRNode.from_proto(message.root))

    def to_json(self) -> JsonDict:
        return _proto_to_json_dict(self.to_proto())

    @classmethod
    def from_json(cls, payload: Any) -> CompositionIR:
        return cls.from_proto(_json_to_proto(payload, pb2.CompositionIR(), cls.__name__))


@dataclass
class CompilerDiagnostic:
    """A diagnostic message from the compiler."""

    severity: str
    code: str
    message: str
    intent_ref: str | None = None
    content_ref: str | None = None
    region_id: str | None = None
    node_id: str | None = None

    def to_proto(self) -> pb2.CompilerDiagnostic:
        return pb2.CompilerDiagnostic(
            severity=self.severity,
            code=self.code,
            message=self.message,
            intent_ref=self.intent_ref or "",
            content_ref=self.content_ref or "",
            region_id=self.region_id or "",
            node_id=self.node_id or "",
        )

    @classmethod
    def from_proto(cls, message: pb2.CompilerDiagnostic) -> CompilerDiagnostic:
        return cls(
            severity=message.severity,
            code=message.code,
            message=message.message,
            intent_ref=_optional_string(message.intent_ref),
            content_ref=_optional_string(message.content_ref),
            region_id=_optional_string(message.region_id),
            node_id=_optional_string(message.node_id),
        )

    def to_json(self) -> JsonDict:
        return _proto_to_json_dict(self.to_proto())

    @classmethod
    def from_json(cls, payload: Any) -> CompilerDiagnostic:
        data = _coerce_json_mapping(payload, cls.__name__)
        for key in ("intent_ref", "content_ref", "region_id", "node_id"):
            if data.get(key) is None:
                data = {**data, key: ""}
        return cls.from_proto(_json_to_proto(data, pb2.CompilerDiagnostic(), cls.__name__))


@dataclass
class CompilerResult:
    """The full compiler output — IR tree plus diagnostics."""

    root: CompositionIR
    diagnostics: list[CompilerDiagnostic] = field(default_factory=list)

    def to_proto(self) -> pb2.CompilerResult:
        return pb2.CompilerResult(
            root=self.root.to_proto(),
            diagnostics=[d.to_proto() for d in self.diagnostics],
        )

    @classmethod
    def from_proto(cls, message: pb2.CompilerResult) -> CompilerResult:
        return cls(
            root=CompositionIR.from_proto(message.root),
            diagnostics=[CompilerDiagnostic.from_proto(d) for d in message.diagnostics],
        )

    def to_json(self) -> JsonDict:
        return _proto_to_json_dict(self.to_proto())

    @classmethod
    def from_json(cls, payload: Any) -> CompilerResult:
        return cls.from_proto(_json_to_proto(payload, pb2.CompilerResult(), cls.__name__))


@dataclass(frozen=True)
class IntentBundle:
    """A substrate + view spec pair — the complete input to the compiler."""

    substrate: SemanticSubstrate
    view_spec: ViewSpec

    def to_proto(self) -> pb2.IntentBundle:
        return pb2.IntentBundle(substrate=self.substrate.to_proto(), view_spec=self.view_spec.to_proto())

    @classmethod
    def from_proto(cls, message: pb2.IntentBundle) -> IntentBundle:
        return cls(
            substrate=SemanticSubstrate.from_proto(message.substrate),
            view_spec=ViewSpec.from_proto(message.view_spec),
        )

    def to_json(self) -> JsonDict:
        return _proto_to_json_dict(self.to_proto())

    @classmethod
    def from_json(cls, payload: Any) -> IntentBundle:
        return cls.from_proto(_json_to_proto(payload, pb2.IntentBundle(), cls.__name__))


@dataclass(frozen=True)
class DesignRequest:
    """Opaque DESIGN.md payload for the hosted compiler."""

    content: str
    format: str = "design.md"
    lint: bool = True

    def to_json(self) -> JsonDict:
        return {
            "format": self.format,
            "content": self.content,
            "lint": self.lint,
        }

    @classmethod
    def from_json(cls, payload: Any) -> DesignRequest:
        data = _coerce_json_mapping(payload, cls.__name__)
        lint = data.get("lint", True)
        if not isinstance(lint, bool):
            raise TypeError("DesignRequest.lint must be a boolean")
        return cls(
            content=str(data.get("content", "")),
            format=str(data.get("format", "design.md")),
            lint=lint,
        )


@dataclass(frozen=True)
class CompileRequestPayload:
    """Hosted compiler request payload with optional DESIGN.md context."""

    bundle: IntentBundle
    design: DesignRequest | None = None

    def to_json(self) -> JsonDict:
        data = self.bundle.to_json()
        if self.design is not None:
            data["design"] = self.design.to_json()
        return data


DesignFindingSeverity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class DesignFinding:
    """One DESIGN.md lint or ingestion finding returned by the hosted API."""

    severity: DesignFindingSeverity
    code: str
    path: str
    message: str

    def to_json(self) -> JsonDict:
        return {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }

    @classmethod
    def from_json(cls, payload: Any) -> DesignFinding:
        data = _coerce_json_mapping(payload, cls.__name__)
        return cls(
            severity=data.get("severity", "info"),
            code=str(data.get("code", "")),
            path=str(data.get("path", "")),
            message=str(data.get("message", "")),
        )


def _int_summary(value: Any) -> dict[str, int]:
    summary = {"errors": 0, "warnings": 0, "info": 0}
    if not isinstance(value, dict):
        return summary
    for key in summary:
        try:
            summary[key] = int(value.get(key, 0) or 0)
        except (TypeError, ValueError):
            summary[key] = 0
    return summary


def _string_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


@dataclass(frozen=True)
class DesignMetadata:
    """DESIGN.md validation and token-application metadata returned by the hosted API."""

    name: str | None = None
    lint_summary: dict[str, int] = field(default_factory=lambda: {"errors": 0, "warnings": 0, "info": 0})
    findings: list[DesignFinding] = field(default_factory=list)
    applied_tokens: dict[str, str] = field(default_factory=dict)
    inferred_hints: dict[str, str] = field(default_factory=dict)
    ignored_tokens: list[str] = field(default_factory=list)
    dropped_tokens: list[str] = field(default_factory=list)

    def to_json(self) -> JsonDict:
        data: JsonDict = {
            "lint_summary": dict(self.lint_summary),
            "findings": [finding.to_json() for finding in self.findings],
            "applied_tokens": dict(self.applied_tokens),
            "inferred_hints": dict(self.inferred_hints),
            "ignored_tokens": list(self.ignored_tokens),
            "dropped_tokens": list(self.dropped_tokens),
        }
        if self.name is not None:
            data["name"] = self.name
        return data

    @classmethod
    def from_json(cls, payload: Any) -> DesignMetadata:
        data = _coerce_json_mapping(payload, cls.__name__)
        findings = [
            DesignFinding.from_json(finding)
            for finding in data.get("findings", [])
            if isinstance(finding, dict)
        ]
        name = data.get("name")
        return cls(
            name=str(name) if name is not None else None,
            lint_summary=_int_summary(data.get("lint_summary")),
            findings=findings,
            applied_tokens=_string_dict(data.get("applied_tokens")),
            inferred_hints=_string_dict(data.get("inferred_hints")),
            ignored_tokens=_string_list(data.get("ignored_tokens")),
            dropped_tokens=_string_list(data.get("dropped_tokens")),
        )


@dataclass(frozen=True)
class CompileResponseMeta:
    """Hosted compiler metadata with typed DESIGN.md details and raw passthrough data."""

    design: DesignMetadata | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> JsonDict:
        data = dict(self.raw)
        if self.design is not None:
            data["design"] = self.design.to_json()
        return data

    @classmethod
    def from_json(cls, payload: Any) -> CompileResponseMeta:
        if payload is None:
            payload = {}
        data = _coerce_json_mapping(payload, cls.__name__)
        design_data = data.get("design")
        design = DesignMetadata.from_json(design_data) if isinstance(design_data, dict) else None
        return cls(design=design, raw=dict(data))


@dataclass(frozen=True)
class ASTBundle:
    """The compiled output bundle — compiler result + style values + title."""

    result: CompilerResult
    style_values: dict[str, str] = field(default_factory=dict)
    title: str = "ViewSpec Artifact"

    def to_proto(self) -> pb2.ASTBundle:
        return pb2.ASTBundle(
            result=self.result.to_proto(),
            style_values=dict(self.style_values),
            title=self.title,
        )

    @classmethod
    def from_proto(cls, message: pb2.ASTBundle) -> ASTBundle:
        return cls(
            result=CompilerResult.from_proto(message.result),
            style_values=dict(message.style_values),
            title=message.title or "ViewSpec Artifact",
        )

    def to_json(self) -> JsonDict:
        return _proto_to_json_dict(self.to_proto())

    @classmethod
    def from_json(cls, payload: Any) -> ASTBundle:
        data = _coerce_json_mapping(payload, cls.__name__)
        if "result" not in data and "root" in data:
            data = {
                "result": {"root": data["root"], "diagnostics": data.get("diagnostics", [])},
                "style_values": data.get("style_values", {}),
                "title": data.get("title", "ViewSpec Artifact"),
            }
        return cls.from_proto(_json_to_proto(data, pb2.ASTBundle(), cls.__name__))


@dataclass(frozen=True)
class CompileResponse:
    """Full hosted compiler response with AST, metadata, derivations, quota, and raw data."""

    ast: ASTBundle
    meta: CompileResponseMeta = field(default_factory=CompileResponseMeta)
    derivations: list[dict[str, Any]] = field(default_factory=list)
    quota: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> JsonDict:
        data = dict(self.raw)
        data["ast"] = self.ast.to_json()
        data["meta"] = self.meta.to_json()
        if self.derivations:
            data["derivations"] = [dict(item) for item in self.derivations]
        if self.quota:
            data["quota"] = dict(self.quota)
        return data

    @classmethod
    def from_json(cls, payload: Any) -> CompileResponse:
        data = _coerce_json_mapping(payload, cls.__name__)
        derivations = data.get("derivations", [])
        quota = data.get("quota", {})
        return cls(
            ast=ASTBundle.from_json(data["ast"]),
            meta=CompileResponseMeta.from_json(data.get("meta", {})),
            derivations=[dict(item) for item in derivations if isinstance(item, dict)] if isinstance(derivations, list) else [],
            quota=dict(quota) if isinstance(quota, dict) else {},
            raw=dict(data),
        )


# ---------------------------------------------------------------------------
# Address utilities
# ---------------------------------------------------------------------------


def parse_canonical_address(address: str) -> dict[str, Any]:
    """Parse a canonical address string into its components."""
    match = CANONICAL_ADDRESS_RE.match(address)
    if not match:
        raise ValueError(f"Invalid canonical address: {address}")
    parts = match.groupdict()
    return {
        "node_id": parts["node_id"],
        "attr": parts["attr"],
        "slot": parts["slot"],
        "slot_index": int(parts["slot_index"]) if parts["slot_index"] is not None else None,
        "edge": parts["edge"],
    }


def parse_viewspec_ref(ref: str) -> tuple[str, str]:
    """Parse a viewspec reference string."""
    match = VIEWSPEC_REF_RE.match(ref)
    if not match:
        raise ValueError(f"Invalid ViewSpec ref: {ref}")
    return match.group("kind"), match.group("ref_id")


def parse_target_ref(ref: str) -> tuple[str, str]:
    """Parse a target reference string."""
    match = TARGET_REF_RE.match(ref)
    if not match:
        raise ValueError(f"Invalid target ref: {ref}")
    return match.group("kind"), match.group("ref_id")


def build_address_index(substrate: SemanticSubstrate) -> dict[str, Any]:
    """Build an index mapping canonical addresses to resolved values."""
    index: dict[str, Any] = {}
    for node in substrate.nodes.values():
        index[f"node:{node.id}"] = {"kind": node.kind, "id": node.id}
        for attr_name, attr_value in node.attrs.items():
            index[f"node:{node.id}#attr:{attr_name}"] = attr_value
        for slot_name, values in node.slots.items():
            index[f"node:{node.id}#slot:{slot_name}"] = list(values)
            for idx, value in enumerate(values):
                index[f"node:{node.id}#slot:{slot_name}[{idx}]"] = value
        for edge_name, target_ids in node.edges.items():
            index[f"node:{node.id}#edge:{edge_name}"] = list(target_ids)
    return index


def resolve_address(address: str, address_index: dict[str, Any]) -> Any:
    """Resolve a canonical address against an address index."""
    if address not in address_index:
        raise KeyError(f"Address not found: {address}")
    return address_index[address]


def normalize_compiler_result(value: CompositionIR | CompilerResult) -> CompilerResult:
    """Normalize a compiler output to CompilerResult."""
    if isinstance(value, CompilerResult):
        if not isinstance(value.root, CompositionIR):
            raise TypeError("CompilerResult.root must be a CompositionIR")
        return value
    if isinstance(value, CompositionIR):
        return CompilerResult(root=value, diagnostics=[])
    raise TypeError("Expected CompositionIR or CompilerResult")
