// CSS-variable preset swap. The IR is rendered once; flipping the
// `data-style-preset` attribute on the wrapper redefines a small set of CSS
// variables that the cards read. Same DOM, completely different look —
// instant, no network, no compile.
//
// Mirrors the per-page demo at /style-derivation/. Kept inline here so the
// landing card stays self-contained and the presets stay in lock-step.

const STYLE_PRESETS = {
  default: {
    label: 'Default',
    feel: 'Clean, neutral, the reference compiler baseline.',
  },
  editorial: {
    label: 'Editorial',
    feel: 'Magazine-dense, high contrast, tight spacing.',
  },
  'data-dense': {
    label: 'Data-Dense',
    feel: 'Bloomberg terminal energy, maximum information.',
  },
  soft: {
    label: 'Soft',
    feel: 'Consumer app, lots of air, rounded everything.',
  },
}

const KPIS = [
  { id: 'kpi_revenue', label: 'Revenue', value: '$2.4M' },
  { id: 'kpi_users', label: 'Active Users', value: '18,472' },
  { id: 'kpi_conversion', label: 'Conversion', value: '3.8%' },
  { id: 'kpi_churn', label: 'Churn', value: '1.2%' },
]

function byId(id) {
  return document.getElementById(id)
}

function setStatus(message, state = 'idle') {
  const el = byId('style-derivation-status')
  if (!el) return
  el.textContent = message
  el.dataset.state = state
}

function setHint(key) {
  const el = byId('style-derivation-hint')
  if (!el) return
  el.textContent = STYLE_PRESETS[key]?.feel || ''
}

function syncButtons(key) {
  document.querySelectorAll('[data-style-preset]').forEach((button) => {
    if (button.id === 'style-derivation-stage') return
    const active = button.getAttribute('data-style-preset') === key
    button.setAttribute('aria-pressed', active ? 'true' : 'false')
    button.dataset.active = active ? 'true' : 'false'
  })
}

function renderArtifact() {
  const stage = byId('style-derivation-stage')
  if (!stage) return
  // Bake the IR-style markup with stable data-ir-id attributes so the page-
  // level CSS rules (selectors like `[data-ir-id="motif_kpis"]`,
  // `[data-ir-id^="motif_kpis_kpi_"]`, `[data-ir-id$="_label"]`,
  // `[data-ir-id$="_value"]`) target the right nodes. The selectors mirror
  // the structure the compiler emits at tier 6 — same shape, different
  // origin (this is a static fixture, not a live AST).
  const cards = KPIS.map((kpi) => `
    <div data-ir-id="motif_kpis_${kpi.id}">
      <div data-ir-id="binding_${kpi.id}_label">${kpi.label}</div>
      <div data-ir-id="binding_${kpi.id}_value">${kpi.value}</div>
    </div>
  `).join('')
  stage.innerHTML = `
    <main data-ir-id="region_main">
      <div data-ir-id="motif_kpis">${cards}</div>
    </main>
  `
}

function activatePreset(key) {
  if (!STYLE_PRESETS[key]) return
  const stage = byId('style-derivation-stage')
  if (!stage) return
  stage.dataset.stylePreset = key
  syncButtons(key)
  setHint(key)
  setStatus(`${STYLE_PRESETS[key].label} preset`, 'live')
}

function installPresetButtons() {
  document.querySelectorAll('button[data-style-preset]').forEach((button) => {
    button.addEventListener('click', () => {
      activatePreset(button.getAttribute('data-style-preset'))
    })
  })
}

export function initLandingStyleDerivation() {
  if (!byId('capabilities')) return
  if (!byId('style-derivation-stage')) return
  renderArtifact()
  installPresetButtons()
  activatePreset('default')
}
