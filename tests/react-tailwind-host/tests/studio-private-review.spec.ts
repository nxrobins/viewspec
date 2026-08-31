import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { spawn, spawnSync, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { request as httpsRequest } from "node:https";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const fixtureRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = resolve(fixtureRoot, "../..");
const workspacePython = join(repoRoot, ".venv", "bin", "python");
const python = process.env.PYTHON ?? (existsSync(workspacePython) ? workspacePython : "python");

let workspace = "";
let sourcePath = "";
let origin = "";
let ownerLink = "";
let reviewerLink = "";
let archivePath = "";
let server: ChildProcessWithoutNullStreams | null = null;
let journeyStartedAt = 0;

function runViewspec(args: string[], cwd = repoRoot): string {
  const result = spawnSync(python, ["-m", "viewspec", ...args], {
    cwd,
    encoding: "utf8",
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
    maxBuffer: 4 * 1024 * 1024,
    timeout: 60_000,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(result.stderr || result.stdout);
  return result.stdout;
}

async function availablePort(): Promise<number> {
  return await new Promise((resolvePort, rejectPort) => {
    const candidate = createServer();
    candidate.once("error", rejectPort);
    candidate.listen(0, "127.0.0.1", () => {
      const address = candidate.address();
      if (address === null || typeof address === "string") return rejectPort(new Error("No loopback port."));
      candidate.close((error) => (error ? rejectPort(error) : resolvePort(address.port)));
    });
  });
}

async function readyLine(process: ChildProcessWithoutNullStreams): Promise<{ origin: string; archive: string }> {
  return await new Promise((resolveReady, rejectReady) => {
    const output = createInterface({ input: process.stdout });
    const errors: string[] = [];
    process.stderr.on("data", (chunk) => errors.push(String(chunk)));
    const timeout = setTimeout(() => rejectReady(new Error(`HTTPS review server did not start: ${errors.join("")}`)), 90_000);
    output.once("line", (line) => {
      clearTimeout(timeout);
      try {
        const value = JSON.parse(line);
        resolveReady({ origin: value.origin, archive: value.archive });
      } catch (error) {
        rejectReady(new Error(`Invalid HTTPS review server readiness: ${line}\n${String(error)}`));
      }
    });
    process.once("exit", (code) => {
      clearTimeout(timeout);
      rejectReady(new Error(`HTTPS review server exited (${code}): ${errors.join("")}`));
    });
  });
}

async function createReview(reviewOrigin: string, archivePath: string, requestId = "browser-create-session-0001"): Promise<{ owner: string; reviewer: string }> {
  const body = readFileSync(archivePath);
  return await new Promise((resolveReview, rejectReview) => {
    const target = new URL("/v1/reviews", reviewOrigin);
    const request = httpsRequest(
      target,
      {
        method: "POST",
        rejectUnauthorized: false,
        headers: {
          Authorization: "Bearer e2e-upload",
          "Content-Type": "application/vnd.viewspec.review+zip",
          "Content-Length": String(body.length),
          "Idempotency-Key": requestId,
          "X-ViewSpec-Disclosure-Accepted": "true",
          "X-ViewSpec-Expiry-Seconds": "3600",
        },
      },
      (response) => {
        const chunks: Buffer[] = [];
        response.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
        response.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf8");
          if (response.statusCode !== 201) return rejectReview(new Error(`Create failed (${response.statusCode}): ${text}`));
          const value = JSON.parse(text);
          resolveReview({ owner: value.links.owner, reviewer: value.links.reviewer });
        });
      },
    );
    request.on("error", rejectReview);
    request.end(body);
  });
}

function runtimeFailures(page: Page): string[] {
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const location = message.location();
    failures.push(`console: ${message.text()} @ ${location.url || "unknown"}:${location.lineNumber}`);
  });
  page.on("response", (response) => { if (response.status() >= 400) failures.push(`HTTP ${response.status()}: ${response.url()}`); });
  return failures;
}

test.beforeAll(async () => {
  workspace = mkdtempSync(join(tmpdir(), "viewspec-private-review-browser-"));
  sourcePath = join(workspace, "viewspec.app.json");
  runViewspec(["init-app", "--out", sourcePath, "--template", "react-app"], workspace);
  const port = await availablePort();
  const certificate = join(workspace, "certificate.pem");
  const key = join(workspace, "key.pem");
  const certificateResult = spawnSync(
    "openssl",
    ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", certificate, "-days", "1", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
    { encoding: "utf8", timeout: 30_000 },
  );
  if (certificateResult.status !== 0) throw new Error(certificateResult.stderr);
  server = spawn(
    python,
    [
      join(repoRoot, "tests", "studio_review_https_server.py"),
      "--source", sourcePath,
      "--state-root", join(workspace, "review-state"),
      "--service-root", join(workspace, "service"),
      "--certificate", certificate,
      "--key", key,
      "--port", String(port),
    ],
    { cwd: repoRoot, env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONPATH: join(repoRoot, "src") } },
  );
  const ready = await readyLine(server);
  origin = ready.origin;
  archivePath = ready.archive;
  journeyStartedAt = Date.now();
  const links = await createReview(origin, ready.archive);
  ownerLink = links.owner;
  reviewerLink = links.reviewer;
});

test.afterAll(() => {
  server?.kill("SIGTERM");
  if (workspace) rmSync(workspace, { recursive: true, force: true });
});

test("private replay waits for delayed React commits on initial load and reset", async ({ page }) => {
  const failures = runtimeFailures(page);
  const links = await createReview(origin, archivePath, "browser-delayed-render-0001");
  let rendererContexts = 0;
  let delayedPosts = 0;
  await page.exposeBinding("__viewspecTestRenderer", () => { rendererContexts++; });
  await page.exposeBinding("__viewspecTestDelayedPost", () => { delayedPosts++; });
  await page.addInitScript(() => {
    if (window.top === window || !location.pathname.includes("/artifact/react/")) return;
    const observed = window as Window & {
      __viewspecTestRenderer: () => Promise<void>;
      __viewspecTestDelayedPost: () => Promise<void>;
    };
    void observed.__viewspecTestRenderer();
    const NativeMessageChannel = window.MessageChannel;
    window.MessageChannel = class extends NativeMessageChannel {
      constructor() {
        super();
        const post = this.port2.postMessage.bind(this.port2);
        this.port2.postMessage = (...args: Parameters<typeof post>) => {
          void observed.__viewspecTestDelayedPost();
          setTimeout(() => post(...args), 250);
        };
      }
    };
  });
  await page.goto(links.reviewer, { waitUntil: "domcontentloaded" });
  await expect(page.locator("#surface-status")).toHaveText("Checked target pair ready");
  const source = JSON.parse(readFileSync(sourcePath, "utf8"));
  const secondRoute = source.routes.find((item: { path: string }) => item.path !== source.app.root_route);
  await page.locator(".routes").getByRole("button", { name: secondRoute.id, exact: true }).click();
  for (const title of ["Static product", "React product"]) {
    await expect(page.frameLocator(`iframe[title="${title}"]`).locator('[data-viewspec-app-screen]:not([hidden])')).toHaveAttribute("data-route-path", secondRoute.path);
  }
  const values = await page.locator('[name="replay"] option').evaluateAll(options => options.map(item => (item as HTMLOptionElement).value));
  expect(values.length).toBeGreaterThan(0);
  await page.locator('[name="replay"]').selectOption(values.at(-1)!);
  await expect(page.locator("#show-replay")).toBeEnabled();
  await page.locator("#show-replay").click();
  await expect(page.locator("#replay-status")).toContainText("Checkpoint active on both targets");
  for (const title of ["Static product", "React product"]) {
    await expect(page.frameLocator(`iframe[title="${title}"]`).locator('[data-binding-id="inc_1043_status"]')).toBeVisible();
  }
  await expect(page.frameLocator('iframe[title="React product"]').locator('[data-binding-id="inc_1043_status"]')).toHaveText("investigating");
  expect(rendererContexts).toBe(2);
  expect(delayedPosts).toBeGreaterThanOrEqual(2);
  expect(failures).toEqual([]);
});

test("a failed render remains unavailable even if React commits after the deadline", async ({ page }) => {
  const failures = runtimeFailures(page);
  const links = await createReview(origin, archivePath, "browser-render-deadline-0001");
  await page.addInitScript(() => {
    if (window.top === window || !location.pathname.includes("/artifact/react/")) return;
    const pending: (() => void)[] = [];
    let released = false;
    (window as Window & { __viewspecTestRelease?: () => void }).__viewspecTestRelease = () => {
      released = true;
      pending.splice(0).forEach(post => post());
    };
    const NativeMessageChannel = window.MessageChannel;
    window.MessageChannel = class extends NativeMessageChannel {
      constructor() {
        super();
        const post = this.port2.postMessage.bind(this.port2);
        this.port2.postMessage = (...args: Parameters<typeof post>) => {
          if (released) post(...args);
          else pending.push(() => post(...args));
        };
      }
    };
  });
  await page.goto(links.reviewer, { waitUntil: "domcontentloaded" });
  await expect(page.locator("#surface-status")).toHaveText("Checked target unavailable · reload this review to retry");
  await expect(page.locator("#show-replay")).toBeDisabled();
  const react = page.frameLocator('iframe[title="React product"]');
  await react.locator("html").evaluate(() => {
    (window as Window & { __viewspecTestRelease?: () => void }).__viewspecTestRelease?.();
  });
  await expect(react.locator('[data-viewspec-app-screen]:not([hidden])')).toHaveCount(1);
  const values = await page.locator('[name="replay"] option').evaluateAll(options => options.map(item => (item as HTMLOptionElement).value));
  await page.locator('[name="replay"]').selectOption(values.at(-1)!);
  await expect(page.locator("#show-replay")).toBeDisabled();
  await expect(page.locator("#surface-status")).toHaveText("Checked target unavailable · reload this review to retry");
  expect(failures).toEqual([]);
});

test("private HTTPS review carries exact targets through comment and owner approval", async ({ browser, page }, testInfo) => {
  const failures = runtimeFailures(page);
  const externalRequests: string[] = [];
  const requestPaths: string[] = [];
  page.on("request", (request) => {
    const target = new URL(request.url());
    requestPaths.push(target.pathname);
    if (target.origin !== origin) externalRequests.push(request.url());
  });
  await page.goto(reviewerLink, { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(new RegExp(`${origin.replaceAll(".", "\\.")}/review/vsr_[A-Za-z0-9_-]{24}/$`));
  await expect(page.locator("#surface-status")).toHaveText("Checked target pair ready");
  await expect(page.locator("iframe")).toHaveCount(2);
  await expect(page.frameLocator('iframe[title="Static product"]').locator("body")).not.toBeEmpty();
  await expect(page.frameLocator('iframe[title="React product"]').locator("body")).not.toBeEmpty();

  await page.getByRole("button", { name: "Tablet" }).click();
  for (const title of ["Static product", "React product"]) {
    await expect.poll(async () => await page.frameLocator(`iframe[title="${title}"]`).locator("html").evaluate((node) => node.clientWidth)).toBe(768);
  }

  const source = JSON.parse(readFileSync(sourcePath, "utf8"));
  const rootRoute = source.routes.find((item: { path: string }) => item.path === source.app.root_route);
  const rootScreen = source.screens.find((item: { id: string }) => item.id === rootRoute.screen_id);
  const secondRoute = source.routes.find((item: { path: string }) => item.path !== source.app.root_route);
  const secondScreen = source.screens.find((item: { id: string }) => item.id === secondRoute.screen_id);
  await page.frameLocator('iframe[title="React product"]').getByRole("button", { name: secondRoute.label, exact: true }).click();
  for (const title of ["Static product", "React product"]) {
    await expect(page.frameLocator(`iframe[title="${title}"]`).getByRole("heading", { name: secondScreen.title, exact: true })).toBeVisible();
  }
  await page.frameLocator('iframe[title="Static product"]').getByRole("button", { name: rootRoute.label, exact: true }).click();
  for (const title of ["Static product", "React product"]) {
    await expect(page.frameLocator(`iframe[title="${title}"]`).getByRole("heading", { name: rootScreen.title, exact: true })).toBeVisible();
  }

  if (await page.locator("#replay-tools").isVisible()) {
    const replayValues = await page.locator('[name="replay"] option').evaluateAll((options) => options.map((item) => (item as HTMLOptionElement).value));
    await page.locator('[name="replay"]').selectOption(replayValues.at(-1));
    if (await page.locator("#show-replay").isEnabled()) {
      await page.locator("#show-replay").click();
      await expect(page.locator("#replay-status")).toContainText("Checkpoint active on both targets");
    }
  }

  const bindingId = rootScreen.intent_bundle.view_spec.bindings[0].id;
  await page.getByRole("button", { name: "Comment on product" }).click();
  await page.frameLocator('iframe[title="Static product"]').locator(`[data-viewspec-app-screen]:not([hidden]) [data-binding-id="${bindingId}"]`).click();
  await expect(page.locator("#selection")).toContainText(bindingId);
  await expect(page.locator("#resource")).toContainText("incidents");
  await page.getByPlaceholder("Point at the outcome you want changed.").fill("Make this outcome easier to scan.");
  await page.getByRole("button", { name: "Send comment" }).click();
  await expect(page.locator("#result")).toContainText("acknowledged");

  const ownerContext: BrowserContext = await browser.newContext({ ignoreHTTPSErrors: true });
  const ownerPage = await ownerContext.newPage();
  const ownerFailures = runtimeFailures(ownerPage);
  await ownerPage.addInitScript(() => {
    (window as Window & { __cspViolations?: unknown[] }).__cspViolations = [];
    document.addEventListener("securitypolicyviolation", (event) => {
      (window as Window & { __cspViolations?: unknown[] }).__cspViolations?.push({
        blockedURI: event.blockedURI,
        directive: event.violatedDirective,
        sample: event.sample,
        sourceFile: event.sourceFile,
      });
    });
  });
  ownerPage.on("request", (request) => {
    const target = new URL(request.url());
    requestPaths.push(target.pathname);
    if (target.origin !== origin) externalRequests.push(request.url());
  });
  await ownerPage.goto(ownerLink, { waitUntil: "domcontentloaded" });
  await expect(ownerPage).toHaveURL(/\/review\/vsr_[A-Za-z0-9_-]{24}\/$/);
  await expect(ownerPage.locator("#surface-status")).toHaveText("Checked target pair ready");
  await expect(ownerPage.locator("#comments")).toContainText("Make this outcome easier to scan.");
  const ownerRevision = await ownerPage.evaluate(async () => await (await fetch("./revision")).json());
  expect(ownerRevision.comments[0].context.evidence_refs).toContain("studio-inspection/resources/incidents/inc_1042/id");
  expect(ownerRevision.comments[0].context.evidence_refs.some((item: string) => item.startsWith("studio-inspection/replays/"))).toBe(true);
  await ownerPage.getByRole("button", { name: "Approve this exact revision" }).click();
  await expect(ownerPage.locator("#result")).toContainText("Revision approved.");
  const approvedRevision = await ownerPage.evaluate(async () => await (await fetch("./revision")).json());
  const ownerViolations = await ownerPage.evaluate(
    () => (window as Window & { __cspViolations?: unknown[] }).__cspViolations || [],
  );
  const journeyElapsedMs = Date.now() - journeyStartedAt;
  expect(journeyElapsedMs).toBeLessThan(5 * 60 * 1_000);
  const evidence = {
    schema_version: 1,
    kind: "studio_private_review_browser_journey",
    status: "passed",
    browser: testInfo.project.name,
    threshold_ms: 5 * 60 * 1_000,
    elapsed_ms: journeyElapsedMs,
    revision_identity_sha256: approvedRevision.session.revision_identity_sha256,
    comment_id: approvedRevision.comments[0].id,
    comment_evidence_refs: approvedRevision.comments[0].context.evidence_refs,
    approval_id: approvedRevision.approval.id,
    approval_receipt_id: approvedRevision.approval.receipt.receipt_id,
    request_paths: [...new Set(requestPaths)].sort(),
    external_request_count: externalRequests.length,
    capability_leak_detected: requestPaths.some((path) => path.includes("vsc_") || path.includes("cap=")),
    csp_violation_count: ownerViolations.length,
    console_error_count: failures.length + ownerFailures.length,
  };
  writeFileSync(testInfo.outputPath("studio-private-review-evidence.json"), `${JSON.stringify(evidence, null, 2)}\n`);
  await testInfo.attach("studio-private-review-evidence", {
    body: Buffer.from(JSON.stringify(evidence)),
    contentType: "application/json",
  });
  expect(externalRequests).toEqual([]);
  expect(evidence.capability_leak_detected).toBe(false);
  expect(ownerViolations).toEqual([]);
  expect(failures).toEqual([]);
  expect(ownerFailures).toEqual([]);
  await ownerContext.close();
});
