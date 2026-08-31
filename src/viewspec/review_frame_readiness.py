"""Shared, bounded DOM-commit readiness for local and hosted review frames.

This observes semantic screen identity, not layout fidelity or runtime proof.
The generated React route listeners are installed in the layout-effect phase,
before a MutationObserver can observe the committed screen.
"""

FRAME_RENDER_WAIT_JS = r"""
  const waitForRenderedScreen = (route = null) => new Promise((resolve, reject) => {
    let settled = false
    const finish = (screen) => {
      if (settled) return
      settled = true
      observer.disconnect()
      clearTimeout(deadline)
      if (screen) resolve(screen)
      else reject(new Error('declared screen did not render'))
    }
    const check = () => {
      const screens = document.querySelectorAll('[data-viewspec-app-screen]:not([hidden])')
      if (screens.length === 1 && (!route || screens[0].dataset.routePath === route)) finish(screens[0])
    }
    const observer = new MutationObserver(check)
    const deadline = setTimeout(() => finish(null), 4000)
    observer.observe(document.documentElement, {
      childList: true, subtree: true, attributes: true,
      attributeFilter: ['hidden', 'data-route-path', 'data-viewspec-app-screen'],
    })
    check()
  })
  let initialRenderPromise = null
  const waitForInitialScreen = (route) => {
    if (!initialRenderPromise) initialRenderPromise = waitForRenderedScreen(route)
    return initialRenderPromise
  }
"""
