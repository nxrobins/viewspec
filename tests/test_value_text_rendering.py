"""Regression tests for audit findings C-1 and C-2.

C-1: every attribute and slot value travels through google.protobuf.Struct, whose numbers are
doubles, so the JSON integer 100 used to render as the visible text "100.0" after a file round
trip and 9007199254740993 silently became 9007199254740992.0. Integral numbers must now render as
integers, the same bundle must compile to the same bytes from an in-memory dict, a JSON file, or
proto bytes, and integers above 2**53 must be rejected at the intent validation boundary.

C-2: _text_from_value used json.dumps with ensure_ascii=True, so a list binding of
["Müller", "Zoë"] rendered literal \\u escapes in the page.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from viewspec import IntentBundle, ViewSpecBuilder, agent_repair_checklist, compile, validate_agent_intent_bundle
from viewspec.cli import main as cli_main
from viewspec.compiler import _text_from_value
from viewspec.emitters.html_tailwind import HtmlTailwindEmitter
from viewspec.emitters.react_tailwind_tsx import ReactTailwindTsxEmitter
from viewspec.emitters.react_tsx import ReactTsxEmitter
from viewspec.intent_contract import MAX_SAFE_INTEGER
from viewspec.intent_tools import validate_intent_bundle_file_tool
from viewspec.schema.viewspec_pb2 import IntentBundle as PbIntentBundle
from viewspec.types import normalize_json_numbers, normalize_json_numbers_in_place

UNSAFE_INTEGER = MAX_SAFE_INTEGER + 1  # 9007199254740993, the audit's reproduction value


def _numeric_dashboard_payload(big_value: object) -> dict:
    """The audit's C-1 reproduction bundle: a dashboard whose values are JSON numbers."""
    return {
        "substrate": {
            "id": "numeric_view_substrate",
            "root_id": "numeric_view",
            "nodes": {
                "numeric_view": {"id": "numeric_view", "kind": "app", "attrs": {}, "slots": {}, "edges": {}},
                "kpis_card_1": {
                    "id": "kpis_card_1",
                    "kind": "dashboard_card",
                    "attrs": {"label": "Open tickets", "value": 100},
                    "slots": {},
                    "edges": {},
                },
                "kpis_card_2": {
                    "id": "kpis_card_2",
                    "kind": "dashboard_card",
                    "attrs": {"label": "Big id", "value": big_value},
                    "slots": {},
                    "edges": {},
                },
            },
        },
        "view_spec": {
            "id": "numeric_view",
            "substrate_id": "numeric_view_substrate",
            "complexity_tier": 1,
            "root_region": "root",
            "regions": [
                {
                    "id": "root",
                    "parent_region": "",
                    "role": "root",
                    "layout": "stack",
                    "min_children": 1,
                    "max_children": None,
                },
                {
                    "id": "main",
                    "parent_region": "root",
                    "role": "main",
                    "layout": "stack",
                    "min_children": 1,
                    "max_children": None,
                },
            ],
            "bindings": [
                {
                    "id": "kpis_card_1_label",
                    "address": "node:kpis_card_1#attr:label",
                    "target_region": "main",
                    "present_as": "label",
                    "cardinality": "exactly_once",
                },
                {
                    "id": "kpis_card_1_value",
                    "address": "node:kpis_card_1#attr:value",
                    "target_region": "main",
                    "present_as": "value",
                    "cardinality": "exactly_once",
                },
                {
                    "id": "kpis_card_2_label",
                    "address": "node:kpis_card_2#attr:label",
                    "target_region": "main",
                    "present_as": "label",
                    "cardinality": "exactly_once",
                },
                {
                    "id": "kpis_card_2_value",
                    "address": "node:kpis_card_2#attr:value",
                    "target_region": "main",
                    "present_as": "value",
                    "cardinality": "exactly_once",
                },
            ],
            "groups": [
                {
                    "id": "metrics",
                    "kind": "ordered",
                    "members": ["kpis_card_1_label", "kpis_card_1_value", "kpis_card_2_label", "kpis_card_2_value"],
                    "target_region": "main",
                },
            ],
            "motifs": [
                {
                    "id": "kpis",
                    "kind": "dashboard",
                    "region": "main",
                    "members": ["kpis_card_1_label", "kpis_card_1_value", "kpis_card_2_label", "kpis_card_2_value"],
                },
            ],
            "styles": [],
            "actions": [],
        },
    }


def _texts(node) -> list[str]:
    found = []
    if "text" in node.props:
        found.append(node.props["text"])
    for child in node.children:
        found.extend(_texts(child))
    return found


def _json_text_roundtrip(bundle: IntentBundle) -> IntentBundle:
    return IntentBundle.from_json(json.loads(json.dumps(bundle.to_json(), sort_keys=True)))


def _proto_roundtrip(bundle: IntentBundle) -> IntentBundle:
    return IntentBundle.from_proto(PbIntentBundle.FromString(bundle.to_proto().SerializeToString()))


def _compiled_bytes(bundle: IntentBundle, out_dir: Path) -> tuple[bytes, bytes]:
    ast = compile(bundle)
    paths = HtmlTailwindEmitter().emit(ast, out_dir)
    return json.dumps(ast.to_json(), sort_keys=True).encode("utf-8"), Path(paths["html"]).read_bytes()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (100, "100"),
        (100.0, "100"),
        (-7.0, "-7"),
        (-0.0, "0"),
        (float(MAX_SAFE_INTEGER), str(MAX_SAFE_INTEGER)),
        (2.5, "2.5"),
        (0.1, "0.1"),
        (1e300, "1e+300"),
        (float(MAX_SAFE_INTEGER) * 2, "1.8014398509481984e+16"),
        (True, "true"),
        (False, "false"),
        (None, "null"),
        ([1.0, 2.5, {"n": 3.0, "ok": True}], '[1,2.5,{"n":3,"ok":true}]'),
        ({"b": 1.0, "a": [0.0, "x"]}, '{"a":[0,"x"],"b":1}'),
    ],
)
def test_text_from_value_renders_integral_numbers_as_integers_and_keeps_real_floats(value, expected):
    assert _text_from_value(value) == expected


def test_text_from_value_keeps_non_ascii_text_literal():
    assert _text_from_value(["Müller", "Zoë"]) == '["Müller","Zoë"]'
    assert _text_from_value({"name": "Zoë", "city": "Zürich"}) == '{"city":"Zürich","name":"Zoë"}'
    assert "\\u" not in _text_from_value(["Müller", "Zoë", "日本語", "😀"])
    assert _text_from_value("Müller") == "Müller"


def test_normalize_json_numbers_restores_safe_integers_only():
    assert normalize_json_numbers(
        {"a": [1.0, 2.5, True, None, "x", float(MAX_SAFE_INTEGER), float(MAX_SAFE_INTEGER) * 2, -3.0]}
    ) == {"a": [1, 2.5, True, None, "x", MAX_SAFE_INTEGER, float(MAX_SAFE_INTEGER) * 2, -3]}
    assert isinstance(normalize_json_numbers(1.0), int)
    assert isinstance(normalize_json_numbers(True), bool)
    assert isinstance(normalize_json_numbers(1e300), float)


def test_normalize_json_numbers_treats_tuples_like_lists():
    """protobuf Struct and JSON turn a tuple into a list, so normalization must too (audit C-1)."""
    value = {"pair": (1.0, 2.0), "rows": [(3.0, 2.5), {"nested": (4.0,)}]}
    normalized = normalize_json_numbers(value)
    assert json.dumps(normalized) == '{"pair": [1, 2], "rows": [[3, 2.5], {"nested": [4]}]}'
    assert normalize_json_numbers((5.0, (6.0, 1.5))) == [5, [6, 1.5]]
    # The argument is never modified.
    assert value == {"pair": (1.0, 2.0), "rows": [(3.0, 2.5), {"nested": (4.0,)}]}
    assert isinstance(value["pair"][0], float)
    assert _text_from_value((1.0, 2.0)) == _text_from_value([1.0, 2.0]) == "[1,2]"

    in_place = {"pair": (1.0, 2.0), "rows": [(3.0, 2.5), {"nested": (4.0,)}]}
    normalize_json_numbers_in_place(in_place)
    assert json.dumps(in_place) == '{"pair": [1, 2], "rows": [[3, 2.5], {"nested": [4]}]}'


def test_from_json_restores_integer_attribute_values():
    bundle = IntentBundle.from_json(_numeric_dashboard_payload(MAX_SAFE_INTEGER))

    assert bundle.substrate.nodes["kpis_card_1"].attrs["value"] == 100
    assert isinstance(bundle.substrate.nodes["kpis_card_1"].attrs["value"], int)
    assert bundle.substrate.nodes["kpis_card_2"].attrs["value"] == MAX_SAFE_INTEGER
    assert isinstance(bundle.substrate.nodes["kpis_card_2"].attrs["value"], int)


def test_integer_attributes_render_as_integers_identically_from_dict_json_and_proto(tmp_path):
    payload = _numeric_dashboard_payload(MAX_SAFE_INTEGER)
    assert validate_agent_intent_bundle(payload).valid
    from_dict = IntentBundle.from_json(payload)

    routes = {
        "dict": from_dict,
        "json_text": _json_text_roundtrip(from_dict),
        "proto": _proto_roundtrip(from_dict),
        "json_text_then_proto": _proto_roundtrip(_json_text_roundtrip(from_dict)),
    }
    outputs = {name: _compiled_bytes(bundle, tmp_path / name) for name, bundle in routes.items()}

    assert _texts(compile(from_dict).result.root.root) == ["Open tickets", "100", "Big id", str(MAX_SAFE_INTEGER)]
    assert len({ast_json for ast_json, _ in outputs.values()}) == 1
    assert len({html for _, html in outputs.values()}) == 1
    html = outputs["dict"][1].decode("utf-8")
    assert "100.0" not in html
    assert f"{MAX_SAFE_INTEGER}.0" not in html
    assert ">100<" in html
    assert f">{MAX_SAFE_INTEGER}<" in html


def test_builder_numbers_compile_identically_in_memory_and_after_round_trips(tmp_path):
    builder = ViewSpecBuilder("numbers_in_memory")
    dashboard = builder.add_dashboard("kpis", region="main", group_id="cards")
    dashboard.add_card(label="Open tickets", value=100, id="open")
    dashboard.add_card(label="Ratio", value=0.25, id="ratio")
    dashboard.add_card(label="Whole", value=3.0, id="whole")
    dashboard.add_card(label="Huge", value=1e300, id="huge")
    dashboard.add_card(label="Flag", value=True, id="flag")
    dashboard.add_card(label="Nothing", value=None, id="nothing")
    in_memory = builder.build_bundle()

    expected = [
        "Open tickets",
        "100",
        "Ratio",
        "0.25",
        "Whole",
        "3",
        "Huge",
        "1e+300",
        "Flag",
        "true",
        "Nothing",
        "null",
    ]
    assert _texts(compile(in_memory).result.root.root) == expected

    baseline = _compiled_bytes(in_memory, tmp_path / "memory")
    assert _compiled_bytes(_json_text_roundtrip(in_memory), tmp_path / "json") == baseline
    assert _compiled_bytes(_proto_roundtrip(in_memory), tmp_path / "proto") == baseline


def test_validate_intent_rejects_integers_above_2_pow_53_with_a_path_and_fix():
    payload = _numeric_dashboard_payload(UNSAFE_INTEGER)

    for route in (payload, json.dumps(payload)):
        result = validate_agent_intent_bundle(route)
        assert not result.valid
        issues = [issue for issue in result.issues if issue.code == "INTEGER_OUT_OF_SAFE_RANGE"]
        assert [issue.path for issue in issues] == ["$.substrate.nodes.kpis_card_2.attrs.value"]
        assert str(UNSAFE_INTEGER) in issues[0].message
        assert "2^53" in issues[0].message
        assert "JSON strings" in issues[0].suggestion
        assert any("2^53" in line for line in agent_repair_checklist(result))


@pytest.mark.parametrize(
    ("mutate", "expected_path"),
    [
        (lambda node: node["attrs"].__setitem__("value", -UNSAFE_INTEGER), "$.substrate.nodes.kpis_card_2.attrs.value"),
        (
            lambda node: node["attrs"].__setitem__("value", {"ids": [1, UNSAFE_INTEGER]}),
            "$.substrate.nodes.kpis_card_2.attrs.value.ids[1]",
        ),
        (
            lambda node: node["attrs"].__setitem__("value", [{"id": UNSAFE_INTEGER}]),
            "$.substrate.nodes.kpis_card_2.attrs.value[0].id",
        ),
        (
            lambda node: node["slots"].__setitem__("items", {"values": ["ok", UNSAFE_INTEGER]}),
            "$.substrate.nodes.kpis_card_2.slots.items.values[1]",
        ),
        (
            lambda node: node["slots"].__setitem__("items", {"values": [[2**70]]}),
            "$.substrate.nodes.kpis_card_2.slots.items.values[0][0]",
        ),
    ],
)
def test_validate_intent_finds_unsafe_integers_at_any_depth(mutate, expected_path):
    payload = _numeric_dashboard_payload(100)
    mutate(payload["substrate"]["nodes"]["kpis_card_2"])

    result = validate_agent_intent_bundle(payload)

    assert not result.valid
    assert [issue.path for issue in result.issues if issue.code == "INTEGER_OUT_OF_SAFE_RANGE"] == [expected_path]


@pytest.mark.parametrize(
    "value", [MAX_SAFE_INTEGER, -MAX_SAFE_INTEGER, 0, True, 1e20, 2.5, {"ids": [MAX_SAFE_INTEGER]}]
)
def test_validate_intent_accepts_numbers_within_the_safe_range(value):
    payload = _numeric_dashboard_payload(value)

    result = validate_agent_intent_bundle(payload)

    assert result.valid, [issue.to_json() for issue in result.issues]


def test_validate_intent_cli_and_tool_surface_the_unsafe_integer_error(tmp_path, capsys):
    path = tmp_path / "viewspec.intent.json"
    path.write_text(json.dumps(_numeric_dashboard_payload(UNSAFE_INTEGER), indent=2), encoding="utf-8")

    assert cli_main(["validate-intent", str(path), "--json"]) == 2
    cli_payload = json.loads(capsys.readouterr().out)
    assert cli_payload["ok"] is False
    assert [issue["code"] for issue in cli_payload["issues"]] == ["INTEGER_OUT_OF_SAFE_RANGE"]
    assert cli_payload["issues"][0]["path"] == "$.substrate.nodes.kpis_card_2.attrs.value"

    tool_payload = validate_intent_bundle_file_tool(path, cwd=tmp_path)
    assert tool_payload["ok"] is False
    assert [error["code"] for error in tool_payload["errors"]] == ["INTEGER_OUT_OF_SAFE_RANGE"]
    assert "JSON strings" in tool_payload["errors"][0]["fix"]

    out_dir = tmp_path / "out"
    assert cli_main(["compile", str(path), "--out", str(out_dir)]) == 2
    assert "INTEGER_OUT_OF_SAFE_RANGE" in capsys.readouterr().err
    assert not (out_dir / "index.html").exists()


def _non_ascii_list_bundle() -> IntentBundle:
    builder = ViewSpecBuilder("team_view", root_attrs={"title": "Team"})
    builder.add_node("team", "team", attrs={"title": "Mitarbeiter"}, slots={"members": ["Müller", "Zoë"]})
    builder.bind_attr("team_title", "team", "title", region="main", present_as="label")
    builder.bind_slot("team_members", "team", "members", region="main")
    return builder.build_bundle()


def test_non_ascii_list_binding_renders_literal_text(tmp_path):
    bundle = _non_ascii_list_bundle()
    ast = compile(bundle)

    assert '["Müller","Zoë"]' in _texts(ast.result.root.root)

    html = Path(HtmlTailwindEmitter().emit(ast, tmp_path / "html")["html"]).read_bytes()
    assert "[&quot;Müller&quot;,&quot;Zoë&quot;]".encode() in html
    assert b"\\u00fc" not in html and b"\\u00eb" not in html

    for name, emitter in (("react", ReactTsxEmitter()), ("react_tailwind", ReactTailwindTsxEmitter())):
        tsx = Path(emitter.emit(ast, tmp_path / name)["tsx"]).read_bytes()
        assert "Müller".encode() in tsx
        assert "Zoë".encode() in tsx

    for route in (_json_text_roundtrip(bundle), _proto_roundtrip(bundle)):
        assert json.dumps(compile(route).to_json(), sort_keys=True) == json.dumps(ast.to_json(), sort_keys=True)
