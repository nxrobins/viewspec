import {
  LANDING_CONFIG,
  compileRequestHeaders,
  hasLiveApiConfig,
  hasProductionCommerceConfig,
  hasPublicApiKey,
  redactedCompileRequestHeaders,
} from './landing-config.js'
import {
  DEFAULT_HINTS,
  TOKEN_DEFINITIONS,
  buildIntentBundle,
  buildStaticCompileResult,
  computeDashboardProfile,
  normalizeHints,
  stableHintKey,
} from './landing-payload.js'
import { countIrNodes, getAstRoot, renderAst } from './landing-emitter.js'

let currentHints = normalizeHints(DEFAULT_HINTS)
let currentAbortController = null
let renderSequence = 0
let debounceTimer = 0
let activeHighlight = null
let latestDerivations = []

const responseCache = new Map()

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

function requestPayloadSummary(payload) {
  const bytes = new Blob([JSON.stringify(payload)]).size
  return {
    bytes,
    hints: normalizeHints(currentHints),
    bindings: payload.view_spec.bindings.length,
    nodes: Object.keys(payload.substrate.nodes).length,
  }
}

function validateCompileResponse(data) {
  if (!data || typeof data !== 'object') throw new Error('Empty compiler response')
  if (!data.ast) throw new Error('Compiler response missing ast')
  if (!getAstRoot(data.ast)) throw new Error('Compiler response missing ast.result.root.root')
  if (!Array.isArray(data.derivations)) data.derivations = []
  if (!data.meta) data.meta = {}
  return data
}

function staticResult(reason, payload) {
  const data = buildStaticCompileResult(currentHints)
  data.__fallbackReason = reason
  return {
    data,
    payload: payload || buildIntentBundle(currentHints),
    status: 'static',
    roundTripMs: 0,
    fromCache: false,
  }
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value))
}

function walkIr(node, visit) {
  if (!node) return
  visit(node)
  ;(node.children || []).forEach((child) => walkIr(child, visit))
}

function findIrNode(node, id) {
  let match = null
  walkIr(node, (candidate) => {
    if (!match && candidate.id === id) match = candidate
  })
  return match
}

function collectLiveBindingNodes(root) {
  const bindings = new Map()
  walkIr(root, (node) => {
    if (node?.id?.startsWith('binding_kpi_')) bindings.set(node.id, node)
  })
  return bindings
}

function needsDashboardPresentationAdapter(root) {
  const motif = findIrNode(root, 'motif_kpis')
  if (!motif) return true
  return motif.primitive !== 'grid' || !(motif.children || []).every((child) => child.primitive === 'surface')
}

function applyLiveBindingsToTemplate(templateAst, liveBindings) {
  const templateRoot = getAstRoot(templateAst)
  walkIr(templateRoot, (node) => {
    const liveNode = liveBindings.get(node.id)
    if (!liveNode) return
    node.props = cloneJson(liveNode.props || node.props || {})
    node.provenance = cloneJson(liveNode.provenance || node.provenance || {})
    node.children = cloneJson(liveNode.children || [])
  })
}

function normalizeCompileResponse(data, hints) {
  const root = getAstRoot(data.ast)
  if (!root || !needsDashboardPresentationAdapter(root)) return data

  const liveBindings = collectLiveBindingNodes(root)
  if (!liveBindings.size) return data

  const presentation = buildStaticCompileResult(hints)
  const adaptedAst = cloneJson(presentation.ast)
  applyLiveBindingsToTemplate(adaptedAst, liveBindings)

  const adapted = {
    ...data,
    ast: {
      ...adaptedAst,
      result: {
        ...adaptedAst.result,
        diagnostics: data.ast?.result?.diagnostics || [],
      },
      style_values: {
        ...(data.ast?.style_values || {}),
        ...(adaptedAst.style_values || {}),
      },
    },
    derivations: Array.isArray(data.derivations) && data.derivations.length ? data.derivations : presentation.derivations,
    __visualAdapter: 'dashboard_motif_from_live_bindings',
  }

  adapted.meta = {
    ...(data.meta || {}),
    ir_node_count: countIrNodes(getAstRoot(adapted.ast)),
    style_token_count: Object.keys(adapted.ast.style_values || {}).length,
  }

  return adapted
}

async function fetchCompiledDashboard(hints) {
  const normalized = normalizeHints(hints)
  const cacheKey = stableHintKey(normalized)
  const payload = buildIntentBundle(normalized)

  if (responseCache.has(cacheKey)) {
    if (currentAbortController) currentAbortController.abort()
    return {
      data: responseCache.get(cacheKey),
      payload,
      status: 'cached',
      roundTripMs: 0,
      fromCache: true,
    }
  }

  if (!hasLiveApiConfig()) {
    return staticResult('Production landing API key is not configured.', payload)
  }

  if (currentAbortController) currentAbortController.abort()
  const endpoints = LANDING_CONFIG.apiUrls
  const perEndpointTimeoutMs = endpoints.length > 1 ? Math.min(LANDING_CONFIG.requestTimeoutMs, 2500) : LANDING_CONFIG.requestTimeoutMs
  let lastError = null

  for (const apiUrl of endpoints) {
    currentAbortController = new AbortController()
    let timedOut = false
    const timeout = window.setTimeout(() => {
      timedOut = true
      currentAbortController.abort()
    }, perEndpointTimeoutMs)
    const startedAt = performance.now()

    try {
      const response = await fetch(apiUrl, {
        method: 'POST',
        headers: compileRequestHeaders(),
        body: JSON.stringify(payload),
        signal: currentAbortController.signal,
      })

      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = normalizeCompileResponse(validateCompileResponse(await response.json()), normalized)
      const roundTripMs = performance.now() - startedAt
      data.meta = {
        ...data.meta,
        api_url: apiUrl,
        endpoint_fallback: apiUrl !== LANDING_CONFIG.apiUrl,
        round_trip_ms: Number(roundTripMs.toFixed(1)),
      }
      responseCache.set(cacheKey, data)
      return {
        apiUrl,
        data,
        payload,
        status: response.status,
        roundTripMs,
        fromCache: false,
      }
    } catch (error) {
      if (error.name === 'AbortError' && !timedOut) return null
      lastError = error.name === 'AbortError' && timedOut ? new Error(`Compiler request timed out for ${apiUrl}.`) : error
    } finally {
      window.clearTimeout(timeout)
    }
  }

  return staticResult(lastError?.message || 'Compiler API unavailable.', payload)
}

function renderStatus(result) {
  const meta = result.data.meta || {}
  const compileMs = `${Number(meta.compile_ms || 0).toFixed(1)}ms`
  if (result.data.__static) {
    setStatus('static sample', 'static')
    setText('hero-compile-time', 'sample')
  } else if (result.fromCache) {
    setStatus('cached response', 'cached')
    setText('hero-compile-time', compileMs)
  } else {
    setStatus(`${compileMs} compile`, 'live')
    setText('hero-compile-time', compileMs)
  }
  setText('live-compile-time', compileMs)
  setText('live-token-count', String(meta.style_token_count || Object.keys(result.data.ast?.style_values || {}).length))
  setText('live-ir-count', String(meta.ir_node_count || countIrNodes(getAstRoot(result.data.ast))))
}

function hintLabel(value) {
  return String(value || '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function updateHintSummary() {
  const profile = computeDashboardProfile(currentHints)
  setText(
    'hint-summary',
    `${hintLabel(currentHints.audience)} / ${hintLabel(currentHints.mood)} / ${hintLabel(currentHints.density)} / ${hintLabel(currentHints.viewport)} / ${profile.layout.minWidth} / ${profile.layout.columns} cols`
  )
}

function elementForDerivation(derivation) {
  const preview = byId('playground-output')
  if (!preview || !derivation) return null
  const targetId = window.CSS?.escape ? CSS.escape(derivation.target_ir_id) : String(derivation.target_ir_id).replace(/"/g, '\\"')
  return preview.querySelector(`[data-ir-id="${targetId}"]`)
}

function clearHighlight() {
  if (activeHighlight) activeHighlight.classList.remove('provenance-active')
  activeHighlight = null
}

function highlightDerivation(derivation) {
  clearHighlight()
  const target = elementForDerivation(derivation)
  if (!target) return
  activeHighlight = target
  target.classList.add('provenance-active')
  target.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' })
}

function renderTokenInspector(derivations) {
  latestDerivations = derivations || []
  const list = byId('token-inspector-list')
  if (!list) return
  list.replaceChildren()

  if (!latestDerivations.length) {
    const empty = document.createElement('p')
    empty.className = 'muted-copy'
    empty.textContent = 'No Level 2 derivations returned for this compile.'
    list.appendChild(empty)
    return
  }

  latestDerivations.forEach((derivation) => {
    const item = document.createElement('button')
    item.type = 'button'
    item.className = 'token-row'
    item.dataset.token = derivation.token
    item.addEventListener('click', () => highlightDerivation(derivation))

    const header = document.createElement('span')
    header.className = 'token-row-title'
    header.textContent = `${derivation.token} -> ${derivation.target_label || derivation.target_ir_id}`

    const trigger = document.createElement('span')
    trigger.className = 'token-row-trigger'
    trigger.textContent = derivation.trigger || 'Compiler derivation'

    const reason = document.createElement('span')
    reason.className = 'token-row-reason'
    reason.textContent = derivation.reason || ''

    item.append(header, trigger, reason)
    list.appendChild(item)
  })
}

function renderApiInspector(result) {
  const requestCode = byId('api-request-code')
  const responseCode = byId('api-response-code')
  if (!requestCode || !responseCode) return

  const summary = requestPayloadSummary(result.payload)
  requestCode.textContent = JSON.stringify(
    {
      method: 'POST',
      url: result.apiUrl || LANDING_CONFIG.apiUrl,
      headers: redactedCompileRequestHeaders(),
      auth: hasPublicApiKey() ? 'public landing key' : 'anonymous free tier',
      payload_bytes: summary.bytes,
      hints: summary.hints,
      body: result.payload,
    },
    null,
    2
  )

  const diagnostics = result.data.ast?.result?.diagnostics || []
  responseCode.textContent = JSON.stringify(
    {
      status: result.status,
      source: result.data.__static ? 'static fixture' : result.fromCache ? 'cache' : 'hosted api',
      endpoint: result.apiUrl || result.data.meta?.api_url || null,
      fallback_reason: result.data.__fallbackReason || null,
      visual_adapter: result.data.__visualAdapter || null,
      meta: result.data.meta || {},
      derivations: (result.data.derivations || []).length,
      diagnostics: diagnostics.length,
      quota: result.data.quota || null,
    },
    null,
    2
  )
}

function updateTokenGallery(derivations) {
  const activeByToken = new Map()
  ;(derivations || []).forEach((derivation) => activeByToken.set(derivation.token, derivation))
  document.querySelectorAll('[data-token-card]').forEach((card) => {
    const token = card.getAttribute('data-token-card')
    const active = activeByToken.has(token)
    card.dataset.active = active ? 'true' : 'false'
    card.setAttribute('aria-pressed', active ? 'true' : 'false')
    card.onclick = () => {
      const derivation = activeByToken.get(token)
      if (derivation) {
        highlightDerivation(derivation)
      } else {
        clearHighlight()
      }
    }
  })
}

function renderProvenanceDetails(el) {
  const panel = byId('provenance-details')
  if (!panel) return
  panel.replaceChildren()
  if (!el) {
    const copy = document.createElement('p')
    copy.className = 'muted-copy'
    copy.textContent = 'Hover the compiled dashboard to inspect provenance.'
    panel.appendChild(copy)
    return
  }

  const refs = {
    ir_id: el.dataset.irId || '',
    style_tokens: JSON.parse(el.dataset.styleTokens || '[]'),
    content_refs: JSON.parse(el.dataset.contentRefs || '[]'),
    intent_refs: JSON.parse(el.dataset.intentRefs || '[]'),
  }
  const matched = latestDerivations.filter((derivation) => {
    return derivation.target_ir_id === refs.ir_id || refs.content_refs.includes(derivation.target_content_ref)
  })
  const pre = document.createElement('pre')
  pre.textContent = JSON.stringify({ ...refs, derivations: matched }, null, 2)
  panel.appendChild(pre)
}

function installHoverInspector() {
  const preview = byId('playground-output')
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
  preview.addEventListener('mouseleave', () => {
    clearHighlight()
    renderProvenanceDetails(null)
  })
}

function renderBeforeAfter() {
  const reference = byId('reference-output')
  const hosted = byId('hosted-output')
  const compareHints = normalizeHints({ ...DEFAULT_HINTS, viewport: 'tablet' })
  if (reference) renderAst(buildStaticCompileResult(compareHints, { mode: 'reference' }).ast, reference)
  if (hosted) renderAst(buildStaticCompileResult(compareHints, { mode: 'hosted' }).ast, hosted)
}

function applyViewportFrame() {
  const profile = computeDashboardProfile(currentHints)
  const heroWidths = {
    mobile: '340px',
    tablet: '520px',
    desktop: '680px',
  }
  document.querySelectorAll('[data-preview-frame]').forEach((frame) => {
    frame.dataset.viewport = currentHints.viewport
    const shell = frame.querySelector('[data-viewport-shell]')
    if (!shell) return
    const compactHero = frame.dataset.previewMode === 'hero'
    const targetWidth = compactHero ? heroWidths[currentHints.viewport] : profile.layout.targetWidth
    const width = `min(${targetWidth}, 100%)`
    shell.dataset.viewport = currentHints.viewport
    shell.style.width = width
    shell.style.minWidth = width
  })
}

function renderResult(result) {
  applyViewportFrame()
  clearHighlight()
  const outputs = document.querySelectorAll('[data-compiled-output]')
  if (!outputs.length) return
  outputs.forEach((output) => renderAst(result.data.ast, output))
  latestDerivations = result.data.derivations || []
  renderStatus(result)
  renderTokenInspector(latestDerivations)
  renderApiInspector(result)
  updateTokenGallery(latestDerivations)
  renderProvenanceDetails(null)
}

async function compileLatest(sequence) {
  setStatus('compiling...', 'loading')
  const result = await fetchCompiledDashboard(currentHints)
  if (!result || sequence !== renderSequence) return
  renderResult(result)
}

function scheduleCompile() {
  renderSequence += 1
  const sequence = renderSequence
  window.clearTimeout(debounceTimer)
  debounceTimer = window.setTimeout(() => compileLatest(sequence), 140)
}

function setHint(group, value) {
  if (currentHints[group] === value) return
  currentHints = normalizeHints({ ...currentHints, [group]: value })
  syncHintButtons()
  updateHintSummary()
  applyViewportFrame()
  scheduleCompile()
}

function syncHintButtons() {
  Object.entries(currentHints).forEach(([group, value]) => {
    document.querySelectorAll(`[data-hint-group="${group}"]`).forEach((button) => {
      const active = button.getAttribute('data-hint-value') === value
      button.setAttribute('aria-pressed', active ? 'true' : 'false')
      button.dataset.active = active ? 'true' : 'false'
    })
  })
}

function installHintControls() {
  syncHintButtons()
  updateHintSummary()
  applyViewportFrame()
  document.querySelectorAll('[data-hint-group]').forEach((button) => {
    button.addEventListener('click', () => {
      setHint(button.getAttribute('data-hint-group'), button.getAttribute('data-hint-value'))
    })
  })
  scheduleCompile()
}

function installCommerceLinks() {
  const links = {
    pro: LANDING_CONFIG.proStripeUrl,
    scale: LANDING_CONFIG.scaleStripeUrl,
    signup: LANDING_CONFIG.signupUrl,
  }
  document.querySelectorAll('[data-config-link]').forEach((link) => {
    const key = link.getAttribute('data-config-link')
    link.href = links[key] || '#pricing'
    if (!hasProductionCommerceConfig()) {
      link.title = 'Production Stripe/API-key URL must be configured before launch.'
    }
  })
}

function renderTokenGalleryDefinitions() {
  document.querySelectorAll('[data-token-card]').forEach((card) => {
    const token = card.getAttribute('data-token-card')
    const definition = TOKEN_DEFINITIONS.find((item) => item.token === token)
    if (!definition) return
    const trigger = card.querySelector('[data-token-trigger]')
    const effect = card.querySelector('[data-token-effect]')
    if (trigger) trigger.textContent = definition.trigger
    if (effect) effect.textContent = definition.visualEffect
  })
}

export function initLandingPlayground() {
  installCommerceLinks()
  renderTokenGalleryDefinitions()
  renderBeforeAfter()
  installHoverInspector()
  installHintControls()
}

initLandingPlayground()
