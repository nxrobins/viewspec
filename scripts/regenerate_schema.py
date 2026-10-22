#!/usr/bin/env python3
"""Reconstruct, verify, and regenerate the ViewSpec protobuf schema.

``src/viewspec/schema/viewspec_pb2.py`` is protoc output for ``universal_ui.proto`` (package
``universal_ui.v1``) and embeds the serialized ``FileDescriptorProto`` it was generated from.
``proto/universal_ui.proto`` is the human-readable source of that descriptor.

Commands (``print`` is the default):

  print     Render the descriptor embedded in viewspec_pb2.py as proto3 source on stdout so it
            can be diffed against proto/universal_ui.proto.
  check     Exit 1 with a unified diff when proto/universal_ui.proto does not describe the same
            schema as the embedded descriptor (comments and blank lines are ignored).
  generate  Run protoc from grpcio-tools over proto/universal_ui.proto and write viewspec_pb2.py
            and viewspec_pb2.pyi into src/viewspec/schema/. ``--stub-only`` writes only the .pyi.

protoc names its outputs after the .proto file (universal_ui_pb2.*); this repository keeps them
as viewspec_pb2.* and the script renames them on the way in. The committed module records the
protoc release that produced it in its ValidateProtobufRuntimeVersion call (protobuf 6.31.1 is
protoc 31.1). Regenerating with a different protoc changes that call, so the protobuf floor in
pyproject.toml has to move with it; ``generate`` warns when the versions differ.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import importlib.util
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TextIO

from google.protobuf import descriptor as descriptor_mod
from google.protobuf import descriptor_pb2, text_format
from google.protobuf.message import Message

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTO_FILE = REPO_ROOT / "proto" / "universal_ui.proto"
SCHEMA_DIR = REPO_ROOT / "src" / "viewspec" / "schema"
GENERATED_MODULE = SCHEMA_DIR / "viewspec_pb2.py"
GENERATED_STUB = SCHEMA_DIR / "viewspec_pb2.pyi"
PROTOC_OUTPUT_STEM = "universal_ui_pb2"

HEADER = """\
// ViewSpec protobuf schema (package universal_ui.v1).
//
// Source for src/viewspec/schema/viewspec_pb2.py and viewspec_pb2.pyi. Regenerate both with
//   python scripts/regenerate_schema.py generate
// and confirm this file still matches the descriptor embedded in viewspec_pb2.py with
//   python scripts/regenerate_schema.py check
"""

_FieldProto = descriptor_pb2.FieldDescriptorProto
_SCALAR_TYPES = {
    _FieldProto.TYPE_DOUBLE: "double",
    _FieldProto.TYPE_FLOAT: "float",
    _FieldProto.TYPE_INT64: "int64",
    _FieldProto.TYPE_UINT64: "uint64",
    _FieldProto.TYPE_INT32: "int32",
    _FieldProto.TYPE_FIXED64: "fixed64",
    _FieldProto.TYPE_FIXED32: "fixed32",
    _FieldProto.TYPE_BOOL: "bool",
    _FieldProto.TYPE_STRING: "string",
    _FieldProto.TYPE_BYTES: "bytes",
    _FieldProto.TYPE_UINT32: "uint32",
    _FieldProto.TYPE_SFIXED32: "sfixed32",
    _FieldProto.TYPE_SFIXED64: "sfixed64",
    _FieldProto.TYPE_SINT32: "sint32",
    _FieldProto.TYPE_SINT64: "sint64",
}
_MESSAGE_RANGE_MAX = 0x20000000  # exclusive upper bound protoc stores for "to max" in messages
_ENUM_RANGE_MAX = 0x7FFFFFFF  # inclusive upper bound protoc stores for "to max" in enums


class SchemaError(RuntimeError):
    """The generated module, the committed .proto, or protoc did not behave as expected."""


# --- reading the generated module -------------------------------------------------------------


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def embedded_serialized_file(source: str) -> bytes:
    """Return the bytes a protoc-generated module passes to ``AddSerializedFile``."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and _call_name(node) == "AddSerializedFile" and node.args:
            value = node.args[0]
            if isinstance(value, ast.Constant) and isinstance(value.value, bytes):
                return value.value
    raise SchemaError("no AddSerializedFile(b'...') call found in the generated module")


def gencode_version(source: str) -> tuple[int, int, int]:
    """Return the protobuf version recorded by ``ValidateProtobufRuntimeVersion`` in generated source."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and _call_name(node) == "ValidateProtobufRuntimeVersion":
            numbers = [
                argument.value
                for argument in node.args
                if isinstance(argument, ast.Constant) and type(argument.value) is int
            ]
            if len(numbers) == 3:
                return (numbers[0], numbers[1], numbers[2])
    raise SchemaError("no ValidateProtobufRuntimeVersion(...) call found in the generated module")


def embedded_descriptor(module_path: Path = GENERATED_MODULE) -> descriptor_pb2.FileDescriptorProto:
    """Parse the FileDescriptorProto embedded in a protoc-generated Python module."""
    source = module_path.read_text(encoding="utf-8")
    return descriptor_pb2.FileDescriptorProto.FromString(embedded_serialized_file(source))


# --- rendering a descriptor as .proto source --------------------------------------------------


def _quote(value: str) -> str:
    escaped = []
    for character in value:
        if character == "\\":
            escaped.append("\\\\")
        elif character == '"':
            escaped.append('\\"')
        elif character == "\n":
            escaped.append("\\n")
        elif character == "\r":
            escaped.append("\\r")
        elif character == "\t":
            escaped.append("\\t")
        elif " " <= character <= "~":
            escaped.append(character)
        else:
            escaped.extend(f"\\{byte:03o}" for byte in character.encode("utf-8"))
    return '"' + "".join(escaped) + '"'


def default_json_name(field_name: str) -> str:
    """protoc's default JSON name: drop underscores and capitalise the letter after each one."""
    out: list[str] = []
    capitalize = False
    for character in field_name:
        if character == "_":
            capitalize = True
            continue
        out.append(character.upper() if capitalize else character)
        capitalize = False
    return "".join(out)


def _range_text(start: int, end: int, *, inclusive_end: bool, max_value: int) -> str:
    last = end if inclusive_end else end - 1
    if last == start:
        return str(start)
    if end >= max_value:
        return f"{start} to max"
    return f"{start} to {last}"


def _is_repeated(field: descriptor_mod.FieldDescriptor) -> bool:
    # protobuf 7 removed FieldDescriptor.label from the upb runtime in favour of is_repeated.
    repeated = getattr(field, "is_repeated", None)
    if repeated is not None:
        return bool(repeated)
    return field.label == descriptor_mod.FieldDescriptor.LABEL_REPEATED


class _ProtoRenderer:
    def __init__(self, file: descriptor_pb2.FileDescriptorProto) -> None:
        if file.syntax == "editions":
            raise SchemaError("editions files are not supported by this renderer")
        self.file = file
        self.package = file.package
        self.proto3 = file.syntax == "proto3"
        self.symbols = self._symbols()

    # names and options

    def _symbols(self) -> set[str]:
        """Every fully qualified name protoc can bind a path component to within this file."""
        symbols: set[str] = set()
        parts = self.package.split(".") if self.package else []
        symbols.update(".".join(parts[: index + 1]) for index in range(len(parts)))

        def visit(
            messages: Iterable[descriptor_pb2.DescriptorProto],
            enums: Iterable[descriptor_pb2.EnumDescriptorProto],
            scope: str,
        ) -> None:
            for enum in enums:
                symbols.add(f"{scope}.{enum.name}" if scope else enum.name)
            for message in messages:
                full_name = f"{scope}.{message.name}" if scope else message.name
                symbols.add(full_name)
                visit(message.nested_type, message.enum_type, full_name)

        visit(self.file.message_type, self.file.enum_type, self.package)
        return symbols

    @staticmethod
    def _enclosing_scopes(scope: str) -> list[str]:
        scopes = []
        while scope:
            scopes.append(scope)
            scope = scope.rpartition(".")[0]
        scopes.append("")
        return scopes

    def _resolve(self, name: str, scope: str) -> str:
        """Mirror protoc: bind the first component in the innermost scope that defines it."""
        first = name.partition(".")[0]
        for enclosing in self._enclosing_scopes(scope):
            if (f"{enclosing}.{first}" if enclosing else first) in self.symbols:
                return f"{enclosing}.{name}" if enclosing else name
        return name

    def _relative_name(self, type_name: str, scope: str = "") -> str:
        """The shortest name that protoc resolves back to ``type_name`` from ``scope``."""
        full_name = type_name.lstrip(".")
        scope = scope or self.package
        for enclosing in self._enclosing_scopes(scope):
            if enclosing and not full_name.startswith(enclosing + "."):
                continue
            candidate = full_name[len(enclosing) + 1 :] if enclosing else full_name
            if self._resolve(candidate, scope) == full_name:
                return candidate
        return "." + full_name

    def _option_value(self, field: descriptor_mod.FieldDescriptor, value: Any) -> str:
        if field.type == descriptor_mod.FieldDescriptor.TYPE_ENUM:
            return field.enum_type.values_by_number[value].name
        if field.type == descriptor_mod.FieldDescriptor.TYPE_BOOL:
            return "true" if value else "false"
        if field.type == descriptor_mod.FieldDescriptor.TYPE_STRING:
            return _quote(str(value))
        if field.type == descriptor_mod.FieldDescriptor.TYPE_BYTES:
            return _quote(bytes(value).decode("latin-1"))
        if field.type == descriptor_mod.FieldDescriptor.TYPE_MESSAGE:
            return "{ " + text_format.MessageToString(value, as_one_line=True) + " }"
        return str(value)

    def _option_items(self, options: Message) -> list[str]:
        items: list[str] = []
        for field, value in options.ListFields():
            if field.name in {"uninterpreted_option", "map_entry", "features"}:
                continue
            name = f"({field.full_name})" if field.is_extension else field.name
            if _is_repeated(field):
                items.extend(f"{name} = {self._option_value(field, item)}" for item in value)
            else:
                items.append(f"{name} = {self._option_value(field, value)}")
        return items

    def _option_lines(self, options: Message, pad: str) -> list[str]:
        return [f"{pad}option {item};" for item in self._option_items(options)]

    # fields

    def _label(self, field: descriptor_pb2.FieldDescriptorProto) -> str:
        if field.label == _FieldProto.LABEL_REPEATED:
            return "repeated "
        if field.label == _FieldProto.LABEL_REQUIRED:
            return "required "
        if field.proto3_optional or not self.proto3:
            return "optional "
        return ""

    def _type_text(self, field: descriptor_pb2.FieldDescriptorProto, scope: str) -> str:
        if field.type in (_FieldProto.TYPE_MESSAGE, _FieldProto.TYPE_ENUM):
            return self._relative_name(field.type_name, scope)
        if field.type == _FieldProto.TYPE_GROUP:
            raise SchemaError(f"proto2 groups are not supported: {field.name}")
        return _SCALAR_TYPES[field.type]

    def _default_literal(self, field: descriptor_pb2.FieldDescriptorProto) -> str:
        if field.type == _FieldProto.TYPE_STRING:
            return _quote(field.default_value)
        if field.type == _FieldProto.TYPE_BYTES:
            return '"' + field.default_value + '"'
        return field.default_value

    def _field_suffix(self, field: descriptor_pb2.FieldDescriptorProto) -> str:
        parts: list[str] = []
        if field.HasField("json_name") and field.json_name != default_json_name(field.name):
            parts.append(f"json_name = {_quote(field.json_name)}")
        if field.HasField("default_value"):
            parts.append(f"default = {self._default_literal(field)}")
        if field.HasField("options"):
            parts.extend(self._option_items(field.options))
        return f" [{', '.join(parts)}]" if parts else ""

    def _field_line(
        self,
        field: descriptor_pb2.FieldDescriptorProto,
        pad: str,
        map_entries: dict[str, descriptor_pb2.DescriptorProto],
        scope: str,
    ) -> str:
        entry = map_entries.get(field.type_name) if field.type == _FieldProto.TYPE_MESSAGE else None
        if entry is not None:
            by_number = {entry_field.number: entry_field for entry_field in entry.field}
            key, value = by_number[1], by_number[2]
            type_text = f"map<{self._type_text(key, scope)}, {self._type_text(value, scope)}>"
            label = ""
        else:
            type_text = self._type_text(field, scope)
            label = self._label(field)
        return f"{pad}{label}{type_text} {field.name} = {field.number}{self._field_suffix(field)};"

    # declarations

    def _enum(self, enum: descriptor_pb2.EnumDescriptorProto, depth: int) -> list[str]:
        pad, inner = "  " * depth, "  " * (depth + 1)
        lines = [f"{pad}enum {enum.name} {{"]
        if enum.HasField("options"):
            lines.extend(self._option_lines(enum.options, inner))
        if enum.reserved_range:
            ranges = ", ".join(
                _range_text(item.start, item.end, inclusive_end=True, max_value=_ENUM_RANGE_MAX)
                for item in enum.reserved_range
            )
            lines.append(f"{inner}reserved {ranges};")
        if enum.reserved_name:
            lines.append(f"{inner}reserved {', '.join(_quote(name) for name in enum.reserved_name)};")
        for value in enum.value:
            items = self._option_items(value.options) if value.HasField("options") else []
            suffix = f" [{', '.join(items)}]" if items else ""
            lines.append(f"{inner}{value.name} = {value.number}{suffix};")
        lines.append(f"{pad}}}")
        return lines

    def _extend_blocks(
        self, extensions: list[descriptor_pb2.FieldDescriptorProto], depth: int, scope: str
    ) -> list[list[str]]:
        pad, inner = "  " * depth, "  " * (depth + 1)
        grouped: dict[str, list[descriptor_pb2.FieldDescriptorProto]] = {}
        for extension in extensions:
            grouped.setdefault(extension.extendee, []).append(extension)
        blocks: list[list[str]] = []
        for extendee, fields in grouped.items():
            lines = [f"{pad}extend {self._relative_name(extendee, scope)} {{"]
            lines.extend(self._field_line(field, inner, {}, scope) for field in fields)
            lines.append(f"{pad}}}")
            blocks.append(lines)
        return blocks

    def _message(self, message: descriptor_pb2.DescriptorProto, depth: int, scope: str) -> list[str]:
        pad, inner = "  " * depth, "  " * (depth + 1)
        full_name = f"{scope}.{message.name}" if scope else message.name
        map_entries = {
            f".{full_name}.{nested.name}": nested for nested in message.nested_type if nested.options.map_entry
        }
        lines = [f"{pad}message {message.name} {{"]
        if message.HasField("options"):
            lines.extend(self._option_lines(message.options, inner))
        if message.reserved_range:
            ranges = ", ".join(
                _range_text(item.start, item.end, inclusive_end=False, max_value=_MESSAGE_RANGE_MAX)
                for item in message.reserved_range
            )
            lines.append(f"{inner}reserved {ranges};")
        if message.reserved_name:
            lines.append(f"{inner}reserved {', '.join(_quote(name) for name in message.reserved_name)};")
        if message.extension_range:
            ranges = ", ".join(
                _range_text(item.start, item.end, inclusive_end=False, max_value=_MESSAGE_RANGE_MAX)
                for item in message.extension_range
            )
            lines.append(f"{inner}extensions {ranges};")

        rendered_oneofs: set[int] = set()
        for field in message.field:
            in_oneof = field.HasField("oneof_index") and not field.proto3_optional
            if not in_oneof:
                lines.append(self._field_line(field, inner, map_entries, full_name))
                continue
            if field.oneof_index in rendered_oneofs:
                continue
            rendered_oneofs.add(field.oneof_index)
            declaration = message.oneof_decl[field.oneof_index]
            lines.append(f"{inner}oneof {declaration.name} {{")
            if declaration.HasField("options"):
                lines.extend(self._option_lines(declaration.options, inner + "  "))
            lines.extend(
                self._field_line(member, inner + "  ", map_entries, full_name)
                for member in message.field
                if member.HasField("oneof_index")
                and member.oneof_index == field.oneof_index
                and not member.proto3_optional
            )
            lines.append(f"{inner}}}")

        nested_blocks: list[list[str]] = []
        nested_blocks.extend(self._enum(enum, depth + 1) for enum in message.enum_type)
        nested_blocks.extend(
            self._message(nested, depth + 1, full_name)
            for nested in message.nested_type
            if not nested.options.map_entry
        )
        nested_blocks.extend(self._extend_blocks(list(message.extension), depth + 1, full_name))
        for block in nested_blocks:
            lines.append("")
            lines.extend(block)
        lines.append(f"{pad}}}")
        return lines

    def _service(self, service: descriptor_pb2.ServiceDescriptorProto) -> list[str]:
        lines = [f"service {service.name} {{"]
        if service.HasField("options"):
            lines.extend(self._option_lines(service.options, "  "))
        for method in service.method:
            request = ("stream " if method.client_streaming else "") + self._relative_name(method.input_type)
            response = ("stream " if method.server_streaming else "") + self._relative_name(method.output_type)
            signature = f"  rpc {method.name}({request}) returns ({response})"
            items = self._option_items(method.options) if method.HasField("options") else []
            if items:
                lines.append(f"{signature} {{")
                lines.extend(f"    option {item};" for item in items)
                lines.append("  }")
            else:
                lines.append(f"{signature};")
        lines.append("}")
        return lines

    def render(self, header: str) -> str:
        file = self.file
        sections: list[str] = []
        if header:
            sections.append(header.rstrip("\n"))
        sections.append(f'syntax = "{file.syntax or "proto2"}";')
        if file.package:
            sections.append(f"package {file.package};")
        if file.dependency:
            public = set(file.public_dependency)
            weak = set(file.weak_dependency)
            imports = []
            for index, dependency in enumerate(file.dependency):
                modifier = "public " if index in public else "weak " if index in weak else ""
                imports.append(f"import {modifier}{_quote(dependency)};")
            sections.append("\n".join(imports))
        if file.HasField("options"):
            option_lines = self._option_lines(file.options, "")
            if option_lines:
                sections.append("\n".join(option_lines))
        blocks: list[list[str]] = []
        blocks.extend(self._enum(enum, 0) for enum in file.enum_type)
        blocks.extend(self._message(message, 0, file.package) for message in file.message_type)
        blocks.extend(self._extend_blocks(list(file.extension), 0, file.package))
        blocks.extend(self._service(service) for service in file.service)
        sections.extend("\n".join(block) for block in blocks)
        return "\n\n".join(sections) + "\n"


def render_proto(file: descriptor_pb2.FileDescriptorProto, *, header: str = HEADER) -> str:
    """Render a FileDescriptorProto as .proto source (proto2 or proto3; editions and groups are rejected)."""
    return _ProtoRenderer(file).render(header)


def strip_comments(text: str) -> str:
    """Drop ``//`` comments, trailing whitespace, and blank lines so schemas compare structurally."""
    kept: list[str] = []
    for line in text.splitlines():
        quote: str | None = None
        escaped = False
        result: list[str] = []
        for index, character in enumerate(line):
            if quote is not None:
                result.append(character)
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = None
            elif character in {'"', "'"}:
                quote = character
                result.append(character)
            elif line.startswith("//", index):
                break
            else:
                result.append(character)
        stripped = "".join(result).rstrip()
        if stripped:
            kept.append(stripped)
    return "\n".join(kept) + "\n"


# --- protoc --------------------------------------------------------------------------------


def protoc_available() -> bool:
    return importlib.util.find_spec("grpc_tools") is not None


def protoc_version() -> tuple[int, int] | None:
    """Return the (major, minor) of the protoc bundled with grpcio-tools, or None when it is not installed."""
    if not protoc_available():
        return None
    completed = subprocess.run(
        [sys.executable, "-m", "grpc_tools.protoc", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    match = re.search(r"(\d+)\.(\d+)", completed.stdout)
    if completed.returncode != 0 or match is None:
        raise SchemaError(f"could not read the protoc version: {completed.stderr.strip() or completed.stdout.strip()}")
    return (int(match.group(1)), int(match.group(2)))


def expected_protoc_version(module_path: Path = GENERATED_MODULE) -> tuple[int, int]:
    """The protoc release that produced the committed module: protobuf X.Y.Z gencode comes from protoc Y.Z."""
    _, minor, patch = gencode_version(module_path.read_text(encoding="utf-8"))
    return (minor, patch)


def _protoc_include_dir() -> Path:
    import grpc_tools  # noqa: PLC0415  (optional tool, imported only when protoc is used)

    return Path(grpc_tools.__file__).resolve().parent / "_proto"


def run_protoc(
    proto_file: Path,
    out_dir: Path,
    *,
    python_out: bool = False,
    pyi_out: bool = False,
    descriptor_set_out: Path | None = None,
) -> None:
    """Invoke the grpcio-tools protoc on one .proto file."""
    arguments = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"-I{proto_file.parent}",
        f"-I{_protoc_include_dir()}",
    ]
    if python_out:
        arguments.append(f"--python_out={out_dir}")
    if pyi_out:
        arguments.append(f"--pyi_out={out_dir}")
    if descriptor_set_out is not None:
        arguments.append(f"--descriptor_set_out={descriptor_set_out}")
    arguments.append(str(proto_file))
    completed = subprocess.run(arguments, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise SchemaError(completed.stderr.strip() or f"protoc exited with status {completed.returncode}")


def compile_descriptor(proto_file: Path = PROTO_FILE) -> descriptor_pb2.FileDescriptorProto:
    """Compile a .proto file with protoc and return its FileDescriptorProto."""
    with tempfile.TemporaryDirectory() as temporary:
        descriptor_set = Path(temporary) / "descriptor_set.pb"
        run_protoc(proto_file, Path(temporary), descriptor_set_out=descriptor_set)
        file_set = descriptor_pb2.FileDescriptorSet.FromString(descriptor_set.read_bytes())
    for file in file_set.file:
        if file.name == proto_file.name:
            return file
    raise SchemaError(f"protoc did not emit a descriptor for {proto_file.name}")


def generate_sources(proto_file: Path = PROTO_FILE) -> dict[str, bytes]:
    """Return the generated module and stub, keyed by their names in src/viewspec/schema/."""
    with tempfile.TemporaryDirectory() as temporary:
        out_dir = Path(temporary)
        run_protoc(proto_file, out_dir, python_out=True, pyi_out=True)
        return {
            GENERATED_MODULE.name: (out_dir / f"{PROTOC_OUTPUT_STEM}.py").read_bytes(),
            GENERATED_STUB.name: (out_dir / f"{PROTOC_OUTPUT_STEM}.pyi").read_bytes(),
        }


def normalize_descriptor(file: descriptor_pb2.FileDescriptorProto) -> descriptor_pb2.FileDescriptorProto:
    """Drop what differs between protoc releases and between protoc output and the embedded copy.

    protoc writes a ``json_name`` for every field into descriptor sets but strips default ones
    from the descriptor it embeds in generated Python; source locations are never embedded.
    """
    normalized = descriptor_pb2.FileDescriptorProto()
    normalized.CopyFrom(file)
    normalized.ClearField("source_code_info")

    def strip_fields(fields: Iterable[descriptor_pb2.FieldDescriptorProto]) -> None:
        for field in fields:
            if field.HasField("json_name") and field.json_name == default_json_name(field.name):
                field.ClearField("json_name")

    def strip_message(message: descriptor_pb2.DescriptorProto) -> None:
        strip_fields(message.field)
        strip_fields(message.extension)
        for nested in message.nested_type:
            strip_message(nested)

    for message in normalized.message_type:
        strip_message(message)
    strip_fields(normalized.extension)
    return normalized


# --- commands -------------------------------------------------------------------------------


def check_committed_proto(
    proto_file: Path = PROTO_FILE,
    module_path: Path = GENERATED_MODULE,
    *,
    out: TextIO = sys.stderr,
) -> int:
    """Return 0 when the committed .proto describes the embedded descriptor, else print a diff and return 1."""
    expected = strip_comments(render_proto(embedded_descriptor(module_path), header=""))
    actual = strip_comments(proto_file.read_text(encoding="utf-8")) if proto_file.exists() else ""
    if expected == actual:
        return 0
    out.writelines(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            actual.splitlines(keepends=True),
            fromfile=f"{module_path.name} (embedded descriptor)",
            tofile=str(proto_file.relative_to(REPO_ROOT)) if proto_file.is_relative_to(REPO_ROOT) else str(proto_file),
        )
    )
    return 1


def generate(*, stub_only: bool = False, out: TextIO = sys.stderr) -> int:
    version = protoc_version()
    if version is None:
        out.write("grpcio-tools is not installed; `uv pip install grpcio-tools` (or pip) and retry.\n")
        return 2
    expected = expected_protoc_version()
    out.write(f"protoc {version[0]}.{version[1]} (grpcio-tools)\n")
    if version != expected:
        out.write(
            f"warning: the committed {GENERATED_MODULE.name} was produced by protoc {expected[0]}.{expected[1]}; "
            f"output from protoc {version[0]}.{version[1]} will not be byte-identical and will require the "
            "matching protobuf runtime, so the protobuf floor in pyproject.toml must move with it.\n"
        )
    sources = generate_sources()
    targets = [GENERATED_STUB] if stub_only else [GENERATED_MODULE, GENERATED_STUB]
    for target in targets:
        data = sources[target.name]
        changed = not target.exists() or target.read_bytes() != data
        target.write_bytes(data)
        out.write(f"{'updated' if changed else 'unchanged'} {target.relative_to(REPO_ROOT)}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("print", help="render the embedded descriptor as .proto source on stdout")
    commands.add_parser("check", help="diff proto/universal_ui.proto against the embedded descriptor")
    generate_parser = commands.add_parser("generate", help="regenerate viewspec_pb2.py and viewspec_pb2.pyi")
    generate_parser.add_argument("--stub-only", action="store_true", help="write only viewspec_pb2.pyi")
    args = parser.parse_args(argv)
    try:
        if args.command in (None, "print"):
            sys.stdout.write(render_proto(embedded_descriptor()))
            return 0
        if args.command == "check":
            return check_committed_proto()
        return generate(stub_only=args.stub_only)
    except SchemaError as error:
        parser.exit(1, f"regenerate_schema: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
