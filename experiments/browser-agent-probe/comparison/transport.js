// Attempt-owned HTTP/WS relay. The configured upstream is fixed; clients cannot select it.
// Journals retain wire bytes in bounded files, including open/aborted exchanges. A quota or
// journal failure invalidates the attempt instead of silently dropping audit evidence.
const http = require("node:http");
const net = require("node:net");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const HOP = new Set(["connection", "proxy-connection", "keep-alive", "transfer-encoding", "upgrade", "te", "trailer", "proxy-authorization", "proxy-authenticate"]);
function headersOf(headers) {
  const excluded = new Set([...HOP, ...(headers.connection || "").split(",").map(x => x.trim().toLowerCase())]);
  return Object.fromEntries(Object.entries(headers).filter(([key]) => !excluded.has(key.toLowerCase())));
}
function trailerNames(headers) {
  return (headers.trailer || "").split(",").map(x => x.trim().toLowerCase()).filter(x => /^[a-z0-9-]+$/.test(x) && !HOP.has(x) && !["host", "content-length", "authorization"].includes(x));
}

async function startProxy({upstream, directory, port = 0, origin: fixedOrigin, cookie = "", methods = ["GET", "HEAD"], maxBytes = 64 * 1024 * 1024, maxExchanges = 10000}) {
  const target = new URL(upstream);
  if (target.protocol !== "http:" || !["127.0.0.1", "[::1]", "localhost"].includes(target.hostname) || target.username || target.password || target.origin !== upstream)
    throw new Error("benchmark upstream must be a fixed local HTTP origin");
  if (fixedOrigin && (new URL(fixedOrigin).origin !== fixedOrigin || !fixedOrigin.startsWith("http://"))) throw new Error("benchmark origin must be an exact HTTP origin");
  if (!Number.isSafeInteger(maxBytes) || maxBytes < 1 || !Number.isSafeInteger(maxExchanges) || maxExchanges < 1) throw new Error("invalid audit byte limit");
  fs.mkdirSync(directory, {recursive: true, mode: 0o700});
  const log = fs.openSync(path.join(directory, "events.jsonl"), "wx", 0o600);
  const sockets = new Set(), writers = new Set(), tunnelPorts = new Set();
  let origin, sequence = 0, used = 0, valid = true, stopped = false;
  const record = event => fs.writeSync(log, JSON.stringify({at: Date.now() / 1000, ...event}) + "\n");
  const track = socket => { sockets.add(socket); socket.once("close", () => sockets.delete(socket)); };
  const nextId = () => {
    const id = ++sequence;
    if (id > maxExchanges && valid) {
      valid = false; record({event: "audit_limit", max_exchanges: maxExchanges});
      server.close(); for (const socket of sockets) socket.destroy();
    }
    return id;
  };
  function capture(id, direction) {
    const file = `${id}.${direction}.bin`, hash = crypto.createHash("sha256");
    const sink = fs.createWriteStream(path.join(directory, file), {flags: "wx", mode: 0o600});
    const closed = new Promise(resolve => sink.once("close", resolve));
    writers.add(closed); closed.finally(() => writers.delete(closed));
    let bytes = 0, ended = false;
    sink.on("error", error => { valid = false; record({id, event: "audit_error", error: error.code}); for (const socket of sockets) socket.destroy(); });
    return {
      sink,
      data(chunk) {
        used += chunk.length; bytes += chunk.length; hash.update(chunk);
        if (used > maxBytes) { valid = false; record({id, event: "audit_limit", max_bytes: maxBytes}); for (const socket of sockets) socket.destroy(); }
      },
      end(complete) {
        if (ended) return; ended = true;
        sink.end(() => record({id, event: "body", direction, file, bytes, sha256: hash.digest("hex"), complete}));
      },
    };
  }
  function address(req) {
    try {
      const url = new URL(req.url, origin);
      if (url.origin === origin && !url.username && !url.password && req.headers.host === url.host) return url;
    } catch {}
    return null;
  }
  function requestHeaders(req) {
    const headers = headersOf(req.headers);
    headers.host = new URL(origin).host;
    if (cookie) {
      const pairs = new Map();
      for (const piece of (cookie + ";" + (headers.cookie || "")).split(";")) {
        const at = piece.indexOf("="); if (at > 0) pairs.set(piece.slice(0, at).trim(), piece.slice(at + 1).trim());
      }
      headers.cookie = [...pairs].map(([key, value]) => `${key}=${value}`).join("; ");
    }
    const trailers = trailerNames(req.headers);
    if (trailers.length) { headers.trailer = trailers.join(", "); delete headers["content-length"]; }
    return headers;
  }
  const server = http.createServer((req, res) => {
    const id = nextId(), url = address(req);
    if (!valid || !url || !methods.includes(req.method)) {
      record({id, event: "blocked", method: req.method, url: req.url}); res.writeHead(403); res.end(); req.resume(); return;
    }
    record({id, event: "request", method: req.method, path: url.pathname + url.search, content_type: req.headers["content-type"] || ""});
    const body = capture(id, "request");
    let completed = false;
    const out = http.request({hostname: target.hostname.replace(/^\[|\]$/g, ""), port: target.port || 80, method: req.method, path: url.pathname + url.search, headers: requestHeaders(req)}, reply => {
      const headers = headersOf(reply.headers), trailers = trailerNames(reply.headers);
      if (trailers.length) { headers.trailer = trailers.join(", "); delete headers["content-length"]; }
      record({id, event: "headers", status: reply.statusCode, content_type: reply.headers["content-type"] || ""});
      res.writeHead(reply.statusCode, headers); res.flushHeaders();
      let bytes = 0;
      reply.on("data", chunk => { if (!bytes) record({id, event: "first_bytes"}); bytes += chunk.length; });
      reply.once("end", () => { completed = true; res.addTrailers(headersOf(reply.trailers)); res.end(); record({id, event: "response_end", bytes}); });
      reply.once("aborted", () => { record({id, event: "response_aborted", bytes}); res.destroy(); });
      reply.on("error", () => res.destroy());
      reply.pipe(res, {end: false});
    });
    out.on("socket", track);
    out.on("error", error => { record({id, event: "upstream_error", error: error.code}); if (!res.headersSent) res.writeHead(502); res.end(); });
    req.on("data", chunk => body.data(chunk));
    req.once("end", () => { body.end(true); out.addTrailers(headersOf(req.trailers)); out.end(); });
    req.once("aborted", () => { body.end(false); out.destroy(); });
    req.on("error", () => { body.end(false); out.destroy(); });
    res.once("close", () => { if (!completed) { record({id, event: "client_closed"}); out.destroy(); } });
    req.pipe(body.sink, {end: false});
    req.pipe(out, {end: false});
  });
  server.on("connect", (req, client, head) => {
    nextId();
    if (!valid || req.url !== new URL(origin).host || tunnelPorts.has(client.remotePort) || !methods.includes("WEBSOCKET")) {
      record({event: "blocked_connect", url: req.url}); client.end("HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n"); return;
    }
    // Chromium tunnels WS through a forward proxy. Feed that tunnel through the same scoped
    // HTTP parser, so cookies, method policy, upgrade handling and wire auditing still apply.
    const remote = net.connect({host: "127.0.0.1", port: server.address().port}, () => {
      tunnelPorts.add(remote.localPort);
      client.write("HTTP/1.1 200 Connection Established\r\n\r\n");
      if (head.length) remote.write(head); client.pipe(remote); remote.pipe(client);
    });
    track(remote);
    const localPort = () => remote.localPort;
    remote.once("connect", () => { const p = localPort(); remote.once("close", () => tunnelPorts.delete(p)); });
    remote.once("close", () => client.destroy()); client.once("close", () => remote.destroy());
    remote.on("error", () => client.destroy()); client.on("error", () => remote.destroy());
    record({event: "connect", authority: req.url});
  });
  server.on("upgrade", (req, client, head) => {
    const id = nextId(), url = address(req);
    if (!valid || !url || !methods.includes("WEBSOCKET") || req.method !== "GET" || req.headers.upgrade?.toLowerCase() !== "websocket") {
      record({id, event: "blocked_upgrade", url: req.url}); client.end("HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n"); return;
    }
    record({id, event: "request", method: "WEBSOCKET", path: url.pathname + url.search, content_type: ""});
    const out = http.request({hostname: target.hostname.replace(/^\[|\]$/g, ""), port: target.port || 80, path: url.pathname + url.search, headers: {...requestHeaders(req), connection: "Upgrade", upgrade: "websocket"}});
    out.on("socket", track);
    out.once("upgrade", (reply, remote, remoteHead) => {
      track(remote);
      record({id, event: "headers", status: reply.statusCode, extensions: reply.headers["sec-websocket-extensions"] || "", protocol: reply.headers["sec-websocket-protocol"] || ""});
      client.write(`HTTP/1.1 ${reply.statusCode} ${reply.statusMessage}\r\n` + reply.rawHeaders.reduce((text, value, i, values) => i % 2 ? text : text + value + ": " + values[i+1] + "\r\n", "") + "\r\n");
      const up = capture(id, "ws-up"), down = capture(id, "ws-down");
      if (head.length) { up.data(head); up.sink.write(head); remote.write(head); }
      if (remoteHead.length) { down.data(remoteHead); down.sink.write(remoteHead); client.write(remoteHead); }
      client.on("data", chunk => up.data(chunk)); remote.on("data", chunk => down.data(chunk));
      client.pipe(up.sink, {end: false}); remote.pipe(down.sink, {end: false});
      client.pipe(remote); remote.pipe(client);
      client.once("end", () => up.end(true)); remote.once("end", () => down.end(true));
      client.once("close", () => { up.end(false); remote.destroy(); record({id, event: "socket_closed"}); });
      remote.once("close", () => { down.end(false); client.destroy(); });
      client.on("error", () => remote.destroy()); remote.on("error", () => client.destroy());
    });
    out.once("response", reply => { record({id, event: "upgrade_refused", status: reply.statusCode}); client.end(`HTTP/1.1 ${reply.statusCode} Rejected\r\nContent-Length: 0\r\n\r\n`); reply.resume(); });
    out.on("error", error => { record({id, event: "upstream_error", error: error.code}); client.destroy(); });
    client.once("close", () => out.destroy()); out.end();
  });
  server.on("connection", track);
  server.on("clientError", (error, socket) => { record({event: "unsupported_or_malformed", error: error.code}); socket.end("HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n"); });
  server.requestTimeout = 0; server.timeout = 0; server.maxConnections = 128;
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(port, "127.0.0.1", resolve); });
  const proxy = `http://127.0.0.1:${server.address().port}`;
  origin = fixedOrigin || proxy;
  return {origin, proxy, async stop() {
    if (stopped) return {valid}; stopped = true;
    for (const socket of sockets) socket.destroy();
    await new Promise(resolve => server.close(resolve));
    await Promise.all([...writers]);
    record({event: "audit_end", valid, captured_bytes: used}); fs.closeSync(log);
    return {valid};
  }};
}

module.exports = {startProxy};
if (require.main === module) {
  startProxy(JSON.parse(fs.readFileSync(process.argv[2], "utf8"))).then(proxy => {
    console.log(JSON.stringify({origin: proxy.origin, proxy: proxy.proxy}));
    process.once("SIGTERM", async () => { const result = await proxy.stop(); process.exit(result.valid ? 0 : 2); });
  }).catch(error => { console.error(error.message); process.exit(1); });
}
