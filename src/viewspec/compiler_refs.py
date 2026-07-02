from __future__ import annotations

from viewspec.types import CompilerDiagnostic

# Maximum nesting depth for agent-authored structure (region parent chains and
# outline semantic chains). Bounds recursion in the compile/emit pipeline so
# pathological input is rejected with a clean diagnostic instead of a Python
# RecursionError. Kept well under the interpreter's ~1000-frame limit even when
# region and outline depth compound. Lives here (not compiler.py) because
# motif_compilers imports it and compiler.py imports motif_compilers.
MAX_COMPILE_NESTING_DEPTH = 64


def region_ref(region_id: str) -> str:
    return f"viewspec:region:{region_id}"


def binding_ref(binding_id: str) -> str:
    return f"viewspec:binding:{binding_id}"


def motif_ref(motif_id: str) -> str:
    return f"viewspec:motif:{motif_id}"


def style_ref(style_id: str) -> str:
    return f"viewspec:style:{style_id}"


def action_ref(action_id: str) -> str:
    return f"viewspec:action:{action_id}"


def view_ref(view_id: str) -> str:
    return f"viewspec:view:{view_id}"


def add_diagnostic(
    diagnostics: list[CompilerDiagnostic],
    code: str,
    message: str,
    *,
    intent_ref: str | None = None,
    content_ref: str | None = None,
    region_id: str | None = None,
) -> CompilerDiagnostic:
    diagnostic = CompilerDiagnostic(
        severity="error",
        code=code,
        message=message,
        intent_ref=intent_ref or "",
        content_ref=content_ref or "",
        region_id=region_id or "",
    )
    diagnostics.append(diagnostic)
    return diagnostic
