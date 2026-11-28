// Private bridge-to-Rust IPC. No Playwright import, fallback, or agent-facing raw CDP API.
const { spawn } = require("node:child_process");
const { EventEmitter } = require("node:events");
const path = require("node:path");
const { StringDecoder } = require("node:string_decoder");

// Lift permits a 25 MiB upload batch; base64 and multipart evidence must fit.
const MAX_COMMAND = 64 * 1024 * 1024;
const MAX_FRAME = 128 * 1024 * 1024;
const MAX_IN_FLIGHT = 128;
const MAX_QUEUED = 256;
const MAX_QUEUED_BYTES = 16 * 1024 * 1024;

class RustHost extends EventEmitter {
  constructor(child, timeoutMs = 40000) {
    super();
    this.child = child;
    this.timeoutMs = timeoutMs;
    this.nextId = 0;
    this.pending = new Map();
    this.queued = [];
    this.queuedBytes = 0;
    this.closing = false;
    this.delayed = new Map();
    this.eventSequence = 0;
    this.buffer = "";
    this.decoder = new StringDecoder("utf8");
    this.closed = false;
    this.exited = new Promise((resolve) => {
      child.once("exit", (code, signal) => resolve({ code, signal }));
      child.once("close", (code, signal) => resolve({ code, signal }));
    });
    child.stdout.on("data", (chunk) => this.receive(chunk));
    child.stdout.on("end", () =>
      this.fail(new Error("Rust browser host closed")),
    );
    child.on("error", () =>
      this.fail(new Error("Rust browser host could not start")),
    );
    child.once("exit", () => this.fail(new Error("Rust browser host exited")));
    // Do not copy arbitrary process diagnostics into a public bridge reply.
    child.stderr?.resume();
    child.stdin.on("error", () =>
      this.fail(new Error("Rust browser host input closed")),
    );
  }

  static async launch({
    executable = process.env.VIEWSPEC_CHROMIUM,
    proxy = null,
    certificatePins = null,
    timeoutMs = 35000,
  } = {}) {
    if (!executable || !path.isAbsolute(executable))
      throw new Error(
        "Rust backend requires an absolute VIEWSPEC_CHROMIUM executable path",
      );
    if (proxy !== null && typeof proxy !== "string")
      throw new Error("Rust browser proxy must be a URL string");
    if (
      certificatePins !== null &&
      (typeof certificatePins !== "string" ||
        !/^[A-Za-z0-9+/]{43}=(,[A-Za-z0-9+/]{43}=)*$/.test(certificatePins) ||
        certificatePins.length > 1024)
    )
      throw new Error("Invalid certificate pins");
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 60000)
      throw new Error("Invalid Rust browser command timeout");
    const binary =
      process.env.VIEWSPEC_RUST_BROWSER_HOST ||
      path.join(
        __dirname,
        "rust-adapter",
        "target",
        "debug",
        "viewspec-browser-host",
      );
    const child = spawn(binary, [], { stdio: ["pipe", "pipe", "pipe"] });
    const host = new RustHost(child, timeoutMs + 5000);
    try {
      const ready = await host.request({
        op: "launch",
        executable,
        proxy,
        certificate_pins: certificatePins,
        timeout_ms: timeoutMs,
      });
      if (
        !Number.isSafeInteger(ready.eventSequence) ||
        ready.eventSequence < 0 ||
        !Number.isSafeInteger(ready.processId)
      ) {
        throw new Error("Rust browser host handshake failed");
      }
      host.eventSequence = ready.eventSequence;
      host.processId = ready.processId;
      return host;
    } catch (error) {
      host.fail(error);
      throw error;
    }
  }

  request(payload) {
    if (this.closed || (this.closing && payload.op !== "close"))
      return Promise.reject(new Error("Rust browser host closed"));
    const id = ++this.nextId;
    const frame = JSON.stringify({ id, ...payload }) + "\n";
    const bytes = Buffer.byteLength(frame);
    if (bytes > MAX_COMMAND)
      return Promise.reject(new Error("Rust browser host command too large"));
    const queued = payload.op !== "close" && this.pending.size >= MAX_IN_FLIGHT;
    if (
      queued &&
      (this.queued.length >= MAX_QUEUED ||
        this.queuedBytes + bytes > MAX_QUEUED_BYTES)
    )
      return Promise.reject(
        new Error("Rust browser host command queue capacity exceeded"),
      );
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        // A missing response/event is unknown evidence, not permission to retry.
        this.fail(new Error("Rust browser host command timed out"));
      }, this.timeoutMs);
      const command = {
        id,
        frame,
        bytes,
        resolve,
        reject,
        timer,
        op: payload.op,
      };
      if (queued) {
        this.queued.push(command);
        this.queuedBytes += bytes;
      } else this.dispatch(command);
    });
  }

  dispatch(command) {
    this.pending.set(command.id, command);
    this.child.stdin.write(command.frame, (error) => {
      if (error) this.fail(new Error("Rust browser host write failed"));
    });
    // Retain command metadata, not another copy of an already transmitted body.
    command.frame = null;
  }

  drain() {
    while (
      !this.closed &&
      !this.closing &&
      this.queued.length &&
      this.pending.size < MAX_IN_FLIGHT
    ) {
      const command = this.queued.shift();
      this.queuedBytes -= command.bytes;
      this.dispatch(command);
    }
  }

  clearQueue(error) {
    for (const command of this.queued) {
      clearTimeout(command.timer);
      command.reject(error);
    }
    this.queued = [];
    this.queuedBytes = 0;
  }

  async send(method, params = {}, sessionId = null) {
    const reply = await this.request({ op: "send", method, params, sessionId });
    if (reply.error) {
      const error = new Error(reply.error.message || "CDP command failed");
      error.code = reply.error.code;
      throw error;
    }
    return reply.result;
  }

  receive(chunk) {
    if (this.closed) return;
    this.buffer += this.decoder.write(chunk);
    try {
      let end;
      while ((end = this.buffer.indexOf("\n")) !== -1) {
        const frame = this.buffer.slice(0, end);
        this.buffer = this.buffer.slice(end + 1);
        if (Buffer.byteLength(frame) > MAX_FRAME)
          throw new Error("oversized host frame");
        const message = JSON.parse(frame);
        if (message.fatal) throw new Error("host failure");
        if (message.event) {
          if (message.sequence !== this.eventSequence + 1)
            throw new Error("host event gap");
          this.eventSequence = message.sequence;
          this.emit("event", message.event);
          for (const [id, response] of this.delayed) {
            if (response.afterEventSequence <= this.eventSequence) {
              this.delayed.delete(id);
              this.finish(response);
            }
          }
        } else {
          if (!this.pending.has(message.id) || typeof message.ok !== "boolean")
            throw new Error("unexpected host response");
          const pending = this.pending.get(message.id);
          if (
            pending.op === "send" &&
            (!Number.isSafeInteger(message.afterEventSequence) ||
              message.afterEventSequence < 0)
          )
            throw new Error("missing event watermark");
          if (this.delayed.has(message.id))
            throw new Error("duplicate delayed response");
          if ((message.afterEventSequence || 0) > this.eventSequence)
            this.delayed.set(message.id, message);
          else this.finish(message);
        }
      }
      if (Buffer.byteLength(this.buffer) > MAX_FRAME)
        throw new Error("oversized host frame");
    } catch {
      this.fail(new Error("Rust browser host protocol failed"));
    }
  }

  finish(message) {
    const pending = this.pending.get(message.id);
    if (!pending)
      return this.fail(new Error("Rust browser host response was duplicated"));
    this.pending.delete(message.id);
    clearTimeout(pending.timer);
    if (!message.ok)
      pending.reject(new Error(message.error || "Rust browser host failed"));
    else pending.resolve(message.reply || message.result);
    this.drain();
  }

  fail(error) {
    if (this.closed) return;
    this.closed = true;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
    this.clearQueue(error);
    this.delayed.clear();
    this.buffer = "";
    // EOF lets Rust run its process/profile cleanup. Kill is only a bounded fallback.
    this.child.stdin.end();
    const kill = setTimeout(() => this.child.kill(), 1000);
    kill.unref();
    this.exited.then(() => clearTimeout(kill));
    this.emit("disconnect", error);
  }

  async close() {
    if (!this.closed) {
      this.closing = true;
      this.clearQueue(
        new Error("Rust browser host closed before command dispatch"),
      );
      try {
        await this.request({ op: "close" });
      } finally {
        this.fail(new Error("Rust browser host closed"));
      }
    }
    await this.exited;
  }
}

module.exports = { RustHost };
