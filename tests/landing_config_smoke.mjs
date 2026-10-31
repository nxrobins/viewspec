import assert from 'node:assert/strict'
import { readdir, readFile, stat } from 'node:fs/promises'
import { dirname, join, normalize } from 'node:path'

// The site is static: no page reads runtime configuration, and every internal link it
// publishes resolves to a file that deploys from demos/.

const shared = (await readdir('demos/shared')).sort()
assert.deepEqual(
  shared,
  ['launch-pages.css', 'nav.js', 'pretext-canvas-surfaces.js'],
  'demos/shared holds only assets that pages load'
)

const home = await readFile('demos/index.html', 'utf8')
assert.doesNotMatch(home, /VIEWSPEC_LANDING_CONFIG|PUBLIC_LANDING_API_KEY/, 'homepage reads no runtime config')
assert.doesNotMatch(home, /data-config-link/, 'homepage links are not rewritten at runtime')
assert.doesNotMatch(home, /<script type="module"/, 'homepage has no module scripts')

async function pagesUnder(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const pages = []
  for (const entry of entries) {
    if (entry.name === 'vendor' || entry.name === 'landing-compiled') continue
    const path = join(directory, entry.name)
    if (entry.isDirectory()) pages.push(...(await pagesUnder(path)))
    else if (entry.name.endsWith('.html')) pages.push(path)
  }
  return pages
}

async function exists(path) {
  try {
    await stat(path)
    return true
  } catch {
    return false
  }
}

async function assertLocalTarget(page, target) {
  const path = target.split(/[?#]/)[0]
  if (!path) return
  const resolved = normalize(join(dirname(page), path))
  const file = path.endsWith('/') ? join(resolved, 'index.html') : resolved
  assert(await exists(file), `${page} links to a missing file: ${target}`)
}

const pages = await pagesUnder('demos')
assert(pages.length >= 13, 'expected every public demo page')
let checkedLinks = 0
for (const page of pages) {
  const html = await readFile(page, 'utf8')
  for (const [, target] of html.matchAll(/(?:href|src)="(\.{1,2}\/[^"]*)"/g)) {
    await assertLocalTarget(page, target)
    checkedLinks += 1
  }
}

for (const [, anchor] of home.matchAll(/href="#([^"]+)"/g)) {
  assert(home.includes(`id="${anchor}"`), `homepage anchor has no target: #${anchor}`)
  checkedLinks += 1
}

const nav = await readFile('demos/shared/nav.js', 'utf8')
for (const [, target] of nav.matchAll(/href: '(\.\.\/[^']*)'/g)) {
  await assertLocalTarget('demos/shared/nav.js', target)
  checkedLinks += 1
}

const sitemap = await readFile('demos/sitemap.xml', 'utf8')
for (const [, path] of sitemap.matchAll(/<loc>https:\/\/viewspec\.dev\/([^<]*)<\/loc>/g)) {
  assert(await exists(join('demos', path, 'index.html')), `sitemap lists a missing page: ${path}`)
  checkedLinks += 1
}

console.log(`Validated ${checkedLinks} internal links across ${pages.length} static pages.`)
