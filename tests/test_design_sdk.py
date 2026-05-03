from __future__ import annotations

from viewspec import CompileRequestPayload, DesignRequest, ViewSpecBuilder


def test_attach_design_reads_local_file(tmp_path):
    design_path = tmp_path / "DESIGN.md"
    design_path.write_text("name: Acme\ncolor.primary: #FFFFFF\n", encoding="utf-8")

    request = ViewSpecBuilder("design_file").attach_design(design_path).build_compile_request()

    assert isinstance(request, CompileRequestPayload)
    assert request.design == DesignRequest(content="name: Acme\ncolor.primary: #FFFFFF\n")
    assert request.to_json()["design"]["content"] == "name: Acme\ncolor.primary: #FFFFFF\n"


def test_attach_design_raw_string_skips_file_io():
    raw_design = "name: Raw Theme\ncolor.primary: #111111\n"

    request = ViewSpecBuilder("design_raw").attach_design(raw_design, is_path=False, lint=False).build_compile_request()

    assert request.design == DesignRequest(content=raw_design, lint=False)
    assert request.to_json()["design"] == {
        "format": "design.md",
        "content": raw_design,
        "lint": False,
    }


def test_build_compile_request_omits_design_by_default():
    builder = ViewSpecBuilder("design_omitted")

    request = builder.build_compile_request()

    assert request.design is None
    assert request.to_json() == builder.build_bundle().to_json()


def test_attach_design_last_call_wins(tmp_path):
    design_path = tmp_path / "DESIGN.md"
    design_path.write_text("name: File Theme\n", encoding="utf-8")

    request = (
        ViewSpecBuilder("design_last_wins")
        .attach_design(design_path)
        .attach_design("name: Inline Theme\n", is_path=False)
        .build_compile_request()
    )

    assert request.design == DesignRequest(content="name: Inline Theme\n")
