// Drive the served Review frames in the pinned Chromium and record the context causes they post.
// Each surface gets its own harness page, served from the frames' origin like the Studio page, so
// history.back() only ever traverses that one frame's entries.
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';

const input = JSON.parse(readFileSync(0, 'utf8'));
const {chromium} = await import(pathToFileURL(input.playwright).href);

// The local Studio frame SDK and the Private Studio review frame client post the same contexts
// under different message names. The hosted frames load in the sandbox the review page gives them.
const protocols = {
  local: {
    names: {ready: 'viewspec-review-ready', context: 'viewspec-review-context', cause: 'sync_cause'},
    sandbox: '',
    restore: route => ({type: 'viewspec-review-restore', nonce: input.nonce, route, scroll_x: 0, scroll_y: 0}),
  },
  hosted: {
    names: {ready: 'viewspec-hosted-ready', context: 'viewspec-hosted-context', cause: 'cause'},
    sandbox: ' sandbox="allow-scripts allow-forms"',
    restore: route => ({type: 'viewspec-hosted-restore', channel: input.channel, route}),
  },
};
const protocol = protocols[input.protocol];
if (!protocol) throw new Error(`unknown protocol ${input.protocol}`);

const harness = frameUrl => `<!doctype html><html><head><meta charset="utf-8"></head><body>
<script>
const names = ${JSON.stringify(protocol.names)};
window.__contexts = [];
window.__ready = false;
window.__flushed = 0;
addEventListener('message', (event) => {
  if (event.source !== document.getElementById('frame')?.contentWindow || !event.data) return;
  if (event.data.type === names.ready) window.__ready = true;
  if (event.data.type === names.context) window.__contexts.push({cause: event.data[names.cause], route: event.data.route});
  if (event.data.type === 'route-sync-flush') window.__flushed = event.data.id;
});
</script>
<iframe id="frame"${protocol.sandbox} src="${frameUrl.replaceAll('&', '&amp;')}" width="390" height="844"></iframe>
</body></html>`;

const observeSurface = async (browser, name, frameUrl) => {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  // Exact responses the caller derived through the server under test, served at their own paths.
  for (const [path, response] of Object.entries(input.responses || {})) {
    await page.route(new URL(path, input.origin).href, route => route.fulfill({
      status: response.status,
      headers: response.headers,
      body: Buffer.from(response.body, 'base64'),
    }));
  }
  const harnessUrl = new URL(`/__route-sync-harness/${name}`, input.origin).href;
  await page.route(harnessUrl, route => route.fulfill({status: 200, contentType: 'text/html', body: harness(frameUrl)}));
  await page.goto(harnessUrl);
  await page.waitForFunction(() => window.__ready === true);
  const frame = await (await page.$('#frame')).contentFrame();
  let flushId = 0;
  // Run one action and return every context the frame posted for it. The step ends once the frame
  // has posted the context a correct client posts last (any route when `last.route` is null), or a
  // bounded wait for it has expired, which the caller's assertions then report, and then a marker
  // two animation frames later: messages from one window arrive in order, so nothing the frame
  // posted for this step can still be in flight once the marker has arrived.
  const step = async (action, last) => {
    const start = await page.evaluate(() => window.__contexts.length);
    await action();
    await page.waitForFunction(
      ([from, cause, route]) => window.__contexts.slice(from).some(item => item.cause === cause && (route === null || item.route === route)),
      [start, last.cause, last.route],
      {timeout: 10_000},
    ).catch(() => {});
    const id = ++flushId;
    await frame.evaluate(marker => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => {
      parent.postMessage({type: 'route-sync-flush', id: marker}, '*');
      resolve();
    }))), id);
    await page.waitForFunction(marker => window.__flushed === marker, id);
    return page.evaluate(from => window.__contexts.slice(from), start);
  };
  // Studio restores the frame to the route the other surface navigated to, exactly as its page does.
  const restore = route => () => page.evaluate(message => document.getElementById('frame').contentWindow.postMessage(message, '*'), protocol.restore(route));
  const click = label => () => frame.getByRole('button', {name: label, exact: true}).click();
  const back = () => frame.evaluate(() => history.back());
  const result = {
    // The local SDK's last restore context names the restored route. The hosted client's names the
    // route its frame shows at the next animation frame, which the app may not have rendered yet.
    restore: await step(restore('/incident'), {cause: 'restore', route: input.protocol === 'local' ? '/incident' : null}),
    // The reviewer uses the app's own navigation, then the browser's back button.
    navigation: await step(click('Queue'), {cause: 'navigation', route: '/'}),
    back: await step(back, {cause: 'navigation', route: '/incident'}),
    errors,
  };
  await page.close();
  return result;
};

const browser = await chromium.launch({headless: true});
try {
  const observed = {};
  for (const [name, frameUrl] of Object.entries(input.frames)) observed[name] = await observeSurface(browser, name, frameUrl);
  process.stdout.write(JSON.stringify(observed));
} finally {
  await browser.close();
}
