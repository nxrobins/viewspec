import { layoutWithLines, prepareWithSegments } from '../vendor/pretext/pretext.esm.js'

const DEFAULT_FONT_FAMILY = '-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif'
const observedWrappers = new Map()
const queuedCanvases = new Set()
let resizeObserver = null
let frame = 0
let fontsReady = false

function injectBaseStyles() {
  if (document.getElementById('viewspec-pretext-canvas-styles')) return
  const style = document.createElement('style')
  style.id = 'viewspec-pretext-canvas-styles'
  style.textContent = `
    .pretext-canvas-wrap {
      display: block;
      min-width: 0;
    }

    [data-pretext-canvas] {
      display: block;
      max-width: 100%;
    }
  `
  document.head.appendChild(style)
}

function parseSize(value, fallback) {
  const parsed = Number.parseFloat(value || '')
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

function computedLineHeight(style, fontSize) {
  const parsed = Number.parseFloat(style.lineHeight || '')
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fontSize * 1.2
}

function textFor(canvas) {
  return canvas.dataset.text || canvas.getAttribute('aria-label') || canvas.textContent || ''
}

function fontFor(canvas, style, fontSize) {
  const explicitFont = canvas.dataset.font
  if (explicitFont && explicitFont.includes('px')) return explicitFont

  const fontStyle = canvas.dataset.fontStyle || style.fontStyle || 'normal'
  const fontWeight = canvas.dataset.weight || style.fontWeight || '400'
  const fontFamily = explicitFont || canvas.dataset.family || style.fontFamily || DEFAULT_FONT_FAMILY
  return `${fontStyle} ${fontWeight} ${fontSize}px ${fontFamily}`
}

function wrapperFor(canvas) {
  return canvas.parentElement || canvas
}

function ensureAccessible(canvas) {
  const text = textFor(canvas)
  if (!canvas.hasAttribute('role')) canvas.setAttribute('role', 'img')
  if (!canvas.hasAttribute('aria-label')) canvas.setAttribute('aria-label', text)
  if (!canvas.textContent.trim()) canvas.textContent = text
}

function drawCanvas(canvas) {
  if (!canvas.isConnected) return

  ensureAccessible(canvas)
  const wrapper = wrapperFor(canvas)
  const wrapperRect = wrapper.getBoundingClientRect()
  if (wrapperRect.width <= 0) return

  const style = window.getComputedStyle(canvas)
  const text = textFor(canvas)
  const fontSize = parseSize(canvas.dataset.size, parseSize(style.fontSize, 16))
  const lineHeight = parseSize(canvas.dataset.lineHeight, computedLineHeight(style, fontSize))
  const maxWidth = parseSize(canvas.dataset.maxWidth, wrapperRect.width)
  const cssWidth = Math.max(1, Math.min(wrapperRect.width, maxWidth))
  const font = fontFor(canvas, style, fontSize)
  const color = canvas.dataset.color || style.color || '#0f172a'
  const align = canvas.dataset.align || style.textAlign || 'left'
  const dpr = window.devicePixelRatio || 1

  const prepared = prepareWithSegments(text, font, { whiteSpace: 'normal' })
  const result = layoutWithLines(prepared, cssWidth, lineHeight)
  const lines = result.lines.length > 0 ? result.lines : [{ text, width: 0 }]
  const cssHeight = Math.max(lineHeight, result.height || lines.length * lineHeight)

  canvas.width = Math.max(1, Math.ceil(cssWidth * dpr))
  canvas.height = Math.max(1, Math.ceil(cssHeight * dpr))
  canvas.style.width = `${cssWidth}px`
  canvas.style.height = `${cssHeight}px`

  const ctx = canvas.getContext('2d')
  if (!ctx) return

  ctx.setTransform(1, 0, 0, 1, 0, 0)
  ctx.clearRect(0, 0, canvas.width, canvas.height)
  ctx.scale(dpr, dpr)
  ctx.font = font
  ctx.fillStyle = color
  ctx.textBaseline = 'top'
  ctx.textAlign = 'left'

  lines.forEach((line, index) => {
    const lineText = line.text || ''
    const lineWidth = Number.isFinite(line.width) ? line.width : ctx.measureText(lineText).width
    let x = 0
    if (align === 'center') x = Math.max(0, (cssWidth - lineWidth) / 2)
    if (align === 'right' || align === 'end') x = Math.max(0, cssWidth - lineWidth)
    ctx.fillText(lineText, x, index * lineHeight)
  })

  canvas.dataset.pretextReady = 'true'
  canvas.dataset.pretextLines = String(lines.length)
  canvas.dataset.pretextWidth = cssWidth.toFixed(2)
  canvas.dataset.pretextHeight = cssHeight.toFixed(2)
  canvas.dataset.pretextBackingWidth = String(canvas.width)
  canvas.dataset.pretextBackingHeight = String(canvas.height)
  canvas.dataset.pretextDpr = String(dpr)
}

function queue(canvas) {
  if (!canvas || !(canvas instanceof HTMLCanvasElement)) return
  queuedCanvases.add(canvas)
  if (frame) return
  frame = window.requestAnimationFrame(() => {
    frame = 0
    const canvases = Array.from(queuedCanvases)
    queuedCanvases.clear()
    canvases.forEach(drawCanvas)
    document.documentElement.dataset.pretextReady = 'true'
  })
}

function canvasesIn(root) {
  if (!root) return []
  if (root instanceof HTMLCanvasElement && root.matches('[data-pretext-canvas]')) return [root]
  if (root instanceof Element || root instanceof Document || root instanceof DocumentFragment) {
    return Array.from(root.querySelectorAll('[data-pretext-canvas]'))
  }
  return []
}

function observe(canvas) {
  const wrapper = wrapperFor(canvas)
  const current = observedWrappers.get(wrapper) || new Set()
  current.add(canvas)
  observedWrappers.set(wrapper, current)
  resizeObserver.observe(wrapper)
}

function install(root = document) {
  injectBaseStyles()
  if (!resizeObserver) {
    resizeObserver = new ResizeObserver((entries) => {
      entries.forEach((entry) => {
        const canvases = observedWrappers.get(entry.target)
        if (!canvases) return
        const width = entry.contentRect.width
        canvases.forEach((canvas) => {
          const previous = Number.parseFloat(canvas.dataset.pretextObservedWidth || '0')
          if (Math.abs(previous - width) <= 0.5) return
          canvas.dataset.pretextObservedWidth = width.toFixed(2)
          queue(canvas)
        })
      })
    })
  }

  canvasesIn(root).forEach((canvas) => {
    ensureAccessible(canvas)
    observe(canvas)
    queue(canvas)
  })
}

export function refresh(root = document) {
  const run = () => {
    install(root)
    canvasesIn(root).forEach(queue)
  }
  if (fontsReady) {
    run()
  } else {
    const targetRoot = root
    readyPromise.then(() => refresh(targetRoot))
  }
}

export function setCanvasText(canvas, text) {
  if (!canvas) return
  canvas.dataset.text = text
  canvas.setAttribute('aria-label', text)
  canvas.textContent = text
  refresh(canvas)
}

const readyPromise = (document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve())
  .catch(() => undefined)
  .then(() => {
    fontsReady = true
    install(document)
  })

window.ViewSpecPretext = {
  refresh,
  setCanvasText,
  ready: readyPromise,
}
