import { compileBundle } from './landing-compile.js'
import { LANDING_CONFIG, hasLiveApiConfig } from './landing-config.js'
import {
  buildIntentBundle,
  buildStaticCompileResult,
  normalizeHints,
  DEFAULT_HINTS,
} from './landing-payload.js'
import { renderAst } from './landing-emitter.js'

// DESIGN.md presets use the canonical YAML frontmatter format the API parses.
// Strict ingestion: colors must be exact 6-char #RRGGBB hex; spacing/rounded
// must be px/rem/em dimensions; font weights must be numeric. The Phase 1
// expansion adds colors.{background,surface,text,warning,positive},
// typography.heading + typography.body, spacing.card, and rounded.lg so a
// brand designer can paste a real DESIGN.md and have most of it land.
const PRESETS = {
  'dark-corporate': {
    label: 'Dark / corporate',
    description: 'Slate background, sky-blue primary, teal accent, sharp Inter headlines.',
    content: [
      '---',
      'name: Atlas',
      'mode: dark',
      'colors:',
      '  primary: "#0EA5E9"',
      '  secondary: "#475569"',
      '  accent: "#2DD4BF"',
      '  background: "#0F172A"',
      '  surface: "#1E293B"',
      '  text: "#F1F5F9"',
      '  warning: "#F97316"',
      '  positive: "#22D3EE"',
      'typography:',
      '  heading:',
      '    fontFamily: Inter, ui-sans-serif, system-ui, sans-serif',
      '    fontWeight: 800',
      '    letterSpacing: -0.03em',
      '  body:',
      '    fontFamily: Inter, ui-sans-serif, system-ui, sans-serif',
      'spacing:',
      '  card: 1.25rem',
      'rounded:',
      '  sm: 6px',
      '  md: 10px',
      '  lg: 14px',
      '---',
      '',
      '## Overview',
      'Modern enterprise telemetry. High-contrast surfaces, generous rounding.',
    ].join('\n'),
  },
  'warm-editorial': {
    label: 'Warm / editorial',
    description: 'Cream paper, deep red primary, terracotta accent, Georgia serif throughout.',
    content: [
      '---',
      'name: Foundry',
      'mode: light',
      'colors:',
      '  primary: "#B91C1C"',
      '  secondary: "#78716C"',
      '  accent: "#EA580C"',
      '  background: "#FBF6EE"',
      '  surface: "#FFFFFF"',
      '  text: "#2A1B10"',
      '  warning: "#C2410C"',
      '  positive: "#4D7C0F"',
      'typography:',
      '  heading:',
      '    fontFamily: Georgia, "Times New Roman", serif',
      '    fontWeight: 700',
      '    letterSpacing: 0.01em',
      '  body:',
      '    fontFamily: Georgia, "Times New Roman", serif',
      'spacing:',
      '  card: 1.5rem',
      'rounded:',
      '  sm: 2px',
      '  md: 4px',
      '  lg: 6px',
      '---',
      '',
      '## Overview',
      'Editorial gravitas for journalistic instruments. Minimal rounding, generous breathing room.',
    ].join('\n'),
  },
  'minimal-data-dense': {
    label: 'Minimal / data-dense',
    description: 'Stark white, near-black everything, IBM Plex Sans, zero rounding.',
    content: [
      '---',
      'name: Telemetry',
      'mode: light',
      'colors:',
      '  primary: "#18181B"',
      '  secondary: "#A1A1AA"',
      '  accent: "#3F3F46"',
      '  background: "#FFFFFF"',
      '  surface: "#FAFAFA"',
      '  text: "#09090B"',
      '  warning: "#B91C1C"',
      '  positive: "#047857"',
      'typography:',
      '  heading:',
      '    fontFamily: '"IBM Plex Sans", ui-sans-serif, system-ui, sans-serif'',
      '    fontWeight: 600',
      '    letterSpacing: 0em',
      '  body:',
      '    fontFamily: '"IBM Plex Sans", ui-sans-serif, system-ui, sans-serif'',
      'spacing:',
      '  card: 0.75rem',
      'rounded:',
      '  sm: 0px',
      '  md: 0px',
      '  lg: 0px',
      '---',
      '',
      '## Overview',
      'Stark scientific data instrument. Dense spacing, hairline rules.',
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
