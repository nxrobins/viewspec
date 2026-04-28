from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from viewspec import ViewSpecBuilder, compile


ROOT = Path(__file__).resolve().parents[1]


def _env():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return env


def _run_example(script, tmp_path, *args):
    return subprocess.run(
        [sys.executable, str(ROOT / "examples" / script), *map(str, args)],
        cwd=tmp_path,
        env=_env(),
        check=True,
        text=True,
        capture_output=True,
    )


def test_builder_examples_export_sdk_compatible_json(tmp_path):
    expected_outputs = {
        "invoice_table.py": "output/invoice.json",
        "kpi_dashboard.py": "output/dashboard.json",
        "comparison_view.py": "output/comparison.json",
    }

    for script, output_path in expected_outputs.items():
        _run_example(script, tmp_path)
        payload = json.loads(tmp_path.joinpath(output_path).read_text(encoding="utf-8"))
        assert isinstance(payload["substrate"]["nodes"], dict)
        assert payload["view_spec"]["substrate_id"] == payload["substrate"]["id"]


def test_emit_html_example_emits_artifacts(tmp_path):
    builder = ViewSpecBuilder("example_emit")
    table = builder.add_table("items", region="main", group_id="rows")
    table.add_row(label="Example", value="42")
    ast = compile(builder.build_bundle())
    ast_path = tmp_path / "ast.json"
    ast_path.write_text(json.dumps(ast.to_json()), encoding="utf-8")

    _run_example("emit_html.py", tmp_path, ast_path, "html_output")

    assert tmp_path.joinpath("html_output/index.html").exists()
    assert tmp_path.joinpath("html_output/provenance_manifest.json").exists()
    assert tmp_path.joinpath("html_output/diagnostics.json").exists()
