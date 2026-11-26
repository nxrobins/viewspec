// Readiness is scoped evidence, never permission to send a request or proof of persistence.
const { EventEmitter } = require("node:events");

class RequestActivity {
  constructor() {
    this.sequence = 0;
    this.pending = new Map();
    this.kinds = new WeakMap();
    this.events = new EventEmitter();
    this.revision = 0;
  }
  changed() { this.revision++; this.events.emit("change"); }
  started(request) {
    this.pending.set(request, ++this.sequence);
    this.kinds.set(request, "unknown");
    this.changed();
  }
  response(response) {
    const request = response.request();
    if (!this.pending.has(request)) return;
    const type = (response.headers()["content-type"] || "").split(";", 1)[0].trim().toLowerCase();
    this.kinds.set(request, type === "text/event-stream" ? "stream" : "finite");
    this.changed();
  }
  finished(request) {
    this.pending.delete(request);
    this.changed();
  }
  counts(after = -1) {
    const counts = {finite: 0, streams: 0, unknown: 0};
    for (const [request, sequence] of this.pending) {
      if (sequence <= after) continue;
      const kind = this.kinds.get(request);
      counts[kind === "stream" ? "streams" : kind === "finite" ? "finite" : "unknown"]++;
    }
    return counts;
  }
  streams() { return new Set([...this.pending.keys()].filter(request => this.kinds.get(request) === "stream")); }
  blockers(background = null) {
    // A new stream opened by a form edit is not known to be background validation-free work.
    return [...this.pending].filter(([request]) =>
      this.kinds.get(request) !== "stream" || (background !== null && !background.has(request)));
  }
  async wait(check, timeoutMs) {
    const began = performance.now();
    const deadline = began + Math.max(0, timeoutMs);
    while (true) {
      const revision = this.revision;
      const ready = await check();
      if (ready || performance.now() >= deadline) return {
        state: ready ? "ready" : "timed_out", waited_ms: Math.round(performance.now() - began),
      };
      if (this.revision !== revision) continue;
      // Request lifecycle events wake immediately. The bounded fallback also observes property
      // changes (aria-busy, field state) that do not produce a network event.
      await new Promise(resolve => {
        let timer;
        const done = () => { clearTimeout(timer); this.events.off("change", done); resolve(); };
        this.events.once("change", done);
        timer = setTimeout(done, Math.min(100, Math.max(0, deadline - performance.now())));
        if (this.revision !== revision) done();
      });
    }
  }
}

module.exports = { RequestActivity };
