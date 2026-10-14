import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { join } from 'node:path'

const runtime = process.argv[2]
const require = createRequire(join(runtime, 'package.json'))
const { chromium } = require('@playwright/test')
const browser = await chromium.launch({ headless: true })
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } })
  page.setDefaultTimeout(4000)
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(process.env.RELEASE_STUDIO_URL, { waitUntil: 'domcontentloaded' })
  await page.waitForFunction(() => document.title === 'ViewSpec Studio')
  await page.waitForFunction(() => document.querySelector('#status')?.textContent === 'Checked target pair ready')
  await page.locator('#viewport').selectOption('mobile')
  const targets = ['html-tailwind-app', 'react-tailwind-app']
  for (const target of targets) {
    const frame = page.frameLocator(`iframe[data-target="${target}"]`)
    await frame.getByText('Release Check Console', { exact: true }).waitFor()
    await frame.getByText('Incident Queue', { exact: true }).waitFor()
  }
  assert.deepEqual(errors, [], 'Studio or its rendered targets raised a browser error')
  console.log(JSON.stringify({ status: 'passed', engine: 'chromium', rendered_targets: targets }))
} finally {
  await browser.close()
}
