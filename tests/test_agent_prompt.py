"""The generated agent system prompt: its sections, budgets, examples, and the correction prompt.

The prompt is a pure function of code. Every example in it is lifted out of the prompt text and run
through ``viewspec check``, so an example the validator rejects or fixes fails here.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("jsonschema")
from jsonschema import Draft202012Validator

import viewspec.agent as agent_module
from viewspec import agent_prompt
from viewspec.aesthetics import AESTHETIC_PROFILE_TOKENS
from viewspec.agent import (
    AGENT_SYSTEM_PROMPT,
    DEFAULT_AGENT_SOURCE_FILES,
    ISSUE_CODE_CLASSIFICATION,
    LOSSY_ISSUE,
    REPAIR_LOOP_PROMPT_SECTION,
    SUPPORTED_AGENT_ACTION_KINDS,
    SUPPORTED_AGENT_MOTIFS,
    SUPPORTED_AGENT_STYLE_TOKENS,
    agent_correction_prompt,
    validate_agent_intent_bundle,
)
from viewspec.agent_prompt import (
    APP_PROMPT_SECTIONS,
    APPBUNDLE_V4_PROMPT_SECTION,
    CANONICAL_ONLY_ACTION_KINDS,
    COMMON_MISTAKES,
    PROMPT_APP_BUDGET_BYTES,
    PROMPT_CONTRACT_BUDGET_BYTES,
    WORKED_EXAMPLE,
    WORKED_EXAMPLE_END,
    WORKED_EXAMPLE_START,
    agent_prompt_sections,
    build_agent_system_prompt,
    compact_form_reference,
    json_line,
    prompt_part_sizes,
    style_token_families,
)
from viewspec.app_agent_schema import APPBUNDLE_V5_PROMPT_SECTION
from viewspec.cli import main as cli_main
from viewspec.compact_form import (
    COMPACT_ERROR_CODES,
    COMPACT_FORM_PROMPT_SECTION,
    COMPACT_FORM_SCHEMA,
    COMPACT_MOTIF_EXAMPLES,
    ITEM_KEY_BY_KIND,
)
from viewspec.error_codes import ERROR_CODES
from viewspec.intent_tools import check_intent_source, starter_compact_payload, validate_intent_text

ROOT = Path(__file__).resolve().parents[1]
SECTION_ORDER = (
    "role",
    "common_mistakes",
    "authoring_format",
    "compact_form",
    "motif_examples",
    "canonical_contract",
    "repair_loop",
    "proof_and_handoff",
    "app_bundle",
    "reference_grounding",
)
UPPER_SNAKE_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")


def _checked(tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str, document: dict) -> dict:
    """Write ``document`` to a file and return the ``viewspec check --json`` report for it."""
    path = tmp_path / name
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    code = cli_main(["check", str(path), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert code == (0 if report["ok"] else 2)
    return report


def _motif_example_lines() -> list[str]:
    """The per-motif example lines, read from the prompt text as a model reads them."""
    section = next(section for section in agent_prompt_sections() if section.name == "motif_examples")
    assert section.text in AGENT_SYSTEM_PROMPT
    return section.text.splitlines()[1:]


# --- generation ---------------------------------------------------------------------------------


def test_the_prompt_is_generated_deterministically_and_exported_unchanged():
    first = build_agent_system_prompt()
    second = build_agent_system_prompt()

    assert first == second == AGENT_SYSTEM_PROMPT == agent_prompt.AGENT_SYSTEM_PROMPT
    assert agent_module.AGENT_SYSTEM_PROMPT is agent_prompt.AGENT_SYSTEM_PROMPT
    assert isinstance(agent_module.AGENT_SYSTEM_PROMPT, str)
    assert "AGENT_SYSTEM_PROMPT" in dir(agent_module)
    assert AGENT_SYSTEM_PROMPT.endswith("\n") and not AGENT_SYSTEM_PROMPT.endswith("\n\n")
    with pytest.raises(AttributeError):
        agent_module.NOT_A_REAL_NAME  # noqa: B018


@pytest.mark.parametrize(
    "first_import",
    ["viewspec.compact_form", "viewspec.agent_prompt", "viewspec.app_agent_schema", "viewspec.agent"],
)
def test_the_prompt_bytes_do_not_depend_on_import_order_or_hash_seed(first_import):
    script = (
        f"import hashlib, {first_import}\n"
        "from viewspec.agent import AGENT_SYSTEM_PROMPT\n"
        "print(hashlib.sha256(AGENT_SYSTEM_PROMPT.encode('utf-8')).hexdigest())\n"
    )
    digests = set()
    for seed in ("0", "1"):
        environment = {**os.environ, "PYTHONHASHSEED": seed}
        completed = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=True, env=environment
        )
        digests.add(completed.stdout.strip())

    assert digests == {hashlib.sha256(AGENT_SYSTEM_PROMPT.encode("utf-8")).hexdigest()}


def test_sections_come_in_the_contract_order_and_join_into_the_prompt():
    sections = agent_prompt_sections()
    names = [section.name for section in sections]

    assert [name for name, _group in itertools.groupby(names)] == list(SECTION_ORDER)
    assert names.count("app_bundle") == len(APP_PROMPT_SECTIONS)
    assert "\n\n".join(section.text for section in sections) + "\n" == AGENT_SYSTEM_PROMPT
    for section in sections:
        assert section.text == section.text.strip(), section.name
        assert "\n\n" not in section.text, section.name


def test_the_budgets_are_pinned_and_met():
    sizes = prompt_part_sizes()

    assert PROMPT_CONTRACT_BUDGET_BYTES == 6000
    assert PROMPT_APP_BUDGET_BYTES == 6000
    assert sizes["contract"] <= PROMPT_CONTRACT_BUDGET_BYTES, sizes
    assert sizes["app_bundle"] <= PROMPT_APP_BUDGET_BYTES, sizes
    assert sizes["total"] == len(AGENT_SYSTEM_PROMPT.encode("utf-8"))
    assert sizes["contract"] + sizes["app_bundle"] + sizes["after_app_bundle"] + 2 * 2 + 1 == sizes["total"]


def test_the_prompt_is_plain_ascii_text_without_model_ids_or_markdown_fences():
    assert AGENT_SYSTEM_PROMPT.isascii()
    assert "```" not in AGENT_SYSTEM_PROMPT
    assert "claude" not in AGENT_SYSTEM_PROMPT.lower()  # no model names or ids
    assert "\t" not in AGENT_SYSTEM_PROMPT and "\r" not in AGENT_SYSTEM_PROMPT


def test_every_code_the_prompt_names_is_registered():
    named = set(UPPER_SNAKE_RE.findall(AGENT_SYSTEM_PROMPT))

    assert named, "the prompt names at least the inferred-presentation code"
    assert named <= ERROR_CODES, sorted(named - ERROR_CODES)


# --- the common mistakes ------------------------------------------------------------------------


def test_common_mistakes_come_first_as_numbered_one_line_rules():
    section = agent_prompt_sections()[1]
    lines = section.text.splitlines()

    assert section.name == "common_mistakes"
    assert 6 <= len(COMMON_MISTAKES) <= 10
    assert lines[1:] == [f"{number}. {mistake.rule}" for number, mistake in enumerate(COMMON_MISTAKES, start=1)]
    assert AGENT_SYSTEM_PROMPT.index(section.text) < AGENT_SYSTEM_PROMPT.index(WORKED_EXAMPLE_START)
    for mistake in COMMON_MISTAKES:
        assert "\n" not in mistake.rule and mistake.rule.endswith(".")
        assert mistake.codes and set(mistake.codes) <= ERROR_CODES, mistake


def test_common_mistakes_cover_the_seeded_lossy_codes_and_the_compact_mistakes():
    codes = {code for mistake in COMMON_MISTAKES for code in mistake.codes}
    canonical = codes - set(COMPACT_ERROR_CODES)

    assert {
        "NODES_MUST_BE_OBJECT",
        "MISSING_MOTIF_MEMBER",
        "DUPLICATE_EXACTLY_ONCE_ADDRESS",
        "INVALID_ACTION_TARGET_REF",
        "DETACHED_REGION",
        "UNKNOWN_FIELD",
        "COMPACT_MOTIF_ITEMS_MISSING",
        "COMPACT_ACTION_TARGET_UNKNOWN",
    } <= codes
    assert canonical <= set(ISSUE_CODE_CLASSIFICATION)
    assert all(ISSUE_CODE_CLASSIFICATION[code] == LOSSY_ISSUE for code in canonical - {"UNKNOWN_FIELD"})


# --- the authoring format and the worked example -------------------------------------------------


def _worked_example_text() -> str:
    start = AGENT_SYSTEM_PROMPT.index(f"\n{WORKED_EXAMPLE_START}\n") + len(WORKED_EXAMPLE_START) + 2
    end = AGENT_SYSTEM_PROMPT.index(f"\n{WORKED_EXAMPLE_END}\n", start)
    return AGENT_SYSTEM_PROMPT[start:end]


def test_the_worked_example_between_its_markers_passes_check_with_no_fixes(tmp_path, capsys):
    text = _worked_example_text()
    assert AGENT_SYSTEM_PROMPT.count(WORKED_EXAMPLE_START) == AGENT_SYSTEM_PROMPT.count(WORKED_EXAMPLE_END) == 1
    assert "\n" not in text
    document = json.loads(text)
    assert text == json_line(WORKED_EXAMPLE) and document == WORKED_EXAMPLE

    report = _checked(tmp_path, capsys, "worked.compact.json", document)

    assert report["ok"] is True, report["issues"]
    assert report["fixes"] == []
    assert report["source_format"] == "compact"


def test_the_worked_example_shows_a_dashboard_a_table_and_a_collection_action():
    kinds = [motif["kind"] for motif in WORKED_EXAMPLE["motifs"]]
    tables = {motif["id"] for motif in WORKED_EXAMPLE["motifs"] if motif["kind"] == "table"}
    collection_actions = [action for action in WORKED_EXAMPLE["actions"] if action.get("collection") in tables]

    assert kinds == ["dashboard", "table"]
    assert collection_actions and collection_actions[0]["payload"]
    assert any("values" in row for motif in WORKED_EXAMPLE["motifs"] for row in motif.get("rows", []))


def test_the_authoring_format_names_the_constructs_only_the_canonical_form_expresses():
    section = next(section for section in agent_prompt_sections() if section.name == "authoring_format")

    assert CANONICAL_ONLY_ACTION_KINDS == ("bulk_action",)
    assert section.text.splitlines()[0] == (
        "Write the compact form by default, as viewspec.compact.json. Write the canonical form only for "
        "bulk_action, as viewspec.intent.json, and in AppBundle screens."
    )


def test_the_prompt_names_the_source_files_the_agent_skill_names():
    skill = ROOT.joinpath("skills/viewspec/SKILL.md").read_text(encoding="utf-8")
    compact, canonical = DEFAULT_AGENT_SOURCE_FILES["compact"], DEFAULT_AGENT_SOURCE_FILES["canonical"]

    # Compact first, the canonical form for bulk_action, and the compact source is what gets proved.
    assert f"Write new UI in the compact form by default: `{compact}`" in skill
    assert f"Write the canonical IntentBundle, `{canonical}`, only for what the compact form cannot" in skill
    assert f"`viewspec prove {compact} --out .viewspec-proof`" in skill
    assert f"`--canonical-out {canonical}`" in skill
    assert f"Write the compact form by default, as {compact}." in AGENT_SYSTEM_PROMPT
    assert f"Write the canonical form only for bulk_action, as {canonical}" in AGENT_SYSTEM_PROMPT
    assert f"viewspec check {compact} --json, then viewspec prove {compact} --out .viewspec-proof." in (
        AGENT_SYSTEM_PROMPT
    )


def test_the_compact_form_section_is_spliced_without_its_closing_example():
    reference = compact_form_reference()
    full = COMPACT_FORM_PROMPT_SECTION.strip()
    dropped = full[len(reference) :]

    assert full.startswith(reference)
    assert reference in AGENT_SYSTEM_PROMPT and full not in AGENT_SYSTEM_PROMPT
    assert reference.endswith("Unknown keys are rejected.")
    assert dropped.startswith(" Example:\n{") and dropped.count("\n") == 1
    # The worked example replaces the dropped line; it is the only compact document in the prompt.
    assert AGENT_SYSTEM_PROMPT.count('"format":"compact"') == 1


# --- the per-motif examples ---------------------------------------------------------------------


def test_one_compact_motif_per_kind_in_contract_order():
    lines = _motif_example_lines()
    motifs = [json.loads(line) for line in lines]

    assert [motif["kind"] for motif in motifs] == list(SUPPORTED_AGENT_MOTIFS)
    assert motifs == [COMPACT_MOTIF_EXAMPLES[kind] for kind in SUPPORTED_AGENT_MOTIFS]
    assert lines == [json_line(motif) for motif in motifs]
    for motif in motifs:
        if motif["kind"] in ITEM_KEY_BY_KIND:
            assert ITEM_KEY_BY_KIND[motif["kind"]] in motif, motif


@pytest.mark.parametrize("kind", SUPPORTED_AGENT_MOTIFS)
def test_each_motif_example_passes_check_alone_with_no_fixes(tmp_path, capsys, kind):
    line = _motif_example_lines()[SUPPORTED_AGENT_MOTIFS.index(kind)]
    document = {"schema_version": 1, "format": "compact", "id": "example", "motifs": [json.loads(line)]}

    report = _checked(tmp_path, capsys, f"{kind}.compact.json", document)

    assert report["ok"] is True, report["issues"]
    assert report["fixes"] == []


def test_the_compact_schema_publishes_the_motif_examples():
    motif_schema = COMPACT_FORM_SCHEMA["properties"]["motifs"]["items"]
    examples = motif_schema["x-viewspec-examples"]
    published = json.loads(ROOT.joinpath("demos/agent-intent-compact.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(COMPACT_FORM_SCHEMA)

    assert examples == [COMPACT_MOTIF_EXAMPLES[kind] for kind in SUPPORTED_AGENT_MOTIFS]
    assert published["properties"]["motifs"]["items"]["x-viewspec-examples"] == examples
    for example in examples:
        assert validator.is_valid({"format": "compact", "id": "example", "motifs": [example]}), example


# --- the canonical contract ---------------------------------------------------------------------


def test_style_token_families_expand_to_exactly_the_published_tokens():
    tokens = tuple(token for token in SUPPORTED_AGENT_STYLE_TOKENS if token not in AESTHETIC_PROFILE_TOKENS)
    families = style_token_families(tokens)
    expanded = []
    for entry in families.split(", "):
        family, _, names = entry.partition(".")
        expanded.extend(f"{family}.{name}" for name in names.strip("{}").split(","))

    assert tuple(expanded) == tokens
    assert f"style tokens {families}." in AGENT_SYSTEM_PROMPT
    for token in AESTHETIC_PROFILE_TOKENS:
        assert token in AGENT_SYSTEM_PROMPT


def test_the_canonical_contract_keeps_the_registries_and_the_structural_rules():
    section = next(section for section in agent_prompt_sections() if section.name == "canonical_contract")

    assert f"action kinds {', '.join(SUPPORTED_AGENT_ACTION_KINDS)};" in section.text
    for expected in (
        "[A-Za-z0-9_.-]+",
        "substrate {id, root_id, nodes}",
        "view_spec {id, substrate_id, complexity_tier, root_region, regions, bindings, groups, motifs, styles, actions}",
        "node:{id}#attr:{name}",
        "Semantic edges must reference declared substrate node IDs.",
        "Motifs must be semantically complete",
        "Loading_state and error_state motifs need exactly one title binding",
        "Stateful collection actions are bounded.",
        "take 1-8 payload_bindings",
        "_selection",
        "Region min_children must be >= 0.",
        "Stay inside the v1 local contract caps: 256KB",
    ):
        assert expected in section.text, expected


def test_the_repair_loop_and_proof_sections_appear_once():
    assert AGENT_SYSTEM_PROMPT.count(REPAIR_LOOP_PROMPT_SECTION.strip()) == 1
    assert AGENT_SYSTEM_PROMPT.count("Generated JSON is not a finished ViewSpec proof.") == 1
    assert AGENT_SYSTEM_PROMPT.count("Optional reference grounding.") == 1
    assert AGENT_SYSTEM_PROMPT.rstrip().endswith("Proceed without it when it is unavailable.")


# --- the AppBundle part -------------------------------------------------------------------------


def test_the_appbundle_part_is_built_from_its_sections_in_order():
    app_text = "\n\n".join(section.text for section in agent_prompt_sections() if section.name == "app_bundle")
    proof = AGENT_SYSTEM_PROMPT.index("Generated JSON is not a finished ViewSpec proof.")
    grounding = AGENT_SYSTEM_PROMPT.index("Optional reference grounding.")

    assert app_text == "\n\n".join(section.strip() for section in APP_PROMPT_SECTIONS)
    assert proof < AGENT_SYSTEM_PROMPT.index(app_text) < grounding
    assert len(app_text.encode("utf-8")) == prompt_part_sizes()["app_bundle"]


def test_the_appbundle_part_splices_the_schema_version_5_section_after_the_version_4_contract():
    v4_index = AGENT_SYSTEM_PROMPT.index(APPBUNDLE_V4_PROMPT_SECTION)
    v5_index = AGENT_SYSTEM_PROMPT.index(APPBUNDLE_V5_PROMPT_SECTION.strip())

    assert APP_PROMPT_SECTIONS == (APPBUNDLE_V4_PROMPT_SECTION, APPBUNDLE_V5_PROMPT_SECTION)
    assert v5_index == v4_index + len(APPBUNDLE_V4_PROMPT_SECTION) + 2
    assert prompt_part_sizes()["app_bundle"] == len(
        f"{APPBUNDLE_V4_PROMPT_SECTION}\n\n{APPBUNDLE_V5_PROMPT_SECTION.strip()}".encode()
    )


def test_the_appbundle_part_states_version_limits_as_facts_of_their_version():
    app_text = "\n\n".join(APP_PROMPT_SECTIONS)

    assert "In schema_version 4, when is one leaf condition, with no composition." in app_text
    assert "In schema_version 4, every rule is single-value, with no formatter." in app_text
    assert "schema_version 5 adds condition composition, multi-placeholder text, and formatters." in app_text
    assert "Each screen holds a canonical IntentBundle" in app_text
    # Version-free limits that a later version lifts would be false beside its section.
    assert "Visibility V0 is bounded" not in app_text
    assert "State Text V0 has no" not in app_text
    assert "no boolean condition composition" not in app_text
    assert "Each rule is exactly {id, screen_id, target_ref, state, template}" not in app_text


# --- the correction prompt ----------------------------------------------------------------------


def _report(prompt: str) -> dict:
    return json.loads(prompt.splitlines()[-1])


def test_a_canonical_correction_prompt_keeps_its_commands_and_names_the_patch_route():
    result = validate_agent_intent_bundle({"substrate": {}, "view_spec": {}})
    prompt = agent_correction_prompt(result)
    report = _report(prompt)

    assert prompt.startswith("Regenerate the full ViewSpec IntentBundle JSON. Output strict JSON only.")
    assert "Return an RFC 6902 JSON Patch instead only when the host asks for one." in prompt
    assert "--canonical-out" not in prompt
    assert report["source_format"] == "canonical"
    assert report["repair_mode"] == "regenerate_full_intent_bundle"
    assert report["retry_command"] == "viewspec check viewspec.intent.json --json"
    assert report["patch_command"] == "viewspec check viewspec.intent.json --patch OPS.json --json"


def test_a_compact_correction_prompt_asks_for_the_compact_document_and_explains_canonical_paths():
    document = starter_compact_payload("table")
    document["styles"] = [{"target": "binding:missing_value", "token": "tone.accent"}]

    payload = validate_intent_text(json.dumps(document))
    prompt = payload["correction_prompt"]
    report = _report(prompt)

    assert payload["source_format"] == "compact"
    assert prompt.startswith('Regenerate the full ViewSpec compact document, with "format": "compact".')
    assert "Paths under $.view_spec and $.substrate name the canonical document" in prompt
    assert "viewspec check --canonical-out" in prompt
    assert "Its pointers name the compact document" in prompt
    assert report["source_format"] == "compact"
    assert report["repair_mode"] == "regenerate_full_compact_document"
    assert report["retry_command"] == f"viewspec check {DEFAULT_AGENT_SOURCE_FILES['compact']} --json"
    assert report["patch_command"] == "viewspec check viewspec.compact.json --patch OPS.json --json"
    assert all(path.startswith("$.view_spec") for path in report["affected_paths"])


@pytest.mark.parametrize("name", ["orders.compact.json", "my orders.json"])
def test_the_correction_prompt_names_the_file_that_was_checked(tmp_path, name):
    path = tmp_path / name
    document = starter_compact_payload("dashboard")
    document["motifs"][0]["cards"].append({"id": "revenue", "label": "Again", "value": 1})
    path.write_text(json.dumps(document), encoding="utf-8")
    shell_path = shlex.quote(path.as_posix())

    report = _report(check_intent_source(path)["correction_prompt"])

    assert report["issue_codes"] == ["COMPACT_DUPLICATE_ID"]
    assert report["retry_command"] == f"viewspec check {shell_path} --json"
    assert report["patch_command"] == f"viewspec check {shell_path} --patch OPS.json --json"
    assert shlex.split(report["retry_command"])[2] == path.as_posix()


def test_a_canonical_file_and_a_failed_compile_name_their_own_paths(tmp_path, capsys):
    canonical = tmp_path / "screen.json"
    canonical.write_text(json.dumps({"substrate": {}, "view_spec": {}}), encoding="utf-8")
    compact = tmp_path / "broken.compact.json"
    compact.write_text(json.dumps({"format": "compact", "id": "x", "motifs": [{"id": "t", "kind": "table"}]}))

    canonical_report = _report(check_intent_source(canonical)["correction_prompt"])
    assert cli_main(["compile", str(compact), "--out", str(tmp_path / "dist"), "--json"]) == 2
    envelope = json.loads(capsys.readouterr().out)
    compile_report = _report(envelope["correction_prompt"])

    assert canonical_report["source_format"] == "canonical"
    assert canonical_report["retry_command"] == f"viewspec check {canonical.as_posix()} --json"
    assert compile_report["source_format"] == "compact"
    assert compile_report["retry_command"] == f"viewspec check {compact.as_posix()} --json"


def test_a_patch_that_fails_on_a_compact_source_gets_a_compact_correction_prompt(tmp_path):
    path = tmp_path / "viewspec.compact.json"
    path.write_text(json.dumps(starter_compact_payload("dashboard")), encoding="utf-8")

    payload = check_intent_source(path, patch=[{"op": "test", "path": "/id", "value": "other"}])
    report = _report(payload["correction_prompt"])

    assert report["issue_codes"] == ["PATCH_TEST_FAILED"]
    assert report["source_format"] == "compact"
    assert report["patch_command"] == f"viewspec check {path.as_posix()} --patch OPS.json --json"


def test_an_unknown_source_format_is_refused():
    result = validate_agent_intent_bundle({"substrate": {}, "view_spec": {}})

    with pytest.raises(ValueError, match="source_format"):
        agent_correction_prompt(result, source_format="yaml")
