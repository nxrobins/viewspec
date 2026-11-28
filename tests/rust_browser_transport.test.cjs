const { test } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { PassThrough } = require("node:stream");
const { RustHost } = require("../experiments/browser-agent-probe/rust_host");

function fakeHost(timeout = 1000) {
  const child = new EventEmitter();
  child.stdin = new PassThrough();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  child.kill = () => child.emit("exit", 0, null);
  child.stdin.on("finish", () => child.emit("exit", 0, null));
  return {
    child,
    host: new RustHost(child, timeout),
    reply: (message) => child.stdout.write(JSON.stringify(message) + "\n"),
  };
}

test("command bursts queue in order and never replay or exceed the active window", async () => {
  const { host, child, reply } = fakeHost();
  const sent = [];
  child.stdin.on("data", (bytes) => sent.push(JSON.parse(bytes.toString())));
  const commands = Array.from({ length: 300 }, (_, value) =>
    host.send("Test", { value }),
  );
  assert.equal(sent.length, 128);
  assert.equal(host.queued.length, 172);
  for (let id = 1; id <= 300; id++) {
    assert.ok(host.pending.size <= 128);
    reply({ id, ok: true, reply: { result: id }, afterEventSequence: 0 });
  }
  assert.deepEqual(
    await Promise.all(commands),
    Array.from({ length: 300 }, (_, i) => i + 1),
  );
  assert.deepEqual(
    sent.map((c) => c.params.value),
    Array.from({ length: 300 }, (_, i) => i),
  );
  assert.equal(host.queuedBytes, 0);
  host.fail(new Error("test finished"));
});

test("queued commands retain count, byte and deadline bounds and fail on disconnect", async () => {
  for (const kind of ["count", "bytes", "deadline"]) {
    const { host, child } = fakeHost(kind === "deadline" ? 10 : 1000);
    const sent = [];
    child.stdin.on("data", (bytes) =>
      sent.push(JSON.parse(bytes.toString()).id),
    );
    const errors = [];
    const send = (params) =>
      host.send("Test", params).catch((e) => errors.push(e.message));
    const commands = Array.from({ length: 128 }, () => send({}));
    if (kind === "count") {
      commands.push(...Array.from({ length: 256 }, () => send({})));
      await assert.rejects(host.send("Overflow"), /queue capacity/);
    } else if (kind === "bytes") {
      const body = "x".repeat(9 * 1024 * 1024);
      commands.push(send({ body }));
      await assert.rejects(host.send("Overflow", { body }), /queue capacity/);
    } else commands.push(send({}));
    if (kind !== "deadline") host.fail(new Error("fixture disconnected"));
    await Promise.all(commands);
    assert.equal(errors.length, commands.length);
    assert.ok(
      errors.every((e) =>
        kind === "deadline" ? /timed out/.test(e) : /disconnected/.test(e),
      ),
    );
    assert.equal(sent.length, 128);
    assert.equal(host.pending.size, 0);
    assert.equal(host.queued.length, 0);
    assert.equal(host.queuedBytes, 0);
  }
});

test("close bypasses a saturated command window without dispatching queued work", async () => {
  const { host, child, reply } = fakeHost();
  const sent = [];
  child.stdin.on("data", (bytes) => sent.push(JSON.parse(bytes.toString())));
  const commands = Array.from({ length: 140 }, () =>
    host.send("Test").catch((e) => e.message),
  );
  const closing = host.close();
  assert.equal(sent.length, 129);
  assert.equal(sent.at(-1).op, "close");
  reply({ id: sent.at(-1).id, ok: true, result: {} });
  await closing;
  assert.ok((await Promise.all(commands)).every((e) => /closed/.test(e)));
  assert.equal(sent.length, 129);
});

test("responses wait for preceding navigation events even if IPC writers reorder them", async () => {
  const { host, reply } = fakeHost();
  const order = [];
  host.on("event", (event) => order.push(event.method));
  const pending = host.send("Runtime.evaluate").then((result) => {
    order.push("reply");
    return result;
  });
  reply({
    id: 1,
    ok: true,
    reply: { result: { value: 7 } },
    afterEventSequence: 2,
  });
  assert.deepEqual(order, []);
  reply({ event: { method: "Page.frameNavigated" }, sequence: 1 });
  assert.deepEqual(order, ["Page.frameNavigated"]);
  reply({ event: { method: "Page.navigatedWithinDocument" }, sequence: 2 });
  assert.deepEqual(await pending, { value: 7 });
  assert.deepEqual(order, [
    "Page.frameNavigated",
    "Page.navigatedWithinDocument",
    "reply",
  ]);
  host.fail(new Error("test finished"));
});

test("response correlation is independent of completion order and UTF-8 chunk boundaries", async () => {
  const { host, child, reply } = fakeHost();
  const a = host.send("A"),
    b = host.send("B");
  reply({
    id: 2,
    ok: true,
    reply: { result: { name: "B" } },
    afterEventSequence: 0,
  });
  const bytes = Buffer.from(
    JSON.stringify({
      id: 1,
      ok: true,
      reply: { result: { name: "Renée 東京" } },
      afterEventSequence: 0,
    }) + "\n",
  );
  for (const byte of bytes) child.stdout.write(Buffer.from([byte]));
  assert.deepEqual(await a, { name: "Renée 東京" });
  assert.deepEqual(await b, { name: "B" });
  host.fail(new Error("test finished"));
});

test("missing events and malformed host messages fail all commands without retry", async () => {
  for (const message of [
    { event: { method: "Lost" }, sequence: 2 },
    { id: 999, ok: true },
    { fatal: "failed" },
  ]) {
    const { host, reply } = fakeHost();
    const a = host.send("A"),
      b = host.send("B");
    reply(message);
    await assert.rejects(a, /protocol failed/);
    await assert.rejects(b, /protocol failed/);
    assert.equal(host.nextId, 2);
    assert.equal(host.closed, true);
  }
});

test("invalid or duplicate event watermarks cannot release a delayed command", async () => {
  for (const watermark of [undefined, -1, "one"]) {
    const { host, reply } = fakeHost();
    const pending = host.send("Test");
    reply({
      id: 1,
      ok: true,
      reply: { result: {} },
      afterEventSequence: watermark,
    });
    await assert.rejects(pending, /protocol failed/);
  }
  const { host, reply } = fakeHost();
  const pending = host.send("Test");
  const response = {
    id: 1,
    ok: true,
    reply: { result: {} },
    afterEventSequence: 1,
  };
  reply(response);
  reply(response);
  await assert.rejects(pending, /protocol failed/);
});

const real = { skip: process.env.VIEWSPEC_RUN_RUST_BROWSER_E2E !== "1" };
test("invalid host launch options fail before spawning a browser", async () => {
  await assert.rejects(
    RustHost.launch({
      executable: "/unused",
      proxy: { server: "http://127.0.0.1:8080" },
    }),
    /proxy must be a URL/,
  );
  await assert.rejects(
    RustHost.launch({ executable: "/unused", timeoutMs: 0 }),
    /Invalid.*timeout/,
  );
});

test(
  "real Rust host streams CDP with ordered navigation events and no Playwright runtime",
  real,
  async () => {
    // Runtime guard: importing Playwright is a failure, including a hidden fallback.
    const Module = require("node:module"),
      original = Module._load;
    Module._load = function (name, ...args) {
      assert.ok(
        !name.includes("playwright"),
        "Rust host attempted to import Playwright",
      );
      return original.call(this, name, ...args);
    };
    let host;
    try {
      host = await RustHost.launch();
      const { targetId } = await host.send("Target.createTarget", {
        url: "about:blank",
      });
      const { sessionId } = await host.send("Target.attachToTarget", {
        targetId,
        flatten: true,
      });
      await host.send("Page.enable", {}, sessionId);
      await host.send("Runtime.enable", {}, sessionId);
      let navigated = false;
      host.on("event", (event) => {
        if (
          event.sessionId === sessionId &&
          event.method === "Page.navigatedWithinDocument"
        )
          navigated = true;
      });
      const result = await host.send(
        "Runtime.evaluate",
        { expression: "location.hash='changed'; 42", returnByValue: true },
        sessionId,
      );
      assert.equal(result.result.value, 42);
      assert.equal(navigated, true);
      const values = await Promise.all(
        Array.from({ length: 300 }, (_, i) => i + 1).map((value) =>
          host.send(
            "Runtime.evaluate",
            { expression: String(value), returnByValue: true },
            sessionId,
          ),
        ),
      );
      assert.deepEqual(
        values.map((v) => v.result.value),
        Array.from({ length: 300 }, (_, i) => i + 1),
      );
    } finally {
      if (host) await host.close();
      Module._load = original;
    }
  },
);

test(
  "real host reports browser death promptly and reaps its process",
  real,
  async () => {
    const host = await RustHost.launch();
    try {
      const { targetId } = await host.send("Target.createTarget", {
        url: "about:blank",
      });
      const { sessionId } = await host.send("Target.attachToTarget", {
        targetId,
        flatten: true,
      });
      const pending = host.send(
        "Runtime.evaluate",
        { expression: "new Promise(()=>{})", awaitPromise: true },
        sessionId,
      );
      process.kill(host.processId, "SIGKILL");
      await assert.rejects(pending, /host|protocol/);
    } finally {
      await host.close();
    }
    assert.throws(() => process.kill(host.processId, 0), { code: "ESRCH" });
  },
);

test("stdin EOF closes the real host and its owned browser", real, async () => {
  const host = await RustHost.launch();
  host.child.stdin.end();
  await host.exited;
  assert.throws(() => process.kill(host.processId, 0), { code: "ESRCH" });
  await host.close();
});
