"""The closed AppBundle V5 state-text formatter set: one vector file, two implementations.

conformance/formatters/vectors.json is the specification. The Python reference in
viewspec.app_format and the generated JavaScript kernel must render every vector exactly. The
random parity test and the Decimal oracle cover values the hand-written vectors do not name.
"""

from __future__ import annotations

import json
import math
import random
import shutil
import struct
import subprocess
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from viewspec.app_format import (
    STATE_TEXT_CURRENCIES,
    STATE_TEXT_FORMAT_NAMES,
    format_state_value,
    generate_state_text_formatter_module,
    js_number_string,
    render_state_scalar,
    state_text_format_problem,
    state_text_formatter_source,
)

ROOT = Path(__file__).resolve().parents[1]
VECTORS_PATH = ROOT / "conformance" / "formatters" / "vectors.json"
VECTORS = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
CASES = VECTORS["cases"]
NODE = shutil.which("node")
NODE_REASON = "Node.js is not on PATH, so the generated JavaScript formatter cannot run against the vectors."

_NODE_RUNNER = """
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const mod = await import(pathToFileURL(process.argv[2]).href);
const input = JSON.parse(readFileSync(process.argv[3], "utf8"));
const rows = input.cases.map((item) => mod.formatStateValue(item.value, item.format ?? undefined));
console.log(JSON.stringify(rows));
""".lstrip()


def _run_node(tmp_path: Path, input_path: Path) -> list[str]:
    module = tmp_path / "formatter.mjs"
    module.write_text(generate_state_text_formatter_module(), encoding="utf-8")
    runner = tmp_path / "runner.mjs"
    runner.write_text(_NODE_RUNNER, encoding="utf-8")
    completed = subprocess.run(
        [str(NODE), str(runner), str(module), str(input_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_vector_file_covers_the_required_edges():
    assert VECTORS["schema_version"] == 1
    assert len(CASES) >= 120
    ids = [case["id"] for case in CASES]
    assert len(ids) == len(set(ids))
    formats = [case["format"] for case in CASES]
    assert {fmt["format"] for fmt in formats if fmt} == set(STATE_TEXT_FORMAT_NAMES)
    assert {fmt["currency"] for fmt in formats if fmt and fmt["format"] == "currency"} == set(STATE_TEXT_CURRENCIES)
    numbers = [case["value"] for case in CASES if type(case["value"]) in {int, float}]
    for tie in (0.5, 1.5, 2.5, -2.5, 1.005, 2.675):
        assert tie in numbers, tie
    for exponent_form in (1e16, 1e21, 1e-7, 123456789012345680000):
        assert exponent_form in numbers, exponent_form
    assert any(value == 0 and math.copysign(1.0, value) < 0 for value in numbers), "negative zero"
    values = [case["value"] for case in CASES]
    assert True in values and False in values and None in values
    assert any(isinstance(value, str) for value in values)
    assert "2024-02-29" in values and "2023-02-29T00:00:00Z" in values
    rounds_to_zero = [case for case in CASES if case["id"].endswith("rounds_to_zero")]
    assert rounds_to_zero and all("-" not in case["expected"] for case in rounds_to_zero)


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_python_reference_renders_every_vector(case):
    assert format_state_value(case["value"], case["format"]) == case["expected"]


@pytest.mark.skipif(NODE is None, reason=NODE_REASON)
def test_generated_javascript_renders_every_vector(tmp_path):
    # Node parses the vector file itself, so both sides read the same JSON number text.
    rendered = _run_node(tmp_path, VECTORS_PATH)
    mismatches = [
        (case["id"], case["expected"], actual)
        for case, actual in zip(CASES, rendered, strict=True)
        if actual != case["expected"]
    ]
    assert not mismatches, mismatches


def _random_values(seed: int) -> list[object]:
    rng = random.Random(seed)
    values: list[object] = []
    while len(values) < 1500:
        (number,) = struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))
        if math.isfinite(number):
            values.append(number)
    values += [rng.randint(-(10**9), 10**9) / 10 ** rng.randint(0, 9) for _ in range(1500)]
    values += [rng.randint(-(2**53), 2**53) for _ in range(500)]
    values += [rng.randint(-62_167_219_200_000, 253_402_300_799_999) for _ in range(500)]
    values += [rng.uniform(-1e6, 1e6) for _ in range(500)]
    return values


@pytest.mark.skipif(NODE is None, reason=NODE_REASON)
def test_python_and_javascript_agree_on_random_numbers(tmp_path):
    formats: list[dict[str, object] | None] = [
        None,
        {"format": "integer"},
        {"format": "decimal", "digits": 3},
        {"format": "percent", "digits": 1},
        {"format": "currency", "currency": "JPY"},
        {"format": "currency", "currency": "USD"},
        {"format": "date_iso"},
    ]
    cases = [{"value": value, "format": fmt} for value in _random_values(20261108) for fmt in formats]
    input_path = tmp_path / "random.json"
    input_path.write_text(json.dumps({"cases": cases}), encoding="utf-8")
    rendered = _run_node(tmp_path, input_path)
    mismatches = [
        (case["value"], case["format"], format_state_value(case["value"], case["format"]), actual)
        for case, actual in zip(cases, rendered, strict=True)
        if format_state_value(case["value"], case["format"]) != actual
    ]
    assert not mismatches, mismatches[:10]


def _decimal_oracle(value: float, digits: int, shift: int, prefix: str, suffix: str) -> str:
    """Round half up on the shortest repr with Decimal, an implementation independent of the kernel."""
    amount = Decimal(repr(float(value))).scaleb(shift)
    quantum = Decimal(1).scaleb(-digits)
    rounded = amount.copy_abs().quantize(quantum, rounding=ROUND_HALF_UP)
    sign = "-" if amount < 0 and rounded != 0 else ""
    return f"{sign}{prefix}{rounded:,.{digits}f}{suffix}"


@settings(max_examples=400, deadline=None)
@given(
    st.floats(allow_nan=False, allow_infinity=False, min_value=-1e18, max_value=1e18)
    | st.integers(min_value=-(2**53), max_value=2**53),
    st.integers(min_value=0, max_value=6),
)
def test_numeric_formats_match_a_decimal_oracle(value, digits):
    assert format_state_value(value, {"format": "decimal", "digits": digits}) == _decimal_oracle(
        value, digits, 0, "", ""
    )
    assert format_state_value(value, {"format": "percent", "digits": digits}) == _decimal_oracle(
        value, digits, 2, "", "%"
    )
    assert format_state_value(value, {"format": "integer"}) == _decimal_oracle(value, 0, 0, "", "")
    symbol, minor = STATE_TEXT_CURRENCIES["CHF"]
    assert format_state_value(value, {"format": "currency", "currency": "CHF"}) == _decimal_oracle(
        value, minor, 0, symbol, ""
    )


_JSON_VALUES = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=12),
    lambda children: st.lists(children, max_size=3) | st.dictionaries(st.text(max_size=4), children, max_size=3),
    max_leaves=6,
)
_FORMATS = st.sampled_from(
    [
        None,
        {"format": "integer"},
        {"format": "decimal", "digits": 2},
        {"format": "percent", "digits": 0},
        {"format": "currency", "currency": "EUR"},
        {"format": "date_iso"},
    ]
)


@settings(max_examples=300, deadline=None)
@given(_JSON_VALUES, _FORMATS)
def test_formatting_is_total(value, fmt):
    # NaN, infinity, and integers beyond the double range included: text never throws.
    assert isinstance(format_state_value(value, fmt), str)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, "0"),
        (-0.0, "0"),
        (1e21, "1e+21"),
        (1e20, "100000000000000000000"),
        (1e-6, "0.000001"),
        (1e-7, "1e-7"),
        (-1.5e-7, "-1.5e-7"),
        (123.456, "123.456"),
        (5e-324, "5e-324"),
        (1.7976931348623157e308, "1.7976931348623157e+308"),
    ],
)
def test_js_number_string_follows_ecmascript(value, expected):
    assert js_number_string(value) == expected


def test_scalar_rendering_mirrors_v4_for_non_numbers():
    assert render_state_scalar(None) == ""
    assert render_state_scalar(True) == "true"
    assert render_state_scalar("$&") == "$&"
    assert render_state_scalar([1]) == ""
    assert render_state_scalar(float("nan")) == ""
    assert render_state_scalar(10**400) == ""  # beyond the double range, like a non-finite number


@pytest.mark.parametrize(
    "fmt",
    [
        None,
        "integer",
        {},
        {"format": "money"},
        {"format": "integer", "digits": 0},
        {"format": "decimal"},
        {"format": "decimal", "digits": 7},
        {"format": "decimal", "digits": -1},
        {"format": "decimal", "digits": True},
        {"format": "decimal", "digits": 2.0},
        {"format": "percent", "digits": "2"},
        {"format": "currency"},
        {"format": "currency", "currency": "usd"},
        {"format": "currency", "currency": "BTC"},
        {"format": "date_iso", "zone": "UTC"},
    ],
)
def test_format_validation_rejects_everything_outside_the_closed_set(fmt):
    assert state_text_format_problem(fmt) is not None


@pytest.mark.parametrize(
    "fmt",
    [
        {"format": "integer"},
        {"format": "decimal", "digits": 0},
        {"format": "decimal", "digits": 6},
        {"format": "percent", "digits": 3},
        *({"format": "currency", "currency": code} for code in sorted(STATE_TEXT_CURRENCIES)),
        {"format": "date_iso"},
    ],
)
def test_format_validation_accepts_the_closed_set(fmt):
    assert state_text_format_problem(fmt) is None


def test_generated_kernel_never_touches_locale_time_or_binary_rounding():
    for typescript in (True, False):
        source = "\n".join(state_text_formatter_source(typescript=typescript))
        for token in ("Intl", "toLocale", "new Date", "Date.", "getTimezone", "toFixed", "toPrecision", "Math.round"):
            assert token not in source, (typescript, token)
        # The static shell inlines this source, so its safety scan must stay quiet.
        for token in ("http:", "https:", "url(", "Worker(", "importScripts", "</script"):
            assert token not in source
