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
  const privateValues = new Set();
  const textEntry = (el) => el.tagName === "TEXTAREA" ||
    (el.tagName === "INPUT" && !/^(button|submit|reset|checkbox|radio|range|color|file|image)$/i.test(el.type)) ||
    el.isContentEditable;
  const privateText = (text) => {
    for (const value of [...privateValues].sort((a, b) => b.length - a.length)) text = text.split(value).join("[redacted]");
    return text;
  };

  // A new document (a real navigation) installs a new library and gets a new id. A hash change or a
  // pushState route change keeps the document, its id, and its refs. The bridge tracks navigation
  // generations separately, so those surviving refs cannot authorize a step from an earlier route.
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
  // Planning names stay complete. Output previews are cut only after the Python privacy projection.
  const clean = (value) => (value || "").replace(/\s+/g, " ").trim();
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
    const sidebar = giteaChoice(el);
    if (sidebar && !el.hasAttribute("aria-label") && !el.hasAttribute("aria-labelledby")) return clean(sidebar.label.innerText);
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
    const editorLabel = window.__viewspecEditor.controlName(el);
    if (editorLabel) return clean(editorLabel);
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
    el.getAttribute("data-lift-secret") === "true" ||
    ((el.tagName === "INPUT" || el.tagName === "TEXTAREA") &&
    ((el.getAttribute("type") || "").toLowerCase() === "password" ||
      SECRET_AUTOCOMPLETE.test(el.getAttribute("autocomplete") || "") ||
      SECRET_NAME.test(el.getAttribute("name") || "") ||
      SECRET_NAME.test(el.id || "") || SECRET_NAME.test(nameOf(el))));

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
    const mode = typeof collectSecrets === "string" ? collectSecrets : "strict";
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
      // Live descriptors are generated here and re-read at action time, never accepted from the page.
      result.removeAttribute("data-lift-editor");
      result.removeAttribute("data-lift-editor-owner");
      const editor = window.__viewspecEditor.describe(el);
      if (editor) result.setAttribute("data-lift-editor", JSON.stringify(editor));
      const editorLabel = window.__viewspecEditor.controlName(el);
      if (editorLabel && !el.hasAttribute("aria-label") && !el.hasAttribute("aria-labelledby") && !el.labels?.length)
        result.setAttribute("aria-label", editorLabel);
      const editorOwner = window.__viewspecEditor.owner(el);
      if (editorOwner && editorOwner !== el) result.setAttribute("data-lift-editor-owner", refOf(editorOwner));
      result.removeAttribute("data-lift-choice");
      if (/^(combobox|listbox)$/.test(el.getAttribute("role") || "") && tag !== "SELECT") {
        result.setAttribute("data-lift-choice", JSON.stringify(choiceState(refOf(el))));
      }
      if (ID_ATTRS.some((name) => result.hasAttribute(name))) {
        const scope = scopeOf(el);
        if (scope !== "0") result.setAttribute("data-lift-scope", scope);
      }

      // Live form state.
      const secret = isSecret(el);
      // A page-authored hint cannot stand in for the live value. Keep only occupancy, before
      // redaction; missing or unsupported state must never imply that a field is empty.
      result.removeAttribute("data-lift-value-state");
      if (textEntry(el)) {
        const value = "value" in el ? el.value : el.textContent;
        result.setAttribute("data-lift-value-state",
          editor ? editor.value_state : typeof value === "string" ? (value === "" ? "empty" : "filled") : "unknown");
      }
      // The private set is independent of the snapshot used to plan interactions. Strict mode
      // treats text-entry values as private regardless of language, label length or field name.
      // A page cannot opt itself out with data-lift-secret=false.
      if (secret || (mode === "strict" && textEntry(el))) {
        const value = "value" in el ? el.value : el.textContent;
        if (value) privateValues.add(value);
      }
      if (tag === "INPUT") {
        const type = (el.getAttribute("type") || "text").toLowerCase();
        if (type === "checkbox" || type === "radio") {
          el.checked ? result.setAttribute("checked", "") : result.removeAttribute("checked");
        } else if (secret) {
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

      // Independent semantic controls remain actionable even inside another control. Only
      // inferred wrappers/decorative descendants share their ancestor's action.
      let inside = state.inside;
      const independent = el.matches('a[href],button,input,select,textarea,summary,[role]:not([role="img"]):not([role="presentation"]):not([role="none"])');
      if (!hidden && (!inside || independent) && el.matches(selector)) {
        const box = el.getBoundingClientRect();
        if (box.width > 0 && box.height > 0) {
          interactive.push({ ref: refOf(el), tag: tag.toLowerCase(), kind: kindOf(el), name: nameOf(el) });
          inside = true;
        }
      }

      if (tag === "TEXTAREA") {
        if (secret) {
          result.textContent = "";
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
    if (collectSecrets) payload.secrets = [...privateValues];
    // What the page wrote into its message regions, and how much it has changed (see the recorder).
    payload.recorder = markReading();
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

  // ---- Identity: the item a control belongs to --------------------------------------------------
  // A ref names a DOM element, and a structural path names a position. Neither says which product an
  // "Add to cart" button is for: a page can write another product into the same elements, or build
  // its list again in another order. The repeated item around the control says it (a card, a table
  // row, a list item), with the text that tells that item from its neighbours.
  //
  // An identity is { tag, name, items }: the control's tag and accessible name, and up to three items
  // around it, innermost first, each { tag, label }. It is read here when the page is observed and
  // checked here before a step runs, so both readings name things the same way.
  const ITEM_TAGS = new Set(["LI", "TR", "ARTICLE", "SECTION", "FIELDSET", "DETAILS", "FORM"]);
  const ITEM_ROLES = new Set(["listitem", "row", "article", "group", "region", "treeitem", "option"]);
  const GENERIC_TAGS = new Set(["DIV", "SPAN"]);
  // Tags that only mark up a phrase: the text around them belongs to the same piece.
  const PHRASE_TAGS = new Set(["B", "STRONG", "I", "EM", "U", "MARK", "SMALL", "SUB", "SUP", "CODE", "ABBR", "BDI", "TIME"]);
  const MAX_ITEMS = 3;
  const MAX_CHUNKS = 8;
  const MAX_CHUNK_NODES = 400;
  const MAX_NEIGHBOUR_STEPS = 20;

  const parentOf = (el) => el.parentElement || el.getRootNode().host || null;
  const firstClass = (el) => (el.getAttribute("class") || "").trim().split(/\s+/)[0];
  const firstRole = (el) => (el.getAttribute("role") || "").trim().toLowerCase().split(" ")[0];
  const newCache = () => ({ chunks: new Map(), labels: new Map() });

  // Whether two siblings are the same kind of thing: two list items, two rows, two custom elements
  // of one kind. A <div> or a <span> says nothing by its tag, so it also needs the same first class.
  function alike(a, b) {
    if (a.tagName !== b.tagName) return false;
    if (ITEM_TAGS.has(a.tagName) || a.tagName.includes("-")) return true;
    const role = firstRole(a);
    if (ITEM_ROLES.has(role)) return firstRole(b) === role;
    return GENERIC_TAGS.has(a.tagName) && firstClass(a) === firstClass(b);
  }

  // The nearest sibling of the same kind on each side.
  function neighbours(el) {
    const found = [];
    for (const step of ["previousElementSibling", "nextElementSibling"]) {
      let node = el[step];
      for (let steps = 0; node && steps < MAX_NEIGHBOUR_STEPS; steps++, node = node[step]) {
        if (alike(el, node)) {
          found.push(node);
          break;
        }
      }
    }
    return found;
  }

  // The first pieces of text in an item, its heading first: what a person reads to tell which item
  // this is. A piece is the text of the element that holds it (a link, a cell, a paragraph), read
  // through <b> and other phrase marks, or an image's alt text.
  function chunksOf(item, cache = newCache()) {
    if (cache.chunks.has(item)) return cache.chunks.get(item);
    const chunks = [];
    const seen = new Set();
    let budget = MAX_CHUNK_NODES;
    let taken = null; // The element whose whole text the last piece took.
    const add = (text) => {
      const value = clean(String(text || ""));
      if (!value || seen.has(norm(value))) return;
      seen.add(norm(value));
      chunks.push(value);
    };
    const visit = (node) => {
      if (budget-- <= 0 || chunks.length >= MAX_CHUNKS) return;
      if (node.nodeType === 3) {
        if (!node.data.trim() || (taken && taken.contains(node))) return;
        let holder = node.parentElement;
        while (holder && holder !== item && PHRASE_TAGS.has(holder.tagName) && holder.parentElement !== item) {
          holder = holder.parentElement;
        }
        if (!holder || holder === item) return add(node.data);
        taken = holder;
        return add(holder.textContent);
      }
      if (node.nodeType !== 1 || SKIP.has(node.tagName) || node.namespaceURI !== "http://www.w3.org/1999/xhtml") return;
      if (node.hidden || node.getAttribute("aria-hidden") === "true") return;
      if (node.tagName === "IMG") return add(node.getAttribute("alt"));
      if (typeof node.checkVisibility === "function" && !node.checkVisibility({ visibilityProperty: true })) {
        if (node.ownerDocument.defaultView.getComputedStyle(node).display !== "contents") return;
      }
      for (const child of composedChildren(node)) visit(child);
    };
    const heading = item.querySelector("h1,h2,h3,h4,h5,h6,[role=heading]");
    // A closed calendar's month is not the visible identity of the field around it.
    if (heading && shown(heading)) add(heading.textContent);
    visit(item);
    cache.chunks.set(item, chunks);
    return chunks;
  }

  // The text that tells a repeated item from its neighbours: its first piece that neither neighbour
  // has, or else its first piece. Empty for an element that is not one of several of a kind.
  function itemLabel(el, cache) {
    if (cache.labels.has(el)) return cache.labels.get(el);
    let label = "";
    const near = neighbours(el);
    if (near.length) {
      const mine = chunksOf(el, cache);
      const theirs = new Set(near.flatMap((other) => chunksOf(other, cache)).map(norm));
      label = mine.find((chunk) => !theirs.has(norm(chunk))) || mine[0] || "";
    }
    cache.labels.set(el, label);
    return label;
  }

  // The items around an element, innermost first. An item whose label is the control's own name adds
  // nothing (a title link names itself), so it is left out.
  function itemsOf(el, name, cache = newCache()) {
    const own = norm(name);
    const items = [];
    for (let node = parentOf(el); node && node.tagName !== "BODY" && items.length < MAX_ITEMS; node = parentOf(node)) {
      const label = itemLabel(node, cache);
      if (label && norm(label) !== own) items.push({ tag: node.tagName.toLowerCase(), label });
    }
    return items;
  }

  function identityOf(el, cache = newCache()) {
    // A form has no short name of its own: its text is everything in it.
    const name = el.tagName === "FORM" ? null : nameOf(el);
    return { tag: el.tagName.toLowerCase(), name, items: itemsOf(el, name || "", cache) };
  }

  // Whether an element still sits in the items it was read in: for each item, innermost first, an
  // ancestor with that tag that still shows the item's label among its first pieces of text. The
  // label only has to be there, so a badge the page adds to the item later changes nothing.
  function inItems(el, items) {
    const cache = newCache();
    let node = parentOf(el);
    for (const item of items) {
      const want = norm(item.label);
      while (node && !(node.tagName.toLowerCase() === item.tag && chunksOf(node, cache).some((chunk) => norm(chunk) === want))) {
        node = parentOf(node);
      }
      if (!node) return false;
      node = parentOf(node);
    }
    return true;
  }

  // A name without its numbers. A button that counts ("Resend code (59s)", "Like 1,204") is still the
  // same button a moment later, so the name of an element that is still in the page is compared
  // without them. The label of an item is compared exactly: "Order 1042" is not "Order 1043".
  const skeleton = (text) => norm(text).replace(/\d[\d.,:]*/g, "#");

  // What has changed about an element that is still in the page, since ``identity`` was read: null,
  // or { kind: "name", now } when it reads as something else, or { kind: "item", now } when the page
  // has written another item around it.
  function changedSince(ref, identity) {
    const el = get(ref);
    if (!el) return { kind: "gone", now: "" };
    const now = nameOf(el);
    if (identity.name !== null && skeleton(now) !== skeleton(identity.name)) return { kind: "name", now };
    if (inItems(el, identity.items)) return null;
    const items = itemsOf(el, now);
    return { kind: "item", now: items.length ? items[0].label : "" };
  }

  // How an element differs from an identity, as a phrase for an error, or null when it fits.
  function differsFrom(el, identity) {
    const tag = el.tagName.toLowerCase();
    if (tag !== identity.tag) return `a <${tag}>, not a <${identity.tag}>`;
    if (identity.name !== null && norm(nameOf(el)) !== norm(identity.name)) {
      return `${JSON.stringify(nameOf(el))}, not ${JSON.stringify(identity.name)}`;
    }
    return inItems(el, identity.items) ? null : `a control outside ${JSON.stringify(identity.items[0].label)}`;
  }

  // The control that an identity names, when the element it was read from is gone or holds another
  // item now: among the visible elements with the same tag and name, the ones in the same items.
  //   { state: "one", ref }      exactly one: it is the control
  //   { state: "many", count }   several, and nothing tells them apart
  //   { state: "moved", items }  none, but the same control shows in other items (their labels)
  //   { state: "none" }          nothing with that tag and name shows
  function successor(identity) {
    const want = identity.name === null ? null : norm(identity.name);
    const named = [];
    for (const root of rootsDeep(document)) {
      for (const el of root.querySelectorAll(CSS.escape(identity.tag))) {
        if (shown(el) && (want === null || norm(nameOf(el)) === want)) named.push(el);
      }
    }
    const fits = named.filter((el) => inItems(el, identity.items));
    if (fits.length === 1) return { state: "one", ref: refOf(fits[0]) };
    if (fits.length > 1) return { state: "many", count: fits.length };
    if (!named.length) return { state: "none" };
    const labels = named.map((el) => itemsOf(el, identity.name || "")[0]).filter(Boolean).map((item) => item.label);
    return { state: "moved", count: named.length, items: [...new Set(labels)].slice(0, 4) };
  }

  // ---- Whether a person could use a control now ---------------------------------------------------
  // A control can be on the page, and readable, without being usable: a dialog covers it, the page
  // has switched its section off (inert), or it ignores the pointer. ``blockedBy`` says which, as a
  // short phrase, or null when nothing here stops a person (or this page cannot tell).
  //
  // Three things stop every kind of step: an inert ancestor, a modal <dialog> the control is not in,
  // and layers that cover the whole viewport (a dialog and its backdrop) without holding the control.
  // Two more stop a step that needs the pointer, but not typing into a field, which a person can
  // reach with the Tab key: something drawn over the control's middle, and pointer-events: none.
  const DIALOG = 'dialog,[role="dialog"],[role="alertdialog"],[aria-modal="true"]';
  const MIN_HIT_PX = 4;
  const COVER_AREA_RATIO = 4;
  const styleOf = (el) => el.ownerDocument.defaultView.getComputedStyle(el);
  const frameOf = (doc) => {
    try {
      return (doc.defaultView && doc.defaultView.frameElement) || null;
    } catch (error) {
      return null; // A cross-origin parent.
    }
  };
  // Whether ``outer`` holds ``inner``, through shadow roots and same-origin frames.
  function holds(outer, inner) {
    for (let node = inner; node; node = parentOf(node) || frameOf(node.ownerDocument)) if (node === outer) return true;
    return false;
  }
  // The element on top at a point of a document's viewport, inside open shadow roots.
  function topAt(doc, x, y) {
    let hit = doc.elementFromPoint(x, y);
    while (hit && hit.shadowRoot) {
      const inner = hit.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === hit) break;
      hit = inner;
    }
    return hit;
  }
  // The nearest ancestor (or the element itself) that stays put while the page scrolls: one that is
  // fixed to the viewport, or sticky. Null for an element that moves with the page.
  function pinnedOf(el, sticky = true) {
    for (let node = el; node && node.nodeType === 1; node = parentOf(node)) {
      const position = styleOf(node).position;
      if (position === "fixed" || (sticky && position === "sticky")) return node;
    }
    return null;
  }
  // The layer an element is drawn in: the nearest dialog around it, or else what pins it to the
  // viewport. Null for an element in the page's own flow.
  function layerOf(el, sticky = false) {
    return el.closest(DIALOG) || pinnedOf(el, sticky);
  }
  // The layers that cover the whole viewport of a document, or null when the page shows through
  // anywhere: nine points, and a layer on top at every one.
  function pageCover(doc) {
    const view = doc.defaultView;
    const layers = new Set();
    for (const fx of [0.1, 0.5, 0.9]) {
      for (const fy of [0.1, 0.5, 0.9]) {
        const hit = topAt(doc, view.innerWidth * fx, view.innerHeight * fy);
        const layer = hit ? layerOf(hit) : null;
        if (!layer) return null;
        layers.add(layer);
      }
    }
    return [...layers];
  }
  // A name for what is in the way: the dialog it belongs to (or the one dialog that shows, for a bare
  // backdrop), the control it is, or "an overlay" with its first words.
  function coverName(cover) {
    const dialog =
      cover.closest(DIALOG) || [...cover.ownerDocument.querySelectorAll(DIALOG)].find((candidate) => shown(candidate));
    if (dialog && (holds(dialog, cover) || !chunksOf(cover).length)) {
      const label = dialog.getAttribute("aria-label") || (dialog.getAttribute("aria-labelledby") ? nameOf(dialog) : "");
      const name = clean(label) || chunksOf(dialog)[0] || "";
      return name ? `dialog ${JSON.stringify(name)}` : "a dialog";
    }
    if (controlLike(cover)) return `${kindOf(cover)} ${JSON.stringify(nameOf(cover))}`;
    const text = chunksOf(cover)[0];
    return text ? `an overlay (${JSON.stringify(text)})` : "an overlay";
  }
  // What is drawn over an element at a point, or null: not the element, anything in it or around it,
  // or its own label, but a layer it is not part of, or something much larger than it (an open menu,
  // a link stretched over a card). A mark the size of the control (a ripple, a floating label) is not
  // an obstacle.
  function coverAt(el, box, x, y) {
    const hit = topAt(el.ownerDocument, x, y);
    if (!hit || holds(el, hit) || holds(hit, el)) return null;
    const label = el.closest("label");
    if ((label && holds(label, hit)) || [...(el.labels || [])].some((own) => holds(own, hit))) return null;
    const layer = layerOf(hit, true);
    if (layer && !holds(layer, el)) return layer;
    let top = hit;
    while (parentOf(top) && !holds(parentOf(top), el)) top = parentOf(top);
    const rect = top.getBoundingClientRect();
    return rect.width * rect.height >= COVER_AREA_RATIO * box.width * box.height ? top : null;
  }
  const typeable = (el) =>
    (el.tagName === "INPUT" && !/^(checkbox|radio|button|submit|reset|image|range)$/i.test(el.type)) ||
    el.tagName === "TEXTAREA" ||
    el.tagName === "SELECT" ||
    el.isContentEditable;

  // ``keys``: the step types into the control or sets its value (a person could Tab to it).
  // ``settled``: the control has just been scrolled into view, so a fixed or sticky bar over it
  // counts. Before that, such a bar says nothing: a scroll would move the control out from under it.
  function blockedBy(el, { keys = false, settled = false, cover = undefined } = {}) {
    for (let node = el; node; node = parentOf(node) || frameOf(node.ownerDocument)) {
      if (node.hasAttribute("inert")) return "inert: the page has switched this part off";
    }
    const top = el.ownerDocument.defaultView.top.document;
    let modal = null;
    try {
      modal = top.querySelector("dialog:modal");
    } catch (error) {
      modal = null; // A browser without :modal.
    }
    if (modal && !holds(modal, el)) return `covered by ${coverName(modal)}`;
    const hit = hitTest(el);
    const layers = cover === undefined ? pageCover(top) : cover;
    if (layers && !layers.some((layer) => holds(layer, el))) {
      // Something covers the whole viewport, and the control is not in it. In view, the control's
      // own middle says whether it is under the cover or over it (a dialog beside its backdrop).
      // Out of view, it is under the cover unless its own layer is the one on top.
      const own = hit ? null : layerOf(el);
      if (hit ? hit.over : !(own && onTop(own))) {
        return `covered by ${coverName(layers.find((layer) => layer.matches(DIALOG)) || (hit && hit.over) || layers[0])}`;
      }
    }
    if (keys && typeable(el)) return null;
    if (styleOf(el).pointerEvents === "none" && !(el.labels && el.labels.length) && !el.closest("label")) {
      return "switched off: it ignores the pointer";
    }
    return hit && hit.over && (settled || !pinnedOf(hit.over)) ? `covered by ${coverName(hit.over)}` : null;
  }

  // What is drawn over the middle of an element's first box, where a click would land, through each
  // frame around it: { over } (null when nothing is), or null when this page cannot tell, because
  // the element is out of view or too small to hit.
  function hitTest(el) {
    let target = el;
    let box = el.getClientRects()[0] || el.getBoundingClientRect();
    if (box.width < MIN_HIT_PX || box.height < MIN_HIT_PX) return null;
    let x = box.left + box.width / 2;
    let y = box.top + box.height / 2;
    for (;;) {
      const view = target.ownerDocument.defaultView;
      if (x < 0 || y < 0 || x >= view.innerWidth || y >= view.innerHeight) return null;
      const over = coverAt(target, box, x, y);
      if (over) return { over };
      const frame = frameOf(target.ownerDocument);
      if (!frame) return { over: null };
      const outer = frame.getBoundingClientRect();
      x += outer.left + frame.clientLeft;
      y += outer.top + frame.clientTop;
      box = outer;
      target = frame;
    }
  }

  // Whether a layer is the one on top, at the middle of the part of it that is in view.
  function onTop(layer) {
    const view = layer.ownerDocument.defaultView;
    const rect = layer.getBoundingClientRect();
    const left = Math.max(rect.left, 0);
    const top = Math.max(rect.top, 0);
    const right = Math.min(rect.right, view.innerWidth);
    const bottom = Math.min(rect.bottom, view.innerHeight);
    if (right - left < 1 || bottom - top < 1) return false;
    const hit = topAt(layer.ownerDocument, (left + right) / 2, (top + bottom) / 2);
    return Boolean(hit) && holds(layer, hit);
  }

  // Whether a step can use an element now: null, or the reason and the control's name for the
  // error. ``settled``: the step has just scrolled the element into view.
  function blockedNow(el, keys, settled) {
    const fileInput = el.tagName === "INPUT" && el.type === "file"; // Files are set without the pointer.
    const reason = blockedBy(el, { keys: keys || fileInput, settled });
    return reason ? { reason, name: nameOf(el) } : null;
  }

  // Capture identity and actionability at observation time, even when no selector is requested.
  // This deliberately does no candidate generation or whole-document selector matching.
  function targetInfo(el, cache, cover) {
    if (!el) return { connected: false };
    return {
      connected: true,
      identity: identityOf(el, cache),
      form_revision: el.tagName === "FORM" ? observeForm(el) : null,
      dialog_revision: el.matches(FIELD_DIALOG) ? observeDialog(el) : null,
      blocked: el.tagName === "FORM" ? null : blockedBy(el, { keys: typeable(el), cover }),
    };
  }

  function readTargets(refs) {
    const cache = newCache();
    const cover = pageCover(document);
    return Object.fromEntries(refs.map((ref) => [ref, targetInfo(get(ref), cache, cover)]));
  }

  // Where an element is: its frame chain (outermost first) and its CSS candidates; what it is (its
  // identity); and what stops a person from using it now (``blocked``), if anything does.
  function describe(ref, cache = newCache(), cover = undefined) {
    const el = get(ref);
    if (!el) return { connected: false };
    const frames = [];
    let doc = el.ownerDocument;
    while (doc.defaultView && doc.defaultView.frameElement) {
      const frame = doc.defaultView.frameElement;
      frames.unshift({ ref: refOf(frame), candidates: candidates(frame) });
      doc = frame.ownerDocument;
    }
    return {
      ...targetInfo(el, cache, cover),
      tag: el.tagName.toLowerCase(),
      frames,
      candidates: candidates(el),
    };
  }

  // Describe many elements in one call. For each, ``css`` (and ``frames``) is the first candidate this
  // page found unique and the same element, by stable attribute or content anchor, in that order.
  // Where this page cannot tell, ``css`` is null and the caller asks Playwright.
  function locateAll(refs) {
    const out = {};
    const cache = newCache();
    const cover = pageCover(document);
    for (const ref of refs) {
      const info = describe(ref, cache, cover);
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

  // ---- What the page did between two readings: the recorder -------------------------------------
  // Two readings of a page show what differs between them. They cannot show a status line that
  // said "Saved" a second time, or a toast that came and went in between. One MutationObserver per
  // root, kept for the life of the document, can. It does two things.
  //
  // It notes every write into a message region: the regions the lift calls messages (role alert,
  // alertdialog, log, or status, or aria-live polite or assertive; the outermost, when one holds
  // another), a region added to the page already holding text, and one that was hidden and now
  // shows. Each note has a sequence number. The last NOTE_LIMIT are kept, and reading them does not
  // clear them: a wait reads the page several times between the two readings that a diff compares.
  //
  // It counts DOM mutations, so a step can say whether the page reacted to it at all, and a wait
  // can tell that the page changed between a reading and the moment the wait began.
  //
  // Some steps the bridge drives through the page's own widgets: it opens a list and types in it,
  // pages through a calendar, scrolls and waits for more to load. What those widgets say on the way
  // and take back again (a calendar's month title, "5 results", "Loading…") is how the step works,
  // not the page's answer to it. The bridge marks the notes written during such a step as asides.
  const NOTE_LIMIT = 48;
  const NOTE_TEXT = 300; // A note keeps this much of what a region says.
  const NOTE_WALK = 4000; // And reads at most this many elements to find it.
  const MESSAGE_ROLES = new Set(["alert", "alertdialog", "log", "status"]);
  const REGION =
    '[role~="alert" i],[role~="alertdialog" i],[role~="log" i],[role~="status" i],[aria-live="polite" i],[aria-live="assertive" i]';
  const CONTROL_CONTENT = new Set(["INPUT", "OPTION", "OPTGROUP", "SELECT", "TEXTAREA"]);
  const recorder = {
    mutations: 0,
    seq: 0,
    notes: [],
    said: new WeakMap(), // Region -> the text it showed when the recorder last looked ("" when hidden).
    watched: new WeakSet(),
    observers: [],
    reading: null, // { mutations, fields } at the last snapshot.
  };

  // The lift's rule (raw_html_agent._message_kind): the first role, or else aria-live.
  function regionKind(el) {
    const role = (el.getAttribute("role") || "").trim().toLowerCase().split(" ")[0];
    if (MESSAGE_ROLES.has(role)) return role;
    const live = (el.getAttribute("aria-live") || "").trim().toLowerCase();
    return live === "polite" || live === "assertive" ? "live" : null;
  }
  // The element above a node in the tree the user sees: through a slot, a shadow root, or a frame.
  function composedParent(node) {
    if (node.assignedSlot) return node.assignedSlot;
    if (node.parentElement) return node.parentElement;
    const root = node.getRootNode();
    if (root.host) return root.host;
    try {
      return root.defaultView && root.defaultView.frameElement;
    } catch (error) {
      return null;
    }
  }
  // The outermost message region that holds a node (or is it), or null.
  function regionOf(node) {
    let found = null;
    for (let el = node.nodeType === 1 ? node : composedParent(node); el; el = composedParent(el)) {
      if (regionKind(el)) found = el;
    }
    return found;
  }
  // Hidden as the lift sees it: the hidden attribute, aria-hidden, or no box (the snapshot's test).
  function unseen(el) {
    if (el.hasAttribute("hidden") || (el.getAttribute("aria-hidden") || "").toLowerCase() === "true") return true;
    if (NO_BOX.has(el.tagName)) return false;
    if (typeof el.checkVisibility !== "function") return el.getClientRects().length === 0;
    return !el.checkVisibility({ visibilityProperty: true }) && getComputedStyle(el).display !== "contents";
  }
  // A region's text as the lift reads a message: what the user sees in it, without what its fields
  // hold; "" for a region the user cannot see. Only the start of a long text is read (NOTE_TEXT
  // characters, NOTE_WALK elements): a page may mark its whole content as a live region, and the
  // recorder runs on every change the page makes.
  function regionText(region) {
    for (let el = region; el; el = composedParent(el)) if (unseen(el)) return "";
    let left = NOTE_WALK;
    let length = 0;
    const walk = (node) => {
      let out = "";
      for (const child of composedChildren(node)) {
        if (left <= 0 || length > 2 * NOTE_TEXT) break;
        if (child.nodeType === 3) {
          const text = privateText(child.data);
          out += text;
          length += text.trim().length;
        }
        if (child.nodeType !== 1 || SKIP.has(child.tagName) || CONTROL_CONTENT.has(child.tagName)) continue;
        left -= 1;
        if (unseen(child)) continue;
        const text = walk(child);
        out += /^inline/.test(getComputedStyle(child).display) ? text : `\n${text}\n`;
      }
      return out;
    };
    return privateText(walk(region)).replace(/\s+/g, " ").trim().slice(0, NOTE_TEXT);
  }
  // Every outermost message region in or at an element (or in a root).
  function regionsIn(node) {
    const found = new Set();
    if (node.nodeType === 1 && regionKind(node)) found.add(regionOf(node));
    if (node.querySelectorAll) for (const el of node.querySelectorAll(REGION)) if (regionKind(el)) found.add(regionOf(el));
    return found;
  }

  // Look at a region again. ``written``: the page wrote into it, so what it says counts as said
  // again even when the words are the same. Otherwise only a region that says something else than
  // it did (one that was hidden and now shows) is noted.
  function look(region, written) {
    const kind = regionKind(region);
    const text = kind ? regionText(region) : "";
    const before = recorder.said.get(region);
    recorder.said.set(region, text);
    if (!text || (!written && before === text)) return;
    const ref = refOf(region);
    // A region that says the same words again and again (a clock the page draws every second) keeps
    // one note, the latest, and does not push the other regions' notes out.
    const last = recorder.notes.findLast((note) => note.ref === ref);
    if (last && last.text === text) recorder.notes.splice(recorder.notes.indexOf(last), 1);
    recorder.seq += 1;
    recorder.notes.push({ seq: recorder.seq, ref, kind, text });
    if (recorder.notes.length > NOTE_LIMIT) recorder.notes.shift();
  }

  function onRecords(records) {
    recorder.mutations += records.length;
    const written = new Set(); // Regions the page wrote into.
    const added = new Set(); // Regions that came into the page.
    const touched = new Set(); // Regions that an attribute may have shown, hidden, or made.
    const redrawn = new Map(); // "kind\ntext" of each region that left the page -> how many.
    for (const record of records) {
      if (record.type === "attributes") {
        for (const region of regionsIn(record.target)) touched.add(region);
        const around = regionOf(record.target);
        if (around) touched.add(around);
        continue;
      }
      const around = regionOf(record.target);
      if (around) {
        written.add(around);
        continue;
      }
      for (const node of record.removedNodes) {
        if (node.nodeType !== 1) continue;
        for (const el of [node, ...node.querySelectorAll(REGION)]) {
          const text = recorder.said.get(el);
          if (!text) continue;
          const key = `${regionKind(el)}\n${text}`;
          redrawn.set(key, (redrawn.get(key) || 0) + 1);
        }
      }
      for (const node of record.addedNodes) {
        if (node.nodeType === 1 && node.isConnected) for (const region of regionsIn(node)) added.add(region);
      }
    }
    for (const region of added) {
      // A page that draws itself again takes a region out and puts one like it back, in one go. That
      // is the same message still showing, not the page saying it again.
      const key = `${regionKind(region)}\n${regionText(region)}`;
      if (!written.has(region) && redrawn.get(key) > 0) {
        redrawn.set(key, redrawn.get(key) - 1);
        recorder.said.set(region, regionText(region));
      } else {
        look(region, true);
      }
    }
    for (const region of written) if (!added.has(region) && region.isConnected) look(region, true);
    for (const region of touched) if (!added.has(region) && !written.has(region) && region.isConnected) look(region, false);
  }

  // Watch every root that is not watched yet, and remember what each region in it says.
  function watch(roots) {
    for (const root of roots) {
      if (recorder.watched.has(root)) continue;
      recorder.watched.add(root);
      const observer = new MutationObserver(onRecords);
      observer.observe(root, { subtree: true, childList: true, attributes: true, characterData: true });
      recorder.observers.push(observer);
      for (const type of ["input", "keyup"]) root.addEventListener(type, noteChoiceQuery, true);
      for (const region of regionsIn(root)) if (!recorder.said.has(region)) recorder.said.set(region, regionText(region));
    }
  }
  // Take in the mutations the browser has queued for the recorder and not yet delivered.
  function flush() {
    for (const observer of recorder.observers) {
      const records = observer.takeRecords();
      if (records.length) onRecords(records);
    }
  }
  // How many DOM mutations this document has had since the recorder began, with the document's id:
  // a count from another document says nothing about this one.
  function mutationCount() {
    flush();
    return { doc: docId, mutations: recorder.mutations };
  }
  // The sequence number of the latest note, and a way to mark every note after one as an aside.
  function noteSeq() {
    flush();
    return recorder.seq;
  }
  function asideSince(seq) {
    flush();
    for (const note of recorder.notes) if (note.seq > seq) note.aside = true;
  }

  // A hash of what the fields hold. A script can answer by setting only .value or .checked, which
  // changes no attribute and no element, so no observer sees it. Only the hash is kept: a field's
  // value, a secret one too, is read to be hashed and never held.
  function fieldState(roots) {
    let hash = 2166136261;
    const mix = (text) => {
      for (let i = 0; i < text.length; i++) hash = Math.imul(hash ^ text.charCodeAt(i), 16777619);
      hash = Math.imul(hash ^ 0x1f, 16777619); // Between fields, so "ab" + "c" is not "a" + "bc".
    };
    for (const root of roots) {
      for (const el of root.querySelectorAll("input,textarea,select")) {
        if (el.tagName === "SELECT") {
          mix(el.multiple ? [...el.selectedOptions].map((option) => option.index).join(",") : String(el.selectedIndex));
        } else {
          mix(String(el.value));
          if (el.tagName === "INPUT") mix(el.checked ? "1" : "0");
        }
      }
    }
    return hash >>> 0;
  }

  // Called by each snapshot. Returns the notes and the mutation count, and keeps what this reading
  // saw, so a wait that begins later can tell whether the page has changed since.
  function markReading() {
    const roots = rootsDeep(document);
    watch(roots);
    flush();
    // What each region says now, for one that showed or hid with no mutation (a style sheet did it).
    for (const root of roots) for (const region of regionsIn(root)) recorder.said.set(region, regionText(region));
    recorder.reading = { mutations: recorder.mutations, fields: fieldState(roots) };
    return { seq: recorder.seq, notes: recorder.notes.slice(), mutations: recorder.mutations };
  }

  // Resolve true after the page changes, once it has been quiet for 100 ms (or 500 ms after the
  // first change, for a page that never stops changing), or false after timeoutMs with no change.
  // A change is a DOM change, or a field that holds something else: every FIELD_POLL_MS the fields
  // are hashed again, because a script that sets only a property (.value, .checked) changes no DOM.
  // With ``sinceReading``, a change the page made after the last snapshot and before this call
  // counts too: the caller read the page, saw nothing, and only then began to wait.
  const FIELD_POLL_MS = 100;
  function nextChange(timeoutMs, sinceReading = false, immediate = false) {
    return new Promise((resolve) => {
      const observers = [];
      const timers = {};
      let done = false;
      const finish = (changed) => {
        if (done) return;
        done = true;
        observers.forEach((observer) => observer.disconnect());
        clearInterval(timers.fields);
        Object.values(timers).forEach(clearTimeout);
        resolve(changed);
      };
      const onChange = () => {
        if (immediate) { if (!timers.cap) timers.cap = setTimeout(() => finish(true), 0); return; }
        if (!timers.cap) timers.cap = setTimeout(() => finish(true), 500);
        clearTimeout(timers.quiet);
        timers.quiet = setTimeout(() => finish(true), 100);
      };
      const roots = rootsDeep(document);
      for (const root of roots) {
        const observer = new MutationObserver(onChange);
        observer.observe(root, { subtree: true, childList: true, attributes: true, characterData: true });
        observers.push(observer);
      }
      const reading = sinceReading ? recorder.reading : null;
      let fields = reading ? reading.fields : fieldState(roots);
      const fieldsChanged = () => {
        const now = fieldState(roots);
        if (now === fields) return false;
        fields = now;
        return true;
      };
      if (reading && (mutationCount().mutations !== reading.mutations || fieldsChanged())) onChange();
      timers.fields = setInterval(() => fieldsChanged() && onChange(), FIELD_POLL_MS);
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
  const choiceQueries = new WeakMap();
  const acceptedChoices = new WeakMap();
  function noteChoiceQuery(event) {
    const el = event.composedPath()[0];
    if (!el || !/^(INPUT|TEXTAREA)$/.test(el.tagName)) return;
    const prior = choiceQueries.get(el);
    choiceQueries.set(el, {text: el.value, generation: (prior?.generation || 0) + 1});
    acceptedChoices.delete(el);
  }
  function choiceQuery(el) {
    if (!el) return null;
    const prior = choiceQueries.get(el);
    if (!prior || prior.text !== el.value) choiceQueries.set(el, {text: el.value || "", generation: (prior?.generation || 0) + 1});
    return {...choiceQueries.get(el)};
  }
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
    const description = (option.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean)
      .map(id => byId(option, id)?.textContent || "").join(" ");
    return { name: clean(lines.join(" ")), label, description: clean(description) };
  }

  // This is deliberately a component adapter, not a rule that arbitrary .item/.checked classes
  // imply choice semantics. Gitea's issue sidebar has explicit cardinality, a checkmark on each
  // value, and a known apply-on-close lifecycle. Other widgets keep the ARIA/manual path.
  function giteaChoice(el) {
    if (!el?.matches?.(".ui.dropdown")) return null;
    const container = el.parentElement;
    if (!container?.matches(".issue-sidebar-combo")) return null;
    const mode = container.getAttribute("data-selection-mode");
    if (!["single", "multiple"].includes(mode) || !["diff", "all"].includes(container.getAttribute("data-update-algo"))) return null;
    const input = container.querySelector(":scope > input.combo-value[type=hidden]");
    const label = el.querySelector(":scope > .fixed-text");
    const popup = el.querySelector(":scope > .menu");
    if (!input || !label || !popup) return null;
    const items = [...popup.querySelectorAll(".menu > .item[data-value]")].filter(o => o.closest(".ui.dropdown") === el);
    if (items.some(o => !o.querySelector(".item-check-mark"))) return null;
    return {container, label, popup, items, mode};
  }
  // Vikunja's label editor: a single-result popup adds to a set of chips. The surrounding
  // labels-list, direct input structure, owned popup and result structure all qualify the
  // adapter; a class or page-authored data-lift hint alone never does.
  function vikunjaLabels(el) {
    if (!el?.matches('input[role="combobox"][aria-autocomplete="list"][aria-haspopup="listbox"]')) return null;
    const root = el.closest('.multiselect[tabindex="-1"][aria-disabled]');
    if (!root?.parentElement?.matches('.labels-list.details') ||
        root.querySelector(':scope > .control > .input-wrapper > input[role="combobox"]') !== el) return null;
    const wrapper = el.parentElement;
    const popup = root.querySelector(':scope > .search-results[role="listbox"]');
    if (popup && listOf(el) !== popup) return null;
    const items = popup ? [...popup.querySelectorAll(':scope > [role="option"]')] : [];
    if (items.some(o => !o.matches('button.search-result-button[type="button"]:has(.hint-text)') && o.getAttribute('aria-disabled') !== 'true')) return null;
    const chips = [...wrapper.querySelectorAll(':scope > .tag')];
    if (chips.some(o => !o.querySelector(':scope > button.delete[type="button"][aria-label]'))) return null;
    return {root, popup, items, chips, busy: Boolean(root.querySelector(':scope > .control.is-loading'))};
  }
  function optionKind(option, text, component, labels) {
    if (component) return "select";
    if (labels) return option.classList.contains('is-create-option') ? "create" : option.matches('button.search-result-button') ? "select" : "unknown";
    if (option.classList.contains('is-create-option')) return "unknown"; // A hint can restrict, never grant, semantic selection.
    if (option.querySelector('button,input,select,textarea,[role="button"]')) return "unknown";
    if (option.matches('a[href]') || option.querySelector('a[href]')) return "navigate";
    // Plain ARIA options retain the generic path. Additional unexplained action text is not
    // silently reduced to the first line; expose it for explicit activation instead.
    return text.name === text.label && !text.description ? "select" : "unknown";
  }
  function optionSignature(option) {
    return JSON.stringify([option.ref, option.label, option.name, option.description, option.kind,
      option.destination, option.selected, Boolean(option.disabled), option.scope]);
  }
  function choiceDisplayMatches(el, label) {
    return Boolean(el) && (!/^(INPUT|TEXTAREA)$/.test(el.tagName) ||
      norm(el.value || shownBeside(el)) === norm(label));
  }
  let choiceActivation = null;
  function finishChoice() {
    const active = choiceActivation;
    if (!active) return null;
    active.cleanup();
    choiceActivation = null;
    return active.stopped;
  }
  function armChoice(ref, option) {
    finishChoice();
    const target = get(option.ref);
    if (!target) throw new Error("choose: the option disappeared");
    const initial = choiceState(ref);
    const active = {url: location.href, popup: initial.popup_ref, query: initial.query, stopped: null};
    const guard = event => {
      if (!event.composedPath().includes(target)) return;
      const now = choiceState(ref);
      const current = now.options.find(o => o.ref === option.ref);
      if (location.href === active.url && now.state !== "loading" && current?.signature === option.signature &&
          now.popup_ref === active.popup && JSON.stringify(now.query) === JSON.stringify(active.query) &&
          !current.disabled && ["select", "navigate"].includes(current.kind) && !active.stopped) return;
      active.stopped = "choose: option meaning, query, or owner changed during activation; inspect the new view";
      event.preventDefault();
      event.stopImmediatePropagation();
    };
    const win = target.ownerDocument.defaultView;
    const events = ["pointerdown", "mousedown", "click"];
    for (const type of events) win.addEventListener(type, guard, true);
    active.cleanup = () => { for (const type of events) win.removeEventListener(type, guard, true); };
    choiceActivation = active;
  }
  function choiceState(ref) {
    const el = get(ref);
    if (!el) return {version: 1, state: "stale", options: [], mode: "unknown"};
    const component = giteaChoice(el);
    const labels = vikunjaLabels(el);
    const list = component?.popup || labels?.popup || listOf(el);
    const open = Boolean(list && shown(list));
    const busy = labels?.busy || list?.getAttribute("aria-busy") === "true";
    const all = component ? component.items : labels ? labels.items : list ? [...list.querySelectorAll('[role="option"]')].filter(o => o.closest('[role="listbox"]') === list) : [];
    const visible = all.filter(shown);
    const multiple = component ? component.mode === "multiple" : (list?.getAttribute("aria-multiselectable") || el.getAttribute("aria-multiselectable")) === "true";
    const search = component ? list.querySelector('input:not([type="hidden"])') : /^(INPUT|TEXTAREA)$/.test(el.tagName) ? el : list?.querySelector('input:not([type="hidden"])');
    const query = choiceQuery(search);
    const options = open ? visible.map(o => {
      const text = optionText(o);
      const option = {ref: refOf(o), ...text, kind: optionKind(o, text, component, labels),
      destination: (o.matches('a[href]') ? o : o.querySelector('a[href]'))?.getAttribute('href') || null,
      selected: component ? o.classList.contains("checked") : o.hasAttribute("aria-selected") ? o.getAttribute("aria-selected") === "true" : null,
      disabled: Boolean(o.getAttribute("aria-disabled") === "true" || o.hasAttribute("disabled") || (component && (o.classList.contains("disabled") || (o.hasAttribute("data-can-change") && o.getAttribute("data-can-change") !== "true")))),
      scope: component ? o.getAttribute("data-scope") || null : null,
      };
      return {...option, signature: optionSignature(option)};
    }) : [];
    // ARIA set-size is evidence about virtualized lists. Without it a custom ARIA list's total
    // coverage remains unknown; single selection can still pick an observed, unique option.
    const sizes = all.map(o => Number(o.getAttribute("aria-setsize"))).filter(n => n > 0);
    const complete = open && !busy && visible.length === all.length &&
      (component ? !search?.value : sizes.length === all.length && sizes.length > 0 && sizes.every(n => n === all.length));
    let known = complete && options.every(o => o.selected !== null);
    let selected = options.filter(o => o.selected === true).map(o => o.label);
    if (!component && !labels && !multiple && !busy) {
      // In a single-choice list, one visible selected option is positive evidence even when
      // filtered options do not establish complete coverage. Missing or conflicting evidence
      // cannot establish a selection, and hidden popup rows are not current visible evidence.
      const picked = options.filter(o => o.selected === true);
      known = picked.length === 1 && picked[0].kind === "select" && choiceDisplayMatches(el, picked[0].label) ||
        picked.length === 0 && known;
    }
    if (labels) {
      known = true;
      selected = labels.chips.filter(shown).map(o => clean(o.querySelector(':scope > span')?.textContent || ""));
    }
    if (component && !open) {
      const summary = component.container.querySelector(":scope > .ui.list");
      known = Boolean(summary && shown(summary));
      selected = known ? [...summary.querySelectorAll(":scope > .item:not(.empty-list)")].filter(shown).map(o => optionText(o).label) : [];
    }
    const residual = open && !options.length && [...list.querySelectorAll('a[href],button,input,[tabindex]')].some(shown);
    return {version: 2, adapter: component ? "gitea-sidebar" : labels ? "vikunja-labels" : "aria", ref, popup_ref: list ? refOf(list) : null,
      search_ref: search ? refOf(search) : null, label: component ? nameOf(el) : null,
      label_ref: component ? refOf(component.label) : null,
      mode: multiple ? "multiple" : "single", expanded: open,
      state: busy ? "loading" : !open ? "closed" : options.length ? "available" : residual && !component ? "unsupported" : "empty",
      coverage: complete ? "complete" : open ? "partial" : "unknown", selection_known: known, selected, options,
      query, operation: labels ? "add" : multiple ? "replace-set" : "replace",
      commit: component ? component.container.getAttribute("data-update-url") ? (multiple ? "close" : "immediate") : "form" : labels ? "immediate" : "unknown",
    };
  }
  // The option named ``value``: by its whole text, then by its label, exactly (ignoring case and runs
  // of whitespace), or else the one option whose label, then whose text, starts with it.
  function findOption(ref, value) {
    const el = get(ref);
    if (!el) return { state: "stale" };
    const descriptor = choiceState(ref);
    if (descriptor.state === "loading") return {state: "missing", coverage: "partial", names: [], open: descriptor.expanded};
    const options = descriptor.options;
    const texts = options;
    const want = norm(value);
    const matching = (test) => texts.map((text, i) => [text, i]).filter(([text]) => test(text));
    let hits = matching((text) => norm(text.name) === want);
    if (hits.length === 0) hits = matching((text) => norm(text.label) === want);
    if (hits.length === 0) hits = matching((text) => norm(text.label).startsWith(want));
    if (hits.length === 0) hits = matching((text) => norm(text.name).startsWith(want));
    if (hits.length === 1) {
      const option = options[hits[0][1]];
      return {state: option.disabled ? "disabled" : ["select", "navigate"].includes(option.kind) ? "found" : "action", ...option,
        query: descriptor.query, popup_ref: descriptor.popup_ref};
    }
    if (hits.length > 1) return { state: "ambiguous", names: hits.map(([text]) => text.label) };
    return { state: "missing", names: texts.map((text) => text.label), open: descriptor.expanded, coverage: descriptor.coverage, unsupported: descriptor.state === "unsupported" };
  }
  // After a choice: does the control show the option, or mark it selected?
  function showsChoice(ref, optionRef, label, before = null) {
    const el = get(ref);
    const option = get(optionRef);
    const want = norm(label);
    const state = choiceState(ref);
    if (state.adapter === "vikunja-labels") return state.state !== "loading" && state.selection_known && state.selected.some(v => norm(v) === want);
    if (option && option.getAttribute("aria-selected") === "true" && choiceDisplayMatches(el, label)) return true;
    if (!el) return false;
    if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
      // Query equality alone is not a selection. A bound option activation must also change
      // the displayed value or close its previously open popup. This is UI evidence only.
      if (norm(el.value) === want) return !state.expanded && before !== null &&
        (norm(before.value) !== want || before.expanded === true) ? true : null;
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
  // the most day cells. Keep that ownership evidence when returning the surrounding calendar.
  function calendarFor(el, seen, calendars) {
    const named = (el.getAttribute("aria-controls") || "").split(/\s+/).filter(Boolean).map((id) => byId(el, id));
    const fieldBox = el.getBoundingClientRect();
    let best = null;
    let bestScore = null;
    let owned = false;
    for (const { el: candidate, count } of calendars || shownCalendars(el)) {
      const isNamed = named.some((n) => n && (n === candidate || n.contains(candidate)));
      if (named.some(Boolean) && !isNamed) continue;
      const isNew = Array.isArray(seen) && !seen.includes(refOf(candidate));
      const near =
        candidate.contains(el) || el.contains(candidate) || boxGap(fieldBox, candidate.getBoundingClientRect()) <= 48;
      if (!isNamed && !isNew && !near) continue;
      const score = [isNamed ? 0 : 1, isNew ? 0 : 1, treeDistance(el, candidate), -count];
      if (!bestScore || lowerScore(score, bestScore)) {
        best = candidate;
        bestScore = score;
        owned = isNamed;
      }
    }
    if (!best) return null;
    // Move up to the dialog around a grid, where the month title and buttons are. Ownership
    // belongs to the matched candidate; aria-controls may name the inner grid, not its wrapper.
    const outer = best.closest("[role=dialog], [class*=calendar i], [class*=picker i]");
    return {calendar: outer && dayCells(outer).length >= dayCells(best).length ? outer : best, owned};
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
    const match = calendarFor(el, seen, calendars);
    if (!match) return { state: "closed", visible };
    const {calendar: cal, owned} = match;
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
      calendar_ref: refOf(cal),
      owned,
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
  function fieldText(el) {
    return "value" in el && el.tagName !== "BUTTON" ? el.value : el.textContent;
  }
  // Format evidence comes from a visible placeholder, never from guessing the requested date.
  function dateFormat(el) {
    const hint = (el.getAttribute("placeholder") || "").trim().toLowerCase().split(/\s+[–—-]\s+|\s+to\s+/)[0];
    let m = hint.match(/^(yyyy|mm|dd)([-/.])(yyyy|mm|dd)\2(yyyy|mm|dd)$/);
    if (m && new Set([m[1], m[3], m[4]]).size === 3) return {order: [m[1][0], m[3][0], m[4][0]], separator: m[2]};
    m = hint.match(/^(\d{1,4})([-/.])(\d{1,2})\2(\d{2,4})$/);
    if (!m) return null;
    const [a, b, c] = [+m[1], +m[3], +m[4]];
    const order = m[1].length === 4 ? ["y", "m", "d"] : c > 31 && a > 12 && a <= 31 && b <= 12 ? ["d", "m", "y"] :
      c > 31 && b > 12 && b <= 31 && a <= 12 ? ["m", "d", "y"] : null;
    return order ? {order, separator: m[2]} : null;
  }
  function dateFillValue(el, iso) {
    if (el.tagName !== "INPUT" || !/^(text|search)$/.test(el.type) || el.readOnly || el.disabled) return null;
    const format = dateFormat(el);
    if (!format) return null;
    const [y, m, d] = iso.split("-");
    return format.order.map(k => ({y, m, d})[k]).join(format.separator);
  }
  function showsDate(ref, iso) {
    const el = get(ref);
    return el ? textShowsDate(fieldText(el), iso, dateFormat(el)) : null;
  }
  function showsRange(ref, startIso, endIso) {
    const el = get(ref);
    if (!el) return null;
    const parts = fieldText(el).split(/\s+(?:-|to|until)\s+|\s*[\u2013\u2014]\s*/i).map(t => t.trim()).filter(Boolean);
    if (parts.length !== 2) return false;
    const matches = [textShowsDate(parts[0], startIso, dateFormat(el)), textShowsDate(parts[1], endIso, dateFormat(el))];
    return matches.includes(false) ? false : matches.includes(null) ? null : true;
  }
  function textShowsDate(text, iso, format) {
    const [y, m, d] = iso.split("-").map(Number);
    const named = fullDate(text);
    if (named) return named.y === y && named.m === m && named.d === d;
    const match = text.trim().match(/^(\d{1,4})[-/.](\d{1,2})[-/.](\d{2,4})$/);
    if (!match) return false;
    const [a, b, c] = match.slice(1).map(Number);
    const order = format?.order || (match[1].length === 4 ? ["y", "m", "d"] : a > 12 ? ["d", "m", "y"] : b > 12 ? ["m", "d", "y"] : a === b ? ["m", "d", "y"] : null);
    if (!order) return null;
    const read = Object.fromEntries(order.map((k, i) => [k, [a, b, c][i]]));
    return read.y === y && read.m === m && read.d === d;
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
    if (window.__viewspecEditor.host(el)) {
      const state = window.__viewspecEditor.state(el);
      return !!state && state.mode !== "rich" && state.text === value;
    }
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

  // Private, bounded form state. Only indexes/statuses leave this closure; baselines may contain
  // passwords. A contract is scoped to one operation and is cleared on every exit.
  let formContract = null;
  const equalState = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const formObservations = new WeakMap();
  let formRevision = 0;
  // Native getters avoid named controls shadowing form.action/elements/requestSubmit. The
  // descriptor remains private: URLs, button values and field names never enter public metadata.
  function nativeForm(form, property) {
    const proto = form.ownerDocument.defaultView.HTMLFormElement.prototype;
    return Object.getOwnPropertyDescriptor(proto, property).get.call(form);
  }
  function ownsField(form, el) {
    return el && ("form" in el ? el.form === form : el.closest("form") === form);
  }
  function formMembers(form) {
    const native = [...nativeForm(form, "elements")];
    const custom = [...form.querySelectorAll('[role=combobox],[role=checkbox],[role=radio],[role=switch],[role=textbox],[contenteditable=true],input[type=image]')];
    if (native.length + custom.length > 512) return null;
    return [...new Set([...native, ...custom])].filter(el => ownsField(form, el) && shown(el));
  }
  function submitControl(el) {
    return /^(BUTTON|INPUT)$/.test(el.tagName) && /^(submit|image)$/.test(el.type);
  }
  function businessField(el) {
    if (/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) return !/^(hidden|submit|image|button|reset)$/.test(el.type);
    return /^(combobox|checkbox|radio|switch|textbox)$/.test(el.getAttribute("role")) ||
      (el.isContentEditable && !el.parentElement?.isContentEditable);
  }
  function submissionState(form, button = null) {
    return {
      tag: button?.tagName, type: button?.type, name: button?.getAttribute("name"),
      value: button?.value, label: button ? nameOf(button) : null,
      action: button?.hasAttribute("formaction") ? button.formAction : nativeForm(form, "action"),
      method: button?.hasAttribute("formmethod") ? button.formMethod : nativeForm(form, "method"),
      enctype: button?.hasAttribute("formenctype") ? button.formEnctype : nativeForm(form, "enctype"),
      target: button?.hasAttribute("formtarget") ? button.formTarget : nativeForm(form, "target"),
      noValidate: nativeForm(form, "noValidate") || Boolean(button?.formNoValidate),
    };
  }
  function formDescriptor(form) {
    if (!form?.isConnected || form.tagName !== "FORM") return null;
    // isConnected alone is true for nodes in a detached/replaced iframe document.
    let doc = form.ownerDocument;
    try {
      while (doc !== document) {
        const frame = doc.defaultView?.frameElement;
        if (!frame?.isConnected || frame.contentDocument !== doc) return null;
        doc = frame.ownerDocument;
      }
    } catch { return null; }
    const members = formMembers(form);
    if (!members || members.length > 256) return null;
    const fields = members.filter(businessField).map(el => ({tag: el.tagName, type: el.type,
      name: el.getAttribute("name"), role: el.getAttribute("role"), label: nameOf(el), multiple: el.multiple,
      // Disabled fields are absent from the observed argument/omission contract. Becoming
      // enabled changes field coverage; this is distinct from submitter readiness.
      disabled: el.matches(":disabled") || el.getAttribute("aria-disabled") === "true"}));
    const buttons = members.filter(submitControl);
    const state = {url: form.ownerDocument.location.href, name: form.getAttribute("aria-label"), fields, submission: submissionState(form),
      buttons: buttons.map(el => submissionState(form, el))};
    if (JSON.stringify(state).length > 65536) return null;
    return {state, buttons, document: form.ownerDocument};
  }
  function observeForm(form) {
    const descriptor = formDescriptor(form);
    if (!descriptor) return null;
    let record = formObservations.get(form);
    if (!record || record.document !== descriptor.document || !equalState(record.state, descriptor.state)) {
      record = {...descriptor, revision: `form-${docId}-${++formRevision}`};
      formObservations.set(form, record);
    } else {
      // Refresh equivalent DOM successors without changing the observation's semantic revision.
      record.buttons = descriptor.buttons;
    }
    return record.revision;
  }
  // Dialog scopes share field semantics, but never acquire native submit authority.
  const FIELD_DIALOG = 'dialog,[role="dialog"]';
  function ownsDialogField(scope, el) {
    return Boolean(el && !el.form && !el.closest("form") && el.closest(FIELD_DIALOG) === scope);
  }
  function dialogMembers(scope) {
    if (!scope?.isConnected || !scope.matches(FIELD_DIALOG) || !shown(scope)) return null;
    const candidates = [...scope.querySelectorAll('input,select,textarea,[role],[contenteditable]')];
    if (candidates.length > 512) return null;
    return candidates.filter(el => shown(el) && el.closest(FIELD_DIALOG) === scope &&
      (businessField(el) || /^(slider|spinbutton|listbox)$/.test(el.getAttribute("role"))));
  }
  function dialogFieldRoots(scope) {
    return dialogMembers(scope)?.filter(el => {
      const editor = window.__viewspecEditor.owner(el);
      if (editor && editor !== el) return false;
      const choice = el.parentElement?.closest('[role=combobox],[role=listbox]');
      return !choice || !scope.contains(choice);
    });
  }
  function dialogDescriptor(scope) {
    const members = dialogFieldRoots(scope);
    if (!members?.length || members.length > 256 || members.some(el => !ownsDialogField(scope, el))) return null;
    let doc = scope.ownerDocument;
    try {
      while (doc !== document) {
        const frame = doc.defaultView?.frameElement;
        if (!frame?.isConnected || frame.contentDocument !== doc) return null;
        doc = frame.ownerDocument;
      }
    } catch { return null; }
    const state = {url: scope.ownerDocument.location.href, tag: scope.tagName, role: scope.getAttribute("role"),
      name: nameOf(scope), parent: scope.parentElement?.closest(FIELD_DIALOG) ? refOf(scope.parentElement.closest(FIELD_DIALOG)) : null,
      fields: members.map(el => ({tag: el.tagName, type: el.type, name: el.getAttribute("name"),
        label: nameOf(el), role: el.getAttribute("role"), multiple: el.multiple,
        disabled: !formEnabled(el), readonly: Boolean(el.readOnly)}))};
    if (JSON.stringify(state).length > 65536) return null;
    return {state, buttons: [], document: scope.ownerDocument};
  }
  function observeDialog(scope) {
    const descriptor = dialogDescriptor(scope);
    if (!descriptor) return null;
    let record = formObservations.get(scope);
    if (!record || record.document !== descriptor.document || !equalState(record.state, descriptor.state)) {
      record = {...descriptor, revision: `dialog-${docId}-${++formRevision}`};
      formObservations.set(scope, record);
    }
    return record.revision;
  }
  function contractOwns(el) {
    return formContract.kind === "dialog" ? ownsDialogField(formContract.form, el) : ownsField(formContract.form, el);
  }
  function contractMembers() {
    return formContract.kind === "dialog" ? dialogFieldRoots(formContract.form) : formMembers(formContract.form);
  }
  function formScopeValid() {
    const c = formContract;
    if (!c || !c.bound || location.href !== c.url || c.form.ownerDocument !== c.document) return false;
    const current = c.kind === "dialog" ? dialogDescriptor(c.form) : formDescriptor(c.form);
    // Record editors often echo a newly supplied Name in the dialog's accessible label.
    // Accept only an exact echo of a non-private, touched field that currently matches its
    // requested value. Every other scope component and every field identity must still match.
    if (current && c.kind === "dialog" && current.state.name !== c.state.name &&
        equalState({...current.state, name: c.state.name}, c.state) && c.fields.some(field => {
          const el = get(field.ref);
          return field.label_echo && c.edited.has(el) && ownsDialogField(c.form, el) &&
            fieldStateFor(el, field.kind) === field.expected && field.expected.replace(/\s+/g, " ").trim() === current.state.name;
        })) c.state = {...c.state, name: current.state.name};
    return current !== null && equalState(current.state, c.state);
  }
  function boundSubmitter() {
    const c = formContract;
    if (!formScopeValid()) return null;
    if (!c.button) return c.form;
    if (c.button.isConnected) return ownsField(c.form, c.button) &&
      equalState(submissionState(c.form, c.button), c.buttonState) ? c.button : null;
    const fits = formMembers(c.form).filter(el => submitControl(el) && equalState(submissionState(c.form, el), c.buttonState));
    if (fits.length !== 1) return null;
    c.button = fits[0];
    return c.button;
  }
  function formEnabled(el) {
    if (!el || el.matches(":disabled")) return false;
    for (let node = el; node; node = parentOf(node))
      if (node.hasAttribute?.("inert") || node.getAttribute?.("aria-disabled") === "true") return false;
    return true;
  }
  function contractTarget(field) {
    const c = formContract;
    let el = get(field.ref);
    if (el) return contractOwns(el) && (!field.identity || differsFrom(el, field.identity) === null) ? el : null;
    if (!field.identity || field.kind === "editor" || !c.form?.isConnected) return null;
    const fits = (contractMembers() || []).filter(el => businessField(el) && differsFrom(el, field.identity) === null);
    if (fits.length !== 1) return null;
    field.ref = refOf(fits[0]);
    return fits[0];
  }
  function fieldStateFor(el, kind) {
    if (!el) return undefined;
    if (kind === "editor") return window.__viewspecEditor.state(el);
    if (kind === "boolean") {
      if (el.tagName === "INPUT" && /^(checkbox|radio)$/.test(el.type)) return el.checked;
      const checked = el.getAttribute("aria-checked");
      return checked === "true" ? true : checked === "false" ? false : undefined;
    }
    if (kind === "select" || kind === "multi") {
      return el.tagName === "SELECT" ? [...el.selectedOptions].map(o => o.value).sort() : undefined;
    }
    if (kind === "file") return el.files ? [...el.files].map(f => ({name: f.name, size: f.size})) : undefined;
    if (kind === "choose") {
      const state = choiceState(refOf(el));
      if (state.selection_known) return state.selected.map(norm).sort();
      if (state.mode === "multiple") return undefined;
      if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
        if (state.expanded) return undefined;
        const accepted = acceptedChoices.get(el);
        if (el.value) return accepted && norm(el.value) === accepted.label ? accepted.label : undefined;
        return norm(shownBeside(el));
      }
      const copy = el.cloneNode(true);
      copy.querySelectorAll("[role=listbox],[role=option]").forEach(o => o.remove());
      return norm(copy.textContent);
    }
    return fieldText(el);
  }
  function beginForm(fields, scope) {
    clearForm();
    const form = get(scope.form_ref), observed = formObservations.get(form);
    const button = scope.submit_ref ? get(scope.submit_ref) : null;
    const index = observed?.buttons.indexOf(button) ?? -1;
    const bound = Boolean(observed && observed.revision === scope.form_revision &&
      (!scope.requested || (scope.submit_ref ? index >= 0 : observed.buttons.length === 0)));
    formContract = {fields, form, kind: scope.kind || "form", document: observed?.document, button, state: observed?.state, bound,
      buttonState: index >= 0 ? observed.state.buttons[index] : null,
      requested: scope.requested, url: location.href, cleanup: null, stopped: null,
      phase: "preparing", activation: "not_dispatched", native_submissions: 0, prevented_submissions: 0,
      touched: null, edited: new Set(), deadline: Infinity};
    if (!formScopeValid()) { formContract.bound = false; return fields.map((_, i) => ({index: i + 1, status: "unknown"})); }
    if (formContract.kind === "dialog") {
      // Exact coverage: every enabled root field must have an intent, and no intent may
      // reach outside this observed dialog. Unsupported/omitted fields cannot disappear.
      const members = (dialogFieldRoots(form) || []).filter(formEnabled);
      const targets = fields.map(field => contractTarget(field));
      if (scope.requested || targets.some(el => !el) || new Set(targets).size !== targets.length ||
          members.length !== targets.length || members.some(el => !targets.includes(el))) {
        formContract.bound = false;
        return fields.map((_, i) => ({index: i + 1, status: "unknown"}));
      }
    } else installFormGuard();
    for (const field of fields) {
      const el = contractTarget(field);
      // Store the input kind too: reusing a text element as a hidden/radio field changes its contract.
      field.inputType = el?.tagName === "INPUT" ? el.type : null;
      if (field.preserve) field.expected = fieldStateFor(el, field.kind);
      if (field.kind === "editor") {
        field.editorMode = window.__viewspecEditor.describe(el)?.mode;
        if (!field.preserve) field.expected = window.__viewspecEditor.expected(el, field.expected);
        if (window.__viewspecEditor.describe(el)?.revision !== field.editor_revision) field.expected = undefined;
      }
      if (["select", "multi"].includes(field.kind) && field.expected) field.expected.sort();
      if (field.kind === "choose" && !field.preserve) field.expected = Array.isArray(field.expected)
        ? field.expected.map(norm).sort() : norm(field.expected);
    }
    return checkForm(true);
  }
  function checkForm(baselineOnly = false) {
    if (!formContract) return [{index: 0, status: "unknown"}];
    const validScope = formScopeValid();
    return formContract.fields.map((field, i) => {
      const el = contractTarget(field);
      let status = "unknown";
      if (validScope && el &&
          (el.tagName !== "INPUT" || el.type === field.inputType)) {
        if (field.kind === "editor" && window.__viewspecEditor.describe(el)?.mode !== field.editorMode) status = "mismatch";
        else if (baselineOnly && !field.preserve && (field.kind !== "editor" || field.expected !== undefined)) status = "not_run";
        else if (field.expected !== undefined) {
          let matches;
          if (!field.preserve && field.kind === "date") matches = showsDate(field.ref, field.expected);
          else if (!field.preserve && field.kind === "range") matches = showsRange(field.ref, ...field.expected.split("/"));
          else {
            const actual = fieldStateFor(el, field.kind);
            let expected = field.expected;
            if (field.kind === "choose") {
              if (Array.isArray(actual) && typeof expected === "string") expected = [expected];
              if (typeof actual === "string" && Array.isArray(expected) && expected.length === 1) expected = expected[0];
            }
            matches = actual === undefined ? null : equalState(actual, expected);
          }
          status = matches === true ? "matched" : matches === false ? "mismatch" : "unknown";
        }
      }
      return {index: i + 1, status};
    });
  }
  function acceptFormChoice(ref, chosen, selected) {
    const el = get(ref);
    if (el) acceptedChoices.set(el, {label: norm(chosen)});
    if (!formContract) return;
    for (const field of formContract.fields) {
      if (field.ref !== ref || field.preserve || field.kind !== "choose") continue;
      field.expected = Array.isArray(field.expected) || choiceState(ref).operation === "add" ? selected?.map(norm).sort() : norm(chosen);
    }
  }
  function formBusy(target = null) {
    if (!formContract) return true;
    const submitter = typeof target === "string" ? get(target) : target;
    const busy = el => {
      for (let node = el; node; node = parentOf(node)) if (node.getAttribute?.("aria-busy") === "true") return true;
      return el?.form?.getAttribute("aria-busy") === "true";
    };
    // Missing or mismatched controls are refused by checkForm, not mistaken for work in flight.
    // Resolve the same identity-checked successors as the field contract after app re-renders.
    return busy(submitter) || formContract.fields.some(field => busy(contractTarget(field)));
  }
  function formStatus() {
    const target = boundSubmitter();
    return {valid: formScopeValid() && (!formContract.requested || Boolean(target)),
      target_ref: target ? refOf(target) : null, enabled: formEnabled(target), busy: formBusy(target),
      fields: checkForm()};
  }
  function touchForm(el) {
    if (!formContract) return;
    if (!formScopeValid() || !contractOwns(el))
      throw new Error("form-unknown: the observed form scope changed; submission withheld");
    formContract.touched = el;
    formContract.edited.add(el);
  }
  function finalizeForm() {
    const c = formContract;
    if (!c) return;
    c.phase = "finalizing";
    const active = c.form?.ownerDocument.activeElement;
    if (c.touched && active && (active === c.touched || c.touched.contains(active))) active.blur();
  }
  function installFormGuard() {
    const c = formContract;
    const seen = new WeakSet();
    const guard = event => {
      const target = boundSubmitter();
      const submit = event.type === "submit";
      if (!(submit ? event.target === c.form || event.submitter === c.button :
        event.composedPath().includes(c.button))) return;
      if (seen.has(event)) return;
      seen.add(event);
      if (submit && c.phase !== "activating") {
        c.prevented_submissions++;
        event.preventDefault(); event.stopImmediatePropagation();
        return;
      }
      if (c.phase !== "activating") return;
      const fields = checkForm();
      const allowed = target && formEnabled(target) && !formBusy(target) &&
        performance.now() < c.deadline && fields.every(f => f.status === "matched") &&
        (!submit || (event.target === c.form && (event.submitter || c.form) === target && c.native_submissions === 0));
      if (allowed && !c.stopped) {
        if (event.type === "click" || submit) c.activation = "dispatched";
        if (submit) c.native_submissions++;
        return;
      }
      if (submit) c.prevented_submissions++;
      // Duplicate native submits are prevented without erasing the first approved dispatch.
      if (!submit || c.native_submissions === 0) c.stopped ||= fields.every(f => f.status === "matched") ? [{index: 0, status: "unknown"}] : fields;
      event.preventDefault(); event.stopImmediatePropagation();
    };
    const events = ["pointerdown", "mousedown", "click", "submit"];
    const view = c.form.ownerDocument.defaultView;
    // The form listener travels with DOM adoption, so a move cannot bypass the native-submit
    // guard before the next scope check. Each event is counted only once across both listeners.
    const targets = [view, c.form];
    for (const target of targets) for (const type of events) target.addEventListener(type, guard, true);
    c.cleanup = () => {
      for (const target of targets) for (const type of events) target.removeEventListener(type, guard, true);
    };
  }
  function armForm(submitter, remaining) {
    const c = formContract;
    if (!c) return [{index: 0, status: "unknown"}];
    const fields = checkForm();
    if (boundSubmitter() !== submitter || !formEnabled(submitter) || formBusy(submitter) || remaining <= 0 || fields.some(f => f.status !== "matched")) {
      c.stopped = fields.some(f => f.status !== "matched") ? fields : [{index: 0, status: "unknown"}];
      return c.stopped;
    }
    c.deadline = performance.now() + remaining;
    c.phase = "activating";
    return fields;
  }
  function disarmForm() {
    if (!formContract) return null;
    formContract.phase = "finished";
    return formContract.stopped;
  }
  function formProgress() {
    if (!formContract) return null;
    const c = formContract;
    const fields = checkForm();
    const reason = c.native_submissions ? "native_submit_seen" : c.stopped ? "event_refused" :
      c.activation === "dispatched" ? "activation_only" : !formScopeValid() ? "scope_changed" :
      fields.some(f => f.status === "mismatch") ? "field_mismatch" :
      fields.some(f => f.status !== "matched") ? "field_unknown" : c.requested ? "readiness_pending" : "prepared";
    return {fields, submission: !c.requested ? "not_requested" : c.activation === "dispatched" && !c.stopped ? "attempted" : "withheld",
      preparation: {version: 1, phase: c.phase, activation: c.activation, reason_code: reason,
        native_submissions: c.native_submissions, prevented_submissions: c.prevented_submissions}};
  }
  function clearForm() {
    formContract?.cleanup?.();
    formContract = null;
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

  watch(rootsDeep(document));

  window.__viewspecLift = {
    docId,
    beginForm, checkForm, formBusy, armForm, disarmForm, clearForm, acceptFormChoice, dateFillValue,
    formStatus, touchForm, finalizeForm, formProgress,
    takeInvalid,
    mutationCount,
    noteSeq,
    asideSince,
    snapshot,
    describe,
    locateAll,
    readTargets,
    refOf,
    get,
    nameOf,
    changedSince,
    differsFrom,
    successor,
    blockedNow,
    nextChange,
    findOption,
    armChoice, finishChoice,
    choiceState,
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
