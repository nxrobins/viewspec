/**
 * Shared navigation bar for ViewSpec demos.
 * Include via <script src="../shared/nav.js"></script> in each demo page.
 */
(function () {
  const demos = [
    { href: '../motif-switcher/', label: 'Motifs' },
    { href: '../provenance-inspector/', label: 'Provenance' },
    { href: '../live-builder/', label: 'Builder' },
    { href: '../invariants/', label: 'Invariants' },
    { href: '../fifteen-lines/', label: '15 Lines' },
  ]

  const current = window.location.pathname

  const nav = document.createElement('nav')
  nav.setAttribute('aria-label', 'Demo navigation')
  nav.style.cssText =
    'position:fixed;top:0;left:0;right:0;z-index:100;' +
    'background:rgba(6,8,11,0.85);backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);' +
    'border-bottom:1px solid rgba(45,212,191,0.08);' +
    'display:flex;align-items:center;gap:0.25rem;padding:0.5rem 1rem;' +
    'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;font-size:0.75rem;'

  // Home link
  const home = document.createElement('a')
  home.href = '../'
  home.textContent = 'ViewSpec'
  home.style.cssText =
    'color:#2dd4bf;font-weight:800;text-decoration:none;margin-right:0.75rem;font-size:0.85rem;letter-spacing:-0.02em;'
  nav.appendChild(home)

  // Separator
  const sep = document.createElement('span')
  sep.textContent = '/'
  sep.style.cssText = 'color:rgba(148,163,184,0.3);margin-right:0.5rem;'
  nav.appendChild(sep)

  demos.forEach(function (d) {
    const a = document.createElement('a')
    a.href = d.href
    a.textContent = d.label
    const isActive = current.includes(d.href.replace('..', '').replace(/\/$/, ''))
    a.style.cssText =
      'text-decoration:none;padding:0.3rem 0.6rem;border-radius:999px;transition:all 200ms ease;' +
      (isActive
        ? 'color:#2dd4bf;background:rgba(45,212,191,0.1);border:1px solid rgba(45,212,191,0.2);'
        : 'color:#94a3b8;border:1px solid transparent;')
    a.addEventListener('mouseenter', function () {
      if (!isActive) { a.style.color = '#e2e8f0'; a.style.background = 'rgba(148,163,184,0.06)'; }
    })
    a.addEventListener('mouseleave', function () {
      if (!isActive) { a.style.color = '#94a3b8'; a.style.background = 'transparent'; }
    })
    nav.appendChild(a)
  })

  document.body.prepend(nav)

  // Push body content down to avoid overlap
  document.body.style.paddingTop = '3rem'
})()
