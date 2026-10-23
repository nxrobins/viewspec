"""The issue forms, the pull request template and CODEOWNERS are valid and match CONTRIBUTING.md.

GitHub reports a broken issue form only in its web interface, never as a failing check, so these
tests apply GitHub's documented issue-form rules here (audit finding H-2). ``issue_form_problems``
checks a subset of them, such as the required and permitted top-level keys, a ``name`` of more
than 3 characters, the permitted keys, attributes and validations of each element type, non-empty
strings where a string is expected, unique ids, labels that are unique unless an id tells them
apart and that do not produce the same reference, and dropdown options that YAML does not turn
into booleans or the reserved word ``None``. ``template_name_problems`` checks that each
template's name, Markdown templates included, is long enough for GitHub to show the template and
differs from every other template's. Passing these checks does not prove that GitHub accepts a
form; ``issue_form_problems`` says what they leave out.

The other tests pin choices of this repository that GitHub does not require: the templates apply
only labels the repository has, the bug report asks for the facts a maintainer needs, the fields
that take code keep it readable in the issue, ``config.yml`` sets blank issues deliberately and
routes security reports, CODEOWNERS starts with a catch-all rule that names an owner,
CONTRIBUTING.md lists every command CI's test job runs, and the pull request checklist repeats
every command and repository rule in CONTRIBUTING.md.

Schema reference: https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms
and the "Syntax for GitHub's form schema", "Common validation errors" and "Configuring issue
templates" pages next to it.
"""

from __future__ import annotations

import json
import re
import tomllib
import unicodedata
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
CI_WORKFLOW = GITHUB / "workflows" / "ci.yml"

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
# The labels nxrobins/viewspec has: GitHub's defaults and the ones Dependabot puts on its pull
# requests. GitHub's schema allows any label, but GitHub skips one the repository does not have, so
# the repository's rule is that a template applies only labels from this list. Add a label here
# when you create it in the repository.
REPOSITORY_LABELS = {
    "bug",
    "dependencies",
    "documentation",
    "duplicate",
    "enhancement",
    "github_actions",
    "good first issue",
    "help wanted",
    "invalid",
    "javascript",
    "python",
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


def _issue_forms(directory: Path = ISSUE_TEMPLATES) -> list[Path]:
    return sorted(path for path in directory.glob("*.y*ml") if path.name not in {"config.yml", "config.yaml"})


def _markdown_templates(directory: Path = ISSUE_TEMPLATES) -> list[Path]:
    # GitHub reads issue template file names without regard to case.
    return sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".md")


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _front_matter(text: str) -> Any:
    """The YAML between the ``---`` lines that open a Markdown issue template; None without them."""
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return None
    for end in range(1, len(lines)):
        if lines[end].rstrip() == "---":
            return yaml.safe_load("\n".join(lines[1:end]))
    return None


def _template_settings(directory: Path = ISSUE_TEMPLATES) -> dict[str, Any]:
    """Each issue template in ``directory`` by file name: a form's YAML, or a Markdown template's front matter."""
    settings = {path.name: _load(path) for path in _issue_forms(directory)}
    for path in _markdown_templates(directory):
        settings[path.name] = _front_matter(path.read_text(encoding="utf-8"))
    return settings


def _template_labels(settings: Any) -> list[Any]:
    """The labels a template applies, given as a list or as a comma-delimited string."""
    labels = settings.get("labels") if isinstance(settings, dict) else None
    if labels is None:
        return []
    if isinstance(labels, str):
        return [label.strip() for label in labels.split(",") if label.strip()]
    return labels if isinstance(labels, list) else [labels]


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
    """The reference GitHub derives from a label without an id, following Rails ``parameterize``.

    Rails transliterates the label to ASCII, turns each run of characters other than letters,
    digits, ``-`` and ``_`` into ``-``, collapses repeated ``-``, strips ``-`` from both ends and
    lowercases the result, so "A - B" and "A B" both become ``a-b`` and "Café" becomes ``cafe``.
    Here the transliteration is NFKD decomposition with the combining marks dropped, which agrees
    with Rails on accented Latin letters. It does not agree on letters that have no decomposition,
    such as "ß", "æ" and "ø", which Rails spells "ss", "ae" and "o" and this turns into ``-``, or on
    compatibility characters such as ligatures and full-width letters, which this expands and Rails
    drops.
    """
    decomposed = "".join(char for char in unicodedata.normalize("NFKD", label) if not unicodedata.combining(char))
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9_-]+", "-", decomposed.lower())).strip("-")


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
    """The documented issue-form rules ``form`` breaks, of those checked here; empty when it breaks none.

    The rules come from GitHub's "Syntax for issue forms", form-schema, "Common validation errors"
    and "Configuring issue templates" pages. Not all of them are checked: the uniqueness of the name
    across templates is left to ``template_name_problems``, and nothing checks the values
    ``render``, ``min_length`` and ``accept`` take or the types of ``assignees``, ``labels``,
    ``projects``, ``title`` and ``type``. GitHub does not publish its whole list of forbidden label
    words, so only the documented one, "password", is checked, and ``_parameterize`` only
    approximates the references GitHub derives from labels. An empty result is not proof that
    GitHub accepts the form.
    """
    if not isinstance(form, dict):
        return ["the form is not a mapping"]
    problems = _non_string_keys(form)
    if problems:
        return problems
    problems += [f"required top level key {key!r} is missing" for key in TOP_LEVEL_REQUIRED if key not in form]
    problems += [f"{key!r} is not a permitted key" for key in form if key not in TOP_LEVEL_KEYS]
    problems += [f"{key} must be a non-empty string" for key in ("name", "description") if not _is_text(form.get(key))]
    name = form.get("name")
    if _is_text(name) and len(name.strip()) <= 3:
        problems.append(f"name {name!r} must be more than 3 characters, or GitHub does not show the template")
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


def template_name_problems(directory: Path = ISSUE_TEMPLATES) -> list[str]:
    """How the names of the issue templates in ``directory`` break GitHub's rules; empty when they break none.

    Every template, issue form or Markdown template, needs a name of more than 3 characters, or
    GitHub does not show it, and the name must differ from every other template's. A Markdown
    template sets its name in its front matter.
    """
    problems: list[str] = []
    names: list[str] = []
    for file_name, settings in _template_settings(directory).items():
        name = settings.get("name") if isinstance(settings, dict) else None
        if not _is_text(name):
            problems.append(f"{file_name}: the template has no name")
        elif len(name.strip()) <= 3:
            problems.append(f"{file_name}: name {name!r} must be more than 3 characters")
        else:
            names.append(name.strip())
    repeated = sorted({name for name in names if names.count(name) > 1})
    return problems + [f"name {name!r} is used by more than one template" for name in repeated]


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


def test_issue_template_names_are_shown_and_unique() -> None:
    assert template_name_problems() == []


def test_template_names_are_read_from_markdown_templates_too(tmp_path: Path) -> None:
    (tmp_path / "config.yml").write_text("blank_issues_enabled: false\n", encoding="utf-8")
    (tmp_path / "bug_report.yml").write_text("name: Bug report\ndescription: d\nbody: []\n", encoding="utf-8")
    (tmp_path / "bug.md").write_text("---\nname: Bug report\nabout: Report a bug.\n---\n\nSteps:\n", encoding="utf-8")
    (tmp_path / "Ask.MD").write_text("---\nname: Ask\nabout: Ask a question.\n---\n", encoding="utf-8")
    assert template_name_problems(tmp_path) == [
        "Ask.MD: name 'Ask' must be more than 3 characters",
        "name 'Bug report' is used by more than one template",
    ]


@pytest.mark.parametrize(("name", "shown"), [("Bugs", True), ("Bug", False), ("  Bug  ", False)])
def test_issue_form_name_needs_more_than_3_characters(name: str, shown: bool) -> None:
    form = {"name": name, "description": "d", "body": [{"type": "input", "attributes": {"label": "A"}}]}
    problems = issue_form_problems(form)
    assert (problems == []) is shown, problems


# Each case breaks one rule from GitHub's "Common validation errors when creating issue forms" page
# or its form schema.
_HEADER = "name: Bug report\ndescription: d\n"
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
    (
        _HEADER + "body: [{type: input, attributes: {label: 'A - B'}}, {type: input, attributes: {label: 'A B'}}]",
        "labels are too similar: 'a-b'",
    ),
    (
        _HEADER + "body: [{type: input, attributes: {label: Café}}, {type: input, attributes: {label: Cafe}}]",
        "labels are too similar: 'cafe'",
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
    (_HEADER + "on: x\nbody: [{type: input, attributes: {label: A}}]", "not a string"),
]


@pytest.mark.parametrize(("text", "expected"), _INVALID_FORMS, ids=[expected for _, expected in _INVALID_FORMS])
def test_issue_form_checker_rejects_what_github_rejects(text: str, expected: str) -> None:
    problems = issue_form_problems(yaml.safe_load(text))
    assert any(expected in problem for problem in problems), problems


# Forms GitHub documents as valid: the fixes its "Common validation errors" page gives for those
# errors, the examples on its form-schema page, and the top-level keys of its sample forms. The
# checkboxes element gets the label the schema requires, which the page's example leaves out.
# Accepting these does not show that the checker accepts every form GitHub accepts.
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
    # Rails keeps underscores, so these labels parameterize apart, to name_1 and name-1.
    "body: [{type: input, attributes: {label: Name_1}}, {type: input, attributes: {label: Name 1}}]",
    # The top-level keys of the sample form on the "Syntax for issue forms" page, whose labels this
    # repository does not have: GitHub skips such a label, it does not reject the form.
    'title: "[Bug]: "\nlabels: ["bug", "triage"]\nprojects: ["octo-org/1", "octo-org/44"]\nassignees: [octocat]\n'
    "type: bug\nbody: [{type: input, attributes: {label: A}}]",
    # The labels of the page's example of a Markdown template converted to a form.
    'labels: ["Bug", "Needs Triage"]\nbody: [{type: input, attributes: {label: A}}]',
]


@pytest.mark.parametrize("text", _VALID_FORMS)
def test_issue_form_checker_accepts_githubs_examples(text: str) -> None:
    assert issue_form_problems(yaml.safe_load(_HEADER + text)) == []


# The references ActiveSupport 7.2's ``parameterize`` gives: its documented examples, the label of
# GitHub's "Labels are too similar" example, separators around a hyphen, an accented letter, and an
# underscore, which Rails keeps.
@pytest.mark.parametrize(
    ("label", "reference"),
    [
        ("Donald E. Knuth", "donald-e-knuth"),
        ("^très|Jolie-- ", "tres-jolie"),
        ("Name???????", "name"),
        ("A - B", "a-b"),
        ("A B", "a-b"),
        ("Café", "cafe"),
        ("Name_1", "name_1"),
    ],
)
def test_parameterize_gives_the_reference_rails_gives(label: str, reference: str) -> None:
    assert _parameterize(label) == reference


def test_bug_report_requires_the_facts_a_maintainer_needs() -> None:
    body = _load(ISSUE_TEMPLATES / "bug_report.yml")["body"]
    required = {element["id"] for element in body if "id" in element and element.get("validations", {}).get("required")}
    missing = BUG_REPORT_FACTS - required
    assert not missing, missing
    output = next(element for element in body if element.get("id") == "output")
    assert "`--json`" in output["attributes"]["description"]
    reproduction = next(element for element in body if element.get("id") == "reproduction")
    assert "IntentBundle" in reproduction["attributes"]["label"]


def code_field_problems(body: list[Any], code_fields: set[str]) -> list[str]:
    """How the textareas in ``body`` break the repository's rules for fields that take code.

    GitHub puts a textarea answer into the issue as Markdown unless ``render`` is set, and
    ``render`` turns off attachments. Unfenced, pasted HTML loses its tags and the front matter of a
    ``DESIGN.md`` becomes a heading. So each field in ``code_fields`` either sets ``render`` or
    shows how to fence the text, without starting a description line with a fence, and a field
    whose description offers attachments does not set ``render``. GitHub requires none of this, and
    other textareas, such as one that asks for pasted links, need no fence.
    """
    textareas = [
        (element.get("id"), element.get("attributes") or {}) for element in body if element.get("type") == "textarea"
    ]
    problems = [f"{field_id}: no textarea has this id" for field_id in sorted(code_fields - {i for i, _ in textareas})]
    for field_id, attributes in textareas:
        description = attributes.get("description", "")
        if "attach" in description.lower() and "render" in attributes:
            problems.append(f"{field_id}: render turns off the attachments the description offers")
        if field_id in code_fields and "render" not in attributes:
            if "```" not in description:
                problems.append(f"{field_id}: takes code but neither sets render nor shows how to fence it")
            # A description line that starts with a fence would turn the rest of it into a code block.
            if any(line.lstrip().startswith("```") for line in description.splitlines()):
                problems.append(f"{field_id}: a description line starts with a fence")
    return problems


@pytest.mark.parametrize("path", _issue_forms(), ids=lambda path: path.name)
def test_text_fields_that_take_code_keep_it_readable_in_the_issue(path: Path) -> None:
    assert code_field_problems(_load(path)["body"], CODE_FIELDS.get(path.name, set())) == []


def test_only_the_fields_that_take_code_must_show_a_fence() -> None:
    links = {
        "type": "textarea",
        "id": "links",
        "attributes": {"label": "Related links", "description": "Paste links to related issues or pull requests."},
    }
    assert code_field_problems([links], set()) == []
    assert code_field_problems([links], {"links"}) == [
        "links: takes code but neither sets render nor shows how to fence it"
    ]


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


def test_issue_templates_apply_only_labels_the_repository_has() -> None:
    """The repository's rule, not GitHub's: every label a template applies is in ``REPOSITORY_LABELS``."""
    # A template may also give its labels as a comma-delimited string, as a Markdown template does.
    assert _template_labels({"labels": "bug, Needs Triage"}) == ["bug", "Needs Triage"]
    unknown = [
        (file_name, label)
        for file_name, settings in _template_settings().items()
        for label in _template_labels(settings)
        if label not in REPOSITORY_LABELS
    ]
    assert unknown == []


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


_OWNER = re.compile(r"@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:/[A-Za-z0-9._-]+)?|[^@\s]+@[^@\s]+\.[^@\s]+")


def codeowners_problems(text: str) -> list[str]:
    """How a CODEOWNERS file breaks GitHub's owner syntax or the repository's catch-all rule.

    Each owner is a username, a team (``@org/team``) or an email address. A rule may name no owner:
    GitHub documents that as leaving the paths it matches without one, as in its ``/apps/github``
    example. The repository's own rule is that the file starts with the catch-all ``*`` and that
    the catch-all names an owner: the last matching rule wins, so a catch-all further down would
    override every rule above it, and a path no later rule matches keeps the catch-all's owner.
    """
    rules = codeowners_rules(text)
    if not rules:
        return ["the file has no rules"]
    problems = [
        f"{pattern}: {name!r} is not a username, team or email address"
        for pattern, owners in rules
        for name in owners
        if not _OWNER.fullmatch(name)
    ]
    pattern, owners = rules[0]
    if pattern != "*":
        problems.append(f"the first rule is {pattern!r}, not the catch-all *")
    elif not owners:
        problems.append("the catch-all rule * names no owner")
    return problems


def test_codeowners_starts_with_a_catch_all_rule_that_names_the_maintainer() -> None:
    text = CODEOWNERS.read_text(encoding="utf-8")
    assert codeowners_problems(text) == []
    assert "@nxrobins" in codeowners_rules(text)[0][1]


# The rules of the example CODEOWNERS file on GitHub's "About code owners" page. /apps/github names
# no owner, so anyone with write access can approve a change to it.
_GITHUB_CODEOWNERS_EXAMPLE = """\
*       @global-owner1 @global-owner2
*.js    @js-owner #This is an inline comment.
*.go docs@example.com
*.txt @octo-org/octocats
/build/logs/ @doctocat
docs/* docs@example.com
apps/ @octocat
/docs/ @doctocat
/scripts/ @doctocat @octocat
**/logs @octocat
/apps/ @octocat
/apps/github
/apps/ @octocat
/apps/github @doctocat
"""


def test_codeowners_check_allows_rules_without_owners() -> None:
    assert codeowners_problems(_GITHUB_CODEOWNERS_EXAMPLE) == []
    assert codeowners_problems("*\n/apps/ @octocat\n") == ["the catch-all rule * names no owner"]
    assert codeowners_problems("/apps/github\n* @nxrobins\n") == [
        "the first rule is '/apps/github', not the catch-all *"
    ]
    assert codeowners_problems("* nxrobins\n") == ["*: 'nxrobins' is not a username, team or email address"]


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


def _contributing_commands() -> list[str]:
    """The commands in the block under "Running the checks" in CONTRIBUTING.md."""
    blocks = re.findall(r"```bash\n(.*?)```", _contributing_section("Running the checks"), flags=re.DOTALL)
    assert blocks, "CONTRIBUTING.md has no command block under 'Running the checks'"
    return [line.strip() for line in blocks[0].splitlines() if line.strip()]


def _ci_commands(job: str | None = None) -> list[str]:
    """The commands ``ci.yml`` runs from the repository root, in ``job`` or in every job.

    Each line of a ``run`` step is one command. Steps that install something, or that run in another
    directory, are left out.
    """
    jobs = _load(CI_WORKFLOW)["jobs"]
    steps = [step for name, body in jobs.items() if job in (None, name) for step in body.get("steps", [])]
    return [
        line.strip()
        for step in steps
        if "run" in step and "working-directory" not in step and not str(step.get("name", "")).startswith("Install")
        for line in str(step["run"]).splitlines()
        if line.strip()
    ]


def _without_quiet_flag(command: str) -> str:
    """``command`` without ``-q``, which changes how much a command prints, not what it checks."""
    return " ".join(word for word in command.split() if word != "-q")


def test_pull_request_checklist_names_every_check_ci_runs() -> None:
    """CONTRIBUTING.md says CI runs the same commands as its "Running the checks" block, and the
    checklist repeats that block. So the block holds every command CI's test job runs from the
    repository root, CI runs every command in the block, and each one has a checklist item."""
    commands = _contributing_commands()
    documented = {_without_quiet_flag(command) for command in commands}
    test_job = [_without_quiet_flag(command) for command in _ci_commands("test")]
    assert "ruff check ." in test_job, test_job
    assert [command for command in test_job if command not in documented] == []
    every_job = {_without_quiet_flag(command) for command in _ci_commands()}
    assert [command for command in commands if _without_quiet_flag(command) not in every_job] == []
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
