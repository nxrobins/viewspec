import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readdir, readFile } from 'node:fs/promises'

// The homepage embeds what the local compiler wrote to demos/landing-compiled, and the page
// itself compiles nothing: every artifact is built ahead of time and served statically.

const COMPILED = 'demos/landing-compiled'

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex')
}

function extractScriptJson(html, id) {
  const match = html.match(new RegExp(`<script type="application/json" id="${id}">\\s*([\\s\\S]*?)\\s*</script>`))
  assert(match, `${id} JSON script is missing`)
  return JSON.parse(match[1])
}

async function assertManifestMatchesArtifact(directory) {
  const manifest = JSON.parse(await readFile(`${directory}/provenance_manifest.json`, 'utf8'))
  assert.equal(manifest.artifact_hash, sha256(await readFile(`${directory}/index.html`)), `${directory} artifact hash`)
  assert.equal(manifest.command, 'compile', `${directory} manifest command`)
  assert.equal(manifest.kind, 'intent_bundle_compile', `${directory} manifest kind`)
  assert.equal(manifest.external_refs.length, 0, `${directory} must not reference external resources`)
}

const home = await readFile('demos/index.html', 'utf8')

const realData = extractScriptJson(home, 'viewspec-real-data')
const intent = JSON.parse(await readFile(`${COMPILED}/intent_bundle.json`, 'utf8'))
assert.deepEqual(realData.intent, intent, 'homepage embeds the compiled IntentBundle')

const artifactBody = await readFile(`${COMPILED}/artifact_body.html`, 'utf8')
assert(home.includes(artifactBody), 'homepage embeds the compiled artifact body verbatim')

const pageEvidence = extractScriptJson(home, 'landing-profile-evidence')
const fileEvidence = JSON.parse(await readFile(`${COMPILED}/profile-evidence.json`, 'utf8'))
assert.deepEqual(pageEvidence, fileEvidence, 'homepage embeds the published profile evidence')

await assertManifestMatchesArtifact(COMPILED)
const profileSlugs = await readdir(`${COMPILED}/profiles`)
for (const slug of profileSlugs) {
  await assertManifestMatchesArtifact(`${COMPILED}/profiles/${slug}`)
}

const inlineScripts = [...home.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/g)]
  .filter(([tag]) => !tag.startsWith('<script type="application/'))
  .map(([, body]) => body)
assert(inlineScripts.length > 0, 'homepage keeps its inline page script')
for (const script of inlineScripts) {
  assert.doesNotMatch(script, /\bfetch\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon/, 'homepage script makes no requests')
}

console.log(`Validated the homepage against ${1 + profileSlugs.length} locally compiled artifacts.`)
