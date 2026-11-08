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
  "[role=radio]",
  "[role=switch]",
  "[role=textbox]",
  "[role=combobox]",
  "[contenteditable='']",
  "[contenteditable=true]",
  "[onclick]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

// Runs inside the page. Walks the composed tree (what the user sees): open shadow roots in place of
// their hosts' light DOM, slots replaced by the nodes assigned to them, and the body of each
// same-origin iframe inside a <div data-lift-frame>. One walk returns:
//
// snapshot     HTML of a copy of that tree, in which attributes carry the live form state (typed
//              values, checked boxes, selected options), the top of each subtree the user cannot see
//              has data-lift-hidden, and an element with a pointer cursor but no semantic control
//              has data-lift-clickable. Password values are never copied. The page is not changed.
// interactive  the visible interactive elements with a short accessible name (the ground truth).
// secrets      the live values of secret fields, so the probe can check that none leaks. The probe
//              keeps them in memory only.
function inspectPage(selector) {
  const SEMANTIC = new Set(["A", "BUTTON", "INPUT", "SELECT", "TEXTAREA", "LABEL", "SUMMARY", "OPTION"]);
  const SKIP = new Set(["SCRIPT", "STYLE", "TEMPLATE", "NOSCRIPT", "LINK", "META"]);
  const NO_BOX = new Set(["OPTION", "OPTGROUP", "HEAD", "TITLE", "BR", "WBR"]);
  const SECRET_AUTOCOMPLETE = /(^|\s)(current-password|new-password|one-time-code|cc-number|cc-csc|cc-exp(-month|-year)?)(\s|$)/i;
  const SECRET_NAME = /pass(word|wd|code)?|secret|token|otp|cvv|cvc|ssn|card.?num|cc.?num|security.?code/i;
  const out = document.implementation.createHTMLDocument("");
  const interactive = [];
  const secrets = [];
  let actionIds = 0;

  const clean = (value) => (value || "").replace(/\s+/g, " ").trim().slice(0, 80);
  const byId = (el, id) => {
    const root = el.getRootNode();
    return (root.getElementById ? root.getElementById(id) : null) || el.ownerDocument.getElementById(id);
  };
  const nameOf = (el) => {
    if (el.getAttribute("aria-label")) return clean(el.getAttribute("aria-label"));
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const text = labelledBy
        .split(/\s+/)
        .map((id) => byId(el, id)?.textContent || "")
        .join(" ");
      if (clean(text)) return clean(text);
    }
    if (el.labels && el.labels.length) {
      // A label's text, without the text of the fields inside it (option text, for example).
      const label = el.labels[0].cloneNode(true);
      label.querySelectorAll("select,textarea,option").forEach((child) => child.remove());
      if (clean(label.textContent)) return clean(label.textContent);
    }
    const tag = el.tagName;
    if (tag === "INPUT" && /^(submit|reset|button|image)$/i.test(el.type)) {
      return clean(el.value || el.getAttribute("alt") || (el.type === "reset" ? "Reset" : "Submit"));
    }
    if (tag !== "INPUT" && tag !== "SELECT" && tag !== "TEXTAREA" && clean(el.innerText)) return clean(el.innerText);
    return clean(el.getAttribute("title") || el.getAttribute("placeholder") || el.querySelector?.("img")?.alt);
  };
  const kindOf = (el) => {
    const role = el.getAttribute("role");
    if (role) return role;
    const tag = el.tagName.toLowerCase();
    if (tag === "input") return `input:${(el.getAttribute("type") || "text").toLowerCase()}`;
    if (tag === "a") return "link";
    return tag;
  };
  const isSecret = (el) =>
    (el.tagName === "INPUT" || el.tagName === "TEXTAREA") &&
    ((el.getAttribute("type") || "").toLowerCase() === "password" ||
      SECRET_AUTOCOMPLETE.test(el.getAttribute("autocomplete") || "") ||
      SECRET_NAME.test(el.getAttribute("name") || "") ||
      SECRET_NAME.test(el.id || ""));

  // The composed children of a node, in rendering order.
  const composedChildren = (node) => {
    if (node.nodeType === 1) {
      if (node.shadowRoot) return [...node.shadowRoot.childNodes];
      if (node.tagName === "SLOT") {
        const assigned = node.assignedNodes({ flatten: true });
        return assigned.length ? assigned : [...node.childNodes];
      }
      if (node.tagName === "IFRAME") {
        try {
          const body = node.contentDocument && node.contentDocument.body;
          return body ? [body] : [];
        } catch (error) {
          return []; // A cross-origin frame cannot be read.
        }
      }
    }
    return [...node.childNodes];
  };

  // Copy one live node (and its composed subtree). Returns a list, because a <slot> copies as its
  // assigned nodes rather than as an element.
  const copy = (node, state) => {
    if (node.nodeType === 3) return [out.createTextNode(node.data)];
    if (node.nodeType !== 1 || SKIP.has(node.tagName)) return [];
    const el = node;
    const tag = el.tagName;
    if (tag === "SLOT") return composedChildren(el).flatMap((child) => copy(child, state));

    const view = el.ownerDocument.defaultView;
    const style = view.getComputedStyle(el);
    let result;
    if (tag === "IFRAME") {
      result = out.createElement("div");
      result.setAttribute("data-lift-frame", el.getAttribute("src") || "srcdoc");
    } else if (tag === "BODY" && el.ownerDocument !== document) {
      result = out.createElement("div"); // the body of a frame
    } else {
      result = out.createElement(tag.toLowerCase());
      for (const attr of el.attributes) result.setAttribute(attr.name, attr.value);
    }

    // Live form state.
    if (tag === "INPUT") {
      const type = (el.getAttribute("type") || "text").toLowerCase();
      if (type === "checkbox" || type === "radio") {
        el.checked ? result.setAttribute("checked", "") : result.removeAttribute("checked");
      } else if (isSecret(el)) {
        if (el.value) secrets.push(el.value);
        result.removeAttribute("value");
      } else if (type !== "file") {
        result.setAttribute("value", el.value);
      }
    } else if (tag === "OPTION") {
      el.selected ? result.setAttribute("selected", "") : result.removeAttribute("selected");
    }
    if (el.hasAttribute("data-action-id")) actionIds += 1;

    // Visibility and pointer hints.
    let hidden = state.hidden;
    let clickable = state.clickable;
    if (!NO_BOX.has(tag) && !hidden) {
      const invisible =
        typeof el.checkVisibility === "function"
          ? !el.checkVisibility({ visibilityProperty: true }) && style.display !== "contents"
          : el.getClientRects().length === 0;
      if (invisible && tag !== "HTML" && tag !== "BODY") {
        hidden = true;
        result.setAttribute("data-lift-hidden", "true");
      }
    }
    if (!hidden && style.cursor === "pointer") {
      const semantic = SEMANTIC.has(tag) || el.closest("a,button,label,summary,[role]") !== null;
      if (!clickable && !semantic) result.setAttribute("data-lift-clickable", "true");
      clickable = true;
    }

    // Ground truth: visible interactive elements, not counting one inside another.
    let inside = state.inside;
    if (!hidden && !inside && el.matches(selector)) {
      const box = el.getBoundingClientRect();
      if (box.width > 0 && box.height > 0) {
        interactive.push({ tag: tag.toLowerCase(), kind: kindOf(el), name: nameOf(el) });
        inside = true;
      }
    }

    if (tag === "TEXTAREA") {
      if (isSecret(el)) {
        if (el.value) secrets.push(el.value);
      } else {
        result.textContent = el.value;
      }
      return [result];
    }
    const next = { hidden, clickable, inside };
    for (const child of composedChildren(el)) {
      for (const copied of copy(child, next)) result.appendChild(copied);
    }
    return [result];
  };

  const [root] = copy(document.documentElement, { hidden: false, clickable: false, inside: false });
  return { snapshot: "<!doctype html>" + root.outerHTML, interactive, secrets, actionIds };
}

async function inspectLoaded(page) {
  await page.waitForLoadState("networkidle", { timeout: 10000 }).catch(() => {});
  return page.evaluate(inspectPage, INTERACTIVE_SELECTOR);
}

async function renderOne(browser, target) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  const result = { target, ok: true };
  try {
    const response = await page.goto(target, { waitUntil: "load", timeout: 30000 });
    result.static_html = response ? await response.text() : "";
    const first = await inspectLoaded(page);
    result.rendered_html = first.snapshot;
    result.interactive = first.interactive;
    result.secrets = first.secrets;
    result.viewspec_action_ids = first.actionIds;
    result.aria_snapshot = await page.locator("body").ariaSnapshot();
    // A second load shows whether the page shape is stable from one visit to the next.
    await page.reload({ waitUntil: "load", timeout: 30000 });
    result.rendered_html_reload = (await inspectLoaded(page)).snapshot;
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
