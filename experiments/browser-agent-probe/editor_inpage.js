// Bounded public-DOM editor semantics. Document baselines and operation plans stay in the browser.
// This is a deliberately small codec, not a Markdown importer or a framework-state adapter.
(() => {
  if (window.__viewspecEditor) return;
  const revisions = new WeakMap(), plans = new WeakMap(), composing = new WeakSet(), listening = new WeakSet();
  const sizes = new WeakMap();
  const MAX_NODES = 1000, MAX_CHARS = 20000;
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const host = el => el?.nodeType === 1 && el.isContentEditable && !el.parentElement?.isContentEditable;
  const owner = el => { for (let p = el; p; p = p.parentElement) if (host(p)) return p; return null; };
  const fail = () => { throw new Error("unsupported editor semantics"); };
  const paragraph = text => ({kind: "p", children: text ? [{text}] : []});
  const proseMirror = el => host(el) && el.classList.contains("ProseMirror");
  // Only the sole empty-block filler has no document meaning. Real hard breaks remain breaks.
  const trailingFiller = (node, root) => proseMirror(root) && node.matches?.('br.ProseMirror-trailingBreak') &&
    node.attributes.length === 1 && node.classList.length === 1 &&
    node.parentElement?.parentElement === root && /^(P|H[1-6])$/.test(node.parentElement.tagName) &&
    node.parentElement.childNodes.length === 1;
  function read(el) {
    if (!host(el)) return undefined;
    listen(el.ownerDocument);
    const mode = el.contentEditable === "plaintext-only" ? "plain" :
      el.classList.contains("mbe-source-mode") ? "source" : "rich";
    const adapter = el.classList.contains("mbe-root") ? "mbe-dom-v1" : "html-dom-v1";
    const result = {mode, adapter, coverage: "unknown"};
    if (composing.has(el)) return result;
    let count = 0, chars = 0, textChars = 0;
    const attr = (node, name) => {
      const value = node.getAttribute(name);
      chars += value?.length || 0;
      if (chars > MAX_CHARS) fail();
      return value;
    };
    const childNodesOf = node => {
      if (node.childNodes.length > MAX_NODES - count) fail();
      const children = [];
      for (const child of node.childNodes) {
        if (child.nodeType === 3 && child.data.length > MAX_CHARS) fail();
        children.push(child);
      }
      return children;
    };
    const childrenOf = node => childNodesOf(node).filter(n => n.nodeType === 1);
    const touch = node => {
      if (++count > MAX_NODES) fail();
      if (node.nodeType === 3) { chars += node.data.length; textChars += node.data.length; if (chars > MAX_CHARS) fail(); }
      if (node.nodeType === 1 && (node.hidden || node.getAttribute("aria-hidden") === "true" ||
          node.hasAttribute("aria-setsize") || node.hasAttribute("aria-posinset") ||
          node.getAttribute("aria-busy") === "true" || node.hasAttribute("data-virtualized"))) fail();
    };
    // Coalesce text split by editor decorations; retain all meaningful whitespace and inline marks.
    const join = children => {
      const out = [];
      for (const item of children.flat()) {
        if (!item) continue;
        if (typeof item.text === "string" && typeof out.at(-1)?.text === "string") out.at(-1).text += item.text;
        else out.push(item);
      }
      return out;
    };
    function inline(node) {
      touch(node);
      if (node.nodeType === 3) return node.data ? [{text: node.data}] : [];
      if (node.nodeType !== 1) return [];
      const tag = node.tagName.toLowerCase();
      if (trailingFiller(node, el)) return [];
      if (node.contentEditable === "false" || node.hasAttribute("style") ||
          (adapter === "html-dom-v1" && node.hasAttribute("class"))) fail();
      if (tag === "br") return node.hasAttribute("data-mbe-fill") ? [] : [{kind: "br"}];
      const children = join(childNodesOf(node).map(inline));
      if (tag === "span" && !node.getAttribute("role")) {
        return node.hasAttribute("data-tag") ? [{kind: "tag", value: attr(node, "data-tag"), children}] : children;
      }
      if (tag === "a") return [{kind: "link", href: attr(node, "href"), target: attr(node, "target"),
        rel: attr(node, "rel"), title: attr(node, "title"), children}];
      if (["strong", "b", "em", "i", "u", "s", "strike", "code", "sub", "sup"].includes(tag))
        return [{kind: ({b: "strong", i: "em", strike: "s"})[tag] || tag, children}];
      fail();
    }
    function contents(node) {
      const children = join(childNodesOf(node).map(inline));
      // A sole BR is the browser's empty-block placeholder. Two BRs remain a real break.
      return children.length === 1 && children[0].kind === "br" ? [] : children;
    }
    function block(node) {
      touch(node);
      if (node.nodeType !== 1 || node.hasAttribute("style") || node.contentEditable === "false") fail();
      const tag = node.tagName.toLowerCase();
      if (/^(p|h[1-6]|blockquote|pre)$/.test(tag)) return {kind: tag, children: contents(node)};
      if (tag === "ul" || tag === "ol") {
        if (childNodesOf(node).some(n => n.nodeType === 3 && n.data.trim())) fail();
        return {kind: tag, start: attr(node, "start"), children: childrenOf(node).map(block)};
      }
      if (tag === "li") return {kind: "li", value: attr(node, "value"), children: contents(node)};
      if (adapter === "mbe-dom-v1" && tag === "div" && node.getAttribute("data-list-type") === "task") {
        const children = childrenOf(node), marker = children[0], content = children[1];
        if (children.length !== 2 || !marker?.classList.contains("mbe-marker") || !content?.classList.contains("mbe-content")) fail();
        const check = marker.firstElementChild;
        if (marker.children.length !== 1 || !check?.matches('input[type="checkbox"]') ||
            childNodesOf(marker).some(n => n.nodeType === 3 && n.data.trim()) || content.hasAttribute("style") ||
            childNodesOf(node).some(n => n.nodeType === 3 && n.data.trim())) fail();
        touch(marker); touch(check); touch(content);
        return {kind: "task", indent: attr(node, "data-indent"), checked: check.checked, children: contents(content)};
      }
      // Native contenteditable commonly uses DIV paragraphs. Nested blocks are unsupported.
      if (adapter === "html-dom-v1" && tag === "div") return {kind: "p", children: contents(node)};
      fail();
    }
    try {
      touch(el);
      result.root = {dir: attr(el, "dir"), style: attr(el, "style"),
        classes: adapter === "html-dom-v1" ? attr(el, "class") : null};
      if (proseMirror(el) && result.root.classes)
        result.root.classes = result.root.classes.split(/\s+/).filter(c => c !== "ProseMirror-focused").join(" ");
      if (el.getAttribute("aria-readonly") === "true") fail();
      if (mode === "source") {
        if (adapter !== "mbe-dom-v1" || el.children.length !== 1 || !el.firstElementChild.matches('.mbe-source')) fail();
        const wrapper = el.firstElementChild;
        if (wrapper.children.length !== 1 || !wrapper.firstElementChild.matches('.mbe-source-text')) fail();
        touch(wrapper); touch(wrapper.firstElementChild);
        const lines = childrenOf(wrapper.firstElementChild);
        if (!lines.length || lines.some(n => !n.matches('.mbe-source-line'))) fail();
        result.text = lines.map(line => {
          touch(line);
          const items = contents(line);
          // Syntax spans are only decoration, never a rich document import contract.
          if (items.some(item => typeof item.text !== "string")) fail();
          return items.map(item => item.text).join("");
        }).join("\n");
      } else if (mode === "plain") {
        // Native plaintext-only DOM: explicit line containers and BRs, no rich semantics.
        const lines = childNodesOf(el).map(node => {
          if (node.nodeType === 1 && /^(DIV|P)$/.test(node.tagName)) { touch(node); return contents(node); }
          return inline(node);
        });
        if (lines.flat().some(n => !Object.hasOwn(n, "text") && n.kind !== "br")) fail();
        result.text = lines.map(line => line.map(n => n.text ?? "\n").join("")).join(
          childrenOf(el).some(n => /^(DIV|P)$/.test(n.tagName)) ? "\n" : "");
      } else {
        const nodes = childNodesOf(el);
        const hasBlocks = nodes.some(n => n.nodeType === 1 && /^(P|DIV|H[1-6]|UL|OL|PRE|BLOCKQUOTE)$/.test(n.tagName));
        result.blocks = hasBlocks ? nodes.filter(n => n.nodeType !== 3 || n.data.trim()).map(block) :
          [{kind: "p", children: contents(el)}];
      }
      if (typeof result.text === "string") chars += Math.max(0, result.text.length - textChars);
      if (chars > MAX_CHARS) fail();
      result.coverage = "complete";
      sizes.set(result, {nodes: count, chars});
    } catch { delete result.root; delete result.text; delete result.blocks; }
    return result;
  }
  function revision() {
    // randomUUID is secure-context-only; ordinary HTTP pages also need opaque revisions.
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
    const hex = [...bytes].map(b => b.toString(16).padStart(2, "0")).join("");
    return `editor-${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  function describe(el) {
    const state = read(el);
    if (!state) return null;
    let record = revisions.get(el);
    const nodes = state.coverage === "complete" ? [...el.children] : [];
    if (!record || !same(record.state, state) || nodes.length !== record.nodes.length || nodes.some((n, i) => n !== record.nodes[i])) {
      const blocks = [];
      if (state.coverage === "complete" && state.mode === "rich" && state.adapter === "html-dom-v1" &&
          nodes.length === state.blocks.length && ![...el.childNodes].some(n => n.nodeType === 3 && n.data.trim())) {
        for (let i = 0; i < nodes.length && blocks.length < 32; i++) {
          const node = nodes[i], block = state.blocks[i];
          if (/^(P|H[1-6])$/.test(node.tagName) && /^(p|h[1-6])$/.test(block.kind) && block.children.every(n => Object.hasOwn(n, "text")) &&
              [...node.childNodes].every(n => n.nodeType === 3 || trailingFiller(n, el) ||
                (n.nodeName === "BR" && !n.attributes.length && node.childNodes.length === 1)))
            blocks.push({ref: revision().replace("editor-", "block-"), position: i + 1, kind: block.kind, node});
        }
      }
      record = {state, revision: revision(), nodes, blocks}; revisions.set(el, record);
    }
    const operations = [];
    if (state.coverage === "complete") {
      if (record.blocks.length) operations.push("replace_block_text");
      if (state.mode !== "rich" || same(state.blocks, [paragraph("")])) operations.push("replace_text");
      const tail = state.blocks?.at(-1);
      if (state.mode === "rich" && tail?.kind === "p" && tail.children.length &&
          el.lastElementChild && /^(P|DIV)$/.test(el.lastElementChild.tagName) &&
          tail.children.every(n => Object.hasOwn(n, "text") || (state.adapter === "mbe-dom-v1" && n.kind === "tag")))
        operations.push("append_paragraph");
    }
    const valueState = state.coverage !== "complete" ? "unknown" :
      (state.mode === "rich" ? same(state.blocks, [paragraph("")]) : state.text === "") ? "empty" : "filled";
    return {version: 1, value_state: valueState, ...Object.fromEntries(["mode", "adapter", "coverage"].map(k => [k, state[k]])),
      revision: record.revision, operations, formats: ["text"],
      ...(record.blocks.length ? {blocks: record.blocks.map(({node, ...block}) => block)} : {})};
  }
  function fits(before, operation, block = null) {
    if (!before || typeof operation?.text !== "string") return false;
    const size = sizes.get(before);
    if (operation.op === "replace_block_text")
      return block && size && size.nodes + 2 <= MAX_NODES &&
        size.chars - before.blocks[block.position - 1].children.map(n => n.text).join("").length + operation.text.length <= MAX_CHARS &&
        !/[\r\n]/.test(operation.text);
    if (operation.op === "append_paragraph")
      return size && size.nodes + 2 <= MAX_NODES && size.chars + operation.text.length <= MAX_CHARS;
    const rootChars = Object.values(before.root || {}).reduce((n, value) => n + (value?.length || 0), 0);
    return rootChars + operation.text.length <= MAX_CHARS &&
      operation.text.split("\n").length * 2 + 4 <= MAX_NODES &&
      (before.mode !== "rich" || !/[\r\n]/.test(operation.text));
  }
  function targetBlock(el, operation) {
    return revisions.get(el)?.blocks.find(b => b.ref === operation.block);
  }
  function replacement(before, operation, block) {
    return {...before, blocks: before.blocks.map((b, i) => i === block.position - 1 ?
      {...b, children: operation.text ? [{text: operation.text}] : []} : b)};
  }
  function expected(el, value) {
    if (!value || !Array.isArray(value.operations) || value.operations.length !== 1) return undefined;
    const descriptor = describe(el), op = value.operations[0], before = state(el);
    const block = targetBlock(el, op);
    if (!fits(before, op, block) || descriptor.revision !== value.base_revision || !descriptor.operations.includes(op.op)) return undefined;
    if (op.op === "replace_block_text") return replacement(before, op, block);
    return op.op === "append_paragraph" ? {...before, blocks: [...before.blocks, paragraph(op.text)]} :
      before.mode === "rich" ? {...before, blocks: [paragraph(op.text)]} : {...before, text: op.text};
  }
  function state(el) { const s = read(el); return s?.coverage === "complete" ? s : undefined; }
  function prepare(el, revision, operation) {
    const descriptor = describe(el);
    if (!descriptor || descriptor.revision !== revision || composing.has(el))
      throw new Error("editor: the editor revision or mode changed; observe and plan again");
    if (!descriptor.operations.includes(operation.op) || typeof operation.text !== "string" ||
        operation.text.length > 10000 || (operation.op === "append_paragraph" && (!operation.text || /[\r\n]/.test(operation.text))))
      throw new Error("editor: unsupported edit; use the observed operations and one plain-text paragraph");
    const before = state(el);
    const block = targetBlock(el, operation);
    if (!fits(before, operation, block)) throw new Error("editor: the requested result exceeds this editor contract; no input ran");
    const expected = operation.op === "replace_block_text" ? replacement(before, operation, block) :
      operation.op === "append_paragraph" ? {...before, blocks: [...before.blocks, paragraph(operation.text)]} : null;
    plans.set(el, {before, expected, operation, block, url: el.ownerDocument.location.href, allowed: before, stopped: false, cleanup: null});
    return true;
  }
  function valid(el) {
    const p = plans.get(el);
    return !!p && el.isConnected && !composing.has(el) && el.ownerDocument.location.href === p.url &&
      (!p.block || el.children[p.block.position - 1] === p.block.node) && same(state(el), p.allowed);
  }
  const focused = el => el.ownerDocument.activeElement === el || el.contains(el.ownerDocument.activeElement);
  function guard(el) {
    if (!valid(el) || !focused(el)) throw new Error("editor: editor changed before input; observe and plan again");
    const p = plans.get(el), win = el.ownerDocument.defaultView;
    const stop = event => {
      const selection = el.ownerDocument.getSelection();
      const range = selection?.rangeCount === 1 ? selection.getRangeAt(0) : null;
      const selected = !p.block || (range && p.block.node.contains(range.startContainer) &&
        p.block.node.contains(range.endContainer) && range.toString() === p.block.node.textContent);
      if (!event.composedPath().includes(el) || !valid(el) || !focused(el) || !selected) { event.preventDefault(); event.stopImmediatePropagation(); p.stopped = true; }
    };
    win.addEventListener("beforeinput", stop, true);
    p.cleanup = () => win.removeEventListener("beforeinput", stop, true);
  }
  function caret(el) {
    if (!valid(el) || !focused(el)) throw new Error("editor: editor changed on focus; observe and plan again");
    const p = plans.get(el);
    if (p.block) {
      const range = el.ownerDocument.createRange(); range.selectNodeContents(p.block.node);
      const selection = el.ownerDocument.getSelection(); selection.removeAllRanges(); selection.addRange(range);
      return;
    }
    if (p.operation.op !== "append_paragraph") return;
    const tail = el.lastElementChild;
    if (!tail || !/^(P|DIV)$/.test(tail.tagName)) throw new Error("editor: no supported paragraph tail");
    const range = el.ownerDocument.createRange(); range.selectNodeContents(tail); range.collapse(false);
    const selection = el.ownerDocument.getSelection(); selection.removeAllRanges(); selection.addRange(range);
  }
  function afterParagraph(el) {
    const p = plans.get(el);
    const actual = state(el);
    const expected = p && {...p.before, blocks: [...p.before.blocks, paragraph("")]};
    const selection = el.ownerDocument.getSelection();
    if (!p || p.stopped || !focused(el) || !same(actual, expected) || !selection?.isCollapsed ||
        !el.lastElementChild?.contains(selection.anchorNode) || el.ownerDocument.location.href !== p.url) return false;
    p.allowed = actual;
    return true;
  }
  function verify(el) {
    const p = plans.get(el), actual = state(el);
    if (!p || p.stopped || !actual || composing.has(el) || el.ownerDocument.location.href !== p.url) return false;
    if (p.expected) return same(actual, p.expected);
    if (actual.mode !== p.before.mode || actual.adapter !== p.before.adapter) return false;
    if (actual.mode !== "rich") return actual.text === p.operation.text;
    return same(actual.blocks, [paragraph(p.operation.text)]);
  }
  function cleanup(el) { plans.get(el)?.cleanup?.(); }
  function needsInput(el) {
    const p = plans.get(el);
    return !p?.block || !same(p.before, p.expected);
  }
  function clear(el) { cleanup(el); plans.delete(el); }
  // Composition is unfinished state, including editors in same-origin frames.
  function listen(doc) {
    if (listening.has(doc)) return;
    listening.add(doc);
    doc.addEventListener("compositionstart", e => { const el = owner(e.target); if (el) composing.add(el); }, true);
    doc.addEventListener("compositionend", e => { const el = owner(e.target); if (el) composing.delete(el); }, true);
  }
  function controlName(el) {
    if (!el.matches('input[type="checkbox"]')) return null;
    const root = owner(el), block = el.closest('[data-list-type="task"]');
    if (!root?.classList.contains("mbe-root") || !block || !root.contains(block) ||
        block.querySelectorAll('input[type="checkbox"]').length !== 1) return null;
    return block.querySelector(':scope > .mbe-content')?.textContent || null;
  }
  window.__viewspecEditor = {host, owner, controlName, describe, state, expected, prepare, valid, guard, caret, afterParagraph, verify, needsInput, cleanup, clear};
})();
