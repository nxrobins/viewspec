import { compileBundle } from './landing-compile.js'
import { LANDING_CONFIG, hasLiveApiConfig } from './landing-config.js'
import {
  buildIntentBundle,
  buildStaticCompileResult,
  normalizeHints,
  DEFAULT_HINTS,
} from './landing-payload.js'
import { renderAst } from './landing-emitter.js'

// DESIGN.md presets follow the strict ingestion rules in
// docs/hosted-agent-integration.md: colors must be exact 6-char #RRGGBB hex,
// density must be a documented value (comfortable | dense | compact), and
// rgba()-style shadows are silently ignored by the API so we omit them.
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
      'density: comfortable',
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

function safeRenderAst(ast, output) {
  try {
    renderAst(ast, output)
    return true
  } catch (error) {
    return false
  }
}

function renderFixturePlaceholder(output) {
  // Render the baseline KPI dashboard so the user has something visible
  // while the live compile is in flight. The DESIGN.md tokens override
  // colors when the live response replaces this.
  const fixture = buildStaticCompileResult(DESIGN_HINTS)
  safeRenderAst(fixture.ast, output)
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

  if (!hasLiveApiConfig()) {
    renderFixturePlaceholder(output)
    setStatus('static fixture (no API configured)', 'static')
    return
  }

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
    const rendered = safeRenderAst(result.data.ast, output)
    if (!rendered) {
      renderFixturePlaceholder(output)
      setStatus('unexpected response shape; showing fixture', 'static')
      return
    }
    const compileMs = Number(result.data?.meta?.compile_ms || result.roundTripMs || 0)
    setStatus(`compiled in ${compileMs.toFixed(1)}ms via ${LANDING_CONFIG.apiUrl}`, 'live')
  } catch (error) {
    if (error?.name === 'AbortError') return
    renderFixturePlaceholder(output)
    setStatus(`offline fixture (${error.message})`, 'static')
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
  // Render a placeholder immediately so the panel is never empty during the
  // initial compile.
  const output = byId('design-output')
  if (output) renderFixturePlaceholder(output)
  renderSource(activePreset)
  syncButtons(activePreset)
  applyPreset(activePreset)
}
