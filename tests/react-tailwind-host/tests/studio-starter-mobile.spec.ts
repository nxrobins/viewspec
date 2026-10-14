import { expect, test } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const workspacePython = join(root, ".venv/bin/python");
const python = process.env.PYTHON ?? (existsSync(workspacePython) ? workspacePython : "python");
const sessions: { name: string; source: string; state: string; url: string; detailLabel: string }[] = [];
let workspace = "";
function run(args: string[], allowFailure = false) {
  const result = spawnSync(python, ["-m", "viewspec", ...args], {
    cwd: root, encoding: "utf8", timeout: 60_000, maxBuffer: 4 * 1024 * 1024,
  });
  if (result.error) throw result.error;
  if (result.status !== 0 && !allowFailure) throw new Error(result.stderr || result.stdout);
  return result.stdout;
}
test.beforeAll(async () => {
  workspace = mkdtempSync(join(tmpdir(), "viewspec-starter-mobile-"));
  for (const name of ["default", "localized"]) {
    const source = join(workspace, `${name}.app.json`), state = join(workspace, name);
    run(["init-app", "--out", source, "--template", "react-app"]);
    if (name === "localized") {
      const app = JSON.parse(readFileSync(source, "utf8"));
      app.app.title = "Einsatzleitstelle";
      app.routes[0].label = "Offene Einsatzvorfälle";
      app.routes[1].label = "Details zum Einsatzvorfall";
      for (const screen of app.screens) {
        const nodes = screen.intent_bundle.substrate.nodes;
        nodes[screen.intent_bundle.substrate.root_id].attrs.title = screen.id === "queue"
          ? "Warteschlange für Einsatzvorfälle" : "Details zum Einsatzvorfall";
      }
      app.resources[0].records[0].severity = "Hohe Priorität";
      app.resources[0].records[0].status = "Wiederherstellung abgeschlossen";
      app.resources[0].records[1].severity = "Mittlere Priorität";
      app.resources[0].records[1].status = "Wartet auf Einsatzbestätigung";
      for (const screen of app.screens) for (const record of app.resources[0].records) {
        const node = screen.intent_bundle.substrate.nodes[record.id];
        if (node) node.attrs = { ...record };
      }
      // Isolate fixture text wrapping from the starter's intentionally English reducer assertions.
      for (const key of ["state", "mutations", "selectors", "visibility", "state_replay_assertions"]) app[key] = [];
      writeFileSync(source, JSON.stringify(app, null, 2));
    }
    const port = await new Promise<number>((resolvePort, reject) => {
      const server = createServer(); server.once("error", reject);
      server.listen(0, "127.0.0.1", () => {
        const address = server.address() as { port: number };
        server.close(() => resolvePort(address.port));
      });
    });
    const started = JSON.parse(run(["studio", source, "--no-open", "--port", String(port),
      "--state-dir", state, "--compare", "--install", "--json"]));
    sessions.push({ name, source, state, url: started.review.url,
      detailLabel: name === "default" ? "Incident" : "Details zum Einsatzvorfall" });
  }
});
test.afterAll(() => {
  for (const session of sessions) run(["review-end", session.source, "--state-dir", session.state, "--json"], true);
  if (workspace) rmSync(workspace, { recursive: true, force: true });
});
test("starter content remains readable on mobile, including long translated values", async ({ page }, testInfo) => {
  const errors: string[] = [], evidence: unknown[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const waitForScreen = async (screen: string) => {
    for (const selector of ["#artifact", "#artifact-react"]) {
      await expect(page.frameLocator(selector).locator(`[data-viewspec-app-screen='${screen}']`)).toBeVisible();
    }
    // Let both route-context messages settle before issuing another navigation.
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  };
  for (const session of sessions) {
    await page.goto(session.url);
    await expect(page.locator("#status")).toHaveText("Checked target pair ready");
    for (const viewport of ["mobile", "tablet", "desktop"]) {
      await page.getByLabel("Canvas", { exact: true }).selectOption(viewport);
      for (const screen of ["queue", "detail"]) {
        if (screen === "detail") {
          await page.frameLocator("#artifact-react").getByRole("button", { name: session.detailLabel, exact: true }).click();
          await waitForScreen("detail");
        }
        for (const selector of ["#artifact", "#artifact-react"]) {
          const frame = page.frameLocator(selector);
          const content = frame.locator(`[data-viewspec-app-screen='${screen}']`);
          await expect(content).toBeVisible();
          const title = session.name === "default"
            ? screen === "queue" ? "Incident Queue" : "Incident Detail"
            : screen === "queue" ? "Warteschlange für Einsatzvorfälle" : "Details zum Einsatzvorfall";
          await expect(content.getByRole("heading", { name: title, exact: true })).toBeVisible();
          if (viewport === "mobile") await page.locator(selector).screenshot({ path: testInfo.outputPath(`${session.name}-${screen}-${selector.slice(1)}.png`) });
          const measured = await frame.locator(`[data-viewspec-app-screen='${screen}'] [data-binding-id]:visible, [data-viewspec-app-screen='${screen}'] h1:visible, .vs-app-header button:visible, .vs-app-chrome button:visible`).evaluateAll(elements => elements.map(element => {
            const box = element.getBoundingClientRect(), words: { text: string; lines: number; fits: boolean }[] = [];
            const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) {
              const node = walker.currentNode;
              for (const match of (node.textContent || "").matchAll(/\S+/g)) {
                const range = document.createRange(); range.setStart(node, match.index!); range.setEnd(node, match.index! + match[0].length);
                const rects = [...range.getClientRects()].filter(rect => rect.width > 0);
                words.push({ text: match[0], lines: new Set(rects.map(rect => Math.round(rect.y))).size,
                  fits: rects.every(rect => rect.left >= box.left - 1 && rect.right <= box.right + 1 && rect.left >= -1 && rect.right <= innerWidth + 1) });
              }
            }
            return { id: element.id, text: element.textContent, words, fontSize: parseFloat(getComputedStyle(element).fontSize) };
          }));
          for (const target of measured) for (const word of target.words) {
            expect(word.lines, `${session.name} ${viewport} ${selector} ${target.id}: ${word.text} splits`).toBe(1);
            expect(word.fits, `${session.name} ${viewport} ${selector} ${target.id}: ${word.text} clips`).toBe(true);
          }
          const overflow = await frame.locator("html").evaluate(element => element.scrollWidth - element.clientWidth);
          expect(overflow).toBeLessThanOrEqual(0);
          evidence.push({ scenario: session.name, screen, viewport, surface: selector, targets: measured, overflow });
        }
      }
      // Return both synchronized renderers to the queue for the next viewport.
      const queueLabel = session.name === "default" ? "Queue" : "Offene Einsatzvorfälle";
      await page.frameLocator("#artifact-react").getByRole("button", { name: queueLabel, exact: true }).click();
      await waitForScreen("queue");
    }
    if (session.name === "default") {
      await page.getByLabel("Canvas", { exact: true }).selectOption("mobile");
      for (const selector of ["#artifact", "#artifact-react"]) {
        const frame = page.frameLocator(selector);
        const status = frame.locator("[data-viewspec-app-screen='queue'] [data-binding-id='inc_1043_status']");
        await expect(status).toBeHidden();
        await frame.getByRole("button", { name: "Triage", exact: true }).click();
        await expect(status).toBeVisible();
        evidence.push({ scenario: "default-after-triage", surface: selector, text: await status.innerText() });
        const lines = await status.evaluate(element => {
          const range = document.createRange(); range.selectNodeContents(element);
          return new Set([...range.getClientRects()].filter(rect => rect.width > 0).map(rect => Math.round(rect.y))).size;
        });
        expect(lines).toBe(1);
      }
    }
  }
  expect(errors).toEqual([]);
  const path = testInfo.outputPath("studio-starter-mobile-evidence.json");
  writeFileSync(path, JSON.stringify({ status: "passed", observations: evidence, runtime_errors: errors }, null, 2));
  await testInfo.attach("studio-starter-mobile-evidence", { path, contentType: "application/json" });
});
