"""Studio's inspector: provenance ids mapped back to source JSON paths, and the overlay that shows them.

The map is computed from ``provenance_manifest.json`` plus the source bundle after each successful
Studio build and served at ``/__viewspec/inspector.json``. The overlay is a script Studio injects
into the page it serves at ``/``. Neither touches the compiled files on disk.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from viewspec.api import source_kind
from viewspec.types import parse_canonical_address

STUDIO_INSPECTOR_PATH = "/__viewspec/inspector.json"
INSPECTOR_SCHEMA_VERSION = 1

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INTENT_REF_KINDS = frozenset({"view", "region", "binding", "group", "motif", "style", "action"})


def empty_inspector_map(generation: int = 0) -> dict[str, Any]:
    """The map Studio serves before its first successful build."""
    return _payload(None, generation, [], [])


def build_inspector_map(source: Path, build_dir: Path, *, generation: int) -> dict[str, Any]:
    """Map every ``data-ir-id``, ``data-binding-id``, and ``data-action-id`` in a build to its source.

    Reads the build's provenance manifest(s) and the source bundle. A problem in either leaves the
    preview alone: the map is then empty and carries one ``STUDIO_INSPECTOR_UNAVAILABLE`` error.
    """
    kind = source_kind(source)
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
        if kind == "app_bundle":
            screens = _app_screens(document, build_dir)
        else:
            screens = [_screen_map(None, "$", document, build_dir / "provenance_manifest.json")]
    except Exception as exc:
        error = {
            "code": "STUDIO_INSPECTOR_UNAVAILABLE",
            "message": f"ViewSpec Studio could not map this build back to its source: {type(exc).__name__}: {exc}",
            "fix": "Fix the source so it compiles, or save it again to rebuild. The preview itself is unaffected.",
        }
        return _payload(kind, generation, [], [error])
    return _payload(kind, generation, screens, [])


def find_inspector_entry(payload: dict[str, Any], ident: str, *, screen: str | None = None) -> dict[str, Any] | None:
    """Return the one entry whose ir id, binding id, or action id is ``ident``, or ``None``."""
    for item in payload.get("screens", []):
        if screen is not None and item.get("id") != screen:
            continue
        for table in ("ir", "bindings", "actions"):
            entry = item.get(table, {}).get(ident)
            if entry is not None:
                return {"generation": payload.get("generation"), "screen": item.get("id"), **entry}
    return None


def _payload(
    kind: str | None, generation: int, screens: list[dict[str, Any]], errors: list[dict[str, str]]
) -> dict[str, Any]:
    return {
        "schema_version": INSPECTOR_SCHEMA_VERSION,
        "generation": generation,
        "source_kind": kind,
        "screens": screens,
        "errors": errors,
    }


def _app_screens(document: Any, build_dir: Path) -> list[dict[str, Any]]:
    screens = document.get("screens") if isinstance(document, dict) else None
    if not isinstance(screens, list):
        return []
    root = build_dir.resolve()
    result: list[dict[str, Any]] = []
    for index, screen in enumerate(screens):
        if not isinstance(screen, dict) or not isinstance(screen.get("id"), str):
            continue
        manifest_path = (root / "screens" / screen["id"] / "artifact" / "provenance_manifest.json").resolve()
        if root not in manifest_path.parents or not manifest_path.is_file():
            continue
        bundle = screen.get("intent_bundle")
        result.append(
            _screen_map(
                screen["id"],
                f"$.screens[{index}].intent_bundle",
                bundle if isinstance(bundle, dict) else {},
                manifest_path,
            )
        )
    return result


def _screen_map(screen_id: str | None, prefix: str, bundle: Any, manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    nodes = manifest.get("nodes") if isinstance(manifest, dict) else None
    index = _SourceIndex(bundle if isinstance(bundle, dict) else {}, prefix)
    ir: dict[str, Any] = {}
    bindings: dict[str, Any] = {}
    actions: dict[str, Any] = {}
    for dom_id, node in (nodes or {}).items():
        if not isinstance(node, dict):
            continue
        entry = index.entry(str(dom_id), node)
        ir[entry["ir_id"]] = entry
        if entry["binding_id"] is not None:
            bindings[entry["binding_id"]] = entry
        if entry["action_id"] is not None:
            actions[entry["action_id"]] = entry
    return {"id": screen_id, "source_path": prefix, "ir": ir, "bindings": bindings, "actions": actions}


class _SourceIndex:
    """One screen's source bundle, indexed by the ids the manifest refers to."""

    def __init__(self, bundle: dict[str, Any], prefix: str) -> None:
        view = bundle.get("view_spec")
        substrate = bundle.get("substrate")
        self.view: dict[str, Any] = view if isinstance(view, dict) else {}
        nodes = substrate.get("nodes") if isinstance(substrate, dict) else None
        self.nodes: dict[str, Any] = nodes if isinstance(nodes, dict) else {}
        self.prefix = prefix
        self.bindings = self._indexed("bindings")
        self.actions = self._indexed("actions")
        self.motifs = self._indexed("motifs")
        self.regions = self._indexed("regions")
        self.motif_of_binding: dict[str, str] = {}
        for motif_id, (_, motif) in self.motifs.items():
            for member in motif.get("members") or []:
                if isinstance(member, str):
                    self.motif_of_binding.setdefault(member, motif_id)

    def _indexed(self, key: str) -> dict[str, tuple[int, dict[str, Any]]]:
        items = self.view.get(key)
        if not isinstance(items, list):
            return {}
        indexed: dict[str, tuple[int, dict[str, Any]]] = {}
        for position, item in enumerate(items):
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                indexed.setdefault(item["id"], (position, item))
        return indexed

    def _view_path(self, key: str, position: int) -> str:
        return f"{self.prefix}.view_spec.{key}[{position}]"

    def entry(self, dom_id: str, node: dict[str, Any]) -> dict[str, Any]:
        ir_id = node.get("ir_id") if isinstance(node.get("ir_id"), str) else dom_id.removeprefix("dom-")
        raw_props = node.get("props")
        props: dict[str, Any] = raw_props if isinstance(raw_props, dict) else {}
        intent_refs = [ref for ref in node.get("intent_refs") or [] if isinstance(ref, str)]
        content_refs = [ref for ref in node.get("content_refs") or [] if isinstance(ref, str)]
        refs = _refs_by_kind(intent_refs)

        binding_id = _string(props.get("binding_id")) or refs.get("binding")
        action_id = _string(props.get("action_id")) or refs.get("action")
        binding = self.bindings.get(binding_id) if binding_id else None
        action = self.actions.get(action_id) if action_id else None

        motif_id = refs.get("motif")
        if motif_id is None and binding_id is not None:
            motif_id = self.motif_of_binding.get(binding_id)
        if motif_id is None and action is not None:
            target_kind, _, target_id = _string(action[1].get("target_ref")).partition(":")
            motif_id = target_id if target_kind == "motif" and target_id else None
        motif = self.motifs.get(motif_id) if motif_id else None

        region_id = refs.get("region")
        if region_id is None and binding is not None:
            region_id = _string(binding[1].get("target_region")) or None
        if region_id is None and action is not None:
            region_id = _string(action[1].get("target_region")) or None
        if region_id is None and motif is not None:
            region_id = _string(motif[1].get("region")) or None
        region = self.regions.get(region_id) if region_id else None

        motif_path = self._view_path("motifs", motif[0]) if motif else None
        region_path = self._view_path("regions", region[0]) if region else None
        if binding is not None:
            source_path: str | None = self._view_path("bindings", binding[0])
        elif action is not None:
            source_path = self._view_path("actions", action[0])
        elif motif_path is not None:
            source_path = motif_path
        elif region_path is not None:
            source_path = region_path
        elif "view" in refs and self.view:
            source_path = f"{self.prefix}.view_spec"
        else:
            source_path = None

        return {
            "ir_id": ir_id,
            "dom_id": dom_id,
            "primitive": _string(node.get("primitive")) or None,
            "binding_id": binding_id,
            "action_id": action_id,
            "motif_id": motif_id,
            "motif_kind": (_string(motif[1].get("kind")) if motif else _string(props.get("motif_kind"))) or None,
            "region_id": region_id,
            "present_as": (_string(binding[1].get("present_as")) if binding else "") or None,
            "action_kind": (_string(action[1].get("kind")) if action else _string(props.get("action_kind"))) or None,
            "source_path": source_path,
            "content_paths": [self.content_path(ref) for ref in content_refs],
            "motif_path": motif_path,
            "region_path": region_path,
            "intent_refs": intent_refs,
            "content_refs": content_refs,
        }

    def content_path(self, ref: str) -> str | None:
        """The substrate path a canonical address names, or ``None`` when the source has no such value."""
        try:
            parts = parse_canonical_address(ref)
        except ValueError:
            return None
        node = self.nodes.get(parts["node_id"])
        if not isinstance(node, dict):
            return None
        path = f"{self.prefix}.substrate.nodes{_key(parts['node_id'])}"
        if parts["attr"] is not None:
            attrs = node.get("attrs")
            if not isinstance(attrs, dict) or parts["attr"] not in attrs:
                return None
            return f"{path}.attrs{_key(parts['attr'])}"
        if parts["slot"] is not None:
            slots = node.get("slots")
            if not isinstance(slots, dict) or parts["slot"] not in slots:
                return None
            path = f"{path}.slots{_key(parts['slot'])}"
            if parts["slot_index"] is None:
                return path
            slot = slots[parts["slot"]]
            if not isinstance(slot, list) or not 0 <= parts["slot_index"] < len(slot):
                return None
            return f"{path}[{parts['slot_index']}]"
        if parts["edge"] is not None:
            edges = node.get("edges")
            if not isinstance(edges, dict) or parts["edge"] not in edges:
                return None
            return f"{path}.edges{_key(parts['edge'])}"
        return path


def _refs_by_kind(intent_refs: list[str]) -> dict[str, str]:
    """``viewspec:binding:x`` becomes ``{"binding": "x"}``; the first ref of each kind wins."""
    refs: dict[str, str] = {}
    for ref in intent_refs:
        scheme, kind, ident = ref.split(":", 2) if ref.count(":") >= 2 else ("", "", "")
        if scheme == "viewspec" and kind in _INTENT_REF_KINDS and ident:
            refs.setdefault(kind, ident)
    return refs


def _key(name: str) -> str:
    """One JSON path step: ``.name`` for an identifier, bracket form for anything else."""
    if _IDENTIFIER_RE.match(name):
        return f".{name}"
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    return f"['{escaped}']"


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


_INSPECTOR_TEMPLATE = r"""<script>
(() => {
  const endpoint = "@@inspector_path@@";
  const storageKey = "viewspec-studio-inspector";
  let map = null;
  let enabled = true;
  let target = null;
  let copiedUntil = 0;
  try { enabled = sessionStorage.getItem(storageKey) !== "off"; } catch (error) { enabled = true; }

  const ui = document.createElement("div");
  ui.setAttribute("data-viewspec-studio-inspector", "");
  ui.style.cssText = "position:fixed;inset:0;z-index:2147483646;pointer-events:none;"
    + "font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#f8fafc";
  const box = document.createElement("div");
  box.style.cssText = "position:fixed;display:none;box-sizing:border-box;border:2px solid #14b8a6;"
    + "border-radius:4px;background:rgba(20,184,166,0.12);pointer-events:none";
  const tip = document.createElement("div");
  tip.style.cssText = "position:fixed;display:none;max-width:min(560px,calc(100vw - 24px));padding:10px 12px;"
    + "border-radius:8px;background:rgba(15,23,42,0.96);box-shadow:0 12px 32px rgba(0,0,0,0.35);"
    + "white-space:pre-wrap;overflow-wrap:anywhere;pointer-events:none";
  const pill = document.createElement("button");
  pill.type = "button";
  pill.style.cssText = "position:fixed;top:8px;right:8px;pointer-events:auto;border:0;border-radius:999px;"
    + "padding:4px 10px;font:inherit;font-weight:700;cursor:pointer;color:#fff;box-shadow:0 1px 3px rgba(0,0,0,0.3)";
  ui.append(box, tip, pill);

  const screenOf = (el) => {
    const section = el.closest("[data-viewspec-app-screen]");
    return section ? section.getAttribute("data-viewspec-app-screen") : null;
  };
  const lookup = (el) => {
    if (!map) return null;
    const irId = el.getAttribute("data-ir-id");
    const screenId = screenOf(el);
    for (const screen of map.screens) {
      if (screen.id === screenId && screen.ir[irId]) return { screen, entry: screen.ir[irId] };
    }
    return null;
  };
  const line = (label, value) => (value ? `${label}: ${value}\n` : "");
  const describe = (el, found) => {
    const hint = Date.now() < copiedUntil
      ? "copied to the clipboard"
      : "click: copy source path | shift+click: copy value path | Alt+I: inspector off";
    if (!found) {
      const reason = map ? "not in this build's inspector map" : "inspector map not loaded";
      return `data-ir-id: ${el.getAttribute("data-ir-id")}\n${reason}\n\n${hint}`;
    }
    const e = found.entry;
    const motif = e.motif_id && e.motif_kind ? `${e.motif_id} (${e.motif_kind})` : e.motif_id;
    const values = (e.content_paths || []).filter(Boolean).join("\n       ");
    return line("data-ir-id", e.ir_id) + line("data-binding-id", e.binding_id) + line("data-action-id", e.action_id)
      + line("primitive", e.primitive) + line("screen", found.screen.id) + line("motif", motif)
      + line("region", e.region_id) + line("present_as", e.present_as) + line("action", e.action_kind)
      + line("source", e.source_path) + line("value", values) + `\n${hint}`;
  };

  const place = () => {
    if (!target || !enabled || !target.isConnected) {
      box.style.display = "none";
      tip.style.display = "none";
      return;
    }
    const r = target.getBoundingClientRect();
    box.style.display = "block";
    box.style.left = `${r.left - 2}px`;
    box.style.top = `${r.top - 2}px`;
    box.style.width = `${r.width + 4}px`;
    box.style.height = `${r.height + 4}px`;
    tip.style.display = "block";
    const width = document.documentElement.clientWidth;
    const height = document.documentElement.clientHeight;
    const left = Math.max(8, Math.min(r.left, width - tip.offsetWidth - 8));
    let top = r.bottom + 8;
    if (top + tip.offsetHeight > height - 8) top = Math.max(8, r.top - tip.offsetHeight - 8);
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  };
  const show = (el) => {
    target = el;
    tip.textContent = describe(el, lookup(el));
    place();
  };
  const hide = () => {
    target = null;
    place();
  };

  const fallbackCopy = (text) => {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.cssText = "position:fixed;top:0;left:0;opacity:0";
    document.body.append(area);
    area.select();
    try { document.execCommand("copy"); } catch (error) { /* nothing to copy with */ }
    area.remove();
  };
  const copy = (text) => {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
    }
    return Promise.resolve(fallbackCopy(text));
  };

  const setEnabled = (value) => {
    enabled = value;
    try {
      if (value) sessionStorage.removeItem(storageKey);
      else sessionStorage.setItem(storageKey, "off");
    } catch (error) { /* the state then lasts for this page only */ }
    pill.textContent = value ? "inspector on (Alt+I)" : "inspector off (Alt+I)";
    pill.style.background = value ? "#0f766e" : "#475569";
    if (!value) hide();
  };

  const inspected = (event) => (event.target instanceof Element ? event.target.closest("[data-ir-id]") : null);
  document.addEventListener("pointerover", (event) => {
    if (!enabled || ui.contains(event.target)) return;
    const el = inspected(event);
    if (el) show(el);
    else hide();
  }, true);
  document.addEventListener("pointerout", (event) => {
    if (enabled && event.relatedTarget === null) hide();
  }, true);
  document.addEventListener("click", (event) => {
    if (!enabled || ui.contains(event.target)) return;
    const el = inspected(event);
    if (!el) return;
    event.preventDefault();
    event.stopPropagation();
    const found = lookup(el);
    if (!found) return;
    const valuePath = (found.entry.content_paths || []).find(Boolean) || null;
    const text = event.shiftKey ? valuePath || found.entry.source_path : found.entry.source_path || valuePath;
    if (!text) return;
    copy(text).then(() => {
      copiedUntil = Date.now() + 1200;
      show(el);
      setTimeout(() => { if (target === el) show(el); }, 1300);
    });
  }, true);
  document.addEventListener("keydown", (event) => {
    if (event.altKey && !event.ctrlKey && !event.metaKey && event.code === "KeyI") {
      event.preventDefault();
      setEnabled(!enabled);
    }
  });
  pill.addEventListener("click", (event) => {
    event.stopPropagation();
    setEnabled(!enabled);
  });
  addEventListener("scroll", place, true);
  addEventListener("resize", place);

  fetch(endpoint)
    .then((response) => (response.ok ? response.json() : null))
    .then((payload) => {
      map = payload && Array.isArray(payload.screens) ? payload : null;
      if (target) show(target);
    })
    .catch(() => { map = null; });
  document.body.append(ui);
  setEnabled(enabled);
})();
</script>"""

INSPECTOR_SCRIPT = _INSPECTOR_TEMPLATE.replace("@@inspector_path@@", STUDIO_INSPECTOR_PATH)

__all__ = [
    "INSPECTOR_SCHEMA_VERSION",
    "INSPECTOR_SCRIPT",
    "STUDIO_INSPECTOR_PATH",
    "build_inspector_map",
    "empty_inspector_map",
    "find_inspector_entry",
]
