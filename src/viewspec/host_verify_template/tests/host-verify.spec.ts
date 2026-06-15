import { expect, test, type Page } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

type ManifestNode = {
  classes?: string[];
  ir_id: string;
  primitive: string;
  props?: Record<string, unknown>;
};

type ActionIntent = {
  schemaVersion: number;
  source: string;
  id: string;
  kind: string;
  targetRef: string;
  payloadBindings: string[];
  payloadValues: Record<string, unknown>;
};

type ActionWindow = Window & {
  __viewspecActions?: ActionIntent[];
};

const fixtureRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const manifest = JSON.parse(readFileSync(join(fixtureRoot, "src/generated/provenance_manifest.json"), "utf8")) as {
  nodes: Record<string, ManifestNode>;
};

function fail(code: string, message: string): never {
  throw new Error(`${code}: ${message}`);
}

function cssId(id: string): string {
  return `#${id}`;
}

function expectedText(node: ManifestNode): string | null {
  const props = node.props ?? {};
  for (const key of ["text", "label", "value"]) {
    const value = props[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return null;
}

function actionNodes(): Array<[string, ManifestNode]> {
  return Object.entries(manifest.nodes).filter(([, node]) => {
    const props = node.props ?? {};
    return node.primitive === "button" && typeof props.action_id === "string" && props.action_id;
  });
}

type StyleAssertion = {
  domId: string;
  property: string;
  expected?: string | RegExp;
  expectedColumnCount?: number;
  expectedSpanCount?: number;
  aestheticLayout?: boolean;
};

const colorByClass: Record<string, RegExp> = {
  "bg-white": /^(rgb\(255, 255, 255\)|oklch\(1 0 0\))$/,
  "bg-slate-50": /^(rgb\(248, 250, 252\)|oklch\(0\.984 0\.003 247\.858\))$/,
  "bg-slate-200": /^(rgb\(226, 232, 240\)|oklch\(0\.929 0\.013 255\.50[78]\))$/,
  "bg-red-50": /^(rgb\(254, 242, 242\)|oklch\(0\.971 0\.013 17\.38\))$/,
  "bg-teal-100": /^(rgb\(204, 251, 241\)|oklch\(0\.953 0\.051 180\.801\))$/,
  "bg-teal-700": /^(rgb\(15, 118, 110\)|oklch\(0\.511 0\.096 186\.391\))$/,
};

const gapByClass: Record<string, string> = {
  "gap-0": "0px",
  "gap-1": "4px",
  "gap-2": "8px",
  "gap-3": "12px",
  "gap-4": "16px",
  "gap-5": "20px",
  "gap-6": "24px",
};

const gridBreakpoints: Record<string, number> = {
  base: 0,
  sm: 640,
  md: 768,
  lg: 1024,
  xl: 1280,
  "2xl": 1536,
};

function expectedGridColumnCount(classes: string[], viewportWidth: number): number | null {
  let expected: { minWidth: number; count: number } | null = null;
  for (const token of classes) {
    const match = token.match(/^(?:(sm|md|lg|xl|2xl):)?grid-cols-([1-6])$/);
    if (!match) continue;
    const minWidth = gridBreakpoints[match[1] ?? "base"];
    if (minWidth > viewportWidth) continue;
    const count = Number.parseInt(match[2], 10);
    if (expected === null || minWidth >= expected.minWidth) expected = { minWidth, count };
  }
  return expected?.count ?? null;
}

function expectedGridSpanCount(classes: string[], viewportWidth: number): number | null {
  let expected: { minWidth: number; count: number } | null = null;
  for (const token of classes) {
    const match = token.match(/^(?:(sm|md|lg|xl|2xl):)?col-span-([1-6])$/);
    if (!match) continue;
    const minWidth = gridBreakpoints[match[1] ?? "base"];
    if (minWidth > viewportWidth) continue;
    const count = Number.parseInt(match[2], 10);
    if (expected === null || minWidth >= expected.minWidth) expected = { minWidth, count };
  }
  return expected?.count ?? null;
}

function computedGridColumnCount(value: string): number {
  if (!value || value === "none") return 0;
  return value.trim().split(/\s+/).filter(Boolean).length;
}

function rootAestheticProfileNode(): [string, ManifestNode] | null {
  const match = Object.entries(manifest.nodes).find(([, node]) => {
    return node.primitive === "root" && typeof node.props?.aesthetic_profile === "string";
  });
  return match ?? null;
}

function aestheticLayoutNodeCount(): number {
  return Object.values(manifest.nodes).filter((node) => typeof node.props?.aesthetic_layout_profile === "string").length;
}

function deriveStyleAssertions(viewportWidth: number): StyleAssertion[] {
  const priorityAssertions: StyleAssertion[] = [];
  const assertions: StyleAssertion[] = [];
  for (const [domId, node] of Object.entries(manifest.nodes)) {
    const classes = node.classes ?? [];
    if (classes.includes("grid")) assertions.push({ domId, property: "display", expected: "grid" });
    if (classes.includes("flex")) assertions.push({ domId, property: "display", expected: "flex" });
    if (classes.includes("inline-flex")) assertions.push({ domId, property: "display", expected: "inline-flex" });
    const expectedColumns = expectedGridColumnCount(classes, viewportWidth);
    if (expectedColumns !== null) {
      priorityAssertions.push({
        domId,
        property: "grid-template-columns",
        expectedColumnCount: expectedColumns,
        aestheticLayout: typeof node.props?.aesthetic_layout_profile === "string",
      });
    }
    const expectedSpan = expectedGridSpanCount(classes, viewportWidth);
    if (expectedSpan !== null) {
      priorityAssertions.push({
        domId,
        property: "grid-column-end",
        expectedSpanCount: expectedSpan,
        aestheticLayout: typeof node.props?.aesthetic_layout_profile === "string",
      });
    }
    if (classes.includes("border") || classes.includes("border-t")) {
      assertions.push({ domId, property: "border-top-width", expected: "1px" });
    }
    const backgroundTokens = classes.filter((token) => colorByClass[token]);
    const gapTokens = classes.filter((token) => gapByClass[token]);
    const backgroundToken = backgroundTokens[backgroundTokens.length - 1];
    const gapToken = gapTokens[gapTokens.length - 1];
    if (backgroundToken) assertions.push({ domId, property: "background-color", expected: colorByClass[backgroundToken] });
    if (gapToken) assertions.push({ domId, property: "column-gap", expected: gapByClass[gapToken] });
  }
  return [...priorityAssertions, ...assertions].slice(0, 32);
}

async function expectComputed(page: Page, assertion: StyleAssertion) {
  if (
    !assertion.domId ||
    !assertion.property ||
    !(
      typeof assertion.expected === "string" ||
      assertion.expected instanceof RegExp ||
      typeof assertion.expectedColumnCount === "number" ||
      typeof assertion.expectedSpanCount === "number"
    )
  ) {
    fail("HOST_VERIFY_STYLE_ASSERTION_TOO_WEAK", "computed style assertions require dom id, property, and exact or regex expected value");
  }
  const value = await page.locator(cssId(assertion.domId)).evaluate((element, prop) => {
    return getComputedStyle(element).getPropertyValue(prop);
  }, assertion.property);
  if (typeof assertion.expectedColumnCount === "number") {
    const count = computedGridColumnCount(value);
    if (count !== assertion.expectedColumnCount) {
      fail(
        "HOST_VERIFY_STYLE_ASSERTION_TOO_WEAK",
        `${assertion.domId} ${assertion.property} expected ${assertion.expectedColumnCount} columns got ${count} from ${value}`,
      );
    }
    return;
  }
  if (typeof assertion.expectedSpanCount === "number") {
    const match = value.trim().match(/^span\s+(\d+)$/);
    const count = match ? Number.parseInt(match[1], 10) : 0;
    if (count !== assertion.expectedSpanCount) {
      fail(
        "HOST_VERIFY_STYLE_ASSERTION_TOO_WEAK",
        `${assertion.domId} ${assertion.property} expected span ${assertion.expectedSpanCount} got ${value}`,
      );
    }
    return;
  }
  const expected = assertion.expected;
  if (!(typeof expected === "string" || expected instanceof RegExp)) {
    fail("HOST_VERIFY_STYLE_ASSERTION_TOO_WEAK", "computed style assertions require an expected value");
  }
  const ok = typeof expected === "string" ? value === expected : expected.test(value);
  if (!ok) {
    fail("HOST_VERIFY_STYLE_ASSERTION_TOO_WEAK", `${assertion.domId} ${assertion.property} expected ${expected.toString()} got ${value}`);
  }
}

function writeBrowserReport(assertions: Record<string, number>) {
  const reportPath = process.env.VIEWSPEC_HOST_VERIFY_BROWSER_REPORT;
  if (!reportPath) return;
  mkdirSync(dirname(reportPath), { recursive: true });
  writeFileSync(reportPath, JSON.stringify({ assertions }, null, 2));
}

test("generated React Tailwind artifact builds and behaves in the bounded host", async ({ page }) => {
  const runtimeErrors: string[] = [];
  page.on("pageerror", (error) => runtimeErrors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") runtimeErrors.push(`console: ${message.text()}`);
  });

  await page.goto("/");
  if (runtimeErrors.length) fail("HOST_VERIFY_BROWSER_RUNTIME_ERROR", runtimeErrors.join("\n"));

  let domCount = 0;
  let visibleTextCount = 0;
  for (const [domId, node] of Object.entries(manifest.nodes)) {
    const locator = page.locator(cssId(domId));
    await expect(locator, `HOST_VERIFY_DOM_NODE_MISSING: missing ${domId}`).toHaveCount(1);
    await expect(locator, `HOST_VERIFY_DOM_NODE_MISSING: ${domId} data-ir-id`).toHaveAttribute("data-ir-id", node.ir_id);
    domCount += 1;
    const text = expectedText(node);
    if (text && node.primitive !== "input") {
      await expect(locator, `HOST_VERIFY_DOM_NODE_MISSING: ${domId} text`).toContainText(text);
      visibleTextCount += 1;
    }
  }
  if (domCount < 1 || visibleTextCount < 1) {
    fail("HOST_VERIFY_DOM_NODE_MISSING", "at least one manifest-backed DOM and visible text assertion is required");
  }

  let aestheticProfileAssertionCount = 0;
  const profileNode = rootAestheticProfileNode();
  if (profileNode !== null) {
    const [domId, node] = profileNode;
    const profile = String(node.props?.aesthetic_profile ?? "");
    await expect(page.locator(cssId(domId)), `HOST_VERIFY_AESTHETIC_PROFILE_ASSERTION_MISSING: ${domId}`).toHaveAttribute(
      "data-aesthetic-profile",
      profile,
    );
    aestheticProfileAssertionCount += 1;
  }

  const viewportWidth = page.viewportSize()?.width ?? 1280;
  const styleAssertions = deriveStyleAssertions(viewportWidth);
  if (styleAssertions.length < 4) {
    fail("HOST_VERIFY_STYLE_ASSERTION_TOO_WEAK", `at least four computed style assertions are required, got ${styleAssertions.length}`);
  }
  const gridColumnAssertionCount = styleAssertions.filter((assertion) => typeof assertion.expectedColumnCount === "number").length;
  const gridSpanAssertionCount = styleAssertions.filter((assertion) => typeof assertion.expectedSpanCount === "number").length;
  const aestheticLayoutAssertionCount = styleAssertions.filter(
    (assertion) =>
      assertion.aestheticLayout === true &&
      (typeof assertion.expectedColumnCount === "number" || typeof assertion.expectedSpanCount === "number"),
  ).length;
  const hasGridColumnClass = Object.values(manifest.nodes).some((node) => expectedGridColumnCount(node.classes ?? [], viewportWidth) !== null);
  if (hasGridColumnClass && gridColumnAssertionCount < 1) {
    fail("HOST_VERIFY_STYLE_ASSERTION_TOO_WEAK", "grid column classes require a computed grid-template-columns assertion");
  }
  const expectedAestheticLayoutAssertions = aestheticLayoutNodeCount();
  if (expectedAestheticLayoutAssertions > 0 && aestheticLayoutAssertionCount < expectedAestheticLayoutAssertions) {
    fail(
      "HOST_VERIFY_AESTHETIC_LAYOUT_ASSERTION_MISSING",
      `expected ${expectedAestheticLayoutAssertions} aesthetic layout assertions got ${aestheticLayoutAssertionCount}`,
    );
  }
  for (const assertion of styleAssertions) await expectComputed(page, assertion);

  const emittedActions = actionNodes();
  let payloadBindingCount = 0;
  for (const [domId, node] of emittedActions) {
    const props = node.props ?? {};
    const actionId = String(props.action_id ?? "");
    const payloadBindings = Array.isArray(props.payload_bindings)
      ? props.payload_bindings.filter((value): value is string => typeof value === "string")
      : [];
    const expectedPayloadValues: Record<string, unknown> = {};
    for (const [index, bindingId] of payloadBindings.entries()) {
      const input = page.locator(`[data-binding-id="${bindingId}"]`);
      const count = await input.count();
      if (count !== 1) fail("HOST_VERIFY_PAYLOAD_VALUE_MISMATCH", `${actionId} payload binding ${bindingId} has ${count} editable inputs`);
      const current = await input.inputValue();
      let next = `verify-${actionId}-${index}`;
      if (next === current) next = `${next}-override`;
      await input.fill(next);
      expectedPayloadValues[bindingId] = next;
      payloadBindingCount += 1;
    }

    const before = await page.evaluate(() => ((window as ActionWindow).__viewspecActions ?? []).length);
    await page.locator(cssId(domId)).click();
    await expect.poll(() => page.evaluate(() => ((window as ActionWindow).__viewspecActions ?? []).length)).toBe(before + 1);
    const actions = await page.evaluate(() => (window as ActionWindow).__viewspecActions ?? []);
    const actual = actions[actions.length - 1];
    const expected = {
      schemaVersion: 1,
      source: "viewspec-react-tailwind-tsx",
      id: actionId,
      kind: String(props.action_kind ?? ""),
      targetRef: String(props.target_ref ?? ""),
      payloadBindings,
      payloadValues: expectedPayloadValues,
    };
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
      fail("HOST_VERIFY_PAYLOAD_VALUE_MISMATCH", `${actionId} payload mismatch: ${JSON.stringify(actual)} expected ${JSON.stringify(expected)}`);
    }
  }
  const actionCount = await page.evaluate(() => ((window as ActionWindow).__viewspecActions ?? []).length);
  if (actionCount !== emittedActions.length) {
    fail("HOST_VERIFY_ACTION_COUNT_MISMATCH", `expected ${emittedActions.length} actions, got ${actionCount}`);
  }
  if (runtimeErrors.length) fail("HOST_VERIFY_BROWSER_RUNTIME_ERROR", runtimeErrors.join("\n"));

  writeBrowserReport({
    action_count: actionCount,
    aesthetic_layout_assertion_count: aestheticLayoutAssertionCount,
    aesthetic_profile_assertion_count: aestheticProfileAssertionCount,
    dom_count: domCount,
    grid_column_assertion_count: gridColumnAssertionCount,
    grid_span_assertion_count: gridSpanAssertionCount,
    payload_binding_count: payloadBindingCount,
    style_assertion_count: styleAssertions.length,
  });
});
