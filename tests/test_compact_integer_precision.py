"""C-15: the compact form rejects an integer that a double cannot hold, before it normalizes.

Normalization drives ViewSpecBuilder, which stores every attribute value in a protobuf Struct, where
a number is an IEEE-754 double. A compact table row whose value was 9007199254740993 passed
``viewspec check``, compiled, and showed 9007199254740992 on the page, while the canonical form
rejects the same value with INTEGER_OUT_OF_SAFE_RANGE. The compact checks now apply the canonical
rule to every value-bearing field (the root title; item labels, values, descriptions and ``values``
entries; the title, eyebrow and description of hero and state motifs; input labels and values) at
any nesting depth. The issue path names the compact document, and the builder is never called, so no
rounded value is produced.
"""

from __future__ import annotations

import json
import re

import pytest

from viewspec import compact_form
from viewspec.agent import validate_agent_intent_bundle
from viewspec.api import prove
from viewspec.cli import main as cli_main
from viewspec.compact_form import normalize, normalize_with_issues
from viewspec.intent_contract import MAX_SAFE_INTEGER
from viewspec.intent_tools import validate_intent_text

CODE = "INTEGER_OUT_OF_SAFE_RANGE"
UNSAFE = MAX_SAFE_INTEGER + 1  # 9007199254740993; the nearest double is 9007199254740992

# The audit's reproduction, byte for byte, and the compact path of its one value.
ORDERS_TEXT = (
    '{"format":"compact","id":"orders","motifs":[{"id":"t","kind":"table","rows":'
    '[{"id":"o1","label":"Order id","value":9007199254740993}]}]}'
)
ORDERS_VALUE = "$.motifs[0].rows[0].value"
NEAR_2_POW_53 = re.compile(r"900719925474099\d")


def _orders(value: object) -> dict:
    document = json.loads(ORDERS_TEXT)
    document["motifs"][0]["rows"][0]["value"] = value
    return document


def _found(issues) -> list[tuple[str, str]]:
    return [(issue.code, issue.path) for issue in issues]


def _every_value_field() -> dict:
    """A valid compact document with every value-bearing field of the compact form, all safe."""
    return {
        "format": "compact",
        "id": "ledger",
        "title": "Ledger",
        "regions": [{"id": "main"}, {"id": "notices"}],
        "motifs": [
            {"id": "intro", "kind": "hero", "eyebrow": "Finance", "title": "Ledger", "description": "Daily close."},
            {
                "id": "orders",
                "kind": "table",
                "rows": [{"id": "o1", "label": "Order", "value": 1, "values": {"ids": [2, {"n": 3}]}}],
            },
            {"id": "kpis", "kind": "dashboard", "cards": [{"id": "open", "label": "Open", "value": 4}]},
            {"id": "plan", "kind": "outline", "items": [{"id": "scope", "label": "Scope"}]},
            {
                "id": "plans",
                "kind": "comparison",
                "items": [{"id": "free", "label": "Free", "value": 0}, {"id": "pro", "label": "Pro", "value": 9}],
            },
            {
                "id": "tasks",
                "kind": "list",
                "items": [{"id": "t1", "label": "Review", "value": 5, "description": "Soon", "values": {"due": 6}}],
            },
            {"id": "contact", "kind": "form", "fields": [{"id": "qty", "label": "Quantity", "value": 7}]},
            {
                "id": "owner",
                "kind": "detail",
                "fields": [{"id": "who", "label": "Owner", "value": "Ada", "values": {"desk": 8}}],
            },
            {"id": "empty", "kind": "empty_state", "region": "notices", "title": "No orders", "description": "Add."},
            {"id": "loading", "kind": "loading_state", "region": "notices", "title": "Loading", "description": "Wait."},
            {"id": "failed", "kind": "error_state", "region": "notices", "title": "Failed", "description": "Retry."},
        ],
        "inputs": [{"id": "query", "label": "Search", "value": 10}],
    }


_STEP = re.compile(r"\.([A-Za-z0-9_-]+)|\[(\d+)\]")


def _set(document: dict, path: str, value: object) -> None:
    """Put ``value`` at a ``$.``-rooted path of plain keys and ``[index]`` steps."""
    steps: list[str | int] = [key or int(index) for key, index in _STEP.findall(path)]
    target = document
    for step in steps[:-1]:
        target = target[step]  # type: ignore[index]
    target[steps[-1]] = value  # type: ignore[index]


# --- the reproduction, through check, compile and prove ------------------------------------------


def test_check_rejects_the_reproduction_at_its_compact_path(tmp_path, capsys):
    source = tmp_path / "orders.compact.json"
    source.write_text(ORDERS_TEXT + "\n", encoding="utf-8")

    assert cli_main(["check", str(source), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["source_format"] == "compact"
    assert [(issue["code"], issue["path"]) for issue in payload["issues"]] == [(CODE, ORDERS_VALUE)]
    assert str(UNSAFE) in payload["issues"][0]["message"]
    assert "JSON strings" in payload["issues"][0]["suggestion"]
    assert CODE in payload["correction_prompt"]
    assert any("2^53" in line for line in payload["repair_checklist"])

    assert cli_main(["check", str(source)]) == 2
    captured = capsys.readouterr()
    assert f"{CODE} at {ORDERS_VALUE}" in captured.out + captured.err


def test_compile_and_prove_refuse_the_reproduction_and_write_no_page(tmp_path, capsys):
    source = tmp_path / "orders.compact.json"
    source.write_text(ORDERS_TEXT + "\n", encoding="utf-8")
    out_dir = tmp_path / "dist"

    assert cli_main(["compile", str(source), "--out", str(out_dir)]) == 2
    assert CODE in capsys.readouterr().err
    assert not out_dir.exists()

    proof_dir = tmp_path / ".viewspec-proof"
    proof = prove(source, out_dir=proof_dir)
    assert proof["ok"] is False
    assert CODE in [error["code"] for error in proof["errors"]]
    assert not (proof_dir / "artifact" / "index.html").exists()


# --- the bound ------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [MAX_SAFE_INTEGER, -MAX_SAFE_INTEGER])
def test_2_pow_53_passes_for_both_signs_and_normalizes_exactly(value):
    canonical, issues = normalize_with_issues(_orders(value))

    assert issues == []
    assert canonical is not None
    assert canonical["substrate"]["nodes"]["o1"]["attrs"]["value"] == value
    assert validate_intent_text(json.dumps(_orders(value)))["ok"] is True


@pytest.mark.parametrize("value", [MAX_SAFE_INTEGER + 1, -MAX_SAFE_INTEGER - 1, 2**64, -(2**80)])
def test_integers_past_2_pow_53_fail_for_both_signs(value):
    canonical, issues = normalize_with_issues(_orders(value))

    assert canonical is None
    assert _found(issues) == [(CODE, ORDERS_VALUE)]
    payload = validate_intent_text(json.dumps(_orders(value)))
    assert payload["ok"] is False
    assert [(issue["code"], issue["path"]) for issue in payload["issues"]] == [(CODE, ORDERS_VALUE)]


@pytest.mark.parametrize("value", [0, True, 2.5, 1e20, float(MAX_SAFE_INTEGER) * 2, str(UNSAFE), [MAX_SAFE_INTEGER]])
def test_values_the_canonical_form_accepts_still_pass(value):
    # Bools are ints in Python but never numbers here, and floats are already doubles, as in the
    # canonical rule. A string is how the issue says to write a large identifier.
    assert normalize_with_issues(_orders(value))[1] == []


@pytest.mark.parametrize("value", [MAX_SAFE_INTEGER, -MAX_SAFE_INTEGER, str(UNSAFE)])
def test_accepted_values_compile_to_the_number_the_author_wrote(tmp_path, value):
    source = tmp_path / "orders.compact.json"
    source.write_text(json.dumps(_orders(value)), encoding="utf-8")
    out_dir = tmp_path / "dist"

    assert cli_main(["compile", str(source), "--out", str(out_dir), "--json"]) == 0
    html = (out_dir / "index.html").read_text(encoding="utf-8")
    assert set(NEAR_2_POW_53.findall(html)) == {str(abs(int(value)))}


# --- every value-bearing field, at any depth, at its compact path --------------------------------

VALUE_FIELDS = [
    "$.title",
    "$.motifs[0].eyebrow",
    "$.motifs[0].title",
    "$.motifs[0].description",
    "$.motifs[1].rows[0].label",
    "$.motifs[1].rows[0].value",
    "$.motifs[1].rows[0].values.ids[0]",
    "$.motifs[1].rows[0].values.ids[1].n",
    "$.motifs[2].cards[0].label",
    "$.motifs[2].cards[0].value",
    "$.motifs[3].items[0].label",
    "$.motifs[4].items[1].value",
    "$.motifs[5].items[0].value",
    "$.motifs[5].items[0].description",
    "$.motifs[5].items[0].values.due",
    "$.motifs[6].fields[0].label",
    "$.motifs[6].fields[0].value",
    "$.motifs[7].fields[0].value",
    "$.motifs[7].fields[0].values.desk",
    "$.motifs[8].title",
    "$.motifs[8].description",
    "$.motifs[9].title",
    "$.motifs[10].description",
    "$.inputs[0].label",
    "$.inputs[0].value",
]


def test_the_document_with_every_value_field_is_valid_while_its_integers_are_safe():
    payload = validate_intent_text(json.dumps(_every_value_field()))

    assert payload["ok"] is True, payload["issues"]
    assert payload["fixes"] == []


@pytest.mark.parametrize("path", VALUE_FIELDS)
@pytest.mark.parametrize("sign", [1, -1], ids=["positive", "negative"])
def test_an_unsafe_integer_in_any_value_field_is_reported_at_that_compact_path(path, sign):
    document = _every_value_field()
    _set(document, path, sign * UNSAFE)

    canonical, issues = normalize_with_issues(document)

    assert canonical is None
    assert _found(issues) == [(CODE, path)]


@pytest.mark.parametrize(
    ("path", "value", "reported"),
    [
        ("$.motifs[2].cards[0].value", {"amount": [1, UNSAFE]}, "$.motifs[2].cards[0].value.amount[1]"),
        ("$.motifs[3].items[0].label", [[UNSAFE]], "$.motifs[3].items[0].label[0][0]"),
        ("$.motifs[0].title", {"text": "Ledger", "id": -UNSAFE}, "$.motifs[0].title.id"),
        ("$.inputs[0].value", [{"n": [2**70]}], "$.inputs[0].value[0].n[0]"),
        (
            "$.motifs[1].rows[0].values",
            {"deep": {"er": [0, {"est": UNSAFE}]}},
            "$.motifs[1].rows[0].values.deep.er[1].est",
        ),
    ],
)
def test_unsafe_integers_are_found_at_any_nesting_depth(path, value, reported):
    document = _every_value_field()
    _set(document, path, value)

    canonical, issues = normalize_with_issues(document)

    assert canonical is None
    assert _found(issues) == [(CODE, reported)]


def test_every_unsafe_integer_is_reported_in_one_pass_beside_the_other_compact_issues():
    document = _every_value_field()
    for path in ("$.title", "$.motifs[1].rows[0].value", "$.motifs[1].rows[0].values.ids[0]", "$.inputs[0].label"):
        _set(document, path, UNSAFE)
    document["motifs"][2]["cards"][0]["extra"] = 1

    canonical, issues = normalize_with_issues(document)

    assert canonical is None
    assert _found(issues) == [
        (CODE, "$.title"),
        (CODE, "$.motifs[1].rows[0].value"),
        (CODE, "$.motifs[1].rows[0].values.ids[0]"),
        ("COMPACT_UNKNOWN_FIELD", "$.motifs[2].cards[0].extra"),
        (CODE, "$.inputs[0].label"),
    ]


def test_a_key_the_compact_form_rejects_is_not_also_reported_as_a_value():
    # An outline item has no value, so its unknown value key is one problem, not two.
    document = _every_value_field()
    document["motifs"][3]["items"][0]["value"] = UNSAFE

    assert _found(normalize_with_issues(document)[1]) == [("COMPACT_UNKNOWN_FIELD", "$.motifs[3].items[0].value")]


# --- before normalization, and the same rule as the canonical form -------------------------------


def test_the_check_runs_before_any_builder_call(monkeypatch):
    def no_builder(*_args, **_kwargs):
        raise AssertionError("normalization started")

    monkeypatch.setattr(compact_form, "ViewSpecBuilder", no_builder)
    with pytest.raises(AssertionError, match="normalization started"):
        normalize_with_issues(_orders(1))

    canonical, issues = normalize_with_issues(json.loads(ORDERS_TEXT))

    assert canonical is None
    assert _found(issues) == [(CODE, ORDERS_VALUE)]


def test_the_compact_issue_is_the_canonical_issue_at_the_compact_path():
    twin = normalize(_orders(MAX_SAFE_INTEGER))
    twin["substrate"]["nodes"]["o1"]["attrs"]["value"] = UNSAFE
    canonical_issues = validate_agent_intent_bundle(twin).issues
    compact_issues = normalize_with_issues(json.loads(ORDERS_TEXT))[1]

    assert _found(canonical_issues) == [(CODE, "$.substrate.nodes.o1.attrs.value")]
    assert _found(compact_issues) == [(CODE, ORDERS_VALUE)]
    canonical_issue, compact_issue = canonical_issues[0].to_json(), compact_issues[0].to_json()
    assert {**canonical_issue, "path": ORDERS_VALUE} == compact_issue


def test_a_patch_that_writes_an_unsafe_integer_is_reported_at_the_compact_path(tmp_path, capsys):
    source = tmp_path / "orders.compact.json"
    source.write_text(json.dumps(_orders("#1042")), encoding="utf-8")
    ops = tmp_path / "ops.json"
    ops.write_text(json.dumps([{"op": "replace", "path": "/motifs/0/rows/0/value", "value": UNSAFE}]), encoding="utf-8")
    before = source.read_bytes()

    assert cli_main(["check", str(source), "--patch", str(ops), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)

    assert payload["source_format"] == "compact"
    assert [(issue["code"], issue["path"]) for issue in payload["issues"]] == [(CODE, ORDERS_VALUE)]
    assert source.read_bytes() == before
