"""Runnable React/Tailwind AppBundle target."""

from __future__ import annotations

import html
import json
import re
from importlib import resources
from pathlib import Path
from typing import Any, Callable

from viewspec._version import __version__
from viewspec.app_diff import _validation_summary
from viewspec.app_errors import AppBundleProofFailure
from viewspec.app_numeric import (
    NUMERIC_KERNEL_PATH,
    generate_numeric_typescript,
    numeric_function_hashes,
    numeric_scope_for_app,
)
from viewspec.app_pretext import (
    PRETEXT_NPM_INTEGRITY,
    PRETEXT_NPM_RESOLVED,
    PRETEXT_PACKAGE,
    PRETEXT_PACKAGE_TREE,
    PRETEXT_PROFILE,
    PRETEXT_VERSION,
    build_pretext_scope,
)
from viewspec.app_pretext_runtime import PRETEXT_RUNTIME_PATH, generate_pretext_runtime_typescript
from viewspec.app_screens import _compile_screen, _visibility_overlays_for
from viewspec.app_state_artifacts import _write_state_artifacts
from viewspec.app_validation import (
    _app_schema_version,
    _app_summary,
    _resource_binding_report_fields,
    _route_assertions,
)
from viewspec.local_tools import atomic_write, check_artifact_dir, file_hash


REACT_APP_TARGET = "react-tailwind-app"
REACT_APP_ROUTE_NAVIGATION = "browser_history_v1"
REACT_APP_MANIFEST = "viewspec_app_manifest.json"
REACT_APP_DIAGNOSTICS = "diagnostics.json"
REACT_APP_ENTRY = "src/ViewSpecApp.tsx"
REACT_APP_MAX_SOURCE_BYTES = 512 * 1024
REACT_APP_TEMPLATE_PACKAGE = "viewspec.host_verify_template"


def _write_react_app(
    payload: dict[str, Any],
    output_dir: Path,
    screen_reports: list[dict[str, Any]],
    *,
    design_path: Path | None,
    root: Path,
    strict_design: bool,
    validation: dict[str, Any],
    resource_binding_report: dict[str, Any] | None,
    screen_proof_dir: Path | None = None,
    generate_reducer: Callable[[dict[str, Any]], str],
    check_conformance: Callable[..., dict[str, Any]],
    build_manifest: Callable[..., dict[str, Any]],
    freerange: bool = False,
    pretext: bool = False,
) -> dict[str, Any]:
    react_screens = _compile_react_screens(
        payload,
        output_dir,
        design_path=design_path,
        root=root,
        strict_design=strict_design,
        screen_proof_dir=screen_proof_dir or output_dir,
    )
    state_artifacts = _write_state_artifacts(
        payload,
        output_dir / "src",
        generate_reducer=generate_reducer,
        check_conformance=check_conformance,
        build_manifest=build_manifest,
    )
    if state_artifacts is None:
        reducer_path = output_dir / "src" / "state_reducer.ts"
        atomic_write(reducer_path, _empty_reducer_source())
    else:
        reducer_path = state_artifacts["reducer_path"]
    numeric_scope = numeric_scope_for_app(payload)
    numeric_path: Path | None = None
    if numeric_scope["status"] == "applicable":
        numeric_path = output_dir / NUMERIC_KERNEL_PATH
        atomic_write(numeric_path, generate_numeric_typescript(numeric_scope))

    pretext_scope = build_pretext_scope(payload, react_screens, output_dir) if pretext else None
    pretext_enabled = bool(pretext_scope and pretext_scope.get("status") == "applicable")
    pretext_runtime_path: Path | None = None
    if pretext_enabled:
        pretext_runtime_path = output_dir / PRETEXT_RUNTIME_PATH
        atomic_write(pretext_runtime_path, generate_pretext_runtime_typescript())

    _write_runtime_template(
        payload,
        output_dir,
        freerange=freerange and numeric_scope["status"] == "applicable",
        pretext=pretext_enabled,
        pretext_scope=pretext_scope,
    )
    app_path = output_dir / REACT_APP_ENTRY
    app_source = _react_app_source(payload)
    if len(app_source.encode("utf-8")) > REACT_APP_MAX_SOURCE_BYTES:
        raise AppBundleProofFailure(
            "APP_REACT_SOURCE_TOO_LARGE",
            f"Generated ViewSpecApp.tsx exceeds {REACT_APP_MAX_SOURCE_BYTES} bytes.",
            "Reduce routes, screens, resources, or state declarations and retry compile-app.",
        )
    atomic_write(app_path, app_source)

    manifest_path = output_dir / REACT_APP_MANIFEST
    diagnostics_path = output_dir / REACT_APP_DIAGNOSTICS
    manifest = _react_app_manifest(
        payload,
        output_dir,
        react_screens,
        state_artifacts,
        numeric_path=numeric_path,
        numeric_scope=numeric_scope,
        pretext_runtime_path=pretext_runtime_path,
        pretext_scope=pretext_scope,
    )
    atomic_write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    manifest_hash = file_hash(manifest_path)
    diagnostics = {
        "schema_version": 1,
        "app_schema_version": _app_schema_version(payload),
        "ok": True,
        "target": REACT_APP_TARGET,
        "route_navigation": REACT_APP_ROUTE_NAVIGATION,
        "manifest_hash": manifest_hash,
        "screen_count": len(react_screens),
        "state_reducer_conformance": state_artifacts.get("conformance") if state_artifacts else None,
    }
    atomic_write(diagnostics_path, json.dumps(diagnostics, indent=2, sort_keys=True) + "\n")

    binding_fields = _resource_binding_report_fields(payload, resource_binding_report)
    paths = _react_app_paths(
        output_dir,
        reducer_path,
        state_artifacts,
        numeric_path=numeric_path,
        pretext_runtime_path=pretext_runtime_path,
    )
    route_assertions = {
        **_route_assertions(payload),
        "browser_history_navigation": True,
        "unknown_route_fallback": True,
    }
    return {
        "schema_version": 1,
        "app_schema_version": _app_schema_version(payload),
        "ok": True,
        "target": REACT_APP_TARGET,
        "route_navigation": REACT_APP_ROUTE_NAVIGATION,
        **binding_fields,
        "policy": {"network_calls": "host_owned", "package_install": "user_initiated"},
        "runtime": {
            "resource_binding": "host_props_with_fixture_fallback_v1",
            "state": "generated_reducer_v1",
            "selectors": "generated_selectors_v1",
            "visibility": "generated_visibility_v1",
            "side_effects": "typed_callbacks_v1",
            **({"text_layout": PRETEXT_PROFILE} if pretext_enabled else {}),
        },
        "app": _app_summary(payload),
        "paths": paths,
        "route_assertions": route_assertions,
        "app_artifact_hash": file_hash(app_path),
        "manifest_hash": manifest_hash,
        "diagnostics_hash": file_hash(diagnostics_path),
        **_state_report_fields(state_artifacts),
        "screens": react_screens,
        "source_screen_proofs": [
            {
                "id": report.get("id"),
                "artifact_hash": report.get("artifact_hash"),
                "manifest_hash": report.get("manifest_hash"),
                "check_status": report.get("check_status"),
            }
            for report in screen_reports
        ],
        "validation": _validation_summary(validation),
        "metadata": {
            "sdk_version": __version__,
            "strict_design": bool(strict_design),
            "screen_source": "embedded_intents",
            "app_kind": "runnable_vite_react_tailwind",
        },
        "next_actions": ["npm ci", "npm run dev"],
        "errors": [],
    }


def _compile_react_screens(
    payload: dict[str, Any],
    output_dir: Path,
    *,
    design_path: Path | None,
    root: Path,
    strict_design: bool,
    screen_proof_dir: Path,
) -> list[dict[str, Any]]:
    overlays = _visibility_overlays_for(payload)
    reports: list[dict[str, Any]] = []
    for screen in payload.get("screens", []):
        screen_id = str(screen["id"])
        intent_path = screen_proof_dir / "screens" / screen_id / "viewspec.intent.json"
        artifact_dir = output_dir / "src" / "screens" / screen_id
        compiled = _compile_screen(
            intent_path,
            artifact_dir,
            design_path=design_path,
            strict_design=strict_design,
            target="react-tailwind-tsx",
            root=root,
            ir_props_overlay=overlays.get(screen_id),
        )
        if not compiled.get("ok"):
            errors = compiled.get("errors") if isinstance(compiled.get("errors"), list) else []
            detail = errors[0].get("message") if errors and isinstance(errors[0], dict) else "screen compile failed"
            raise AppBundleProofFailure(
                "APP_REACT_SCREEN_COMPILE_FAILED",
                f"React screen {screen_id} failed: {detail}.",
                "Fix the embedded screen IntentBundle and retry compile-app.",
            )
        checked = check_artifact_dir(artifact_dir)
        if not checked.get("ok"):
            raise AppBundleProofFailure(
                "APP_REACT_SCREEN_CHECK_FAILED",
                f"React screen {screen_id} failed artifact checking: {checked.get('errors')}.",
                "Regenerate the screen from a valid embedded IntentBundle.",
            )
        tsx_path = artifact_dir / "ViewSpecView.tsx"
        manifest_path = artifact_dir / "provenance_manifest.json"
        reports.append(
            {
                "id": screen_id,
                "title": screen.get("title"),
                "compile_status": "passed",
                "check_status": "passed",
                "artifact_hash": file_hash(tsx_path),
                "manifest_hash": file_hash(manifest_path),
                "paths": {
                    "tsx": str(tsx_path),
                    "manifest": str(manifest_path),
                    "diagnostics": str(artifact_dir / "diagnostics.json"),
                },
                "errors": [],
            }
        )
    return reports


def _write_runtime_template(
    payload: dict[str, Any],
    output_dir: Path,
    *,
    freerange: bool = False,
    pretext: bool = False,
    pretext_scope: dict[str, Any] | None = None,
) -> None:
    package_json = json.loads(_template_text("package.json"))
    package_lock = json.loads(_template_text("package-lock.json"))
    package_name = _package_name(str(payload["app"]["id"]))
    package_json["name"] = package_name
    package_json["scripts"] = {
        "dev": "vite",
        "typecheck": "tsc --noEmit",
        "build": "vite build",
        "preview": "vite preview",
        "viewspec:verify": (
            "playwright test --reporter=line --grep-invert Pretext" if pretext else "playwright test --reporter=line"
        ),
        **({"viewspec:verify-pretext": "playwright test --reporter=line --grep Pretext"} if pretext else {}),
    }
    if freerange:
        package_json["devDependencies"]["@chenglou/freerange"] = "0.0.1"
        package_lock["packages"][""]["devDependencies"]["@chenglou/freerange"] = "0.0.1"
        package_lock["packages"]["node_modules/@chenglou/freerange"] = {
            "version": "0.0.1",
            "resolved": "https://registry.npmjs.org/@chenglou/freerange/-/freerange-0.0.1.tgz",
            "integrity": "sha512-RCdvTZX66Dp5roRrld+2GH4tJV+uyo21nEsF/lxwDBjzDFagG9CnJ7go5Qim2ZDHTC40lQWNF1AprDxTDQTxfg==",
            "dev": True,
            "license": "MIT",
            "dependencies": {"typescript": "^6.0.2"},
            "bin": {"fr": "fr.ts"},
        }
    if pretext:
        package_json["dependencies"][PRETEXT_PACKAGE] = PRETEXT_VERSION
        package_lock["packages"][""]["dependencies"][PRETEXT_PACKAGE] = PRETEXT_VERSION
        package_lock["packages"][f"node_modules/{PRETEXT_PACKAGE}"] = {
            "version": PRETEXT_VERSION,
            "resolved": PRETEXT_NPM_RESOLVED,
            "integrity": PRETEXT_NPM_INTEGRITY,
            "license": "MIT",
        }
    package_lock["name"] = package_name
    package_lock["packages"][""]["name"] = package_name
    atomic_write(output_dir / "package.json", json.dumps(package_json, indent=2) + "\n")
    atomic_write(output_dir / "package-lock.json", json.dumps(package_lock, indent=2) + "\n")
    atomic_write(output_dir / "tsconfig.json", _template_text("tsconfig.json"))
    atomic_write(output_dir / "vite.config.ts", _vite_config_source())
    atomic_write(output_dir / "playwright.config.ts", _playwright_config_source())
    atomic_write(output_dir / "index.html", _index_html(str(payload["app"]["title"])))
    atomic_write(output_dir / "src" / "main.tsx", _main_source(pretext=pretext))
    atomic_write(output_dir / "src" / "vite-env.d.ts", _template_text("src/vite-env.d.ts"))
    atomic_write(output_dir / "src" / "index.css", _styles_source(pretext=pretext))
    atomic_write(
        output_dir / "tests" / "viewspec-app.spec.ts",
        _playwright_test_source(payload, pretext_scope=pretext_scope if pretext else None),
    )


def _react_app_source(payload: dict[str, Any]) -> str:
    screens = payload.get("screens", [])
    imports = [
        f'import Screen{index} from "./screens/{screen["id"]}/ViewSpecView";' for index, screen in enumerate(screens)
    ]
    schema_version = _app_schema_version(payload) or 1
    state_imports = "initialState, reduceViewSpecState, selectViewSpecState"
    if schema_version == 4:
        state_imports += ", evaluateViewSpecVisibility"
    fixture_resources = _fixture_resources(payload)
    resource_bindings = _resource_bindings(payload)
    state_sources = _state_resource_sources(payload)
    triggers = _mutation_triggers(payload)
    routes = [
        {
            "id": route["id"],
            "path": route["path"],
            "label": route["label"],
            "screenId": route["screen_id"],
        }
        for route in payload.get("routes", [])
    ]
    visibility_eval = (
        "const evaluateRuntimeVisibility = evaluateViewSpecVisibility;"
        if schema_version == 4
        else "const evaluateRuntimeVisibility = (_state: ViewSpecState): Record<string, boolean> => ({});"
    )
    screen_renderers = _screen_renderers(payload)
    return "\n".join(
        [
            '"use client";',
            "// Generated by ViewSpec. Do not edit.",
            "",
            'import * as React from "react";',
            *imports,
            f'import {{ {state_imports} }} from "./state_reducer";',
            "",
            "export type ViewSpecState = Record<string, unknown>;",
            "export type ViewSpecResourceRecord = Record<string, unknown> & { id: string };",
            "export type ViewSpecResources = Record<string, readonly ViewSpecResourceRecord[]>;",
            "type ViewSpecResourceBinding = {",
            "  screenId: string; bindingId: string; resourceId: string; recordId: string; field: string; stateId: string;",
            "};",
            "type ViewSpecStateResourceSource = {",
            "  stateId: string; screenId: string; viewId: string; resourceId: string;",
            "  recordIds: readonly string[]; fields: readonly string[];",
            "};",
            "export type ViewSpecActionIntent = {",
            "  schemaVersion: 1;",
            "  source: string;",
            "  id: string;",
            "  kind: string;",
            "  targetRef: string;",
            "  payloadBindings: string[];",
            "  payloadValues: Record<string, unknown>;",
            "};",
            "export type ViewSpecAppAction = ViewSpecActionIntent & { screenId: string; mutationIds: string[] };",
            "export type ViewSpecStateSnapshot = {",
            "  state: ViewSpecState;",
            "  selectors: Record<string, unknown>;",
            "  visibility: Record<string, boolean>;",
            "};",
            "export type ViewSpecAppProps = {",
            "  resources?: ViewSpecResources;",
            "  initialPath?: string;",
            "  onNavigate?: (path: string) => void;",
            "  onAction?: (action: ViewSpecAppAction) => void;",
            "  onStateChange?: (snapshot: ViewSpecStateSnapshot) => void;",
            "  onError?: (error: Error) => void;",
            "};",
            "",
            f"const routes = {_safe_json(routes)} as const;",
            f"const fixtureResources = {_safe_json(fixture_resources)} as ViewSpecResources;",
            f"const resourceBindings: readonly ViewSpecResourceBinding[] = {_safe_json(resource_bindings)};",
            f"const stateResourceSources: readonly ViewSpecStateResourceSource[] = {_safe_json(state_sources)};",
            f"const mutationTriggers = {_safe_json(triggers)} as Record<string, string[]>;",
            visibility_eval,
            "",
            "const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;",
            "const currentBrowserPath = (): string =>",
            '  typeof window === "undefined" ? "/" : window.location.pathname;',
            "",
            "function mergeResources(resources: ViewSpecResources | undefined): ViewSpecResources {",
            "  return { ...fixtureResources, ...(resources ?? {}) };",
            "}",
            "",
            "function recordsForSource(",
            "  source: ViewSpecStateResourceSource,",
            "  resources: ViewSpecResources,",
            "): ViewSpecResourceRecord[] {",
            "  const records = resources[source.resourceId] ?? [];",
            "  return source.recordIds.flatMap((recordId) => {",
            "    const record = records.find((candidate) => String(candidate.id) === recordId);",
            "    if (!record) return [];",
            "    const projected: Record<string, unknown> = { id: recordId };",
            "    source.fields.forEach((field) => { projected[field] = record[field]; });",
            "    return [projected as ViewSpecResourceRecord];",
            "  });",
            "}",
            "",
            "export function createInitialState(resources: ViewSpecResources = fixtureResources): ViewSpecState {",
            "  const state = clone(initialState) as ViewSpecState;",
            "  stateResourceSources.forEach((source) => {",
            "    state[source.stateId] = recordsForSource(source, resources);",
            "  });",
            "  return state;",
            "}",
            "",
            "export function ViewSpecApp({",
            "  resources,",
            "  initialPath,",
            "  onNavigate,",
            "  onAction,",
            "  onStateChange,",
            "  onError,",
            "}: ViewSpecAppProps) {",
            "  const mergedResources = React.useMemo(() => mergeResources(resources), [resources]);",
            "  const [path, setPath] = React.useState(() => initialPath ?? currentBrowserPath());",
            "  const [state, setState] = React.useState<ViewSpecState>(() => createInitialState(mergedResources));",
            "  const [runtimeError, setRuntimeError] = React.useState<Error | null>(null);",
            "  const selectors = React.useMemo(() => selectViewSpecState(state), [state]);",
            "  const visibility = React.useMemo(() => evaluateRuntimeVisibility(state), [state]);",
            "  const activeRoute = routes.find((route) => route.path === path);",
            "",
            "  React.useEffect(() => {",
            '    if (typeof window === "undefined") return undefined;',
            "    const syncPath = () => setPath(window.location.pathname);",
            '    window.addEventListener("popstate", syncPath);',
            '    return () => window.removeEventListener("popstate", syncPath);',
            "  }, []);",
            "",
            "  React.useEffect(() => {",
            "    onStateChange?.({ state, selectors, visibility });",
            "  }, [onStateChange, selectors, state, visibility]);",
            "",
            "  const navigate = (nextPath: string) => {",
            '    if (typeof window !== "undefined" && window.location.pathname !== nextPath) {',
            '      window.history.pushState({}, "", nextPath);',
            "    }",
            "    setPath(nextPath);",
            "    onNavigate?.(nextPath);",
            "  };",
            "",
            "  const screenData = (screenId: string): Record<string, unknown> => {",
            "    const data: Record<string, unknown> = {};",
            "    resourceBindings.forEach((binding) => {",
            "      if (binding.screenId !== screenId) return;",
            "      const stateRecords = binding.stateId ? state[binding.stateId] : undefined;",
            "      const records = Array.isArray(stateRecords)",
            "        ? stateRecords",
            "        : (mergedResources[binding.resourceId] ?? []);",
            "      const record = records.find((candidate) =>",
            '        candidate && typeof candidate === "object" && String(candidate.id) === binding.recordId',
            "      );",
            '      if (record && typeof record === "object") data[binding.bindingId] = record[binding.field];',
            "    });",
            "    return data;",
            "  };",
            "",
            "  const handleAction = (screenId: string, intent: ViewSpecActionIntent) => {",
            "    const mutationIds = mutationTriggers[`${screenId}::${intent.id}`] ?? [];",
            "    let nextState = state;",
            "    for (const mutationId of mutationIds) {",
            "      try {",
            "        nextState = reduceViewSpecState(nextState, {",
            "          mutation_id: mutationId,",
            "          payload_values: intent.payloadValues,",
            "        }) as ViewSpecState;",
            "      } catch (caught) {",
            '        const error = caught instanceof Error ? caught : new Error("ViewSpec state mutation failed");',
            "        setRuntimeError(error);",
            "        onError?.(error);",
            "        break;",
            "      }",
            "    }",
            "    if (nextState !== state) setState(nextState);",
            "    onAction?.({ ...intent, screenId, mutationIds });",
            "  };",
            "",
            "  const renderScreen = () => {",
            *screen_renderers,
            "    return (",
            '      <section className="vs-app-not-found" data-viewspec-app-not-found>',
            "        <h1>Page not found</h1>",
            "        <p>No ViewSpec route matches {path}.</p>",
            "      </section>",
            "    );",
            "  };",
            "",
            "  return (",
            '    <div className="vs-app-shell">',
            '      <header className="vs-app-header">',
            f"        <strong>{{{_safe_json(str(payload['app']['title']))}}}</strong>",
            '        <nav aria-label="Application">',
            "          {routes.map((route) => (",
            "            <button",
            "              key={route.id}",
            '              type="button"',
            '              aria-current={activeRoute?.id === route.id ? "page" : undefined}',
            "              onClick={() => navigate(route.path)}",
            "            >",
            "              {route.label}",
            "            </button>",
            "          ))}",
            "        </nav>",
            "      </header>",
            '      {runtimeError ? <p role="alert" className="vs-app-error">{runtimeError.message}</p> : null}',
            '      <main className="vs-app-main">{renderScreen()}</main>',
            "    </div>",
            "  );",
            "}",
            "",
            "export default ViewSpecApp;",
            "",
        ]
    )


def _screen_renderers(payload: dict[str, Any]) -> list[str]:
    visibility_screens = {
        rule.get("screen_id")
        for rule in payload.get("visibility", [])
        if isinstance(rule, dict) and isinstance(rule.get("screen_id"), str)
    }
    lines: list[str] = []
    for index, screen in enumerate(payload.get("screens", [])):
        screen_id = str(screen["id"])
        visibility_prop = " visibility={visibility}" if screen_id in visibility_screens else ""
        lines.extend(
            [
                f"    if (activeRoute?.screenId === {_safe_json(screen_id)}) {{",
                "      return (",
                f'        <section data-viewspec-app-screen="{screen_id}">',
                f"          <Screen{index}",
                f"            data={{screenData({_safe_json(screen_id)})}}",
                f"            onAction={{(intent) => handleAction({_safe_json(screen_id)}, intent)}}",
                f"           {visibility_prop}",
                "          />",
                "        </section>",
                "      );",
                "    }",
            ]
        )
    return lines


def _fixture_resources(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        str(resource["id"]): list(resource.get("records", []))
        for resource in payload.get("resources", [])
        if isinstance(resource, dict)
        and resource.get("kind") == "fixture"
        and isinstance(resource.get("records"), list)
    }


def _resource_bindings(payload: dict[str, Any]) -> list[dict[str, str]]:
    state_sources = {
        (str(source["screenId"]), str(source["viewId"])): str(source["stateId"])
        for source in _state_resource_sources(payload)
    }
    bindings: list[dict[str, str]] = []
    for screen in payload.get("screens", []):
        screen_id = str(screen["id"])
        intent = screen.get("intent_bundle", {})
        intent_bindings = intent.get("view_spec", {}).get("bindings", [])
        by_address = {
            str(binding.get("address")): str(binding.get("id"))
            for binding in intent_bindings
            if isinstance(binding, dict)
            and isinstance(binding.get("address"), str)
            and isinstance(binding.get("id"), str)
        }
        for view in screen.get("resource_views", []):
            if not isinstance(view, dict):
                continue
            state_id = state_sources.get((screen_id, str(view.get("id"))))
            for record_id in view.get("record_ids", []):
                for field in view.get("fields", []):
                    address = f"node:{record_id}#attr:{field}"
                    binding_id = by_address.get(address)
                    if binding_id is None:
                        continue
                    bindings.append(
                        {
                            "screenId": screen_id,
                            "bindingId": binding_id,
                            "resourceId": str(view.get("resource_id")),
                            "recordId": str(record_id),
                            "field": str(field),
                            "stateId": state_id or "",
                        }
                    )
    return bindings


def _state_resource_sources(payload: dict[str, Any]) -> list[dict[str, Any]]:
    views: dict[tuple[str, str], dict[str, Any]] = {}
    for screen in payload.get("screens", []):
        for view in screen.get("resource_views", []):
            if isinstance(view, dict):
                views[(str(screen["id"]), str(view.get("id")))] = view
    sources: list[dict[str, Any]] = []
    for state in payload.get("state", []):
        if not isinstance(state, dict):
            continue
        initial = state.get("initial")
        ref = initial.get("from_resource_view") if isinstance(initial, dict) else None
        if not isinstance(ref, dict):
            continue
        screen_id = str(ref.get("screen_id"))
        view_id = str(ref.get("view_id"))
        view = views.get((screen_id, view_id))
        if view is None:
            continue
        sources.append(
            {
                "stateId": str(state["id"]),
                "screenId": screen_id,
                "viewId": view_id,
                "resourceId": str(view.get("resource_id")),
                "recordIds": [str(item) for item in view.get("record_ids", [])],
                "fields": [str(item) for item in view.get("fields", [])],
            }
        )
    return sources


def _mutation_triggers(payload: dict[str, Any]) -> dict[str, list[str]]:
    triggers: dict[str, list[str]] = {}
    for mutation in payload.get("mutations", []):
        if not isinstance(mutation, dict) or not isinstance(mutation.get("trigger"), dict):
            continue
        trigger = mutation["trigger"]
        key = f"{trigger.get('screen_id')}::{trigger.get('action_id')}"
        triggers.setdefault(key, []).append(str(mutation.get("id")))
    return triggers


def _react_app_manifest(
    payload: dict[str, Any],
    output_dir: Path,
    react_screens: list[dict[str, Any]],
    state_artifacts: dict[str, Any] | None,
    *,
    numeric_path: Path | None,
    numeric_scope: dict[str, Any],
    pretext_runtime_path: Path | None,
    pretext_scope: dict[str, Any] | None,
) -> dict[str, Any]:
    file_paths = [
        output_dir / "index.html",
        output_dir / "package.json",
        output_dir / "package-lock.json",
        output_dir / "vite.config.ts",
        output_dir / "playwright.config.ts",
        output_dir / "tsconfig.json",
        output_dir / "src" / "main.tsx",
        output_dir / "src" / "vite-env.d.ts",
        output_dir / "src" / "index.css",
        output_dir / REACT_APP_ENTRY,
        output_dir / "src" / "state_reducer.ts",
        output_dir / "tests" / "viewspec-app.spec.ts",
    ]
    if numeric_path is not None:
        file_paths.append(numeric_path)
    if pretext_runtime_path is not None:
        file_paths.append(pretext_runtime_path)
    if state_artifacts is not None:
        file_paths.append(Path(state_artifacts["manifest_path"]))
    reducer_path = output_dir / "src" / "state_reducer.ts"
    numeric_analysis = dict(numeric_scope)
    if numeric_path is not None:
        numeric_analysis.update(
            {
                "files": [
                    {
                        "path": str(numeric_path.relative_to(output_dir)),
                        "sha256": file_hash(numeric_path),
                        "required_functions": list(numeric_scope["required_functions"]),
                        "function_sha256": numeric_function_hashes(numeric_scope),
                        "allowed_requires": dict(numeric_scope["allowed_requires"]),
                        "required_ensures": dict(numeric_scope["required_ensures"]),
                    }
                ],
                "call_sites": [
                    {
                        "path": str(reducer_path.relative_to(output_dir)),
                        "sha256": file_hash(reducer_path),
                        "required_functions": list(numeric_scope["required_functions"]),
                        "connection": "generated_import_and_call_v1",
                    }
                ],
            }
        )
    return {
        "schema_version": 1,
        "app_schema_version": _app_schema_version(payload),
        "sdk_version": __version__,
        "target": REACT_APP_TARGET,
        "route_navigation": REACT_APP_ROUTE_NAVIGATION,
        "entry_file": REACT_APP_ENTRY,
        "route_count": len(payload.get("routes", [])),
        "screen_count": len(react_screens),
        "numeric_analysis": numeric_analysis,
        **({"text_layout_analysis": pretext_scope} if pretext_scope is not None else {}),
        **(
            {
                "text_layout_engine": {
                    "package": PRETEXT_PACKAGE,
                    "version": PRETEXT_VERSION,
                    "resolved": PRETEXT_NPM_RESOLVED,
                    "integrity": PRETEXT_NPM_INTEGRITY,
                    "package_tree": dict(PRETEXT_PACKAGE_TREE),
                    "license": "MIT",
                    "font_family": "Arial",
                    "runtime_path": str(pretext_runtime_path.relative_to(output_dir)),
                    "runtime_sha256": file_hash(pretext_runtime_path),
                }
            }
            if pretext_runtime_path is not None
            else {}
        ),
        "screen_artifacts": [
            {
                "id": report["id"],
                "tsx": str(Path(report["paths"]["tsx"]).relative_to(output_dir)),
                "manifest": str(Path(report["paths"]["manifest"]).relative_to(output_dir)),
                "artifact_hash": report["artifact_hash"],
                "manifest_hash": report["manifest_hash"],
            }
            for report in react_screens
        ],
        "state_contract_hash": state_artifacts.get("contract_hash") if state_artifacts else None,
        "runtime": {
            "resource_binding": "host_props_with_fixture_fallback_v1",
            "state": "generated_reducer_v1",
            "selectors": "generated_selectors_v1",
            "visibility": "generated_visibility_v1",
            "side_effects": "typed_callbacks_v1",
            **({"text_layout": PRETEXT_PROFILE} if pretext_runtime_path is not None else {}),
        },
        "files": [{"path": str(path.relative_to(output_dir)), "sha256": file_hash(path)} for path in file_paths],
    }


def _react_app_paths(
    output_dir: Path,
    reducer_path: Path,
    state_artifacts: dict[str, Any] | None,
    *,
    numeric_path: Path | None,
    pretext_runtime_path: Path | None,
) -> dict[str, str]:
    paths = {
        "output_dir": str(output_dir),
        "index": str(output_dir / "index.html"),
        "package_json": str(output_dir / "package.json"),
        "package_lock": str(output_dir / "package-lock.json"),
        "vite_config": str(output_dir / "vite.config.ts"),
        "playwright_config": str(output_dir / "playwright.config.ts"),
        "tsconfig": str(output_dir / "tsconfig.json"),
        "main": str(output_dir / "src" / "main.tsx"),
        "vite_env": str(output_dir / "src" / "vite-env.d.ts"),
        "app": str(output_dir / REACT_APP_ENTRY),
        "styles": str(output_dir / "src" / "index.css"),
        "runtime_test": str(output_dir / "tests" / "viewspec-app.spec.ts"),
        "manifest": str(output_dir / REACT_APP_MANIFEST),
        "diagnostics": str(output_dir / REACT_APP_DIAGNOSTICS),
        "state_reducer": str(reducer_path),
    }
    if state_artifacts is not None:
        paths["state_manifest"] = str(state_artifacts["manifest_path"])
    if numeric_path is not None:
        paths["numeric_kernel"] = str(numeric_path)
    if pretext_runtime_path is not None:
        paths["pretext_runtime"] = str(pretext_runtime_path)
    return paths


def _state_report_fields(state_artifacts: dict[str, Any] | None) -> dict[str, Any]:
    if state_artifacts is None:
        return {}
    return {
        "state_reducer_hash": state_artifacts["reducer_hash"],
        "state_manifest_hash": state_artifacts["manifest_hash"],
        "state_contract_hash": state_artifacts["contract_hash"],
        "state_replay": state_artifacts["replay"],
        "state_reducer_conformance": state_artifacts["conformance"],
    }


def _empty_reducer_source() -> str:
    return """// Generated by ViewSpec. Do not edit.
export type ViewSpecState = Record<string, unknown>;
export type ViewSpecStateEvent = { mutation_id: string; payload_values?: Record<string, unknown> };
export const initialState: ViewSpecState = {};
export function reduceViewSpecState(state: ViewSpecState, _event: ViewSpecStateEvent): ViewSpecState {
  return { ...state };
}
export function selectViewSpecState(_state: ViewSpecState): Record<string, unknown> { return {}; }
"""


def _template_text(name: str) -> str:
    return resources.files(REACT_APP_TEMPLATE_PACKAGE).joinpath(*name.split("/")).read_text(encoding="utf-8")


def _package_name(app_id: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", app_id.lower().replace("_", "-")).strip("-")
    return f"viewspec-{normalized or 'app'}"


def _vite_config_source() -> str:
    return """import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
});
"""


def _playwright_config_source() -> str:
    return """import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  workers: 1,
  use: { baseURL: "http://127.0.0.1:4178" },
  webServer: {
    command: "npm run preview -- --host 127.0.0.1 --port 4178 --strictPort",
    url: "http://127.0.0.1:4178",
    reuseExistingServer: false,
    timeout: 20000,
  },
});
"""


def _playwright_test_source(
    payload: dict[str, Any],
    *,
    pretext_scope: dict[str, Any] | None = None,
) -> str:
    routes = [
        {
            "id": route["id"],
            "path": route["path"],
            "label": route["label"],
            "screenId": route["screen_id"],
        }
        for route in payload.get("routes", [])
    ]
    proof_cases = _runtime_proof_cases(payload)
    runtime_report = {
        "route_count": len(routes),
        "history_assertion_count": 1 if len(routes) > 1 else 0,
        "unknown_route_assertion_count": 1,
        "state_action_count": sum(len(case["events"]) for case in proof_cases),
        "rebound_binding_count": sum(len(case["bindings"]) for case in proof_cases),
        "selector_assertion_count": sum(case["selectorAssertionCount"] for case in proof_cases),
        "visibility_assertion_count": sum(len(case["visibility"]) for case in proof_cases),
    }
    pretext_enabled = bool(pretext_scope and pretext_scope.get("status") == "applicable")
    lines = [
        'import { expect, test, type Page } from "@playwright/test";',
        'import { writeFile } from "node:fs/promises";',
        "",
        "type RuntimeProofEvent = { screenId: string; actionId: string; payloadValues: Record<string, unknown> };",
        "type RuntimeBindingAssertion = { screenId: string; bindingId: string; text: string };",
        "type RuntimeVisibilityAssertion = { screenId: string; ruleId: string; visible: boolean };",
        "type RuntimeProofCase = {",
        "  id: string; events: readonly RuntimeProofEvent[]; bindings: readonly RuntimeBindingAssertion[];",
        "  selectorAssertionCount: number; visibility: readonly RuntimeVisibilityAssertion[];",
        "};",
        "",
        f"const routes = {_safe_json(routes)} as const;",
        f"const proofCases: readonly RuntimeProofCase[] = {_safe_json(proof_cases)};",
        f"const runtimeReport = {_safe_json(runtime_report)};",
        "",
        "const routeForScreen = (screenId: string) => routes.find((route) => route.screenId === screenId);",
        "",
        "async function navigateToScreen(page: Page, screenId: string) {",
        "  const route = routeForScreen(screenId);",
        "  if (!route) throw new Error(`APP_REACT_VERIFY_ROUTE_MISSING:${screenId}`);",
        "  const current = await page.locator('[data-viewspec-app-screen]').getAttribute('data-viewspec-app-screen');",
        "  if (current !== screenId) await page.getByRole('navigation').getByRole('button', { name: route.label }).click();",
        '  await expect(page.locator(`[data-viewspec-app-screen="${screenId}"]`)).toBeVisible();',
        "}",
        "",
    ]
    if pretext_enabled:
        lines.extend(
            [
                "type PretextCache = { prepare_calls: number; unique_inputs: number; layout_calls: number; cache_hits: number };",
                "type PretextProbeResult = {",
                "  engine: Record<string, unknown>; environment: Record<string, unknown>;",
                "  items: Array<Record<string, unknown>>; cache: PretextCache; errors: Array<Record<string, unknown>>;",
                "};",
                f"const pretextScope = {_safe_json(pretext_scope)} as const;",
                "const pretextItems: Array<Record<string, unknown>> = [];",
                "const pretextErrors: Array<Record<string, unknown>> = [];",
                "const pretextCache: PretextCache = { prepare_calls: 0, unique_inputs: 0, layout_calls: 0, cache_hits: 0 };",
                "let pretextEnvironment: Record<string, unknown> = {};",
                "let pretextEngine: Record<string, unknown> = {",
                f'  name: "pretext", package: "{PRETEXT_PACKAGE}", version: "{PRETEXT_VERSION}"',
                "};",
                "",
                "function pretextSummary() {",
                "  const statusCount = (status: string) => pretextItems.filter((item) => item.status === status).length;",
                "  return {",
                "    required: pretextScope.required_observation_count, accounted: pretextItems.length,",
                "    measured: statusCount('passed'), hidden: statusCount('hidden'),",
                "    unsupported: statusCount('unsupported'), failed: statusCount('failed'),",
                "  };",
                "}",
                "",
            ]
        )
    lines.extend(
        [
            "test.afterAll(async () => {",
            '  await writeFile("viewspec_runtime_report.json", JSON.stringify(runtimeReport, null, 2));',
        ]
    )
    if pretext_enabled:
        lines.extend(
            [
                '  await writeFile("viewspec_pretext_report.json", JSON.stringify({',
                "    schema_version: 1, engine: pretextEngine, profile: pretextScope.profile, protocol: pretextScope.protocol,",
                "    environment: pretextEnvironment, viewports: pretextScope.viewports, items: pretextItems,",
                "    summary: pretextSummary(), cache: pretextCache, errors: pretextErrors,",
                "  }, null, 2));",
            ]
        )
    lines.extend(
        [
            "});",
            "",
            'test("browser history, routes, and unknown path", async ({ page }) => {',
            "  for (const route of routes) {",
            "    await page.goto(route.path);",
            '    await expect(page.locator(`[data-viewspec-app-screen="${route.screenId}"]`)).toBeVisible();',
            "  }",
        ]
    )
    if len(routes) > 1:
        lines.extend(
            [
                "  await page.goto(routes[0].path);",
                "  await page.getByRole('navigation').getByRole('button', { name: routes[1].label }).click();",
                "  await expect(page).toHaveURL(new RegExp(`${routes[1].path}$`));",
                "  await page.goBack();",
                "  await expect(page).toHaveURL(new RegExp(`${routes[0].path}$`));",
                '  await expect(page.locator(`[data-viewspec-app-screen="${routes[0].screenId}"]`)).toBeVisible();',
            ]
        )
    lines.extend(
        [
            '  await page.goto("/__viewspec_missing__");',
            "  await expect(page.locator('[data-viewspec-app-not-found]')).toBeVisible();",
            "});",
        ]
    )
    if proof_cases:
        lines.extend(
            [
                "",
                'test("state actions rebind data and visibility", async ({ page }) => {',
                "  for (const proofCase of proofCases) {",
                "    await page.goto(routes[0].path);",
                "    for (const event of proofCase.events) {",
                "      await navigateToScreen(page, event.screenId);",
                "      for (const [bindingId, value] of Object.entries(event.payloadValues)) {",
                '        const input = page.locator(`input[data-binding-id="${bindingId}"]`);',
                "        if (await input.count()) await input.fill(String(value ?? ''));",
                "      }",
                '      await page.locator(`[data-action-id="${event.actionId}"]`).click();',
                "    }",
                "    for (const assertion of proofCase.bindings) {",
                "      await navigateToScreen(page, assertion.screenId);",
                '      await expect(page.locator(`[data-binding-id="${assertion.bindingId}"]`)).toHaveText(assertion.text);',
                "    }",
                "    for (const assertion of proofCase.visibility) {",
                "      await navigateToScreen(page, assertion.screenId);",
                '      const target = page.locator(`[data-visibility-rule="${assertion.ruleId}"]`);',
                "      if (assertion.visible) await expect(target).toBeVisible();",
                "      else await expect(target).toBeHidden();",
                "    }",
                "  }",
                "});",
            ]
        )
    if pretext_enabled:
        lines.extend(
            [
                "",
                'test("Pretext native DOM text layout", async ({ page }) => {',
                "  let sawPretextResult = false;",
                "  let initializedPretextPage = false;",
                "  for (const screen of pretextScope.screens) {",
                "    for (const viewport of pretextScope.viewports) {",
                "      await page.setViewportSize({ width: viewport.width, height: viewport.height });",
                "      if (!initializedPretextPage) {",
                "        await page.goto(`${screen.route_path}?__viewspec_pretext=1`);",
                "        initializedPretextPage = true;",
                "      } else {",
                "        await navigateToScreen(page, screen.screen_id);",
                "      }",
                '      await expect(page.locator(`[data-viewspec-app-screen="${screen.screen_id}"]`)).toBeVisible();',
                "      const result = await page.evaluate(async (input) => {",
                "        const probe = window.__viewspecPretextProbe;",
                "        if (typeof probe !== 'function') throw new Error('APP_PRETEXT_ENGINE_MISSING');",
                "        return await probe({",
                "          screenId: input.screenId, routeId: input.routeId, viewportId: input.viewportId, surfaces: input.surfaces,",
                "        });",
                "      }, {",
                "        screenId: screen.screen_id, routeId: screen.route_id, viewportId: viewport.id, surfaces: screen.surfaces,",
                "      }) as PretextProbeResult;",
                "      pretextItems.push(...result.items);",
                "      pretextErrors.push(...result.errors);",
                "      pretextEnvironment = result.environment;",
                "      pretextEngine = result.engine;",
                "      pretextCache.prepare_calls = result.cache.prepare_calls;",
                "      pretextCache.unique_inputs = result.cache.unique_inputs;",
                "      pretextCache.layout_calls = result.cache.layout_calls;",
                "      pretextCache.cache_hits = result.cache.cache_hits;",
                "      sawPretextResult = true;",
                "    }",
                "  }",
                "  if (!sawPretextResult) throw new Error('APP_PRETEXT_COVERAGE_INCOMPLETE');",
                "});",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def _runtime_proof_cases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    mutations = {
        str(mutation["id"]): mutation
        for mutation in payload.get("mutations", [])
        if isinstance(mutation, dict) and isinstance(mutation.get("id"), str)
    }
    rules = {
        str(rule["id"]): rule
        for rule in payload.get("visibility", [])
        if isinstance(rule, dict) and isinstance(rule.get("id"), str)
    }
    resource_bindings = _resource_bindings(payload)
    cases: list[dict[str, Any]] = []
    for assertion in payload.get("state_replay_assertions", []):
        if not isinstance(assertion, dict):
            continue
        events: list[dict[str, Any]] = []
        for event in assertion.get("events", []):
            mutation = mutations.get(str(event.get("mutation_id"))) if isinstance(event, dict) else None
            trigger = mutation.get("trigger") if isinstance(mutation, dict) else None
            if not isinstance(trigger, dict):
                continue
            events.append(
                {
                    "screenId": str(trigger.get("screen_id")),
                    "actionId": str(trigger.get("action_id")),
                    "payloadValues": event.get("payload_values", {}),
                }
            )
        bindings = _runtime_binding_assertions(assertion.get("expect_state"), resource_bindings)
        visibility = [
            {
                "ruleId": str(rule_id),
                "screenId": str(rules[rule_id].get("screen_id")),
                "visible": bool(visible),
            }
            for rule_id, visible in assertion.get("expect_visibility", {}).items()
            if rule_id in rules and isinstance(visible, bool)
        ]
        cases.append(
            {
                "id": str(assertion.get("id")),
                "events": events,
                "bindings": bindings,
                "selectorAssertionCount": len(assertion.get("expect_selectors", {})),
                "visibility": visibility,
            }
        )
    return cases


def _runtime_binding_assertions(expect_state: object, resource_bindings: list[dict[str, str]]) -> list[dict[str, str]]:
    if not isinstance(expect_state, dict):
        return []
    assertions: list[dict[str, str]] = []
    for binding in resource_bindings:
        state_id = binding.get("stateId")
        records = expect_state.get(state_id) if state_id else None
        if not isinstance(records, list):
            continue
        record = next(
            (item for item in records if isinstance(item, dict) and str(item.get("id")) == binding.get("recordId")),
            None,
        )
        if not isinstance(record, dict) or binding.get("field") not in record:
            continue
        assertions.append(
            {
                "screenId": binding["screenId"],
                "bindingId": binding["bindingId"],
                "text": _runtime_text(record[binding["field"]]),
            }
        )
    return assertions


def _runtime_text(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _index_html(title: str) -> str:
    escaped = html.escape(title, quote=True)
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{escaped}</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
"""


def _main_source(*, pretext: bool = False) -> str:
    if not pretext:
        return """import * as React from "react";
import { createRoot } from "react-dom/client";
import ViewSpecApp from "./ViewSpecApp";
import "./index.css";

const root = document.getElementById("root");
if (!root) throw new Error("APP_REACT_MOUNT_MISSING: missing #root");

createRoot(root).render(
  <React.StrictMode>
    <ViewSpecApp />
  </React.StrictMode>,
);
"""

    return """import * as React from "react";
import { createRoot } from "react-dom/client";
import ViewSpecApp from "./ViewSpecApp";
import { installViewSpecPretextProbe } from "./viewspec_pretext";
import "./index.css";

if (new URLSearchParams(window.location.search).get("__viewspec_pretext") === "1") {
  installViewSpecPretextProbe();
}

const root = document.getElementById("root");
if (!root) throw new Error("APP_REACT_MOUNT_MISSING: missing #root");

createRoot(root).render(
  <React.StrictMode>
    <ViewSpecApp />
  </React.StrictMode>,
);
"""


def _styles_source(*, pretext: bool = False) -> str:
    source = """@import "tailwindcss";
@source "./**/*.tsx";

:root {
  color: #17211d;
  background: #f5f7f6;
  font-family: Inter, ui-sans-serif, system-ui, sans-serif;
}

* { box-sizing: border-box; }
html, body, #root { min-height: 100%; }
body { margin: 0; }
button, input { font: inherit; }

.vs-app-shell { min-height: 100vh; background: #f5f7f6; }
.vs-app-header {
  min-height: 64px;
  padding: 0 24px;
  border-bottom: 1px solid #cdd8d3;
  background: #ffffff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
}
.vs-app-header nav { display: flex; align-items: center; gap: 4px; }
.vs-app-header button {
  min-height: 36px;
  padding: 6px 10px;
  border: 1px solid transparent;
  background: transparent;
  color: #405049;
  cursor: pointer;
}
.vs-app-header button[aria-current="page"] {
  border-color: #9bb7ab;
  background: #e6f0ec;
  color: #0f503b;
}
.vs-app-main { width: min(100%, 1240px); margin: 0 auto; }
.vs-app-error { margin: 16px 24px 0; color: #991b1b; }
.vs-app-not-found { padding: 48px 24px; }

@media (max-width: 640px) {
  .vs-app-header { align-items: flex-start; flex-direction: column; gap: 10px; padding: 14px 16px; }
  .vs-app-header nav { width: 100%; overflow-x: auto; }
}
"""
    if not pretext:
        return source
    return source.replace(
        "font-family: Inter, ui-sans-serif, system-ui, sans-serif;",
        "font-family: Arial, sans-serif;",
    ).replace(
        "* { box-sizing: border-box; }",
        "* { box-sizing: border-box; }\n.vs-app-main [data-ir-id] { overflow-wrap: anywhere; }",
    )


def _safe_json(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


__all__ = [
    "REACT_APP_DIAGNOSTICS",
    "REACT_APP_ENTRY",
    "REACT_APP_MANIFEST",
    "REACT_APP_ROUTE_NAVIGATION",
    "REACT_APP_TARGET",
    "_write_react_app",
]
