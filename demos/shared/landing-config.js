const runtimeConfig = window.VIEWSPEC_LANDING_CONFIG || {}
const configuredApiKey = runtimeConfig.publicApiKey || window.PUBLIC_LANDING_API_KEY || ''
const primaryApiUrl = runtimeConfig.apiUrl || 'https://api.viewspec.dev/v1/compile'

function urlList(value) {
  if (!value) return []
  return Array.isArray(value) ? value : [value]
}

const fallbackApiUrls = runtimeConfig.fallbackApiUrls
  ? urlList(runtimeConfig.fallbackApiUrls)
  : ['https://viewspec-api.fly.dev/v1/compile']

function uniqueUrls(urls) {
  return [...new Set(urls.filter((value) => value && !value.includes('REPLACE_WITH')))]
}

export const LANDING_CONFIG = {
  apiUrl: primaryApiUrl,
  apiUrls: uniqueUrls([primaryApiUrl, ...fallbackApiUrls]),
  publicApiKey: configuredApiKey,
  proStripeUrl: runtimeConfig.proStripeUrl || 'https://buy.stripe.com/6oU4gA6PqcM9afq6qq2Z0b8',
  enterpriseUrl: runtimeConfig.enterpriseUrl || 'mailto:hello@viewspec.dev?subject=ViewSpec%20Enterprise',
  requestTimeoutMs: Number(runtimeConfig.requestTimeoutMs || 6000),
  endpointStaggerMs: Number(runtimeConfig.endpointStaggerMs || 50),
  endpointFailureTtlMs: Number(runtimeConfig.endpointFailureTtlMs || 300000),
}

export function hasLiveApiConfig() {
  return LANDING_CONFIG.apiUrls.length > 0
}

export function hasPublicApiKey() {
  return Boolean(
    LANDING_CONFIG.publicApiKey &&
      !LANDING_CONFIG.publicApiKey.includes('REPLACE_WITH') &&
      !LANDING_CONFIG.publicApiKey.includes('YOUR_')
  )
}

export function compileRequestHeaders() {
  const headers = {
    'Content-Type': 'application/json',
  }
  if (hasPublicApiKey()) headers.Authorization = `Bearer ${LANDING_CONFIG.publicApiKey}`
  return headers
}

export function hasProductionCommerceConfig() {
  return [LANDING_CONFIG.proStripeUrl, LANDING_CONFIG.enterpriseUrl].every((value) => {
    return value && !value.includes('REPLACE_WITH') && !value.includes('YOUR_')
  })
}

export function redactedCompileRequestHeaders() {
  const headers = {
    'Content-Type': 'application/json',
  }
  if (hasPublicApiKey()) headers.Authorization = 'Bearer ***REDACTED***'
  return headers
}
