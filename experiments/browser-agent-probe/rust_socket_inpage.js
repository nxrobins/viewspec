(() => {
  if (window.__viewspecSocketReply) return;
  const request = window.__viewspecSocketRequest,
    sockets = new Map(),
    states = new WeakMap();
  let next = 0;
  const encoder = new TextEncoder();
  const emit = (socket, type, event) => {
    socket.dispatchEvent(event);
    socket["on" + type]?.call(socket, event);
  };
  const bytes = (data) =>
    typeof data === "string"
      ? encoder.encode(data).length
      : (data.size ?? data.byteLength);
  const base64 = (buffer) => {
    let text = "";
    for (const byte of new Uint8Array(buffer))
      text += String.fromCharCode(byte);
    return btoa(text);
  };
  class RoutedWebSocket extends EventTarget {
    constructor(address, protocols = []) {
      super();
      const url = new URL(address, document.baseURI);
      if (url.protocol === "http:") url.protocol = "ws:";
      if (url.protocol === "https:") url.protocol = "wss:";
      if (!["ws:", "wss:"].includes(url.protocol) || url.hash)
        throw new DOMException("Invalid WebSocket URL", "SyntaxError");
      protocols =
        typeof protocols === "string" ? [protocols] : Array.from(protocols);
      if (
        protocols.some(
          (p) =>
            typeof p !== "string" ||
            !p ||
            !/^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/.test(p),
        ) ||
        new Set(protocols).size !== protocols.length
      )
        throw new DOMException("Invalid WebSocket protocol", "SyntaxError");
      const id = ++next;
      states.set(this, {
        id,
        url: url.href,
        state: 0,
        protocol: "",
        extensions: "",
        binaryType: "blob",
        buffered: 0,
        queue: Promise.resolve(),
      });
      sockets.set(id, this);
      request(JSON.stringify({ op: "open", id, url: url.href, protocols }));
    }
    get url() {
      return states.get(this).url;
    }
    get readyState() {
      return states.get(this).state;
    }
    get protocol() {
      return states.get(this).protocol;
    }
    get extensions() {
      return states.get(this).extensions;
    }
    get bufferedAmount() {
      return states.get(this).buffered;
    }
    get binaryType() {
      return states.get(this).binaryType;
    }
    set binaryType(value) {
      if (["blob", "arraybuffer"].includes(value))
        states.get(this).binaryType = value;
    }
    send(data) {
      const state = states.get(this);
      if (state.state === 0)
        throw new DOMException("WebSocket is connecting", "InvalidStateError");
      if (state.state !== 1) return;
      const binary =
        data instanceof Blob ||
        data instanceof ArrayBuffer ||
        ArrayBuffer.isView(data);
      if (!binary) data = String(data);
      const size = bytes(data);
      if (size > 8 * 1024 * 1024)
        throw new DOMException("Message exceeds limit", "QuotaExceededError");
      state.buffered += size;
      state.queue = state.queue.then(async () => {
        const body = !binary
          ? data
          : base64(
              data instanceof Blob
                ? await data.arrayBuffer()
                : ArrayBuffer.isView(data)
                  ? data.buffer.slice(
                      data.byteOffset,
                      data.byteOffset + data.byteLength,
                    )
                  : data,
            );
        request(
          JSON.stringify({ op: "send", id: state.id, data: body, binary }),
        );
      });
    }
    close(code, reason = "") {
      if (
        code !== undefined &&
        code !== 1000 &&
        (!Number.isInteger(code) || code < 3000 || code > 4999)
      )
        throw new DOMException("Invalid close code", "InvalidAccessError");
      if (encoder.encode(reason).length > 123)
        throw new DOMException("Close reason too long", "SyntaxError");
      const state = states.get(this);
      if (state.state >= 2) return;
      state.state = 2;
      state.queue = state.queue.then(() =>
        request(JSON.stringify({ op: "close", id: state.id, code, reason })),
      );
    }
  }
  for (const [name, value] of Object.entries({
    CONNECTING: 0,
    OPEN: 1,
    CLOSING: 2,
    CLOSED: 3,
  })) {
    Object.defineProperty(RoutedWebSocket, name, { value });
    Object.defineProperty(RoutedWebSocket.prototype, name, { value });
  }
  Object.defineProperty(window, "WebSocket", {
    value: RoutedWebSocket,
    writable: true,
    configurable: true,
  });
  window.__viewspecSocketReply = (message) => {
    const socket = sockets.get(message.id);
    if (!socket) return;
    const state = states.get(socket);
    if (message.type === "open") {
      if (state.state !== 0) return;
      state.state = 1;
      state.protocol = message.protocol;
      state.extensions = message.extensions;
      emit(socket, "open", new Event("open"));
    } else if (message.type === "sent")
      state.buffered = Math.max(0, state.buffered - message.bytes);
    else if (message.type === "error")
      emit(socket, "error", new Event("error"));
    else if (message.type === "close") {
      state.state = 3;
      state.buffered = 0;
      sockets.delete(message.id);
      emit(
        socket,
        "close",
        new CloseEvent("close", {
          code: message.code,
          reason: message.reason,
          wasClean: message.clean,
        }),
      );
    } else if (message.type === "message") {
      let data = message.data;
      if (message.binary) {
        const buffer = Uint8Array.from(atob(data), (c) =>
          c.charCodeAt(0),
        ).buffer;
        data = state.binaryType === "arraybuffer" ? buffer : new Blob([buffer]);
      }
      emit(
        socket,
        "message",
        new MessageEvent("message", {
          data,
          origin: new URL(state.url).origin,
        }),
      );
    }
  };
})();
