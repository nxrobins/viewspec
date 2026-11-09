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
  // Tags that flow inside a line of text by default. The same list as the package's INLINE_TAGS; the
  // snapshot marks an element whose layout differs from its tag's default (data-lift-display).
  const INLINE = new Set([
    "A", "ABBR", "B", "BUTTON", "CODE", "EM", "I", "IMG", "INPUT", "LABEL", "OUTPUT", "SELECT", "SMALL", "SPAN",
    "STRONG", "SUB", "SUP", "TEXTAREA",
  ]);
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
  // The text of an element's content as its name: like innerText, but without aria-hidden subtrees
  // (a switch's "On"/"Off" marks, a button's decorative "−"), which ARIA leaves out of a name. A
  // block-level child is set apart by spaces, as the package's join_text does.
  function nameText(el, skipControls = false) {
    let out = "";
    for (const node of el.childNodes) {
      if (node.nodeType === 3) {
        out += node.data;
        continue;
      }
      if (node.nodeType !== 1 || SKIP.has(node.tagName) || node.getAttribute("aria-hidden") === "true" || !shown(node)) continue;
      if (skipControls && /^(INPUT|SELECT|TEXTAREA|OPTION)$/.test(node.tagName)) continue; // A label's text, not its field's.
      const text = nameText(node, skipControls);
      out += /^inline/.test(getComputedStyle(node).display) ? text : ` ${text} `;
    }
    return out;
  }
  function nameOf(el) {
    // aria-labelledby first: each element named contributes its own aria-label, or else its text,
    // and an element may name itself this way ("Remove" + "Recipient Name 1").
    const labelledBy = el.getAttribute("aria-labelledby");
    if (labelledBy) {
      const text = labelledBy
        .split(/\s+/)
        .map((id) => {
          const target = byId(el, id);
          return target ? target.getAttribute("aria-label") || nameText(target) : "";
        })
        .join(" ");
      if (clean(text)) return clean(text);
    }
    if (el.getAttribute("aria-label")) return clean(el.getAttribute("aria-label"));
    if (el.labels && el.labels.length) {
      // A label's text, without the text of the fields inside it and without its aria-hidden marks.
      const text = clean(nameText(el.labels[0], true));
      if (text) return text;
    }
    const tag = el.tagName;
    if (tag === "INPUT" && /^(submit|reset|button|image)$/i.test(el.type)) {
      return clean(el.value || el.getAttribute("alt") || (el.type === "reset" ? "Reset" : "Submit"));
    }
    if (tag !== "INPUT" && tag !== "SELECT" && tag !== "TEXTAREA" && clean(nameText(el))) return clean(nameText(el));
    const fallback = clean(el.getAttribute("title") || el.getAttribute("placeholder") || el.querySelector?.("img")?.alt);
    if (fallback || (tag !== "INPUT" && tag !== "SELECT" && tag !== "TEXTAREA")) return fallback;
    return labelBeside(el);
  }
  // The text written beside a field that nothing labels, as a person reads it: the text just before
  // it in its block ("Date: <input>"), or just after a bare checkbox or radio ("<input> Remember me");
  // when that is blank, the inline element next to it ("<span>Amount:</span>"). Only a short,
  // label-like phrase counts, without its colon. The package's _text_beside does the same.
  function labelBeside(el) {
    const after = el.tagName === "INPUT" && /^(checkbox|radio)$/i.test(el.type);
    let text = "";
    for (let node = after ? el.nextSibling : el.previousSibling; node; node = after ? node.nextSibling : node.previousSibling) {
      if (node.nodeType === 3) {
        text = after ? text + node.data : node.data + text;
        continue;
      }
      if (node.nodeType !== 1) continue;
      if (!text.trim() && shown(node) && /^inline/.test(getComputedStyle(node).display) && !controlLike(node)) {
        text = node.innerText || node.textContent || "";
      }
      break;
    }
    text = text.replace(/\s+/g, " ").trim().replace(/:$/, "").trim();
    return text && text.length <= 40 && !/\.$/.test(text) ? text : "";
  }
  // What the package's _classify would call a control: a native control, a control role, something
  // editable, a tab stop, or an element with a pointer cursor. The label beside a field is never one.
  const CONTROL_ROLES = new Set([
    "button", "link", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "treeitem",
    "checkbox", "combobox", "listbox", "radio", "searchbox", "slider", "spinbutton", "switch", "textbox",
  ]);
  function controlLike(node) {
    if (node.matches("input,select,textarea,button,summary,a[href]")) return true;
    const editable = (node.getAttribute("contenteditable") || "missing").toLowerCase();
    if (editable === "" || editable === "true" || editable === "plaintext-only") return true;
    const role = (node.getAttribute("role") || "").trim().toLowerCase().split(" ")[0];
    if (CONTROL_ROLES.has(role)) return true;
    const tabindex = parseInt(node.getAttribute("tabindex") || "-1", 10);
    return tabindex >= 0 || getComputedStyle(node).cursor === "pointer";
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
  // - an iframe becomes <div data-lift-frame> around the frame's body;
  // - an element that has or names an id, outside the page's own tree, has data-lift-scope.
  // A secret value is never copied. The live page is not changed.
  //
  // The copy is one document, but each frame and each shadow root keeps ids of its own: two frames
  // may both have <input id="email">, each with its own <label for="email">. data-lift-scope names
  // the element's tree: "2" for a frame's document, "2.5" for a shadow root in that frame (its
  // document first, as byId falls back to it). The page itself is "0", and is never written.
  const ID_ATTRS = [
    "id", "for", "form", "list", "aria-labelledby", "aria-describedby", "aria-controls", "aria-owns",
    "aria-activedescendant",
  ];
  function snapshot(selector, collectSecrets) {
    prune();
    const out = document.implementation.createHTMLDocument("");
    const interactive = [];
    const secrets = [];
    let actionIds = 0;
    const scopes = new Map([[document, "0"]]);
    const scopeOf = (el) => {
      const root = el.getRootNode();
      if (!scopes.has(root)) {
        const doc = root === el.ownerDocument ? "" : scopeOf(el.ownerDocument.documentElement) + ".";
        scopes.set(root, doc + scopes.size);
      }
      return scopes.get(root);
    };

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
      if (ID_ATTRS.some((name) => result.hasAttribute(name))) {
        const scope = scopeOf(el);
        if (scope !== "0") result.setAttribute("data-lift-scope", scope);
      }

      // Live form state.
      const secret = isSecret(el);
      if (tag === "INPUT") {
        const type = (el.getAttribute("type") || "text").toLowerCase();
        if (type === "checkbox" || type === "radio") {
          el.checked ? result.setAttribute("checked", "") : result.removeAttribute("checked");
        } else if (secret) {
          if (collectSecrets && el.value) secrets.push(el.value);
          result.removeAttribute("value");
        } else if (type === "file") {
          // Name the chosen files; their bytes stay in the page.
          result.setAttribute("data-lift-files", [...(el.files || [])].map((f) => f.name).join(", "));
        } else {
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
      // Layout hint for text order: a span laid out as a block is a line of its own (Wikipedia's
      // suggestions show a title over a description), and a div laid out inline runs into its line.
      if (!hidden && tag !== "HTML" && tag !== "BODY" && !NO_BOX.has(tag)) {
        const inlineNow = /^inline/.test(style.display);
        if (INLINE.has(tag) && !inlineNow && style.display !== "contents") result.setAttribute("data-lift-display", "block");
        else if (!INLINE.has(tag) && inlineNow) result.setAttribute("data-lift-display", "inline");
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

  // The one visible element with this tag and this accessible name, in the document and its open
  // shadow roots, or null when there is none or more than one. The drift guard's own criterion,
  // used to find a control again when the page replaced it and its saved locator finds nothing.
  function byTagAndName(tag, name) {
    const want = norm(name);
    const hits = [];
    for (const root of rootsOf(document)) {
      for (const el of root.querySelectorAll(tag)) if (shown(el) && norm(nameOf(el)) === want) hits.push(el);
    }
    return hits.length === 1 ? hits[0] : null;
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
  // A loading indicator is not content: a live region that says "Loading…", a progress bar, or a
  // subtree the page marks busy. A scroll that loads more waits for other elements.
  const INDICATOR =
    '[aria-busy="true"],[role~="progressbar"],[role~="status"],[role~="log"],[role~="alert"],[aria-live]:not([aria-live="off"])';
  // The elements in an area, without its loading indicators and without scripts and styles.
  function countItems(area) {
    let excluded = 0;
    for (const indicator of area.querySelectorAll(INDICATOR)) {
      let node = indicator.parentElement;
      let nested = false;
      while (node && node !== area) {
        if (node.matches(INDICATOR)) {
          nested = true; // Already counted with the indicator around it.
          break;
        }
        node = node.parentElement;
      }
      if (!nested) excluded += 1 + indicator.querySelectorAll("*").length;
    }
    let total = 0;
    for (const el of area.querySelectorAll("*")) if (!SKIP.has(el.tagName)) total += 1;
    return total - excluded;
  }
  function scrollState(area) {
    return {
      top: Math.round(area.scrollTop),
      height: area.scrollHeight,
      client: area.clientHeight,
      items: countItems(area),
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
  // An option's text as the page lays it out (innerText keeps its lines apart), and its label: the
  // first line, when a description sits under the title (Wikipedia shows "Alan Turing" over
  // "English computer scientist"). The package names options the same way.
  function optionText(option) {
    const text = option.getAttribute("aria-label") || option.innerText || option.textContent || "";
    const lines = text.split("\n").map((line) => clean(line)).filter(Boolean);
    // The label is the first line that holds a letter or a digit: an icon on a line of its own above
    // the title is not the title. The package's first_line does the same.
    const label = lines.find((line) => /[\p{L}\p{N}]/u.test(line)) || lines[0] || "";
    return { name: clean(lines.join(" ")), label };
  }
  // The option named ``value``: by its whole text, then by its label, exactly (ignoring case and runs
  // of whitespace), or else the one option whose label, then whose text, starts with it.
  function findOption(ref, value) {
    const el = get(ref);
    if (!el) return { state: "stale" };
    const list = listOf(el);
    const options = list
      ? [...list.querySelectorAll("[role=option]")].filter((o) => shown(o) && o.getAttribute("aria-disabled") !== "true")
      : [];
    const texts = options.map(optionText);
    const want = norm(value);
    const matching = (test) => texts.map((text, i) => [text, i]).filter(([text]) => test(text));
    let hits = matching((text) => norm(text.name) === want);
    if (hits.length === 0) hits = matching((text) => norm(text.label) === want);
    if (hits.length === 0) hits = matching((text) => norm(text.label).startsWith(want));
    if (hits.length === 0) hits = matching((text) => norm(text.name).startsWith(want));
    if (hits.length === 1) return { state: "found", ref: refOf(options[hits[0][1]]), label: hits[0][0].label };
    if (hits.length > 1) return { state: "ambiguous", names: hits.map(([text]) => text.label) };
    return { state: "missing", names: texts.map((text) => text.label), open: Boolean(list && shown(list)) };
  }
  // After a choice: does the control show the option, or mark it selected?
  function showsChoice(ref, optionRef, label) {
    const el = get(ref);
    const option = get(optionRef);
    const want = norm(label);
    if (option && option.getAttribute("aria-selected") === "true") return true;
    if (!el) return false;
    if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
      if (norm(el.value) === want) return true;
      // react-select and Ant Design keep the input empty and show the choice in the box beside it.
      return !el.value && norm(shownBeside(el)) === want;
    }
    const copy = el.cloneNode(true);
    copy.querySelectorAll("[role=listbox],[role=option]").forEach((item) => item.remove());
    return norm(copy.textContent) === want;
  }
  // The text a widget shows beside an empty input: the text of the nearest ancestor that has any,
  // without the input, hidden nodes, lists and options, live regions, labels, buttons, and other
  // fields. The search stops at a form, a list item, a table cell, or a section, which hold more than
  // one widget. The same rule as the package's `_shown_beside`.
  const BESIDE_SKIP =
    'label,button,input,select,textarea,[role~="listbox"],[role~="option"],[role~="status"],[role~="log"],[role~="alert"],[aria-live]:not([aria-live="off"])';
  const WIDGET_BOUNDARY = new Set([
    "ARTICLE", "ASIDE", "BODY", "DIALOG", "FIELDSET", "FOOTER", "FORM", "HEADER", "HTML", "LI", "MAIN", "NAV",
    "SECTION", "TABLE", "TD", "TH", "TR",
  ]);
  function textBeside(node, el) {
    if (node.nodeType === 3) return node.nodeValue;
    if (node.nodeType !== 1 || node === el || SKIP.has(node.tagName) || node.matches(BESIDE_SKIP) || !shown(node)) return "";
    let text = "";
    for (const child of node.childNodes) text += textBeside(child, el);
    return text;
  }
  function shownBeside(el) {
    for (let node = el.parentElement; node && !WIDGET_BOUNDARY.has(node.tagName); node = node.parentElement) {
      const text = textBeside(node, el).replace(/\s+/g, " ").trim();
      if (text) return text;
    }
    return "";
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
    const match = (text || "").match(/([A-Za-z]+)\.?,?\s+(\d{4})/); // \s also matches the &nbsp; jQuery UI puts here.
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
  const CALENDAR_SELECTOR = "[role=dialog], [role=grid], [role=application], table, [class*=calendar i], [class*=picker i]";
  // The visible calendar-like blocks with a month of day cells, anywhere in the field's document,
  // each with its count of day cells (read once; readCalendar and calendarFor both use it).
  function shownCalendars(el) {
    const found = [];
    for (const root of rootsOf(el.ownerDocument)) {
      for (const candidate of root.querySelectorAll(CALENDAR_SELECTOR)) {
        if (!shown(candidate)) continue;
        const count = dayCells(candidate).length;
        if (count >= 28) found.push({ el: candidate, count });
      }
    }
    return found;
  }
  // The gap in pixels between two boxes: 0 when they touch or overlap.
  function boxGap(a, b) {
    return Math.max(0, b.left - a.right, a.left - b.right, b.top - a.bottom, a.top - b.bottom);
  }
  // The open calendar of a field, among the visible calendars (see shownCalendars): one that is the
  // field's. That is the one the field names in aria-controls; else one that was not shown before
  // the field was clicked (``seen`` lists the calendars shown then; a popup appended to the end of
  // the page is found this way); else one drawn next to the field (within 48 px) or around it. A
  // calendar always on the page somewhere else (an inline example) is not taken for the field's.
  // Among those, the named one wins, then a new one, then the nearest in the tree, then the one with
  // the most day cells.
  function calendarFor(el, seen, calendars) {
    const named = (el.getAttribute("aria-controls") || "").split(/\s+/).filter(Boolean).map((id) => byId(el, id));
    const fieldBox = el.getBoundingClientRect();
    let best = null;
    let bestScore = null;
    for (const { el: candidate, count } of calendars || shownCalendars(el)) {
      const isNamed = named.some((n) => n && (n === candidate || n.contains(candidate)));
      const isNew = Array.isArray(seen) && !seen.includes(refOf(candidate));
      const near =
        candidate.contains(el) || el.contains(candidate) || boxGap(fieldBox, candidate.getBoundingClientRect()) <= 48;
      if (!isNamed && !isNew && !near) continue;
      const score = [isNamed ? 0 : 1, isNew ? 0 : 1, treeDistance(el, candidate), -count];
      if (!bestScore || lowerScore(score, bestScore)) {
        best = candidate;
        bestScore = score;
      }
    }
    // Move up to the dialog around a grid, where the month title and buttons are.
    const outer = best && best.closest("[role=dialog], [class*=calendar i], [class*=picker i]");
    return outer && dayCells(outer).length >= dayCells(best).length ? outer : best;
  }
  // The months a calendar shows, from their titles ("November 2026"), each with the day cells under
  // it: [{month: {y, m}, cells}]. Several titles mean several months side by side. Each month's
  // cells are those in the nearest block around its title that holds a month of days and no other
  // title; when the titles share a header row, the grids are matched to the titles in order.
  function monthPanels(cal, cells) {
    const selector = "h1, h2, h3, h4, h5, h6, caption, [aria-live], [class*=title i], [class*=caption i], [class*=header i], [class*=month i]";
    const found = [...cal.querySelectorAll(selector)].filter((el) => shown(el) && monthTitle(el.textContent));
    const titles = found.filter((el) => !found.some((other) => other !== el && el.contains(other)));
    const months = [];
    for (const el of titles) {
      const month = monthTitle(el.textContent);
      if (!months.some((t) => t.month.y === month.y && t.month.m === month.m)) months.push({ el, month });
    }
    if (months.length <= 1) return months.map(({ month }) => ({ month, cells }));
    const cellsIn = (root) => cells.filter((c) => root.contains(c.cell));
    let roots = months.map(({ el }) => {
      let node = el.parentElement;
      while (node && node !== cal && cellsIn(node).length < 28) node = node.parentElement;
      return node;
    });
    const own = roots.every((root, i) => root && root !== cal && !months.some((t, k) => k !== i && root.contains(t.el)));
    if (!own) {
      const grids = [...cal.querySelectorAll("table, [role=grid]")].filter((g) => shown(g) && cellsIn(g).length >= 28);
      const outer = grids.filter((g) => !grids.some((other) => other !== g && g.contains(other)));
      if (outer.length !== months.length) return [{ month: months[0].month, cells }];
      roots = outer;
    }
    return months.map(({ month }, i) => ({ month, cells: cellsIn(roots[i]) }));
  }
  // Read the open calendar of the field ``ref`` for the date ``iso`` (YYYY-MM-DD). ``seen`` lists
  // the calendars (refs) that were shown before the field was clicked, or null on the first read;
  // ``visible`` in the result lists the ones shown now, for the next read.
  function readCalendar(ref, iso, seen) {
    const el = get(ref);
    if (!el) return { state: "stale" };
    const calendars = shownCalendars(el);
    const visible = calendars.map((item) => refOf(item.el));
    const cal = calendarFor(el, seen, calendars);
    if (!cal) return { state: "closed", visible };
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
    // The months shown, each with its day cells. Most calendars show one month; a range picker
    // often shows two side by side, each under its own title.
    let panels = [];
    if (monthList && yearList) {
      panels = [{ month: { y: +yearList.selectedOptions[0].textContent, m: monthOf(monthList.selectedOptions[0].textContent) }, cells }];
    }
    if (!panels.length) panels = monthPanels(cal, cells);
    if (!panels.length) {
      const dated = cells.filter((c) => c.date && !c.outside);
      if (dated.length) {
        const middle = dated[Math.floor(dated.length / 2)].date;
        panels = [{ month: { y: middle.y, m: middle.m }, cells }];
      }
    }
    const shownMonth = panels.length ? panels[0].month : null;
    const lastMonth = panels.length ? panels[panels.length - 1].month : null;
    // The day to click: by its full date, or by its number in the month shown (skipping the days of
    // the months around it, which repeat numbers at the start and end of the grid).
    let target = cells.find((c) => c.date && c.date.y === ty && c.date.m === tm && c.date.d === td) || null;
    const panel = panels.find((p) => p.month.y === ty && p.month.m === tm);
    if (!target && panel) {
      const inMonth = panel.cells.filter((c) => !c.date && !c.outside);
      const days = inMonth.map((c) => c.day);
      const start = days.findIndex((day, i) => day === 1 && days.slice(i, i + 28).every((d, k) => d === k + 1));
      const run = start >= 0 ? inMonth.slice(start) : inMonth;
      target = run.find((c, k) => c.day === td && (start < 0 || k === td - 1)) || null;
    }
    const nameOfButton = (b) => `${b.getAttribute("aria-label") || ""} ${b.getAttribute("title") || ""} ${b.textContent || ""}`;
    const isCell = (b) => cells.some((c) => c.cell === b);
    const buttons = [...cal.querySelectorAll("button, [role=button], a")].filter((b) => shown(b) && !isCell(b));
    // Arrows with no name: a bare span around an icon (flatpickr), told apart only by a class or a
    // data attribute ("flatpickr-next-month", data-action="prev"). Only an element that holds no day
    // cell and no control counts, so a header around the arrow is not taken for it.
    const icons = [...cal.querySelectorAll("span, div, i, th")].filter(
      (b) =>
        shown(b) && !isCell(b) && !cells.some((c) => b.contains(c.cell)) && !b.querySelector("button, [role=button], a, select, input"),
    );
    const classWords = (b) => `${typeof b.className === "string" ? b.className : ""} ${Object.values(b.dataset || {}).join(" ")}`;
    const nextIcon = (b) => /(^|[^a-z])(next|forward)([^a-z]|$)/i.test(classWords(b));
    const prevIcon = (b) => /(^|[^a-z])(prev|previous|back)([^a-z]|$)/i.test(classWords(b));
    const next =
      buttons.find((b) => /next|›|»|→|forward/i.test(nameOfButton(b)) || nameOfButton(b).trim() === ">") || icons.find(nextIcon);
    const prev =
      buttons.find((b) => /prev|‹|«|←|back/i.test(nameOfButton(b)) || nameOfButton(b).trim() === "<") || icons.find(prevIcon);
    return {
      state: "open",
      visible,
      shown: shownMonth,
      last: lastMonth,
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
  function fieldText(el) {
    return "value" in el && el.tagName !== "BUTTON" ? el.value : el.textContent;
  }
  function showsDate(ref, iso) {
    const el = get(ref);
    return Boolean(el) && textShowsDate(fieldText(el), iso);
  }
  // Does the field show the range from ``startIso`` to ``endIso`` ("20/11/2026 - 23/11/2026")?
  function showsRange(ref, startIso, endIso) {
    const el = get(ref);
    if (!el) return false;
    const parts = fieldText(el).split(/\s+(?:-|to|until)\s+|\s*[\u2013\u2014]\s*/i).map((t) => t.trim()).filter(Boolean);
    return parts.length === 2 && textShowsDate(parts[0], startIso) && textShowsDate(parts[1], endIso);
  }
  function textShowsDate(text, iso) {
    const [y, m, d] = iso.split("-").map(Number);
    const named = fullDate(text);
    if (named) return named.y === y && named.m === m && named.d === d;
    const numbers = (text.match(/\d+/g) || []).map(Number);
    if (numbers.length !== 3) return false;
    const [a, b, c] = numbers;
    return (a === y && b === m && c === d) || (c === y && ((a === m && b === d) || (a === d && b === m)));
  }

  // ---- Uploads --------------------------------------------------------------------------------
  // The names of the files a file input holds now.
  function fileNames(el) {
    return [...(el.files || [])].map((f) => f.name);
  }
  // Whether a file input's accept list allows a file (".pdf", "image/*", "text/csv").
  function acceptsFile(accept, name, type) {
    const tokens = (accept || "").split(",").map((t) => t.trim().toLowerCase()).filter(Boolean);
    if (!tokens.length) return true;
    const n = name.toLowerCase();
    const t = (type || "").toLowerCase();
    return tokens.some((token) => (token.startsWith(".") && n.endsWith(token)) ||
      (token.endsWith("/*") && t.startsWith(token.slice(0, -1))) || token === t);
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

  // ---- Submits the browser refuses --------------------------------------------------------------
  // When a form is sent, the browser fires `invalid` on each field that fails its constraints (a
  // required field left empty, an email with no "@") and shows the reason only in a tooltip that is
  // not in the DOM. Record them, so a step can say which fields the browser refused, and why.
  const invalidEvents = [];
  function onInvalid(event) {
    const el = event.target;
    if (!el || el.nodeType !== 1) return;
    invalidEvents.push({
      ref: refOf(el),
      name: nameOf(el) || el.getAttribute("name") || "",
      message: el.validationMessage || "",
    });
  }
  for (const root of rootsDeep(document)) root.addEventListener("invalid", onInvalid, true);
  // The fields the browser refused since the last call, each once, in the order it refused them.
  function takeInvalid() {
    const seen = new Set();
    return invalidEvents.splice(0).filter((item) => !seen.has(item.ref) && seen.add(item.ref));
  }

  window.__viewspecLift = {
    docId,
    takeInvalid,
    snapshot,
    describe,
    locateAll,
    refOf,
    get,
    nameOf,
    byTagAndName,
    nextChange,
    findOption,
    showsChoice,
    scrollStep,
    scrollNow,
    readCalendar,
    fileNames,
    acceptsFile,
    calendarOpener,
    showsDate,
    showsRange,
    hasValue,
    hasSelected,
    isChecked,
  };
})();
