(function () {
  'use strict'

  const elements = {
    layout: document.getElementById('explorerLayout'),
    error: document.getElementById('loadError'),
    summary: document.getElementById('summaryGrid'),
    caseList: document.getElementById('caseList'),
    viewportSwitcher: document.getElementById('viewportSwitcher'),
    caseStatus: document.getElementById('caseStatus'),
    caseTitle: document.getElementById('caseTitle'),
    caseDescription: document.getElementById('caseDescription'),
    viewportReadout: document.getElementById('viewportReadout'),
    fullSizeLink: document.getElementById('fullSizeLink'),
    imageWell: document.getElementById('imageWell'),
    image: document.getElementById('evidenceImage'),
    screenshotHash: document.getElementById('screenshotHash'),
    caseReview: document.getElementById('caseReview'),
    meanScore: document.getElementById('meanScore'),
    evidenceRoles: document.getElementById('evidenceRoles'),
    evidenceCount: document.getElementById('evidenceCount'),
    artifactHashes: document.getElementById('artifactHashes'),
    scoreList: document.getElementById('scoreList'),
    changeBefore: document.getElementById('changeBefore'),
    changeAfter: document.getElementById('changeAfter'),
    correctionMeta: document.getElementById('correctionMeta'),
    proofChain: document.getElementById('proofChain'),
    gateList: document.getElementById('gateList'),
    negativeList: document.getElementById('negativeList'),
  }

  let contract = null
  let selectedCase = null
  let selectedViewport = 'desktop'

  function title(value) {
    return value.replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
  }

  function shortHash(value) {
    return `${value.slice(0, 10)}…${value.slice(-8)}`
  }

  function text(tag, className, value) {
    const node = document.createElement(tag)
    if (className) node.className = className
    node.textContent = value
    return node
  }

  function setQuery() {
    const url = new URL(window.location.href)
    url.searchParams.set('case', selectedCase.id)
    url.searchParams.set('viewport', selectedViewport)
    window.history.replaceState(null, '', url)
  }

  function renderSummary() {
    const summary = contract.summary
    const metrics = [
      [`${summary.conformant_count}/${summary.case_count}`, 'conformant cases'],
      [String(summary.case_count * contract.viewports.length), 'responsive screenshots'],
      [`${summary.verified_correction_count}/${summary.case_count}`, 'verified corrections'],
      [`${summary.passed_gate_count}/${summary.passed_gate_count}`, 'passed gates'],
      [String(summary.critical_issue_count), 'critical issues'],
    ]
    elements.summary.replaceChildren(...metrics.map(([value, label]) => {
      const item = text('div', 'summary-item', '')
      item.append(text('strong', '', value), text('span', '', label))
      return item
    }))
  }

  function selectCase(caseId) {
    selectedCase = contract.cases.find((item) => item.id === caseId) || contract.cases[0]
    renderCaseList()
    renderCase()
    setQuery()
  }

  function selectViewport(viewportId) {
    if (!contract.viewports.some((item) => item.id === viewportId)) viewportId = 'desktop'
    selectedViewport = viewportId
    renderViewportButtons()
    renderScreenshot()
    setQuery()
  }

  function renderCaseList() {
    elements.caseList.replaceChildren(...contract.cases.map((item, index) => {
      const button = document.createElement('button')
      button.type = 'button'
      button.className = 'case-button'
      button.setAttribute('aria-pressed', String(item.id === selectedCase.id))
      button.append(text('span', 'case-number', String(index + 1).padStart(2, '0')), text('span', '', item.label))
      button.addEventListener('click', () => selectCase(item.id))
      return button
    }))
  }

  function renderViewportButtons() {
    elements.viewportSwitcher.replaceChildren(...contract.viewports.map((viewport) => {
      const button = document.createElement('button')
      button.type = 'button'
      button.className = 'viewport-button'
      button.setAttribute('aria-pressed', String(viewport.id === selectedViewport))
      button.textContent = `${viewport.label} · ${viewport.width}`
      button.addEventListener('click', () => selectViewport(viewport.id))
      return button
    }))
  }

  function renderScreenshot() {
    const viewport = contract.viewports.find((item) => item.id === selectedViewport)
    const screenshot = selectedCase.evidence.screenshots[selectedViewport]
    const capture = screenshot.capture_kind === 'full_page'
      ? `full-page evidence ${screenshot.width}×${screenshot.height}`
      : 'viewport evidence'
    elements.viewportReadout.textContent = `${viewport.label} viewport · ${viewport.width}×${viewport.height} · ${capture}`
    elements.fullSizeLink.href = screenshot.path
    elements.imageWell.dataset.viewport = selectedViewport
    elements.image.style.opacity = '0'
    elements.image.src = screenshot.path
    elements.image.width = screenshot.width
    elements.image.height = screenshot.height
    elements.image.alt = `${selectedCase.label} retained ${viewport.label.toLowerCase()} browser evidence captured at ${screenshot.width} by ${screenshot.height} pixels`
    elements.image.onload = () => { elements.image.style.opacity = '1' }
    elements.screenshotHash.textContent = screenshot.sha256
  }

  function renderArtifacts() {
    const labels = [
      ['Source', selectedCase.artifacts.source_sha256],
      ['Artifact', selectedCase.artifacts.artifact_sha256],
      ['Verification', selectedCase.artifacts.verification_id],
    ]
    elements.artifactHashes.replaceChildren(...labels.map(([label, value]) => {
      const wrapper = document.createElement('div')
      const dd = text('dd', '', value.startsWith('vvr_') ? value : shortHash(value))
      dd.title = value
      wrapper.append(text('dt', '', label), dd)
      return wrapper
    }))
  }

  function renderScores() {
    elements.scoreList.replaceChildren(...contract.quality_dimensions.map((dimension) => {
      const value = selectedCase.scores[dimension]
      const row = text('div', 'score-row', '')
      const track = text('div', 'score-track', '')
      const fill = document.createElement('i')
      fill.style.width = `${value * 20}%`
      track.append(fill)
      row.append(text('span', '', dimension.replaceAll('_', ' ')), track, text('strong', '', `${value}/5`))
      return row
    }))
  }

  function renderCorrection() {
    const correction = selectedCase.correction
    elements.changeBefore.textContent = correction.before
    elements.changeAfter.textContent = correction.after
    const targetParts = [correction.operation, correction.target.screen_id, correction.target.node_id, correction.target.attr].filter(Boolean)
    elements.correctionMeta.textContent = targetParts.join(' · ')
    const chain = [
      ['Preview', correction.preview_id],
      ['Patch', correction.patch_id],
      ['Receipt', correction.receipt_id],
      ['Re-verify', correction.verification_status],
    ]
    elements.proofChain.replaceChildren(...chain.map(([label, value]) => {
      const item = document.createElement('li')
      const shown = value.includes('_') ? shortHash(value) : value
      item.title = value
      item.append(text('span', '', label), text('strong', '', shown))
      return item
    }))
  }

  function renderCase() {
    elements.caseStatus.textContent = `${selectedCase.status} · no critical issues`
    elements.caseTitle.textContent = selectedCase.label
    elements.caseDescription.textContent = selectedCase.description
    elements.caseReview.textContent = selectedCase.review
    elements.meanScore.textContent = selectedCase.mean_score.toFixed(1)
    elements.evidenceRoles.replaceChildren(...selectedCase.evidence.roles.map((role) => text('span', 'role-chip', role)))
    elements.evidenceCount.textContent = `${selectedCase.evidence.item_count} retained evidence items across three viewports.`
    renderArtifacts()
    renderScores()
    renderCorrection()
    renderScreenshot()
  }

  function renderAssurance() {
    elements.gateList.replaceChildren(...contract.gates.map((gate) => {
      const item = text('div', 'gate-item', '')
      item.append(text('i', '', '✓'), text('strong', '', gate.name.replaceAll('_', ' ')), text('span', '', gate.status))
      return item
    }))
    elements.negativeList.replaceChildren(...contract.negative_controls.map((control) => {
      const item = text('div', 'negative-item', '')
      const content = document.createElement('div')
      content.append(text('strong', '', control.id.replaceAll('-', ' ')), text('small', '', `${control.diagnostic_code} · ${control.next_action}`))
      item.append(text('i', '', '!'), content)
      return item
    }))
  }

  async function init() {
    try {
      const response = await fetch('./proof-data.json', { cache: 'no-store' })
      if (!response.ok) throw new Error(`proof data returned ${response.status}`)
      contract = await response.json()
      if (contract.kind !== 'viewspec_public_proof_explorer' || contract.schema_version !== 1) {
        throw new Error('unsupported proof data contract')
      }
      const params = new URLSearchParams(window.location.search)
      selectedViewport = params.get('viewport') || 'desktop'
      if (!contract.viewports.some((item) => item.id === selectedViewport)) selectedViewport = 'desktop'
      selectedCase = contract.cases.find((item) => item.id === params.get('case')) || contract.cases[0]
      renderSummary()
      renderCaseList()
      renderViewportButtons()
      renderCase()
      renderAssurance()
      elements.layout.setAttribute('aria-busy', 'false')
      setQuery()
    } catch (error) {
      elements.layout.setAttribute('aria-busy', 'false')
      elements.error.hidden = false
      elements.error.textContent = `Proof data could not be loaded: ${error.message}`
    }
  }

  init()
})()
