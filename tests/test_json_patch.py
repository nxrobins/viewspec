"""RFC 6902 JSON Patch and RFC 6901 pointers: the appendix A cases, round trips, and the bounds."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from viewspec.error_codes import ERROR_CODES
from viewspec.json_patch import (
    MAX_JSON_PATCH_OPERATIONS,
    JsonPatchError,
    apply_json_patch,
    format_json_pointer,
    json_equal,
    parse_json_pointer,
    resolve_json_pointer,
)


def _apply(document: Any, patch_text: str) -> Any:
    return apply_json_patch(document, json.loads(patch_text))


def _fails(document: Any, operations: Any, code: str) -> JsonPatchError:
    with pytest.raises(JsonPatchError) as caught:
        apply_json_patch(document, operations)
    assert caught.value.code == code
    assert code in ERROR_CODES
    return caught.value


# RFC 6902 appendix A ---------------------------------------------------------------------------


def test_a1_adding_an_object_member() -> None:
    assert _apply({"foo": "bar"}, '[{ "op": "add", "path": "/baz", "value": "qux" }]') == {"baz": "qux", "foo": "bar"}


def test_a2_adding_an_array_element() -> None:
    assert _apply({"foo": ["bar", "baz"]}, '[{ "op": "add", "path": "/foo/1", "value": "qux" }]') == {
        "foo": ["bar", "qux", "baz"]
    }


def test_a3_removing_an_object_member() -> None:
    assert _apply({"baz": "qux", "foo": "bar"}, '[{ "op": "remove", "path": "/baz" }]') == {"foo": "bar"}


def test_a4_removing_an_array_element() -> None:
    assert _apply({"foo": ["bar", "qux", "baz"]}, '[{ "op": "remove", "path": "/foo/1" }]') == {"foo": ["bar", "baz"]}


def test_a5_replacing_a_value() -> None:
    assert _apply({"baz": "qux", "foo": "bar"}, '[{ "op": "replace", "path": "/baz", "value": "boo" }]') == {
        "baz": "boo",
        "foo": "bar",
    }


def test_a6_moving_a_value() -> None:
    document = {"foo": {"bar": "baz", "waldo": "fred"}, "qux": {"corge": "grault"}}

    assert _apply(document, '[{ "op": "move", "from": "/foo/waldo", "path": "/qux/thud" }]') == {
        "foo": {"bar": "baz"},
        "qux": {"corge": "grault", "thud": "fred"},
    }


def test_a7_moving_an_array_element() -> None:
    assert _apply(
        {"foo": ["all", "grass", "cows", "eat"]}, '[{ "op": "move", "from": "/foo/1", "path": "/foo/3" }]'
    ) == {"foo": ["all", "cows", "eat", "grass"]}


def test_a8_testing_a_value_success() -> None:
    document = {"baz": "qux", "foo": ["a", 2, "c"]}
    patch = '[{ "op": "test", "path": "/baz", "value": "qux" }, { "op": "test", "path": "/foo/1", "value": 2 }]'

    assert _apply(document, patch) == document


def test_a9_testing_a_value_error() -> None:
    error = _fails({"baz": "qux"}, [{"op": "test", "path": "/baz", "value": "bar"}], "PATCH_TEST_FAILED")

    assert error.index == 0


def test_a10_adding_a_nested_member_object() -> None:
    assert _apply({"foo": "bar"}, '[{ "op": "add", "path": "/child", "value": { "grandchild": { } } }]') == {
        "foo": "bar",
        "child": {"grandchild": {}},
    }


def test_a11_ignoring_unrecognized_elements() -> None:
    assert _apply({"foo": "bar"}, '[{ "op": "add", "path": "/baz", "value": "qux", "xyz": 123 }]') == {
        "foo": "bar",
        "baz": "qux",
    }


def test_a12_adding_to_a_nonexistent_target() -> None:
    error = _fails({"foo": "bar"}, [{"op": "add", "path": "/baz/bat", "value": "qux"}], "PATCH_APPLY_FAILED")

    assert "/baz" in error.message


def test_a13_invalid_json_patch_document() -> None:
    # The appendix case has two "op" members in one object. The intent tools read a patch file with
    # strict JSON parsing, which rejects the duplicate key (tests/test_repair_loop_cli.py); the
    # operation object itself must name exactly one known op.
    _fails({"foo": "bar"}, [{"path": "/baz", "value": "qux"}], "PATCH_INVALID")
    _fails({"foo": "bar"}, [{"op": "frobnicate", "path": "/baz"}], "PATCH_INVALID")


def test_a14_escape_ordering() -> None:
    document = {"/": 9, "~1": 10}

    assert _apply(document, '[{"op": "test", "path": "/~01", "value": 10}]') == document
    assert resolve_json_pointer(document, "/~1") == 9


def test_a15_comparing_strings_and_numbers() -> None:
    _fails({"/": 9, "~1": 10}, [{"op": "test", "path": "/~01", "value": "10"}], "PATCH_TEST_FAILED")


def test_a16_adding_an_array_value() -> None:
    assert _apply({"foo": ["bar"]}, '[{ "op": "add", "path": "/foo/-", "value": ["abc", "def"] }]') == {
        "foo": ["bar", ["abc", "def"]]
    }


# Pointers, equality, and the remaining rules -------------------------------------------------


@pytest.mark.parametrize(
    ("pointer", "tokens"),
    [
        ("", []),
        ("/", [""]),
        ("/a~1b", ["a/b"]),
        ("/c%d", ["c%d"]),
        ("/m~0n", ["m~n"]),
        ("/ ", [" "]),
        ("/0", ["0"]),
        ("/a/b/c", ["a", "b", "c"]),
    ],
)
def test_pointer_parsing_round_trips(pointer: str, tokens: list[str]) -> None:
    assert parse_json_pointer(pointer) == tokens
    assert format_json_pointer(tokens) == pointer


@pytest.mark.parametrize("pointer", ["a", "a/b", "/~", "/~2", "/a~"])
def test_malformed_pointers_are_invalid(pointer: str) -> None:
    with pytest.raises(JsonPatchError) as caught:
        parse_json_pointer(pointer)
    assert caught.value.code == "PATCH_INVALID"


def test_json_equality_follows_json_not_python() -> None:
    assert json_equal(1, 1.0)
    assert not json_equal(True, 1)
    assert not json_equal(0, False)
    assert not json_equal("1", 1)
    assert not json_equal(None, 0)
    assert json_equal({"a": [1, {"b": None}]}, {"a": [1.0, {"b": None}]})
    assert not json_equal({"a": 1}, {"a": 1, "b": 2})
    assert not json_equal([1, 2], [2, 1])


def test_add_replaces_an_existing_member_and_appends_with_dash() -> None:
    assert apply_json_patch({"a": 1}, [{"op": "add", "path": "/a", "value": 2}]) == {"a": 2}
    assert apply_json_patch([1], [{"op": "add", "path": "/-", "value": 2}]) == [1, 2]
    assert apply_json_patch([1], [{"op": "add", "path": "/1", "value": 2}]) == [1, 2]
    assert apply_json_patch({"a": 1}, [{"op": "add", "path": "", "value": [0]}]) == [0]
    assert apply_json_patch({"a": 1}, [{"op": "replace", "path": "", "value": None}]) is None


def test_copy_deep_copies_the_source_value() -> None:
    patched = apply_json_patch({"a": {"x": [1]}}, [{"op": "copy", "from": "/a", "path": "/b"}])

    patched["a"]["x"].append(2)

    assert patched["b"] == {"x": [1]}


@pytest.mark.parametrize(
    ("document", "operation", "fragment"),
    [
        ([1, 2], {"op": "add", "path": "/3", "value": 0}, "out of range"),
        ([1, 2], {"op": "add", "path": "/01", "value": 0}, "not a decimal index"),
        ([1, 2], {"op": "add", "path": "/x", "value": 0}, "not a decimal index"),
        ([1, 2], {"op": "remove", "path": "/-"}, "names no array element"),
        ([1, 2], {"op": "replace", "path": "/2", "value": 0}, "out of range"),
        ({"a": 1}, {"op": "remove", "path": "/b"}, "does not exist"),
        ({"a": 1}, {"op": "remove", "path": ""}, "root cannot be removed"),
        ({"a": 1}, {"op": "replace", "path": "/b", "value": 0}, "does not exist"),
        ({"a": 1}, {"op": "add", "path": "/a/b", "value": 0}, "scalar"),
        ({"a": {"b": 1}}, {"op": "move", "from": "/a", "path": "/a/b"}, "proper prefix"),
        ({"a": 1}, {"op": "move", "from": "/zz", "path": "/b"}, "does not exist"),
        ({"a": 1}, {"op": "copy", "from": "/zz", "path": "/b"}, "does not exist"),
        ({"a": 1}, {"op": "test", "path": "/zz", "value": 1}, "does not exist"),
    ],
)
def test_operations_that_do_not_apply_name_the_operation(document: Any, operation: dict, fragment: str) -> None:
    error = _fails(document, [operation], "PATCH_APPLY_FAILED")

    assert error.index == 0
    assert fragment in error.message
    assert "Operation 0" in error.message


@pytest.mark.parametrize(
    "operations",
    [
        {"op": "add", "path": "/a", "value": 1},
        [None],
        [{"op": "add", "value": 1}],
        [{"op": "add", "path": "/a"}],
        [{"op": "test", "path": "/a"}],
        [{"op": "replace", "path": "/a"}],
        [{"op": "move", "path": "/a"}],
        [{"op": "copy", "path": "/a", "from": 3}],
        [{"op": "add", "path": 3, "value": 1}],
        [{"op": "add", "path": "a", "value": 1}],
        [{"op": "add", "path": "/~x", "value": 1}],
        [{"op": 7, "path": "/a"}],
    ],
)
def test_malformed_operations_are_invalid(operations: Any) -> None:
    _fails({"a": 1}, operations, "PATCH_INVALID")


def test_the_operation_count_is_bounded() -> None:
    operations = [{"op": "add", "path": "/a", "value": 1}] * (MAX_JSON_PATCH_OPERATIONS + 1)

    _fails({}, operations, "PATCH_TOO_MANY_OPERATIONS")
    assert apply_json_patch({}, operations[:MAX_JSON_PATCH_OPERATIONS]) == {"a": 1}
    with pytest.raises(JsonPatchError) as caught:
        apply_json_patch({}, operations[:3], max_operations=2)
    assert caught.value.code == "PATCH_TOO_MANY_OPERATIONS"


def test_the_input_document_is_never_mutated_even_when_a_later_operation_fails() -> None:
    document = {"a": [1, 2], "b": {"c": 3}}
    before = copy.deepcopy(document)

    apply_json_patch(document, [{"op": "add", "path": "/a/-", "value": 9}])
    _fails(document, [{"op": "remove", "path": "/b/c"}, {"op": "remove", "path": "/zz"}], "PATCH_APPLY_FAILED")

    assert document == before


def test_errors_name_the_failing_operation_index() -> None:
    error = _fails(
        {"a": 1}, [{"op": "test", "path": "/a", "value": 1}, {"op": "remove", "path": "/b"}], "PATCH_APPLY_FAILED"
    )

    assert error.index == 1
    assert error.message.startswith("Operation 1 (remove /b)")


# Round trips over generated documents --------------------------------------------------------

_scalars = st.one_of(st.none(), st.booleans(), st.integers(-1000, 1000), st.text(max_size=8))
_keys = st.text(alphabet="ab~/ 0", min_size=0, max_size=3)
_documents = st.recursive(
    _scalars,
    lambda children: st.one_of(st.lists(children, max_size=4), st.dictionaries(_keys, children, max_size=4)),
    max_leaves=12,
)


def _pointers(document: Any, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    found = [prefix]
    if isinstance(document, dict):
        for key, value in document.items():
            found.extend(_pointers(value, (*prefix, key)))
    elif isinstance(document, list):
        for index, value in enumerate(document):
            found.extend(_pointers(value, (*prefix, str(index))))
    return found


@settings(max_examples=150, deadline=None)
@given(document=_documents, value=_documents, data=st.data())
def test_replace_then_replace_back_restores_the_document(document: Any, value: Any, data: st.DataObject) -> None:
    pointer = format_json_pointer(data.draw(st.sampled_from(_pointers(document))))
    before = copy.deepcopy(document)
    original = resolve_json_pointer(document, pointer)

    replaced = apply_json_patch(document, [{"op": "replace", "path": pointer, "value": value}])
    restored = apply_json_patch(replaced, [{"op": "replace", "path": pointer, "value": original}])

    assert json_equal(resolve_json_pointer(replaced, pointer), value)
    assert json_equal(restored, before)
    assert document == before


@settings(max_examples=150, deadline=None)
@given(document=_documents, data=st.data())
def test_remove_then_add_at_the_same_location_restores_the_document(document: Any, data: st.DataObject) -> None:
    candidates = [tokens for tokens in _pointers(document) if tokens]
    if not candidates:
        return
    tokens = data.draw(st.sampled_from(candidates))
    pointer = format_json_pointer(tokens)
    before = copy.deepcopy(document)
    original = resolve_json_pointer(document, pointer)

    removed = apply_json_patch(document, [{"op": "remove", "path": pointer}])
    restored = apply_json_patch(removed, [{"op": "add", "path": pointer, "value": original}])

    assert json_equal(restored, before)
    assert json_equal(apply_json_patch(document, [{"op": "test", "path": pointer, "value": original}]), before)
    assert document == before


@settings(max_examples=150, deadline=None)
@given(document=st.dictionaries(_keys, _documents, max_size=4), key=_keys, value=_documents)
def test_add_a_new_member_then_remove_it_restores_the_object(document: dict, key: str, value: Any) -> None:
    if key in document:
        return
    pointer = format_json_pointer((key,))
    before = copy.deepcopy(document)

    added = apply_json_patch(document, [{"op": "add", "path": pointer, "value": value}])
    restored = apply_json_patch(added, [{"op": "remove", "path": pointer}])

    assert json_equal(resolve_json_pointer(added, pointer), value)
    assert json_equal(restored, before)
    assert document == before
