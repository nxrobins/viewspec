import assert from 'node:assert/strict'
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

for (const [file, canonical] of pages) {
  const html = await readFile(file, 'utf8')
  assert.match(html, /<title>ViewSpec/, `${file} needs a ViewSpec title`)
  assert.match(html, /<meta name="description" content="[^"]{80,220}">/, `${file} needs a useful meta description`)
  assert.match(html, new RegExp(`<link rel="canonical" href="${canonical.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}">`))
  assert.match(html, /<meta name="robots" content="index,follow,max-snippet:-1,max-image-preview:large">/)
  assert.match(html, /href="https:\/\/viewspec\.dev\/llms\.txt"/)
  assert.match(html, /href="https:\/\/viewspec\.dev\/openapi\.json"/)

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

const robots = await readFile('demos/robots.txt', 'utf8')
assert.match(robots, /User-agent: \*/)
assert.match(robots, /Sitemap: https:\/\/viewspec\.dev\/sitemap\.xml/)

const sitemap = await readFile('demos/sitemap.xml', 'utf8')
for (const [, canonical] of pages) {
  assert.match(sitemap, new RegExp(`<loc>${canonical.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}</loc>`))
}

const llms = await readFile('demos/llms.txt', 'utf8')
assert.match(llms, /agent-native UI IR/i)
assert.match(llms, /agentic engineering/i)
assert.match(llms, /https:\/\/api\.viewspec\.dev\/v1\/compile/)
// Pricing was removed from llms.txt during the pricing rewrite ($699 → $149).
// Asserting on a specific dollar amount in an LLM-targeted product overview
// couples too tightly to the pricing surface; drop the assertion.

const landing = await readFile('demos/index.html', 'utf8')
assert.doesNotMatch(landing, /agent-native UI IR, agent-native UI IR/)
assert.doesNotMatch(landing, /\"price\": \"2500\"/)
// Both Pro and Enterprise CTAs were rewired in commit 1a62a55 from
// data-config-link to direct hrefs (Stripe link + mailto). Assert on the
// actual destinations rather than the deprecated config-link attribute.
assert.match(landing, /href=\"https:\/\/buy\.stripe\.com\//)
assert.match(landing, /href=\"mailto:hello@viewspec\.dev\?subject=ViewSpec%20Enterprise\"/)

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
assert.deepEqual(agentSchema.$defs.motif.properties.kind.enum, ['table', 'dashboard', 'outline', 'comparison'])

const artifactIndex = JSON.parse(await readFile('demos/cross-platform-dashboard/artifacts/artifact_index.json', 'utf8'))
assert.equal(artifactIndex.prompt, 'agent_prompt.txt')
assert.doesNotMatch(JSON.stringify(artifactIndex), /\.test-tmp/)

const launchHtml = await readFile('demos/cross-platform-dashboard/artifacts/html/index.html', 'utf8')
const launchTsx = await readFile('demos/cross-platform-dashboard/artifacts/react-tsx/ViewSpecView.tsx', 'utf8')
assert.doesNotMatch(launchHtml, /style="[^"]*\bscale:\s*[^;]+;[^"]*\bscale:/)
assert.doesNotMatch(launchTsx, /\bscale:\s*"[^"]+",[^}]*\bscale:/)

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
