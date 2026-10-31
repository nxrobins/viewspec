"""Test ViewSpec type round-tripping through JSON and protobuf."""

import json
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from viewspec import ASTBundle, IntentBundle, ViewSpecBuilder, compile
from viewspec.agent import validate_agent_intent_bundle
from viewspec.compiler import CompilerInputError
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
from viewspec.intent_contract import MAX_SAFE_INTEGER
from viewspec.schema.viewspec_pb2 import IntentBundle as PbIntentBundle
from viewspec.types import CompilerResult, CompositionIR, IRNode, Provenance, SemanticNode, normalize_json_numbers


def test_builder_roundtrip_json():
    """ViewSpecBuilder → JSON → IntentBundle → JSON → identical."""
    builder = ViewSpecBuilder("test_view")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Alpha", value="100")
    table.add_row(label="Beta", value="200")

    bundle = builder.build_bundle()
    json_str = json.dumps(bundle.to_json(), sort_keys=True)
    restored = IntentBundle.from_json(json.loads(json_str))
    json_str_2 = json.dumps(restored.to_json(), sort_keys=True)
    assert json_str == json_str_2


def test_builder_roundtrip_proto():
    """ViewSpecBuilder → proto → IntentBundle → proto → identical."""
    builder = ViewSpecBuilder("test_view")
    builder.add_dashboard("kpis", region="main", group_id="metrics")

    bundle = builder.build_bundle()
    proto_bytes = bundle.to_proto().SerializeToString()
    from viewspec.schema.viewspec_pb2 import IntentBundle as PbBundle

    restored = IntentBundle.from_proto(PbBundle.FromString(proto_bytes))
    proto_bytes_2 = restored.to_proto().SerializeToString()
    assert proto_bytes == proto_bytes_2


def test_ast_bundle_roundtrip():
    """ASTBundle round-trips through JSON."""
    result = CompilerResult(
        root=CompositionIR(
            root=IRNode(
                id="root_0",
                primitive="root",
                children=[
                    IRNode(
                        id="text_1",
                        primitive="text",
                        props={"text": "Hello, ViewSpec"},
                        provenance=Provenance(
                            content_refs=["node:doc#attr:title"], intent_refs=["viewspec:binding:b1"]
                        ),
                    ),
                ],
            )
        ),
    )
    ast = ASTBundle(result=result, style_values={"emphasis.high": "font-weight: 700;"}, title="Test")
    json_str = json.dumps(ast.to_json(), sort_keys=True)
    restored = ASTBundle.from_json(json.loads(json_str))
    assert restored.title == "Test"
    assert restored.result.root.root.children[0].props["text"] == "Hello, ViewSpec"
    assert restored.result.root.root.children[0].provenance.content_refs == ["node:doc#attr:title"]


def test_binding_provenance_exactly_once():
    """Every binding in a ViewSpec has a unique address — the exactly-once invariant."""
    builder = ViewSpecBuilder("provenance_test")
    table = builder.add_table("t", region="main")
    table.add_row(label="A", value="1")
    table.add_row(label="B", value="2")
    table.add_row(label="C", value="3")

    spec = builder.view_spec
    addresses = [b.address for b in spec.bindings]
    assert len(addresses) == len(set(addresses)), f"Duplicate addresses: {addresses}"


def test_empty_viewspec():
    """A ViewSpec with no bindings is valid."""
    builder = ViewSpecBuilder("empty")
    bundle = builder.build_bundle()
    json_data = bundle.to_json()
    restored = IntentBundle.from_json(json_data)
    assert restored.view_spec.id == "empty"
    assert len(restored.view_spec.bindings) == 0


def test_slots_edges_actions_roundtrip_json_and_proto():
    """Map-shaped nodes, slots, edges, styles, and actions preserve wire shape."""
    builder = ViewSpecBuilder(
        "graph_view",
        root_slots={"children": ["child"]},
        root_edges={"next": ["child"]},
    )
    builder.add_node("child", "document", attrs={"title": "Graph"})
    builder.bind_slot("root_children", "graph_view", "children", region="main")
    builder.bind_attr("child_title", "child", "title", region="main", present_as="label")
    builder.add_style("title_style", "binding:child_title", "emphasis.high")
    builder.add_action(
        "open_child",
        "open",
        "Open",
        target_region="main",
        target_ref="binding:child_title",
        payload_bindings=["child_title"],
    )

    bundle = builder.build_bundle()
    json_restored = IntentBundle.from_json(bundle.to_json())
    proto_restored = IntentBundle.from_proto(bundle.to_proto())

    assert json_restored.substrate.nodes["graph_view"].slots["children"] == ["child"]
    assert json_restored.substrate.nodes["graph_view"].edges["next"] == ["child"]
    assert json_restored.view_spec.actions[0].target_ref == "binding:child_title"
    assert proto_restored.to_json() == json_restored.to_json()


# ---------------------------------------------------------------------------
# Property tests over scalar and nested values
#
# Every attribute and slot value crosses google.protobuf.Struct, whose numbers are doubles. These
# properties pin the contract that a bundle built in memory compiles to the same bytes as the same
# bundle read back from JSON text or proto bytes, for ints inside the 2**53 safe range, floats,
# bools, null, non-ASCII strings, and nested lists and dicts, and that ints beyond 2**53 are
# rejected at the intent validation boundary instead of being rounded silently.
# ---------------------------------------------------------------------------

_SAFE_INTEGERS = st.integers(min_value=-MAX_SAFE_INTEGER, max_value=MAX_SAFE_INTEGER)
_UNSAFE_INTEGERS = st.one_of(
    st.integers(min_value=MAX_SAFE_INTEGER + 1, max_value=2**80),
    st.integers(min_value=-(2**80), max_value=-MAX_SAFE_INTEGER - 1),
)
_FLOATS = st.floats(allow_nan=False, allow_infinity=False)
_TEXT = st.text(alphabet=st.characters(exclude_categories=("Cs",)), max_size=12)
_NON_ASCII_TEXT = st.text(
    alphabet=st.characters(min_codepoint=0x80, exclude_categories=("Cs",)), min_size=1, max_size=8
)
_SCALARS = st.one_of(st.none(), st.booleans(), _SAFE_INTEGERS, _FLOATS, _TEXT, _NON_ASCII_TEXT)
# Tuples are included because the Python SDK accepts them; JSON and proto turn them into lists.
_JSON_VALUES = st.recursive(
    _SCALARS,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.lists(children, max_size=4).map(tuple),
        st.dictionaries(_TEXT, children, max_size=4),
    ),
    max_leaves=12,
)


def _value_bundle(attr_value, slot_values) -> IntentBundle:
    builder = ViewSpecBuilder("roundtrip_values")
    builder.add_node(
        "record", "record", attrs={"label": "Record", "value": attr_value}, slots={"items": list(slot_values)}
    )
    builder.bind_attr("record_label", "record", "label", region="main", present_as="label")
    builder.bind_attr("record_value", "record", "value", region="main", present_as="value")
    builder.bind_slot("record_items", "record", "items", region="main")
    return builder.build_bundle()


def _json_text_roundtrip(bundle: IntentBundle) -> IntentBundle:
    return IntentBundle.from_json(json.loads(json.dumps(bundle.to_json(), sort_keys=True)))


def _proto_roundtrip(bundle: IntentBundle) -> IntentBundle:
    return IntentBundle.from_proto(PbIntentBundle.FromString(bundle.to_proto().SerializeToString()))


def _compiled_bytes(bundle: IntentBundle, out_dir: Path) -> tuple[bytes, bytes]:
    ast = compile(bundle)
    paths = HtmlTailwindEmitter().emit(ast, out_dir)
    return json.dumps(ast.to_json(), sort_keys=True).encode("utf-8"), Path(paths["html"]).read_bytes()


def _binding_text(bundle: IntentBundle, node_id: str) -> str:
    def walk(node):
        if node.id == node_id:
            return node.props["text"]
        for child in node.children:
            found = walk(child)
            if found is not None:
                return found
        return None

    text = walk(compile(bundle).result.root.root)
    assert text is not None, f"{node_id} was not compiled"
    return text


@settings(
    max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow]
)
@given(attr_value=_JSON_VALUES, slot_values=st.lists(_JSON_VALUES, max_size=3))
def test_values_compile_byte_identically_in_memory_and_after_json_and_proto_round_trips(
    tmp_path, attr_value, slot_values
):
    in_memory = _value_bundle(attr_value, slot_values)

    baseline = _compiled_bytes(in_memory, tmp_path / "memory")
    assert _compiled_bytes(_json_text_roundtrip(in_memory), tmp_path / "json") == baseline
    assert _compiled_bytes(_proto_roundtrip(in_memory), tmp_path / "proto") == baseline
    assert _compiled_bytes(_proto_roundtrip(_json_text_roundtrip(in_memory)), tmp_path / "json_proto") == baseline


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(attr_value=_JSON_VALUES, slot_values=st.lists(_JSON_VALUES, max_size=3))
def test_values_survive_json_and_proto_round_trips_up_to_integral_double_normalization(attr_value, slot_values):
    in_memory = _value_bundle(attr_value, slot_values)
    expected_attrs = normalize_json_numbers({"label": "Record", "value": attr_value})
    expected_slots = normalize_json_numbers(list(slot_values))

    for restored in (_json_text_roundtrip(in_memory), _proto_roundtrip(in_memory)):
        node = restored.substrate.nodes["record"]
        assert node.attrs == expected_attrs
        assert node.slots["items"] == expected_slots
        assert json.dumps(restored.to_json(), sort_keys=True) == json.dumps(in_memory.to_json(), sort_keys=True)


@settings(max_examples=60, deadline=None)
@given(value=_SAFE_INTEGERS)
def test_safe_integers_render_exactly_and_validate(value):
    bundle = _value_bundle(value, [value])

    assert _binding_text(bundle, "binding_record_value") == str(value)
    assert _binding_text(bundle, "binding_record_items") == f"[{value}]"
    assert _binding_text(_json_text_roundtrip(bundle), "binding_record_value") == str(value)
    assert _binding_text(_proto_roundtrip(bundle), "binding_record_items") == f"[{value}]"
    assert validate_agent_intent_bundle(bundle.to_json()).valid


@settings(max_examples=40, deadline=None)
@given(value=_FLOATS)
def test_floats_render_identically_in_memory_and_after_round_trips(value):
    bundle = _value_bundle(value, [])

    text = _binding_text(bundle, "binding_record_value")
    assert _binding_text(_json_text_roundtrip(bundle), "binding_record_value") == text
    assert _binding_text(_proto_roundtrip(bundle), "binding_record_value") == text
    if value.is_integer() and abs(value) <= MAX_SAFE_INTEGER:
        assert text == str(int(value))
    else:
        assert text == json.dumps(value)


@settings(max_examples=40, deadline=None)
@given(words=st.lists(_NON_ASCII_TEXT, min_size=1, max_size=4))
def test_non_ascii_list_bindings_render_literal_text_in_memory_and_after_round_trips(words):
    bundle = _value_bundle("x", words)
    expected = json.dumps(words, ensure_ascii=False, separators=(",", ":"))

    assert _binding_text(bundle, "binding_record_items") == expected
    assert _binding_text(_json_text_roundtrip(bundle), "binding_record_items") == expected
    assert _binding_text(_proto_roundtrip(bundle), "binding_record_items") == expected
    assert "\\u" not in expected.replace("\\\\", "")


@settings(max_examples=40, deadline=None)
@given(value=_UNSAFE_INTEGERS, placement=st.sampled_from(["attr", "nested_attr", "slot", "nested_slot"]))
def test_unsafe_integers_are_rejected_at_the_intent_boundary_wherever_they_appear(value, placement):
    payload = _value_bundle("placeholder", ["placeholder"]).to_json()
    node = payload["substrate"]["nodes"]["record"]
    if placement == "attr":
        node["attrs"]["value"] = value
        expected_path = "$.substrate.nodes.record.attrs.value"
    elif placement == "nested_attr":
        node["attrs"]["value"] = {"ids": ["ok", value]}
        expected_path = "$.substrate.nodes.record.attrs.value.ids[1]"
    elif placement == "slot":
        node["slots"]["items"]["values"] = [value]
        expected_path = "$.substrate.nodes.record.slots.items.values[0]"
    else:
        node["slots"]["items"]["values"] = [["ok", {"id": value}]]
        expected_path = "$.substrate.nodes.record.slots.items.values[0][1].id"

    for route in (payload, json.dumps(payload)):
        result = validate_agent_intent_bundle(route)
        assert not result.valid
        assert [issue.path for issue in result.issues if issue.code == "INTEGER_OUT_OF_SAFE_RANGE"] == [expected_path]


def test_negative_zero_and_integral_floats_are_canonical_in_memory():
    """-0.0 and 100.0 must serialize like 0 and 100 before any round trip."""
    bundle = _value_bundle(-0.0, [100.0, {"nested": 2.0}, [3.0, 1.5]])
    node = bundle.substrate.nodes["record"]
    assert node.attrs["value"] == 0 and isinstance(node.attrs["value"], int)
    assert json.dumps(node.attrs["value"]) == "0"
    assert node.slots["items"] == [100, {"nested": 2}, [3, 1.5]]
    assert all(
        isinstance(v, int)
        for v in (node.slots["items"][0], node.slots["items"][1]["nested"], node.slots["items"][2][0])
    )
    assert isinstance(node.slots["items"][2][1], float)
    for restored in (_json_text_roundtrip(bundle), _proto_roundtrip(bundle)):
        assert json.dumps(restored.to_json(), sort_keys=True) == json.dumps(bundle.to_json(), sort_keys=True)


def test_tuples_render_like_lists_in_memory_and_after_json_and_proto_round_trips(tmp_path):
    """A tuple of integral floats renders like the list JSON and proto turn it into."""
    bundle = _value_bundle((1.0, 2.0), [(3.0, 4.5), {"pair": (5.0, 6.0)}])
    node = bundle.substrate.nodes["record"]
    assert node.attrs["value"] == [1, 2]
    assert node.slots["items"] == [[3, 4.5], {"pair": [5, 6]}]
    assert json.dumps(node.attrs["value"]) == "[1, 2]"

    assert _binding_text(bundle, "binding_record_value") == "[1,2]"
    assert _binding_text(bundle, "binding_record_items") == '[[3,4.5],{"pair":[5,6]}]'
    for restored in (_json_text_roundtrip(bundle), _proto_roundtrip(bundle)):
        assert _binding_text(restored, "binding_record_value") == "[1,2]"
        assert _binding_text(restored, "binding_record_items") == '[[3,4.5],{"pair":[5,6]}]'

    baseline = _compiled_bytes(bundle, tmp_path / "memory")
    assert _compiled_bytes(_json_text_roundtrip(bundle), tmp_path / "json") == baseline
    assert _compiled_bytes(_proto_roundtrip(bundle), tmp_path / "proto") == baseline


def test_semantic_node_does_not_rewrite_caller_owned_data():
    """Normalizing integral doubles must not change the dicts and lists the caller passed in."""
    row = {"price": 10.0, "tags": [1.0, 2.5]}
    slot_item = {"qty": 7.0}
    slot_list = [3.0, [4.0]]
    appended = {"q": 8.0}
    builder = ViewSpecBuilder("caller_data")
    builder.add_node("record", "record", attrs={"meta": row}, slots={"items": [slot_item, slot_list]})
    builder.append_slot("record", "items", appended)
    top_attrs = {"a": 5.0, "pair": (6.0, 7.0)}
    top_slots = {"s": [9.0, {"n": 10.0}]}
    direct = SemanticNode(id="direct", kind="record", attrs=top_attrs, slots=top_slots)

    assert json.dumps(row) == '{"price": 10.0, "tags": [1.0, 2.5]}'
    assert json.dumps(slot_item) == '{"qty": 7.0}'
    assert json.dumps(slot_list) == "[3.0, [4.0]]"
    assert json.dumps(appended) == '{"q": 8.0}'
    assert top_attrs == {"a": 5.0, "pair": (6.0, 7.0)} and isinstance(top_attrs["a"], float)
    assert json.dumps(top_slots) == '{"s": [9.0, {"n": 10.0}]}'

    # The nodes themselves hold the normalized values.
    node = builder.build_bundle().substrate.nodes["record"]
    assert json.dumps(node.attrs) == '{"meta": {"price": 10, "tags": [1, 2.5]}}'
    assert json.dumps(node.slots) == '{"items": [{"qty": 7}, [3, [4]], {"q": 8}]}'
    assert json.dumps(direct.attrs) == '{"a": 5, "pair": [6, 7]}'
    assert json.dumps(direct.slots) == '{"s": [9, {"n": 10}]}'


def test_semantic_node_keeps_wrongly_shaped_containers_for_compile_to_reject():
    """Normalization must not turn a malformed attrs or slots value into an accepted one."""
    builder = ViewSpecBuilder("shapes")
    bundle = builder.build_bundle()
    root_id = bundle.substrate.root_id
    root = bundle.substrate.nodes[root_id]

    tuple_slot = SemanticNode(id=root.id, kind=root.kind, attrs=dict(root.attrs), slots={"items": (1.0, 2.0)})
    assert tuple_slot.slots["items"] == (1.0, 2.0)
    bundle.substrate.nodes[root_id] = tuple_slot
    with pytest.raises(CompilerInputError, match=r"slots\['items'\] must be a list"):
        compile(bundle)

    list_attrs = SemanticNode(id=root.id, kind=root.kind, attrs=[1.0])  # type: ignore[arg-type]
    assert list_attrs.attrs == [1.0]
    bundle.substrate.nodes[root_id] = list_attrs
    with pytest.raises(CompilerInputError, match=r"attrs must be a dictionary"):
        compile(bundle)
