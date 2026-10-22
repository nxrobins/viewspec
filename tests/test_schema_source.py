"""The protobuf schema source, the generated module, and its stub stay in sync.

proto/universal_ui.proto is the human-readable schema; src/viewspec/schema/viewspec_pb2.py embeds
the FileDescriptorProto protoc produced from it, and viewspec_pb2.pyi is the matching stub. These
tests pin the three together, and pin the protobuf floor in pyproject.toml to the gencode version
recorded in the generated module so the package imports on the oldest runtime it claims.
"""

from __future__ import annotations

import ast
import io
import re
import tomllib
from pathlib import Path

import pytest
from google.protobuf import descriptor_pb2, text_format

from scripts import regenerate_schema as schema

ROOT = Path(__file__).resolve().parents[1]
PROTO_FILE = ROOT / "proto" / "universal_ui.proto"
GENERATED_MODULE = ROOT / "src" / "viewspec" / "schema" / "viewspec_pb2.py"
GENERATED_STUB = ROOT / "src" / "viewspec" / "schema" / "viewspec_pb2.pyi"

requires_protoc = pytest.mark.skipif(not schema.protoc_available(), reason="grpcio-tools (protoc) is not installed")


def _embedded() -> descriptor_pb2.FileDescriptorProto:
    return schema.embedded_descriptor(GENERATED_MODULE)


def _descriptor_text(file: descriptor_pb2.FileDescriptorProto) -> str:
    return text_format.MessageToString(schema.normalize_descriptor(file))


def test_embedded_descriptor_is_the_universal_ui_v1_schema():
    file = _embedded()

    assert file.name == "universal_ui.proto"
    assert file.package == "universal_ui.v1"
    assert file.syntax == "proto3"
    assert list(file.dependency) == ["google/protobuf/struct.proto"]
    names = [message.name for message in file.message_type]
    assert names[0] == "ValueList"
    assert {"SemanticSubstrate", "ViewSpec", "IntentBundle", "ASTBundle"} <= set(names)


def test_committed_proto_matches_the_embedded_descriptor():
    rendered = schema.render_proto(_embedded())
    committed = PROTO_FILE.read_text(encoding="utf-8")

    assert schema.strip_comments(rendered) == schema.strip_comments(committed)
    assert schema.check_committed_proto(PROTO_FILE, GENERATED_MODULE, out=io.StringIO()) == 0


def test_check_prints_a_diff_when_the_proto_drifts(tmp_path: Path):
    committed = PROTO_FILE.read_text(encoding="utf-8")
    drifted = committed.replace("  string title = 3;\n", "  string title = 3;\n  string subtitle = 4;\n")
    assert drifted != committed
    proto = tmp_path / "universal_ui.proto"
    proto.write_text(drifted, encoding="utf-8")
    out = io.StringIO()

    assert schema.check_committed_proto(proto, GENERATED_MODULE, out=out) == 1
    assert "+  string subtitle = 4;" in out.getvalue()


def test_print_command_renders_the_committed_proto(capsys: pytest.CaptureFixture[str]):
    assert schema.main([]) == 0
    assert capsys.readouterr().out == PROTO_FILE.read_text(encoding="utf-8")
    assert schema.main(["check"]) == 0


def test_stub_declares_every_message_field_and_field_number():
    classes: dict[str, ast.ClassDef] = {}

    def collect(body: list[ast.stmt], prefix: str) -> None:
        for statement in body:
            if isinstance(statement, ast.ClassDef):
                classes[f"{prefix}{statement.name}"] = statement
                collect(statement.body, f"{prefix}{statement.name}.")

    collect(ast.parse(GENERATED_STUB.read_text(encoding="utf-8")).body, "")

    def check(message: descriptor_pb2.DescriptorProto, prefix: str) -> None:
        full_name = f"{prefix}{message.name}"
        assert full_name in classes, full_name
        annotated = {
            statement.target.id
            for statement in classes[full_name].body
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
        }
        for field in message.field:
            assert field.name in annotated, f"{full_name}.{field.name}"
            assert f"{field.name.upper()}_FIELD_NUMBER" in annotated, f"{full_name}.{field.name}"
        for nested in message.nested_type:
            check(nested, f"{full_name}.")

    for message in _embedded().message_type:
        check(message, "")


def test_protobuf_floor_matches_the_gencode_version():
    gencode = ".".join(str(part) for part in schema.gencode_version(GENERATED_MODULE.read_text(encoding="utf-8")))
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = [dep for dep in pyproject["project"]["dependencies"] if re.match(r"protobuf\b", dep)]
    assert len(requirements) == 1, pyproject["project"]["dependencies"]
    floor = re.search(r">=\s*([0-9][0-9A-Za-z.]*)", requirements[0])

    assert floor is not None, f"{requirements[0]!r} declares no >= floor"
    assert floor.group(1) == gencode, (
        f"pyproject.toml declares {requirements[0]!r} but viewspec_pb2.py was generated for protobuf {gencode}, "
        f"and the protobuf runtime rejects gencode newer than itself; declare protobuf>={gencode}"
    )


@requires_protoc
def test_committed_proto_compiles_to_the_embedded_descriptor():
    compiled = schema.compile_descriptor(PROTO_FILE)

    assert compiled.name == "universal_ui.proto"
    assert _descriptor_text(compiled) == _descriptor_text(_embedded())


@requires_protoc
def test_generated_module_and_stub_are_byte_identical_when_protoc_matches():
    version = schema.protoc_version()
    expected = schema.expected_protoc_version(GENERATED_MODULE)
    if version != expected:
        pytest.skip(f"protoc {version} differs from the protoc {expected} that produced the committed module")

    sources = schema.generate_sources(PROTO_FILE)

    assert sources[GENERATED_MODULE.name] == GENERATED_MODULE.read_bytes()
    assert sources[GENERATED_STUB.name] == GENERATED_STUB.read_bytes()


@requires_protoc
def test_renderer_round_trips_enums_oneofs_maps_options_and_services(tmp_path: Path):
    source = """\
syntax = "proto3";

package demo.v1;

option java_package = "com.example.demo";

enum Mode {
  MODE_UNSPECIFIED = 0;
  MODE_FAST = 1 [deprecated = true];
}

message Outer {
  option deprecated = true;
  reserved 9, 20 to 29;
  reserved "legacy";
  string id = 1;
  Kind kind = 2;
  Inner inner = 3;
  map<string, Inner> inners = 4;
  oneof payload {
    string text = 5;
    bytes blob = 6;
  }
  optional int64 maybe = 7 [json_name = "maybeValue"];
  repeated Mode modes = 8 [deprecated = true];

  enum Kind {
    KIND_UNSPECIFIED = 0;
    KIND_A = 1;
  }

  message Inner {
    int32 count = 1;
  }
}

service Demo {
  rpc Get(Outer) returns (Outer);
  rpc Watch(Outer) returns (stream Outer);
}
"""
    original = tmp_path / "original" / "demo.proto"
    original.parent.mkdir()
    original.write_text(source, encoding="utf-8")
    first = schema.compile_descriptor(original)

    rendered = tmp_path / "rendered" / "demo.proto"
    rendered.parent.mkdir()
    rendered.write_text(schema.render_proto(first, header=""), encoding="utf-8")
    second = schema.compile_descriptor(rendered)

    assert _descriptor_text(second) == _descriptor_text(first)
    assert schema.strip_comments(rendered.read_text(encoding="utf-8")) == schema.strip_comments(source)
