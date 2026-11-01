"""ViewSpec: describe intent, compile a checked interface."""

import logging

from viewspec._version import __version__
from viewspec.api import check, compile, prove
from viewspec.app_bundle import AppBundle, AppBundleError  # noqa: F401
from viewspec.compiler import CompilerInputError, UnsupportedMotifError  # noqa: F401
from viewspec.compiler_errors import ViewSpecError
from viewspec.design_md import DesignSystemContext, DesignSystemError, load_design_system  # noqa: F401
from viewspec.error_codes import ERROR_CODES
from viewspec.mcp_apps import compile_mcp_app
from viewspec.sdk import ViewSpecBuilder
from viewspec.types import ASTBundle, IntentBundle

# Library code logs through ``logging``; the application decides where records go.
logging.getLogger(__name__).addHandler(logging.NullHandler())

# The concrete errors stay importable, but ViewSpecError is the name that callers catch.
__all__ = [
    "__version__",
    "ASTBundle",
    "AppBundle",
    "DesignSystemContext",
    "ERROR_CODES",
    "IntentBundle",
    "ViewSpecBuilder",
    "ViewSpecError",
    "check",
    "compile",
    "compile_mcp_app",
    "load_design_system",
    "prove",
]
