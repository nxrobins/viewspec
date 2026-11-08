// A Playwright browser session driven over stdio, one JSON object per line.
//
//   request:  {"id": 1, "cmd": "open", "url": "https://example.com", "block_writes": true}
//   response: {"id": 1, "ok": true, "result": {...}}  or  {"id": 1, "ok": false, "error": "..."}
//
// Commands:
//   open      {url, block_writes, allowed_origins}
//                                          load a page; returns its status and server HTML
//   snapshot  {collect_secrets}            the composed-tree snapshot (see lift_inpage.js)
//   aria      {}                           Playwright's ARIA snapshot of the body
//   reload    {}                           reload the page
//   locate    {requests: [{key, ref, role, name}]}
//                                          a verified locator for each ref (see below)
//   act       {op, ref, locator, value, values}
//                                          run one planned step (see below)
//   writes    {}                           the writes and navigations that were stopped
//   document  {}                           the id of the current document (see lift_inpage.js)
//   wait_change {timeout_ms}               wait until the DOM changes or the page navigates
//   close     {}
//
// With block_writes, every request that is not GET, HEAD, or OPTIONS (a form post, a fetch that
// changes data, a beacon) is stopped, recorded, and answered with a small page that says so. An
// agent can then fill in and submit a real form without changing anything outside the browser.
//
// With allowed_origins (for example ["https://shop.example"]), a top-level navigation to any other
// origin is stopped and recorded, so a click cannot take the agent off the sites it may use.

const fs = require("fs");
const path = require("path");
const readline = require("readline");
const { execSync } = require("child_process");

// MCP hosts often start servers with a small environment (no NODE_PATH), so look for Playwright
// next to this file, then on NODE_PATH, then in npm's global folder.
function loadPlaywright() {
  try {
    return require("playwright");
  } catch (error) {
    // Not installed locally or on NODE_PATH.
  }
  try {
    const root = execSync("npm root -g", { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
    return require(path.join(root, "playwright"));
  } catch (error) {
    process.stderr.write(
      "Playwright for Node was not found. Run `npm install playwright` in " +
        `${__dirname}, or set NODE_PATH to the folder that holds it.\n`,
    );
    process.exit(2);
  }
}

const { chromium } = loadPlaywright();

const LIBRARY = fs.readFileSync(path.join(__dirname, "lift_inpage.js"), "utf8");
const INTERACTIVE_SELECTOR = [
  "a[href]",
  "button",
  "input:not([type=hidden])",
  "select",
  "textarea",
  "summary",
  "[role=button]",
  "[role=link]",
  "[role=tab]",
  "[role=menuitem]",
  "[role=checkbox]",
  "[role=radio]",
  "[role=switch]",
  "[role=textbox]",
  "[role=combobox]",
  "[contenteditable='']",
  "[contenteditable=true]",
  "[onclick]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");
const ACTION_TIMEOUT = 5000;
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

let browser = null;
let page = null;
let writes = [];
let navigations = [];
let mainNavigations = 0;

const escapeHtml = (text) =>
  String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function originOf(url) {
  try {
    return new URL(url).origin;
  } catch (error) {
    return "null";
  }
}

async function ensureLibrary() {
  await page.evaluate(LIBRARY);
}

async function launch() {
  try {
    return await chromium.launch();
  } catch (error) {
    throw new Error(
      "Chromium did not start. Set PLAYWRIGHT_BROWSERS_PATH to the folder with Playwright's browsers, " +
        "or run `npx playwright install chromium`. " +
        String(error.message || error).split("\n")[0],
    );
  }
}

async function open({ url, block_writes: blockWrites = true, allowed_origins: allowedOrigins = null }) {
  if (!browser) browser = await launch();
  if (page) await page.close();
  page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  writes = [];
  navigations = [];
  page.on("framenavigated", (frame) => {
    if (frame === page.mainFrame()) mainNavigations += 1;
  });
  const allowed = allowedOrigins && allowedOrigins.length ? new Set(allowedOrigins) : null;
  if (blockWrites || allowed) {
    await page.route("**/*", async (route) => {
      const request = route.request();
      const topLevel = request.isNavigationRequest() && request.frame() === page.mainFrame();
      if (allowed && topLevel && !allowed.has(originOf(request.url()))) {
        navigations.push({ url: request.url(), reason: "origin not allowed" });
        // A 204 answer to a navigation leaves the page where it is. Aborting it instead would make
        // Chromium show an error page, and the agent would lose the page it was on.
        return route.fulfill({ status: 204, body: "" });
      }
      if (!blockWrites || SAFE_METHODS.has(request.method())) return route.continue();
      writes.push({
        method: request.method(),
        url: request.url(),
        resource_type: request.resourceType(),
        post_data: request.postData(),
      });
      const body =
        "<!doctype html><title>Blocked</title><h1>Write blocked</h1>" +
        `<p>${escapeHtml(request.method())} ${escapeHtml(request.url())}</p>`;
      return route.fulfill({ status: 200, contentType: "text/html", body });
    });
  }
  const response = await page.goto(url, { waitUntil: "load", timeout: 30000 });
  await settle();
  return { url: page.url(), status: response ? response.status() : null, static_html: response ? await response.text() : "" };
}

async function settle() {
  await page.waitForLoadState("load", { timeout: 10000 }).catch(() => {});
  await page.waitForLoadState("networkidle", { timeout: 3000 }).catch(() => {});
}

async function snapshot({ collect_secrets: collectSecrets = false }) {
  await ensureLibrary();
  const result = await page.evaluate(
    ([selector, secrets]) => window.__viewspecLift.snapshot(selector, secrets),
    [INTERACTIVE_SELECTOR, collectSecrets],
  );
  return { url: page.url(), title: await page.title(), ...result };
}

// ---- Locators -----------------------------------------------------------------------------------
// A locator is verified when Playwright finds exactly one element for it and that element is the
// one the ref names. CSS candidates come first (best first); a role-and-name locator is the fallback.

async function sameElement(locator, ref) {
  try {
    return await locator.evaluate((el, expected) => window.top.__viewspecLift.refOf(el) === expected, ref, {
      timeout: 1000,
    });
  } catch (error) {
    return false;
  }
}

async function uniqueAndSame(locator, ref) {
  try {
    return (await locator.count()) === 1 && (await sameElement(locator, ref));
  } catch (error) {
    return false; // A selector Playwright cannot parse.
  }
}

function playwrightCode(frames, { css, role, name, text }) {
  let code = "page";
  for (const frame of frames) code += `.frameLocator(${JSON.stringify(frame)})`;
  if (css) return `${code}.locator(${JSON.stringify(css)})`;
  if (role) return `${code}.getByRole(${JSON.stringify(role)}, { name: ${JSON.stringify(name)}, exact: true })`;
  return `${code}.getByText(${JSON.stringify(text)}, { exact: true })`;
}

function buildLocator(spec) {
  let scope = page;
  for (const frame of spec.frames || []) scope = scope.frameLocator(frame);
  if (spec.css) return scope.locator(spec.css);
  if (spec.role) return scope.getByRole(spec.role, { name: spec.name, exact: true });
  if (spec.text) return scope.getByText(spec.text, { exact: true });
  return null;
}

async function locateOne(request, frameCache) {
  const info = await page.evaluate((ref) => window.__viewspecLift.describe(ref), request.ref);
  if (!info.connected) return { strategy: null, reason: "not in the page" };
  // The frame chain: one verified selector per iframe, outermost first.
  const frames = [];
  let scope = page;
  for (const frame of info.frames) {
    let chosen = frameCache.get(frame.ref);
    if (chosen === undefined) {
      chosen = null;
      for (const candidate of frame.candidates) {
        if (candidate.unique === false) continue;
        if (await uniqueAndSame(scope.locator(candidate.css), frame.ref)) {
          chosen = candidate.css;
          break;
        }
      }
      frameCache.set(frame.ref, chosen);
    }
    if (chosen === null) return { strategy: null, reason: "frame has no unique selector" };
    frames.push(chosen);
    scope = scope.frameLocator(chosen);
  }
  // Stable attributes and content anchors first, then role and name, then a structural path.
  const tryCss = async (kinds) => {
    for (const candidate of info.candidates) {
      if (candidate.unique === false || !kinds.includes(candidate.kind)) continue;
      if (await uniqueAndSame(scope.locator(candidate.css), request.ref)) {
        return { strategy: "css", kind: candidate.kind, frames, css: candidate.css, playwright: playwrightCode(frames, { css: candidate.css }) };
      }
    }
    return null;
  };
  const byAttribute = await tryCss(["attr", "anchored"]);
  if (byAttribute) return byAttribute;
  if (request.role && request.name) {
    const locator = scope.getByRole(request.role, { name: request.name, exact: true });
    if (await uniqueAndSame(locator, request.ref)) {
      return {
        strategy: "role",
        kind: "role",
        frames,
        role: request.role,
        name: request.name,
        playwright: playwrightCode(frames, { role: request.role, name: request.name }),
      };
    }
  }
  if (request.name) {
    // An element with no role (a clickable <div>) can still be found by its exact visible text.
    const locator = scope.getByText(request.name, { exact: true });
    if (await uniqueAndSame(locator, request.ref)) {
      return {
        strategy: "text",
        kind: "text",
        frames,
        text: request.name,
        playwright: playwrightCode(frames, { text: request.name }),
      };
    }
  }
  const byPath = await tryCss(["path"]);
  if (byPath) return byPath;
  return { strategy: null, reason: "no selector finds exactly this element" };
}

async function locate({ requests }) {
  await ensureLibrary();
  const frameCache = new Map();
  const results = {};
  for (const request of requests) results[request.key] = await locateOne(request, frameCache);
  return results;
}

// ---- Actions ------------------------------------------------------------------------------------
// The exact element (by ref) is used while it is still in the page. If the page replaced it (a
// re-render or a navigation), the saved locator finds its successor, and the tag must still match.

async function resolveTarget(ref, spec) {
  await ensureLibrary();
  if (ref) {
    const handle = await page.evaluateHandle((r) => window.__viewspecLift.get(r), ref);
    const element = handle.asElement();
    if (element) return { target: element, resolvedBy: "ref" };
    await handle.dispose();
  }
  const locator = spec ? buildLocator(spec) : null;
  if (locator && (await locator.count()) === 1) {
    // The page replaced the element. Its locator found one element; act on it only if it is still
    // the same kind of control with the same name, so a changed page cannot redirect the action.
    const found = await locator.evaluate((el) => ({
      tag: el.tagName.toLowerCase(),
      name: window.top.__viewspecLift.nameOf(el),
    }));
    const normal = (text) => (text || "").toLowerCase().replace(/\s+/g, " ").trim();
    if (spec.tag && found.tag !== spec.tag) {
      throw new Error(`drift: the locator now finds a <${found.tag}>, not a <${spec.tag}>`);
    }
    if (spec.expect_name && normal(found.name) !== normal(spec.expect_name)) {
      throw new Error(`drift: the locator now finds ${JSON.stringify(found.name)}, not ${JSON.stringify(spec.expect_name)}`);
    }
    return { target: locator, resolvedBy: spec.strategy };
  }
  throw new Error(`stale: neither ref ${ref} nor its locator finds the element`);
}

async function act({ op, ref, locator: spec, value, values }) {
  const { target, resolvedBy } = await resolveTarget(ref, spec);
  const navigationsBefore = mainNavigations;
  const urlBefore = page.url();
  let verified = null;
  const options = { timeout: ACTION_TIMEOUT };
  if (op === "click") {
    await target.click(options);
  } else if (op === "fill") {
    await target.fill(value, options);
    verified = await target.evaluate((el, v) => window.top.__viewspecLift.hasValue(el, v), value);
  } else if (op === "check" || op === "uncheck") {
    await (op === "check" ? target.check(options) : target.uncheck(options));
    verified = (await target.evaluate((el) => window.top.__viewspecLift.isChecked(el))) === (op === "check");
  } else if (op === "select") {
    await target.selectOption(values, options);
    verified = await target.evaluate((el, v) => window.top.__viewspecLift.hasSelected(el, v), values);
  } else if (op === "submit") {
    await target.evaluate((form) => form.requestSubmit());
  } else {
    throw new Error(`unknown op ${op}`);
  }
  await settle();
  return {
    resolved_by: resolvedBy,
    verified,
    navigated: mainNavigations !== navigationsBefore,
    url_before: urlBefore,
    url: page.url(),
  };
}

// Wait for the next change: a DOM change anywhere in the composed tree, or a navigation. Returns
// { reason: "mutation" | "navigation" | "timeout" }. The caller reads the page again and decides
// whether the change is the one it waits for.
async function waitChange({ timeout_ms: timeoutMs = 5000 }) {
  const limit = Math.max(0, Math.min(Number(timeoutMs) || 0, 30000));
  await ensureLibrary();
  const navigation = page
    .waitForEvent("framenavigated", { predicate: (frame) => frame === page.mainFrame(), timeout: limit + 50 })
    .then(() => "navigation")
    .catch(() => "timeout");
  const mutation = page
    .evaluate((ms) => window.__viewspecLift.nextChange(ms), limit)
    .then((changed) => (changed ? "mutation" : "timeout"))
    .catch(() => "navigation"); // The page went away while it was being watched.
  const reason = await Promise.race([navigation, mutation]);
  if (reason === "navigation") await settle();
  return { reason };
}

const COMMANDS = {
  wait_change: waitChange,
  open,
  snapshot,
  locate,
  act,
  aria: async () => ({ aria: await page.locator("body").ariaSnapshot() }),
  reload: async () => {
    await page.reload({ waitUntil: "load", timeout: 30000 });
    await settle();
    return { url: page.url() };
  },
  writes: async () => ({ writes, navigations }),
  document: async () => {
    await ensureLibrary();
    return { doc_id: await page.evaluate(() => window.__viewspecLift.docId), url: page.url() };
  },
  close: async () => {
    if (browser) await browser.close();
    browser = null;
    page = null;
    return {};
  },
};

const lines = readline.createInterface({ input: process.stdin });
let queue = Promise.resolve();
lines.on("line", (line) => {
  queue = queue.then(async () => {
    let request = {};
    try {
      request = JSON.parse(line);
      const command = COMMANDS[request.cmd];
      if (!command) throw new Error(`unknown command ${request.cmd}`);
      if (request.cmd !== "open" && request.cmd !== "close" && !page) throw new Error("no page is open");
      const result = await command(request);
      process.stdout.write(JSON.stringify({ id: request.id, ok: true, result }) + "\n");
      if (request.cmd === "close") process.exit(0);
    } catch (error) {
      const message = String(error && error.message ? error.message : error).split("\n")[0];
      process.stdout.write(JSON.stringify({ id: request.id, ok: false, error: message }) + "\n");
    }
  });
});
lines.on("close", async () => {
  await queue;
  if (browser) await browser.close();
  process.exit(0);
});
