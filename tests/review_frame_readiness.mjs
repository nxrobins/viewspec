// Deterministic clocks exercise real emitted clients; no sleeps or network.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const input = JSON.parse(readFileSync(0, 'utf8'));
const hosted = input.surface === 'hosted';
const messages = [], listeners = new Map(), observers = new Set(), frames = new Map(), timers = new Map();
let sequence = 0, screenRoute = null, duplicate = false, clicks = 0, payload = 'record-1';
const screen = {dataset: {viewspecAppScreen: 'queue', routePath: '/queue'}};
const visible = () => screenRoute === null ? [] : [screen, ...(duplicate ? [screen] : [])];
const binding = {textContent: payload};
const action = {click() { clicks++; }};
const select = selector => {
  if (selector.includes('data-viewspec-app-screen')) return visible();
  if (selector.includes('data-binding-id')) return screenRoute === '/queue' ? [{...binding, textContent: payload}] : [];
  if (selector.includes('data-action-id')) return screenRoute === '/queue' ? [action] : [];
  return [];
};
screen.querySelectorAll = select;
screen.querySelector = selector => select(selector)[0] || null;
const fire = (name, event = {}) => { for (const callback of listeners.get(name) || []) callback(event); };
const parent = {postMessage: value => messages.push(value)};
const location = {pathname: '/queue', hash: '', reload() {}};
const document = {
  currentScript: {dataset: {surface: 'react-tailwind-app'}}, readyState: 'loading',
  documentElement: {dataset: {}},
  querySelectorAll: select, querySelector: selector => select(selector)[0] || null,
};
const sandbox = {
  document, parent, location, console, CSS: {escape: value => value},
  __viewspecInitialPath: '/queue', __viewspecHostedReviewTransportV1: {channel: 'test-channel'},
  innerWidth: 390, innerHeight: 844, scrollX: 0, scrollY: 0,
  history: {pushState(_a, _b, path) {location.pathname = path;}, replaceState(_a, _b, path) {location.pathname = path;}},
  addEventListener(name, callback) {listeners.set(name, [...(listeners.get(name) || []), callback]);},
  dispatchEvent(event) {fire(event.type, event);},
  requestAnimationFrame(callback) {const id = ++sequence; frames.set(id, callback); return id;},
  cancelAnimationFrame(id) {frames.delete(id);},
  setTimeout(callback, delay) {const id = ++sequence; timers.set(id, {callback, delay}); return id;},
  clearTimeout(id) {timers.delete(id);},
  CustomEvent: class {constructor(type, options = {}) {this.type = type; this.detail = options.detail;}},
  PopStateEvent: class {constructor(type) {this.type = type;}},
  MutationObserver: class {
    constructor(callback) {this.callback = callback;}
    observe() {observers.add(this);}
    disconnect() {observers.delete(this);}
  },
};
sandbox.window = sandbox;
vm.runInNewContext(input.script, sandbox);
const tick = async (count = 4) => {
  for (let i = 0; i < count; i++) {
    const pending = [...frames.values()]; frames.clear();
    pending.forEach(callback => callback());
    await Promise.resolve(); await Promise.resolve();
  }
};
const render = (route, multiple = false) => {
  screenRoute = route; duplicate = multiple;
  screen.dataset.routePath = route; screen.dataset.viewspecAppScreen = route === '/queue' ? 'queue' : 'detail';
  for (const observer of [...observers]) observer.callback([]);
};
const readyType = hosted ? 'viewspec-hosted-ready' : 'viewspec-review-ready';
const failedType = hosted ? 'viewspec-hosted-render-failed' : 'viewspec-review-render-failed';
const resultType = hosted ? 'viewspec-hosted-replay-result' : 'viewspec-studio-replay-result';
const ready = () => messages.filter(message => message.type === readyType);
const results = () => messages.filter(message => message.type === resultType);
document.readyState = 'complete'; fire('DOMContentLoaded'); fire('load');

if (['delayed_initial', 'wrong_initial_route', 'duplicate_screen', 'timeout_terminal'].includes(input.case)) {
  if (input.case === 'wrong_initial_route') render('/detail');
  if (input.case === 'duplicate_screen') render('/queue', true);
  await tick();
  assert.equal(ready().length, 0, 'readiness must wait for one exact initial screen');
  if (input.case === 'timeout_terminal') {
    for (const [id, timer] of [...timers]) if (timer.delay === 4000) {timers.delete(id); timer.callback();}
    await tick();
    assert.equal(messages.filter(message => message.type === failedType).length, 1);
    render('/queue'); await tick();
    assert.equal(ready().length, 0, 'late rendering must not reverse a failed handshake');
    assert.equal(observers.size, 0); assert.equal(frames.size, 0);
  } else {
    render('/queue'); await tick();
    assert.equal(ready().length, 1);
    render('/queue'); fire('load'); await tick();
    assert.equal(ready().length, 1, 'ready must be announced exactly once');
    assert.equal(observers.size, 0); assert.equal(timers.size, 0);
  }
} else {
  render('/queue'); await tick(); assert.equal(ready().length, 1);
  render('/detail'); await tick();
  if (input.case === 'payload_rejected') payload = 'wrong-record';
  fire('message', {source: parent, data: {
    channel: 'test-channel', nonce: 'test-nonce',
    type: hosted ? 'viewspec-hosted-replay' : 'viewspec-studio-replay-apply', evidence_ref: 'test-checkpoint',
    events: [{route: '/queue', screen_id: 'queue', action_id: 'review', payload_values: {record: 'record-1'}}],
  }});
  await tick();
  assert.equal(results().length, 0, 'replay must wait for the requested route to commit');
  assert.equal(clicks, 0);
  if (input.case === 'replay_timeout') {
    for (const [id, timer] of [...timers]) if (timer.delay === 4000) {timers.delete(id); timer.callback();}
    await tick();
    assert.equal(results().length, 1); assert.equal(results()[0].ok, false);
  }
  render('/queue'); await tick(8);
  assert.equal(results().length, 1);
  assert.equal(results()[0].ok, !['payload_rejected', 'replay_timeout'].includes(input.case));
  assert.equal(clicks, ['payload_rejected', 'replay_timeout'].includes(input.case) ? 0 : 1);
  assert.equal(observers.size, 0); assert.equal(timers.size, 0);
}
