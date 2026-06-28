from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Any

from viewspec.compiler_refs import add_diagnostic
from viewspec.types import (
    BindingSpec,
    CompilerDiagnostic,
    IRNode,
    IntentBundle,
    MotifSpec,
    SemanticSubstrate,
    parse_canonical_address,
)


MOTIF_PLUGIN_ABI_VERSION = "motif_plugin_abi_v1"
SAFE_MOTIF_KIND_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class MotifPluginError(ValueError):
    """Raised when a motif plugin violates the local compiler ABI."""


@dataclass(frozen=True)
class MotifCompileContext:
    substrate: SemanticSubstrate
    ordered_positions: dict[str, int]
    bindings_by_region: dict[str, list[BindingSpec]]
    binding_nodes: dict[str, IRNode]
    diagnostics: list[CompilerDiagnostic]

    def add_diagnostic(
        self,
        code: str,
        message: str,
        *,
        intent_ref: str | None = None,
        content_ref: str | None = None,
        region_id: str | None = None,
    ) -> CompilerDiagnostic:
        return add_diagnostic(
            self.diagnostics,
            code,
            message,
            intent_ref=intent_ref,
            content_ref=content_ref,
            region_id=region_id,
        )


@dataclass(frozen=True)
class MotifCompileResult:
    wrapper: IRNode
    placed_binding_ids: frozenset[str]


@dataclass(frozen=True)
class MotifPluginSlot:
    name: str
    required: bool = True
    present_as: tuple[str, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "present_as", _coerce_str_tuple(self.present_as))

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "required": self.required,
            "present_as": list(self.present_as),
            "description": self.description,
        }


@dataclass(frozen=True)
class MotifPluginManifest:
    plugin_id: str
    version: str
    kinds: tuple[str, ...]
    abi_version: str = MOTIF_PLUGIN_ABI_VERSION
    description: str = ""
    input_slots: tuple[MotifPluginSlot, ...] = ()
    output_guarantees: tuple[str, ...] = ()
    diagnostic_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "kinds", _coerce_str_tuple(self.kinds))
        object.__setattr__(self, "input_slots", tuple(self.input_slots))
        object.__setattr__(self, "output_guarantees", _coerce_str_tuple(self.output_guarantees))
        object.__setattr__(self, "diagnostic_codes", _coerce_str_tuple(self.diagnostic_codes))

    def to_json(self) -> dict[str, Any]:
        return {
            "plugin_id": self.plugin_id,
            "version": self.version,
            "kinds": list(self.kinds),
            "abi_version": self.abi_version,
            "description": self.description,
            "input_slots": [slot.to_json() for slot in self.input_slots],
            "output_guarantees": list(self.output_guarantees),
            "diagnostic_codes": list(self.diagnostic_codes),
        }


MotifBuildFn = Callable[[MotifSpec, list[BindingSpec], MotifCompileContext], MotifCompileResult]
MotifValidateFn = Callable[[MotifSpec, list[BindingSpec], MotifCompileContext], bool]


@dataclass(frozen=True)
class MotifCompiler:
    kinds: tuple[str, ...]
    build: MotifBuildFn
    validate: MotifValidateFn | None = None
    manifest: MotifPluginManifest | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kinds", _coerce_str_tuple(self.kinds))

    def compile(
        self,
        motif: MotifSpec,
        motif_bindings: list[BindingSpec],
        context: MotifCompileContext,
    ) -> MotifCompileResult | None:
        if self.validate is not None and not self.validate(motif, motif_bindings, context):
            return None
        result = self.build(motif, motif_bindings, context)
        if not isinstance(result, MotifCompileResult):
            raise MotifPluginError(f"Motif compiler for '{motif.kind}' must return MotifCompileResult.")
        if not isinstance(result.wrapper, IRNode):
            raise MotifPluginError(f"Motif compiler for '{motif.kind}' returned a non-IRNode wrapper.")
        if not isinstance(result.placed_binding_ids, frozenset):
            raise MotifPluginError(f"Motif compiler for '{motif.kind}' must return frozenset placed_binding_ids.")
        return result


MotifPlugin = MotifCompiler


@dataclass(frozen=True)
class MotifRegistry:
    """Immutable motif plugin registry used by the local compiler microkernel."""

    compilers: Mapping[str, MotifPlugin]
    builtin_kinds: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "compilers", MappingProxyType(dict(self.compilers)))

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(self.compilers)

    def __contains__(self, kind: str) -> bool:
        return kind in self.compilers

    def __getitem__(self, kind: str) -> MotifPlugin:
        return self.compilers[kind]

    def get(self, kind: str) -> MotifPlugin | None:
        return self.compilers.get(kind)

    def describe(self) -> dict[str, Any]:
        custom_kinds = [kind for kind in self.kinds if kind not in self.builtin_kinds]
        custom_plugins: list[dict[str, Any]] = []
        seen_plugin_ids: set[str] = set()
        for kind in custom_kinds:
            manifest = self.compilers[kind].manifest
            if manifest is None or manifest.plugin_id in seen_plugin_ids:
                continue
            seen_plugin_ids.add(manifest.plugin_id)
            custom_plugins.append(manifest.to_json())
        return {
            "builtin_kinds": [kind for kind in self.kinds if kind in self.builtin_kinds],
            "custom_kinds": custom_kinds,
            "custom_plugins": custom_plugins,
        }

    def with_plugins(self, *motif_plugins: MotifPlugin) -> MotifRegistry:
        if not motif_plugins:
            return self
        registry = _register_motif_compilers(
            motif_plugins,
            protected_kinds=self.builtin_kinds,
            base=self.compilers,
        )
        return MotifRegistry(compilers=registry, builtin_kinds=self.builtin_kinds)


@dataclass(frozen=True)
class MotifPluginFixture:
    id: str
    bundle: IntentBundle
    expected_motif_kinds: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected_motif_kinds", _coerce_str_tuple(self.expected_motif_kinds))


@dataclass(frozen=True)
class MotifPluginCheckIssue:
    code: str
    message: str
    fixture_id: str = ""
    motif_kind: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "fixture_id": self.fixture_id,
            "motif_kind": self.motif_kind,
        }


@dataclass(frozen=True)
class MotifPluginCheckReport:
    ok: bool
    issues: tuple[MotifPluginCheckIssue, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [issue.to_json() for issue in self.issues],
        }


def _coerce_str_tuple(value: Iterable[str] | str) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def _is_safe_id(value: object) -> bool:
    return isinstance(value, str) and SAFE_MOTIF_KIND_RE.fullmatch(value) is not None


def _require_safe_id(label: str, value: object) -> None:
    if not _is_safe_id(value):
        raise MotifPluginError(f"Motif plugin {label} '{value}' is not a safe ViewSpec id.")


def _validate_motif_manifest(compiler: MotifCompiler) -> None:
    manifest = compiler.manifest
    if manifest is None:
        return
    if not isinstance(manifest, MotifPluginManifest):
        raise MotifPluginError("Motif plugin manifest must be a MotifPluginManifest instance.")
    _require_safe_id("id", manifest.plugin_id)
    if not isinstance(manifest.version, str) or not manifest.version:
        raise MotifPluginError(f"Motif plugin manifest '{manifest.plugin_id}' must declare a version.")
    if manifest.abi_version != MOTIF_PLUGIN_ABI_VERSION:
        raise MotifPluginError(
            f"Motif plugin manifest '{manifest.plugin_id}' declares unsupported ABI "
            f"'{manifest.abi_version}'. Expected '{MOTIF_PLUGIN_ABI_VERSION}'."
        )
    if manifest.kinds != compiler.kinds:
        raise MotifPluginError(
            f"Motif plugin manifest '{manifest.plugin_id}' kinds must exactly match plugin kinds."
        )
    for kind in manifest.kinds:
        _require_safe_id("kind", kind)
    for slot in manifest.input_slots:
        if not isinstance(slot, MotifPluginSlot):
            raise MotifPluginError(
                f"Motif plugin manifest '{manifest.plugin_id}' input slots must be MotifPluginSlot instances."
            )
        _require_safe_id("slot", slot.name)
        for present_as in slot.present_as:
            _require_safe_id("slot present_as", present_as)
    for diagnostic_code in manifest.diagnostic_codes:
        _require_safe_id("diagnostic code", diagnostic_code)


def _register_motif_compilers(
    compilers: Iterable[MotifPlugin],
    *,
    protected_kinds: frozenset[str] = frozenset(),
    base: Mapping[str, MotifPlugin] | None = None,
) -> dict[str, MotifPlugin]:
    registry: dict[str, MotifPlugin] = dict(base or {})
    plugin_ids = {
        compiler.manifest.plugin_id
        for compiler in registry.values()
        if isinstance(compiler, MotifCompiler) and compiler.manifest is not None
    }
    for compiler in compilers:
        if not isinstance(compiler, MotifCompiler):
            raise MotifPluginError("Motif plugins must be MotifPlugin instances.")
        if not compiler.kinds:
            raise MotifPluginError("Motif plugins must declare at least one motif kind.")
        if not callable(compiler.build):
            raise MotifPluginError("Motif plugins must declare a callable build function.")
        if compiler.validate is not None and not callable(compiler.validate):
            raise MotifPluginError("Motif plugin validate hooks must be callable.")
        _validate_motif_manifest(compiler)
        if compiler.manifest is not None:
            if compiler.manifest.plugin_id in plugin_ids:
                raise MotifPluginError(
                    f"Duplicate motif plugin manifest id '{compiler.manifest.plugin_id}'."
                )
            plugin_ids.add(compiler.manifest.plugin_id)
        for kind in compiler.kinds:
            if not _is_safe_id(kind):
                raise MotifPluginError(f"Motif plugin kind '{kind}' is not a safe ViewSpec id.")
            if kind in protected_kinds:
                raise MotifPluginError(f"Motif plugin kind '{kind}' cannot override a built-in motif compiler.")
            if kind in registry:
                raise MotifPluginError(f"Duplicate motif compiler registration for motif kind '{kind}'.")
            registry[kind] = compiler
    return registry


def builtin_motif_registry() -> MotifRegistry:
    from viewspec.motif_compilers import BUILTIN_MOTIF_REGISTRY

    return BUILTIN_MOTIF_REGISTRY


def create_motif_registry(*motif_plugins: MotifPlugin, base: MotifRegistry | None = None) -> MotifRegistry:
    registry = base or builtin_motif_registry()
    return registry.with_plugins(*motif_plugins)


def motif_compiler_registry(motif_plugins: tuple[MotifPlugin, ...] = ()) -> MotifRegistry:
    if not motif_plugins:
        return builtin_motif_registry()
    return create_motif_registry(*motif_plugins)


def check_motif_plugin(
    plugin: MotifPlugin,
    fixtures: Iterable[MotifPluginFixture] = (),
    *,
    require_manifest: bool = True,
) -> MotifPluginCheckReport:
    issues: list[MotifPluginCheckIssue] = []
    if not isinstance(plugin, MotifCompiler):
        issues.append(MotifPluginCheckIssue(code="INVALID_PLUGIN", message="Plugin must be a MotifPlugin instance."))
        return MotifPluginCheckReport(ok=False, issues=tuple(issues))
    if require_manifest and plugin.manifest is None:
        issues.append(
            MotifPluginCheckIssue(
                code="MISSING_PLUGIN_MANIFEST",
                message="Reusable motif plugins must declare a MotifPluginManifest.",
            )
        )

    registry: MotifRegistry | None = None
    try:
        registry = create_motif_registry(plugin)
    except MotifPluginError as exc:
        issues.append(MotifPluginCheckIssue(code="PLUGIN_ABI_ERROR", message=str(exc)))

    for fixture in fixtures:
        if not isinstance(fixture, MotifPluginFixture):
            issues.append(
                MotifPluginCheckIssue(
                    code="INVALID_FIXTURE",
                    message="Fixtures must be MotifPluginFixture instances.",
                )
            )
            continue
        _check_fixture_expected_kinds(plugin, fixture, issues)
        if plugin.manifest is not None:
            _check_fixture_required_slots(plugin, fixture, issues)
        if registry is not None:
            _check_fixture_determinism(registry, fixture, issues)

    return MotifPluginCheckReport(ok=not issues, issues=tuple(issues))


def _check_fixture_expected_kinds(
    plugin: MotifPlugin,
    fixture: MotifPluginFixture,
    issues: list[MotifPluginCheckIssue],
) -> None:
    fixture_kinds = {motif.kind for motif in fixture.bundle.view_spec.motifs}
    for kind in fixture.expected_motif_kinds:
        if kind not in plugin.kinds:
            issues.append(
                MotifPluginCheckIssue(
                    code="EXPECTED_KIND_NOT_DECLARED",
                    message=f"Fixture expected motif kind '{kind}', but the plugin does not declare it.",
                    fixture_id=fixture.id,
                    motif_kind=kind,
                )
            )
        if kind not in fixture_kinds:
            issues.append(
                MotifPluginCheckIssue(
                    code="EXPECTED_KIND_NOT_IN_FIXTURE",
                    message=f"Fixture does not contain expected motif kind '{kind}'.",
                    fixture_id=fixture.id,
                    motif_kind=kind,
                )
            )


def _check_fixture_required_slots(
    plugin: MotifPlugin,
    fixture: MotifPluginFixture,
    issues: list[MotifPluginCheckIssue],
) -> None:
    manifest = plugin.manifest
    if manifest is None:
        return
    required_slots = [slot for slot in manifest.input_slots if slot.required]
    if not required_slots:
        return
    bindings_by_id = {binding.id: binding for binding in fixture.bundle.view_spec.bindings}
    for motif in fixture.bundle.view_spec.motifs:
        if motif.kind not in plugin.kinds:
            continue
        motif_bindings = [bindings_by_id[member_id] for member_id in motif.members if member_id in bindings_by_id]
        for slot in required_slots:
            if any(_binding_matches_slot(binding, slot) for binding in motif_bindings):
                continue
            issues.append(
                MotifPluginCheckIssue(
                    code="MISSING_REQUIRED_SLOT",
                    message=(
                        f"Fixture motif '{motif.id}' does not include required slot '{slot.name}' "
                        "by canonical attr/slot name or present_as."
                    ),
                    fixture_id=fixture.id,
                    motif_kind=motif.kind,
                )
            )


def _binding_matches_slot(binding: BindingSpec, slot: MotifPluginSlot) -> bool:
    attr_or_slot = ""
    try:
        parts = parse_canonical_address(binding.address)
        attr_or_slot = str(parts.get("attr") or parts.get("slot") or "")
    except ValueError:
        pass
    accepted_present_as = {slot.name, *slot.present_as}
    return attr_or_slot == slot.name or binding.present_as in accepted_present_as


def _check_fixture_determinism(
    registry: MotifRegistry,
    fixture: MotifPluginFixture,
    issues: list[MotifPluginCheckIssue],
) -> None:
    from viewspec.compiler import compile as compile_bundle

    try:
        first = compile_bundle(fixture.bundle, motif_registry=registry)
        second = compile_bundle(fixture.bundle, motif_registry=registry)
    except Exception as exc:
        issues.append(
            MotifPluginCheckIssue(
                code="FIXTURE_COMPILE_ERROR",
                message=str(exc),
                fixture_id=fixture.id,
            )
        )
        return
    if first.to_json() != second.to_json():
        issues.append(
            MotifPluginCheckIssue(
                code="NONDETERMINISTIC_FIXTURE",
                message="Fixture produced different AST JSON across two compile runs.",
                fixture_id=fixture.id,
            )
        )


__all__ = [
    "MOTIF_PLUGIN_ABI_VERSION",
    "MotifBuildFn",
    "MotifCompileContext",
    "MotifCompileResult",
    "MotifCompiler",
    "MotifPlugin",
    "MotifPluginCheckIssue",
    "MotifPluginCheckReport",
    "MotifPluginError",
    "MotifPluginFixture",
    "MotifPluginManifest",
    "MotifPluginSlot",
    "MotifRegistry",
    "MotifValidateFn",
    "builtin_motif_registry",
    "check_motif_plugin",
    "create_motif_registry",
    "motif_compiler_registry",
]
