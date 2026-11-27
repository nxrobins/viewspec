"""Typed editor planning and bounded schema output, independent of browser transport."""

import html
import json

import pytest

from viewspec.raw_html import lift_html
from viewspec.raw_html_actions import AgentActionError, plan_control_action, plan_form_call

REVISION = "editor-00000000-0000-4000-8000-000000000000"


def page(*, mode="rich", coverage="complete", ops=("append_paragraph",), text="Existing document", blocks=()):
    descriptor = {
        "version": 1,
        "mode": mode,
        "adapter": "html-dom-v1",
        "coverage": coverage,
        "revision": REVISION,
        "operations": list(ops),
        "blocks": list(blocks),
    }
    return lift_html(
        f"""<form aria-label="Article"><div role="textbox" aria-label="Body"
        contenteditable="true" data-lift-ref="editor" data-lift-editor='{html.escape(json.dumps(descriptor))}'>
        <p>{text}</p><input type="checkbox" aria-label="Done" data-lift-editor-owner="editor"></div>
        <input name="note"><button>Save</button></form>""",
        profile="agent",
    )


def value(text="Added", op="append_paragraph"):
    return {"base_revision": REVISION, "operations": [{"op": op, "text": text}]}


def test_act_and_form_plan_share_typed_editor_intent():
    lift = page()
    editor = next(c for c in lift.controls if c.name == "Body")
    single = plan_control_action(editor, "edit", value())[0]
    batch = plan_form_call(lift, "article", {"body": value()}, submit=False)
    assert single == batch[0] and single.op == "edit"
    assert batch.intents[0].kind == "editor" and batch.intents[0].expected == value()
    assert batch.intents[1].preserve
    assert next(c for c in lift.controls if c.name == "Done").editor_owner == "editor"
    assert set(lift.form_tool_schemas()[0]["input_schema"]["properties"]) == {"body", "note"}


@pytest.mark.parametrize(
    "bad",
    [
        "Markdown",
        {},
        {"base_revision": "old", "operations": []},
        value("one\ntwo"),
        value("", "append_paragraph"),
        value("X" * 10001),
        value("X", "import_markdown"),
        {"base_revision": REVISION, "operations": [value(), value()]},
    ],
)
def test_invalid_editor_intent_is_rejected_before_a_plan_exists(bad):
    lift = page()
    with pytest.raises(AgentActionError):
        plan_form_call(lift, "article", {"body": bad})


def test_form_schema_does_not_duplicate_full_editor_document():
    small = page(text="A").form_tool_schemas()[0]["input_schema"]["properties"]["body"]
    large = page(text="A" * 15000).form_tool_schemas()[0]["input_schema"]["properties"]["body"]
    assert "default" not in large
    assert small == large
    assert len(json.dumps(large)) < 1000


def test_static_editor_without_live_capabilities_is_unknown():
    lift = lift_html('<div contenteditable="true" aria-label="Body">Hello</div>', profile="agent")
    assert lift.controls[0].editor.coverage == "unknown"
    with pytest.raises(AgentActionError):
        plan_control_action(lift.controls[0], "fill", "replacement")


BLOCK = {"ref": "block-00000000-0000-4000-8000-000000000001", "position": 1, "kind": "h1"}


def test_scoped_editor_operation_has_same_schema_and_plan_for_act_and_call():
    from jsonschema import validate

    lift = page(ops=("replace_block_text", "append_paragraph"), blocks=(BLOCK,))
    editor = next(c for c in lift.controls if c.name == "Body")
    scoped = value("New title", "replace_block_text")
    scoped["operations"][0]["block"] = BLOCK["ref"]
    schema = lift.form_tool_schemas()[0]["input_schema"]["properties"]["body"]
    validate(scoped, schema)
    validate(value(), schema)
    assert (
        plan_control_action(editor, "edit", scoped)[0]
        == plan_form_call(lift, "article", {"body": scoped}, submit=False)[0]
    )
    assert schema["x-editor"]["blocks"] == [BLOCK]


@pytest.mark.parametrize("block", [None, [], {}, "wrong", "block-00000000-0000-4000-8000-000000000002"])
def test_scoped_editor_rejects_missing_foreign_or_invalid_block(block):
    lift = page(ops=("replace_block_text",), blocks=(BLOCK,))
    scoped = value("New", "replace_block_text")
    if block is not None:
        scoped["operations"][0]["block"] = block
    with pytest.raises(AgentActionError):
        plan_form_call(lift, "article", {"body": scoped})


@pytest.mark.parametrize("blocks", [[BLOCK, BLOCK], [BLOCK | {"kind": []}], [BLOCK | {"position": True}], [BLOCK] * 33])
def test_malformed_block_descriptors_do_not_advertise_usable_editor(blocks):
    lift = page(ops=("replace_block_text",), blocks=blocks)
    assert next(c for c in lift.controls if c.name == "Body").editor.coverage == "unknown"
