import { expect, test, type Locator } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const workspacePython = join(repoRoot, ".venv/bin/python");
const python = process.env.PYTHON ?? (existsSync(workspacePython) ? workspacePython : "python");
let workspace = "", source = "", state = "", url = "";
function run(args: string[], allowFailure = false) {
  const result = spawnSync(python, ["-m", "viewspec", ...args], {
    cwd: repoRoot, encoding: "utf8", timeout: 60_000, maxBuffer: 4 * 1024 * 1024,
  });
  if (result.error) throw result.error;
  if (result.status !== 0 && !allowFailure) throw new Error(result.stderr || result.stdout);
  return result.stdout;
}
test.beforeAll(async () => {
  workspace = mkdtempSync(join(tmpdir(), "viewspec-keyboard-"));
  source = join(workspace, "viewspec.app.json"); state = join(workspace, "review-state");
  run(["init-app", "--out", source, "--template", "react-app"]);
  const port = await new Promise<number>((resolvePort) => {
    const server = createServer(); server.listen(0, "127.0.0.1", () => {
      const address = server.address() as { port: number };
      server.close(() => resolvePort(address.port));
    });
  });
  url = JSON.parse(run(["studio", source, "--no-open", "--port", String(port),
    "--state-dir", state, "--compare", "--install", "--json"])).review.url;
});
test.afterAll(() => {
  if (source && state) run(["review-end", source, "--state-dir", state, "--json"], true);
  if (workspace) rmSync(workspace, { recursive: true, force: true });
});
test("keyboard reviewers select ordinary content, send exact requests, and return to the canvas", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(url);
  await expect(page.locator("#status")).toHaveText("Checked target pair ready");
  const focused = (element: Locator) => element.evaluate(node =>
    node === node.ownerDocument.activeElement && node.ownerDocument.hasFocus());
  const tabTo = async (element: Locator) => {
    for (let i = 0; i < 100 && !(await focused(element)); i++) await page.keyboard.press("Tab");
    expect(await focused(element)).toBe(true);
  };
  const evidence: unknown[] = [];
  for (const selector of ["#artifact-react", "#artifact"]) {
    const frame = page.frameLocator(selector);
    const queue = frame.getByRole("button", { name: "Queue", exact: true });
    await tabTo(queue);
    const heading = frame.getByRole("heading", { name: "Incident Queue", exact: true });
    const status = frame.locator("[data-viewspec-app-screen='queue'] [data-binding-id='inc_1042_status']");
    const action = frame.getByRole("button", { name: "Triage", exact: true });
    const original = await heading.getAttribute("tabindex");
    const tabOrder = () => frame.locator("[id][data-ir-id]").evaluateAll(nodes =>
      nodes.map(node => [node.id, node.getAttribute("tabindex")]));
    const originalTabs = await tabOrder();
    await page.keyboard.press("Control+i");
    await expect(page.getByRole("button", { name: "Preview", exact: true })).toBeVisible();
    for (const [index, target] of [heading, status, action].entries()) {
      // Navigate from a native control to semantic headings, cells, and actions with real keys.
      for (let i = 0; i < 100 && !(await focused(target)); i++) await page.keyboard.press("ArrowDown");
      expect(await focused(target)).toBe(true);
      const id = await target.getAttribute("id");
      expect(id).toBeTruthy();
      await expect(frame.locator("[data-viewspec-review-target][tabindex='0']")).toHaveCount(1);
      await expect(frame.getByRole("status")).toContainText("Enter or Space");
      expect(await target.evaluate(node => getComputedStyle(node).outlineStyle)).toBe("solid");
      if (index === 0) await page.screenshot({ path: testInfo.outputPath(`keyboard-heading-${selector.slice(1)}.png`) });
      await page.keyboard.press(index === 1 ? "Space" : "Enter");
      await expect(page.getByLabel("What should be different?", { exact: true })).toBeFocused();
      await expect(page.locator("#trace")).toContainText(`Selected: ${id}`);
      const body = `Keyboard request ${selector} ${index}`;
      await page.keyboard.type(body);
      await tabTo(page.getByRole("button", { name: "Send to agent", exact: true }));
      const submitted = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith("/api/v1/events"));
      await page.keyboard.press("Enter");
      expect((await (await submitted).json()).ok).toBe(true);
      const polled = JSON.parse(run(["review-poll", source, "--state-dir", state, "--timeout-ms", "1", "--json"]));
      const event = polled.batch.events[0];
      expect(event.body).toBe(body);
      expect(event.target.target_resolution).toBe("exact");
      expect(event.target.screen_id).toBe("queue");
      if (index === 1) expect(event.target.binding_id).toBe("inc_1042_status");
      if (index === 2) expect(event.target.action_id).toBe("triage_incident");
      evidence.push({ surface: selector, dom_id: id, target: event.target, sent: true });
      run(["review-poll", source, "--state-dir", state, "--ack", polled.batch.batch_id, "--timeout-ms", "1", "--json"]);
      if (index === 0) {
        await tabTo(page.getByRole("button", { name: "Close", exact: true }));
        await page.keyboard.press("Enter");
      } else await page.keyboard.press("Escape");
      await expect(page.locator("#studio-panel")).toHaveAttribute("aria-hidden", "true");
      await expect(target).toBeFocused();
      await expect.poll(() => focused(target)).toBe(true);
      if (index === 0) {
        // A target becoming hidden while focused must leave the annotation order and recover focus.
        const hiddenHeading = frame.locator(`[id='${id}']`);
        await hiddenHeading.evaluate(node => node.setAttribute("hidden", ""));
        await expect(hiddenHeading).not.toBeFocused();
        await expect(hiddenHeading).not.toHaveAttribute("data-viewspec-review-target", "true");
        await expect(frame.locator("[data-viewspec-review-target][tabindex='0']")).toBeFocused();
        await hiddenHeading.evaluate(node => node.removeAttribute("hidden"));
      }
    }
    await page.keyboard.press("Home");
    const first = await frame.locator("[data-viewspec-review-target][tabindex='0']").getAttribute("id");
    await page.keyboard.press("End");
    const last = await frame.locator("[data-viewspec-review-target][tabindex='0']").getAttribute("id");
    expect(last).not.toBe(first);
    await page.keyboard.press("ArrowDown");
    await expect(frame.locator("[data-viewspec-review-target][tabindex='0']")).toHaveAttribute("id", first!);
    await page.keyboard.press("ArrowUp");
    await expect(frame.locator("[data-viewspec-review-target][tabindex='0']")).toHaveAttribute("id", last!);
    await page.keyboard.press("Control+i");
    await expect.poll(() => heading.getAttribute("tabindex")).toBe(original);
    await expect.poll(tabOrder).toEqual(originalTabs);
    await expect(frame.locator("[data-viewspec-review-target]")).toHaveCount(0);
    await expect(frame.getByRole("status")).toHaveCount(0);
    // Native navigation still activates in Preview and changes the rendered screen.
    await tabTo(frame.getByRole("button", { name: "Incident", exact: true }));
    await page.keyboard.press("Enter");
    await expect(frame.getByRole("heading", { name: "Incident Detail", exact: true })).toBeVisible();
    await page.keyboard.press("Control+i");
    await expect(page.getByRole("button", { name: "Preview", exact: true })).toBeVisible();
    await page.keyboard.press("End");
    await expect(frame.locator("[data-viewspec-review-target][tabindex='0']")).toHaveCount(1);
    await expect(frame.locator("[data-viewspec-app-screen='queue'] [data-viewspec-review-target]")).toHaveCount(0);
    const detailStatus = frame.locator("[data-viewspec-app-screen='detail'] [data-binding-id='inc_1042_status']");
    for (let i = 0; i < 100 && !(await focused(detailStatus)); i++) await page.keyboard.press("ArrowDown");
    await expect(detailStatus).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByLabel("What should be different?", { exact: true })).toBeFocused();
    await expect(page.locator("#trace")).toContainText("Screen: detail");
    await page.keyboard.press("Escape");
    await expect(detailStatus).toBeFocused();
    await page.keyboard.press("Control+i");
    await tabTo(queue); await page.keyboard.press("Enter");
    await expect(heading).toBeVisible();
  }
  expect(errors).toEqual([]);
  const path = testInfo.outputPath("studio-keyboard-evidence.json");
  writeFileSync(path, JSON.stringify({ status: "passed", selections: evidence, runtime_errors: errors }, null, 2));
  await testInfo.attach("studio-keyboard-evidence", { path, contentType: "application/json" });
});
