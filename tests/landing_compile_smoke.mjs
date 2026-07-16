import assert from 'node:assert/strict'
import { copyFile, mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const sourceRoot = resolve('demos/shared')

function memoryStorage() {
  const data = new Map()
  return {
    getItem(key) {
      return data.has(key) ? data.get(key) : null
    },
    setItem(key, value) {
      data.set(key, String(value))
    },
  }
}

async function loadCompile(config = {}) {
  const tempDir = await mkdtemp(join(tmpdir(), 'viewspec-landing-compile-'))
  await Promise.all([
    copyFile(join(sourceRoot, 'landing-config.js'), join(tempDir, 'landing-config.js')),
    copyFile(join(sourceRoot, 'landing-compile.js'), join(tempDir, 'landing-compile.js')),
  ])
  const sessionStorage = memoryStorage()
  globalThis.window = {
    VIEWSPEC_LANDING_CONFIG: config,
    clearTimeout,
    sessionStorage,
    setTimeout,
  }
  const module = await import(pathToFileURL(join(tempDir, 'landing-compile.js')).href)
  return { module, sessionStorage }
}

function response(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() {
      return payload
    },
  }
}

{
  const { module } = await loadCompile({
    apiUrl: 'https://primary.test/v1/compile',
    fallbackApiUrls: [],
    requestTimeoutMs: 100,
  })
  globalThis.fetch = async () => response(200, null)
  await assert.rejects(
    () => module.compileBundle({ substrate: {}, view_spec: {} }),
    /Compiler response payload must be an object/,
  )
}

{
  const primary = 'https://primary.test/v1/compile'
  const fallback = 'https://fallback.test/v1/compile'
  const { module, sessionStorage } = await loadCompile({
    apiUrl: primary,
    fallbackApiUrls: [fallback],
    endpointFailureTtlMs: 300000,
    endpointStaggerMs: 1,
    requestTimeoutMs: 100,
  })
  globalThis.fetch = async (url) => {
    if (url === primary) return response(503, { error: { message: 'primary unavailable' } })
    return response(200, { ast: { result: {} }, meta: { compile_ms: 2.5 } })
  }

  const result = await module.compileBundle({ substrate: {}, view_spec: {} })
  assert.equal(result.apiUrl, fallback)
  assert.equal(result.data.meta.api_url, fallback)
  assert.equal(result.data.meta.endpoint_fallback, true)
  assert.equal(Number.isFinite(result.data.meta.round_trip_ms), true)
  const stored = JSON.parse(sessionStorage.getItem('viewspec.landing.endpointState.v1'))
  assert(Number(stored.failures[primary]) > Date.now())
  assert.deepEqual(module.orderedApiUrls(), [fallback])
}

{
  const primary = 'https://slow-primary.test/v1/compile'
  const fallback = 'https://fast-fallback.test/v1/compile'
  const { module, sessionStorage } = await loadCompile({
    apiUrl: primary,
    fallbackApiUrls: [fallback],
    endpointFailureTtlMs: 300000,
    endpointStaggerMs: 1,
    requestTimeoutMs: 100,
  })
  globalThis.fetch = (url, options) => {
    if (url === fallback) {
      return Promise.resolve(response(200, { ast: { result: {} }, meta: {} }))
    }
    return new Promise((resolveFetch, rejectFetch) => {
      options.signal.addEventListener(
        'abort',
        () => {
          const error = new Error('aborted after another endpoint won')
          error.name = 'AbortError'
          rejectFetch(error)
        },
        { once: true },
      )
    })
  }

  const result = await module.compileBundle({ substrate: {}, view_spec: {} })
  assert.equal(result.apiUrl, fallback)
  await new Promise((resolveWait) => setTimeout(resolveWait, 0))
  const stored = JSON.parse(sessionStorage.getItem('viewspec.landing.endpointState.v1'))
  assert.deepEqual(stored.failures, {})
  assert.deepEqual(module.orderedApiUrls(), [fallback, primary])
}

console.log('Validated landing response shape, timing, failover, and circuit-breaker races.')
