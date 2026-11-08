"""Closed state-text formatter set for AppBundle schema_version 5.

This module is the single source for the five formatters, the currency table, and the scalar
fallback. It holds the Python reference used by replay and compile-time text baking, and it
emits the JavaScript and TypeScript kernel that the generated reducer carries. Both sides follow
one rule set, and conformance/formatters/vectors.json pins it:

* Numeric formats accept only finite JSON numbers. Booleans, numeric strings, and every other
  value fall back to the scalar rendering, so text never throws.
* A number is read from its shortest round-trip representation (Python ``repr(float(x))``,
  JavaScript ``String(x)``), normalized into digits and a decimal point position, and rounded
  half away from zero in that digit space. No binary floating point rounding takes place.
* Nothing calls Intl, a locale, a time zone, or a local-time Date API.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from typing import Any

STATE_TEXT_FORMAT_FIELDS: dict[str, frozenset[str]] = {
    "integer": frozenset({"format"}),
    "decimal": frozenset({"format", "digits"}),
    "percent": frozenset({"format", "digits"}),
    "currency": frozenset({"format", "currency"}),
    "date_iso": frozenset({"format"}),
}
STATE_TEXT_FORMAT_NAMES = tuple(STATE_TEXT_FORMAT_FIELDS)
STATE_TEXT_FORMAT_MAX_DIGITS = 6
# The closed currency table: code -> (symbol printed before the amount, minor digits).
STATE_TEXT_CURRENCIES: dict[str, tuple[str, int]] = {
    "CHF": ("CHF ", 2),
    "EUR": ("€", 2),
    "GBP": ("£", 2),
    "INR": ("₹", 2),
    "JPY": ("¥", 0),
    "USD": ("$", 2),
}
# Epoch milliseconds of 0000-01-01T00:00:00.000Z and 9999-12-31T23:59:59.999Z.
STATE_TEXT_DATE_MIN_EPOCH_MS = -62_167_219_200_000
STATE_TEXT_DATE_MAX_EPOCH_MS = 253_402_300_799_999
_MS_PER_DAY = 86_400_000
_DATE_PREFIX_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_FORMATTER_EXPORT = "formatStateValue"


def state_text_format_problem(fmt: Any) -> str | None:
    """Return why ``fmt`` is not one of the closed formatters, or None when it is valid."""
    names = ", ".join(STATE_TEXT_FORMAT_NAMES[:-1]) + f", or {STATE_TEXT_FORMAT_NAMES[-1]}"
    if not isinstance(fmt, dict):
        return f"format must be an object whose format is {names}."
    name = fmt.get("format")
    if not isinstance(name, str) or name not in STATE_TEXT_FORMAT_FIELDS:
        return f"format must be {names}."
    fields = STATE_TEXT_FORMAT_FIELDS[name]
    if set(fmt) != fields:
        return f"The {name} format takes exactly the field(s) {', '.join(sorted(fields))}."
    if "digits" in fields:
        digits = fmt.get("digits")
        if type(digits) is not int or not 0 <= digits <= STATE_TEXT_FORMAT_MAX_DIGITS:
            return f"The {name} format takes digits as an integer from 0 through {STATE_TEXT_FORMAT_MAX_DIGITS}."
    if name == "currency" and fmt.get("currency") not in STATE_TEXT_CURRENCIES:
        return f"currency must be one of {', '.join(sorted(STATE_TEXT_CURRENCIES))}."
    return None


def js_number_string(value: float) -> str:
    """Return ECMAScript ``Number::toString(10)`` of a finite double, built from Python's repr."""
    negative, digits, point = _decimal_digits(repr(float(value)))
    if not digits:
        return "0"
    count = len(digits)
    if count <= point <= 21:
        body = digits + "0" * (point - count)
    elif 0 < point <= 21:
        body = f"{digits[:point]}.{digits[point:]}"
    elif -6 < point <= 0:
        body = "0." + "0" * -point + digits
    else:
        exponent = point - 1
        fraction = f".{digits[1:]}" if count > 1 else ""
        body = f"{digits[0]}{fraction}e{'+' if exponent >= 0 else '-'}{abs(exponent)}"
    return ("-" if negative else "") + body


def render_state_scalar(value: Any) -> str:
    """Render one state value with no formatter, exactly as the generated stateTextScalar does."""
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return value
    number = _finite_number(value)
    return js_number_string(number) if number is not None else ""


def format_state_value(value: Any, fmt: Mapping[str, Any] | None) -> str:
    """Render ``value`` through one closed formatter; values a formatter rejects use the scalar rendering."""
    if fmt is None:
        return render_state_scalar(value)
    name = fmt.get("format")
    if name == "date_iso":
        date = _date_iso(value)
        return date if date is not None else render_state_scalar(value)
    number = _finite_number(value)
    if number is None:
        return render_state_scalar(value)
    if name == "integer":
        return _number_text(number, 0, 0, "", "")
    if name == "decimal":
        return _number_text(number, int(fmt["digits"]), 0, "", "")
    if name == "percent":
        return _number_text(number, int(fmt["digits"]), 2, "", "%")
    currency = STATE_TEXT_CURRENCIES.get(str(fmt.get("currency")))
    if name == "currency" and currency is not None:
        return _number_text(number, currency[1], 0, currency[0], "")
    return render_state_scalar(value)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _decimal_digits(text: str) -> tuple[bool, str, int]:
    """Normalize a shortest round-trip number text into (negative, digits, point position).

    The value is ``0.<digits> * 10**point``; digits carry no leading or trailing zeros and are
    empty for zero. Python and JavaScript switch to exponent notation at different magnitudes,
    and both forms land in the same triple.
    """
    negative = text.startswith("-")
    body = text[1:] if negative else text
    mantissa, _, exponent = body.partition("e")
    whole, _, fraction = mantissa.partition(".")
    digits = whole + fraction
    trimmed = digits.lstrip("0")
    point = len(whole) + (int(exponent) if exponent else 0) - (len(digits) - len(trimmed))
    significant = trimmed.rstrip("0")
    return (negative, significant, point) if significant else (negative, "", 0)


def _increment_digits(digits: str) -> str:
    carry = 1
    out = ""
    for char in reversed(digits):
        total = ord(char) - 48 + carry
        carry = 1 if total > 9 else 0
        out = str(total % 10) + out
    return "1" + out if carry else out


def _round_units(digits: str, point: int, places: int) -> str:
    """Digits of ``|x| * 10**places`` rounded half away from zero, computed in digit space."""
    if not digits:
        return "0"
    keep = point + places
    if keep >= len(digits):
        return digits + "0" * (keep - len(digits))
    if keep < 0:
        return "0"
    head = digits[:keep]
    rounded = _increment_digits(head) if digits[keep] >= "5" else head
    return rounded.lstrip("0") or "0"


def _group_digits(whole: str) -> str:
    lead = len(whole) % 3 or 3
    return ",".join([whole[:lead], *(whole[index : index + 3] for index in range(lead, len(whole), 3))])


def _number_text(number: float, places: int, shift: int, prefix: str, suffix: str) -> str:
    negative, digits, point = _decimal_digits(repr(number))
    units = _round_units(digits, point + shift, places)
    padded = units.rjust(places + 1, "0") if places > 0 else units
    whole = padded[: len(padded) - places] if places > 0 else padded
    fraction = "." + padded[len(padded) - places :] if places > 0 else ""
    sign = "-" if negative and units.strip("0") else ""
    return sign + prefix + _group_digits(whole) + fraction + suffix


def _days_in_month(year: int, month: int) -> int:
    leap = (year % 4 == 0 and year % 100 != 0) or year % 400 == 0
    return (31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month - 1]


def _date_iso(value: Any) -> str | None:
    if isinstance(value, str):
        if _DATE_PREFIX_RE.match(value) is None:
            return None
        year, month, day = int(value[0:4]), int(value[5:7]), int(value[8:10])
        if not 1 <= month <= 12 or not 1 <= day <= _days_in_month(year, month):
            return None
        return value[:10]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        value = int(value)
    if not STATE_TEXT_DATE_MIN_EPOCH_MS <= value <= STATE_TEXT_DATE_MAX_EPOCH_MS:
        return None
    # Howard Hinnant's civil_from_days over proleptic Gregorian UTC days; integers only.
    z = value // _MS_PER_DAY + 719_468
    era = z // 146_097
    doe = z - era * 146_097
    yoe = (doe - doe // 1460 + doe // 36_524 - doe // 146_096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    day = doy - (153 * mp + 2) // 5 + 1
    month = mp + 3 if mp < 10 else mp - 9
    year = yoe + era * 400 + (1 if month <= 2 else 0)
    return f"{year:04d}-{month:02d}-{day:02d}"


def _annotation(annotation: str, *, typescript: bool) -> str:
    return f": {annotation}" if typescript else ""


def _signature(name: str, parameters: tuple[tuple[str, str], ...], return_type: str, *, typescript: bool) -> str:
    rendered = ", ".join(f"{parameter}{_annotation(kind, typescript=typescript)}" for parameter, kind in parameters)
    return f"function {name}({rendered}){_annotation(return_type, typescript=typescript)} {{"


def state_text_scalar_source(*, typescript: bool) -> list[str]:
    """The generated scalar fallback, byte-identical to the V4 stateTextScalar."""
    return [
        _signature("stateTextScalar", (("value", "unknown"),), "string", typescript=typescript),
        '  if (value === null || value === undefined) return "";',
        '  if (typeof value === "string") return value;',
        '  if (typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value))) return String(value);',
        '  return "";',
        "}",
    ]


def state_text_formatter_source(*, typescript: bool) -> list[str]:
    """The generated formatter kernel; it calls stateTextScalar for every value it rejects."""

    def ann(annotation: str) -> str:
        return _annotation(annotation, typescript=typescript)

    currencies = {code: [symbol, minor] for code, (symbol, minor) in STATE_TEXT_CURRENCIES.items()}
    return [
        f"const viewspecCurrencies{ann('Record<string, [string, number]>')} = {json.dumps(currencies, sort_keys=True)};",
        _signature("vsDecimalDigits", (("text", "string"),), "[boolean, string, number]", typescript=typescript),
        '  const negative = text.startsWith("-");',
        "  const body = negative ? text.slice(1) : text;",
        '  const mark = body.indexOf("e");',
        "  const mantissa = mark < 0 ? body : body.slice(0, mark);",
        '  const dot = mantissa.indexOf(".");',
        "  const whole = dot < 0 ? mantissa : mantissa.slice(0, dot);",
        '  const digits = whole + (dot < 0 ? "" : mantissa.slice(dot + 1));',
        '  const trimmed = digits.replace(/^0+/, "");',
        "  const point = whole.length + (mark < 0 ? 0 : Number(body.slice(mark + 1))) - (digits.length - trimmed.length);",
        '  const significant = trimmed.replace(/0+$/, "");',
        '  return significant ? [negative, significant, point] : [negative, "", 0];',
        "}",
        _signature("vsIncrementDigits", (("digits", "string"),), "string", typescript=typescript),
        "  let carry = 1;",
        '  let out = "";',
        "  for (let index = digits.length - 1; index >= 0; index -= 1) {",
        "    const total = digits.charCodeAt(index) - 48 + carry;",
        "    carry = total > 9 ? 1 : 0;",
        "    out = String(total % 10) + out;",
        "  }",
        '  return carry ? "1" + out : out;',
        "}",
        _signature(
            "vsRoundUnits",
            (("digits", "string"), ("point", "number"), ("places", "number")),
            "string",
            typescript=typescript,
        ),
        '  if (!digits) return "0";',
        "  const keep = point + places;",
        '  if (keep >= digits.length) return digits + "0".repeat(keep - digits.length);',
        '  if (keep < 0) return "0";',
        "  const head = digits.slice(0, keep);",
        "  const rounded = digits.charCodeAt(keep) >= 53 ? vsIncrementDigits(head) : head;",
        '  return rounded.replace(/^0+/, "") || "0";',
        "}",
        _signature("vsGroupDigits", (("whole", "string"),), "string", typescript=typescript),
        "  const lead = whole.length % 3 || 3;",
        "  let out = whole.slice(0, lead);",
        '  for (let index = lead; index < whole.length; index += 3) out += "," + whole.slice(index, index + 3);',
        "  return out;",
        "}",
        _signature(
            "vsNumberText",
            (
                ("value", "number"),
                ("places", "number"),
                ("shift", "number"),
                ("prefix", "string"),
                ("suffix", "string"),
            ),
            "string",
            typescript=typescript,
        ),
        "  const [negative, digits, point] = vsDecimalDigits(String(value));",
        "  const units = vsRoundUnits(digits, point + shift, places);",
        '  const padded = places > 0 ? units.padStart(places + 1, "0") : units;',
        "  const whole = places > 0 ? padded.slice(0, padded.length - places) : padded;",
        '  const fraction = places > 0 ? "." + padded.slice(padded.length - places) : "";',
        '  const sign = negative && /[1-9]/.test(units) ? "-" : "";',
        "  return sign + prefix + vsGroupDigits(whole) + fraction + suffix;",
        "}",
        _signature("vsDaysInMonth", (("year", "number"), ("month", "number")), "number", typescript=typescript),
        "  const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;",
        "  return [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];",
        "}",
        _signature("vsDateIso", (("value", "unknown"),), "string | null", typescript=typescript),
        '  if (typeof value === "string") {',
        "    if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}/.test(value)) return null;",
        "    const year = Number(value.slice(0, 4));",
        "    const month = Number(value.slice(5, 7));",
        "    const day = Number(value.slice(8, 10));",
        "    if (month < 1 || month > 12 || day < 1 || day > vsDaysInMonth(year, month)) return null;",
        "    return value.slice(0, 10);",
        "  }",
        '  if (typeof value !== "number" || !Number.isInteger(value)) return null;',
        f"  if (value < {STATE_TEXT_DATE_MIN_EPOCH_MS} || value > {STATE_TEXT_DATE_MAX_EPOCH_MS}) return null;",
        f"  const rest = ((value % {_MS_PER_DAY}) + {_MS_PER_DAY}) % {_MS_PER_DAY};",
        f"  const z = (value - rest) / {_MS_PER_DAY} + 719468;",
        "  const era = Math.floor(z / 146097);",
        "  const doe = z - era * 146097;",
        "  const yoe = Math.floor((doe - Math.floor(doe / 1460) + Math.floor(doe / 36524) - Math.floor(doe / 146096)) / 365);",
        "  const doy = doe - (365 * yoe + Math.floor(yoe / 4) - Math.floor(yoe / 100));",
        "  const mp = Math.floor((5 * doy + 2) / 153);",
        "  const day = doy - Math.floor((153 * mp + 2) / 5) + 1;",
        "  const month = mp < 10 ? mp + 3 : mp - 9;",
        "  const year = yoe + era * 400 + (month <= 2 ? 1 : 0);",
        '  return String(year).padStart(4, "0") + "-" + String(month).padStart(2, "0") + "-" + String(day).padStart(2, "0");',
        "}",
        _signature(
            _FORMATTER_EXPORT,
            (("value", "unknown"), ("format", "StateTextFormat | undefined")),
            "string",
            typescript=typescript,
        ),
        "  if (!format) return stateTextScalar(value);",
        '  if (format.format === "date_iso") {',
        "    const date = vsDateIso(value);",
        "    return date === null ? stateTextScalar(value) : date;",
        "  }",
        '  if (typeof value !== "number" || !Number.isFinite(value)) return stateTextScalar(value);',
        '  if (format.format === "integer") return vsNumberText(value, 0, 0, "", "");',
        '  if (format.format === "decimal") return vsNumberText(value, format.digits, 0, "", "");',
        '  if (format.format === "percent") return vsNumberText(value, format.digits, 2, "", "%");',
        "  const currency = viewspecCurrencies[format.currency];",
        '  return currency ? vsNumberText(value, currency[1], 0, currency[0], "") : stateTextScalar(value);',
        "}",
    ]


def state_text_passthrough_source(*, typescript: bool) -> list[str]:
    """The formatter entry point for a bundle that declares no format: the scalar rendering only."""
    return [
        _signature(
            _FORMATTER_EXPORT,
            (("value", "unknown"), ("_format", "StateTextFormat | undefined")),
            "string",
            typescript=typescript,
        ),
        "  return stateTextScalar(value);",
        "}",
    ]


def state_text_format_type_declarations() -> list[str]:
    return [
        "type StateTextFormat =",
        '  | { format: "integer" }',
        '  | { format: "decimal"; digits: number }',
        '  | { format: "percent"; digits: number }',
        '  | { format: "currency"; currency: string }',
        '  | { format: "date_iso" };',
    ]


def generate_state_text_formatter_module() -> str:
    """A standalone ES module of the generated kernel, for conformance tests against the vectors."""
    lines = [
        "// Generated by ViewSpec from viewspec.app_format. Do not edit.",
        *state_text_scalar_source(typescript=False),
        *state_text_formatter_source(typescript=False),
        f"export {{ {_FORMATTER_EXPORT}, stateTextScalar }};",
    ]
    return "\n".join(lines) + "\n"


__all__ = [
    "STATE_TEXT_CURRENCIES",
    "STATE_TEXT_DATE_MAX_EPOCH_MS",
    "STATE_TEXT_DATE_MIN_EPOCH_MS",
    "STATE_TEXT_FORMAT_FIELDS",
    "STATE_TEXT_FORMAT_MAX_DIGITS",
    "STATE_TEXT_FORMAT_NAMES",
    "format_state_value",
    "generate_state_text_formatter_module",
    "js_number_string",
    "render_state_scalar",
    "state_text_format_problem",
    "state_text_format_type_declarations",
    "state_text_formatter_source",
    "state_text_passthrough_source",
    "state_text_scalar_source",
]
