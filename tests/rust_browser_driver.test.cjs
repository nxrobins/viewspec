const { test } = require("node:test");
const assert = require("node:assert/strict");
const http = require("node:http");
const net = require("node:net");
const { once } = require("node:events");
const Module = require("node:module");
const {
  WebSocketServer,
} = require("../experiments/browser-agent-probe/node_modules/ws");
const { chromium } = require("../experiments/browser-agent-probe/rust_browser");
const { Keyboard } = require("../experiments/browser-agent-probe/rust_dom");

const real = {
  skip: process.env.VIEWSPEC_RUN_RUST_BROWSER_E2E !== "1",
  timeout: 20000,
};

test(
  "the full 25 MiB upload batch preserves bytes through separate form submission",
  { ...real, timeout: 45000 },
  async () => {
    const browser = await chromium.launch();
    const files = [10, 10, 5].map((size, i) => ({
      name: `part-${i}.bin`,
      mimeType: "application/octet-stream",
      buffer: Buffer.alloc(size * 1024 * 1024, [0, 128, 255][i]),
    }));
    let submitted = 0;
    try {
      const page = await browser.newPage();
      await page.context().route("**/*", async (route) => {
        if (route.request().method() === "POST") {
          submitted++;
          const body = route.request().postDataBuffer();
          assert.ok(body.length > 25 * 1024 * 1024);
          for (const file of files)
            assert.ok(body.includes(file.buffer), file.name + " bytes missing");
          return route.fulfill({
            contentType: "text/html",
            body: "<!doctype html><h1>Saved</h1>",
          });
        }
        return route.fulfill({
          contentType: "text/html",
          body: "<!doctype html><form action='/upload' method=POST enctype='multipart/form-data'><input name=files type=file multiple><button>Save</button></form>",
        });
      });
      await page.goto("http://127.0.0.1:65010/files");
      await page.locator("input").setInputFiles(files);
      assert.deepEqual(
        await page.evaluate(() =>
          [...document.querySelector("input").files].map((f) => f.size),
        ),
        files.map((f) => f.buffer.length),
      );
      assert.equal(submitted, 0);
      await Promise.all([
        page.waitForEvent("framenavigated", {
          timeout: 20000,
          predicate: (frame) => frame.url().endsWith("/upload"),
        }),
        page.locator("button").click({ timeout: 20000 }),
      ]);
      await page.waitForLoadState();
      assert.equal(submitted, 1);
      assert.equal(page.failure, null);
      assert.equal(
        await page.evaluate(() => document.querySelector("h1")?.textContent),
        "Saved",
      );
    } finally {
      await browser.close();
    }
  },
);

test(
  "asset bursts wait for bounded transport capacity and finish loading",
  real,
  async () => {
    const browser = await chromium.launch();
    const assets = new Set();
    try {
      const page = await browser.newPage();
      await page.context().route("**/*", async (route) => {
        const url = new URL(route.request().url());
        if (url.pathname === "/burst")
          return route.fulfill({
            contentType: "text/html",
            body:
              "<!doctype html>" +
              Array.from(
                { length: 300 },
                (_, i) => `<script async src='/asset-${i}.js'></script>`,
              ).join(""),
          });
        assets.add(url.pathname);
        return route.fulfill({
          contentType: "application/javascript",
          body: "window.loaded=(window.loaded||0)+1;",
        });
      });
      const response = await page.goto("http://127.0.0.1:65010/burst");
      assert.ok((await response.text()).startsWith("<!doctype html>"));
      assert.equal(await page.evaluate(() => window.loaded), 300);
      assert.equal(assets.size, 300);
      assert.equal(page.failure, null);
      assert.equal(page.requests.size, 0);
    } finally {
      await browser.close();
    }
  },
);

test("keyboard accepts literal plus and rejects unknown shortcut modifiers", async () => {
  const events = [];
  const keyboard = new Keyboard({
    session: {
      send: async (method, params) => events.push({ method, params }),
    },
  });
  await keyboard.press("+");
  assert.equal(events[0].params.key, "+");
  assert.equal(events[0].params.text, "+");
  await keyboard.press("Control++");
  assert.equal(events[2].params.key, "+");
  assert.equal(events[2].params.modifiers, 2);
  assert.equal(events[2].params.text, undefined);
  await assert.rejects(keyboard.press("Unknown+a"), /modifier/);
  assert.equal(events.length, 4);
});

test(
  "typing preserves plus and Unicode with native input events",
  real,
  async () => {
    const browser = await chromium.launch();
    try {
      const page = await browser.newPage();
      await page.context().route("**/*", (route) =>
        route.fulfill({
          contentType: "text/html",
          body: "<!doctype html><input id=search><script>window.inputs=[];search.oninput=e=>inputs.push(e.isTrusted)</script>",
        }),
      );
      await page.goto("http://127.0.0.1:65010/typing");
      const text =
        "ops+東京😀@example.test " +
        Array.from({ length: 95 }, (_, i) => String.fromCharCode(32 + i)).join(
          "",
        );
      await page.locator("#search").type(text);
      assert.deepEqual(
        await page.evaluate(() => ({
          value: search.value,
          trusted: inputs.every(Boolean),
          count: inputs.length,
        })),
        { value: text, trusted: true, count: [...text].length },
      );
    } finally {
      await browser.close();
    }
  },
);

test(
  "typing and key presses keep their original focused control and route",
  real,
  async () => {
    const browser = await chromium.launch();
    try {
      const page = await browser.newPage();
      await page.context().route("**/*", (route) =>
        route.fulfill({
          contentType: "text/html",
          body: "<!doctype html><input id=first><input id=second><script>first.onfocus=()=>second.focus()</script>",
        }),
      );
      await page.goto("http://127.0.0.1:65010/typing");
      for (const method of ["type", "press"])
        await assert.rejects(page.locator("#first")[method]("x"), /lost focus/);
      assert.deepEqual(await page.evaluate(() => [first.value, second.value]), [
        "",
        "",
      ]);
      await page.evaluate(() => {
        first.onfocus = null;
        first.oninput = () => {
          history.pushState({}, "", "/other");
          history.pushState({}, "", "/typing");
          second.focus();
        };
      });
      await assert.rejects(
        page.locator("#first").type("abc"),
        /drift:.*changed/,
      );
      assert.deepEqual(await page.evaluate(() => [first.value, second.value]), [
        "a",
        "",
      ]);
    } finally {
      await browser.close();
    }
  },
);

test(
  "click and Enter wait for queued form navigation before exposing the next document",
  real,
  async () => {
    const browser = await chromium.launch();
    try {
      const page = await browser.newPage();
      await page.context().route("**/*", async (route) => {
        const address = new URL(route.request().url());
        if (address.pathname === "/saved")
          await new Promise((resolve) => setTimeout(resolve, 20));
        await route.fulfill({
          contentType: "text/html",
          body:
            address.pathname === "/saved"
              ? "<!doctype html><h1>Saved</h1>"
              : "<!doctype html><form action='/saved'><input name=q value=fixture><button type=button onclick='this.form.requestSubmit()'>Choose</button></form>",
        });
      });
      for (let attempt = 0; attempt < 5; attempt++) {
        await page.goto("http://127.0.0.1:65010/search");
        if (attempt % 2) await page.locator("button").press("Enter");
        else await page.locator("button").click();
        assert.equal(new URL(page.url()).pathname, "/saved");
        assert.equal(
          await page.evaluate(() => document.querySelector("h1").textContent),
          "Saved",
        );
      }
    } finally {
      await browser.close();
    }
  },
);

test(
  "reload and history wait for the new document; focus cannot hide an away-and-back route change",
  real,
  async () => {
    const browser = await chromium.launch();
    let loads = 0;
    try {
      const page = await browser.newPage();
      await page.context().route("**/*", (route) =>
        route.fulfill({
          contentType: "text/html",
          body: `<!doctype html><h1>${++loads}</h1><input id=title><script>title.onfocus=()=>{history.pushState({},'', '/other');history.pushState({},'', '/first');};title.oninput=()=>document.body.dataset.edited='yes';</script>`,
        }),
      );
      await page.goto("http://127.0.0.1:65010/first");
      const initial = await page.evaluate(
        () => document.querySelector("h1").textContent,
      );
      await page.reload({ waitUntil: "domcontentloaded" });
      const reloaded = await page.evaluate(
        () => document.querySelector("h1").textContent,
      );
      assert.notEqual(reloaded, initial);
      await page.goto("http://127.0.0.1:65010/second");
      await page.goBack({ waitUntil: "domcontentloaded" });
      assert.equal(page.url(), "http://127.0.0.1:65010/first");
      await assert.rejects(
        page.locator("#title").fill("must not be entered"),
        /drift:.*changed/,
      );
      assert.deepEqual(
        await page.evaluate(() => ({
          value: document.querySelector("input").value,
          edited: document.body.dataset.edited || null,
        })),
        { value: "", edited: null },
      );
    } finally {
      await browser.close();
    }
  },
);

test(
  "Rust socket relay preserves messages and cookies while native sockets remain blocked",
  real,
  async () => {
    const received = [],
      tunnels = [],
      decisions = [];
    const server = http.createServer((_request, response) =>
      response.end("fixture"),
    );
    const sockets = new WebSocketServer({ server });
    sockets.on("connection", (socket, request) => {
      received.push({
        url: request.url,
        cookie: request.headers.cookie,
        origin: request.headers.origin,
      });
      socket.on("message", (data, binary) => socket.send(data, { binary }));
    });
    server.listen(0, "127.0.0.1");
    await once(server, "listening");
    const address = `127.0.0.1:${server.address().port}`,
      base = `http://${address}`;
    const proxy = http.createServer((_request, response) => {
      response.writeHead(403);
      response.end();
    });
    const connected = new Set();
    proxy.on("connect", (request, downstream, head) => {
      if (request.url !== address) {
        downstream.destroy();
        return;
      }
      tunnels.push(request.url);
      const upstream = net.connect(server.address().port, "127.0.0.1", () => {
        downstream.write("HTTP/1.1 200 Connection Established\r\n\r\n");
        if (head.length) upstream.write(head);
        upstream.pipe(downstream);
        downstream.pipe(upstream);
      });
      for (const socket of [downstream, upstream]) {
        connected.add(socket);
        socket.on("close", () => connected.delete(socket));
        socket.on("error", () => {
          upstream.destroy();
          downstream.destroy();
        });
      }
    });
    proxy.listen(0, "127.0.0.1");
    await once(proxy, "listening");
    const load = Module._load;
    Module._load = function (name, ...args) {
      assert.ok(
        !name.includes("playwright"),
        "Rust driver imported Playwright",
      );
      return load.call(this, name, ...args);
    };
    let browser;
    try {
      browser = await chromium.launch({
        proxy: { server: `http://127.0.0.1:${proxy.address().port}` },
      });
      const page = await browser.newPage();
      await page.context().addCookies([
        {
          name: "session",
          value: "fixture-cookie",
          url: base,
          httpOnly: true,
        },
      ]);
      await page.context().route("**/*", (route) =>
        route.fulfill({
          contentType: "text/html",
          body: "<!doctype html><h1>Sockets</h1>",
        }),
      );
      // Preserve the native constructor before the facade: even a hostile page that
      // recovers it must not escape the enforced document CSP.
      await page.session.send("Page.addScriptToEvaluateOnNewDocument", {
        source: "window.nativeSocket = WebSocket",
      });
      await page.context().routeWebSocket(/.*/, (route) => {
        decisions.push(route.url());
        if (new URL(route.url()).pathname === "/allowed")
          route.connectToServer();
        else route.close({ code: 1008, reason: "blocked" });
      });
      await page.goto(base);
      const echoed = await page.evaluate(
        (address) =>
          new Promise((resolve, reject) => {
            const socket = new WebSocket(`ws://${address}/allowed`, ["echo"]),
              values = [];
            socket.binaryType = "arraybuffer";
            const timer = setTimeout(
              () => reject(new Error("echo timeout")),
              3000,
            );
            socket.onerror = () => {
              clearTimeout(timer);
              reject(new Error("echo failed"));
            };
            socket.onopen = () => {
              socket.send("Renée 東京");
              socket.send(new Uint8Array([0, 255, 7]));
            };
            socket.onmessage = (event) => {
              values.push(
                typeof event.data === "string"
                  ? event.data
                  : Array.from(new Uint8Array(event.data)),
              );
              if (values.length === 2) socket.close(1000, "done");
            };
            socket.onclose = (event) => {
              clearTimeout(timer);
              resolve({ values, code: event.code, protocol: socket.protocol });
            };
          }),
        address,
      );
      assert.deepEqual(echoed, {
        values: ["Renée 東京", [0, 255, 7]],
        code: 1000,
        protocol: "echo",
      });
      const denied = await page.evaluate(
        (address) =>
          new Promise((resolve) => {
            const socket = new WebSocket(`ws://${address}/denied`);
            socket.onclose = (event) => resolve(event.code);
          }),
        address,
      );
      assert.equal(denied, 1008);
      const native = await page.evaluate(
        (address) =>
          new Promise((resolve) => {
            const socket = new nativeSocket(`ws://${address}/native-bypass`);
            socket.onopen = () => resolve("opened");
            socket.onerror = () => resolve("blocked");
          }),
        address,
      );
      assert.equal(native, "blocked");
      assert.deepEqual(decisions, [
        `ws://${address}/allowed`,
        `ws://${address}/denied`,
      ]);
      assert.deepEqual(received, [
        { url: "/allowed", cookie: "session=fixture-cookie", origin: base },
      ]);
      assert.deepEqual(tunnels, [address]);
    } finally {
      if (browser) await browser.close();
      Module._load = load;
      for (const socket of connected) socket.destroy();
      for (const client of sockets.clients) client.terminate();
      await new Promise((resolve) => sockets.close(resolve));
      await Promise.all(
        [server, proxy].map(
          (server) => new Promise((resolve) => server.close(resolve)),
        ),
      );
    }
  },
);
