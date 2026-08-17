import { expect, test, type Page } from '../fixtures/click-ledger'

/**
 * Characterization pins for frontend/js/app/model-manager.js (866 lines) —
 * "step 0" of a later verbatim decomposition (the file regrew past the 800-line
 * budget when the truthful-lifecycle work landed: 6247560 + fa6bdd8). Mirrors
 * the shipped app-core / gallery / scan-diagnostics pins: pin the OBSERVABLE
 * behavior of the load-bearing seams so a future split is provably
 * zero-behavior-change — the suite MUST pass before AND after.
 *
 * SCOPE: this file covers the MODULE SURFACE that the substantial sibling
 * tests/e2e/specs/model-manager.spec.ts does NOT already exercise. That spec
 * owns the integration flows (prepare button state machine, byte progress, the
 * background-continue lifecycle at 3 viewports, 8-strike poll-error streak,
 * bulk conflict, disk-usage, SAM3, the system-python setup-guide happy path,
 * "no Downloaded badge"). This spec pins the pure helpers and the finer DOM
 * contracts those flows never touch directly:
 *   - the two DIVERGENT byte formatters (_formatBulkBytes vs _formatBytes),
 *   - _parseModelPrepareStart's full validation/trim/throw matrix,
 *   - _modelPrepareConflictMessage interpolation,
 *   - renderModelManager's summary math + Essentials/Additional section split
 *     + per-card status fallback + the external-link scheme allowlist (security),
 *   - showModelSetupGuide's structure, escaping, focus fallback, RAW-bytes copy,
 *     singleton cleanup, Escape/backdrop close + focus restore,
 *   - openModelManager's setup-pulse/localStorage side effect + failure summary.
 *
 * ASSEMBLY SHAPE (verified): model-manager.js is a classic
 * (non-module, no 'use strict') script sharing ONE global lexical environment
 * with app.js and the other app/ parts (index.html line 7117 loads it BEFORE
 * app.js in original line order). Consequence:
 *   - its 9 top-level `function` declarations become window.* properties, so
 *     openModelManager/renderModelManager/showModelSetupGuide/_formatBytes/… are
 *     reachable from page.evaluate as bare window globals;
 *   - the single top-level `let _modelSetupGuideCleanup` lives in the global
 *     LEXICAL environment and is invisible on window;
 *   - NONE of its members are re-published on the sealed window.App facade —
 *     cross-file readers (boot-listeners-shell.js, nav-mobile.js call
 *     openModelManager bare; v321/tagger-tabs.js reads window.openModelManager;
 *     disk-usage.js + settings.js call _formatBytes bare) use the shared-scope
 *     globals directly. The split must preserve the window-global surface.
 *
 * The isolated e2e DB starts empty; the suite storageState seeds
 * aurora-entry-skip=1 so we land straight in the app. The globals exist as soon
 * as model-manager.js parses at page load, so most pins need only a booted page.
 */

type ModelMgrWin = typeof window & {
  App: Record<string, unknown>
  openModelManager: (initialTab?: string) => Promise<void>
  renderModelManager: (models?: unknown[]) => void
  showModelSetupGuide: (pr: unknown) => void
  _formatBulkBytes: (bytes: unknown) => string
  _formatBytes: (bytes: unknown) => string
  _parseModelPrepareStart: (payload: unknown, id: unknown) => { status: string, activeModelId: string }
  _modelPrepareConflictMessage: (requested: string, active: string) => string
  appT: (key: string, fallback: string, vars?: Record<string, unknown>) => string
  __clip: string | null
}

async function gotoApp(page: Page): Promise<void> {
  await page.goto('/')
  await page.waitForLoadState('domcontentloaded')
  // model-manager.js parses at load; wait until its globals AND the sealed
  // facade are published before probing.
  await page.waitForFunction(() =>
    typeof (window as unknown as ModelMgrWin).openModelManager === 'function'
    && typeof (window as unknown as ModelMgrWin).App === 'object',
  )
}

test.beforeEach(async ({ page }) => {
  await gotoApp(page)
})

// Replace navigator.clipboard.writeText so the setup-guide copy button resolves
// deterministically and we can capture the exact bytes it would copy. The
// product calls navigator.clipboard.writeText at click time (a live browser-API
// read, NOT a lexical binding), so overriding the property here is observed.
async function installClipboardCapture(page: Page): Promise<void> {
  await page.evaluate(() => {
    const w = window as unknown as ModelMgrWin
    w.__clip = null
    Object.defineProperty(window.navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: (text: string) => {
          w.__clip = String(text)
          return Promise.resolve()
        },
      },
    })
  })
}

// ---------------------------------------------------------------------------
// 1. Assembly surface — the split contract. Nine shared-scope window-global
//    function declarations; the lone `let` stays lexical (invisible on window);
//    NOTHING is published on the sealed window.App facade.
// ---------------------------------------------------------------------------

test('model-manager exposes 9 helpers as shared-scope window globals, keeps _modelSetupGuideCleanup lexical, and adds nothing to the App facade', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const win = window as unknown as Record<string, unknown>
    const App = (window as unknown as ModelMgrWin).App
    const windowFns = [
      'openModelManager', 'renderModelManager', 'promptBulkDownloadModels',
      'runBulkDownload', 'showModelSetupGuide', '_formatBulkBytes',
      '_parseModelPrepareStart', '_modelPrepareConflictMessage', '_formatBytes',
    ]
    // These same names must NOT appear on the sealed facade — cross-file readers
    // use the bare shared-scope globals, not window.App.
    const facadeNames = [
      'openModelManager', 'renderModelManager', 'showModelSetupGuide',
      'promptBulkDownloadModels', 'runBulkDownload',
    ]
    return {
      sealed: Object.isSealed(App),
      missingWindowFns: windowFns.filter((k) => typeof win[k] !== 'function'),
      leakedFacadeNames: facadeNames.filter((k) => (App as Record<string, unknown>)[k] !== undefined),
      // Top-level `let`: a global LEXICAL binding, never a window property.
      cleanupOnWindow: win['_modelSetupGuideCleanup'],
    }
  })

  expect(probe.sealed).toBe(true)
  expect(probe.missingWindowFns).toEqual([])
  expect(probe.leakedFacadeNames).toEqual([])
  expect(probe.cleanupOnWindow).toBeUndefined()
})

// ---------------------------------------------------------------------------
// 2. _formatBulkBytes — the bulk-download confirmation formatter. No "B" tier
//    (starts at KB); KB/MB rounded to whole numbers; GB to 2 decimals; a
//    non-numeric / null / falsy value collapses to "0 KB" via `Number()||0`.
// ---------------------------------------------------------------------------

test('_formatBulkBytes uses KB/MB whole numbers, GB to 2 decimals, and coerces junk to "0 KB"', async ({ page }) => {
  const out = await page.evaluate(() => {
    const w = window as unknown as ModelMgrWin
    return {
      zero: w._formatBulkBytes(0),
      oneKb: w._formatBulkBytes(1024),
      oneMbExact: w._formatBulkBytes(1024 * 1024),
      fiveMb: w._formatBulkBytes(5 * 1024 * 1024),
      threeGb: w._formatBulkBytes(3 * 1024 * 1024 * 1024),
      nul: w._formatBulkBytes(null),
      str: w._formatBulkBytes('2048'),
      junk: w._formatBulkBytes('abc'),
    }
  })

  expect(out.zero).toBe('0 KB')
  expect(out.oneKb).toBe('1 KB')
  // 1 MiB is NOT < 1 MiB, so it crosses into the MB tier.
  expect(out.oneMbExact).toBe('1 MB')
  expect(out.fiveMb).toBe('5 MB')
  expect(out.threeGb).toBe('3.00 GB')
  expect(out.nul).toBe('0 KB')
  expect(out.str).toBe('2 KB')
  expect(out.junk).toBe('0 KB')
})

// ---------------------------------------------------------------------------
// 3. _formatBytes — the disk-usage formatter (also consumed by disk-usage.js +
//    settings.js). It DIVERGES from _formatBulkBytes: it has a raw "B" tier and
//    uses 1 decimal for KB/MB. Pinning both keeps a future merge honest — the
//    two are NOT interchangeable.
// ---------------------------------------------------------------------------

test('_formatBytes keeps a raw "B" tier and 1-decimal KB/MB (divergent from _formatBulkBytes)', async ({ page }) => {
  const out = await page.evaluate(() => {
    const w = window as unknown as ModelMgrWin
    return {
      zero: w._formatBytes(0),
      bytes: w._formatBytes(512),
      subKb: w._formatBytes(1023),
      oneKb: w._formatBytes(1024),
      fiveMb: w._formatBytes(5 * 1024 * 1024),
      threeGb: w._formatBytes(3 * 1024 * 1024 * 1024),
      nul: w._formatBytes(null),
    }
  })

  expect(out.zero).toBe('0 B')
  expect(out.bytes).toBe('512 B')
  expect(out.subKb).toBe('1023 B')
  expect(out.oneKb).toBe('1.0 KB')
  expect(out.fiveMb).toBe('5.0 MB')
  expect(out.threeGb).toBe('3.00 GB')
  expect(out.nul).toBe('0 B')
})

// ---------------------------------------------------------------------------
// 4. _parseModelPrepareStart — the prepare-start validation gate. Returns the
//    TRIMMED {status, activeModelId}; throws a TypeError naming the requested id
//    for a blank/non-string id, a non-object/array payload, or a missing/blank
//    status or model_id.
// ---------------------------------------------------------------------------

test('_parseModelPrepareStart returns trimmed status+model_id and throws a descriptive TypeError for every bad shape', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const w = window as unknown as ModelMgrWin
    const grab = (fn: () => unknown) => {
      try {
        return { threw: false, name: '', message: '', value: fn() }
      } catch (error) {
        return { threw: true, name: (error as Error).name, message: (error as Error).message, value: null }
      }
    }
    return {
      ok: w._parseModelPrepareStart({ status: 'downloading', model_id: 'wd14' }, 'wd14'),
      trimmed: w._parseModelPrepareStart({ status: '  downloading  ', model_id: '  wd14  ' }, 'wd14'),
      blankId: grab(() => w._parseModelPrepareStart({ status: 'x', model_id: 'y' }, '   ')),
      nonStringId: grab(() => w._parseModelPrepareStart({ status: 'x', model_id: 'y' }, 5)),
      nullPayload: grab(() => w._parseModelPrepareStart(null, 'wd14')),
      arrayPayload: grab(() => w._parseModelPrepareStart([], 'wd14')),
      stringPayload: grab(() => w._parseModelPrepareStart('downloading', 'wd14')),
      missingStatus: grab(() => w._parseModelPrepareStart({ model_id: 'wd14' }, 'wd14')),
      missingModelId: grab(() => w._parseModelPrepareStart({ status: 'downloading' }, 'wd14')),
      numericStatus: grab(() => w._parseModelPrepareStart({ status: 7, model_id: 'wd14' }, 'wd14')),
    }
  })

  expect(probe.ok).toEqual({ status: 'downloading', activeModelId: 'wd14' })
  expect(probe.trimmed).toEqual({ status: 'downloading', activeModelId: 'wd14' })

  expect(probe.blankId.threw).toBe(true)
  expect(probe.blankId.name).toBe('TypeError')
  expect(probe.blankId.message).toContain('must be a non-empty string')
  expect(probe.nonStringId.message).toContain('must be a non-empty string')

  for (const bad of [probe.nullPayload, probe.arrayPayload, probe.stringPayload]) {
    expect(bad.threw).toBe(true)
    expect(bad.name).toBe('TypeError')
    expect(bad.message).toContain("'wd14'")
    expect(bad.message).toContain('must be an object')
  }

  for (const bad of [probe.missingStatus, probe.missingModelId, probe.numericStatus]) {
    expect(bad.threw).toBe(true)
    expect(bad.message).toContain('must include status and model_id')
  }
})

// ---------------------------------------------------------------------------
// 5. _modelPrepareConflictMessage — interpolates BOTH the requested and the
//    already-active model id into the user-facing conflict string.
// ---------------------------------------------------------------------------

test('_modelPrepareConflictMessage names both the requested and the active model', async ({ page }) => {
  const message = await page.evaluate(() => {
    const w = window as unknown as ModelMgrWin
    return w._modelPrepareConflictMessage('wd14', 'artist')
  })

  expect(message).toContain('wd14')
  expect(message).toContain('artist')
  // The {requested}/{active} placeholders must have been substituted, not left raw.
  expect(message).not.toContain('{requested}')
  expect(message).not.toContain('{active}')
})

// ---------------------------------------------------------------------------
// 6. renderModelManager — summary math (ready/missing/total counts key off the
//    strict `status` field) and the essentials-first split: recommended models
//    render in a leading "Essentials" section, the rest in "Additional".
// ---------------------------------------------------------------------------

test('renderModelManager computes ready/missing/total counts and splits recommended vs optional into two sections', async ({ page }) => {
  await page.route('**/api/models/mirror', async (route) => {
    await route.fulfill({ json: { mirror: 'auto', options: ['auto', 'hf-mirror', 'modelscope'] } })
  })

  const probe = await page.evaluate(() => {
    const w = window as unknown as ModelMgrWin
    w.renderModelManager([
      { id: 'wd14', name: 'WD14', status: 'ready', recommended: true },
      { id: 'clip', name: 'CLIP', status: 'missing', recommended: true },
      { id: 'torii', name: 'ToriiGate', status: 'missing', recommended: false },
    ])
    const summary = document.getElementById('model-manager-summary') as HTMLElement
    const grid = document.getElementById('model-manager-grid') as HTMLElement
    return {
      stats: Array.from(summary.querySelectorAll('.model-manager-stat strong')).map((el) => el.textContent),
      sectionCount: grid.querySelectorAll('.model-manager-section').length,
      cardOrder: Array.from(grid.querySelectorAll<HTMLElement>('.model-card')).map((el) => el.dataset.modelId),
      // The first grid element is the Essentials heading (recommendedModels.length truthy).
      firstIsSection: grid.firstElementChild?.classList.contains('model-manager-section') ?? false,
    }
  })

  // Summary order is ready, missing, total.
  expect(probe.stats).toEqual(['1', '2', '3'])
  expect(probe.sectionCount).toBe(2)
  expect(probe.cardOrder).toEqual(['wd14', 'clip', 'torii'])
  expect(probe.firstIsSection).toBe(true)
})

// ---------------------------------------------------------------------------
// 7. renderModelManager card contract — status falls back to `available` when
//    `status` is absent; the recommended card carries is-recommended; a
//    download-supported card gets a Prepare/Repair button (label keys on Ready);
//    a non-download-supported card gets the manual "no auto download" hint and
//    NO prepare button.
// ---------------------------------------------------------------------------

test('renderModelManager derives status from available, labels the prepare button by state, and hides it when download is unsupported', async ({ page }) => {
  await page.route('**/api/models/mirror', async (route) => {
    await route.fulfill({ json: { mirror: 'auto', options: ['auto', 'hf-mirror', 'modelscope'] } })
  })

  const probe = await page.evaluate(() => {
    const w = window as unknown as ModelMgrWin
    w.renderModelManager([
      { id: 'avail', name: 'Avail', available: true, recommended: true, download_supported: true },
      { id: 'missdl', name: 'MissDL', status: 'missing', recommended: false, download_supported: true },
      { id: 'nodl', name: 'NoDL', status: 'missing', recommended: false, download_supported: false },
    ])
    const grid = document.getElementById('model-manager-grid') as HTMLElement
    const card = (id: string) => grid.querySelector<HTMLElement>(`.model-card[data-model-id="${id}"]`)!
    const avail = card('avail')
    const missdl = card('missdl')
    const nodl = card('nodl')
    return {
      availReady: avail.classList.contains('is-ready'),
      availRecommended: avail.classList.contains('is-recommended'),
      availBadge: avail.querySelector('.model-card-status')?.textContent?.trim(),
      expectedReadyBadge: w.appT('models.readyBadge', 'Ready'),
      availPrepareLabel: avail.querySelector('.btn-prepare-model')?.textContent?.trim(),
      expectedRepair: w.appT('models.repair', 'Recheck / Repair'),
      missdlMissing: missdl.classList.contains('is-missing'),
      missdlPrepareLabel: missdl.querySelector('.btn-prepare-model')?.textContent?.trim(),
      expectedPrepare: w.appT('models.prepare', 'Prepare / Download'),
      nodlHasPrepare: nodl.querySelector('.btn-prepare-model') !== null,
      nodlHint: nodl.textContent?.includes(w.appT('models.noAutoDownload', 'Automatic download not available — follow manual steps above')) ?? false,
    }
  })

  expect(probe.availReady).toBe(true)
  expect(probe.availRecommended).toBe(true)
  expect(probe.availBadge).toBe(probe.expectedReadyBadge)
  expect(probe.availPrepareLabel).toBe(probe.expectedRepair)
  expect(probe.missdlMissing).toBe(true)
  expect(probe.missdlPrepareLabel).toBe(probe.expectedPrepare)
  expect(probe.nodlHasPrepare).toBe(false)
  expect(probe.nodlHint).toBe(true)
})

// ---------------------------------------------------------------------------
// 8. renderModelManager external-link scheme allowlist (defense in depth) —
//    only http(s) URLs survive; javascript:, data:, and scheme-relative paths
//    collapse to href="#". All links open with rel="noopener noreferrer".
// ---------------------------------------------------------------------------

test('renderModelManager only lets http(s) external links through; javascript:/data:/relative collapse to href="#"', async ({ page }) => {
  await page.route('**/api/models/mirror', async (route) => {
    await route.fulfill({ json: { mirror: 'auto', options: ['auto', 'hf-mirror', 'modelscope'] } })
  })

  const probe = await page.evaluate(() => {
    const w = window as unknown as ModelMgrWin
    w.renderModelManager([
      {
        id: 'links',
        name: 'Links',
        status: 'missing',
        download_supported: false,
        external_links: [
          { label: 'Safe', url: 'https://example.com/model' },
          { label: 'JS', url: 'javascript:alert(1)' },
          { label: 'Data', url: 'data:text/html,x' },
          { label: 'Rel', url: '/relative/path' },
        ],
      },
    ])
    const anchors = Array.from(
      document.querySelectorAll<HTMLAnchorElement>('.model-card[data-model-id="links"] .model-card-actions a'),
    )
    return {
      hrefs: anchors.map((a) => a.getAttribute('href')),
      rels: anchors.map((a) => a.getAttribute('rel')),
      targets: anchors.map((a) => a.getAttribute('target')),
    }
  })

  expect(probe.hrefs).toEqual(['https://example.com/model', '#', '#', '#'])
  expect(probe.rels).toEqual(['noopener noreferrer', 'noopener noreferrer', 'noopener noreferrer', 'noopener noreferrer'])
  expect(probe.targets).toEqual(['_blank', '_blank', '_blank', '_blank'])
})

// ---------------------------------------------------------------------------
// 9. showModelSetupGuide — structure: title, provider label, escaped message,
//    manual_steps as an escaped <ol><li> list; a payload without steps renders
//    NO <ol>. The steps sink is escaped (no live markup injection).
// ---------------------------------------------------------------------------

test('showModelSetupGuide renders an escaped ordered step list and omits the list when there are no steps', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const w = window as unknown as ModelMgrWin
    w.showModelSetupGuide({
      provider: 'Civitai',
      message: 'Login wall hit.',
      manual_steps: ['<b>bold step</b>', 'Second step'],
    })
    const backdrop = document.getElementById('model-setup-guide-backdrop') as HTMLElement
    const items = Array.from(backdrop.querySelectorAll('ol li'))
    const withSteps = {
      title: document.getElementById('model-setup-guide-title')?.textContent,
      hasProvider: backdrop.textContent?.includes('Civitai') ?? false,
      hasMessage: backdrop.textContent?.includes('Login wall hit.') ?? false,
      stepCount: items.length,
      firstStepText: items[0]?.textContent,
      // Escaped: the <b> was rendered as text, not as a live element.
      injectedBold: backdrop.querySelector('ol li b') !== null,
    }
    // A second call replaces the first (singleton). No manual_steps => no <ol>.
    w.showModelSetupGuide({ message: 'No steps here.' })
    const backdrop2 = document.getElementById('model-setup-guide-backdrop') as HTMLElement
    const withoutSteps = {
      backdropCount: document.querySelectorAll('#model-setup-guide-backdrop').length,
      hasList: backdrop2.querySelector('ol') !== null,
    }
    return { withSteps, withoutSteps }
  })

  expect(probe.withSteps.title).toBeTruthy()
  expect(probe.withSteps.hasProvider).toBe(true)
  expect(probe.withSteps.hasMessage).toBe(true)
  expect(probe.withSteps.stepCount).toBe(2)
  expect(probe.withSteps.firstStepText).toBe('<b>bold step</b>')
  expect(probe.withSteps.injectedBold).toBe(false)
  expect(probe.withoutSteps.backdropCount).toBe(1)
  expect(probe.withoutSteps.hasList).toBe(false)
})

// ---------------------------------------------------------------------------
// 10. showModelSetupGuide — with NO external_url the "Open Download Page" button
//     is absent and focus (async, ~50ms) falls to the secondary Close button.
// ---------------------------------------------------------------------------

test('showModelSetupGuide without an external_url omits the open button and focuses the Close button', async ({ page }) => {
  await page.evaluate(() => {
    const w = window as unknown as ModelMgrWin
    w.showModelSetupGuide({
      message: 'No download page available.',
      manual_steps: ['Do the thing.'],
    })
  })

  await expect(page.locator('#model-setup-guide-open')).toHaveCount(0)
  await expect(page.locator('#model-setup-guide-close')).toBeFocused()
})

// ---------------------------------------------------------------------------
// 11. showModelSetupGuide — the "Copy folder path" button copies the RAW,
//     unredacted target_dir bytes to the clipboard.
// ---------------------------------------------------------------------------

test('showModelSetupGuide copy button writes the raw target_dir to the clipboard', async ({ page }) => {
  await installClipboardCapture(page)

  await page.evaluate(() => {
    const w = window as unknown as ModelMgrWin
    document.getElementById('toast-container')?.replaceChildren()
    w.showModelSetupGuide({
      message: 'Manual placement required.',
      target_dir: '/home/u/models/wd14',
      manual_steps: ['Save the files here.'],
    })
  })

  await page.locator('#model-setup-guide-copy').click()

  const clip = await page.evaluate(() => (window as unknown as ModelMgrWin).__clip)
  expect(clip).toBe('/home/u/models/wd14')
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(1)
})

// ---------------------------------------------------------------------------
// 12. showModelSetupGuide lifecycle — a single-instance dialog: opening it while
//     one is up replaces the old one (exactly one backdrop); Escape closes it
//     and restores focus to the element that was focused when it opened; a click
//     on the backdrop (not the dialog) closes it too.
// ---------------------------------------------------------------------------

test('showModelSetupGuide is a singleton, restores focus on Escape, and closes on a backdrop click', async ({ page }) => {
  // Singleton: two opens leave exactly one backdrop.
  const singleton = await page.evaluate(() => {
    const w = window as unknown as ModelMgrWin
    w.showModelSetupGuide({ message: 'first', manual_steps: ['a'] })
    w.showModelSetupGuide({ message: 'second', manual_steps: ['b'] })
    return document.querySelectorAll('#model-setup-guide-backdrop').length
  })
  expect(singleton).toBe(1)

  // Escape restores focus to whatever was focused at open time.
  await page.evaluate(() => {
    const opener = document.getElementById('btn-open-model-manager') as HTMLElement
    opener.focus()
    ;(window as unknown as ModelMgrWin).showModelSetupGuide({ message: 'focus test', manual_steps: ['a'] })
  })
  await expect(page.locator('#model-setup-guide-backdrop')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.locator('#model-setup-guide-backdrop')).toHaveCount(0)
  await expect(page.locator('#btn-open-model-manager')).toBeFocused()

  // Backdrop click (target === backdrop) closes; clicking the dialog would not.
  await page.evaluate(() => {
    ;(window as unknown as ModelMgrWin).showModelSetupGuide({ message: 'backdrop test', manual_steps: ['a'] })
  })
  await expect(page.locator('#model-setup-guide-backdrop')).toBeVisible()
  await page.locator('#model-setup-guide-backdrop').click({ position: { x: 5, y: 5 } })
  await expect(page.locator('#model-setup-guide-backdrop')).toHaveCount(0)
})

// ---------------------------------------------------------------------------
// 13. openModelManager — opening clears the first-run pulse (removes the
//     setup-pulse class and records sd-image-sorter-setup-clicked); when the
//     model-status probe fails, the summary shows the "load failed" state.
// ---------------------------------------------------------------------------

test('openModelManager clears the first-run pulse, records the click, and shows a failure summary when status errors', async ({ page }) => {
  await page.route('**/api/models/status', async (route) => {
    await route.abort('failed')
  })
  await page.route('**/api/disk/cache-status', async (route) => {
    await route.fulfill({ json: { safe_to_clean: [], preserved: [], settings: {}, thumbnail_cache: {}, runtime_environment: {} } })
  })

  const probe = await page.evaluate(async () => {
    const w = window as unknown as ModelMgrWin
    localStorage.removeItem('sd-image-sorter-setup-clicked')
    document.getElementById('btn-open-model-manager')?.classList.add('setup-pulse')
    await w.openModelManager('models')
    return {
      pulseRemoved: !document.getElementById('btn-open-model-manager')?.classList.contains('setup-pulse'),
      clickedFlag: localStorage.getItem('sd-image-sorter-setup-clicked'),
      summaryText: document.getElementById('model-manager-summary')?.textContent ?? '',
      expectedFailed: w.appT('models.failedTitle', 'Load failed'),
    }
  })

  expect(probe.pulseRemoved).toBe(true)
  expect(probe.clickedFlag).toBe('1')
  expect(probe.summaryText).toContain(probe.expectedFailed)
})
