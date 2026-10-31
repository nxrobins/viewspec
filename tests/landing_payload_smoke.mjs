import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'

const COMPILED = 'demos/landing-compiled'

function assertNodeShape(nodeId, node) {
  assert.equal(node.id, nodeId, `node key ${nodeId} must match node.id`)
  assert.equal(typeof node.kind, 'string', `${nodeId}.kind must be a string`)
  assert.equal(typeof node.attrs, 'object', `${nodeId}.attrs must be an object`)
  assert.equal(typeof node.slots, 'object', `${nodeId}.slots must be an object`)
  assert.equal(typeof node.edges, 'object', `${nodeId}.edges must be an object`)

  for (const [slotName, slotValue] of Object.entries(node.slots)) {
    assert(Array.isArray(slotValue.values), `${nodeId}.slots.${slotName}.values must be an array`)
  }
  for (const [edgeName, edgeValue] of Object.entries(node.edges)) {
    assert(Array.isArray(edgeValue.values), `${nodeId}.edges.${edgeName}.values must be an array`)
  }
}

function assertAddressResolves(address, nodes) {
  const match = /^node:([^#]+)(?:#attr:([^#]+)|#slot:([^#[\]]+)(?:\[(\d+)])?|#edge:([^#]+))?$/.exec(address)
  assert(match, `binding address must be canonical: ${address}`)
  const [, nodeId, attr, slot, slotIndex, edge] = match
  const node = nodes[nodeId]
  assert(node, `binding address node must exist: ${address}`)
  if (attr) assert(Object.hasOwn(node.attrs, attr), `binding attr must exist: ${address}`)
  if (slot) {
    assert(Object.hasOwn(node.slots, slot), `binding slot must exist: ${address}`)
    if (slotIndex !== undefined) {
      assert(Number(slotIndex) < node.slots[slot].values.length, `binding slot index must exist: ${address}`)
    }
  }
  if (edge) assert(Object.hasOwn(node.edges, edge), `binding edge must exist: ${address}`)
}

function assertBundleShape(label, bundle) {
  assert.equal(typeof bundle.substrate, 'object', `${label}: bundle.substrate is required`)
  assert.equal(typeof bundle.view_spec, 'object', `${label}: bundle.view_spec is required`)

  const { substrate, view_spec: viewSpec } = bundle
  assert.equal(viewSpec.substrate_id, substrate.id, `${label}: view_spec.substrate_id must match substrate.id`)
  assert.equal(typeof viewSpec.complexity_tier, 'number', `${label}: view_spec.complexity_tier must be numeric`)
  assert.equal(typeof viewSpec.root_region, 'string', `${label}: view_spec.root_region must be present`)
  assert(!Array.isArray(substrate.nodes), `${label}: substrate.nodes must be a dict keyed by node ID`)
  assert.equal(typeof substrate.nodes, 'object', `${label}: substrate.nodes must be an object`)
  assert(Object.hasOwn(substrate.nodes, substrate.root_id), `${label}: substrate.root_id must exist in substrate.nodes`)

  for (const [nodeId, node] of Object.entries(substrate.nodes)) {
    assertNodeShape(nodeId, node)
  }

  const bindingIds = new Set()
  for (const binding of viewSpec.bindings) {
    assert(!bindingIds.has(binding.id), `${label}: binding id must be unique: ${binding.id}`)
    bindingIds.add(binding.id)
    assertAddressResolves(binding.address, substrate.nodes)
  }

  for (const group of viewSpec.groups) {
    for (const memberId of group.members) {
      assert(bindingIds.has(memberId), `${label}: group ${group.id} member must resolve: ${memberId}`)
    }
  }
  for (const motif of viewSpec.motifs) {
    for (const memberId of motif.members) {
      assert(bindingIds.has(memberId), `${label}: motif ${motif.id} member must resolve: ${memberId}`)
    }
  }
}

function withoutAestheticProfile(bundle) {
  const styles = bundle.view_spec.styles.filter((style) => !style.token.startsWith('aesthetic.'))
  return { ...bundle, view_spec: { ...bundle.view_spec, styles } }
}

async function readBundle(path) {
  return JSON.parse(await readFile(path, 'utf8'))
}

const landing = await readBundle(`${COMPILED}/intent_bundle.json`)
assertBundleShape('landing', landing)

const motifs = landing.view_spec.motifs.map((motif) => motif.id)
for (const motifId of ['proof_badges', 'compile_flow', 'capabilities', 'agent_workflow', 'open_source', 'artifact_identity']) {
  assert(motifs.includes(motifId), `landing payload needs the ${motifId} motif`)
}

const profileSlugs = (await readdir(`${COMPILED}/profiles`)).sort()
assert.equal(profileSlugs.length, 8, 'landing payload ships one bundle per aesthetic profile')
for (const slug of profileSlugs) {
  const bundle = await readBundle(`${COMPILED}/profiles/${slug}/intent_bundle.json`)
  assertBundleShape(slug, bundle)
  assert.deepEqual(
    withoutAestheticProfile(bundle),
    withoutAestheticProfile(landing),
    `${slug} must differ from the landing payload only by its aesthetic profile`
  )
}

const starters = ['demos/agent-intent-example.dashboard.json']
for (const path of starters) {
  assertBundleShape(path, await readBundle(path))
}

console.log(`Validated ${1 + profileSlugs.length + starters.length} landing and starter payloads.`)
