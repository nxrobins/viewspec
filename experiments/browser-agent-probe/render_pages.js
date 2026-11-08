// Render each target in Chromium and print one JSON object per target to stdout.
//
// Usage: node render_pages.js TARGET [TARGET ...]
// A TARGET is an http(s) URL or a file:// URL. The Python probe calls this script.

const { chromium } = require("playwright");

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
  "[role=switch]",
  "[role=textbox]",
  "[role=combobox]",
  "[contenteditable='']",
  "[contenteditable=true]",
  "[onclick]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

// Runs inside the page. Lists the visible interactive elements with a short accessible name.
function collectInteractive(selector) {
  const clean = (value) => (value || "").replace(/\s+/g, " ").trim().slice(0, 80);
  const nameOf = (el) => {
    if (el.getAttribute("aria-label")) return clean(el.getAttribute("aria-label"));
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const text = labelledBy
        .split(/\s+/)
        .map((id) => document.getElementById(id)?.innerText || "")
        .join(" ");
      if (clean(text)) return clean(text);
    }
    if (el.labels && el.labels.length) return clean(el.labels[0].innerText);
    if (clean(el.innerText)) return clean(el.innerText);
    return clean(el.getAttribute("placeholder") || el.value || el.getAttribute("title") || el.querySelector("img")?.alt);
  };
  const kindOf = (el) => {
    const role = el.getAttribute("role");
    if (role) return role;
    const tag = el.tagName.toLowerCase();
    if (tag === "input") return `input:${(el.getAttribute("type") || "text").toLowerCase()}`;
    if (tag === "a") return "link";
    return tag;
  };
  const visible = (el) => {
    if (typeof el.checkVisibility === "function" && !el.checkVisibility()) return false;
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  };
  const seen = new Set();
  const items = [];
  for (const el of document.querySelectorAll(selector)) {
    // Skip an element that sits inside an interactive element already counted.
    let parent = el.parentElement;
    let nested = false;
    while (parent) {
      if (seen.has(parent)) {
        nested = true;
        break;
      }
      parent = parent.parentElement;
    }
    if (nested || !visible(el)) continue;
    seen.add(el);
    items.push({ tag: el.tagName.toLowerCase(), kind: kindOf(el), name: nameOf(el) });
  }
  return items;
}

async function renderOne(browser, target) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const result = { target, ok: true };
  try {
    const response = await page.goto(target, { waitUntil: "load", timeout: 30000 });
    result.static_html = response ? await response.text() : "";
    await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
    result.rendered_html = await page.evaluate(() => document.documentElement.outerHTML);
    result.aria_snapshot = await page.locator("body").ariaSnapshot();
    result.interactive = await page.evaluate(collectInteractive, INTERACTIVE_SELECTOR);
    result.viewspec_action_ids = await page.evaluate(
      () => document.querySelectorAll("[data-action-id]").length,
    );
    // A second load shows whether the page shape is stable from one visit to the next.
    await page.reload({ waitUntil: "load", timeout: 30000 });
    await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
    result.rendered_html_reload = await page.evaluate(() => document.documentElement.outerHTML);
  } catch (error) {
    result.ok = false;
    result.error = String(error && error.message ? error.message : error);
  } finally {
    await page.close();
  }
  return result;
}

(async () => {
  const targets = process.argv.slice(2);
  const browser = await chromium.launch();
  try {
    for (const target of targets) {
      process.stdout.write(JSON.stringify(await renderOne(browser, target)) + "\n");
    }
  } finally {
    await browser.close();
  }
})();
