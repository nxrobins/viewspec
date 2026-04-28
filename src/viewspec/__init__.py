"""ViewSpec — Universal UI from semantic data."""

from viewspec.types import (
    ActionIntent,
    ASTBundle,
    BindingSpec,
    CompilerDiagnostic,
    CompilerResult,
    CompositionIR,
    GroupSpec,
    IntentBundle,
    IRNode,
    MotifSpec,
    Provenance,
    RegionSpec,
    SemanticNode,
    SemanticSubstrate,
    StyleSpec,
    ViewSpec,
    build_address_index,
    normalize_compiler_result,
    parse_canonical_address,
    resolve_address,
)
from viewspec.sdk import (
    ComparisonBuilder,
    DashboardBuilder,
    OutlineBuilder,
    TableBuilder,
    ViewSpecBuilder,
)
from viewspec.compiler import (
    compile,
    compile_auto,
    compile_remote,
    CompilerAPIError,
    CompilerInputError,
    UnsupportedMotifError,
)

__version__ = "0.1.0"

__all__ = [
    # Types
    "ActionIntent",
    "ASTBundle",
    "BindingSpec",
    "CompilerDiagnostic",
    "CompilerResult",
    "CompositionIR",
    "GroupSpec",
    "IntentBundle",
    "IRNode",
    "MotifSpec",
    "Provenance",
    "RegionSpec",
    "SemanticNode",
    "SemanticSubstrate",
    "StyleSpec",
    "ViewSpec",
    # SDK Builders
    "ComparisonBuilder",
    "DashboardBuilder",
    "OutlineBuilder",
    "TableBuilder",
    "ViewSpecBuilder",
    # Compiler
    "compile",
    "compile_auto",
    "compile_remote",
    "CompilerAPIError",
    "CompilerInputError",
    "UnsupportedMotifError",
    # Utilities
    "build_address_index",
    "normalize_compiler_result",
    "parse_canonical_address",
    "resolve_address",
]
