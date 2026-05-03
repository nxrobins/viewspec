import { compileBundle } from './landing-compile.js'
import {
  buildIntentBundle,
  buildStaticCompileResult,
  normalizeHints,
  DEFAULT_HINTS,
} from './landing-payload.js'
import { renderAst } from './landing-emitter.js'

const HINTS = normalizeHints({ ...DEFAULT_HINTS, viewport: 'tablet' })

let derivationOn = true
let abortController = null

function byId(id) {
  return document.getElementById(id)
}

function setStatus(message, state = 'idle') {
  const el = byId('derivation-status')
  if (!el) return
  el.textContent = message
  el.dataset.state = state
}

function syncToggle() {
  document.querySelectorAll('[data-derivation-toggle]').forEach((button) => {
    const value = button.getAttribute('data-derivation-toggle')
    const active = (value === 'on') === derivationOn
    button.setAttribute('aria-pressed', active ? 'true' : 'false')
    button.dataset.active = active ? 'true' : 'false'
  })
}

function attachTooltips(container, derivations) {
  if (!container || !Array.isArray(derivations)) return
  derivations.forEach((derivation) => {
    const targetId = derivation?.target_ir_id
    if (!targetId) return
    const escaped = window.CSS?.escape ? CSS.escape(targetId) : String(targetId).replace(/"/g, '\\"')
    const el = container.querySelector(`[data-ir-id="${escaped}"]`)
    if (!el) return
    const reason = derivation.reason || derivation.trigger || derivation.token
    el.title = `${derivation.token} — ${reason}`
    el.dataset.derived = 'true'
  })
}

async function renderState() {
  const output = byId('derivation-output')
  if (!output) return
  syncToggle()

  if (abortController) abortController.abort()
  abortController = new AbortController()

  if (!derivationOn) {
    const fixture = buildStaticCompileResult(HINTS, { mode: 'reference' })
    renderAst(fixture.ast, output)
    setStatus('reference mode (no derivations)', 'static')
    return
  }

  setStatus('compiling with derivations...', 'loading')
  try {
    const payload = buildIntentBundle(HINTS)
    const result = await compileBundle(payload, { signal: abortController.signal })
    if (abortController.signal.aborted) return
    renderAst(result.data.ast, output)
    attachTooltips(output, result.data.derivations || [])
    const count = (result.data.derivations || []).length
    const compileMs = Number(result.data?.meta?.compile_ms || result.roundTripMs || 0)
    setStatus(`${count} derivations applied in ${compileMs.toFixed(1)}ms`, 'live')
  } catch (error) {
    if (error?.name === 'AbortError') return
    // Live compile failed — fall back to the static hosted fixture so the
    // capability still demonstrates what derivations look like.
    const fixture = buildStaticCompileResult(HINTS, { mode: 'hosted' })
    renderAst(fixture.ast, output)
    attachTooltips(output, fixture.derivations || [])
    setStatus(`offline fixture (${error.message})`, 'static')
  }
}

function installToggle() {
  document.querySelectorAll('[data-derivation-toggle]').forEach((button) => {
    button.addEventListener('click', () => {
      const next = button.getAttribute('data-derivation-toggle') === 'on'
      if (next === derivationOn) return
      derivationOn = next
      renderState()
    })
  })
}

export function initLandingDerivation() {
  if (!byId('capabilities')) return
  installToggle()
  renderState()
}
