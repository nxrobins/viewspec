import { LANDING_CONFIG } from './landing-config.js'
import { compileBundle } from './landing-compile.js'

const ARTIFACT_BASE = './cross-platform-dashboard/artifacts'

const SOURCES = {
  intent: `${ARTIFACT_BASE}/intent_bundle.json`,
  html: `${ARTIFACT_BASE}/html/index.html`,
  'react-tsx': `${ARTIFACT_BASE}/react-tsx/ViewSpecView.tsx`,
  swiftui: `${ARTIFACT_BASE}/swiftui/ViewSpecView.swift`,
  flutter: `${ARTIFACT_BASE}/flutter/viewspec_view.dart`,
}

let cachedIntentBundle = null
const sourceCache = new Map()

function byId(id) {
  return document.getElementById(id)
}

async function fetchText(url) {
  if (sourceCache.has(url)) return sourceCache.get(url)
  const response = await fetch(url)
  if (!response.ok) throw new Error(`Failed to load ${url} (HTTP ${response.status})`)
  const text = await response.text()
  sourceCache.set(url, text)
  return text
}

async function fetchIntentBundle() {
  if (cachedIntentBundle) return cachedIntentBundle
  const text = await fetchText(SOURCES.intent)
  cachedIntentBundle = JSON.parse(text)
  return cachedIntentBundle
}

function setStatus(message, state = 'idle') {
  const el = byId('proof-status')
  if (!el) return
  el.textContent = message
  el.dataset.state = state
}

function setTimingBadge(ms) {
  const badge = byId('proof-compile-time')
  if (!badge) return
  if (ms == null) {
    badge.textContent = 'pre-baked'
    badge.dataset.state = 'static'
  } else {
    badge.textContent = `${Number(ms).toFixed(1)}ms live`
    badge.dataset.state = 'live'
  }
}

async function renderJsonPanel() {
  const target = byId('proof-json')
  if (!target) return
  try {
    const bundle = await fetchIntentBundle()
    target.textContent = JSON.stringify(bundle, null, 2)
  } catch (error) {
    target.textContent = `// Unable to load intent bundle: ${error.message}`
  }
}

function activateCodeTab(name) {
  document.querySelectorAll('[data-proof-tab]').forEach((button) => {
    const active = button.getAttribute('data-proof-tab') === name
    button.setAttribute('aria-pressed', active ? 'true' : 'false')
    button.dataset.active = active ? 'true' : 'false'
  })
  document.querySelectorAll('[data-proof-panel]').forEach((panel) => {
    const active = panel.getAttribute('data-proof-panel') === name
    panel.hidden = !active
  })
}

async function loadCodePanel(name) {
  const url = SOURCES[name]
  const target = document.querySelector(`[data-proof-panel="${name}"] code`)
  if (!url || !target) return
  if (target.dataset.loaded === 'true') return
  target.textContent = 'Loading source...'
  try {
    const text = await fetchText(url)
    target.textContent = text
    target.dataset.loaded = 'true'
  } catch (error) {
    target.textContent = `// Unable to load source: ${error.message}`
  }
}

function installTabHandlers() {
  document.querySelectorAll('[data-proof-tab]').forEach((button) => {
    button.addEventListener('click', () => {
      const name = button.getAttribute('data-proof-tab')
      activateCodeTab(name)
      loadCodePanel(name)
    })
  })
}

async function recompileHtml(button) {
  const frame = byId('proof-html-frame')
  if (!frame) return
  const originalLabel = button.textContent
  button.disabled = true
  button.textContent = 'compiling...'
  setStatus('compiling', 'loading')

  try {
    const bundle = await fetchIntentBundle()
    const result = await compileBundle(bundle)
    const html = result.data?.ast?.emitters?.html
      || result.data?.ast?.html
      || result.data?.html
    if (typeof html === 'string' && html.length > 0) {
      frame.srcdoc = html
    } else {
      // The hosted endpoint may not echo the HTML emitter output for every request;
      // in that case keep the iframe pointed at the pre-baked artifact and just
      // confirm the live compile round-tripped successfully.
      frame.src = `${SOURCES.html}?live=${Date.now()}`
    }
    const compileMs = Number(result.data?.meta?.compile_ms || result.roundTripMs || 0)
    setTimingBadge(compileMs)
    setStatus(`compiled via ${result.apiUrl}`, 'live')
  } catch (error) {
    setStatus(error?.message || 'compile failed', 'static')
  } finally {
    button.disabled = false
    button.textContent = originalLabel
  }
}

function installCompileButton() {
  const button = byId('proof-compile-button')
  if (!button) return
  button.addEventListener('click', () => recompileHtml(button))
}

export function initLandingProof() {
  if (!byId('proof')) return
  renderJsonPanel()
  // The HTML iframe loads from its src attribute on first paint.
  // React TSX shown by default — fetch lazily on demand.
  activateCodeTab('react-tsx')
  loadCodePanel('react-tsx')
  installTabHandlers()
  installCompileButton()
  setTimingBadge(null)
  setStatus(`pre-baked from ${LANDING_CONFIG.apiUrl}`, 'static')
}
