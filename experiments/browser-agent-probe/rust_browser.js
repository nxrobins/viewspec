// Browser-driver binding for the shared Lift bridge. Chromium/CDP are owned by Rust.
// This implements the bridge's used surface, not the general Playwright API.
const { EventEmitter } = require("node:events");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { RustHost } = require("./rust_host");
const { SocketRelay, SOCKET_CSP } = require("./rust_socket");
const {
  Handle,
  Locator,
  Keyboard,
  Mouse,
  expression,
  valueOf,
} = require("./rust_dom");

const lower = (headers) =>
  Object.fromEntries(
    Object.entries(headers || {}).map(([k, v]) => [k.toLowerCase(), String(v)]),
  );
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
};

async function until(emitter, check, timeout = 5000) {
  const deadline = performance.now() + timeout;
  while (true) {
    const value = await check();
    if (value) return value;
    if (performance.now() >= deadline)
      throw new Error("Browser condition timed out");
    await new Promise((resolve) => {
      let timer;
      const done = () => {
        clearTimeout(timer);
        emitter.off("change", done);
        resolve();
      };
      emitter.once("change", done);
      timer = setTimeout(
        done,
        Math.min(25, Math.max(0, deadline - performance.now())),
      );
    });
  }
}

class Session extends EventEmitter {
  constructor(browser, id, context) {
    super();
    this.browser = browser;
    this.id = id;
    this.context = context;
    this.paused = new Map();
  }
  async send(method, params = {}) {
    // Native WebSockets are blocked by an enforced document policy. Only the
    // policy-checked host relay may connect; replacing page JavaScript cannot bypass it.
    if (
      method === "Fetch.fulfillRequest" &&
      this.context?.sockets &&
      this.paused.get(params.requestId)?.resourceType === "Document"
    ) {
      params = {
        ...params,
        responseHeaders: [
          ...(params.responseHeaders || []),
          { name: "Content-Security-Policy", value: SOCKET_CSP },
        ],
      };
    }
    const result = await this.browser.host.send(method, params, this.id);
    if (
      [
        "Fetch.fulfillRequest",
        "Fetch.failRequest",
        "Fetch.continueRequest",
        "Fetch.continueResponse",
      ].includes(method)
    )
      this.paused.delete(params.requestId);
    return result;
  }
  dispatch(method, params) {
    if (method === "Fetch.requestPaused")
      this.paused.set(params.requestId, params);
    this.emit(method, params);
  }
}

class Browser {
  constructor(host, proxy) {
    this.host = host;
    this.proxy = proxy;
    this.contexts = new Map();
    this.sessions = new Map();
    this.closed = false;
    host.on("event", (event) => {
      if (event.sessionId)
        this.sessions
          .get(event.sessionId)
          ?.dispatch(event.method, event.params || {});
      else this.event(event.method, event.params || {});
    });
    host.on("disconnect", () => {
      for (const context of this.contexts.values()) context.destroy();
    });
  }
  async initialize() {
    await this.host.send("Target.setAutoAttach", {
      autoAttach: true,
      waitForDebuggerOnStart: true,
      flatten: true,
      filter: [{ type: "page" }, { type: "iframe" }, { exclude: true }],
    });
  }
  event(method, event) {
    if (method === "Target.attachedToTarget") {
      const info = event.targetInfo,
        context = this.contexts.get(info.browserContextId);
      if (!context || info.type !== "page") {
        this.host
          .send("Target.closeTarget", { targetId: info.targetId })
          .catch(() => {});
        return;
      }
      const session = new Session(this, event.sessionId, context);
      this.sessions.set(session.id, session);
      if (context.pages.has(info.targetId)) return; // Auxiliary response-policy CDP session.
      const options = info.openerId ? {} : context.creating.shift() || {};
      const page = new Page(
        context,
        info.targetId,
        session,
        info.openerId,
        options,
      );
      context.pages.set(info.targetId, page);
      page.ready = page.initialize().catch((error) => {
        page.failure = error;
        throw error;
      });
      page.ready.catch(() => {
        if (!page.closed)
          this.host.fail(new Error("Rust browser page initialization failed"));
      });
      context.emit("change");
    } else if (method === "Target.detachedFromTarget") {
      for (const context of this.contexts.values())
        for (const page of context.pages.values())
          if (page.session.id === event.sessionId) page.detached();
      this.sessions.delete(event.sessionId);
    } else if (method === "Browser.downloadWillBegin") {
      for (const context of this.contexts.values())
        for (const page of context.pages.values())
          if (page.frameMap.has(event.frameId)) {
            const item = new Download(context, event);
            context.downloads.set(event.guid, item);
            page.emit("download", item);
            return;
          }
    } else if (method === "Browser.downloadProgress") {
      for (const context of this.contexts.values())
        context.downloads.get(event.guid)?.progress(event);
    }
  }
  async newPage(options = {}) {
    const { browserContextId } = await this.host.send(
      "Target.createBrowserContext",
      { disposeOnDetach: true },
    );
    const context = new Context(this, browserContextId, options);
    this.contexts.set(browserContextId, context);
    await context.initialize();
    return context.newPage();
  }
  async close() {
    if (this.closed) return;
    this.closed = true;
    await this.host.close();
    for (const context of this.contexts.values()) context.destroy();
    this.contexts.clear();
    this.sessions.clear();
  }
}

class Context extends EventEmitter {
  constructor(browser, id, options) {
    super();
    this.browser = browser;
    this.id = id;
    this.options = options;
    this.pages = new Map();
    this.creating = [];
    this.origins = new Set();
    this.downloads = new Map();
    this.downloadDir = fs.mkdtempSync(
      path.join(os.tmpdir(), "viewspec-rust-download-"),
    );
    this.routeHandler = null;
    this.sockets = null;
    this.closed = false;
  }
  async initialize() {
    await this.browser.host.send("Browser.setDownloadBehavior", {
      browserContextId: this.id,
      behavior: "allowAndName",
      downloadPath: this.downloadDir,
      eventsEnabled: true,
    });
    const state = this.options.storageState || {};
    if (state.cookies?.length) await this.addCookies(state.cookies);
    for (const entry of state.origins || []) {
      if (
        !/^https?:\/\//.test(entry.origin) ||
        new URL(entry.origin).origin !== entry.origin
      )
        throw new Error("Invalid storage-state origin");
      const page = await this.newPage({ internal: true });
      try {
        await page.goto(entry.origin);
        await page.evaluate((items) => {
          for (const item of items) localStorage.setItem(item.name, item.value);
        }, entry.localStorage || []);
      } finally {
        await page.close();
      }
      this.origins.add(entry.origin);
    }
  }
  async newPage(options = {}) {
    if (this.closed) throw new Error("Browser context closed");
    this.creating.push(options);
    const { targetId } = await this.browser.host.send("Target.createTarget", {
      url: "about:blank",
      browserContextId: this.id,
    });
    const page = await until(this, () => this.pages.get(targetId));
    await page.ready;
    return page;
  }
  async newCDPSession(page) {
    const { sessionId } = await this.browser.host.send(
      "Target.attachToTarget",
      { targetId: page.id, flatten: true },
    );
    const session =
      this.browser.sessions.get(sessionId) ||
      new Session(this.browser, sessionId, this);
    this.browser.sessions.set(sessionId, session);
    return session;
  }
  async route(_pattern, handler) {
    this.routeHandler = handler;
    for (const page of this.pages.values()) await page.enableRoutes();
  }
  async routeWebSocket(_pattern, handler) {
    this.sockets = new SocketRelay(this, handler);
    for (const page of this.pages.values()) await this.sockets.install(page);
  }
  async addCookies(cookies) {
    await this.browser.host.send("Storage.setCookies", {
      browserContextId: this.id,
      cookies,
    });
  }
  async cookies(urls) {
    const { cookies } = await this.browser.host.send("Storage.getCookies", {
      browserContextId: this.id,
    });
    if (!urls) return cookies;
    return cookies.filter((cookie) =>
      urls.some((address) => {
        const url = new URL(address);
        const domain = cookie.domain.replace(/^\./, "");
        return (
          (url.hostname === domain ||
            (cookie.domain.startsWith(".") &&
              url.hostname.endsWith("." + domain))) &&
          url.pathname.startsWith(cookie.path) &&
          (!cookie.secure || url.protocol === "https:")
        );
      }),
    );
  }
  async storageState() {
    const cookies = (await this.cookies()).map((c) =>
      Object.fromEntries(
        [
          "name",
          "value",
          "domain",
          "path",
          "expires",
          "httpOnly",
          "secure",
          "sameSite",
        ].map((k) => [k, c[k]]),
      ),
    );
    const origins = [];
    for (const origin of [...this.origins]) {
      const page = await this.newPage({ internal: true });
      try {
        await page.goto(origin);
        origins.push({
          origin,
          localStorage: await page.evaluate(() =>
            Object.entries(localStorage).map(([name, value]) => ({
              name,
              value,
            })),
          ),
        });
      } finally {
        await page.close();
      }
    }
    return { cookies, origins };
  }
  async close() {
    if (this.closed) return;
    await this.browser.host.send("Target.disposeBrowserContext", {
      browserContextId: this.id,
    });
    this.destroy();
    this.browser.contexts.delete(this.id);
  }
  destroy() {
    if (this.closed) return;
    this.closed = true;
    this.sockets?.close();
    for (const page of this.pages.values()) page.detached();
    for (const download of this.downloads.values())
      download.done.resolve("browser context closed");
    fs.rmSync(this.downloadDir, { recursive: true, force: true });
  }
}

class Frame {
  constructor(page, id) {
    this.owner = page;
    this.id = id;
    this.parentId = null;
    this.address = "about:blank";
    this.contextId = null;
    this.loader = null;
    this.generation = 0;
  }
  page() {
    return this.owner;
  }
  url() {
    return this.address;
  }
  parentFrame() {
    return this.owner.frameMap.get(this.parentId) || null;
  }
  async evaluate(fn, arg) {
    return this.owner.evaluateIn(this, fn, arg, true);
  }
  async evaluateHandle(fn, arg) {
    return this.owner.evaluateIn(this, fn, arg, false);
  }
  locator(css) {
    return new Locator(this, { css });
  }
  getByRole(role, options) {
    return new Locator(this, { role, ...options });
  }
  getByText(text, options) {
    return new Locator(this, { text, ...options });
  }
  frameLocator(css) {
    return new Locator(this, { frame: css });
  }
}

class Page extends EventEmitter {
  constructor(context, id, session, opener, options) {
    super();
    this.setMaxListeners(0);
    this.owner = context;
    this.id = id;
    this.session = session;
    this.opener = opener;
    this.options = options;
    this.frameMap = new Map();
    this.contextFrames = new Map();
    this.mainId = null;
    this.loads = new Map();
    this.requests = new Map();
    this.responses = new Map();
    this.navigationRequests = 0;
    this.actionNavigation = null;
    this.closed = false;
    this.starting = true;
    this.failure = null;
    this.popupEmitted = false;
    this.keyboard = new Keyboard(this);
    this.mouse = new Mouse(this);
    session.on("Page.frameAttached", (e) => {
      this.frame(e.frameId).parentId = e.parentFrameId;
    });
    session.on("Page.frameRequestedNavigation", (e) => {
      if (e.disposition === "currentTab") {
        const frame = this.frame(e.frameId);
        this.actionNavigation = {
          sequence: ++this.navigationRequests,
          frame,
          generation: frame.generation,
          failed: false,
        };
      }
    });
    session.on("Page.frameNavigated", (e) => this.navigated(e.frame));
    session.on("Page.navigatedWithinDocument", (e) => {
      const frame = this.frame(e.frameId);
      frame.address = e.url;
      frame.generation++;
      this.emit("framenavigated", frame);
      this.emit("change");
    });
    session.on("Page.frameDetached", (e) => {
      if (e.reason !== "swap") this.frameMap.delete(e.frameId);
    });
    session.on("Runtime.executionContextCreated", (e) => {
      const c = e.context;
      if (c.auxData?.isDefault) {
        const frame = this.frame(c.auxData.frameId);
        frame.contextId = c.id;
        frame.securityOrigin = c.origin;
        this.contextFrames.set(c.id, frame);
        this.emit("change");
      }
    });
    session.on("Runtime.executionContextDestroyed", (e) => {
      const frame = this.contextFrames.get(e.executionContextId);
      if (frame?.contextId === e.executionContextId) frame.contextId = null;
      this.contextFrames.delete(e.executionContextId);
      this.owner.sockets?.destroyContext(this, e.executionContextId);
    });
    session.on("Runtime.executionContextsCleared", () => {
      for (const id of this.contextFrames.keys())
        this.owner.sockets?.destroyContext(this, id);
      for (const frame of this.frameMap.values()) frame.contextId = null;
      this.contextFrames.clear();
    });
    session.on("Page.lifecycleEvent", (e) => {
      if (!this.loads.has(e.loaderId)) this.loads.set(e.loaderId, new Set());
      this.loads.get(e.loaderId).add(e.name);
      while (this.loads.size > 64)
        this.loads.delete(this.loads.keys().next().value);
      this.emit("change");
    });
    session.on("Network.requestWillBeSent", (e) => this.requested(e));
    session.on("Network.responseReceived", (e) => {
      const request = this.requests.get(e.requestId);
      if (request) {
        request.response = new Response(request, e.response);
        // Assets share their document's loader ID. Only the main document's
        // response belongs to goto(); a later script must not replace its HTML.
        if (request.isNavigationRequest() && request.frameId === this.mainId) {
          this.responses.clear();
          this.responses.set(request.loader, request.response);
        }
        if (
          request.isNavigationRequest() &&
          [204, 205].includes(e.response.status) &&
          this.actionNavigation?.frame.id === request.frameId
        )
          this.actionNavigation.failed = true;
        if (!request.silent()) this.emit("response", request.response);
      }
    });
    session.on("Network.loadingFinished", (e) => this.finished(e.requestId));
    session.on("Network.loadingFailed", (e) =>
      this.finished(e.requestId, e.errorText || "failed"),
    );
    session.on("Fetch.requestPaused", (e) => {
      this.route(e).catch(() => {
        this.session
          .send("Fetch.failRequest", {
            requestId: e.requestId,
            errorReason: "BlockedByClient",
          })
          .catch(() => {});
        this.failure = new Error("request policy could not be applied");
      });
    });
    session.on("Page.javascriptDialogOpening", (e) => {
      const dialog = {
        type: () => e.type,
        message: () => e.message,
        accept: () =>
          session.send("Page.handleJavaScriptDialog", { accept: true }),
        dismiss: () =>
          session.send("Page.handleJavaScriptDialog", { accept: false }),
      };
      if (this.listenerCount("dialog")) this.emit("dialog", dialog);
      else dialog.dismiss().catch(() => {});
    });
    session.on("Page.fileChooserOpened", (e) => {
      this.fileChooser(e).catch(() => {});
    });
  }
  frame(id) {
    if (!this.frameMap.has(id)) this.frameMap.set(id, new Frame(this, id));
    return this.frameMap.get(id);
  }
  context() {
    return this.owner;
  }
  mainFrame() {
    return this.frameMap.get(this.mainId);
  }
  frames() {
    return [...this.frameMap.values()];
  }
  url() {
    return this.mainFrame()?.url() || "about:blank";
  }
  async initialize() {
    // A popup's renderer can defer Page.enable until it is resumed. Install
    // request interception first, then resume: the shared policy refuses its
    // first request before contact, including form POSTs into a new tab.
    if (this.opener) {
      await this.session.send("Fetch.enable", {
        patterns: [{ urlPattern: "*", requestStage: "Request" }],
      });
      await this.session.send("Runtime.runIfWaitingForDebugger");
    }
    await this.session.send("Page.enable");
    await this.session.send("Runtime.enable");
    await this.session.send("Network.enable", { maxPostDataSize: 1024 * 1024 });
    await this.session.send("Network.setBypassServiceWorker", { bypass: true });
    const { frameTree } = await this.session.send("Page.getFrameTree");
    this.mainId = frameTree.frame.id;
    Object.assign(this.frame(this.mainId), {
      address: frameTree.frame.url,
      loader: frameTree.frame.loaderId,
    });
    await this.session.send("Page.setLifecycleEventsEnabled", {
      enabled: true,
    });
    await this.session.send("Page.setInterceptFileChooserDialog", {
      enabled: true,
    });
    const viewport = this.owner.options.viewport || {
      width: 1280,
      height: 900,
    };
    await this.session.send("Emulation.setDeviceMetricsOverride", {
      ...viewport,
      deviceScaleFactor: 1,
      mobile: false,
    });
    if (this.owner.routeHandler || this.options.internal)
      await this.enableRoutes();
    if (this.owner.sockets) await this.owner.sockets.install(this);
    await this.session.send("Runtime.runIfWaitingForDebugger");
    this.starting = false;
  }
  async enableRoutes() {
    await this.session.send("Network.setCacheDisabled", {
      cacheDisabled: true,
    });
    await this.session.send("Fetch.enable", {
      patterns: [{ urlPattern: "*", requestStage: "Request" }],
    });
  }
  navigated(info) {
    const frame = this.frame(info.id);
    frame.parentId = info.parentId || null;
    frame.address = info.url + (info.urlFragment || "");
    frame.loader = info.loaderId;
    frame.generation++;
    if (!info.parentId) this.mainId = info.id;
    if (/^https?:\/\//.test(info.url) && !this.options.internal)
      this.owner.origins.add(new URL(info.url).origin);
    this.emit("framenavigated", frame);
    this.emit("change");
  }
  requested(event) {
    let request = this.requests.get(event.requestId);
    if (event.redirectResponse && request) {
      request.response = new Response(request, event.redirectResponse);
      this.emit("response", request.response);
      this.finished(event.requestId);
      request = null;
    }
    if (!request || request.url() !== event.request.url) {
      request = new Request(this, event.requestId, event);
      this.requests.set(event.requestId, request);
    } else request.update(event);
    if (!request.started) {
      request.started = true;
      if (!request.silent()) this.emit("request", request);
    }
  }
  finished(id, error = null) {
    const request = this.requests.get(id);
    if (request && !request.ended) {
      request.ended = true;
      request.done.resolve(error);
      if (
        error &&
        request.isNavigationRequest() &&
        this.actionNavigation?.frame.id === request.frameId
      )
        this.actionNavigation.failed = true;
      if (!request.silent())
        this.emit(error ? "requestfailed" : "requestfinished", request);
      this.requests.delete(id);
      this.emit("change");
    }
  }
  async route(event) {
    let request = this.requests.get(event.networkId);
    if (!request || request.url() !== event.request.url) {
      request = new Request(this, event.networkId || event.requestId, {
        ...event,
        type: event.resourceType,
      });
      this.requests.set(request.id, request);
    }
    request.data = { ...request.data, ...event.request };
    request.kind = event.resourceType;
    const route = new Route(this, event.requestId, request);
    // Browser chrome's automatic icon load is not page activity. Suppress its
    // contact as well as its evidence, matching the shared bridge's baseline.
    if (request.silent()) {
      await route.abort();
      return;
    }
    if (this.options.internal)
      await route.fulfill({
        status: 200,
        contentType: "text/html",
        body: "<!doctype html><title>Storage</title>",
      });
    else if (this.owner.routeHandler) await this.owner.routeHandler(route);
    else await route.continue();
    if (this.opener && !this.popupEmitted) {
      this.popupEmitted = true;
      this.owner.pages.get(this.opener)?.emit("popup", this);
    }
  }
  async evaluateIn(frame, fn, arg, byValue) {
    if (this.closed) throw new Error("Browser page closed");
    if (this.failure) throw this.failure;
    const contextId = await until(this, () => frame.contextId, 5000);
    const reply = await this.session.send("Runtime.evaluate", {
      expression: expression(fn, arg),
      contextId,
      returnByValue: byValue,
      awaitPromise: true,
      userGesture: true,
    });
    if (reply.exceptionDetails)
      throw new Error(
        reply.exceptionDetails.exception?.description ||
          reply.exceptionDetails.text ||
          "Page evaluation failed",
      );
    return byValue ? valueOf(reply.result) : new Handle(frame, reply.result);
  }
  evaluate(fn, arg) {
    return this.mainFrame().evaluate(fn, arg);
  }
  evaluateHandle(fn, arg) {
    return this.mainFrame().evaluateHandle(fn, arg);
  }
  locator(css) {
    return this.mainFrame().locator(css);
  }
  getByRole(role, options) {
    return this.mainFrame().getByRole(role, options);
  }
  getByText(text, options) {
    return this.mainFrame().getByText(text, options);
  }
  frameLocator(css) {
    return this.mainFrame().frameLocator(css);
  }
  async goto(url, { waitUntil = "load", timeout = 30000 } = {}) {
    const reply = await this.session.send("Page.navigate", { url });
    if (reply.errorText) throw new Error(reply.errorText);
    if (reply.isDownload) throw new Error("Navigation started a download");
    if (!reply.loaderId) return null;
    const state =
      waitUntil === "domcontentloaded" ? "DOMContentLoaded" : "load";
    await until(
      this,
      () => {
        if (this.closed) throw new Error("Browser page closed");
        if (this.failure) throw this.failure;
        return this.loads.get(reply.loaderId)?.has(state);
      },
      timeout,
    );
    return this.responses.get(reply.loaderId) || null;
  }
  async waitForLoadState(state = "load", { timeout = 30000 } = {}) {
    const name = state === "domcontentloaded" ? "DOMContentLoaded" : "load";
    await until(
      this,
      () => this.loads.get(this.mainFrame().loader)?.has(name),
      timeout,
    );
  }
  async waitForActionNavigation(mark, timeout) {
    const navigation = this.actionNavigation;
    if (!navigation || navigation.sequence <= mark) return;
    await until(
      this,
      () =>
        navigation.failed ||
        navigation.frame.generation !== navigation.generation ||
        this.closed,
      timeout,
    );
    if (!this.closed && navigation.frame.generation !== navigation.generation)
      await until(
        this,
        () =>
          this.loads.get(navigation.frame.loader)?.has("DOMContentLoaded") ||
          this.closed,
        timeout,
      );
  }
  async reload(options = {}) {
    const loader = this.mainFrame().loader;
    await this.session.send("Page.reload");
    await until(
      this,
      () => this.mainFrame().loader !== loader && this.mainFrame().contextId,
      options.timeout || 30000,
    );
    await this.waitForLoadState(options.waitUntil || "load", options);
  }
  async goBack(options = {}) {
    const { currentIndex, entries } = await this.session.send(
      "Page.getNavigationHistory",
    );
    if (currentIndex < 1) return null;
    const generation = this.mainFrame().generation;
    await this.session.send("Page.navigateToHistoryEntry", {
      entryId: entries[currentIndex - 1].id,
    });
    await until(
      this,
      () => this.mainFrame().generation !== generation,
      options.timeout || 30000,
    );
    await this.waitForLoadState(options.waitUntil || "load", options);
    return null;
  }
  waitForEvent(name, { timeout = 30000, predicate = () => true } = {}) {
    return new Promise((resolve, reject) => {
      let timer;
      const done = (value) => {
        if (!predicate(value)) return;
        clearTimeout(timer);
        this.off(name, done);
        resolve(value);
      };
      this.on(name, done);
      timer = setTimeout(() => {
        this.off(name, done);
        reject(new Error(`Browser ${name} timed out`));
      }, timeout);
    });
  }
  async fileChooser(event) {
    const { object } = await this.session.send("DOM.resolveNode", {
      backendNodeId: event.backendNodeId,
    });
    const input = new Handle(this.frame(event.frameId), object);
    this.emit("filechooser", {
      element: () => input,
      isMultiple: () => event.mode === "selectMultiple",
      setFiles: (files) => input.setInputFiles(files),
    });
  }
  async close() {
    if (this.closed) return;
    await this.owner.browser.host.send("Target.closeTarget", {
      targetId: this.id,
    });
    this.detached();
  }
  detached() {
    if (this.closed) return;
    this.closed = true;
    for (const request of this.requests.values())
      request.done.resolve("page closed");
    this.emit("change");
    this.emit("close");
    this.owner.pages.delete(this.id);
  }
}

class Request {
  constructor(page, id, event) {
    this.page = page;
    this.id = id;
    this.started = false;
    this.ended = false;
    this.done = deferred();
    this.update(event);
  }
  update(event) {
    this.data = event.request;
    this.frameId = event.frameId;
    this.kind = event.type || event.resourceType;
    this.loader = event.loaderId;
  }
  url() {
    return this.data.url;
  }
  method() {
    return this.data.method;
  }
  headers() {
    return lower(this.data.headers);
  }
  resourceType() {
    return (this.kind || "other").toLowerCase();
  }
  silent() {
    return (
      this.method() === "GET" &&
      ["Other", "Image"].includes(this.kind) &&
      this.url().endsWith("/favicon.ico")
    );
  }
  isNavigationRequest() {
    return this.kind === "Document";
  }
  frame() {
    return this.page.frame(this.frameId);
  }
  serviceWorker() {
    return null;
  }
  postDataBuffer() {
    if (this.data.postDataEntries?.every((e) => e.bytes !== undefined))
      return Buffer.concat(
        this.data.postDataEntries.map((e) => Buffer.from(e.bytes, "base64")),
      );
    return this.data.postData === undefined
      ? null
      : Buffer.from(this.data.postData);
  }
  postData() {
    return this.postDataBuffer()?.toString("utf8") ?? null;
  }
}
class Response {
  constructor(request, data) {
    this.req = request;
    this.data = data;
  }
  request() {
    return this.req;
  }
  status() {
    return this.data.status;
  }
  headers() {
    return lower(this.data.headers);
  }
  finished() {
    return this.req.done.promise;
  }
  async text() {
    const failure = await this.finished();
    if (failure) throw new Error(failure);
    const reply = await this.req.page.session.send("Network.getResponseBody", {
      requestId: this.req.id,
    });
    return Buffer.from(
      reply.body,
      reply.base64Encoded ? "base64" : "utf8",
    ).toString("utf8");
  }
}
class Route {
  constructor(page, id, request) {
    this.page = page;
    this.id = id;
    this.req = request;
  }
  request() {
    return this.req;
  }
  async continue({ headers } = {}) {
    await this.page.session.send("Fetch.continueRequest", {
      requestId: this.id,
      ...(headers
        ? {
            headers: Object.entries(headers).map(([name, value]) => ({
              name,
              value: String(value),
            })),
          }
        : {}),
    });
  }
  async abort() {
    await this.page.session.send("Fetch.failRequest", {
      requestId: this.id,
      errorReason: "BlockedByClient",
    });
  }
  async fulfill({ status = 200, headers = {}, contentType, body = "" } = {}) {
    const buffer = Buffer.isBuffer(body) ? body : Buffer.from(body);
    const all = {
      ...headers,
      "content-length": String(buffer.length),
      ...(contentType ? { "content-type": contentType } : {}),
    };
    await this.page.session.send("Fetch.fulfillRequest", {
      requestId: this.id,
      responseCode: status,
      responseHeaders: Object.entries(all).map(([name, value]) => ({
        name,
        value: String(value),
      })),
      body: buffer.toString("base64"),
    });
  }
}
class Download {
  constructor(context, event) {
    this.context = context;
    this.event = event;
    this.done = deferred();
    this.error = null;
  }
  url() {
    return this.event.url;
  }
  suggestedFilename() {
    return this.event.suggestedFilename;
  }
  progress(event) {
    if (event.receivedBytes > 50 * 1024 * 1024) {
      this.error = "download exceeds 50 MB";
      this.cancel().catch(() => {});
    }
    if (event.state !== "inProgress")
      this.done.resolve(
        event.state === "completed" ? null : this.error || "canceled",
      );
  }
  failure() {
    return this.done.promise;
  }
  async cancel() {
    await this.context.browser.host.send("Browser.cancelDownload", {
      guid: this.event.guid,
      browserContextId: this.context.id,
    });
  }
  file() {
    if (!/^[\w-]+$/.test(this.event.guid))
      throw new Error("Invalid download identity");
    return path.join(this.context.downloadDir, this.event.guid);
  }
  async saveAs(file) {
    const failure = await this.failure();
    if (failure) throw new Error(failure);
    await fs.promises.copyFile(this.file(), file);
  }
  async delete() {
    await this.failure();
    await fs.promises.rm(this.file(), { force: true });
  }
}

const chromium = {
  async launch(options = {}) {
    const host = await RustHost.launch({
      proxy: options.proxy?.server || null,
      certificatePins:
        (options.args || [])
          .find((s) => s.startsWith("--ignore-certificate-errors-spki-list="))
          ?.split("=")
          .slice(1)
          .join("=") || null,
    });
    const browser = new Browser(host, options.proxy?.server || null);
    try {
      await browser.initialize();
      return browser;
    } catch (error) {
      await browser.close();
      throw error;
    }
  },
};
module.exports = { chromium, until };
