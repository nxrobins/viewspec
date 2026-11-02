// Proves a compiled html-tailwind or mcp-app document in Chromium, one plan viewport at a time. The
// document is never rebuilt or edited: html-tailwind is served as it stands from a loopback port, and
// an mcp-app mounts in the ext-apps SDK's AppBridge, in a sandboxed iframe under the default CSP.
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium } from "playwright";

const here = dirname(fileURLToPath(import.meta.url));
const config = JSON.parse(readFileSync(process.argv[2], "utf8"));
const manifestNodes = JSON.parse(readFileSync(config.manifest, "utf8")).nodes;
const documentBytes = readFileSync(config.artifact);
const isMcpApp = config.target === "mcp-app";

// OFFLINE_EMITTER_CSS stacks these grids and drops every span at or below this width.
const GRID_COLLAPSE_MAX_WIDTH = 760;
const COLLAPSING_GRIDS = ["vs-role-content-grid", "vs-role-metric-grid"];
// The primitives whose display does not depend on the tag the emitter chose for them.
const EXPECTED_DISPLAY = { root: "flex", surface: "flex", grid: "grid" };
const SURFACE_CLASSES = ["vs-text", "vs-label", "vs-value", "vs-badge", "vs-button", "vs-error-boundary"];
const HANDSHAKE_TIMEOUT_MS = 8_000;
const SWAP_TIMEOUT_MS = 3_000;
const EVIDENCE_TEXT_LIMIT = 500;
const MESSAGE_LIMIT = 500;
const HOST_PAGE = "<!doctype html><html style='scrollbar-width:none'><body style='margin:0'></body></html>";
// The React proof pins the same measurement font in its build. Here the rule is adopted at runtime,
// in a page used for nothing else, so the document's bytes and the layout proof stay untouched.
const PRETEXT_MEASUREMENT_CSS = `[data-ir-id][data-ir-id] {
  font-family: Arial, sans-serif !important;
  overflow-wrap: anywhere !important;
}`;

async function bundle(entry) {
  const output = await build({ entryPoints: [entry], absWorkingDir: here, bundle: true, format: "iife", write: false });
  return output.outputFiles[0].text;
}

// Runs in the page. Only reads, so a snapshot can never change what the next check sees.
function snapshot() {
  const nodes = Array.from(document.querySelectorAll("[data-ir-id]"), (element) => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return {
      domId: element.id,
      irId: element.getAttribute("data-ir-id"),
      parentId: element.parentElement?.closest("[data-ir-id]")?.id ?? null,
      tag: element.localName,
      classes: Array.from(element.classList),
      inlineStyle: element.getAttribute("style") ?? "",
      aestheticProfile: element.getAttribute("data-aesthetic-profile"),
      hiddenByRule: element.closest("[hidden]") !== null,
      text: (element.textContent ?? "").replace(/\s+/g, " ").trim(),
      visible: rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none",
      position: style.position,
      display: style.display,
      flexDirection: style.flexDirection,
      gridTemplateColumns: style.gridTemplateColumns,
      gridColumnEnd: style.gridColumnEnd,
      rect: {
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height,
      },
    };
  });
  const accessibilityNodes = Array.from(document.querySelectorAll("button,input,select,textarea,a,[role],img"), (element) => {
    const tag = element.tagName.toLowerCase();
    const explicitRole = element.getAttribute("role");
    const role = explicitRole || (tag === "a" ? "link" : tag);
    const labelledBy = element.getAttribute("aria-labelledby");
    const labelledText = labelledBy
      ? labelledBy
          .split(/\s+/)
          .map((id) => document.getElementById(id)?.textContent ?? "")
          .join(" ")
          .trim()
      : "";
    const labels = "labels" in element && element.labels ? Array.from(element.labels) : [];
    const name = (
      element.getAttribute("aria-label") ||
      labelledText ||
      labels.map((label) => label.textContent ?? "").join(" ") ||
      (tag === "img" ? element.getAttribute("alt") : element.textContent) ||
      ""
    ).trim();
    return { domId: element.id || null, tag, role, name };
  });
  const violations = [];
  const seenIds = new Set();
  for (const element of document.querySelectorAll("[id]")) {
    if (seenIds.has(element.id)) {
      violations.push({ type: "duplicate_id", domId: element.id, message: `Duplicate DOM id ${element.id}.` });
    }
    seenIds.add(element.id);
  }
  for (const node of accessibilityNodes) {
    if (["button", "input", "select", "textarea", "link", "img"].includes(node.role) && !node.name) {
      violations.push({ type: "missing_name", domId: node.domId, message: `${node.role} has no accessible name.` });
    }
  }
  return {
    nodes,
    accessibilityNodes,
    violations,
    documentWidth: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
  };
}

function eligibleSurfaces(classes) {
  return Array.from(document.querySelectorAll("[data-ir-id]"))
    .filter((element) => classes.some((token) => element.classList.contains(token)))
    .map((element) => [element.id, element.getAttribute("data-ir-id") ?? ""]);
}

async function settle(frame) {
  await frame.evaluate(async () => {
    await document.fonts.ready;
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  });
}

async function mount(browser, origin, hostScript, viewport) {
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    deviceScaleFactor: 1,
    locale: "en-US",
  });
  const page = await context.newPage();
  const view = { context, page, frame: page.mainFrame(), problems: [], logsSeen: 0, handshake: true };
  page.on("console", (message) => {
    if (message.type() === "error") view.problems.push(`console: ${message.text()}`);
  });
  page.on("pageerror", (error) => view.problems.push(`pageerror: ${error.message}`));
  page.on("request", (request) => {
    const url = request.url();
    if (url !== `${origin}/` && !url.endsWith("/favicon.ico") && !/^(about|data):/.test(url)) {
      view.problems.push(`network: ${url}`);
    }
  });
  await page.goto(`${origin}/`);
  if (!isMcpApp) {
    await page.waitForLoadState("load");
    return view;
  }
  await page.addScriptTag({ content: hostScript });
  await page.evaluate((html) => window.startHost(html), documentBytes.toString("utf8"));
  view.frame = await (await page.locator("#view").elementHandle()).contentFrame();
  await view.frame.waitForLoadState("load");
  view.handshake = await page
    .waitForFunction(() => window.hostLog.initialized >= 1 && window.hostLog.sizes.length >= 1, null, {
      timeout: HANDSHAKE_TIMEOUT_MS,
    })
    .then(
      () => true,
      () => false,
    );
  return view;
}

function newRun(viewport) {
  return {
    viewport,
    assertions: {
      aesthetic_layout_assertion_count: 0,
      aesthetic_profile_assertion_count: 0,
      dom_count: 0,
      grid_column_assertion_count: 0,
      grid_span_assertion_count: 0,
      handshake_count: 0,
      style_assertion_count: 0,
      swap_count: 0,
    },
    diagnostics: [],
    evidence: [],
  };
}

function report(run, refs, { code, message, fix, sourceRef = null, label = null, evidence = "dom" }) {
  run.diagnostics.push({
    code,
    severity: "error",
    message: (label ? `${label}: ${message}` : message).slice(0, MESSAGE_LIMIT),
    fix,
    source_ref: sourceRef,
    viewport: run.viewport.name,
    evidence_refs: evidence === "dom" ? [refs.screenshot, refs.dom] : [refs.screenshot, refs.accessibility],
  });
}

function writeEvidence(run, observed, state) {
  const stem = state ? `${run.viewport.name}.${state}` : run.viewport.name;
  const write = (name, payload) => {
    writeFileSync(join(config.evidence_dir, name), JSON.stringify({ viewport: run.viewport, ...payload }, null, 2));
  };
  write(`${stem}.dom.json`, {
    documentWidth: observed.documentWidth,
    nodes: observed.nodes.map((node) => ({ ...node, text: node.text.slice(0, EVIDENCE_TEXT_LIMIT) })),
  });
  write(`${stem}.a11y.json`, { nodes: observed.accessibilityNodes, violations: observed.violations });
  const refs = {
    screenshot: `evidence/${stem}.png`,
    dom: `evidence/${stem}.dom.json`,
    accessibility: `evidence/${stem}.a11y.json`,
  };
  run.evidence.push(
    { path: refs.screenshot, role: "screenshot", content_type: "image/png" },
    { path: refs.dom, role: "dom", content_type: "application/json" },
    { path: refs.accessibility, role: "accessibility", content_type: "application/json" },
  );
  return { stem, refs };
}

function sourceRef(nodes, domId) {
  return nodes[domId] ? `ir:${nodes[domId].ir_id}` : null;
}

// What the emitter wrote for this node, so a child's text never stands in for a missing one.
function expectedText(node) {
  const props = node.props ?? {};
  let value;
  if (node.primitive === "button") value = props.text ?? props.label;
  else if (["input", "image_slot", "svg", "error_boundary"].includes(node.primitive)) value = undefined;
  else if (typeof props.state_text_id === "string") value = props.state_text_initial;
  else value = props.text;
  const text = value === undefined || value === null ? "" : String(value).replace(/\s+/g, " ").trim();
  return text || null;
}

function expectedColumns(node, width) {
  if (node.primitive !== "grid") return null;
  const collapses = width <= GRID_COLLAPSE_MAX_WIDTH && COLLAPSING_GRIDS.some((token) => (node.classes ?? []).includes(token));
  return collapses ? 1 : Number(node.props?.columns) || 1;
}

function expectedSpan(node, width) {
  const span = node.props?.span_columns;
  const profiled = typeof node.props?.aesthetic_layout_profile === "string";
  if (typeof span !== "number") return profiled && node.primitive !== "grid" ? "auto" : null;
  return width > GRID_COLLAPSE_MAX_WIDTH ? `span ${span}` : "auto";
}

function computedColumnCount(value) {
  return !value || value === "none" ? 0 : value.trim().split(/\s+/).length;
}

function checkContent(run, refs, nodes, observed, label) {
  const seen = new Map(observed.nodes.map((node) => [node.domId, node]));
  const fix = "Recompile the artifact from its source; the document must carry every manifest node.";
  for (const [domId, node] of Object.entries(nodes)) {
    const found = seen.get(domId);
    if (!found || found.irId !== node.ir_id) {
      const message = `${domId} (${node.ir_id}) is missing from the rendered document.`;
      report(run, refs, { code: "VERIFY_CONTENT_MISSING", message, fix, sourceRef: `ir:${node.ir_id}`, label });
      continue;
    }
    run.assertions.dom_count += 1;
    const text = expectedText(node);
    if (!text || found.text.includes(text)) continue;
    const message = `${domId} does not show its manifest text.`;
    report(run, refs, { code: "VERIFY_CONTENT_MISSING", message, fix, sourceRef: `ir:${node.ir_id}`, label });
  }
  for (const [domId, node] of Object.entries(nodes)) {
    const found = seen.get(domId);
    if (!found || found.visible || found.hiddenByRule || !expectedText(node)) continue;
    const message = `${domId} renders nothing at the ${run.viewport.name} viewport.`;
    report(run, refs, { code: "VERIFY_CONTENT_MISSING", message, fix, sourceRef: `ir:${node.ir_id}`, label });
  }
}

function checkStyles(run, refs, nodes, observed, label) {
  const width = observed.viewportWidth;
  const fix = "Restore the compiled grid; the referenced node must lay out as its manifest declares.";
  const fail = (domId, message) => {
    report(run, refs, {
      code: "VERIFY_LAYOUT_CONFORMANCE_FAILED",
      message,
      fix,
      sourceRef: sourceRef(nodes, domId),
      label,
    });
  };
  for (const found of observed.nodes) {
    const node = nodes[found.domId];
    if (!node) continue;
    const profile = node.props?.aesthetic_profile;
    if (typeof profile === "string" && profile) {
      run.assertions.aesthetic_profile_assertion_count += 1;
      if (found.aestheticProfile !== profile) {
        const message = `${found.domId} expected data-aesthetic-profile ${profile} got ${found.aestheticProfile}.`;
        report(run, refs, { code: "VERIFY_CONTENT_MISSING", message, fix, sourceRef: sourceRef(nodes, found.domId), label });
      }
    }
    if (!found.visible) continue;
    const profiled = typeof node.props?.aesthetic_layout_profile === "string";
    const display = EXPECTED_DISPLAY[node.primitive];
    if (display) {
      run.assertions.style_assertion_count += 1;
      if (found.display !== display) fail(found.domId, `${found.domId} display expected ${display} got ${found.display}.`);
    }
    if (node.primitive === "root") {
      run.assertions.style_assertion_count += 1;
      if (found.flexDirection !== "column") {
        fail(found.domId, `${found.domId} flex-direction expected column got ${found.flexDirection}.`);
      }
    }
    const columns = expectedColumns(node, width);
    if (columns !== null) {
      run.assertions.style_assertion_count += 1;
      run.assertions.grid_column_assertion_count += 1;
      if (profiled) run.assertions.aesthetic_layout_assertion_count += 1;
      const count = computedColumnCount(found.gridTemplateColumns);
      if (count !== columns) {
        fail(found.domId, `${found.domId} grid-template-columns expected ${columns} columns got ${count} at ${width}px.`);
      }
    }
    const span = expectedSpan(node, width);
    if (span !== null) {
      run.assertions.style_assertion_count += 1;
      run.assertions.grid_span_assertion_count += 1;
      if (profiled) run.assertions.aesthetic_layout_assertion_count += 1;
      if (found.gridColumnEnd !== span) {
        fail(found.domId, `${found.domId} grid-column-end expected ${span} got ${found.gridColumnEnd} at ${width}px.`);
      }
    }
  }
}

function overlaps(left, right) {
  if (!left.visible || !right.visible || left.parentId !== right.parentId || left.parentId === null) return false;
  if ([left.position, right.position].some((position) => ["absolute", "fixed", "sticky"].includes(position))) return false;
  const width = Math.min(left.rect.right, right.rect.right) - Math.max(left.rect.left, right.rect.left);
  const height = Math.min(left.rect.bottom, right.rect.bottom) - Math.max(left.rect.top, right.rect.top);
  return width > 2 && height > 2;
}

function checkGeometry(run, refs, nodes, observed, label) {
  const name = run.viewport.name;
  if (observed.documentWidth > observed.viewportWidth + 1) {
    const root = observed.nodes.find((node) => node.classes.includes("vs-root"));
    report(run, refs, {
      code: "VERIFY_LAYOUT_OVERFLOW",
      message: `Document width ${observed.documentWidth}px exceeds the ${observed.viewportWidth}px viewport.`,
      fix: "Constrain the referenced layout width and remove horizontal document overflow.",
      sourceRef: root ? sourceRef(nodes, root.domId) : null,
      label,
    });
  }
  for (const node of observed.nodes) {
    if (!node.visible || node.rect.width <= 0) continue;
    if (node.rect.left < -1 || node.rect.right > observed.viewportWidth + 1) {
      report(run, refs, {
        code: "VERIFY_LAYOUT_OVERFLOW",
        message: `${node.domId} extends outside the ${name} viewport.`,
        fix: "Constrain the referenced source node at this viewport and retry verification.",
        sourceRef: sourceRef(nodes, node.domId),
        label,
      });
    }
  }
  const siblings = new Map();
  for (const node of observed.nodes) {
    if (!siblings.has(node.parentId)) siblings.set(node.parentId, []);
    siblings.get(node.parentId).push(node);
  }
  for (const group of siblings.values()) {
    for (let leftIndex = 0; leftIndex < group.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < group.length; rightIndex += 1) {
        const left = group[leftIndex];
        const right = group[rightIndex];
        if (!overlaps(left, right)) continue;
        report(run, refs, {
          code: "VERIFY_LAYOUT_OVERLAP",
          message: `${left.domId} overlaps sibling ${right.domId} at the ${name} viewport.`,
          fix: "Adjust the referenced sibling layout so their rendered bounds do not overlap.",
          sourceRef: sourceRef(nodes, right.domId) ?? sourceRef(nodes, left.domId),
          label,
        });
      }
    }
  }
}

function checkAccessibility(run, refs, nodes, observed, label) {
  for (const violation of observed.violations) {
    report(run, refs, {
      code: "VERIFY_A11Y_VIOLATION",
      message: violation.message,
      fix: "Add a unique semantic identity and accessible name to the referenced source node.",
      sourceRef: violation.domId ? sourceRef(nodes, violation.domId) : null,
      label,
      evidence: "accessibility",
    });
  }
}

async function checkRuntime(run, refs, view, label) {
  const fix = "Keep the document self-contained: it may not fail, log errors, or load anything at runtime.";
  for (const problem of view.problems.splice(0)) {
    report(run, refs, { code: "VERIFY_RUNTIME_ERROR", message: problem, fix, label });
  }
  if (!isMcpApp) return;
  const logs = await view.page.evaluate((seen) => window.hostLog.logs.slice(seen), view.logsSeen);
  view.logsSeen += logs.length;
  for (const log of logs) {
    report(run, refs, { code: "VERIFY_RUNTIME_ERROR", message: `host log: ${String(log.data)}`, fix, label });
  }
}

// A render arrives with no manifest, so its own markup says what it should be: the columns the
// compiler wrote inline and the span classes it chose.
function nodesFromRoot(observed) {
  const nodes = {};
  for (const node of observed.nodes) {
    const columns = node.inlineStyle.match(/grid-template-columns:\s*repeat\((\d+)/)?.[1];
    const span = node.classes.find((token) => /^vs-span-\d$/.test(token))?.slice(-1);
    nodes[node.domId] = {
      ir_id: node.irId,
      primitive: (node.classes[0] ?? "").replace(/^vs-/, "").replaceAll("-", "_"),
      classes: node.classes,
      props: { ...(columns ? { columns: Number(columns) } : {}), ...(span ? { span_columns: Number(span) } : {}) },
    };
  }
  return nodes;
}

async function proveState(view, run, { manifest = null, state = null, label = null } = {}) {
  await settle(view.frame);
  const observed = await view.frame.evaluate(snapshot);
  const { stem, refs } = writeEvidence(run, observed, state);
  await view.page.screenshot({ path: join(config.evidence_dir, `${stem}.png`), fullPage: true });
  const nodes = manifest ?? nodesFromRoot(observed);
  if (manifest) checkContent(run, refs, nodes, observed, label);
  checkStyles(run, refs, nodes, observed, label);
  checkGeometry(run, refs, nodes, observed, label);
  checkAccessibility(run, refs, nodes, observed, label);
  await checkRuntime(run, refs, view, label);
  return refs;
}

async function checkHandshake(view, run, refs) {
  const log = await view.page.evaluate(() => window.hostLog);
  if (view.handshake && log.initialized === 1 && log.sizes[0]?.height > 0) {
    run.assertions.handshake_count += 1;
    return;
  }
  report(run, refs, {
    code: "VERIFY_RUNTIME_ERROR",
    message: `The ui/initialize handshake did not complete cleanly: initialized ${log.initialized} times, ${log.sizes.length} size reports.`,
    fix: "Recompile the MCP App so the pinned bridge can reach the host, then retry verification.",
  });
}

// Sends the root as a host would and waits for the view to swap it in or log its refusal. Reports
// through the element itself: a refused root leaves it connected, and a replacement detaches it.
async function swapRoot(view, structuredContent) {
  const logged = await view.page.evaluate(() => window.hostLog.logs.length);
  const prior = await view.frame.evaluateHandle(() => document.querySelector(".vs-root"));
  await view.page.evaluate(
    (content) => window.bridge.sendToolResult({ content: [{ type: "text", text: "Rendered." }], structuredContent: content }),
    structuredContent,
  );
  const deadline = Date.now() + SWAP_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (!(await prior.evaluate((element) => element.isConnected))) return true;
    if ((await view.page.evaluate(() => window.hostLog.logs.length)) > logged) return false;
    await view.page.waitForTimeout(25);
  }
  return false;
}

async function proveSwap(view, run, refs, { name, structuredContent, manifest = null }) {
  const label = `After ${name}`;
  if (await swapRoot(view, structuredContent)) {
    run.assertions.swap_count += 1;
    return proveState(view, run, { manifest, state: name, label });
  }
  report(run, refs, {
    code: "VERIFY_RUNTIME_ERROR",
    message: `${label}: the view refused the root, so the result never rendered.`,
    fix: "Return app.render() from the tool, so the root comes from the compiler the bridge expects.",
  });
  await checkRuntime(run, refs, view, label);
  return refs;
}

async function proveViewport(browser, origin, hostScript, viewport) {
  const run = newRun(viewport);
  const view = await mount(browser, origin, hostScript, viewport);
  try {
    let refs = await proveState(view, run, { manifest: manifestNodes });
    if (isMcpApp) {
      await checkHandshake(view, run, refs);
      if (view.handshake) {
        const compiled = { structuredContent: { viewspecRoot: config.compiled_root }, manifest: manifestNodes };
        refs = await proveSwap(view, run, refs, { name: "swap", ...compiled });
        for (const render of config.renders) refs = await proveSwap(view, run, refs, render);
      }
    }
  } finally {
    await view.context.close();
  }
  const { assertions, diagnostics, evidence } = run;
  writeFileSync(
    join(config.reports_dir, `${viewport.name}.json`),
    JSON.stringify({ viewport, assertions, diagnostics, evidence }, null, 2),
  );
}

function surfaceInventory(pairs) {
  const inventory = pairs.sort(([leftId, leftIr], [rightId, rightIr]) => {
    if (leftId !== rightId) return leftId < rightId ? -1 : 1;
    if (leftIr !== rightIr) return leftIr < rightIr ? -1 : 1;
    return 0;
  });
  return {
    eligible_surface_count: inventory.length,
    eligible_surface_sha256: createHash("sha256").update(JSON.stringify(inventory)).digest("hex"),
  };
}

// One page across every viewport, so the probe's prepared text survives each resize, as the
// validator requires. Everything reaches the page through evaluation, which the view's CSP allows.
async function provePretext(browser, origin, hostScript) {
  const { scope, probe, report: reportPath } = config.pretext;
  const probeScript = await bundle(probe);
  const [{ screen_id: screenId, route_id: routeId, surfaces }] = scope.screens;
  const view = await mount(browser, origin, hostScript, scope.viewports[0]);
  const items = [];
  const inventories = [];
  const errors = [];
  let result;
  try {
    await view.frame.evaluate((css) => {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(css);
      document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
    }, PRETEXT_MEASUREMENT_CSS);
    await view.frame.evaluate(probeScript);
    for (const viewport of scope.viewports) {
      await view.page.setViewportSize({ width: viewport.width, height: viewport.height });
      await settle(view.frame);
      inventories.push({
        screen_id: screenId,
        route_id: routeId,
        viewport_id: viewport.id,
        ...surfaceInventory(await view.frame.evaluate(eligibleSurfaces, SURFACE_CLASSES)),
      });
      result = await view.frame.evaluate((input) => window.__viewspecPretextProbe(input), {
        screenId,
        routeId,
        viewportId: viewport.id,
        surfaces,
      });
      items.push(...result.items);
      errors.push(...result.errors);
    }
  } finally {
    await view.context.close();
  }
  const count = (status) => items.filter((item) => item.status === status).length;
  const summary = {
    required: scope.required_observation_count,
    accounted: items.length,
    measured: count("passed"),
    hidden: count("hidden"),
    unsupported: count("unsupported"),
    failed: count("failed"),
  };
  writeFileSync(
    reportPath,
    JSON.stringify(
      {
        schema_version: 2,
        engine: result.engine,
        profile: scope.profile,
        protocol: scope.protocol,
        environment: result.environment,
        viewports: scope.viewports,
        inventories,
        items,
        summary,
        cache: result.cache,
        errors,
      },
      null,
      2,
    ),
  );
}

async function main() {
  mkdirSync(config.reports_dir, { recursive: true });
  mkdirSync(config.evidence_dir, { recursive: true });
  const hostScript = isMcpApp ? await bundle("host.ts") : null;
  const server = createServer((request, response) => {
    if (request.url !== "/") {
      response.writeHead(404).end();
      return;
    }
    response.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" });
    response.end(isMcpApp ? HOST_PAGE : documentBytes);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch();
  try {
    for (const viewport of config.plan.viewports) await proveViewport(browser, origin, hostScript, viewport);
    if (config.pretext) await provePretext(browser, origin, hostScript);
  } finally {
    await browser.close();
    server.close();
  }
}

main().catch((error) => {
  console.error(`HOST_VERIFY_BROWSER_RUNTIME_ERROR: ${error.stack ?? error}`);
  process.exit(1);
});
