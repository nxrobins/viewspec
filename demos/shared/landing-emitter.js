export const TAILWIND_BY_PRIMITIVE = {
  root: 'w-full transition-all duration-300',
  stack: 'flex flex-col gap-2',
  grid: 'grid gap-3',
  cluster: 'flex flex-row flex-wrap gap-2',
  // Surface is a card. Always lays out children top-to-bottom with gap +
  // padding. The compiler may also assign density.* tokens that re-emit gap +
  // padding via inline styles; cssText assignment runs last so those win when
  // present. When absent (today), these Tailwind classes are the floor.
  surface: 'flex flex-col gap-2 p-4 transition-all duration-300',
  text: 'block transition-colors duration-300',
  label: 'block transition-all duration-300',
  value: 'block transition-all duration-300 leading-tight',
  badge: 'inline-flex w-fit items-center px-2 py-0.5',
  image_slot: 'grid place-items-center',
  rule: 'my-2 border-slate-200',
  svg: 'grid place-items-center',
  button: 'inline-flex w-fit items-center rounded-md bg-teal-700 px-4 py-2 text-sm font-bold text-white shadow-sm hover:bg-teal-800',
  error_boundary: 'rounded-md border-2 border-dashed border-red-500 bg-red-50 p-4 font-mono text-sm text-red-800',
}

const TAG_BY_PRIMITIVE = {
  root: 'div',
  stack: 'div',
  grid: 'div',
  cluster: 'div',
  surface: 'div',
  text: 'p',
  label: 'span',
  value: 'span',
  badge: 'span',
  image_slot: 'div',
  rule: 'hr',
  svg: 'div',
  button: 'button',
  error_boundary: 'div',
}

export function getAstRoot(ast) {
  return ast?.result?.root?.root || ast?.root?.root || ast?.root || null
}

export function renderIRNode(node, styleValues = {}) {
  const tag = TAG_BY_PRIMITIVE[node?.primitive] || 'div'
  const el = document.createElement(tag)
  const provenance = node.provenance || {}
  const contentRefs = provenance.content_refs || []
  const intentRefs = provenance.intent_refs || []
  const styleTokens = node.style_tokens || []
  const props = node.props || {}

  el.id = `dom-${node.id}`
  el.className = TAILWIND_BY_PRIMITIVE[node.primitive] || 'rounded border border-slate-200 p-2'
  el.dataset.irId = node.id
  el.dataset.contentRefs = JSON.stringify(contentRefs)
  el.dataset.intentRefs = JSON.stringify(intentRefs)
  el.dataset.tokenCount = String(styleTokens.length)
  el.dataset.styleTokens = JSON.stringify(styleTokens)

  const css = styleTokens
    .map((token) => styleValues[token] || '')
    .join(' ')
    .trim()
  if (css) el.style.cssText = css

  // cssText assignment clobbers inline properties, so computed layout overrides happen after it.
  if (node.primitive === 'grid' && props.columns) {
    el.style.gridTemplateColumns = `repeat(${Number(props.columns)}, minmax(0, 1fr))`
  }

  if (node.primitive === 'button') {
    el.type = 'button'
    if (props.action_id) el.dataset.actionId = String(props.action_id)
    if (props.action_kind) el.dataset.actionKind = String(props.action_kind)
    if (props.payload_bindings) el.dataset.payloadBindings = JSON.stringify(props.payload_bindings)
  }

  if (node.primitive === 'rule') return el

  if (node.primitive === 'image_slot') {
    el.textContent = String(props.alt || 'image slot')
  } else if (node.primitive === 'svg') {
    el.textContent = String(props.label || 'vector slot')
  } else if (node.primitive === 'error_boundary') {
    const code = document.createElement('div')
    code.className = 'font-bold'
    code.textContent = String(props.diagnostic_code || 'COMPILER_ERROR')
    const message = document.createElement('div')
    message.className = 'mt-1'
    message.textContent = String(props.message || 'Compiler diagnostic')
    el.append(code, message)
  } else if (Object.prototype.hasOwnProperty.call(props, 'text')) {
    el.appendChild(document.createTextNode(String(props.text)))
  }

  ;(node.children || []).forEach((child) => {
    el.appendChild(renderIRNode(child, styleValues))
  })

  return el
}

export function renderAst(ast, container) {
  const root = getAstRoot(ast)
  if (!root) throw new Error('Compiler response did not include ast.result.root.root')
  const rendered = renderIRNode(root, ast.style_values || {})
  container.replaceChildren(rendered)
  return rendered
}

export function countIrNodes(node) {
  if (!node) return 0
  return 1 + (node.children || []).reduce((total, child) => total + countIrNodes(child), 0)
}
