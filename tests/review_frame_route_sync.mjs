// Drive the served Review frames in the pinned Chromium and record the context causes they post.
// Each surface gets its own harness page, served from the Review origin like the Studio page, so
// history.back() only ever traverses that one frame's entries.
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';

const input = JSON.parse(readFileSync(0, 'utf8'));
const {chromium} = await import(pathToFileURL(input.playwright).href);

const harness = frameUrl => `<!doctype html><html><head><meta charset="utf-8"></head><body>
<script>
window.__contexts = [];
window.__ready = false;
window.__flushed = 0;
addEventListener('message', (event) => {
  if (event.source !== document.getElementById('frame')?.contentWindow || !event.data) return;
  if (event.data.type === 'viewspec-review-ready') window.__ready = true;
  if (event.data.type === 'viewspec-review-context') window.__contexts.push({cause: event.data.sync_cause, route: event.data.route});
  if (event.data.type === 'route-sync-flush') window.__flushed = event.data.id;
});
</script>
<iframe id="frame" src="${frameUrl}" width="390" height="844"></iframe>
</body></html>`;

const observeSurface = async (browser, name, frameUrl) => {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const harnessUrl = new URL(`/__route-sync-harness/${name}`, input.origin).href;
  await page.route(harnessUrl, route => route.fulfill({status: 200, contentType: 'text/html', body: harness(frameUrl)}));
  await page.goto(harnessUrl);
  await page.waitForFunction(() => window.__ready === true);
  const frame = await (await page.$('#frame')).contentFrame();
  let flushId = 0;
  // Run one action and return every context the frame posted for it. The step ends once the frame
  // has posted the context a correct SDK posts last (or a bounded wait for it has expired, which
  // the caller's assertions then report), and then a marker two animation frames later: messages
  // from one window arrive in order, so nothing the frame posted for this step can still be in
  // flight once the marker has arrived.
  const step = async (action, last) => {
    const start = await page.evaluate(() => window.__contexts.length);
    await action();
    await page.waitForFunction(
      ([from, cause, route]) => window.__contexts.slice(from).some(item => item.cause === cause && item.route === route),
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
  const restore = route => () => page.evaluate(([to, nonce]) => document.getElementById('frame').contentWindow.postMessage(
    {type: 'viewspec-review-restore', nonce, route: to, scroll_x: 0, scroll_y: 0}, '*'), [route, input.nonce]);
  const click = label => () => frame.getByRole('button', {name: label, exact: true}).click();
  const back = () => frame.evaluate(() => history.back());
  const result = {
    restore: await step(restore('/incident'), {cause: 'restore', route: '/incident'}),
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
