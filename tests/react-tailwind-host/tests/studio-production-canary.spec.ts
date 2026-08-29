import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { readFileSync, writeFileSync } from "node:fs";

const reviewOrigin = "https://review.viewspec.dev";
const apiOrigin = "https://api.viewspec.dev";
const archivePath = process.env.VIEWSPEC_CANARY_ARCHIVE || "";
const apiKey = process.env.VIEWSPEC_CANARY_API_KEY || "";
const runId = process.env.VIEWSPEC_CANARY_RUN_ID || "";
const evidencePath = process.env.VIEWSPEC_CANARY_BROWSER_EVIDENCE || "";
const privateStatePath = process.env.VIEWSPEC_CANARY_BROWSER_PRIVATE_STATE || "";

function requiredInputs(): void {
  if (!archivePath || !apiKey || !/^vsrcan_[0-9a-f]{32}$/.test(runId) || !evidencePath || !privateStatePath) {
    throw new Error("Production browser canary inputs are incomplete.");
  }
}

function observe(page: Page, external: string[], failures: string[], csp: unknown[]): void {
  page.on("request", (request) => {
    const target = new URL(request.url());
    if (target.origin !== reviewOrigin) external.push(request.url());
  });
  page.on("pageerror", (error) => failures.push(`pageerror:${error.name}`));
  page.on("console", (message) => {
    if (message.type() === "error") failures.push(`console:${message.type()}`);
  });
  page.on("domcontentloaded", async () => {
    const violations = await page.evaluate(() =>
      (window as Window & { __viewspecCanaryCsp?: unknown[] }).__viewspecCanaryCsp || [],
    );
    csp.push(...violations);
  });
}

async function addCspObserver(context: BrowserContext): Promise<void> {
  await context.addInitScript(() => {
    const target = window as Window & { __viewspecCanaryCsp?: unknown[] };
    target.__viewspecCanaryCsp = [];
    document.addEventListener("securitypolicyviolation", (event) => {
      target.__viewspecCanaryCsp?.push({
        directive: event.violatedDirective,
        blocked: event.blockedURI ? "present" : "absent",
      });
    });
  });
}

async function createReview(engine: string): Promise<Record<string, unknown>> {
  const response = await fetch(`${apiOrigin}/v1/reviews`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/vnd.viewspec.review+zip",
      "X-ViewSpec-Disclosure-Accepted": "true",
      "X-ViewSpec-Expiry-Seconds": "3600",
      "Idempotency-Key": `${runId}-${engine}-create`,
    },
    body: readFileSync(archivePath),
  });
  const body = await response.json();
  if (response.status !== 201 || !body || typeof body !== "object") {
    throw new Error(`Production review create failed with status ${response.status}.`);
  }
  return body as Record<string, unknown>;
}

async function assertTargets(page: Page): Promise<{ staticPassed: boolean; reactPassed: boolean }> {
  await expect(page.locator("#surface-status")).toHaveText("Checked target pair ready");
  const staticBody = page.frameLocator('iframe[title="Static product"]').locator("body");
  const reactBody = page.frameLocator('iframe[title="React product"]').locator("body");
  await expect(staticBody).not.toBeEmpty();
  await expect(reactBody).not.toBeEmpty();
  await page.getByRole("button", { name: "Tablet" }).click();
  for (const title of ["Static product", "React product"]) {
    await expect
      .poll(async () => page.frameLocator(`iframe[title="${title}"]`).locator("html").evaluate((node) => node.clientWidth))
      .toBe(768);
  }
  return { staticPassed: true, reactPassed: true };
}

async function hardenedCookie(context: BrowserContext, sessionPath: string): Promise<boolean> {
  const cookies = await context.cookies(reviewOrigin);
  const cookie = cookies.find((item) => item.name === "viewspec_review" && item.path === sessionPath);
  return Boolean(cookie?.secure && cookie?.httpOnly && cookie?.sameSite === "Strict");
}

async function completeJourney(browser: Browser, engine: string): Promise<Record<string, unknown>> {
  const started = Date.now();
  const created = await createReview(engine);
  const links = created.links as Record<string, string>;
  const ownerLink = links.owner;
  const reviewerLink = links.reviewer;
  if (!ownerLink?.startsWith(`${reviewOrigin}/review/`) || !reviewerLink?.startsWith(`${reviewOrigin}/review/`)) {
    throw new Error("Production review links do not bind the canonical origin.");
  }
  const sessionPath = new URL(ownerLink).pathname;
  const anonymous = await browser.newContext();
  const anonymousRevision = await anonymous.request.get(`${reviewOrigin}${sessionPath}revision`);
  const anonymousDenied = anonymousRevision.status() === 404;
  await anonymous.close();

  const external: string[] = [];
  const failures: string[] = [];
  const csp: unknown[] = [];
  const reviewer = await browser.newContext();
  await addCspObserver(reviewer);
  const reviewerPage = await reviewer.newPage();
  observe(reviewerPage, external, failures, csp);
  await reviewerPage.goto(reviewerLink, { waitUntil: "domcontentloaded" });
  await expect(reviewerPage).toHaveURL(new RegExp(`${reviewOrigin}/review/vsr_[A-Za-z0-9_-]{24}/$`));
  const reviewerFragmentRemoved = new URL(reviewerPage.url()).hash === "";
  const reviewerCookieHardened = await hardenedCookie(reviewer, sessionPath);
  const targets = await assertTargets(reviewerPage);
  const revision = await reviewerPage.evaluate(async () => await (await fetch("./revision")).json());
  await reviewerPage.getByRole("button", { name: "Comment on product" }).click();
  const targetOptions = await reviewerPage.locator('[name="target"] option').count();
  if (targetOptions < 1) throw new Error("Production review exposes no semantic target.");
  await reviewerPage.locator('[name="target"]').selectOption({ index: 0 });
  await reviewerPage.getByPlaceholder("Point at the outcome you want changed.").fill("Make this outcome easier to scan.");
  await reviewerPage.getByRole("button", { name: "Send comment" }).click();
  await expect(reviewerPage.locator("#result")).toContainText("acknowledged");
  const reviewerApproval = await reviewerPage.evaluate(async (identity) => {
    const response = await fetch("./approval", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": `${crypto.randomUUID()}-reviewer` },
      body: JSON.stringify({ revision_identity_sha256: identity }),
    });
    return response.status;
  }, revision.session.revision_identity_sha256);

  const owner = await browser.newContext();
  await addCspObserver(owner);
  const ownerPage = await owner.newPage();
  observe(ownerPage, external, failures, csp);
  await ownerPage.goto(ownerLink, { waitUntil: "domcontentloaded" });
  await expect(ownerPage).toHaveURL(new RegExp(`${reviewOrigin}/review/vsr_[A-Za-z0-9_-]{24}/$`));
  const ownerFragmentRemoved = new URL(ownerPage.url()).hash === "";
  const ownerCookieHardened = await hardenedCookie(owner, sessionPath);
  await assertTargets(ownerPage);
  await expect(ownerPage.locator("#comments")).toContainText("Make this outcome easier to scan.");
  await ownerPage.getByRole("button", { name: "Approve this exact revision" }).click();
  await expect(ownerPage.locator("#result")).toContainText("Revision approved.");
  const approved = await ownerPage.evaluate(async () => await (await fetch("./revision")).json());
  const ownerCsp = await ownerPage.evaluate(() =>
    (window as Window & { __viewspecCanaryCsp?: unknown[] }).__viewspecCanaryCsp || [],
  );
  const reviewerCsp = await reviewerPage.evaluate(() =>
    (window as Window & { __viewspecCanaryCsp?: unknown[] }).__viewspecCanaryCsp || [],
  );
  csp.push(...ownerCsp, ...reviewerCsp);
  const ownerCookies = await owner.cookies(reviewOrigin);
  const privateState = {
    schema_version: 1,
    session_path: sessionPath,
    owner_cookie: ownerCookies.find((item) => item.name === "viewspec_review")?.value || "",
    approval_receipt: approved.approval?.receipt || null,
  };
  writeFileSync(privateStatePath, `${JSON.stringify(privateState, null, 2)}\n`, { mode: 0o600 });
  await owner.close();
  await reviewer.close();
  return {
    schema_version: 1,
    kind: "studio_review_production_browser_receipt",
    engine,
    elapsed_ms: Date.now() - started,
    create_passed: true,
    anonymous_access_denied: anonymousDenied,
    fragment_removed: ownerFragmentRemoved && reviewerFragmentRemoved,
    secure_http_only_same_site_cookie: ownerCookieHardened && reviewerCookieHardened,
    static_target_passed: targets.staticPassed,
    react_target_passed: targets.reactPassed,
    semantic_comment_acknowledged: approved.comments?.length === 1,
    reviewer_approval_denied: reviewerApproval === 403,
    owner_approval_passed: approved.approval?.status === "approved",
    approval_receipt_present: typeof approved.approval?.receipt?.receipt_id === "string",
    external_request_count: external.length,
    capability_leak_count: external.filter((url) => url.includes("cap=") || url.includes("vsc_")).length,
    csp_violation_count: csp.length,
    console_error_count: failures.length,
  };
}

test("production private review completes one exact owner/reviewer journey", async ({ browser }, testInfo) => {
  requiredInputs();
  const evidence = await completeJourney(browser, testInfo.project.name);
  writeFileSync(evidencePath, `${JSON.stringify(evidence, null, 2)}\n`, { mode: 0o600 });
  expect(Number(evidence.elapsed_ms)).toBeLessThan(5 * 60 * 1000);
  expect(evidence).toMatchObject({
    create_passed: true,
    anonymous_access_denied: true,
    fragment_removed: true,
    secure_http_only_same_site_cookie: true,
    static_target_passed: true,
    react_target_passed: true,
    semantic_comment_acknowledged: true,
    reviewer_approval_denied: true,
    owner_approval_passed: true,
    approval_receipt_present: true,
    external_request_count: 0,
    capability_leak_count: 0,
    csp_violation_count: 0,
    console_error_count: 0,
  });
});
