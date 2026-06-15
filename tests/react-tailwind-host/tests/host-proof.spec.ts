import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

type ManifestNode = {
  app_role?: string;
  ir_id: string;
  primitive: string;
  props?: Record<string, unknown>;
};

const fixtureRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const manifest = JSON.parse(readFileSync(join(fixtureRoot, "src/generated/provenance_manifest.json"), "utf8")) as {
  nodes: Record<string, ManifestNode>;
};

function fail(code: string, message: string): never {
  throw new Error(`${code}: ${message}`);
}

function domIdForAppRole(role: string): string {
  const match = Object.entries(manifest.nodes).find(([, node]) => node.app_role === role);
  if (!match) fail("HOST_PROOF_TAILWIND_STYLE_MISSING", `missing app_role ${role}`);
  return match[0];
}

function domIdForProp(prop: string, value: string): string {
  const match = Object.entries(manifest.nodes).find(([, node]) => node.props?.[prop] === value);
  if (!match) fail("HOST_PROOF_TAILWIND_STYLE_MISSING", `missing prop ${prop}=${value}`);
  return match[0];
}

async function expectComputed(page: Page, domId: string, property: string, expected: string | RegExp) {
  if (!domId || !property || !(typeof expected === "string" || expected instanceof RegExp)) {
    fail("HOST_PROOF_STYLE_ASSERTION_TOO_WEAK", "computed style assertions require dom id, property, and exact or regex expected value");
  }
  const value = await page.locator(`#${domId}`).evaluate((element, prop) => getComputedStyle(element).getPropertyValue(prop), property);
  const ok = typeof expected === "string" ? value === expected : expected.test(value);
  if (!ok) fail("HOST_PROOF_TAILWIND_STYLE_MISSING", `${domId} ${property} expected ${expected.toString()} got ${value}`);
}

test("generated React Tailwind artifact builds and behaves in a host app", async ({ page }) => {
  const runtimeErrors: string[] = [];
  page.on("pageerror", (error) => runtimeErrors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") runtimeErrors.push(`console: ${message.text()}`);
  });

  await page.goto("/");
  expect(runtimeErrors, "HOST_PROOF_BROWSER_RUNTIME_ERROR").toEqual([]);
  await expect(page.getByText("Review requests")).toBeVisible();
  await expect(page.getByText("Open", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Apply filters" })).toBeVisible();

  const roleIds = {
    appShell: domIdForAppRole("app_shell"),
    contentGrid: domIdForAppRole("content_grid"),
    filterBar: domIdForAppRole("filter_bar"),
    metricCard: domIdForAppRole("metric_card"),
  };
  const searchInput = domIdForProp("binding_id", "search_value");
  const applyButton = domIdForProp("action_id", "apply_filters");
  await expect(page.locator(`#${roleIds.contentGrid}`)).toHaveAttribute("data-ir-id", manifest.nodes[roleIds.contentGrid].ir_id);
  await expect(page.locator(`#${roleIds.appShell}`)).toHaveAttribute("data-aesthetic-profile", "aesthetic.data_dense");

  const styleAssertions: Array<[string, string, string | RegExp]> = [
    [roleIds.appShell, "background-color", /^(rgb\(226, 232, 240\)|oklch\(0\.929 0\.013 255\.50[78]\))$/],
    [roleIds.appShell, "padding-left", "20px"],
    [roleIds.contentGrid, "display", "grid"],
    [roleIds.contentGrid, "column-gap", "20px"],
    [roleIds.filterBar, "display", "flex"],
    [roleIds.filterBar, "column-gap", "12px"],
    [roleIds.filterBar, "border-top-width", "1px"],
    [roleIds.metricCard, "min-height", "80px"],
    [searchInput, "border-top-width", "1px"],
    [applyButton, "background-color", /^(rgb\(29, 78, 216\)|oklch\(0\.488 0\.243 264\.376\))$/],
  ];
  if (styleAssertions.length < 8) fail("HOST_PROOF_STYLE_ASSERTION_TOO_WEAK", "at least 8 computed style assertions are required");
  for (const [domId, property, expected] of styleAssertions) {
    await expectComputed(page, domId, property, expected);
  }

  await page.locator('[data-binding-id="search_value"]').fill("blocked only");
  await page.locator('[data-binding-id="owner_value"]').fill("Ada");
  await page.getByRole("button", { name: "Apply filters" }).click();
  await expect.poll(() => page.evaluate(() => window.__viewspecActions?.length ?? 0)).toBe(1);
  const actions = await page.evaluate(() => window.__viewspecActions ?? []);
  if (actions.length !== 1) fail("HOST_PROOF_ACTION_COUNT_MISMATCH", `expected 1 action, got ${actions.length}`);
  expect(actions[0]).toEqual({
    schemaVersion: 1,
    source: "viewspec-react-tailwind-tsx",
    id: "apply_filters",
    kind: "submit",
    targetRef: "motif:queue_filters",
    payloadBindings: ["search_value", "owner_value"],
    payloadValues: {
      owner_value: "Ada",
      search_value: "blocked only",
    },
  });
  expect(runtimeErrors, "HOST_PROOF_BROWSER_RUNTIME_ERROR").toEqual([]);
});
