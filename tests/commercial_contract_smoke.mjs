import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const [facts, openapi, config, source, built] = await Promise.all([
  readFile(resolve('demos/public-facts.json'), 'utf8').then(JSON.parse),
  readFile(resolve('demos/openapi.json'), 'utf8').then(JSON.parse),
  readFile(resolve('demos/shared/landing-config.js'), 'utf8'),
  readFile(resolve('demos/build_landing.py'), 'utf8'),
  readFile(resolve('demos/index.html'), 'utf8'),
])

const pro = facts.pricing.pro
assert.equal(pro.price_usd_month, 149)
assert.equal(pro.hosted_compile_calls_per_day, 10_000)
assert.equal(pro.custom_motif_instances_per_compile, 5)
assert.deepEqual(pro.artifact_targets, ['html-tailwind', 'react-tsx', 'swiftui', 'flutter'])
assert.equal(pro.signed_usage_receipts, true)
assert.equal(openapi['x-viewspec-public-facts'].proPriceUsdMonth, pro.price_usd_month)
assert.equal(openapi['x-viewspec-public-facts'].proHostedCompileCallsPerDay, pro.hosted_compile_calls_per_day)
for (const path of ['/v1/compile', '/v1/plans', '/v1/artifacts', '/v1/app-bundles/build', '/v1/usage', '/v1/receipt-key', '/v1/checkout/claim', '/v1/keys/rotate', '/v1/keys/current']) {
  assert(Object.hasOwn(openapi.paths, path), `OpenAPI is missing ${path}`)
}
assert(config.includes('https://buy.stripe.com/6oU4gA6PqcM9afq6gq2ZO00'))

for (const page of [source, built]) {
  assert(page.includes('10,000 hosted compile calls/day'))
  assert(page.includes('Up to 5 custom motifs per compile'))
  assert(page.includes('HTML, React, SwiftUI, and Flutter artifacts'))
  assert(page.includes('Signed team usage receipts'))
}

console.log('Validated public pricing, checkout, and current hosted capabilities.')
