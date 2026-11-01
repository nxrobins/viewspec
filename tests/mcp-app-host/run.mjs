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
const renders = JSON.parse(readFileSync(path.join(views, "renders.json"), "utf8"));
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
  l = await log();
  check("calls: loading the view and its opening result send no tools/call", l.calls.length === 0, l.calls);

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
  await page.waitForTimeout(150);
  l = await log();
  check("calls: one click sends exactly one tools/call", l.calls.length === 1, l.calls);
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

// ---- Reopened: a host can reload a view without running the opening tool, so no result arrives
{
  const { page, frame, log, problems } = await open(browser, "view.html");
  await page.waitForTimeout(300);
  let l = await log();
  check("reopen: the view sends no tools/call on its own", l.calls.length === 0, l.calls);
  check("reopen: the document stands alone", (await frame.locator("tr").count()) === 2);
  await page.evaluate((r) => void (window.nextCallResult = r), renders.five);
  await frame.locator('[data-action-id="refresh"]').click();
  await frame.waitForFunction(() => document.querySelectorAll("tr").length === 5, null, { timeout: 3000 });
  await page.waitForTimeout(150);
  l = await log();
  check("reopen: one click brings the data back", l.calls.length === 1, l.calls);
  check("reopen: clean console", problems.length === 0, problems);
  await page.close();
}

// ---- A tool result changes how many rows the view shows
{
  const { page, frame, log, problems, requests } = await open(browser, "view.html");
  const text = (id) => frame.locator(`[data-binding-id="${id}"]`).innerText();
  const height = () => page.evaluate(() => document.getElementById("view").getBoundingClientRect().height);
  const send = (result) => page.evaluate((r) => window.bridge.sendToolResult(r), result);
  const answerCallsWith = (result) => page.evaluate((r) => void (window.nextCallResult = r), result);
  const rows = () => frame.locator("tr").count();
  const rowsBecome = (n) =>
    frame.waitForFunction((want) => document.querySelectorAll("tr").length === want, n, { timeout: 3000 });
  const activeIdentity = () =>
    frame.evaluate(() => {
      const el = document.activeElement;
      return el?.dataset.actionId ?? el?.dataset.bindingId ?? el?.tagName;
    });
  const pwned = () => frame.evaluate(() => window.pwned ?? null);
  const holdCalls = () =>
    page.evaluate(() => {
      window.hold = new Promise((release) => {
        window.release = release;
      });
    });
  const releaseCalls = () =>
    page.evaluate(() => {
      window.release();
      window.hold = undefined;
    });
  const query = frame.locator('[data-binding-id="query_value"]');
  const refresh = frame.locator('[data-action-id="refresh"]');

  check("render: the view opens with 2 rows", (await rows()) === 2, await rows());
  await page.evaluate(() => window.bridge.sendToolInput({ arguments: {} }));

  // 2 -> 5, from the result of the tool that opened the view, while the user is typing.
  await query.fill("typed value");
  await query.evaluate((el) => {
    el.focus();
    el.setSelectionRange(2, 5);
  });
  await page.waitForTimeout(400);
  const twoRowHeight = await height();
  await send(renders.five);
  await rowsBecome(5);
  check("render: a tool result grows the table from 2 rows to 5", (await rows()) === 5, await rows());
  check("render: the new rows carry their data", (await text("inc5_label")) === "INC-406", await text("inc5_label"));
  check("render: typed text survives the swap", (await query.inputValue()) === "typed value", await query.inputValue());
  check("render: focus survives the swap", (await activeIdentity()) === "query_value", await activeIdentity());
  const caret = await query.evaluate((el) => [el.selectionStart, el.selectionEnd]);
  check("render: the caret survives the swap", caret[0] === 2 && caret[1] === 5, caret);
  await page.waitForTimeout(400);
  check("calls: a render sends no tools/call", (await log()).calls.length === 0, (await log()).calls);
  const fiveRowHeight = await height();
  check(`size: follows a render up (${twoRowHeight}px -> ${fiveRowHeight}px)`, fiveRowHeight > twoRowHeight, {
    twoRowHeight,
    fiveRowHeight,
  });

  // 5 -> 1, from an action's response, which also proves the wiring outlived the first swap.
  await answerCallsWith(renders.one);
  await refresh.focus();
  await page.keyboard.press("Enter");
  await rowsBecome(1);
  await page.waitForTimeout(150);
  let l = await log();
  check("calls: one key press on a swapped button sends exactly one tools/call", l.calls.length === 1, l.calls);
  check("render: an action response shrinks the table from 5 rows to 1", (await rows()) === 1, await rows());
  check("render: a swapped button still calls its tool", l.calls.at(-1)?.name === "refresh_incidents", l.calls);
  check("render: a swapped button still sends its payload", l.calls.at(-1)?.arguments?.query_value === "typed value", l.calls);
  await frame.locator('[data-action-id="refresh"]:enabled').waitFor({ timeout: 3000 });
  check("render: the replacement button is enabled", await refresh.isEnabled());
  check("render: keyboard focus returns to the button", (await activeIdentity()) === "refresh", await activeIdentity());
  await page.waitForTimeout(400);
  const oneRowHeight = await height();
  check(`size: follows a render down (${fiveRowHeight}px -> ${oneRowHeight}px)`, oneRowHeight < fiveRowHeight, {
    fiveRowHeight,
    oneRowHeight,
  });

  // And it keeps working after a second swap, for every route an action can take.
  await answerCallsWith(renders.five);
  await refresh.click();
  await rowsBecome(5);
  await page.waitForTimeout(150);
  l = await log();
  check("render: an action works again after two swaps", (await rows()) === 5, await rows());
  check("calls: swaps do not stack listeners, so a click is still one tools/call", l.calls.length === 2, l.calls);
  await answerCallsWith(null);
  await frame.locator('[data-action-id="approve"]').click();
  await page.waitForFunction(() => window.hostLog.messages.length === 1, null, { timeout: 3000 });
  l = await log();
  check("render: an unmapped action still reaches ui/message", l.messages[0]?.content?.[0]?.text.includes("typed value"), l.messages);
  await frame.locator('[data-action-id="pick"]').click();
  await page.waitForFunction(() => window.hostLog.context.length === 1, null, { timeout: 3000 });
  check("render: select still reaches ui/update-model-context", true);
  await frame.locator('[data-action-id="boom"]').click();
  await frame.locator('[data-action-id="boom"][data-mcp-state="error"]').waitFor({ timeout: 3000 });
  check("render: a failure marks the live button", await frame.locator('[data-action-id="boom"]').isEnabled());

  // A render and bindings in one result: the bindings apply to the new root.
  await send({
    content: [],
    structuredContent: { ...renders.one.structuredContent, viewspecBindings: { inc1_value: "bound after render" } },
  });
  await rowsBecome(1);
  check("render: bindings in the same result reach the new root", (await text("inc1_value")) === "bound after render");

  // A swap that lands while a tool is running must not free its button early.
  await holdCalls();
  await answerCallsWith(renders.five);
  await refresh.click();
  await frame.locator('[data-action-id="refresh"]:disabled').waitFor({ timeout: 3000 });
  await send(renders.one);
  await page.waitForTimeout(150);
  check("render: a button waiting on a tool stays disabled across a swap", await refresh.isDisabled());
  await releaseCalls();
  await rowsBecome(5);
  await frame.locator('[data-action-id="refresh"]:enabled').waitFor({ timeout: 3000 });
  check("render: that button is freed when its tool finishes", await refresh.isEnabled());

  // And a failure that lands after a swap marks the button on the page, not the one it replaced.
  await holdCalls();
  await frame.locator('[data-action-id="boom"]').click();
  await send(renders.one);
  await rowsBecome(1);
  await releaseCalls();
  await frame.locator('[data-action-id="boom"][data-mcp-state="error"]').waitFor({ timeout: 3000 });
  check("render: a failure after a swap marks the live button", await frame.locator('[data-action-id="boom"]').isEnabled());

  // A render can drop the very button that asked for it.
  await answerCallsWith(renders.no_actions);
  await refresh.click();
  await rowsBecome(1);
  await refresh.waitFor({ state: "detached", timeout: 3000 });
  check("render: an action can render its own button away", (await frame.locator("[data-action-id]").count()) === 0);
  await send(renders.five);
  await rowsBecome(5);
  check("render: a later render brings the action back", await refresh.isEnabled());
  await answerCallsWith(null);

  // A result with isError changes nothing, and neither does a forged sender.
  await send({ ...renders.one, isError: true });
  await page.waitForTimeout(150);
  check("render: isError result ignored", (await rows()) === 5, await rows());
  await page.evaluate((params) => {
    const sibling = document.createElement("iframe");
    sibling.setAttribute("sandbox", "allow-scripts");
    const message = { jsonrpc: "2.0", method: "ui/notifications/tool-result", params };
    sibling.srcdoc = `<script>parent.frames[0].postMessage(${JSON.stringify(message)},'*')<\/script>`;
    document.body.appendChild(sibling);
  }, renders.one);
  await page.waitForTimeout(300);
  check("spoof: a render from a non-parent window ignored", (await rows()) === 5, await rows());

  // Tool data that goes through the compiler arrives as text.
  await send(renders.hostile);
  await rowsBecome(1);
  check("xss: hostile data stays text through the compiler", (await text("inc1_value")).includes("onerror"), await text("inc1_value"));
  check("xss: no element injected through the compiler", (await frame.locator("img").count()) === 0);

  // A root that did not come from the compiler is refused whole. The first four execute under a
  // plain DOMParser swap at this CSP, and a refused root leaves the single row above in place.
  const forged = {
    "img onerror": `<main class="vs-root"><img src=x onerror="window.pwned='img'"></main>`,
    "details ontoggle": `<main class="vs-root"><details open ontoggle="window.pwned='details'">x</details></main>`,
    "source onerror": `<main class="vs-root"><video><source src=x onerror="window.pwned='video'"></video></main>`,
    "style onload": `<main class="vs-root"><style onload="window.pwned='style'"></style></main>`,
    "handler on the root": `<main class="vs-root" onclick="window.pwned='root'"><button>x</button></main>`,
    "handler on an allowed tag": `<main class="vs-root"><div onclick="window.pwned='div'">x</div></main>`,
    "javascript link": `<main class="vs-root"><a href="javascript:window.pwned='a'">x</a></main>`,
    "script": `<main class="vs-root"><script>window.pwned='script'<\/script></main>`,
    "svg onload": `<main class="vs-root"><svg onload="window.pwned='svg'"></svg></main>`,
    "second top-level element": `<main class="vs-root"></main><img src=x onerror="window.pwned='second'">`,
    "element in head": `<link rel="stylesheet" href="x"><main class="vs-root"></main>`,
    "root without the class": `<main><div>x</div></main>`,
    "plain text": "not markup",
    "not a string": 42,
    "null": null,
  };
  for (const [name, root] of Object.entries(forged)) {
    const before = (await log()).logs.length;
    await send({ content: [], structuredContent: { viewspecRoot: root } });
    const logged = await page
      .waitForFunction((n) => window.hostLog.logs.length === n + 1, before, { timeout: 1000 })
      .then(
        () => true,
        () => false,
      );
    await page.waitForTimeout(100);
    await frame
      .locator(".vs-root")
      .click({ position: { x: 1, y: 1 }, timeout: 500 })
      .catch(() => {});
    const outcome = { logged, rows: await rows(), pwned: await pwned() };
    const refused = logged && outcome.rows === 1 && outcome.pwned === null;
    check(`xss: forged root refused: ${name}`, refused, outcome);
    if (!refused) {
      await frame.evaluate(() => void delete window.pwned);
      await send(renders.one);
      await rowsBecome(1);
    }
  }
  await answerCallsWith(renders.five);
  await refresh.click();
  await rowsBecome(5);
  check("xss: the view still renders after refusing roots", (await rows()) === 5, await rows());

  const csp = problems.filter((p) => /content security policy|refused/i.test(p));
  check("render: no CSP violations", csp.length === 0, csp);
  check("render: console clean", problems.length === 0, problems);
  const external = requests.filter((u) => !/^(about:|data:)/.test(u));
  check("render: zero network requests", external.length === 0, external);
  await page.close();
}

// ---- View compiled with no actions
{
  const { page, frame, log, problems } = await open(browser, "view_noactions.html");
  let l = await log();
  check("no-actions view: handshake + size", l.initialized === 1 && l.sizes.length >= 1, l);
  await page.evaluate(() =>
    window.bridge.sendToolResult({ content: [], structuredContent: { viewspecBindings: { c1_value: "99" } } }),
  );
  await frame.locator('[data-binding-id="c1_value"]', { hasText: "99" }).waitFor({ timeout: 3000 });
  check("no-actions view: rebind works", true);
  await page.evaluate((r) => window.bridge.sendToolResult(r), renders.first_action);
  await frame.locator('[data-action-id="pick"]').click();
  await page.waitForFunction(() => window.hostLog.context.length === 1, null, { timeout: 3000 });
  l = await log();
  check("no-actions view: a render's first action works", Array.isArray(l.context[0]?.content), l.context);
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
