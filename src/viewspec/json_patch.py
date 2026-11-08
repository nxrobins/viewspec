"""RFC 6902 JSON Patch over RFC 6901 JSON Pointers, with no dependency beyond the standard library.

``apply_json_patch`` never mutates its input. It deep-copies the document, applies every operation in
order, and returns the result, or raises ``JsonPatchError`` naming the first operation that failed.
The patch is bounded: it holds at most ``MAX_JSON_PATCH_OPERATIONS`` operations. The caller bounds the
size of the result, because the byte count depends on how the caller serializes it.

Error codes, all registered in ``viewspec.error_codes``:

- ``PATCH_INVALID``: the patch is not a list of operation objects, or an operation is malformed: an
  unknown ``op``, a missing ``path``, ``from`` or ``value``, or a pointer that is not RFC 6901 syntax.
- ``PATCH_TOO_MANY_OPERATIONS``: the patch holds more than ``MAX_JSON_PATCH_OPERATIONS`` operations.
- ``PATCH_APPLY_FAILED``: a well-formed operation does not apply to the document: a missing target,
  an array index out of range, or a ``move`` whose ``from`` is a proper prefix of its ``path``.
- ``PATCH_TEST_FAILED``: a ``test`` operation found a different value.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

MAX_JSON_PATCH_OPERATIONS = 256
JSON_PATCH_OPERATIONS = ("add", "remove", "replace", "move", "copy", "test")
_VALUE_OPERATIONS = frozenset({"add", "replace", "test"})
_FROM_OPERATIONS = frozenset({"move", "copy"})


class JsonPatchError(ValueError):
    """A JSON Patch that cannot be applied. ``code`` is a registered ``PATCH_*`` code."""

    def __init__(self, code: str, message: str, *, index: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.index = index


def parse_json_pointer(pointer: str) -> list[str]:
    """Split an RFC 6901 pointer into unescaped reference tokens; ``""`` is the whole document."""
    if not isinstance(pointer, str):
        raise JsonPatchError("PATCH_INVALID", f"JSON Pointer must be a string, not {type(pointer).__name__}.")
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise JsonPatchError("PATCH_INVALID", f"JSON Pointer {pointer!r} must start with '/' or be empty.")
    tokens: list[str] = []
    for raw in pointer[1:].split("/"):
        tokens.append(_unescape_token(raw, pointer))
    return tokens


def _unescape_token(raw: str, pointer: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "~":
            escape = raw[index + 1 : index + 2]
            if escape == "0":
                out.append("~")
            elif escape == "1":
                out.append("/")
            else:
                raise JsonPatchError(
                    "PATCH_INVALID", f"JSON Pointer {pointer!r} holds an invalid escape; use ~0 for '~' and ~1 for '/'."
                )
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def format_json_pointer(tokens: Sequence[str]) -> str:
    """The inverse of :func:`parse_json_pointer`."""
    return "".join("/" + token.replace("~", "~0").replace("/", "~1") for token in tokens)


def json_equal(left: Any, right: Any) -> bool:
    """JSON value equality as RFC 6902 ``test`` defines it: ``true`` differs from ``1``, ``1`` equals ``1.0``."""
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(json_equal(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right, strict=True))
    return False


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    """Return the value ``pointer`` names in ``document``; raise ``PATCH_APPLY_FAILED`` when it is absent."""
    return _resolve_tokens(document, parse_json_pointer(pointer), pointer, index=None)


def apply_json_patch(
    document: Any,
    operations: Any,
    *,
    max_operations: int = MAX_JSON_PATCH_OPERATIONS,
) -> Any:
    """Apply an RFC 6902 patch to a deep copy of ``document`` and return the patched copy."""
    if not isinstance(operations, list):
        raise JsonPatchError("PATCH_INVALID", "A JSON Patch document must be an array of operation objects.")
    if len(operations) > max_operations:
        raise JsonPatchError(
            "PATCH_TOO_MANY_OPERATIONS",
            f"The patch holds {len(operations)} operations; the limit is {max_operations}.",
        )
    result = copy.deepcopy(document)
    for index, operation in enumerate(operations):
        result = _apply_operation(result, operation, index)
    return result


def _apply_operation(document: Any, operation: Any, index: int) -> Any:
    if not isinstance(operation, Mapping):
        raise JsonPatchError("PATCH_INVALID", f"Operation {index} is not an object.", index=index)
    op = operation.get("op")
    if not isinstance(op, str) or op not in JSON_PATCH_OPERATIONS:
        raise JsonPatchError(
            "PATCH_INVALID",
            f"Operation {index} has op {op!r}; use one of {', '.join(JSON_PATCH_OPERATIONS)}.",
            index=index,
        )
    if "path" not in operation:
        raise JsonPatchError("PATCH_INVALID", f"Operation {index} ({op}) has no path member.", index=index)
    path = operation["path"]
    tokens = _parse_pointer_member(path, index, op, "path")
    label = f"Operation {index} ({op} {path})"
    if op in _VALUE_OPERATIONS and "value" not in operation:
        raise JsonPatchError("PATCH_INVALID", f"{label} has no value member.", index=index)
    if op in _FROM_OPERATIONS:
        if "from" not in operation:
            raise JsonPatchError("PATCH_INVALID", f"{label} has no from member.", index=index)
        from_tokens = _parse_pointer_member(operation["from"], index, op, "from")
        label = f"Operation {index} ({op} {operation['from']} to {path})"
    else:
        from_tokens = []

    if op == "add":
        return _add(document, tokens, copy.deepcopy(operation["value"]), label, index)
    if op == "remove":
        document, _removed = _remove(document, tokens, label, index)
        return document
    if op == "replace":
        _resolve_tokens(document, tokens, path, index=index, label=label)
        return _replace(document, tokens, copy.deepcopy(operation["value"]), label, index)
    if op == "test":
        actual = _resolve_tokens(document, tokens, path, index=index, label=label)
        if not json_equal(actual, operation["value"]):
            raise JsonPatchError("PATCH_TEST_FAILED", f"{label} found a different value.", index=index)
        return document
    if op == "copy":
        value = copy.deepcopy(_resolve_tokens(document, from_tokens, operation["from"], index=index, label=label))
        return _add(document, tokens, value, label, index)
    # move
    if len(from_tokens) < len(tokens) and tokens[: len(from_tokens)] == from_tokens:
        raise JsonPatchError("PATCH_APPLY_FAILED", f"{label}: from must not be a proper prefix of path.", index=index)
    _resolve_tokens(document, from_tokens, operation["from"], index=index, label=label)
    if from_tokens == tokens:
        return document
    document, value = _remove(document, from_tokens, label, index)
    return _add(document, tokens, value, label, index)


def _parse_pointer_member(value: Any, index: int, op: str, member: str) -> list[str]:
    if not isinstance(value, str):
        raise JsonPatchError(
            "PATCH_INVALID", f"Operation {index} ({op}) {member} must be a JSON Pointer string.", index=index
        )
    try:
        return parse_json_pointer(value)
    except JsonPatchError as exc:
        raise JsonPatchError("PATCH_INVALID", f"Operation {index} ({op}) {member}: {exc.message}", index=index) from exc


def _resolve_tokens(
    document: Any, tokens: Sequence[str], pointer: str, *, index: int | None, label: str | None = None
) -> Any:
    current = document
    prefix = f"{label}: " if label else ""
    for position, token in enumerate(tokens):
        if isinstance(current, Mapping):
            if token not in current:
                raise JsonPatchError(
                    "PATCH_APPLY_FAILED",
                    f"{prefix}{format_json_pointer(tokens[: position + 1])} does not exist.",
                    index=index,
                )
            current = current[token]
        elif isinstance(current, list):
            current = current[
                _array_index(token, len(current), allow_end=False, where=pointer, index=index, label=label)
            ]
        else:
            raise JsonPatchError(
                "PATCH_APPLY_FAILED",
                f"{prefix}{format_json_pointer(tokens[:position])} is a scalar and has no member {token!r}.",
                index=index,
            )
    return current


def _array_index(token: str, length: int, *, allow_end: bool, where: str, index: int | None, label: str | None) -> int:
    prefix = f"{label}: " if label else ""
    if token == "-":
        if allow_end:
            return length
        raise JsonPatchError("PATCH_APPLY_FAILED", f"{prefix}'-' names no array element in {where}.", index=index)
    if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
        raise JsonPatchError(
            "PATCH_APPLY_FAILED", f"{prefix}array index {token!r} in {where} is not a decimal index.", index=index
        )
    position = int(token)
    limit = length if allow_end else length - 1
    if position > limit:
        raise JsonPatchError(
            "PATCH_APPLY_FAILED",
            f"{prefix}array index {position} in {where} is out of range for {length} elements.",
            index=index,
        )
    return position


def _add(document: Any, tokens: Sequence[str], value: Any, label: str, index: int) -> Any:
    if not tokens:
        return value
    parent = _resolve_tokens(document, tokens[:-1], format_json_pointer(tokens[:-1]), index=index, label=label)
    last = tokens[-1]
    if isinstance(parent, Mapping):
        if not isinstance(parent, dict):
            raise JsonPatchError("PATCH_APPLY_FAILED", f"{label}: target object is read-only.", index=index)
        parent[last] = value
    elif isinstance(parent, list):
        position = _array_index(
            last, len(parent), allow_end=True, where=format_json_pointer(tokens), index=index, label=label
        )
        parent.insert(position, value)
    else:
        raise JsonPatchError(
            "PATCH_APPLY_FAILED",
            f"{label}: {format_json_pointer(tokens[:-1])} is a scalar and cannot hold member {last!r}.",
            index=index,
        )
    return document


def _replace(document: Any, tokens: Sequence[str], value: Any, label: str, index: int) -> Any:
    if not tokens:
        return value
    parent = _resolve_tokens(document, tokens[:-1], format_json_pointer(tokens[:-1]), index=index, label=label)
    last = tokens[-1]
    if isinstance(parent, dict):
        parent[last] = value
    elif isinstance(parent, list):
        parent[
            _array_index(
                last, len(parent), allow_end=False, where=format_json_pointer(tokens), index=index, label=label
            )
        ] = value
    else:  # pragma: no cover - _resolve_tokens already rejected a scalar parent
        raise JsonPatchError("PATCH_APPLY_FAILED", f"{label}: target parent is a scalar.", index=index)
    return document


def _remove(document: Any, tokens: Sequence[str], label: str, index: int) -> tuple[Any, Any]:
    if not tokens:
        raise JsonPatchError("PATCH_APPLY_FAILED", f"{label}: the document root cannot be removed.", index=index)
    parent = _resolve_tokens(document, tokens[:-1], format_json_pointer(tokens[:-1]), index=index, label=label)
    last = tokens[-1]
    if isinstance(parent, dict):
        if last not in parent:
            raise JsonPatchError(
                "PATCH_APPLY_FAILED", f"{label}: {format_json_pointer(tokens)} does not exist.", index=index
            )
        return document, parent.pop(last)
    if isinstance(parent, list):
        position = _array_index(
            last, len(parent), allow_end=False, where=format_json_pointer(tokens), index=index, label=label
        )
        return document, parent.pop(position)
    raise JsonPatchError(
        "PATCH_APPLY_FAILED",
        f"{label}: {format_json_pointer(tokens[:-1])} is a scalar and has no member {last!r}.",
        index=index,
    )


__all__ = [
    "JSON_PATCH_OPERATIONS",
    "MAX_JSON_PATCH_OPERATIONS",
    "JsonPatchError",
    "apply_json_patch",
    "format_json_pointer",
    "json_equal",
    "parse_json_pointer",
    "resolve_json_pointer",
]
