export const DEFAULT_HINTS = Object.freeze({
  audience: 'executive',
  mood: 'urgent',
  density: 'breathe',
  viewport: 'desktop',
})

export const HINT_OPTIONS = Object.freeze({
  audience: ['executive', 'engineer'],
  mood: ['calm', 'urgent'],
  density: ['breathe', 'compact'],
  viewport: ['mobile', 'tablet', 'desktop'],
})

export const METRICS = Object.freeze([
  {
    id: 'kpi_revenue',
    label: 'Revenue',
    value: '$2.4M',
    rawValue: 2400000,
    baseline: 1850000,
    trend: 0.22,
    trendLabel: '+22% vs baseline',
    unit: 'USD',
    polarity: 'positive',
  },
  {
    id: 'kpi_users',
    label: 'Active Users',
    value: '18,472',
    rawValue: 18472,
    baseline: 16980,
    trend: 0.09,
    trendLabel: '+9% vs baseline',
    unit: 'count',
    polarity: 'positive',
  },
  {
    id: 'kpi_conversion',
    label: 'Conversion',
    value: '3.8%',
    rawValue: 3.8,
    baseline: 3.4,
    trend: 0.04,
    trendLabel: '+4% vs baseline',
    unit: 'percent',
    polarity: 'positive',
  },
  {
    id: 'kpi_churn',
    label: 'Churn',
    value: '1.2%',
    rawValue: 1.2,
    baseline: 0.68,
    trend: 0.31,
    trendLabel: '+31% vs baseline',
    unit: 'percent',
    polarity: 'negative',
  },
])

export const TOKEN_DEFINITIONS = Object.freeze([
  {
    token: 'tone.warning',
    trigger: 'KL surprise > threshold',
    visualEffect: 'Warm accent and heavier border',
  },
  {
    token: 'tone.positive',
    trigger: 'Upward trends or growth terms',
    visualEffect: 'Cool teal accent and mild glow',
  },
  {
    token: 'emphasis.focal',
    trigger: 'Max eigenvector centrality',
    visualEffect: 'Elevated weight and scale',
  },
  {
    token: 'emphasis.diminished',
    trigger: 'Low entropy repeated values',
    visualEffect: 'Reduced weight and muted color',
  },
  {
    token: 'contrast.separation',
    trigger: 'Wide numeric span in comparison',
    visualEffect: 'More separation and stronger borders',
  },
  {
    token: 'contrast.similarity',
    trigger: 'Tight numeric span',
    visualEffect: 'Harmonized palette and softer borders',
  },
  {
    token: 'palette.temperature',
    trigger: 'Aggregate data sentiment',
    visualEffect: 'Warm or cool overall bias',
  },
  {
    token: 'palette.energy',
    trigger: 'Wasserstein transport cost',
    visualEffect: 'Saturation tracks distribution energy',
  },
  {
    token: 'rhythm.hierarchy',
    trigger: 'IR tree depth >= 2',
    visualEffect: 'Adjusted level size ratio',
  },
  {
    token: 'rhythm.density',
    trigger: 'Bindings per viewport threshold',
    visualEffect: 'Compressed or linear spacing curve',
  },
  {
    token: 'narrative.entry',
    trigger: 'Max eigenvector centrality node',
    visualEffect: 'Scale, position, and contrast boost',
  },
  {
    token: 'narrative.flow',
    trigger: 'Heat diffusion from entry',
    visualEffect: 'Graduated visual weight decay',
  },
])

export function normalizeHints(hints = {}) {
  const normalized = { ...DEFAULT_HINTS }
  Object.keys(HINT_OPTIONS).forEach((key) => {
    if (HINT_OPTIONS[key].includes(hints[key])) normalized[key] = hints[key]
  })
  return normalized
}

export function stableHintKey(hints = {}) {
  const normalized = normalizeHints(hints)
  return JSON.stringify({
    audience: normalized.audience,
    mood: normalized.mood,
    density: normalized.density,
    viewport: normalized.viewport,
  })
}

export function buildIntentBundle(hints = {}) {
  const normalized = normalizeHints(hints)
  const nodes = {
    premium_dashboard: {
      id: 'premium_dashboard',
      kind: 'app',
      attrs: {
        title: 'Hosted compiler KPI dashboard',
        audience: normalized.audience,
        mood: normalized.mood,
        density: normalized.density,
        viewport: normalized.viewport,
      },
      slots: {
        metrics: {
          values: METRICS.map((metric) => metric.id),
        },
      },
      edges: {},
    },
  }

  METRICS.forEach((metric) => {
    nodes[metric.id] = {
      id: metric.id,
      kind: 'dashboard_card',
      attrs: {
        label: metric.label,
        value: metric.value,
        raw_value: metric.rawValue,
        baseline: metric.baseline,
        trend: metric.trend,
        trend_label: metric.trendLabel,
        unit: metric.unit,
        polarity: metric.polarity,
      },
      slots: {},
      edges: {},
    }
  })

  const bindings = []
  METRICS.forEach((metric) => {
    bindings.push({
      id: `${metric.id}_label`,
      address: `node:${metric.id}#attr:label`,
      target_region: 'main',
      present_as: 'label',
      cardinality: 'exactly_once',
    })
    bindings.push({
      id: `${metric.id}_value`,
      address: `node:${metric.id}#attr:value`,
      target_region: 'main',
      present_as: 'value',
      cardinality: 'exactly_once',
    })
    bindings.push({
      id: `${metric.id}_trend_label`,
      address: `node:${metric.id}#attr:trend_label`,
      target_region: 'main',
      present_as: 'badge',
      cardinality: 'exactly_once',
    })
  })

  const members = bindings.map((binding) => binding.id)
  const styles = [
    { id: 'hint_audience', target: 'view:premium_kpi_dashboard', token: `hint.audience.${normalized.audience}` },
    { id: 'hint_mood', target: 'view:premium_kpi_dashboard', token: `hint.mood.${normalized.mood}` },
    { id: 'hint_density', target: 'view:premium_kpi_dashboard', token: `hint.density.${normalized.density}` },
    { id: 'hint_viewport', target: 'view:premium_kpi_dashboard', token: `hint.viewport.${normalized.viewport}` },
  ]

  return {
    substrate: {
      id: 'premium_dashboard_substrate',
      root_id: 'premium_dashboard',
      nodes,
    },
    view_spec: {
      id: 'premium_kpi_dashboard',
      substrate_id: 'premium_dashboard_substrate',
      complexity_tier: 2,
      root_region: 'root',
      regions: [
        {
          id: 'root',
          parent_region: '',
          role: 'root',
          layout: 'stack',
          min_children: 1,
          max_children: null,
        },
        {
          id: 'main',
          parent_region: 'root',
          role: 'main',
          layout: normalized.viewport === 'mobile' ? 'stack' : 'grid',
          min_children: 1,
          max_children: null,
        },
      ],
      bindings,
      groups: [
        {
          id: 'metrics',
          kind: 'ordered',
          members,
          target_region: 'main',
        },
      ],
      motifs: [
        {
          id: 'kpis',
          kind: 'dashboard',
          region: 'main',
          members,
        },
      ],
      styles,
      actions: [],
    },
  }
}

const VIEWPORT_PROFILES = Object.freeze({
  mobile: { columns: 1, minWidth: 320, targetWidth: 390 },
  tablet: { columns: 2, minWidth: 768, targetWidth: 768 },
  desktop: { columns: 4, minWidth: 1024, targetWidth: 1024 },
})

const DENSITY_PROFILES = Object.freeze({
  breathe: {
    rootPadding: '20px',
    cardPadding: '18px',
    gridGap: '16px',
    cardMinHeight: '142px',
    labelSize: '12px',
    valueSize: '30px',
    badgePadding: '6px 10px',
    trendMode: 'long',
  },
  compact: {
    rootPadding: '14px',
    cardPadding: '13px',
    gridGap: '10px',
    cardMinHeight: '116px',
    labelSize: '11px',
    valueSize: '25px',
    badgePadding: '5px 8px',
    trendMode: 'short',
  },
})

const AUDIENCE_PROFILES = Object.freeze({
  executive: {
    order: ['kpi_revenue', 'kpi_conversion', 'kpi_users', 'kpi_churn'],
    labelColor: '#64748b',
    labelFamily: 'Inter, ui-sans-serif, system-ui, sans-serif',
    valueWeight: 900,
    context: 'Optimized for executive summary: revenue and conversion become the first-read path.',
  },
  engineer: {
    order: ['kpi_churn', 'kpi_conversion', 'kpi_revenue', 'kpi_users'],
    labelColor: '#0f172a',
    labelFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
    valueWeight: 760,
    context: 'Optimized for engineering diagnostics: risk and comparison boundaries move forward.',
  },
})

const MOOD_PROFILES = Object.freeze({
  calm: {
    accent: '#0d9488',
    accentBg: 'rgba(20, 184, 166, 0.10)',
    warning: '#b45309',
    warningBg: 'rgba(251, 191, 36, 0.14)',
    surface: '#ffffff',
    badgeBg: '#ccfbf1',
    badgeText: '#0f766e',
    shadow: '0 12px 28px rgba(15, 23, 42, 0.08)',
    context: 'Calm hint cools the palette so growth reads as stable progress.',
  },
  urgent: {
    accent: '#2563eb',
    accentBg: 'rgba(37, 99, 235, 0.09)',
    warning: '#dc2626',
    warningBg: 'rgba(254, 226, 226, 0.88)',
    surface: '#ffffff',
    badgeBg: '#dbeafe',
    badgeText: '#1d4ed8',
    shadow: '0 14px 30px rgba(15, 23, 42, 0.10)',
    context: 'Urgent hint warms the risk path so churn reads before the user parses the number.',
  },
})

function metricById(id) {
  return METRICS.find((metric) => metric.id === id)
}

function shortTrend(metric, profile) {
  if (profile.density.trendMode === 'long') return metric.trendLabel
  if (metric.id === 'kpi_churn') return 'Risk +31%'
  return metric.trendLabel.replace(' vs baseline', '')
}

function displayMetric(metric, profile) {
  const engineer = profile.hints.audience === 'engineer'
  return {
    ...metric,
    label: engineer ? `${metric.label} / ${metric.unit}` : metric.label,
    trendLabel: shortTrend(metric, profile),
  }
}

export function computeDashboardProfile(hints = {}) {
  const normalized = normalizeHints(hints)
  const viewport = VIEWPORT_PROFILES[normalized.viewport]
  const density = DENSITY_PROFILES[normalized.density]
  const audience = AUDIENCE_PROFILES[normalized.audience]
  const mood = MOOD_PROFILES[normalized.mood]
  const orderedMetrics = audience.order.map(metricById).filter(Boolean)

  return {
    hints: normalized,
    viewport,
    density,
    audience,
    mood,
    orderedMetrics,
    layout: {
      columns: viewport.columns,
      minWidth: `${viewport.minWidth}px`,
      targetWidth: `${viewport.targetWidth}px`,
      gap: density.gridGap,
      rootPadding: density.rootPadding,
    },
    surface: {
      background: mood.surface,
      padding: density.cardPadding,
      minHeight: density.cardMinHeight,
      shadow: mood.shadow,
    },
    typography: {
      labelColor: audience.labelColor,
      labelFamily: audience.labelFamily,
      labelSize: density.labelSize,
      valueSize: density.valueSize,
      valueWeight: audience.valueWeight,
    },
    accents: {
      color: mood.accent,
      bg: mood.accentBg,
      warning: mood.warning,
      warningBg: mood.warningBg,
      badgeBg: mood.badgeBg,
      badgeText: mood.badgeText,
    },
    derivationContext: `${audience.context} ${mood.context}`,
  }
}

function baseStyleValues(hints = {}) {
  const profile = computeDashboardProfile(hints)
  const compact = profile.hints.density === 'compact'
  return {
    'root.surface': [
      'background: #f8fafc',
      'border: 1px solid rgba(203, 213, 225, 0.95)',
      'border-radius: 12px',
      `padding: ${profile.layout.rootPadding}`,
      'color: #0f172a',
      'box-shadow: 0 18px 38px rgba(15, 23, 42, 0.12)',
    ].join('; ') + ';',
    'grid.rhythm': `gap: ${profile.layout.gap}; align-items: stretch;`,
    'surface.card': [
      `background: linear-gradient(135deg, var(--kpi-accent-bg, ${profile.surface.background}), #ffffff 72%)`,
      'border: 1px solid #dbe3ef',
      `border-left: var(--kpi-accent-width, 4px) solid var(--kpi-accent, ${profile.accents.color})`,
      'border-radius: 8px',
      `box-shadow: var(--kpi-card-shadow, ${profile.surface.shadow})`,
      'display: flex',
      'flex-direction: column',
      'justify-content: space-between',
      `gap: ${compact ? '10px' : '14px'}`,
      `min-height: ${profile.surface.minHeight}`,
      `padding: var(--kpi-card-padding, ${profile.surface.padding})`,
      'overflow: hidden',
    ].join('; ') + ';',
    'typography.label': [
      `color: ${profile.typography.labelColor}`,
      `font-family: ${profile.typography.labelFamily}`,
      `font-size: ${profile.typography.labelSize}`,
      'font-weight: 820',
      'letter-spacing: 0.06em',
      'line-height: 1.12',
      'overflow-wrap: anywhere',
      'text-transform: uppercase',
      'opacity: var(--kpi-muted-opacity, 1)',
    ].join('; ') + ';',
    'typography.value': [
      'color: var(--kpi-value-color, #0f172a)',
      `font-size: ${profile.typography.valueSize}`,
      `font-weight: var(--kpi-value-weight, ${profile.typography.valueWeight})`,
      'font-variant-numeric: tabular-nums',
      'letter-spacing: 0',
      'line-height: 1.02',
      'white-space: nowrap',
    ].join('; ') + ';',
    'badge.metric': [
      'align-items: center',
      `background: var(--kpi-badge-bg, ${profile.accents.badgeBg})`,
      'border: 1px solid rgba(203, 213, 225, 0.92)',
      'border-radius: 999px',
      `color: var(--kpi-status-color, ${profile.accents.badgeText})`,
      'display: inline-flex',
      `font-size: ${compact ? '11px' : '12px'}`,
      'font-weight: 820',
      'line-height: 1',
      'white-space: nowrap',
      `padding: ${profile.density.badgePadding}`,
      'max-width: 100%',
      'width: max-content',
    ].join('; ') + ';',
    'tone.warning': `--kpi-accent: ${profile.accents.warning}; --kpi-accent-bg: ${profile.accents.warningBg}; --kpi-status-color: ${profile.accents.badgeText}; --kpi-badge-bg: ${profile.accents.warningBg};`,
    'tone.positive': `--kpi-accent: ${profile.accents.color}; --kpi-accent-bg: ${profile.accents.bg}; --kpi-status-color: ${profile.accents.color}; --kpi-badge-bg: ${profile.accents.badgeBg};`,
    'emphasis.focal': `--kpi-value-weight: ${profile.hints.audience === 'executive' ? 960 : 840}; --kpi-value-color: #0f172a; --kpi-card-shadow: 0 18px 34px rgba(15, 23, 42, 0.16);`,
    'emphasis.diminished': '--kpi-muted-opacity: 0.68;',
    'contrast.separation': '--kpi-accent: #2563eb; --kpi-accent-bg: rgba(37, 99, 235, 0.10); --kpi-card-shadow: 0 12px 28px rgba(30, 64, 175, 0.14);',
    'contrast.similarity': '--kpi-accent: #94a3b8; --kpi-accent-bg: rgba(148, 163, 184, 0.10);',
    'palette.temperature': `--kpi-accent: ${profile.accents.warning}; --kpi-accent-bg: ${profile.accents.warningBg}; --kpi-badge-bg: ${profile.accents.warningBg};`,
    'palette.energy': `filter: saturate(${profile.hints.mood === 'urgent' ? '1.12' : '1.04'});`,
    'rhythm.hierarchy': `--kpi-grid-gap: ${profile.layout.gap}; --kpi-card-padding: ${profile.surface.padding};`,
    'rhythm.density': `--kpi-grid-gap: ${profile.layout.gap}; --kpi-card-padding: ${profile.surface.padding};`,
    'narrative.entry': '--kpi-accent-width: 6px; --kpi-card-shadow: 0 18px 34px rgba(15, 23, 42, 0.16);',
    'narrative.flow': '--kpi-muted-opacity: 0.9;',
  }
}

function contentRefs(metric, attr) {
  return [`node:${metric.id}#attr:${attr}`]
}

function intentRefs(metric, attr) {
  return [`viewspec:binding:${metric.id}_${attr}`]
}

function bindingNode(metric, attr, primitive, styleTokens = []) {
  const key = attr === 'label' ? 'label' : attr === 'trend_label' ? 'trendLabel' : 'value'
  return {
    id: `binding_${metric.id}_${attr}`,
    primitive,
    props: {
      text: String(metric[key]),
    },
    children: [],
    provenance: {
      content_refs: contentRefs(metric, attr),
      intent_refs: intentRefs(metric, attr),
    },
    style_tokens: styleTokens,
  }
}

function hostedDerivations(hints = {}) {
  const profile = computeDashboardProfile(hints)
  const normalized = profile.hints
  const entryMetric = profile.orderedMetrics[0] || METRICS[0]
  const flowMetric = profile.hints.audience === 'executive' ? metricById('kpi_conversion') : metricById('kpi_churn')
  const styleValues = baseStyleValues(normalized)
  const derivations = [
    {
      token: 'tone.warning',
      level: 2,
      target_ir_id: 'motif_kpis_kpi_churn',
      target_content_ref: 'node:kpi_churn#attr:value',
      target_label: 'Churn',
      trigger: 'KL surprise score 0.87 > threshold 0.60',
      reason: 'Churn deviates sharply from its baseline while its polarity is negative.',
      visual_effect: 'Warm surface, heavier border, and warning emphasis.',
      style_value: styleValues['tone.warning'],
    },
    {
      token: 'narrative.entry',
      level: 2,
      target_ir_id: `motif_kpis_${entryMetric.id}`,
      target_content_ref: `node:${entryMetric.id}#attr:value`,
      target_label: entryMetric.label,
      trigger: 'Eigenvector centrality 0.94 is highest in the IR tree',
      reason: profile.hints.audience === 'executive'
        ? 'Revenue is the executive first-read metric and anchors the summary path.'
        : 'Churn is pulled forward because engineering diagnostics prioritize active risk.',
      visual_effect: 'Elevated card and stronger first-read contrast.',
      style_value: styleValues['narrative.entry'],
    },
    {
      token: 'palette.energy',
      level: 2,
      target_ir_id: 'motif_kpis',
      target_content_ref: 'node:premium_dashboard#slot:metrics',
      target_label: 'KPI group',
      trigger: 'Wasserstein 1D cost 0.34 across normalized metric values',
      reason: 'The metric distribution contains enough tension to justify a more saturated read.',
      visual_effect: 'Slight saturation lift across the dashboard.',
      style_value: styleValues['palette.energy'],
    },
    {
      token: 'emphasis.focal',
      level: 2,
      target_ir_id: `binding_${entryMetric.id}_value`,
      target_content_ref: `node:${entryMetric.id}#attr:value`,
      target_label: entryMetric.label,
      trigger: 'Same node as narrative.entry',
      reason: 'The eye should land on the highest centrality metric first.',
      visual_effect: 'Larger value weight and focal scale.',
      style_value: styleValues['emphasis.focal'],
    },
  ]

  if (normalized.audience === 'executive') {
    derivations.push({
      token: 'narrative.flow',
      level: 2,
      target_ir_id: `motif_kpis_${flowMetric.id}`,
      target_content_ref: `node:${flowMetric.id}#attr:value`,
      target_label: flowMetric.label,
      trigger: 'Heat diffusion from Revenue entry point',
      reason: 'The executive reading path moves from revenue into conversion before the supporting volume metrics.',
      visual_effect: 'Secondary weight in the reading path.',
      style_value: styleValues['narrative.flow'],
    })
  } else {
    derivations.push({
      token: 'contrast.separation',
      level: 2,
      target_ir_id: 'motif_kpis_kpi_churn',
      target_content_ref: 'node:kpi_churn#attr:value',
      target_label: 'Churn',
      trigger: 'Engineer audience favors metric separation over narrative flow',
      reason: 'Developer-facing scans benefit from stronger risk boundaries and diagnostic contrast.',
      visual_effect: 'Cooler outline and stronger separation.',
      style_value: styleValues['contrast.separation'],
    })
  }

  if (normalized.mood === 'calm') {
    derivations.push({
      token: 'tone.positive',
      level: 2,
      target_ir_id: 'motif_kpis_kpi_revenue',
      target_content_ref: 'node:kpi_revenue#attr:trend',
      target_label: 'Revenue trend',
      trigger: 'Positive trend 0.22 with calm mood hint',
      reason: 'Growth should read as stable progress rather than urgency.',
      visual_effect: 'Cool positive accent.',
      style_value: styleValues['tone.positive'],
    })
  } else {
    derivations.push({
      token: 'palette.temperature',
      level: 2,
      target_ir_id: 'motif_kpis_kpi_churn',
      target_content_ref: 'node:kpi_churn#attr:trend',
      target_label: 'Churn trend',
      trigger: 'Urgent mood with negative polarity metric',
      reason: 'The palette warms around risk so the problem reads before the number is parsed.',
      visual_effect: 'Warm local temperature around risk.',
      style_value: styleValues['palette.temperature'],
    })
  }

  if (normalized.density === 'compact') {
    derivations.push({
      token: 'rhythm.density',
      level: 2,
      target_ir_id: 'motif_kpis',
      target_content_ref: 'node:premium_dashboard#slot:metrics',
      target_label: 'Dashboard rhythm',
      trigger: 'Compact density hint',
      reason: 'The compiler compresses gaps, card height, and badge copy while preserving all bindings.',
      visual_effect: 'Compressed gap and padding curve.',
      style_value: styleValues['rhythm.density'],
    })
  } else {
    derivations.push({
      token: 'rhythm.hierarchy',
      level: 2,
      target_ir_id: 'motif_kpis',
      target_content_ref: 'node:premium_dashboard#slot:metrics',
      target_label: 'Dashboard hierarchy',
      trigger: 'IR tree depth 3 with dashboard motif',
      reason: 'Breathe density gives the dashboard room for stronger value scale and reading hierarchy.',
      visual_effect: 'Clearer label-to-value scale relationship.',
      style_value: styleValues['rhythm.hierarchy'],
    })
  }

  return derivations
}

export function buildStaticCompileResult(hints = {}, options = {}) {
  const normalized = normalizeHints(hints)
  const profile = computeDashboardProfile(normalized)
  const hosted = options.mode !== 'reference'
  const derivations = hosted ? hostedDerivations(normalized) : []
  const activeTokensByTarget = new Map()
  derivations.forEach((derivation) => {
    const current = activeTokensByTarget.get(derivation.target_ir_id) || []
    current.push(derivation.token)
    activeTokensByTarget.set(derivation.target_ir_id, current)
  })

  const metricOrder = hosted ? profile.orderedMetrics : METRICS
  const cardChildren = metricOrder.map((sourceMetric) => {
    const metric = displayMetric(sourceMetric, profile)
    const cardTokens = ['surface.card']
    if (hosted) cardTokens.push(...(activeTokensByTarget.get(`motif_kpis_${metric.id}`) || []))
    const valueTokens = ['typography.value']
    if (hosted) valueTokens.push(...(activeTokensByTarget.get(`binding_${metric.id}_value`) || []))
    const badgeTokens = ['badge.metric', metric.polarity === 'negative' ? 'tone.warning' : 'tone.positive']
    const labelTokens = ['typography.label']
    if (!hosted && metric.id !== 'kpi_revenue') labelTokens.push('emphasis.diminished')
    return {
      id: `motif_kpis_${metric.id}`,
      primitive: 'surface',
      props: { layout_role: 'surface', motif_kind: 'dashboard' },
      children: [
        bindingNode(metric, 'label', 'label', labelTokens),
        bindingNode(metric, 'value', 'value', valueTokens),
        bindingNode(metric, 'trend_label', 'badge', badgeTokens),
      ],
      provenance: {
        content_refs: [],
        intent_refs: ['viewspec:motif:kpis'],
      },
      style_tokens: cardTokens,
    }
  })

  const columns = profile.layout.columns
  const root = {
    id: 'region_root',
    primitive: 'root',
    props: { layout_role: 'root' },
    children: [
      {
        id: 'region_main',
        primitive: 'stack',
        props: { layout_role: 'stack' },
        children: [
          {
            id: 'motif_kpis',
            primitive: 'grid',
            props: { layout_role: 'grid', motif_kind: 'dashboard', columns },
            children: cardChildren,
            provenance: {
              content_refs: [],
              intent_refs: ['viewspec:motif:kpis'],
            },
            style_tokens: ['grid.rhythm', ...(hosted ? activeTokensByTarget.get('motif_kpis') || [] : [])],
          },
        ],
        provenance: {
          content_refs: [],
          intent_refs: ['viewspec:region:main'],
        },
        style_tokens: [],
      },
    ],
    provenance: {
      content_refs: [],
      intent_refs: ['viewspec:view:premium_kpi_dashboard', 'viewspec:region:root'],
    },
    style_tokens: ['root.surface'],
  }

  const styleValues = baseStyleValues(normalized)
  return {
    ast: {
      result: {
        root: { root },
        diagnostics: [],
      },
      style_values: styleValues,
      title: hosted ? 'premium_kpi_dashboard_hosted' : 'premium_kpi_dashboard_reference',
    },
    meta: {
      request_id: hosted ? 'static-hosted-fixture' : 'static-reference-fixture',
      compiler: hosted ? 'hosted-level2-static-fixture' : 'reference-static-fixture',
      compile_ms: hosted ? 3.2 : 1.1,
      ir_node_count: 19,
      style_token_count: hosted ? Object.keys(styleValues).length : 8,
    },
    derivations,
    quota: {
      limit_per_day: 500,
      remaining: 500,
      reset_at: 'demo',
    },
    __static: true,
  }
}
