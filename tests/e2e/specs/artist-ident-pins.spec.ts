import { expect, test, type Page } from '../fixtures/click-ledger'

/**
 * Characterization pins for the artist-ident.js god-file (1,171 lines) — "step 0" of a
 * later VERBATIM decomposition (mirrors the shipped gallery.js -> gallery/*.js,
 * app.js -> app/*.js, image-reader, similar, smart-tag, censor, dataset, autosep,
 * manual-sort, prompt-lab, v321-ui splits).
 *
 * ASSEMBLY-SHAPE VERDICT:
 *   artist-ident.js is a single object LITERAL —
 *       `const ArtistIdent = { ...~1160 lines... };`  (line 6)
 *       `window.ArtistIdent = ArtistIdent;`           (line 1168)
 *   — that is NOT wrapped in an IIFE and holds NO closure-private state (every method
 *   uses `this.*` + `window.*` globals). That is the exact shape gallery.js /
 *   image-reader.js / similar.js have, so — unlike queue-solitaire.js's true-IIFE
 *   exemption — it is fully splittable by reassembling the object incrementally
 *   (`Object.assign(window.ArtistIdent, {...})`). The object is NOT sealed. The file has
 *   NO 'use strict' directive (classic script, sloppy mode). This is why the smart-tag
 *   walker reported "0 internal top-level decls": there are none — it is a bare literal.
 *
 * Cross-module consumers the split MUST keep working (grep is exhaustive):
 *   - app/view-switch.js  -> `window.ArtistIdent.init()` when the Artist view activates.
 *   - app/settings.js     -> `window.ArtistIdent.savePreferences()` +
 *                            `window.ArtistIdent.resetSavedPreferences({apply,silent})`.
 *   - gallery/modal-analysis.js -> `window.ArtistIdent.getThresholdValue()`,
 *                            `window.ArtistIdent._getIdentifyModelConfig()` (the single-
 *                            image "Identify Artist" modal button reuses BOTH), and
 *                            `window.ArtistIdent.loadStats()` after a single identify.
 * backend/tests/test_frontend_contract.py does NOT pin any artist-ident.js literal (its
 * only "artist" hit is a dataset tag-pill category), but its generic per-file rules
 * (no `AppState.*` writes, no `window.App.*` writes) DO cover every future
 * frontend/js/artist/*.js file — artist-ident.js already complies (its only filter
 * mutation goes through the sanctioned `window.App.updateFilters(fn)` API).
 *
 * No DB seeding and no Kaloscope/LSNet models: every case drives ArtistIdent in-page via
 * direct method calls + route-mocked /api/artists/* (and /api/images) responses. This
 * avoids the `.tmp/e2e-data-<port>` cross-run pollution pitfall and the missing-model
 * dependency (the feature is experimental — routers/artists.py needs models absent on a
 * clean machine). It MUST pass before AND after the refactor.
 */

test.describe.configure({ mode: 'serial' })

/**
 * Land on the app, wait for window.ArtistIdent + App.API to exist, and reveal
 * #view-artist so its controls are visible (the view is otherwise display:none).
 * Deliberately does NOT call ArtistIdent.init(): the pins call ArtistIdent methods
 * directly, so the real diagnostics/stats/batch-progress boot never fires unless a test
 * opts in via initArtistView(). Also resets the object's mutable state so serial tests
 * do not leak into each other within a shared page (each test still gets a fresh goto).
 */
async function gotoArtist(page: Page): Promise<void> {
  await page.goto('/')
  await page.waitForLoadState('domcontentloaded')
  await page.waitForFunction(() => {
    const w = window as any
    return !!w.ArtistIdent
      && typeof w.ArtistIdent.selectArtist === 'function'
      && typeof w.ArtistIdent.init === 'function'
      && typeof w.App?.API?.get === 'function'
  })
  await page.evaluate(() => {
    const view = document.getElementById('view-artist')
    if (!view) return
    document.querySelectorAll('.view').forEach((node) => {
      if (node !== view) (node as HTMLElement).style.display = 'none'
    })
    ;(view as HTMLElement).style.display = 'block'
    view.classList.add('active')
  })
}

/**
 * Run the real init() once with the boot endpoints mocked to a "ready" state, so
 * bindEvents() wires the delegated document handlers. Used only by the bindEvents pin.
 */
async function initArtistView(page: Page): Promise<void> {
  await page.route('**/api/artists/diagnostics', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ available: true }) }))
  await page.route('**/api/artists/stats', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ total_images: 0, identified_images: 0, undefined_count: 0, artist_counts: {}, artist_stats: {} }),
    }))
  await page.route('**/api/artists/batch-progress', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ running: false }) }))
  await page.evaluate(() => (window as any).ArtistIdent.init())
  await page.waitForFunction(() => (window as any).ArtistIdent.eventsBound === true)
}

test.beforeEach(async ({ page }) => {
  await gotoArtist(page)
})

// ---------------------------------------------------------------------------
// 1. Public surface — the (unsealed) window.ArtistIdent other modules depend on.
// ---------------------------------------------------------------------------

test('window.ArtistIdent is an unsealed object literal exposing the load-bearing surface + documented defaults', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const A = (window as any).ArtistIdent
    // Public entries + the internal methods a bad cut could drop off the reassembled
    // object. The FOUR cross-module seams are called out inline.
    const requiredFns = [
      'init', 'bindEvents',                                    // view-switch.js seam
      'savePreferences', 'resetSavedPreferences',              // settings.js seams
      'getThresholdValue', '_getIdentifyModelConfig',          // modal-analysis.js seams
      'loadStats',                                             // modal-analysis.js + boot
      'applySavedPreferences', 'capturePreferences', 'resetPreferenceControls',
      'syncThresholdDisplay', '_getIdentifyPayload', 'loadDiagnostics', 'renderArtistGrid',
      'getInitials', 'formatArtistName', 'formatConfidencePercent', 'getArtistStat',
      'selectArtist', 'filterGalleryByArtist', 'clearArtistFilter', 'updateProgressUi',
      'resumeBatchProgress', 'identifyAll', 'pollProgress', 'identifySelected',
      'clearAllData', 'refreshAvailabilityState', 'syncSelectionActionState',
      '_buildCompletionToast', '_escapeHtml', '_decodeArtistValue',
      'localizeDiagnosticsMessage', 'dismissFirstUseCard', 'refreshFirstUseCard',
      'showFirstUseGuide', '_syncControls', 'tText', 'tKey',
      // Confidence tiering (2c15c9e): describeArtistResult is the single
      // formatter every caller — including gallery/modal-analysis.js — must use
      // instead of reading `artist`, which is the "undefined" sentinel below the
      // high tier.
      'describeArtistResult', 'confidenceTierLabel', 'renderLowConfidenceArtists',
      'refreshVocabularyState', 'checkArtistVocabulary',
    ]
    const requiredProps = [
      'isIdentifying', 'selectedArtist', 'selectedArtistPageSize',
      'selectedArtistOffset', 'selectedArtistHasMore', 'selectedArtistImages',
      'artistRequestToken', 'statsRequestToken', 'viewMode', 'stats', 'diagnostics', 'eventsBound',
      'progressTracker', 'thresholdDefaults', 'vocabulary',
    ]
    return {
      isObject: A !== null && typeof A === 'object',
      sealed: Object.isSealed(A),
      identity: (window as any).ArtistIdent === A,
      missingFns: requiredFns.filter((k) => typeof A[k] !== 'function'),
      missingProps: requiredProps.filter((k) => !(k in A)),
      isIdentifying: A.isIdentifying,
      viewMode: A.viewMode,
      pageSize: A.selectedArtistPageSize,
      offset: A.selectedArtistOffset,
      hasMore: A.selectedArtistHasMore,
      token: A.artistRequestToken,
      statsToken: A.statsRequestToken,
      selectedArtist: A.selectedArtist,
      diagnostics: A.diagnostics,
      thresholdDefaults: A.thresholdDefaults,
    }
  })

  expect(probe.isObject).toBe(true)
  // Deliberately NOT sealed: the split reassembles it with Object.assign.
  expect(probe.sealed).toBe(false)
  expect(probe.identity).toBe(true)
  expect(probe.missingFns).toEqual([])
  expect(probe.missingProps).toEqual([])
  // Documented default state (the object-literal initializers).
  expect(probe.isIdentifying).toBe(false)
  expect(probe.viewMode).toBe('grid')
  expect(probe.pageSize).toBe(120)
  expect(probe.offset).toBe(0)
  expect(probe.hasMore).toBe(false)
  expect(probe.token).toBe(0)
  expect(probe.statsToken).toBe(0)
  expect(probe.selectedArtist).toBeNull()
  expect(probe.diagnostics).toBeNull()
  // `suggestedLow`/`suggestedHigh` (0.02-0.08) were the pre-tiering "try a
  // lower threshold" band. Since 2c15c9e a score under ARTIST_CONFIDENT_THRESHOLD
  // can no longer have a name written, so lowering the slider cannot produce the
  // result that advice promised; `confident` pins the value that actually gates it.
  expect(probe.thresholdDefaults).toEqual({ value: 0.03, confident: 0.20 })
})

// ---------------------------------------------------------------------------
// 2. getThresholdValue / syncThresholdDisplay — slider read, default fallback, 2dp label.
// ---------------------------------------------------------------------------

test('getThresholdValue reads the slider (default 0.03 when absent) and syncThresholdDisplay renders it to two decimals', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const A = (window as any).ArtistIdent
    const el = document.getElementById('artist-threshold') as HTMLInputElement
    const label = document.getElementById('artist-threshold-value') as HTMLElement
    const original = el.value

    el.value = '0.07'
    const setValue = A.getThresholdValue()
    A.syncThresholdDisplay()
    const setLabel = label.textContent

    // Hide the slider from getElementById -> the `?.value || default` fallback fires.
    el.id = 'artist-threshold-temp'
    const fallbackValue = A.getThresholdValue()
    el.id = 'artist-threshold'
    el.value = original
    return { setValue, setLabel, fallbackValue }
  })

  expect(probe.setValue).toBeCloseTo(0.07, 5)
  expect(probe.setLabel).toBe('0.07')
  // thresholdDefaults.value is the fallback when the slider element is missing.
  expect(probe.fallbackValue).toBeCloseTo(0.03, 5)
})

// ---------------------------------------------------------------------------
// 3. _getIdentifyModelConfig / _getIdentifyPayload — the identify request contract.
// ---------------------------------------------------------------------------

test('_getIdentifyModelConfig maps source/path/gpu, requires a local path, and _getIdentifyPayload adds threshold + top_k:5', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const A = (window as any).ArtistIdent
    const source = document.getElementById('artist-model-source') as HTMLSelectElement
    const path = document.getElementById('artist-model-path') as HTMLInputElement
    const gpu = document.getElementById('artist-use-gpu') as HTMLInputElement
    const threshold = document.getElementById('artist-threshold') as HTMLInputElement

    // huggingface + gpu checked -> model_path null, use_gpu true.
    source.value = 'huggingface'
    path.value = ''
    gpu.checked = true
    const hf = A._getIdentifyModelConfig()

    // local WITHOUT a path throws (the required-path guard).
    source.value = 'local'
    path.value = ''
    let threwForLocal = false
    try { A._getIdentifyModelConfig() } catch (e) { threwForLocal = true }

    // local WITH a (padded) path + gpu unchecked -> trimmed path, use_gpu false.
    source.value = 'local'
    path.value = '  C:/models/best.pth  '
    gpu.checked = false
    const local = A._getIdentifyModelConfig()

    // Payload wraps the config with the slider threshold, top_k:5 and image_ids.
    source.value = 'huggingface'
    path.value = ''
    gpu.checked = true
    threshold.value = '0.05'
    const payload = A._getIdentifyPayload([11, 22, 33])
    return { hf, threwForLocal, local, payload }
  })

  expect(probe.hf).toEqual({ model_source: 'huggingface', model_path: null, use_gpu: true })
  expect(probe.threwForLocal).toBe(true)
  expect(probe.local).toEqual({ model_source: 'local', model_path: 'C:/models/best.pth', use_gpu: false })
  expect(probe.payload.image_ids).toEqual([11, 22, 33])
  expect(probe.payload.top_k).toBe(5)
  expect(probe.payload.threshold).toBeCloseTo(0.05, 5)
  expect(probe.payload.model_source).toBe('huggingface')
  expect(probe.payload.model_path).toBeNull()
  expect(probe.payload.use_gpu).toBe(true)
})

// ---------------------------------------------------------------------------
// 4. capturePreferences / applySavedPreferences — DOM <-> App.Prefs round-trip.
// ---------------------------------------------------------------------------

test('capturePreferences reads the control DOM and applySavedPreferences writes saved values back (guarded threshold + local group reveal)', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const A = (window as any).ArtistIdent
    const source = document.getElementById('artist-model-source') as HTMLSelectElement
    const path = document.getElementById('artist-model-path') as HTMLInputElement
    const gpu = document.getElementById('artist-use-gpu') as HTMLInputElement
    const threshold = document.getElementById('artist-threshold') as HTMLInputElement
    const localGroup = document.getElementById('artist-local-model-group') as HTMLElement

    source.value = 'local'
    path.value = '  /trim/me.pth  '
    gpu.checked = false
    threshold.value = '0.04'
    const captured = A.capturePreferences()

    // Stub App.Prefs.getArtistDefaults so applySavedPreferences has something to apply.
    const w = window as any
    w.App = w.App || {}
    w.App.Prefs = { ...(w.App.Prefs || {}), getArtistDefaults: () => ({
      modelSource: 'local', modelPath: '/saved/model.pth', threshold: 0.09, useGpu: true,
    }) }
    const applied = A.applySavedPreferences()

    return {
      captured,
      applied,
      appliedSource: source.value,
      appliedPath: path.value,
      appliedThreshold: threshold.value,
      appliedGpu: gpu.checked,
      localGroupDisplay: localGroup.style.display,
    }
  })

  // capture: source/path trimmed, threshold from the slider, gpu from the checkbox.
  expect(probe.captured.modelSource).toBe('local')
  expect(probe.captured.modelPath).toBe('/trim/me.pth')
  expect(probe.captured.threshold).toBeCloseTo(0.04, 5)
  expect(probe.captured.useGpu).toBe(false)
  // apply: writes the saved values back and returns true; _syncControls reveals local group.
  expect(probe.applied).toBe(true)
  expect(probe.appliedSource).toBe('local')
  expect(probe.appliedPath).toBe('/saved/model.pth')
  expect(probe.appliedThreshold).toBe('0.09') // 0.09 is within the [0, 0.25] guard
  expect(probe.appliedGpu).toBe(true)
  expect(probe.localGroupDisplay).toBe('block')
})

// ---------------------------------------------------------------------------
// 5. getInitials / formatArtistName / formatConfidencePercent — pure display transforms.
// ---------------------------------------------------------------------------

test('name + confidence formatters handle multi-word, single-word, empty and "undefined" sentinels', async ({ page }) => {
  await page.evaluate(() => (window as any).I18n.setLang('en'))
  const probe = await page.evaluate(() => {
    const A = (window as any).ArtistIdent
    return {
      initialsMulti: A.getInitials('greg_rutkowski'),
      initialsSingle: A.getInitials('picasso'),
      initialsOneChar: A.getInitials('a'),
      initialsEmpty: A.getInitials(''),
      initialsUndef: A.getInitials('undefined'),
      nameMulti: A.formatArtistName('greg_rutkowski'),
      nameEmpty: A.formatArtistName(''),
      nameUndef: A.formatArtistName('undefined'),
      pct: A.formatConfidencePercent(0.856),
      pctZero: A.formatConfidencePercent(0),
      pctNull: A.formatConfidencePercent(null),
    }
  })

  expect(probe.initialsMulti).toBe('GR')
  expect(probe.initialsSingle).toBe('PI')
  expect(probe.initialsOneChar).toBe('A')
  expect(probe.initialsEmpty).toBe('?')
  expect(probe.initialsUndef).toBe('?')
  expect(probe.nameMulti).toBe('Greg Rutkowski')
  // "undefined" is the backend's refusal-to-answer sentinel. Rendering it (even
  // title-cased as the old pin required) is the refusal reading as an answer, so
  // the sentinel now formats as the localized no-match label instead.
  expect(probe.nameEmpty).toBe('No match')
  expect(probe.nameUndef).toBe('No match')
  expect(probe.nameUndef.toLowerCase()).not.toContain('undefined')
  expect(probe.pct).toBe('85.6%')
  expect(probe.pctZero).toBe('0.0%')
  expect(probe.pctNull).toBe('0.0%')
})

// ---------------------------------------------------------------------------
// 6. _buildCompletionToast — the batch-result -> toast decision table + crash ordering.
// ---------------------------------------------------------------------------

test('_buildCompletionToast picks error/warning/success by branch and the whole-batch crash (step:error) wins over the count paths', async ({ page }) => {
  await page.evaluate(() => (window as any).I18n.setLang('en'))
  const probe = await page.evaluate(() => {
    const A = (window as any).ArtistIdent
    // Batch results carry confidence_level since 2c15c9e; a bare `{artist}` is
    // the pre-tiering shape the backend no longer emits.
    const high = { artist: 'greg', confidence: 0.71, confidence_level: 'high' }
    return {
      // step:'error' returns the raw backend message even though results exist and
      // errors===0 (the documented crash-before-count ordering).
      crash: A._buildCompletionToast({ step: 'error', message: 'kaboom', results: [high], errors: 0 }, 5),
      // per-image errors -> warning.
      withErrors: A._buildCompletionToast({ results: [high], errors: 2, total: 3 }, 3),
      // nothing reached the confident tier -> warning, and the message must not
      // send the user back to the slider: that cannot change the outcome.
      noConfident: A._buildCompletionToast({
        results: [
          { artist: 'undefined', candidate_artist: 'greg', confidence: 0.07, confidence_level: 'low' },
          { artist: 'undefined', confidence: 0.004, confidence_level: 'none' },
        ],
        errors: 0,
      }, 0),
      // at least one confident match -> success, with the tier breakdown.
      counted: A._buildCompletionToast({ results: [high], errors: 0, total: 1 }, 3),
      // nothing to report -> generic success.
      generic: A._buildCompletionToast({ results: [], errors: 0, total: 0 }, 0),
    }
  })

  expect(probe.crash.level).toBe('error')
  expect(probe.crash.message).toBe('kaboom')
  expect(probe.withErrors.level).toBe('warning')
  expect(probe.noConfident.level).toBe('warning')
  expect(probe.noConfident.message).toContain('1 unconfirmed candidate')
  expect(probe.noConfident.message.toLowerCase()).not.toContain('threshold')
  expect(probe.counted.level).toBe('success')
  expect(probe.counted.message).toContain('1 confident')
  expect(probe.generic.level).toBe('success')

  for (const built of Object.values(probe)) {
    expect((built as { message: string }).message.toLowerCase()).not.toContain('undefined')
  }
})

// ---------------------------------------------------------------------------
// 7. renderArtistGrid — count-sorted cards, grid/list mode, two distinct empty states.
// ---------------------------------------------------------------------------

test('renderArtistGrid sorts by count desc, toggles list-mode, and shows distinct empty copy for "none yet" vs "all below threshold"', async ({ page }) => {
  const gridProbe = await page.evaluate(() => {
    const A = (window as any).ArtistIdent
    A.stats = { identified_images: 0, undefined_count: 0, artist_stats: {} }
    A.renderArtistGrid({ alice: 5, bob: 10, carol: 3 }, 'grid')
    const grid = document.getElementById('artist-results-grid') as HTMLElement
    const cards = Array.from(grid.querySelectorAll('.artist-card'))
    return {
      count: cards.length,
      firstArtistAttr: cards[0]?.getAttribute('data-artist'),
      firstName: cards[0]?.querySelector('.artist-name')?.textContent,
      firstCount: cards[0]?.querySelector('.artist-count')?.textContent,
      listModeInGrid: grid.classList.contains('list-mode'),
    }
  })

  // bob(10) sorts ahead of alice(5) and carol(3).
  expect(gridProbe.count).toBe(3)
  expect(gridProbe.firstArtistAttr).toBe('bob')
  expect(gridProbe.firstName).toBe('Bob')
  expect(gridProbe.firstCount).toBe('10 images')
  expect(gridProbe.listModeInGrid).toBe(false)

  const listProbe = await page.evaluate(() => {
    const A = (window as any).ArtistIdent
    A.renderArtistGrid({ alice: 5 }, 'list')
    const grid = document.getElementById('artist-results-grid') as HTMLElement
    return {
      listModeInList: grid.classList.contains('list-mode'),
      listCard: grid.querySelectorAll('.artist-card-list').length,
    }
  })
  expect(listProbe.listModeInList).toBe(true)
  expect(listProbe.listCard).toBe(1)

  const emptyProbe = await page.evaluate(() => {
    const A = (window as any).ArtistIdent
    const grid = document.getElementById('artist-results-grid') as HTMLElement
    // Nothing identified yet.
    A.stats = { identified_images: 0, undefined_count: 0 }
    A.renderArtistGrid({}, 'grid')
    const noneTitle = grid.querySelector('.empty-state p')?.textContent?.trim() ?? ''
    // Everything identified but all below threshold.
    A.stats = { identified_images: 5, undefined_count: 5 }
    A.renderArtistGrid({}, 'grid')
    const allUndefTitle = grid.querySelector('.empty-state p')?.textContent?.trim() ?? ''
    return { noneTitle, allUndefTitle, hasHint: !!grid.querySelector('.empty-hint') }
  })
  // Both are non-empty empty-states, but the copy differs by stats context.
  expect(emptyProbe.noneTitle).not.toBe('')
  expect(emptyProbe.allUndefTitle).not.toBe('')
  expect(emptyProbe.noneTitle).not.toBe(emptyProbe.allUndefTitle)
  expect(emptyProbe.hasHint).toBe(true)
})

test('loadStats clears stale artist cards after a failed refresh', async ({ page }) => {
  await page.waitForFunction(() => document.documentElement.dataset.appReady === '1')
  await page.evaluate(() => (window as any).I18n.setLang('en'))
  let statsCalls = 0
  await page.route('**/api/artists/stats', async (route) => {
    statsCalls += 1
    if (statsCalls <= 2) {
      if (statsCalls === 2) {
        await new Promise((resolve) => setTimeout(resolve, 300))
      }
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          total_images: 4,
          identified_images: 4,
          undefined_count: 0,
          artist_counts: { stale_artist: 4 },
          artist_stats: {
            stale_artist: { count: 4, avg_confidence: 0.9, max_confidence: 0.95 },
          },
        }),
      })
    }
    return route.fulfill({
      status: 503,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'stats unavailable' }),
    })
  })

  await page.evaluate(() => {
    localStorage.setItem('artist-guide-seen', 'true')
    document.querySelectorAll<HTMLElement>('.view').forEach((view) => {
      view.style.removeProperty('display')
    })
    ;(window as any).App.switchView('artist')
  })
  await expect(page.locator('#view-artist')).toHaveClass(/\bactive\b/)
  await expect(page.locator('#view-gallery')).toBeHidden()
  await expect(page.locator('#artist-results-grid .artist-card')).toHaveCount(1)

  await page.evaluate(() => {
    const artist = (window as any).ArtistIdent
    return Promise.all([artist.loadStats(), artist.loadStats()])
  })

  const failure = await page.evaluate(() => {
    const artist = (window as any).ArtistIdent
    const statsText = document.getElementById('artist-stats')?.textContent?.trim() || ''
    const grid = document.getElementById('artist-results-grid')
    return {
      stats: artist.stats,
      cardCount: grid?.querySelectorAll('.artist-card').length ?? -1,
      statsHasFailure: statsText.includes('Failed to load stats'),
      gridHasFailure: (grid?.textContent || '').includes('Failed to load stats'),
    }
  })

  expect(failure.stats).toEqual({})
  expect(failure.cardCount).toBe(0)
  expect(failure.statsHasFailure).toBe(true)
  expect(failure.gridHasFailure).toBe(true)

  for (const viewport of [
    { width: 1366, height: 768 },
    { width: 1920, height: 1080 },
    { width: 2560, height: 1440 },
  ]) {
    await page.setViewportSize(viewport)
    const layout = await page.evaluate(() => {
      const view = document.getElementById('view-artist')
      const gallery = document.getElementById('view-gallery')
      const stats = document.getElementById('artist-stats')
      const grid = document.getElementById('artist-results-grid')
      const failureLabel = grid?.querySelector('.empty-state p')
      const statsRect = stats?.getBoundingClientRect()
      const gridRect = grid?.getBoundingClientRect()
      const failureRect = failureLabel?.getBoundingClientRect()
      const overlapWidth = statsRect && gridRect
        ? Math.max(0, Math.min(statsRect.right, gridRect.right) - Math.max(statsRect.left, gridRect.left))
        : 0
      const overlapHeight = statsRect && gridRect
        ? Math.max(0, Math.min(statsRect.bottom, gridRect.bottom) - Math.max(statsRect.top, gridRect.top))
        : 0
      return {
        artistActive: Boolean(view?.classList.contains('active')),
        galleryHidden: gallery ? getComputedStyle(gallery).display === 'none' : false,
        pageOverflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        viewOverflowX: view ? view.scrollWidth - view.clientWidth : -1,
        gridVisible: Boolean(gridRect && gridRect.width > 0 && gridRect.height > 0),
        failureVisible: Boolean(grid?.textContent?.includes('Failed to load stats')),
        failureInViewport: Boolean(
          failureRect
          && failureRect.top >= 0
          && failureRect.bottom <= window.innerHeight
          && failureRect.left >= 0
          && failureRect.right <= window.innerWidth
        ),
        overlapArea: overlapWidth * overlapHeight,
      }
    })

    expect(layout, `${viewport.width}x${viewport.height}`).toEqual({
      artistActive: true,
      galleryHidden: true,
      pageOverflowX: 0,
      viewOverflowX: 0,
      gridVisible: true,
      failureVisible: true,
      failureInViewport: true,
      overlapArea: 0,
    })
  }
})

// ---------------------------------------------------------------------------
// 8. selectArtist — paged images URL, detail render, preview cards + 4 actions, load-more.
// ---------------------------------------------------------------------------

test('selectArtist requests the paged images URL, renders the detail header + preview cards with 4 per-image actions, and toggles Load More', async ({ page }) => {
  const imagesUrls: string[] = []
  let imagesResponse: unknown = {}
  await page.route('**/api/artists/images/**', (route) => {
    imagesUrls.push(route.request().url())
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(imagesResponse) })
  })

  imagesResponse = {
    images: [
      { image_id: 501, filename: 'a.png', confidence_percent: 88 },
      { image_id: 502, filename: 'b.png', confidence_percent: 75 },
    ],
    has_more: true,
  }
  await page.evaluate(() => {
    const A = (window as any).ArtistIdent
    A.stats = {
      artist_counts: { greg_rutkowski: 4 },
      artist_stats: { greg_rutkowski: { count: 4, avg_confidence: 0.7, max_confidence: 0.9 } },
    }
    return A.selectArtist('greg_rutkowski')
  })

  // URL carries the default page size (120) + offset 0, artist path-encoded.
  expect(imagesUrls[0]).toMatch(/\/api\/artists\/images\/greg_rutkowski\?limit=120&offset=0(?:&|$)/)

  await expect(page.locator('#artist-detail-content h4')).toHaveText('Greg Rutkowski')

  const preview = page.locator('#artist-images-preview')
  await expect(preview.locator('.artist-image-card')).toHaveCount(2)
  await expect(preview.locator('.artist-image-card[data-image-id="501"] .artist-image-confidence')).toHaveText('88%')
  const actions = await preview
    .locator('.artist-image-card[data-image-id="501"] .artist-image-action')
    .evaluateAll((els) => els.map((el) => el.getAttribute('data-action')))
  expect(actions).toEqual(['preview', 'reader', 'edit', 'build'])

  // has_more:true -> Load More is shown; the Gallery filter CTA is present.
  await expect(page.locator('#btn-artist-load-more')).toBeVisible()
  await expect(page.locator('#btn-filter-by-artist')).toBeVisible()

  // Re-select with has_more:false -> Load More hides.
  imagesResponse = { images: [{ image_id: 501, filename: 'a.png', confidence_percent: 88 }], has_more: false }
  await page.evaluate(() => (window as any).ArtistIdent.selectArtist('greg_rutkowski'))
  await expect(page.locator('#btn-artist-load-more')).toBeHidden()
})

// ---------------------------------------------------------------------------
// 9. selectArtist token guard — a newer selection supersedes an in-flight older one.
// ---------------------------------------------------------------------------

test('a newer artist selection wins the race and the slower older response is dropped (artistRequestToken guard)', async ({ page }) => {
  await page.route('**/api/artists/images/**', async (route) => {
    const url = route.request().url()
    if (url.includes('/images/slow_one')) {
      await new Promise((resolve) => setTimeout(resolve, 300))
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ images: [{ image_id: 111, filename: 'old.png', confidence_percent: 10 }], has_more: false }),
      })
    } else {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ images: [{ image_id: 222, filename: 'new.png', confidence_percent: 20 }], has_more: false }),
      })
    }
  })

  await page.evaluate(() => {
    const A = (window as any).ArtistIdent
    A.stats = { artist_counts: {}, artist_stats: {} }
    A.selectArtist('slow_one') // token N (slow)
    A.selectArtist('fast_two') // token N+1 (fast) -> becomes the live token
  })

  // The fast, newer response renders.
  await expect(page.locator('#artist-detail-content h4')).toHaveText('Fast Two')
  await expect(page.locator('#artist-images-preview .artist-image-card[data-image-id="222"]')).toHaveCount(1)
  // After the slow one lands it must NOT overwrite the newer detail/preview.
  await page.waitForTimeout(600)
  await expect(page.locator('#artist-detail-content h4')).toHaveText('Fast Two')
  await expect(page.locator('#artist-images-preview .artist-image-card[data-image-id="111"]')).toHaveCount(0)
})

// ---------------------------------------------------------------------------
// 10. loadDiagnostics — ready banner vs needs-setup (with the model-guidance button).
// ---------------------------------------------------------------------------

test('loadDiagnostics renders a plain ready banner when available and a warning banner + open-model-guidance button when not', async ({ page }) => {
  let diagResponse: unknown = {}
  await page.route('**/api/artists/diagnostics', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(diagResponse) }))

  diagResponse = { available: true }
  const ready = await page.evaluate(async () => {
    const A = (window as any).ArtistIdent
    await A.loadDiagnostics()
    const banner = document.getElementById('artist-model-health') as HTMLElement
    return {
      warning: banner.classList.contains('model-health-banner-warning'),
      visible: banner.classList.contains('is-visible'),
      setupBtn: !!banner.querySelector('[data-action="open-model-guidance"]'),
      diagStored: A.diagnostics?.available,
    }
  })
  expect(ready.visible).toBe(true)
  expect(ready.warning).toBe(false)
  expect(ready.setupBtn).toBe(false)
  expect(ready.diagStored).toBe(true)

  diagResponse = {
    available: false,
    message: 'Artist identification still needs the LSNet runtime, Kaloscope files, or Python dependencies.',
    missing_dependencies: ['timm', 'einops'],
  }
  const needsSetup = await page.evaluate(async () => {
    const A = (window as any).ArtistIdent
    await A.loadDiagnostics()
    const banner = document.getElementById('artist-model-health') as HTMLElement
    return {
      warning: banner.classList.contains('model-health-banner-warning'),
      setupBtn: !!banner.querySelector('[data-action="open-model-guidance"]'),
      hasDetails: !!banner.querySelector('.model-health-details'),
      identifyAllDisabled: (document.getElementById('btn-identify-all') as HTMLButtonElement).disabled,
    }
  })
  expect(needsSetup.warning).toBe(true)
  expect(needsSetup.setupBtn).toBe(true)
  expect(needsSetup.hasDetails).toBe(true)
  // refreshAvailabilityState (called at the end of loadDiagnostics) gates the run button.
  expect(needsSetup.identifyAllDisabled).toBe(true)
})

// ---------------------------------------------------------------------------
// 11. refreshAvailabilityState / syncSelectionActionState — button gating machine.
// ---------------------------------------------------------------------------

test('run and clear buttons are gated on availability, in-flight state, and gallery selection', async ({ page }) => {
  const consoleErrors: string[] = []
  const failedResponses: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('response', (response) => {
    if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`)
  })

  const probe = await page.evaluate(() => {
    const A = (window as any).ArtistIdent
    const w = window as any
    w.App = w.App || {}
    w.App.AppState = w.App.AppState || {}
    let explicitSelectionIds = [1, 2]
    w.AppFilterAccess = w.AppFilterAccess || {}
    w.AppFilterAccess.getSelectedImageIds = () => [...explicitSelectionIds]
    const identifyAll = () => (document.getElementById('btn-identify-all') as HTMLButtonElement).disabled
    const identifySel = () => (document.getElementById('btn-identify-selected') as HTMLButtonElement).disabled
    const clearData = () => {
      const button = document.getElementById('btn-clear-artist-data') as HTMLButtonElement
      return { disabled: button.disabled, ariaDisabled: button.getAttribute('aria-disabled') }
    }

    // Unavailable runtime -> Identify All disabled regardless of selection.
    A.isIdentifying = false
    A.diagnostics = { available: false }
    A.refreshAvailabilityState()
    const unavailableAll = identifyAll()

    // Available + a selection -> both enabled.
    A.diagnostics = { available: true }
    A.refreshAvailabilityState()
    const availableAll = identifyAll()
    const availableSelWithPick = identifySel()
    const idleClear = clearData()

    // Available but NO selection -> Identify Selected disabled, Identify All still enabled.
    explicitSelectionIds = []
    A.syncSelectionActionState()
    const availableSelNoPick = identifySel()
    const availableAllNoPick = identifyAll()

    // Filtered-token exclusions are internal negative state, never explicit Artist input.
    w.App.AppState.selectionToken = 'filtered-token'
    w.App.AppState.selectedIds = new Set([99])
    A.syncSelectionActionState()
    const availableSelWithFilteredExclusion = identifySel()

    // A run in progress disables Identify All even when available.
    A.isIdentifying = true
    A.refreshAvailabilityState()
    const runningAll = identifyAll()
    const runningClear = clearData()
    A.isIdentifying = false
    A.refreshAvailabilityState()
    const restoredClear = clearData()
    return {
      unavailableAll,
      availableAll,
      availableSelWithPick,
      availableSelNoPick,
      availableAllNoPick,
      availableSelWithFilteredExclusion,
      runningAll,
      idleClear,
      runningClear,
      restoredClear,
    }
  })

  expect(probe.unavailableAll).toBe(true)
  expect(probe.availableAll).toBe(false)
  expect(probe.availableSelWithPick).toBe(false)
  expect(probe.availableSelNoPick).toBe(true)
  expect(probe.availableAllNoPick).toBe(false)
  expect(probe.availableSelWithFilteredExclusion).toBe(true)
  expect(probe.runningAll).toBe(true)
  expect(probe.idleClear).toEqual({ disabled: false, ariaDisabled: 'false' })
  expect(probe.runningClear).toEqual({ disabled: true, ariaDisabled: 'true' })
  expect(probe.restoredClear).toEqual({ disabled: false, ariaDisabled: 'false' })

  for (const viewport of [
    { width: 1366, height: 768 },
    { width: 1920, height: 1080 },
    { width: 2560, height: 1440 },
  ]) {
    await page.setViewportSize(viewport)
    await page.evaluate(() => {
      const A = (window as any).ArtistIdent
      A.isIdentifying = true
      A.refreshAvailabilityState()
    })

    const clearButton = page.locator('#btn-clear-artist-data')
    await expect(clearButton).toBeVisible()
    await expect(clearButton).toBeDisabled()
    await expect(clearButton).toHaveAttribute('aria-disabled', 'true')
    await clearButton.scrollIntoViewIfNeeded()
    const geometry = await clearButton.evaluate((element) => {
      const rect = element.getBoundingClientRect()
      const artistView = element.closest('#view-artist')
      const overlappingButtons = [...(artistView?.querySelectorAll('button') || [])]
        .filter((candidate) => candidate !== element)
        .filter((candidate) => {
          const other = candidate.getBoundingClientRect()
          return other.width > 0
            && other.height > 0
            && rect.left < other.right
            && rect.right > other.left
            && rect.top < other.bottom
            && rect.bottom > other.top
        })
        .map((candidate) => candidate.id || candidate.textContent?.trim() || candidate.tagName)
      return {
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
        width: rect.width,
        height: rect.height,
        overlappingButtons,
        pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      }
    })
    expect(geometry.left).toBeGreaterThanOrEqual(0)
    expect(geometry.top).toBeGreaterThanOrEqual(0)
    expect(geometry.right).toBeLessThanOrEqual(viewport.width)
    expect(geometry.bottom).toBeLessThanOrEqual(viewport.height)
    expect(geometry.width).toBeGreaterThan(0)
    expect(geometry.height).toBeGreaterThan(0)
    expect(geometry.overlappingButtons).toEqual([])
    expect(geometry.pageOverflow).toBe(false)
  }

  expect(consoleErrors).toEqual([])
  expect(failedResponses).toEqual([])
})

// ---------------------------------------------------------------------------
// 12. identifyAll — image collection, identify-batch POST shape, poll, stats refetch,
//     plus the empty-library and unavailable-runtime early returns.
// ---------------------------------------------------------------------------

test('identifyAll collects image ids, posts the identify-batch payload, polls to completion + refetches stats, and short-circuits on empty library / unavailable runtime', async ({ page }) => {
  let imagesResponse: Record<string, unknown> = { images: [{ id: 1, filename: 'x1.png' }, { id: 2, filename: 'x2.png' }], has_more: false }
  let imagesCalls = 0
  let batchBody: Record<string, unknown> | null = null
  let batchCalls = 0

  await page.route(/\/api\/images\?/, (route) => {
    imagesCalls += 1
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(imagesResponse) })
  })
  await page.route(/\/api\/artists\/identify-batch/, (route) => {
    batchCalls += 1
    batchBody = JSON.parse(route.request().postData() || '{}')
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ started: true }) })
  })
  await page.route(/\/api\/artists\/batch-progress/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        running: false,
        total: 2,
        processed: 2,
        errors: 0,
        results: [
          { artist: 'greg', confidence: 0.64, confidence_level: 'high' },
          { artist: 'greg', confidence: 0.52, confidence_level: 'high' },
        ],
      }),
    }))
  await page.route(/\/api\/artists\/stats/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ total_images: 2, identified_images: 2, undefined_count: 0, artist_counts: { greg: 2 }, artist_stats: {} }),
    }))

  // Spy on App.showToast so the completion level is deterministic.
  await page.evaluate(() => {
    const w = window as any
    w.__artistToasts = []
    const orig = w.App.showToast?.bind(w.App)
    w.App.showToast = (message: string, level: string) => {
      w.__artistToasts.push({ message, level })
      return orig ? orig(message, level) : undefined
    }
  })

  // --- Happy path: available runtime, 2 images collected + identified. ---
  const happy = await page.evaluate(async () => {
    const A = (window as any).ArtistIdent
    A.diagnostics = { available: true }
    A.isIdentifying = false
    ;(document.getElementById('artist-threshold') as HTMLInputElement).value = '0.05'
    ;(document.getElementById('artist-model-source') as HTMLSelectElement).value = 'huggingface'
    ;(document.getElementById('artist-use-gpu') as HTMLInputElement).checked = true
    ;(window as any).__artistToasts = []
    await A.identifyAll()
    return (window as any).__artistToasts.slice(-1)[0]
  })

  expect(batchCalls).toBe(1)
  expect(batchBody).not.toBeNull()
  expect((batchBody as any).image_ids).toEqual([1, 2])
  expect((batchBody as any).threshold).toBeCloseTo(0.05, 5)
  expect((batchBody as any).top_k).toBe(5)
  expect((batchBody as any).model_source).toBe('huggingface')
  expect((batchBody as any).model_path).toBeNull()
  expect((batchBody as any).use_gpu).toBe(true)
  expect(happy.level).toBe('success')

  // --- Empty library: warns, never posts identify-batch again. ---
  imagesResponse = { images: [], has_more: false }
  const emptyToast = await page.evaluate(async () => {
    const A = (window as any).ArtistIdent
    A.isIdentifying = false
    ;(window as any).__artistToasts = []
    await A.identifyAll()
    return (window as any).__artistToasts.slice(-1)[0]
  })
  expect(batchCalls).toBe(1) // no new POST
  expect(emptyToast.level).toBe('warning')

  // --- Unavailable runtime: short-circuits before touching /api/images. ---
  const imagesBefore = imagesCalls
  const unavailableToast = await page.evaluate(async () => {
    const A = (window as any).ArtistIdent
    A.isIdentifying = false
    A.diagnostics = { available: false }
    ;(window as any).__artistToasts = []
    await A.identifyAll()
    return (window as any).__artistToasts.slice(-1)[0]
  })
  expect(imagesCalls).toBe(imagesBefore) // never collected images
  expect(batchCalls).toBe(1)             // never posted
  expect(unavailableToast.level).toBe('warning')
})

test('existing batch handoff reaches terminal state and restores controls for all and selected starts', async ({ page }) => {
  let progressCalls = 0
  let batchCalls = 0

  await page.route(/\/api\/images\?/, (route) => {
    const url = new URL(route.request().url())
    if (url.searchParams.get('limit') !== '1000') return route.continue()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ images: [{ id: 11, filename: 'handoff.png' }], has_more: false }),
    })
  })
  await page.route(/\/api\/artists\/identify-batch/, (route) => {
    batchCalls += 1
    return route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({ error: 'Artist identification is already in progress' }),
    })
  })
  await page.route(/\/api\/artists\/batch-progress/, (route) => {
    progressCalls += 1
    const running = progressCalls % 2 === 1
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        running,
        total: 1,
        processed: running ? 0 : 1,
        errors: 0,
        results: running ? [] : [{ image_id: 11, artist: 'fixture_artist', confidence: 0.97 }],
        step: running ? 'identifying' : 'done',
        message: running ? 'Identifying handoff.png' : 'Completed artist identification',
      }),
    })
  })
  await page.route(/\/api\/artists\/stats/, (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      total_images: 1,
      identified_images: 1,
      undefined_count: 0,
      artist_counts: { fixture_artist: 1 },
      artist_stats: { fixture_artist: { count: 1, avg_confidence: 0.97, max_confidence: 0.97 } },
    }),
  }))

  const runAll = await page.evaluate(async () => {
    const A = (window as any).ArtistIdent
    A.diagnostics = { available: true }
    A.isIdentifying = false
    await A.identifyAll()
    return {
      identifying: A.isIdentifying,
      identifyAllDisabled: (document.getElementById('btn-identify-all') as HTMLButtonElement).disabled,
      clearDisabled: (document.getElementById('btn-clear-artist-data') as HTMLButtonElement).disabled,
      clearAriaDisabled: document.getElementById('btn-clear-artist-data')?.getAttribute('aria-disabled'),
    }
  })

  await gotoArtist(page)
  const runSelected = await page.evaluate(async () => {
    const w = window as any
    const A = w.ArtistIdent
    w.AppFilterAccess = w.AppFilterAccess || {}
    w.AppFilterAccess.getSelectedImageIds = () => [11]
    A.diagnostics = { available: true }
    A.isIdentifying = false
    await A.identifySelected()
    return {
      identifying: A.isIdentifying,
      identifyAllDisabled: (document.getElementById('btn-identify-all') as HTMLButtonElement).disabled,
      clearDisabled: (document.getElementById('btn-clear-artist-data') as HTMLButtonElement).disabled,
      clearAriaDisabled: document.getElementById('btn-clear-artist-data')?.getAttribute('aria-disabled'),
    }
  })

  const restored = {
    identifying: false,
    identifyAllDisabled: false,
    clearDisabled: false,
    clearAriaDisabled: 'false',
  }
  expect(runAll).toEqual(restored)
  expect(runSelected).toEqual(restored)
  expect(batchCalls).toBe(2)
  expect(progressCalls).toBe(4)
})

test('clear conflict surfaces the actionable server reason after confirmation', async ({ page }) => {
  await initArtistView(page)
  const reason = 'Artist identification is already in progress; wait for it to finish before clearing predictions'
  await page.route('**/api/artists/clear', (route) => route.fulfill({
    status: 409,
    contentType: 'application/json',
    body: JSON.stringify({ error: reason }),
  }))

  const clearButton = page.locator('#btn-clear-artist-data')
  await expect(clearButton).toBeEnabled()
  await clearButton.click()
  await expect(page.locator('#confirm-modal.visible')).toBeVisible()
  await page.locator('#confirm-modal #btn-confirm-ok').click()

  await expect(page.locator('#toast-container .toast').last()).toContainText(reason)
})

// ---------------------------------------------------------------------------
// 13. filterGalleryByArtist / clearArtistFilter — the Artist -> Gallery handoff.
// ---------------------------------------------------------------------------

test('filterGalleryByArtist and clearArtistFilter route through App.updateFilters + switchView(gallery) + loadImages with the right artist value', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const A = (window as any).ArtistIdent
    const w = window as any
    w.App = w.App || {}
    w.App.AppState = w.App.AppState || {}
    w.App.AppState.filters = w.App.AppState.filters || {}

    const artistValues: (string | null | undefined)[] = []
    const switched: string[] = []
    let loadImagesCalls = 0
    w.App.updateFilters = (fn: (f: Record<string, unknown>) => void) => {
      const probeFilters: Record<string, unknown> = {}
      fn(probeFilters)
      artistValues.push(probeFilters.artist as string | null)
    }
    w.App.updateFilterSummary = () => {}
    w.App.switchView = (name: string) => { switched.push(name) }
    w.App.loadImages = () => { loadImagesCalls += 1 }
    w.App.showToast = () => {}

    A.filterGalleryByArtist('greg_rutkowski')
    A.clearArtistFilter()
    return { artistValues, switched, loadImagesCalls }
  })

  expect(probe.artistValues[0]).toBe('greg_rutkowski')
  expect(probe.artistValues[1]).toBeNull()
  expect(probe.switched).toEqual(['gallery', 'gallery'])
  expect(probe.loadImagesCalls).toBe(2)
})

// ---------------------------------------------------------------------------
// 14. bindEvents (via init) — idempotent guard, delegated button routing, view toggle.
// ---------------------------------------------------------------------------

test('after init the delegated click handlers route the action buttons exactly once and the Grid/List toggle re-renders', async ({ page }) => {
  await initArtistView(page)

  const probe = await page.evaluate(async () => {
    const A = (window as any).ArtistIdent
    // A second bindEvents() must be a no-op (eventsBound guard) — no double handlers.
    A.bindEvents()

    let identifyAllCalls = 0
    let loadStatsCalls = 0
    let clearAllDataCalls = 0
    A.identifyAll = () => { identifyAllCalls += 1 }
    A.loadStats = () => { loadStatsCalls += 1 }
    A.clearAllData = () => { clearAllDataCalls += 1 }
    // Ensure the run button is enabled so the click event actually dispatches.
    A.diagnostics = { available: true }
    A.isIdentifying = false
    A.refreshAvailabilityState()
    A.stats = { artist_counts: { alice: 1 } }

    document.getElementById('btn-identify-all')?.click()
    document.getElementById('btn-refresh-artist-stats')?.click()
    document.getElementById('btn-clear-artist-data')?.click()
    ;(document.querySelector('.view-toggle .toggle-btn[data-view="list"]') as HTMLElement)?.click()

    const grid = document.getElementById('artist-results-grid') as HTMLElement
    return {
      eventsBound: A.eventsBound,
      identifyAllCalls,
      loadStatsCalls,
      clearAllDataCalls,
      listMode: grid.classList.contains('list-mode'),
    }
  })

  expect(probe.eventsBound).toBe(true)
  expect(probe.identifyAllCalls).toBe(1)
  expect(probe.loadStatsCalls).toBe(1)
  expect(probe.clearAllDataCalls).toBe(1)
  expect(probe.listMode).toBe(true)
})

// ---------------------------------------------------------------------------
// 15. Confidence tiering (2c15c9e) — `artist` is the "undefined" sentinel below
//     the high tier, so a low/none result must never render as "the artist".
// ---------------------------------------------------------------------------

test('a low-confidence result is shown as an unconfirmed candidate, never as the artist, and "undefined" never reaches the screen', async ({ page }) => {
  await page.evaluate(() => (window as any).I18n.setLang('en'))
  const probe = await page.evaluate(() => {
    const A = (window as any).ArtistIdent
    return {
      high: A.describeArtistResult({
        artist: 'greg_rutkowski',
        candidate_artist: 'greg_rutkowski',
        confidence: 0.71,
        confidence_level: 'high',
        vocabulary_size: 39261,
        advisory: 'Confident match. / 高置信度匹配。',
      }),
      low: A.describeArtistResult({
        artist: 'undefined',
        candidate_artist: 'wlop',
        confidence: 0.084,
        confidence_level: 'low',
        out_of_vocabulary_likely: true,
        vocabulary_size: 39261,
        advisory: 'Unconfirmed suggestion, not an identification. / 这只是低置信度候选，不是识别结果。',
      }),
      none: A.describeArtistResult({
        artist: 'undefined',
        candidate_artist: null,
        confidence: 0.004,
        confidence_level: 'none',
        out_of_vocabulary_likely: true,
        vocabulary_size: 39261,
        advisory: 'No match. / 没有匹配。',
      }),
    }
  })

  // high: a real name plus the score.
  expect(probe.high.level).toBe('high')
  expect(probe.high.artistName).toBe('Greg Rutkowski')
  expect(probe.high.headline).toContain('Greg Rutkowski')
  expect(probe.high.headline).toContain('71.0%')

  // low: the candidate is offered, but never as `artistName`.
  expect(probe.low.level).toBe('low')
  expect(probe.low.artistName).toBeNull()
  expect(probe.low.candidateName).toBe('Wlop')
  expect(probe.low.headline).toContain('Unconfirmed candidate')
  expect(probe.low.headline).toContain('Wlop')
  expect(probe.low.headline).toContain('8.4%')
  expect(probe.low.advisory).toContain('Unconfirmed suggestion')
  // The bilingual "EN / ZH" advisory is split, so an English UI never shows both.
  expect(probe.low.advisory).not.toContain('这只是')

  // none: no name at all, only the advisory.
  expect(probe.none.level).toBe('none')
  expect(probe.none.artistName).toBeNull()
  expect(probe.none.candidateName).toBeNull()
  expect(probe.none.displayName).toBeNull()
  expect(probe.none.advisory).toContain('No match')

  for (const described of Object.values(probe)) {
    const tier = described as { headline: string; advisory: string; tierLabel: string }
    expect(tier.headline.toLowerCase()).not.toContain('undefined')
    expect(tier.advisory.toLowerCase()).not.toContain('undefined')
    expect(tier.tierLabel.toLowerCase()).not.toContain('undefined')
  }
})

test('the single-image modal toast reports the tier instead of the raw artist field', async ({ page }) => {
  await page.evaluate(() => (window as any).I18n.setLang('en'))
  let identifyBody: Record<string, unknown> | null = null
  let identifyResponse: Record<string, unknown> = {}
  await page.route('**/api/artists/identify', (route) => {
    identifyBody = JSON.parse(route.request().postData() || '{}')
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(identifyResponse) })
  })
  await page.route('**/api/artists/stats', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ total_images: 1, identified_images: 1, undefined_count: 1, confident_count: 0, low_confidence_count: 0, artist_counts: {}, artist_stats: {}, low_confidence_artist_counts: {} }),
  }))

  await page.evaluate(() => {
    const w = window as any
    w.__artistToasts = []
    w.App.showToast = (message: string, level: string) => { w.__artistToasts.push({ message, level }) }
  })

  identifyResponse = {
    image_id: 7,
    artist: 'undefined',
    candidate_artist: 'wlop',
    confidence: 0.09,
    confidence_level: 'low',
    out_of_vocabulary_likely: true,
    vocabulary_size: 39261,
    advisory: 'Unconfirmed suggestion, not an identification. / 这只是低置信度候选，不是识别结果。',
    top_predictions: [],
    model_loaded: true,
  }
  const lowToast = await page.evaluate(async () => {
    const G = (window as any).Gallery
    G._currentPreviewId = 7
    G._modalAnalysisRunning = new Set()
    await G._handleModalAnalysis('artist')
    return (window as any).__artistToasts.slice(-1)[0]
  })

  expect(identifyBody).not.toBeNull()
  expect((identifyBody as any).image_id).toBe(7)
  expect(lowToast.level).toBe('info')
  expect(lowToast.message).toContain('Unconfirmed candidate')
  expect(lowToast.message).toContain('Wlop')
  expect(lowToast.message.toLowerCase()).not.toContain('undefined')

  identifyResponse = {
    image_id: 7,
    artist: 'greg_rutkowski',
    candidate_artist: 'greg_rutkowski',
    confidence: 0.55,
    confidence_level: 'high',
    vocabulary_size: 39261,
    advisory: 'Confident match. / 高置信度匹配。',
    top_predictions: [],
    model_loaded: true,
  }
  const highToast = await page.evaluate(async () => {
    const G = (window as any).Gallery
    G._currentPreviewId = 7
    G._modalAnalysisRunning = new Set()
    await G._handleModalAnalysis('artist')
    return (window as any).__artistToasts.slice(-1)[0]
  })
  expect(highToast.level).toBe('success')
  expect(highToast.message).toContain('Greg Rutkowski')
  expect(highToast.message.toLowerCase()).not.toContain('undefined')
})

test('the unconfirmed bucket renders apart from Top Artists and per-image rows below the confident tier are badged', async ({ page }) => {
  await page.evaluate(() => (window as any).I18n.setLang('en'))
  await page.route('**/api/artists/stats', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      total_images: 30,
      identified_images: 30,
      undefined_count: 18,
      confident_count: 4,
      low_confidence_count: 8,
      confident_threshold: 0.2,
      artist_counts: { greg_rutkowski: 4 },
      artist_stats: { greg_rutkowski: { count: 4, avg_confidence: 0.44, max_confidence: 0.71 } },
      low_confidence_artist_counts: { wlop: 5, sakimichan: 3 },
    }),
  }))
  await page.route('**/api/artists/images/**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      images: [
        { image_id: 1, filename: 'sure.png', confidence_percent: 71, confidence_level: 'high' },
        { image_id: 2, filename: 'maybe.png', confidence_percent: 9, confidence_level: 'low' },
      ],
      has_more: false,
    }),
  }))

  await page.evaluate(() => (window as any).ArtistIdent.loadStats())

  // Confident-only grid, unconfirmed bucket kept separate and clearly labelled.
  await expect(page.locator('#artist-results-grid .artist-card')).toHaveCount(1)
  await expect(page.locator('#artist-results-grid')).toContainText('Greg Rutkowski')
  const bucket = page.locator('#artist-low-confidence')
  await expect(bucket).toBeVisible()
  await expect(bucket).toContainText('Unconfirmed candidates (8)')
  await expect(bucket.locator('.artist-candidate-chip')).toHaveCount(2)
  await expect(page.locator('#artist-results-grid')).not.toContainText('Wlop')

  // Five disjoint buckets, no merged "Identified" number.
  const stats = page.locator('#artist-stats')
  await expect(stats).toContainText('Confident Matches')
  await expect(stats).toContainText('Unconfirmed')
  await expect(stats).toContainText('No match')
  await expect(stats.locator('.stat-card')).toHaveCount(5)

  // Opening a confident artist still lists its sub-threshold rows — badged.
  await page.locator('#artist-results-grid .artist-card').first().click()
  const preview = page.locator('#artist-images-preview')
  await expect(preview.locator('.artist-image-card')).toHaveCount(2)
  await expect(preview.locator('.artist-image-card[data-image-id="1"] .artist-image-tier')).toHaveCount(0)
  await expect(preview.locator('.artist-image-card[data-image-id="2"] .artist-image-tier')).toHaveText('Unconfirmed')

  // Opening an unconfirmed candidate says so instead of reporting "0 images".
  await bucket.locator('.artist-candidate-chip').first().click()
  const detail = page.locator('#artist-detail-content')
  await expect(detail.locator('.artist-tier-badge')).toHaveText('Unconfirmed')
  await expect(detail).toContainText('5 images suggested for this name')
  await expect(detail.locator('.artist-detail-advisory')).toBeVisible()
  await expect(detail).not.toContainText('undefined')
})

test('the vocabulary lookup answers "is my artist supported?" before a run is started', async ({ page }) => {
  await page.evaluate(() => (window as any).I18n.setLang('en'))
  const lookupUrls: string[] = []
  await page.route('**/api/artists/vocabulary**', (route) => {
    const url = route.request().url()
    lookupUrls.push(url)
    const names = new URL(url).searchParams.getAll('name')
    const known: Record<string, boolean> = {}
    names.forEach((name) => { known[name] = name === 'wlop' })
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ vocabulary_size: 39261, vocabulary_loaded: true, known }),
    })
  })

  // Real view activation (not the inline-style reveal gotoArtist does): the
  // lookup is driven by the delegated handlers bindEvents() installs, and a
  // real click needs the other views genuinely out of the way.
  await page.evaluate(() => {
    localStorage.setItem('artist-guide-seen', 'true')
    document.querySelectorAll<HTMLElement>('.view').forEach((view) => { view.style.removeProperty('display') })
    ;(window as any).App.switchView('artist')
  })
  await expect(page.locator('#view-artist')).toHaveClass(/\bactive\b/)
  await expect(page.locator('#view-gallery')).toBeHidden()

  const result = page.locator('#artist-vocabulary-result')
  await expect(result).toContainText('39,261')

  await page.locator('#artist-vocabulary-input').fill('wlop, not_a_real_artist')
  await page.locator('#btn-artist-vocabulary-check').click()

  await expect(result.locator('.artist-vocabulary-row.is-known')).toHaveText(/wlop/)
  const unknown = result.locator('.artist-vocabulary-row.is-unknown')
  await expect(unknown).toHaveCount(1)
  await expect(unknown).toContainText('not_a_real_artist')
  await expect(unknown).toContainText('can never be predicted')

  const lastUrl = new URL(lookupUrls[lookupUrls.length - 1])
  expect(lastUrl.searchParams.getAll('name')).toEqual(['wlop', 'not_a_real_artist'])

  // It sits above the run buttons: the answer decides whether a run is worth it.
  const order = await page.evaluate(() => {
    const section = document.getElementById('artist-vocabulary-section')
    const runButton = document.getElementById('btn-identify-all')
    if (!section || !runButton) return -1
    return section.compareDocumentPosition(runButton) & Node.DOCUMENT_POSITION_FOLLOWING ? 1 : 0
  })
  expect(order).toBe(1)

  for (const viewport of [
    { width: 1366, height: 768 },
    { width: 1920, height: 1080 },
    { width: 2560, height: 1440 },
  ]) {
    await page.setViewportSize(viewport)
    const geometry = await page.evaluate(() => {
      const section = document.getElementById('artist-vocabulary-section')
      const input = document.getElementById('artist-vocabulary-input')
      const button = document.getElementById('btn-artist-vocabulary-check')
      const inputRect = input?.getBoundingClientRect()
      const buttonRect = button?.getBoundingClientRect()
      return {
        sectionVisible: !!section && section.getBoundingClientRect().height > 0,
        inputWidth: inputRect?.width ?? 0,
        buttonWidth: buttonRect?.width ?? 0,
        overlaps: !!(inputRect && buttonRect && inputRect.right > buttonRect.left + 1),
        pageOverflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      }
    })
    expect(geometry.sectionVisible, `${viewport.width}x${viewport.height}`).toBe(true)
    expect(geometry.inputWidth).toBeGreaterThan(0)
    expect(geometry.buttonWidth).toBeGreaterThan(0)
    expect(geometry.overlaps).toBe(false)
    expect(geometry.pageOverflowX).toBe(0)
  }
})
