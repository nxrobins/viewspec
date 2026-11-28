// Native sockets are CSP-blocked. Page requests reach this relay as untrusted data;
// only the shared bridge's socket policy can authorize an actual connection.
const http = require("node:http");
const tls = require("node:tls");
const net = require("node:net");
const fs = require("node:fs");
const path = require("node:path");
const WebSocket = require("ws");
const SOURCE = fs.readFileSync(
  path.join(__dirname, "rust_socket_inpage.js"),
  "utf8",
);
const SOCKET_CSP = "connect-src http: https: data: blob:";
const MAX_MESSAGE = 8 * 1024 * 1024;

class SocketRelay {
  constructor(context, handler) {
    this.context = context;
    this.handler = handler;
    this.records = new Map();
    this.installed = new Set();
  }
  async install(page) {
    if (this.installed.has(page.id)) return;
    this.installed.add(page.id);
    page.session.on("Runtime.bindingCalled", (event) => {
      if (event.name === "__viewspecSocketRequest")
        this.receive(page, event).catch(() => {});
    });
    await page.session.send("Runtime.addBinding", {
      name: "__viewspecSocketRequest",
    });
    await page.session.send("Page.addScriptToEvaluateOnNewDocument", {
      source: SOURCE,
    });
    if (!page.starting) await page.evaluate(SOURCE);
  }
  key(page, context, id) {
    return `${page.id}:${context}:${id}`;
  }
  async notify(record, message) {
    if (record.page.closed || !record.page.contextFrames.has(record.context))
      return;
    await record.page.session.send("Runtime.evaluate", {
      contextId: record.context,
      expression: `window.__viewspecSocketReply(${JSON.stringify({ id: record.id, ...message })})`,
      returnByValue: true,
    });
  }
  async receive(page, event) {
    if (event.payload.length > 12 * 1024 * 1024) return;
    let data;
    try {
      data = JSON.parse(event.payload);
    } catch {
      return;
    }
    if (!Number.isSafeInteger(data.id) || data.id < 1) return;
    const frame = page.contextFrames.get(event.executionContextId);
    if (!frame) return;
    const key = this.key(page, event.executionContextId, data.id);
    if (data.op === "open") {
      if (this.records.has(key) || this.records.size >= 256) return;
      if (
        typeof data.url !== "string" ||
        data.url.length > 65536 ||
        !Array.isArray(data.protocols) ||
        data.protocols.length > 64
      )
        return;
      const record = {
        key,
        page,
        context: event.executionContextId,
        id: data.id,
        origin: frame.securityOrigin || new URL(frame.url()).origin,
        url: data.url,
        closed: false,
        ws: null,
        tunnel: null,
        socket: null,
        queued: 0,
        delivery: Promise.resolve(),
      };
      this.records.set(key, record);
      const route = {
        url: () => record.url,
        connectToServer: () => {
          this.connect(record, data.protocols).catch(() =>
            this.stop(record, 1006, "", true),
          );
        },
        close: ({ code = 1008, reason = "blocked" } = {}) =>
          this.stop(record, code, reason, true),
      };
      try {
        await this.handler(route);
      } catch {
        await this.stop(record, 1006, "", true);
      }
    } else {
      const record = this.records.get(key);
      if (!record || record.closed) return;
      if (data.op === "close") {
        if (
          data.code !== undefined &&
          data.code !== 1000 &&
          !(
            Number.isInteger(data.code) &&
            data.code >= 3000 &&
            data.code <= 4999
          )
        )
          return;
        if (
          typeof data.reason !== "string" ||
          Buffer.byteLength(data.reason) > 123
        )
          return;
        if (record.ws?.readyState === WebSocket.OPEN)
          record.ws.close(data.code, data.reason);
        else this.stop(record, 1006, "", false);
      } else if (
        data.op === "send" &&
        record.ws?.readyState === WebSocket.OPEN &&
        typeof data.data === "string"
      ) {
        const body = data.binary ? Buffer.from(data.data, "base64") : data.data;
        if (
          Buffer.byteLength(body) > MAX_MESSAGE ||
          record.ws.bufferedAmount > 2 * MAX_MESSAGE
        )
          return this.stop(record, 1009, "message limit", true);
        record.ws.send(body, { binary: Boolean(data.binary) }, (error) => {
          if (error) this.stop(record, 1006, "", true);
          else
            this.notify(record, {
              type: "sent",
              bytes: Buffer.byteLength(body),
            }).catch(() => {});
        });
      }
    }
  }
  async tunnel(record, url) {
    const proxy = this.context.browser.proxy;
    if (!proxy) throw new Error("WebSocket relay requires the policy proxy");
    const address = new URL(proxy),
      port = url.port || (url.protocol === "wss:" ? 443 : 80);
    let socket = await new Promise((resolve, reject) => {
      const request = http.request({
        host: address.hostname,
        port: address.port,
        method: "CONNECT",
        path: `${url.hostname}:${port}`,
        timeout: 5000,
      });
      record.tunnel = request;
      request.on("error", reject);
      request.on("timeout", () => request.destroy(new Error("proxy timeout")));
      request.on("connect", (response, socket, head) => {
        if (response.statusCode !== 200) {
          socket.destroy();
          reject(new Error("proxy refused socket"));
          return;
        }
        if (head.length) socket.unshift(head);
        resolve(socket);
      });
      request.end();
    });
    record.socket = socket;
    if (record.closed) {
      socket.destroy();
      throw new Error("socket context closed");
    }
    if (url.protocol === "wss:")
      socket = await new Promise((resolve, reject) => {
        const secure = tls.connect({
          socket,
          host: url.hostname,
          ...(net.isIP(url.hostname) ? {} : { servername: url.hostname }),
        });
        record.socket = secure;
        secure.once("error", reject);
        secure.once("secureConnect", () => resolve(secure));
        secure.setTimeout(5000, () => secure.destroy(new Error("TLS timeout")));
      });
    socket.setTimeout(0);
    return socket;
  }
  async connect(record, protocols) {
    const url = new URL(record.url);
    if (!["ws:", "wss:"].includes(url.protocol) || url.username || url.password)
      throw new Error("invalid socket URL");
    const socket = await this.tunnel(record, url);
    if (record.closed) {
      socket.destroy();
      return;
    }
    const cookies = await this.context.cookies([
      record.url.replace(/^ws/, "http"),
    ]);
    if (record.closed) {
      socket.destroy();
      return;
    }
    const headers = {
      Origin: record.origin,
      ...(cookies.length
        ? { Cookie: cookies.map((c) => `${c.name}=${c.value}`).join("; ") }
        : {}),
    };
    const ws = new WebSocket(url, protocols, {
      headers,
      createConnection: () => socket,
      handshakeTimeout: 5000,
      maxPayload: MAX_MESSAGE,
      followRedirects: false,
    });
    record.ws = ws;
    ws.on("open", () =>
      this.notify(record, {
        type: "open",
        protocol: ws.protocol,
        extensions: ws.extensions,
      }).catch(() => this.stop(record, 1006, "", false)),
    );
    ws.on("message", (body, binary) => {
      if (record.queued >= 64)
        return this.stop(record, 1009, "message queue limit", true);
      record.queued++;
      ws.pause();
      record.delivery = record.delivery
        .then(() =>
          this.notify(record, {
            type: "message",
            data: binary ? body.toString("base64") : body.toString(),
            binary,
          }),
        )
        .catch(() => this.stop(record, 1006, "", false))
        .finally(() => {
          record.queued--;
          if (!record.closed) ws.resume();
        });
    });
    ws.on("error", () =>
      this.notify(record, { type: "error" }).catch(() => {}),
    );
    ws.on("close", (code, reason) => {
      if (record.closed) return;
      record.closed = true;
      this.records.delete(record.key);
      record.delivery
        .then(() =>
          this.notify(record, {
            type: "close",
            code,
            reason: reason.toString(),
            clean: code !== 1006,
          }),
        )
        .catch(() => {});
    });
  }
  async stop(record, code, reason, error) {
    if (record.closed) return;
    record.closed = true;
    this.records.delete(record.key);
    record.tunnel?.destroy();
    record.ws?.terminate();
    record.socket?.destroy();
    if (error) await this.notify(record, { type: "error" }).catch(() => {});
    await this.notify(record, {
      type: "close",
      code,
      reason,
      clean: false,
    }).catch(() => {});
  }
  destroyContext(page, context) {
    for (const record of this.records.values())
      if (record.page === page && record.context === context)
        this.stop(record, 1001, "document closed", false).catch(() => {});
  }
  close() {
    for (const record of this.records.values())
      this.stop(record, 1001, "context closed", false).catch(() => {});
  }
}
module.exports = { SocketRelay, SOCKET_CSP };
