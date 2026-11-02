import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";

// The spec's restrictive default, which is what a compiled MCP App asks for by listing no domains.
const CSP =
  "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; " +
  "img-src 'self' data:; media-src 'self' data:; connect-src 'none';";

(window as any).startHost = async (html: string) => {
  const log: any = { initialized: 0, sizes: [], logs: [] };
  const iframe = document.createElement("iframe");
  iframe.id = "view";
  iframe.setAttribute("sandbox", "allow-scripts");
  iframe.style.cssText = "display:block;width:100%;height:100vh;border:0";
  document.body.appendChild(iframe);
  const bridge = new AppBridge(
    null,
    { name: "viewspec-prove", version: "1" },
    { serverTools: {}, logging: {}, message: { text: {} }, updateModelContext: { text: {} } },
  );
  bridge.oninitialized = () => {
    log.initialized += 1;
  };
  bridge.onsizechange = (params) => {
    log.sizes.push(params);
    // A flexible host: the iframe follows what the view reports, and the width stays the viewport's.
    if (params.height != null) iframe.style.height = `${params.height}px`;
  };
  bridge.onloggingmessage = (params) => {
    log.logs.push(params);
  };
  await bridge.connect(new PostMessageTransport(iframe.contentWindow!, iframe.contentWindow!));
  iframe.srcdoc = html.replace("<head>", `<head><meta http-equiv="Content-Security-Policy" content="${CSP}">`);
  (window as any).bridge = bridge;
  (window as any).hostLog = log;
};
