// Drives compiled MCP Apps through the ext-apps SDK's own AppBridge in Chromium. The view runs in a
// sandboxed iframe under the spec's default CSP, so the SDK's schemas decide what counts as valid.
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium } from "playwright";

const here = path.dirname(fileURLToPath(import.meta.url));
const views = process.argv[2];
const bundled = await build({
  entryPoints: [path.join(here, "host.ts")],
  absWorkingDir: here,
  bundle: true,
  format: "iife",
  write: false,
});
const hostScript = bundled.outputFiles[0].text;
const results = [];
const check = (name, ok, detail = "") => results.push({ name, ok: Boolean(ok), detail: ok ? "" : JSON.stringify(detail) });

async function open(browser, file) {
  const page = await browser.newPage();
  const problems = [];
  const requests = [];
  page.on("console", (m) => {
    if (["error", "warning"].includes(m.type())) problems.push(m.text());
  });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("request", (r) => requests.push(r.url()));
  await page.setContent("<!doctype html><html><body style='margin:0'></body></html>");
  await page.addScriptTag({ content: hostScript });
  await page.evaluate((html) => window.startHost(html), readFileSync(path.join(views, file), "utf8"));
  await page.waitForFunction(() => window.hostLog.initialized >= 1, null, { timeout: 8000 });
  await page.waitForFunction(() => window.hostLog.sizes.length >= 1, null, { timeout: 8000 });
  const frame = page.frames().find((f) => f !== page.mainFrame());
  const log = () => page.evaluate(() => window.hostLog);
  return { page, frame, log, problems, requests };
}

const browser = await chromium.launch();

// ---- Full view
{
  const { page, frame, log, problems, requests } = await open(browser, "view.html");
  const text = (id) => frame.locator(`[data-binding-id="${id}"]`).innerText();
  const height = () => page.evaluate(() => document.getElementById("view").getBoundingClientRect().height);

  let l = await log();
  check("handshake: initialized fired exactly once", l.initialized === 1, l.initialized);
  check("size: reported after handshake", l.sizes.length >= 1 && l.sizes[0].height > 0, l.sizes);

  await page.evaluate(() => window.bridge.sendToolInput({ arguments: {} }));
  await page.evaluate(() =>
    window.bridge.sendToolResult({
      content: [{ type: "text", text: "x" }],
      structuredContent: { viewspecBindings: { inc1_value: "from tool result", inc2_label: "INC-999" } },
    }),
  );
  await frame.locator('[data-binding-id="inc1_value"]', { hasText: "from tool result" }).waitFor({ timeout: 3000 });
  check("rebind: ui/notifications/tool-result updates value", (await text("inc1_value")) === "from tool result");
  check("rebind: updates label too", (await text("inc2_label")) === "INC-999");

  // XSS through a binding value stays inert text.
  const payload = "<img src=x onerror=\"document.title='pwned'\">";
  await page.evaluate(
    (v) => window.bridge.sendToolResult({ content: [], structuredContent: { viewspecBindings: { inc1_value: v } } }),
    payload,
  );
  await frame.locator('[data-binding-id="inc1_value"]', { hasText: "onerror" }).waitFor({ timeout: 3000 });
  check("xss: binding value rendered as text", (await text("inc1_value")) === payload);
  check("xss: no element injected", (await frame.locator("img").count()) === 0);
  check("xss: title untouched", (await frame.title()) !== "pwned");

  // isError results must not touch the DOM.
  const before = await text("inc2_value");
  await page.evaluate(() =>
    window.bridge.sendToolResult({ isError: true, content: [], structuredContent: { viewspecBindings: { inc2_value: "BAD" } } }),
  );
  await page.waitForTimeout(150);
  check("rebind: isError result ignored", (await text("inc2_value")) === before);

  // Forged message from a sibling frame.
  await page.evaluate(() => {
    const sibling = document.createElement("iframe");
    sibling.setAttribute("sandbox", "allow-scripts");
    sibling.srcdoc = `<script>parent.frames[0].postMessage({jsonrpc:'2.0',method:'ui/notifications/tool-result',params:{structuredContent:{viewspecBindings:{inc2_value:'FORGED'}}}},'*')<\/script>`;
    document.body.appendChild(sibling);
  });
  await page.waitForTimeout(300);
  check("spoof: message from a non-parent window ignored", (await text("inc2_value")) === before);

  // Ratchet: a long value grows the iframe, a short one must shrink it again.
  await page.evaluate(() =>
    window.bridge.sendToolResult({ content: [], structuredContent: { viewspecBindings: { inc1_value: "long ".repeat(900) } } }),
  );
  await page.waitForTimeout(400);
  const tall = await height();
  await page.evaluate(() =>
    window.bridge.sendToolResult({ content: [], structuredContent: { viewspecBindings: { inc1_value: "short" } } }),
  );
  await page.waitForTimeout(400);
  const short = await height();
  check(`size: grows and shrinks with content (${tall}px -> ${short}px)`, tall > short + 100 && short < 700, { tall, short });

  // Mapped action -> tools/call, and the response rebinds.
  await frame.locator('[data-binding-id="query_value"]').fill("typed value");
  await frame.locator('[data-action-id="refresh"]').click();
  await frame.locator('[data-binding-id="inc1_value"]', { hasText: "filtered:typed value" }).waitFor({ timeout: 3000 });
  l = await log();
  check("action: mapped -> tools/call with name", l.calls.at(-1)?.name === "refresh_incidents", l.calls);
  check("action: tools/call args keyed by binding", l.calls.at(-1)?.arguments?.query_value === "typed value", l.calls);
  check("action: tools/call response rebinds", (await text("inc1_value")) === "filtered:typed value");
  check("action: button re-enabled", await frame.locator('[data-action-id="refresh"]').isEnabled());

  // Unmapped -> ui/message, validated by the SDK's schema (array content).
  await frame.locator('[data-action-id="approve"]').click();
  await page.waitForFunction(() => window.hostLog.messages.length === 1, null, { timeout: 3000 });
  l = await log();
  check("action: unmapped -> ui/message accepted by SDK", l.messages[0]?.role === "user" && Array.isArray(l.messages[0]?.content), l.messages);
  check("action: message carries label + payload", /^Approve: /.test(l.messages[0]?.content?.[0]?.text) && l.messages[0].content[0].text.includes("typed value"), l.messages);

  // select -> ui/update-model-context
  await frame.locator('[data-action-id="pick"]').click();
  await page.waitForFunction(() => window.hostLog.context.length === 1, null, { timeout: 3000 });
  l = await log();
  check("action: select -> ui/update-model-context", Array.isArray(l.context[0]?.content), l.context);
  check("action: select does not post to chat", l.messages.length === 1, l.messages);

  // Tool failures are visible and logged.
  await frame.locator('[data-action-id="boom"]').click();
  await frame.locator('[data-action-id="boom"][data-mcp-state="error"]').waitFor({ timeout: 3000 });
  check("failure: isError marks the button", true);
  await frame.locator('[data-action-id="explode"]').click();
  await frame.locator('[data-action-id="explode"][data-mcp-state="error"]').waitFor({ timeout: 3000 });
  check("failure: JSON-RPC error marks the button", true);
  await page.waitForFunction(() => window.hostLog.logs.length >= 2, null, { timeout: 3000 });
  l = await log();
  check("failure: logged via notifications/message", l.logs.every((x) => x.level === "error"), l.logs);
  check("failure: button usable again", await frame.locator('[data-action-id="boom"]').isEnabled());

  // Host-to-view requests.
  const rawRequest = (method) =>
    page.evaluate(
      (m) =>
        new Promise((resolve) => {
          const id = 9000 + m.length;
          const listener = (event) => {
            if (event.data?.id !== id) return;
            window.removeEventListener("message", listener);
            resolve(event.data);
          };
          window.addEventListener("message", listener);
          document.getElementById("view").contentWindow.postMessage({ jsonrpc: "2.0", id, method: m }, "*");
        }),
      method,
    );
  const ping = await rawRequest("ping");
  check("host request: ping answered with a result", ping.result && !ping.error, ping);
  const unknown = await rawRequest("ui/not-a-method");
  check("host request: unknown method gets -32601", unknown.error?.code === -32601, unknown);
  const teardown = await page.evaluate(async () => {
    try {
      await window.bridge.teardownResource({});
      return "ok";
    } catch (e) {
      return String(e);
    }
  });
  check("host request: ui/resource-teardown answered", teardown === "ok", teardown);

  const csp = problems.filter((p) => /content security policy|refused/i.test(p));
  check("csp: no violations under the spec default policy", csp.length === 0, csp);
  const real = problems.filter((p) => !/kaboom/i.test(p));
  check("console: no errors or warnings", real.length === 0, real);
  const external = requests.filter((u) => !/^(about:|data:)/.test(u));
  check("network: zero requests", external.length === 0, external);
  await page.close();
}

// ---- View with no actions (bridge only, no action runtime)
{
  const { page, frame, log, problems } = await open(browser, "view_noactions.html");
  const l = await log();
  check("no-actions view: handshake + size", l.initialized === 1 && l.sizes.length >= 1, l);
  await page.evaluate(() =>
    window.bridge.sendToolResult({ content: [], structuredContent: { viewspecBindings: { c1_value: "99" } } }),
  );
  await frame.locator('[data-binding-id="c1_value"]', { hasText: "99" }).waitFor({ timeout: 3000 });
  check("no-actions view: rebind works", true);
  check("no-actions view: clean console", problems.length === 0, problems);
  await page.close();
}

// ---- Opened directly, with no host at all
{
  const page = await browser.newPage();
  const problems = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await page.setContent(readFileSync(path.join(views, "view.html"), "utf8"));
  await page.locator('[data-action-id="refresh"]').click();
  await page.waitForTimeout(150);
  check("standalone: bridge inert without a host", problems.length === 0, problems);
  check("standalone: action not stuck disabled", await page.locator('[data-action-id="refresh"]').isEnabled());
  await page.close();
}

await browser.close();
let failed = 0;
for (const r of results) {
  if (!r.ok) failed += 1;
  console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.name}${r.ok ? "" : `  -> ${r.detail}`}`);
}
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
