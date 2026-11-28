// DOM and native input binding. Shared Lift code supplies semantic identity checks.
const expression = (fn, arg) =>
  typeof fn === "string"
    ? fn
    : `(${fn.toString()})(${arg === undefined ? "undefined" : JSON.stringify(arg)})`;
function valueOf(remote) {
  if (remote.unserializableValue) {
    const values = {
      NaN: NaN,
      Infinity: Infinity,
      "-Infinity": -Infinity,
      "-0": -0,
    };
    return values[remote.unserializableValue];
  }
  return remote.value;
}
function query(spec) {
  const clean = (text) =>
    String(text || "")
      .replace(/\s+/g, " ")
      .trim();
  const roots = [document];
  for (let i = 0; i < roots.length; i++)
    for (const element of roots[i].querySelectorAll("*"))
      if (element.shadowRoot) roots.push(element.shadowRoot);
  const all = (selector) =>
    roots.flatMap((root) => [...root.querySelectorAll(selector)]);
  const name = (el) =>
    window.top.__viewspecLift?.nameOf(el) ||
    el.getAttribute("aria-label") ||
    clean(el.innerText || el.value || "");
  let nodes;
  if (spec.css) {
    const texts = [];
    const css = spec.css.replace(
      /:has-text\(("(?:[^"\\]|\\.)*")\)/g,
      (_, literal) => {
        texts.push(clean(JSON.parse(literal)).toLowerCase());
        return "";
      },
    );
    nodes = all(css).filter((el) =>
      texts.every((text) => clean(el.textContent).toLowerCase().includes(text)),
    );
  } else if (spec.role) {
    const role = (el) =>
      el.getAttribute("role") ||
      {
        BUTTON: "button",
        A: el.hasAttribute("href") ? "link" : "",
        TEXTAREA: "textbox",
        SELECT: el.multiple || el.size > 1 ? "listbox" : "combobox",
        OPTION: "option",
        SUMMARY: "button",
        IMG: "img",
        H1: "heading",
        H2: "heading",
        H3: "heading",
        H4: "heading",
        DIALOG: "dialog",
        FORM: "form",
      }[el.tagName] ||
      (el.tagName === "INPUT"
        ? {
            checkbox: "checkbox",
            radio: "radio",
            button: "button",
            submit: "button",
            reset: "button",
            range: "slider",
            number: "spinbutton",
          }[el.type] || "textbox"
        : "");
    nodes = all("*").filter(
      (el) =>
        role(el) === spec.role &&
        (spec.name === undefined ||
          (spec.exact
            ? clean(name(el)) === clean(spec.name)
            : clean(name(el))
                .toLowerCase()
                .includes(clean(spec.name).toLowerCase()))),
    );
  } else {
    const matches = (el) => {
      const text = clean(
        el.matches("input[type=button],input[type=submit]")
          ? el.value
          : el.textContent,
      );
      return spec.exact
        ? text === clean(spec.text)
        : text.toLowerCase().includes(clean(spec.text).toLowerCase());
    };
    nodes = all("*").filter(
      (el) =>
        !el.matches("script,style") &&
        matches(el) &&
        ![...el.children].some(matches),
    );
  }
  return nodes;
}

class Handle {
  constructor(frame, remote) {
    this.frame = frame;
    this.page = frame.page();
    this.remote = remote;
    this.disposed = false;
  }
  asElement() {
    return this.remote.subtype === "node" ? this : null;
  }
  async dispose() {
    if (!this.disposed && this.remote.objectId) {
      this.disposed = true;
      await this.page.session
        .send("Runtime.releaseObject", { objectId: this.remote.objectId })
        .catch(() => {});
    }
  }
  async run(fn, arg, byValue) {
    if (this.disposed || !this.remote.objectId)
      throw new Error("Element handle is no longer available");
    const reply = await this.page.session.send("Runtime.callFunctionOn", {
      objectId: this.remote.objectId,
      functionDeclaration: `function(arg){return (${fn.toString()})(this,arg);}`,
      arguments: [arg === undefined ? {} : { value: arg }],
      returnByValue: byValue,
      awaitPromise: true,
      userGesture: true,
    });
    if (reply.exceptionDetails)
      throw new Error(
        reply.exceptionDetails.exception?.description ||
          reply.exceptionDetails.text ||
          "Element evaluation failed",
      );
    return byValue
      ? valueOf(reply.result)
      : new Handle(this.frame, reply.result);
  }
  evaluate(fn, arg) {
    return this.run(fn, arg, true);
  }
  evaluateHandle(fn, arg) {
    return this.run(fn, arg, false);
  }
  async checked(editable = false) {
    const reason = await this.evaluate((el, editable) => {
      if (!el.isConnected) return "element detached";
      if (
        el.matches(":disabled") ||
        el.closest('[aria-disabled="true"],[inert]')
      )
        return "element disabled";
      if (!el.checkVisibility({ checkVisibilityCSS: true }))
        return "element is not visible";
      if (
        editable &&
        (el.readOnly || el.getAttribute("aria-readonly") === "true")
      )
        return "element is readonly";
      return null;
    }, editable);
    if (reason) throw new Error(reason);
  }
  async scrollIntoViewIfNeeded() {
    await this.page.session.send("DOM.scrollIntoViewIfNeeded", {
      objectId: this.remote.objectId,
    });
  }
  async boundingBox() {
    try {
      const { model } = await this.page.session.send("DOM.getBoxModel", {
        objectId: this.remote.objectId,
      });
      const q = model.border,
        xs = q.filter((_, i) => i % 2 === 0),
        ys = q.filter((_, i) => i % 2 === 1);
      return {
        x: Math.min(...xs),
        y: Math.min(...ys),
        width: Math.max(...xs) - Math.min(...xs),
        height: Math.max(...ys) - Math.min(...ys),
      };
    } catch {
      return null;
    }
  }
  async identity() {
    const generation = this.frame.generation;
    return {
      generation,
      lift: await this.evaluate((el) => {
        const lift = window.top.__viewspecLift;
        return lift
          ? {
              ref: lift.refOf(el),
              identity: lift.readTargets([lift.refOf(el)])[lift.refOf(el)]
                .identity,
              doc: lift.docId,
              url: el.ownerDocument.location.href,
            }
          : null;
      }),
    };
  }
  async unchanged(identity) {
    if (this.frame.generation !== identity.generation)
      throw new Error("drift: document or route changed before input");
    const valid = await this.evaluate((el, identity) => {
      if (!el.isConnected) return false;
      if (!identity) return true;
      const lift = window.top.__viewspecLift;
      return (
        lift?.docId === identity.doc &&
        el.ownerDocument.location.href === identity.url &&
        lift.get(identity.ref) === el &&
        !lift.differsFrom(el, identity.identity)
      );
    }, identity.lift);
    if (!valid || this.frame.generation !== identity.generation)
      throw new Error(
        "drift: element identity or document changed before input",
      );
  }
  async point(identity) {
    await this.checked();
    await this.unchanged(identity);
    const blocked = await this.evaluate((el) => {
      const lift = window.top.__viewspecLift;
      if (lift && lift.blockedNow(el, false, true)) return true;
      const r = el.getBoundingClientRect(),
        view = el.ownerDocument.defaultView;
      const x = (Math.max(0, r.left) + Math.min(view.innerWidth, r.right)) / 2,
        y = (Math.max(0, r.top) + Math.min(view.innerHeight, r.bottom)) / 2;
      let hit = el.ownerDocument.elementFromPoint(x, y);
      while (hit?.shadowRoot) {
        const inner = hit.shadowRoot.elementFromPoint(x, y);
        if (!inner || inner === hit) break;
        hit = inner;
      }
      return !hit || !el.contains(hit);
    });
    if (blocked) throw new Error("element does not receive pointer events");
    const { quads } = await this.page.session.send("DOM.getContentQuads", {
      objectId: this.remote.objectId,
    });
    const q = quads?.find(
      (q) =>
        Math.abs(
          q[0] * q[3] -
            q[2] * q[1] +
            (q[2] * q[5] - q[4] * q[3]) +
            (q[4] * q[7] - q[6] * q[5]) +
            (q[6] * q[1] - q[0] * q[7]),
        ) > 1,
    );
    if (!q) throw new Error("element has no clickable area");
    return {
      x: (q[0] + q[2] + q[4] + q[6]) / 4,
      y: (q[1] + q[3] + q[5] + q[7]) / 4,
    };
  }
  async click({ timeout = 5000 } = {}) {
    const identity = await this.identity();
    await this.checked();
    await this.scrollIntoViewIfNeeded();
    const deadline = performance.now() + timeout;
    let point = await this.point(identity);
    while (true) {
      await this.page.evaluate(
        () =>
          new Promise((resolve) => {
            const timer = setTimeout(resolve, 50);
            requestAnimationFrame(() => {
              clearTimeout(timer);
              resolve();
            });
          }),
      );
      const next = await this.point(identity);
      if (
        Math.abs(point.x - next.x) < 0.25 &&
        Math.abs(point.y - next.y) < 0.25
      ) {
        point = next;
        break;
      }
      if (performance.now() > deadline)
        throw new Error("element is not stable");
      point = next;
    }
    await this.page.mouse.move(point.x, point.y);
    point = await this.point(identity);
    const navigation = this.page.navigationRequests;
    await this.page.mouse.down(point);
    await this.page.mouse.up(point);
    // Input acknowledgement can precede the renderer's queued form-submission
    // task. A renderer round trip exposes its navigation signal before deciding
    // that this action stayed on the current document.
    await this.page.session.send("Page.enable");
    await this.page.waitForActionNavigation(
      navigation,
      Math.max(1, deadline - performance.now()),
    );
  }
  async focus() {
    await this.checked();
    await this.evaluate((el) => el.focus());
  }
  async fill(value) {
    if (typeof value !== "string") throw new Error("fill requires text");
    await this.checked(true);
    const identity = await this.identity();
    const result = await this.evaluate((el, value) => {
      if (
        el.matches(
          "input[type=date],input[type=datetime-local],input[type=month],input[type=time],input[type=week],input[type=color],input[type=range]",
        )
      ) {
        el.focus();
        el.value = value;
        if (el.value !== value)
          throw new Error("value is not valid for this input type");
        el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return "done";
      }
      if (
        el.matches(
          "input[type=file],input[type=checkbox],input[type=radio],input[type=hidden]",
        )
      )
        throw new Error("input cannot be filled with text");
      el.focus();
      if (el.isContentEditable) {
        const range = el.ownerDocument.createRange();
        range.selectNodeContents(el);
        const selection = el.ownerDocument.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      } else if (el.matches("input,textarea")) {
        if (
          el.type === "number" &&
          value !== "" &&
          !Number.isFinite(Number(value))
        )
          throw new Error("invalid number");
        el.select();
      } else throw new Error("element is not editable");
      return "input";
    }, value);
    if (result === "done") return;
    await this.unchanged(identity);
    await this.focused();
    if (value) await this.page.keyboard.insertText(value);
    else await this.page.keyboard.press("Backspace");
  }
  async focused() {
    const focused = await this.evaluate((el) => {
      let active = el.ownerDocument.activeElement;
      while (active?.shadowRoot?.activeElement)
        active = active.shadowRoot.activeElement;
      return active === el;
    });
    if (!focused) throw new Error("element lost focus before input");
  }
  async type(value) {
    const identity = await this.identity();
    await this.focus();
    for (const character of value) {
      await this.checked(true);
      await this.unchanged(identity);
      await this.focused();
      await this.page.keyboard.press(character);
    }
  }
  async press(key, { timeout = 5000 } = {}) {
    const identity = await this.identity();
    await this.focus();
    await this.unchanged(identity);
    await this.focused();
    const navigation = this.page.navigationRequests;
    await this.page.keyboard.press(key);
    await this.page.session.send("Page.enable");
    await this.page.waitForActionNavigation(navigation, timeout);
  }
  inputValue() {
    return this.evaluate((el) => el.value);
  }
  isEditable() {
    return this.evaluate(
      (el) =>
        !el.matches(':disabled,[aria-disabled="true"]') &&
        !el.readOnly &&
        el.getAttribute("aria-readonly") !== "true" &&
        (el.matches("input,textarea,select") || el.isContentEditable),
    );
  }
  getAttribute(name) {
    return this.evaluate((el, name) => el.getAttribute(name), name);
  }
  async check() {
    if (!(await this.isChecked())) {
      await this.click();
      if (!(await this.isChecked()))
        throw new Error("checkbox did not become checked");
    }
  }
  async uncheck() {
    if (await this.isChecked()) {
      await this.click();
      if (await this.isChecked())
        throw new Error("checkbox did not become unchecked");
    }
  }
  isChecked() {
    return this.evaluate((el) =>
      el.matches("input[type=checkbox],input[type=radio]")
        ? el.checked
        : el.getAttribute("aria-checked") === "true",
    );
  }
  async selectOption(values) {
    await this.checked();
    return this.evaluate((el, values) => {
      if (el.tagName !== "SELECT") throw new Error("element is not a select");
      const selected = (Array.isArray(values) ? values : [values]).map(
        (value) => (typeof value === "string" ? { value } : value),
      );
      const options = [...el.options],
        matches = selected.map((want) =>
          options.filter(
            (o, i) =>
              (want.value === undefined || o.value === want.value) &&
              (want.label === undefined || o.label === want.label) &&
              (want.index === undefined || i === want.index),
          ),
        );
      if (
        matches.some((m) => m.length !== 1) ||
        (!el.multiple && selected.length > 1)
      )
        throw new Error("select option is missing or ambiguous");
      if (matches.some((m) => m[0].disabled || m[0].parentElement?.disabled))
        throw new Error("select option is disabled");
      el.focus();
      for (const option of options)
        option.selected = matches.some((m) => m[0] === option);
      el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return [...el.selectedOptions].map((o) => o.value);
    }, values);
  }
  async setInputFiles(files) {
    for (const file of files) {
      if (
        !file.name ||
        /[\\/\0]/.test(file.name) ||
        [".", ".."].includes(file.name)
      )
        throw new Error("Invalid upload filename");
    }
    // The bridge supplies bounded byte payloads. Keep them in memory: Chromium
    // omits disk-backed file bytes from its intercepted request evidence.
    await this.evaluate(
      (input, files) => {
        if (
          !input.isConnected ||
          !input.matches("input[type=file]") ||
          input.disabled
        )
          throw new Error("file input unavailable");
        if (!input.multiple && files.length > 1)
          throw new Error("file input does not accept multiple files");
        const transfer = new DataTransfer();
        for (const file of files)
          transfer.items.add(
            new File(
              [Uint8Array.from(atob(file.data), (c) => c.charCodeAt(0))],
              file.name,
              { type: file.mimeType },
            ),
          );
        input.files = transfer.files;
        input.dispatchEvent(
          new Event("input", { bubbles: true, composed: true }),
        );
        input.dispatchEvent(new Event("change", { bubbles: true }));
      },
      files.map((file) => ({
        name: file.name,
        mimeType: file.mimeType,
        data: file.buffer.toString("base64"),
      })),
    );
  }
}

class Locator {
  constructor(frame, spec, frames = []) {
    this.base = frame;
    this.spec = spec;
    this.chain = frames;
    this.cached = null;
  }
  async frame() {
    let frame = this.base;
    for (const css of this.chain) {
      const element = await new Locator(frame, { css }).handle();
      try {
        const { node } = await frame.page().session.send("DOM.describeNode", {
          objectId: element.remote.objectId,
        });
        frame = frame.page().frame(node.frameId);
      } finally {
        await element.dispose();
      }
    }
    return frame;
  }
  frameLocator(css) {
    return new Locator(this.base, { frame: css }, [
      ...this.chain,
      ...(this.spec.frame ? [this.spec.frame] : []),
    ]);
  }
  locator(css) {
    return new Locator(this.base, { css }, [
      ...this.chain,
      ...(this.spec.frame ? [this.spec.frame] : []),
    ]);
  }
  getByRole(role, options) {
    return new Locator(this.base, { role, ...options }, [
      ...this.chain,
      ...(this.spec.frame ? [this.spec.frame] : []),
    ]);
  }
  getByText(text, options) {
    return new Locator(this.base, { text, ...options }, [
      ...this.chain,
      ...(this.spec.frame ? [this.spec.frame] : []),
    ]);
  }
  async count() {
    const frame = await this.frame();
    return frame.evaluate(
      `(${query.toString()})(${JSON.stringify(this.spec)}).length`,
    );
  }
  async handle() {
    if (this.cached) return this.cached;
    const frame = await this.frame();
    const expression = `(()=>{const elements=(${query.toString()})(${JSON.stringify(this.spec)});if(elements.length!==1)throw new Error('locator is not unique');return elements[0]})()`;
    this.cached = await frame.evaluateHandle(expression);
    return this.cached;
  }
  async evaluate(fn, arg) {
    return (await this.handle()).evaluate(fn, arg);
  }
  async evaluateHandle(fn, arg) {
    return (await this.handle()).evaluateHandle(fn, arg);
  }
  async all() {
    const frame = await this.frame(),
      count = await this.count(),
      handles = [];
    for (let i = 0; i < count; i++)
      handles.push(
        await frame.evaluateHandle(
          `(${query.toString()})(${JSON.stringify(this.spec)})[${i}]`,
        ),
      );
    return handles;
  }
  async ariaSnapshot() {
    const frame = await this.frame();
    const { nodes } = await frame
      .page()
      .session.send("Accessibility.getFullAXTree", { frameId: frame.id });
    const map = new Map(nodes.map((n) => [n.nodeId, n]));
    const lines = [];
    function walk(node, depth) {
      if (!node) return;
      const role = node.role?.value,
        name = node.name?.value || "";
      const kept =
        !node.ignored && !["InlineTextBox", "generic", "none"].includes(role);
      if (kept) {
        const label =
          role === "StaticText"
            ? "text"
            : role === "RootWebArea"
              ? "document"
              : role;
        lines.push(
          "  ".repeat(depth) +
            "- " +
            label +
            (name ? " " + JSON.stringify(name) : ""),
        );
      }
      for (const child of node.childIds || [])
        walk(map.get(child), depth + (kept ? 1 : 0));
    }
    walk(nodes[0], 0);
    return lines.join("\n");
  }
}
for (const method of [
  "click",
  "fill",
  "type",
  "press",
  "focus",
  "check",
  "uncheck",
  "selectOption",
  "inputValue",
  "getAttribute",
  "isChecked",
  "isEditable",
  "boundingBox",
  "scrollIntoViewIfNeeded",
  "setInputFiles",
])
  Locator.prototype[method] = async function (...args) {
    return (await this.handle())[method](...args);
  };

class Keyboard {
  constructor(page) {
    this.page = page;
  }
  insertText(text) {
    return this.page.session.send("Input.insertText", { text });
  }
  async press(combination) {
    if (typeof combination !== "string" || !combination)
      throw new Error("Unsupported keyboard key");
    const parts = combination.split("+");
    // A trailing plus is the key itself ("+" or "Control++").
    if (parts.at(-1) === "") {
      parts.pop();
      parts[parts.length - 1] = "+";
    }
    let key = parts.pop(),
      modifiers = 0;
    for (const modifier of parts) {
      const bit = {
        Alt: 1,
        Control: 2,
        Meta: 4,
        Shift: 8,
        ControlOrMeta: process.platform === "darwin" ? 4 : 2,
      }[modifier];
      if (bit === undefined) throw new Error("Unsupported keyboard modifier");
      modifiers |= bit;
    }
    const codes = {
      Enter: [13, "Enter", "\r"],
      Tab: [9, "Tab"],
      Backspace: [8, "Backspace"],
      Escape: [27, "Escape"],
      ArrowDown: [40, "ArrowDown"],
      ArrowUp: [38, "ArrowUp"],
      ArrowLeft: [37, "ArrowLeft"],
      ArrowRight: [39, "ArrowRight"],
      Home: [36, "Home"],
      End: [35, "End"],
      Delete: [46, "Delete"],
      PageUp: [33, "PageUp"],
      PageDown: [34, "PageDown"],
      " ": [32, "Space", " "],
    };
    // ASCII code points are not virtual-key codes: e.g. '.' is 46, which
    // Chromium interprets as Delete even when a text payload is supplied.
    for (const [plain, shifted, keyCode, code] of [
      ["=", "+", 187, "Equal"],
      ["-", "_", 189, "Minus"],
      [".", ">", 190, "Period"],
      [",", "<", 188, "Comma"],
      ["/", "?", 191, "Slash"],
      [";", ":", 186, "Semicolon"],
      ["'", '"', 222, "Quote"],
      ["[", "{", 219, "BracketLeft"],
      ["]", "}", 221, "BracketRight"],
      ["\\", "|", 220, "Backslash"],
      ["`", "~", 192, "Backquote"],
    ]) {
      codes[plain] = [keyCode, code, plain];
      codes[shifted] = [keyCode, code, shifted];
    }
    for (const [index, character] of [...")!@#$%^&*("].entries())
      codes[character] = [48 + index, "Digit" + index, character];
    const entry = codes[key];
    if (!entry && [...key].length !== 1)
      throw new Error("Unsupported keyboard key");
    if (!entry && key.codePointAt(0) > 127) return this.insertText(key);
    const code =
        entry?.[1] ||
        (/[a-z]/i.test(key)
          ? "Key" + key.toUpperCase()
          : /[0-9]/.test(key)
            ? "Digit" + key
            : ""),
      windowsVirtualKeyCode = entry?.[0] || key.toUpperCase().charCodeAt(0);
    const text = modifiers & 7 ? undefined : entry ? entry[2] : key;
    const commands =
      modifiers & 6 && key.toLowerCase() === "a" ? ["selectAll"] : undefined;
    await this.page.session.send("Input.dispatchKeyEvent", {
      type: text ? "keyDown" : "rawKeyDown",
      key,
      code,
      windowsVirtualKeyCode,
      modifiers,
      ...(text ? { text, unmodifiedText: text } : {}),
      ...(commands ? { commands } : {}),
    });
    await this.page.session.send("Input.dispatchKeyEvent", {
      type: "keyUp",
      key,
      code,
      windowsVirtualKeyCode,
      modifiers,
    });
  }
}
class Mouse {
  constructor(page) {
    this.page = page;
    this.x = 0;
    this.y = 0;
  }
  async move(x, y) {
    this.x = x;
    this.y = y;
    await this.page.session.send("Input.dispatchMouseEvent", {
      type: "mouseMoved",
      x,
      y,
    });
  }
  async down(point = { x: this.x, y: this.y }) {
    await this.page.session.send("Input.dispatchMouseEvent", {
      type: "mousePressed",
      button: "left",
      buttons: 1,
      clickCount: 1,
      ...point,
    });
  }
  async up(point = { x: this.x, y: this.y }) {
    await this.page.session.send("Input.dispatchMouseEvent", {
      type: "mouseReleased",
      button: "left",
      buttons: 0,
      clickCount: 1,
      ...point,
    });
  }
}
module.exports = { Handle, Locator, Keyboard, Mouse, expression, valueOf };
