const runtimeConfig = window.VIEWSPEC_LANDING_CONFIG || {}

export const LANDING_CONFIG = {
  apiUrl: runtimeConfig.apiUrl || 'https://api.viewspec.dev/v1/compile',
  publicApiKey: runtimeConfig.publicApiKey || window.PUBLIC_LANDING_API_KEY || 'pk_live_REPLACE_WITH_PUBLIC_LANDING_KEY',
  proStripeUrl: runtimeConfig.proStripeUrl || 'https://buy.stripe.com/7sY00i9v67cJebDd1K1oI00',
  scaleStripeUrl: runtimeConfig.scaleStripeUrl || 'https://buy.stripe.com/4gM6oGcHi68FgjLd1K1oI01',
  signupUrl: runtimeConfig.signupUrl || 'https://viewspec.dev/#pricing',
  requestTimeoutMs: Number(runtimeConfig.requestTimeoutMs || 6000),
}

export function hasLiveApiConfig() {
  return Boolean(
    LANDING_CONFIG.publicApiKey &&
      !LANDING_CONFIG.publicApiKey.includes('REPLACE_WITH') &&
      !LANDING_CONFIG.publicApiKey.includes('YOUR_')
  )
}

export function hasProductionCommerceConfig() {
  return [LANDING_CONFIG.proStripeUrl, LANDING_CONFIG.scaleStripeUrl, LANDING_CONFIG.signupUrl].every((value) => {
    return value && !value.includes('REPLACE_WITH') && !value.includes('YOUR_')
  })
}

export function redactedAuthorizationHeader() {
  return 'Bearer pk_live_***REDACTED***'
}
