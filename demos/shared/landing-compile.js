import {
  LANDING_CONFIG,
  compileRequestHeaders,
  hasLiveApiConfig,
} from './landing-config.js'

const ENDPOINT_STATE_KEY = 'viewspec.landing.endpointState.v1'

let endpointState = readEndpointState()
let preferredApiUrl = endpointState.preferredApiUrl || null

function readEndpointState() {
  try {
    const raw = window.sessionStorage?.getItem(ENDPOINT_STATE_KEY)
    if (!raw) return { failures: {} }
    const parsed = JSON.parse(raw)
    const failures = parsed?.failures && typeof parsed.failures === 'object' ? parsed.failures : {}
    return {
      preferredApiUrl: typeof parsed?.preferredApiUrl === 'string' ? parsed.preferredApiUrl : null,
      failures,
    }
  } catch {
    return { failures: {} }
  }
}

function writeEndpointState() {
  try {
    window.sessionStorage?.setItem(ENDPOINT_STATE_KEY, JSON.stringify(endpointState))
  } catch {
    // sessionStorage may be unavailable; in-memory state still applies.
  }
}

function pruneEndpointFailures() {
  const cutoff = Date.now()
  const configuredUrls = LANDING_CONFIG.apiUrls || []
  const failures = { ...(endpointState.failures || {}) }
  let changed = false
  Object.entries(failures).forEach(([apiUrl, expiresAt]) => {
    if (Number(expiresAt) <= cutoff || !configuredUrls.includes(apiUrl)) {
      delete failures[apiUrl]
      changed = true
    }
  })
  if (changed) {
    endpointState = { ...endpointState, failures }
    writeEndpointState()
  }
}

function endpointIsCoolingDown(apiUrl) {
  return Number(endpointState.failures?.[apiUrl] || 0) > Date.now()
}

export function markEndpointFailure(apiUrl) {
  if (!apiUrl) return
  const failures = { ...(endpointState.failures || {}) }
  failures[apiUrl] = Date.now() + LANDING_CONFIG.endpointFailureTtlMs
  endpointState = { ...endpointState, failures }
  writeEndpointState()
}

export function markEndpointSuccess(apiUrl) {
  if (!apiUrl) return
  const failures = { ...(endpointState.failures || {}) }
  delete failures[apiUrl]
  preferredApiUrl = apiUrl
  endpointState = {
    ...endpointState,
    preferredApiUrl: apiUrl,
    failures,
  }
  writeEndpointState()
}

export function orderedApiUrls() {
  const urls = LANDING_CONFIG.apiUrls || []
  pruneEndpointFailures()
  const preferredFirst = preferredApiUrl && urls.includes(preferredApiUrl)
    ? [preferredApiUrl, ...urls.filter((url) => url !== preferredApiUrl)]
    : urls
  const available = preferredFirst.filter((apiUrl) => !endpointIsCoolingDown(apiUrl))
  return available.length ? available : preferredFirst
}

function abortError() {
  const error = new Error('Compiler request cancelled.')
  error.name = 'AbortError'
  return error
}

/**
 * Post a payload to /v1/compile with endpoint failover.
 *
 * Returns an object: { apiUrl, data, status, roundTripMs } on success.
 * Throws on total failure (all endpoints down) or when the caller-supplied
 * AbortSignal fires.
 */
export async function compileBundle(payload, options = {}) {
  if (!hasLiveApiConfig()) {
    throw new Error('Hosted compiler is not configured.')
  }

  const externalSignal = options.signal
  const masterController = new AbortController()
  const onExternalAbort = () => masterController.abort()
  if (externalSignal) {
    if (externalSignal.aborted) throw abortError()
    externalSignal.addEventListener('abort', onExternalAbort, { once: true })
  }

  const endpoints = orderedApiUrls()

  async function compileFromEndpoint(apiUrl) {
    if (masterController.signal.aborted) throw abortError()
    const endpointController = new AbortController()
    const abortEndpoint = () => endpointController.abort()
    masterController.signal.addEventListener('abort', abortEndpoint, { once: true })
    let timedOut = false
    const timeout = window.setTimeout(() => {
      timedOut = true
      endpointController.abort()
    }, LANDING_CONFIG.requestTimeoutMs)
    const startedAt = performance.now()

    try {
      const response = await fetch(apiUrl, {
        method: 'POST',
        headers: compileRequestHeaders(),
        body: JSON.stringify(payload),
        signal: endpointController.signal,
      })

      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      const roundTripMs = performance.now() - startedAt
      data.meta = {
        ...(data.meta || {}),
        api_url: apiUrl,
        endpoint_fallback: apiUrl !== LANDING_CONFIG.apiUrl,
        round_trip_ms: Number(roundTripMs.toFixed(1)),
      }
      return {
        apiUrl,
        data,
        status: response.status,
        roundTripMs,
      }
    } catch (error) {
      if (masterController.signal.aborted) throw abortError()
      markEndpointFailure(apiUrl)
      if (error.name === 'AbortError' && timedOut) {
        throw new Error(`Compiler request timed out for ${apiUrl}.`)
      }
      throw error
    } finally {
      masterController.signal.removeEventListener('abort', abortEndpoint)
      window.clearTimeout(timeout)
    }
  }

  function compileAfterStagger(apiUrl, index) {
    const delay = index === 0 ? 0 : LANDING_CONFIG.endpointStaggerMs
    return new Promise((resolve, reject) => {
      if (masterController.signal.aborted) {
        reject(abortError())
        return
      }
      const timer = window.setTimeout(() => {
        compileFromEndpoint(apiUrl).then(resolve, reject)
      }, delay)
      masterController.signal.addEventListener(
        'abort',
        () => {
          window.clearTimeout(timer)
          reject(abortError())
        },
        { once: true }
      )
    })
  }

  try {
    const result = await Promise.any(endpoints.map((apiUrl, index) => compileAfterStagger(apiUrl, index)))
    endpoints.slice(0, endpoints.indexOf(result.apiUrl)).forEach(markEndpointFailure)
    markEndpointSuccess(result.apiUrl)
    masterController.abort()
    return result
  } catch (error) {
    if (masterController.signal.aborted) throw abortError()
    const lastError = error?.errors?.find((candidate) => candidate?.name !== 'AbortError') || error
    throw new Error(lastError?.message || 'Compiler API unavailable.')
  } finally {
    if (externalSignal) externalSignal.removeEventListener('abort', onExternalAbort)
  }
}
