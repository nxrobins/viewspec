#!/usr/bin/env python3
"""Measure how often a model produces valid ViewSpec agent JSON.

The script sends every prompt in ``conformance/eval/prompts.json`` to a model together with the
SDK's agent system prompt, validates each answer with the SDK (``validate_intent_text`` for an
IntentBundle, ``validate_app_text`` for an AppBundle), feeds the correction prompt back once, and
writes ``eval_report.json`` and ``eval_report.md`` into the output directory.

It calls a model only when ``VIEWSPEC_EVAL_MODEL`` and the provider key (``ANTHROPIC_API_KEY``) are
set. Without them it exits 2 with one line of explanation, unless ``--dry-run`` asks for the plan
alone. The SDK never imports this file and performs no model calls; the test suite drives the
harness with fake providers only.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import http.client
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import viewspec
from viewspec.aesthetics import AESTHETIC_PROFILE_TOKENS
from viewspec.agent import AGENT_INTENT_BUNDLE_SCHEMA, AGENT_SYSTEM_PROMPT
from viewspec.app_bundle import AGENT_APP_BUNDLE_SCHEMA, validate_app_text
from viewspec.app_validation import APP_BUNDLE_SUPPORTED_SCHEMA_VERSIONS
from viewspec.compiler import SUPPORTED_ACTION_KINDS, SUPPORTED_MOTIF_KINDS
from viewspec.intent_tools import validate_intent_text

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPTS = ROOT / "conformance" / "eval" / "prompts.json"
DEFAULT_OUT_ROOT = Path(".agent-eval-runs")
# The model is configuration only: a run needs VIEWSPEC_EVAL_MODEL, and no model id lives here.
MODEL_ENV = "VIEWSPEC_EVAL_MODEL"
DEFAULT_PROVIDER = "anthropic"
PROVIDER_KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY"}
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
# API version 2026-10-21 rejects temperature, top_p and top_k on every model. 2023-06-01 lets an
# explicit --temperature reach the models that still accept one.
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 16000
DEFAULT_TIMEOUT_SECONDS = 600.0
TRANSPORT_RETRIES = 2
RETRYABLE_HTTP_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
MAX_RETRY_AFTER_SECONDS = 60.0
MAX_ERROR_SUMMARY_CHARS = 300
PROMPT_SET_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
PLAN_SCHEMA_VERSION = 1
PROMPT_KINDS = ("intent", "app")
PROMPT_REQUIRED_FIELDS = frozenset({"id", "kind", "prompt", "expected_motifs"})
PROMPT_OPTIONAL_FIELDS = frozenset(
    {"expected_app_schema_version", "expected_action_kinds", "expected_aesthetic_profile"}
)
PROMPT_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MIN_PROMPTS = 30
MAX_PROMPTS = 40
MAX_CORRECTION_ISSUES = 20
MAX_CORRECTION_PATHS = 20
TOP_ISSUE_CODES = 10
RESPONSES_DIR = "responses"
TEMPERATURE_RANGE = (0.0, 1.0)

# Keywords the structured-output grammar does not accept. The official SDKs remove the same
# constraints before sending a schema and validate them client side; here the SDK validator is
# that client-side check.
STRUCTURED_OUTPUT_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "$id",
        "$schema",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "multipleOf",
        "pattern",
        "propertyNames",
        "uniqueItems",
    }
)
STRUCTURED_OUTPUT_FORMATS = frozenset(
    {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}
)
_SCHEMA_MAP_KEYWORDS = frozenset({"properties", "$defs", "definitions"})
_SCHEMA_LITERAL_KEYWORDS = frozenset({"enum", "const", "required", "default", "examples", "title", "description"})


# --- prompt set ----------------------------------------------------------------------------------


class PromptSetError(ValueError):
    """The prompt set or the prompt selection is unusable."""


@dataclass(frozen=True)
class EvalPrompt:
    id: str
    kind: str
    prompt: str
    expected_motifs: tuple[str, ...]
    expected_app_schema_version: int | None = None
    expected_action_kinds: tuple[str, ...] = ()
    expected_aesthetic_profile: str | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> EvalPrompt:
        return cls(
            id=data["id"],
            kind=data["kind"],
            prompt=data["prompt"],
            expected_motifs=tuple(data["expected_motifs"]),
            expected_app_schema_version=data.get("expected_app_schema_version"),
            expected_action_kinds=tuple(data.get("expected_action_kinds", ())),
            expected_aesthetic_profile=data.get("expected_aesthetic_profile"),
        )

    def expected(self) -> dict[str, Any]:
        return {
            "motifs": list(self.expected_motifs),
            "action_kinds": list(self.expected_action_kinds),
            "aesthetic_profile": self.expected_aesthetic_profile,
            "app_schema_version": self.expected_app_schema_version,
        }


def validate_prompt_set(data: Any) -> list[str]:
    """Return every problem with a prompt set document; an empty list means it is usable."""
    if not isinstance(data, dict):
        return ["prompt set must be a JSON object"]
    problems: list[str] = []
    if data.get("schema_version") != PROMPT_SET_SCHEMA_VERSION:
        problems.append(f"schema_version must be {PROMPT_SET_SCHEMA_VERSION}")
    prompts = data.get("prompts")
    if not isinstance(prompts, list):
        problems.append("prompts must be an array")
        return problems
    if not MIN_PROMPTS <= len(prompts) <= MAX_PROMPTS:
        problems.append(f"prompt set holds {len(prompts)} prompts; expected {MIN_PROMPTS} to {MAX_PROMPTS}")
    seen_ids: set[str] = set()
    seen_kinds: set[str] = set()
    for index, entry in enumerate(prompts):
        label = f"prompts[{index}]"
        if not isinstance(entry, dict):
            problems.append(f"{label} must be an object")
            continue
        prompt_id = entry.get("id")
        label = f"{label} ({prompt_id!r})"
        missing = sorted(PROMPT_REQUIRED_FIELDS - entry.keys())
        unknown = sorted(entry.keys() - PROMPT_REQUIRED_FIELDS - PROMPT_OPTIONAL_FIELDS)
        if missing:
            problems.append(f"{label} lacks {', '.join(missing)}")
        if unknown:
            problems.append(f"{label} has unknown fields {', '.join(unknown)}")
        if not isinstance(prompt_id, str) or not PROMPT_ID_RE.match(prompt_id):
            problems.append(f"{label} id must be a lowercase slug of letters, digits and single dashes")
        elif prompt_id in seen_ids:
            problems.append(f"{label} duplicates an earlier id")
        else:
            seen_ids.add(prompt_id)
        kind = entry.get("kind")
        if kind not in PROMPT_KINDS:
            problems.append(f"{label} kind must be one of {', '.join(PROMPT_KINDS)}")
        else:
            seen_kinds.add(kind)
        prompt = entry.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            problems.append(f"{label} prompt must be a non-empty string")
        motifs = entry.get("expected_motifs")
        if not isinstance(motifs, list) or not motifs:
            problems.append(f"{label} expected_motifs must be a non-empty array")
        else:
            bad = [motif for motif in motifs if motif not in SUPPORTED_MOTIF_KINDS]
            if bad or len(set(motifs)) != len(motifs):
                problems.append(f"{label} expected_motifs must be distinct v1 motif kinds")
        actions = entry.get("expected_action_kinds", [])
        if not isinstance(actions, list) or any(action not in SUPPORTED_ACTION_KINDS for action in actions):
            problems.append(f"{label} expected_action_kinds must be v1 action kinds")
        aesthetic = entry.get("expected_aesthetic_profile")
        if aesthetic is not None and aesthetic not in AESTHETIC_PROFILE_TOKENS:
            problems.append(f"{label} expected_aesthetic_profile must be an aesthetic profile token")
        version = entry.get("expected_app_schema_version")
        if kind == "app":
            if not isinstance(version, int) or isinstance(version, bool):
                problems.append(f"{label} app prompts need an integer expected_app_schema_version")
            elif version not in APP_BUNDLE_SUPPORTED_SCHEMA_VERSIONS:
                problems.append(f"{label} expected_app_schema_version is not a supported AppBundle schema version")
        elif "expected_app_schema_version" in entry:
            problems.append(f"{label} intent prompts must not declare expected_app_schema_version")
    if seen_kinds != set(PROMPT_KINDS):
        problems.append("prompt set must hold both intent and app prompts")
    return problems


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def load_prompt_set(path: Path) -> tuple[list[EvalPrompt], str]:
    """Load and validate a prompt set; return the prompts and the sha256 of the file bytes."""
    raw = path.read_bytes()
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise PromptSetError(f"{path} is not JSON: {exc}") from None
    problems = validate_prompt_set(data)
    if problems:
        raise PromptSetError(
            f"{path} is not a usable prompt set:\n" + "\n".join(f"- {problem}" for problem in problems)
        )
    return [EvalPrompt.from_json(entry) for entry in data["prompts"]], sha256_bytes(raw)


def select_prompts(prompts: Sequence[EvalPrompt], ids: Sequence[str] | None, limit: int | None) -> list[EvalPrompt]:
    """Keep the requested ids in file order, then apply the limit."""
    selected = list(prompts)
    if ids:
        wanted = set(ids)
        known = {prompt.id for prompt in prompts}
        unknown = sorted(wanted - known)
        if unknown:
            raise PromptSetError(f"unknown prompt ids: {', '.join(unknown)}")
        selected = [prompt for prompt in selected if prompt.id in wanted]
    if limit is not None:
        if limit < 1:
            raise PromptSetError("--limit must be at least 1")
        selected = selected[:limit]
    return selected


# --- provider ------------------------------------------------------------------------------------


class ProviderError(RuntimeError):
    """The provider returned no message for a request."""


class Provider(Protocol):
    name: str

    def generate(self, system: str, user: str, schema: dict[str, Any] | None) -> str: ...


def request_options(max_tokens: int, timeout: float, temperature: float | None) -> dict[str, Any]:
    """The request facts the plan and the report record; ``temperature`` is None when none is sent."""
    return {
        "anthropic_version": ANTHROPIC_VERSION,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "timeout_seconds": timeout,
        "transport_retries": TRANSPORT_RETRIES,
    }


def _error_summary(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        error = data["error"]
        text = f"{error.get('type', 'error')}: {error.get('message', '')}"
    return " ".join(text.split())[:MAX_ERROR_SUMMARY_CHARS]


def _backoff_seconds(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass
    return min(float(2**attempt), MAX_RETRY_AFTER_SECONDS)


@dataclass
class AnthropicMessagesProvider:
    """One ``POST /v1/messages`` per generation, through ``urllib`` alone."""

    api_key: str
    model: str
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    # Sent only when set. A model that takes no sampling parameters answers HTTP 400, which the run
    # records as a provider error.
    temperature: float | None = None
    url: str = ANTHROPIC_MESSAGES_URL
    transport_retries: int = TRANSPORT_RETRIES
    # Resolved at call time so a test can replace urllib.request.urlopen after import.
    opener: Callable[..., Any] | None = None
    sleep: Callable[[float], None] = time.sleep
    last_meta: dict[str, Any] = field(default_factory=dict)

    name = "anthropic"

    def request_body(self, system: str, user: str, schema: dict[str, Any] | None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if schema is not None:
            body["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        return body

    def generate(self, system: str, user: str, schema: dict[str, Any] | None) -> str:
        payload = json.dumps(self.request_body(system, user, schema)).encode("utf-8")
        response, request_id = self._post(payload)
        content = response.get("content")
        blocks = content if isinstance(content, list) else []
        text = "".join(
            block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("type") == "text"
        )
        usage = response.get("usage")
        self.last_meta = {
            "stop_reason": response.get("stop_reason"),
            "usage": (
                {key: usage[key] for key in ("input_tokens", "output_tokens") if key in usage}
                if isinstance(usage, dict)
                else None
            ),
            "served_model": response.get("model"),
            "message_id": response.get("id"),
            "request_id": request_id,
        }
        return text

    def _post(self, payload: bytes) -> tuple[dict[str, Any], str | None]:
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
        opener = self.opener or urllib.request.urlopen
        attempt = 0
        while True:
            request = urllib.request.Request(self.url, data=payload, headers=headers, method="POST")
            try:
                with opener(request, timeout=self.timeout) as response:
                    raw = response.read()
                    response_headers = getattr(response, "headers", None)
            except urllib.error.HTTPError as exc:
                detail = f"HTTP {exc.code}: {_error_summary(exc.read())}"
                if exc.code in RETRYABLE_HTTP_STATUS and attempt < self.transport_retries:
                    attempt += 1
                    self.sleep(_backoff_seconds(attempt, exc.headers.get("retry-after") if exc.headers else None))
                    continue
                raise ProviderError(detail) from None
            except (OSError, http.client.HTTPException) as exc:
                if attempt < self.transport_retries:
                    attempt += 1
                    self.sleep(_backoff_seconds(attempt, None))
                    continue
                raise ProviderError(f"connection error: {type(exc).__name__}: {exc}") from None
            try:
                data = json.loads(raw)
            except ValueError:
                raise ProviderError("response body is not JSON") from None
            if not isinstance(data, dict):
                raise ProviderError("response body is not a JSON object")
            if data.get("type") == "error":
                raise ProviderError(_error_summary(raw))
            request_id = response_headers.get("request-id") if response_headers is not None else None
            return data, (request_id if isinstance(request_id, str) else None)


# --- structured output schemas -------------------------------------------------------------------


def structured_output_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of ``schema`` without the keywords the structured-output grammar rejects.

    ``oneOf`` becomes ``anyOf``. Property names and ``$defs`` keys are never filtered, only schema
    keywords are. Map-typed objects (``additionalProperties`` holding a schema) stay as they are;
    :func:`structured_output_warnings` reports them.
    """

    def strip(node: Any) -> Any:
        if isinstance(node, list):
            return [strip(item) for item in node]
        if not isinstance(node, dict):
            return node
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in STRUCTURED_OUTPUT_UNSUPPORTED_KEYWORDS or key.startswith("x-"):
                continue
            if key == "format" and value not in STRUCTURED_OUTPUT_FORMATS:
                continue
            target = "anyOf" if key == "oneOf" else key
            if key in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
                out[target] = {name: strip(subschema) for name, subschema in value.items()}
            elif key in _SCHEMA_LITERAL_KEYWORDS:
                out[target] = copy.deepcopy(value)
            else:
                out[target] = strip(value)
        return out

    return strip(schema)


def structured_output_warnings(schema: Mapping[str, Any]) -> list[str]:
    """Facts about ``schema`` that structured output may still reject."""
    maps = 0

    def walk(node: Any) -> None:
        nonlocal maps
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        additional = node.get("additionalProperties")
        if isinstance(additional, dict) or additional is True:
            maps += 1
        for value in node.values():
            walk(value)

    walk(schema)
    warnings: list[str] = []
    if maps:
        warnings.append(
            f"{maps} object schemas use additionalProperties other than false (JSON maps), which structured "
            "output may reject; if the provider returns HTTP 400 for the schema, rerun without "
            "--structured-output or --schema"
        )
    return warnings


def resolve_schemas(
    schema_path: str | None, *, structured_output: bool
) -> tuple[dict[str, dict[str, Any] | None], dict[str, Any]]:
    """The schema sent for each prompt kind, and the facts the plan and report record.

    Structured output is off unless ``structured_output`` asks for the SDK schemas or
    ``schema_path`` names a file, which is sent as given. When it is off, every kind gets None and
    the facts are ``structured_output: False`` with null ``schemas`` and ``schema_warnings``.
    """
    if schema_path is not None:
        raw = Path(schema_path).read_bytes()
        schema = json.loads(raw)
        if not isinstance(schema, dict):
            raise PromptSetError(f"{schema_path} must hold a JSON Schema object")
        source = {"source": f"file:{schema_path}", "sha256": sha256_bytes(raw)}
        warnings = structured_output_warnings(schema)
        return dict.fromkeys(PROMPT_KINDS, schema), {
            "structured_output": True,
            "schemas": {kind: dict(source) for kind in PROMPT_KINDS},
            "schema_warnings": {kind: list(warnings) for kind in PROMPT_KINDS},
        }
    if not structured_output:
        return dict.fromkeys(PROMPT_KINDS), {"structured_output": False, "schemas": None, "schema_warnings": None}
    sources = {"intent": AGENT_INTENT_BUNDLE_SCHEMA, "app": AGENT_APP_BUNDLE_SCHEMA}
    names = {"intent": "sdk:AGENT_INTENT_BUNDLE_SCHEMA", "app": "sdk:AGENT_APP_BUNDLE_SCHEMA"}
    schemas = {kind: structured_output_schema(source) for kind, source in sources.items()}
    return schemas, {
        "structured_output": True,
        "schemas": {
            kind: {
                "source": names[kind],
                "sha256": sha256_text(json.dumps(schema, sort_keys=True, separators=(",", ":"))),
            }
            for kind, schema in schemas.items()
        },
        "schema_warnings": {kind: structured_output_warnings(schema) for kind, schema in schemas.items()},
    }


# --- validation and observation ------------------------------------------------------------------


def validate_generation(kind: str, text: str) -> dict[str, Any]:
    if kind == "intent":
        return validate_intent_text(text)
    if kind == "app":
        return validate_app_text(text)
    raise ValueError(f"unknown prompt kind {kind!r}")


def issue_codes(validation: Mapping[str, Any]) -> list[str]:
    issues = validation.get("issues")
    if not isinstance(issues, list):
        return []
    return sorted({issue["code"] for issue in issues if isinstance(issue, dict) and isinstance(issue.get("code"), str)})


def fix_codes(validation: Mapping[str, Any]) -> list[str]:
    """Codes under a ``fixes`` key when the envelope carries one; today's envelopes do not."""
    fixes = validation.get("fixes")
    if not isinstance(fixes, list):
        return []
    return sorted({fix["code"] for fix in fixes if isinstance(fix, dict) and isinstance(fix.get("code"), str)})


def bounded_correction_prompt(kind: str, validation: Mapping[str, Any]) -> str:
    """A correction prompt shaped like the SDK's, for envelopes that carry none (AppBundles)."""
    issues = [issue for issue in validation.get("issues", []) if isinstance(issue, dict)]
    shown = issues[:MAX_CORRECTION_ISSUES]
    paths: list[str] = []
    for issue in issues:
        path = issue.get("path")
        if isinstance(path, str) and path not in paths:
            paths.append(path)
    document = "AppBundle" if kind == "app" else "IntentBundle"
    file_name = "viewspec.app.json" if kind == "app" else "viewspec.intent.json"
    report = {
        "issue_count": len(issues),
        "shown_issue_count": len(shown),
        "truncated": len(issues) > len(shown),
        "issue_codes": issue_codes(validation),
        "affected_paths": paths[:MAX_CORRECTION_PATHS],
        "repair_mode": f"regenerate_full_{document.lower().replace('bundle', '_bundle')}",
        "retry_command": f"viewspec check {file_name} --json",
        "issues": shown,
    }
    return (
        f"Regenerate the full ViewSpec {document} JSON. Output strict JSON only. "
        "Do not patch fragments. Fix this bounded validation report:\n"
        f"{json.dumps(report, separators=(',', ':'), sort_keys=True)}"
    )


def correction_prompt(kind: str, validation: Mapping[str, Any]) -> str:
    prompt = validation.get("correction_prompt")
    if isinstance(prompt, str) and prompt:
        return prompt
    return bounded_correction_prompt(kind, validation)


def retry_user_message(prompt: str, previous_output: str, correction: str) -> str:
    return f"{prompt}\n\nYour previous answer was:\n{previous_output}\n\n{correction}"


def parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def compact_bytes(parsed: Mapping[str, Any]) -> int:
    return len(json.dumps(parsed, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8"))


def _intent_bundles(kind: str, parsed: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if kind == "intent":
        return [parsed]
    screens = parsed.get("screens")
    if not isinstance(screens, list):
        return []
    return [
        screen["intent_bundle"]
        for screen in screens
        if isinstance(screen, dict) and isinstance(screen.get("intent_bundle"), dict)
    ]


def observe(kind: str, parsed: Mapping[str, Any]) -> dict[str, Any]:
    """The contract facts a parsed answer declares, read defensively from untrusted JSON."""
    motifs: set[str] = set()
    actions: set[str] = set()
    aesthetic: str | None = None
    for bundle in _intent_bundles(kind, parsed):
        view_spec = bundle.get("view_spec")
        if not isinstance(view_spec, dict):
            continue
        for key, found in (("motifs", motifs), ("actions", actions)):
            entries = view_spec.get(key)
            if isinstance(entries, list):
                found.update(
                    entry["kind"] for entry in entries if isinstance(entry, dict) and isinstance(entry.get("kind"), str)
                )
        styles = view_spec.get("styles")
        if isinstance(styles, list):
            for style in styles:
                token = style.get("token") if isinstance(style, dict) else None
                if aesthetic is None and isinstance(token, str) and token.startswith("aesthetic."):
                    aesthetic = token
    version = parsed.get("schema_version") if kind == "app" else None
    return {
        "motif_kinds": sorted(motifs),
        "action_kinds": sorted(actions),
        "aesthetic_profile": aesthetic,
        "app_schema_version": version if isinstance(version, int) and not isinstance(version, bool) else None,
    }


def expectations_met(prompt: EvalPrompt, observed: Mapping[str, Any]) -> bool:
    if not set(prompt.expected_motifs) <= set(observed["motif_kinds"]):
        return False
    if not set(prompt.expected_action_kinds) <= set(observed["action_kinds"]):
        return False
    if (
        prompt.expected_aesthetic_profile is not None
        and observed["aesthetic_profile"] != prompt.expected_aesthetic_profile
    ):
        return False
    return (
        prompt.expected_app_schema_version is None
        or observed["app_schema_version"] == prompt.expected_app_schema_version
    )


# --- running -------------------------------------------------------------------------------------


def _elapsed_ms(start: float, end: float) -> int:
    return max(int(round((end - start) * 1000)), 0)


def run_attempt(
    prompt: EvalPrompt,
    provider: Provider,
    *,
    attempt: int,
    system_prompt: str,
    user: str,
    schema: dict[str, Any] | None,
    clock: Callable[[], float] = time.perf_counter,
) -> tuple[dict[str, Any], str | None, dict[str, Any] | None]:
    """One generation plus its validation: the record, the raw text, and the validation payload."""
    record: dict[str, Any] = {
        "attempt": attempt,
        "status": "provider_error",
        "ok": False,
        "issue_codes": [],
        "issue_count": 0,
        "fix_codes": [],
        "parsed_json": False,
        "raw_bytes": 0,
        "compact_bytes": None,
        "elapsed_ms": 0,
        "stop_reason": None,
        "usage": None,
        "served_model": None,
        "message_id": None,
        "request_id": None,
        "error": None,
        "observed": None,
        "expectations_met": None,
        "response_file": None,
    }
    start = clock()
    try:
        text = provider.generate(system_prompt, user, schema)
    except ProviderError as exc:
        record["elapsed_ms"] = _elapsed_ms(start, clock())
        record["error"] = str(exc)
        return record, None, None
    record["elapsed_ms"] = _elapsed_ms(start, clock())
    meta = dict(getattr(provider, "last_meta", None) or {})
    validation = validate_generation(prompt.kind, text)
    parsed = parse_json_object(text)
    observed = observe(prompt.kind, parsed) if parsed is not None else None
    ok = bool(validation.get("ok"))
    issues = validation.get("issues")
    record.update(
        {
            "status": "valid" if ok else "invalid",
            "ok": ok,
            "issue_codes": issue_codes(validation),
            "issue_count": len(issues) if isinstance(issues, list) else 0,
            "fix_codes": fix_codes(validation),
            "parsed_json": parsed is not None,
            "raw_bytes": len(text.encode("utf-8")),
            "compact_bytes": compact_bytes(parsed) if parsed is not None else None,
            "stop_reason": meta.get("stop_reason"),
            "usage": meta.get("usage"),
            "served_model": meta.get("served_model"),
            "message_id": meta.get("message_id"),
            "request_id": meta.get("request_id"),
            "observed": observed,
            "expectations_met": expectations_met(prompt, observed) if observed is not None else None,
        }
    )
    return record, text, validation


def _write_response(out_dir: Path, prompt_id: str, attempt: int, text: str) -> str:
    responses = out_dir / RESPONSES_DIR
    responses.mkdir(parents=True, exist_ok=True)
    name = f"{prompt_id}.attempt{attempt}.txt"
    (responses / name).write_text(text, encoding="utf-8")
    return f"{RESPONSES_DIR}/{name}"


def run_prompt(
    prompt: EvalPrompt,
    provider: Provider,
    *,
    system_prompt: str,
    schema: dict[str, Any] | None,
    retry: bool,
    out_dir: Path,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Generate, validate, and, when the first answer fails, generate once more with the correction prompt."""
    first, text, validation = run_attempt(
        prompt, provider, attempt=1, system_prompt=system_prompt, user=prompt.prompt, schema=schema, clock=clock
    )
    if text is not None:
        first["response_file"] = _write_response(out_dir, prompt.id, 1, text)
    attempts = [first]
    second: dict[str, Any] | None = None
    if retry and text is not None and validation is not None and not first["ok"]:
        user = retry_user_message(prompt.prompt, text, correction_prompt(prompt.kind, validation))
        second, second_text, _ = run_attempt(
            prompt, provider, attempt=2, system_prompt=system_prompt, user=user, schema=schema, clock=clock
        )
        if second_text is not None:
            second["response_file"] = _write_response(out_dir, prompt.id, 2, second_text)
        attempts.append(second)
    final = attempts[-1]
    return {
        "id": prompt.id,
        "kind": prompt.kind,
        "prompt": prompt.prompt,
        "expected": prompt.expected(),
        "attempts": attempts,
        "first_pass_ok": first["ok"],
        "second_pass_ok": second["ok"] if second is not None else None,
        "final_ok": final["ok"],
        "expectations_met": final["expectations_met"],
    }


def format_progress(index: int, total: int, record: Mapping[str, Any]) -> str:
    parts = []
    for attempt in record["attempts"]:
        detail = attempt["status"]
        if attempt["issue_codes"]:
            detail += f" ({', '.join(attempt['issue_codes'])})"
        elif attempt["error"]:
            detail += f" ({attempt['error']})"
        size = f"{attempt['compact_bytes']} B" if attempt["compact_bytes"] is not None else "no JSON"
        parts.append(f"attempt {attempt['attempt']} {detail} {size} {attempt['elapsed_ms']} ms")
    return f"[{index}/{total}] {record['id']} {record['kind']}: " + "; ".join(parts)


def run_eval(
    prompts: Sequence[EvalPrompt],
    provider: Provider,
    *,
    system_prompt: str,
    schemas: Mapping[str, dict[str, Any] | None],
    retry: bool,
    out_dir: Path,
    progress: Callable[[str], None] | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> list[dict[str, Any]]:
    records = []
    for index, prompt in enumerate(prompts, start=1):
        record = run_prompt(
            prompt,
            provider,
            system_prompt=system_prompt,
            schema=schemas.get(prompt.kind),
            retry=retry,
            out_dir=out_dir,
            clock=clock,
        )
        records.append(record)
        if progress is not None:
            progress(format_progress(index, len(prompts), record))
    return records


# --- reports -------------------------------------------------------------------------------------


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 4)


def _median(values: Sequence[int]) -> int | float | None:
    if not values:
        return None
    median = statistics.median(values)
    return int(median) if float(median).is_integer() else median


def build_report(records: Iterable[Mapping[str, Any]], run: Mapping[str, Any]) -> dict[str, Any]:
    """The report document: a pure function of the per-prompt records and the run facts."""
    ordered = sorted((dict(record) for record in records), key=lambda record: record["id"])
    total = len(ordered)
    first_valid = sum(1 for record in ordered if record["first_pass_ok"])
    second_attempted = [record for record in ordered if record["second_pass_ok"] is not None]
    second_valid = sum(1 for record in second_attempted if record["second_pass_ok"])
    final_valid = sum(1 for record in ordered if record["final_ok"])
    first_bytes = [
        record["attempts"][0]["compact_bytes"]
        for record in ordered
        if record["attempts"] and record["attempts"][0]["compact_bytes"] is not None
    ]
    codes = Counter(code for record in ordered for attempt in record["attempts"] for code in attempt["issue_codes"])
    top_codes = [
        {"code": code, "count": count}
        for code, count in sorted(codes.items(), key=lambda item: (-item[1], item[0]))[:TOP_ISSUE_CODES]
    ]
    usage_totals = {"input_tokens": 0, "output_tokens": 0}
    for record in ordered:
        for attempt in record["attempts"]:
            usage = attempt.get("usage")
            if isinstance(usage, dict):
                for key in usage_totals:
                    value = usage.get(key)
                    if isinstance(value, int) and not isinstance(value, bool):
                        usage_totals[key] += value
    evaluated = [record for record in ordered if record["expectations_met"] is not None]
    by_kind = {}
    for kind in PROMPT_KINDS:
        of_kind = [record for record in ordered if record["kind"] == kind]
        if of_kind:
            by_kind[kind] = {
                "prompt_count": len(of_kind),
                "first_pass_valid": sum(1 for record in of_kind if record["first_pass_ok"]),
                "final_valid": sum(1 for record in of_kind if record["final_ok"]),
            }
    summary = {
        "prompt_count": total,
        "first_pass_valid": first_valid,
        "first_pass_rate": _rate(first_valid, total),
        "second_pass_attempted": len(second_attempted),
        "second_pass_valid": second_valid,
        "second_pass_rate": _rate(second_valid, len(second_attempted)),
        "final_valid": final_valid,
        "final_rate": _rate(final_valid, total),
        "median_compact_bytes": _median(first_bytes),
        "top_issue_codes": top_codes,
        "provider_error_count": sum(
            1 for record in ordered for attempt in record["attempts"] if attempt["status"] == "provider_error"
        ),
        "expectations_met": sum(1 for record in evaluated if record["expectations_met"]),
        "expectations_evaluated": len(evaluated),
        "by_kind": by_kind,
        "usage_totals": usage_totals,
    }
    return {"schema_version": REPORT_SCHEMA_VERSION, "run": dict(run), "summary": summary, "prompts": ordered}


def _percent(rate: float | None) -> str:
    return "n/a" if rate is None else f"{rate * 100:.1f}%"


def _cell(value: Any) -> str:
    text = "-" if value is None or value == [] else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _attempt_cell(attempt: Mapping[str, Any] | None) -> str:
    if attempt is None:
        return "-"
    return {"valid": "valid", "invalid": "invalid", "provider_error": "error"}.get(attempt["status"], attempt["status"])


def render_markdown(report: Mapping[str, Any]) -> str:
    run = report["run"]
    summary = report["summary"]
    if run.get("structured_output"):
        sources = sorted({facts["source"] for facts in (run.get("schemas") or {}).values()})
        structured_text = "on (" + ", ".join(sources) + ")"
    else:
        structured_text = "off"
    temperature = (run.get("request") or {}).get("temperature")
    usage = summary.get("usage_totals") or {}
    lines = [
        "# Agent contract eval",
        "",
        f"- Model: `{run['model']}` ({run['provider']})",
        f"- ViewSpec SDK: {run['sdk_version']}",
        f"- System prompt: {run['system_prompt_source']} (sha256 `{run['system_prompt_sha256']}`)",
        f"- Prompt set: `{run['prompt_set_path']}` (sha256 `{run['prompt_set_sha256']}`), "
        f"{summary['prompt_count']} prompts run",
        f"- Second attempt with the correction prompt: {'on' if run.get('retry') else 'off'}",
        f"- Structured output: {structured_text}",
        f"- Temperature: {'not sent' if temperature is None else temperature}",
        f"- Started {run['started_at']}, finished {run['finished_at']}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| First-pass validity | {summary['first_pass_valid']}/{summary['prompt_count']} "
        f"({_percent(summary['first_pass_rate'])}) |",
        f"| Second-pass validity | {summary['second_pass_valid']}/{summary['second_pass_attempted']} attempted "
        f"({_percent(summary['second_pass_rate'])}) |",
        f"| Final validity | {summary['final_valid']}/{summary['prompt_count']} ({_percent(summary['final_rate'])}) |",
        f"| Median compact bytes, first attempt | {_cell(summary['median_compact_bytes'])} |",
        f"| Expectations met, final attempt | {summary['expectations_met']}/{summary['expectations_evaluated']} |",
        f"| Provider errors | {summary['provider_error_count']} |",
        f"| Tokens, all attempts | {usage.get('input_tokens', 0)} input, {usage.get('output_tokens', 0)} output |",
        "",
        "## Top issue codes",
        "",
    ]
    if summary["top_issue_codes"]:
        lines.extend(["| Code | Count |", "| --- | --- |"])
        lines.extend(f"| `{entry['code']}` | {entry['count']} |" for entry in summary["top_issue_codes"])
    else:
        lines.append("No validation issues.")
    lines.extend(
        [
            "",
            "## Prompts",
            "",
            "| Prompt | Kind | First pass | Second pass | Bytes | ms | Expectations | Issue codes (first attempt) |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for record in report["prompts"]:
        first = record["attempts"][0] if record["attempts"] else None
        second = record["attempts"][1] if len(record["attempts"]) > 1 else None
        expectations = record["expectations_met"]
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(record["id"]),
                    _cell(record["kind"]),
                    _attempt_cell(first),
                    _attempt_cell(second),
                    _cell(first["compact_bytes"] if first else None),
                    _cell(first["elapsed_ms"] if first else None),
                    "-" if expectations is None else ("yes" if expectations else "no"),
                    _cell(", ".join(first["issue_codes"]) if first and first["issue_codes"] else None),
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def write_reports(out_dir: Path, report: Mapping[str, Any]) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "eval_report.json"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path = out_dir / "eval_report.md"
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


# --- command line --------------------------------------------------------------------------------


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def default_out_dir(started: datetime, prompt_set_sha256: str) -> Path:
    return DEFAULT_OUT_ROOT / f"{started.strftime('%Y%m%dT%H%M%SZ')}-{prompt_set_sha256[:8]}"


def load_system_prompt(path: str | None) -> tuple[str, str]:
    if path is None:
        return AGENT_SYSTEM_PROMPT, "sdk"
    return Path(path).read_text(encoding="utf-8"), path


def display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def temperature_argument(text: str) -> float:
    """Parse ``--temperature``: a finite number from 0 to 1."""
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    low, high = TEMPERATURE_RANGE
    if not math.isfinite(value) or not low <= value <= high:
        raise argparse.ArgumentTypeError(f"must be a number from {low:g} to {high:g}, not {text!r}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eval_agent_contract.py",
        description=(
            "Ask a model for IntentBundle and AppBundle JSON, validate every answer with the SDK, retry once "
            "with the correction prompt, and write eval_report.json and eval_report.md. Runs only when "
            f"{MODEL_ENV} and the provider key are set; --dry-run writes the plan without a model call."
        ),
    )
    parser.add_argument("--prompts", default=str(DEFAULT_PROMPTS), help="Prompt set JSON (default: %(default)s).")
    parser.add_argument(
        "--out",
        default=None,
        help=f"Output directory (default: {DEFAULT_OUT_ROOT.as_posix()}/<utc-timestamp>-<prompt-set-sha256[:8]>).",
    )
    parser.add_argument(
        "--system-prompt",
        default=None,
        metavar="PATH",
        help="System prompt file (default: the SDK's AGENT_SYSTEM_PROMPT). Use it to measure an older published prompt.",
    )
    structured = parser.add_mutually_exclusive_group()
    structured.add_argument(
        "--structured-output",
        action="store_true",
        help=(
            "Send a structured-output copy of the SDK schema for each prompt kind. Off by default. "
            "The provider may reject it while the SDK schemas hold JSON maps."
        ),
    )
    structured.add_argument(
        "--schema",
        default=None,
        metavar="PATH",
        help="Send this JSON Schema file as the structured-output format, as given. Implies structured output.",
    )
    parser.add_argument("--limit", type=int, default=None, metavar="N", help="Run at most N prompts.")
    parser.add_argument("--ids", default=None, metavar="a,b", help="Run only these prompt ids.")
    parser.add_argument("--no-retry", action="store_true", help="Skip the second attempt with the correction prompt.")
    parser.add_argument("--dry-run", action="store_true", help="Write plan.json and exit 0 without a model call.")
    parser.add_argument(
        "--provider",
        choices=sorted(PROVIDER_KEY_ENV),
        default=DEFAULT_PROVIDER,
        help="Model provider (default: %(default)s).",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=DEFAULT_MAX_TOKENS,
        metavar="N",
        help="max_tokens per request (default: %(default)s).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        metavar="SECONDS",
        help="HTTP timeout per request (default: %(default)s).",
    )
    parser.add_argument(
        "--temperature",
        type=temperature_argument,
        default=None,
        metavar="T",
        help="Sampling temperature from 0 to 1. The request carries one only when this is given.",
    )
    return parser


def build_plan(
    *,
    prompts: Sequence[EvalPrompt],
    provider: str,
    model: str | None,
    model_source: str,
    key_present: bool,
    system_prompt_sha256: str,
    system_prompt_source: str,
    prompt_set_path: str,
    prompt_set_sha256: str,
    retry: bool,
    schema_facts: Mapping[str, Any],
    request: Mapping[str, Any],
    out_dir: Path,
    dry_run: bool,
    created_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "created_at": created_at.isoformat(),
        "dry_run": dry_run,
        "provider": provider,
        "model": model,
        "model_source": model_source,
        "key_present": key_present,
        "sdk_version": viewspec.__version__,
        "system_prompt_sha256": system_prompt_sha256,
        "system_prompt_source": system_prompt_source,
        "prompt_set_path": prompt_set_path,
        "prompt_set_sha256": prompt_set_sha256,
        "prompt_count": len(prompts),
        "retry": retry,
        **schema_facts,
        "request": dict(request),
        "out_dir": str(out_dir),
        "prompts": [{"id": prompt.id, "kind": prompt.kind, "expected": prompt.expected()} for prompt in prompts],
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    prompts_path = Path(args.prompts)
    ids = [part.strip() for part in args.ids.split(",") if part.strip()] if args.ids else None
    try:
        prompts, prompt_set_sha256 = load_prompt_set(prompts_path)
        selected = select_prompts(prompts, ids, args.limit)
        system_prompt, system_prompt_source = load_system_prompt(args.system_prompt)
        schemas, schema_facts = resolve_schemas(args.schema, structured_output=args.structured_output)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    model = os.environ.get(MODEL_ENV) or None
    key_env = PROVIDER_KEY_ENV[args.provider]
    api_key = os.environ.get(key_env) or None
    if not args.dry_run and (model is None or api_key is None):
        missing = [name for name, present in ((MODEL_ENV, model), (key_env, api_key)) if present is None]
        print(
            f"not configured: set {' and '.join(missing)} to call the model, or pass --dry-run to write the plan only",
            file=sys.stderr,
        )
        return 2

    started = now_utc()
    out_dir = Path(args.out) if args.out else default_out_dir(started, prompt_set_sha256)
    out_dir.mkdir(parents=True, exist_ok=True)
    request = request_options(args.max_tokens, args.timeout, args.temperature)
    plan = build_plan(
        prompts=selected,
        provider=args.provider,
        model=model,
        model_source="env" if model else "unset",
        key_present=api_key is not None,
        system_prompt_sha256=sha256_text(system_prompt),
        system_prompt_source=system_prompt_source,
        prompt_set_path=display_path(prompts_path),
        prompt_set_sha256=prompt_set_sha256,
        retry=not args.no_retry,
        schema_facts=schema_facts,
        request=request,
        out_dir=out_dir,
        dry_run=args.dry_run,
        created_at=started,
    )
    plan_path = out_dir / "plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"plan: {plan_path}")
    # schema_warnings is None unless structured output is on, so these lines print only then.
    for kind, warnings in (schema_facts["schema_warnings"] or {}).items():
        for warning in warnings:
            print(f"warning: {kind} schema: {warning}")
    if args.dry_run:
        print(f"dry run: {len(selected)} prompts planned for model {model or '(unset)'}; no model call made")
        return 0

    # The configuration gate above returned 2 unless both are set.
    assert api_key is not None and model is not None
    provider = AnthropicMessagesProvider(
        api_key=api_key,
        model=model,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
        temperature=args.temperature,
    )
    records = run_eval(
        selected,
        provider,
        system_prompt=system_prompt,
        schemas=schemas,
        retry=not args.no_retry,
        out_dir=out_dir,
        progress=print,
    )
    finished = now_utc()
    run = {
        "provider": provider.name,
        "model": model,
        "model_source": "env",
        "sdk_version": viewspec.__version__,
        "system_prompt_sha256": sha256_text(system_prompt),
        "system_prompt_source": system_prompt_source,
        "prompt_set_path": display_path(prompts_path),
        "prompt_set_sha256": prompt_set_sha256,
        "prompt_count": len(selected),
        "selection": {"ids": ids, "limit": args.limit},
        "retry": not args.no_retry,
        **schema_facts,
        "request": request,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
    }
    report = build_report(records, run)
    json_path, markdown_path = write_reports(out_dir, report)
    summary = report["summary"]
    print(
        f"first pass {summary['first_pass_valid']}/{summary['prompt_count']}, "
        f"final {summary['final_valid']}/{summary['prompt_count']}, "
        f"provider errors {summary['provider_error_count']}"
    )
    print(f"report: {json_path}")
    print(f"report: {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
