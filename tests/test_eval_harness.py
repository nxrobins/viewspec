"""Offline tests for scripts/eval_agent_contract.py.

The harness is driven with fake providers only. Every test that could reach the network replaces
``urllib.request.urlopen`` and ``socket.create_connection`` with functions that fail loudly, so a
regression that calls a model shows up as a test failure rather than a silent request. Model ids
here are neutral placeholders: the model is configuration, never code.
"""

from __future__ import annotations

import email.message
import http.client
import io
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from scripts import eval_agent_contract as harness
from viewspec.aesthetics import AESTHETIC_PROFILE_TOKENS
from viewspec.agent import AGENT_INTENT_BUNDLE_SCHEMA, AGENT_SYSTEM_PROMPT
from viewspec.app_bundle import AGENT_APP_BUNDLE_SCHEMA, starter_app_bundle, starter_react_app_bundle
from viewspec.compiler import COLLECTION_ACTION_KINDS, SUPPORTED_MOTIF_KINDS
from viewspec.intent_tools import starter_compact_payload, starter_intent_payload

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "conformance" / "eval" / "prompts.json"
SCRIPT = ROOT / "scripts" / "eval_agent_contract.py"
SCRIPT_OPTIONS = (
    "--prompts",
    "--out",
    "--system-prompt",
    "--schema",
    "--structured-output",
    "--limit",
    "--ids",
    "--no-retry",
    "--dry-run",
    "--temperature",
)
TEST_MODEL = "test-model"
OTHER_MODEL = "other-model"

VALID_INTENT = json.dumps(starter_intent_payload("dashboard"))
INVALID_INTENT = '{"substrate": {}, "view_spec": {}}'
VALID_APP = json.dumps(starter_app_bundle("internal_tool"))
INVALID_APP = '{"schema_version": 1, "app": {"id": "x"}}'
NOT_JSON = "```json\n{}\n```"

RUN_FACTS = {
    "provider": "fake",
    "model": TEST_MODEL,
    "model_source": "env",
    "sdk_version": "0.0.0",
    "system_prompt_sha256": "a" * 64,
    "system_prompt_source": "sdk",
    "prompt_set_path": "conformance/eval/prompts.json",
    "prompt_set_sha256": "b" * 64,
    "prompt_count": 3,
    "selection": {"ids": None, "limit": None},
    "retry": True,
    "structured_output": False,
    "schemas": None,
    "schema_warnings": None,
    "request": {"anthropic_version": "2023-06-01", "max_tokens": 16000, "temperature": None, "timeout_seconds": 600.0},
    "started_at": "2026-01-01T00:00:00+00:00",
    "finished_at": "2026-01-01T00:10:00+00:00",
}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the eval harness tests must not open a network connection")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.delenv(harness.MODEL_ENV, raising=False)
    for env in harness.PROVIDER_KEY_ENV.values():
        monkeypatch.delenv(env, raising=False)


class ScriptedProvider:
    """Returns the scripted outputs in order; an exception instance is raised instead of returned."""

    name = "fake"

    def __init__(self, *outputs: str | Exception) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []

    def generate(self, system: str, user: str, schema: dict[str, Any] | None) -> str:
        self.calls.append({"system": system, "user": user, "schema": schema})
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


class ConstantProvider:
    """Answers every request with the same text."""

    name = "fake"

    def __init__(self, output: str) -> None:
        self.output = output
        self.schemas: list[dict[str, Any] | None] = []

    def generate(self, system: str, user: str, schema: dict[str, Any] | None) -> str:
        self.schemas.append(schema)
        return self.output


class FixedClock:
    """A perf_counter stand-in that advances a quarter second per reading."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        self.now += 0.25
        return self.now


def _prompt(prompt_id: str = "table-orders", kind: str = "intent", **overrides: Any) -> harness.EvalPrompt:
    fields: dict[str, Any] = {
        "id": prompt_id,
        "kind": kind,
        "prompt": "Show a table of orders.",
        "expected_motifs": ("dashboard",) if kind == "intent" else ("table", "detail"),
        "expected_app_schema_version": 1 if kind == "app" else None,
    }
    fields.update(overrides)
    return harness.EvalPrompt(**fields)


# --- prompt set --------------------------------------------------------------------------------


def test_prompt_set_is_valid_and_covers_the_contract():
    data = json.loads(PROMPTS.read_text(encoding="utf-8"))

    assert harness.validate_prompt_set(data) == []
    prompts = data["prompts"]
    assert harness.MIN_PROMPTS <= len(prompts) <= harness.MAX_PROMPTS
    ids = [prompt["id"] for prompt in prompts]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", prompt_id) for prompt_id in ids)
    assert {prompt["kind"] for prompt in prompts} == {"intent", "app"}

    motifs = {motif for prompt in prompts for motif in prompt["expected_motifs"]}
    assert motifs == set(SUPPORTED_MOTIF_KINDS)
    actions = {action for prompt in prompts for action in prompt.get("expected_action_kinds", [])}
    assert actions == set(COLLECTION_ACTION_KINDS)
    aesthetics = {prompt["expected_aesthetic_profile"] for prompt in prompts if "expected_aesthetic_profile" in prompt}
    assert aesthetics == set(AESTHETIC_PROFILE_TOKENS)

    app_versions = {prompt["expected_app_schema_version"] for prompt in prompts if prompt["kind"] == "app"}
    assert {1, 2, 4} <= app_versions
    assert all("expected_app_schema_version" not in prompt for prompt in prompts if prompt["kind"] == "intent")

    for prompt in prompts:
        text = prompt["prompt"].strip()
        assert text, prompt["id"]
        # One or two sentences: at most two sentence ends.
        assert len(re.findall(r"[.!?](?=\s|$)", text)) <= 2, prompt["id"]
        assert len(text) <= 300, prompt["id"]


def test_validate_prompt_set_names_each_problem():
    data = json.loads(PROMPTS.read_text(encoding="utf-8"))
    prompts = data["prompts"]
    prompts[1]["id"] = prompts[0]["id"]
    prompts[2]["kind"] = "widget"
    prompts[3]["expected_motifs"] = ["carousel"]
    prompts[4]["expected_app_schema_version"] = 1
    prompts[5]["extra"] = True
    app = next(prompt for prompt in prompts if prompt["kind"] == "app")
    del app["expected_app_schema_version"]
    problems = "\n".join(harness.validate_prompt_set(data))

    assert "duplicates an earlier id" in problems
    assert "kind must be one of intent, app" in problems
    assert "distinct v1 motif kinds" in problems
    assert "intent prompts must not declare expected_app_schema_version" in problems
    assert "unknown fields extra" in problems
    assert "app prompts need an integer expected_app_schema_version" in problems
    assert harness.validate_prompt_set([]) == ["prompt set must be a JSON object"]
    assert "prompts must be an array" in harness.validate_prompt_set({"schema_version": 1})


def test_select_prompts_filters_by_id_in_file_order_then_limits():
    prompts, digest = harness.load_prompt_set(PROMPTS)

    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    chosen = harness.select_prompts(prompts, [prompts[3].id, prompts[1].id], None)
    assert [prompt.id for prompt in chosen] == [prompts[1].id, prompts[3].id]
    assert [prompt.id for prompt in harness.select_prompts(prompts, None, 2)] == [prompts[0].id, prompts[1].id]
    with pytest.raises(harness.PromptSetError, match="unknown prompt ids: nope"):
        harness.select_prompts(prompts, ["nope"], None)
    with pytest.raises(harness.PromptSetError, match="--limit"):
        harness.select_prompts(prompts, None, 0)


# --- command line ------------------------------------------------------------------------------


def test_help_exits_cleanly_and_documents_its_options():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(entry for entry in (str(ROOT / "src"), env.get("PYTHONPATH")) if entry)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.startswith("usage:")
    for option in SCRIPT_OPTIONS:
        assert option in result.stdout, option
    assert "--no-schema" not in result.stdout


def test_dry_run_without_a_model_records_model_null_and_makes_no_model_call(tmp_path, capsys):
    out = tmp_path / "run"

    assert harness.main(["--dry-run", "--limit", "3", "--out", str(out)]) == 0

    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    assert plan["dry_run"] is True
    assert plan["model"] is None
    assert plan["model_source"] == "unset"
    assert plan["key_present"] is False
    assert plan["prompt_count"] == 3 and len(plan["prompts"]) == 3
    assert plan["system_prompt_sha256"] == harness.sha256_text(AGENT_SYSTEM_PROMPT)
    assert plan["structured_output"] is False
    assert plan["schemas"] is None and plan["schema_warnings"] is None
    assert plan["request"]["temperature"] is None
    assert sorted(path.name for path in out.iterdir()) == ["plan.json"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["run"]
    captured = capsys.readouterr()
    assert "planned for model (unset); no model call made" in captured.out
    assert "warning:" not in captured.out  # the map-schema warning prints only with structured output
    assert captured.err == ""


def test_dry_run_takes_the_model_from_the_environment(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(harness.MODEL_ENV, OTHER_MODEL)
    out = tmp_path / "run"
    args = ["--dry-run", "--ids", "table-orders,form-signup", "--no-retry", "--structured-output", "--out", str(out)]

    assert harness.main(args) == 0

    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    assert plan["model"] == OTHER_MODEL and plan["model_source"] == "env"
    assert [prompt["id"] for prompt in plan["prompts"]] == ["table-orders", "form-signup"]
    assert plan["retry"] is False
    assert plan["structured_output"] is True
    assert {kind: facts["source"] for kind, facts in plan["schemas"].items()} == {
        "intent": "sdk:AGENT_INTENT_BUNDLE_SCHEMA",
        "app": "sdk:AGENT_APP_BUNDLE_SCHEMA",
    }
    assert plan["schema_warnings"]["intent"] and plan["schema_warnings"]["app"]
    assert plan["request"]["temperature"] is None
    printed = capsys.readouterr().out
    assert "warning: intent schema:" in printed and "warning: app schema:" in printed


def test_structured_output_and_schema_together_are_a_usage_error(tmp_path, capsys):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type": "object", "additionalProperties": false}', encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        harness.main(["--dry-run", "--structured-output", "--schema", str(schema), "--out", str(tmp_path / "run")])

    assert exit_info.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err
    assert not (tmp_path / "run").exists()


def test_schema_file_implies_structured_output(tmp_path, capsys):
    schema = tmp_path / "schema.json"
    schema.write_text('{"type": "object", "additionalProperties": {"type": "string"}}', encoding="utf-8")
    out = tmp_path / "run"

    assert harness.main(["--dry-run", "--limit", "1", "--schema", str(schema), "--out", str(out)]) == 0

    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    assert plan["structured_output"] is True
    source = {"source": f"file:{schema}", "sha256": harness.sha256_bytes(schema.read_bytes())}
    assert plan["schemas"] == {"intent": source, "app": source}
    assert "warning: intent schema: 1 object schemas use additionalProperties" in capsys.readouterr().out


def test_dry_run_records_the_temperature_only_when_given(tmp_path):
    assert harness.main(["--dry-run", "--limit", "1", "--out", str(tmp_path / "plain")]) == 0
    assert harness.main(["--dry-run", "--limit", "1", "--temperature", "0", "--out", str(tmp_path / "zero")]) == 0

    plain = json.loads((tmp_path / "plain" / "plan.json").read_text(encoding="utf-8"))
    zero = json.loads((tmp_path / "zero" / "plan.json").read_text(encoding="utf-8"))
    assert plain["request"]["temperature"] is None
    assert zero["request"]["temperature"] == 0.0


@pytest.mark.parametrize("value", ["1.5", "-0.1", "nan", "inf", "warm"])
def test_temperature_must_be_a_number_from_0_to_1(tmp_path, capsys, value):
    with pytest.raises(SystemExit) as exit_info:
        harness.main(["--dry-run", "--temperature", value, "--out", str(tmp_path / "run")])

    assert exit_info.value.code == 2
    assert "--temperature: must be a number from 0 to 1" in capsys.readouterr().err
    assert not (tmp_path / "run").exists()
    assert [harness.temperature_argument(text) for text in ("0", "0.25", "1")] == [0.0, 0.25, 1.0]


def test_without_model_and_key_the_script_exits_2_with_one_line(tmp_path, capsys):
    assert harness.main(["--out", str(tmp_path / "run")]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1
    assert harness.MODEL_ENV in captured.err and "ANTHROPIC_API_KEY" in captured.err
    assert not (tmp_path / "run").exists()


def test_key_without_model_still_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    assert harness.main(["--out", str(tmp_path / "run")]) == 2
    err = capsys.readouterr().err
    assert harness.MODEL_ENV in err and "ANTHROPIC_API_KEY" not in err


def test_bad_prompt_set_and_unknown_ids_exit_1(tmp_path, capsys):
    bad = tmp_path / "prompts.json"
    bad.write_text('{"schema_version": 1, "prompts": []}', encoding="utf-8")

    assert harness.main(["--dry-run", "--prompts", str(bad), "--out", str(tmp_path / "a")]) == 1
    assert "not a usable prompt set" in capsys.readouterr().err
    assert harness.main(["--dry-run", "--ids", "missing-one", "--out", str(tmp_path / "b")]) == 1
    assert "unknown prompt ids: missing-one" in capsys.readouterr().err


def test_default_out_dir_names_the_time_and_the_prompt_set():
    started = harness.datetime(2026, 1, 2, 3, 4, 5, tzinfo=harness.UTC)

    assert harness.default_out_dir(started, "abcdef0123456789") == Path(".agent-eval-runs/20260102T030405Z-abcdef01")


def test_system_prompt_file_overrides_the_sdk_prompt(tmp_path):
    older = tmp_path / "older-prompt.txt"
    older.write_text("You are an older prompt.\n", encoding="utf-8")
    out = tmp_path / "run"

    assert harness.main(["--dry-run", "--limit", "1", "--system-prompt", str(older), "--out", str(out)]) == 0
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    assert plan["system_prompt_source"] == str(older)
    assert plan["system_prompt_sha256"] == harness.sha256_text("You are an older prompt.\n")


@pytest.mark.parametrize(
    ("extra_args", "temperature", "temperature_text"),
    [([], None, "not sent"), (["--temperature", "0.5"], 0.5, "0.5")],
    ids=["no-temperature", "temperature"],
)
def test_main_runs_with_a_fake_provider_and_records_the_temperature(
    tmp_path, monkeypatch, capsys, extra_args, temperature, temperature_text
):
    monkeypatch.setenv(harness.MODEL_ENV, TEST_MODEL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    created: list[dict[str, Any]] = []
    providers: list[ConstantProvider] = []

    def provider_factory(**kwargs: Any) -> ConstantProvider:
        created.append(kwargs)
        providers.append(ConstantProvider(VALID_INTENT))
        return providers[-1]

    monkeypatch.setattr(harness, "AnthropicMessagesProvider", provider_factory)
    out = tmp_path / "run"

    assert harness.main(["--ids", "table-orders,form-signup", "--out", str(out), *extra_args]) == 0

    # Structured output is off by default, so neither request carries a schema.
    assert providers[0].schemas == [None, None]
    assert created == [
        {
            "api_key": "sk-test",
            "model": TEST_MODEL,
            "max_tokens": harness.DEFAULT_MAX_TOKENS,
            "timeout": harness.DEFAULT_TIMEOUT_SECONDS,
            "temperature": temperature,
        }
    ]
    assert sorted(path.name for path in out.iterdir()) == [
        "eval_report.json",
        "eval_report.md",
        "plan.json",
        "responses",
    ]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["run"]
    plan = json.loads((out / "plan.json").read_text(encoding="utf-8"))
    report = json.loads((out / "eval_report.json").read_text(encoding="utf-8"))
    assert plan["model"] == report["run"]["model"] == TEST_MODEL
    assert plan["model_source"] == report["run"]["model_source"] == "env"
    assert plan["request"]["temperature"] == report["run"]["request"]["temperature"] == temperature
    assert report["summary"]["first_pass_valid"] == 2
    assert [prompt["id"] for prompt in report["prompts"]] == ["form-signup", "table-orders"]
    assert report["run"]["structured_output"] is False and report["run"]["schemas"] is None
    markdown = (out / "eval_report.md").read_text(encoding="utf-8")
    assert f"- Temperature: {temperature_text}\n" in markdown
    assert "- Structured output: off\n" in markdown
    printed = capsys.readouterr().out
    assert "first pass 2/2, final 2/2, provider errors 0" in printed
    assert "warning:" not in printed


def test_main_sends_the_sdk_schema_only_with_structured_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(harness.MODEL_ENV, TEST_MODEL)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    provider = ConstantProvider(VALID_INTENT)
    monkeypatch.setattr(harness, "AnthropicMessagesProvider", lambda **kwargs: provider)
    out = tmp_path / "run"

    assert harness.main(["--ids", "table-orders", "--structured-output", "--out", str(out)]) == 0

    assert provider.schemas == [harness.structured_output_schema(AGENT_INTENT_BUNDLE_SCHEMA)]
    report = json.loads((out / "eval_report.json").read_text(encoding="utf-8"))
    assert report["run"]["structured_output"] is True
    assert report["run"]["schemas"]["intent"]["source"] == "sdk:AGENT_INTENT_BUNDLE_SCHEMA"
    markdown = (out / "eval_report.md").read_text(encoding="utf-8")
    assert "- Structured output: on (sdk:AGENT_APP_BUNDLE_SCHEMA, sdk:AGENT_INTENT_BUNDLE_SCHEMA)\n" in markdown
    assert "warning: intent schema:" in capsys.readouterr().out


# --- the retry path with a fake provider -------------------------------------------------------


def test_fake_provider_exercises_the_retry_path_for_an_intent(tmp_path):
    provider = ScriptedProvider(INVALID_INTENT, VALID_INTENT)
    out = tmp_path / "run"

    record = harness.run_prompt(
        _prompt(),
        provider,
        system_prompt="SYSTEM",
        schema={"type": "object"},
        retry=True,
        out_dir=out,
        clock=FixedClock(),
    )

    assert record["first_pass_ok"] is False
    assert record["second_pass_ok"] is True
    assert record["final_ok"] is True
    first, second = record["attempts"]
    assert first["status"] == "invalid" and "MISSING_FIELD" in first["issue_codes"]
    assert first["issue_count"] >= len(first["issue_codes"])
    # The SDK adds the missing schema_version as a lossless fix, and the harness records its code.
    assert first["fix_codes"] == ["FIX_SCHEMA_VERSION_ADDED"]
    assert first["parsed_json"] is True and first["compact_bytes"] == len(INVALID_INTENT.replace(" ", ""))
    assert first["elapsed_ms"] == 250
    assert second["status"] == "valid" and second["issue_codes"] == [] and second["fix_codes"] == []
    assert second["observed"]["motif_kinds"] == ["dashboard"]
    assert second["expectations_met"] is True
    assert record["expectations_met"] is True

    assert len(provider.calls) == 2
    assert provider.calls[0] == {"system": "SYSTEM", "user": "Show a table of orders.", "schema": {"type": "object"}}
    retry_user = provider.calls[1]["user"]
    assert retry_user.startswith("Show a table of orders.")
    assert INVALID_INTENT in retry_user
    assert "Regenerate the full ViewSpec IntentBundle JSON" in retry_user
    assert '"MISSING_FIELD"' in retry_user
    assert provider.calls[1]["schema"] == {"type": "object"}

    assert first["response_file"] == "responses/table-orders.attempt1.txt"
    assert (out / "responses" / "table-orders.attempt1.txt").read_text(encoding="utf-8") == INVALID_INTENT
    assert (out / "responses" / "table-orders.attempt2.txt").read_text(encoding="utf-8") == VALID_INTENT
    assert sorted(path.name for path in tmp_path.iterdir()) == ["run"]


def test_fake_provider_exercises_the_retry_path_for_an_app(tmp_path):
    provider = ScriptedProvider(INVALID_APP, VALID_APP)

    record = harness.run_prompt(
        _prompt("app-x", "app"),
        provider,
        system_prompt="S",
        schema=None,
        retry=True,
        out_dir=tmp_path,
        clock=FixedClock(),
    )

    first, second = record["attempts"]
    assert first["status"] == "invalid" and first["issue_codes"]
    assert second["status"] == "valid"
    assert second["observed"] == {
        "motif_kinds": ["detail", "table"],
        "action_kinds": ["navigate"],
        "aesthetic_profile": None,
        "app_schema_version": 1,
    }
    assert record["final_ok"] is True and record["expectations_met"] is True
    retry_user = provider.calls[1]["user"]
    assert "Regenerate the full ViewSpec AppBundle JSON" in retry_user
    assert '"retry_command":"viewspec check viewspec.app.json --json"' in retry_user
    assert '"repair_mode":"regenerate_full_app_bundle"' in retry_user


def test_no_retry_stops_after_the_first_attempt(tmp_path):
    provider = ScriptedProvider(NOT_JSON, VALID_INTENT)

    record = harness.run_prompt(_prompt(), provider, system_prompt="S", schema=None, retry=False, out_dir=tmp_path)

    assert len(record["attempts"]) == 1
    assert record["attempts"][0]["issue_codes"] == ["INVALID_JSON"]
    assert record["attempts"][0]["parsed_json"] is False
    assert record["attempts"][0]["compact_bytes"] is None
    assert record["second_pass_ok"] is None and record["final_ok"] is False
    assert record["expectations_met"] is None
    assert len(provider.calls) == 1


def test_a_valid_first_attempt_needs_no_second_call(tmp_path):
    provider = ScriptedProvider(VALID_INTENT, "unused")

    record = harness.run_prompt(_prompt(), provider, system_prompt="S", schema=None, retry=True, out_dir=tmp_path)

    assert record["first_pass_ok"] is True and record["second_pass_ok"] is None
    assert len(provider.calls) == 1


def test_a_provider_error_is_recorded_and_not_retried(tmp_path):
    provider = ScriptedProvider(harness.ProviderError("HTTP 529: overloaded_error: Overloaded"), VALID_INTENT)

    record = harness.run_prompt(_prompt(), provider, system_prompt="S", schema=None, retry=True, out_dir=tmp_path)

    (attempt,) = record["attempts"]
    assert attempt["status"] == "provider_error" and attempt["ok"] is False
    assert attempt["error"] == "HTTP 529: overloaded_error: Overloaded"
    assert attempt["response_file"] is None
    assert record["second_pass_ok"] is None and record["final_ok"] is False
    assert not (tmp_path / "responses").exists()


def test_run_attempt_records_the_provider_meta(tmp_path):
    provider = ScriptedProvider(VALID_INTENT)
    provider.last_meta = {
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 5, "output_tokens": 7},
        "served_model": OTHER_MODEL,
        "message_id": "msg_02",
        "request_id": "req_02",
    }

    record = harness.run_prompt(_prompt(), provider, system_prompt="S", schema=None, retry=True, out_dir=tmp_path)

    (attempt,) = record["attempts"]
    assert {key: attempt[key] for key in provider.last_meta} == provider.last_meta


def test_expectations_cover_actions_aesthetics_and_app_versions():
    bundle = starter_intent_payload("dashboard")
    bundle["view_spec"]["styles"].append(
        {"id": "mood", "target": "view:" + bundle["view_spec"]["id"], "token": "aesthetic.calm_ops"}
    )
    observed = harness.observe("intent", bundle)
    assert observed["aesthetic_profile"] == "aesthetic.calm_ops"

    prompt = _prompt(expected_motifs=("dashboard",), expected_aesthetic_profile="aesthetic.calm_ops")
    assert harness.expectations_met(prompt, observed) is True
    assert harness.expectations_met(_prompt(expected_aesthetic_profile="aesthetic.brutalist"), observed) is False
    assert harness.expectations_met(_prompt(expected_action_kinds=("search",)), observed) is False
    assert harness.expectations_met(_prompt(expected_motifs=("table",)), observed) is False

    react = harness.observe("app", starter_react_app_bundle("internal_tool"))
    assert react["app_schema_version"] == 4
    assert harness.expectations_met(_prompt("a", "app", expected_app_schema_version=4), react) is True
    assert (
        harness.expectations_met(_prompt("a", "app", expected_motifs=("list",), expected_app_schema_version=4), react)
        is False
    )
    assert harness.expectations_met(
        _prompt("a", "app", expected_motifs=("table",), expected_app_schema_version=4), react
    )
    assert (
        harness.expectations_met(_prompt("a", "app", expected_motifs=("table",), expected_app_schema_version=2), react)
        is False
    )

    # Malformed answers never raise.
    assert harness.observe("intent", {"view_spec": {"motifs": [1, {"kind": 2}, {"kind": "table"}]}})["motif_kinds"] == [
        "table"
    ]
    assert harness.observe("app", {"schema_version": True, "screens": "no"})["app_schema_version"] is None


def test_observe_reads_a_compact_answer_through_normalization():
    # The prompt now asks for the compact form, so a compact answer must report what it declares.
    compact = starter_compact_payload("dashboard")
    observed = harness.observe("intent", compact)
    assert observed["motif_kinds"] == ["dashboard"]
    assert observed == harness.observe("intent", starter_intent_payload("dashboard"))

    styled = {**compact, "aesthetic": "aesthetic.calm_ops"}
    assert harness.observe("intent", styled)["aesthetic_profile"] == "aesthetic.calm_ops"

    # One that does not normalize still reports its own motifs, actions, and aesthetic.
    broken = {
        "format": "compact",
        "id": "x",
        "aesthetic": "aesthetic.brutalist",
        "motifs": [{"id": "t", "kind": "table"}, "junk"],
        "actions": [{"id": "s", "kind": "search"}],
    }
    observed = harness.observe("intent", broken)
    assert observed["motif_kinds"] == ["table"]
    assert observed["action_kinds"] == ["search"]
    assert observed["aesthetic_profile"] == "aesthetic.brutalist"


def test_fix_codes_tolerate_a_missing_fixes_key():
    assert harness.fix_codes({"issues": []}) == []
    assert harness.fix_codes({"fixes": [{"code": "B"}, {"code": "A"}, {"code": "B"}, "junk"]}) == ["A", "B"]
    assert harness.issue_codes({"issues": [{"code": "Z"}, {"code": "Y"}, {"no": "code"}]}) == ["Y", "Z"]


# --- reports -----------------------------------------------------------------------------------


def _records(tmp_path: Path) -> list[dict[str, Any]]:
    clock = FixedClock()
    out = tmp_path / "records"
    prompts = [
        (_prompt("zeta-table", expected_motifs=("dashboard",)), ScriptedProvider(INVALID_INTENT, VALID_INTENT)),
        (_prompt("alpha-form"), ScriptedProvider(VALID_INTENT)),
        (_prompt("mid-app", "app"), ScriptedProvider(NOT_JSON, INVALID_APP)),
    ]
    return [
        harness.run_prompt(prompt, provider, system_prompt="S", schema=None, retry=True, out_dir=out, clock=clock)
        for prompt, provider in prompts
    ]


def test_report_writer_is_byte_stable_and_orders_prompts_by_id(tmp_path):
    records = _records(tmp_path)

    report = harness.build_report(records, RUN_FACTS)
    reversed_report = harness.build_report(list(reversed(records)), dict(RUN_FACTS))
    json_a, md_a = harness.write_reports(tmp_path / "a", report)
    json_b, md_b = harness.write_reports(tmp_path / "b", reversed_report)

    assert json_a.read_bytes() == json_b.read_bytes()
    assert md_a.read_bytes() == md_b.read_bytes()
    assert json_a.read_bytes() == harness.write_reports(tmp_path / "c", report)[0].read_bytes()

    written = json.loads(json_a.read_text(encoding="utf-8"))
    assert written["schema_version"] == 1
    assert [prompt["id"] for prompt in written["prompts"]] == ["alpha-form", "mid-app", "zeta-table"]
    summary = written["summary"]
    assert summary["prompt_count"] == 3
    assert summary["first_pass_valid"] == 1 and summary["first_pass_rate"] == 0.3333
    assert (
        summary["second_pass_attempted"] == 2
        and summary["second_pass_valid"] == 1
        and summary["second_pass_rate"] == 0.5
    )
    assert summary["final_valid"] == 2 and summary["final_rate"] == 0.6667
    assert summary["provider_error_count"] == 0
    # The app's final answer parsed as JSON, so its expectations were evaluated (and not met).
    assert summary["expectations_met"] == 2 and summary["expectations_evaluated"] == 3
    assert summary["by_kind"] == {
        "app": {"prompt_count": 1, "first_pass_valid": 0, "final_valid": 0},
        "intent": {"prompt_count": 2, "first_pass_valid": 1, "final_valid": 2},
    }
    first_bytes = sorted(
        prompt["attempts"][0]["compact_bytes"]
        for prompt in written["prompts"]
        if prompt["attempts"][0]["compact_bytes"]
    )
    assert summary["median_compact_bytes"] == sum(first_bytes) / 2  # two parseable first attempts
    assert summary["top_issue_codes"][0]["count"] >= summary["top_issue_codes"][-1]["count"]
    assert {entry["code"] for entry in summary["top_issue_codes"]} >= {"MISSING_FIELD", "APP_INVALID_JSON"}
    assert len(summary["top_issue_codes"]) <= 10
    assert summary["usage_totals"] == {"input_tokens": 0, "output_tokens": 0}
    assert written["run"] == RUN_FACTS

    markdown = md_a.read_text(encoding="utf-8")
    assert markdown.startswith("# Agent contract eval\n")
    assert "| First-pass validity | 1/3 (33.3%) |" in markdown
    assert "| Second-pass validity | 1/2 attempted (50.0%) |" in markdown
    assert "| Final validity | 2/3 (66.7%) |" in markdown
    assert "| `MISSING_FIELD` |" in markdown
    rows = [
        line
        for line in markdown.splitlines()
        if line.startswith("| alpha-form") or line.startswith("| mid-app") or line.startswith("| zeta-table")
    ]
    assert [row.split(" | ")[0] for row in rows] == ["| alpha-form", "| mid-app", "| zeta-table"]
    assert "| zeta-table | intent | invalid | valid |" in markdown
    assert "| alpha-form | intent | valid | - |" in markdown
    assert "| mid-app | app | invalid | invalid | - | 250 | no | APP_INVALID_JSON |" in markdown
    assert markdown.endswith("|\n")


def _attempt(
    number: int,
    status: str,
    *,
    codes: tuple[str, ...] = (),
    compact: int | None = None,
    elapsed: int = 10,
    usage: dict[str, int] | None = None,
    expectations: bool | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "attempt": number,
        "status": status,
        "ok": status == "valid",
        "issue_codes": list(codes),
        "issue_count": len(codes),
        "fix_codes": [],
        "parsed_json": compact is not None,
        "raw_bytes": compact or 0,
        "compact_bytes": compact,
        "elapsed_ms": elapsed,
        "stop_reason": None if status == "provider_error" else "end_turn",
        "usage": usage,
        "served_model": None,
        "message_id": None,
        "request_id": None,
        "error": error,
        "observed": None,
        "expectations_met": expectations,
        "response_file": None,
    }


def _record(prompt_id: str, kind: str, attempts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": prompt_id,
        "kind": kind,
        "prompt": "A request.",
        "expected": {"motifs": ["table"], "action_kinds": [], "aesthetic_profile": None, "app_schema_version": None},
        "attempts": attempts,
        "first_pass_ok": attempts[0]["ok"],
        "second_pass_ok": attempts[1]["ok"] if len(attempts) > 1 else None,
        "final_ok": attempts[-1]["ok"],
        "expectations_met": attempts[-1]["expectations_met"],
    }


def test_markdown_report_matches_the_golden_text():
    records = [
        _record(
            "c-intent",
            "intent",
            [_attempt(1, "provider_error", elapsed=30, error="HTTP 400: invalid_request_error: bad schema")],
        ),
        _record(
            "b-intent",
            "intent",
            [
                _attempt(
                    1,
                    "invalid",
                    codes=("MISSING_FIELD", "UNKNOWN_FIELD"),
                    compact=120,
                    elapsed=900,
                    usage={"input_tokens": 100, "output_tokens": 50},
                    expectations=False,
                ),
                _attempt(
                    2,
                    "valid",
                    compact=300,
                    elapsed=1100,
                    usage={"input_tokens": 150, "output_tokens": 80},
                    expectations=True,
                ),
            ],
        ),
        _record(
            "a-app",
            "app",
            [
                _attempt(
                    1,
                    "valid",
                    compact=500,
                    elapsed=2000,
                    usage={"input_tokens": 200, "output_tokens": 120},
                    expectations=True,
                )
            ],
        ),
    ]

    report = harness.build_report(records, RUN_FACTS)

    assert report["summary"] == {
        "prompt_count": 3,
        "first_pass_valid": 1,
        "first_pass_rate": 0.3333,
        "second_pass_attempted": 1,
        "second_pass_valid": 1,
        "second_pass_rate": 1.0,
        "final_valid": 2,
        "final_rate": 0.6667,
        "median_compact_bytes": 310,
        "top_issue_codes": [{"code": "MISSING_FIELD", "count": 1}, {"code": "UNKNOWN_FIELD", "count": 1}],
        "provider_error_count": 1,
        "expectations_met": 2,
        "expectations_evaluated": 2,
        "by_kind": {
            "intent": {"prompt_count": 2, "first_pass_valid": 0, "final_valid": 1},
            "app": {"prompt_count": 1, "first_pass_valid": 1, "final_valid": 1},
        },
        "usage_totals": {"input_tokens": 450, "output_tokens": 250},
    }
    assert harness.render_markdown(report) == "\n".join(
        [
            "# Agent contract eval",
            "",
            f"- Model: `{TEST_MODEL}` (fake)",
            "- ViewSpec SDK: 0.0.0",
            f"- System prompt: sdk (sha256 `{'a' * 64}`)",
            f"- Prompt set: `conformance/eval/prompts.json` (sha256 `{'b' * 64}`), 3 prompts run",
            "- Second attempt with the correction prompt: on",
            "- Structured output: off",
            "- Temperature: not sent",
            "- Started 2026-01-01T00:00:00+00:00, finished 2026-01-01T00:10:00+00:00",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            "| First-pass validity | 1/3 (33.3%) |",
            "| Second-pass validity | 1/1 attempted (100.0%) |",
            "| Final validity | 2/3 (66.7%) |",
            "| Median compact bytes, first attempt | 310 |",
            "| Expectations met, final attempt | 2/2 |",
            "| Provider errors | 1 |",
            "| Tokens, all attempts | 450 input, 250 output |",
            "",
            "## Top issue codes",
            "",
            "| Code | Count |",
            "| --- | --- |",
            "| `MISSING_FIELD` | 1 |",
            "| `UNKNOWN_FIELD` | 1 |",
            "",
            "## Prompts",
            "",
            "| Prompt | Kind | First pass | Second pass | Bytes | ms | Expectations | Issue codes (first attempt) |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
            "| a-app | app | valid | - | 500 | 2000 | yes | - |",
            "| b-intent | intent | invalid | valid | 120 | 900 | yes | MISSING_FIELD, UNKNOWN_FIELD |",
            "| c-intent | intent | error | - | - | 30 | - | - |",
            "",
        ]
    )


def test_report_of_provider_errors_renders_without_bytes(tmp_path):
    provider = ScriptedProvider(harness.ProviderError("HTTP 401: authentication_error: invalid x-api-key"))
    record = harness.run_prompt(_prompt(), provider, system_prompt="S", schema=None, retry=True, out_dir=tmp_path)

    report = harness.build_report([record], RUN_FACTS)
    markdown = harness.render_markdown(report)

    assert report["summary"]["provider_error_count"] == 1
    assert report["summary"]["median_compact_bytes"] is None
    assert report["summary"]["top_issue_codes"] == []
    assert "No validation issues." in markdown
    assert "| table-orders | intent | error | - | - |" in markdown
    assert "| Median compact bytes, first attempt | - |" in markdown


def test_run_eval_reports_progress_per_prompt(tmp_path):
    provider = ScriptedProvider(VALID_INTENT, NOT_JSON, VALID_INTENT)
    lines: list[str] = []

    records = harness.run_eval(
        [_prompt("one"), _prompt("two")],
        provider,
        system_prompt="S",
        schemas={"intent": None, "app": None},
        retry=True,
        out_dir=tmp_path,
        progress=lines.append,
        clock=FixedClock(),
    )

    assert [record["id"] for record in records] == ["one", "two"]
    assert lines[0].startswith("[1/2] one intent: attempt 1 valid ")
    assert lines[1].startswith("[2/2] two intent: attempt 1 invalid (INVALID_JSON) no JSON 250 ms; attempt 2 valid ")


# --- the Anthropic adapter, offline ------------------------------------------------------------


class _FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        super().__init__(body)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


def _message(text: str, **extra: Any) -> bytes:
    body = {
        "id": "msg_01",
        "type": "message",
        # The served model can differ from the requested id, so the report records both.
        "model": OTHER_MODEL,
        "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 12, "output_tokens": 34, "cache_read_input_tokens": 0},
    }
    body.update(extra)
    return json.dumps(body).encode("utf-8")


def test_anthropic_provider_builds_the_messages_request_and_reads_the_text():
    seen: list[urllib.request.Request] = []

    def opener(request, timeout):
        seen.append(request)
        assert timeout == 12.5
        return _FakeResponse(_message('{"a": 1}'), {"request-id": "req_01"})

    provider = harness.AnthropicMessagesProvider(api_key="sk-test", model=TEST_MODEL, timeout=12.5, opener=opener)
    schema = {"type": "object", "additionalProperties": False}

    text = provider.generate("SYSTEM", "USER", schema)

    assert text == '{"a": 1}'
    (request,) = seen
    assert request.full_url == "https://api.anthropic.com/v1/messages"
    assert request.get_method() == "POST"
    assert request.get_header("X-api-key") == "sk-test"
    assert request.get_header("Anthropic-version") == "2023-06-01"
    assert request.get_header("Content-type") == "application/json"
    body = json.loads(request.data)
    assert body == {
        "model": TEST_MODEL,
        "max_tokens": 16000,
        "system": "SYSTEM",
        "messages": [{"role": "user", "content": "USER"}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }
    assert provider.last_meta == {
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 12, "output_tokens": 34},
        "served_model": OTHER_MODEL,
        "message_id": "msg_01",
        "request_id": "req_01",
    }


def test_azure_openai_provider_builds_the_chat_request_and_reads_the_text():
    seen: list[urllib.request.Request] = []
    answer = {
        "id": "chatcmpl-01",
        "model": "gpt-test-2026",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": '{"a": 1}'}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46},
    }

    def opener(request, timeout):
        seen.append(request)
        assert timeout == 12.5
        return _FakeResponse(json.dumps(answer).encode("utf-8"), {"apim-request-id": "apim_01"})

    provider = harness.AzureOpenAIChatProvider(
        api_key="az-test",
        model="gpt-5.5",
        endpoint="https://resource.services.ai.azure.com/",
        api_version="2025-04-01-preview",
        timeout=12.5,
        opener=opener,
    )
    schema = {"type": "object", "additionalProperties": False}

    text = provider.generate("SYSTEM", "USER", schema)

    assert text == '{"a": 1}'
    (request,) = seen
    assert request.full_url == (
        "https://resource.services.ai.azure.com/openai/deployments/gpt-5.5/chat/completions?api-version=2025-04-01-preview"
    )
    assert request.get_header("Api-key") == "az-test" and request.get_header("Content-type") == "application/json"
    body = json.loads(request.data)
    assert body == {
        "messages": [{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "USER"}],
        "max_completion_tokens": 16000,
        "response_format": {"type": "json_schema", "json_schema": {"name": "viewspec_bundle", "schema": schema}},
    }
    assert "max_tokens" not in body and "temperature" not in body
    assert provider.last_meta == {
        "stop_reason": "stop",
        "usage": {"input_tokens": 12, "output_tokens": 34},
        "served_model": "gpt-test-2026",
        "message_id": "chatcmpl-01",
        "request_id": "apim_01",
    }


def test_azure_openai_provider_needs_the_endpoint_and_is_chosen_by_name(monkeypatch, tmp_path):
    monkeypatch.setenv("VIEWSPEC_EVAL_MODEL", "gpt-5.5")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-test")
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    code = harness.main(["--provider", "azure-openai", "--out", str(tmp_path), "--limit", "1"])
    assert code == 2  # The endpoint is part of the configuration gate.

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://resource.openai.azure.com")
    args = harness.build_parser().parse_args(["--provider", "azure-openai"])
    provider = harness.build_provider("azure-openai", api_key="az-test", model="gpt-5.5", args=args)
    assert isinstance(provider, harness.AzureOpenAIChatProvider)
    assert provider.endpoint == "https://resource.openai.azure.com" and provider.api_version == "2025-04-01-preview"


def test_anthropic_provider_sends_a_temperature_only_when_given():
    plain = harness.AnthropicMessagesProvider(api_key="k", model=TEST_MODEL).request_body("S", "U", None)
    zero = harness.AnthropicMessagesProvider(api_key="k", model=TEST_MODEL, temperature=0.0).request_body(
        "S", "U", None
    )
    warm = harness.AnthropicMessagesProvider(api_key="k", model=TEST_MODEL, temperature=0.7).request_body(
        "S", "U", None
    )

    assert "temperature" not in plain
    assert "output_config" not in plain
    assert zero["temperature"] == 0.0 and json.loads(json.dumps(zero))["temperature"] == 0
    assert warm["temperature"] == 0.7
    assert harness.request_options(100, 5.0, None) == {
        "anthropic_version": "2023-06-01",
        "max_tokens": 100,
        "temperature": None,
        "timeout_seconds": 5.0,
        "transport_retries": 2,
    }
    assert harness.request_options(100, 5.0, 0.0)["temperature"] == 0.0


def _http_error(code: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    message = io.BytesIO(json.dumps(body).encode("utf-8"))
    header_message = email.message.Message()
    for key, value in (headers or {}).items():
        header_message[key] = value
    return urllib.error.HTTPError("https://api.anthropic.com/v1/messages", code, "error", header_message, message)


def test_anthropic_provider_retries_transport_failures_then_gives_up():
    outcomes = [
        _http_error(529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}),
        OSError("connection reset"),
        _FakeResponse(_message("ok")),
    ]
    slept: list[float] = []

    def opener(request, timeout):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    provider = harness.AnthropicMessagesProvider(api_key="k", model=TEST_MODEL, opener=opener, sleep=slept.append)

    assert provider.generate("S", "U", None) == "ok"
    assert slept == [2.0, 4.0]
    assert provider.last_meta["request_id"] is None  # this response carried no request-id header

    rate_limited = [
        _http_error(
            429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}, {"retry-after": "7"}
        ),
        _http_error(
            429,
            {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}},
            {"retry-after": "999"},
        ),
        _http_error(429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}),
    ]
    slept.clear()
    provider = harness.AnthropicMessagesProvider(
        api_key="k",
        model=TEST_MODEL,
        opener=lambda request, timeout: (_ for _ in ()).throw(rate_limited.pop(0)),
        sleep=slept.append,
    )
    with pytest.raises(harness.ProviderError, match="HTTP 429: rate_limit_error: slow down"):
        provider.generate("S", "U", None)
    assert slept == [7.0, 60.0]


def test_anthropic_provider_treats_a_broken_response_as_a_transport_error():
    def broken(request, timeout):
        raise http.client.IncompleteRead(b"{")

    provider = harness.AnthropicMessagesProvider(api_key="k", model=TEST_MODEL, opener=broken, transport_retries=0)

    with pytest.raises(harness.ProviderError, match="connection error: IncompleteRead"):
        provider.generate("S", "U", None)


def test_anthropic_provider_does_not_retry_client_errors_and_rejects_bad_bodies():
    slept: list[float] = []

    def bad_request(request, timeout):
        raise _http_error(
            400, {"type": "error", "error": {"type": "invalid_request_error", "message": "temperature: not allowed"}}
        )

    provider = harness.AnthropicMessagesProvider(
        api_key="k", model=TEST_MODEL, temperature=0.0, opener=bad_request, sleep=slept.append
    )
    with pytest.raises(harness.ProviderError, match="HTTP 400: invalid_request_error: temperature: not allowed"):
        provider.generate("S", "U", None)
    assert slept == []

    provider = harness.AnthropicMessagesProvider(
        api_key="k", model=TEST_MODEL, opener=lambda request, timeout: _FakeResponse(b"<html>")
    )
    with pytest.raises(harness.ProviderError, match="not JSON"):
        provider.generate("S", "U", None)

    provider = harness.AnthropicMessagesProvider(
        api_key="k", model=TEST_MODEL, opener=lambda request, timeout: _FakeResponse(_message("", content=[]))
    )
    assert provider.generate("S", "U", None) == ""


def test_the_default_opener_is_urllib_urlopen_resolved_at_call_time():
    provider = harness.AnthropicMessagesProvider(
        api_key="k", model=TEST_MODEL, sleep=lambda seconds: None, transport_retries=0
    )

    with pytest.raises(AssertionError, match="must not open a network connection"):
        provider.generate("S", "U", None)


# --- structured output schemas -----------------------------------------------------------------


def _keys(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            found.add(key)
            found |= _keys(value)
    elif isinstance(node, list):
        for item in node:
            found |= _keys(item)
    return found


def test_structured_output_schema_strips_unsupported_keywords_and_keeps_the_shape():
    intent = harness.structured_output_schema(AGENT_INTENT_BUNDLE_SCHEMA)
    app = harness.structured_output_schema(AGENT_APP_BUNDLE_SCHEMA)

    for schema in (intent, app):
        keys = _keys(schema)
        assert not keys & harness.STRUCTURED_OUTPUT_UNSUPPORTED_KEYWORDS, (
            keys & harness.STRUCTURED_OUTPUT_UNSUPPORTED_KEYWORDS
        )
        assert not {key for key in keys if key.startswith("x-")}
        assert "oneOf" not in keys
    assert set(intent["$defs"]) == set(AGENT_INTENT_BUNDLE_SCHEMA["$defs"])
    assert intent["properties"]["schema_version"] == {
        "type": "integer",
        "const": 1,
        "description": AGENT_INTENT_BUNDLE_SCHEMA["properties"]["schema_version"]["description"],
    }
    assert (
        intent["$defs"]["motif"]["properties"]["kind"]
        == AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["motif"]["properties"]["kind"]
    )
    assert intent["$defs"]["binding"]["properties"]["address"] == {"type": "string"}
    assert "anyOf" in app and len(app["anyOf"]) == len(AGENT_APP_BUNDLE_SCHEMA["oneOf"]) == 5
    assert AGENT_INTENT_BUNDLE_SCHEMA["$defs"]["binding"]["properties"]["address"]["pattern"]  # the source is untouched

    # Property names that look like keywords survive; only schema keywords are filtered.
    tricky = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "format": "uuid"},
            "format": {"type": "string", "format": "phone"},
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }
    assert harness.structured_output_schema(tricky) == {
        "type": "object",
        "properties": {"pattern": {"type": "string", "format": "uuid"}, "format": {"type": "string"}},
        "required": ["pattern"],
        "additionalProperties": False,
    }


def test_structured_output_warnings_name_json_maps():
    (warning,) = harness.structured_output_warnings(harness.structured_output_schema(AGENT_INTENT_BUNDLE_SCHEMA))
    assert "additionalProperties other than false" in warning
    assert "rerun without --structured-output or --schema" in warning
    assert harness.structured_output_warnings({"type": "object", "additionalProperties": False}) == []


def test_resolve_schemas_describes_off_sdk_and_file_sources(tmp_path):
    schemas, facts = harness.resolve_schemas(None, structured_output=False)
    assert schemas == {"intent": None, "app": None}
    assert facts == {"structured_output": False, "schemas": None, "schema_warnings": None}

    schemas, facts = harness.resolve_schemas(None, structured_output=True)
    assert set(schemas) == {"intent", "app"} and all(isinstance(schema, dict) for schema in schemas.values())
    assert facts["structured_output"] is True
    assert facts["schemas"]["intent"]["source"] == "sdk:AGENT_INTENT_BUNDLE_SCHEMA"
    assert facts["schemas"]["app"]["source"] == "sdk:AGENT_APP_BUNDLE_SCHEMA"
    assert facts["schema_warnings"]["intent"] and facts["schema_warnings"]["app"]

    custom = tmp_path / "schema.json"
    custom.write_text('{"type": "object", "additionalProperties": false}', encoding="utf-8")
    schemas, facts = harness.resolve_schemas(str(custom), structured_output=False)
    assert schemas == {
        "intent": {"type": "object", "additionalProperties": False},
        "app": {"type": "object", "additionalProperties": False},
    }
    assert facts["structured_output"] is True
    assert facts["schemas"]["intent"] == {
        "source": f"file:{custom}",
        "sha256": harness.sha256_bytes(custom.read_bytes()),
    }
    assert facts["schema_warnings"] == {"intent": [], "app": []}

    custom.write_text("[]", encoding="utf-8")
    with pytest.raises(harness.PromptSetError, match="JSON Schema object"):
        harness.resolve_schemas(str(custom), structured_output=False)
