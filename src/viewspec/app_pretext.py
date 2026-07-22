"""Fail-closed validation for pinned Pretext native-DOM layout evidence.

The browser harness that produces this report is intentionally outside this module.  This
adapter accepts only an exact, bounded report for a manifest-derived scope and returns
sanitized evidence: identities, counts, numeric measurements, and hashes, never raw text.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping, Sequence


PRETEXT_PACKAGE = "@chenglou/pretext"
PRETEXT_VERSION = "0.0.8"
PRETEXT_NPM_RESOLVED = "https://registry.npmjs.org/@chenglou/pretext/-/pretext-0.0.8.tgz"
PRETEXT_NPM_INTEGRITY = (
    "sha512-yqm2GMxnPI7VHcHwe84P8ZF0JK/2d2DMKPqMN+s95jQhwDMYYXKVFVJUMEaVWckQStdsjdLav/0Vu+d9YbtGxA=="
)
PRETEXT_PACKAGE_TREE = {
    "sha256": "e5a45193af0a178be6f0c9138f704ba92bdb18eff1da39da99533c2c17b4f103",
    "bytes": 902_216,
    "files": 69,
}
PRETEXT_PROFILE = "viewspec_pretext_native_dom_v1"
PRETEXT_PROTOCOL = "viewspec.pretext-runtime-v1"

PRETEXT_JSON_MAX_BYTES = 4 * 1024 * 1024
PRETEXT_RUNTIME_REPORT_MAX_BYTES = 2 * 1024 * 1024
PRETEXT_PACKAGE_TREE_MAX_BYTES = 16 * 1024 * 1024
PRETEXT_PACKAGE_TREE_MAX_FILES = 256
PRETEXT_MANIFEST_MAX_BYTES = 4 * 1024 * 1024
PRETEXT_MAX_MANIFEST_NODES = 4096
PRETEXT_MAX_VIEWPORTS = 8
PRETEXT_MAX_SCREENS = 32
PRETEXT_MAX_SURFACES_PER_SCREEN = 512
PRETEXT_MAX_OBSERVATIONS = 16_384
PRETEXT_MAX_INPUT_BYTES = 2 * 1024 * 1024
PRETEXT_MAX_ID_CHARS = 160
PRETEXT_MAX_ROUTE_CHARS = 256
PRETEXT_MAX_VIEWPORT_DIMENSION = 16_384

PRETEXT_VIEWPORTS: tuple[dict[str, int | str], ...] = (
    {"id": "mobile", "width": 390, "height": 844},
    {"id": "tablet", "width": 768, "height": 1024},
    {"id": "desktop", "width": 1440, "height": 1000},
)
PRETEXT_VISIBLE_PRIMITIVES = frozenset({"text", "label", "value", "badge", "button", "error_boundary"})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_LOCALE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SCOPE_KEYS = {
    "schema_version",
    "status",
    "profile",
    "protocol",
    "viewports",
    "screens",
    "required_observation_count",
}
_VIEWPORT_KEYS = {"id", "width", "height"}
_SCREEN_KEYS = {"screen_id", "route_id", "route_path", "surfaces"}
_SURFACE_KEYS = {"surface_id", "ir_id"}
_REPORT_KEYS = {
    "schema_version",
    "engine",
    "profile",
    "protocol",
    "environment",
    "viewports",
    "items",
    "summary",
    "cache",
    "errors",
}
_ENGINE_KEYS = {"name", "package", "version"}
_ENVIRONMENT_KEYS = {"browser", "locale", "device_scale_factor", "font_status"}
_ITEM_IDENTITY_KEYS = {
    "screen_id",
    "route_id",
    "viewport_id",
    "surface_id",
    "ir_id",
    "input_sha256",
    "input_bytes",
    "status",
}
_ITEM_METRIC_KEYS = {
    "available_width",
    "line_height",
    "predicted_line_count",
    "observed_line_count",
    "horizontal_overflow",
    "vertical_overflow",
}
_SUMMARY_KEYS = {"required", "accounted", "measured", "hidden", "unsupported", "failed"}
_CACHE_KEYS = {"prepare_calls", "unique_inputs", "layout_calls", "cache_hits"}


class PretextFailure(ValueError):
    """Stable-code failure carrying bounded Pretext evidence when available."""

    def __init__(
        self,
        code: str,
        message: str,
        fix: str,
        *,
        report: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.fix = fix
        self.report = report

    def to_json(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "fix": self.fix}

    def attach_report(self, report: dict[str, Any]) -> "PretextFailure":
        if self.report is None:
            self.report = report
        return self


class _DuplicateKey(ValueError):
    pass


def validate_pretext_installation(app_dir: str | Path) -> dict[str, Any]:
    """Validate the exact pinned Pretext package and return sanitized identity evidence."""

    try:
        root = _resolve_directory(app_dir)
        package_path = root / "package.json"
        lock_path = root / "package-lock.json"
        installed_root = root / "node_modules" / "@chenglou" / "pretext"
        installed_package_path = installed_root / "package.json"
        package = _read_json_object(
            package_path,
            limit=PRETEXT_JSON_MAX_BYTES,
            missing_code="APP_PRETEXT_PACKAGE_MISSING",
            invalid_code="APP_PRETEXT_PACKAGE_INTEGRITY",
            label="package.json",
        )
        lock = _read_json_object(
            lock_path,
            limit=PRETEXT_JSON_MAX_BYTES,
            missing_code="APP_PRETEXT_PACKAGE_MISSING",
            invalid_code="APP_PRETEXT_PACKAGE_INTEGRITY",
            label="package-lock.json",
        )
        installed_package = _read_json_object(
            installed_package_path,
            limit=PRETEXT_JSON_MAX_BYTES,
            missing_code="APP_PRETEXT_PACKAGE_MISSING",
            invalid_code="APP_PRETEXT_PACKAGE_INTEGRITY",
            label="installed package metadata",
        )

        dependencies = package.get("dependencies")
        if not isinstance(dependencies, Mapping) or dependencies.get(PRETEXT_PACKAGE) != PRETEXT_VERSION:
            raise PretextFailure(
                "APP_PRETEXT_VERSION_MISMATCH",
                f"package.json runtime dependencies must pin {PRETEXT_PACKAGE} exactly to {PRETEXT_VERSION}.",
                "Regenerate the React proof package with the supported Pretext dependency.",
            )
        packages = lock.get("packages")
        if lock.get("lockfileVersion") != 3 or not isinstance(packages, Mapping):
            raise _package_integrity_failure("package-lock.json must use the supported npm lockfile v3 shape.")
        lock_root = packages.get("")
        lock_entry = packages.get("node_modules/@chenglou/pretext")
        if not isinstance(lock_root, Mapping) or not isinstance(lock_root.get("dependencies"), Mapping):
            raise _package_integrity_failure("package-lock.json has no root runtime dependency map.")
        if lock_root["dependencies"].get(PRETEXT_PACKAGE) != PRETEXT_VERSION:
            raise PretextFailure(
                "APP_PRETEXT_VERSION_MISMATCH",
                f"package-lock.json runtime dependencies must pin {PRETEXT_PACKAGE} exactly to {PRETEXT_VERSION}.",
                "Regenerate the proof package lockfile from the supported dependency set.",
            )
        expected_lock = {
            "version": PRETEXT_VERSION,
            "resolved": PRETEXT_NPM_RESOLVED,
            "integrity": PRETEXT_NPM_INTEGRITY,
        }
        if not isinstance(lock_entry, Mapping) or any(
            lock_entry.get(key) != value for key, value in expected_lock.items()
        ):
            raise _package_integrity_failure("The Pretext lock entry version, URL, or npm integrity is not exact.")
        if lock_entry.get("dev") not in (None, False):
            raise _package_integrity_failure(
                "The Pretext lock entry must classify the package as a runtime dependency."
            )
        if installed_package.get("name") != PRETEXT_PACKAGE or installed_package.get("version") != PRETEXT_VERSION:
            raise PretextFailure(
                "APP_PRETEXT_VERSION_MISMATCH",
                "The installed Pretext package metadata does not match the pinned package.",
                "Install only from the unchanged generated package-lock.json.",
            )

        tree = _tree_hash(installed_root)
        if tree != PRETEXT_PACKAGE_TREE:
            raise _package_integrity_failure("The installed Pretext tree does not match the exact 0.0.8 npm artifact.")
        return {
            "status": "ready",
            "engine": _engine_identity(),
            "package": {
                "name": PRETEXT_PACKAGE,
                "version": PRETEXT_VERSION,
                "resolved": PRETEXT_NPM_RESOLVED,
                "integrity": PRETEXT_NPM_INTEGRITY,
                "tree_sha256": tree["sha256"],
                "tree_bytes": tree["bytes"],
                "tree_files": tree["files"],
            },
            "configuration_sha256": {
                "package_json": _hash_file(package_path, PRETEXT_JSON_MAX_BYTES)["sha256"],
                "package_lock": _hash_file(lock_path, PRETEXT_JSON_MAX_BYTES)["sha256"],
                "installed_package_json": _hash_file(installed_package_path, PRETEXT_JSON_MAX_BYTES)["sha256"],
            },
            "errors": [],
        }
    except PretextFailure as error:
        raise error.attach_report(_failure_evidence(error))


def build_pretext_scope(
    payload: Mapping[str, Any],
    react_screens: Sequence[Mapping[str, Any]],
    output_dir: str | Path,
) -> dict[str, Any]:
    """Derive a deterministic native-DOM observation scope from checked screen manifests."""

    try:
        root = _resolve_directory(output_dir)
        reports: dict[str, Mapping[str, Any]] = {}
        for report in react_screens:
            screen_id = report.get("id") if isinstance(report, Mapping) else None
            if not _valid_id(screen_id) or screen_id in reports:
                raise _scope_failure("React screen reports contain an invalid or duplicate screen identity.")
            reports[screen_id] = report

        routes = payload.get("routes") if isinstance(payload, Mapping) else None
        if not isinstance(routes, list) or not routes:
            raise _scope_failure("AppBundle routes are required to build a Pretext scope.")
        scoped_screens: list[dict[str, Any]] = []
        manifest_surfaces: dict[str, list[dict[str, str]]] = {}
        for route in sorted(routes, key=_route_sort_key):
            if not isinstance(route, Mapping):
                raise _scope_failure("Every AppBundle route must be an object.")
            screen_id = route.get("screen_id")
            route_id = route.get("id")
            route_path = route.get("path")
            if not _valid_id(screen_id) or not _valid_id(route_id) or not _valid_route(route_path):
                raise _scope_failure("AppBundle route identity is invalid for Pretext scope generation.")
            report = reports.get(screen_id)
            if report is None:
                raise _scope_failure("An AppBundle route has no checked React screen report.")
            if screen_id not in manifest_surfaces:
                manifest_path = _checked_manifest_path(root, report)
                manifest = _read_json_object(
                    manifest_path,
                    limit=PRETEXT_MANIFEST_MAX_BYTES,
                    missing_code="APP_PRETEXT_SCOPE_INVALID",
                    invalid_code="APP_PRETEXT_SCOPE_INVALID",
                    label="screen provenance manifest",
                )
                nodes = manifest.get("nodes")
                if not isinstance(nodes, Mapping):
                    raise _scope_failure("A screen provenance manifest must contain a nodes object.")
                if len(nodes) > PRETEXT_MAX_MANIFEST_NODES:
                    raise _scope_failure("A screen provenance manifest exceeds the bounded node count.")
                surfaces: list[dict[str, str]] = []
                for surface_id, item in sorted(nodes.items(), key=lambda pair: str(pair[0])):
                    if not _valid_id(surface_id) or not isinstance(item, Mapping):
                        raise _scope_failure("A screen provenance manifest contains an invalid node entry.")
                    primitive = item.get("primitive")
                    ir_id = item.get("ir_id")
                    if not isinstance(primitive, str) or not _valid_id(ir_id):
                        raise _scope_failure("A screen provenance manifest node identity is invalid.")
                    if primitive not in PRETEXT_VISIBLE_PRIMITIVES:
                        continue
                    surfaces.append({"surface_id": surface_id, "ir_id": ir_id})
                if len(surfaces) > PRETEXT_MAX_SURFACES_PER_SCREEN:
                    raise _scope_failure("A screen exceeds the bounded Pretext surface count.")
                manifest_surfaces[screen_id] = surfaces
            scoped_screens.append(
                {
                    "screen_id": screen_id,
                    "route_id": route_id,
                    "route_path": route_path,
                    "surfaces": [dict(item) for item in manifest_surfaces[screen_id]],
                }
            )
        if len(scoped_screens) > PRETEXT_MAX_SCREENS:
            raise _scope_failure("The Pretext scope exceeds its bounded routed-screen count.")
        viewports = [dict(item) for item in PRETEXT_VIEWPORTS]
        required = len(viewports) * sum(len(screen["surfaces"]) for screen in scoped_screens)
        if required > PRETEXT_MAX_OBSERVATIONS:
            raise _scope_failure("The generated Pretext scope exceeds its bounded observation count.")
        scope = {
            "schema_version": 1,
            "status": "applicable" if required else "not_applicable",
            "profile": PRETEXT_PROFILE,
            "protocol": PRETEXT_PROTOCOL,
            "viewports": viewports,
            "screens": scoped_screens,
            "required_observation_count": required,
        }
        _validate_scope(scope)
        return scope
    except PretextFailure as error:
        raise error.attach_report(_failure_evidence(error))


def validate_pretext_scope(scope: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a detached exact manifest-derived Pretext scope."""

    try:
        validated = _validate_scope(scope)
        return {
            "schema_version": 1,
            "status": validated["status"],
            "profile": PRETEXT_PROFILE,
            "protocol": PRETEXT_PROTOCOL,
            "viewports": [dict(item) for item in validated["viewports"]],
            "screens": [
                {
                    "screen_id": screen["screen_id"],
                    "route_id": screen["route_id"],
                    "route_path": screen["route_path"],
                    "surfaces": [dict(surface) for surface in screen["surfaces"]],
                }
                for screen in scope["screens"]
            ],
            "required_observation_count": len(validated["expected"]),
        }
    except PretextFailure as error:
        raise error.attach_report(_failure_evidence(error))


def validate_pretext_runtime_report(
    report_path: str | Path,
    scope: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate exact runtime coverage and return sanitized, hash-bound evidence."""

    file_evidence: dict[str, Any] = {}
    counts: dict[str, int] | None = None
    try:
        validated_scope = _validate_scope(scope)
        path = Path(report_path).expanduser().resolve()
        report, file_evidence = _read_runtime_report(path)
        _require_exact_keys(report, _REPORT_KEYS, "runtime report", "APP_PRETEXT_PROTOCOL_INVALID")
        if report.get("schema_version") != 1:
            raise _protocol_failure("Runtime report schema_version is not supported.")

        engine = report.get("engine")
        _require_exact_keys(engine, _ENGINE_KEYS, "runtime engine", "APP_PRETEXT_PROTOCOL_INVALID")
        if engine != {"name": "pretext", "package": PRETEXT_PACKAGE, "version": PRETEXT_VERSION}:
            raise _protocol_failure("Runtime engine identity does not match pinned Pretext 0.0.8.")
        if report.get("profile") != PRETEXT_PROFILE or report.get("protocol") != PRETEXT_PROTOCOL:
            raise _protocol_failure("Runtime profile or protocol does not match the requested Pretext contract.")
        report_viewports = _validate_viewports(report.get("viewports"), code="APP_PRETEXT_PROTOCOL_INVALID")
        if report_viewports != validated_scope["viewports"]:
            raise _protocol_failure("Runtime viewports do not exactly match the requested scope.")
        environment = _validate_environment(report.get("environment"))

        items = report.get("items")
        if not isinstance(items, list) or len(items) > PRETEXT_MAX_OBSERVATIONS:
            raise _protocol_failure("Runtime items must be a bounded array.")
        expected = validated_scope["expected"]
        seen: set[tuple[str, str, str, str]] = set()
        sanitized_items: list[dict[str, Any]] = []
        measured_inputs: list[tuple[str, str]] = []
        measured = 0
        hidden = 0
        for item in items:
            sanitized = _validate_runtime_item(item, expected, validated_scope["viewport_ids"])
            key = (
                sanitized["screen_id"],
                sanitized["route_id"],
                sanitized["viewport_id"],
                sanitized["surface_id"],
            )
            if key in seen:
                raise PretextFailure(
                    "APP_PRETEXT_DUPLICATE_EVIDENCE",
                    "Runtime report repeats a required observation identity.",
                    "Emit exactly one item for each scoped route, viewport, and surface.",
                )
            seen.add(key)
            sanitized_items.append(sanitized)
            if sanitized["status"] == "passed":
                measured += 1
                measured_inputs.append((sanitized["input_sha256"], sanitized["viewport_id"]))
            else:
                hidden += 1
        if seen != set(expected):
            raise PretextFailure(
                "APP_PRETEXT_COVERAGE_INCOMPLETE",
                "Runtime report does not exactly account for every scoped observation.",
                "Measure or mark hidden every scoped surface at every viewport exactly once.",
            )
        if measured < 1:
            raise PretextFailure(
                "APP_PRETEXT_COVERAGE_INCOMPLETE",
                "Runtime report contains no measured Pretext observation.",
                "Ensure at least one scoped text surface is visible and measured.",
            )

        summary = _validate_count_object(report.get("summary"), _SUMMARY_KEYS, "runtime summary")
        counts = {
            "required": len(expected),
            "accounted": len(items),
            "measured": measured,
            "hidden": hidden,
            "unsupported": 0,
            "failed": 0,
        }
        if summary != counts:
            raise PretextFailure(
                "APP_PRETEXT_COVERAGE_INCOMPLETE",
                "Runtime summary does not exactly match scoped and observed coverage.",
                "Recompute the summary from the emitted runtime observation items.",
            )
        if report.get("errors") != []:
            raise PretextFailure(
                "APP_PRETEXT_LAYOUT_FAILED",
                "Runtime report contains Pretext errors.",
                "Fix the browser measurement failure and rerun the immutable proof snapshot.",
            )
        cache = _validate_count_object(report.get("cache"), _CACHE_KEYS, "runtime cache")
        unique_inputs = len({digest for digest, _viewport_id in measured_inputs})
        if (
            cache["unique_inputs"] != unique_inputs
            or cache["prepare_calls"] != unique_inputs
            or cache["layout_calls"] != measured
            or cache["prepare_calls"] + cache["cache_hits"] != cache["layout_calls"]
        ):
            raise PretextFailure(
                "APP_PRETEXT_CACHE_INVARIANT",
                "Pretext cache counters contradict measured runtime inputs.",
                "Prepare each unique input once and reuse it for every additional layout width.",
            )
        by_input: dict[str, set[str]] = {}
        for digest, viewport_id in measured_inputs:
            by_input.setdefault(digest, set()).add(viewport_id)
        minimum_cross_width_hits = sum(max(0, len(viewport_ids) - 1) for viewport_ids in by_input.values())
        if cache["cache_hits"] < minimum_cross_width_hits:
            raise PretextFailure(
                "APP_PRETEXT_CACHE_INVARIANT",
                "Pretext did not reuse prepared input across repeated viewport layouts.",
                "Keep width out of the preparation key and reuse prepared text across widths.",
            )

        canonical_observations = sorted(
            sanitized_items,
            key=lambda item: (
                item["screen_id"],
                item["route_id"],
                item["viewport_id"],
                item["surface_id"],
            ),
        )

        return {
            "status": "passed",
            "engine": dict(engine),
            "profile": PRETEXT_PROFILE,
            "protocol": PRETEXT_PROTOCOL,
            "environment": environment,
            "viewports": [dict(item) for item in report_viewports],
            "coverage": counts,
            "cache": cache,
            "items": sanitized_items,
            "observation_digest": _canonical_sha256(canonical_observations),
            "scope_sha256": _canonical_sha256(scope),
            "report_sha256": file_evidence["sha256"],
            "report_bytes": file_evidence["bytes"],
            "errors": [],
        }
    except PretextFailure as error:
        raise error.attach_report(_failure_evidence(error, file_evidence=file_evidence, coverage=counts))


def _validate_scope(scope: Mapping[str, Any]) -> dict[str, Any]:
    _require_exact_keys(scope, _SCOPE_KEYS, "Pretext scope", "APP_PRETEXT_SCOPE_INVALID")
    status = scope.get("status")
    if (
        scope.get("schema_version") != 1
        or status not in {"applicable", "not_applicable"}
        or scope.get("profile") != PRETEXT_PROFILE
        or scope.get("protocol") != PRETEXT_PROTOCOL
    ):
        raise _scope_failure("Pretext scope identity is invalid.")
    viewports = _validate_viewports(scope.get("viewports"), code="APP_PRETEXT_SCOPE_INVALID")
    if viewports != [dict(item) for item in PRETEXT_VIEWPORTS]:
        raise _scope_failure("Pretext scope viewports do not match the canonical support profile.")
    screens = scope.get("screens")
    if not isinstance(screens, list) or not screens or len(screens) > PRETEXT_MAX_SCREENS:
        raise _scope_failure("Pretext scope screens must be a non-empty bounded array.")
    route_ids: set[str] = set()
    route_paths: set[str] = set()
    expected: dict[tuple[str, str, str, str], str] = {}
    for screen in screens:
        _require_exact_keys(screen, _SCREEN_KEYS, "scope screen", "APP_PRETEXT_SCOPE_INVALID")
        screen_id = screen.get("screen_id")
        route_id = screen.get("route_id")
        route_path = screen.get("route_path")
        if not _valid_id(screen_id) or not _valid_id(route_id) or not _valid_route(route_path):
            raise _scope_failure("Pretext scope contains an invalid screen or route identity.")
        if route_id in route_ids or route_path in route_paths:
            raise _scope_failure("Pretext scope repeats a route identity or path.")
        route_ids.add(route_id)
        route_paths.add(route_path)
        surfaces = screen.get("surfaces")
        if not isinstance(surfaces, list) or len(surfaces) > PRETEXT_MAX_SURFACES_PER_SCREEN:
            raise _scope_failure("Scope surfaces must be a bounded array.")
        surface_ids: set[str] = set()
        for surface in surfaces:
            _require_exact_keys(surface, _SURFACE_KEYS, "scope surface", "APP_PRETEXT_SCOPE_INVALID")
            surface_id = surface.get("surface_id")
            ir_id = surface.get("ir_id")
            if not _valid_id(surface_id) or not _valid_id(ir_id) or surface_id in surface_ids:
                raise _scope_failure("Pretext scope contains an invalid or duplicate surface identity.")
            surface_ids.add(surface_id)
            for viewport in viewports:
                expected[(screen_id, route_id, viewport["id"], surface_id)] = ir_id
    required = scope.get("required_observation_count")
    if (
        not _is_int(required)
        or required != len(expected)
        or required < 0
        or required > PRETEXT_MAX_OBSERVATIONS
        or (status == "applicable" and required == 0)
        or (status == "not_applicable" and required != 0)
    ):
        raise _scope_failure("required_observation_count does not exactly match the scope cross-product.")
    return {
        "status": status,
        "viewports": viewports,
        "viewport_ids": frozenset(item["id"] for item in viewports),
        "expected": expected,
    }


def _validate_viewports(value: object, *, code: str) -> list[dict[str, int | str]]:
    if not isinstance(value, list) or not value or len(value) > PRETEXT_MAX_VIEWPORTS:
        raise PretextFailure(
            code, "Viewports must be a non-empty bounded array.", "Use the canonical Pretext viewport set."
        )
    result: list[dict[str, int | str]] = []
    ids: set[str] = set()
    for viewport in value:
        _require_exact_keys(viewport, _VIEWPORT_KEYS, "viewport", code)
        viewport_id = viewport.get("id")
        width = viewport.get("width")
        height = viewport.get("height")
        if (
            not _valid_id(viewport_id)
            or viewport_id in ids
            or not _is_int(width)
            or not _is_int(height)
            or not 1 <= width <= PRETEXT_MAX_VIEWPORT_DIMENSION
            or not 1 <= height <= PRETEXT_MAX_VIEWPORT_DIMENSION
        ):
            raise PretextFailure(
                code, "Viewport identity or dimensions are invalid.", "Use unique bounded canonical viewports."
            )
        ids.add(viewport_id)
        result.append({"id": viewport_id, "width": width, "height": height})
    return result


def _validate_environment(value: object) -> dict[str, Any]:
    _require_exact_keys(value, _ENVIRONMENT_KEYS, "runtime environment", "APP_PRETEXT_PROTOCOL_INVALID")
    locale = value.get("locale")
    scale = value.get("device_scale_factor")
    if (
        value.get("browser") != "chromium"
        or value.get("font_status") != "loaded"
        or not isinstance(locale, str)
        or len(locale) > 64
        or _LOCALE_RE.fullmatch(locale) is None
        or not _finite_number(scale)
        or float(scale) != 1
    ):
        raise _protocol_failure("Runtime environment is not the supported loaded-font Chromium environment.")
    return {
        "browser": "chromium",
        "locale": locale,
        "device_scale_factor": _rounded_number(scale),
        "font_status": "loaded",
    }


def _validate_runtime_item(
    value: object,
    expected: Mapping[tuple[str, str, str, str], str],
    viewport_ids: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _protocol_failure("Every runtime item must be an object.")
    status_value = value.get("status")
    if status_value not in {"passed", "hidden"}:
        raise PretextFailure(
            "APP_PRETEXT_LAYOUT_FAILED",
            "Runtime item status must be passed or hidden.",
            "Resolve unsupported or failed layouts before accepting Pretext evidence.",
        )
    keys = set(value)
    full_keys = _ITEM_IDENTITY_KEYS | _ITEM_METRIC_KEYS
    if status_value == "passed":
        if keys != full_keys:
            raise _protocol_failure("A passed runtime item must include the complete metric set.")
    elif keys not in (_ITEM_IDENTITY_KEYS, full_keys):
        raise _protocol_failure("A hidden runtime item has partial or unsupported fields.")

    identities = [value.get(key) for key in ("screen_id", "route_id", "viewport_id", "surface_id", "ir_id")]
    if any(not _valid_id(item) for item in identities) or identities[2] not in viewport_ids:
        raise _protocol_failure("Runtime item contains an invalid observation identity.")
    key = (identities[0], identities[1], identities[2], identities[3])
    if key not in expected or expected[key] != identities[4]:
        raise PretextFailure(
            "APP_PRETEXT_COVERAGE_INCOMPLETE",
            "Runtime item is outside the requested scope or has the wrong IR identity.",
            "Generate evidence only from the immutable manifest-derived Pretext scope.",
        )
    digest = value.get("input_sha256")
    input_bytes = value.get("input_bytes")
    if (
        not isinstance(digest, str)
        or _SHA256_RE.fullmatch(digest) is None
        or not _is_int(input_bytes)
        or not 0 <= input_bytes <= PRETEXT_MAX_INPUT_BYTES
    ):
        raise _protocol_failure("Runtime item input hash or byte count is invalid.")
    sanitized: dict[str, Any] = {
        "screen_id": identities[0],
        "route_id": identities[1],
        "viewport_id": identities[2],
        "surface_id": identities[3],
        "ir_id": identities[4],
        "input_sha256": digest,
        "input_bytes": input_bytes,
        "status": status_value,
    }
    if keys == full_keys:
        available_width = value.get("available_width")
        line_height = value.get("line_height")
        predicted = value.get("predicted_line_count")
        observed = value.get("observed_line_count")
        if (
            not _finite_number(available_width)
            or not _finite_number(line_height)
            or float(available_width) <= 0
            or float(line_height) <= 0
            or not _is_int(predicted)
            or not _is_int(observed)
            or predicted < 1
            or observed < 1
        ):
            raise PretextFailure(
                "APP_PRETEXT_LAYOUT_MISMATCH",
                "Runtime item has invalid finite layout metrics.",
                "Measure the visible surface after fonts load and emit bounded numeric metrics.",
            )
        if (
            predicted != observed
            or value.get("horizontal_overflow") is not False
            or value.get("vertical_overflow") is not False
        ):
            raise PretextFailure(
                "APP_PRETEXT_LAYOUT_MISMATCH",
                "Pretext prediction does not exactly match observed layout or the surface overflows.",
                "Fix the responsive text surface and rerun the browser evidence pass.",
            )
        sanitized.update(
            {
                "available_width": _rounded_number(available_width),
                "line_height": _rounded_number(line_height),
                "predicted_line_count": predicted,
                "observed_line_count": observed,
                "horizontal_overflow": False,
                "vertical_overflow": False,
            }
        )
    return sanitized


def _validate_count_object(value: object, keys: set[str], label: str) -> dict[str, int]:
    _require_exact_keys(value, keys, label, "APP_PRETEXT_PROTOCOL_INVALID")
    result: dict[str, int] = {}
    for key in sorted(keys):
        item = value.get(key)
        if not _is_int(item) or item < 0 or item > PRETEXT_MAX_OBSERVATIONS:
            raise _protocol_failure(f"{label} counters must be bounded non-negative integers.")
        result[key] = item
    return result


def _read_runtime_report(path: Path) -> tuple[Mapping[str, Any], dict[str, Any]]:
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise PretextFailure(
                "APP_PRETEXT_REPORT_MISSING",
                "The Pretext runtime report is not a regular file.",
                "Regenerate the exact bounded runtime report.",
            )
        with path.open("rb") as handle:
            raw = handle.read(PRETEXT_RUNTIME_REPORT_MAX_BYTES + 1)
        if len(raw) > PRETEXT_RUNTIME_REPORT_MAX_BYTES:
            raise PretextFailure(
                "APP_PRETEXT_REPORT_TOO_LARGE",
                "The Pretext runtime report exceeds its byte bound.",
                "Regenerate bounded runtime evidence and retry.",
            )
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_reject_duplicate_keys)
    except PretextFailure:
        raise
    except _DuplicateKey as error:
        raise PretextFailure(
            "APP_PRETEXT_DUPLICATE_EVIDENCE",
            "Runtime report JSON contains a duplicate object key.",
            "Emit strict JSON with each field exactly once.",
        ) from error
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PretextFailure(
            "APP_PRETEXT_REPORT_INVALID",
            "Runtime report is not strict UTF-8 JSON.",
            "Regenerate the bounded Pretext runtime report.",
        ) from error
    if not isinstance(value, Mapping):
        raise PretextFailure(
            "APP_PRETEXT_REPORT_INVALID",
            "Runtime report root must be an object.",
            "Regenerate the bounded Pretext runtime report.",
        )
    evidence = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    return value, evidence


def _read_json_object(
    path: Path,
    *,
    limit: int,
    missing_code: str,
    invalid_code: str,
    label: str,
) -> Mapping[str, Any]:
    _hash_file(path, limit, missing_code=missing_code, limit_code=invalid_code)
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="strict"), object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, _DuplicateKey) as error:
        raise PretextFailure(
            invalid_code,
            f"{label} is not strict duplicate-free UTF-8 JSON.",
            "Regenerate the exact bounded Pretext proof input.",
        ) from error
    if not isinstance(value, Mapping):
        raise PretextFailure(
            invalid_code,
            f"{label} root must be an object.",
            "Regenerate the exact bounded Pretext proof input.",
        )
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _tree_hash(root: Path) -> dict[str, Any]:
    try:
        root_mode = root.lstat().st_mode
    except OSError as error:
        raise PretextFailure(
            "APP_PRETEXT_PACKAGE_MISSING",
            "The installed Pretext package directory is missing.",
            "Install from the unchanged generated package-lock.json.",
        ) from error
    if not stat.S_ISDIR(root_mode):
        raise _package_integrity_failure("The installed Pretext package root is symlinked or not a directory.")
    paths: list[Path] = []
    for current_root, directory_names, file_names in os.walk(root, followlinks=False):
        current = Path(current_root)
        for name in directory_names:
            path = current / name
            if path.is_symlink():
                raise _package_integrity_failure("The installed Pretext tree contains a symlinked directory.")
        for name in file_names:
            path = current / name
            try:
                mode = path.lstat().st_mode
            except OSError as error:
                raise _package_integrity_failure("The installed Pretext tree changed while it was read.") from error
            if not stat.S_ISREG(mode):
                raise _package_integrity_failure("The installed Pretext tree contains a non-regular file.")
            paths.append(path)
    if len(paths) > PRETEXT_PACKAGE_TREE_MAX_FILES:
        raise _package_integrity_failure("The installed Pretext tree exceeds its file-count bound.")
    paths.sort(key=lambda path: path.relative_to(root).as_posix())
    digest = hashlib.sha256()
    total = 0
    for path in paths:
        item = _hash_file(path, PRETEXT_PACKAGE_TREE_MAX_BYTES)
        total += item["bytes"]
        if total > PRETEXT_PACKAGE_TREE_MAX_BYTES:
            raise _package_integrity_failure("The installed Pretext tree exceeds its byte bound.")
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item["bytes"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(item["sha256"]))
        digest.update(b"\0")
    return {"sha256": digest.hexdigest(), "bytes": total, "files": len(paths)}


def _hash_file(
    path: Path,
    limit: int,
    *,
    missing_code: str = "APP_PRETEXT_PACKAGE_INTEGRITY",
    limit_code: str = "APP_PRETEXT_PACKAGE_INTEGRITY",
) -> dict[str, Any]:
    try:
        mode = path.lstat().st_mode
        size = path.stat().st_size
    except OSError as error:
        raise PretextFailure(
            missing_code, "A required Pretext proof input is missing.", "Regenerate the exact proof input."
        ) from error
    if not stat.S_ISREG(mode):
        raise PretextFailure(
            missing_code, "A required Pretext proof input is not a regular file.", "Regenerate the exact proof input."
        )
    if size > limit:
        raise PretextFailure(
            limit_code, "A Pretext proof input exceeds its byte bound.", "Regenerate bounded evidence and retry."
        )
    digest = hashlib.sha256()
    counted = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                counted += len(chunk)
                if counted > limit:
                    raise PretextFailure(
                        limit_code,
                        "A Pretext proof input grew beyond its byte bound.",
                        "Stabilize the input and retry.",
                    )
                digest.update(chunk)
    except OSError as error:
        raise PretextFailure(
            missing_code, "A required Pretext proof input could not be read.", "Regenerate the exact proof input."
        ) from error
    return {"sha256": digest.hexdigest(), "bytes": counted}


def _checked_manifest_path(root: Path, report: Mapping[str, Any]) -> Path:
    paths = report.get("paths")
    raw_path = paths.get("manifest") if isinstance(paths, Mapping) else None
    if not isinstance(raw_path, str):
        raise _scope_failure("A React screen report has no provenance manifest path.")
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise _scope_failure("A React screen manifest escapes the generated app directory.") from error
    return candidate


def _resolve_directory(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise PretextFailure(
            "APP_PRETEXT_PACKAGE_MISSING",
            "The requested Pretext app directory does not exist.",
            "Pass the generated React app directory.",
        )
    return path


def _require_exact_keys(value: object, expected: set[str], label: str, code: str) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        fix = "Regenerate the exact manifest-derived Pretext evidence contract."
        raise PretextFailure(code, f"{label} has missing or unsupported fields.", fix)


def _valid_id(value: object) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= PRETEXT_MAX_ID_CHARS and _ID_RE.fullmatch(value) is not None


def _valid_route(value: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= PRETEXT_MAX_ROUTE_CHARS
        and value.startswith("/")
        and "\\" not in value
        and "\x00" not in value
    )


def _route_sort_key(route: object) -> tuple[str, str, str]:
    if not isinstance(route, Mapping):
        return ("", "", "")
    return (str(route.get("path", "")), str(route.get("id", "")), str(route.get("screen_id", "")))


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _rounded_number(value: int | float) -> int | float:
    if _is_int(value):
        return value
    return round(float(value), 6)


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _engine_identity() -> dict[str, str]:
    return {
        "name": "pretext",
        "package": PRETEXT_PACKAGE,
        "version": PRETEXT_VERSION,
        "integrity": PRETEXT_NPM_INTEGRITY,
        "package_tree_sha256": PRETEXT_PACKAGE_TREE["sha256"],
        "profile": PRETEXT_PROFILE,
        "protocol": PRETEXT_PROTOCOL,
    }


def _failure_evidence(
    error: PretextFailure,
    *,
    file_evidence: Mapping[str, Any] | None = None,
    coverage: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    evidence = dict(file_evidence or {})
    return {
        "status": "failed",
        "engine": _engine_identity(),
        "profile": PRETEXT_PROFILE,
        "protocol": PRETEXT_PROTOCOL,
        "coverage": dict(coverage or {}),
        **({"report_sha256": evidence["sha256"]} if isinstance(evidence.get("sha256"), str) else {}),
        **({"report_bytes": evidence["bytes"]} if _is_int(evidence.get("bytes")) else {}),
        "errors": [error.to_json()],
    }


def _scope_failure(message: str) -> PretextFailure:
    return PretextFailure(
        "APP_PRETEXT_SCOPE_INVALID",
        message,
        "Regenerate the scope from checked React screen manifests; do not hand-edit it.",
    )


def _protocol_failure(message: str) -> PretextFailure:
    return PretextFailure(
        "APP_PRETEXT_PROTOCOL_INVALID",
        message,
        "Regenerate the runtime report with the pinned ViewSpec Pretext harness.",
    )


def _package_integrity_failure(message: str) -> PretextFailure:
    return PretextFailure(
        "APP_PRETEXT_PACKAGE_INTEGRITY",
        message,
        "Regenerate the package and install only from its unchanged package-lock.json.",
    )


__all__ = [
    "PRETEXT_JSON_MAX_BYTES",
    "PRETEXT_MANIFEST_MAX_BYTES",
    "PRETEXT_MAX_MANIFEST_NODES",
    "PRETEXT_MAX_OBSERVATIONS",
    "PRETEXT_NPM_INTEGRITY",
    "PRETEXT_NPM_RESOLVED",
    "PRETEXT_PACKAGE",
    "PRETEXT_PACKAGE_TREE",
    "PRETEXT_PACKAGE_TREE_MAX_BYTES",
    "PRETEXT_PACKAGE_TREE_MAX_FILES",
    "PRETEXT_PROFILE",
    "PRETEXT_PROTOCOL",
    "PRETEXT_RUNTIME_REPORT_MAX_BYTES",
    "PRETEXT_VERSION",
    "PRETEXT_VIEWPORTS",
    "PretextFailure",
    "build_pretext_scope",
    "validate_pretext_installation",
    "validate_pretext_scope",
    "validate_pretext_runtime_report",
]
