import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile, stat } from 'node:fs/promises'

const pages = [
  ['demos/index.html', 'https://viewspec.dev/'],
  ['demos/cross-platform-dashboard/index.html', 'https://viewspec.dev/cross-platform-dashboard/'],
  ['demos/appbundle-state-ir/index.html', 'https://viewspec.dev/appbundle-state-ir/'],
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
assertPublicText(publicFacts.proof.scope, 'compact style-delta counts', 'public facts proof style summary scope')
assertPublicText(publicFacts.proof.non_claim, 'not pixel-perfect visual regression', 'public facts proof non-claim')
assertPublicEqual(publicFacts.proof.host_assertion_requirements.report_key, 'assertion_requirements', 'public facts host assertion requirements key')
assert.deepEqual(
  publicFacts.proof.host_assertion_requirements.base_minimums,
  { dom_count: 1, style_assertion_count: 4 },
  'public facts host assertion base minimums'
)
assert.deepEqual(
  publicFacts.proof.host_assertion_requirements.manifest_derived_fields,
  ['aesthetic_layout_assertion_count', 'aesthetic_profile_assertion_count', 'grid_span_assertion_count'],
  'public facts host assertion manifest-derived fields'
)
assert.deepEqual(
  publicFacts.proof.host_assertion_requirements.all_fields,
  [
    'aesthetic_layout_assertion_count',
    'aesthetic_profile_assertion_count',
    'dom_count',
    'grid_span_assertion_count',
    'style_assertion_count',
  ],
  'public facts host assertion fields'
)

assertPublicEqual(publicFacts.appbundle_state_ir.demo_url, 'https://viewspec.dev/appbundle-state-ir/', 'public facts State IR demo URL')
assertPublicEqual(publicFacts.appbundle_state_ir.state_profile, 'interactive_state_v0', 'public facts State IR profile')
assertPublicEqual(publicFacts.appbundle_state_ir.reducer_artifact, 'state_reducer.ts', 'public facts State IR reducer artifact')
assertPublicEqual(publicFacts.appbundle_state_ir.reducer_export, 'reduceViewSpecState', 'public facts State IR reducer export')
assertPublicEqual(publicFacts.appbundle_state_ir.replay_field, 'state_replay_assertions', 'public facts State IR replay field')
assertPublicEqual(publicFacts.appbundle_state_ir.proof_command_short, 'viewspec prove-app --with-shell', 'public facts State IR short proof command')
assertPublicText(JSON.stringify(publicFacts.appbundle_state_ir), 'prove-app --with-shell', 'public facts State IR proof command')
assertPublicText(publicFacts.appbundle_state_ir.scope, 'interactive_state_v0', 'public facts State IR scope')
assertPublicText(publicFacts.appbundle_state_ir.non_claim, 'not Redux', 'public facts State IR non-claim')
assertPublicText(publicFacts.appbundle_state_ir.non_claim, 'Zustand', 'public facts State IR non-claim')
assertPublicText(publicFacts.appbundle_state_ir.non_claim, 'CRDT', 'public facts State IR non-claim')
assertPublicText(publicFacts.appbundle_state_ir.non_claim, 'persistence', 'public facts State IR non-claim')
assertPublicText(publicFacts.appbundle_state_ir.non_claim, 'backend generation', 'public facts State IR non-claim')
assert.deepEqual(
  publicFacts.appbundle_state_ir.proof_facts,
  ['state replay passed', 'reducer generated', 'manifest checked', 'shell hash matched', 'no runtime LLM'],
  'public facts State IR proof badges'
)

const aestheticProfileTokens = [
  'aesthetic.calm_ops',
  'aesthetic.premium_saas',
  'aesthetic.data_dense',
  'aesthetic.editorial_product',
  'aesthetic.executive_review',
  'aesthetic.brutalist',
  'aesthetic.neon_cyber',
  'aesthetic.warm_organic',
]
assert.deepEqual(publicFacts.aesthetic_profiles.tokens, aestheticProfileTokens, 'public facts aesthetic profile tokens')
assertPublicEqual(publicFacts.aesthetic_profiles.homepage_url, 'https://viewspec.dev/', 'public facts aesthetic homepage URL')
assertPublicEqual(
  publicFacts.aesthetic_profiles.evidence_artifact,
  'https://viewspec.dev/landing-compiled/profile-evidence.json',
  'public facts aesthetic evidence artifact'
)
assertPublicEqual(
  publicFacts.aesthetic_profiles.default_profile,
  'aesthetic.calm_ops',
  'public facts aesthetic default profile'
)
assertPublicText(publicFacts.aesthetic_profiles.scope, 'deterministic view-level art-direction handles', 'public facts aesthetic scope')
assertPublicText(publicFacts.aesthetic_profiles.scope, 'eight compiled landing artifacts', 'public facts aesthetic homepage scope')
assertPublicText(publicFacts.aesthetic_profiles.scope, 'stable semantic ids', 'public facts aesthetic semantic ids scope')
assertPublicText(publicFacts.aesthetic_profiles.scope, 'zero shell overrides', 'public facts aesthetic shell override scope')
assertPublicText(publicFacts.aesthetic_profiles.scope, 'compact style-delta counts', 'public facts aesthetic style summary scope')
assertPublicText(publicFacts.aesthetic_profiles.non_claim, 'not arbitrary CSS', 'public facts aesthetic non-claim')

assertPublicText(publicFacts.accessibility.scope, 'fails closed', 'public facts a11y scope')
assertPublicText(publicFacts.accessibility.contrast_standard, 'WCAG 2.x AA', 'public facts a11y contrast standard')
assertPublicText(publicFacts.accessibility.scope, 'react-tailwind-tsx', 'public facts a11y React contrast proven')
assertPublicText(publicFacts.accessibility.non_claim, 'not a full WCAG audit', 'public facts a11y non-claim')
assert.deepEqual(
  publicFacts.accessibility.check_names,
  ['a11y_contrast', 'a11y_names'],
  'public facts a11y check names'
)

const expectedProfileSlugs = {
  'aesthetic.calm_ops': 'calm-ops',
  'aesthetic.premium_saas': 'premium-saas',
  'aesthetic.data_dense': 'data-dense',
  'aesthetic.editorial_product': 'editorial-product',
  'aesthetic.executive_review': 'executive-review',
  'aesthetic.brutalist': 'brutalist',
  'aesthetic.neon_cyber': 'neon-cyber',
  'aesthetic.warm_organic': 'warm-organic',
}
const landingProfileEvidence = JSON.parse(await readFile('demos/landing-compiled/profile-evidence.json', 'utf8'))
assert.equal(landingProfileEvidence.version, 'landing_compiled_aesthetic_profiles.v1')
assert.equal(landingProfileEvidence.defaultProfile, 'aesthetic.calm_ops')
assert.equal(landingProfileEvidence.profileCount, 8)
assert.equal(landingProfileEvidence.semanticIdsStable, true)
assert.equal(landingProfileEvidence.shellOverrides, 0)
assert.equal(landingProfileEvidence.styleProjectionDistinct, true)
assert.equal(landingProfileEvidence.styleProjectionHashCount, 8)
assert.deepEqual(Object.keys(landingProfileEvidence.profiles).sort(), [...aestheticProfileTokens].sort())
const profileSemanticHashes = new Set()
const profileStyleHashes = new Set()
const profileNodeCounts = new Set()
for (const token of aestheticProfileTokens) {
  const profile = landingProfileEvidence.profiles[token]
  const profileDir = `demos/landing-compiled/profiles/${expectedProfileSlugs[token]}`
  assert(profile, `${token} profile evidence is missing`)
  assert.equal(profile.slug, expectedProfileSlugs[token], `${token} profile slug`)
  assert.equal(profile.manifestAestheticProfile, token, `${token} manifest profile marker`)
  assert.equal(profile.invariantFlags?.manifestProfileMatches, true, `${token} manifest profile invariant`)
  assert.equal(profile.invariantFlags?.sameSemanticGraph, true, `${token} semantic graph invariant`)
  assert.equal(profile.invariantFlags?.semanticIdsStable, true, `${token} semantic id invariant`)
  assert.equal(profile.invariantFlags?.shellOverridesZero, true, `${token} shell override invariant`)
  assert.equal(profile.invariantFlags?.styleProjectionDistinct, true, `${token} style hash invariant`)
  assert(profile.artifactBodyUrl.includes('/landing-compiled/profiles/'), `${token} artifact body URL`)
  assert(profile.manifestUrl.includes('/landing-compiled/profiles/'), `${token} manifest URL`)
  assert(profile.intentUrl.includes('/landing-compiled/profiles/'), `${token} intent URL`)
  assert(profile.styleSignature.includes('changed tokens'), `${token} style signature`)
  assert.equal(
    profile.styleSignature,
    `${profile.styleProof.changed_token_count} changed tokens / ${profile.styleProof.category_count} categories / ${profile.styleProof.declaration_count} declarations`,
    `${token} style signature proof`
  )
  assert(profile.layoutSignature.includes('workspace'), `${token} layout signature`)
  assert(profile.nodeCount > 0, `${token} node count`)
  await stat(`${profileDir}/index.html`)
  await stat(`${profileDir}/intent_bundle.json`)
  await stat(`${profileDir}/artifact_body.html`)
  await stat(`${profileDir}/provenance_manifest.json`)
  const profileBody = await readFile(`${profileDir}/artifact_body.html`, 'utf8')
  assertPublicText(profileBody, `data-aesthetic-profile="${token}"`, `${token} compiled body aesthetic marker`)
  assert.equal((profileBody.match(/data-viewspec-page-artifact="true"/g) || []).length, 1, `${token} active artifact root count`)
  const profileManifest = JSON.parse(await readFile(`${profileDir}/provenance_manifest.json`, 'utf8'))
  assert.equal(profileManifest.nodes?.['dom-region_root']?.props?.aesthetic_profile, token, `${token} manifest aesthetic profile`)
  assert(profileManifest.nodes?.['dom-region_root']?.style_tokens?.includes(token), `${token} manifest root style token`)
  assert.equal(profileManifest.command, 'compile', `${token} profile manifest command`)
  assert.equal(profileManifest.kind, 'intent_bundle_compile', `${token} profile manifest kind`)
  const profileIntent = JSON.parse(await readFile(`${profileDir}/intent_bundle.json`, 'utf8'))
  assert(
    profileIntent.view_spec.styles.some((style) => style.token === token || style.tokens?.includes(token)),
    `${token} profile intent declares aesthetic token`
  )
  profileSemanticHashes.add(profile.semanticHash)
  profileStyleHashes.add(profile.styleProjectionHash)
  profileNodeCounts.add(profile.nodeCount)
}
assert.equal(profileSemanticHashes.size, 1, 'compiled profile semantic hashes stay stable')
assert.equal(profileStyleHashes.size, 8, 'compiled profile style hashes differ')
assert.equal(profileNodeCounts.size, 1, 'compiled profile node counts stay stable')

const agentManifest = JSON.parse(await readFile('demos/agent-assets.json', 'utf8'))
assertPublicEqual(publicFacts.agent_assets.manifest_url, 'https://viewspec.dev/agent-assets.json', 'public facts agent assets manifest')
assertPublicEqual(publicFacts.agent_assets.schema_version, agentManifest.schema_version, 'public facts agent asset schema version')
assertPublicEqual(publicFacts.agent_assets.contract_profile, agentManifest.contract.profile, 'public facts agent asset contract profile')
assertPublicEqual(publicFacts.agent_assets.intent_schema_id, agentManifest.intent_schema_id, 'public facts agent asset schema id')
assertPublicEqual(publicFacts.agent_assets.export_command, agentManifest.contract.export_command, 'public facts agent asset export command')
assertPublicEqual(publicFacts.agent_assets.check_command, agentManifest.contract.check_command, 'public facts agent asset check command')
assertPublicEqual(publicFacts.agent_assets.network_policy, agentManifest.contract.network_policy, 'public facts agent asset network policy')
assert.deepEqual(
  publicFacts.agent_assets.payload_files,
  agentManifest.files.map((file) => file.path),
  'public facts agent asset payload files'
)

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
  assertPublicText(text, 'style-delta counts', `${publicTextPath} proof style summary`)
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
  assertPublicText(text, 'style-delta counts', `${aestheticTextPath} aesthetic style summary`)
}

for (const diffIntentTextPath of ['README.md', 'docs/getting-started.md', 'docs/agent-integration.md', 'demos/llms.txt', 'demos/llms-full.txt']) {
  const text = await readFile(diffIntentTextPath, 'utf8')
  assertPublicText(text, 'diff-intent', `${diffIntentTextPath} diff-intent review surface`)
  assertPublicText(text, 'aesthetic profile', `${diffIntentTextPath} diff-intent aesthetic profile surface`)
  assertPublicText(text, 'intent_semantic_change_lines', `${diffIntentTextPath} semantic summary helper`)
}

for (const agentAssetTextPath of ['README.md', 'docs/getting-started.md', 'docs/agent-integration.md', 'demos/llms.txt', 'demos/llms-full.txt']) {
  const text = await readFile(agentAssetTextPath, 'utf8')
  assertPublicText(text, `schema version \`${publicFacts.agent_assets.schema_version}\``, `${agentAssetTextPath} agent asset schema version`)
  assertPublicText(text, 'local_v1', `${agentAssetTextPath} agent asset contract profile`)
  assertPublicText(text, 'viewspec export-agent-assets --out .viewspec', `${agentAssetTextPath} agent asset export command`)
  assertPublicText(text, 'viewspec check-agent-assets .viewspec --json', `${agentAssetTextPath} agent asset check command`)
}

{
  const agentPrompt = await readFile('demos/agent-system-prompt.txt', 'utf8')
  assertPublicText(agentPrompt, 'diff-intent', 'agent prompt diff-intent review surface')
  assertPublicText(agentPrompt, 'semantic_changes', 'agent prompt semantic changes review')
  assertPublicText(agentPrompt, 'semantic_summary', 'agent prompt MCP semantic summary')
  assertPublicText(agentPrompt, 'intent_semantic_change_lines', 'agent prompt Python semantic summary helper')
}

for (const proofTextPath of ['README.md', 'docs/getting-started.md', 'docs/agent-integration.md', 'demos/index.html', 'demos/proof-bundle/index.html', 'demos/llms.txt', 'demos/llms-full.txt']) {
  const text = await readFile(proofTextPath, 'utf8')
  assertPublicText(text, 'style-delta counts', `${proofTextPath} proof style summary`)
  if (text.includes('proof_report.json') && !text.includes('PROOF.md')) {
    publicFactDrift(`${proofTextPath} mentions proof_report.json without PROOF.md`)
  }
  if (text.includes('proof_report.json') && !text.includes('support_bundle.json')) {
    publicFactDrift(`${proofTextPath} mentions proof_report.json without support_bundle.json`)
  }
}

for (const proofMetadataTextPath of ['README.md', 'docs/agent-integration.md', 'demos/llms-full.txt']) {
  const text = await readFile(proofMetadataTextPath, 'utf8')
  assertPublicText(text, 'proof identity', `${proofMetadataTextPath} proof identity metadata`)
}

for (const hostProofTextPath of ['README.md', 'docs/getting-started.md', 'docs/agent-integration.md', 'docs/free-sdk-reliability.md', 'docs/known-limits-react-tailwind-tsx.md', 'demos/llms.txt', 'demos/llms-full.txt']) {
  const text = await readFile(hostProofTextPath, 'utf8')
  assertPublicText(text, 'grid column/span counts', `${hostProofTextPath} host proof grid span scope`)
  assertPublicText(text, 'profiled aesthetic marker', `${hostProofTextPath} host proof aesthetic marker scope`)
  assertPublicText(text, 'action payload', `${hostProofTextPath} host proof action payload scope`)
  assertPublicText(text, publicFacts.proof.host_assertion_requirements.report_key, `${hostProofTextPath} host assertion requirements key`)
  assertPublicText(text, 'dom_count', `${hostProofTextPath} host assertion DOM requirement`)
  assertPublicText(text, 'style_assertion_count', `${hostProofTextPath} host assertion style requirement`)
  assertPublicText(text, 'aesthetic_layout_assertion_count', `${hostProofTextPath} host assertion layout requirement`)
}

for (const productTextPath of ['README.md', 'demos/llms.txt', 'demos/llms-full.txt']) {
  const productText = await readFile(productTextPath, 'utf8')
  assertPublicText(productText, proPrice, `${productTextPath} pro price`)
  assertPublicText(productText, proCalls, `${productTextPath} pro hosted calls`)
}
for (const productTextPath of ['README.md', 'demos/llms.txt', 'demos/llms-full.txt']) {
  const productText = await readFile(productTextPath, 'utf8')
  assertPublicText(productText, freeCalls, `${productTextPath} free hosted calls`)
}
assertPublicText(await readFile('README.md', 'utf8'), publicFacts.package_url, 'README package URL')
assertPublicText(home, './proof-bundle/', 'landing proof bundle guide link')

const proofBundlePage = await readFile('demos/proof-bundle/index.html', 'utf8')
for (const expected of [
  publicFacts.proof.first_proof_command,
  'PROOF.md',
  'proof_report.json',
  'support_bundle.json',
  'source_artifact',
  'react_tailwind_reference_host',
  'Manifest Summary',
  'style-delta counts',
  publicFacts.proof.host_assertion_requirements.report_key,
  'style_assertion_count',
  'Hashes',
  'Checks',
  'Errors',
  publicFacts.proof.non_claim,
]) {
  assertPublicText(proofBundlePage, expected, 'proof bundle page')
}
assertPublicText(await readFile('demos/llms.txt', 'utf8'), 'https://viewspec.dev/proof-bundle/', 'llms proof bundle public URL')
assertPublicText(await readFile('demos/llms-full.txt', 'utf8'), 'https://viewspec.dev/proof-bundle/', 'llms-full proof bundle public URL')

const stateIrTerms = [
  'interactive_state_v0',
  'reduceViewSpecState',
  'state_replay_assertions',
  'prove-app --with-shell',
  'state_reducer.ts',
]
for (const stateIrTextPath of ['demos/index.html', 'demos/appbundle-state-ir/index.html', 'demos/llms.txt', 'demos/llms-full.txt']) {
  const text = await readFile(stateIrTextPath, 'utf8')
  for (const term of stateIrTerms) {
    assertPublicText(text, term, `${stateIrTextPath} State IR term`)
  }
}
for (const term of stateIrTerms) {
  assertPublicText(JSON.stringify(publicFacts.appbundle_state_ir), term, 'public facts State IR term')
}
assertPublicText(home, './appbundle-state-ir/', 'landing State IR page link')
for (const expected of ['state replay passed', 'reducer generated', 'shell hash matched', 'No runtime LLM']) {
  assertPublicText(home, expected, 'landing State IR proof badge')
}
assertPublicText(await readFile('demos/llms.txt', 'utf8'), 'https://viewspec.dev/appbundle-state-ir/', 'llms State IR public URL')
assertPublicText(await readFile('demos/llms-full.txt', 'utf8'), 'https://viewspec.dev/appbundle-state-ir/', 'llms-full State IR public URL')

const stateIrPage = await readFile('demos/appbundle-state-ir/index.html', 'utf8')
for (const expected of [
  'AppBundle JSON',
  'Generated reducer',
  'Replay assertion',
  'Rendered shell',
  'Proof report facts',
  'state replay',
  'reducer generated',
  'manifest checked',
  'shell hash matched',
  'Runtime LLM',
  'not Redux',
  'not Zustand',
  'not CRDT',
  'not persistence',
  'not backend generation',
]) {
  assertPublicText(stateIrPage, expected, 'State IR demo')
}

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
  'Same Intent, Eight Art Directions',
  'Beauty as a checked compiler handle',
  'deterministic art direction, not CSS',
  'semantic ids stable',
  'data-presentation-contract="light-gallery-showroom"',
  '0 shell overrides',
  'Demo shell CSS frames the page only',
  'data-aesthetic-profile',
  'aesthetic-profile-proof',
  'active-layout-signature',
  'active-style-signature',
  'bounded grid metadata',
  'changed tokens',
]) {
  assertPublicText(aestheticProfilesPage, expected, 'aesthetic profiles demo')
}
for (const token of aestheticProfileTokens) {
  assertPublicText(aestheticProfilesPage, token, 'aesthetic profiles demo token')
}
assert.equal((aestheticProfilesPage.match(/class="profile-card"/g) || []).length, 8)
const aestheticProof = extractScriptJson(aestheticProfilesPage, 'aesthetic-profile-proof')
assert.deepEqual(Object.keys(aestheticProof).sort(), [...aestheticProfileTokens].sort(), 'aesthetic profile proof tokens')
const expectedAestheticLayout = {
  'aesthetic.calm_ops': [2, 2, null, null],
  'aesthetic.premium_saas': [2, 2, 2, 'featured'],
  'aesthetic.data_dense': [3, 3, null, null],
  'aesthetic.editorial_product': [2, 1, null, null],
  'aesthetic.executive_review': [2, 3, 2, 'featured'],
  'aesthetic.brutalist': [1, 1, null, null],
  'aesthetic.neon_cyber': [2, 2, null, null],
  'aesthetic.warm_organic': [2, 3, 2, 'featured'],
}
for (const [token, [workspaceColumns, metricColumns, metricSpan, metricEmphasis]] of Object.entries(expectedAestheticLayout)) {
  assert.equal(
    aestheticProof[token].styleSignature,
    `${aestheticProof[token].styleProof.changed_token_count} changed tokens / ${aestheticProof[token].styleProof.category_count} categories / ${aestheticProof[token].styleProof.declaration_count} declarations`,
    `${token} style signature`
  )
  assert(aestheticProof[token].styleProof.changed_token_count >= 6, `${token} changed style token count`)
  assert(aestheticProof[token].styleProof.category_count >= 3, `${token} style category count`)
  assert(
    aestheticProof[token].styleProof.declaration_count >= aestheticProof[token].styleProof.changed_token_count,
    `${token} style declaration count`
  )
  assert.equal(
    aestheticProof[token].styleProof.changed_tokens.length,
    aestheticProof[token].styleProof.changed_token_count,
    `${token} changed token list length`
  )
  assert.equal(
    aestheticProof[token].styleProof.categories.length,
    aestheticProof[token].styleProof.category_count,
    `${token} category list length`
  )
  for (const tokenName of aestheticProof[token].styleProof.changed_tokens) {
    assert.doesNotMatch(tokenName, /[:;]/, `${token} style proof should expose token names, not CSS`)
  }
  assert.equal(aestheticProof[token].layoutProof.content_grid.columns, workspaceColumns, `${token} workspace columns`)
  assert.equal(aestheticProof[token].layoutProof.metric_grid.columns, metricColumns, `${token} metric columns`)
  assert.equal(aestheticProof[token].layoutProof.content_grid.profile, token, `${token} content grid profile marker`)
  assert.equal(aestheticProof[token].layoutProof.metric_grid.profile, token, `${token} metric grid profile marker`)
  if (metricSpan === null) {
    assert.equal(aestheticProof[token].layoutProof.metric_card, undefined, `${token} metric card span`)
  } else {
    assert.equal(aestheticProof[token].layoutProof.metric_card.spanColumns, metricSpan, `${token} metric card span`)
    assert.equal(aestheticProof[token].layoutProof.metric_card.layoutEmphasis, metricEmphasis, `${token} metric card emphasis`)
    assert.equal(aestheticProof[token].layoutProof.metric_card.profile, token, `${token} metric card profile marker`)
  }
  assert.equal(
    aestheticProof[token].layoutSignature,
    `workspace ${workspaceColumns} / metrics ${metricColumns}${metricSpan === null ? '' : ` / featured metric span ${metricSpan} + ${metricEmphasis} emphasis`}`,
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
for (const expected of [
  'Agent-native app compiler',
  'Intent goes in. Interface comes out.',
  'state_replay_assertions',
  'CompositionIR to UI and reducer',
]) {
  assertPublicText(landingCompiledHtml, expected, 'compiled landing artifact')
}

const robots = await readFile('demos/robots.txt', 'utf8')
assert.match(robots, /User-agent: \*/)
assert.match(robots, /Sitemap: https:\/\/viewspec\.dev\/sitemap\.xml/)

const sitemap = await readFile('demos/sitemap.xml', 'utf8')
for (const [, canonical] of pages) {
  assert.match(sitemap, new RegExp(`<loc>${canonical.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}</loc>`))
}
assert.doesNotMatch(sitemap, /https:\/\/viewspec\.dev\/aesthetic-profiles\//)

const llms = await readFile('demos/llms.txt', 'utf8')
assert.match(llms, /agent-native UI compiler/i)
assert.match(llms, /viewspec init-intent/)
assert.match(llms, /viewspec validate-intent/)
assert.match(llms, /agentic engineering/i)
assert.match(llms, /https:\/\/api\.viewspec\.dev\/v1\/compile/)
assertPublicText(llms, 'Homepage compiled aesthetic profiles: https://viewspec.dev/', 'llms homepage aesthetic public URL')
assertPublicText(
  llms,
  'https://viewspec.dev/landing-compiled/profile-evidence.json',
  'llms profile evidence public URL'
)
assert.doesNotMatch(llms, /Aesthetic profiles demo/)
assert.doesNotMatch(llms, /https:\/\/viewspec\.dev\/aesthetic-profiles\//)
assert.doesNotMatch(llms, /\$699|699\/mo/)
const llmsFull = await readFile('demos/llms-full.txt', 'utf8')
assertPublicText(
  llmsFull,
  'Homepage compiled aesthetic profiles: https://viewspec.dev/',
  'llms-full homepage aesthetic public URL'
)
assertPublicText(
  llmsFull,
  'https://viewspec.dev/landing-compiled/profile-evidence.json',
  'llms-full profile evidence public URL'
)
assert.doesNotMatch(llmsFull, /Aesthetic profiles demo/)
assert.doesNotMatch(llmsFull, /https:\/\/viewspec\.dev\/aesthetic-profiles\//)
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
assert.match(landing, /Ship agent-built apps you can prove/)
assert.match(landing, /Intent goes in\. Interface comes out\./)
for (const expected of [
  'data-viewspec-page-artifact="true"',
  'data-viewspec-profile="aesthetic.calm_ops"',
  'id="landing-profile-evidence"',
  'id="viewspec-real-data"',
  'id="viewspec-artifact-slot"',
  'data-profile-token="aesthetic.calm_ops"',
  'data-profile-token="aesthetic.premium_saas"',
  'data-profile-token="aesthetic.data_dense"',
  'data-profile-token="aesthetic.editorial_product"',
  'data-profile-token="aesthetic.executive_review"',
  'id="profileGroup"',
  'id="inspectBtn"',
  'id="traceOut"',
  'id="hud"',
  'id="noteReal"',
  'Same graph, new projection',
  'same semantic graph',
  'compiled aesthetic profile',
  'Compiled aesthetic profile homepage artifacts',
  'Everything above is compiled output',
  'landing-compiled/profile-evidence.json',
  'landing-compiled/intent_bundle.json',
  'landing-compiled/provenance_manifest.json',
  'id="pricing"',
  'id="pricing-actions"',
  'skip-link',
]) {
  assertPublicText(landing, expected, 'landing page artifact controls')
}
// The embedded specimen is REAL compiler output carrying real IR provenance.
assert.match(landing, /id="viewspec-artifact-slot"[\s\S]*?data-ir-id="region_root"/, 'landing embeds the real compiled artifact')
assert.doesNotMatch(landing, /data-page-style=/)
assert.doesNotMatch(landing, /data-viewspec-style=/)
assert.match(landing, /\.profiles\s*{[\s\S]*?flex-wrap:\s*wrap;/, 'profile switcher wraps')
for (const motifId of ['dom-motif_compile_flow', 'dom-motif_agent_workflow', 'dom-motif_artifact_identity', 'dom-motif_pricing']) {
  assert.match(landing, new RegExp(`<[^>]+id="${motifId}"`), `${motifId} should be emitted in the embedded artifact`)
}
// proof_contract was never a real motif; keep asserting it is absent.
assert.doesNotMatch(landing, /<[^>]+id="dom-motif_proof_contract"/)
await stat('demos/vendor/pretext/pretext.esm.js')
await stat('demos/vendor/pretext/pretext.global.js')
for (const file of ['analysis', 'line-break', 'line-text', 'measurement']) {
  await stat(`demos/vendor/pretext/dist/${file}.js`)
}
const pretextGlobal = await readFile('demos/vendor/pretext/pretext.global.js', 'utf8')
assertPublicText(pretextGlobal, '@chenglou/pretext@0.0.6', 'landing Pretext global bundle')
assertPublicText(pretextGlobal, 'window.ViewSpecPretext', 'landing Pretext global export')
assert.doesNotMatch(landing, /self-render-frame/)
assert.doesNotMatch(landing, /<iframe[^>]+landing-compiled\/index\.html/)
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
assertPublicEqual(openapi['x-viewspec-public-facts'].proofIdentityMetadataKey, publicFacts.proof.identity_metadata_key, 'OpenAPI public facts proof identity key')
assert.deepEqual(
  openapi['x-viewspec-public-facts'].proofIdentityHashFields,
  publicFacts.proof.identity_hash_fields,
  'OpenAPI public facts proof identity hash fields'
)
assert.deepEqual(
  openapi['x-viewspec-public-facts'].proofHostAssertionRequirements,
  publicFacts.proof.host_assertion_requirements,
  'OpenAPI public facts host assertion requirements'
)
assertPublicEqual(openapi['x-viewspec-public-facts'].appbundleStateIrDemoUrl, publicFacts.appbundle_state_ir.demo_url, 'OpenAPI public facts State IR demo URL')
assertPublicEqual(openapi['x-viewspec-public-facts'].appbundleStateIrProfile, publicFacts.appbundle_state_ir.state_profile, 'OpenAPI public facts State IR profile')
assertPublicEqual(openapi['x-viewspec-public-facts'].appbundleStateIrReducerArtifact, publicFacts.appbundle_state_ir.reducer_artifact, 'OpenAPI public facts State IR reducer artifact')
assertPublicEqual(openapi['x-viewspec-public-facts'].appbundleStateIrReducerExport, publicFacts.appbundle_state_ir.reducer_export, 'OpenAPI public facts State IR reducer export')
assertPublicEqual(openapi['x-viewspec-public-facts'].appbundleStateIrReplayField, publicFacts.appbundle_state_ir.replay_field, 'OpenAPI public facts State IR replay field')
assertPublicEqual(openapi['x-viewspec-public-facts'].appbundleStateIrProofCommandShort, publicFacts.appbundle_state_ir.proof_command_short, 'OpenAPI public facts State IR short proof command')
assertPublicEqual(openapi['x-viewspec-public-facts'].appbundleStateIrProofCommand, publicFacts.appbundle_state_ir.proof_command, 'OpenAPI public facts State IR proof command')
assertPublicEqual(openapi['x-viewspec-public-facts'].agentAssetManifest, publicFacts.agent_assets.manifest_url, 'OpenAPI public facts agent asset manifest')
assertPublicEqual(openapi['x-viewspec-public-facts'].agentAssetSchemaVersion, publicFacts.agent_assets.schema_version, 'OpenAPI public facts agent asset schema version')
assertPublicEqual(openapi['x-viewspec-public-facts'].agentAssetContractProfile, publicFacts.agent_assets.contract_profile, 'OpenAPI public facts agent asset profile')
assertPublicEqual(openapi['x-viewspec-public-facts'].agentAssetIntentSchemaId, publicFacts.agent_assets.intent_schema_id, 'OpenAPI public facts agent asset schema id')
assertPublicEqual(openapi['x-viewspec-public-facts'].agentAssetAppSchemaId, publicFacts.agent_assets.app_schema_id, 'OpenAPI public facts app asset schema id')
assertPublicEqual(openapi['x-viewspec-public-facts'].agentAssetExportCommand, publicFacts.agent_assets.export_command, 'OpenAPI public facts agent asset export')
assertPublicEqual(openapi['x-viewspec-public-facts'].agentAssetCheckCommand, publicFacts.agent_assets.check_command, 'OpenAPI public facts agent asset check')
assertPublicEqual(openapi['x-viewspec-public-facts'].agentAssetNetworkPolicy, publicFacts.agent_assets.network_policy, 'OpenAPI public facts agent asset network policy')
assert(openapi.paths['/v1/compile']?.post, 'OpenAPI needs POST /v1/compile')
assert.equal(openapi.paths['/v1/compile'].post.requestBody.content['application/json'].schema.$ref, '#/components/schemas/CompileRequestPayload')
assert.equal(openapi.components.schemas.CompileRequestPayload.properties.design.$ref, '#/components/schemas/DesignRequest')
assert(!('design' in openapi.components.schemas.IntentBundle.properties), 'OpenAPI IntentBundle schema should not absorb hosted design context')
assert.equal(openapi['x-viewspec-agent-artifacts'].assetSchemaVersion, 7)
assert.equal(openapi['x-viewspec-agent-artifacts'].assetManifest, 'https://viewspec.dev/agent-assets.json')
assert.equal(openapi['x-viewspec-agent-artifacts'].contractProfile, 'local_v1')
assert.equal(openapi['x-viewspec-agent-artifacts'].exportCommand, 'viewspec export-agent-assets --out .viewspec')
assert.equal(openapi['x-viewspec-agent-artifacts'].checkCommand, 'viewspec check-agent-assets .viewspec --json')
assert.equal(openapi['x-viewspec-agent-artifacts'].networkPolicy, 'no SDK network calls')
assert.equal(openapi['x-viewspec-agent-artifacts'].assetSchemaVersion, publicFacts.agent_assets.schema_version)
assert.equal(openapi['x-viewspec-agent-artifacts'].contractProfile, publicFacts.agent_assets.contract_profile)
assert.equal(openapi['x-viewspec-agent-artifacts'].exportCommand, publicFacts.agent_assets.export_command)
assert.equal(openapi['x-viewspec-agent-artifacts'].checkCommand, publicFacts.agent_assets.check_command)
assert.equal(openapi['x-viewspec-agent-artifacts'].networkPolicy, publicFacts.agent_assets.network_policy)
assert.equal(openapi['x-viewspec-agent-artifacts'].systemPrompt, 'https://viewspec.dev/agent-system-prompt.txt')
assert.equal(openapi['x-viewspec-agent-artifacts'].intentBundleExample, 'https://viewspec.dev/agent-intent-example.dashboard.json')
assert.equal(openapi['x-viewspec-agent-artifacts'].appBundleSchema, 'https://viewspec.dev/agent-app-bundle.schema.json')
assert.equal(openapi['x-viewspec-agent-artifacts'].appBundleExample, 'https://viewspec.dev/agent-app-example.internal-tool.json')

const agentPrompt = await readFile('demos/agent-system-prompt.txt', 'utf8')
assert.match(agentPrompt, /IntentBundle/)
assert.match(agentPrompt, /CompositionIR is compiler output only/)
assert.match(agentPrompt, /Generated JSON is not a finished ViewSpec proof/)
assert.match(agentPrompt, /viewspec prove --out \.viewspec-proof/)
assert.match(agentPrompt, /\.viewspec-proof\/PROOF\.md/)
assert.match(agentPrompt, /proof_report\.json/)
assert.match(agentPrompt, /support_bundle\.json/)
assert.match(agentPrompt, /pixel-perfect visual regression/)
assert.match(agentPrompt, /AppBundle JSON/)
assert.match(agentPrompt, /fixture_readonly_v0/)
assert.match(agentPrompt, /resource_views/)
assert.match(agentPrompt, /viewspec validate-app viewspec\.app\.json --json/)
assert.match(agentPrompt, /viewspec compile-app viewspec\.app\.json --out app-dist --target html-tailwind-app --json/)
assert.match(agentPrompt, /viewspec prove-app --app viewspec\.app\.json --out \.viewspec-app-proof --with-shell --json/)
assert.match(agentPrompt, /Static Shell V0/)
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
assert.equal(agentManifest.schema_version, 7)
assert.equal(agentManifest.contract.profile, 'local_v1')
assert.equal(agentManifest.contract.export_command, 'viewspec export-agent-assets --out .viewspec')
assert.equal(agentManifest.contract.check_command, 'viewspec check-agent-assets .viewspec --json')
assert.equal(agentManifest.contract.network_policy, 'no SDK network calls')
assert.equal(agentManifest.contract.files.intent_schema, 'agent-intent-bundle.schema.json')
assert.equal(agentManifest.contract.files.app_schema, 'agent-app-bundle.schema.json')
assert.deepEqual(agentManifest.files.map((file) => file.path), [
  'agent-system-prompt.txt',
  'agent-intent-bundle.schema.json',
  'agent-intent-example.dashboard.json',
  'agent-app-bundle.schema.json',
  'agent-app-example.internal-tool.json'
])
const agentExample = JSON.parse(await readFile('demos/agent-intent-example.dashboard.json', 'utf8'))
assert.equal(agentExample.view_spec.motifs[0].kind, 'dashboard')
assert.equal(agentExample.view_spec.substrate_id, agentExample.substrate.id)
const appSchema = JSON.parse(await readFile('demos/agent-app-bundle.schema.json', 'utf8'))
assert.equal(appSchema.$id, 'https://viewspec.dev/agent-app-bundle.schema.json')
assert.deepEqual(appSchema['x-viewspec-resource-bindings'], ['unbound_v0', 'fixture_readonly_v0'])
const appExample = JSON.parse(await readFile('demos/agent-app-example.internal-tool.json', 'utf8'))
assert.equal(appExample.app.kind, 'internal_tool')
assert.equal(appExample.resource_binding ?? 'unbound_v0', 'unbound_v0')
assert.equal(appExample.screens.length, 2)

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
