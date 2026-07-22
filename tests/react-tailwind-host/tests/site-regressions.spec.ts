import { expect, test, type Page } from "@playwright/test";

const publicPages = [
  "/",
  "/cross-platform-dashboard/",
  "/appbundle-state-ir/",
  "/custom-motifs/",
  "/interactive-compose/",
  "/stateful-collections/",
  "/motif-switcher/",
  "/provenance-inspector/",
  "/live-builder/",
  "/invariants/",
  "/proof-bundle/",
  "/fifteen-lines/",
  "/style-derivation/",
  "/style-range/",
] as const;

const viewports = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844 },
] as const;

function captureRuntimeFailures(page: Page): string[] {
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") failures.push(`console: ${message.text()}`);
  });
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.origin === "http://127.0.0.1:4178" && response.status() >= 400) {
      failures.push(`HTTP ${response.status()}: ${url.pathname}`);
    }
  });
  return failures;
}

test("public demos are runtime-clean and viewport-bounded", async ({ page }) => {
  const failures = captureRuntimeFailures(page);

  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    for (const path of publicPages) {
      failures.length = 0;
      const response = await page.goto(path, { waitUntil: "domcontentloaded" });
      expect(response?.status(), `${path} should load`).toBe(200);
      await page.waitForTimeout(150);
      expect(await page.title(), `${path} should have a title`).toMatch(/^ViewSpec/);
      await expect(page.locator("main").first(), `${path} should have main content`).toBeVisible();

      const overflow = await page.evaluate(() => ({
        clientWidth: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
      }));
      expect(
        overflow.scrollWidth,
        `${path} overflows horizontally at ${viewport.name}`,
      ).toBeLessThanOrEqual(overflow.clientWidth + 1);

      const unnamedControls = await page.locator("button, input, select, textarea").evaluateAll((controls) =>
        controls
          .filter((control) => {
            const element = control as HTMLElement;
            const labelledBy = element.getAttribute("aria-labelledby");
            const labelledText = labelledBy
              ? labelledBy
                  .split(/\s+/)
                  .map((id) => document.getElementById(id)?.textContent || "")
                  .join(" ")
              : "";
            const explicitLabel = element.id
              ? document.querySelector(`label[for="${CSS.escape(element.id)}"]`)?.textContent || ""
              : "";
            const wrappingLabel = element.closest("label")?.textContent || "";
            return !(
              element.getAttribute("aria-label")?.trim() ||
              labelledText.trim() ||
              explicitLabel.trim() ||
              wrappingLabel.trim() ||
              element.textContent?.trim() ||
              element.getAttribute("title")?.trim()
            );
          })
          .map((control) => (control as HTMLElement).outerHTML.slice(0, 180)),
      );
      expect(unnamedControls, `${path} has unnamed controls`).toEqual([]);

      const invalidTabs = await page.locator('[role="tab"]').evaluateAll((tabs) =>
        tabs
          .filter((tab) => {
            const controls = tab.getAttribute("aria-controls");
            return (
              !tab.closest('[role="tablist"]') ||
              !["true", "false"].includes(tab.getAttribute("aria-selected") || "") ||
              !controls ||
              document.getElementById(controls)?.getAttribute("role") !== "tabpanel"
            );
          })
          .map((tab) => (tab as HTMLElement).outerHTML.slice(0, 180)),
      );
      expect(invalidTabs, `${path} has invalid tab semantics`).toEqual([]);
      expect(failures, `${path} emitted runtime failures`).toEqual([]);
    }
  }
});

test("copy controls fall back when the Clipboard API rejects", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: () => Promise.reject(new Error("permission denied")) },
    });
    document.execCommand = (command: string) => command === "copy";
  });
  const failures = captureRuntimeFailures(page);
  await page.goto("/", { waitUntil: "domcontentloaded" });

  const copyButtons = page.locator("[data-copy-text]");
  expect(await copyButtons.count()).toBeGreaterThan(0);
  for (let index = 0; index < (await copyButtons.count()); index += 1) {
    const button = copyButtons.nth(index);
    await button.click();
    await expect(button).toHaveAttribute("data-copy-state", "success");
    await expect(button).toContainText(/copied/i);
  }
  expect(failures).toEqual([]);
});

test("checkout key copy falls back without leaking a runtime rejection", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: () => Promise.reject(new Error("permission denied")) },
    });
    document.execCommand = (command: string) => command === "copy";
  });
  await page.route("https://api.viewspec.dev/v1/checkout/claim", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ api_key: "vsk_test_browser_only" }),
    });
  });
  const failures = captureRuntimeFailures(page);
  await page.goto("/checkout/success/?session_id=cs_test_browser", { waitUntil: "domcontentloaded" });
  const copyButton = page.locator("#copy-key");
  await expect(copyButton).toBeVisible();
  await copyButton.click();
  await expect(copyButton).toHaveAttribute("data-copy-state", "success");
  await expect(copyButton).toHaveText("Copied");
  expect(failures).toEqual([]);
});

test("proof bundle exposes complete bounded proof content", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/proof-bundle/", { waitUntil: "domcontentloaded" });
  const proof = page.locator('.proof-panel[aria-label="Example proof summary"]');
  await expect(proof).toBeVisible();
  await expect(proof).toContainText("Status: PASSED");
  await expect(proof).toContainText("proof_summary: written");
  const bounds = await proof.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return { left: rect.left, right: rect.right, viewport: document.documentElement.clientWidth };
  });
  expect(bounds.left).toBeGreaterThanOrEqual(0);
  expect(bounds.right).toBeLessThanOrEqual(bounds.viewport + 1);
  expect(failures).toEqual([]);
});

test("homepage exposes bounded opt-in Freerange and Pretext proof integrations", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/", { waitUntil: "domcontentloaded" });

  const proof = page.locator("#proof");
  const freerange = proof.locator('[data-proof-integration="freerange"]');
  const pretext = proof.locator('[data-proof-integration="pretext"]');
  await expect(freerange).toBeVisible();
  await expect(freerange).toContainText("@chenglou/freerange@0.0.1");
  await expect(freerange).toContainText("user-installed stable Bun 1.x");
  await expect(freerange).toContainText("ViewSpec never installs Bun");
  await expect(freerange.getByRole("link", { name: "Freerange" })).toHaveAttribute(
    "href",
    "https://github.com/chenglou/freerange",
  );

  await expect(pretext).toBeVisible();
  await expect(pretext).toContainText("@chenglou/pretext@0.0.8");
  await expect(pretext).toContainText("preserves native DOM semantics");
  await expect(pretext).toContainText("390×844, 768×1024, and 1440×1000");
  await expect(pretext).toContainText("No Bun required");
  await expect(pretext.getByRole("link", { name: "Pretext" })).toHaveAttribute(
    "href",
    "https://github.com/chenglou/pretext",
  );

  const composition = proof.locator(".integration-proof-compose");
  await expect(composition).toContainText("--freerange --pretext --json");
  await expect(composition).toContainText("does not broaden either guarantee");
  await expect(composition).toContainText("does not claim that a hosted run has completed");
  expect(failures).toEqual([]);
});

test("provenance hover remains populated across sibling transitions", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const receipts = page.locator("#under .receipts [data-node]");
  expect(await receipts.count()).toBeGreaterThanOrEqual(2);
  await receipts.nth(0).hover();
  await expect(page.locator("#traceOut")).toContainText("receipt_intent_bundle");
  await receipts.nth(1).hover();
  await expect(page.locator("#traceOut")).toContainText("receipt_provenance_manifest");
  await expect(page.locator("#traceOut")).not.toContainText("Click, tap, hover, or focus");
  expect(failures).toEqual([]);
});

test("pricing conversion links match the public commercial contract", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  await page.goto("/", { waitUntil: "domcontentloaded" });
  const pricing = page.locator("#pricing");
  await expect(pricing.getByText("$149", { exact: false })).toBeVisible();
  await expect(pricing.getByText("10,000 hosted compile calls/day", { exact: true })).toBeVisible();
  await expect(pricing.getByRole("link", { name: "Get Pro" })).toHaveAttribute(
    "href",
    "https://buy.stripe.com/6oU4gA6PqcM9afq6gq2ZO00",
  );
  await expect(pricing.getByRole("link", { name: "Talk to us" })).toHaveAttribute(
    "href",
    "mailto:hello@viewspec.dev?subject=ViewSpec%20Enterprise",
  );
  await expect(pricing.getByRole("link", { name: /Try the one-minute proof/ })).toHaveAttribute(
    "href",
    "./proof-bundle/",
  );
  expect(failures).toEqual([]);
});

test("provenance inspector locks a verified semantic chain", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  await page.goto("/provenance-inspector/", { waitUntil: "domcontentloaded" });
  const artifactNode = page.locator("#artifact [data-ir-id]").first();
  await expect(artifactNode).toBeVisible();
  await artifactNode.click();
  await expect(page.locator("#lock-state-text")).toHaveText("Locked");
  await expect(page.locator("#inspector-body")).toContainText("Provenance verified:");
  expect(failures).toEqual([]);
});
