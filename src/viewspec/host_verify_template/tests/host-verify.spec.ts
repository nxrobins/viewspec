import { expect, test, type Page, type TestInfo } from "@playwright/test";
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

type VerificationDiagnostic = {
  code: string;
  severity: "info" | "warning" | "error";
  message: string;
  fix: string;
  source_ref: string | null;
  viewport: string;
  evidence_refs: string[];
};

type EvidenceDeclaration = {
  path: string;
  role: "screenshot" | "dom" | "accessibility";
  content_type: string;
};

type DomSnapshot = {
  domId: string;
  parentId: string | null;
  tag: string;
  text: string;
  visible: boolean;
  position: string;
  rect: { left: number; top: number; right: number; bottom: number; width: number; height: number };
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
    if (classes.includes("inline-flex")) {
      assertions.push({ domId, property: "display", expected: /^(inline-flex|flex)$/ });
    }
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

function plannedViewport(projectName: string): { name: string; width: number; height: number } {
  const raw = process.env.VIEWSPEC_HOST_VERIFY_PLAN_JSON;
  if (!raw) return { name: projectName, width: 1280, height: 900 };
  const parsed = JSON.parse(raw) as { viewports?: Array<{ name: string; width: number; height: number }> };
  const viewport = parsed.viewports?.find((item) => item.name === projectName);
  if (!viewport) fail("HOST_VERIFY_PROOF_REPORT_INVALID", `missing viewport plan for ${projectName}`);
  return viewport;
}

function sourceRef(domId: string): string | null {
  const node = manifest.nodes[domId];
  return node ? `ir:${node.ir_id}` : null;
}

function overlaps(left: DomSnapshot, right: DomSnapshot): boolean {
  if (!left.visible || !right.visible || left.parentId !== right.parentId || left.parentId === null) return false;
  if ([left.position, right.position].some((position) => ["absolute", "fixed", "sticky"].includes(position))) return false;
  const width = Math.min(left.rect.right, right.rect.right) - Math.max(left.rect.left, right.rect.left);
  const height = Math.min(left.rect.bottom, right.rect.bottom) - Math.max(left.rect.top, right.rect.top);
  return width > 2 && height > 2;
}

async function writeVerificationEvidence(
  page: Page,
  testInfo: TestInfo,
): Promise<{ diagnostics: VerificationDiagnostic[]; evidence: EvidenceDeclaration[] }> {
  const evidenceRoot = process.env.VIEWSPEC_HOST_VERIFY_EVIDENCE_DIR;
  if (!evidenceRoot) return { diagnostics: [], evidence: [] };
  mkdirSync(evidenceRoot, { recursive: true });
  const viewport = plannedViewport(testInfo.project.name);
  const screenshotName = `${viewport.name}.png`;
  const domName = `${viewport.name}.dom.json`;
  const accessibilityName = `${viewport.name}.a11y.json`;
  await page.screenshot({ path: join(evidenceRoot, screenshotName), fullPage: true });

  const ids = Object.keys(manifest.nodes);
  const browserState = await page.evaluate(({ manifestIds }) => {
    const nodes = manifestIds.flatMap((domId) => {
      const element = document.getElementById(domId);
      if (!element) return [];
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return [
        {
          domId,
          parentId: element.parentElement?.id || null,
          tag: element.tagName.toLowerCase(),
          text: (element.textContent ?? "").trim().slice(0, 500),
          visible: rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none",
          position: style.position,
          rect: {
            left: rect.left,
            top: rect.top,
            right: rect.right,
            bottom: rect.bottom,
            width: rect.width,
            height: rect.height,
          },
        },
      ];
    });
    const semanticElements = Array.from(
      document.querySelectorAll<HTMLElement>("button,input,select,textarea,a,[role],img"),
    );
    const accessibilityNodes = semanticElements.map((element) => {
      const tag = element.tagName.toLowerCase();
      const explicitRole = element.getAttribute("role");
      const role = explicitRole || (tag === "a" ? "link" : tag === "img" ? "img" : tag);
      const labelledBy = element.getAttribute("aria-labelledby");
      const labelledText = labelledBy
        ? labelledBy
            .split(/\s+/)
            .map((id) => document.getElementById(id)?.textContent ?? "")
            .join(" ")
            .trim()
        : "";
      const labels = "labels" in element && element.labels ? Array.from(element.labels as NodeListOf<HTMLLabelElement>) : [];
      const name = (
        element.getAttribute("aria-label") ||
        labelledText ||
        labels.map((label) => label.textContent ?? "").join(" ") ||
        (tag === "img" ? element.getAttribute("alt") : element.textContent) ||
        ""
      ).trim();
      return { domId: element.id || null, tag, role, name };
    });
    const violations: Array<{ type: string; domId: string | null; message: string }> = [];
    const seenIds = new Set<string>();
    for (const element of Array.from(document.querySelectorAll<HTMLElement>("[id]"))) {
      if (seenIds.has(element.id)) {
        violations.push({ type: "duplicate_id", domId: element.id, message: `Duplicate DOM id ${element.id}.` });
      }
      seenIds.add(element.id);
    }
    for (const node of accessibilityNodes) {
      if (["button", "input", "select", "textarea", "link", "img"].includes(node.role) && !node.name) {
        violations.push({
          type: "missing_name",
          domId: node.domId,
          message: `${node.role} has no accessible name.`,
        });
      }
    }
    return {
      nodes,
      accessibilityNodes,
      violations,
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
    };
  }, { manifestIds: ids });

  writeFileSync(
    join(evidenceRoot, domName),
    JSON.stringify({ viewport, documentWidth: browserState.documentWidth, nodes: browserState.nodes }, null, 2),
  );
  writeFileSync(
    join(evidenceRoot, accessibilityName),
    JSON.stringify({ viewport, nodes: browserState.accessibilityNodes, violations: browserState.violations }, null, 2),
  );

  const screenshotRef = `evidence/${screenshotName}`;
  const domRef = `evidence/${domName}`;
  const accessibilityRef = `evidence/${accessibilityName}`;
  const diagnostics: VerificationDiagnostic[] = [];
  if (browserState.documentWidth > browserState.viewportWidth + 1) {
    const root = Object.entries(manifest.nodes).find(([, node]) => node.primitive === "root");
    diagnostics.push({
      code: "VERIFY_LAYOUT_OVERFLOW",
      severity: "error",
      message: `Document width ${browserState.documentWidth}px exceeds the ${browserState.viewportWidth}px viewport.`,
      fix: "Constrain the referenced layout width and remove horizontal document overflow.",
      source_ref: root ? `ir:${root[1].ir_id}` : null,
      viewport: viewport.name,
      evidence_refs: [screenshotRef, domRef],
    });
  }
  for (const node of browserState.nodes) {
    if (!node.visible || node.rect.width <= 0) continue;
    if (node.rect.left < -1 || node.rect.right > browserState.viewportWidth + 1) {
      diagnostics.push({
        code: "VERIFY_LAYOUT_OVERFLOW",
        severity: "error",
        message: `${node.domId} extends outside the ${viewport.name} viewport.`,
        fix: "Constrain the referenced source node at this viewport and retry verification.",
        source_ref: sourceRef(node.domId),
        viewport: viewport.name,
        evidence_refs: [screenshotRef, domRef],
      });
    }
  }
  for (let leftIndex = 0; leftIndex < browserState.nodes.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < browserState.nodes.length; rightIndex += 1) {
      const left = browserState.nodes[leftIndex];
      const right = browserState.nodes[rightIndex];
      if (!overlaps(left, right)) continue;
      diagnostics.push({
        code: "VERIFY_LAYOUT_OVERLAP",
        severity: "error",
        message: `${left.domId} overlaps sibling ${right.domId} at the ${viewport.name} viewport.`,
        fix: "Adjust the referenced sibling layout so their rendered bounds do not overlap.",
        source_ref: sourceRef(right.domId) ?? sourceRef(left.domId),
        viewport: viewport.name,
        evidence_refs: [screenshotRef, domRef],
      });
    }
  }
  for (const violation of browserState.violations) {
    diagnostics.push({
      code: "VERIFY_A11Y_VIOLATION",
      severity: "error",
      message: violation.message,
      fix: "Add a unique semantic identity and accessible name to the referenced source node.",
      source_ref: violation.domId ? sourceRef(violation.domId) : null,
      viewport: viewport.name,
      evidence_refs: [screenshotRef, accessibilityRef],
    });
  }
  return {
    diagnostics,
    evidence: [
      { path: screenshotRef, role: "screenshot", content_type: "image/png" },
      { path: domRef, role: "dom", content_type: "application/json" },
      { path: accessibilityRef, role: "accessibility", content_type: "application/json" },
    ],
  };
}

function writeBrowserReport(
  assertions: Record<string, number>,
  testInfo: TestInfo,
  diagnostics: VerificationDiagnostic[],
  evidence: EvidenceDeclaration[],
) {
  const reportDir = process.env.VIEWSPEC_HOST_VERIFY_BROWSER_REPORT_DIR;
  if (reportDir) {
    mkdirSync(reportDir, { recursive: true });
    const viewport = plannedViewport(testInfo.project.name);
    writeFileSync(
      join(reportDir, `${viewport.name}.json`),
      JSON.stringify({ viewport, assertions, diagnostics, evidence }, null, 2),
    );
    return;
  }
  const reportPath = process.env.VIEWSPEC_HOST_VERIFY_BROWSER_REPORT;
  if (!reportPath) return;
  mkdirSync(dirname(reportPath), { recursive: true });
  writeFileSync(reportPath, JSON.stringify({ assertions }, null, 2));
}

test("generated React Tailwind artifact builds and behaves in the bounded host", async ({ page }, testInfo) => {
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
  if (!process.env.VIEWSPEC_HOST_VERIFY_PLAN_JSON && styleAssertions.length < 4) {
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

  // Capture the reviewable initial UI before action-contract checks fill inputs with
  // deterministic verifier values. Interaction proof continues below against the same page.
  const verification = await writeVerificationEvidence(page, testInfo);

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
      const binding = page.locator(`[data-binding-id="${bindingId}"]`);
      const count = await binding.count();
      if (count !== 1) fail("HOST_VERIFY_PAYLOAD_VALUE_MISMATCH", `${actionId} payload binding ${bindingId} has ${count} bound nodes`);
      const editable = binding.locator("input,textarea,select");
      const tag = await binding.evaluate((element) => element.tagName.toLowerCase());
      if (["input", "textarea", "select"].includes(tag)) {
        const current = await binding.inputValue();
        let next = `verify-${actionId}-${index}`;
        if (next === current) next = `${next}-override`;
        await binding.fill(next);
        expectedPayloadValues[bindingId] = next;
      } else if (await editable.count()) {
        const current = await editable.first().inputValue();
        let next = `verify-${actionId}-${index}`;
        if (next === current) next = `${next}-override`;
        await editable.first().fill(next);
        expectedPayloadValues[bindingId] = next;
      } else {
        const boundDomId = await binding.getAttribute("id");
        const boundNode = boundDomId ? manifest.nodes[boundDomId] : undefined;
        const staticValue = boundNode?.props?.value ?? boundNode?.props?.text;
        if (staticValue === undefined) {
          fail("HOST_VERIFY_PAYLOAD_VALUE_MISMATCH", `${actionId} static payload binding ${bindingId} has no manifest value`);
        }
        expectedPayloadValues[bindingId] = staticValue;
      }
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
  }, testInfo, verification.diagnostics, verification.evidence);
});
