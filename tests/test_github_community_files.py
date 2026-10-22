"""The issue forms, the pull request template and CODEOWNERS are valid and match CONTRIBUTING.md.

GitHub reports a broken issue form only in its web interface, never as a failing check, so these
tests apply the documented form schema here (audit finding H-2): the required top-level keys, the
permitted keys and attributes for each element type, unique ids, labels that are unique unless an
id tells them apart, and options that YAML does not turn into booleans or the reserved word
``None``. They also pin the facts the bug report must ask for, the code fences that keep pasted
input readable in the issue, the deliberate blank-issue setting and the security route in
``config.yml``, an owner for every path in CODEOWNERS, and a pull request checklist that repeats
every command and repository rule in CONTRIBUTING.md.

Schema reference: https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms
and the "Syntax for GitHub's form schema" and "Common validation errors" pages next to it.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from viewspec.cli import main as cli_main

ROOT = Path(__file__).resolve().parents[1]
GITHUB = ROOT / ".github"
ISSUE_TEMPLATES = GITHUB / "ISSUE_TEMPLATE"
CONFIG = ISSUE_TEMPLATES / "config.yml"
PULL_REQUEST_TEMPLATE = GITHUB / "PULL_REQUEST_TEMPLATE.md"
CODEOWNERS = GITHUB / "CODEOWNERS"

TOP_LEVEL_REQUIRED = ("name", "description", "body")
TOP_LEVEL_KEYS = {*TOP_LEVEL_REQUIRED, "assignees", "labels", "title", "type", "projects"}
ELEMENT_KEYS = {"type", "id", "attributes", "validations"}
# Per element type: (required attributes, permitted attributes, permitted validations).
ELEMENT_SCHEMA: dict[str, tuple[set[str], set[str], set[str]]] = {
    "markdown": ({"value"}, {"value"}, set()),
    "textarea": ({"label"}, {"label", "description", "placeholder", "value", "render"}, {"required", "min_length"}),
    "input": ({"label"}, {"label", "description", "placeholder", "value"}, {"required", "min_length"}),
    "dropdown": ({"label", "options"}, {"label", "description", "multiple", "options", "default"}, {"required"}),
    "checkboxes": ({"label", "options"}, {"label", "description", "options"}, {"required"}),
    "upload": ({"label"}, {"label", "description"}, {"required", "accept"}),
}
STRING_ATTRIBUTES = {"value", "label", "description", "placeholder", "render"}
ID_RE = re.compile(r"[A-Za-z0-9_-]+")
# GitHub silently skips a label the repository does not have, so the forms may only apply labels
# that GitHub creates in every new repository (and that nxrobins/viewspec has).
DEFAULT_LABELS = {
    "bug",
    "documentation",
    "duplicate",
    "enhancement",
    "good first issue",
    "help wanted",
    "invalid",
    "question",
    "wontfix",
}
# The facts a maintainer needs to reproduce a bug: every one is a required field of the bug form.
BUG_REPORT_FACTS = {"viewspec-version", "python-version", "os", "command", "reproduction", "output"}
# The text fields whose answers are code or data (JSON, HTML, DESIGN.md, scripts, command output).
CODE_FIELDS = {
    "bug_report.yml": {"environment", "command", "reproduction", "output"},
    "feature_request.yml": {"proposal"},
}


def _issue_forms() -> list[Path]:
    return sorted(path for path in ISSUE_TEMPLATES.glob("*.y*ml") if path.name not in {"config.yml", "config.yaml"})


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _non_string_keys(node: Any, where: str = "") -> list[str]:
    """Keys YAML did not read as strings, such as ``on`` or ``yes`` read as booleans."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                found.append(f"{where}: key {key!r} is not a string")
            found += _non_string_keys(value, f"{where}.{key}")
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found += _non_string_keys(item, f"{where}[{index}]")
    return found


def _parameterize(label: str) -> str:
    """The reference GitHub derives from a label without an id (Rails ``parameterize``)."""
    return re.sub(r"[^a-z0-9_-]+", "-", label.lower()).strip("-")


def _element_problems(index: int, element: Any) -> list[str]:
    where = f"body[{index}]"
    if not isinstance(element, dict):
        return [f"{where} is not a mapping"]
    problems = [f"{where}: {key!r} is not a permitted key" for key in element if key not in ELEMENT_KEYS]
    kind = element.get("type")
    if kind not in ELEMENT_SCHEMA:
        return [*problems, f"{where}: {kind!r} is not a valid input type"]
    required, permitted, permitted_validations = ELEMENT_SCHEMA[kind]
    attributes = element.get("attributes")
    if not isinstance(attributes, dict):
        return [*problems, f"{where}: attributes are missing"]
    problems += [f"{where}: required attribute {key!r} is missing" for key in sorted(required - set(attributes))]
    problems += [f"{where}: {key!r} is not a permitted attribute" for key in attributes if key not in permitted]
    problems += [
        f"{where}: {key} must be a non-empty string"
        for key in sorted(STRING_ATTRIBUTES & set(attributes))
        if not _is_text(attributes[key])
    ]
    # GitHub refuses words that ask for credentials in the label of a text field.
    label = attributes.get("label")
    if kind in {"input", "textarea"} and _is_text(label) and "password" in label.lower():
        problems.append(f"{where}: label contains the forbidden word 'password'")
    if "id" in element:
        if kind == "markdown":
            problems.append(f"{where}: a markdown element takes no id")
        elif not isinstance(element["id"], str) or not ID_RE.fullmatch(element["id"]):
            problems.append(f"{where}: id {element['id']!r} can only contain numbers, letters, - and _")
    validations = element.get("validations", {})
    if not isinstance(validations, dict):
        problems.append(f"{where}: validations must be a mapping")
    else:
        problems += [
            f"{where}: {key!r} is not a permitted validation" for key in validations if key not in permitted_validations
        ]
        if "required" in validations and not isinstance(validations["required"], bool):
            problems.append(f"{where}: validations.required must be a boolean")
    options = attributes.get("options")
    if kind == "dropdown":
        if not isinstance(options, list) or not options:
            problems.append(f"{where}: options cannot be empty")
        else:
            if not all(_is_text(option) for option in options):
                problems.append(f"{where}: options must be strings (quote yes, no, true and similar words)")
            elif len(set(options)) != len(options):
                problems.append(f"{where}: options must be unique")
            if any(str(option).strip().lower() == "none" for option in options):
                problems.append(f"{where}: options must not include the reserved word none")
            default = attributes.get("default")
            if default is not None and not (
                isinstance(default, int) and not isinstance(default, bool) and 0 <= default < len(options)
            ):
                problems.append(f"{where}: default must be an index into options")
            if default is not None and any(str(option).strip().lower() == "n/a" for option in options):
                problems.append(f"{where}: options cannot include n/a when a default is set")
        if "multiple" in attributes and not isinstance(attributes["multiple"], bool):
            problems.append(f"{where}: multiple must be a boolean")
    if kind == "checkboxes":
        if not isinstance(options, list) or not options:
            problems.append(f"{where}: options cannot be empty")
        else:
            for number, option in enumerate(options):
                if not isinstance(option, dict) or not _is_text(option.get("label")):
                    problems.append(f"{where}: option {number} needs a label")
                elif set(option) - {"label", "required"}:
                    problems.append(f"{where}: option {number} has keys other than label and required")
                elif not isinstance(option.get("required", False), bool):
                    problems.append(f"{where}: option {number} required must be a boolean")
    return problems


def issue_form_problems(form: Any) -> list[str]:
    """The documented issue-form rules ``form`` breaks; empty when it breaks none.

    The rules come from GitHub's form-schema and "Common validation errors" pages. GitHub does not
    publish its whole list of forbidden label words, so only the documented one, "password", is
    checked, and an empty result is not proof that GitHub accepts the form.
    """
    if not isinstance(form, dict):
        return ["the form is not a mapping"]
    problems = _non_string_keys(form)
    if problems:
        return problems
    problems += [f"required top level key {key!r} is missing" for key in TOP_LEVEL_REQUIRED if key not in form]
    problems += [f"{key!r} is not a permitted key" for key in form if key not in TOP_LEVEL_KEYS]
    problems += [f"{key} must be a non-empty string" for key in ("name", "description") if not _is_text(form.get(key))]
    labels = form.get("labels", [])
    if isinstance(labels, str):
        labels = [label.strip() for label in labels.split(",")]
    problems += [f"label {label!r} is not a GitHub default label" for label in labels if label not in DEFAULT_LABELS]
    body = form.get("body")
    if not isinstance(body, list) or not body:
        return [*problems, "body cannot be empty"]
    for index, element in enumerate(body):
        problems += _element_problems(index, element)
    elements = [element for element in body if isinstance(element, dict) and element.get("type") in ELEMENT_SCHEMA]
    inputs = [element for element in elements if element["type"] != "markdown"]
    if not inputs:
        problems.append("body must contain at least one non-markdown field")
    ids = [element["id"] for element in inputs if "id" in element]
    problems += [
        f"body must have unique ids: {item!r} repeats" for item in sorted({i for i in ids if ids.count(i) > 1})
    ]
    # Two elements may share a label when at least one has an id to tell them apart, so only the
    # labels of elements without an id must be unique, together with checkbox options, which
    # cannot have an id.
    field_labels: list[str] = []
    option_labels: list[str] = []
    references: list[str] = []
    for element in inputs:
        attributes = element.get("attributes") or {}
        if element["type"] == "checkboxes":
            option_labels += [
                option["label"]
                for option in attributes.get("options") or []
                if isinstance(option, dict) and _is_text(option.get("label"))
            ]
        label = attributes.get("label")
        if not _is_text(label):
            continue
        if "id" not in element:
            field_labels.append(label)
        references.append(element["id"] if "id" in element else _parameterize(label))
    labels_seen = field_labels + option_labels
    problems += [
        f"{'checkboxes' if item in option_labels else 'body'} must have unique labels: {item!r} repeats"
        for item in sorted({x for x in labels_seen if labels_seen.count(x) > 1})
    ]
    problems += [
        f"labels are too similar: {item!r}" for item in sorted({r for r in references if references.count(r) > 1})
    ]
    return problems


def _repository_url() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["urls"]["Repository"]


def _contributing_section(heading: str) -> str:
    text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, flags=re.MULTILINE | re.DOTALL)
    assert match is not None, f"CONTRIBUTING.md has no '## {heading}' section"
    return match.group(1)


def test_the_issue_forms_exist() -> None:
    names = {path.name for path in _issue_forms()}
    assert {"bug_report.yml", "feature_request.yml"} <= names, names


@pytest.mark.parametrize("path", _issue_forms(), ids=lambda path: path.name)
def test_issue_form_follows_the_github_form_schema(path: Path) -> None:
    assert issue_form_problems(_load(path)) == []


def test_issue_form_names_are_unique() -> None:
    names = [_load(path)["name"] for path in _issue_forms()]
    assert len(names) == len(set(names)), names


# Each case breaks one rule from GitHub's "Common validation errors when creating issue forms" page
# or its form schema.
_HEADER = "name: n\ndescription: d\n"
_INVALID_FORMS = [
    ("description: d\nbody: [{type: input, attributes: {label: A}}]", "'name' is missing"),
    (_HEADER + "body: []", "body cannot be empty"),
    (_HEADER + "body: [{type: markdown, attributes: {value: x}}]", "non-markdown"),
    (
        _HEADER + "body: [{type: input, id: a, attributes: {label: A}}, {type: input, id: a, attributes: {label: B}}]",
        "unique ids",
    ),
    (
        _HEADER + "body: [{type: input, attributes: {label: A}}, {type: textarea, attributes: {label: A}}]",
        "unique labels",
    ),
    (
        _HEADER + "body: [{type: textarea, attributes: {label: Name}},"
        " {type: checkboxes, attributes: {label: C, options: [{label: Name}]}}]",
        "checkboxes must have unique labels",
    ),
    (
        _HEADER + "body: [{type: input, attributes: {label: 'Name?'}}, {type: input, attributes: {label: 'Name???'}}]",
        "too similar",
    ),
    (
        _HEADER + "body: [{type: input, attributes: {label: 'Name?'}},"
        " {type: input, id: name, attributes: {label: 'Name???'}}]",
        "labels are too similar: 'name'",
    ),
    (_HEADER + "body: [{type: input, id: a b, attributes: {label: A}}]", "can only contain"),
    (_HEADER + "body: [{type: markdown, id: m, attributes: {value: x}}]", "takes no id"),
    (_HEADER + "body: [{type: input, attributes: {description: x}}]", "'label' is missing"),
    (_HEADER + "body: [{type: input, attributes: {label: A, options: [x]}}]", "permitted attribute"),
    (_HEADER + "body: [{type: input, required: true, attributes: {label: A}}]", "permitted key"),
    (_HEADER + "body: [{type: text, attributes: {label: A}}]", "not a valid input type"),
    (_HEADER + "body: [{type: dropdown, attributes: {label: A, options: [None, x]}}]", "reserved word"),
    (_HEADER + "body: [{type: dropdown, attributes: {label: A, options: [yes, no]}}]", "must be strings"),
    (_HEADER + "body: [{type: dropdown, attributes: {label: A, options: [x, n/a], default: 0}}]", "n/a"),
    (_HEADER + "body: [{type: input, attributes: {label: Password}}]", "forbidden word"),
    (_HEADER + "body: [{type: input, attributes: {label: ' '}}]", "non-empty string"),
    (_HEADER + "labels: [triage]\nbody: [{type: input, attributes: {label: A}}]", "default label"),
    (_HEADER + "on: x\nbody: [{type: input, attributes: {label: A}}]", "not a string"),
]


@pytest.mark.parametrize(("text", "expected"), _INVALID_FORMS, ids=[expected for _, expected in _INVALID_FORMS])
def test_issue_form_checker_rejects_what_github_rejects(text: str, expected: str) -> None:
    problems = issue_form_problems(yaml.safe_load(text))
    assert any(expected in problem for problem in problems), problems


# The fixes GitHub's "Common validation errors" page gives for those errors, and its form-schema
# examples. The checkboxes element gets the label the schema requires, which the page's example leaves out.
_VALID_FORMS = [
    # Two elements with the same label, told apart by their ids.
    "body: [{type: textarea, id: name_1, attributes: {label: Name}},"
    " {type: textarea, id: name_2, attributes: {label: Name}}]",
    # One id is enough.
    "body: [{type: textarea, id: name_1, attributes: {label: Name}}, {type: textarea, attributes: {label: Name}}]",
    # A checkbox option may repeat the label of an element that has an id.
    "body: [{type: textarea, id: name_1, attributes: {label: Name}},"
    " {type: checkboxes, attributes: {label: C, options: [{label: Name}]}}]",
    # Labels that parameterize alike, told apart by an id.
    "body: [{type: input, attributes: {label: 'Name?'}}, {type: input, id: your-name, attributes: {label: 'Name???'}}]",
    # A dropdown with a default option.
    "body: [{type: dropdown, id: download, attributes: {label: 'How did you download the software?',"
    " options: [Built from source, Homebrew, MacPorts, apt-get], default: 0}, validations: {required: true}}]",
]


@pytest.mark.parametrize("text", _VALID_FORMS)
def test_issue_form_checker_accepts_what_github_accepts(text: str) -> None:
    assert issue_form_problems(yaml.safe_load(_HEADER + text)) == []


def test_bug_report_requires_the_facts_a_maintainer_needs() -> None:
    body = _load(ISSUE_TEMPLATES / "bug_report.yml")["body"]
    required = {element["id"] for element in body if "id" in element and element.get("validations", {}).get("required")}
    missing = BUG_REPORT_FACTS - required
    assert not missing, missing
    output = next(element for element in body if element.get("id") == "output")
    assert "`--json`" in output["attributes"]["description"]
    reproduction = next(element for element in body if element.get("id") == "reproduction")
    assert "IntentBundle" in reproduction["attributes"]["label"]


@pytest.mark.parametrize("path", _issue_forms(), ids=lambda path: path.name)
def test_text_fields_that_take_code_keep_it_readable_in_the_issue(path: Path) -> None:
    """GitHub puts a textarea answer into the issue as Markdown unless ``render`` is set, and
    ``render`` turns off attachments. Unfenced, pasted HTML loses its tags and the front matter of a
    ``DESIGN.md`` becomes a heading. So a field that takes code, or asks for pasted text, either sets
    ``render`` or shows how to fence the text, and a field that offers attachments does not set
    ``render``."""
    textareas = [
        (element.get("id"), element["attributes"])
        for element in _load(path)["body"]
        if element.get("type") == "textarea"
    ]
    code_fields = CODE_FIELDS.get(path.name, set())
    assert code_fields <= {field_id for field_id, _ in textareas}, code_fields
    for field_id, attributes in textareas:
        description = attributes.get("description", "")
        where = (path.name, field_id)
        if "attach" in description.lower():
            assert "render" not in attributes, where
        if (field_id in code_fields or "paste" in description.lower()) and "render" not in attributes:
            assert "```" in description, where
            # A description line that starts with a fence would turn the rest of it into a code block.
            assert not any(line.lstrip().startswith("```") for line in description.splitlines()), where


def test_bug_report_promises_error_paths_only_from_commands_that_report_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``viewspec prove --json``, the form's example command, lists error codes without paths, so
    the output field points to the validate commands for paths, and their JSON has to give them."""
    body = _load(ISSUE_TEMPLATES / "bug_report.yml")["body"]
    output = next(element for element in body if element.get("id") == "output")
    description = " ".join(output["attributes"]["description"].split())
    bundle = tmp_path / "bad.json"
    bundle.write_text('{"bogus": 1}', encoding="utf-8")
    for command in ("validate-intent", "validate-app"):
        assert f"`viewspec {command} <file> --json`" in description, command
        assert cli_main([command, str(bundle), "--json"]) == 2
        issues = json.loads(capsys.readouterr().out)["issues"]
        assert issues, command
        assert all(issue["path"].startswith("$") for issue in issues), issues


def test_issue_forms_apply_the_bug_and_enhancement_labels() -> None:
    assert "bug" in _load(ISSUE_TEMPLATES / "bug_report.yml")["labels"]
    assert "enhancement" in _load(ISSUE_TEMPLATES / "feature_request.yml")["labels"]


def test_template_chooser_sets_blank_issues_deliberately_and_routes_security_reports() -> None:
    config = _load(CONFIG)
    assert set(config) <= {"blank_issues_enabled", "contact_links"}
    # Leaving the key out means GitHub's default; the setting is a decision, so it is spelled out.
    assert isinstance(config.get("blank_issues_enabled"), bool)
    links = config["contact_links"]
    for link in links:
        assert set(link) == {"name", "url", "about"}, link
        assert all(_is_text(link[key]) for key in link), link
        assert link["url"].startswith("https://"), link
    security = [link for link in links if link["url"].endswith("/SECURITY.md")]
    assert len(security) == 1, links
    assert security[0]["url"].startswith(f"{_repository_url()}/blob/")
    assert "security" in security[0]["name"].lower()

    contributing = " ".join((ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8").split())
    if config["blank_issues_enabled"]:
        assert "Blank issues are turned off" not in contributing
    else:
        assert "Blank issues are turned off" in contributing


def test_links_into_the_repository_name_files_that_exist() -> None:
    blob = re.compile(rf"{re.escape(_repository_url())}/blob/main/([^\s)\"'#]+)")
    texts = [path.read_text(encoding="utf-8") for path in (*_issue_forms(), CONFIG, PULL_REQUEST_TEMPLATE)]
    targets = {target for text in texts for target in blob.findall(text)}
    assert "SECURITY.md" in targets
    assert [target for target in sorted(targets) if not (ROOT / target).is_file()] == []


def codeowners_rules(text: str) -> list[tuple[str, list[str]]]:
    """Each rule of a CODEOWNERS file as (pattern, owners).

    A ``#`` at the start of a line or after whitespace starts a comment, as in GitHub's example
    ``*.js    @js-owner #This is an inline comment.``
    """
    rules: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        words = re.split(r"(?:^|\s)#", line, maxsplit=1)[0].split()
        if words:
            rules.append((words[0], words[1:]))
    return rules


def test_codeowners_rules_leave_out_comments() -> None:
    text = "# Owners\n* @nxrobins\n\n  # An indented comment\ndocs/ @nxrobins # documentation owner\n*.js @a #note\n"
    assert codeowners_rules(text) == [("*", ["@nxrobins"]), ("docs/", ["@nxrobins"]), ("*.js", ["@a"])]


def test_codeowners_names_an_owner_for_every_path() -> None:
    rules = codeowners_rules(CODEOWNERS.read_text(encoding="utf-8"))
    assert rules, "CODEOWNERS has no rules"
    owner = re.compile(r"@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:/[A-Za-z0-9._-]+)?|[^@\s]+@[^@\s]+\.[^@\s]+")
    for pattern, owners in rules:
        assert owners and all(owner.fullmatch(name) for name in owners), (pattern, owners)
    # The last matching rule wins, so the catch-all comes first or it would override every other rule.
    assert rules[0][0] == "*", rules
    assert "@nxrobins" in rules[0][1]


def _pull_request_checklist() -> list[str]:
    return [
        line for line in PULL_REQUEST_TEMPLATE.read_text(encoding="utf-8").splitlines() if line.startswith("- [ ] ")
    ]


def _repository_rules() -> list[str]:
    """The rules under "Repository rules" in CONTRIBUTING.md.

    A ``###`` subsection of prose states one rule, its heading. A subsection of bullets states one
    rule per bullet, the bullet's first sentence.
    """
    rules: list[str] = []
    subsections = re.findall(
        r"^### ([^\n]+)\n(.*?)(?=^### |\Z)", _contributing_section("Repository rules"), flags=re.MULTILINE | re.DOTALL
    )
    for heading, text in subsections:
        bullets = [" ".join(item.split()) for item in re.split(r"^- ", text, flags=re.MULTILINE)[1:]]
        rules += [re.split(r"\.\s", bullet, maxsplit=1)[0].rstrip(".") for bullet in bullets] or [heading]
    return rules


def test_pull_request_checklist_names_every_check_in_contributing() -> None:
    blocks = re.findall(r"```bash\n(.*?)```", _contributing_section("Running the checks"), flags=re.DOTALL)
    assert blocks, "CONTRIBUTING.md has no command block under 'Running the checks'"
    commands = [line.strip() for line in blocks[0].splitlines() if line.strip()]
    assert "ruff check ." in commands, commands
    template = PULL_REQUEST_TEMPLATE.read_text(encoding="utf-8")
    checklist = _pull_request_checklist()
    for command in commands:
        assert any(line.startswith(f"- [ ] `{command}`") for line in checklist), command
    for rule in ("CHANGELOG.md", "src/viewspec/error_codes.py", "requirements-ci.txt"):
        assert any(f"`{rule}`" in line for line in checklist), rule
    named_files = re.findall(r"`([\w./-]+\.(?:md|py|json|txt|toml|yml))`", template)
    assert [name for name in named_files if not (ROOT / name).is_file()] == []


def test_pull_request_checklist_repeats_every_repository_rule() -> None:
    """CONTRIBUTING.md says the checklist repeats the repository rules, so each rule appears, in its
    own words, in a checklist item; a rule added to CONTRIBUTING.md needs an item too."""
    rules = _repository_rules()
    # Both kinds of rule are found: the ### subsections and the bullets, the no-network rule among them.
    assert "The error-code registry is closed" in rules, rules
    assert any("network calls" in rule for rule in rules), rules
    checklist = _pull_request_checklist()
    assert [rule for rule in rules if not any(rule in line for line in checklist)] == []


def test_contributing_points_to_the_templates() -> None:
    section = _contributing_section("Issues and pull requests")
    for path in (ISSUE_TEMPLATES, PULL_REQUEST_TEMPLATE, CODEOWNERS):
        relative = path.relative_to(ROOT).as_posix() + ("/" if path.is_dir() else "")
        assert path.exists(), relative
        assert f"`{relative}`" in section, relative
