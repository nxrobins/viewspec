import { buildStaticCompileResult, normalizeHints, DEFAULT_HINTS } from './landing-payload.js'
import { renderAst } from './landing-emitter.js'

const HINTS = normalizeHints({ ...DEFAULT_HINTS, viewport: 'tablet' })

let derivationOn = true

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

// The hosted fixture matches what the live API returns for a successful
// hosted-tier compile. Rendering it directly keeps the toggle instant, the
// IR ids stable for tooltip lookup, and avoids one extra API call per page
// load. The "reference" fixture is what the V1 reference compiler emits —
// no derivations, base tokens only.
function renderState() {
  const output = byId('derivation-output')
  if (!output) return
  syncToggle()

  try {
    if (derivationOn) {
      const fixture = buildStaticCompileResult(HINTS, { mode: 'hosted' })
      renderAst(fixture.ast, output)
      attachTooltips(output, fixture.derivations || [])
      const count = (fixture.derivations || []).length
      setStatus(`${count} derivations applied`, 'live')
    } else {
      const fixture = buildStaticCompileResult(HINTS, { mode: 'reference' })
      renderAst(fixture.ast, output)
      setStatus('reference mode (no derivations)', 'static')
    }
  } catch (error) {
    output.replaceChildren()
    const note = document.createElement('p')
    note.className = 'muted-copy'
    note.textContent = 'Unable to render derivation preview.'
    output.appendChild(note)
    setStatus('render failed', 'static')
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
