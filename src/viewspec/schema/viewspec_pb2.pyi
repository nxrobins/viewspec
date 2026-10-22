from google.protobuf import struct_pb2 as _struct_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ValueList(_message.Message):
    __slots__ = ("values",)
    VALUES_FIELD_NUMBER: _ClassVar[int]
    values: _containers.RepeatedCompositeFieldContainer[_struct_pb2.Value]
    def __init__(self, values: _Optional[_Iterable[_Union[_struct_pb2.Value, _Mapping]]] = ...) -> None: ...

class StringList(_message.Message):
    __slots__ = ("values",)
    VALUES_FIELD_NUMBER: _ClassVar[int]
    values: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, values: _Optional[_Iterable[str]] = ...) -> None: ...

class SemanticNode(_message.Message):
    __slots__ = ("id", "kind", "attrs", "slots", "edges")
    class SlotsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: ValueList
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[ValueList, _Mapping]] = ...) -> None: ...
    class EdgesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: StringList
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[StringList, _Mapping]] = ...) -> None: ...
    ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    ATTRS_FIELD_NUMBER: _ClassVar[int]
    SLOTS_FIELD_NUMBER: _ClassVar[int]
    EDGES_FIELD_NUMBER: _ClassVar[int]
    id: str
    kind: str
    attrs: _struct_pb2.Struct
    slots: _containers.MessageMap[str, ValueList]
    edges: _containers.MessageMap[str, StringList]
    def __init__(self, id: _Optional[str] = ..., kind: _Optional[str] = ..., attrs: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., slots: _Optional[_Mapping[str, ValueList]] = ..., edges: _Optional[_Mapping[str, StringList]] = ...) -> None: ...

class SemanticSubstrate(_message.Message):
    __slots__ = ("id", "root_id", "nodes")
    class NodesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: SemanticNode
        def __init__(self, key: _Optional[str] = ..., value: _Optional[_Union[SemanticNode, _Mapping]] = ...) -> None: ...
    ID_FIELD_NUMBER: _ClassVar[int]
    ROOT_ID_FIELD_NUMBER: _ClassVar[int]
    NODES_FIELD_NUMBER: _ClassVar[int]
    id: str
    root_id: str
    nodes: _containers.MessageMap[str, SemanticNode]
    def __init__(self, id: _Optional[str] = ..., root_id: _Optional[str] = ..., nodes: _Optional[_Mapping[str, SemanticNode]] = ...) -> None: ...

class Region(_message.Message):
    __slots__ = ("id", "parent_region", "role", "layout", "min_children", "max_children")
    ID_FIELD_NUMBER: _ClassVar[int]
    PARENT_REGION_FIELD_NUMBER: _ClassVar[int]
    ROLE_FIELD_NUMBER: _ClassVar[int]
    LAYOUT_FIELD_NUMBER: _ClassVar[int]
    MIN_CHILDREN_FIELD_NUMBER: _ClassVar[int]
    MAX_CHILDREN_FIELD_NUMBER: _ClassVar[int]
    id: str
    parent_region: str
    role: str
    layout: str
    min_children: int
    max_children: _struct_pb2.Value
    def __init__(self, id: _Optional[str] = ..., parent_region: _Optional[str] = ..., role: _Optional[str] = ..., layout: _Optional[str] = ..., min_children: _Optional[int] = ..., max_children: _Optional[_Union[_struct_pb2.Value, _Mapping]] = ...) -> None: ...

class Binding(_message.Message):
    __slots__ = ("id", "address", "target_region", "present_as", "cardinality")
    ID_FIELD_NUMBER: _ClassVar[int]
    ADDRESS_FIELD_NUMBER: _ClassVar[int]
    TARGET_REGION_FIELD_NUMBER: _ClassVar[int]
    PRESENT_AS_FIELD_NUMBER: _ClassVar[int]
    CARDINALITY_FIELD_NUMBER: _ClassVar[int]
    id: str
    address: str
    target_region: str
    present_as: str
    cardinality: str
    def __init__(self, id: _Optional[str] = ..., address: _Optional[str] = ..., target_region: _Optional[str] = ..., present_as: _Optional[str] = ..., cardinality: _Optional[str] = ...) -> None: ...

class Group(_message.Message):
    __slots__ = ("id", "kind", "members", "target_region")
    ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    MEMBERS_FIELD_NUMBER: _ClassVar[int]
    TARGET_REGION_FIELD_NUMBER: _ClassVar[int]
    id: str
    kind: str
    members: _containers.RepeatedScalarFieldContainer[str]
    target_region: str
    def __init__(self, id: _Optional[str] = ..., kind: _Optional[str] = ..., members: _Optional[_Iterable[str]] = ..., target_region: _Optional[str] = ...) -> None: ...

class Motif(_message.Message):
    __slots__ = ("id", "kind", "region", "members")
    ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    REGION_FIELD_NUMBER: _ClassVar[int]
    MEMBERS_FIELD_NUMBER: _ClassVar[int]
    id: str
    kind: str
    region: str
    members: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, id: _Optional[str] = ..., kind: _Optional[str] = ..., region: _Optional[str] = ..., members: _Optional[_Iterable[str]] = ...) -> None: ...

class Style(_message.Message):
    __slots__ = ("id", "target", "token")
    ID_FIELD_NUMBER: _ClassVar[int]
    TARGET_FIELD_NUMBER: _ClassVar[int]
    TOKEN_FIELD_NUMBER: _ClassVar[int]
    id: str
    target: str
    token: str
    def __init__(self, id: _Optional[str] = ..., target: _Optional[str] = ..., token: _Optional[str] = ...) -> None: ...

class ActionIntent(_message.Message):
    __slots__ = ("id", "kind", "label", "target_region", "target_ref", "payload_bindings")
    ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    LABEL_FIELD_NUMBER: _ClassVar[int]
    TARGET_REGION_FIELD_NUMBER: _ClassVar[int]
    TARGET_REF_FIELD_NUMBER: _ClassVar[int]
    PAYLOAD_BINDINGS_FIELD_NUMBER: _ClassVar[int]
    id: str
    kind: str
    label: str
    target_region: str
    target_ref: str
    payload_bindings: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, id: _Optional[str] = ..., kind: _Optional[str] = ..., label: _Optional[str] = ..., target_region: _Optional[str] = ..., target_ref: _Optional[str] = ..., payload_bindings: _Optional[_Iterable[str]] = ...) -> None: ...

class ViewSpec(_message.Message):
    __slots__ = ("id", "substrate_id", "complexity_tier", "root_region", "regions", "bindings", "groups", "motifs", "styles", "actions")
    ID_FIELD_NUMBER: _ClassVar[int]
    SUBSTRATE_ID_FIELD_NUMBER: _ClassVar[int]
    COMPLEXITY_TIER_FIELD_NUMBER: _ClassVar[int]
    ROOT_REGION_FIELD_NUMBER: _ClassVar[int]
    REGIONS_FIELD_NUMBER: _ClassVar[int]
    BINDINGS_FIELD_NUMBER: _ClassVar[int]
    GROUPS_FIELD_NUMBER: _ClassVar[int]
    MOTIFS_FIELD_NUMBER: _ClassVar[int]
    STYLES_FIELD_NUMBER: _ClassVar[int]
    ACTIONS_FIELD_NUMBER: _ClassVar[int]
    id: str
    substrate_id: str
    complexity_tier: int
    root_region: str
    regions: _containers.RepeatedCompositeFieldContainer[Region]
    bindings: _containers.RepeatedCompositeFieldContainer[Binding]
    groups: _containers.RepeatedCompositeFieldContainer[Group]
    motifs: _containers.RepeatedCompositeFieldContainer[Motif]
    styles: _containers.RepeatedCompositeFieldContainer[Style]
    actions: _containers.RepeatedCompositeFieldContainer[ActionIntent]
    def __init__(self, id: _Optional[str] = ..., substrate_id: _Optional[str] = ..., complexity_tier: _Optional[int] = ..., root_region: _Optional[str] = ..., regions: _Optional[_Iterable[_Union[Region, _Mapping]]] = ..., bindings: _Optional[_Iterable[_Union[Binding, _Mapping]]] = ..., groups: _Optional[_Iterable[_Union[Group, _Mapping]]] = ..., motifs: _Optional[_Iterable[_Union[Motif, _Mapping]]] = ..., styles: _Optional[_Iterable[_Union[Style, _Mapping]]] = ..., actions: _Optional[_Iterable[_Union[ActionIntent, _Mapping]]] = ...) -> None: ...

class Provenance(_message.Message):
    __slots__ = ("content_refs", "intent_refs")
    CONTENT_REFS_FIELD_NUMBER: _ClassVar[int]
    INTENT_REFS_FIELD_NUMBER: _ClassVar[int]
    content_refs: _containers.RepeatedScalarFieldContainer[str]
    intent_refs: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, content_refs: _Optional[_Iterable[str]] = ..., intent_refs: _Optional[_Iterable[str]] = ...) -> None: ...

class IRNode(_message.Message):
    __slots__ = ("id", "primitive", "props", "children", "provenance", "style_tokens")
    ID_FIELD_NUMBER: _ClassVar[int]
    PRIMITIVE_FIELD_NUMBER: _ClassVar[int]
    PROPS_FIELD_NUMBER: _ClassVar[int]
    CHILDREN_FIELD_NUMBER: _ClassVar[int]
    PROVENANCE_FIELD_NUMBER: _ClassVar[int]
    STYLE_TOKENS_FIELD_NUMBER: _ClassVar[int]
    id: str
    primitive: str
    props: _struct_pb2.Struct
    children: _containers.RepeatedCompositeFieldContainer[IRNode]
    provenance: Provenance
    style_tokens: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, id: _Optional[str] = ..., primitive: _Optional[str] = ..., props: _Optional[_Union[_struct_pb2.Struct, _Mapping]] = ..., children: _Optional[_Iterable[_Union[IRNode, _Mapping]]] = ..., provenance: _Optional[_Union[Provenance, _Mapping]] = ..., style_tokens: _Optional[_Iterable[str]] = ...) -> None: ...

class CompositionIR(_message.Message):
    __slots__ = ("root",)
    ROOT_FIELD_NUMBER: _ClassVar[int]
    root: IRNode
    def __init__(self, root: _Optional[_Union[IRNode, _Mapping]] = ...) -> None: ...

class CompilerDiagnostic(_message.Message):
    __slots__ = ("severity", "code", "message", "intent_ref", "content_ref", "region_id", "node_id")
    SEVERITY_FIELD_NUMBER: _ClassVar[int]
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    INTENT_REF_FIELD_NUMBER: _ClassVar[int]
    CONTENT_REF_FIELD_NUMBER: _ClassVar[int]
    REGION_ID_FIELD_NUMBER: _ClassVar[int]
    NODE_ID_FIELD_NUMBER: _ClassVar[int]
    severity: str
    code: str
    message: str
    intent_ref: str
    content_ref: str
    region_id: str
    node_id: str
    def __init__(self, severity: _Optional[str] = ..., code: _Optional[str] = ..., message: _Optional[str] = ..., intent_ref: _Optional[str] = ..., content_ref: _Optional[str] = ..., region_id: _Optional[str] = ..., node_id: _Optional[str] = ...) -> None: ...

class CompilerResult(_message.Message):
    __slots__ = ("root", "diagnostics")
    ROOT_FIELD_NUMBER: _ClassVar[int]
    DIAGNOSTICS_FIELD_NUMBER: _ClassVar[int]
    root: CompositionIR
    diagnostics: _containers.RepeatedCompositeFieldContainer[CompilerDiagnostic]
    def __init__(self, root: _Optional[_Union[CompositionIR, _Mapping]] = ..., diagnostics: _Optional[_Iterable[_Union[CompilerDiagnostic, _Mapping]]] = ...) -> None: ...

class IntentBundle(_message.Message):
    __slots__ = ("substrate", "view_spec")
    SUBSTRATE_FIELD_NUMBER: _ClassVar[int]
    VIEW_SPEC_FIELD_NUMBER: _ClassVar[int]
    substrate: SemanticSubstrate
    view_spec: ViewSpec
    def __init__(self, substrate: _Optional[_Union[SemanticSubstrate, _Mapping]] = ..., view_spec: _Optional[_Union[ViewSpec, _Mapping]] = ...) -> None: ...

class ASTBundle(_message.Message):
    __slots__ = ("result", "style_values", "title")
    class StyleValuesEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    RESULT_FIELD_NUMBER: _ClassVar[int]
    STYLE_VALUES_FIELD_NUMBER: _ClassVar[int]
    TITLE_FIELD_NUMBER: _ClassVar[int]
    result: CompilerResult
    style_values: _containers.ScalarMap[str, str]
    title: str
    def __init__(self, result: _Optional[_Union[CompilerResult, _Mapping]] = ..., style_values: _Optional[_Mapping[str, str]] = ..., title: _Optional[str] = ...) -> None: ...
