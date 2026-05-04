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
  // TODO: swap to the new $149/mo Pro Payment Link before launch.
  // Current default is the legacy $699 product; the pricing card already
  // shows $149, so clicking "Get Pro" today reaches a wrong-priced checkout.
  // Update this URL the moment the new Stripe Payment Link is live.
  proStripeUrl: runtimeConfig.proStripeUrl || 'https://buy.stripe.com/7sY00i9v67cJebDd1K1oI00',
  // Enterprise CTA: real Stripe link if/when one exists, otherwise contact via
  // GitHub issues. `scaleStripeUrl` was a legacy alias with no consumers and
  // has been removed; `signupUrl` was unused (no [data-config-link="signup"]
  // exists in the HTML) and has been removed too.
  enterpriseUrl: runtimeConfig.enterpriseUrl || 'https://github.com/nxrobins/viewspec/issues',
  requestTimeoutMs: Number(runtimeConfig.requestTimeoutMs || 6000),
  endpointStaggerMs: Number(runtimeConfig.endpointStaggerMs || 120),
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
