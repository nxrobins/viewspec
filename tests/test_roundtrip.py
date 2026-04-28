"""Test ViewSpec type round-tripping through JSON and protobuf."""

import json

from viewspec import ViewSpecBuilder, IntentBundle, ASTBundle, CompilerResult, CompositionIR, IRNode, Provenance


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
                        provenance=Provenance(content_refs=["node:doc#attr:title"], intent_refs=["viewspec:binding:b1"]),
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
