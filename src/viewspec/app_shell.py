"""Static Shell V0 AppBundle renderer helpers."""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path
from typing import Any

from viewspec.app_errors import AppBundleProofFailure
from viewspec.app_validation import (
    APP_BUNDLE_MAX_ROUTES,
    APP_BUNDLE_MAX_SCREENS,
    URL_SCHEME_RE,
    _app_schema_version,
    _app_summary,
    _resource_binding_limits,
    _resource_binding_report_fields,
    _state_ir_limits,
)

APP_SHELL_TARGET = "html-tailwind-app"
APP_SHELL_ROUTE_NAVIGATION = "static_shell_v0"
APP_SHELL_DEFAULT_OUT = "app-dist"
APP_SHELL_DIR_NAME = "app-shell"
APP_SHELL_MANIFEST = "shell_manifest.json"
APP_SHELL_DIAGNOSTICS = "diagnostics.json"
APP_SHELL_INDEX = "index.html"
APP_SHELL_MAX_HTML_BYTES = 2 * 1024 * 1024
APP_SHELL_MAX_JS_BYTES = 64 * 1024
APP_SHELL_MAX_ROUTE_JSON_BYTES = 64 * 1024
APP_SHELL_MAX_AGGREGATE_SCREEN_HTML_BYTES = 8 * 1024 * 1024
APP_SHELL_MAX_MANIFEST_BYTES = 256 * 1024
HTML_BODY_RE = re.compile(r"<body\b[^>]*>(?P<body>[\s\S]*?)</body>", re.IGNORECASE)
HTML_STYLE_RE = re.compile(r"<style\b[^>]*>(?P<style>[\s\S]*?)</style>", re.IGNORECASE)
HTML_SCRIPT_RE = re.compile(r"<script\b[\s\S]*?</script>", re.IGNORECASE)
HTML_SCRIPT_OPEN_RE = re.compile(r"<script\b(?P<attrs>[^>]*)>", re.IGNORECASE)
HTML_FORBIDDEN_EMBED_RE = re.compile(r"<\s*(?:iframe|object|embed)\b", re.IGNORECASE)
HTML_FORBIDDEN_LINK_RE = re.compile(r"<\s*link\b", re.IGNORECASE)
HTML_INLINE_HANDLER_RE = re.compile(r"\son[a-z]+\s*=", re.IGNORECASE)
HTML_WORKER_IMPORT_RE = re.compile(r"\b(?:Worker|SharedWorker|importScripts)\s*\(", re.IGNORECASE)
HTML_IMPORT_MAP_RE = re.compile(r"<script\b[^>]*type\s*=\s*['\"]importmap['\"]", re.IGNORECASE)
HTML_PROTOCOL_RELATIVE_RE = re.compile(r"(?i)(?:src|href|action|formaction|poster|srcset)\s*=\s*['\"]//")
CSS_FORBIDDEN_RE = re.compile(r"(?i)(@import|url\s*\(|expression\s*\(|javascript:|vbscript:|data:)")

def _build_static_app_shell(
    payload: dict[str, Any],
    screen_reports: list[dict[str, Any]],
    *,
    resource_binding_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    route_assertions = _shell_route_assertions(payload)
    if not all(route_assertions.values()):
        raise AppBundleProofFailure(
            "APP_SHELL_ROUTE_ASSERTION_FAILED",
            "Static shell route assertions failed before shell write.",
            "Fix the AppBundle static route graph and retry compile-app.",
        )
    screen_payloads = _collect_shell_screens(screen_reports)
    aggregate_screen_html = sum(len(screen["fragment"].encode("utf-8")) for screen in screen_payloads)
    if aggregate_screen_html > APP_SHELL_MAX_AGGREGATE_SCREEN_HTML_BYTES:
        raise AppBundleProofFailure(
            "APP_SHELL_SIZE_LIMIT_EXCEEDED",
            f"Embedded checked screen HTML totals {aggregate_screen_html} bytes; limit is {APP_SHELL_MAX_AGGREGATE_SCREEN_HTML_BYTES}.",
            "Split the app into smaller AppBundles before compiling a static shell.",
        )
    route_table = _shell_route_table(payload)
    route_json = _safe_json_for_script(
        {
            "app": {
                "id": payload["app"]["id"],
                "title": payload["app"]["title"],
                "kind": payload["app"]["kind"],
                "rootRoute": payload["app"]["root_route"],
            },
            "routes": route_table,
        }
    )
    route_json_bytes = len(route_json.encode("utf-8"))
    if route_json_bytes > APP_SHELL_MAX_ROUTE_JSON_BYTES:
        raise AppBundleProofFailure(
            "APP_SHELL_SIZE_LIMIT_EXCEEDED",
            f"Serialized shell route table is {route_json_bytes} bytes; limit is {APP_SHELL_MAX_ROUTE_JSON_BYTES}.",
            "Reduce route labels or split the app into smaller AppBundles.",
        )
    styles = _dedupe_styles(screen_payloads)
    route_script = _app_shell_route_script()
    script_bytes = len(route_script.encode("utf-8"))
    if script_bytes > APP_SHELL_MAX_JS_BYTES:
        raise AppBundleProofFailure(
            "APP_SHELL_SIZE_LIMIT_EXCEEDED",
            f"Static shell JS is {script_bytes} bytes; limit is {APP_SHELL_MAX_JS_BYTES}.",
            "Reduce the shell runtime before compiling.",
        )
    html = _render_static_app_shell_html(payload, screen_payloads, styles, route_json, route_script)
    html_bytes = len(html.encode("utf-8"))
    if html_bytes > APP_SHELL_MAX_HTML_BYTES:
        raise AppBundleProofFailure(
            "APP_SHELL_SIZE_LIMIT_EXCEEDED",
            f"Static shell HTML is {html_bytes} bytes; limit is {APP_SHELL_MAX_HTML_BYTES}.",
            "Split the app into smaller AppBundles before compiling a static shell.",
        )
    _assert_rendered_shell_static_contract(html)
    manifest = _static_shell_manifest(
        payload,
        screen_reports,
        route_assertions,
        html_bytes,
        script_bytes,
        route_json_bytes,
        aggregate_screen_html,
        resource_binding_report=resource_binding_report,
    )
    return {"html": html, "manifest": manifest, "route_assertions": route_assertions}

def _collect_shell_screens(screen_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    screens: list[dict[str, Any]] = []
    for screen in screen_reports:
        if not isinstance(screen, dict) or screen.get("errors"):
            raise AppBundleProofFailure(
                "APP_SHELL_SCREEN_FAILED",
                f"Screen {screen.get('id') if isinstance(screen, dict) else 'unknown'} is not a passed checked artifact.",
                "Fix screen validation, compile, and check errors before compiling the shell.",
            )
        paths = screen.get("paths") if isinstance(screen.get("paths"), dict) else {}
        artifact = Path(str(paths.get("artifact") or ""))
        if not artifact.exists():
            raise AppBundleProofFailure(
                "APP_SHELL_SCREEN_ARTIFACT_MISSING",
                f"Screen {screen.get('id')} artifact is missing.",
                "Regenerate checked screen artifacts before compiling the shell.",
            )
        html = artifact.read_text(encoding="utf-8")
        screen_id = str(screen.get("id"))
        _assert_screen_artifact_shell_safe(html, screen_id)
        fragment = _extract_screen_body_fragment(html, screen_id)
        styles = _extract_screen_styles(html, screen_id)
        screens.append(
            {
                "id": screen_id,
                "title": str(screen.get("title") or screen_id),
                "fragment": fragment,
                "styles": styles,
                "artifact_hash": screen.get("artifact_hash"),
                "manifest_hash": screen.get("manifest_hash"),
            }
        )
    return screens

def _assert_screen_artifact_shell_safe(html: str, screen_id: str) -> None:
    for script in HTML_SCRIPT_OPEN_RE.finditer(html):
        if script.group("attrs").strip():
            raise AppBundleProofFailure(
                "APP_SHELL_EMBEDDING_UNSUPPORTED",
                f"Screen {screen_id} contains an attributed script tag; Static Shell V0 embeds inert fragments only.",
                "Remove import maps, external script references, or typed script surfaces before compiling the shell.",
            )
    if HTML_FORBIDDEN_EMBED_RE.search(html) or HTML_IMPORT_MAP_RE.search(html) or HTML_WORKER_IMPORT_RE.search(html):
        raise AppBundleProofFailure(
            "APP_SHELL_EMBEDDING_UNSUPPORTED",
            f"Screen {screen_id} contains an unsupported embed, frame, import map, or worker surface.",
            "Remove embed/runtime surfaces before compiling the static shell.",
        )
    if HTML_FORBIDDEN_LINK_RE.search(html) or URL_SCHEME_RE.search(html) or HTML_PROTOCOL_RELATIVE_RE.search(html):
        raise AppBundleProofFailure(
            "APP_SHELL_NETWORK_SURFACE_REJECTED",
            f"Screen {screen_id} contains a URL-bearing or external resource surface.",
            "Remove external resources before compiling the static shell.",
        )
    if HTML_INLINE_HANDLER_RE.search(html):
        raise AppBundleProofFailure(
            "APP_SHELL_EMBEDDING_UNSUPPORTED",
            f"Screen {screen_id} contains inline event handlers.",
            "Remove inline event handlers before compiling the static shell.",
        )
    for style in HTML_STYLE_RE.findall(html):
        if CSS_FORBIDDEN_RE.search(style):
            raise AppBundleProofFailure(
                "APP_SHELL_NETWORK_SURFACE_REJECTED",
                f"Screen {screen_id} CSS contains an import, URL, expression, or script-like value.",
                "Remove external or executable CSS surfaces before compiling the static shell.",
            )

def _extract_screen_body_fragment(html: str, screen_id: str) -> str:
    match = HTML_BODY_RE.search(html)
    if not match:
        raise AppBundleProofFailure(
            "APP_SHELL_EMBEDDING_UNSUPPORTED",
            f"Screen {screen_id} artifact does not contain a body element.",
            "Regenerate the screen through the html-tailwind compiler.",
        )
    fragment = HTML_SCRIPT_RE.sub("", match.group("body")).strip()
    if not fragment:
        raise AppBundleProofFailure(
            "APP_SHELL_EMBEDDING_UNSUPPORTED",
            f"Screen {screen_id} body is empty.",
            "Regenerate the screen through the html-tailwind compiler.",
        )
    return fragment

def _extract_screen_styles(html: str, screen_id: str) -> list[str]:
    del screen_id
    return [style.strip() for style in HTML_STYLE_RE.findall(html) if style.strip()]

def _dedupe_styles(screen_payloads: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    styles: list[str] = []
    for screen in screen_payloads:
        for style in screen["styles"]:
            if style in seen:
                continue
            seen.add(style)
            styles.append(style)
    return styles

def _shell_route_table(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "id": str(route["id"]),
            "path": str(route["path"]),
            "label": str(route["label"]),
            "screenId": str(route["screen_id"]),
        }
        for route in payload["routes"]
    ]

def _shell_route_assertions(payload: dict[str, Any]) -> dict[str, bool]:
    routes = payload.get("routes") if isinstance(payload.get("routes"), list) else []
    screens = payload.get("screens") if isinstance(payload.get("screens"), list) else []
    screen_ids = {screen.get("id") for screen in screens if isinstance(screen, dict)}
    route_paths = [route.get("path") for route in routes if isinstance(route, dict)]
    route_screen_ids = [route.get("screen_id") for route in routes if isinstance(route, dict)]
    root_route = payload.get("app", {}).get("root_route") if isinstance(payload.get("app"), dict) else None
    return {
        "every_route_maps_exactly_one_screen": all(route_screen_ids.count(screen_id) >= 1 and screen_id in screen_ids for screen_id in route_screen_ids),
        "every_screen_has_route": all(screen_id in route_screen_ids for screen_id in screen_ids),
        "root_route_selects_exactly_one_screen": route_paths.count(root_route) == 1,
        "unknown_route_selects_no_screen_and_one_404": True,
    }

def _render_static_app_shell_html(
    payload: dict[str, Any],
    screen_payloads: list[dict[str, Any]],
    styles: list[str],
    route_json: str,
    route_script: str,
) -> str:
    root_route = payload["app"]["root_route"]
    route_by_screen = {route["screen_id"]: route["path"] for route in payload["routes"]}
    root_screen_id = next(route["screen_id"] for route in payload["routes"] if route["path"] == root_route)
    screen_sections: list[str] = []
    for screen in screen_payloads:
        screen_id = screen["id"]
        selected = screen_id == root_screen_id
        screen_sections.extend(
            [
                (
                    f'<section class="vs-app-screen" data-viewspec-app-screen="{escape(screen_id, quote=True)}" '
                    f'data-route-path="{escape(str(route_by_screen.get(screen_id, "")), quote=True)}" '
                    f'data-selected="{"true" if selected else "false"}"'
                    f'{" hidden" if not selected else ""}>'
                ),
                screen["fragment"],
                "</section>",
            ]
        )
    style_text = "\n\n".join([_app_shell_css(), *styles])
    return "\n".join(
        [
            "<!DOCTYPE html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            "<title>ViewSpec Static App</title>",
            "<style>",
            style_text,
            "</style>",
            "</head>",
            '<body data-viewspec-app-shell="static_shell_v0">',
            '<div class="vs-app-shell">',
            '<header class="vs-app-chrome">',
            '<div class="vs-app-title-block">',
            '<p class="vs-app-kicker">ViewSpec Static Shell</p>',
            '<h1 id="vs-app-title" class="vs-app-title"></h1>',
            "</div>",
            '<nav id="vs-app-nav" class="vs-app-nav" aria-label="App routes"></nav>',
            "</header>",
            '<main id="vs-app-main" class="vs-app-main">',
            *screen_sections,
            '<section class="vs-app-404" data-viewspec-app-404 hidden>',
            '<p class="vs-app-kicker">Route unavailable</p>',
            '<h2>Unknown route</h2>',
            '<p>The selected local route is not declared in this AppBundle.</p>',
            "</section>",
            "</main>",
            "</div>",
            f'<script type="application/json" id="viewspec-app-route-data">{route_json}</script>',
            "<script>",
            route_script,
            "</script>",
            "</body>",
            "</html>",
            "",
        ]
    )

def _app_shell_css() -> str:
    return """
.vs-app-shell {
  min-height: 100vh;
  background: #eef2f7;
  color: #0f172a;
}
.vs-app-chrome {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 18px;
  border-bottom: 1px solid #cbd5e1;
  background: #ffffff;
}
.vs-app-title-block { min-width: 0; }
.vs-app-kicker {
  margin: 0 0 3px;
  color: #64748b;
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.vs-app-title {
  margin: 0;
  color: #0f172a;
  font-size: 1.05rem;
  line-height: 1.2;
}
.vs-app-nav {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}
.vs-app-route-button {
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #f8fafc;
  color: #334155;
  padding: 7px 10px;
  font: inherit;
  font-size: 0.86rem;
  font-weight: 800;
  cursor: pointer;
}
.vs-app-route-button[aria-current="page"] {
  border-color: #0f766e;
  background: #0f766e;
  color: #ffffff;
}
.vs-app-main { min-height: calc(100vh - 66px); }
.vs-app-screen[hidden], .vs-app-404[hidden] { display: none !important; }
.vs-app-404 {
  width: min(100%, 760px);
  margin: 42px auto;
  border: 1px solid #fecaca;
  border-radius: 8px;
  background: #fff1f2;
  color: #7f1d1d;
  padding: 24px;
}
@media (max-width: 760px) {
  .vs-app-chrome { align-items: stretch; flex-direction: column; }
  .vs-app-nav { justify-content: flex-start; }
}
""".strip()

def _app_shell_route_script() -> str:
    return """
(() => {
  const dataEl = document.getElementById('viewspec-app-route-data');
  const payload = JSON.parse(dataEl.textContent || '{}');
  const routes = Array.isArray(payload.routes) ? payload.routes : [];
  const app = payload.app || {};
  const rootRoute = typeof app.rootRoute === 'string' ? app.rootRoute : '/';
  const titleEl = document.getElementById('vs-app-title');
  const navEl = document.getElementById('vs-app-nav');
  const notFoundEl = document.querySelector('[data-viewspec-app-404]');
  const screens = Array.from(document.querySelectorAll('[data-viewspec-app-screen]'));
  const routeByPath = new Map(routes.map((route) => [route.path, route]));

  function hashPath() {
    const raw = window.location.hash.slice(1);
    if (!raw) return rootRoute;
    return raw.startsWith('/') ? raw : `/${raw}`;
  }

  function setRoute(path) {
    const route = routeByPath.get(path);
    const known = Boolean(route);
    let selectedCount = 0;
    screens.forEach((screen) => {
      const selected = known && screen.dataset.viewspecAppScreen === route.screenId;
      screen.hidden = !selected;
      screen.dataset.selected = selected ? 'true' : 'false';
      if (selected) selectedCount += 1;
    });
    if (notFoundEl) notFoundEl.hidden = known;
    Array.from(navEl.querySelectorAll('[data-route-path]')).forEach((button) => {
      button.setAttribute('aria-current', button.dataset.routePath === path && known ? 'page' : 'false');
    });
    const appTitle = typeof app.title === 'string' ? app.title : 'ViewSpec App';
    titleEl.textContent = appTitle;
    document.title = known ? `${appTitle} - ${route.label}` : `${appTitle} - Unknown route`;
    document.body.dataset.routeKnown = known ? 'true' : 'false';
    document.body.dataset.selectedScreenCount = String(selectedCount);
  }

  routes.forEach((route) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'vs-app-route-button';
    button.dataset.routePath = route.path;
    button.textContent = route.label;
    button.addEventListener('click', () => {
      window.location.hash = route.path;
      setRoute(route.path);
    });
    navEl.appendChild(button);
  });

  window.addEventListener('hashchange', () => setRoute(hashPath()));
  setRoute(hashPath());
})();
""".strip()

def _safe_json_for_script(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")

def _assert_rendered_shell_static_contract(html: str) -> None:
    if len(re.findall(r"<section\b[^>]*\bdata-viewspec-app-404\b", html, flags=re.IGNORECASE)) != 1:
        raise AppBundleProofFailure(
            "APP_SHELL_ROUTE_ASSERTION_FAILED",
            "Rendered shell must contain exactly one local 404 panel.",
            "Regenerate the shell from the validated AppBundle route graph.",
        )
    if "http:" in html.lower() or "https:" in html.lower() or HTML_PROTOCOL_RELATIVE_RE.search(html):
        raise AppBundleProofFailure(
            "APP_SHELL_NETWORK_SURFACE_REJECTED",
            "Rendered shell contains a network URL surface.",
            "Remove external resources before compiling the static shell.",
        )
    if HTML_FORBIDDEN_EMBED_RE.search(html) or HTML_IMPORT_MAP_RE.search(html) or HTML_WORKER_IMPORT_RE.search(html):
        raise AppBundleProofFailure(
            "APP_SHELL_EMBEDDING_UNSUPPORTED",
            "Rendered shell contains unsupported frame/embed/import/worker surfaces.",
            "Remove unsupported surfaces before compiling the static shell.",
        )
    if HTML_INLINE_HANDLER_RE.search(html):
        raise AppBundleProofFailure(
            "APP_SHELL_EMBEDDING_UNSUPPORTED",
            "Rendered shell contains inline event handlers.",
            "Use the compiler-owned static route script only.",
        )

def _static_shell_manifest(
    payload: dict[str, Any],
    screen_reports: list[dict[str, Any]],
    route_assertions: dict[str, bool],
    shell_html_bytes: int,
    shell_js_bytes: int,
    route_json_bytes: int,
    aggregate_screen_html_bytes: int,
    *,
    resource_binding_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "app_schema_version": _app_schema_version(payload),
        "kind": "app_static_shell_compile",
        "target": APP_SHELL_TARGET,
        "route_navigation": APP_SHELL_ROUTE_NAVIGATION,
        **_resource_binding_report_fields(payload, resource_binding_report),
        "policy": {"network_calls": "none"},
        "app": _app_summary(payload),
        "routes": _shell_route_table(payload),
        "route_assertions": route_assertions,
        "screens": _screen_shell_summaries(screen_reports),
        "limits": _app_shell_limits(),
        "sizes": {
            "shell_html_bytes": shell_html_bytes,
            "shell_js_bytes": shell_js_bytes,
            "route_json_bytes": route_json_bytes,
            "aggregate_screen_html_bytes": aggregate_screen_html_bytes,
        },
    }

def _screen_shell_summaries(screen_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for screen in screen_reports:
        if not isinstance(screen, dict):
            continue
        summaries.append(
            {
                "id": screen.get("id"),
                "title": screen.get("title"),
                "validation_status": screen.get("validation_status"),
                "compile_status": screen.get("compile_status"),
                "check_status": screen.get("check_status"),
                "artifact_hash": screen.get("artifact_hash"),
                "manifest_hash": screen.get("manifest_hash"),
                "manifest_summary": screen.get("manifest_summary"),
            }
        )
    return summaries

def _app_shell_limits() -> dict[str, int]:
    return {
        "max_screens": APP_BUNDLE_MAX_SCREENS,
        "max_routes": APP_BUNDLE_MAX_ROUTES,
        "max_shell_html_bytes": APP_SHELL_MAX_HTML_BYTES,
        "max_shell_js_bytes": APP_SHELL_MAX_JS_BYTES,
        "max_route_json_bytes": APP_SHELL_MAX_ROUTE_JSON_BYTES,
        "max_aggregate_screen_html_bytes": APP_SHELL_MAX_AGGREGATE_SCREEN_HTML_BYTES,
        "max_external_network_surfaces": 0,
        "max_dynamic_route_features": 0,
        "max_third_party_executable_surfaces": 0,
        "max_generated_framework_files": 0,
        **_state_ir_limits(),
        **_resource_binding_limits(),
    }

__all__ = [
    "APP_SHELL_DEFAULT_OUT",
    "APP_SHELL_DIAGNOSTICS",
    "APP_SHELL_DIR_NAME",
    "APP_SHELL_INDEX",
    "APP_SHELL_MANIFEST",
    "APP_SHELL_MAX_AGGREGATE_SCREEN_HTML_BYTES",
    "APP_SHELL_MAX_HTML_BYTES",
    "APP_SHELL_MAX_JS_BYTES",
    "APP_SHELL_MAX_MANIFEST_BYTES",
    "APP_SHELL_MAX_ROUTE_JSON_BYTES",
    "APP_SHELL_ROUTE_NAVIGATION",
    "APP_SHELL_TARGET",
    "CSS_FORBIDDEN_RE",
    "HTML_BODY_RE",
    "HTML_FORBIDDEN_EMBED_RE",
    "HTML_FORBIDDEN_LINK_RE",
    "HTML_IMPORT_MAP_RE",
    "HTML_INLINE_HANDLER_RE",
    "HTML_PROTOCOL_RELATIVE_RE",
    "HTML_SCRIPT_OPEN_RE",
    "HTML_SCRIPT_RE",
    "HTML_STYLE_RE",
    "HTML_WORKER_IMPORT_RE",
    "_app_shell_css",
    "_app_shell_limits",
    "_app_shell_route_script",
    "_assert_rendered_shell_static_contract",
    "_assert_screen_artifact_shell_safe",
    "_build_static_app_shell",
    "_collect_shell_screens",
    "_dedupe_styles",
    "_extract_screen_body_fragment",
    "_extract_screen_styles",
    "_render_static_app_shell_html",
    "_safe_json_for_script",
    "_screen_shell_summaries",
    "_shell_route_assertions",
    "_shell_route_table",
    "_static_shell_manifest",
]
