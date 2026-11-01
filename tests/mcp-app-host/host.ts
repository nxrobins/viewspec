import { AppBridge, PostMessageTransport } from "@modelcontextprotocol/ext-apps/app-bridge";

// The spec's restrictive default, used when a resource declares no ui.csp.
const CSP =
  "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; " +
  "img-src 'self' data:; media-src 'self' data:; connect-src 'none';";

(window as any).startHost = async (html: string) => {
  const log: any = { initialized: 0, sizes: [], calls: [], messages: [], context: [], logs: [] };
  const iframe = document.createElement("iframe");
  iframe.id = "view";
  iframe.setAttribute("sandbox", "allow-scripts");
  iframe.style.cssText = "width:600px;height:500px;border:0";
  document.body.appendChild(iframe);
  const bridge = new AppBridge(
    null,
    { name: "harness", version: "1" },
    { serverTools: {}, logging: {}, message: { text: {} }, updateModelContext: { text: {} } },
  );
  bridge.oninitialized = () => {
    log.initialized += 1;
  };
  bridge.onsizechange = (params) => {
    log.sizes.push(params);
    // A flexible host: the iframe follows what the view reports.
    if (params.height != null) iframe.style.height = `${params.height}px`;
  };
  bridge.onloggingmessage = (params) => {
    log.logs.push(params);
  };
  bridge.onmessage = async (params) => {
    log.messages.push(params);
    return {};
  };
  bridge.onupdatemodelcontext = async (params) => {
    log.context.push(params);
    return {};
  };
  bridge.oncalltool = async (params) => {
    log.calls.push(params);
    if (params.name === "rpc_error_tool") throw new Error("kaboom");
    if (params.name === "failing_tool") return { isError: true, content: [{ type: "text", text: "nope" }] };
    return {
      content: [{ type: "text", text: "ok" }],
      structuredContent: { viewspecBindings: { inc1_value: `filtered:${params.arguments?.query_value}` } },
    };
  };
  await bridge.connect(new PostMessageTransport(iframe.contentWindow!, iframe.contentWindow!));
  iframe.srcdoc = html.replace("<head>", `<head><meta http-equiv="Content-Security-Policy" content="${CSP}">`);
  (window as any).bridge = bridge;
  (window as any).hostLog = log;
};
