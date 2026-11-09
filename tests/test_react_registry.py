"""The host component registry for the React targets.

The compiler owns structure, semantics, and provenance; the host owns the components. These tests pin
the closed primitive contract against the emitters and the compiled corpus, the fail-closed registry
file checks, byte-level determinism, the manifest section and its ``viewspec check`` drift detection,
the registry-independent semantic digest, the CLI surface, and the bounded host proof's mounting of
the host module.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from viewspec import ASTBundle, compile
from viewspec.cli import main as cli_main
from viewspec.conformance import load_conformance_corpus
from viewspec.emitters.html_tailwind import SUPPORTED_PRIMITIVES
from viewspec.emitters.react_registry import (
    DEFAULT_REACT_COMPONENT_REGISTRY,
    REACT_PRIMITIVE_CONTRACT,
    REACT_PRIMITIVE_ELEMENT_TAGS,
    REACT_PRIMITIVE_KINDS,
    REACT_REGISTRY_DEFAULT_EXPORTS,
    ReactComponentRegistry,
    ReactRegistryError,
    default_react_registry_module,
    load_react_component_registry,
    parse_react_component_registry,
    validate_react_registry_artifact,
)
from viewspec.emitters.react_tailwind_tsx import ReactTailwindTsxEmitter
from viewspec.emitters.react_tsx import ReactTsxEmitter
from viewspec.error_codes import ERROR_CODES
from viewspec.host_verify import HostVerifyFailure, _assert_registry_module_safe, verify_host_artifact_dir
from viewspec.intent_tools import (
    STARTER_INTENT_KINDS,
    compile_intent_bundle_file_tool,
    prepare_intent_source,
    starter_intent_bundle,
)
from viewspec.local_tools import check_artifact_dir, file_hash
from viewspec.local_tools_constants import REACT_TSX_FORBIDDEN_SURFACES
from viewspec.local_tools_validators import _semantic_tsx_source_nodes, _strip_tsx_literals_and_comments
from viewspec.types import CompilerResult, CompositionIR, IRNode, Provenance

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_REGISTRY = ROOT / "tests" / "fixtures" / "react_registry" / "registry.json"
FIXTURE_MODULE = ROOT / "tests" / "fixtures" / "react_registry" / "viewspec-registry.tsx"
EXAMPLE_REGISTRY = ROOT / "examples" / "react_registry" / "registry.json"
EXAMPLE_MODULE = ROOT / "examples" / "react_registry" / "viewspec-registry.tsx"
ADMIN_INTENT = ROOT / "tests" / "fixtures" / "tailwind_admin_workspace.intent.json"
REACT_TARGETS = ("react-tsx", "react-tailwind-tsx")
REGISTRY_CODES = sorted(code for code in ERROR_CODES if code.startswith("REGISTRY_"))


def _ir_kinds(node: IRNode) -> set[str]:
    kinds = {node.primitive}
    for child in node.children:
        kinds |= _ir_kinds(child)
    return kinds


def _coverage_ast() -> ASTBundle:
    """A hand-built CompositionIR that uses every primitive kind, including the ones no intent reaches."""
    root = IRNode(
        id="root",
        primitive="root",
        provenance=Provenance(intent_refs=["viewspec:view:coverage"]),
        children=[
            IRNode(
                id="metrics",
                primitive="grid",
                props={"columns": 2},
                children=[
                    IRNode(
                        id="card",
                        primitive="surface",
                        children=[
                            IRNode(id="label", primitive="label", props={"text": "Open"}),
                            IRNode(id="value", primitive="value", props={"text": "18"}),
                            IRNode(id="badge", primitive="badge", props={"text": "Ready"}),
                        ],
                    )
                ],
            ),
            IRNode(
                id="rows",
                primitive="stack",
                props={"motif_kind": "table"},
                children=[
                    IRNode(
                        id="row",
                        primitive="cluster",
                        props={"motif_kind": "table"},
                        children=[
                            IRNode(id="cell", primitive="text", props={"text": "Body", "table_cell_role": "cell"}),
                        ],
                    )
                ],
            ),
            IRNode(id="divider", primitive="rule"),
            IRNode(
                id="field", primitive="input", props={"value": "", "binding_id": "field_value", "aria_label": "Field"}
            ),
            IRNode(id="image", primitive="image_slot", props={"alt": "Preview"}),
            IRNode(id="chart", primitive="svg", props={"label": "Trend"}),
            IRNode(
                id="send",
                primitive="button",
                props={
                    "text": "Send",
                    "action_id": "send",
                    "action_kind": "submit",
                    "target_ref": "view:coverage",
                    "payload_bindings": ["field_value"],
                },
            ),
            IRNode(id="boundary", primitive="error_boundary", props={"diagnostic_code": "TEST", "message": "Broken"}),
        ],
    )
    return ASTBundle(
        result=CompilerResult(root=CompositionIR(root=root), diagnostics=[]), style_values={}, title="Coverage"
    )


def _registry_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "module": "./viewspec-registry",
        "primitives": dict(REACT_REGISTRY_DEFAULT_EXPORTS),
    }
    payload.update(overrides)
    return payload


def _compile(tmp_path: Path, name: str, *, target: str, registry: Path | None = FIXTURE_REGISTRY) -> Path:
    out_dir = tmp_path / name
    argv = ["compile", str(ADMIN_INTENT), "--target", target, "--out", str(out_dir)]
    if registry is not None:
        argv.extend(["--registry", str(registry)])
    assert cli_main(argv) == 0
    return out_dir


def _manifest(out_dir: Path) -> dict:
    return json.loads((out_dir / "provenance_manifest.json").read_text(encoding="utf-8"))


def _write_manifest(out_dir: Path, manifest: dict) -> None:
    (out_dir / "provenance_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _registry_errors(out_dir: Path) -> list[str]:
    return [error for error in check_artifact_dir(out_dir)["errors"] if error.startswith("REGISTRY_MANIFEST_MISMATCH")]


# The primitive contract ----------------------------------------------------------------------


def test_primitive_kinds_are_the_closed_sorted_emitter_set():
    assert list(REACT_PRIMITIVE_KINDS) == sorted(REACT_PRIMITIVE_KINDS)
    assert set(REACT_PRIMITIVE_KINDS) == SUPPORTED_PRIMITIVES
    assert set(REACT_PRIMITIVE_CONTRACT) == set(REACT_PRIMITIVE_KINDS)
    assert set(REACT_REGISTRY_DEFAULT_EXPORTS) == set(REACT_PRIMITIVE_KINDS)
    assert len(set(REACT_REGISTRY_DEFAULT_EXPORTS.values())) == len(REACT_PRIMITIVE_KINDS)
    for contract in REACT_PRIMITIVE_CONTRACT.values():
        assert set(contract.elements) <= set(REACT_PRIMITIVE_ELEMENT_TAGS)
    assert REGISTRY_CODES == [
        "REGISTRY_EXPORT_INVALID",
        "REGISTRY_INVALID",
        "REGISTRY_MANIFEST_MISMATCH",
        "REGISTRY_MODULE_INVALID",
        "REGISTRY_PRIMITIVE_MISSING",
        "REGISTRY_PRIMITIVE_UNKNOWN",
        "REGISTRY_TARGET_UNSUPPORTED",
    ]


def test_every_primitive_kind_is_emitted_by_the_coverage_corpus(tmp_path):
    corpus = load_conformance_corpus(str(ROOT / "conformance/verification/corpus.json"))
    covered: dict[str, set[str]] = {}
    for case in corpus.cases:
        text = case.source_path.read_text(encoding="utf-8")
        if case.kind != "intent":
            payload = json.loads(text)
            screen = next(item for item in payload["screens"] if item["id"] == case.screen_id)
            text = json.dumps(screen["intent_bundle"])
        # The compiler's own source pipeline, so a compact case normalizes to its canonical bundle.
        prepared = prepare_intent_source(text)
        assert prepared.validation["ok"], (case.id, prepared.validation["issues"])
        bundle = prepared.result.bundle
        assert bundle is not None, case.id
        for kind in _ir_kinds(compile(bundle).result.root.root):
            covered.setdefault(kind, set()).add(case.id)
    for starter in STARTER_INTENT_KINDS:
        for kind in _ir_kinds(compile(starter_intent_bundle(starter)).result.root.root):
            covered.setdefault(kind, set()).add(f"starter:{starter}")
    coverage = _coverage_ast()
    for kind in _ir_kinds(coverage.result.root.root):
        covered.setdefault(kind, set()).add("fixture:coverage")

    assert set(covered) == set(REACT_PRIMITIVE_KINDS), sorted(set(REACT_PRIMITIVE_KINDS) ^ set(covered))
    # Intent reaches ten kinds through the corpus and the starters; the hand-built IR covers the rest.
    assert {"button", "cluster", "grid", "input", "label", "root", "stack", "surface", "text", "value"} <= {
        kind for kind, sources in covered.items() if any(not source.startswith("fixture:") for source in sources)
    }

    for emitter_cls in (ReactTsxEmitter, ReactTailwindTsxEmitter):
        out_dir = tmp_path / emitter_cls.__name__
        emitter_cls(component_registry=DEFAULT_REACT_COMPONENT_REGISTRY).emit(coverage, out_dir)
        tsx = (out_dir / "ViewSpecView.tsx").read_text(encoding="utf-8")
        for kind in REACT_PRIMITIVE_KINDS:
            export = REACT_REGISTRY_DEFAULT_EXPORTS[kind]
            assert re.search(rf"<{export} as=\"[a-z0-9]+\" id=", tsx), (emitter_cls.__name__, kind)
            assert f'data-ir-primitive={{"{kind}"}}' in tsx
        as_values = set(re.findall(r' as="([a-z0-9]+)"', tsx))
        assert as_values <= set(REACT_PRIMITIVE_ELEMENT_TAGS)
        assert "<tbody>" not in tsx
        assert (
            'import { Badge, Button, Cluster, ErrorBoundary, Grid, ImageSlot, Input, Label, Root, Rule, Stack, Surface, Svg, Text, Value } from "./viewspec-registry";'
            in tsx
        )
        assert len(re.findall(r"^import ", tsx, flags=re.MULTILINE)) == 2


def test_every_html_tag_the_compiler_names_is_in_the_closed_element_vocabulary(tmp_path):
    seen: set[str] = set()
    for starter in STARTER_INTENT_KINDS:
        out_dir = tmp_path / starter
        ReactTsxEmitter(component_registry=DEFAULT_REACT_COMPONENT_REGISTRY).emit(
            compile(starter_intent_bundle(starter)), out_dir
        )
        seen |= set(re.findall(r' as="([a-z0-9]+)"', (out_dir / "ViewSpecView.tsx").read_text(encoding="utf-8")))
    assert seen <= set(REACT_PRIMITIVE_ELEMENT_TAGS)
    assert {
        "main",
        "table",
        "tr",
        "th",
        "td",
        "dl",
        "dt",
        "dd",
        "ul",
        "li",
        "h1",
        "h2",
        "p",
        "section",
        "header",
    } <= seen


# The registry file ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda payload: [], "REGISTRY_INVALID"),
        (lambda payload: {**payload, "schema_version": 2}, "REGISTRY_INVALID"),
        (lambda payload: {**payload, "schema_version": True}, "REGISTRY_INVALID"),
        (lambda payload: {**payload, "extra": 1}, "REGISTRY_INVALID"),
        (lambda payload: {key: value for key, value in payload.items() if key != "primitives"}, "REGISTRY_INVALID"),
        (lambda payload: {**payload, "primitives": ["Badge"]}, "REGISTRY_INVALID"),
        (lambda payload: {key: value for key, value in payload.items() if key != "module"}, "REGISTRY_MODULE_INVALID"),
        (lambda payload: {**payload, "module": "viewspec-registry"}, "REGISTRY_MODULE_INVALID"),
        (lambda payload: {**payload, "module": "../shared/registry"}, "REGISTRY_MODULE_INVALID"),
        (lambda payload: {**payload, "module": "./ui/../../registry"}, "REGISTRY_MODULE_INVALID"),
        (lambda payload: {**payload, "module": "https://example.com/registry"}, "REGISTRY_MODULE_INVALID"),
        (lambda payload: {**payload, "module": "./viewspec-registry.tsx"}, "REGISTRY_MODULE_INVALID"),
        (lambda payload: {**payload, "module": "./dir/"}, "REGISTRY_MODULE_INVALID"),
        (lambda payload: {**payload, "module": "./a b"}, "REGISTRY_MODULE_INVALID"),
        (lambda payload: {**payload, "module": ".\\registry"}, "REGISTRY_MODULE_INVALID"),
        (
            lambda payload: {**payload, "primitives": {k: v for k, v in payload["primitives"].items() if k != "badge"}},
            "REGISTRY_PRIMITIVE_MISSING",
        ),
        (
            lambda payload: {**payload, "primitives": {**payload["primitives"], "table": "Table"}},
            "REGISTRY_PRIMITIVE_UNKNOWN",
        ),
        (
            lambda payload: {**payload, "primitives": {**payload["primitives"], "badge": "badge"}},
            "REGISTRY_EXPORT_INVALID",
        ),
        (
            lambda payload: {**payload, "primitives": {**payload["primitives"], "badge": "Badge-Chip"}},
            "REGISTRY_EXPORT_INVALID",
        ),
        (lambda payload: {**payload, "primitives": {**payload["primitives"], "badge": 7}}, "REGISTRY_EXPORT_INVALID"),
    ],
)
def test_registry_file_fails_closed_with_the_registered_code(mutation, code):
    with pytest.raises(ReactRegistryError) as raised:
        parse_react_component_registry(mutation(_registry_payload()))
    assert raised.value.code == code
    assert code in ERROR_CODES
    assert str(raised.value).startswith(f"{code}: ")
    assert raised.value.fix


def test_registry_accepts_shared_exports_and_nested_modules():
    registry = parse_react_component_registry(
        _registry_payload(
            module="./ui/./viewspec",
            primitives={**REACT_REGISTRY_DEFAULT_EXPORTS, "label": "Text", "value": "Text"},
        )
    )
    assert registry.module == "./ui/./viewspec"
    assert registry.module_file_name is None
    assert registry.export_names == tuple(
        sorted({"Text", *REACT_REGISTRY_DEFAULT_EXPORTS.values()} - {"Label", "Value"})
    )
    assert registry.import_line(["label", "value", "badge"]) == 'import { Badge, Text } from "./ui/./viewspec";'


def test_registry_file_loader_rejects_duplicate_keys_bad_json_and_missing_files(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(
        '{"schema_version": 1, "module": "./r", "primitives": {"badge": "A", "badge": "B"}}', encoding="utf-8"
    )
    with pytest.raises(ReactRegistryError) as duplicate:
        load_react_component_registry(path)
    assert duplicate.value.code == "REGISTRY_INVALID"
    assert "more than once" in duplicate.value.message

    path.write_text("{", encoding="utf-8")
    with pytest.raises(ReactRegistryError) as invalid:
        load_react_component_registry(path)
    assert invalid.value.code == "REGISTRY_INVALID"

    with pytest.raises(ReactRegistryError) as missing:
        load_react_component_registry(tmp_path / "absent.json")
    assert missing.value.code == "REGISTRY_INVALID"

    loaded = load_react_component_registry(FIXTURE_REGISTRY)
    assert loaded == DEFAULT_REACT_COMPONENT_REGISTRY


def test_registry_sha256_is_the_canonical_json_digest():
    registry = DEFAULT_REACT_COMPONENT_REGISTRY
    canonical = json.dumps(
        {"module": registry.module, "primitives": dict(registry.primitives), "schema_version": 1},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    assert registry.sha256 == hashlib.sha256(canonical).hexdigest()
    reordered = parse_react_component_registry(
        {
            "primitives": dict(reversed(list(registry.primitives.items()))),
            "module": registry.module,
            "schema_version": 1,
        }
    )
    assert reordered.sha256 == registry.sha256
    assert registry.to_manifest()["primitive_count"] == 15
    assert ReactComponentRegistry(module="./other", primitives=dict(registry.primitives)).sha256 != registry.sha256


# Emission, determinism, and the default output ------------------------------------------------


@pytest.mark.parametrize("target", REACT_TARGETS)
def test_same_registry_gives_the_same_bytes_across_two_compiles(tmp_path, target):
    first = _compile(tmp_path, "first", target=target)
    second = _compile(tmp_path, "second", target=target)
    for name in ("ViewSpecView.tsx", "provenance_manifest.json", "diagnostics.json"):
        assert file_hash(first / name) == file_hash(second / name), name
    manifest = _manifest(first)
    assert manifest["registry"]["sha256"] == DEFAULT_REACT_COMPONENT_REGISTRY.sha256
    assert manifest["registry"]["module"] == "./viewspec-registry"
    assert manifest["registry"]["primitive_count"] == 15
    assert manifest["registry"]["primitives"] == dict(REACT_REGISTRY_DEFAULT_EXPORTS)
    assert manifest["command_args"][-4:] == ["--registry", "registry.json", "--out", "<out>"]
    assert manifest["artifact_hash"] == file_hash(first / "ViewSpecView.tsx")
    assert cli_main(["check", str(first)]) == 0


@pytest.mark.parametrize("target", REACT_TARGETS)
def test_default_output_has_no_registry_traces(tmp_path, target):
    plain = _compile(tmp_path, "plain", target=target, registry=None)
    tsx = (plain / "ViewSpecView.tsx").read_text(encoding="utf-8")
    manifest = _manifest(plain)
    assert "registry" not in manifest
    assert "--registry" not in manifest["command_args"]
    assert len(re.findall(r"^import ", tsx, flags=re.MULTILINE)) == 1
    assert ' as="' not in tsx
    if target == "react-tsx":
        assert "data-ir-primitive" not in tsx


@pytest.mark.parametrize("target", REACT_TARGETS)
def test_registry_build_shares_the_semantic_digest_of_the_plain_build(tmp_path, target):
    plain = _compile(tmp_path, "plain", target=target, registry=None)
    registry = _compile(tmp_path, "registry", target=target)
    plain_manifest = _manifest(plain)
    registry_manifest = _manifest(registry)
    assert registry_manifest["semantic_digest"]["digest"] == plain_manifest["semantic_digest"]["digest"]
    assert (
        registry_manifest["semantic_digest"]["source_projection"]
        == plain_manifest["semantic_digest"]["source_projection"]
    )
    primitives = {dom_id: entry["primitive"] for dom_id, entry in plain_manifest["nodes"].items()}
    plain_nodes = _semantic_tsx_source_nodes(
        (plain / "ViewSpecView.tsx").read_text(encoding="utf-8"), primitive_by_dom_id=primitives
    )
    registry_nodes = _semantic_tsx_source_nodes(
        (registry / "ViewSpecView.tsx").read_text(encoding="utf-8"), primitive_by_dom_id=primitives
    )
    assert registry_nodes == plain_nodes
    assert {node["tag"] for node in registry_nodes} >= {"main", "table", "tr", "th", "td"}


def test_registry_build_keeps_the_action_and_input_contract(tmp_path):
    out_dir = _compile(tmp_path, "tailwind", target="react-tailwind-tsx")
    tsx = (out_dir / "ViewSpecView.tsx").read_text(encoding="utf-8")
    assert re.search(
        r'<Button as="button" id=\{"dom-[^"]+"\} [^\n]*type="button" data-action-id=\{"apply_filters"\}', tsx
    )
    assert re.search(r'<Input as="input" id=\{"dom-[^"]+"\} [^\n]*type="text" name=\{"search_value"\}', tsx)
    assert re.search(r'<Stack as="table" [^\n]*className="[^"]+">\n\s+<Cluster as="tr"', tsx)
    assert "<tbody>" not in tsx
    for pattern, message in REACT_TSX_FORBIDDEN_SURFACES:
        assert not pattern.search(_strip_tsx_literals_and_comments(tsx)), message


# viewspec check ------------------------------------------------------------------------------


@pytest.mark.parametrize("target", REACT_TARGETS)
def test_check_fails_with_registry_manifest_mismatch_after_the_section_is_edited(tmp_path, target):
    out_dir = _compile(tmp_path, "artifact", target=target)
    assert _registry_errors(out_dir) == []
    pristine = _manifest(out_dir)

    edited = json.loads(json.dumps(pristine))
    edited["registry"]["module"] = "./other-registry"
    _write_manifest(out_dir, edited)
    errors = _registry_errors(out_dir)
    assert errors and all(error.startswith("REGISTRY_MANIFEST_MISMATCH: ") for error in errors)
    assert any("sha256" in error for error in errors)

    # A consistent registry that maps a used kind to another export: the source disagrees twice.
    edited = json.loads(json.dumps(pristine))
    edited["registry"]["primitives"]["label"] = "Chip"
    edited["registry"]["sha256"] = ReactComponentRegistry(
        module=edited["registry"]["module"], primitives=edited["registry"]["primitives"]
    ).sha256
    _write_manifest(out_dir, edited)
    errors = _registry_errors(out_dir)
    assert any("renders <Label> but the registry maps label to Chip" in error for error in errors)
    assert any("import does not match" in error for error in errors)

    edited = json.loads(json.dumps(pristine))
    edited["registry"]["primitives"]["surface"] = "Root"
    edited["registry"]["sha256"] = ReactComponentRegistry(
        module=edited["registry"]["module"], primitives=edited["registry"]["primitives"]
    ).sha256
    _write_manifest(out_dir, edited)
    assert any("renders <Surface>" in error for error in _registry_errors(out_dir))

    edited = json.loads(json.dumps(pristine))
    edited["registry"]["primitive_count"] = 14
    _write_manifest(out_dir, edited)
    assert any("primitive_count" in error for error in _registry_errors(out_dir))

    edited = json.loads(json.dumps(pristine))
    del edited["registry"]
    _write_manifest(out_dir, edited)
    assert any("does not declare" in error for error in _registry_errors(out_dir))

    _write_manifest(out_dir, pristine)
    assert cli_main(["check", str(out_dir)]) == 0


def test_check_json_reports_the_registry_code_and_summary(tmp_path, capsys):
    out_dir = _compile(tmp_path, "artifact", target="react-tailwind-tsx")
    assert cli_main(["check", str(out_dir)]) == 0
    assert "registry: module=./viewspec-registry primitives=15 sha256=" in capsys.readouterr().out

    manifest = _manifest(out_dir)
    manifest["registry"]["sha256"] = "0" * 64
    _write_manifest(out_dir, manifest)
    assert cli_main(["check", str(out_dir), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any(error.startswith("REGISTRY_MANIFEST_MISMATCH: ") for error in payload["errors"])
    assert payload["manifest_summary"]["registry"]["module"] == "./viewspec-registry"


def test_check_rejects_a_registry_on_an_html_artifact_and_a_registry_source_without_a_section(tmp_path):
    html_dir = tmp_path / "html"
    assert cli_main(["compile", str(ADMIN_INTENT), "--out", str(html_dir)]) == 0
    manifest = _manifest(html_dir)
    manifest["registry"] = DEFAULT_REACT_COMPONENT_REGISTRY.to_manifest()
    _write_manifest(html_dir, manifest)
    assert any(error.startswith("REGISTRY_MANIFEST_MISMATCH") for error in check_artifact_dir(html_dir)["errors"])

    plain_tsx = 'import * as React from "react";\nimport { Root } from "./x";\n  return (\n    <Root as="main" id={"dom-r"} data-ir-id={"r"} data-ir-primitive={"root"}>\n    </Root>\n  );\n'
    undeclared = validate_react_registry_artifact(plain_tsx, {})
    assert undeclared[0] == (
        "REGISTRY_MANIFEST_MISMATCH: ViewSpecView.tsx imports a component registry the manifest does not declare"
    )
    assert "renders a registry component but the manifest declares no registry" in undeclared[1]
    assert (
        validate_react_registry_artifact(
            'import * as React from "react";\n  return (\n    <main id={"dom-r"} data-ir-id={"r"}>\n    </main>\n  );\n',
            {},
        )
        == []
    )
    assert validate_react_registry_artifact(plain_tsx, {"registry": {"sha256": "x"}}) == [
        "REGISTRY_MANIFEST_MISMATCH: manifest registry must have exactly the keys module, primitive_count, primitives, sha256"
    ]


def test_registry_manifest_section_validates_against_the_manifest_schema(tmp_path):
    schema = json.loads((ROOT / "docs" / "manifest-v1.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    out_dir = _compile(tmp_path, "artifact", target="react-tailwind-tsx")
    manifest = _manifest(out_dir)
    assert "registry" in schema["properties"]
    assert validator.is_valid(manifest), list(validator.iter_errors(manifest))[:3]
    manifest["registry"]["primitives"]["badge"] = "badge"
    assert not validator.is_valid(manifest)


# The CLI and the compile tool -----------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["compile", str(ADMIN_INTENT), "--out", "{out}", "--registry", str(FIXTURE_REGISTRY)],
        ["compile", str(ADMIN_INTENT), "--target", "mcp-app", "--out", "{out}", "--registry", str(FIXTURE_REGISTRY)],
        [
            "compile",
            str(ROOT / "demos" / "agent-app-example.internal-tool.json"),
            "--out",
            "{out}",
            "--registry",
            str(FIXTURE_REGISTRY),
        ],
        [
            "compile",
            str(ROOT / "examples" / "raw_html_report.html"),
            "--out",
            "{out}",
            "--registry",
            str(FIXTURE_REGISTRY),
        ],
    ],
)
def test_cli_rejects_registry_on_other_targets(tmp_path, capsys, argv):
    argv = [item.replace("{out}", str(tmp_path / "out")) for item in argv]
    assert cli_main([*argv, "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["errors"][0]["code"] == "REGISTRY_TARGET_UNSUPPORTED"
    assert not (tmp_path / "out").exists()
    assert cli_main(argv) == 2
    assert capsys.readouterr().err.startswith("error: REGISTRY_TARGET_UNSUPPORTED: ")


def test_cli_reports_a_bad_registry_file_before_writing(tmp_path, capsys):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps(_registry_payload(primitives={"badge": "Badge"})), encoding="utf-8")
    out_dir = tmp_path / "out"
    assert (
        cli_main(
            [
                "compile",
                str(ADMIN_INTENT),
                "--target",
                "react-tsx",
                "--registry",
                str(registry),
                "--out",
                str(out_dir),
                "--json",
            ]
        )
        == 2
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["errors"][0]["code"] == "REGISTRY_PRIMITIVE_MISSING"
    assert "button" in payload["errors"][0]["message"]
    assert not out_dir.exists()


def test_compile_json_reports_the_registry_in_metadata(tmp_path, capsys):
    out_dir = tmp_path / "out"
    exit_code = cli_main(
        [
            "compile",
            str(ADMIN_INTENT),
            "--target",
            "react-tsx",
            "--registry",
            str(FIXTURE_REGISTRY),
            "--out",
            str(out_dir),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert set(payload["paths"]) == {"tsx", "manifest", "diagnostics"}
    assert payload["metadata"]["registry"] == DEFAULT_REACT_COMPONENT_REGISTRY.to_summary()
    assert any("./viewspec-registry" in action for action in payload["next_actions"])


def test_compile_tool_takes_a_registry_and_rejects_other_targets(tmp_path):
    result = compile_intent_bundle_file_tool(
        ADMIN_INTENT,
        tmp_path / "out",
        target="react-tailwind-tsx",
        registry=FIXTURE_REGISTRY,
        cwd=ROOT,
        allow_outside_cwd=True,
    )
    assert result["ok"] is True, result
    assert result["metadata"]["registry"]["primitive_count"] == 15
    assert result["metadata"]["manifest_summary"]["registry"]["module"] == "./viewspec-registry"
    assert _manifest(tmp_path / "out")["command_args"][-4:] == ["--registry", "registry.json", "--out", "<out>"]

    loaded = compile_intent_bundle_file_tool(
        ADMIN_INTENT,
        tmp_path / "loaded",
        target="react-tsx",
        registry=DEFAULT_REACT_COMPONENT_REGISTRY,
        cwd=ROOT,
        allow_outside_cwd=True,
    )
    assert loaded["ok"] is True
    assert "--registry" not in _manifest(tmp_path / "loaded")["command_args"]

    rejected = compile_intent_bundle_file_tool(
        ADMIN_INTENT,
        tmp_path / "html",
        target="html-tailwind",
        registry=FIXTURE_REGISTRY,
        cwd=ROOT,
        allow_outside_cwd=True,
    )
    assert rejected["ok"] is False
    assert rejected["errors"][0]["code"] == "REGISTRY_TARGET_UNSUPPORTED"
    assert not (tmp_path / "html").exists()


def test_init_registry_writes_the_default_registry_and_module(tmp_path, capsys):
    out = tmp_path / "host" / "viewspec-registry.json"
    assert cli_main(["init", "registry", "--out", str(out), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    module = tmp_path / "host" / "viewspec-registry.tsx"
    assert payload["ok"] is True
    assert payload["paths"] == {"registry": str(out), "registry_module": str(module)}
    assert load_react_component_registry(out) == DEFAULT_REACT_COMPONENT_REGISTRY
    assert module.read_text(encoding="utf-8") == default_react_registry_module()

    assert cli_main(["init", "registry", "--out", str(out)]) == 2
    assert "pass --force" in capsys.readouterr().err
    assert cli_main(["init", "registry", "--out", str(out), "--force"]) == 0

    compiled = tmp_path / "host"
    assert (
        cli_main(
            [
                "compile",
                str(ADMIN_INTENT),
                "--target",
                "react-tailwind-tsx",
                "--registry",
                str(out),
                "--out",
                str(compiled),
            ]
        )
        == 0
    )
    assert cli_main(["check", str(compiled)]) == 0


# Composition with the source pipeline -------------------------------------------------------
#
# The registry is an emission option. The source pipeline (patch, compact normalization, lossless
# fixes, validation) runs first and unchanged; the registry renders the canonical bundle it produces.


def _default_registry_file(tmp_path: Path) -> Path:
    registry = tmp_path / "host" / "viewspec-registry.json"
    assert cli_main(["init", "registry", "--out", str(registry)]) == 0
    return registry


@pytest.mark.parametrize("target", REACT_TARGETS)
def test_compact_source_compiles_through_the_default_registry(tmp_path, capsys, target):
    compact = tmp_path / "viewspec.compact.json"
    assert cli_main(["init", "compact", "--out", str(compact)]) == 0
    registry = _default_registry_file(tmp_path)
    twin = tmp_path / "twin.intent.json"
    out_dir = tmp_path / "compact-out"
    capsys.readouterr()

    exit_code = cli_main(
        [
            "compile",
            str(compact),
            "--target",
            target,
            "--registry",
            str(registry),
            "--canonical-out",
            str(twin),
            "--out",
            str(out_dir),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0, payload
    assert payload["metadata"]["source_format"] == "compact"
    assert payload["metadata"]["registry"] == DEFAULT_REACT_COMPONENT_REGISTRY.to_summary()
    assert payload["paths"]["canonical_out"] == str(twin)
    manifest = _manifest(out_dir)
    assert manifest["source_format"] == "compact"
    assert manifest["compact_source_sha256"] == manifest["raw_source_hash"]
    assert manifest["source_hash"] == hashlib.sha256(twin.read_bytes()).hexdigest()
    assert manifest["registry"] == DEFAULT_REACT_COMPONENT_REGISTRY.to_manifest()
    assert manifest["command_args"][-4:] == ["--registry", "viewspec-registry.json", "--out", "<out>"]
    tsx = (out_dir / "ViewSpecView.tsx").read_text(encoding="utf-8")
    assert re.search(r'^import \{ [A-Za-z, ]+ \} from "\./viewspec-registry";$', tsx, flags=re.MULTILINE)
    assert cli_main(["check", str(out_dir)]) == 0
    assert "registry: module=./viewspec-registry primitives=15" in capsys.readouterr().out

    # The registry renders the normalized canonical bundle, so the canonical twin compiled through the
    # same registry gives the same component and the same semantic digest.
    twin_dir = tmp_path / "twin-out"
    assert (
        cli_main(["compile", str(twin), "--target", target, "--registry", str(registry), "--out", str(twin_dir)]) == 0
    )
    twin_manifest = _manifest(twin_dir)
    assert file_hash(twin_dir / "ViewSpecView.tsx") == file_hash(out_dir / "ViewSpecView.tsx")
    assert twin_manifest["semantic_digest"]["digest"] == manifest["semantic_digest"]["digest"]
    assert "source_format" not in twin_manifest
    assert twin_manifest["registry"] == manifest["registry"]


@pytest.mark.parametrize("target", REACT_TARGETS)
def test_patched_canonical_source_compiles_through_the_registry(tmp_path, capsys, target):
    source = tmp_path / "viewspec.intent.json"
    assert cli_main(["init", "--out", str(source)]) == 0
    registry = _default_registry_file(tmp_path)
    ops = tmp_path / "ops.json"
    ops.write_text(
        json.dumps(
            [
                {"op": "test", "path": "/substrate/nodes/revenue/attrs/value", "value": "$12.4K"},
                {"op": "replace", "path": "/substrate/nodes/revenue/attrs/value", "value": "$13.1K"},
            ]
        ),
        encoding="utf-8",
    )
    patched = tmp_path / "patched.intent.json"
    out_dir = tmp_path / "cli-out"
    capsys.readouterr()

    exit_code = cli_main(
        [
            "compile",
            str(source),
            "--target",
            target,
            "--patch",
            str(ops),
            "--patched-out",
            str(patched),
            "--registry",
            str(registry),
            "--out",
            str(out_dir),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0, payload
    assert payload["paths"]["patched_out"] == str(patched)
    assert payload["metadata"]["registry"]["sha256"] == DEFAULT_REACT_COMPONENT_REGISTRY.sha256
    manifest = _manifest(out_dir)
    assert manifest["command_args"][-6:] == [
        "--patch",
        "ops.json",
        "--registry",
        "viewspec-registry.json",
        "--out",
        "<out>",
    ]
    # The patch applied before validation and emission: the manifest hashes the patched source, and
    # the registry-rendered component carries the patched value, not the original one.
    assert manifest["raw_source_hash"] == hashlib.sha256(patched.read_bytes()).hexdigest()
    assert "source_format" not in manifest
    tsx = (out_dir / "ViewSpecView.tsx").read_text(encoding="utf-8")
    assert "$13.1K" in tsx
    assert "$12.4K" not in tsx
    assert "<Value as=" in tsx
    assert source.read_text(encoding="utf-8").count("$12.4K") == 1  # the source file is unchanged
    assert cli_main(["check", str(out_dir)]) == 0

    # The compile tool threads the same registry through the same pipeline: same bytes, same args.
    tool_dir = tmp_path / "tool-out"
    result = compile_intent_bundle_file_tool(
        source, tool_dir, target=target, patch_path=ops, registry=registry, cwd=tmp_path
    )
    assert result["ok"] is True, result
    assert file_hash(tool_dir / "ViewSpecView.tsx") == file_hash(out_dir / "ViewSpecView.tsx")
    assert _manifest(tool_dir)["command_args"] == manifest["command_args"]


# Module shapes -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("registry_path", "module_path"),
    [
        (FIXTURE_REGISTRY, FIXTURE_MODULE),
        (EXAMPLE_REGISTRY, EXAMPLE_MODULE),
        (None, None),
    ],
)
def test_registry_modules_export_every_mapped_name_and_import_only_react(tmp_path, registry_path, module_path):
    registry = load_react_component_registry(registry_path) if registry_path else DEFAULT_REACT_COMPONENT_REGISTRY
    source = module_path.read_text(encoding="utf-8") if module_path else default_react_registry_module()
    for export in registry.export_names:
        assert re.search(rf"^export (?:function {export}\(|const {export} =)", source, flags=re.MULTILINE), export
    imports = re.findall(r"^import\s+.+;$", source, flags=re.MULTILINE)
    assert imports == ['import * as React from "react";']
    code = _strip_tsx_literals_and_comments(source)
    for pattern, message in REACT_TSX_FORBIDDEN_SURFACES:
        assert not pattern.search(code), message
    assert "tbody" in source
    assert "...rest" in source
    # Every shipped module passes the reference host's module scan.
    mounted = tmp_path / "viewspec-registry.tsx"
    mounted.write_text(source, encoding="utf-8")
    _assert_registry_module_safe(mounted)


# The bounded host proof ------------------------------------------------------------------------


def _registry_artifact(tmp_path: Path) -> Path:
    out_dir = _compile(tmp_path, "artifact", target="react-tailwind-tsx")
    (out_dir / "viewspec-registry.tsx").write_text(FIXTURE_MODULE.read_text(encoding="utf-8"), encoding="utf-8")
    return out_dir


def _fake_registry_runtime(primitive_count: int):
    def runtime(host_dir, *, install, started, timings):
        generated = host_dir / "src" / "generated"
        assert sorted(path.name for path in generated.iterdir()) == [
            "ViewSpecView.tsx",
            "diagnostics.json",
            "provenance_manifest.json",
            "viewspec-registry.tsx",
        ]
        assert (generated / "viewspec-registry.tsx").read_text(encoding="utf-8") == FIXTURE_MODULE.read_text(
            encoding="utf-8"
        )
        timings["build"] = 1
        return {
            "assertions": {
                "action_count": 1,
                "aesthetic_layout_assertion_count": 2,
                "aesthetic_profile_assertion_count": 1,
                "dom_count": 49,
                "grid_column_assertion_count": 2,
                "grid_span_assertion_count": 0,
                "payload_binding_count": 2,
                "registry_primitive_assertion_count": primitive_count,
                "style_assertion_count": 9,
            },
            "node_version": "v22.0.0",
            "npm_version": "10.0.0",
        }

    return runtime


def test_host_verify_mounts_the_registry_module_and_requires_primitive_assertions(tmp_path, monkeypatch):
    out_dir = _registry_artifact(tmp_path)
    node_count = len(_manifest(out_dir)["nodes"])
    monkeypatch.setattr("viewspec.host_verify._run_host_browser_phases", _fake_registry_runtime(node_count))

    report = verify_host_artifact_dir(out_dir)

    assert report["ok"] is True, report["errors"]
    assert report["assertion_requirements"]["registry_primitive_assertion_count"] == node_count
    assert report["assertions"]["registry_primitive_assertion_count"] == node_count
    assert report["registry"] == {
        **DEFAULT_REACT_COMPONENT_REGISTRY.to_summary(),
        "module_file": "viewspec-registry.tsx",
        "module_hash": file_hash(FIXTURE_MODULE),
    }

    monkeypatch.setattr("viewspec.host_verify._run_host_browser_phases", _fake_registry_runtime(node_count - 1))
    weak = verify_host_artifact_dir(out_dir)
    assert weak["ok"] is False
    assert weak["errors"][0]["code"] == "HOST_VERIFY_DOM_NODE_MISSING"
    assert "data-ir-primitive" in weak["errors"][0]["message"]
    assert weak["assertion_requirements"]["registry_primitive_assertion_count"] == node_count
    assert weak["registry"]["module_file"] == "viewspec-registry.tsx"


def test_host_verify_fails_closed_on_a_missing_or_unsafe_registry_module(tmp_path, monkeypatch):
    out_dir = _registry_artifact(tmp_path)
    monkeypatch.setattr("viewspec.host_verify._run_host_browser_phases", _fake_registry_runtime(49))
    module = out_dir / "viewspec-registry.tsx"

    module.unlink()
    missing = verify_host_artifact_dir(out_dir)
    assert missing["ok"] is False
    assert missing["errors"][0]["code"] == "REGISTRY_MODULE_INVALID"
    assert "viewspec-registry.tsx" in missing["errors"][0]["message"]

    module.write_text(
        'import * as React from "react";\nimport { Card } from "@/components/ui/card";\n', encoding="utf-8"
    )
    foreign = verify_host_artifact_dir(out_dir)
    assert foreign["errors"][0]["code"] == "REGISTRY_MODULE_INVALID"
    assert "imports more than react" in foreign["errors"][0]["message"]

    module.write_text(
        'import * as React from "react";\nexport function Root() { fetch("/x"); return null; }\n', encoding="utf-8"
    )
    active = verify_host_artifact_dir(out_dir)
    assert active["errors"][0]["code"] == "REGISTRY_MODULE_INVALID"
    assert "fetch()" in active["errors"][0]["message"]

    module.write_text(FIXTURE_MODULE.read_text(encoding="utf-8"), encoding="utf-8")
    manifest = _manifest(out_dir)
    manifest["registry"]["module"] = "./ui/registry"
    _write_manifest(out_dir, manifest)
    deeper = verify_host_artifact_dir(out_dir)
    assert deeper["ok"] is False
    assert deeper["errors"][0]["code"] == "HOST_VERIFY_ARTIFACT_CHECK_FAILED"
    assert any("REGISTRY_MANIFEST_MISMATCH" in error["message"] for error in deeper["errors"])


@pytest.mark.parametrize(
    "source",
    [
        'import {\n  Card,\n  CardHeader,\n} from "@/components/ui/card";\n',
        'import * as React from "react";\nexport { Button } from "./button";\n',
        'export * from "lucide-react";\n',
        'import "./styles.css";\n',
        "import { clsx } from 'clsx';\n",
        'import * as React from "react";\nconst shared = require("./shared");\n',
        'import * as React from "react";\nexport const Lazy = React.lazy(() => import("./lazy"));\n',
    ],
)
def test_registry_module_scan_rejects_every_specifier_but_react(tmp_path, source):
    module = tmp_path / "viewspec-registry.tsx"
    module.write_text(source, encoding="utf-8")
    with pytest.raises(HostVerifyFailure) as raised:
        _assert_registry_module_safe(module)
    assert raised.value.code == "REGISTRY_MODULE_INVALID"


@pytest.mark.parametrize(
    "source",
    [
        'import * as React from "react";\n',
        "import type { ReactNode } from 'react';\n",
        'import {\n  useState,\n} from "react";\n',
        'import * as React from "react";\n// import { Card } from "@/components/ui/card";\n',
        'import * as React from "react";\nexport function Text() {\n  return "copied from \'shadcn\'";\n}\n',
    ],
)
def test_registry_module_scan_accepts_react_only_modules(tmp_path, source):
    module = tmp_path / "viewspec-registry.tsx"
    module.write_text(source, encoding="utf-8")
    _assert_registry_module_safe(module)


def test_host_verify_plain_artifact_report_declares_no_registry(tmp_path, monkeypatch):
    out_dir = _compile(tmp_path, "plain", target="react-tailwind-tsx", registry=None)

    def runtime(host_dir, *, install, started, timings):
        assert sorted(path.name for path in (host_dir / "src" / "generated").iterdir()) == [
            "ViewSpecView.tsx",
            "diagnostics.json",
            "provenance_manifest.json",
        ]
        return {
            "assertions": {
                "dom_count": 1,
                "style_assertion_count": 4,
                "aesthetic_profile_assertion_count": 1,
                "aesthetic_layout_assertion_count": 2,
            },
            "node_version": "v22.0.0",
            "npm_version": "10.0.0",
        }

    monkeypatch.setattr("viewspec.host_verify._run_host_browser_phases", runtime)
    report = verify_host_artifact_dir(out_dir)
    assert report["ok"] is True, report["errors"]
    assert report["registry"] is None
    assert report["assertion_requirements"]["registry_primitive_assertion_count"] == 0
