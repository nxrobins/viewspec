import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile, stat } from 'node:fs/promises'

const pages = [
  ['demos/index.html', 'https://viewspec.dev/'],
  ['demos/cross-platform-dashboard/index.html', 'https://viewspec.dev/cross-platform-dashboard/'],
  ['demos/custom-motifs/index.html', 'https://viewspec.dev/custom-motifs/'],
  ['demos/interactive-compose/index.html', 'https://viewspec.dev/interactive-compose/'],
  ['demos/motif-switcher/index.html', 'https://viewspec.dev/motif-switcher/'],
  ['demos/provenance-inspector/index.html', 'https://viewspec.dev/provenance-inspector/'],
  ['demos/live-builder/index.html', 'https://viewspec.dev/live-builder/'],
  ['demos/invariants/index.html', 'https://viewspec.dev/invariants/'],
  ['demos/fifteen-lines/index.html', 'https://viewspec.dev/fifteen-lines/'],
  ['demos/style-derivation/index.html', 'https://viewspec.dev/style-derivation/'],
]

function extractJsonLd(html) {
  return [...html.matchAll(/<script type="application\/ld\+json">\s*([\s\S]*?)\s*<\/script>/g)].map((match) =>
    JSON.parse(match[1])
  )
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
assert(openapi.paths['/v1/compile']?.post, 'OpenAPI needs POST /v1/compile')
assert.equal(openapi['x-viewspec-agent-artifacts'].systemPrompt, 'https://viewspec.dev/agent-system-prompt.txt')

const agentPrompt = await readFile('demos/agent-system-prompt.txt', 'utf8')
assert.match(agentPrompt, /IntentBundle/)
assert.match(agentPrompt, /CompositionIR is compiler output only/)
assert.doesNotMatch(agentPrompt, /You output ViewSpec IR/)

const agentSchema = JSON.parse(await readFile('demos/agent-intent-bundle.schema.json', 'utf8'))
assert.deepEqual(agentSchema.$defs.motif.properties.kind.enum, ['table', 'dashboard', 'outline', 'comparison', 'list', 'form', 'detail', 'empty_state', 'hero'])

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
