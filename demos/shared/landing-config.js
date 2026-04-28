const runtimeConfig = window.VIEWSPEC_LANDING_CONFIG || {}
const configuredApiKey = runtimeConfig.publicApiKey || window.PUBLIC_LANDING_API_KEY || ''

export const LANDING_CONFIG = {
  apiUrl: runtimeConfig.apiUrl || 'https://api.viewspec.dev/v1/compile',
  publicApiKey: configuredApiKey,
  proStripeUrl: runtimeConfig.proStripeUrl || 'https://buy.stripe.com/7sY00i9v67cJebDd1K1oI00',
  scaleStripeUrl: runtimeConfig.scaleStripeUrl || 'https://buy.stripe.com/4gM6oGcHi68FgjLd1K1oI01',
  signupUrl: runtimeConfig.signupUrl || 'https://viewspec.dev/#pricing',
  requestTimeoutMs: Number(runtimeConfig.requestTimeoutMs || 6000),
}

export function hasLiveApiConfig() {
  return Boolean(LANDING_CONFIG.apiUrl && !LANDING_CONFIG.apiUrl.includes('REPLACE_WITH'))
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
  return [LANDING_CONFIG.proStripeUrl, LANDING_CONFIG.scaleStripeUrl, LANDING_CONFIG.signupUrl].every((value) => {
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
