"""Emitter plugin base classes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any
import re

from viewspec.types import ASTBundle, IRNode


SAFE_EMITTER_PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class EmitterPlugin(ABC):
    """Interface implemented by deterministic AST emitters."""

    @abstractmethod
    def emit(self, ast_bundle: ASTBundle, output_dir: str | Path) -> dict[str, str]:
        """Emit artifacts for an AST bundle and return artifact paths."""


@dataclass(frozen=True)
class EmitterNodeContext:
    """Read-only context passed to internal primitive renderer plugins."""

    target: str
    root: IRNode
    parent: IRNode | None = None
    state: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", MappingProxyType(dict(self.state)))


@dataclass(frozen=True)
class RenderedNode:
    """A target-local render spec for one CompositionIR node."""

    tag: str
    attrs: tuple[str, ...] = ()
    text: str | None = None
    self_closing: bool = False
    child_wrapper_tag: str | None = None
    manifest_entry: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "attrs", tuple(self.attrs))
        if self.self_closing and self.text is not None:
            raise ValueError("RenderedNode cannot be self-closing and text-bearing.")
        if self.child_wrapper_tag is not None and self.self_closing:
            raise ValueError("RenderedNode cannot wrap children when self-closing.")
        if self.manifest_entry is not None:
            object.__setattr__(self, "manifest_entry", MappingProxyType(dict(self.manifest_entry)))


EmitterNodeMatchFn = Callable[[IRNode, EmitterNodeContext], bool]
EmitterNodeRenderFn = Callable[[IRNode, EmitterNodeContext], RenderedNode]


@dataclass(frozen=True)
class EmitterNodePlugin:
    """Internal renderer plugin for a CompositionIR node family."""

    plugin_id: str
    priority: int
    matches: EmitterNodeMatchFn
    render: EmitterNodeRenderFn

    def __post_init__(self) -> None:
        if not isinstance(self.plugin_id, str) or SAFE_EMITTER_PLUGIN_ID_RE.fullmatch(self.plugin_id) is None:
            raise ValueError(f"Emitter node plugin id {self.plugin_id!r} is not a safe ViewSpec id.")
        if not isinstance(self.priority, int) or isinstance(self.priority, bool):
            raise ValueError(f"Emitter node plugin {self.plugin_id!r} priority must be an integer.")
        if not callable(self.matches):
            raise ValueError(f"Emitter node plugin {self.plugin_id!r} matches must be callable.")
        if not callable(self.render):
            raise ValueError(f"Emitter node plugin {self.plugin_id!r} render must be callable.")


@dataclass(frozen=True)
class EmitterNodeRegistry:
    """Immutable ordered registry for internal primitive renderer plugins."""

    plugins: tuple[EmitterNodePlugin, ...]

    def __init__(self, plugins: tuple[EmitterNodePlugin, ...] | list[EmitterNodePlugin]) -> None:
        plugin_tuple = tuple(plugins)
        seen: set[str] = set()
        for plugin in plugin_tuple:
            if not isinstance(plugin, EmitterNodePlugin):
                raise ValueError("EmitterNodeRegistry only accepts EmitterNodePlugin instances.")
            if plugin.plugin_id in seen:
                raise ValueError(f"Duplicate emitter node plugin registration: {plugin.plugin_id}.")
            seen.add(plugin.plugin_id)
        object.__setattr__(self, "plugins", plugin_tuple)

    def with_plugins(self, *plugins: EmitterNodePlugin) -> EmitterNodeRegistry:
        return EmitterNodeRegistry((*self.plugins, *plugins))

    def select(self, node: IRNode, context: EmitterNodeContext) -> EmitterNodePlugin:
        matches = [plugin for plugin in self.plugins if plugin.matches(node, context)]
        if not matches:
            raise ValueError(f"No emitter node plugin matched primitive {node.primitive!r} for target {context.target}.")
        matches.sort(key=lambda plugin: (-plugin.priority, plugin.plugin_id))
        best = matches[0]
        conflicts = [plugin.plugin_id for plugin in matches[1:] if plugin.priority == best.priority]
        if conflicts:
            ids = ", ".join([best.plugin_id, *conflicts])
            raise ValueError(
                f"Ambiguous emitter node plugins for primitive {node.primitive!r} at priority {best.priority}: {ids}."
            )
        return best

    def render(self, node: IRNode, context: EmitterNodeContext) -> RenderedNode:
        rendered = self.select(node, context).render(node, context)
        if not isinstance(rendered, RenderedNode):
            raise ValueError(f"Emitter node plugin for primitive {node.primitive!r} returned a non-RenderedNode value.")
        return rendered
