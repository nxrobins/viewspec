import assert from 'node:assert/strict'
import { copyFile, mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

const sourcePath = resolve('demos/shared/landing-payload.js')
const tempDir = await mkdtemp(join(tmpdir(), 'viewspec-landing-payload-'))
const modulePath = join(tempDir, 'landing-payload.mjs')
await copyFile(sourcePath, modulePath)

const { HINT_OPTIONS, buildIntentBundle } = await import(pathToFileURL(modulePath).href)

function cartesian(optionMap) {
  const entries = Object.entries(optionMap)
  let combinations = [{}]
  for (const [key, values] of entries) {
    combinations = combinations.flatMap((combo) => values.map((value) => ({ ...combo, [key]: value })))
  }
  return combinations
}

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

for (const hints of cartesian(HINT_OPTIONS)) {
  const bundle = buildIntentBundle(hints)
  assert.equal(typeof bundle.substrate, 'object', 'bundle.substrate is required')
  assert.equal(typeof bundle.view_spec, 'object', 'bundle.view_spec is required')

  const { substrate, view_spec: viewSpec } = bundle
  assert.equal(viewSpec.substrate_id, substrate.id, 'view_spec.substrate_id must match substrate.id')
  assert.equal(typeof viewSpec.complexity_tier, 'number', 'view_spec.complexity_tier must be numeric')
  assert.equal(typeof viewSpec.root_region, 'string', 'view_spec.root_region must be present')
  assert(!Array.isArray(substrate.nodes), 'substrate.nodes must be a dict keyed by node ID')
  assert.equal(typeof substrate.nodes, 'object', 'substrate.nodes must be an object')
  assert(Object.hasOwn(substrate.nodes, substrate.root_id), 'substrate.root_id must exist in substrate.nodes')

  for (const [nodeId, node] of Object.entries(substrate.nodes)) {
    assertNodeShape(nodeId, node)
  }

  const bindingIds = new Set()
  for (const binding of viewSpec.bindings) {
    assert(!bindingIds.has(binding.id), `binding id must be unique: ${binding.id}`)
    bindingIds.add(binding.id)
    assertAddressResolves(binding.address, substrate.nodes)
  }

  for (const group of viewSpec.groups) {
    for (const memberId of group.members) {
      assert(bindingIds.has(memberId), `group ${group.id} member must resolve: ${memberId}`)
    }
  }
  for (const motif of viewSpec.motifs) {
    for (const memberId of motif.members) {
      assert(bindingIds.has(memberId), `motif ${motif.id} member must resolve: ${memberId}`)
    }
  }
}

console.log(`Validated ${cartesian(HINT_OPTIONS).length} landing payload combinations.`)
