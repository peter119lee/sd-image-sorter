import { expect, test, type Page } from '../fixtures/click-ledger'

/**
 * Characterization pins for the vlm-caption.js god-file (1,073 lines) — "step 0" of a
 * later VERBATIM decomposition (mirrors the shipped gallery.js -> gallery/*.js,
 * app.js -> app/*.js, image-reader, similar, smart-tag, censor, dataset, autosep,
 * manual-sort, prompt-lab, v321-ui, artist-ident splits).
 *
 * ASSEMBLY-SHAPE VERDICT:
 *   vlm-caption.js is a single object LITERAL —
 *       `const VLMCaption = { ...~1060 lines... };`  (line 6)
 *       `window.VLMCaption = VLMCaption;`            (line 1073, EOF publish)
 *   — that is NOT wrapped in an IIFE and holds NO closure-private state (every method
 *   uses `this.*` + `window.*` globals + the bare global `escapeHtml`). That is the exact
 *   shape gallery.js / artist-ident.js / image-reader.js have, so — unlike
 *   queue-solitaire.js's true-IIFE exemption — it is fully splittable by reassembling the
 *   object incrementally (`Object.assign(window.VLMCaption, {...})`). The object is NOT
 *   sealed. The file has NO 'use strict' directive (classic script, sloppy mode): the bare
 *   `escapeHtml` read (app/constants-prefs.js defines it as a top-level global, loaded at
 *   index.html:7061 BEFORE vlm-caption.js at 7335) resolves via the shared global scope,
 *   so the split MUST keep the family in classic-script global scope and must NOT
 *   re-`const`-declare VLMCaption across split files.
 *
 * BIDIRECTIONAL SEAM (both directions pinned below):
 *   Inbound — window.VLMCaption.* callers the split MUST keep working (grep is exhaustive):
 *     - app/toasts-modals.js:394  -> window.VLMCaption.openSettingsModal()  (openVlmSettings)
 *     - v321/tagger-picker.js:439 -> window.VLMCaption.startBatchCaption()  (VLM tag route)
 *     - v321/tagger-tabs.js:242/294/363 + tagger-picker.js:73
 *                                 -> window.VLMCaption.syncWorkflowVisibility()
 *   Outbound — globals VLMCaption reads at call time (must stay resolvable post-split):
 *     - window.V321Integration.{refreshVLMBannerStatus, activeTaggerTab, syncVisibleTaggerCopy}
 *     - window.App.{showConfirm, AppState, buildSelectionFilterRequest}, window.AppFilterAccess.*
 *     - window.{loadImages, loadStats, Gallery, confirm, I18n}, bare escapeHtml
 *
 * backend/tests/test_frontend_contract.py reads frontend/js/vlm-caption.js DIRECTLY (as a
 * single file, not a family) in three tests — the split agent must convert those reads to a
 * family concatenation the same way the gallery/app splits did:
 *   - test_v321_modules_read_runtime_selection_store_from_window_app  (line ~360)
 *   - test_full_selection_workflows_do_not_fallback_to_gallery_dom    (line ~486)
 *   - test_vlm_caption_uses_selection_token_without_resolving_full_id_list (line ~651)
 * The selection-token-not-full-id-list invariant those pin is re-pinned behaviorally below
 * (see "_getBatchTarget prefers the selection token").
 *
 * No models and no Ollama/proxy: every case drives VLMCaption in-page via direct method
 * calls + route-mocked /api/vlm/* responses. This avoids the shared-DB / missing-model
 * dependencies. It MUST pass before AND after the refactor.
 */

test.describe.configure({ mode: 'serial' })

/**
 * Land on the app, wait for window.VLMCaption + the bare escapeHtml global, then reset the
 * object's mutable state (and stop any boot-time poll) so serial tests do not leak into each
 * other. Deliberately does NOT re-run init(): the pins call VLMCaption methods directly.
 */
async function gotoVlm(page: Page): Promise<void> {
  await page.goto('/')
  await page.waitForLoadState('domcontentloaded')
  await page.waitForFunction(() => {
    const w = window as any
    return !!w.VLMCaption
      && typeof w.VLMCaption.openSettingsModal === 'function'
      && typeof w.VLMCaption.startBatchCaption === 'function'
      && typeof w.VLMCaption.syncWorkflowVisibility === 'function'
      && typeof w.escapeHtml === 'function'
  })
  await page.evaluate(() => {
    const V = (window as any).VLMCaption
    V.stopPolling()
    V.isRunning = false
    V._startInFlight = false
    V.lastProgress = null
    V.lastFailedImageIds = []
    V._queuedSince = 0
  })
}

/**
 * Reveal the NL/VLM workflow context so the visibility-gated methods
 * (_showBatchUI / syncWorkflowVisibility / _syncRetryFailedButton) render: the card is
 * shown only when V321Integration.activeTaggerTab === 'nl' AND the NL source is 'vlm'.
 */
async function makeWorkflowContextVisible(page: Page): Promise<void> {
  await page.evaluate(() => {
    const w = window as any
    w.V321Integration = w.V321Integration || {}
    w.V321Integration.activeTaggerTab = 'nl'
    const radio = document.querySelector('input[name="tagger-nl-source"][value="vlm"]') as HTMLInputElement | null
    if (radio) radio.checked = true
  })
}

test.beforeEach(async ({ page }) => {
  await gotoVlm(page)
})

// ---------------------------------------------------------------------------
// 1. Public surface — the (unsealed) window.VLMCaption other modules depend on.
// ---------------------------------------------------------------------------

test('window.VLMCaption is an unsealed object literal exposing the seams + documented default state', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const V = (window as any).VLMCaption
    const requiredFns = [
      // inbound cross-module seams:
      'openSettingsModal',      // app/toasts-modals.js -> openVlmSettings()
      'startBatchCaption',      // v321/tagger-picker.js -> VLM batch route
      'syncWorkflowVisibility', // v321/tagger-tabs.js + tagger-picker.js
      // boot + lifecycle:
      'init', 'bindEvents', 'loadSettings', 'resumeActiveBatch', 'tText',
      // settings form:
      'populateSettingsForm', '_collectSettingsForm', 'saveSettings', '_saveBeforeAction',
      '_setOutputFormat', '_getOutputFormat', '_bindOutputFormat',
      '_refreshAdvancedSectionsVisibility', '_autoDetectProvider', 'applyPreset',
      'testConnection', 'fetchModels',
      // batch:
      'cancelBatch', 'retryFailedImages', 'startPolling', 'stopPolling', 'pollProgress',
      '_updateProgressUI', '_showBatchSummary', '_showBatchUI', '_getBatchTarget',
      '_buildImageIdsBatchTarget', '_extractFailedImageIds', '_getFailedImageIds',
      '_isVlmWorkflowVisibleContext', '_syncRetryFailedButton', '_syncTaggerActionState',
      // local models:
      'loadRecommendedModels', '_populateModelSuggestions', 'confirmDeleteModel',
      'deleteModel', 'pullModel', '_startPullPolling', 'startOllama',
      // debug chat + helpers:
      'openDebugChat', 'closeDebugChat', 'loadDebugChat', '_renderDebugChatEvent',
      '_formatApiStatus', '_showStatus', '_setVal', '_getVal', '_setChecked', '_getChecked', '_t',
    ]
    const requiredProps = ['isRunning', '_startInFlight', 'pollInterval', 'settings', 'lastProgress', 'lastFailedImageIds', '_pullPollInterval']
    return {
      isObject: V !== null && typeof V === 'object',
      sealed: Object.isSealed(V),
      identity: (window as any).VLMCaption === V,
      missingFns: requiredFns.filter((k) => typeof V[k] !== 'function'),
      missingProps: requiredProps.filter((k) => !(k in V)),
      isRunning: V.isRunning,
      startInFlight: V._startInFlight,
      pollInterval: V.pollInterval,
      settingsType: typeof V.settings,
      lastProgress: V.lastProgress,
      lastFailedIsArray: Array.isArray(V.lastFailedImageIds),
      pullPollInterval: V._pullPollInterval,
    }
  })

  expect(probe.isObject).toBe(true)
  // Deliberately NOT sealed: the split reassembles it with Object.assign.
  expect(probe.sealed).toBe(false)
  expect(probe.identity).toBe(true)
  expect(probe.missingFns).toEqual([])
  expect(probe.missingProps).toEqual([])
  expect(probe.isRunning).toBe(false)
  expect(probe.startInFlight).toBe(false)
  expect(probe.pollInterval).toBeNull()
  expect(probe.settingsType).toBe('object')
  expect(probe.lastProgress).toBeNull()
  expect(probe.lastFailedIsArray).toBe(true)
  expect(probe.pullPollInterval).toBeNull()
})

// ---------------------------------------------------------------------------
// 2. openSettingsModal (inbound seam) + populateSettingsForm — settings -> DOM.
// ---------------------------------------------------------------------------

test('openSettingsModal shows the modal and populateSettingsForm maps settings (proxies, output format, vertex, masked fields) into the DOM', async ({ page }) => {
  await page.route('**/api/vlm/local-models/recommended', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ollama_installed: false, models: [] }) }))

  const probe = await page.evaluate(() => {
    const V = (window as any).VLMCaption
    V.settings = {
      provider: 'gemini',
      endpoint: 'https://example/v1',
      api_key_display: '*** (configured)',
      model: 'qwen2.5-vl:7b',
      caption_max_tokens: 2048,
      caption_temperature: 0, // 0 must round-trip (not treated as "unset")
      max_image_size: 768,
      system_prompt: 'sys',
      user_prompt: 'usr',
      user_prompt_with_tags: 'usr+tags',
      include_tags_as_context: false,
      output_format: 'both',
      http_proxy: 'http://p:8080',
      https_proxy: 'http://s:8080',
      socks_proxy: 'socks5://l:1080',
      use_vertex: true,
      vertex_project: 'proj',
      vertex_location: 'europe-west1',
      service_account_json_display: '*** (configured)',
    }
    V.openSettingsModal()
    const val = (id: string) => (document.getElementById(id) as HTMLInputElement)?.value
    const checked = (id: string) => (document.getElementById(id) as HTMLInputElement)?.checked
    return {
      modalVisible: document.getElementById('vlm-settings-modal')?.classList.contains('visible'),
      provider: val('vlm-provider'),
      endpoint: val('vlm-endpoint'),
      apiKey: val('vlm-api-key'),
      model: val('vlm-model'),
      maxTokens: val('vlm-caption-max-tokens'),
      temperature: val('vlm-caption-temperature'),
      httpProxy: val('vlm-http-proxy'),
      httpsProxy: val('vlm-https-proxy'),
      socksProxy: val('vlm-socks-proxy'),
      includeTags: checked('vlm-include-tags'),
      useVertex: checked('vlm-use-vertex'),
      vertexProject: val('vlm-vertex-project'),
      vertexLocation: val('vlm-vertex-location'),
      saJson: val('vlm-vertex-sa-json'),
      outputFormat: V._getOutputFormat(),
      currentPresetWithTags: V._currentPresetWithTags,
      vertexDetailsHidden: (document.getElementById('vlm-vertex-details') as HTMLElement)?.hidden,
      proxyBadgeHidden: (document.getElementById('vlm-proxy-active-badge') as HTMLElement)?.hidden,
    }
  })

  expect(probe.modalVisible).toBe(true)
  expect(probe.provider).toBe('gemini')
  expect(probe.endpoint).toBe('https://example/v1')
  expect(probe.apiKey).toBe('*** (configured)') // shown from api_key_display
  expect(probe.model).toBe('qwen2.5-vl:7b')
  expect(probe.maxTokens).toBe('2048')
  expect(probe.temperature).toBe('0')
  expect(probe.httpProxy).toBe('http://p:8080')
  expect(probe.httpsProxy).toBe('http://s:8080')
  expect(probe.socksProxy).toBe('socks5://l:1080')
  expect(probe.includeTags).toBe(false)
  expect(probe.useVertex).toBe(true)
  expect(probe.vertexProject).toBe('proj')
  expect(probe.vertexLocation).toBe('europe-west1')
  expect(probe.saJson).toBe('') // masked service account -> textarea cleared
  expect(probe.outputFormat).toBe('both')
  expect(probe.currentPresetWithTags).toBe('usr+tags')
  expect(probe.vertexDetailsHidden).toBe(false) // provider=gemini reveals the vertex section
  expect(probe.proxyBadgeHidden).toBe(false) // proxies present -> "active" badge shown
})

// ---------------------------------------------------------------------------
// 3. _collectSettingsForm — DOM -> save payload (guards + masking omission).
// ---------------------------------------------------------------------------

test('_collectSettingsForm serializes the form: temperature 0 kept, masked api_key/service-account omitted, proxies + vertex included', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const V = (window as any).VLMCaption
    const set = (id: string, v: string) => { const el = document.getElementById(id) as HTMLInputElement; if (el) el.value = v }
    const setChk = (id: string, v: boolean) => { const el = document.getElementById(id) as HTMLInputElement; if (el) el.checked = v }
    set('vlm-provider', 'openai_compat')
    set('vlm-endpoint', 'http://localhost:11434/v1')
    set('vlm-model', 'm')
    set('vlm-max-retries', '4')
    set('vlm-timeout', '45')
    set('vlm-concurrent', '3')
    set('vlm-caption-max-tokens', '1500')
    set('vlm-caption-temperature', '0') // legitimate 0 -> kept
    set('vlm-retry-delay', 'not-a-number') // NaN -> default 2
    set('vlm-max-image-size', '900')
    set('vlm-http-proxy', 'http://p')
    set('vlm-https-proxy', 'http://s')
    set('vlm-socks-proxy', 'socks5://l')
    setChk('vlm-use-vertex', true)
    set('vlm-vertex-project', 'proj')
    set('vlm-vertex-location', '') // empty -> default us-central1
    set('vlm-api-key', '*** (configured)') // masked -> omitted
    set('vlm-vertex-sa-json', '') // empty -> omitted
    V._currentPresetWithTags = 'withtags'
    const masked = V._collectSettingsForm()

    set('vlm-api-key', 'sk-real-key') // real -> included
    set('vlm-vertex-sa-json', '{"type":"service_account"}')
    const real = V._collectSettingsForm()
    return { masked, real }
  })

  expect(probe.masked.caption_temperature).toBe(0)
  expect(probe.masked.retry_delay_seconds).toBe(2)
  expect(probe.masked.max_retries).toBe(4)
  expect(probe.masked.concurrent_requests).toBe(3)
  expect(probe.masked.caption_max_tokens).toBe(1500)
  expect(probe.masked.max_image_size).toBe(900)
  expect(probe.masked.http_proxy).toBe('http://p')
  expect(probe.masked.https_proxy).toBe('http://s')
  expect(probe.masked.socks_proxy).toBe('socks5://l')
  expect(probe.masked.use_vertex).toBe(true)
  expect(probe.masked.vertex_project).toBe('proj')
  expect(probe.masked.vertex_location).toBe('us-central1') // empty -> default
  expect(probe.masked.user_prompt_with_tags).toBe('withtags')
  expect('api_key' in probe.masked).toBe(false) // masked value not sent
  expect('service_account_json' in probe.masked).toBe(false)
  expect(probe.real.api_key).toBe('sk-real-key') // freshly typed -> sent
  expect(probe.real.service_account_json).toBe('{"type":"service_account"}')
})

// ---------------------------------------------------------------------------
// 4. saveSettings — POST round-trip + V321Integration outbound seam + error branch.
// ---------------------------------------------------------------------------

test('saveSettings POSTs the collected form, merges settings, notifies V321Integration, and surfaces backend errors', async ({ page }) => {
  let postBody: Record<string, unknown> | null = null
  let saveStatus = 200
  await page.route('**/api/vlm/settings', (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    postBody = JSON.parse(route.request().postData() || '{}')
    if (saveStatus === 200) return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'ok' }) })
    return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ detail: 'boom' }) })
  })

  const ok = await page.evaluate(async () => {
    const w = window as any
    const V = w.VLMCaption
    w.V321Integration = w.V321Integration || {}
    let bannerRefreshed = 0
    w.V321Integration.refreshVLMBannerStatus = () => { bannerRefreshed += 1 }
    ;(document.getElementById('vlm-endpoint') as HTMLInputElement).value = 'http://x/v1'
    ;(document.getElementById('vlm-api-key') as HTMLInputElement).value = 'sk-abc'
    const result = await V.saveSettings()
    return {
      result,
      bannerRefreshed,
      statusClass: document.getElementById('vlm-status')?.className,
      endpointInSettings: V.settings.endpoint,
      apiKeyDeletedFromSettings: !('api_key' in V.settings),
    }
  })

  expect(ok.result).toBe(true)
  expect((postBody as any)?.endpoint).toBe('http://x/v1')
  expect((postBody as any)?.api_key).toBe('sk-abc')
  expect(ok.bannerRefreshed).toBe(1) // outbound V321Integration.refreshVLMBannerStatus seam
  expect(ok.statusClass).toContain('vlm-status-success')
  expect(ok.endpointInSettings).toBe('http://x/v1')
  expect(ok.apiKeyDeletedFromSettings).toBe(true) // api_key never retained in this.settings

  saveStatus = 500
  const fail = await page.evaluate(async () => {
    const V = (window as any).VLMCaption
    const result = await V.saveSettings()
    return { result, statusClass: document.getElementById('vlm-status')?.className }
  })
  expect(fail.result).toBe(false)
  expect(fail.statusClass).toContain('vlm-status-error')
})

// ---------------------------------------------------------------------------
// 5. _getBatchTarget / _buildImageIdsBatchTarget — the selection-scope contract.
//    (Re-pins the backend test_vlm_caption_uses_selection_token invariant behaviorally.)
// ---------------------------------------------------------------------------

test('_getBatchTarget prefers the selection token, then explicit ids, then filters — never resolving a full id list itself', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const w = window as any
    const V = w.VLMCaption
    const realApp = w.App
    const realAccess = w.AppFilterAccess

    // 1) A filtered selection token wins — sent verbatim, no id expansion.
    w.AppFilterAccess = { getActiveSelectionToken: () => 'tok-123', getSelectionTotal: () => 42, getSelectedImageIds: () => [1, 2, 3] }
    const tokenTarget = V._getBatchTarget()

    // 2) No token -> explicitly selected ids (normalized: coerce, drop <= 0 / NaN).
    w.AppFilterAccess = { getActiveSelectionToken: () => null, getSelectionTotal: () => 0, getSelectedImageIds: () => [10, '20', -5, 0, 30] }
    const idsTarget = V._getBatchTarget()

    // 3) No token, no selection -> current-view filters (backend expands server-side).
    //    Fully stub window.App so this is seal-proof (real window.App is Object.seal'd).
    w.AppFilterAccess = { getActiveSelectionToken: () => null, getSelectionTotal: () => 0, getSelectedImageIds: () => [] }
    w.App = { AppState: { images: [{ id: 7 }, { id: 8 }], pagination: { total: 99 } }, buildSelectionFilterRequest: () => ({ folder: 'F' }) }
    const filtersTarget = V._getBatchTarget()
    w.App = realApp
    w.AppFilterAccess = realAccess

    const normalized = V._buildImageIdsBatchTarget([5, '6', 0, -1, NaN])
    return { tokenTarget, idsTarget, filtersTarget, normalized }
  })

  expect(probe.tokenTarget).toEqual({ count: 42, payload: { selection_token: 'tok-123' } })
  expect(probe.idsTarget).toEqual({ count: 3, payload: { image_ids: [10, 20, 30] } })
  expect(probe.filtersTarget).toEqual({ count: 99, payload: { filters: { folder: 'F' } } })
  expect(probe.normalized).toEqual({ count: 2, payload: { image_ids: [5, 6] } })
})

// ---------------------------------------------------------------------------
// 6. startBatchCaption (inbound seam) — double-click guard + started/empty/409/queued.
// ---------------------------------------------------------------------------

test('startBatchCaption guards double-clicks, posts the batch payload, and handles started / empty / 409 / queued responses', async ({ page }) => {
  await makeWorkflowContextVisible(page)
  let postCount = 0
  let postBody: Record<string, unknown> | null = null
  let mode: 'started' | 'conflict' | 'queued' = 'started'
  await page.route('**/api/vlm/caption-batch', (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    postCount += 1
    postBody = JSON.parse(route.request().postData() || '{}')
    if (mode === 'conflict') return route.fulfill({ status: 409, contentType: 'application/json', body: '{}' })
    if (mode === 'queued') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'queued', pipeline_queued: true }) })
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'started' }) })
  })
  await page.route('**/api/vlm/caption-batch/progress', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ running: true, total: 2, completed: 0, failed: 0 }) }))

  // Double-click guard: an in-flight start blocks a second call (no POST fires).
  const guarded = await page.evaluate(async () => {
    const w = window as any
    const V = w.VLMCaption
    w.AppFilterAccess = { getActiveSelectionToken: () => null, getSelectionTotal: () => 0, getSelectedImageIds: () => [11, 22] }
    V._startInFlight = true
    await V.startBatchCaption()
    V._startInFlight = false
    return { isRunning: V.isRunning }
  })
  expect(guarded.isRunning).toBe(false)
  expect(postCount).toBe(0)

  // Empty target: no POST, error status, not running.
  const empty = await page.evaluate(async () => {
    const w = window as any
    const V = w.VLMCaption
    w.AppFilterAccess = { getActiveSelectionToken: () => null, getSelectionTotal: () => 0, getSelectedImageIds: () => [] }
    w.App = { AppState: { images: [] }, buildSelectionFilterRequest: () => ({}) }
    await V.startBatchCaption()
    return { isRunning: V.isRunning, statusClass: document.getElementById('vlm-batch-status')?.className }
  })
  expect(postCount).toBe(0)
  expect(empty.isRunning).toBe(false)
  expect(empty.statusClass).toContain('vlm-status-error')

  // Started: posts the id payload verbatim, flips running, starts polling.
  const started = await page.evaluate(async () => {
    const w = window as any
    const V = w.VLMCaption
    w.AppFilterAccess = { getActiveSelectionToken: () => null, getSelectionTotal: () => 0, getSelectedImageIds: () => [11, 22] }
    await V.startBatchCaption()
    const running = V.isRunning
    V.stopPolling() // avoid interval leak within the test
    return { running }
  })
  expect(postCount).toBe(1)
  expect(postBody).toEqual({ image_ids: [11, 22] })
  expect(started.running).toBe(true)

  // Reset, then 409 conflict -> not running + error status.
  await page.evaluate(() => { const V = (window as any).VLMCaption; V.stopPolling(); V.isRunning = false; V._startInFlight = false })
  mode = 'conflict'
  const conflict = await page.evaluate(async () => {
    const w = window as any
    const V = w.VLMCaption
    w.AppFilterAccess = { getActiveSelectionToken: () => null, getSelectionTotal: () => 0, getSelectedImageIds: () => [11, 22] }
    await V.startBatchCaption()
    return { isRunning: V.isRunning, statusClass: document.getElementById('vlm-batch-status')?.className }
  })
  expect(postCount).toBe(2)
  expect(conflict.isRunning).toBe(false)
  expect(conflict.statusClass).toContain('vlm-status-error')

  // Queued (200 pipeline_queued): running true + queued timestamp + polling starts.
  mode = 'queued'
  const queued = await page.evaluate(async () => {
    const w = window as any
    const V = w.VLMCaption
    w.AppFilterAccess = { getActiveSelectionToken: () => null, getSelectionTotal: () => 0, getSelectedImageIds: () => [11, 22] }
    await V.startBatchCaption()
    const res = { running: V.isRunning, queuedSince: V._queuedSince }
    V.stopPolling()
    V.isRunning = false
    return res
  })
  expect(postCount).toBe(3)
  expect(queued.running).toBe(true)
  expect(queued.queuedSince).toBeGreaterThan(0)
})

// ---------------------------------------------------------------------------
// 7. pollProgress completion — stop, summary, gallery refresh, failed ids, event.
// ---------------------------------------------------------------------------

test('pollProgress completion stops polling, renders the summary, refreshes gallery/stats, records failed ids, and emits vlmBatchCompleted', async ({ page }) => {
  await makeWorkflowContextVisible(page)
  await page.route('**/api/vlm/caption-batch/progress', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        running: false, total: 3, completed: 2, failed: 1, tokens_used: 120,
        errors: [{ image_id: 55, error: 'nsfw refuse', error_type: 'refusal' }],
      }),
    }))
  await page.route('**/api/vlm/caption-batch/debug-chat', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ events: [] }) }))

  const probe = await page.evaluate(async () => {
    const w = window as any
    const V = w.VLMCaption
    let loadImagesCalls = 0
    let loadStatsCalls = 0
    w.loadImages = () => { loadImagesCalls += 1 }
    w.loadStats = () => { loadStatsCalls += 1 }
    let event: any = null
    document.addEventListener('vlmBatchCompleted', (e: any) => { event = e.detail }, { once: true })
    V.isRunning = true
    V.startPolling() // registers interval + fires an immediate pollProgress()
    await new Promise((r) => setTimeout(r, 150))
    V.stopPolling()
    return {
      isRunning: V.isRunning,
      pollInterval: V.pollInterval,
      lastFailedImageIds: V.lastFailedImageIds,
      statusClass: document.getElementById('vlm-batch-status')?.className,
      loadImagesCalls,
      loadStatsCalls,
      event,
      retryDisplay: (document.getElementById('btn-vlm-retry-failed') as HTMLElement)?.style.display,
    }
  })

  expect(probe.isRunning).toBe(false)
  expect(probe.pollInterval).toBeNull()
  expect(probe.lastFailedImageIds).toEqual([55])
  expect(probe.statusClass).toContain('vlm-status-warning') // failed > 0 -> warning
  expect(probe.loadImagesCalls).toBeGreaterThanOrEqual(1)
  expect(probe.loadStatsCalls).toBeGreaterThanOrEqual(1)
  expect(probe.event).toEqual({ completed: 2, failed: 1, tokens_used: 120 })
  expect(probe.retryDisplay).toBe('inline-flex') // failed ids + visible context -> retry shown
})

// ---------------------------------------------------------------------------
// 8. pollProgress queued state (v3.4.1 AI job queue) — keep polling, surface position.
// ---------------------------------------------------------------------------

test('pollProgress renders the AI-job-queue waiting state and keeps polling until the batch actually starts', async ({ page }) => {
  await makeWorkflowContextVisible(page)
  await page.route('**/api/vlm/caption-batch/progress', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ running: false, pipeline_queue: { queued: [{ position: 3 }] } }) }))

  const probe = await page.evaluate(async () => {
    const w = window as any
    const V = w.VLMCaption
    V.isRunning = true
    V._queuedSince = Date.now()
    await V.pollProgress()
    const res = {
      isRunning: V.isRunning, // stays running (still queued, no completion)
      statusText: document.getElementById('vlm-batch-status')?.textContent,
      statusClass: document.getElementById('vlm-batch-status')?.className,
      workflowDisplay: (document.getElementById('tagger-nl-workflow-card') as HTMLElement)?.style.display,
    }
    V.stopPolling()
    V.isRunning = false
    return res
  })

  expect(probe.isRunning).toBe(true)
  expect(probe.statusText).toContain('3') // queue position surfaced
  expect(probe.statusClass).toContain('vlm-status-info')
  expect(probe.workflowDisplay).toBe('grid') // batch UI kept visible while queued
})

// ---------------------------------------------------------------------------
// 9. fetchModels — save-first, list returned models, select one into the input.
// ---------------------------------------------------------------------------

test('fetchModels saves first, lists returned models as clickable buttons, and selecting one fills the model input', async ({ page }) => {
  await page.route('**/api/vlm/settings', (route) => {
    if (route.request().method() === 'POST') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    return route.continue()
  })
  await page.route('**/api/vlm/models', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ models: ['qwen2.5-vl:7b', 'minicpm-v'] }) }))

  const probe = await page.evaluate(async () => {
    const V = (window as any).VLMCaption
    await V.fetchModels()
    const list = document.getElementById('vlm-model-list') as HTMLElement
    const buttons = Array.from(list.querySelectorAll('.vlm-model-item')) as HTMLElement[]
    buttons[1]?.click()
    return {
      count: buttons.length,
      first: buttons[0]?.getAttribute('data-model'),
      selectedModel: (document.getElementById('vlm-model') as HTMLInputElement)?.value,
      statusClass: document.getElementById('vlm-model-list-status')?.className,
    }
  })

  expect(probe.count).toBe(2)
  expect(probe.first).toBe('qwen2.5-vl:7b')
  expect(probe.selectedModel).toBe('minicpm-v') // clicking a model item fills #vlm-model
  expect(probe.statusClass).toContain('vlm-status-success')
})

// ---------------------------------------------------------------------------
// 10. loadRecommendedModels — datalist + Ollama cards + "Use This" endpoint wiring.
// ---------------------------------------------------------------------------

test('loadRecommendedModels fills the model datalist + Ollama cards and "Use This" wires the local endpoint/provider', async ({ page }) => {
  await page.route('**/api/vlm/local-models/recommended', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ollama_installed: true,
        ollama_running: true,
        models: [
          { id: 'qwen3-vl:8b-instruct', name: 'Qwen3-VL 8B Instruct', description: 'desc', installed: true, nsfw_ok: true, size_gb: 6.1, vram_min_gb: 6 },
          { id: 'minicpm-v', name: 'MiniCPM-V', description: 'desc2', installed: false, nsfw_ok: false, size_gb: 5, vram_min_gb: 6 },
        ],
      }),
    }))

  const probe = await page.evaluate(async () => {
    const V = (window as any).VLMCaption
    await V.loadRecommendedModels()
    const container = document.getElementById('vlm-local-models') as HTMLElement
    const datalist = document.getElementById('vlm-model-suggestions') as HTMLElement
    ;(container.querySelector('[data-vlm-use="qwen3-vl:8b-instruct"]') as HTMLElement)?.click()
    return {
      cards: container.querySelectorAll('.vlm-model-card').length,
      datalistCount: datalist.querySelectorAll('option').length,
      pullButtons: container.querySelectorAll('[data-vlm-pull]').length,
      useButtons: container.querySelectorAll('[data-vlm-use]').length,
      model: (document.getElementById('vlm-model') as HTMLInputElement)?.value,
      endpoint: (document.getElementById('vlm-endpoint') as HTMLInputElement)?.value,
      provider: (document.getElementById('vlm-provider') as HTMLSelectElement)?.value,
    }
  })

  expect(probe.cards).toBe(2)
  expect(probe.datalistCount).toBe(2)
  expect(probe.pullButtons).toBe(1) // the not-installed model offers Download
  expect(probe.useButtons).toBe(1) // the installed model offers Use This
  expect(probe.model).toBe('qwen3-vl:8b-instruct')
  expect(probe.endpoint).toBe('http://localhost:11434/v1')
  expect(probe.provider).toBe('openai_compat')
})

// ---------------------------------------------------------------------------
// 11. applyPreset — preset prompts, with-tags variant storage, output format swap.
// ---------------------------------------------------------------------------

test('applyPreset loads the preset prompts, stores the with-tags variant, and switches the output format', async ({ page }) => {
  await page.route('**/api/vlm/presets', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        presets: {
          vlm_danbooru: { name: 'Danbooru Tags', system_prompt: 'sys-p', user_prompt: 'usr-p', user_prompt_with_tags: 'usr-p-tags', output_format: 'danbooru_tags' },
        },
      }),
    }))

  const probe = await page.evaluate(async () => {
    const V = (window as any).VLMCaption
    await V.applyPreset('vlm_danbooru')
    return {
      system: (document.getElementById('vlm-system-prompt') as HTMLTextAreaElement)?.value,
      user: (document.getElementById('vlm-user-prompt') as HTMLTextAreaElement)?.value,
      withTags: V._currentPresetWithTags,
      outputFormat: V._getOutputFormat(),
      statusClass: document.getElementById('vlm-status')?.className,
    }
  })

  expect(probe.system).toBe('sys-p')
  expect(probe.user).toBe('usr-p') // textarea always shows the plain user prompt
  expect(probe.withTags).toBe('usr-p-tags') // with-tags variant stored separately
  expect(probe.outputFormat).toBe('danbooru_tags')
  expect(probe.statusClass).toContain('vlm-status-success')
})

// ---------------------------------------------------------------------------
// 12. Workflow visibility — V321Integration.activeTaggerTab outbound + the v321 seam.
// ---------------------------------------------------------------------------

test('_isVlmWorkflowVisibleContext gates on V321Integration.activeTaggerTab, and _showBatchUI / syncWorkflowVisibility follow it', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const w = window as any
    const V = w.VLMCaption
    w.V321Integration = w.V321Integration || {}
    const workflow = document.getElementById('tagger-nl-workflow-card') as HTMLElement
    const radio = document.querySelector('input[name="tagger-nl-source"][value="vlm"]') as HTMLInputElement
    if (radio) radio.checked = true

    // NL tab + vlm source -> visible context; _showBatchUI(true) reveals the card.
    w.V321Integration.activeTaggerTab = 'nl'
    const nlContext = V._isVlmWorkflowVisibleContext()
    V._showBatchUI(true)
    const shownDisplay = workflow.style.display

    // Local tab -> hidden context; _showBatchUI hides the card even when "running".
    w.V321Integration.activeTaggerTab = 'local'
    const localContext = V._isVlmWorkflowVisibleContext()
    V._showBatchUI(true)
    const hiddenDisplay = workflow.style.display

    // syncWorkflowVisibility (the v321 tagger-tabs/picker seam) with a running batch
    // + visible context -> grid.
    w.V321Integration.activeTaggerTab = 'nl'
    V.isRunning = true
    V.syncWorkflowVisibility()
    const syncDisplay = workflow.style.display
    V.isRunning = false
    return { nlContext, localContext, shownDisplay, hiddenDisplay, syncDisplay }
  })

  expect(probe.nlContext).toBe(true)
  expect(probe.localContext).toBe(false)
  expect(probe.shownDisplay).toBe('grid')
  expect(probe.hiddenDisplay).toBe('none')
  expect(probe.syncDisplay).toBe('grid')
})

// ---------------------------------------------------------------------------
// 13. _autoDetectProvider (WS6) — endpoint URL -> provider select, blank left alone.
// ---------------------------------------------------------------------------

test('_autoDetectProvider classifies the endpoint URL via the backend and syncs the provider select (blank endpoint is left alone)', async ({ page }) => {
  let detectCalls = 0
  await page.route('**/api/vlm/detect-provider', (route) => {
    detectCalls += 1
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ provider: 'gemini' }) })
  })

  const probe = await page.evaluate(async () => {
    const V = (window as any).VLMCaption
    const select = document.getElementById('vlm-provider') as HTMLSelectElement
    select.value = 'openai_compat'
    // Blank endpoint -> no detection call (a deliberate manual pick is preserved).
    ;(document.getElementById('vlm-endpoint') as HTMLInputElement).value = ''
    await V._autoDetectProvider()
    const afterBlank = select.value
    // Real endpoint -> detect + switch (only if the returned provider is a real option).
    ;(document.getElementById('vlm-endpoint') as HTMLInputElement).value = 'https://generativelanguage.googleapis.com'
    await V._autoDetectProvider()
    return { afterBlank, afterDetect: select.value, hasGeminiOption: Array.from(select.options).some((o) => o.value === 'gemini') }
  })

  expect(probe.afterBlank).toBe('openai_compat')
  expect(detectCalls).toBe(1) // blank endpoint skipped the network call
  if (probe.hasGeminiOption) expect(probe.afterDetect).toBe('gemini')
})

// ---------------------------------------------------------------------------
// 14. _formatApiStatus + _updateProgressUI — the batch progress readout formatting.
// ---------------------------------------------------------------------------

test('_formatApiStatus and _updateProgressUI format the batch progress readout', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const V = (window as any).VLMCaption
    const status = V._formatApiStatus({ api_status: 'error', last_api_error: 'boom', active_requests: 2, last_api_latency_ms: 50 })
    V._updateProgressUI({ total: 10, completed: 4, failed: 1, running: true, tokens_used: 20, current_image: 'x.png' })
    return {
      status,
      fill: (document.getElementById('vlm-progress-fill') as HTMLElement)?.style.width,
      text: document.getElementById('vlm-progress-text')?.textContent || '',
      idleStatus: V._formatApiStatus({ running: false }),
    }
  })

  expect(probe.status).toContain('boom') // last_api_error appended on error status
  expect(probe.status).toContain('2') // active request count
  expect(probe.status).toContain('50 ms') // latency
  expect(probe.fill).toBe('50%') // (completed 4 + failed 1) / total 10
  expect(probe.text).toContain('4/10')
  expect(probe.text).toContain('20 tokens')
  expect(probe.text).toContain('x.png')
  expect(probe.idleStatus.length).toBeGreaterThan(0) // idle fallback label rendered
})
