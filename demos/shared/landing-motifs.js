import { compileBundle } from './landing-compile.js'
import { LANDING_CONFIG, hasLiveApiConfig } from './landing-config.js'
import {
  buildIntentBundle,
  buildStaticCompileResult,
  normalizeHints,
  DEFAULT_HINTS,
  MOTIF_KINDS,
} from './landing-payload.js'
import { renderAst } from './landing-emitter.js'

const KIND_COPY = {
  dashboard: {
    label: 'Dashboard',
    hint: 'Dashboard cards. Focal value gets the largest scale, badge sits beneath, focal card lifted.',
  },
  table: {
    label: 'Table',
    hint: 'Same bindings, table rows. Label / value / trend become columns; emphasis travels.',
  },
  comparison: {
    label: 'Comparison',
    hint: 'Side-by-side comparison. Spans, contrasts, and ranking emerge from the data shape.',
  },
}

const MOTIF_HINTS = normalizeHints({ ...DEFAULT_HINTS, viewport: 'tablet' })

let activeKind = 'dashboard'
let abortController = null

function byId(id) {
  return document.getElementById(id)
}

function setStatus(message, state = 'idle') {
  const el = byId('motifs-status')
  if (!el) return
  el.textContent = message
  el.dataset.state = state
}

function setHint(kind) {
  const el = byId('motifs-hint')
  if (!el) return
  el.textContent = KIND_COPY[kind]?.hint || ''
}

function syncButtons(kind) {
  document.querySelectorAll('[data-motif-kind]').forEach((button) => {
    const active = button.getAttribute('data-motif-kind') === kind
    button.setAttribute('aria-pressed', active ? 'true' : 'false')
    button.dataset.active = active ? 'true' : 'false'
  })
}

function safeRenderAst(ast, output) {
  try {
    renderAst(ast, output)
    return true
  } catch (error) {
    return false
  }
}

function renderFixturePlaceholder(output) {
  const fixture = buildStaticCompileResult(MOTIF_HINTS)
  safeRenderAst(fixture.ast, output)
}

async function applyKind(kind) {
  if (!MOTIF_KINDS.includes(kind)) return
  activeKind = kind
  syncButtons(kind)
  setHint(kind)

  const output = byId('motifs-output')
  if (!output) return

  if (abortController) abortController.abort()
  abortController = new AbortController()

  if (!hasLiveApiConfig()) {
    renderFixturePlaceholder(output)
    setStatus('static fixture (no API configured)', 'static')
    return
  }

  setStatus(`compiling ${KIND_COPY[kind].label.toLowerCase()}`, 'loading')

  try {
    const payload = buildIntentBundle(MOTIF_HINTS, { motifKind: kind })
    const result = await compileBundle(payload, { signal: abortController.signal })
    if (abortController.signal.aborted) return
    const rendered = safeRenderAst(result.data.ast, output)
    if (!rendered) {
      renderFixturePlaceholder(output)
      setStatus('unexpected response shape; showing fixture', 'static')
      return
    }
    const compileMs = Number(result.data?.meta?.compile_ms || result.roundTripMs || 0)
    setStatus(`${KIND_COPY[kind].label} compiled in ${compileMs.toFixed(1)}ms via ${LANDING_CONFIG.apiUrl}`, 'live')
  } catch (error) {
    if (error?.name === 'AbortError') return
    renderFixturePlaceholder(output)
    setStatus(`offline fixture (${error.message})`, 'static')
  }
}

function installKindButtons() {
  document.querySelectorAll('[data-motif-kind]').forEach((button) => {
    button.addEventListener('click', () => {
      const kind = button.getAttribute('data-motif-kind')
      if (kind === activeKind) return
      applyKind(kind)
    })
  })
}

export function initLandingMotifs() {
  if (!byId('capabilities')) return
  installKindButtons()
  const output = byId('motifs-output')
  if (output) renderFixturePlaceholder(output)
  syncButtons(activeKind)
  setHint(activeKind)
  applyKind(activeKind)
}
