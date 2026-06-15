import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile, stat } from 'node:fs/promises'

const pages = [
  ['demos/index.html', 'https://viewspec.dev/'],
  ['demos/cross-platform-dashboard/index.html', 'https://viewspec.dev/cross-platform-dashboard/'],
  ['demos/custom-motifs/index.html', 'https://viewspec.dev/custom-motifs/'],
  ['demos/interactive-compose/index.html', 'https://viewspec.dev/interactive-compose/'],
  ['demos/stateful-collections/index.html', 'https://viewspec.dev/stateful-collections/'],
  ['demos/motif-switcher/index.html', 'https://viewspec.dev/motif-switcher/'],
  ['demos/provenance-inspector/index.html', 'https://viewspec.dev/provenance-inspector/'],
  ['demos/live-builder/index.html', 'https://viewspec.dev/live-builder/'],
  ['demos/invariants/index.html', 'https://viewspec.dev/invariants/'],
  ['demos/proof-bundle/index.html', 'https://viewspec.dev/proof-bundle/'],
  ['demos/fifteen-lines/index.html', 'https://viewspec.dev/fifteen-lines/'],
  ['demos/style-derivation/index.html', 'https://viewspec.dev/style-derivation/'],
  ['demos/aesthetic-profiles/index.html', 'https://viewspec.dev/aesthetic-profiles/'],
]

function extractJsonLd(html) {
  return [...html.matchAll(/<script type="application\/ld\+json">\s*([\s\S]*?)\s*<\/script>/g)].map((match) =>
    JSON.parse(match[1])
  )
}

function extractTaggedStyle(html, marker) {
  const escaped = marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = html.match(new RegExp(`<style ${escaped}>\\s*([\\s\\S]*?)\\s*</style>`))
  return match ? match[1] : ''
}

function extractScriptJson(html, id) {
  const escaped = id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = html.match(new RegExp(`<script type="application/json" id="${escaped}">\\s*([\\s\\S]*?)\\s*</script>`))
  assert(match, `${id} JSON script is missing`)
  return JSON.parse(match[1])
}

function sha256(text) {
  return createHash('sha256').update(text).digest('hex')
}

for (const [file, canonical] of pages) {
  const html = await readFile(file, 'utf8')
  assert.match(html, /<title>ViewSpec/, `${file} needs a ViewSpec title`)
  assert.match(html, /<meta name="description" content="[^"]{80,220}">/, `${file} needs a useful meta description`)
  assert.match(html, new RegExp(`<link rel="canonical" href="${canonical.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}">`))
  assert.match(html, /<meta name="robots" content="index,follow,max-snippet:-1,max-image-preview:large">/)
  assert.match(html, /href="https:\/\/viewspec\.dev\/llms\.txt"/)
  assert.match(html, /href="https:\/\/viewspec\.dev\/openapi\.json"/)
  assert.doesNotMatch(html, /const btn = e\.target\.closest\('\[data-action-id\]'\)/, `${file} has stale global action runtime`)
  assert.doesNotMatch(html, /console\.log\('Action Dispatched:/, `${file} has debug action logging`)
  assert.doesNotMatch(html, /document\.querySelectorAll\('\[data-binding-id\]'\)/, `${file} has document-wide payload collection`)
  assert.doesNotMatch(html, /payloadBindings:\s*JSON\.parse/, `${file} has throwing action payload parsing`)
  if (html.includes('viewspec-action')) {
    assert.match(html, /schemaVersion: 1/, `${file} action runtime needs a versioned event payload`)
    assert.match(html, /source: 'viewspec-html-tailwind'/, `${file} action runtime needs a stable source marker`)
  }

  const jsonLd = extractJsonLd(html)
  assert(jsonLd.length > 0, `${file} needs JSON-LD`)
}

const home = await readFile('demos/index.html', 'utf8')
const publicFacts = JSON.parse(await readFile('demos/public-facts.json', 'utf8'))

function publicFactDrift(message) {
  assert.fail(`PUBLIC_FACTS_DRIFT: ${message}`)
}

function statefulCollectionsDrift(message) {
  assert.fail(`STATEFUL_COLLECTIONS_PUBLIC_CONTRACT_DRIFT: ${message}`)
}

function assertPublicText(text, expected, label) {
  if (!text.includes(String(expected))) publicFactDrift(`${label} missing ${expected}`)
}

function assertPublicEqual(actual, expected, label) {
  if (actual !== expected) publicFactDrift(`${label} expected ${expected}, got ${actual}`)
}

const proPrice = `$${publicFacts.pricing.pro.price_usd_month}`
const proCalls = publicFacts.pricing.pro.hosted_compile_calls_per_day.toLocaleString('en-US')
const freeCalls = String(publicFacts.pricing.free.hosted_compile_calls_per_day)

assertPublicEqual(publicFacts.schema_version, 1, 'public facts schema_version')
assert.match(publicFacts.sdk_version, /^\d+\.\d+\.\d+(?:[a-z]+\d+)?$/, 'public facts sdk_version shape')
assertPublicEqual(publicFacts.canonical_api_url, 'https://api.viewspec.dev/v1/compile', 'public facts canonical_api_url')
assertPublicEqual(publicFacts.package_url, 'https://pypi.org/project/viewspec/', 'public facts package_url')
assertPublicEqual(publicFacts.proof.first_proof_command, 'viewspec prove --out .viewspec-proof', 'public facts first proof command')
assertPublicEqual(publicFacts.proof.human_summary_file, '.viewspec-proof/PROOF.md', 'public facts proof summary file')
assertPublicEqual(publicFacts.proof.machine_report_file, '.viewspec-proof/proof_report.json', 'public facts proof report file')
assertPublicEqual(publicFacts.proof.support_bundle_file, '.viewspec-proof/support_bundle.json', 'public facts proof support bundle file')
assertPublicText(publicFacts.proof.non_claim, 'not pixel-perfect visual regression', 'public facts proof non-claim')

const aestheticProfileTokens = [
  'aesthetic.calm_ops',
  'aesthetic.premium_saas',
  'aesthetic.data_dense',
  'aesthetic.editorial_product',
  'aesthetic.executive_review',
]
assert.deepEqual(publicFacts.aesthetic_profiles.tokens, aestheticProfileTokens, 'public facts aesthetic profile tokens')
assertPublicText(publicFacts.aesthetic_profiles.scope, 'deterministic view-level art-direction handles', 'public facts aesthetic scope')
assertPublicText(publicFacts.aesthetic_profiles.non_claim, 'not arbitrary CSS', 'public facts aesthetic non-claim')

const pyproject = await readFile('pyproject.toml', 'utf8')
const versionModule = await readFile('src/viewspec/_version.py', 'utf8')
assertPublicText(pyproject, `version = "${publicFacts.sdk_version}"`, 'pyproject version')
assertPublicText(versionModule, `__version__ = "${publicFacts.sdk_version}"`, 'runtime version')

for (const publicTextPath of ['README.md', 'docs/getting-started.md', 'demos/llms.txt', 'demos/llms-full.txt']) {
  const text = await readFile(publicTextPath, 'utf8')
  assertPublicText(text, publicFacts.proof.first_proof_command, `${publicTextPath} first proof`)
  assertPublicText(text, 'PROOF.md', `${publicTextPath} proof summary`)
  assertPublicText(text, 'support_bundle.json', `${publicTextPath} proof support bundle`)
  assertPublicText(text, 'proof-bundle.md', `${publicTextPath} proof guide`)
  assertPublicText(text, publicFacts.proof.non_claim.split(',')[0], `${publicTextPath} proof scope`)
}

for (const aestheticTextPath of ['README.md', 'docs/getting-started.md', 'docs/agent-integration.md', 'docs/free-sdk-reliability.md', 'demos/llms.txt', 'demos/llms-full.txt']) {
  const text = await readFile(aestheticTextPath, 'utf8')
  for (const token of aestheticProfileTokens) {
    assertPublicText(text, token, `${aestheticTextPath} aesthetic profile token`)
  }
  if (!text.includes('not CSS') && !text.includes('not arbitrary CSS')) {
    publicFactDrift(`${aestheticTextPath} missing aesthetic profile non-css scope`)
  }
}

for (const proofTextPath of ['README.md', 'docs/getting-started.md', 'docs/agent-integration.md', 'demos/index.html', 'demos/proof-bundle/index.html', 'demos/llms.txt', 'demos/llms-full.txt']) {
  const text = await readFile(proofTextPath, 'utf8')
  if (text.includes('proof_report.json') && !text.includes('PROOF.md')) {
    publicFactDrift(`${proofTextPath} mentions proof_report.json without PROOF.md`)
  }
  if (text.includes('proof_report.json') && !text.includes('support_bundle.json')) {
    publicFactDrift(`${proofTextPath} mentions proof_report.json without support_bundle.json`)
  }
}

for (const hostProofTextPath of ['README.md', 'docs/getting-started.md', 'docs/free-sdk-reliability.md', 'docs/known-limits-react-tailwind-tsx.md']) {
  const text = await readFile(hostProofTextPath, 'utf8')
  assertPublicText(text, 'profiled aesthetic marker', `${hostProofTextPath} host proof aesthetic marker scope`)
  assertPublicText(text, 'action payload', `${hostProofTextPath} host proof action payload scope`)
}

for (const productTextPath of ['README.md', 'demos/index.html', 'demos/llms.txt', 'demos/llms-full.txt']) {
  const productText = await readFile(productTextPath, 'utf8')
  assertPublicText(productText, proPrice, `${productTextPath} pro price`)
  assertPublicText(productText, proCalls, `${productTextPath} pro hosted calls`)
}
for (const productTextPath of ['README.md', 'demos/index.html', 'demos/llms.txt', 'demos/llms-full.txt']) {
  const productText = await readFile(productTextPath, 'utf8')
  assertPublicText(productText, freeCalls, `${productTextPath} free hosted calls`)
}
assertPublicText(await readFile('README.md', 'utf8'), publicFacts.package_url, 'README package URL')
assertPublicText(home, publicFacts.proof.first_proof_command, 'landing first proof')
assertPublicText(home, 'PROOF.md', 'landing proof summary')
assertPublicText(home, 'support_bundle.json', 'landing support bundle')
assertPublicText(home, './proof-bundle/', 'landing proof bundle guide link')
assertPublicText(home, publicFacts.proof.non_claim.split(',')[0], 'landing proof scope')

const proofBundlePage = await readFile('demos/proof-bundle/index.html', 'utf8')
for (const expected of [
  publicFacts.proof.first_proof_command,
  'PROOF.md',
  'proof_report.json',
  'support_bundle.json',
  'source_artifact',
  'react_tailwind_reference_host',
  'Hashes',
  'Checks',
  'Errors',
  publicFacts.proof.non_claim,
]) {
  assertPublicText(proofBundlePage, expected, 'proof bundle page')
}
assertPublicText(await readFile('demos/llms.txt', 'utf8'), 'https://viewspec.dev/proof-bundle/', 'llms proof bundle public URL')
assertPublicText(await readFile('demos/llms-full.txt', 'utf8'), 'https://viewspec.dev/proof-bundle/', 'llms-full proof bundle public URL')

const statefulCollectionsPage = await readFile('demos/stateful-collections/index.html', 'utf8')
for (const expected of [
  'Stateful Collections Desk',
  'data-action-id="search_incidents"',
  'data-action-kind="bulk_action"',
  'role="status"',
  'aria-busy="true"',
  'role="alert"',
  'Generated buttons dispatch ViewSpec events only',
]) {
  assertPublicText(statefulCollectionsPage, expected, 'stateful collections demo')
}

const aestheticProfilesPage = await readFile('demos/aesthetic-profiles/index.html', 'utf8')
for (const expected of [
  'Same Intent, Five Art Directions',
  'Beauty as a checked compiler handle',
  'deterministic art direction, not CSS',
  'semantic ids stable',
  'data-presentation-contract="light-gallery-showroom"',
  '0 shell overrides',
  'Demo shell CSS frames the page only',
  'data-aesthetic-profile',
  'aesthetic-profile-proof',
  'active-layout-signature',
  'bounded grid metadata',
]) {
  assertPublicText(aestheticProfilesPage, expected, 'aesthetic profiles demo')
}
for (const token of aestheticProfileTokens) {
  assertPublicText(aestheticProfilesPage, token, 'aesthetic profiles demo token')
}
assert.equal((aestheticProfilesPage.match(/class="profile-card"/g) || []).length, 5)
const aestheticProof = extractScriptJson(aestheticProfilesPage, 'aesthetic-profile-proof')
assert.deepEqual(Object.keys(aestheticProof).sort(), [...aestheticProfileTokens].sort(), 'aesthetic profile proof tokens')
const expectedAestheticLayout = {
  'aesthetic.calm_ops': [2, 2, null],
  'aesthetic.premium_saas': [2, 2, 2],
  'aesthetic.data_dense': [3, 3, null],
  'aesthetic.editorial_product': [2, 1, null],
  'aesthetic.executive_review': [2, 2, 2],
}
for (const [token, [workspaceColumns, metricColumns, metricSpan]] of Object.entries(expectedAestheticLayout)) {
  assert.equal(aestheticProof[token].layoutProof.content_grid.columns, workspaceColumns, `${token} workspace columns`)
  assert.equal(aestheticProof[token].layoutProof.metric_grid.columns, metricColumns, `${token} metric columns`)
  assert.equal(aestheticProof[token].layoutProof.content_grid.profile, token, `${token} content grid profile marker`)
  assert.equal(aestheticProof[token].layoutProof.metric_grid.profile, token, `${token} metric grid profile marker`)
  if (metricSpan === null) {
    assert.equal(aestheticProof[token].layoutProof.metric_card, undefined, `${token} metric card span`)
  } else {
    assert.equal(aestheticProof[token].layoutProof.metric_card.spanColumns, metricSpan, `${token} metric card span`)
    assert.equal(aestheticProof[token].layoutProof.metric_card.profile, token, `${token} metric card profile marker`)
  }
  assert.equal(
    aestheticProof[token].layoutSignature,
    `workspace ${workspaceColumns} / metrics ${metricColumns}${metricSpan === null ? '' : ` / featured metric span ${metricSpan}`}`,
    `${token} layout signature`
  )
}
const aestheticShellCss = extractTaggedStyle(aestheticProfilesPage, 'data-demo-shell-css="true"')
if (!aestheticShellCss) publicFactDrift('aesthetic profiles demo missing tagged shell CSS')
assert.doesNotMatch(aestheticShellCss, /color-scheme:\s*dark/, 'aesthetic profiles shell should stay in the light gallery presentation')
assert.match(aestheticShellCss, /\.artifact-frame\s*\{[\s\S]*background: #ffffff/, 'aesthetic profiles shell needs a white artifact showroom frame')
if (/\.vs-|data-ir-id/.test(aestheticShellCss)) {
  assert.fail('AESTHETIC_DEMO_BYPASS: aesthetic profiles shell CSS styles generated artifact internals')
}

// Three install pills: nav + hero + footer. The footer pill was added in
// the site bug sweep (b7d5b96); the test was previously asserting on the
// pre-bug-sweep count of 2.
assert.equal((home.match(/data-copy-text="pip install viewspec"/g) || []).length, 3)
assert.match(home, /aria-label="Copy pip install viewspec command"/)
const homeJsonLd = extractJsonLd(home)
const graph = homeJsonLd.find((entry) => Array.isArray(entry['@graph']))?.['@graph'] || []
assert(graph.some((entry) => entry['@type'] === 'SoftwareApplication'), 'home JSON-LD needs SoftwareApplication')
assert(graph.some((entry) => entry['@type'] === 'WebAPI'), 'home JSON-LD needs WebAPI')
assert(graph.some((entry) => entry['@type'] === 'FAQPage'), 'home JSON-LD needs FAQPage')

const landingCompiledBytes = await readFile('demos/landing-compiled/index.html')
const landingCompiledHtml = landingCompiledBytes.toString('utf8')
const landingCompiledManifest = JSON.parse(await readFile('demos/landing-compiled/provenance_manifest.json', 'utf8'))
assert.equal(landingCompiledManifest.kind, 'intent_bundle_compile')
assert.equal(landingCompiledManifest.command, 'compile')
assert.equal(landingCompiledManifest.policy_version, 'viewspec-intent-bundle@1')
assert.equal(landingCompiledManifest.guarantees?.decompilation, 'not_applicable')
assert.equal(landingCompiledManifest.artifact_hash, sha256(landingCompiledBytes))
assert(Array.isArray(landingCompiledManifest.diagnostics), 'landing compiled manifest needs diagnostics array')
assert.equal(landingCompiledManifest.external_refs.length, 0)

const robots = await readFile('demos/robots.txt', 'utf8')
assert.match(robots, /User-agent: \*/)
assert.match(robots, /Sitemap: https:\/\/viewspec\.dev\/sitemap\.xml/)

const sitemap = await readFile('demos/sitemap.xml', 'utf8')
for (const [, canonical] of pages) {
  assert.match(sitemap, new RegExp(`<loc>${canonical.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}</loc>`))
}

const llms = await readFile('demos/llms.txt', 'utf8')
assert.match(llms, /agent-native UI compiler/i)
assert.match(llms, /viewspec init-intent/)
assert.match(llms, /viewspec validate-intent/)
assert.match(llms, /agentic engineering/i)
assert.match(llms, /https:\/\/api\.viewspec\.dev\/v1\/compile/)
assert.doesNotMatch(llms, /\$699|699\/mo/)
for (const file of ['demos/llms-full.txt', 'demos/openapi.json', 'demos/cross-platform-dashboard/index.html']) {
  const text = await readFile(file, 'utf8')
  assert.doesNotMatch(text, /agent-native UI IR/i, `${file} should describe IntentBundle/compiler, not IR as source`)
  assert.doesNotMatch(text, /semantic UI IR/i, `${file} should describe the compiler contract, not IR as product source`)
}

const landing = await readFile('demos/index.html', 'utf8')
assert.doesNotMatch(landing, /agent-native UI IR, agent-native UI IR/)
assert.doesNotMatch(landing, /Your agent writes HTML/)
assert.doesNotMatch(landing, /agent HTML governance first/)
assert.doesNotMatch(landing, /agent HTML governance/)
assert.match(landing, /Stop asking agents to write DOM/)
assert.match(landing, /viewspec init-intent/)
assert.match(landing, /viewspec validate-intent/)
assert.doesNotMatch(landing, /\"price\": \"2500\"/)
// Pricing CTAs keep direct href fallbacks for crawlers and data-config-link
// hooks so window.VIEWSPEC_LANDING_CONFIG can override destinations at runtime.
assert.match(landing, /href=\"https:\/\/buy\.stripe\.com\//)
assert.match(landing, /href=\"mailto:hello@viewspec\.dev\?subject=ViewSpec%20Enterprise\"/)
assert.match(landing, /data-config-link=\"pro\"/)
assert.match(landing, /data-config-link=\"enterprise\"/)
assert.doesNotMatch(landing, /\$699|699\/mo|7sY00i9v67cJebDd1K1oI00/)

for (const productTextPath of ['README.md', 'demos/llms-full.txt', 'demos/shared/landing-config.js']) {
  const productText = await readFile(productTextPath, 'utf8')
  assert.doesNotMatch(
    productText,
    /\$699|699\/mo|25,000 hosted compile|10,000 hosted renders|7sY00i9v67cJebDd1K1oI00/,
    `${productTextPath} has stale Pro pricing`
  )
}

const openapi = JSON.parse(await readFile('demos/openapi.json', 'utf8'))
assert.equal(openapi.openapi, '3.1.0')
assert.equal(openapi.servers[0].url, 'https://api.viewspec.dev')
assertPublicEqual(openapi['x-viewspec-public-facts'].manifest, 'https://viewspec.dev/public-facts.json', 'OpenAPI public facts manifest')
assertPublicEqual(openapi['x-viewspec-public-facts'].sdkVersion, publicFacts.sdk_version, 'OpenAPI public facts sdkVersion')
assertPublicEqual(openapi['x-viewspec-public-facts'].proPriceUsdMonth, publicFacts.pricing.pro.price_usd_month, 'OpenAPI public facts proPriceUsdMonth')
assertPublicEqual(openapi['x-viewspec-public-facts'].proHostedCompileCallsPerDay, publicFacts.pricing.pro.hosted_compile_calls_per_day, 'OpenAPI public facts pro calls')
assertPublicEqual(openapi['x-viewspec-public-facts'].firstProofCommand, publicFacts.proof.first_proof_command, 'OpenAPI public facts first proof')
assertPublicEqual(openapi['x-viewspec-public-facts'].proofSummaryFile, publicFacts.proof.human_summary_file, 'OpenAPI public facts proof summary')
assertPublicEqual(openapi['x-viewspec-public-facts'].proofReportFile, publicFacts.proof.machine_report_file, 'OpenAPI public facts proof report')
assertPublicEqual(openapi['x-viewspec-public-facts'].proofSupportBundleFile, publicFacts.proof.support_bundle_file, 'OpenAPI public facts proof support bundle')
assert(openapi.paths['/v1/compile']?.post, 'OpenAPI needs POST /v1/compile')
assert.equal(openapi.paths['/v1/compile'].post.requestBody.content['application/json'].schema.$ref, '#/components/schemas/CompileRequestPayload')
assert.equal(openapi.components.schemas.CompileRequestPayload.properties.design.$ref, '#/components/schemas/DesignRequest')
assert(!('design' in openapi.components.schemas.IntentBundle.properties), 'OpenAPI IntentBundle schema should not absorb hosted design context')
assert.equal(openapi['x-viewspec-agent-artifacts'].assetSchemaVersion, 3)
assert.equal(openapi['x-viewspec-agent-artifacts'].assetManifest, 'https://viewspec.dev/agent-assets.json')
assert.equal(openapi['x-viewspec-agent-artifacts'].systemPrompt, 'https://viewspec.dev/agent-system-prompt.txt')
assert.equal(openapi['x-viewspec-agent-artifacts'].intentBundleExample, 'https://viewspec.dev/agent-intent-example.dashboard.json')

const agentPrompt = await readFile('demos/agent-system-prompt.txt', 'utf8')
assert.match(agentPrompt, /IntentBundle/)
assert.match(agentPrompt, /CompositionIR is compiler output only/)
assert.match(agentPrompt, /Generated JSON is not a finished ViewSpec proof/)
assert.match(agentPrompt, /viewspec prove --out \.viewspec-proof/)
assert.match(agentPrompt, /\.viewspec-proof\/PROOF\.md/)
assert.match(agentPrompt, /proof_report\.json/)
assert.match(agentPrompt, /support_bundle\.json/)
assert.match(agentPrompt, /pixel-perfect visual regression/)
assert.doesNotMatch(agentPrompt, /You output ViewSpec IR/)

const agentSchema = JSON.parse(await readFile('demos/agent-intent-bundle.schema.json', 'utf8'))
assert.deepEqual(agentSchema.$defs.motif.properties.kind.enum, ['table', 'dashboard', 'outline', 'comparison', 'list', 'form', 'detail', 'empty_state', 'loading_state', 'error_state', 'hero'])
assert.deepEqual(agentSchema.$defs.action.properties.kind.enum, ['select', 'submit', 'navigate', 'search', 'filter', 'sort', 'paginate', 'bulk_action'])
for (const publicTextPath of ['README.md', 'docs/getting-started.md', 'docs/agent-integration.md', 'demos/llms.txt', 'demos/llms-full.txt']) {
  const text = await readFile(publicTextPath, 'utf8')
  for (const expected of ['loading_state', 'error_state', 'search', 'filter', 'sort', 'paginate', 'bulk_action']) {
    if (!text.includes(expected)) statefulCollectionsDrift(`${publicTextPath} missing ${expected}`)
  }
}
const agentManifest = JSON.parse(await readFile('demos/agent-assets.json', 'utf8'))
assert.equal(agentManifest.schema_version, 3)
assert.deepEqual(agentManifest.files.map((file) => file.path), ['agent-system-prompt.txt', 'agent-intent-bundle.schema.json', 'agent-intent-example.dashboard.json'])
const agentExample = JSON.parse(await readFile('demos/agent-intent-example.dashboard.json', 'utf8'))
assert.equal(agentExample.view_spec.motifs[0].kind, 'dashboard')
assert.equal(agentExample.view_spec.substrate_id, agentExample.substrate.id)

const artifactIndex = JSON.parse(await readFile('demos/cross-platform-dashboard/artifacts/artifact_index.json', 'utf8'))
assert.equal(artifactIndex.prompt, 'agent_prompt.txt')
assert.equal(artifactIndex.contract_profile, 'hosted_extended_v1')
assert.match(artifactIndex.contract_profile_description, /inputs, rules, projections/)
assert.doesNotMatch(JSON.stringify(artifactIndex), /\.test-tmp/)

async function readUtf8NoBom(path) {
  const text = await readFile(path, 'utf8')
  assert(!text.startsWith('\uFEFF'), `${path} must not start with a UTF-8 BOM`)
  return text
}

for (const artifactPath of [
  'demos/cross-platform-dashboard/artifacts/agent_prompt.txt',
  'demos/cross-platform-dashboard/artifacts/artifact_index.json',
  'demos/cross-platform-dashboard/artifacts/ast_bundle.json',
  'demos/cross-platform-dashboard/artifacts/intent_bundle.json',
  'demos/cross-platform-dashboard/artifacts/flutter/provenance_manifest.json',
  'demos/cross-platform-dashboard/artifacts/flutter/viewspec_view.dart',
  'demos/cross-platform-dashboard/artifacts/html/index.html',
  'demos/cross-platform-dashboard/artifacts/html/provenance_manifest.json',
  'demos/cross-platform-dashboard/artifacts/react-tsx/ViewSpecView.tsx',
  'demos/cross-platform-dashboard/artifacts/react-tsx/provenance_manifest.json',
  'demos/cross-platform-dashboard/artifacts/swiftui/ViewSpecView.swift',
  'demos/cross-platform-dashboard/artifacts/swiftui/provenance_manifest.json',
]) {
  await readUtf8NoBom(artifactPath)
}

const launchHtml = await readUtf8NoBom('demos/cross-platform-dashboard/artifacts/html/index.html')
const launchTsx = await readUtf8NoBom('demos/cross-platform-dashboard/artifacts/react-tsx/ViewSpecView.tsx')
const launchSwift = await readUtf8NoBom('demos/cross-platform-dashboard/artifacts/swiftui/ViewSpecView.swift')
const launchFlutter = await readUtf8NoBom('demos/cross-platform-dashboard/artifacts/flutter/viewspec_view.dart')
const launchIntent = await readUtf8NoBom('demos/cross-platform-dashboard/artifacts/intent_bundle.json')
const launchIntentJson = JSON.parse(launchIntent)
assert.doesNotMatch(launchHtml, /style="[^"]*\bscale:\s*[^;]+;[^"]*\bscale:/)
assert.doesNotMatch(launchTsx, /\bscale:\s*"[^"]+",[^}]*\bscale:/)
assert.doesNotMatch(launchIntent, /"target_ref"\s*:\s*"\/[^"]+"/)
assert.match(launchIntent, /"target_ref"\s*:\s*"view:launch_operations_dashboard"/)
assert.match(launchHtml, /data-action-target-ref="view:launch_operations_dashboard"/)
assert.match(launchHtml, /schemaVersion: 1/)
assert.match(launchHtml, /source: 'viewspec-html-tailwind'/)
assert.doesNotMatch(launchHtml, /const payloadBindings = JSON\.parse/)
assert.match(launchHtml, /payloadValues/)
assert.match(launchTsx, /targetRef: "view:launch_operations_dashboard"/)
assert.match(launchTsx, /schemaVersion: 1/)
assert.match(launchTsx, /source: "viewspec-react-tsx"/)
assert.match(launchTsx, /payloadValues: collectPayloadValues/)
assert.doesNotMatch(launchTsx, /payload: collectPayload/)
assert.match(launchSwift, /schemaVersion: 1/)
assert.match(launchSwift, /source: "viewspec-swiftui"/)
assert.match(launchSwift, /payloadValues: collectPayloadValues/)
assert.doesNotMatch(launchSwift, /payload: collectPayload/)
assert.match(launchFlutter, /schemaVersion: 1/)
assert.match(launchFlutter, /source: 'viewspec-flutter'/)
assert.match(launchFlutter, /payloadValues: _collectPayloadValues/)
assert.doesNotMatch(launchFlutter, /payload: _collectPayload/)
for (const field of ['inputs', 'rules', 'projections']) {
  assert(Object.hasOwn(launchIntentJson.view_spec, field), `hosted extended launch IntentBundle needs view_spec.${field}`)
}

const headlinerManifest = JSON.parse(await readFile('demos/launch-assets/headliner-manifest.json', 'utf8'))
assert.equal(headlinerManifest.outputs.mp4, 'demos/launch-assets/headliner-prompt-four-outputs.mp4')
for (const assetPath of Object.values(headlinerManifest.outputs)) {
  const info = await stat(assetPath)
  assert(info.size > 0, `${assetPath} should not be empty`)
}

const hnDemoManifest = JSON.parse(await readFile('demos/launch-assets/hn-launch-demo-manifest.json', 'utf8'))
assert.equal(hnDemoManifest.outputs.mp4, 'demos/launch-assets/hn-launch-demo.mp4')
assert.equal(hnDemoManifest.outputs.gif, 'demos/launch-assets/hn-launch-demo.gif')
assert.equal(hnDemoManifest.outputs.poster, 'demos/launch-assets/hn-launch-demo-poster.png')
assert.equal(hnDemoManifest.format, 'silent captions')
assert(hnDemoManifest.duration_seconds >= 45 && hnDemoManifest.duration_seconds <= 60)
assert(hnDemoManifest.code_capture_zoom_minimum >= 1.5)
assert(hnDemoManifest.output_sizes.gif <= hnDemoManifest.gif_target_max_bytes)
for (const slug of ['proof-artifacts', 'react-tsx', 'swiftui', 'flutter']) {
  const storyboardEntry = hnDemoManifest.storyboard.find((entry) => entry.slug === slug)
  assert(storyboardEntry, `HN demo storyboard needs ${slug}`)
  assert(storyboardEntry.zoom >= 1.5, `${slug} capture should use 150% zoom or larger`)
}
for (const assetPath of Object.values(hnDemoManifest.outputs)) {
  const info = await stat(assetPath)
  assert(info.size > 0, `${assetPath} should not be empty`)
}

const landingPlayground = await readFile('demos/shared/landing-playground.js', 'utf8')
assert.match(landingPlayground, /navigator\.clipboard\.writeText/)
assert.match(landingPlayground, /document\.execCommand\('copy'\)/)
assert.match(landingPlayground, /textarea\.focus\(\)/)

console.log(`Validated SEO and agent metadata for ${pages.length} pages.`)
