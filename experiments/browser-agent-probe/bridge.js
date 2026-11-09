// A Playwright browser session driven over stdio, one JSON object per line.
//
//   request:  {"id": 1, "cmd": "open", "url": "https://example.com", "block_writes": true}
//   response: {"id": 1, "ok": true, "result": {...}}  or  {"id": 1, "ok": false, "error": "..."}
//
// Commands:
//   open      {url, block_writes, allowed_origins, download_dir}
//                                          load a page; returns its status and server HTML
//   snapshot  {collect_secrets}            the composed-tree snapshot (see lift_inpage.js)
//   aria      {}                           Playwright's ARIA snapshot of the body
//   reload    {}                           reload the page
//   locate    {requests: [{key, ref, role, name}]}
//                                          a verified locator for each ref (see below)
//   act       {op, ref, locator, end_ref, end_locator, value, values, files}
//                                          run one planned step (see below)
//   writes    {}                           the writes and navigations that were stopped
//   document  {}                           the id of the current document (see lift_inpage.js)
//   wait_change {timeout_ms}               wait until the DOM changes or the page navigates
//   confirm   {items: [{key, ref, locator}]}
//                                          ask Playwright whether each locator finds exactly the
//                                          element its ref names (the probe checks page-checked ones)
//   close     {}
//
// With block_writes, every request that is not GET, HEAD, or OPTIONS (a form post, a fetch that
// changes data, a beacon) is stopped, recorded, and answered with a small page that says so. An
// agent can then fill in and submit a real form without changing anything outside the browser.
//
// With allowed_origins (for example ["https://shop.example"]), a top-level navigation to any other
// origin is stopped and recorded, so a click cannot take the agent off the sites it may use.
//
// With download_dir, a "download" step saves the file a click downloads in that folder, under a
// safe name the page cannot turn into a path, and never over another file. Without it, nothing is
// saved. A download that any other step starts is not saved either; the step reports its name.

const crypto = require("crypto");
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
const DOWNLOAD_START_TIMEOUT = 4000;
const MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024;
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

let browser = null;
let page = null;
let writes = [];
let navigations = [];
let mainNavigations = 0;
let downloadDir = null;
let expectingDownload = false;
let strayDownloads = [];
let navigationRequests = 0;

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

async function open({ url, block_writes: blockWrites = true, allowed_origins: allowedOrigins = null, download_dir: dir = null }) {
  if (!browser) browser = await launch();
  if (page) await page.close();
  page = await browser.newPage({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
  writes = [];
  navigations = [];
  downloadDir = dir ? path.resolve(dir) : null;
  strayDownloads = [];
  page.on("download", (item) => {
    if (!expectingDownload) strayDownloads.push(item);
  });
  page.on("request", (request) => {
    if (request.isNavigationRequest() && request.frame() === page.mainFrame()) navigationRequests += 1;
  });
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
      const buffer = request.postDataBuffer();
      writes.push({
        method: request.method(),
        url: request.url(),
        resource_type: request.resourceType(),
        content_type: request.headers()["content-type"] || null,
        post_data: request.postData(),
        post_data_b64: buffer ? buffer.toString("base64") : null,
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

async function locateOne(request, frameCache, info) {
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

// Most locators are checked in the page, in one call: a stable attribute or a content anchor that
// matches only this element there. Playwright checks the rest (role and name, exact text, a path
// across a shadow root), one by one. A locator is used only when a ref has gone stale, and then
// Playwright must find exactly one element with the same tag and name, so a page-checked locator
// that Playwright reads differently fails safely.
async function locate({ requests }) {
  await ensureLibrary();
  const infos = await page.evaluate(
    (refs) => window.__viewspecLift.locateAll(refs),
    requests.map((request) => request.ref),
  );
  const frameCache = new Map();
  const results = {};
  for (const request of requests) {
    const info = infos[request.ref];
    if (info && info.chosen) {
      const { css, kind, frames } = info.chosen;
      results[request.key] = { strategy: "css", kind, frames, css, checked: "page", playwright: playwrightCode(frames, { css }) };
    } else {
      results[request.key] = await locateOne(request, frameCache, info || { connected: false });
    }
  }
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

// Choose the option named ``value`` from a custom combobox or listbox. If the option is not in the
// list yet, open the list: type the value into an editable combobox (most filter as you type), or
// click the control. Then wait for the option, waking on DOM changes, and click the one match.
async function choose(target, value) {
  const ref = await target.evaluate((el) => window.top.__viewspecLift.refOf(el));
  const find = () => page.evaluate(([r, v]) => window.__viewspecLift.findOption(r, v), [ref, value]);
  const deadline = Date.now() + ACTION_TIMEOUT;
  let found = await find();
  let typedOver = null; // The field's text before this step typed into it.
  if (found.state === "missing") {
    const editable = await target.evaluate(
      (el) => el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable,
    );
    if (editable) {
      typedOver = await target.evaluate((el) => ("value" in el ? el.value : el.textContent));
      await target.fill(value, { timeout: ACTION_TIMEOUT });
    } else {
      await target.click({ timeout: ACTION_TIMEOUT });
    }
    found = await find();
    while (found.state === "missing" && Date.now() < deadline) {
      const left = Math.max(0, deadline - Date.now());
      await page.evaluate((ms) => window.__viewspecLift.nextChange(ms), left).catch(() => false);
      found = await find();
    }
  }
  if (found.state !== "found") {
    // Leave the control as it was: put back the text this step typed over, and close the list.
    let undo = "";
    if (typedOver !== null) {
      await target.fill(typedOver, { timeout: ACTION_TIMEOUT }).catch(() => {});
      undo = "; the typed text was removed";
    }
    await target.press("Escape", { timeout: ACTION_TIMEOUT }).catch(() => {});
    if (found.state === "ambiguous") {
      const names = found.names.slice(0, 10).join(", ");
      throw new Error(`choose: more than one option matches ${JSON.stringify(value)}: ${names}${undo}`);
    }
    const names = (found.names || []).slice(0, 10);
    const seen = names.length ? `; the list shows: ${names.join(", ")}` : found.open ? "; the list is empty" : "; no list opened";
    throw new Error(`choose: no option named ${JSON.stringify(value)}${seen}${undo}`);
  }
  const option = (await page.evaluateHandle((r) => window.__viewspecLift.get(r), found.ref)).asElement();
  await option.click({ timeout: ACTION_TIMEOUT });
  await settle();
  const verified = await page.evaluate(
    ([r, o, label]) => window.__viewspecLift.showsChoice(r, o, label),
    [ref, found.ref, found.label],
  );
  return { verified, chosen: found.label };
}

// Scroll once, then wait (up to waitMs) for the page to answer: an infinite list loads more items
// after a scroll, often after a server call. ``loaded`` says whether the area grew.
async function scroll(ref, direction, waitMs) {
  await ensureLibrary();
  const first = await page.evaluate(([r, d]) => window.__viewspecLift.scrollStep(r, d), [ref, direction]);
  if (first.stale) throw new Error(`stale: the control ${ref} is no longer in the page`);
  const changed = await page
    .evaluate((ms) => window.__viewspecLift.nextChange(ms), Math.max(0, Math.min(waitMs, 30000)))
    .catch(() => true);
  await settle();
  const now = (await page.evaluate((r) => window.__viewspecLift.scrollNow(r), first.area)) || first.after;
  return {
    page: first.page,
    moved: first.after.top !== first.before.top,
    changed,
    loaded: now.height > first.before.height,
    at_end: now.atEnd,
    at_start: now.atStart,
    top: now.top,
    height: now.height,
  };
}

// Pick the date ``iso`` (YYYY-MM-DD) from the calendar of a custom date field: open the calendar (by
// a click on the field, or on a calendar button beside it), go to the right month (with the
// calendar's month and year lists, or its next and previous buttons), and click the day.
async function chooseDate(target, iso) {
  const ref = await target.evaluate((el) => window.top.__viewspecLift.refOf(el));
  const read = () => page.evaluate(([r, d]) => window.__viewspecLift.readCalendar(r, d), [ref, iso]);
  const changed = (ms = 1500) => page.evaluate((t) => window.__viewspecLift.nextChange(t), ms).catch(() => true);
  const clickRef = async (r) => {
    const handle = (await page.evaluateHandle((x) => window.__viewspecLift.get(x), r)).asElement();
    await handle.click({ timeout: ACTION_TIMEOUT });
  };
  let info = await read();
  let openedBy = null; // What this step clicked to open the calendar, to close it again on failure.
  if (info.state === "closed") {
    await target.click({ timeout: ACTION_TIMEOUT });
    await changed();
    info = await read();
    if (info.state === "open") openedBy = "field";
  }
  if (info.state === "closed") {
    const opener = await page.evaluate((r) => window.__viewspecLift.calendarOpener(r), ref);
    if (opener) {
      await clickRef(opener);
      await changed();
      info = await read();
      if (info.state === "open") openedBy = opener;
    }
  }
  if (info.state !== "open") throw new Error(`choose_date: no calendar opened for ${iso}`);
  try {
    return await pickDay(info, read, changed, clickRef, target, ref, iso);
  } catch (error) {
    // Leave the page as it was: close the calendar this step opened, so another field's calendar
    // is not mistaken for it later.
    await target.press("Escape", { timeout: ACTION_TIMEOUT }).catch(() => {});
    if (openedBy && openedBy !== "field" && (await read()).state === "open") await clickRef(openedBy).catch(() => {});
    throw error;
  }
}

async function pickDay(info, read, changed, clickRef, target, ref, iso) {
  const [year, month] = iso.split("-").map(Number);
  let usedLists = false;
  for (let step = 0; step < 240 && !info.target; step++) {
    if (info.monthList && info.yearList && !usedLists) {
      if (!info.yearList.years.includes(year)) throw new Error(`choose_date: the calendar offers no year ${year}`);
      const yearHandle = (await page.evaluateHandle((x) => window.__viewspecLift.get(x), info.yearList.ref)).asElement();
      await yearHandle.selectOption({ label: String(year) });
      const monthHandle = (await page.evaluateHandle((x) => window.__viewspecLift.get(x), info.monthList)).asElement();
      await monthHandle.selectOption({ index: month - 1 });
      usedLists = true;
      await changed();
      info = await read();
      continue;
    }
    if (!info.shown) throw new Error("choose_date: cannot tell which month the calendar shows");
    // A calendar may show several months side by side: from info.shown to info.last.
    const last = info.last || info.shown;
    const ahead = (year - last.y) * 12 + (month - last.m);
    const behind = (year - info.shown.y) * 12 + (month - info.shown.m);
    if (ahead <= 0 && behind >= 0) throw new Error(`choose_date: the calendar shows the month, but no day for ${iso}`);
    const button = ahead > 0 ? info.next : info.prev;
    if (!button) throw new Error(`choose_date: the calendar has no ${ahead > 0 ? "next" : "previous"} month button`);
    const before = info.shown;
    await clickRef(button);
    await changed();
    info = await read();
    if (info.state !== "open" || !info.shown || (info.shown.y === before.y && info.shown.m === before.m)) {
      throw new Error("choose_date: the calendar did not move to another month");
    }
  }
  if (!info.target) throw new Error(`choose_date: could not reach ${iso} in the calendar`);
  if (info.target.disabled) throw new Error(`choose_date: ${iso} is not available in the calendar`);
  await clickRef(info.target.ref);
  await settle();
  const verified = await page.evaluate(([r, d]) => window.__viewspecLift.showsDate(r, d), [ref, iso]);
  return { verified, chosen: iso };
}

// Pick a date range: the start day, then the end day. ``end`` is the field that holds the end of a
// pair, or null for one field that holds the whole range. A range picker keeps its calendar open
// after the first day and waits for the second; otherwise the end field's own calendar is opened.
async function chooseDateRange(target, end, value) {
  const [startIso, endIso] = value.split("/");
  const ref = await target.evaluate((el) => window.top.__viewspecLift.refOf(el));
  const endRef = end ? await end.evaluate((el) => window.top.__viewspecLift.refOf(el)) : ref;
  const endField = end || target;
  const read = () => page.evaluate(([r, d]) => window.__viewspecLift.readCalendar(r, d), [endRef, endIso]);
  const changed = (ms = 1500) => page.evaluate((t) => window.__viewspecLift.nextChange(t), ms).catch(() => true);
  const clickRef = async (r) => {
    const handle = (await page.evaluateHandle((x) => window.__viewspecLift.get(x), r)).asElement();
    await handle.click({ timeout: ACTION_TIMEOUT });
  };
  await chooseDate(target, startIso);
  const info = await read();
  if (info.state === "open") {
    try {
      await pickDay(info, read, changed, clickRef, endField, endRef, endIso);
    } catch (error) {
      await endField.press("Escape", { timeout: ACTION_TIMEOUT }).catch(() => {});
      throw error;
    }
  } else if (end) {
    await chooseDate(end, endIso);
  } else {
    throw new Error(`choose_date_range: the calendar closed after ${startIso}, before the end could be picked`);
  }
  const verified = end
    ? await page.evaluate(([a, b, s, e]) => window.__viewspecLift.showsDate(a, s) && window.__viewspecLift.showsDate(b, e), [ref, endRef, startIso, endIso])
    : await page.evaluate(([r, s, e]) => window.__viewspecLift.showsRange(r, s, e), [ref, startIso, endIso]);
  return { verified, chosen: value };
}

// A file name the page cannot turn into a path: no folders, no control characters, no leading dot.
function safeFileName(suggested) {
  const base = String(suggested || "").split(/[\\/]/).pop() || "";
  const clean = base.replace(/[\u0000-\u001f\u007f<>:"|?*]/g, "_").replace(/^[.\s]+/, "").trim();
  if (!clean) return "download";
  if (clean.length <= 200) return clean;
  const ext = path.extname(clean).slice(0, 20);
  return clean.slice(0, 200 - ext.length) + ext;
}

// A path in the download folder that holds no file yet: "name.pdf", then "name (1).pdf", ...
function freePath(dir, name) {
  const ext = path.extname(name);
  const stem = name.slice(0, name.length - ext.length);
  for (let n = 0; n < 1000; n++) {
    const candidate = path.join(dir, n === 0 ? name : `${stem} (${n})${ext}`);
    if (!path.resolve(candidate).startsWith(dir + path.sep)) break;
    if (!fs.existsSync(candidate)) return candidate;
  }
  throw new Error(`download: no free file name for ${name} in the download folder`);
}

// Click the control, wait for the download it starts, and save it in the download folder.
async function download(target) {
  if (!downloadDir) throw new Error("download: this session has no download folder");
  expectingDownload = true;
  let item;
  try {
    [item] = await Promise.all([
      page.waitForEvent("download", { timeout: DOWNLOAD_START_TIMEOUT }),
      target.click({ timeout: ACTION_TIMEOUT }),
    ]);
  } catch (error) {
    throw new Error("download: no download started when the control was clicked");
  } finally {
    expectingDownload = false;
  }
  const failure = await item.failure();
  if (failure) throw new Error(`download: the download failed (${failure})`);
  const file = freePath(downloadDir, safeFileName(item.suggestedFilename()));
  await item.saveAs(file);
  await item.delete().catch(() => {});
  const bytes = fs.statSync(file).size;
  if (bytes > MAX_DOWNLOAD_BYTES) {
    fs.unlinkSync(file);
    throw new Error(`download: ${path.basename(file)} is larger than 50 MB, so it was not kept`);
  }
  const sha256 = crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
  const name = path.basename(file);
  return {
    verified: true, // Saved, whole, in the download folder.
    chosen: name,
    download: { file: name, path: file, bytes, sha256, suggested: item.suggestedFilename(), url: item.url() },
  };
}

// The downloads that steps other than "download" started: not saved, only named.
async function dropStrayDownloads() {
  const names = [];
  for (const item of strayDownloads) {
    names.push(item.suggestedFilename());
    await item.cancel().catch(() => {});
    await item.delete().catch(() => {});
  }
  strayDownloads = [];
  return names;
}

// Give files to a file input, or to the file chooser that a button opens (its file input is often
// hidden). A chooser's accept and multiple are checked here; a file input's were checked when the
// step was planned. Returns the names the input holds afterwards.
async function upload(target, files) {
  const payload = (files || []).map((f) => ({ name: f.name, mimeType: f.mime_type, buffer: Buffer.from(f.data_b64, "base64") }));
  const isInput = await target.evaluate((el) => el.tagName === "INPUT" && el.type === "file");
  let input = target;
  if (isInput) {
    await target.setInputFiles(payload, { timeout: ACTION_TIMEOUT });
  } else {
    let chooser;
    try {
      [chooser] = await Promise.all([
        page.waitForEvent("filechooser", { timeout: ACTION_TIMEOUT }),
        target.click({ timeout: ACTION_TIMEOUT }),
      ]);
    } catch (error) {
      throw new Error("upload: the control did not open a file chooser");
    }
    input = chooser.element();
    if (!chooser.isMultiple() && payload.length > 1) {
      throw new Error(`upload: the file chooser takes one file, not ${payload.length}`);
    }
    const accept = await input.evaluate((el) => el.getAttribute("accept") || "");
    for (const file of payload) {
      const ok = await page.evaluate(
        ([a, n, t]) => window.__viewspecLift.acceptsFile(a, n, t),
        [accept, file.name, file.mimeType],
      );
      if (!ok) throw new Error(`upload: ${file.name} (${file.mimeType}) is not a type the chooser accepts (${accept})`);
    }
    await chooser.setFiles(payload, { timeout: ACTION_TIMEOUT });
  }
  await settle();
  const names = await input.evaluate((el) => [...(el.files || [])].map((f) => f.name));
  return { verified: JSON.stringify(names) === JSON.stringify(payload.map((f) => f.name)), chosen: names.join(", ") };
}

async function act({ op, ref, locator: spec, end_ref: endRef, end_locator: endSpec, value, values, files, wait_ms: waitMs = 2500 }) {
  if (op === "scroll" && !ref && !spec) {
    const result = await scroll(null, value, waitMs);
    return { resolved_by: "page", verified: null, chosen: null, navigated: false, url: page.url(), scroll: result };
  }
  const { target, resolvedBy } = await resolveTarget(ref, spec);
  const navigationsBefore = mainNavigations;
  const urlBefore = page.url();
  let verified = null;
  let chosen = null;
  let saved = null;
  strayDownloads = [];
  const requestsBefore = navigationRequests;
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
  } else if (op === "choose") {
    ({ verified, chosen } = await choose(target, value));
  } else if (op === "upload") {
    ({ verified, chosen } = await upload(target, files));
  } else if (op === "download") {
    ({ verified, chosen, download: saved } = await download(target));
  } else if (op === "choose_date") {
    ({ verified, chosen } = await chooseDate(target, value));
  } else if (op === "choose_date_range") {
    const end = endRef || endSpec ? (await resolveTarget(endRef, endSpec)).target : null;
    ({ verified, chosen } = await chooseDateRange(target, end, value));
  } else if (op === "scroll") {
    const liveRef = await target.evaluate((el) => window.top.__viewspecLift.refOf(el));
    const result = await scroll(liveRef, value, waitMs);
    return { resolved_by: resolvedBy, verified: null, chosen: null, navigated: false, url: page.url(), scroll: result };
  } else if (op === "submit") {
    await target.evaluate((form) => form.requestSubmit());
  } else {
    throw new Error(`unknown op ${op}`);
  }
  await settle();
  const noPage = navigationRequests !== requestsBefore && mainNavigations === navigationsBefore;
  if (op !== "download" && noPage && !strayDownloads.length) {
    // The step asked for a page that never loaded: often a file that downloads instead. Its
    // download event comes a moment after the page went idle, so wait for it, briefly.
    await page.waitForEvent("download", { timeout: 1500 }).catch(() => {});
  }
  return {
    resolved_by: resolvedBy,
    verified,
    chosen,
    download: saved,
    unsaved_downloads: await dropStrayDownloads(),
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

async function confirm({ items }) {
  await ensureLibrary();
  const results = {};
  for (const item of items) {
    const locator = buildLocator(item.locator);
    results[item.key] = locator ? await uniqueAndSame(locator, item.ref) : false;
  }
  return results;
}

const COMMANDS = {
  confirm,
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
