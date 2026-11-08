"""Host component registry for the React targets.

The compiler owns structure, semantics, and provenance; the host owns the components. A registry
file maps each CompositionIR primitive kind the React emitters produce to a named export of one
host module. With ``viewspec compile --registry REGISTRY.json`` the emitted ``ViewSpecView.tsx``
imports those exports and renders every primitive through them. Each component receives the same
props and ``data-*`` provenance attributes the emitter writes today, plus ``as``: the element the
default registry renders for that node. Without a registry the emitters write the same bytes as
before.

Agents never write components or registries. The registry file and the module are host-owned
compiler input, like DESIGN.md. ``viewspec init registry`` writes the default registry and the
default module, which reproduces today's elements, so a host can replace one component at a time.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from viewspec._canonical import canonical_json_utf8_bytes, canonical_sha256
from viewspec.compiler_errors import LocalToolContractError

REACT_REGISTRY_SCHEMA_VERSION = 1
REACT_REGISTRY_TARGETS: tuple[str, ...] = ("react-tsx", "react-tailwind-tsx")
REACT_REGISTRY_DEFAULT_MODULE = "./viewspec-registry"
REACT_REGISTRY_DEFAULT_MODULE_FILE = "viewspec-registry.default.tsx"
REACT_REGISTRY_DEFAULT_OUT = "viewspec-registry.json"
REACT_REGISTRY_MAX_BYTES = 64 * 1024
REACT_REGISTRY_KEYS = frozenset({"module", "primitives", "schema_version"})
REACT_REGISTRY_MANIFEST_KEYS = frozenset({"module", "primitive_count", "primitives", "sha256"})

# The closed list of primitive kinds the React emitters render. tests/test_react_registry.py pins it
# against the emitters' supported set and against the compiled coverage corpus.
REACT_PRIMITIVE_KINDS: tuple[str, ...] = (
    "badge",
    "button",
    "cluster",
    "error_boundary",
    "grid",
    "image_slot",
    "input",
    "label",
    "root",
    "rule",
    "stack",
    "surface",
    "svg",
    "text",
    "value",
)

# Every element the compiler can name in the ``as`` prop. A host component renders the element
# ``as`` names, or its own replacement with the same role.
REACT_PRIMITIVE_ELEMENT_TAGS: tuple[str, ...] = (
    "button",
    "dd",
    "div",
    "dl",
    "dt",
    "h1",
    "h2",
    "header",
    "hr",
    "input",
    "li",
    "main",
    "p",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
)

# Props every primitive receives. The ``data-*`` ones are provenance; a host component spreads them
# onto its root element. ``style`` is written by react-tsx only.
REACT_PRIMITIVE_COMMON_PROPS: tuple[str, ...] = (
    "as",
    "id",
    "data-ir-id",
    "data-ir-primitive",
    "data-content-refs",
    "data-intent-refs",
    "data-style-tokens",
    "className",
    "style",
    "data-binding-id",
    "data-resource-id",
    "data-resource-view-id",
    "data-record-id",
    "data-resource-field",
    "data-aesthetic-profile",
    "data-visibility-rule",
    "data-visibility-state",
    "hidden",
    "data-state-text-id",
    "children",
)


@dataclass(frozen=True)
class ReactPrimitiveContract:
    """What one primitive kind's host component receives."""

    kind: str
    default_export: str
    content: str
    elements: tuple[str, ...]
    props: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.content not in {"nodes", "text", "none"}:
            raise ValueError(f"primitive contract {self.kind} has an unknown content kind {self.content!r}")
        unknown = sorted(set(self.elements) - set(REACT_PRIMITIVE_ELEMENT_TAGS))
        if unknown:
            raise ValueError(f"primitive contract {self.kind} names unknown elements {unknown}")


REACT_PRIMITIVE_CONTRACT: Mapping[str, ReactPrimitiveContract] = MappingProxyType(
    {
        contract.kind: contract
        for contract in (
            ReactPrimitiveContract("badge", "Badge", "text", ("div", "dd", "td"), ("scope",)),
            ReactPrimitiveContract(
                "button",
                "Button",
                "text",
                ("button",),
                (
                    "type",
                    "data-action-id",
                    "data-action-kind",
                    "data-action-target-ref",
                    "data-payload-bindings",
                    "onClick",
                ),
            ),
            ReactPrimitiveContract("cluster", "Cluster", "nodes", ("div", "tr"), ()),
            ReactPrimitiveContract("error_boundary", "ErrorBoundary", "text", ("div",), ("role",)),
            ReactPrimitiveContract("grid", "Grid", "nodes", ("div",), ()),
            ReactPrimitiveContract("image_slot", "ImageSlot", "text", ("div",), ("role", "aria-label")),
            ReactPrimitiveContract(
                "input",
                "Input",
                "none",
                ("input",),
                ("type", "name", "value", "onChange", "aria-label", "aria-labelledby"),
            ),
            ReactPrimitiveContract("label", "Label", "text", ("div", "dt", "p", "th"), ("scope",)),
            ReactPrimitiveContract("root", "Root", "nodes", ("main", "div"), ("data-viewspec-screen-root",)),
            ReactPrimitiveContract("rule", "Rule", "none", ("hr",), ()),
            ReactPrimitiveContract(
                "stack", "Stack", "nodes", ("div", "dl", "section", "table", "ul"), ("role", "aria-label")
            ),
            ReactPrimitiveContract(
                "surface",
                "Surface",
                "nodes",
                ("div", "header", "li", "section"),
                ("role", "aria-label", "aria-busy"),
            ),
            ReactPrimitiveContract("svg", "Svg", "text", ("div",), ("role", "aria-label")),
            ReactPrimitiveContract("text", "Text", "text", ("div", "dd", "p", "td"), ("scope",)),
            ReactPrimitiveContract("value", "Value", "text", ("div", "dd", "h1", "h2", "td"), ("scope",)),
        )
    }
)

REACT_REGISTRY_DEFAULT_EXPORTS: Mapping[str, str] = MappingProxyType(
    {kind: REACT_PRIMITIVE_CONTRACT[kind].default_export for kind in REACT_PRIMITIVE_KINDS}
)

# JSX treats a tag that starts with a lowercase letter as an HTML element, so a host export must
# start with an uppercase ASCII letter. The rest is the ASCII identifier alphabet.
REACT_REGISTRY_EXPORT_RE = re.compile(r"^[A-Z][A-Za-z0-9_$]*$")
REACT_REGISTRY_MODULE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
REACT_REGISTRY_MODULE_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json")
REACT_REGISTRY_MODULE_MAX_LENGTH = 200
_REACT_IMPORT_LINE = 'import * as React from "react";'
_IMPORT_LINE_RE = re.compile(r"^import\s+.+;$", re.MULTILINE)
_REGISTRY_IMPORT_RE = re.compile(r'^import \{ (?P<names>[^}]*) \} from "(?P<module>[^"]*)";$')
_JSX_TAG_RE = re.compile(r"<([A-Za-z][A-Za-z0-9]*)\b")
_JSX_JSON_ATTR_RE = re.compile(r'([A-Za-z][-A-Za-z0-9]*)=\{("(?:\\.|[^"\\])*")\}')
_JSX_LITERAL_ATTR_RE = re.compile(r'([A-Za-z][-A-Za-z0-9]*)="([^"]*)"')


class ReactRegistryError(LocalToolContractError):
    """A registry file, a registry manifest section, or a registry build is invalid.

    ``code`` is one of the ``REGISTRY_*`` codes in :data:`viewspec.error_codes.ERROR_CODES`.
    """

    def __init__(self, code: str, message: str, fix: str | None = None) -> None:
        super().__init__(code, message)
        self.fix = fix or "Fix the registry file and rerun viewspec compile."


def _registry_sha256(module: str, primitives: Mapping[str, str]) -> str:
    payload = {
        "module": module,
        "primitives": {kind: primitives[kind] for kind in sorted(primitives)},
        "schema_version": REACT_REGISTRY_SCHEMA_VERSION,
    }
    return canonical_sha256(canonical_json_utf8_bytes(payload))


def validate_react_registry_module(module: object) -> str:
    """Return ``module`` when it is a relative specifier inside the output root; raise otherwise."""
    fix = (
        'Name the module as a relative path such as "./viewspec-registry": no scheme, no URL, no '
        "extension, and no .. segment above the output directory."
    )
    if not isinstance(module, str) or not module:
        raise ReactRegistryError("REGISTRY_MODULE_INVALID", "Registry module must be a non-empty string.", fix)
    if len(module) > REACT_REGISTRY_MODULE_MAX_LENGTH:
        raise ReactRegistryError(
            "REGISTRY_MODULE_INVALID",
            f"Registry module is longer than {REACT_REGISTRY_MODULE_MAX_LENGTH} characters.",
            fix,
        )
    if not module.startswith("./"):
        raise ReactRegistryError("REGISTRY_MODULE_INVALID", f"Registry module {module!r} must start with './'.", fix)
    if "\\" in module or ":" in module or "//" in module or any(char.isspace() for char in module):
        raise ReactRegistryError(
            "REGISTRY_MODULE_INVALID",
            f"Registry module {module!r} must be a relative path without a scheme, URL, or backslash.",
            fix,
        )
    if module.endswith("/"):
        raise ReactRegistryError("REGISTRY_MODULE_INVALID", f"Registry module {module!r} names no file.", fix)
    if module.lower().endswith(REACT_REGISTRY_MODULE_EXTENSIONS):
        raise ReactRegistryError(
            "REGISTRY_MODULE_INVALID", f"Registry module {module!r} must not carry a file extension.", fix
        )
    depth = 0
    for segment in module[2:].split("/"):
        if segment == "..":
            depth -= 1
            if depth < 0:
                raise ReactRegistryError(
                    "REGISTRY_MODULE_INVALID",
                    f"Registry module {module!r} escapes above the output directory.",
                    fix,
                )
            continue
        if segment == ".":
            continue
        if not REACT_REGISTRY_MODULE_SEGMENT_RE.match(segment):
            raise ReactRegistryError(
                "REGISTRY_MODULE_INVALID",
                f"Registry module {module!r} has a segment outside letters, digits, underscore, dot, and dash.",
                fix,
            )
        depth += 1
    if depth < 1:
        raise ReactRegistryError("REGISTRY_MODULE_INVALID", f"Registry module {module!r} names no file.", fix)
    return module


def validate_react_registry_export(kind: str, export: object) -> str:
    """Return ``export`` when it is a TypeScript identifier that JSX treats as a component."""
    if not isinstance(export, str) or not REACT_REGISTRY_EXPORT_RE.match(export):
        raise ReactRegistryError(
            "REGISTRY_EXPORT_INVALID",
            f"Registry export for {kind} must be a TypeScript identifier that starts with an uppercase letter; "
            f"got {export!r}.",
            "Name each export like a React component: letters, digits, underscore, or $, starting with A-Z.",
        )
    return export


@dataclass(frozen=True)
class ReactComponentRegistry:
    """A checked mapping from every primitive kind to a named export of one host module."""

    module: str
    primitives: Mapping[str, str]

    def __post_init__(self) -> None:
        validate_react_registry_module(self.module)
        if not isinstance(self.primitives, Mapping):
            raise ReactRegistryError("REGISTRY_INVALID", "Registry primitives must be an object.")
        unknown = sorted(str(kind) for kind in self.primitives if kind not in REACT_PRIMITIVE_KINDS)
        if unknown:
            raise ReactRegistryError(
                "REGISTRY_PRIMITIVE_UNKNOWN",
                f"Registry maps unknown primitive kind(s): {', '.join(unknown)}.",
                f"Map only these kinds: {', '.join(REACT_PRIMITIVE_KINDS)}.",
            )
        missing = [kind for kind in REACT_PRIMITIVE_KINDS if kind not in self.primitives]
        if missing:
            raise ReactRegistryError(
                "REGISTRY_PRIMITIVE_MISSING",
                f"Registry maps no export for primitive kind(s): {', '.join(missing)}.",
                "Map every primitive kind exactly once, or start from viewspec init registry.",
            )
        checked = {kind: validate_react_registry_export(kind, self.primitives[kind]) for kind in REACT_PRIMITIVE_KINDS}
        object.__setattr__(self, "primitives", MappingProxyType(checked))

    @property
    def sha256(self) -> str:
        """SHA-256 of the canonical registry JSON: ``{"module", "primitives", "schema_version"}``."""
        return _registry_sha256(self.module, self.primitives)

    @property
    def export_names(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.primitives.values())))

    @property
    def module_file_name(self) -> str | None:
        """``<name>.tsx`` when the module sits beside ``ViewSpecView.tsx``; ``None`` for a deeper path."""
        parts = [part for part in PurePosixPath(self.module[2:]).parts if part != "."]
        if len(parts) != 1 or parts[0] == "..":
            return None
        return f"{parts[0]}.tsx"

    def export_for(self, kind: str) -> str:
        if kind not in self.primitives:
            raise ReactRegistryError("REGISTRY_PRIMITIVE_UNKNOWN", f"Registry maps no export for {kind!r}.")
        return self.primitives[kind]

    def import_line(self, kinds: Iterable[str]) -> str:
        """The import statement for the exports ``kinds`` use, sorted and deduplicated."""
        names = sorted({self.export_for(kind) for kind in kinds})
        if not names:
            raise ReactRegistryError("REGISTRY_INVALID", "A registry import needs at least one primitive kind.")
        return f"import {{ {', '.join(names)} }} from {json.dumps(self.module)};"

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": REACT_REGISTRY_SCHEMA_VERSION,
            "module": self.module,
            "primitives": {kind: self.primitives[kind] for kind in REACT_PRIMITIVE_KINDS},
        }

    def to_manifest(self) -> dict[str, Any]:
        """The additive ``registry`` section of ``provenance_manifest.json``."""
        return {
            "sha256": self.sha256,
            "module": self.module,
            "primitive_count": len(self.primitives),
            "primitives": {kind: self.primitives[kind] for kind in REACT_PRIMITIVE_KINDS},
        }

    def to_summary(self) -> dict[str, Any]:
        return {"sha256": self.sha256, "module": self.module, "primitive_count": len(self.primitives)}


DEFAULT_REACT_COMPONENT_REGISTRY = ReactComponentRegistry(
    module=REACT_REGISTRY_DEFAULT_MODULE, primitives=dict(REACT_REGISTRY_DEFAULT_EXPORTS)
)


def parse_react_component_registry(payload: object, *, source: str = "registry") -> ReactComponentRegistry:
    """Build a registry from a decoded ``REGISTRY.json`` payload, failing closed on every defect."""
    if not isinstance(payload, Mapping):
        raise ReactRegistryError("REGISTRY_INVALID", f"{source} must be a JSON object.")
    unknown = sorted(str(key) for key in payload if key not in REACT_REGISTRY_KEYS)
    if unknown:
        raise ReactRegistryError(
            "REGISTRY_INVALID",
            f"{source} has unknown field(s): {', '.join(unknown)}.",
            "Use only schema_version, module, and primitives.",
        )
    schema_version = payload.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != REACT_REGISTRY_SCHEMA_VERSION:
        raise ReactRegistryError(
            "REGISTRY_INVALID",
            f"{source} schema_version must be {REACT_REGISTRY_SCHEMA_VERSION}; got {schema_version!r}.",
        )
    if "module" not in payload:
        raise ReactRegistryError("REGISTRY_MODULE_INVALID", f"{source} is missing module.")
    if "primitives" not in payload:
        raise ReactRegistryError("REGISTRY_INVALID", f"{source} is missing primitives.")
    primitives = payload["primitives"]
    if not isinstance(primitives, Mapping):
        raise ReactRegistryError("REGISTRY_INVALID", f"{source} primitives must be an object.")
    return ReactComponentRegistry(module=str(payload["module"]), primitives=dict(primitives))


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReactRegistryError(
                "REGISTRY_INVALID", f"Registry key {key!r} appears more than once.", "Map every key exactly once."
            )
        result[key] = value
    return result


def load_react_component_registry(path: str | Path) -> ReactComponentRegistry:
    """Read and check a ``REGISTRY.json`` file."""
    registry_path = Path(path)
    try:
        data = registry_path.read_bytes()
    except OSError as exc:
        raise ReactRegistryError(
            "REGISTRY_INVALID", f"Could not read registry file {registry_path.name}: {exc}"
        ) from exc
    if len(data) > REACT_REGISTRY_MAX_BYTES:
        raise ReactRegistryError(
            "REGISTRY_INVALID", f"Registry file {registry_path.name} is larger than {REACT_REGISTRY_MAX_BYTES} bytes."
        )
    try:
        payload = json.loads(data.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReactRegistryError(
            "REGISTRY_INVALID", f"Registry file {registry_path.name} is not valid UTF-8 JSON: {exc}"
        ) from exc
    return parse_react_component_registry(payload, source=f"registry file {registry_path.name}")


def react_component_registry_from_manifest(section: object) -> ReactComponentRegistry:
    """Rebuild the registry a manifest ``registry`` section describes, checking its own consistency."""
    mismatch = "REGISTRY_MANIFEST_MISMATCH"
    fix = "Re-run viewspec compile with the same --registry file instead of editing the manifest."
    if not isinstance(section, Mapping):
        raise ReactRegistryError(mismatch, "manifest registry must be an object", fix)
    keys = set(section)
    if keys != REACT_REGISTRY_MANIFEST_KEYS:
        raise ReactRegistryError(
            mismatch,
            f"manifest registry must have exactly the keys {', '.join(sorted(REACT_REGISTRY_MANIFEST_KEYS))}",
            fix,
        )
    primitives = section["primitives"]
    if not isinstance(primitives, Mapping):
        raise ReactRegistryError(mismatch, "manifest registry.primitives must be an object", fix)
    try:
        registry = ReactComponentRegistry(module=str(section["module"]), primitives=dict(primitives))
    except ReactRegistryError as exc:
        raise ReactRegistryError(mismatch, f"manifest registry is not a valid registry: {exc.message}", fix) from exc
    count = section["primitive_count"]
    if isinstance(count, bool) or count != len(registry.primitives):
        raise ReactRegistryError(mismatch, "manifest registry.primitive_count does not match its primitives", fix)
    if section["sha256"] != registry.sha256:
        raise ReactRegistryError(mismatch, "manifest registry.sha256 does not match its module and primitives", fix)
    return registry


def default_react_registry_module() -> str:
    """The packaged default module: one pass-through component per kind that renders ``as``."""
    return resources.files(__name__).joinpath(REACT_REGISTRY_DEFAULT_MODULE_FILE).read_text(encoding="utf-8")


def write_default_react_registry(path: str | Path, *, force: bool = False) -> tuple[Path, Path]:
    """Write the default ``REGISTRY.json`` at ``path`` and the default module beside it.

    Returns ``(registry_path, module_path)``. Refuses to overwrite either file unless ``force``.
    """
    from viewspec.local_tools_io import atomic_write

    registry_path = Path(path)
    module_name = DEFAULT_REACT_COMPONENT_REGISTRY.module_file_name
    if module_name is None:  # pragma: no cover - the default module is a single segment
        raise ReactRegistryError("REGISTRY_MODULE_INVALID", "The default registry module must be a single segment.")
    module_path = registry_path.parent / module_name
    for target in (registry_path, module_path):
        if target.exists() and not force:
            raise ValueError(f"{target} already exists; pass --force to overwrite")
    atomic_write(registry_path, json.dumps(DEFAULT_REACT_COMPONENT_REGISTRY.to_json(), indent=2) + "\n")
    atomic_write(module_path, default_react_registry_module())
    return registry_path, module_path


@dataclass(frozen=True)
class _SourceElement:
    line_number: int
    jsx_tag: str
    attrs: Mapping[str, str]


def _render_block_elements(tsx: str) -> list[_SourceElement]:
    lines = tsx.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == "return (":
            start = index + 1
            break
    if start is None:
        return []
    elements: list[_SourceElement] = []
    for offset, raw_line in enumerate(lines[start:], start=start):
        line = raw_line.strip()
        if line == ");":
            break
        if not line.startswith("<") or line.startswith("</") or line in {"<tbody>", "</tbody>"}:
            continue
        match = _JSX_TAG_RE.match(line)
        if match is None:
            continue
        attrs: dict[str, str] = {}
        for name, encoded in _JSX_JSON_ATTR_RE.findall(line):
            try:
                attrs[name] = str(json.loads(encoded))
            except json.JSONDecodeError:
                continue
        for name, value in _JSX_LITERAL_ATTR_RE.findall(line):
            attrs.setdefault(name, value)
        elements.append(_SourceElement(offset + 1, match.group(1), attrs))
    return elements


def react_registry_imports(tsx: str) -> list[str]:
    """Every import statement in ``tsx`` other than the React namespace import."""
    return [line for line in _IMPORT_LINE_RE.findall(tsx) if line != _REACT_IMPORT_LINE]


def expected_react_registry_import(tsx: str, manifest: Mapping[str, Any]) -> str | None:
    """The registry import line a ``viewspec check`` of this artifact expects, or ``None`` without a registry."""
    section = manifest.get("registry")
    if section is None:
        return None
    try:
        registry = react_component_registry_from_manifest(section)
    except ReactRegistryError:
        return None
    kinds = {
        element.attrs["data-ir-primitive"]
        for element in _render_block_elements(tsx)
        if "data-ir-primitive" in element.attrs
    }
    kinds &= set(REACT_PRIMITIVE_KINDS)
    if not kinds:
        return None
    return registry.import_line(kinds)


def validate_react_registry_artifact(tsx: str, manifest: Mapping[str, Any]) -> list[str]:
    """Check the manifest ``registry`` section against the emitted source. Errors carry the mismatch code."""
    code = "REGISTRY_MANIFEST_MISMATCH"
    imports = react_registry_imports(tsx)
    elements = _render_block_elements(tsx)
    section = manifest.get("registry")
    if section is None:
        errors: list[str] = []
        if imports:
            errors.append(f"{code}: ViewSpecView.tsx imports a component registry the manifest does not declare")
        for element in elements:
            if element.jsx_tag[:1].isupper() or "as" in element.attrs:
                errors.append(
                    f"{code}: ViewSpecView.tsx line {element.line_number} renders a registry component "
                    "but the manifest declares no registry"
                )
                break
        return errors
    try:
        registry = react_component_registry_from_manifest(section)
    except ReactRegistryError as exc:
        return [f"{code}: {exc.message}"]
    errors = []
    used_kinds: set[str] = set()
    for element in elements:
        kind = element.attrs.get("data-ir-primitive")
        dom_id = element.attrs.get("id", f"line {element.line_number}")
        if kind is None or kind not in registry.primitives:
            errors.append(f"{code}: ViewSpecView.tsx node {dom_id} has no registry primitive kind")
            continue
        used_kinds.add(kind)
        expected = registry.primitives[kind]
        if element.jsx_tag != expected:
            errors.append(
                f"{code}: ViewSpecView.tsx node {dom_id} renders <{element.jsx_tag}> but the registry maps {kind} to {expected}"
            )
        if element.attrs.get("as") not in REACT_PRIMITIVE_ELEMENT_TAGS:
            errors.append(f"{code}: ViewSpecView.tsx node {dom_id} has no closed `as` element")
    if not used_kinds:
        errors.append(f"{code}: ViewSpecView.tsx renders no registry components")
        return errors
    expected_import = registry.import_line(used_kinds)
    if imports != [expected_import]:
        errors.append(f"{code}: ViewSpecView.tsx registry import does not match the manifest registry")
    return errors


__all__ = [
    "DEFAULT_REACT_COMPONENT_REGISTRY",
    "REACT_PRIMITIVE_COMMON_PROPS",
    "REACT_PRIMITIVE_CONTRACT",
    "REACT_PRIMITIVE_ELEMENT_TAGS",
    "REACT_PRIMITIVE_KINDS",
    "REACT_REGISTRY_DEFAULT_EXPORTS",
    "REACT_REGISTRY_DEFAULT_MODULE",
    "REACT_REGISTRY_DEFAULT_MODULE_FILE",
    "REACT_REGISTRY_DEFAULT_OUT",
    "REACT_REGISTRY_SCHEMA_VERSION",
    "REACT_REGISTRY_TARGETS",
    "ReactComponentRegistry",
    "ReactPrimitiveContract",
    "ReactRegistryError",
    "default_react_registry_module",
    "expected_react_registry_import",
    "load_react_component_registry",
    "parse_react_component_registry",
    "react_component_registry_from_manifest",
    "react_registry_imports",
    "validate_react_registry_artifact",
    "validate_react_registry_export",
    "validate_react_registry_module",
    "write_default_react_registry",
]
