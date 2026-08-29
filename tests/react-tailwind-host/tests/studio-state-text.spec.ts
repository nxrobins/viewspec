import { expect, test, type Page } from "@playwright/test";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const fixtureRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(fixtureRoot, "../..");
const workspacePython = join(repoRoot, ".venv", "bin", "python");
const python = process.env.PYTHON ?? (existsSync(workspacePython) ? workspacePython : "python");
const sourceFixture = join(
  repoRoot,
  "conformance",
  "agent-ui-v2",
  "fixtures",
  "shakedown-104729-2026-08-06-v5",
  "viewspec-deep.app.json",
);

let workspace = "";
let sourcePath = "";
let reviewState = "";
let studioUrl = "";
let studioPort = 0;

function runViewspec(args: string[], allowFailure = false): string {
  const result = spawnSync(python, ["-m", "viewspec", ...args], {
    cwd: repoRoot,
    encoding: "utf8",
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
    maxBuffer: 4 * 1024 * 1024,
    timeout: 60_000,
  });
  if (result.error) throw result.error;
  if (!allowFailure && result.status !== 0) {
    throw new Error(`viewspec ${args[0]} failed (${result.status}): ${result.stderr || result.stdout}`);
  }
  return result.stdout;
}

async function availablePort(): Promise<number> {
  return await new Promise((resolvePort, rejectPort) => {
    const server = createServer();
    server.once("error", rejectPort);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (address === null || typeof address === "string") {
        server.close();
        rejectPort(new Error("Could not allocate a loopback port."));
        return;
      }
      server.close((error) => (error ? rejectPort(error) : resolvePort(address.port)));
    });
  });
}

function captureRuntimeFailures(page: Page): string[] {
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") failures.push(`console: ${message.text()}`);
  });
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.hostname === "127.0.0.1" && Number(url.port) === studioPort && response.status() >= 400) {
      failures.push(`HTTP ${response.status()}: ${url.pathname}`);
    }
  });
  return failures;
}

test.beforeAll(async () => {
  workspace = mkdtempSync(join(tmpdir(), "viewspec-state-text-browser-"));
  sourcePath = join(workspace, "viewspec.app.json");
  reviewState = join(workspace, "review-state");
  copyFileSync(sourceFixture, sourcePath);
  studioPort = await availablePort();

  const started = JSON.parse(
    runViewspec([
      "studio",
      sourcePath,
      "--no-open",
      "--port",
      String(studioPort),
      "--state-dir",
      reviewState,
      "--compare",
      "--install",
      "--json",
    ]),
  );
  expect(started.studio.comparison.status).toBe("ready");
  expect(started.studio.comparison.targets).toEqual(["html-tailwind-app", "react-tailwind-app"]);
  expect(started.studio.comparison.inspection.coherence_status).toBe("browser_observed");
  expect(started.studio.comparison.inspection.coherence_contract).toBe("semantic_geometry_v1");
  expect(started.studio.comparison.inspection.state_status).toBe("ready");
  studioUrl = started.review.url;
});

test.afterAll(() => {
  if (sourcePath && reviewState) {
    runViewspec(["review-end", sourcePath, "--state-dir", reviewState, "--json"], true);
  }
  if (workspace) rmSync(workspace, { recursive: true, force: true });
});

test("Static and React show the same review-count truth after interaction and replay", async ({ page }, testInfo) => {
  const failures = captureRuntimeFailures(page);
  const response = await page.goto(studioUrl, { waitUntil: "domcontentloaded" });
  expect(response?.status()).toBe(200);
  await expect(page.locator("#status")).toHaveText("Checked target pair ready");
  await expect(page.locator("#studio-panel")).toHaveAttribute("aria-hidden", "true");
  await expect(page.locator("#coherence")).toBeHidden();
  await expect(page.locator("#coherence-card")).toHaveAttribute("data-status", "aligned");
  await expect(page.locator("#coherence-summary")).toHaveText("Static + React align at Mobile");
  await page.getByLabel("Canvas", { exact: true }).selectOption("mobile");

  const staticFrame = page.frameLocator("#artifact");
  const reactFrame = page.frameLocator("#artifact-react");
  const staticCount = staticFrame.locator("[data-state-text-id='review_count_text']");
  const reactCount = reactFrame.locator("[data-state-text-id='review_count_text']");

  await expect(staticCount).toBeHidden();
  await expect(reactCount).toBeHidden();
  await staticFrame.getByRole("button", { name: "Record review", exact: true }).click();
  await reactFrame.getByRole("button", { name: "Record review", exact: true }).click();
  await expect(staticCount).toBeVisible();
  await expect(reactCount).toBeVisible();
  await expect(staticCount).toHaveText("Review count: 1");
  await expect(reactCount).toHaveText("Review count: 1");

  await reactCount.evaluate((element) => {
    (element as HTMLElement).style.transform = "translateX(140px)";
  });
  const details = page.getByRole("button", { name: "Details", exact: true });
  if ((await details.getAttribute("aria-expanded")) !== "true") await details.click();
  await expect(page.locator("#studio-panel")).toHaveAttribute("aria-hidden", "false");
  await page.getByRole("button", { name: "Recheck targets", exact: true }).click();
  await expect(page.locator("#coherence-card")).toHaveAttribute("data-status", "mismatch");
  await expect(page.locator("#coherence-summary")).toHaveText("Targets differ at Mobile");
  const mismatchDetail = await page.locator("#coherence-detail").innerText();
  expect(mismatchDetail).toContain("Review count: 1");
  expect(mismatchDetail).toContain("in React");
  await page.getByRole("button", { name: "Review this", exact: true }).click();
  await expect(page.locator("#selection-title")).toHaveText("Review count: 1");
  await expect(page.locator("#selection-detail")).toContainText("Matched across Static + React");
  await expect(page.getByLabel("What should be different?", { exact: true })).toHaveValue(
    `Make this consistent across Static and React. ${mismatchDetail}`,
  );
  const submission = page.waitForResponse(
    (response) => response.request().method() === "POST" && response.url().endsWith("/api/v1/events"),
  );
  await page.getByRole("button", { name: "Send to agent", exact: true }).click();
  const submissionResponse = await submission;
  const submissionPayload = await submissionResponse.json();
  if (submissionPayload.ok !== true) throw new Error(JSON.stringify(submissionPayload));
  await expect(page.locator("#queued")).toHaveText("1");
  const polled = JSON.parse(
    runViewspec(["review-poll", sourcePath, "--state-dir", reviewState, "--timeout-ms", "1", "--json"]),
  );
  const event = polled.batch.events[0];
  expect(event.body).toBe(`Make this consistent across Static and React. ${mismatchDetail}`);
  expect(event.target.target_resolution).toBe("exact");
  expect(event.target.binding_id).toBe("review_label");
  expect(event.target.ir_id).toBe("binding_review_label");
  runViewspec([
    "review-poll",
    sourcePath,
    "--state-dir",
    reviewState,
    "--ack",
    polled.batch.batch_id,
    "--agent-reply",
    "The exact cross-target mismatch is ready for a checked change.",
    "--timeout-ms",
    "1",
    "--json",
  ]);

  await reactCount.evaluate((element) => {
    (element as HTMLElement).style.transform = "";
  });
  await page.getByRole("button", { name: "Recheck targets", exact: true }).click();
  await expect(page.locator("#coherence-card")).toHaveAttribute("data-status", "aligned");
  await expect(page.locator("#coherence-summary")).toHaveText("Static + React align at Mobile");

  await page.locator("#replay-checkpoint").selectOption("review_twice:2");
  await expect(page.locator("#replay-proof")).toContainText("Review count: 2");
  await page.getByRole("button", { name: "Show", exact: true }).click();
  await expect(page.locator("#status")).toHaveText("Replay checkpoint · both targets applied");

  for (const viewport of ["mobile", "tablet", "desktop"] as const) {
    await page.getByLabel("Canvas", { exact: true }).selectOption(viewport);
    await expect(staticCount).toBeVisible();
    await expect(reactCount).toBeVisible();
    await expect(staticCount).toHaveText("Review count: 2");
    await expect(reactCount).toHaveText("Review count: 2");
  }

  await page.locator("#replay-checkpoint").selectOption("review_twice:0");
  await page.getByRole("button", { name: "Show", exact: true }).click();
  await expect(page.locator("#status")).toHaveText("Replay checkpoint · both targets applied");
  await expect(staticCount).toBeHidden();
  await expect(reactCount).toBeHidden();

  expect(failures).toEqual([]);
  const evidence = {
    schema_version: 1,
    status: "passed",
    contract: "state-text-visible-truth-v0",
    source_sha256: createHash("sha256").update(readFileSync(sourcePath)).digest("hex"),
    one_click_expected: "Review count: 1",
    replay_expected: "Review count: 2",
    coherence_negative: {
      injected_target: "binding_review_label",
      detector: mismatchDetail,
      exact_review_binding: event.target.binding_id,
      recovered: true,
    },
    targets: ["html-tailwind-app", "react-tailwind-app"],
    responsive_viewports: [390, 768, 1440],
    runtime_failures: failures,
  };
  const evidencePath = testInfo.outputPath("studio-state-text-evidence.json");
  writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`);
  await testInfo.attach("studio-state-text-evidence", {
    path: evidencePath,
    contentType: "application/json",
  });
});
