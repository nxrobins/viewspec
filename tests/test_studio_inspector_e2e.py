"""Opt-in proof that the `viewspec studio` inspector overlay works in real Chromium.

The unit tests check the inspector map and that the overlay parses. This drives the overlay itself:
hover shows a node's source path, click and shift+click copy the source and value paths, Alt+I
turns the inspector off and the choice survives a reload, and a source edit reloads the preview.

Set ``VIEWSPEC_RUN_STUDIO_E2E=1`` after ``npm ci --ignore-scripts`` and ``npx playwright install
chromium`` in ``src/viewspec/html_verify_template``, whose pinned Playwright this test borrows.
``VIEWSPEC_HTML_VERIFY_NODE_MODULES_DIR`` points at another install of the same harness. Once opted
in, a missing harness fails rather than skips.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from viewspec.cli import main as cli_main
from viewspec.intent_tools import starter_compact_payload
from viewspec.studio import Studio

E2E_OPT_IN = "VIEWSPEC_RUN_STUDIO_E2E"
ROOT = Path(__file__).resolve().parents[1]

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(os.environ.get(E2E_OPT_IN) != "1", reason=f"set {E2E_OPT_IN}=1 to run the Studio proof"),
]

# The script prints one JSON object; every step it cannot take is an error, never a silent pass.
_DRIVER = r"""
import { readFileSync, writeFileSync } from "node:fs";
import { chromium } from "playwright";

const [url, sourcePath, before, after] = process.argv.slice(2);
const out = { pageErrors: [], consoleErrors: [] };
const tipText = (page) =>
  page.evaluate(() => {
    const tip = document.querySelector("[data-viewspec-studio-inspector]").children[1];
    return tip.style.display === "none" ? null : tip.textContent;
  });
const pillText = (page) =>
  page.evaluate(() => document.querySelector("[data-viewspec-studio-inspector] button").textContent);

const browser = await chromium.launch();
try {
  const context = await browser.newContext();
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: new URL(url).origin });
  const page = await context.newPage();
  page.on("pageerror", (error) => out.pageErrors.push(String(error)));
  page.on("console", (message) => { if (message.type() === "error") out.consoleErrors.push(message.text()); });
  await page.goto(url);

  const value = page.locator("#dom-binding_revenue_value");
  const label = page.locator("#dom-binding_revenue_label");
  await page.waitForFunction(() => document.querySelector("[data-viewspec-studio-inspector] button"));
  out.pillOn = await pillText(page);

  // The map loads after the page, so hover until the tip names a source.
  await value.hover();
  await page.waitForFunction(
    () => /source: /.test(document.querySelector("[data-viewspec-studio-inspector]").children[1].textContent),
  );
  out.hoverTip = await tipText(page);

  await value.click();
  out.clickCopy = await page.evaluate(() => navigator.clipboard.readText());
  await value.click({ modifiers: ["Shift"] });
  out.shiftClickCopy = await page.evaluate(() => navigator.clipboard.readText());

  await page.keyboard.press("Alt+KeyI");
  out.pillOff = await pillText(page);
  await label.hover();
  out.tipWhileOff = await tipText(page);

  await page.reload();
  await page.waitForFunction(() => document.querySelector("[data-viewspec-studio-inspector] button"));
  out.pillAfterReload = await pillText(page);
  await page.keyboard.press("Alt+KeyI");
  out.pillToggledBack = await pillText(page);

  // Live reload: the page polls the build generation and reloads when it moves.
  const source = readFileSync(sourcePath, "utf8");
  if (!source.includes(before)) throw new Error(`source does not contain ${before}`);
  writeFileSync(sourcePath, source.replace(before, after));
  await page.waitForFunction((text) => document.querySelector("#dom-binding_revenue_value")?.textContent === text, after, {
    timeout: 15000,
  });
  out.reloadedText = await value.textContent();
} finally {
  await browser.close();
}
console.log(JSON.stringify(out));
"""


def _harness_node_modules() -> Path:
    configured = os.environ.get("VIEWSPEC_HTML_VERIFY_NODE_MODULES_DIR")
    path = Path(configured) if configured else ROOT / "src" / "viewspec" / "html_verify_template" / "node_modules"
    if shutil.which("node") is None:
        pytest.fail("the Studio proof needs Node.js on PATH")
    if not (path / "playwright").is_dir():
        pytest.fail(f"no Playwright under {path}; run npm ci --ignore-scripts in src/viewspec/html_verify_template")
    return path


@pytest.fixture
def compact_studio(tmp_path: Path) -> Iterator[tuple[Studio, Path]]:
    source = tmp_path / "viewspec.compact.json"
    source.write_text(json.dumps(starter_compact_payload("dashboard"), indent=2), encoding="utf-8")
    with Studio(source, port=0) as studio:
        threading.Thread(target=studio.serve, daemon=True).start()
        yield studio, source


def _canonical_paths(tmp_path: Path, source: Path) -> tuple[str, str]:
    # Work the expected paths out from the canonical document, not from the inspector map.
    canonical = tmp_path / "canonical.intent.json"
    assert cli_main(["check", str(source), "--canonical-out", str(canonical)]) == 0
    bundle = json.loads(canonical.read_text(encoding="utf-8"))
    bindings = bundle["view_spec"]["bindings"]
    index = next(i for i, binding in enumerate(bindings) if binding["id"] == "revenue_value")
    node, attr = bindings[index]["address"].removeprefix("node:").split("#attr:")
    assert bundle["substrate"]["nodes"][node]["attrs"][attr] == "$12.4K"
    return f"$.view_spec.bindings[{index}]", f"$.substrate.nodes.{node}.attrs.{attr}"


def test_the_inspector_overlay_works_in_chromium(tmp_path, compact_studio):
    studio, source = compact_studio
    source_path, value_path = _canonical_paths(tmp_path, source)

    driver_dir = tmp_path / "driver"
    driver_dir.mkdir()
    (driver_dir / "node_modules").symlink_to(_harness_node_modules(), target_is_directory=True)
    (driver_dir / "drive.mjs").write_text(_DRIVER, encoding="utf-8")
    completed = subprocess.run(
        ["node", "drive.mjs", studio.url, str(source), "$12.4K", "$99.9K"],
        cwd=driver_dir,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    result = json.loads(completed.stdout.strip().splitlines()[-1])

    assert result["pillOn"] == "inspector on (Alt+I)"
    tip = result["hoverTip"]
    assert "data-ir-id: binding_revenue_value" in tip
    assert "data-binding-id: revenue_value" in tip
    assert "motif: metrics (dashboard)" in tip
    assert "region: main" in tip
    assert f"source: {source_path}\n" in tip
    assert f"value: {value_path}\n" in tip
    assert "paths name the canonical bundle" in tip

    assert result["clickCopy"] == source_path
    assert result["shiftClickCopy"] == value_path

    assert result["pillOff"] == "inspector off (Alt+I)"
    assert result["tipWhileOff"] is None
    assert result["pillAfterReload"] == "inspector off (Alt+I)"
    assert result["pillToggledBack"] == "inspector on (Alt+I)"

    assert result["reloadedText"] == "$99.9K"
    assert result["pageErrors"] == []
    assert result["consoleErrors"] == []


# The page is served with the generation it shows. When it learned the generation from its first poll,
# half a second after load, an edit that rebuilt before that poll left the old build on screen.
_RACE_DRIVER = r"""
import { readFileSync, writeFileSync } from "node:fs";
import { chromium } from "playwright";

const [url, sourcePath, before, after] = process.argv.slice(2);
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.goto(url);
  writeFileSync(sourcePath, readFileSync(sourcePath, "utf8").replace(before, after));
  const reloaded = await page
    .waitForFunction((text) => document.querySelector("#dom-binding_revenue_value")?.textContent === text, after, {
      timeout: 5000,
    })
    .then(() => true, () => false);
  console.log(JSON.stringify({ reloaded }));
} finally {
  await browser.close();
}
"""


def test_an_edit_right_after_load_still_reloads(tmp_path, compact_studio):
    studio, source = compact_studio
    driver_dir = tmp_path / "driver"
    driver_dir.mkdir()
    (driver_dir / "node_modules").symlink_to(_harness_node_modules(), target_is_directory=True)
    (driver_dir / "race.mjs").write_text(_RACE_DRIVER, encoding="utf-8")
    completed = subprocess.run(
        ["node", "race.mjs", studio.url, str(source), "$12.4K", "$99.9K"],
        cwd=driver_dir,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    assert studio.generation == 2, "the edit never rebuilt, so this would not show the race"
    assert json.loads(completed.stdout.strip().splitlines()[-1])["reloaded"] is True
