"""File uploads: file inputs in the lift, files to upload, and checked upload steps."""

from __future__ import annotations

import base64
import json

import pytest

from viewspec.raw_html import lift_html
from viewspec.raw_html_actions import (
    MAX_UPLOAD_BYTES,
    AgentActionError,
    UploadFile,
    accepts,
    plan_control_action,
    plan_form_call,
    plan_upload,
)


def _control(html: str):
    [control] = lift_html(html, profile="agent").controls
    return control


def test_files_are_read_from_text_or_base64():
    text = UploadFile.from_json({"name": "notes.txt", "content": "héllo"})
    assert (text.name, text.data, text.mime_type, text.size) == ("notes.txt", "héllo".encode(), "text/plain", 6)
    raw = UploadFile.from_json(
        {"name": "dot.png", "content": base64.b64encode(b"\x89PNG").decode(), "encoding": "base64"}
    )
    assert raw.data == b"\x89PNG" and raw.mime_type == "image/png"
    typed = UploadFile.from_json({"name": "data", "content": "1,2", "mime_type": "text/csv"})
    assert typed.mime_type == "text/csv"
    assert UploadFile.named("blob", b"").mime_type == "application/octet-stream"
    assert "héllo" not in repr(text)  # The bytes are never shown.


@pytest.mark.parametrize(
    ("payload", "words"),
    [
        ({"name": "a.png", "content": "not base64!", "encoding": "base64"}, "not base64"),
        ({"name": "a.txt"}, "needs content"),
        ({"content": "x"}, "needs a name"),
        ({"name": "a.txt", "content": "x", "encoding": "hex"}, "needs content"),
        ({"path": "photo.jpg"}, "upload folder"),
        ("a.txt", "an object"),
    ],
)
def test_bad_files_are_refused(payload, words):
    with pytest.raises(AgentActionError, match=words) as caught:
        UploadFile.from_json(payload)
    assert caught.value.code == "AGENT_ACTION_VALUE_INVALID"


@pytest.mark.parametrize(
    ("accept", "name", "allowed"),
    [
        (None, "anything.bin", True),
        (".pdf,.docx", "CV.PDF", True),
        (".pdf,.docx", "cv.txt", False),
        ("image/*", "a.png", True),
        ("image/*", "a.txt", False),
        ("text/csv", "a.csv", True),
        (" .zip , image/* ", "b.jpeg", True),
    ],
)
def test_accept_matches_extensions_and_types(accept, name, allowed):
    assert accepts(accept, UploadFile.named(name, b"x")) is allowed


def test_a_file_input_shows_its_files_and_limits():
    control = _control(
        '<input type="file" aria-label="Photos" accept="image/*" multiple data-lift-files="a.png, b.png" data-lift-ref="e3">'
    )
    assert (control.control, control.value, control.accept, control.multiple) == (
        "file",
        "a.png, b.png",
        "image/*",
        True,
    )


def test_uploads_to_a_file_input_are_checked():
    single = _control('<input type="file" aria-label="CV" accept=".pdf" data-lift-ref="e1">')
    pdf = UploadFile.named("cv.pdf", b"%PDF-1.4")

    step = plan_upload(single, [pdf])
    assert step.to_json() == {
        "op": "upload",
        "node_id": single.node_id,
        "ref": "e1",
        "name": "CV",
        "files": [{"name": "cv.pdf", "mime_type": "application/pdf", "size": 8}],
    }
    assert "%PDF" not in json.dumps(step.to_json(redact=False))
    assert plan_control_action(single, "upload", {"name": "cv.pdf", "content": "%PDF"})[0].op == "upload"
    assert plan_upload(single, []).files == ()  # An empty list clears the input.

    for files, words in (
        ([pdf, pdf], "takes one file"),
        ([UploadFile.named("cv.exe", b"MZ")], "not a type"),
        ([UploadFile.named("../cv.pdf", b"x")], "not a file name"),
        ([UploadFile.named(" ", b"x")], "not a file name"),
        ([UploadFile.named("big.pdf", b"x" * (MAX_UPLOAD_BYTES + 1))], "larger than 10 MB"),
    ):
        with pytest.raises(AgentActionError, match=words):
            plan_upload(single, files)

    many = _control('<input type="file" aria-label="Scans" multiple>')
    three = [UploadFile.named(f"s{i}.pdf", b"x" * (9 * 1024 * 1024)) for i in range(3)]
    with pytest.raises(AgentActionError, match="25 MB together"):
        plan_upload(many, three)
    assert len(plan_upload(many, three[:2]).files) == 2


def test_a_button_may_open_a_file_chooser():
    button = _control('<button data-lift-ref="e9">Add photos</button>')
    step = plan_upload(button, [UploadFile.named("a.txt", b"x")])  # Its chooser's accept is checked later.
    assert (step.op, step.ref) == ("upload", "e9")
    with pytest.raises(AgentActionError, match="at least one file"):
        plan_upload(button, [])
    for html, code in (
        ('<input aria-label="Name">', "AGENT_ACTION_UNSUPPORTED"),
        ("<button disabled>Add photos</button>", "AGENT_ACTION_DISABLED"),
    ):
        with pytest.raises(AgentActionError) as caught:
            plan_upload(_control(html), [UploadFile.named("a.txt", b"x")])
        assert caught.value.code == code
    with pytest.raises(AgentActionError, match="cannot be filled"):
        plan_control_action(_control('<input type="file" aria-label="F">'), "fill", "a.txt")


def test_form_tools_take_files():
    lift = lift_html(
        """
        <form aria-label="Apply" enctype="multipart/form-data">
          <label>Name <input name="name"></label>
          <label>Résumé <input type="file" name="resume" accept=".pdf,.docx" required></label>
          <button>Send</button>
        </form>
        """,
        profile="agent",
    )
    [schema] = lift.form_tool_schemas()
    resume = schema["input_schema"]["properties"]["resume"]

    assert resume["type"] == "array" and resume["maxItems"] == 1 and "accepts .pdf,.docx" in resume["description"]
    assert "resume" in schema["input_schema"]["required"]
    steps = plan_form_call(lift, "apply", {"name": "Bo", "resume": [{"name": "cv.pdf", "content": "%PDF"}]})
    assert [step.op for step in steps] == ["fill", "upload", "click"]
    assert steps[1].files[0].name == "cv.pdf"
    with pytest.raises(AgentActionError) as caught:
        plan_form_call(lift, "apply", {"resume": [{"path": "cv.pdf"}]})
    assert "upload folder" in caught.value.details[0]["message"]
