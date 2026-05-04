import {
  LANDING_CONFIG,
  hasLiveApiConfig,
  hasProductionCommerceConfig,
} from './landing-config.js'
import {
  DEFAULT_HINTS,
  buildIntentBundle,
  buildStaticCompileResult,
  normalizeHints,
} from './landing-payload.js'
import { countIrNodes, getAstRoot, renderAst } from './landing-emitter.js'
import { compileBundle } from './landing-compile.js'
import { initLandingProof } from './landing-proof.js'
import { initLandingStyleDerivation } from './landing-style-derivation.js'
import { initLandingMotifs } from './landing-motifs.js'

const HERO_HINTS = normalizeHints({ ...DEFAULT_HINTS, viewport: 'desktop' })
const PROVENANCE_HINTS = normalizeHints({ ...DEFAULT_HINTS, viewport: 'tablet' })

let activeHighlight = null
let provenanceDerivations = []

function byId(id) {
  return document.getElementById(id)
}

function setText(id, value) {
  const el = byId(id)
  if (el) el.textContent = String(value)
}

function setStatus(text, state = 'idle') {
  const el = byId('compile-status')
  if (!el) return
  el.textContent = text
  el.dataset.state = state
}

function setHeroBadge(value) {
  setText('hero-compile-time', value)
}

function setHeroIrCount(value) {
  setText('hero-ir-count', value)
}

function setHeroTokenCount(value) {
  setText('hero-token-count', value)
}

function applyViewportFrame(viewport) {
  const widths = { mobile: '340px', tablet: '520px', desktop: '680px' }
  const frame = byId('hero-preview-frame')
  if (!frame) return
  frame.dataset.viewport = viewport
  const shell = frame.querySelector('[data-viewport-shell]')
  if (!shell) return
  shell.dataset.viewport = viewport
  const target = widths[viewport] || widths.desktop
  shell.style.width = `min(${target}, 100%)`
  shell.style.minWidth = `min(${target}, 100%)`
}

function renderHeroPreview(ast) {
  const output = byId('hero-playground-output')
  if (!output) return
  renderAst(ast, output)
}

async function compileHero() {
  applyViewportFrame(HERO_HINTS.viewport)

  // Always render the static fixture for the visual. The hosted compiler can
  // return a different IR shape for the anonymous tier (no derivations, flat
  // structure), and the fixture is what makes the dashboard look like a
  // dashboard. The live call below is purely for the timing badge.
  const fixture = buildStaticCompileResult(HERO_HINTS)
  renderHeroPreview(fixture.ast)
  setHeroBadge('warming up')
  setHeroIrCount(String(countIrNodes(getAstRoot(fixture.ast))))
  setHeroTokenCount(String(Object.keys(fixture.ast.style_values || {}).length))

  if (!hasLiveApiConfig()) {
    setStatus('static fixture', 'static')
    setHeroBadge('reference')
    return
  }

  setStatus('compiling', 'loading')
  try {
    const result = await compileBundle(buildIntentBundle(HERO_HINTS))
    const compileMs = Number(result?.data?.meta?.compile_ms || result?.roundTripMs || 0)
    setHeroBadge(`${compileMs.toFixed(1)}ms`)
    const irCount = result?.data?.meta?.ir_node_count
      || countIrNodes(getAstRoot(result?.data?.ast))
      || countIrNodes(getAstRoot(fixture.ast))
    setHeroIrCount(String(irCount))
    const tokenCount = result?.data?.meta?.style_token_count
      || Object.keys(result?.data?.ast?.style_values || {}).length
      || Object.keys(fixture.ast.style_values || {}).length
    setHeroTokenCount(String(tokenCount))
    setStatus(`${compileMs.toFixed(1)}ms compile`, 'live')
  } catch (error) {
    setStatus('offline fixture', 'static')
    setHeroBadge('offline')
  }
}

function clearHighlight() {
  if (activeHighlight) activeHighlight.classList.remove('provenance-active')
  activeHighlight = null
}

function renderProvenanceDetails(el) {
  const panel = byId('provenance-details')
  if (!panel) return
  panel.replaceChildren()
  if (!el) {
    const copy = document.createElement('p')
    copy.className = 'muted-copy'
    copy.textContent = 'Hover any cell. The full chain appears here.'
    panel.appendChild(copy)
    return
  }

  const refs = {
    ir_id: el.dataset.irId || '',
    style_tokens: safeParse(el.dataset.styleTokens, []),
    content_refs: safeParse(el.dataset.contentRefs, []),
    intent_refs: safeParse(el.dataset.intentRefs, []),
  }
  const matched = provenanceDerivations.filter((derivation) => {
    return derivation.target_ir_id === refs.ir_id || refs.content_refs.includes(derivation.target_content_ref)
  })
  const pre = document.createElement('pre')
  pre.textContent = JSON.stringify({ ...refs, derivations: matched }, null, 2)
  panel.appendChild(pre)
}

function safeParse(raw, fallback) {
  if (!raw) return fallback
  try {
    return JSON.parse(raw)
  } catch {
    return fallback
  }
}

function installHoverInspector() {
  const preview = byId('provenance-output')
  if (!preview) return
  preview.addEventListener('mouseover', (event) => {
    const target = event.target.closest('[data-ir-id]')
    if (!target || !preview.contains(target)) return
    if (target !== activeHighlight) {
      clearHighlight()
      activeHighlight = target
      activeHighlight.classList.add('provenance-active')
    }
    renderProvenanceDetails(target)
  })
  // Use `mouseout` (not `mouseleave`) so we can inspect `relatedTarget` and
  // avoid resetting the hover details on every cell-to-cell pointer movement.
  // Only clear when the cursor actually leaves the preview container.
  preview.addEventListener('mouseout', (event) => {
    const next = event.relatedTarget
    if (next && preview.contains(next)) return
    clearHighlight()
    renderProvenanceDetails(null)
  })
}

function compileProvenance() {
  const output = byId('provenance-output')
  if (!output) return
  // Provenance hover demonstrates the binding chain. Use the static fixture
  // only — the IR ids and content_refs are stable, the derivation reasons
  // are rich, and we avoid one network call per page load. A live API call
  // here would risk returning a flat reference-tier AST whose ids do not
  // line up with the derivation targets the hover panel expects.
  const fixture = buildStaticCompileResult(PROVENANCE_HINTS)
  try {
    renderAst(fixture.ast, output)
    provenanceDerivations = fixture.derivations || []
  } catch (error) {
    output.replaceChildren()
    const note = document.createElement('p')
    note.className = 'muted-copy'
    note.textContent = 'Unable to render provenance preview.'
    output.appendChild(note)
  }
}

function installCommerceLinks() {
  const links = {
    pro: LANDING_CONFIG.proStripeUrl,
    enterprise: LANDING_CONFIG.enterpriseUrl,
  }
  document.querySelectorAll('[data-config-link]').forEach((link) => {
    const key = link.getAttribute('data-config-link')
    link.href = links[key] || '#pricing'
    if (!hasProductionCommerceConfig()) {
      link.title = 'Production Stripe/API-key URL must be configured before launch.'
    }
  })
}

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text)
      return
    } catch {
      // Fall through to the textarea fallback.
    }
  }
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.left = '0'
  textarea.style.opacity = '0'
  textarea.style.pointerEvents = 'none'
  textarea.style.top = '0'
  document.body.appendChild(textarea)
  textarea.focus()
  textarea.select()
  textarea.setSelectionRange(0, text.length)
  const copied = document.execCommand('copy')
  textarea.remove()
  if (!copied) throw new Error('Copy command was rejected.')
}

function installCopyTextControls() {
  document.querySelectorAll('[data-copy-text]').forEach((button) => {
    const text = button.getAttribute('data-copy-text')
    const defaultLabel = button.textContent
    button.addEventListener('click', async () => {
      if (!text) return
      try {
        await copyText(text)
        button.textContent = 'copied'
        button.dataset.copyState = 'copied'
        window.setTimeout(() => {
          button.textContent = defaultLabel
          delete button.dataset.copyState
        }, 1400)
      } catch {
        button.textContent = text
        button.dataset.copyState = 'failed'
      }
    })
  })
}

export function initLandingPlayground() {
  installCommerceLinks()
  installCopyTextControls()
  installHoverInspector()
  renderProvenanceDetails(null)
  compileHero()
  compileProvenance()
  initLandingProof()
  initLandingStyleDerivation()
  initLandingMotifs()
}

initLandingPlayground()
