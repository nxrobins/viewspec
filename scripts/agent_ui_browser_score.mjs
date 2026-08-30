#!/usr/bin/env node

import { createServer } from "node:http";
import { createHash } from "node:crypto";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { extname, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";

const repoRoot = resolve(new URL("..", import.meta.url).pathname);
const defaultPlaywright = resolve(
  repoRoot,
  "src/viewspec/host_verify_template/node_modules/playwright/index.mjs",
);

function argumentsMap(values) {
  const result = {};
  for (let index = 0; index < values.length; index += 2) {
    const key = values[index];
    const value = values[index + 1];
    if (!key?.startsWith("--") || value === undefined) throw new Error(`Invalid argument: ${key || "<missing>"}`);
    result[key.slice(2)] = value;
  }
  return result;
}

function safePath(root, requestPath, defaultEntry) {
  const relative = decodeURIComponent(requestPath).replace(/^\/+/, "") || defaultEntry;
  const selected = resolve(root, relative.endsWith("/") ? `${relative}index.html` : relative);
  if (selected !== root && !selected.startsWith(`${root}${sep}`)) throw new Error("request escaped the artifact root");
  return selected;
}

function mime(path) {
  return {
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
  }[extname(path)] || "application/octet-stream";
}

async function startServer(candidateRoot, referenceRoot, candidateEntry, referenceEntry) {
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url || "/", "http://127.0.0.1");
      const isReference = url.pathname === "/__reference__" || url.pathname.startsWith("/__reference__/");
      const root = isReference ? referenceRoot : candidateRoot;
      const requestPath = isReference ? url.pathname.slice("/__reference__".length) || "/" : url.pathname;
      let path = safePath(root, requestPath, isReference ? referenceEntry : candidateEntry);
      try {
        const content = await readFile(path);
        response.writeHead(200, { "content-type": mime(path), "cache-control": "no-store" });
        response.end(content);
      } catch (error) {
        if (!isReference && error?.code === "ENOENT") {
          path = resolve(candidateRoot, candidateEntry);
          response.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" });
          response.end(await readFile(path));
          return;
        }
        throw error;
      }
    } catch (error) {
      if (response.headersSent) {
        response.destroy(error instanceof Error ? error : undefined);
        return;
      }
      response.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
      response.end(String(error));
    }
  });
  await new Promise((accept, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", accept);
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("browser score server did not expose a port");
  return { server, baseUrl: `http://127.0.0.1:${address.port}` };
}

function normalize(value) {
  return value.replace(/\s+/g, " ").trim().toLocaleLowerCase("en-US");
}

async function anchor(page, text) {
  const matches = page.getByText(text, { exact: true });
  let locator = null;
  for (let index = 0; index < await matches.count(); index += 1) {
    const candidate = matches.nth(index);
    if (await candidate.isVisible()) {
      locator = candidate;
      break;
    }
  }
  if (locator === null) return null;
  return locator.evaluate((element) => {
    const rectangle = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return {
      x: rectangle.x / innerWidth,
      y: rectangle.y / innerHeight,
      width: rectangle.width / innerWidth,
      height: rectangle.height / innerHeight,
      fontSize: Number.parseFloat(style.fontSize) / innerWidth,
      fontWeight: Number.parseInt(style.fontWeight, 10) || 400,
      color: style.color,
    };
  });
}

function anchorSimilarity(left, right) {
  if (!left || !right) return 0;
  const geometry =
    Math.abs(left.x - right.x) +
    Math.abs(left.y - right.y) +
    Math.abs(left.width - right.width) +
    Math.abs(left.height - right.height);
  const typography = Math.abs(left.fontSize - right.fontSize) * 4 + Math.abs(left.fontWeight - right.fontWeight) / 1800;
  const colorPenalty = left.color === right.color ? 0 : 0.035;
  return Math.max(0, Math.min(1, 1 - (geometry * 1.6 + typography + colorPenalty)));
}

async function pageFacts(page) {
  return page.evaluate(() => {
    const headings = [...document.querySelectorAll("h1,h2,h3,h4,h5,h6")]
      .filter((element) => {
        const style = getComputedStyle(element);
        return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
      })
      .map((element) => Number(element.tagName.slice(1)));
    let headingOrder = true;
    for (let index = 1; index < headings.length; index += 1) {
      if (headings[index] > headings[index - 1] + 1) headingOrder = false;
    }
    const visibleButtons = [...document.querySelectorAll("button")].filter((element) => {
      const style = getComputedStyle(element);
      return style.display !== "none" && style.visibility !== "hidden" && element.getClientRects().length > 0;
    });
    return {
      text: document.body.innerText,
      overflow: document.documentElement.scrollWidth - innerWidth,
      mainCount: document.querySelectorAll("main").length,
      headingOrder,
      buttonNamesComplete: visibleButtons.every((button) => (button.getAttribute("aria-label") || button.innerText).trim()),
      elementCount: document.querySelectorAll("*").length,
      scrollWidth: document.documentElement.scrollWidth,
      scrollHeight: document.documentElement.scrollHeight,
      viewportWidth: innerWidth,
      viewportHeight: innerHeight,
    };
  });
}

async function uniqueVisible(locator) {
  const matches = [];
  for (let index = 0; index < await locator.count(); index += 1) {
    const item = locator.nth(index);
    if (await item.isVisible()) matches.push(item);
  }
  return matches.length === 1 ? matches[0] : null;
}

async function textGeometry(page, contract) {
  const identityLocators = [];
  if (contract.identity) {
    identityLocators.push(page.locator(`[data-eval-id=${JSON.stringify(contract.identity)}]`));
  }
  if (contract.resource?.record_id && contract.resource?.field) {
    identityLocators.push(page.locator(
      `[data-record-id=${JSON.stringify(contract.resource.record_id)}]` +
      `[data-resource-field=${JSON.stringify(contract.resource.field)}]`,
    ));
  }
  let locator = null;
  for (const candidate of identityLocators) {
    locator = await uniqueVisible(candidate);
    if (locator !== null) break;
  }
  if (locator === null && identityLocators.length === 0) {
    locator = await uniqueVisible(page.getByText(contract.text, { exact: true }));
  }
  if (locator === null) return null;
  return locator.evaluate((element) => {
    const style = getComputedStyle(element);
    const rectangle = element.getBoundingClientRect();
    const range = document.createRange();
    range.selectNodeContents(element);
    const tops = [...range.getClientRects()]
      .filter((item) => item.width > 0 && item.height > 0)
      .map((item) => Math.round(item.top * 10) / 10);
    const fragmentedWords = [];
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    let textNode = walker.nextNode();
    while (textNode) {
      const value = textNode.textContent || "";
      for (const match of value.matchAll(/\S+/g)) {
        const wordRange = document.createRange();
        wordRange.setStart(textNode, match.index);
        wordRange.setEnd(textNode, match.index + match[0].length);
        const wordTops = new Set(
          [...wordRange.getClientRects()]
            .filter((item) => item.width > 0 && item.height > 0)
            .map((item) => Math.round(item.top * 10) / 10),
        );
        if (wordTops.size > 1) fragmentedWords.push(match[0]);
      }
      textNode = walker.nextNode();
    }
    return {
      actual_text: (element.textContent || "").replace(/\s+/g, " ").trim(),
      line_count: new Set(tops).size || 1,
      clipped:
        element.scrollWidth > element.clientWidth + 1 ||
        element.scrollHeight > element.clientHeight + 1 ||
        rectangle.left < -1 ||
        rectangle.right > innerWidth + 1,
      white_space: style.whiteSpace,
      overflow_x: style.overflowX,
      overflow_y: style.overflowY,
      client_width: element.clientWidth,
      client_height: element.clientHeight,
      scroll_width: element.scrollWidth,
      scroll_height: element.scrollHeight,
      fragmented_words: fragmentedWords,
      word_break: style.wordBreak,
      overflow_wrap: style.overflowWrap,
    };
  }).then((geometry) => {
    const actual = normalize(geometry.actual_text);
    const expected = normalize(contract.text);
    return {
      ...geometry,
      text_matches: actual === expected || actual === `${expected}.`,
    };
  });
}

async function visibleExactTextCount(page, text) {
  const locator = page.getByText(text, { exact: true });
  let count = 0;
  for (let index = 0; index < await locator.count(); index += 1) {
    if (await locator.nth(index).isVisible()) count += 1;
  }
  return count;
}

async function visiblePrimaryHeadingCount(page, text) {
  return page.locator("h1").evaluateAll((elements, expected) => {
    const normalizeText = (value) => String(value || "").replace(/\s+/g, " ").trim().toLocaleLowerCase("en-US");
    return elements.filter((element) => {
      const style = getComputedStyle(element);
      const box = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && box.width > 0 && box.height > 0
        && normalizeText(element.innerText) === normalizeText(expected);
    }).length;
  }, text);
}

async function visibleNormalizedTextCount(page, text) {
  return page.locator("body *").evaluateAll((elements, expected) => {
    const normalizedExpected = String(expected).replace(/\s+/g, " ").trim().toLocaleLowerCase("en-US");
    const normalizeText = (value) => String(value || "").replace(/\s+/g, " ").trim().toLocaleLowerCase("en-US");
    const visible = (element) => {
      const style = getComputedStyle(element);
      const box = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && box.width > 0 && box.height > 0;
    };
    return elements.filter((element) => {
      if (!visible(element) || normalizeText(element.innerText) !== normalizedExpected) return false;
      return !Array.from(element.children).some(
        (child) => visible(child) && normalizeText(child.innerText) === normalizedExpected,
      );
    }).length;
  }, text);
}

function criterion(criteria, id, dimension, passed, details = {}) {
  criteria.push({ id, dimension, passed: Boolean(passed), ...details });
}

async function scoreViewport({ browser, baseUrl, spec, viewport, evidenceDir, referenceStep }) {
  const page = await browser.newPage({ viewport });
  const reference = await browser.newPage({ viewport });
  const criteria = [];
  const runtime = { console_errors: [], console_warnings: [], page_errors: [], failed_requests: [], external_requests: [] };
  page.on("console", (message) => {
    if (message.type() === "error") runtime.console_errors.push(message.text().slice(0, 1000));
    if (message.type() === "warning") runtime.console_warnings.push(message.text().slice(0, 1000));
  });
  page.on("pageerror", (error) => runtime.page_errors.push(String(error).slice(0, 1000)));
  page.on("requestfailed", (request) => runtime.failed_requests.push(request.url().slice(0, 1000)));
  page.on("request", (request) => {
    if (new URL(request.url()).origin !== new URL(baseUrl).origin) runtime.external_requests.push(request.url().slice(0, 1000));
  });
  try {
    await Promise.all([
      page.goto(`${baseUrl}/`, { waitUntil: "networkidle" }),
      reference.goto(`${baseUrl}/__reference__/?step=${referenceStep}`, { waitUntil: "networkidle" }),
    ]);
    const facts = await pageFacts(page);
    const normalized = normalize(facts.text);
    for (const text of spec.required_text || []) {
      criterion(criteria, `text:${text}`, "semantics", normalized.includes(normalize(text)), { expected: text });
    }
    if (spec.primary_heading) {
      const count = await visiblePrimaryHeadingCount(page, spec.primary_heading);
      criterion(criteria, `primary-heading:${spec.primary_heading}`, "semantics", count === 1, {
        count,
        expected: spec.primary_heading,
        level: 1,
      });
    }
    for (const text of spec.forbidden_text || []) {
      criterion(criteria, `forbidden:${text}`, "semantics", !normalized.includes(normalize(text)), { forbidden: text });
    }
    for (const name of spec.required_buttons || []) {
      const count = await page.getByRole("button", { name, exact: true }).count();
      criterion(criteria, `button:${name}`, "interaction", count === 1, { count, expected: name });
    }
    for (const text of spec.unique_text || []) {
      const count = await visibleExactTextCount(page, text);
      criterion(criteria, `unique-text:${text}`, "semantics", count === 1, { count, expected: 1 });
    }
    for (const resource of spec.resources || []) {
      const textCount = await visibleExactTextCount(page, resource.text);
      const hook = page.locator(`[data-eval-id="${resource.identity}"]`);
      const hookCount = await hook.count();
      const expected = resource.count || 1;
      criterion(
        criteria,
        `resource:${resource.identity}`,
        "semantics",
        textCount === expected && (hookCount === 0 || hookCount === expected),
        { text: resource.text, text_count: textCount, hook_count: hookCount, expected },
      );
    }
    if ((spec.text_order || []).length) {
      const indexes = spec.text_order.map((text) => normalized.indexOf(normalize(text)));
      const ordered = indexes.every((value, index) => value >= 0 && (index === 0 || value > indexes[index - 1]));
      criterion(criteria, "text-order", "semantics", ordered, { expected: spec.text_order, indexes });
    }
    for (const contract of spec.text_geometry || []) {
      if (contract.viewport_width && contract.viewport_width !== viewport.width) continue;
      const geometry = await textGeometry(page, contract);
      const textMatches = geometry !== null && geometry.text_matches;
      const enoughLines = geometry !== null && geometry.line_count >= (contract.minimum_lines || 1);
      const boundedLines = geometry !== null && (
        !contract.maximum_lines || geometry.line_count <= contract.maximum_lines
      );
      const wideEnough = geometry !== null && (
        !contract.minimum_width_px || geometry.client_width >= contract.minimum_width_px
      );
      const wholeWords = geometry !== null && (
        !contract.no_word_fragmentation || geometry.fragmented_words.length === 0
      );
      const unclipped = geometry !== null && (!contract.no_clip || !geometry.clipped);
      criterion(
        criteria,
        `text-geometry:${contract.text}`,
        "responsive",
        textMatches && enoughLines && boundedLines && wideEnough && wholeWords && unclipped,
        { expected: contract, observed: geometry },
      );
    }
    criterion(criteria, "no-horizontal-overflow", "responsive", facts.overflow <= 1, { overflow_px: facts.overflow });
    criterion(criteria, "main-landmark", "accessibility", facts.mainCount === 1, { count: facts.mainCount });
    criterion(criteria, "heading-order", "accessibility", facts.headingOrder);
    criterion(criteria, "button-names", "accessibility", facts.buttonNamesComplete);
    const interactions = spec.interactions || (spec.click ? [spec.click] : []);
    for (const interaction of interactions) {
      await page.goto(`${baseUrl}/`, { waitUntil: "networkidle" });
      const target = page.getByRole("button", { name: interaction.button, exact: true });
      let passedInteraction = false;
      let observedAssertions = [];
      if ((await target.count()) === 1) {
        await target.click();
        const assertions = interaction.assertions || [{ kind: "visible_text", text: interaction.reveals }];
        observedAssertions = [];
        for (const assertion of assertions) {
          if (assertion.kind !== "visible_text") throw new Error(`Unsupported interaction assertion: ${assertion.kind}`);
          const visibleCount = await visibleNormalizedTextCount(page, assertion.text);
          observedAssertions.push({ ...assertion, visible_count: visibleCount, passed: visibleCount >= 1 });
        }
        passedInteraction = observedAssertions.every((assertion) => assertion.passed);
      }
      const criterionId = spec.click
        ? "click-reveals"
        : `interaction:${interaction.button}`;
      criterion(criteria, criterionId, "interaction", passedInteraction, {
        ...interaction,
        observed_assertions: observedAssertions,
      });
    }
    if (interactions.length) {
      await page.goto(`${baseUrl}/`, { waitUntil: "networkidle" });
    }
    const similarities = [];
    for (const text of spec.visual_anchors || []) {
      const [candidateAnchor, referenceAnchor] = await Promise.all([anchor(page, text), anchor(reference, text)]);
      if (!referenceAnchor) continue;
      const similarity = anchorSimilarity(candidateAnchor, referenceAnchor);
      similarities.push(similarity);
      criterion(criteria, `anchor:${text}`, "layout_fidelity", similarity >= 0.45, {
        similarity,
        candidate_anchor: candidateAnchor,
        reference_anchor: referenceAnchor,
      });
    }
    const screenshot = resolve(evidenceDir, `${viewport.width}x${viewport.height}.png`);
    await page.screenshot({ path: screenshot, fullPage: true });
    const screenshotBytes = await readFile(screenshot);
    const navigation = await page.evaluate(() => {
      const entry = performance.getEntriesByType("navigation")[0];
      if (!entry) return null;
      return {
        dom_content_loaded_ms: Math.round(entry.domContentLoadedEventEnd),
        load_ms: Math.round(entry.loadEventEnd),
        transfer_bytes: entry.transferSize,
      };
    });
    return {
      viewport,
      criteria,
      layout_fidelity: similarities.length ? similarities.reduce((left, right) => left + right, 0) / similarities.length : null,
      screenshot,
      screenshot_bytes: screenshotBytes.byteLength,
      screenshot_sha256: createHash("sha256").update(screenshotBytes).digest("hex"),
      telemetry: { ...runtime, navigation, document: facts },
    };
  } finally {
    await Promise.all([page.close(), reference.close()]);
  }
}

async function main() {
  const args = argumentsMap(process.argv.slice(2));
  for (const required of ["candidate", "reference", "spec", "out", "evidence", "reference-step"]) {
    if (!args[required]) throw new Error(`Missing --${required}`);
  }
  const candidateRoot = resolve(args.candidate);
  const referencePath = resolve(args.reference);
  const referenceRoot = resolve(referencePath, "..");
  const specText = await readFile(resolve(args.spec), "utf8");
  const spec = JSON.parse(specText);
  await mkdir(resolve(args.evidence), { recursive: true });
  const modulePath = process.env.VIEWSPEC_EVAL_PLAYWRIGHT_MODULE || defaultPlaywright;
  const { chromium } = await import(pathToFileURL(modulePath).href);
  const { server, baseUrl } = await startServer(
    candidateRoot,
    referenceRoot,
    args["candidate-entry"] || "index.html",
    referencePath.split(/[\\/]/).at(-1),
  );
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const viewports = [
      { width: 390, height: 844 },
      { width: 768, height: 1024 },
      { width: 1440, height: 1000 },
    ];
    spec.visual_anchors ||= [];
    const results = [];
    for (const viewport of viewports) {
      results.push(await scoreViewport({
        browser,
        baseUrl,
        spec,
        viewport,
        evidenceDir: resolve(args.evidence),
        referenceStep: Number(args["reference-step"]),
      }));
    }
    const allCriteria = results.flatMap((result) => result.criteria);
    const passed = allCriteria.filter((item) => item.passed).length;
    const dimensions = {};
    for (const item of allCriteria) {
      dimensions[item.dimension] ||= { passed: 0, total: 0 };
      dimensions[item.dimension].total += 1;
      if (item.passed) dimensions[item.dimension].passed += 1;
    }
    for (const value of Object.values(dimensions)) value.score = value.total ? value.passed / value.total : null;
    const report = {
      schema_version: 1,
      ok: passed === allCriteria.length,
      passed,
      total: allCriteria.length,
      dimensions,
      viewports: results,
      scorer: {
        browser_version: browser.version(),
        score_spec_sha256: createHash("sha256").update(specText).digest("hex"),
      },
    };
    await writeFile(resolve(args.out), `${JSON.stringify(report, null, 2)}\n`, "utf8");
    process.stdout.write(`${JSON.stringify(report)}\n`);
    process.exitCode = report.ok ? 0 : 2;
  } finally {
    try {
      await browser?.close();
    } finally {
      await new Promise((accept) => server.close(accept));
    }
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
