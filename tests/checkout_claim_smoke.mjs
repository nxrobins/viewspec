import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const page = await readFile(resolve('demos/checkout/success/index.html'), 'utf8')

assert(page.includes('URLSearchParams'))
assert(page.includes('session_id'))
assert(page.includes('https://api.viewspec.dev/v1/checkout/claim'))
assert(page.includes('cache-control'))
assert(page.includes('no-store'))
assert(!page.includes('localStorage'))
assert(!page.includes('sessionStorage'))
assert(!page.includes('console.log'))
assert(!page.includes('innerHTML'))

console.log('Validated one-time checkout key claim page.')
