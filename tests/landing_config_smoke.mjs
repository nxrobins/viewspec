import assert from 'node:assert/strict'
import { readFile, writeFile, mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const source = await readFile(resolve('demos/shared/landing-config.js'), 'utf8')

async function loadConfig(windowConfig = {}) {
  const tempDir = await mkdtemp(join(tmpdir(), 'viewspec-landing-config-'))
  const modulePath = join(tempDir, 'landing-config.mjs')
  await writeFile(
    modulePath,
    `globalThis.window = { VIEWSPEC_LANDING_CONFIG: ${JSON.stringify(windowConfig)} };\n${source}`,
    'utf8'
  )
  return import(pathToFileURL(modulePath).href)
}

const { LANDING_CONFIG, hasLiveApiConfig } = await loadConfig()

assert.equal(LANDING_CONFIG.apiUrl, 'https://api.viewspec.dev/v1/compile')
assert(LANDING_CONFIG.apiUrls.includes('https://api.viewspec.dev/v1/compile'))
assert(LANDING_CONFIG.apiUrls.includes('https://viewspec-api.fly.dev/v1/compile'))
assert.equal(LANDING_CONFIG.endpointStaggerMs, 120)
assert.equal(LANDING_CONFIG.endpointFailureTtlMs, 300000)
assert.equal(LANDING_CONFIG.enterpriseUrl, 'https://github.com/nxrobins/viewspec/issues')
// `scaleStripeUrl` and `signupUrl` were dropped in the site bug sweep
// (b7d5b96) because nothing in the HTML referenced them. Re-asserting them
// here is what kept SDK Reliability red on every commit since that merge;
// the test now matches the surface that actually exists on LANDING_CONFIG.
assert.equal(LANDING_CONFIG.scaleStripeUrl, undefined)
assert.equal(LANDING_CONFIG.signupUrl, undefined)
assert.equal(new Set(LANDING_CONFIG.apiUrls).size, LANDING_CONFIG.apiUrls.length)
assert.equal(hasLiveApiConfig(), true)

const customCommerce = await loadConfig({ enterpriseUrl: 'https://enterprise.test/contact' })
assert.equal(customCommerce.LANDING_CONFIG.enterpriseUrl, 'https://enterprise.test/contact')

const custom = await loadConfig({ apiUrl: 'https://example.test/v1/compile', fallbackApiUrls: 'https://fallback.test/v1/compile' })
assert.deepEqual(custom.LANDING_CONFIG.apiUrls, [
  'https://example.test/v1/compile',
  'https://fallback.test/v1/compile',
])

console.log(`Validated ${LANDING_CONFIG.apiUrls.length} landing API endpoint candidates.`)
