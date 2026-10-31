import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFile, stat } from 'node:fs/promises'

const pages = [
  ['demos/index.html', 'https://viewspec.dev/'],
  ['demos/appbundle-state-ir/index.html', 'https://viewspec.dev/appbundle-state-ir/'],
  ['demos/custom-motifs/index.html', 'https://viewspec.dev/custom-motifs/'],
  ['demos/stateful-collections/index.html', 'https://viewspec.dev/stateful-collections/'],
  ['demos/motif-switcher/index.html', 'https://viewspec.dev/motif-switcher/'],
  ['demos/provenance-inspector/index.html', 'https://viewspec.dev/provenance-inspector/'],
  ['demos/live-builder/index.html', 'https://viewspec.dev/live-builder/'],
  ['demos/invariants/index.html', 'https://viewspec.dev/invariants/'],
  ['demos/proof-bundle/index.html', 'https://viewspec.dev/proof-bundle/'],
  ['demos/fifteen-lines/index.html', 'https://viewspec.dev/fifteen-lines/'],
  ['demos/style-derivation/index.html', 'https://viewspec.dev/style-derivation/'],
  ['demos/style-range/index.html', 'https://viewspec.dev/style-range/'],
]

// Retired paid and hosted surfaces. The character classes keep this guard from matching a
// repository-wide search for the same terms.
const RETIRED_SURFACES =
  /buy\.stripe|api\.viewspec|viewspec-api\.fly|[$]149|149\/mo|proof[-]explorer|openapi\.json|signed\s+receipt|patch[-]preview|converge[-]|validate[-]intent|prove[-]app|compile[-]app|data-config-link/i

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
  assert.doesNotMatch(html, RETIRED_SURFACES, `${file} references a retired surface`)
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

assertPublicEqual(publicFacts.schema_version, 1, 'public facts schema_version')
assert.match(publicFacts.sdk_version, /^\d+\.\d+\.\d+(?:[a-z]+\d+)?$/, 'public facts sdk_version shape')
assertPublicEqual(publicFacts.package_url, 'https://pypi.org/project/viewspec/', 'public facts package_url')
assertPublicEqual(publicFacts.installation.release_channel, 'beta', 'public facts release channel')
assert.match(publicFacts.sdk_version, /[a-z]+\d+$/, 'beta channel requires a prerelease SDK version')
assertPublicEqual(
  publicFacts.installation.current_command,
  'python -m pip install --pre viewspec',
  'public facts current beta install command'
)
assertPublicEqual(
  publicFacts.installation.stable_command,
  'python -m pip install viewspec',
  'public facts stable install command'
)
assertPublicEqual(publicFacts.license, 'MIT', 'public facts license')
assertPublicEqual(publicFacts.distribution.cost, 'free', 'public facts cost')
assertPublicEqual(publicFacts.distribution.hosted_service, false, 'public facts hosted service')
assertPublicText(publicFacts.distribution.scope, 'no hosted service', 'public facts distribution scope')
assertPublicText(await readFile('LICENSE', 'utf8'), 'MIT License', 'repository license')
for (const retiredKey of ['canonical_api_url', 'pricing', 'core_refinement']) {
  assert(!Object.hasOwn(publicFacts, retiredKey), `public facts must not publish ${retiredKey}`)
}
assert.deepEqual(publicFacts.cli_commands, ['init', 'compile', 'check', 'prove', 'studio', 'mcp'], 'public facts CLI commands')
assert(!Object.hasOwn(publicFacts.studio, 'creation_command'), 'public facts must not publish a Studio creation command')
assertPublicText(publicFacts.studio.command, 'viewspec studio', 'public facts Studio command')
assertPublicText(publicFacts.studio.scope, '127.0.0.1', 'public facts Studio scope')
assertPublicEqual(publicFacts.proof.first_proof_command, 'viewspec prove --out .viewspec-proof', 'public facts first proof command')
assertPublicEqual(
  publicFacts.proof.authored_proof_command,
  'viewspec prove viewspec.intent.json --target react-tailwind-tsx --install --out .viewspec-proof --json',
  'public facts authored proof command'
)
assertPublicEqual(publicFacts.proof.human_summary_file, '.viewspec-proof/PROOF.md', 'public facts proof summary file')
assertPublicEqual(publicFacts.proof.machine_report_file, '.viewspec-proof/proof_report.json', 'public facts proof report file')
assertPublicEqual(publicFacts.proof.support_bundle_file, '.viewspec-proof/support_bundle.json', 'public facts proof support bundle file')
assertPublicText(publicFacts.proof.scope, 'compact style-delta counts', 'public facts proof style summary scope')
assertPublicText(publicFacts.proof.non_claim, 'not pixel-perfect visual regression', 'public facts proof non-claim')
assertPublicEqual(publicFacts.proof_integrations.target, 'react-tailwind-app', 'proof integrations target')
assertPublicEqual(publicFacts.proof_integrations.opt_in, true, 'proof integrations opt-in policy')
assertPublicEqual(publicFacts.proof_integrations.freerange.repository_url, 'https://github.com/chenglou/freerange', 'Freerange repository URL')
assertPublicEqual(publicFacts.proof_integrations.freerange.package, '@chenglou/freerange', 'Freerange package')
assertPublicEqual(publicFacts.proof_integrations.freerange.version, '0.0.1', 'Freerange version')
assertPublicText(publicFacts.proof_integrations.freerange.command, '--freerange --json', 'Freerange command')
assertPublicText(publicFacts.proof_integrations.freerange.prerequisite, 'user-installed stable Bun 1.x', 'Freerange Bun prerequisite')
assertPublicText(publicFacts.proof_integrations.freerange.prerequisite, 'ViewSpec never installs Bun', 'Freerange Bun ownership')
assert.deepEqual(
  publicFacts.proof_integrations.freerange.report_keys,
  ['static_analysis', 'analyses.freerange'],
  'Freerange report keys'
)
assertPublicText(publicFacts.proof_integrations.freerange.scope, 'generated numeric kernel', 'Freerange bounded scope')
assertPublicText(publicFacts.proof_integrations.freerange.non_claim, 'does not analyze CSS', 'Freerange CSS non-claim')
assertPublicText(publicFacts.proof_integrations.freerange.non_claim, 'arbitrary math', 'Freerange arbitrary-math non-claim')
assertPublicEqual(publicFacts.proof_integrations.pretext.repository_url, 'https://github.com/chenglou/pretext', 'Pretext repository URL')
assertPublicEqual(publicFacts.proof_integrations.pretext.package, '@chenglou/pretext', 'Pretext package')
assertPublicEqual(publicFacts.proof_integrations.pretext.version, '0.0.8', 'Pretext proof version')
assertPublicText(publicFacts.proof_integrations.pretext.command, '--pretext --json', 'Pretext command')
assertPublicText(publicFacts.proof_integrations.pretext.prerequisite, 'does not require Bun', 'Pretext Bun boundary')
assertPublicEqual(publicFacts.proof_integrations.pretext.profile, 'viewspec_pretext_native_dom_v2', 'Pretext profile')
assertPublicEqual(publicFacts.proof_integrations.pretext.font_family, 'Arial, sans-serif', 'Pretext font family')
assert.deepEqual(
  publicFacts.proof_integrations.pretext.viewports,
  [
    { width: 390, height: 844 },
    { width: 768, height: 1024 },
    { width: 1440, height: 1000 },
  ],
  'Pretext canonical viewports'
)
assert.deepEqual(
  publicFacts.proof_integrations.pretext.report_keys,
  ['text_layout', 'analyses.pretext'],
  'Pretext report keys'
)
assertPublicText(publicFacts.proof_integrations.pretext.scope, 'preserves native DOM semantics', 'Pretext DOM-preservation scope')
assertPublicText(publicFacts.proof_integrations.pretext.scope, 'line counts and overflow', 'Pretext metric scope')
assertPublicText(publicFacts.proof_integrations.pretext.non_claim, 'not general typography proof', 'Pretext typography non-claim')
assertPublicText(publicFacts.proof_integrations.pretext.non_claim, 'canvas-rendering proof', 'Pretext canvas non-claim')
assertPublicText(publicFacts.proof_integrations.composed.command, '--freerange --pretext --json', 'composed proof command')
assertPublicText(publicFacts.proof_integrations.composed.non_claim, 'does not broaden', 'composed proof non-claim')
assertPublicEqual(publicFacts.proof_integrations.e2e.status, 'configured', 'proof integrations CI status')
assertPublicText(publicFacts.proof_integrations.e2e.scope, 'does not claim that a CI run has completed', 'CI evidence boundary')
assertPublicText(home, `"softwareVersion": "${publicFacts.sdk_version}"`, 'homepage SDK version')
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
assertPublicEqual(publicFacts.appbundle_state_ir.proof_command_short, 'viewspec prove viewspec.app.json --with-shell', 'public facts State IR short proof command')
assertPublicText(JSON.stringify(publicFacts.appbundle_state_ir), 'viewspec prove viewspec.app.json --out .viewspec-app-proof --with-shell', 'public facts State IR proof command')
assertPublicText(publicFacts.appbundle_state_ir.scope, 'interactive_state_v0', 'public facts State IR scope')
assertPublicText(publicFacts.appbundle_state_ir.scope, 'visibility_v0', 'public facts visibility scope')
assertPublicText(publicFacts.appbundle_state_ir.scope, 'evaluateViewSpecVisibility', 'public facts visibility evaluator scope')
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

const intentContractSchema = JSON.parse(await readFile('demos/agent-intent-bundle.schema.json', 'utf8'))
const appContractSchema = JSON.parse(await readFile('demos/agent-app-bundle.schema.json', 'utf8'))
assertPublicEqual(publicFacts.agent_assets.intent_schema_id, intentContractSchema.$id, 'public facts agent intent schema id')
assertPublicEqual(publicFacts.agent_assets.app_schema_id, appContractSchema.$id, 'public facts agent app schema id')
assertPublicEqual(publicFacts.agent_assets.network_policy, 'no SDK network calls', 'public facts agent asset network policy')
for (const payloadFile of publicFacts.agent_assets.payload_files) {
  await stat(`demos/${payloadFile}`)
}

const pyproject = await readFile('pyproject.toml', 'utf8')
const versionModule = await readFile('src/viewspec/_version.py', 'utf8')
assertPublicText(pyproject, 'dynamic = ["version"]', 'pyproject dynamic version declaration')
assertPublicText(versionModule, `__version__ = "${publicFacts.sdk_version}"`, 'runtime version')

for (const publicTextPath of ['README.md', 'docs/getting-started.md', 'demos/llms.txt', 'demos/llms-full.txt']) {
  const text = await readFile(publicTextPath, 'utf8')
  assertPublicText(text, publicFacts.installation.current_command, `${publicTextPath} current beta install command`)
  assertPublicText(text, publicFacts.proof.first_proof_command, `${publicTextPath} first proof`)
  assertPublicText(text, 'PROOF.md', `${publicTextPath} proof summary`)
  assertPublicText(text, 'support_bundle.json', `${publicTextPath} proof support bundle`)
}

for (const llmTextPath of ['demos/llms.txt', 'demos/llms-full.txt']) {
  const text = await readFile(llmTextPath, 'utf8')
  assertPublicText(text, publicFacts.sdk_version, `${llmTextPath} SDK version`)
  assertPublicText(text, publicFacts.installation.stable_command, `${llmTextPath} stable-channel distinction`)
  assertPublicText(text, 'proof-bundle.md', `${llmTextPath} proof guide`)
  assertPublicText(text, 'style-delta counts', `${llmTextPath} proof style summary`)
  assertPublicText(text, publicFacts.proof.non_claim, `${llmTextPath} proof scope`)
  assertPublicText(text, publicFacts.package_url, `${llmTextPath} package URL`)
  assertPublicText(text, publicFacts.proof.authored_proof_command, `${llmTextPath} authored proof path`)
  assertPublicText(text, '@chenglou/freerange@0.0.1', `${llmTextPath} Freerange proof pin`)
  assertPublicText(text, publicFacts.proof_integrations.freerange.command, `${llmTextPath} Freerange command`)
  assertPublicText(text, 'user-installed stable Bun 1.x', `${llmTextPath} Freerange Bun prerequisite`)
  assertPublicText(text, '@chenglou/pretext@0.0.8', `${llmTextPath} Pretext proof pin`)
  assertPublicText(text, publicFacts.proof_integrations.pretext.command, `${llmTextPath} Pretext command`)
  assertPublicText(text, '390x844', `${llmTextPath} Pretext mobile viewport`)
  assertPublicText(text, publicFacts.proof_integrations.composed.command, `${llmTextPath} composed proof command`)
  assertPublicText(text, 'does not claim that a CI run has completed', `${llmTextPath} CI boundary`)
}

for (const aestheticTextPath of ['README.md', 'docs/getting-started.md', 'docs/agent-integration.md', 'demos/llms.txt', 'demos/llms-full.txt']) {
  const text = await readFile(aestheticTextPath, 'utf8')
  for (const token of aestheticProfileTokens) {
    assertPublicText(text, token, `${aestheticTextPath} aesthetic profile token`)
  }
  if (!text.includes('not CSS') && !text.includes('not arbitrary CSS')) {
    publicFactDrift(`${aestheticTextPath} missing aesthetic profile non-css scope`)
  }
}

for (const proofTextPath of ['demos/index.html', 'demos/proof-bundle/index.html', 'demos/llms.txt', 'demos/llms-full.txt']) {
  const text = await readFile(proofTextPath, 'utf8')
  assertPublicText(text, 'style-delta counts', `${proofTextPath} proof style summary`)
  if (text.includes('proof_report.json') && !text.includes('PROOF.md')) {
    publicFactDrift(`${proofTextPath} mentions proof_report.json without PROOF.md`)
  }
  if (text.includes('proof_report.json') && !text.includes('support_bundle.json')) {
    publicFactDrift(`${proofTextPath} mentions proof_report.json without support_bundle.json`)
  }
}

for (const hostProofTextPath of ['docs/known-limits-react-tailwind-tsx.md', 'demos/llms.txt', 'demos/llms-full.txt']) {
  const text = await readFile(hostProofTextPath, 'utf8')
  assertPublicText(text, 'grid column/span counts', `${hostProofTextPath} host proof grid span scope`)
  assertPublicText(text, 'profiled aesthetic marker', `${hostProofTextPath} host proof aesthetic marker scope`)
  assertPublicText(text, 'action payload', `${hostProofTextPath} host proof action payload scope`)
  assertPublicText(text, publicFacts.proof.host_assertion_requirements.report_key, `${hostProofTextPath} host assertion requirements key`)
  assertPublicText(text, 'dom_count', `${hostProofTextPath} host assertion DOM requirement`)
  assertPublicText(text, 'style_assertion_count', `${hostProofTextPath} host assertion style requirement`)
  assertPublicText(text, 'aesthetic_layout_assertion_count', `${hostProofTextPath} host assertion layout requirement`)
}

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
  'viewspec prove viewspec.app.json --with-shell',
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

// Style-range specimen wall: one intent, every profile, all at once — honest compiler output.
const styleRangePage = await readFile('demos/style-range/index.html', 'utf8')
for (const expected of [
  'The whole style range, compiled at once',
  'One intent, eight grounds',
  'real compiler output, not a mockup',
  'data-presentation-contract="specimen-wall"',
  'distinct style hashes',
  'shell overrides',
  'Why every specimen is honest',
]) {
  assertPublicText(styleRangePage, expected, 'style range demo')
}
for (const token of aestheticProfileTokens) {
  assertPublicText(styleRangePage, token, 'style range demo token')
}
assert.equal(
  (styleRangePage.match(/class="sr-tile"/g) || []).length,
  aestheticProfileTokens.length,
  'style range specimen count'
)
const styleRangeShellCss = extractTaggedStyle(styleRangePage, 'data-demo-shell-css="true"')
if (!styleRangeShellCss) publicFactDrift('style range demo missing tagged shell CSS')
if (/\.vs-|data-ir-id/.test(styleRangeShellCss)) {
  assert.fail('STYLE_RANGE_BYPASS: style range shell CSS styles generated artifact internals')
}

// Two install pills: hero + closing call to action.
const installCommandPattern = publicFacts.installation.current_command.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
assert.equal((home.match(new RegExp(`data-copy-text="${installCommandPattern}"`, 'g')) || []).length, 2)
assert.match(home, /aria-label="Copy ViewSpec beta install command"/)
assert.doesNotMatch(home, /data-copy-text="python -m pip install viewspec"/)
const homeJsonLd = extractJsonLd(home)
const graph = homeJsonLd.find((entry) => Array.isArray(entry['@graph']))?.['@graph'] || []
assert(graph.some((entry) => entry['@type'] === 'SoftwareApplication'), 'home JSON-LD needs SoftwareApplication')
assert(!graph.some((entry) => entry['@type'] === 'WebAPI'), 'home JSON-LD must not describe a hosted API')
assert(graph.some((entry) => entry['@type'] === 'FAQPage'), 'home JSON-LD needs FAQPage')
const softwareJsonLd = graph.find((entry) => entry['@type'] === 'SoftwareApplication') || {}
assert(!Object.hasOwn(softwareJsonLd, 'offers'), 'home JSON-LD must not publish offers')
assertPublicEqual(softwareJsonLd.isAccessibleForFree, true, 'home JSON-LD free access')
assertPublicText(softwareJsonLd.license || '', 'MIT', 'home JSON-LD license')
assertPublicText(softwareJsonLd.keywords || '', 'Freerange numeric kernel proof', 'home JSON-LD Freerange keyword')
assertPublicText(softwareJsonLd.keywords || '', 'Pretext native DOM text proof', 'home JSON-LD Pretext keyword')
assertPublicText(JSON.stringify(softwareJsonLd.featureList || []), '@chenglou/freerange@0.0.1', 'home JSON-LD Freerange feature')
assertPublicText(JSON.stringify(softwareJsonLd.featureList || []), '@chenglou/pretext@0.0.8', 'home JSON-LD Pretext feature')

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
  'Local UI compiler for you and your agent',
  'Tell your agent what to build. See the real product.',
  'state_replay_assertions',
  'CompositionIR to UI and reducer',
  'No hosted service, account, or API key.',
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
assert.match(llms, /viewspec init --out viewspec\.intent\.json/)
assert.match(llms, /viewspec check viewspec\.intent\.json --json/)
assert.match(llms, /agentic engineering/i)
assert.match(llms, /no hosted service/i)
assertPublicText(llms, 'Homepage compiled aesthetic profiles: https://viewspec.dev/', 'llms homepage aesthetic public URL')
assertPublicText(
  llms,
  'https://viewspec.dev/landing-compiled/profile-evidence.json',
  'llms profile evidence public URL'
)
assert.doesNotMatch(llms, /Aesthetic profiles demo/)
assert.doesNotMatch(llms, /https:\/\/viewspec\.dev\/aesthetic-profiles\//)
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
for (const file of ['demos/llms.txt', 'demos/llms-full.txt']) {
  const text = await readFile(file, 'utf8')
  assert.doesNotMatch(text, /agent-native UI IR/i, `${file} should describe IntentBundle/compiler, not IR as source`)
  assert.doesNotMatch(text, /semantic UI IR/i, `${file} should describe the compiler contract, not IR as product source`)
}

const landing = await readFile('demos/index.html', 'utf8')
assert.doesNotMatch(landing, /agent-native UI IR, agent-native UI IR/)
assert.doesNotMatch(landing, /Your agent writes HTML/)
assert.doesNotMatch(landing, /agent HTML governance first/)
assert.doesNotMatch(landing, /agent HTML governance/)
assert.match(landing, /<title>ViewSpec - Free, local UI compiler for coding agents<\/title>/)
assert.match(landing, /Tell your agent what to build\. See the real product\. Prove what compiled\./)
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
  'id="commands"',
  'id="start"',
  'id="cta-actions"',
  'Try the one-minute proof',
  './proof-bundle/',
  'id="integration-proofs-title"',
  'data-cli-step="init"',
  'data-cli-step="compile"',
  'data-cli-step="check"',
  'data-cli-step="prove"',
  'data-cli-step="studio"',
  'data-proof-integration="freerange"',
  'data-proof-integration="pretext"',
  '@chenglou/freerange@0.0.1',
  '@chenglou/pretext@0.0.8',
  'https://github.com/chenglou/freerange',
  'https://github.com/chenglou/pretext',
  '--freerange --pretext --json',
  'A dedicated CI workflow is configured',
  'does not claim that a CI run has completed',
  'skip-link',
]) {
  assertPublicText(landing, expected, 'landing page artifact controls')
}
assertPublicText(home, publicFacts.studio.command, 'homepage Studio command')
assertPublicText(home, publicFacts.studio.non_claim, 'homepage Studio non-claim')
assertPublicText(home, publicFacts.proof.first_proof_command, 'homepage first proof command')
assert.match(landing, /<section id="commands"[\s\S]*?data-cli-step="mcp"/, 'landing should expose all six commands')
assert.doesNotMatch(landing, /private (?:review|sharing) link/i, 'landing must not claim unimplemented hosted sharing')
assert.doesNotMatch(landing, /<a class="ghost mono" href="#top"[^>]*>read the docs/, 'landing docs CTA must not loop back to the hero')
assert.match(landing, /href="https:\/\/github\.com\/nxrobins\/viewspec\/blob\/main\/docs\/getting-started\.md"[^>]*>read the docs/, 'landing docs CTA points to getting-started docs')
assert.match(landing, /<a href="#commands">Commands<\/a>/, 'landing primary nav leads with the commands')
assert.match(landing, /<a href="#start">Start<\/a>/, 'landing primary nav reaches the call to action')
assert.doesNotMatch(landing, /@media \(max-width:720px\)\{\s*\.nav-links a:not\(\.cta-mini\)\{\s*display:none;\s*\}\s*\}/, 'mobile nav should keep core links reachable')
assertPublicText(landing, 'Click, tap, or hover', 'landing provenance copy supports touch and keyboard users')
assertPublicText(landing, 'data-trace-target', 'landing provenance receipts should be traceable click targets')
// The embedded specimen is REAL compiler output carrying real IR provenance.
assert.match(landing, /id="viewspec-artifact-slot"[\s\S]*?data-ir-id="region_root"/, 'landing embeds the real compiled artifact')
assert.doesNotMatch(landing, /data-page-style=/)
assert.doesNotMatch(landing, /data-viewspec-style=/)
assert.match(landing, /\.profiles\s*{[\s\S]*?flex-wrap:\s*wrap;/, 'profile switcher wraps')
for (const motifId of ['dom-motif_compile_flow', 'dom-motif_agent_workflow', 'dom-motif_artifact_identity', 'dom-motif_open_source']) {
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
assertPublicText(landing, '@chenglou/pretext@0.0.8', 'landing Pretext proof integration pin')
// The homepage canvas-heading demo remains a separate vendored Pretext 0.0.6 runtime.
assertPublicText(pretextGlobal, '@chenglou/pretext@0.0.6', 'landing Pretext global bundle')
assertPublicText(pretextGlobal, 'window.ViewSpecPretext', 'landing Pretext global export')
assert.doesNotMatch(landing, /self-render-frame/)
assert.doesNotMatch(landing, /<iframe[^>]+landing-compiled\/index\.html/)

const sharedNav = await readFile('demos/shared/nav.js', 'utf8')
assert.match(sharedNav, /label: 'Pipeline'/, 'shared demo nav should name the preset explorer honestly')
assert.doesNotMatch(sharedNav, /label: 'Builder'/, 'shared demo nav should not overpromise builder behavior')

const liveBuilder = await readFile('demos/live-builder/index.html', 'utf8')
assert.match(liveBuilder, /ViewSpec Demo - Pipeline Explorer/, 'live-builder page title should be renamed')
assert.match(liveBuilder, /Pipeline Explorer/, 'live-builder visible heading should be renamed')
assert.match(liveBuilder, /read-only preset explorer/i, 'live-builder page should set read-only expectations')
assert.doesNotMatch(liveBuilder, /<title>ViewSpec Demo - Live Builder<\/title>/)

const provenanceInspector = await readFile('demos/provenance-inspector/index.html', 'utf8')
assert.match(provenanceInspector, /Click, tap, or hover any rendered element/, 'provenance page should support touch-first instructions')
assert.match(provenanceInspector, /data-text="Click\/tap"/, 'provenance lock-state pill should avoid hover-only wording')
assert.doesNotMatch(provenanceInspector, /Hover over any element to inspect its provenance/)

for (const retiredPath of ['demos/llms.txt', 'demos/llms-full.txt', 'demos/public-facts.json', 'demos/sitemap.xml', 'demos/shared/nav.js']) {
  assert.doesNotMatch(await readFile(retiredPath, 'utf8'), RETIRED_SURFACES, `${retiredPath} references a retired surface`)
}
const cliCommands = new Set(publicFacts.cli_commands)
for (const cliTextPath of ['demos/llms.txt', 'demos/llms-full.txt']) {
  const cliText = await readFile(cliTextPath, 'utf8')
  for (const [, command] of cliText.matchAll(/`viewspec ([a-z][a-z-]*)/g)) {
    assert(cliCommands.has(command), `${cliTextPath} names a removed command: viewspec ${command}`)
  }
}
for (const [label, command] of [
  ['first proof', publicFacts.proof.first_proof_command],
  ['authored proof', publicFacts.proof.authored_proof_command],
  ['host proof', publicFacts.proof.react_tailwind_host_command],
  ['Freerange', publicFacts.proof_integrations.freerange.command],
  ['Pretext', publicFacts.proof_integrations.pretext.command],
  ['composed proof', publicFacts.proof_integrations.composed.command],
  ['State IR proof', publicFacts.appbundle_state_ir.proof_command],
  ['Studio', publicFacts.studio.command],
]) {
  assert(cliCommands.has(command.split(' ')[1]), `${label} command uses a removed CLI command: ${command}`)
}

const agentPrompt = await readFile('demos/agent-system-prompt.txt', 'utf8')
assert.match(agentPrompt, /IntentBundle/)
assert.match(agentPrompt, /CompositionIR is compiler output only/)
assert.match(agentPrompt, /Generated JSON is not a finished ViewSpec proof/)
assert.match(agentPrompt, /viewspec prove viewspec\.intent\.json --out \.viewspec-proof/)
assert.match(agentPrompt, /\.viewspec-proof\/PROOF\.md/)
assert.match(agentPrompt, /proof_report\.json/)
assert.match(agentPrompt, /support_bundle\.json/)
assert.match(agentPrompt, /pixel-perfect visual regression/)
assert.match(agentPrompt, /AppBundle JSON/)
assert.match(agentPrompt, /fixture_readonly_v0/)
assert.match(agentPrompt, /resource_views/)
assert.match(agentPrompt, /viewspec check viewspec\.app\.json --json/)
assert.match(agentPrompt, /viewspec compile viewspec\.app\.json --out app-dist --target html-tailwind-app --json/)
assert.match(agentPrompt, /viewspec prove viewspec\.app\.json --out \.viewspec-app-proof --with-shell --json/)
assert.match(agentPrompt, /Static Shell V0/)
assert.doesNotMatch(agentPrompt, /You output ViewSpec IR/)

const agentSchema = JSON.parse(await readFile('demos/agent-intent-bundle.schema.json', 'utf8'))
assert.deepEqual(agentSchema.$defs.motif.properties.kind.enum, ['table', 'dashboard', 'outline', 'comparison', 'list', 'form', 'detail', 'empty_state', 'loading_state', 'error_state', 'hero'])
assert.deepEqual(agentSchema.$defs.action.properties.kind.enum, ['select', 'submit', 'navigate', 'search', 'filter', 'sort', 'paginate', 'bulk_action'])
for (const publicTextPath of ['README.md', 'docs/agent-integration.md', 'demos/llms.txt', 'demos/llms-full.txt']) {
  const text = await readFile(publicTextPath, 'utf8')
  for (const expected of ['loading_state', 'error_state', 'search', 'filter', 'sort', 'paginate', 'bulk_action']) {
    if (!text.includes(expected)) statefulCollectionsDrift(`${publicTextPath} missing ${expected}`)
  }
}
assert.deepEqual(publicFacts.agent_assets.payload_files, [
  'agent-system-prompt.txt',
  'agent-intent-bundle.schema.json',
  'agent-intent-example.dashboard.json',
  'agent-app-bundle.schema.json',
  'agent-app-example.internal-tool.json',
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

async function readUtf8NoBom(path) {
  const text = await readFile(path, 'utf8')
  assert(!text.startsWith('﻿'), `${path} must not start with a UTF-8 BOM`)
  return text
}

for (const payloadFile of publicFacts.agent_assets.payload_files) {
  await readUtf8NoBom(`demos/${payloadFile}`)
}

console.log(`Validated SEO and agent metadata for ${pages.length} pages.`)
