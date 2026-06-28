"""AppBundle validation contract helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from viewspec.agent import SAFE_AGENT_ID_PATTERN
from viewspec.intent_tools import validate_intent_text
from viewspec.state_ir import (
    APP_STATE_MAX_ENTRIES,
    APP_STATE_MAX_EVENTS_PER_REPLAY,
    APP_STATE_MAX_MANIFEST_BYTES,
    APP_STATE_MAX_MUTATIONS,
    APP_STATE_MAX_OPS_PER_MUTATION,
    APP_STATE_MAX_REDUCER_BYTES,
    APP_STATE_MAX_REPLAY_ASSERTIONS,
    APP_STATE_MAX_SELECTOR_OPS,
    APP_STATE_MAX_SELECTORS,
    INTERACTIVE_STATE_PROFILE,
    state_ir_summary,
    validate_state_ir,
)

APP_BUNDLE_SCHEMA_VERSION = 1
APP_BUNDLE_BOUND_SCHEMA_VERSION = 2
APP_BUNDLE_STATE_SCHEMA_VERSION = 3
APP_BUNDLE_SUPPORTED_SCHEMA_VERSIONS = (
    APP_BUNDLE_SCHEMA_VERSION,
    APP_BUNDLE_BOUND_SCHEMA_VERSION,
    APP_BUNDLE_STATE_SCHEMA_VERSION,
)
APP_BUNDLE_RESULT_SCHEMA_VERSION = 1
APP_BUNDLE_RESOURCE_BINDING = "unbound_v0"
APP_BUNDLE_RESOURCE_BINDING_READONLY = "fixture_readonly_v0"
APP_BUNDLE_BINDING_SCOPE = "declared_resource_views_only"
APP_BUNDLE_MAX_BYTES = 1024 * 1024
APP_BUNDLE_MAX_SCREENS = 16
APP_BUNDLE_MAX_ROUTES = 32
APP_BUNDLE_MAX_RESOURCES = 8
APP_BUNDLE_MAX_RECORDS_PER_RESOURCE = 100
APP_BUNDLE_MAX_RECORD_FIELDS = 32
APP_BUNDLE_MAX_SCALAR_STRING_CHARS = 2048
APP_BUNDLE_MAX_EMBEDDED_INTENT_BYTES = 256 * 1024
APP_BUNDLE_MAX_AGGREGATE_INTENT_BYTES = 1024 * 1024
APP_BUNDLE_MAX_PROOF_REPORT_BYTES = 256 * 1024
APP_BUNDLE_MAX_SUPPORT_BUNDLE_BYTES = 16 * 1024
APP_BUNDLE_MAX_ID_CHARS = 96
APP_BUNDLE_MAX_ROUTE_CHARS = 96
APP_RESOURCE_BINDING_MAX_VIEWS = 32
APP_RESOURCE_BINDING_MAX_VIEWS_PER_SCREEN = 8
APP_RESOURCE_BINDING_MAX_RECORD_REFS_PER_VIEW = 50
APP_RESOURCE_BINDING_MAX_FIELDS_PER_VIEW = 16
APP_RESOURCE_BINDING_MAX_ASSERTIONS = 800
APP_RESOURCE_BINDING_MAX_REPORT_BYTES = 128 * 1024
APP_BUNDLE_ALLOWED_KINDS = ("internal_tool",)
APP_BUNDLE_ALLOWED_RESOURCE_KINDS = ("fixture",)
APP_BUNDLE_ALLOWED_ROOT_FIELDS = {"schema_version", "app", "routes", "resources", "screens"}
APP_BUNDLE_ALLOWED_ROOT_FIELDS_V2 = APP_BUNDLE_ALLOWED_ROOT_FIELDS | {"resource_binding"}
APP_BUNDLE_ALLOWED_ROOT_FIELDS_V3 = APP_BUNDLE_ALLOWED_ROOT_FIELDS_V2 | {
    "interactive_state",
    "state",
    "mutations",
    "selectors",
    "state_replay_assertions",
}
APP_BUNDLE_ALLOWED_APP_FIELDS = {"id", "title", "kind", "root_route"}
APP_BUNDLE_ALLOWED_ROUTE_FIELDS = {"id", "path", "label", "screen_id"}
APP_BUNDLE_ALLOWED_RESOURCE_FIELDS = {"id", "kind", "records"}
APP_BUNDLE_ALLOWED_SCREEN_FIELDS = {"id", "title", "intent_bundle"}
APP_BUNDLE_ALLOWED_SCREEN_FIELDS_V2 = APP_BUNDLE_ALLOWED_SCREEN_FIELDS | {"resource_views"}
APP_BUNDLE_ALLOWED_RESOURCE_VIEW_FIELDS = {"id", "resource_id", "mode", "record_ids", "fields", "target_motif_id"}
APP_RESOURCE_BINDING_TEXT_PRIMITIVES = {"badge", "label", "text", "value"}

SAFE_APP_ID_RE = re.compile(SAFE_AGENT_ID_PATTERN)
SAFE_ROUTE_RE = re.compile(r"^/[A-Za-z0-9_.~\-/]*$")
URL_SCHEME_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://")
ENV_REF_RE = re.compile(r"(\$\{[^}]+\}|\$env:|%[A-Za-z_][A-Za-z0-9_]*%|process\.env|os\.environ)", re.IGNORECASE)
PACKAGE_INSTALL_RE = re.compile(r"(\b(?:npm|pnpm|yarn|pip)\s+install\b|--install\b)", re.IGNORECASE)
FORBIDDEN_APP_FIELD_NAMES = {
    "adapter",
    "api_key",
    "auth",
    "authorization",
    "compiler",
    "credential",
    "credentials",
    "env",
    "environment",
    "fetch",
    "hosted",
    "install",
    "mutation",
    "mutations",
    "package",
    "packages",
    "password",
    "secret",
    "token",
}


def _validate_app_payload(payload: dict[str, Any], issues: list[dict[str, str]], *, compile_check: bool) -> None:
    schema_version = payload.get("schema_version")
    version: int | None = None
    if type(schema_version) is not int:
        issues.append(_issue("APP_SCHEMA_VERSION_REQUIRED", "$.schema_version", "AppBundle schema_version must be integer 1, 2, or 3."))
    elif schema_version not in APP_BUNDLE_SUPPORTED_SCHEMA_VERSIONS:
        issues.append(
            _issue(
                "APP_SCHEMA_VERSION_UNSUPPORTED",
                "$.schema_version",
                f"Unsupported AppBundle schema_version {schema_version}.",
                "Use AppBundle schema_version 1 for unbound V0, 2 for fixture_readonly_v0, or 3 for interactive_state_v0.",
            )
        )
    else:
        version = schema_version

    if version == APP_BUNDLE_STATE_SCHEMA_VERSION:
        allowed_root_fields = APP_BUNDLE_ALLOWED_ROOT_FIELDS_V3
    elif version == APP_BUNDLE_BOUND_SCHEMA_VERSION:
        allowed_root_fields = APP_BUNDLE_ALLOWED_ROOT_FIELDS_V2
    else:
        allowed_root_fields = APP_BUNDLE_ALLOWED_ROOT_FIELDS | {"resource_binding"}
    _reject_unknown_fields(payload, "$", allowed_root_fields, issues)
    forbidden_root_payload = (
        {key: value for key, value in payload.items() if key != "mutations"}
        if version == APP_BUNDLE_STATE_SCHEMA_VERSION
        else payload
    )
    _reject_forbidden_object_keys(forbidden_root_payload, "$", issues)
    resource_binding = payload.get("resource_binding")
    if version == APP_BUNDLE_SCHEMA_VERSION:
        if "resource_binding" in payload:
            issues.append(
                _issue(
                    "APP_SCHEMA_VERSION_RESOURCE_BINDING_MISMATCH",
                    "$.resource_binding",
                    "schema_version 1 must not declare resource_binding.",
                    "Remove resource_binding or upgrade to schema_version 2 with fixture_readonly_v0.",
                )
            )
    elif version == APP_BUNDLE_BOUND_SCHEMA_VERSION:
        if resource_binding != APP_BUNDLE_RESOURCE_BINDING_READONLY:
            issues.append(
                _issue(
                    "APP_SCHEMA_VERSION_RESOURCE_BINDING_MISMATCH",
                    "$.resource_binding",
                    "schema_version 2 requires resource_binding fixture_readonly_v0.",
                    "Set resource_binding to fixture_readonly_v0 or use schema_version 1.",
                )
            )
    elif version == APP_BUNDLE_STATE_SCHEMA_VERSION:
        if resource_binding != APP_BUNDLE_RESOURCE_BINDING_READONLY:
            issues.append(
                _issue(
                    "APP_SCHEMA_VERSION_RESOURCE_BINDING_MISMATCH",
                    "$.resource_binding",
                    "schema_version 3 requires resource_binding fixture_readonly_v0.",
                    "Set resource_binding to fixture_readonly_v0 for AppBundle V3.",
                )
            )

    app = _required_object(payload, "app", "$", issues)
    routes = _required_array(payload, "routes", "$", issues)
    resources = _required_array(payload, "resources", "$", issues)
    screens = _required_array(payload, "screens", "$", issues)
    if app is not None:
        _validate_app_object(app, issues)
    _check_list_count(routes, APP_BUNDLE_MAX_ROUTES, "$.routes", "APP_TOO_MANY_ROUTES", "routes", issues)
    _check_list_count(resources, APP_BUNDLE_MAX_RESOURCES, "$.resources", "APP_TOO_MANY_RESOURCES", "resources", issues)
    _check_list_count(screens, APP_BUNDLE_MAX_SCREENS, "$.screens", "APP_TOO_MANY_SCREENS", "screens", issues)

    route_ids = _validate_routes(routes, issues)
    resource_ids = _validate_resources(resources, issues)
    screen_ids, aggregate_intent_bytes = _validate_screens(screens, issues, compile_check=compile_check, schema_version=version)
    if aggregate_intent_bytes > APP_BUNDLE_MAX_AGGREGATE_INTENT_BYTES:
        issues.append(
            _issue(
                "APP_EMBEDDED_INTENTS_TOO_LARGE",
                "$.screens",
                f"Embedded IntentBundles total {aggregate_intent_bytes} bytes; the V0 aggregate limit is {APP_BUNDLE_MAX_AGGREGATE_INTENT_BYTES} bytes.",
                "Split the app into fewer or smaller screen IntentBundles.",
            )
        )
    _validate_unique_ids(route_ids, "$.routes", "APP_DUPLICATE_ROUTE_ID", issues)
    _validate_unique_ids(resource_ids, "$.resources", "APP_DUPLICATE_RESOURCE_ID", issues)
    _validate_unique_ids(screen_ids, "$.screens", "APP_DUPLICATE_SCREEN_ID", issues)
    _validate_route_graph(app, routes, set(screen_ids), issues)
    if version in {APP_BUNDLE_BOUND_SCHEMA_VERSION, APP_BUNDLE_STATE_SCHEMA_VERSION}:
        _validate_resource_binding_v0(resources, screens, issues)
    if version == APP_BUNDLE_STATE_SCHEMA_VERSION:
        _state_ir, state_issues = validate_state_ir(payload)
        issues.extend(issue.to_json() for issue in state_issues)

def _validate_app_object(app: dict[str, Any], issues: list[dict[str, str]]) -> None:
    _reject_unknown_fields(app, "$.app", APP_BUNDLE_ALLOWED_APP_FIELDS, issues)
    _reject_forbidden_object_keys(app, "$.app", issues)
    app_id = _required_string(app, "id", "$.app", issues)
    _validate_safe_id(app_id, "$.app.id", "app id", issues)
    title = _required_string(app, "title", "$.app", issues)
    _validate_app_scalar_string(title, "$.app.title", issues)
    kind = _required_string(app, "kind", "$.app", issues)
    if kind and kind not in APP_BUNDLE_ALLOWED_KINDS:
        issues.append(
            _issue(
                "APP_KIND_UNSUPPORTED",
                "$.app.kind",
                f"AppBundle supports {', '.join(APP_BUNDLE_ALLOWED_KINDS)} only.",
                "Use app.kind internal_tool for this slice.",
            )
        )
    root_route = _required_string(app, "root_route", "$.app", issues)
    _validate_route_path(root_route, "$.app.root_route", issues)

def _validate_routes(routes: list[Any], issues: list[dict[str, str]]) -> list[str]:
    ids: list[str] = []
    paths: dict[str, str] = {}
    for index, route in enumerate(routes):
        path = f"$.routes[{index}]"
        if not isinstance(route, dict):
            issues.append(_issue("APP_ROUTE_NOT_OBJECT", path, "Each route must be an object."))
            continue
        _reject_unknown_fields(route, path, APP_BUNDLE_ALLOWED_ROUTE_FIELDS, issues)
        _reject_forbidden_object_keys(route, path, issues)
        route_id = _required_string(route, "id", path, issues)
        if route_id:
            ids.append(route_id)
        _validate_safe_id(route_id, f"{path}.id", "route id", issues)
        route_path = _required_string(route, "path", path, issues)
        _validate_route_path(route_path, f"{path}.path", issues)
        if isinstance(route_path, str):
            previous = paths.setdefault(route_path, path)
            if previous != path:
                issues.append(
                    _issue(
                        "APP_DUPLICATE_ROUTE_PATH",
                        f"{path}.path",
                        f"Duplicate route path {route_path}.",
                        "Use one canonical static path per route.",
                    )
                )
        label = _required_string(route, "label", path, issues)
        _validate_app_scalar_string(label, f"{path}.label", issues)
        screen_id = _required_string(route, "screen_id", path, issues)
        _validate_safe_id(screen_id, f"{path}.screen_id", "route screen id", issues)
    return ids

def _validate_resources(resources: list[Any], issues: list[dict[str, str]]) -> list[str]:
    ids: list[str] = []
    for index, resource in enumerate(resources):
        path = f"$.resources[{index}]"
        if not isinstance(resource, dict):
            issues.append(_issue("APP_RESOURCE_NOT_OBJECT", path, "Each resource must be an object."))
            continue
        _reject_unknown_fields(resource, path, APP_BUNDLE_ALLOWED_RESOURCE_FIELDS, issues)
        _reject_forbidden_object_keys(resource, path, issues)
        resource_id = _required_string(resource, "id", path, issues)
        if resource_id:
            ids.append(resource_id)
        _validate_safe_id(resource_id, f"{path}.id", "resource id", issues)
        kind = _required_string(resource, "kind", path, issues)
        if kind and kind not in APP_BUNDLE_ALLOWED_RESOURCE_KINDS:
            issues.append(
                _issue(
                    "APP_RESOURCE_KIND_UNSUPPORTED",
                    f"{path}.kind",
                    "AppBundle resources support fixture kind only.",
                    "Use kind fixture or remove the resource adapter from this V0 bundle.",
                )
            )
        records = _required_array(resource, "records", path, issues)
        _check_list_count(
            records,
            APP_BUNDLE_MAX_RECORDS_PER_RESOURCE,
            f"{path}.records",
            "APP_RESOURCE_TOO_MANY_RECORDS",
            "fixture records per resource",
            issues,
        )
        _validate_fixture_records(records, f"{path}.records", issues)
    return ids

def _validate_fixture_records(records: list[Any], path: str, issues: list[dict[str, str]]) -> None:
    for index, record in enumerate(records):
        record_path = f"{path}[{index}]"
        if not isinstance(record, dict):
            issues.append(_issue("APP_FIXTURE_RECORD_NOT_OBJECT", record_path, "Fixture records must be objects."))
            continue
        _reject_forbidden_object_keys(record, record_path, issues)
        if len(record) > APP_BUNDLE_MAX_RECORD_FIELDS:
            issues.append(
                _issue(
                    "APP_FIXTURE_TOO_MANY_FIELDS",
                    record_path,
                    f"Fixture record declares {len(record)} fields; the V0 limit is {APP_BUNDLE_MAX_RECORD_FIELDS}.",
                    "Trim fixture records to scalar fields needed for app context.",
                )
            )
        for key, value in record.items():
            key_path = f"{record_path}.{key}"
            _validate_safe_id(key if isinstance(key, str) else None, key_path, "fixture field", issues)
            _validate_fixture_scalar(value, key_path, issues)

def _validate_screens(
    screens: list[Any],
    issues: list[dict[str, str]],
    *,
    compile_check: bool,
    schema_version: int | None,
) -> tuple[list[str], int]:
    ids: list[str] = []
    aggregate_intent_bytes = 0
    for index, screen in enumerate(screens):
        path = f"$.screens[{index}]"
        if not isinstance(screen, dict):
            issues.append(_issue("APP_SCREEN_NOT_OBJECT", path, "Each screen must be an object."))
            continue
        allowed_fields = (
            APP_BUNDLE_ALLOWED_SCREEN_FIELDS_V2
            if schema_version in {APP_BUNDLE_BOUND_SCHEMA_VERSION, APP_BUNDLE_STATE_SCHEMA_VERSION}
            else APP_BUNDLE_ALLOWED_SCREEN_FIELDS | {"resource_views"}
        )
        _reject_unknown_fields(screen, path, allowed_fields, issues)
        _reject_forbidden_object_keys({key: value for key, value in screen.items() if key != "intent_bundle"}, path, issues)
        if schema_version == APP_BUNDLE_SCHEMA_VERSION and "resource_views" in screen:
            issues.append(
                _issue(
                    "APP_SCHEMA_VERSION_RESOURCE_BINDING_MISMATCH",
                    f"{path}.resource_views",
                    "schema_version 1 screens must not declare resource_views.",
                    "Remove resource_views or upgrade to schema_version 2 with fixture_readonly_v0.",
                )
            )
        if schema_version in {APP_BUNDLE_BOUND_SCHEMA_VERSION, APP_BUNDLE_STATE_SCHEMA_VERSION} and "resource_views" not in screen:
            issues.append(
                _issue(
                    "APP_RESOURCE_BINDING_VIEWS_REQUIRED",
                    f"{path}.resource_views",
                    f"schema_version {schema_version} screens must declare resource_views, even when the list is empty.",
                    "Add resource_views to every screen.",
                )
            )
        screen_id = _required_string(screen, "id", path, issues)
        if screen_id:
            ids.append(screen_id)
        _validate_safe_id(screen_id, f"{path}.id", "screen id", issues)
        title = _required_string(screen, "title", path, issues)
        _validate_app_scalar_string(title, f"{path}.title", issues)
        intent = screen.get("intent_bundle")
        if not isinstance(intent, dict):
            issues.append(_issue("APP_SCREEN_INTENT_NOT_OBJECT", f"{path}.intent_bundle", "screen.intent_bundle must be an IntentBundle object."))
            continue
        try:
            intent_text = _stable_json(intent)
        except ValueError as exc:
            issues.append(
                _issue(
                    "APP_SCREEN_INTENT_INVALID_JSON",
                    f"{path}.intent_bundle",
                    f"Embedded screen intent is not strict JSON: {exc}",
                    "Regenerate this screen IntentBundle as strict JSON.",
                )
            )
            continue
        intent_bytes = len(intent_text.encode("utf-8"))
        aggregate_intent_bytes += intent_bytes
        if intent_bytes > APP_BUNDLE_MAX_EMBEDDED_INTENT_BYTES:
            issues.append(
                _issue(
                    "APP_SCREEN_INTENT_TOO_LARGE",
                    f"{path}.intent_bundle",
                    f"Embedded screen intent is {intent_bytes} bytes; the V0 per-screen limit is {APP_BUNDLE_MAX_EMBEDDED_INTENT_BYTES}.",
                    "Split this screen into a smaller IntentBundle.",
                )
            )
            continue
        validation = validate_intent_text(intent_text, compile_check=compile_check)
        if not validation["ok"]:
            first_issue = validation["issues"][0] if validation["issues"] else {}
            nested_code = first_issue.get("code", "INTENT_INVALID") if isinstance(first_issue, dict) else "INTENT_INVALID"
            nested_message = first_issue.get("message", "Embedded IntentBundle validation failed.") if isinstance(first_issue, dict) else "Embedded IntentBundle validation failed."
            issues.append(
                _issue(
                    "APP_SCREEN_INTENT_INVALID",
                    f"{path}.intent_bundle",
                    f"Screen {screen_id or index} IntentBundle failed local V1 validation: {nested_code}: {nested_message}",
                    "Regenerate the full embedded IntentBundle using the local V1 contract.",
                )
            )
    return ids, aggregate_intent_bytes

def _validate_route_graph(
    app: dict[str, Any] | None,
    routes: list[Any],
    screen_ids: set[str],
    issues: list[dict[str, str]],
) -> None:
    if app is None:
        return
    route_paths = {route.get("path") for route in routes if isinstance(route, dict)}
    root_route = app.get("root_route")
    if isinstance(root_route, str) and root_route not in route_paths:
        issues.append(
            _issue(
                "APP_ROOT_ROUTE_MISSING",
                "$.app.root_route",
                f"Root route {root_route} does not match any route.path.",
                "Add a route for app.root_route.",
            )
        )
    route_screen_ids = set()
    for index, route in enumerate(routes):
        if not isinstance(route, dict):
            continue
        screen_id = route.get("screen_id")
        if isinstance(screen_id, str):
            route_screen_ids.add(screen_id)
            if screen_id not in screen_ids:
                issues.append(
                    _issue(
                        "APP_ROUTE_SCREEN_MISSING",
                        f"$.routes[{index}].screen_id",
                        f"Route {route.get('id')} references missing screen {screen_id}.",
                        "Add the screen or update route.screen_id.",
                    )
                )
    for screen_id in sorted(screen_ids - route_screen_ids):
        issues.append(
            _issue(
                "APP_SCREEN_UNREACHABLE",
                "$.screens",
                f"Screen {screen_id} is not reachable from any route.",
                "Add a static route pointing to this screen or remove the screen.",
            )
        )

def _validate_resource_binding_v0(resources: list[Any], screens: list[Any], issues: list[dict[str, str]]) -> None:
    resource_records = _fixture_records_by_resource(resources, issues)
    total_views = 0
    total_assertions = 0
    for screen_index, screen in enumerate(screens):
        if not isinstance(screen, dict):
            continue
        screen_path = f"$.screens[{screen_index}]"
        resource_views = screen.get("resource_views")
        if not isinstance(resource_views, list):
            if "resource_views" in screen:
                issues.append(_issue("APP_RESOURCE_BINDING_VIEWS_NOT_ARRAY", f"{screen_path}.resource_views", "resource_views must be an array."))
            continue
        if len(resource_views) > APP_RESOURCE_BINDING_MAX_VIEWS_PER_SCREEN:
            issues.append(
                _issue(
                    "APP_RESOURCE_BINDING_LIMIT_EXCEEDED",
                    f"{screen_path}.resource_views",
                    f"Screen declares {len(resource_views)} resource views; limit is {APP_RESOURCE_BINDING_MAX_VIEWS_PER_SCREEN}.",
                    "Split the app or remove resource views.",
                )
            )
        total_views += len(resource_views)
        target_motifs = _screen_target_motif_ids(screen)
        seen_view_ids: list[str] = []
        for view_index, resource_view in enumerate(resource_views):
            path = f"{screen_path}.resource_views[{view_index}]"
            if not isinstance(resource_view, dict):
                issues.append(_issue("APP_RESOURCE_BINDING_VIEW_NOT_OBJECT", path, "Each resource_view must be an object."))
                continue
            extra = sorted(set(resource_view) - APP_BUNDLE_ALLOWED_RESOURCE_VIEW_FIELDS)
            if extra:
                issues.append(
                    _issue(
                        "APP_RESOURCE_BINDING_QUERY_UNSUPPORTED",
                        path,
                        f"resource_view contains unsupported field(s): {', '.join(extra)}.",
                        "Remove transform, query, pagination, grouping, aggregation, or adapter fields from Resource Binding V0.",
                    )
                )
            view_id = _required_string(resource_view, "id", path, issues)
            if view_id:
                seen_view_ids.append(view_id)
            _validate_safe_id(view_id, f"{path}.id", "resource view id", issues)
            resource_id = _required_string(resource_view, "resource_id", path, issues)
            _validate_safe_id(resource_id, f"{path}.resource_id", "resource view resource id", issues)
            mode = _required_string(resource_view, "mode", path, issues)
            if mode and mode != "list":
                issues.append(
                    _issue(
                        "APP_RESOURCE_BINDING_MODE_UNSUPPORTED",
                        f"{path}.mode",
                        "Resource Binding V0 supports mode list only.",
                        "Use mode list or remove the resource_view.",
                    )
                )
            record_ids = _required_array(resource_view, "record_ids", path, issues)
            fields = _required_array(resource_view, "fields", path, issues)
            _check_list_count(record_ids, APP_RESOURCE_BINDING_MAX_RECORD_REFS_PER_VIEW, f"{path}.record_ids", "APP_RESOURCE_BINDING_LIMIT_EXCEEDED", "record refs per resource view", issues)
            _check_list_count(fields, APP_RESOURCE_BINDING_MAX_FIELDS_PER_VIEW, f"{path}.fields", "APP_RESOURCE_BINDING_LIMIT_EXCEEDED", "fields per resource view", issues)
            clean_record_ids = _validate_resource_binding_string_list(record_ids, f"{path}.record_ids", "record id", issues)
            clean_fields = _validate_resource_binding_string_list(fields, f"{path}.fields", "field", issues)
            _validate_unique_ids(clean_record_ids, f"{path}.record_ids", "APP_RESOURCE_BINDING_DUPLICATE_RECORD_REF", issues)
            _validate_unique_ids(clean_fields, f"{path}.fields", "APP_RESOURCE_BINDING_DUPLICATE_FIELD", issues)
            target_motif_id = _required_string(resource_view, "target_motif_id", path, issues)
            _validate_safe_id(target_motif_id, f"{path}.target_motif_id", "resource view target motif id", issues)
            if target_motif_id and target_motif_id not in target_motifs:
                issues.append(
                    _issue(
                        "APP_RESOURCE_BINDING_MOTIF_MISSING",
                        f"{path}.target_motif_id",
                        f"resource_view targets missing motif {target_motif_id}.",
                        "Use a motif id declared by this screen IntentBundle.",
                    )
                )
            records_by_id = resource_records.get(resource_id or "")
            if resource_id and records_by_id is None:
                issues.append(
                    _issue(
                        "APP_RESOURCE_BINDING_RESOURCE_MISSING",
                        f"{path}.resource_id",
                        f"resource_view references missing fixture resource {resource_id}.",
                        "Use an existing fixture resource id.",
                    )
                )
                continue
            for record_id in clean_record_ids:
                record = records_by_id.get(record_id) if isinstance(records_by_id, dict) else None
                if record is None:
                    issues.append(
                        _issue(
                            "APP_RESOURCE_BINDING_RECORD_MISSING",
                            f"{path}.record_ids",
                            f"resource_view references missing fixture record {record_id}.",
                            "Use a record id declared by the fixture resource.",
                        )
                    )
                    continue
                for field in clean_fields:
                    if field not in record:
                        issues.append(
                            _issue(
                                "APP_RESOURCE_BINDING_FIELD_MISSING",
                                f"{path}.fields",
                                f"resource_view references missing fixture field {field} on record {record_id}.",
                                "Use only fields present on every referenced fixture record.",
                            )
                        )
                    elif not _is_resource_binding_scalar(record.get(field)):
                        issues.append(
                            _issue(
                                "APP_RESOURCE_BINDING_VALUE_UNSUPPORTED",
                                f"{path}.fields",
                                f"resource_view references non-scalar fixture field {field} on record {record_id}.",
                                "Use only string, number, boolean, or null fixture scalars in Resource Binding V0.",
                            )
                        )
                    total_assertions += 1
        _validate_unique_ids(seen_view_ids, f"{screen_path}.resource_views", "APP_RESOURCE_BINDING_DUPLICATE_VIEW_ID", issues)
    if total_views > APP_RESOURCE_BINDING_MAX_VIEWS:
        issues.append(
            _issue(
                "APP_RESOURCE_BINDING_LIMIT_EXCEEDED",
                "$.screens",
                f"App declares {total_views} resource views; limit is {APP_RESOURCE_BINDING_MAX_VIEWS}.",
                "Split the app or remove resource views.",
            )
        )
    if total_assertions > APP_RESOURCE_BINDING_MAX_ASSERTIONS:
        issues.append(
            _issue(
                "APP_RESOURCE_BINDING_LIMIT_EXCEEDED",
                "$.screens",
                f"App declares {total_assertions} record-field assertions; limit is {APP_RESOURCE_BINDING_MAX_ASSERTIONS}.",
                "Reduce record refs or fields per resource view.",
            )
        )
    if total_assertions == 0:
        issues.append(
            _issue(
                "APP_RESOURCE_BINDING_EMPTY_ASSERTIONS",
                "$.screens",
                "fixture_readonly_v0 requires at least one concrete record-field assertion.",
                "Declare at least one resource_view with one record_id and one field.",
            )
        )

def _fixture_records_by_resource(resources: list[Any], issues: list[dict[str, str]]) -> dict[str, dict[str, dict[str, Any]]]:
    resources_by_id: dict[str, dict[str, dict[str, Any]]] = {}
    for resource_index, resource in enumerate(resources):
        if not isinstance(resource, dict) or not isinstance(resource.get("id"), str):
            continue
        resource_id = resource["id"]
        records = resource.get("records") if isinstance(resource.get("records"), list) else []
        by_id: dict[str, dict[str, Any]] = {}
        seen_ids: list[str] = []
        for record_index, record in enumerate(records):
            path = f"$.resources[{resource_index}].records[{record_index}].id"
            if not isinstance(record, dict):
                continue
            record_id = record.get("id")
            if not isinstance(record_id, str) or not record_id:
                issues.append(
                    _issue(
                        "APP_RESOURCE_BINDING_RECORD_ID_REQUIRED",
                        path,
                        "fixture_readonly_v0 fixture records must declare a non-empty string id.",
                        "Add a safe string id field to every fixture record.",
                    )
                )
                continue
            _validate_safe_id(record_id, path, "fixture record id", issues)
            seen_ids.append(record_id)
            if record_id not in by_id:
                by_id[record_id] = record
        _validate_unique_ids(seen_ids, f"$.resources[{resource_index}].records", "APP_RESOURCE_BINDING_DUPLICATE_RECORD_ID", issues)
        resources_by_id[resource_id] = by_id
    return resources_by_id

def _screen_target_motif_ids(screen: dict[str, Any]) -> set[str]:
    intent = screen.get("intent_bundle") if isinstance(screen.get("intent_bundle"), dict) else {}
    view_spec = intent.get("view_spec") if isinstance(intent.get("view_spec"), dict) else {}
    motifs = view_spec.get("motifs") if isinstance(view_spec.get("motifs"), list) else []
    return {motif.get("id") for motif in motifs if isinstance(motif, dict) and isinstance(motif.get("id"), str)}

def _validate_resource_binding_string_list(values: list[Any], path: str, label: str, issues: list[dict[str, str]]) -> list[str]:
    clean: list[str] = []
    for index, value in enumerate(values):
        item_path = f"{path}[{index}]"
        if not isinstance(value, str) or not value:
            issues.append(
                _issue(
                    "APP_RESOURCE_BINDING_REF_INVALID",
                    item_path,
                    f"resource_view {label} refs must be non-empty strings.",
                    "Use safe string ids only.",
                )
            )
            continue
        _validate_safe_id(value, item_path, label, issues)
        clean.append(value)
    return clean

def _is_resource_binding_scalar(value: Any) -> bool:
    return isinstance(value, str) or isinstance(value, bool) or value is None or type(value) in {int, float}

def _route_assertions(payload: dict[str, Any]) -> dict[str, bool]:
    routes = payload.get("routes") if isinstance(payload.get("routes"), list) else []
    screens = payload.get("screens") if isinstance(payload.get("screens"), list) else []
    app = payload.get("app") if isinstance(payload.get("app"), dict) else {}
    route_paths = {route.get("path") for route in routes if isinstance(route, dict)}
    screen_ids = {screen.get("id") for screen in screens if isinstance(screen, dict)}
    route_screen_ids = {route.get("screen_id") for route in routes if isinstance(route, dict)}
    return {
        "root_route_resolves": app.get("root_route") in route_paths,
        "all_routes_resolve": all(screen_id in screen_ids for screen_id in route_screen_ids),
        "all_screens_reachable": all(screen_id in route_screen_ids for screen_id in screen_ids),
    }

def _validation_payload(
    payload: dict[str, Any] | None,
    issues: list[dict[str, str]],
    *,
    compile_check: bool,
    raw_bytes: int,
) -> dict[str, Any]:
    summary = _app_summary(payload) if isinstance(payload, dict) else None
    route_assertions = _route_assertions(payload) if isinstance(payload, dict) else None
    compile_status = "skipped" if not compile_check else "passed" if not issues else "failed"
    resource_binding = _resource_binding_for_payload(payload)
    binding_validation = _resource_binding_validation_summary(payload) if isinstance(payload, dict) and resource_binding == APP_BUNDLE_RESOURCE_BINDING_READONLY else None
    state_summary = state_ir_summary(payload) if isinstance(payload, dict) else None
    return {
        "schema_version": APP_BUNDLE_RESULT_SCHEMA_VERSION,
        "app_schema_version": _app_schema_version(payload),
        "ok": not issues,
        "compile_check": compile_status,
        "resource_binding": resource_binding,
        **({"binding_scope": APP_BUNDLE_BINDING_SCOPE} if resource_binding == APP_BUNDLE_RESOURCE_BINDING_READONLY else {}),
        **({"resource_binding_validation": binding_validation} if binding_validation is not None else {}),
        **({"interactive_state": INTERACTIVE_STATE_PROFILE, "state_ir": state_summary} if state_summary is not None else {}),
        "summary": summary,
        "route_assertions": route_assertions,
        "raw_bytes": raw_bytes,
        "limits": _app_limits(),
        "issues": issues,
    }

def _app_summary(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    app = payload.get("app") if isinstance(payload.get("app"), dict) else {}
    routes = payload.get("routes") if isinstance(payload.get("routes"), list) else []
    screens = payload.get("screens") if isinstance(payload.get("screens"), list) else []
    resources = payload.get("resources") if isinstance(payload.get("resources"), list) else []
    return {
        "schema_version": payload.get("schema_version"),
        "id": app.get("id"),
        "title": app.get("title"),
        "kind": app.get("kind"),
        "root_route": app.get("root_route"),
        "route_count": len(routes),
        "screen_count": len(screens),
        "resource_count": len(resources),
        **({"state_ir": state_ir_summary(payload)} if state_ir_summary(payload) is not None else {}),
    }

def _app_schema_version(payload: dict[str, Any] | None) -> int | None:
    if not isinstance(payload, dict):
        return None
    schema_version = payload.get("schema_version")
    return schema_version if type(schema_version) is int else None

def _resource_binding_for_payload(payload: dict[str, Any] | None) -> str:
    if isinstance(payload, dict) and payload.get("schema_version") in {
        APP_BUNDLE_BOUND_SCHEMA_VERSION,
        APP_BUNDLE_STATE_SCHEMA_VERSION,
    }:
        return APP_BUNDLE_RESOURCE_BINDING_READONLY
    return APP_BUNDLE_RESOURCE_BINDING

def _resource_binding_report_fields(
    payload: dict[str, Any] | None,
    resource_binding_report: dict[str, Any] | None,
) -> dict[str, Any]:
    resource_binding = _resource_binding_for_payload(payload)
    fields: dict[str, Any] = {"resource_binding": resource_binding}
    if resource_binding != APP_BUNDLE_RESOURCE_BINDING_READONLY:
        return fields
    fields["binding_scope"] = APP_BUNDLE_BINDING_SCOPE
    if isinstance(resource_binding_report, dict):
        fields["resource_binding_assertions"] = resource_binding_report
    return fields

def _resource_binding_fields_from_validation(validation: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(validation, dict):
        return {"resource_binding": APP_BUNDLE_RESOURCE_BINDING}
    resource_binding = str(validation.get("resource_binding") or APP_BUNDLE_RESOURCE_BINDING)
    fields: dict[str, Any] = {"resource_binding": resource_binding}
    if resource_binding == APP_BUNDLE_RESOURCE_BINDING_READONLY:
        fields["binding_scope"] = str(validation.get("binding_scope") or APP_BUNDLE_BINDING_SCOPE)
        binding_validation = validation.get("resource_binding_validation")
        if isinstance(binding_validation, dict):
            fields["resource_binding_validation"] = binding_validation
    return fields

def _resource_binding_validation_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
    resource_view_count = 0
    assertion_count = 0
    screen_count = 0
    if not isinstance(payload, dict):
        return {"binding_scope": APP_BUNDLE_BINDING_SCOPE, "resource_view_count": 0, "assertion_count": 0, "bound_screen_count": 0}
    screens = payload.get("screens") if isinstance(payload.get("screens"), list) else []
    resources = _fixture_records_by_resource(payload.get("resources") if isinstance(payload.get("resources"), list) else [], [])
    for screen in screens:
        if not isinstance(screen, dict):
            continue
        views = screen.get("resource_views") if isinstance(screen.get("resource_views"), list) else []
        if views:
            screen_count += 1
        for resource_view in views:
            if not isinstance(resource_view, dict):
                continue
            resource_view_count += 1
            records = resources.get(str(resource_view.get("resource_id") or ""), {})
            record_ids = [item for item in resource_view.get("record_ids", []) if isinstance(item, str)]
            fields = [item for item in resource_view.get("fields", []) if isinstance(item, str)]
            for record_id in record_ids:
                record = records.get(record_id)
                if not isinstance(record, dict):
                    continue
                assertion_count += sum(1 for field in fields if field in record)
    return {
        "binding_scope": APP_BUNDLE_BINDING_SCOPE,
        "resource_view_count": resource_view_count,
        "bound_screen_count": screen_count,
        "assertion_count": assertion_count,
        "limits": _resource_binding_limits(),
    }

def _app_limits() -> dict[str, int]:
    return {
        "max_raw_json_bytes": APP_BUNDLE_MAX_BYTES,
        "max_screens": APP_BUNDLE_MAX_SCREENS,
        "max_routes": APP_BUNDLE_MAX_ROUTES,
        "max_fixture_resources": APP_BUNDLE_MAX_RESOURCES,
        "max_records_per_resource": APP_BUNDLE_MAX_RECORDS_PER_RESOURCE,
        "max_scalar_fields_per_record": APP_BUNDLE_MAX_RECORD_FIELDS,
        "max_scalar_string_chars": APP_BUNDLE_MAX_SCALAR_STRING_CHARS,
        "max_embedded_intent_bytes": APP_BUNDLE_MAX_EMBEDDED_INTENT_BYTES,
        "max_aggregate_embedded_intent_bytes": APP_BUNDLE_MAX_AGGREGATE_INTENT_BYTES,
        "max_proof_report_bytes": APP_BUNDLE_MAX_PROOF_REPORT_BYTES,
        "max_support_bundle_bytes": APP_BUNDLE_MAX_SUPPORT_BUNDLE_BYTES,
        "max_id_chars": APP_BUNDLE_MAX_ID_CHARS,
        "max_route_chars": APP_BUNDLE_MAX_ROUTE_CHARS,
        **_state_ir_limits(),
        **_resource_binding_limits(),
    }

def _resource_binding_limits() -> dict[str, int]:
    return {
        "max_resource_views": APP_RESOURCE_BINDING_MAX_VIEWS,
        "max_resource_views_per_screen": APP_RESOURCE_BINDING_MAX_VIEWS_PER_SCREEN,
        "max_record_refs_per_resource_view": APP_RESOURCE_BINDING_MAX_RECORD_REFS_PER_VIEW,
        "max_fields_per_resource_view": APP_RESOURCE_BINDING_MAX_FIELDS_PER_VIEW,
        "max_resource_binding_assertions": APP_RESOURCE_BINDING_MAX_ASSERTIONS,
        "max_resource_binding_report_bytes": APP_RESOURCE_BINDING_MAX_REPORT_BYTES,
    }

def _state_ir_limits() -> dict[str, int]:
    return {
        "max_state_entries": APP_STATE_MAX_ENTRIES,
        "max_state_mutations": APP_STATE_MAX_MUTATIONS,
        "max_state_ops_per_mutation": APP_STATE_MAX_OPS_PER_MUTATION,
        "max_state_selectors": APP_STATE_MAX_SELECTORS,
        "max_state_selector_ops": APP_STATE_MAX_SELECTOR_OPS,
        "max_state_replay_assertions": APP_STATE_MAX_REPLAY_ASSERTIONS,
        "max_state_events_per_replay": APP_STATE_MAX_EVENTS_PER_REPLAY,
        "max_state_reducer_bytes": APP_STATE_MAX_REDUCER_BYTES,
        "max_state_manifest_bytes": APP_STATE_MAX_MANIFEST_BYTES,
    }

def _reject_unknown_fields(
    obj: dict[str, Any],
    path: str,
    allowed: set[str],
    issues: list[dict[str, str]],
) -> None:
    for key in sorted(set(obj) - allowed):
        issues.append(
            _issue(
                "APP_UNKNOWN_FIELD",
                f"{path}.{key}",
                f"Unknown AppBundle field {key}.",
                "Remove extension fields; AppBundle rejects unknown fields.",
            )
        )

def _reject_forbidden_object_keys(obj: dict[str, Any], path: str, issues: list[dict[str, str]]) -> None:
    for key in obj:
        lowered = str(key).lower()
        if lowered in FORBIDDEN_APP_FIELD_NAMES:
            issues.append(
                _issue(
                    "APP_FORBIDDEN_SURFACE",
                    f"{path}.{key}",
                    f"AppBundle rejects local-only side-effect or credential field {key}.",
                    "Remove URL, env, credential, adapter, fetch, mutation, package install, or hosted compiler config.",
                )
            )

def _validate_safe_id(value: str | None, path: str, label: str, issues: list[dict[str, str]]) -> bool:
    if not value:
        return False
    if len(value) > APP_BUNDLE_MAX_ID_CHARS:
        issues.append(
            _issue(
                "APP_ID_TOO_LONG",
                path,
                f"{label} exceeds {APP_BUNDLE_MAX_ID_CHARS} characters.",
                "Use a shorter stable id.",
            )
        )
        return False
    if SAFE_APP_ID_RE.match(value):
        return True
    issues.append(
        _issue(
            "APP_INVALID_ID",
            path,
            f"{label} '{value}' must match {SAFE_AGENT_ID_PATTERN}.",
            "Use only letters, digits, underscore, dot, and dash. Do not use spaces, slashes, colons, markup, or paths.",
        )
    )
    return False

def _validate_route_path(value: str | None, path: str, issues: list[dict[str, str]]) -> None:
    if not isinstance(value, str) or value == "":
        return
    bad = (
        len(value) > APP_BUNDLE_MAX_ROUTE_CHARS
        or not value.startswith("/")
        or not SAFE_ROUTE_RE.match(value)
        or "//" in value
        or "/../" in value
        or value.endswith("/..")
        or "/./" in value
        or value.endswith("/.")
        or "%" in value
        or "?" in value
        or "#" in value
        or "\\" in value
    )
    if bad:
        issues.append(
            _issue(
                "APP_ROUTE_PATH_INVALID",
                path,
                f"Route path {value!r} is not a canonical static AppBundle path.",
                "Use a unique path starting with / and only letters, digits, _, ., ~, -, and /.",
            )
        )

def _validate_app_scalar_string(value: str | None, path: str, issues: list[dict[str, str]]) -> None:
    if value is None:
        return
    if len(value) > APP_BUNDLE_MAX_SCALAR_STRING_CHARS:
        issues.append(
            _issue(
                "APP_STRING_TOO_LONG",
                path,
                f"String exceeds {APP_BUNDLE_MAX_SCALAR_STRING_CHARS} characters.",
                "Shorten AppBundle-owned strings.",
            )
        )
    if URL_SCHEME_RE.search(value) or ENV_REF_RE.search(value) or PACKAGE_INSTALL_RE.search(value):
        issues.append(
            _issue(
                "APP_FORBIDDEN_SURFACE",
                path,
                "AppBundle rejects URL schemes, environment references, and package-install flags.",
                "Remove network, environment, package install, or hosted compiler references.",
            )
        )

def _validate_fixture_scalar(value: Any, path: str, issues: list[dict[str, str]]) -> None:
    if value is None or isinstance(value, bool) or type(value) in {int, float}:
        return
    if isinstance(value, str):
        _validate_app_scalar_string(value, path, issues)
        return
    issues.append(
        _issue(
            "APP_FIXTURE_VALUE_NOT_SCALAR",
            path,
            "Fixture record values must be scalar JSON values.",
            "Use only strings, numbers, booleans, or null in fixture records.",
        )
    )

def _required_object(obj: dict[str, Any], key: str, path: str, issues: list[dict[str, str]]) -> dict[str, Any] | None:
    value = obj.get(key)
    if not isinstance(value, dict):
        issues.append(_issue("APP_MISSING_FIELD", f"{path}.{key}", f"Missing required object field {key}."))
        return None
    return value

def _required_array(obj: dict[str, Any], key: str, path: str, issues: list[dict[str, str]]) -> list[Any]:
    value = obj.get(key)
    if not isinstance(value, list):
        issues.append(_issue("APP_MISSING_FIELD", f"{path}.{key}", f"Missing required array field {key}."))
        return []
    return value

def _required_string(obj: dict[str, Any], key: str, path: str, issues: list[dict[str, str]]) -> str | None:
    value = obj.get(key)
    if not isinstance(value, str) or value == "":
        issues.append(_issue("APP_MISSING_FIELD", f"{path}.{key}", f"Missing required string field {key}."))
        return None
    return value

def _check_list_count(
    values: list[Any],
    limit: int,
    path: str,
    code: str,
    label: str,
    issues: list[dict[str, str]],
) -> None:
    if len(values) <= limit:
        return
    issues.append(
        _issue(
            code,
            path,
            f"AppBundle declares {len(values)} {label}; the V0 limit is {limit}.",
            "Split the app into smaller AppBundles.",
        )
    )

def _validate_unique_ids(ids: list[str], path: str, code: str, issues: list[dict[str, str]]) -> None:
    seen: set[str] = set()
    for item_id in ids:
        if item_id in seen:
            issues.append(_issue(code, path, f"Duplicate id {item_id}.", "Use unique stable ids."))
        seen.add(item_id)

def _issue(code: str, path: str, message: str, suggestion: str | None = None) -> dict[str, str]:
    return {
        "severity": "error",
        "code": code,
        "path": path,
        "message": message,
        "suggestion": suggestion or "Regenerate the AppBundle using the local AppBundle contract.",
    }

def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value {value} is not allowed")

def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def validate_app_text(text: str, *, compile_check: bool = True) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    raw_bytes = len(text.encode("utf-8"))
    if raw_bytes > APP_BUNDLE_MAX_BYTES:
        issues.append(
            _issue(
                "APP_BUNDLE_TOO_LARGE",
                "$",
                f"AppBundle is {raw_bytes} bytes; the V0 limit is {APP_BUNDLE_MAX_BYTES} bytes.",
                "Split the app into a smaller AppBundle before validating.",
            )
        )
        return _validation_payload(None, issues, compile_check=compile_check, raw_bytes=raw_bytes)
    try:
        payload = json.loads(text, parse_constant=_reject_json_constant)
    except ValueError as exc:
        issues.append(
            _issue(
                "APP_INVALID_JSON",
                "$",
                f"AppBundle must be strict JSON: {exc}",
                "Regenerate strict AppBundle JSON without comments, markdown fences, or non-finite numbers.",
            )
        )
        return _validation_payload(None, issues, compile_check=compile_check, raw_bytes=raw_bytes)
    if not isinstance(payload, dict):
        issues.append(_issue("APP_ROOT_NOT_OBJECT", "$", "AppBundle root must be a JSON object."))
        return _validation_payload(payload, issues, compile_check=compile_check, raw_bytes=raw_bytes)

    _validate_app_payload(payload, issues, compile_check=compile_check)
    return _validation_payload(payload, issues, compile_check=compile_check, raw_bytes=raw_bytes)

def validate_app_file(path: str | Path, *, compile_check: bool = True) -> dict[str, Any]:
    return validate_app_text(Path(path).read_text(encoding="utf-8"), compile_check=compile_check)

__all__ = [
    "validate_app_text",
    "validate_app_file",
    "APP_BUNDLE_ALLOWED_APP_FIELDS",
    "APP_BUNDLE_ALLOWED_KINDS",
    "APP_BUNDLE_ALLOWED_RESOURCE_FIELDS",
    "APP_BUNDLE_ALLOWED_RESOURCE_KINDS",
    "APP_BUNDLE_ALLOWED_RESOURCE_VIEW_FIELDS",
    "APP_BUNDLE_ALLOWED_ROOT_FIELDS",
    "APP_BUNDLE_ALLOWED_ROOT_FIELDS_V2",
    "APP_BUNDLE_ALLOWED_ROOT_FIELDS_V3",
    "APP_BUNDLE_ALLOWED_ROUTE_FIELDS",
    "APP_BUNDLE_ALLOWED_SCREEN_FIELDS",
    "APP_BUNDLE_ALLOWED_SCREEN_FIELDS_V2",
    "APP_BUNDLE_BINDING_SCOPE",
    "APP_BUNDLE_BOUND_SCHEMA_VERSION",
    "APP_BUNDLE_MAX_AGGREGATE_INTENT_BYTES",
    "APP_BUNDLE_MAX_BYTES",
    "APP_BUNDLE_MAX_EMBEDDED_INTENT_BYTES",
    "APP_BUNDLE_MAX_ID_CHARS",
    "APP_BUNDLE_MAX_PROOF_REPORT_BYTES",
    "APP_BUNDLE_MAX_RECORDS_PER_RESOURCE",
    "APP_BUNDLE_MAX_RECORD_FIELDS",
    "APP_BUNDLE_MAX_RESOURCES",
    "APP_BUNDLE_MAX_ROUTES",
    "APP_BUNDLE_MAX_ROUTE_CHARS",
    "APP_BUNDLE_MAX_SCALAR_STRING_CHARS",
    "APP_BUNDLE_MAX_SCREENS",
    "APP_BUNDLE_MAX_SUPPORT_BUNDLE_BYTES",
    "APP_BUNDLE_RESOURCE_BINDING",
    "APP_BUNDLE_RESOURCE_BINDING_READONLY",
    "APP_BUNDLE_RESULT_SCHEMA_VERSION",
    "APP_BUNDLE_SCHEMA_VERSION",
    "APP_BUNDLE_STATE_SCHEMA_VERSION",
    "APP_BUNDLE_SUPPORTED_SCHEMA_VERSIONS",
    "APP_RESOURCE_BINDING_MAX_ASSERTIONS",
    "APP_RESOURCE_BINDING_MAX_FIELDS_PER_VIEW",
    "APP_RESOURCE_BINDING_MAX_RECORD_REFS_PER_VIEW",
    "APP_RESOURCE_BINDING_MAX_REPORT_BYTES",
    "APP_RESOURCE_BINDING_MAX_VIEWS",
    "APP_RESOURCE_BINDING_MAX_VIEWS_PER_SCREEN",
    "APP_RESOURCE_BINDING_TEXT_PRIMITIVES",
    "ENV_REF_RE",
    "FORBIDDEN_APP_FIELD_NAMES",
    "PACKAGE_INSTALL_RE",
    "SAFE_APP_ID_RE",
    "SAFE_ROUTE_RE",
    "URL_SCHEME_RE",
    "_app_limits",
    "_app_schema_version",
    "_app_summary",
    "_check_list_count",
    "_fixture_records_by_resource",
    "_is_resource_binding_scalar",
    "_issue",
    "_reject_forbidden_object_keys",
    "_reject_json_constant",
    "_reject_unknown_fields",
    "_required_array",
    "_required_object",
    "_required_string",
    "_resource_binding_fields_from_validation",
    "_resource_binding_for_payload",
    "_resource_binding_limits",
    "_resource_binding_report_fields",
    "_resource_binding_validation_summary",
    "_route_assertions",
    "_screen_target_motif_ids",
    "_stable_json",
    "_state_ir_limits",
    "_validate_app_object",
    "_validate_app_payload",
    "_validate_app_scalar_string",
    "_validate_fixture_records",
    "_validate_fixture_scalar",
    "_validate_resource_binding_string_list",
    "_validate_resource_binding_v0",
    "_validate_resources",
    "_validate_route_graph",
    "_validate_route_path",
    "_validate_routes",
    "_validate_safe_id",
    "_validate_screens",
    "_validate_unique_ids",
    "_validation_payload",
]
