// In-page library for the ViewSpec agent lift. The bridge evaluates this file in the top frame of
// each page; it installs window.__viewspecLift once and does nothing on a second evaluation.
//
// It keeps a registry of element refs ("e1", "e2", ...). An element keeps its ref for as long as it
// stays in the page, across snapshots, so an agent can act on exactly the element it read. The
// registry holds weak references and never keeps a removed element alive.
(() => {
  if (window.__viewspecLift) return;

  const SEMANTIC = new Set(["A", "BUTTON", "INPUT", "SELECT", "TEXTAREA", "LABEL", "SUMMARY", "OPTION"]);
  const SKIP = new Set(["SCRIPT", "STYLE", "TEMPLATE", "NOSCRIPT", "LINK", "META"]);
  const NO_BOX = new Set(["OPTION", "OPTGROUP", "HEAD", "TITLE", "BR", "WBR"]);
  const SECRET_AUTOCOMPLETE =
    /(^|\s)(current-password|new-password|one-time-code|cc-number|cc-csc|cc-exp(-month|-year)?)(\s|$)/i;
  const SECRET_NAME = /pass(word|wd|code)?|secret|token|otp|cvv|cvc|ssn|card.?num|cc.?num|security.?code/i;
  const TEST_ID_ATTRS = ["data-testid", "data-test", "data-qa", "data-cy", "data-action-id"];

  // A new document (a real navigation) installs a new library and gets a new id. A hash change or a
  // pushState route change keeps the document, its id, and its refs.
  const docId = Math.random().toString(36).slice(2) + Date.now().toString(36);

  // ---- Refs -------------------------------------------------------------------------------------
  const refByElement = new WeakMap();
  const elementByRef = new Map();
  let nextRef = 1;

  function refOf(el) {
    let ref = refByElement.get(el);
    if (!ref) {
      ref = `e${nextRef++}`;
      refByElement.set(el, ref);
      elementByRef.set(ref, new WeakRef(el));
    }
    return ref;
  }

  function get(ref) {
    const el = elementByRef.get(ref)?.deref();
    return el && el.isConnected ? el : null;
  }

  function prune() {
    for (const [ref, weak] of elementByRef) {
      const el = weak.deref();
      if (!el || !el.isConnected) elementByRef.delete(ref);
    }
  }

  // ---- Names and kinds (the ground truth list) --------------------------------------------------
  const clean = (value) => (value || "").replace(/\s+/g, " ").trim().slice(0, 80);
  const byId = (el, id) => {
    const root = el.getRootNode();
    return (root.getElementById ? root.getElementById(id) : null) || el.ownerDocument.getElementById(id);
  };
  function nameOf(el) {
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
  }
  function kindOf(el) {
    const role = el.getAttribute("role");
    if (role) return role;
    const tag = el.tagName.toLowerCase();
    if (tag === "input") return `input:${(el.getAttribute("type") || "text").toLowerCase()}`;
    if (tag === "a") return "link";
    return tag;
  }
  const isSecret = (el) =>
    (el.tagName === "INPUT" || el.tagName === "TEXTAREA") &&
    ((el.getAttribute("type") || "").toLowerCase() === "password" ||
      SECRET_AUTOCOMPLETE.test(el.getAttribute("autocomplete") || "") ||
      SECRET_NAME.test(el.getAttribute("name") || "") ||
      SECRET_NAME.test(el.id || ""));

  // ---- The composed tree ------------------------------------------------------------------------
  // What the user sees: open shadow roots in place of their hosts' light DOM, slots replaced by the
  // nodes assigned to them, and the body of each same-origin iframe.
  function composedChildren(node) {
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
  }

  // Serialize a copy of the composed tree. In the copy:
  // - every element has data-lift-ref;
  // - attributes carry the live form state (typed values, checked boxes, selected options);
  // - the top of each subtree the user cannot see has data-lift-hidden;
  // - an element with a pointer cursor but no semantic control has data-lift-clickable;
  // - a custom element (a tag with a dash) becomes a <div data-lift-tag>, so its role and tab stop
  //   survive the lift, which unwraps unknown tags;
  // - an iframe becomes <div data-lift-frame> around the frame's body.
  // A secret value is never copied. The live page is not changed.
  function snapshot(selector, collectSecrets) {
    prune();
    const out = document.implementation.createHTMLDocument("");
    const interactive = [];
    const secrets = [];
    let actionIds = 0;

    const copy = (node, state) => {
      if (node.nodeType === 3) return [out.createTextNode(node.data)];
      if (node.nodeType !== 1 || SKIP.has(node.tagName)) return [];
      const el = node;
      const tag = el.tagName;
      if (tag === "SLOT") return composedChildren(el).flatMap((child) => copy(child, state));

      const style = el.ownerDocument.defaultView.getComputedStyle(el);
      let result;
      if (tag === "IFRAME") {
        result = out.createElement("div");
        result.setAttribute("data-lift-frame", el.getAttribute("title") || el.getAttribute("src") || "frame");
      } else if (tag === "BODY" && el.ownerDocument !== document) {
        result = out.createElement("div"); // the body of a frame
      } else if (tag.includes("-")) {
        result = out.createElement("div");
        for (const attr of el.attributes) result.setAttribute(attr.name, attr.value);
        result.setAttribute("data-lift-tag", tag.toLowerCase());
      } else {
        result = out.createElement(tag.toLowerCase());
        for (const attr of el.attributes) result.setAttribute(attr.name, attr.value);
      }
      result.setAttribute("data-lift-ref", refOf(el));

      // Live form state.
      const secret = isSecret(el);
      if (tag === "INPUT") {
        const type = (el.getAttribute("type") || "text").toLowerCase();
        if (type === "checkbox" || type === "radio") {
          el.checked ? result.setAttribute("checked", "") : result.removeAttribute("checked");
        } else if (secret) {
          if (collectSecrets && el.value) secrets.push(el.value);
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
          interactive.push({ ref: refOf(el), tag: tag.toLowerCase(), kind: kindOf(el), name: nameOf(el) });
          inside = true;
        }
      }

      if (tag === "TEXTAREA") {
        if (secret) {
          if (collectSecrets && el.value) secrets.push(el.value);
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
    const payload = { html: "<!doctype html>" + root.outerHTML, interactive, actionIds, docId };
    if (collectSecrets) payload.secrets = secrets;
    return payload;
  }

  // ---- Locator candidates -----------------------------------------------------------------------
  const quote = (value) => '"' + String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
  // An id that looks generated (React ":r1:", "ember123", hashes) changes between page loads.
  const stableId = (id) =>
    Boolean(id) && id.length <= 64 && !/\d{3,}|:|^\d|[0-9a-f]{8,}/i.test(id) && !/^(ember|react|radix|mui)/i.test(id);

  // Every open shadow root in a document, for queries that pierce shadow DOM as Playwright does.
  function rootsOf(doc) {
    const roots = [doc];
    for (let i = 0; i < roots.length; i++) {
      for (const el of roots[i].querySelectorAll("*")) if (el.shadowRoot) roots.push(el.shadowRoot);
    }
    return roots;
  }

  function matchesIn(roots, css) {
    try {
      return roots.flatMap((root) => [...root.querySelectorAll(css)]);
    } catch (error) {
      return null;
    }
  }

  // Structural path from the nearest ancestor with a stable unique id (or from <html>). Playwright's
  // ">" crosses into open shadow roots, so the path continues from a host into its shadow tree.
  function structuralPath(el, roots) {
    const parts = [];
    let current = el;
    while (current && current.nodeType === 1) {
      const tag = current.tagName.toLowerCase();
      if (current !== el && stableId(current.id) && matchesIn(roots, "#" + CSS.escape(current.id))?.length === 1) {
        parts.unshift("#" + CSS.escape(current.id));
        break;
      }
      const parent = current.parentElement || current.getRootNode().host || null;
      const siblings = current.parentNode ? [...current.parentNode.children].filter((s) => s.tagName === current.tagName) : [];
      parts.unshift(siblings.length > 1 ? `${tag}:nth-of-type(${siblings.indexOf(current) + 1})` : tag);
      if (tag === "html") break;
      current = parent;
    }
    return parts.join(" > ");
  }

  // Text that tells one repeated item from its siblings: the item's first heading, or else its first
  // line of text that is not the target's own name. Empty when there is none.
  function anchorText(item, target) {
    const heading = item.querySelector("h1,h2,h3,h4,h5,h6");
    const own = clean(target.innerText || target.value || "");
    const text = clean(heading ? heading.innerText : "") ||
      (item.innerText || "").split("\n").map(clean).find((line) => line && line !== own) || "";
    return text.length >= 2 && text.length <= 40 ? text : "";
  }

  // A selector anchored on content: the nearest ancestor that repeats among its siblings (a list
  // item, a table row, a card) and the text that tells it apart, then the element inside it.
  // ":has-text()" is Playwright CSS, so only Playwright can check these.
  function anchoredCandidates(el, tag) {
    const found = [];
    let item = el.parentElement;
    for (let depth = 0; item && depth < 6; depth++, item = item.parentElement) {
      const parent = item.parentElement;
      if (!parent) break;
      const repeats = [...parent.children].filter((s) => s.tagName === item.tagName).length > 1;
      if (!repeats) continue;
      const text = anchorText(item, el);
      if (text) found.push({ item: item.tagName.toLowerCase(), text, target: tag });
      if (found.length) break;
    }
    return found;
  }

  // What Playwright's "item:has-text(text) target" finds, counted in this page: every target inside an
  // item whose text contains the anchor text (ignoring case and runs of whitespace).
  function anchoredMatches(roots, anchor) {
    const needle = anchor.text.replace(/\s+/g, " ").trim().toLowerCase();
    const items = matchesIn(roots, anchor.item) || [];
    const found = new Set();
    for (const item of items) {
      if (!(item.textContent || "").replace(/\s+/g, " ").toLowerCase().includes(needle)) continue;
      for (const target of item.querySelectorAll(anchor.target)) found.add(target);
    }
    return [...found];
  }

  // Candidates for an element, best first. ``kind`` is "attr" (a stable attribute), "anchored"
  // (content-anchored), or "path" (structural; it breaks when the layout changes, so it is the last
  // resort, after a role-and-name locator). ``unique`` is true or false when this page can tell, and
  // null when only Playwright can say (a selector that crosses a shadow root, or Playwright CSS).
  function candidates(el) {
    const roots = rootsOf(el.ownerDocument);
    const tag = el.tagName.toLowerCase();
    const list = [];
    const seen = new Set();
    const add = (css, simple, kind = "attr", anchor = null) => {
      if (!css || seen.has(css)) return;
      seen.add(css);
      const found = anchor ? anchoredMatches(roots, anchor) : matchesIn(roots, css);
      if (found === null) return;
      const exact = found.length === 1 && found[0] === el;
      list.push({ css, kind, unique: exact ? true : simple || found.length > 1 ? false : null });
    };
    if (stableId(el.id)) add("#" + CSS.escape(el.id), true);
    for (const attr of TEST_ID_ATTRS) if (el.getAttribute(attr)) add(`[${attr}=${quote(el.getAttribute(attr))}]`, true);
    const name = el.getAttribute("name");
    if (name) {
      add(`${tag}[name=${quote(name)}]`, true);
      if (el.getAttribute("value") !== null && /^(radio|checkbox)$/i.test(el.type || "")) {
        add(`${tag}[name=${quote(name)}][value=${quote(el.getAttribute("value"))}]`, true);
      }
    }
    for (const attr of ["aria-label", "title", "placeholder"]) {
      if (el.getAttribute(attr)) add(`${tag}[${attr}=${quote(el.getAttribute(attr))}]`, true);
    }
    if (tag === "a" && el.getAttribute("href")) add(`a[href=${quote(el.getAttribute("href"))}]`, true);
    if (name && el.form && stableId(el.form.id)) add(`#${CSS.escape(el.form.id)} ${tag}[name=${quote(name)}]`, false);
    for (const anchor of anchoredCandidates(el, tag)) {
      add(`${anchor.item}:has-text(${quote(anchor.text)}) ${anchor.target}`, true, "anchored", anchor);
    }
    add(structuralPath(el, roots), false, "path");
    return list;
  }

  // Where an element is: its frame chain (outermost first) and its CSS candidates.
  function describe(ref) {
    const el = get(ref);
    if (!el) return { connected: false };
    const frames = [];
    let doc = el.ownerDocument;
    while (doc.defaultView && doc.defaultView.frameElement) {
      const frame = doc.defaultView.frameElement;
      frames.unshift({ ref: refOf(frame), candidates: candidates(frame) });
      doc = frame.ownerDocument;
    }
    return { connected: true, tag: el.tagName.toLowerCase(), frames, candidates: candidates(el) };
  }

  // Describe many elements in one call. For each, ``css`` (and ``frames``) is the first candidate this
  // page found unique and the same element, by stable attribute or content anchor, in that order.
  // Where this page cannot tell, ``css`` is null and the caller asks Playwright.
  function locateAll(refs) {
    const out = {};
    for (const ref of refs) {
      const info = describe(ref);
      if (!info.connected) {
        out[ref] = info;
        continue;
      }
      const pick = (candidates, kinds) =>
        candidates.find((candidate) => candidate.unique === true && kinds.includes(candidate.kind)) || null;
      const frames = info.frames.map((frame) => pick(frame.candidates, ["attr", "anchored", "path"]));
      const chosen = pick(info.candidates, ["attr", "anchored"]);
      out[ref] = {
        ...info,
        chosen: chosen && frames.every(Boolean) ? { css: chosen.css, kind: chosen.kind, frames: frames.map((f) => f.css) } : null,
      };
    }
    return out;
  }

  // ---- Waiting for the page to change -----------------------------------------------------------
  // Every root a change can happen in: the document, its open shadow roots, and the same for each
  // same-origin iframe.
  function rootsDeep(doc) {
    const roots = rootsOf(doc);
    for (const root of [...roots]) {
      for (const frame of root.querySelectorAll("iframe")) {
        try {
          if (frame.contentDocument) roots.push(...rootsDeep(frame.contentDocument));
        } catch (error) {
          // A cross-origin frame cannot be watched.
        }
      }
    }
    return roots;
  }

  // Resolve true after the DOM changes, once it has been quiet for 100 ms (or 500 ms after the first
  // change, for a page that never stops changing), or false after timeoutMs with no change. A change
  // that only sets a property (typed text) is not a DOM change.
  function nextChange(timeoutMs) {
    return new Promise((resolve) => {
      const observers = [];
      const timers = {};
      let done = false;
      const finish = (changed) => {
        if (done) return;
        done = true;
        observers.forEach((observer) => observer.disconnect());
        Object.values(timers).forEach(clearTimeout);
        resolve(changed);
      };
      const onChange = () => {
        if (!timers.cap) timers.cap = setTimeout(() => finish(true), 500);
        clearTimeout(timers.quiet);
        timers.quiet = setTimeout(() => finish(true), 100);
      };
      for (const root of rootsDeep(document)) {
        const observer = new MutationObserver(onChange);
        observer.observe(root, { subtree: true, childList: true, attributes: true, characterData: true });
        observers.push(observer);
      }
      timers.limit = setTimeout(() => finish(false), Math.max(0, timeoutMs));
    });
  }

  // ---- Scrolling --------------------------------------------------------------------------------
  // The area that scrolls for an element: the nearest ancestor (or the element itself) that can
  // scroll vertically, crossing shadow roots; or else its document.
  function scrollArea(el) {
    let node = el;
    while (node && node.nodeType === 1) {
      const style = node.ownerDocument.defaultView.getComputedStyle(node);
      if (/(auto|scroll|overlay)/.test(style.overflowY) && node.scrollHeight > node.clientHeight + 1) return node;
      node = node.parentElement || node.getRootNode().host || null;
    }
    return el.ownerDocument.scrollingElement;
  }
  function scrollState(area) {
    return {
      top: Math.round(area.scrollTop),
      height: area.scrollHeight,
      client: area.clientHeight,
      atEnd: area.scrollTop + area.clientHeight >= area.scrollHeight - 2,
      atStart: area.scrollTop <= 0,
    };
  }
  // Scroll the page (ref null) or the area of the element ``ref``: to the ``end``, to the ``top``, or
  // about one screen ``down`` or ``up``.
  function scrollStep(ref, direction) {
    const el = ref ? get(ref) : null;
    if (ref && !el) return { stale: true };
    const area = el ? scrollArea(el) : document.scrollingElement;
    const before = scrollState(area);
    const screen = area.clientHeight * 0.9;
    const top = { end: area.scrollHeight, top: 0, down: area.scrollTop + screen, up: area.scrollTop - screen }[direction];
    area.scrollTo({ top, behavior: "instant" });
    return { area: refOf(area), page: area === area.ownerDocument.scrollingElement, before, after: scrollState(area) };
  }
  function scrollNow(areaRef) {
    const area = get(areaRef);
    return area ? scrollState(area) : null;
  }

  // ---- Choosing from a custom list --------------------------------------------------------------
  const norm = (text) => (text || "").replace(/\s+/g, " ").trim().toLowerCase();
  function shown(el) {
    if (typeof el.checkVisibility === "function" && !el.checkVisibility({ visibilityProperty: true })) return false;
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  }
  // The list of a combobox (aria-controls, aria-owns, or a list inside it), or a listbox itself.
  function listOf(el) {
    for (const attr of ["aria-controls", "aria-owns"]) {
      for (const id of (el.getAttribute(attr) || "").split(/\s+/).filter(Boolean)) {
        const list = byId(el, id);
        if (list) return list;
      }
    }
    if ((el.getAttribute("role") || "").split(" ")[0] === "listbox") return el;
    return el.querySelector("[role=listbox]");
  }
  // The option named ``value`` in the list of the control ``ref``: the one with exactly that name
  // (ignoring case and runs of whitespace), or else the one whose name starts with it.
  function findOption(ref, value) {
    const el = get(ref);
    if (!el) return { state: "stale" };
    const list = listOf(el);
    const options = list
      ? [...list.querySelectorAll("[role=option]")].filter((o) => shown(o) && o.getAttribute("aria-disabled") !== "true")
      : [];
    const names = options.map((o) => clean(o.getAttribute("aria-label") || o.textContent));
    const want = norm(value);
    let hits = names.map((name, i) => [name, i]).filter(([name]) => norm(name) === want);
    if (hits.length === 0) hits = names.map((name, i) => [name, i]).filter(([name]) => norm(name).startsWith(want));
    if (hits.length === 1) return { state: "found", ref: refOf(options[hits[0][1]]), label: hits[0][0] };
    if (hits.length > 1) return { state: "ambiguous", names: hits.map(([name]) => name) };
    return { state: "missing", names, open: Boolean(list && shown(list)) };
  }
  // After a choice: does the control show the option, or mark it selected?
  function showsChoice(ref, optionRef, label) {
    const el = get(ref);
    const option = get(optionRef);
    const want = norm(label);
    if (option && option.getAttribute("aria-selected") === "true") return true;
    if (!el) return false;
    if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") return norm(el.value) === want;
    const copy = el.cloneNode(true);
    copy.querySelectorAll("[role=listbox],[role=option]").forEach((item) => item.remove());
    return norm(copy.textContent) === want;
  }

  // ---- Date pickers -----------------------------------------------------------------------------
  const MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december"];
  function monthOf(word) {
    const w = (word || "").toLowerCase().replace(/\.$/, "");
    if (w.length < 3) return 0;
    const index = MONTH_NAMES.findIndex((name) => name.startsWith(w));
    return index + 1;
  }
  // A full date in a label ("Friday, March 7, 1990", "7 March 1990", "1990-03-07"): {y, m, d} or null.
  function fullDate(text) {
    const t = (text || "").replace(/\s+/g, " ");
    let match = t.match(/(\d{4})-(\d{2})-(\d{2})/);
    if (match) return { y: +match[1], m: +match[2], d: +match[3] };
    match = t.match(/([A-Za-z]+)\.? (\d{1,2})(?:st|nd|rd|th)?,? (\d{4})/);
    if (match && monthOf(match[1])) return { y: +match[3], m: monthOf(match[1]), d: +match[2] };
    match = t.match(/(\d{1,2})(?:st|nd|rd|th)? ([A-Za-z]+)\.?,? (\d{4})/);
    if (match && monthOf(match[2])) return { y: +match[3], m: monthOf(match[2]), d: +match[1] };
    return null;
  }
  // A month and year in a calendar's title ("November 2026"): {y, m} or null.
  function monthTitle(text) {
    const match = (text || "").match(/([A-Za-z]+)\.?,? (\d{4})/);
    return match && monthOf(match[1]) ? { y: +match[2], m: monthOf(match[1]) } : null;
  }
  const dayLabel = (el) => el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("data-date") ||
    el.getAttribute("datetime") || "";
  const OUTSIDE = /outside|other-?month|adjacent|prev-?month|next-?month|not-?current|muted/i;

  // The day cells of a calendar: the innermost visible elements that show a day number (1-31) or carry
  // a full date in their label.
  function dayCells(root) {
    const found = [];
    for (const el of root.querySelectorAll("button, td, a, [role=gridcell], [role=button], [role=option], span, div")) {
      if (!shown(el)) continue;
      const text = (el.textContent || "").trim();
      const labelled = fullDate(dayLabel(el));
      if (!labelled && !/^\d{1,2}$/.test(text)) continue;
      if (!labelled && (+text < 1 || +text > 31)) continue;
      found.push(el);
    }
    return found.filter((el) => !found.some((other) => other !== el && el.contains(other)));
  }
  function lowerScore(a, b) {
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] < b[i];
    return false;
  }
  // How far apart two elements are in the tree: steps up from ``a`` to the nearest element that
  // also holds ``b``.
  function treeDistance(a, b) {
    let steps = 0;
    for (let node = a; node; node = node.parentElement || node.getRootNode().host || null, steps++) {
      if (node.contains(b)) return steps;
    }
    return Infinity;
  }
  // The open calendar of a field: a visible dialog, grid, or calendar-like block with a month of day
  // cells. The one the field names in aria-controls wins; then the one nearest the field in the tree
  // (another field's calendar may still be open); then the one with the most day cells.
  function calendarFor(el) {
    const named = (el.getAttribute("aria-controls") || "").split(/\s+/).filter(Boolean).map((id) => byId(el, id));
    let best = null;
    let bestScore = null;
    const selector = "[role=dialog], [role=grid], [role=application], table, [class*=calendar i], [class*=picker i]";
    for (const root of rootsOf(el.ownerDocument)) {
      for (const candidate of root.querySelectorAll(selector)) {
        if (!shown(candidate)) continue;
        const count = dayCells(candidate).length;
        if (count < 28) continue;
        // Lower is better, field by field: named by the field, nearer in the tree, more day cells.
        const score = [
          named.some((n) => n && (n === candidate || n.contains(candidate))) ? 0 : 1,
          treeDistance(el, candidate),
          -count,
        ];
        if (!bestScore || lowerScore(score, bestScore)) {
          best = candidate;
          bestScore = score;
        }
      }
    }
    // Move up to the dialog around a grid, where the month title and buttons are.
    const outer = best && best.closest("[role=dialog], [class*=calendar i], [class*=picker i]");
    return outer && dayCells(outer).length >= dayCells(best).length ? outer : best;
  }
  // Read the open calendar of the field ``ref`` for the date ``iso`` (YYYY-MM-DD).
  function readCalendar(ref, iso) {
    const el = get(ref);
    if (!el) return { state: "stale" };
    const cal = calendarFor(el);
    if (!cal) return { state: "closed" };
    const [ty, tm, td] = iso.split("-").map(Number);
    const cells = dayCells(cal).map((cell) => {
      const date = fullDate(dayLabel(cell));
      const outside = OUTSIDE.test(`${cell.className} ${cell.parentElement ? cell.parentElement.className : ""}`);
      const disabled = cell.disabled || cell.getAttribute("aria-disabled") === "true" || /disabled|unavailable/i.test(cell.className);
      return { cell, date, day: date ? date.d : +cell.textContent.trim(), outside, disabled };
    });
    // Which month is shown: its month and year lists, its title, or the dates of its days.
    const lists = [...cal.querySelectorAll("select")];
    const monthList = lists.find((s) => s.options.length === 12 && [...s.options].every((o) => monthOf(o.textContent)));
    const yearList = lists.find((s) => s.options.length > 1 && [...s.options].every((o) => /^\d{4}$/.test(o.textContent.trim())));
    let shownMonth = null;
    if (monthList && yearList) {
      shownMonth = { y: +yearList.selectedOptions[0].textContent, m: monthOf(monthList.selectedOptions[0].textContent) };
    }
    if (!shownMonth) {
      for (const title of cal.querySelectorAll("h1, h2, h3, h4, h5, h6, caption, [aria-live], [class*=title i], [class*=caption i], [class*=header i]")) {
        shownMonth = monthTitle(title.textContent);
        if (shownMonth) break;
      }
    }
    if (!shownMonth) {
      const dated = cells.filter((c) => c.date && !c.outside);
      if (dated.length) shownMonth = { y: dated[Math.floor(dated.length / 2)].date.y, m: dated[Math.floor(dated.length / 2)].date.m };
    }
    // The day to click: by its full date, or by its number in the month shown (skipping the days of
    // the months around it, which repeat numbers at the start and end of the grid).
    let target = cells.find((c) => c.date && c.date.y === ty && c.date.m === tm && c.date.d === td) || null;
    if (!target && shownMonth && shownMonth.y === ty && shownMonth.m === tm) {
      const inMonth = cells.filter((c) => !c.date && !c.outside);
      const days = inMonth.map((c) => c.day);
      const start = days.findIndex((day, i) => day === 1 && days.slice(i, i + 28).every((d, k) => d === k + 1));
      const run = start >= 0 ? inMonth.slice(start) : inMonth;
      target = run.find((c, k) => c.day === td && (start < 0 || k === td - 1)) || null;
    }
    const nameOfButton = (b) => `${b.getAttribute("aria-label") || ""} ${b.getAttribute("title") || ""} ${b.textContent || ""}`;
    const buttons = [...cal.querySelectorAll("button, [role=button], a")].filter((b) => shown(b) && !cells.some((c) => c.cell === b));
    const next = buttons.find((b) => /next|›|»|→|forward/i.test(nameOfButton(b)) || nameOfButton(b).trim() === ">");
    const prev = buttons.find((b) => /prev|‹|«|←|back/i.test(nameOfButton(b)) || nameOfButton(b).trim() === "<");
    return {
      state: "open",
      shown: shownMonth,
      target: target ? { ref: refOf(target.cell), disabled: target.disabled } : null,
      next: next ? refOf(next) : null,
      prev: prev ? refOf(prev) : null,
      monthList: monthList ? refOf(monthList) : null,
      yearList: yearList ? { ref: refOf(yearList), years: [...yearList.options].map((o) => +o.textContent) } : null,
    };
  }
  // A button beside the field that opens its calendar: named for a date, a calendar, or choosing.
  function calendarOpener(ref) {
    const el = get(ref);
    let scope = el ? el.parentElement : null;
    for (let depth = 0; scope && depth < 3; depth++, scope = scope.parentElement) {
      const found = [...scope.querySelectorAll("button, [role=button]")].find(
        (b) => b !== el && shown(b) && /date|calendar|choose|pick/i.test(nameOf(b)),
      );
      if (found) return refOf(found);
    }
    return null;
  }
  // Does the field show the date ``iso``, in any common order of day, month, and year?
  function showsDate(ref, iso) {
    const el = get(ref);
    if (!el) return false;
    const [y, m, d] = iso.split("-").map(Number);
    const text = "value" in el && el.tagName !== "BUTTON" ? el.value : el.textContent;
    const named = fullDate(text);
    if (named) return named.y === y && named.m === m && named.d === d;
    const numbers = (text.match(/\d+/g) || []).map(Number);
    if (numbers.length !== 3) return false;
    const [a, b, c] = numbers;
    return (a === y && b === m && c === d) || (c === y && ((a === m && b === d) || (a === d && b === m)));
  }

  // ---- Checks after an action -------------------------------------------------------------------
  // Each returns true or false and never returns the element's value, so a secret stays in the page.
  function hasValue(el, value) {
    if (el.tagName === "SELECT") return false;
    return ("value" in el ? el.value : el.textContent) === value;
  }
  function hasSelected(el, values) {
    if (el.tagName !== "SELECT") return false;
    const selected = [...el.selectedOptions].map((option) => option.value).sort();
    return JSON.stringify(selected) === JSON.stringify([...values].sort());
  }
  function isChecked(el) {
    if ("checked" in el && el.tagName === "INPUT") return el.checked;
    return el.getAttribute("aria-checked") === "true";
  }

  window.__viewspecLift = {
    docId,
    snapshot,
    describe,
    locateAll,
    refOf,
    get,
    nameOf,
    nextChange,
    findOption,
    showsChoice,
    scrollStep,
    scrollNow,
    readCalendar,
    calendarOpener,
    showsDate,
    hasValue,
    hasSelected,
    isChecked,
  };
})();
