"""The compact authoring form: normalize() drives ViewSpecBuilder, so compact and builder output agree.

Every test here pins one of four properties. Twin identity: a compact document and the builder calls
it serializes give byte-identical canonical JSON and byte-identical artifacts. Determinism: the same
compact bytes give the same canonical bytes. Fail-closed: every construct the builder cannot express
or would lose silently is rejected with a ``COMPACT_*`` code at a ``$.``-rooted compact path. The
canonical form is untouched: a bundle without ``format`` validates, compiles and records exactly what
it did before.
"""

from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

pytest.importorskip("jsonschema")
from jsonschema import Draft202012Validator

from scripts.update_corpus_goldens import GOLDENS_PATH
from viewspec import ViewSpecBuilder
from viewspec.agent import DEFAULT_AGENT_REPAIR_SUGGESTION, validate_agent_intent_bundle
from viewspec.api import INIT_DEFAULT_OUT, check, prove, source_kind
from viewspec.app_bundle import starter_app_bundle, validate_app_text
from viewspec.cli import main as cli_main
from viewspec.compact_form import (
    COMPACT_ERROR_CODES,
    COMPACT_FORM_PROMPT_SECTION,
    COMPACT_FORM_SCHEMA,
    COMPACT_FORM_SCHEMA_ID,
    ITEM_KEY_BY_KIND,
    MAX_COMPACT_BYTES,
    CompactFormError,
    canonical_json_text,
    detect_compact,
    normalize,
    normalize_with_issues,
)
from viewspec.error_codes import ERROR_CODES
from viewspec.intent_tools import (
    INTENT_COMPILE_TARGETS,
    STARTER_INTENT_KINDS,
    compile_intent_bundle_file_tool,
    patch_target_problem,
    prepare_intent_source,
    starter_compact_payload,
    starter_intent_payload,
    validate_intent_text,
)
from viewspec.local_tools import check_artifact_dir, source_hash
from viewspec.mcp_server import ViewSpecTools
from viewspec.studio import _compile_preview
from viewspec.studio_inspector import build_inspector_map

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "conformance" / "verification" / "fixtures"
GOLDENS = json.loads(GOLDENS_PATH.read_text(encoding="utf-8"))
SCHEMA = Draft202012Validator(COMPACT_FORM_SCHEMA)

# Compact corpus cases and the canonical case each one is a twin of.
TWIN_PAIRS = {
    "data-dense-dashboard-compact": "data-dense-dashboard",
    "invoice-table-compact": "invoice-table",
    "kpi-dashboard-compact": "kpi-dashboard",
    "pricing-comparison-compact": "pricing-comparison",
}
# examples/*.py builder scripts, the file each writes, and the corpus case that pins its output.
EXAMPLE_TWINS = {
    "invoice_table.py": ("output/invoice.json", "invoice-table"),
    "kpi_dashboard.py": ("output/dashboard.json", "kpi-dashboard"),
    "comparison_view.py": ("output/comparison.json", "pricing-comparison"),
}


def _fixture_text(name: str) -> str:
    return FIXTURES.joinpath(name).read_text(encoding="utf-8")


def _render(document: dict) -> str:
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _compact_document() -> dict:
    """A small compact document that exercises every root key."""
    return {
        "schema_version": 1,
        "format": "compact",
        "id": "ops_console",
        "title": "Ops console",
        "aesthetic": "aesthetic.calm_ops",
        "regions": [{"id": "main"}, {"id": "side", "parent": "root", "layout": "stack"}],
        "motifs": [
            {"id": "intro", "kind": "hero", "eyebrow": "Ops", "title": "Incident console", "description": "Live."},
            {
                "id": "kpis",
                "kind": "dashboard",
                "group": "cards",
                "cards": [
                    {"id": "open", "label": "Open", "value": 4},
                    {"label": "Blocked", "value": "1", "present_as": "badge"},
                ],
            },
            {
                "id": "incidents",
                "kind": "table",
                "group": "rows",
                "rows": [
                    {"id": "inc402", "label": "INC-402", "value": "Replica lag", "values": {"owner": "Ada"}},
                    {"label": "INC-403", "value": "Queue depth"},
                ],
            },
            {
                "id": "contact",
                "kind": "form",
                "region": "side",
                "group": "fields",
                "fields": [{"id": "message", "label": "Message", "value": ""}],
            },
        ],
        "inputs": [{"id": "query", "label": "Search", "region": "side"}],
        "styles": [
            {"target": "motif:kpis", "token": "density.compact"},
            {"id": "s2", "target": "open_value", "token": "emphasis.high"},
        ],
        "actions": [
            {
                "id": "send",
                "kind": "submit",
                "label": "Send",
                "region": "side",
                "target": "motif:contact",
                "payload": ["message"],
            },
            {
                "id": "search",
                "kind": "search",
                "label": "Search",
                "region": "side",
                "collection": "incidents",
                "payload": ["query"],
            },
        ],
    }


def _builder_twin() -> dict:
    """The ViewSpecBuilder calls that ``_compact_document`` serializes."""
    builder = ViewSpecBuilder("ops_console", root_attrs={"title": "Ops console"}, default_main_region=False)
    builder.add_region("main", parent_region="root", role="main", layout="stack", min_children=1)
    builder.add_region("side", parent_region="root", role="side", layout="stack", min_children=1)
    builder.add_hero("intro", eyebrow="Ops", title="Incident console", description="Live.", region="main")
    dashboard = builder.add_dashboard("kpis", region="main", group_id="cards")
    dashboard.add_card(label="Open", value=4, id="open")
    dashboard.add_card(label="Blocked", value="1", value_present_as="badge")
    table = builder.add_table("incidents", region="main", group_id="rows")
    table.add_row(label="INC-402", value="Replica lag", values={"owner": "Ada"}, id="inc402")
    table.add_row(label="INC-403", value="Queue depth")
    form = builder.add_form("contact", region="side", group_id="fields")
    form.add_field(label="Message", value="", id="message")
    query = builder.add_text_input("query", label="Search", region="side")
    builder.add_style("style_1", "motif:kpis", "density.compact")
    builder.add_style("s2", "open_value", "emphasis.high")
    builder.set_aesthetic_profile("aesthetic.calm_ops")
    builder.add_action(
        "send", "submit", "Send", target_region="side", target_ref="motif:contact", payload_bindings=["message_value"]
    )
    builder.add_collection_action(
        "search", "search", "Search", collection_id="incidents", target_region="side", payload_bindings=[query]
    )
    return {"schema_version": 1, **builder.build_bundle().to_json()}


# --- twin identity --------------------------------------------------------------------------


@pytest.mark.parametrize("kind", STARTER_INTENT_KINDS)
def test_every_starter_has_a_compact_twin_that_normalizes_byte_identically(kind):
    compact = starter_compact_payload(kind)

    assert detect_compact(compact)
    assert SCHEMA.is_valid(compact), sorted(error.message for error in SCHEMA.iter_errors(compact))
    assert canonical_json_text(normalize(compact)) == canonical_json_text(starter_intent_payload(kind))
    assert validate_agent_intent_bundle(normalize(compact)).valid


def test_compact_document_normalizes_to_the_builder_calls_it_serializes():
    assert canonical_json_text(normalize(_compact_document())) == canonical_json_text(_builder_twin())


def test_example_builder_scripts_have_byte_identical_compact_twins(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    for script, (output, case_id) in EXAMPLE_TWINS.items():
        subprocess.run(
            [sys.executable, str(ROOT / "examples" / script)],
            cwd=tmp_path,
            env=env,
            stdin=subprocess.DEVNULL,
            check=True,
            capture_output=True,
            text=True,
        )
        exported = {"schema_version": 1, **json.loads(tmp_path.joinpath(output).read_text(encoding="utf-8"))}
        canonical_text = _fixture_text(f"{case_id}.intent.json")
        compact_text = _fixture_text(f"{case_id}.compact.json")

        # The checked-in canonical twin is the script's own output, and the compact twin normalizes to it.
        assert canonical_text == _render(exported), case_id
        assert _render(normalize(json.loads(compact_text))) == canonical_text, case_id
        assert len(compact_text.encode("utf-8")) < len(canonical_text.encode("utf-8")) / 4, case_id


def test_compact_starter_twin_fixture_is_the_init_starter():
    assert json.loads(_fixture_text("data-dense-dashboard.compact.json")) == starter_compact_payload("dashboard")
    published = json.loads(ROOT.joinpath("demos/agent-intent-example.dashboard.json").read_text(encoding="utf-8"))
    assert normalize(starter_compact_payload("dashboard")) == published


@pytest.mark.parametrize(("compact_id", "canonical_id"), sorted(TWIN_PAIRS.items()))
def test_corpus_twin_pairs_pin_identical_artifact_hashes(compact_id, canonical_id):
    compact_case = GOLDENS["cases"][compact_id]
    canonical_case = GOLDENS["cases"][canonical_id]

    assert compact_case["source_format"] == "compact"
    assert "source_format" not in canonical_case
    compact_document = json.loads(ROOT.joinpath(compact_case["source"]).read_text(encoding="utf-8"))
    assert compact_case["canonical_source_hash"] == source_hash(canonical_json_text(normalize(compact_document)))
    for target in INTENT_COMPILE_TARGETS:
        compact_pin = compact_case["targets"][target]
        canonical_pin = canonical_case["targets"][target]
        assert compact_pin["artifact_hash"] == canonical_pin["artifact_hash"], target
        assert compact_pin["semantic_digest"] == canonical_pin["semantic_digest"], target
        assert (
            compact_pin["files"][compact_pin["artifact_file"]] == canonical_pin["files"][canonical_pin["artifact_file"]]
        )
        assert compact_pin["files"]["diagnostics.json"] == canonical_pin["files"]["diagnostics.json"], target


@pytest.mark.parametrize(("compact_id", "canonical_id"), sorted(TWIN_PAIRS.items()))
def test_corpus_twin_pairs_compile_to_identical_artifacts(tmp_path, compact_id, canonical_id):
    sources = {case_id: ROOT / GOLDENS["cases"][case_id]["source"] for case_id in (compact_id, canonical_id)}
    staged = {}
    for case_id, path in sources.items():
        copy_path = tmp_path / f"{case_id}.json"
        copy_path.write_bytes(path.read_bytes())
        staged[case_id] = copy_path
    for target in ("html-tailwind", "react-tailwind-tsx"):
        manifests = {}
        for case_id, path in staged.items():
            out_dir = tmp_path / case_id / target
            result = compile_intent_bundle_file_tool(path, out_dir, target=target, cwd=tmp_path)
            assert result["ok"] is True, result
            manifests[case_id] = json.loads((out_dir / "provenance_manifest.json").read_text(encoding="utf-8"))
            assert check_artifact_dir(out_dir)["ok"] is True
        compact_manifest, canonical_manifest = manifests[compact_id], manifests[canonical_id]
        artifact = compact_manifest["artifact_file"]
        assert (tmp_path / compact_id / target / artifact).read_bytes() == (
            tmp_path / canonical_id / target / artifact
        ).read_bytes()
        assert compact_manifest["artifact_hash"] == canonical_manifest["artifact_hash"]
        assert compact_manifest["semantic_digest"] == canonical_manifest["semantic_digest"]
        assert compact_manifest["nodes"] == canonical_manifest["nodes"]
        assert compact_manifest["source_format"] == "compact"
        assert compact_manifest["compact_source_sha256"] == compact_manifest["raw_source_hash"]
        assert compact_manifest["compact_source_sha256"] == source_hash(staged[compact_id].read_text(encoding="utf-8"))
        assert (
            compact_manifest["source_hash"]
            == prepare_intent_source(staged[compact_id].read_text(encoding="utf-8")).manifest_source_hash
        )
        assert "source_format" not in canonical_manifest
        assert "compact_source_sha256" not in canonical_manifest
        assert canonical_manifest["source_hash"] == canonical_manifest["raw_source_hash"]


# --- determinism ----------------------------------------------------------------------------


def test_normalize_is_deterministic_across_runs_and_json_round_trips():
    document = _compact_document()
    first = canonical_json_text(normalize(document))
    second = canonical_json_text(normalize(json.loads(json.dumps(document))))
    third = canonical_json_text(normalize(json.loads(json.dumps(document, sort_keys=True, separators=(",", ":")))))

    assert first == second == third
    assert document == _compact_document(), "normalize does not mutate its input"


_LABEL = st.text(alphabet=st.characters(categories=("L", "N", "P", "Zs")), min_size=1, max_size=12)
_VALUE = st.one_of(_LABEL, st.integers(min_value=-(2**53), max_value=2**53), st.booleans())


def _item(kind: str):
    if kind == "outline":
        return st.fixed_dictionaries({"label": _LABEL})
    if kind == "form":
        return st.fixed_dictionaries({"label": _LABEL}, optional={"value": _LABEL})
    if kind == "list":
        return st.fixed_dictionaries({"label": _LABEL}, optional={"value": _VALUE, "description": _LABEL})
    # Tables, dashboards, comparisons and details need a value binding to compile.
    return st.fixed_dictionaries({"label": _LABEL, "value": _VALUE})


def _motif(index: int):
    def build(kind: str):
        item_key = ITEM_KEY_BY_KIND[kind]
        min_items = 2 if kind == "comparison" else 1
        return st.fixed_dictionaries(
            {
                "id": st.just(f"m{index}"),
                "kind": st.just(kind),
                item_key: st.lists(_item(kind), min_size=min_items, max_size=4),
            },
            optional={"group": st.just(f"g{index}")},
        )

    return st.sampled_from(["table", "dashboard", "outline", "comparison", "list", "form", "detail"]).flatmap(build)


_DOCUMENTS = st.integers(min_value=1, max_value=4).flatmap(
    lambda count: st.fixed_dictionaries(
        {
            "format": st.just("compact"),
            "id": st.just("doc"),
            "motifs": st.tuples(*[_motif(index) for index in range(count)]).map(list),
        },
        optional={"title": _LABEL, "schema_version": st.just(1)},
    )
)


@settings(max_examples=40, deadline=None)
@given(document=_DOCUMENTS)
def test_generated_compact_documents_normalize_deterministically_to_valid_bundles(document):
    assert SCHEMA.is_valid(document), sorted(error.message for error in SCHEMA.iter_errors(document))
    first = normalize(document)
    second = normalize(json.loads(json.dumps(document)))

    assert canonical_json_text(first) == canonical_json_text(second)
    result = validate_agent_intent_bundle(first)
    assert result.valid, [issue.to_json() for issue in result.issues]


# --- fail closed ----------------------------------------------------------------------------


def _with(mutate):
    document = _compact_document()
    mutate(document)
    return document


def _set_rows_cards(document):
    document["motifs"][2]["cards"] = document["motifs"][2].pop("rows")


REJECTIONS = [
    pytest.param(lambda d: d.update(extra=1), "COMPACT_UNKNOWN_FIELD", "$.extra", id="root-unknown-key"),
    pytest.param(
        lambda d: d.__setitem__("regions", [{"id": "main", "min": 1}]),
        "COMPACT_UNKNOWN_FIELD",
        "$.regions[0].min",
        id="region-unknown-key",
    ),
    pytest.param(
        lambda d: d["motifs"][2]["rows"][0].update(present_as="badge"),
        "COMPACT_UNKNOWN_FIELD",
        "$.motifs[2].rows[0].present_as",
        id="row-unknown-key",
    ),
    pytest.param(_set_rows_cards, "COMPACT_UNKNOWN_FIELD", "$.motifs[2].cards", id="table-with-cards"),
    pytest.param(_set_rows_cards, "COMPACT_MOTIF_ITEMS_MISSING", "$.motifs[2].rows", id="table-without-rows"),
    pytest.param(
        lambda d: d["motifs"][2].__setitem__("rows", []),
        "COMPACT_MOTIF_ITEMS_MISSING",
        "$.motifs[2].rows",
        id="empty-rows",
    ),
    pytest.param(lambda d: d.pop("id"), "COMPACT_MISSING_FIELD", "$.id", id="missing-id"),
    pytest.param(lambda d: d.__setitem__("id", "bad id"), "COMPACT_FIELD_INVALID", "$.id", id="unsafe-id"),
    pytest.param(
        lambda d: d.__setitem__("schema_version", 2),
        "UNSUPPORTED_SCHEMA_VERSION",
        "$.schema_version",
        id="schema-version",
    ),
    pytest.param(lambda d: d.__setitem__("format", "canonical"), "COMPACT_FIELD_INVALID", "$.format", id="format"),
    pytest.param(
        lambda d: d.__setitem__("aesthetic", "aesthetic.unknown"),
        "COMPACT_FIELD_INVALID",
        "$.aesthetic",
        id="aesthetic",
    ),
    pytest.param(
        lambda d: d["motifs"][1].__setitem__("kind", "chat"),
        "COMPACT_MOTIF_KIND_UNKNOWN",
        "$.motifs[1].kind",
        id="motif-kind",
    ),
    pytest.param(
        lambda d: d["motifs"][1].pop("kind"), "COMPACT_MISSING_FIELD", "$.motifs[1].kind", id="motif-kind-missing"
    ),
    pytest.param(lambda d: d["motifs"][1].pop("id"), "COMPACT_MISSING_FIELD", "$.motifs[1].id", id="motif-id-missing"),
    pytest.param(
        lambda d: d["motifs"][0].pop("title"), "COMPACT_MISSING_FIELD", "$.motifs[0].title", id="hero-title-missing"
    ),
    pytest.param(
        lambda d: d["motifs"][1]["cards"][1].pop("value"),
        "COMPACT_MISSING_FIELD",
        "$.motifs[1].cards[1].value",
        id="card-value-missing",
    ),
    pytest.param(
        lambda d: d["motifs"][1]["cards"][0].__setitem__("value", None),
        "COMPACT_FIELD_INVALID",
        "$.motifs[1].cards[0].value",
        id="null-value",
    ),
    pytest.param(
        lambda d: d["motifs"][1]["cards"][0].__setitem__("present_as", "huge"),
        "COMPACT_FIELD_INVALID",
        "$.motifs[1].cards[0].present_as",
        id="present-as",
    ),
    pytest.param(
        lambda d: d["motifs"][2]["rows"][0]["values"].__setitem__("label", "x"),
        "COMPACT_FIELD_INVALID",
        "$.motifs[2].rows[0].values.label",
        id="values-label",
    ),
    pytest.param(
        lambda d: d["motifs"][2]["rows"][0]["values"].__setitem__("bad key", "x"),
        "COMPACT_FIELD_INVALID",
        "$.motifs[2].rows[0].values.bad key",
        id="values-unsafe-key",
    ),
    pytest.param(
        lambda d: d["motifs"][2].__setitem__("id", "kpis"),
        "COMPACT_DUPLICATE_ID",
        "$.motifs[2].id",
        id="duplicate-motif-id",
    ),
    pytest.param(
        lambda d: d["motifs"][2].__setitem__("group", "cards"),
        "COMPACT_DUPLICATE_ID",
        "$.motifs[2].group",
        id="duplicate-group",
    ),
    pytest.param(
        lambda d: d["motifs"][2]["rows"][1].__setitem__("id", "inc402"),
        "COMPACT_DUPLICATE_ID",
        "$.motifs[2].rows[1].id",
        id="duplicate-item-id",
    ),
    pytest.param(
        lambda d: d["motifs"][2]["rows"][1].__setitem__("id", "ops_console"),
        "COMPACT_DUPLICATE_ID",
        "$.motifs[2].rows[1].id",
        id="item-id-is-view-id",
    ),
    pytest.param(
        # The hero claims node "open" first, so the card that reuses it is the duplicate.
        lambda d: d["motifs"][0].__setitem__("id", "open"),
        "COMPACT_DUPLICATE_ID",
        "$.motifs[1].cards[0].id",
        id="hero-id-is-node-id",
    ),
    pytest.param(
        lambda d: d["inputs"][0].__setitem__("id", "message"),
        "COMPACT_DUPLICATE_ID",
        "$.inputs[0].id",
        id="input-id-is-field-id",
    ),
    pytest.param(
        # Row inc402 with extra attribute owner_label derives binding inc402_owner_label; so does the
        # label of a row whose id is inc402_owner.
        lambda d: (
            d["motifs"][2]["rows"][0].__setitem__("values", {"owner_label": "Ada"}),
            d["motifs"][2]["rows"][1].__setitem__("id", "inc402_owner"),
        ),
        "COMPACT_DUPLICATE_ID",
        "$.motifs[2].rows[1]",
        id="derived-binding-collision",
    ),
    pytest.param(
        lambda d: d["motifs"][1].__setitem__("region", "nowhere"),
        "COMPACT_REGION_UNKNOWN",
        "$.motifs[1].region",
        id="motif-region",
    ),
    pytest.param(
        lambda d: d["inputs"][0].__setitem__("region", "nowhere"),
        "COMPACT_REGION_UNKNOWN",
        "$.inputs[0].region",
        id="input-region",
    ),
    pytest.param(
        lambda d: d["regions"].append({"id": "deep", "parent": "missing"}),
        "COMPACT_REGION_UNKNOWN",
        "$.regions[2].parent",
        id="region-parent",
    ),
    pytest.param(
        lambda d: d["regions"].append({"id": "root"}), "COMPACT_DUPLICATE_ID", "$.regions[2].id", id="region-root"
    ),
    pytest.param(
        lambda d: d["regions"].append({"id": "side"}), "COMPACT_DUPLICATE_ID", "$.regions[2].id", id="region-duplicate"
    ),
    pytest.param(
        lambda d: d["regions"][1].__setitem__("layout", "flex"),
        "COMPACT_FIELD_INVALID",
        "$.regions[1].layout",
        id="region-layout",
    ),
    pytest.param(
        lambda d: d["actions"][0].__setitem__("kind", "bulk_action"),
        "COMPACT_ACTION_KIND_UNSUPPORTED",
        "$.actions[0].kind",
        id="bulk-action",
    ),
    pytest.param(
        lambda d: d["actions"][0].__setitem__("kind", "teleport"),
        "COMPACT_ACTION_KIND_UNSUPPORTED",
        "$.actions[0].kind",
        id="action-kind",
    ),
    pytest.param(
        lambda d: d["actions"][0].__setitem__("target", "motif:missing"),
        "COMPACT_ACTION_TARGET_UNKNOWN",
        "$.actions[0].target",
        id="action-target",
    ),
    pytest.param(
        lambda d: d["actions"][0].__setitem__("target", "items"),
        "COMPACT_FIELD_INVALID",
        "$.actions[0].target",
        id="action-target-bare",
    ),
    pytest.param(
        lambda d: d["actions"][1].__setitem__("collection", "intro"),
        "COMPACT_ACTION_TARGET_UNKNOWN",
        "$.actions[1].collection",
        id="collection-not-a-collection",
    ),
    pytest.param(
        lambda d: d["actions"][1].__setitem__("target", "motif:incidents"),
        "COMPACT_FIELD_INVALID",
        "$.actions[1].collection",
        id="target-and-collection",
    ),
    pytest.param(
        lambda d: d["actions"][0].__setitem__("payload", ["nothing"]),
        "COMPACT_ACTION_PAYLOAD_UNKNOWN",
        "$.actions[0].payload[0]",
        id="payload-unknown",
    ),
    pytest.param(
        lambda d: d["actions"][1].__setitem__("id", "send"),
        "COMPACT_DUPLICATE_ID",
        "$.actions[1].id",
        id="duplicate-action-id",
    ),
    pytest.param(
        lambda d: d["actions"][0].pop("label"), "COMPACT_MISSING_FIELD", "$.actions[0].label", id="action-label"
    ),
    pytest.param(lambda d: d["styles"][0].pop("token"), "COMPACT_MISSING_FIELD", "$.styles[0].token", id="style-token"),
    pytest.param(
        lambda d: d["styles"][0].__setitem__("id", "s2"),
        "COMPACT_DUPLICATE_ID",
        "$.styles[1].id",
        id="duplicate-style-id",
    ),
    pytest.param(lambda d: d.__setitem__("motifs", {}), "COMPACT_FIELD_INVALID", "$.motifs", id="motifs-not-array"),
    pytest.param(
        lambda d: d.__setitem__("root", {"min_children": -1}),
        "COMPACT_FIELD_INVALID",
        "$.root.min_children",
        id="root-min-children",
    ),
]


@pytest.mark.parametrize(("mutate", "code", "path"), REJECTIONS)
def test_compact_constructs_the_builder_cannot_express_are_rejected_with_a_code(mutate, code, path):
    document = _with(mutate)

    canonical, issues = normalize_with_issues(document)

    assert canonical is None
    found = [issue for issue in issues if issue.code == code and issue.path == path]
    assert found, [issue.to_json() for issue in issues]
    for issue in issues:
        assert issue.code in ERROR_CODES
        payload = issue.to_json()
        assert set(payload) == {"severity", "code", "path", "message", "suggestion"}
        assert payload["severity"] == "error"
        assert payload["path"].startswith("$")
        assert payload["message"] and payload["suggestion"] != DEFAULT_AGENT_REPAIR_SUGGESTION
    with pytest.raises(CompactFormError) as raised:
        normalize(document)
    assert raised.value.issues == issues
    assert code in str(raised.value) or issues[0].code in str(raised.value)


def test_every_compact_code_is_registered_and_exercised():
    assert set(COMPACT_ERROR_CODES) <= ERROR_CODES
    exercised = {code for _mutate, code, _path in (param.values for param in REJECTIONS)}
    exercised.add("COMPACT_TOO_LARGE")  # text-level; test_compact_text_over_the_cap_is_rejected_before_parsing
    assert set(COMPACT_ERROR_CODES) <= exercised


def test_schema_rejections_are_a_lower_bound_on_normalize_rejections():
    accepted = _compact_document()
    assert SCHEMA.is_valid(accepted)
    assert normalize_with_issues(accepted)[1] == []
    schema_rejected = 0
    for param in REJECTIONS:
        mutate = param.values[0]
        document = _with(mutate)
        if not SCHEMA.is_valid(document):
            schema_rejected += 1
            assert normalize_with_issues(document)[0] is None, param.id
    assert schema_rejected >= len(REJECTIONS) // 2


def test_compact_text_over_the_cap_is_rejected_before_parsing():
    document = _compact_document()
    document["title"] = "x" * MAX_COMPACT_BYTES
    text = json.dumps(document)

    prepared = prepare_intent_source(text)
    assert prepared.compact
    assert prepared.result.document is None
    assert prepared.canonical_text is None
    assert [issue.code for issue in prepared.result.issues] == ["COMPACT_TOO_LARGE"]
    payload = validate_intent_text(text)
    assert payload["ok"] is False
    assert payload["source_format"] == "compact"
    assert payload["issues"][0]["code"] == "COMPACT_TOO_LARGE"
    assert payload["correction_prompt"]


def test_the_cap_also_applies_to_the_normalized_bundle():
    # 190 rows with long labels: the compact text stays under 256 KB while the canonical bundle,
    # which repeats each row across its node and two bindings, does not.
    label = "y" * 1200
    document = {
        "format": "compact",
        "id": "wide",
        "motifs": [{"id": "items", "kind": "table", "rows": [{"label": label, "value": "v"} for _ in range(190)]}],
    }
    text = json.dumps(document)
    assert len(text.encode("utf-8")) < MAX_COMPACT_BYTES

    payload = validate_intent_text(text)

    assert payload["ok"] is False
    assert payload["issues"][0]["code"] == "INTENT_TOO_LARGE"


def test_compact_validation_issues_keep_the_shared_shape_and_a_correction_prompt():
    document = _with(lambda d: d["motifs"][1]["cards"].append({"label": "Open", "value": 1, "id": "open"}))

    payload = validate_intent_text(json.dumps(document))

    assert payload["ok"] is False
    assert payload["source_format"] == "compact"
    assert payload["compile_check"] == "failed"
    assert [issue["code"] for issue in payload["issues"]] == ["COMPACT_DUPLICATE_ID"]
    assert set(payload["issues"][0]) == {"severity", "code", "path", "message", "suggestion"}
    assert payload["repair_checklist"]
    assert "COMPACT_DUPLICATE_ID" in payload["correction_prompt"]


def test_canonical_validator_issues_on_a_compact_source_keep_canonical_paths():
    # A style target the compact form does not resolve itself is reported by the canonical
    # validator against the normalized bundle, so the path is the canonical one.
    document = _with(lambda d: d["styles"].append({"target": "binding:missing_value", "token": "tone.accent"}))

    payload = validate_intent_text(json.dumps(document))

    assert payload["ok"] is False
    assert payload["source_format"] == "compact"
    codes = {issue["code"] for issue in payload["issues"]}
    assert "UNKNOWN_STYLE_TARGET" in codes
    assert all(issue["path"].startswith("$.view_spec") for issue in payload["issues"])


# --- the canonical form is untouched ----------------------------------------------------------


def test_canonical_sources_load_as_before():
    canonical = json.dumps(starter_intent_payload("dashboard"), indent=2, sort_keys=True)

    prepared = prepare_intent_source(canonical)

    assert prepared.source_format == "canonical" and not prepared.compact
    assert prepared.result.document == starter_intent_payload("dashboard")
    assert prepared.canonical_text == canonical
    assert prepared.manifest_source_hash == prepared.raw_source_hash == source_hash(canonical)
    assert validate_intent_text(canonical) == {
        "schema_version": 1,
        "ok": True,
        "compile_check": "passed",
        "issues": [],
        "fixes": [],
        "repair_checklist": [],
        "correction_prompt": None,
    }
    broken = prepare_intent_source("{not json")
    assert broken.source_format == "canonical" and broken.result.document is None
    assert [issue.code for issue in broken.result.issues] == ["INVALID_JSON"]
    assert validate_intent_text("{not json")["issues"][0]["code"] == "INVALID_JSON"


def test_a_format_key_on_a_canonical_bundle_is_still_an_unknown_field():
    payload = dict(starter_intent_payload("dashboard"), format="canonical")

    result = validate_intent_text(json.dumps(payload))

    assert result["ok"] is False
    assert "source_format" not in result
    assert any(issue["code"] == "UNKNOWN_FIELD" and issue["path"] == "$.format" for issue in result["issues"])


def test_source_kind_detects_a_compact_bundle_whatever_the_file_name(tmp_path):
    path = tmp_path / "viewspec.app.json"
    path.write_text(json.dumps(starter_compact_payload("table")), encoding="utf-8")

    assert source_kind(path) == "intent_bundle"
    assert check(path)["ok"] is True


# --- schema, prompt section, exports --------------------------------------------------------


def _object_schemas(node, path="#"):
    if isinstance(node, dict):
        if "properties" in node:
            yield path, node
        for key, value in node.items():
            yield from _object_schemas(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _object_schemas(value, f"{path}/{index}")


def test_compact_schema_is_strict_and_well_formed():
    Draft202012Validator.check_schema(COMPACT_FORM_SCHEMA)

    assert (
        COMPACT_FORM_SCHEMA["$id"] == COMPACT_FORM_SCHEMA_ID == "https://viewspec.dev/agent-intent-compact.schema.json"
    )
    assert COMPACT_FORM_SCHEMA["required"] == ["format", "id"]
    assert COMPACT_FORM_SCHEMA["properties"]["format"]["const"] == "compact"
    assert COMPACT_FORM_SCHEMA["x-viewspec-compact-item-keys"] == ITEM_KEY_BY_KIND
    for path, schema in _object_schemas(COMPACT_FORM_SCHEMA):
        assert schema.get("additionalProperties") is False, path
    assert SCHEMA.is_valid(_compact_document())
    assert not SCHEMA.is_valid(dict(_compact_document(), extra=1))
    assert not SCHEMA.is_valid(starter_intent_payload("dashboard"))


def test_published_compact_schema_matches_the_runtime_contract():
    published = json.loads(ROOT.joinpath("demos/agent-intent-compact.schema.json").read_text(encoding="utf-8"))
    facts = json.loads(ROOT.joinpath("demos/public-facts.json").read_text(encoding="utf-8"))

    assert published == COMPACT_FORM_SCHEMA
    assert "agent-intent-compact.schema.json" in facts["agent_assets"]["payload_files"]
    assert facts["agent_assets"]["compact_schema_id"] == COMPACT_FORM_SCHEMA_ID


def test_prompt_section_is_bounded_and_names_the_contract():
    assert len(COMPACT_FORM_PROMPT_SECTION.encode("utf-8")) <= 1500
    assert COMPACT_FORM_PROMPT_SECTION.isascii()
    for expected in (
        '"format": "compact"',
        "rows",
        "cards",
        "fields",
        "items",
        "--canonical-out",
        "bulk_action",
        "Unknown keys are rejected",
    ):
        assert expected in COMPACT_FORM_PROMPT_SECTION, expected
    example = COMPACT_FORM_PROMPT_SECTION.strip().splitlines()[-1]
    assert validate_agent_intent_bundle(normalize(json.loads(example))).valid


# --- CLI, Python API and MCP ------------------------------------------------------------------


def test_init_compact_writes_a_small_starter_that_check_normalizes_to_the_init_starter(tmp_path, capsys):
    compact_path = tmp_path / "starter.compact.json"
    canonical_path = tmp_path / "canonical.json"
    intent_path = tmp_path / "viewspec.intent.json"

    assert cli_main(["init", "compact", "--out", str(compact_path)]) == 0
    assert capsys.readouterr().out == f"{compact_path}\n"
    compact = json.loads(compact_path.read_text(encoding="utf-8"))
    assert compact == starter_compact_payload("dashboard")
    assert len(json.dumps(compact, separators=(",", ":")).encode("utf-8")) <= 700
    assert compact_path.read_text(encoding="utf-8") == canonical_json_text(compact)

    assert cli_main(["check", str(compact_path), "--json", "--canonical-out", str(canonical_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["source_format"] == "compact"
    assert payload["canonical_out"] == str(canonical_path)
    assert cli_main(["init", "--out", str(intent_path)]) == 0
    capsys.readouterr()
    assert canonical_path.read_bytes() == intent_path.read_bytes()

    assert cli_main(["check", str(compact_path), "--canonical-out", str(canonical_path)]) == 0
    assert capsys.readouterr().out == f"ok: compile_check=passed\ncanonical_out: {canonical_path}\n"


def test_init_compact_json_envelope_default_name_kind_and_force(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert cli_main(["init", "compact", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["paths"] == {"compact": INIT_DEFAULT_OUT["compact"]} == {"compact": "viewspec.compact.json"}
    assert payload["metadata"]["kind"] == "dashboard"
    assert any("--canonical-out" in action for action in payload["next_actions"])

    assert cli_main(["init", "compact", "--json"]) == 2
    refused = json.loads(capsys.readouterr().out)
    assert refused["ok"] is False
    assert refused["errors"][0]["code"] == "IO_ERROR"

    assert cli_main(["init", "compact", "--kind", "form", "--force"]) == 0
    capsys.readouterr()
    assert json.loads(Path("viewspec.compact.json").read_text(encoding="utf-8")) == starter_compact_payload("form")
    assert check("viewspec.compact.json")["ok"] is True


def test_canonical_out_is_not_written_when_a_compact_source_does_not_normalize(tmp_path, capsys):
    # Compact issues name the compact file itself; there is no canonical document to write.
    compact_path = tmp_path / "bad.compact.json"
    compact_path.write_text(json.dumps(_with(lambda d: d["motifs"][2].pop("rows"))), encoding="utf-8")
    canonical_path = tmp_path / "canonical.json"

    assert cli_main(["check", str(compact_path), "--json", "--canonical-out", str(canonical_path)]) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert "canonical_out" not in payload
    assert payload["issues"][0]["code"] == "COMPACT_MOTIF_ITEMS_MISSING"
    assert not canonical_path.exists()


def test_canonical_out_refuses_the_input_file_a_directory_and_an_app_bundle(tmp_path, capsys):
    compact_path = tmp_path / "viewspec.compact.json"
    compact_path.write_text(canonical_json_text(starter_compact_payload("dashboard")), encoding="utf-8")
    before = compact_path.read_bytes()
    app_path = tmp_path / "viewspec.app.json"
    assert cli_main(["init", "app", "--out", str(app_path)]) == 0
    capsys.readouterr()

    assert cli_main(["check", str(compact_path), "--canonical-out", str(compact_path)]) == 2
    assert "must not be the input file" in capsys.readouterr().err
    assert compact_path.read_bytes() == before
    assert cli_main(["check", str(compact_path), "--canonical-out", str(tmp_path)]) == 2
    assert "not a directory" in capsys.readouterr().err
    assert cli_main(["check", str(app_path), "--json", "--canonical-out", str(tmp_path / "c.json")]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "IntentBundle source" in payload["errors"][0]["message"]
    assert not (tmp_path / "c.json").exists()
    assert (
        cli_main(
            ["compile", str(app_path), "--out", str(tmp_path / "app-dist"), "--canonical-out", str(tmp_path / "c.json")]
        )
        == 2
    )
    capsys.readouterr()
    assert (
        cli_main(
            [
                "compile",
                str(compact_path),
                "--out",
                str(tmp_path / "dist"),
                "--canonical-out",
                str(tmp_path / "dist" / "index.html"),
            ]
        )
        == 2
    )
    assert "compiled output file" in capsys.readouterr().err


def test_canonical_out_for_a_canonical_source_writes_the_same_document_in_init_formatting(tmp_path, capsys):
    source = tmp_path / "loose.json"
    document = starter_intent_payload("table")
    source.write_text(json.dumps(document), encoding="utf-8")
    out = tmp_path / "canonical.json"

    assert cli_main(["check", str(source), "--json", "--canonical-out", str(out)]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert "source_format" not in payload
    assert payload["canonical_out"] == str(out)
    assert out.read_text(encoding="utf-8") == canonical_json_text(document)


def test_compile_compact_writes_the_canonical_twins_artifact_and_records_both_hashes(tmp_path, capsys):
    compact_path = tmp_path / "viewspec.compact.json"
    compact_path.write_text(canonical_json_text(starter_compact_payload("dashboard")), encoding="utf-8")
    canonical_path = tmp_path / "viewspec.intent.json"
    compact_out = tmp_path / "compact-dist"
    canonical_out = tmp_path / "canonical-dist"

    exit_code = cli_main(
        ["compile", str(compact_path), "--out", str(compact_out), "--canonical-out", str(canonical_path), "--json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["paths"]["canonical_out"] == str(canonical_path)
    assert payload["metadata"]["source_format"] == "compact"
    assert payload["fixes"] == []
    assert cli_main(["compile", str(canonical_path), "--out", str(canonical_out)]) == 0
    capsys.readouterr()
    assert (compact_out / "index.html").read_bytes() == (canonical_out / "index.html").read_bytes()
    assert (compact_out / "diagnostics.json").read_bytes() == (canonical_out / "diagnostics.json").read_bytes()

    manifest = json.loads((compact_out / "provenance_manifest.json").read_text(encoding="utf-8"))
    twin = json.loads((canonical_out / "provenance_manifest.json").read_text(encoding="utf-8"))
    compact_text = compact_path.read_text(encoding="utf-8")
    assert manifest["source_format"] == "compact"
    assert manifest["raw_source_hash"] == manifest["compact_source_sha256"] == source_hash(compact_text)
    assert manifest["source_hash"] == source_hash(canonical_path.read_text(encoding="utf-8")) == twin["source_hash"]
    assert manifest["artifact_hash"] == twin["artifact_hash"]
    assert manifest["semantic_digest"] == twin["semantic_digest"]
    assert "source_format" not in twin and "compact_source_sha256" not in twin
    assert list(manifest)[:8] == [
        "version",
        "manifest_schema_version",
        "kind",
        "sdk_version",
        "source_name",
        "raw_source_hash",
        "source_hash",
        "source_format",
    ]

    assert cli_main(["check", str(compact_out), "--json"]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["ok"] is True
    assert checked["manifest_summary"]["source_format"] == "compact"
    assert cli_main(["check", str(canonical_out), "--json"]) == 0
    # A canonical compile leaves the summary as it was: no source_format key means canonical.
    assert "source_format" not in json.loads(capsys.readouterr().out)["manifest_summary"]


def test_compile_rejects_an_invalid_compact_source_before_writing(tmp_path, capsys):
    compact_path = tmp_path / "bad.compact.json"
    compact_path.write_text(
        json.dumps(_with(lambda d: d["actions"][0].__setitem__("kind", "bulk_action"))), encoding="utf-8"
    )
    out_dir = tmp_path / "dist"

    assert cli_main(["compile", str(compact_path), "--out", str(out_dir), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is False
    assert payload["errors"][0]["code"] == "COMPACT_ACTION_KIND_UNSUPPORTED"
    assert payload["validation"]["source_format"] == "compact"
    assert not out_dir.exists()


def test_check_rejects_a_manifest_that_claims_a_compact_source_without_its_hash(tmp_path):
    compact_path = tmp_path / "viewspec.compact.json"
    compact_path.write_text(canonical_json_text(starter_compact_payload("dashboard")), encoding="utf-8")
    out_dir = tmp_path / "dist"
    assert compile_intent_bundle_file_tool(compact_path, out_dir, cwd=tmp_path)["ok"] is True
    manifest_path = out_dir / "provenance_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    manifest.pop("compact_source_sha256")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report = check_artifact_dir(out_dir)
    assert report["ok"] is False
    assert any("compact_source_sha256" in error for error in report["errors"])

    manifest["source_format"] = "zipped"
    manifest["compact_source_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report = check_artifact_dir(out_dir)
    assert report["ok"] is False
    assert any("source_format must be canonical or compact" in error for error in report["errors"])


def test_python_api_and_prove_accept_a_compact_source(tmp_path):
    compact_path = tmp_path / "viewspec.compact.json"
    compact_path.write_text(canonical_json_text(starter_compact_payload("form")), encoding="utf-8")

    report = check(compact_path)
    assert report["ok"] is True
    assert report["source_format"] == "compact"

    proof = prove(compact_path, out_dir=tmp_path / ".viewspec-proof")
    assert proof["ok"] is True, proof["errors"]
    assert (tmp_path / ".viewspec-proof" / "PROOF.md").is_file()
    manifest = json.loads(
        (tmp_path / ".viewspec-proof" / "artifact" / "provenance_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["source_format"] == "compact"
    assert manifest["compact_source_sha256"] == source_hash(compact_path.read_text(encoding="utf-8"))


def test_mcp_init_writes_a_compact_starter_the_other_tools_accept(tmp_path):
    tools = ViewSpecTools(tmp_path)
    try:
        written = tools.init(what="compact", kind="detail")
        assert written["ok"] is True
        assert written["paths"] == {"compact": str(tmp_path / "viewspec.compact.json")}
        assert json.loads((tmp_path / "viewspec.compact.json").read_text(encoding="utf-8")) == starter_compact_payload(
            "detail"
        )
        assert tools.check("viewspec.compact.json")["ok"] is True
        compiled = tools.compile("viewspec.compact.json", "dist")
        assert compiled["ok"] is True
        assert compiled["metadata"]["manifest_summary"]["source_format"] == "compact"
        assert tools.check("dist")["ok"] is True
    finally:
        tools.close()


def test_starter_compact_payload_rejects_unknown_kinds():
    with pytest.raises(ValueError, match="Unknown starter intent kind"):
        starter_compact_payload("chat")
    assert copy.deepcopy(starter_compact_payload("hero")) == starter_compact_payload("hero")


# --- composition with the repair loop: patch, then normalize, then fix, then validate -----------

# The starter dashboard's last card, priority, binds its value as bindings[7] of the canonical bundle.
PRIORITY_VALUE_PRESENT_AS = "$.view_spec.bindings[7].present_as"


def _write_compact(tmp_path: Path, document: dict | None = None, name: str = "viewspec.compact.json") -> Path:
    path = tmp_path / name
    path.write_text(canonical_json_text(document or starter_compact_payload("dashboard")), encoding="utf-8")
    return path


def _write_ops(tmp_path: Path, operations: list) -> Path:
    path = tmp_path / "ops.json"
    path.write_text(json.dumps(operations), encoding="utf-8")
    return path


def _at(document: object, path: str) -> object:
    """The value a ``$.``-rooted issue or fix path names, for paths of plain keys and ``[index]`` steps."""
    current = document
    for key, index in re.findall(r"\.([^.\[\]]+)|\[(\d+)\]", path):
        current = current[int(index)] if index else current[key]  # type: ignore[index]
    return current


def test_the_compact_starter_check_reports_no_fixes_and_its_source_format(tmp_path, capsys):
    compact_path = tmp_path / "viewspec.compact.json"
    assert cli_main(["init", "compact", "--out", str(compact_path)]) == 0
    capsys.readouterr()

    assert cli_main(["check", str(compact_path), "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1,
        "ok": True,
        "compile_check": "passed",
        "issues": [],
        "fixes": [],
        "repair_checklist": [],
        "correction_prompt": None,
        "source_format": "compact",
    }


def test_a_patch_names_the_compact_document_and_the_patched_source_normalizes_and_passes(tmp_path, capsys):
    compact_path = _write_compact(tmp_path)
    before = compact_path.read_bytes()
    ops = _write_ops(
        tmp_path,
        [
            {"op": "test", "path": "/format", "value": "compact"},
            {"op": "replace", "path": "/motifs/0/cards/0/value", "value": "$13.1K"},
            {"op": "add", "path": "/motifs/0/cards/-", "value": {"id": "churn", "label": "Churn", "value": "2.1%"}},
        ],
    )
    patched_path = tmp_path / "patched.compact.json"
    canonical_path = tmp_path / "viewspec.intent.json"
    assert patch_target_problem(compact_path) is None

    exit_code = cli_main(
        [
            "check",
            str(compact_path),
            "--patch",
            str(ops),
            "--patched-out",
            str(patched_path),
            "--canonical-out",
            str(canonical_path),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0, payload
    assert payload["ok"] is True
    assert payload["source_format"] == "compact"
    assert payload["fixes"] == []
    assert payload["patched_out"] == str(patched_path)
    assert payload["canonical_out"] == str(canonical_path)
    assert compact_path.read_bytes() == before
    patched = json.loads(patched_path.read_text(encoding="utf-8"))
    assert detect_compact(patched)
    assert patched["motifs"][0]["cards"][-1] == {"id": "churn", "label": "Churn", "value": "2.1%"}
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    assert canonical == normalize(patched)
    assert canonical["substrate"]["nodes"]["revenue"]["attrs"]["value"] == "$13.1K"
    assert canonical["substrate"]["nodes"]["churn"]["attrs"] == {"label": "Churn", "value": "2.1%"}


def test_a_patch_pointer_into_the_canonical_document_does_not_apply_to_a_compact_source(tmp_path, capsys):
    compact_path = _write_compact(tmp_path)
    ops = _write_ops(tmp_path, [{"op": "replace", "path": "/view_spec/bindings/7/present_as", "value": "value"}])

    assert cli_main(["check", str(compact_path), "--patch", str(ops), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)

    assert [(issue["code"], issue["path"]) for issue in payload["issues"]] == [("PATCH_APPLY_FAILED", "patch[0]")]


def test_the_patched_size_bound_applies_to_the_patched_compact_text(tmp_path, capsys):
    # The patch itself stays small; the copy doubles a long title into a card label.
    compact_path = _write_compact(tmp_path)
    ops = _write_ops(
        tmp_path,
        [
            {"op": "replace", "path": "/title", "value": "x" * (MAX_COMPACT_BYTES // 2 + 1000)},
            {"op": "copy", "from": "/title", "path": "/motifs/0/cards/0/label"},
        ],
    )

    assert cli_main(["check", str(compact_path), "--patch", str(ops), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)

    assert [issue["code"] for issue in payload["issues"]] == ["PATCH_RESULT_TOO_LARGE"]


def test_a_compact_compile_with_a_patch_hashes_the_patched_compact_text_and_the_canonical_out_bytes(tmp_path, capsys):
    compact_path = _write_compact(tmp_path)
    ops = _write_ops(tmp_path, [{"op": "replace", "path": "/motifs/0/cards/1/label", "value": "Active users"}])
    patched_path = tmp_path / "patched.compact.json"
    canonical_path = tmp_path / "viewspec.intent.json"
    out_dir = tmp_path / "dist"

    exit_code = cli_main(
        [
            "compile",
            str(compact_path),
            "--patch",
            str(ops),
            "--patched-out",
            str(patched_path),
            "--canonical-out",
            str(canonical_path),
            "--out",
            str(out_dir),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    manifest = json.loads((out_dir / "provenance_manifest.json").read_text(encoding="utf-8"))

    assert exit_code == 0, payload
    assert payload["paths"]["patched_out"] == str(patched_path)
    assert payload["paths"]["canonical_out"] == str(canonical_path)
    assert payload["metadata"]["source_format"] == "compact"
    patched_text = patched_path.read_text(encoding="utf-8")
    assert manifest["raw_source_hash"] == manifest["compact_source_sha256"] == source_hash(patched_text)
    assert manifest["source_hash"] == source_hash(canonical_path.read_text(encoding="utf-8"))
    assert manifest["source_format"] == "compact"
    assert manifest["command_args"][-4:-2] == ["--patch", "ops.json"]
    assert json.loads(canonical_path.read_text(encoding="utf-8"))["substrate"]["nodes"]["users"]["attrs"] == {
        "label": "Active users",
        "value": "1,284",
    }
    assert check_artifact_dir(out_dir)["ok"] is True


def test_an_aliased_present_as_on_a_compact_card_is_fixed_at_its_canonical_path(tmp_path, capsys):
    aliased = starter_compact_payload("dashboard")
    aliased["motifs"][0]["cards"][3]["present_as"] = "metric"
    compact_path = _write_compact(tmp_path, aliased)
    canonical_path = tmp_path / "viewspec.intent.json"

    assert cli_main(["check", str(compact_path), "--json", "--canonical-out", str(canonical_path)]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["source_format"] == "compact"
    (fix,) = payload["fixes"]
    assert (fix["code"], fix["path"], fix["lossy"]) == ("FIX_PRESENT_AS_ALIAS", PRIORITY_VALUE_PRESENT_AS, False)
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    assert _at(canonical, fix["path"]) == "value"
    assert _at(canonical, PRIORITY_VALUE_PRESENT_AS.rsplit(".", 1)[0])["id"] == "priority_value"  # type: ignore[index]
    twin = copy.deepcopy(aliased)
    twin["motifs"][0]["cards"][3]["present_as"] = "value"
    assert canonical == normalize(twin)

    # --no-fix reports the same problem as an issue at the same canonical path.
    assert cli_main(["check", str(compact_path), "--json", "--no-fix"]) == 2
    unfixed = json.loads(capsys.readouterr().out)
    assert unfixed["fixes"] == []
    assert ("UNKNOWN_PRESENT_AS", PRIORITY_VALUE_PRESENT_AS) in {
        (issue["code"], issue["path"]) for issue in unfixed["issues"]
    }

    # compile compiles the fixed document and hashes exactly the bytes --canonical-out writes.
    compiled_canonical = tmp_path / "compiled.intent.json"
    out_dir = tmp_path / "dist"
    assert (
        cli_main(
            ["compile", str(compact_path), "--out", str(out_dir), "--canonical-out", str(compiled_canonical), "--json"]
        )
        == 0
    )
    compiled = json.loads(capsys.readouterr().out)
    assert [item["code"] for item in compiled["fixes"]] == ["FIX_PRESENT_AS_ALIAS"]
    manifest = json.loads((out_dir / "provenance_manifest.json").read_text(encoding="utf-8"))
    assert compiled_canonical.read_bytes() == canonical_path.read_bytes()
    assert manifest["source_hash"] == source_hash(compiled_canonical.read_text(encoding="utf-8"))
    assert manifest["compact_source_sha256"] == source_hash(compact_path.read_text(encoding="utf-8"))
    twin_path = _write_compact(tmp_path, twin, name="twin.compact.json")
    twin_out = tmp_path / "twin-dist"
    assert compile_intent_bundle_file_tool(twin_path, twin_out, cwd=tmp_path)["ok"] is True
    twin_manifest = json.loads((twin_out / "provenance_manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifact_hash"] == twin_manifest["artifact_hash"]
    assert manifest["source_hash"] == twin_manifest["source_hash"]


def test_canonical_out_holds_the_document_that_canonical_issue_paths_name(tmp_path, capsys):
    document = _with(lambda d: d["styles"].append({"target": "binding:missing_value", "token": "tone.accent"}))
    compact_path = _write_compact(tmp_path, document)
    canonical_path = tmp_path / "canonical.json"

    assert cli_main(["check", str(compact_path), "--json", "--canonical-out", str(canonical_path)]) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["source_format"] == "compact"
    assert payload["canonical_out"] == str(canonical_path)
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    issue = next(issue for issue in payload["issues"] if issue["code"] == "UNKNOWN_STYLE_TARGET")
    named = _at(canonical, issue["path"])
    assert named == "binding:missing_value" or named["target"] == "binding:missing_value"  # type: ignore[index]


def test_fixed_out_on_a_compact_source_is_a_usage_error_that_names_canonical_out(tmp_path, capsys):
    compact_path = _write_compact(tmp_path)
    fixed_path = tmp_path / "fixed.json"
    ops = _write_ops(tmp_path, [{"op": "replace", "path": "/title", "value": "Patched"}])

    assert cli_main(["check", str(compact_path), "--fixed-out", str(fixed_path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--canonical-out" in captured.err
    assert cli_main(["check", str(compact_path), "--fixed-out", str(fixed_path), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "--canonical-out" in payload["errors"][0]["message"]
    assert cli_main(["check", str(compact_path), "--patch", str(ops), "--fixed-out", str(fixed_path)]) == 2
    assert "--canonical-out" in capsys.readouterr().err
    assert (
        cli_main(["compile", str(compact_path), "--out", str(tmp_path / "dist"), "--fixed-out", str(fixed_path)]) == 2
    )
    assert "--canonical-out" in capsys.readouterr().err
    report = compile_intent_bundle_file_tool(compact_path, tmp_path / "tool-dist", cwd=tmp_path, fixed_out=fixed_path)
    assert report["ok"] is False
    assert "--canonical-out" in report["errors"][0]["message"]
    assert not fixed_path.exists()
    assert not (tmp_path / "dist").exists()
    assert not (tmp_path / "tool-dist").exists()


def test_the_mcp_patch_tool_patches_a_compact_source(tmp_path):
    tools = ViewSpecTools(tmp_path)
    try:
        assert tools.init(what="compact")["ok"] is True
        source = tmp_path / "viewspec.compact.json"
        before = source.read_bytes()

        report = tools.patch(
            "viewspec.compact.json",
            [{"op": "replace", "path": "/motifs/0/cards/0/label", "value": "Net revenue"}],
            out="patched.compact.json",
        )

        assert report["ok"] is True, report
        assert report["source_format"] == "compact"
        assert report["fixes"] == []
        assert Path(report["patched_out"]).resolve() == (tmp_path / "patched.compact.json").resolve()
        assert source.read_bytes() == before
        patched = json.loads((tmp_path / "patched.compact.json").read_text(encoding="utf-8"))
        assert patched["motifs"][0]["cards"][0]["label"] == "Net revenue"
        failed = tools.patch("viewspec.compact.json", [{"op": "remove", "path": "/motifs/0/cards"}])
        assert failed["ok"] is False
        assert [(issue["code"], issue["path"]) for issue in failed["issues"]] == [
            ("COMPACT_MOTIF_ITEMS_MISSING", "$.motifs[0].cards")
        ]
    finally:
        tools.close()


def test_an_appbundle_screen_stays_a_canonical_bundle(tmp_path):
    app = starter_app_bundle("internal_tool")
    app["screens"][0]["intent_bundle"] = starter_compact_payload("table")

    report = validate_app_text(json.dumps(app))

    assert report["ok"] is False
    assert any(issue["path"].startswith("$.screens[0].intent_bundle") for issue in report["issues"])


def test_studio_previews_a_compact_source(tmp_path):
    compact_path = _write_compact(tmp_path)
    out_dir = tmp_path / "build"

    assert _compile_preview(compact_path, out_dir, None) == []
    assert (out_dir / "index.html").is_file()
    inspector = build_inspector_map(compact_path, out_dir, generation=1)
    assert inspector["errors"] == []
    assert inspector["source_format"] == "compact"
    bindings = inspector["screens"][0]["bindings"]
    assert bindings
    # Every path names the canonical bundle that --canonical-out writes, the document the compiler built.
    canonical = json.loads(prepare_intent_source(compact_path.read_text(encoding="utf-8")).canonical_text)
    for binding_id, entry in bindings.items():
        index = int(re.fullmatch(r"\$\.view_spec\.bindings\[(\d+)\]", entry["source_path"]).group(1))
        assert canonical["view_spec"]["bindings"][index]["id"] == binding_id
        assert entry["content_paths"] and all(entry["content_paths"])
