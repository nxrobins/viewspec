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
      '---',
      'name: Atlas',
      'colors:',
      '  primary: "#0EA5E9"',
      '  secondary: "#334155"',
      '  accent: "#2DD4BF"',
      '  neutral: "#E0F2FE"',
      'typography:',
      '  fontFamily: Inter, ui-sans-serif, system-ui, sans-serif',
      '  scale: 1.15',
      'rounded:',
      '  sm: 4px',
      '  md: 8px',
      '---',
    ].join('\n'),
  },
  'warm-editorial': {
    label: 'Warm / editorial',
    description: 'Cream paper, terracotta accent, serif headlines.',
    content: [
      '---',
      'name: Foundry',
      'colors:',
      '  primary: "#B91C1C"',
      '  secondary: "#78716C"',
      '  accent: "#EA580C"',
      '  neutral: "#FEF3C7"',
      'typography:',
      '  fontFamily: Georgia, serif',
      '  scale: 1.2',
      'rounded:',
      '  sm: 2px',
      '  md: 3px',
      '---',
    ].join('\n'),
  },
  'minimal-data-dense': {
    label: 'Minimal / data-dense',
    description: 'Stark white, hairline rules, monospace numerals.',
    content: [
      '---',
      'name: Telemetry',
      'colors:',
      '  primary: "#18181B"',
      '  secondary: "#A1A1AA"',
      '  accent: "#3F3F46"',
      '  neutral: "#F4F4F5"',
      'typography:',
      '  fontFamily: "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif',
      '  scale: 1.0',
      'rounded:',
      '  sm: 0px',
      '  md: 0px',
      '---',
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
