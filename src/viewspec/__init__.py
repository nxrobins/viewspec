"""ViewSpec: describe intent, compile a checked interface."""

import logging

from viewspec._version import __version__
from viewspec.api import check, compile, prove
from viewspec.app_bundle import AppBundle, AppBundleError
from viewspec.compiler import CompilerInputError, UnsupportedMotifError
from viewspec.design_md import DesignSystemError
from viewspec.sdk import ViewSpecBuilder
from viewspec.types import ASTBundle, IntentBundle

# Library code logs through ``logging``; the application decides where records go.
logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    "__version__",
    "ASTBundle",
    "AppBundle",
    "AppBundleError",
    "CompilerInputError",
    "DesignSystemError",
    "IntentBundle",
    "UnsupportedMotifError",
    "ViewSpecBuilder",
    "check",
    "compile",
    "prove",
]
