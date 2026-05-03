import { compileBundle } from './landing-compile.js'
import { buildIntentBundle, normalizeHints, DEFAULT_HINTS } from './landing-payload.js'
import { renderAst } from './landing-emitter.js'

const PRESETS = {
  'dark-corporate': {
    label: 'Dark / corporate',
    description: 'High-contrast surfaces, blue-teal accents, geometric sans.',
    content: [
      'name: Atlas',
      'mode: dark',
      'color.background: #0B1220',
      'color.surface: #131C2E',
      'color.text: #F1F5F9',
      'color.muted: #94A3B8',
      'color.primary: #38BDF8',
      'color.accent: #2DD4BF',
      'color.warning: #F97316',
      'color.positive: #22D3EE',
      'fontFamily.body: Inter, ui-sans-serif, system-ui, sans-serif',
      'fontFamily.heading: Inter, ui-sans-serif, system-ui, sans-serif',
      'fontFamily.mono: JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, monospace',
      'radius.sm: 4px',
      'radius.md: 6px',
      'radius.lg: 10px',
      'shadow.card: 0 18px 32px rgba(2, 6, 23, 0.45)',
      'density: comfortable',
    ].join('\n'),
  },
  'warm-editorial': {
    label: 'Warm / editorial',
    description: 'Cream paper, terracotta accent, serif headlines.',
    content: [
      'name: Foundry',
      'mode: light',
      'color.background: #FBF6EE',
      'color.surface: #FFFFFF',
      'color.text: #2A1B10',
      'color.muted: #8C7B6A',
      'color.primary: #B4441F',
      'color.accent: #D9803A',
      'color.warning: #C2410C',
      'color.positive: #4D7C0F',
      'fontFamily.body: "Source Serif Pro", Georgia, serif',
      'fontFamily.heading: "Playfair Display", "Source Serif Pro", Georgia, serif',
      'fontFamily.mono: "IBM Plex Mono", ui-monospace, Menlo, monospace',
      'radius.sm: 2px',
      'radius.md: 3px',
      'radius.lg: 4px',
      'shadow.card: 0 1px 0 rgba(42, 27, 16, 0.08), 0 12px 28px rgba(42, 27, 16, 0.07)',
      'density: editorial',
    ].join('\n'),
  },
  'minimal-data-dense': {
    label: 'Minimal / data-dense',
    description: 'Stark white, hairline rules, monospace numerals.',
    content: [
      'name: Telemetry',
      'mode: light',
      'color.background: #FFFFFF',
      'color.surface: #FFFFFF',
      'color.text: #0A0A0A',
      'color.muted: #525252',
      'color.primary: #0A0A0A',
      'color.accent: #1F2937',
      'color.warning: #B91C1C',
      'color.positive: #047857',
      'fontFamily.body: "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif',
      'fontFamily.heading: "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif',
      'fontFamily.mono: "IBM Plex Mono", ui-monospace, SFMono-Regular, monospace',
      'radius.sm: 0px',
      'radius.md: 0px',
      'radius.lg: 0px',
      'shadow.card: 0 0 0 1px #E5E5E5',
      'density: dense',
    ].join('\n'),
  },
}

const DESIGN_HINTS = normalizeHints({ ...DEFAULT_HINTS, density: 'compact', viewport: 'tablet' })

let activePreset = 'dark-corporate'
let abortController = null

function byId(id) {
  return document.getElementById(id)
}

function setStatus(message, state = 'idle') {
  const el = byId('design-status')
  if (!el) return
  el.textContent = message
  el.dataset.state = state
}

function renderSource(name) {
  const target = byId('design-source')
  if (!target) return
  target.textContent = PRESETS[name].content
}

function syncButtons(name) {
  document.querySelectorAll('[data-design-preset]').forEach((button) => {
    const active = button.getAttribute('data-design-preset') === name
    button.setAttribute('aria-pressed', active ? 'true' : 'false')
    button.dataset.active = active ? 'true' : 'false'
  })
}

async function applyPreset(name) {
  if (!PRESETS[name]) return
  activePreset = name
  syncButtons(name)
  renderSource(name)

  const output = byId('design-output')
  if (!output) return

  if (abortController) abortController.abort()
  abortController = new AbortController()

  setStatus(`compiling with ${PRESETS[name].label}`, 'loading')

  try {
    const payload = {
      ...buildIntentBundle(DESIGN_HINTS),
      design: {
        format: 'design.md',
        content: PRESETS[name].content,
        lint: true,
      },
    }
    const result = await compileBundle(payload, { signal: abortController.signal })
    if (abortController.signal.aborted) return
    renderAst(result.data.ast, output)
    const compileMs = Number(result.data?.meta?.compile_ms || result.roundTripMs || 0)
    setStatus(`compiled in ${compileMs.toFixed(1)}ms`, 'live')
  } catch (error) {
    if (error?.name === 'AbortError') return
    output.textContent = ''
    const fallback = document.createElement('p')
    fallback.className = 'muted-copy'
    fallback.textContent = `Live compile unavailable: ${error.message}. Source DESIGN.md still shown.`
    output.appendChild(fallback)
    setStatus('compile failed', 'static')
  }
}

function installPresetButtons() {
  document.querySelectorAll('[data-design-preset]').forEach((button) => {
    button.addEventListener('click', () => {
      const name = button.getAttribute('data-design-preset')
      if (name === activePreset) return
      applyPreset(name)
    })
  })
}

export function initLandingDesign() {
  if (!byId('capabilities')) return
  installPresetButtons()
  applyPreset(activePreset)
}
