import { expect, test, type Page } from '../fixtures/click-ledger'

/**
 * prompt-lab.js god-file — characterization pins (decomposition step 0).
 *
 * `frontend/js/prompt-lab.js` is `const PromptLab = { ...~2460 lines... }`
 * (the gallery.js / v321-ui.js object-literal model, NOT the app.js
 * top-level-function model) published as `window.PromptLab`, plus an idempotent
 * `window.initPromptLab()` boot (guarded by a module-level `promptLabInitialized`
 * flag, run when App.switchView('promptlab') is first hit). It owns the Prompt
 * Helper view's four modes — Stats, Compare, Build, Random — the slot builder,
 * presets, tag-sets/exclusions, the category board + build category workbench,
 * and the Prompt Lab → Gallery round-trip.
 *
 * These pins lock the load-bearing behaviour the FUTURE split (Object.assign
 * mixins over a shared base object, gallery / v321 precedent) must keep
 * byte-for-byte identical, and that is NOT already covered by:
 *   - smoke.spec.ts (affix prepend/append dedupe merge, generate() config
 *     payload, runtime empty states, tag-set option catalog, Build workbench +
 *     use-checked + drop-quality, preset-load-clears-stale + use-gallery,
 *     validate() conflict toast),
 *   - lazy-human.spec.ts (mode tab click → panel .active for stats/compare/
 *     build/random; random+generate+validate button smoke),
 *   - tag-autocomplete.spec.ts (pl-build-prompt / pl-build-negative attach
 *     caption-autocomplete in INSERT mode + caret-word completion).
 *
 * Determinism: every pin runs on the empty clean-DB e2e server with NO seeded
 * image rows. The object surface + pure helpers are probed via
 * `window.PromptLab` directly (the object + all methods exist at script load,
 * before any nav); the two nav-dependent pins (first-use card, build-source
 * handoff option) touch only statically-present DOM. Green on two consecutive
 * clean DBs.
 */

declare global {
  interface Window {
    PromptLab?: any
    initPromptLab?: any
  }
}

// Load-bearing methods the Object.assign split MUST keep on the one reassembled
// object: externally-consumed (openCategoryBoard, ensureBuildSourceOption via
// tag-category-copy.js / app/handoffs.js), contract-name-pinned
// (submitCategoryBoard / _renderBuildCategoryWorkbench / _useCheckedBuildCategories
// / _cleanBuildPrompt in test_frontend_contract.py), and the core mode/slot/
// preset/round-trip API plus the pure helpers exercised below.
const REQUIRED_METHODS = [
  'init',
  'activateMode',
  'generate',
  'randomize',
  'validate',
  'savePreset',
  'loadPreset',
  'deletePreset',
  'applyTagSet',
  'recategorizeTag',
  'toggleTagInSlot',
  'removeTagFromSlot',
  'clearAll',
  'hasBuilderSelection',
  'getSelectedTags',
  'usePromptInGallery',
  'copyPrompt',
  'openCategoryBoard',
  'closeCategoryBoard',
  'submitCategoryBoard',
  'openImagePicker',
  'selectImageFromPicker',
  'ensureBuildSourceOption',
  'loadStats',
  'populateImageSelectors',
  'populateBuildSelector',
  'runCompare',
  'loadBuildSource',
  '_renderBuildCategoryWorkbench',
  '_useCheckedBuildCategories',
  '_cleanBuildPrompt',
  '_getPickerTargetMeta',
  '_mergePromptTags',
  '_normalizePromptTag',
  '_applyPrependAppend',
  '_stripAffixesFromPrompt',
] as const

/** goto '/', wait for App ready + the prompt-lab.js script boot (window.PromptLab
 *  object + window.initPromptLab fn are published at script load, before nav). */
async function openApp(page: Page): Promise<void> {
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect
    .poll(async () =>
      page.evaluate(
        () =>
          Boolean(
            window.App &&
              typeof window.App.loadImages === 'function' &&
              window.App.AppState?.isLoading === false &&
              window.PromptLab &&
              typeof window.PromptLab === 'object' &&
              typeof window.initPromptLab === 'function',
          ),
      ),
    )
    .toBe(true)
}

/** Switch to the Prompt Helper view (view-switch.js calls initPromptLab) and
 *  wait for PromptLab.init() to finish (isReady flips true only after the async
 *  loads + showFirstUseGuide have run). */
async function openPromptLab(page: Page): Promise<void> {
  await openApp(page)
  await page.evaluate(() => window.App.switchView('promptlab'))
  await expect(page.locator('#view-promptlab.active')).toBeVisible()
  await expect
    .poll(async () => page.evaluate(() => window.PromptLab?.isReady === true))
    .toBe(true)
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('sd-image-sorter-lang', 'en')
  })
})

// (1) Object-literal public surface — the contract the split must preserve.
// The future split reassembles `PromptLab` via Object.assign mixins over a
// shared base object (gallery.js / v321-ui.js precedent); every consumer name
// must stay attached to the one `window.PromptLab`, and `window.initPromptLab`
// must remain a function (app/view-switch.js:115, app/handoffs.js:56 call it).
// prompt-lab.js:20, :2493, :2494.
test('window.PromptLab exposes the object-literal surface consumers depend on', async ({ page }) => {
  await openApp(page)

  const surface = await page.evaluate((methods) => {
    const P = window.PromptLab
    const isPlainObject = (v: unknown) =>
      Boolean(v) && typeof v === 'object' && !Array.isArray(v)
    return {
      isObject: isPlainObject(P),
      selfSame: P === window.PromptLab,
      initBootIsFn: typeof window.initPromptLab === 'function',
      missing: methods.filter((name: string) => typeof P?.[name] !== 'function'),
      slotsIsObject: isPlainObject(P?.slots),
      weightsIsObject: isPlainObject(P?.weights),
      lockedIsObject: isPlainObject(P?.locked),
      presetsIsArray: Array.isArray(P?.presets),
      tagSetsIsArray: Array.isArray(P?.tagSets),
      exclusionRulesIsArray: Array.isArray(P?.exclusionRules),
      imageCatalogIsArray: Array.isArray(P?.imageCatalog),
      randomizeExcludedIsSet: P?.randomizeExcludedCategories instanceof Set,
      statsCountsTopTagsIsNumber: typeof P?.statsVisibleCounts?.topTags === 'number',
      generatedPromptIsString: typeof P?.generatedPrompt === 'string',
      isReadyIsBoolean: typeof P?.isReady === 'boolean',
      eventsBoundIsBoolean: typeof P?.eventsBound === 'boolean',
    }
  }, REQUIRED_METHODS as unknown as string[])

  expect(surface.isObject).toBe(true)
  expect(surface.selfSame).toBe(true)
  expect(surface.initBootIsFn).toBe(true)
  expect(surface.missing).toEqual([])
  expect(surface.slotsIsObject).toBe(true)
  expect(surface.weightsIsObject).toBe(true)
  expect(surface.lockedIsObject).toBe(true)
  expect(surface.presetsIsArray).toBe(true)
  expect(surface.tagSetsIsArray).toBe(true)
  expect(surface.exclusionRulesIsArray).toBe(true)
  expect(surface.imageCatalogIsArray).toBe(true)
  expect(surface.randomizeExcludedIsSet).toBe(true)
  expect(surface.statsCountsTopTagsIsNumber).toBe(true)
  expect(surface.generatedPromptIsString).toBe(true)
  expect(surface.isReadyIsBoolean).toBe(true)
  expect(surface.eventsBoundIsBoolean).toBe(true)
})

// (2) activateMode: valid ids honored, unknown id normalizes to 'stats'.
// A real runtime decision (prompt-lab.js:102 —
// `['stats','compare','build','random'].includes(mode) ? mode : 'stats'`), NOT
// a hardcode: the valid-id branch below proves each real id is honored. Exactly
// one `.promptlab-mode.active` panel and one `.promptlab-tab.active` at a time.
// (lazy-human pins the click→panel toggle; the unknown-id fallback is unpinned.)
test('activateMode honors real modes and normalizes an unknown mode to stats', async ({ page }) => {
  await openApp(page)

  const result = await page.evaluate(() => {
    const snapshot = (mode: string) => {
      window.PromptLab.activateMode(mode)
      const activePanel = document.querySelector('.promptlab-mode.active') as HTMLElement | null
      const activeTab = document.querySelector('.promptlab-tab.active') as HTMLElement | null
      return {
        panel: activePanel?.id || null,
        tab: activeTab?.dataset.mode || null,
        activePanelCount: document.querySelectorAll('.promptlab-mode.active').length,
        activeTabCount: document.querySelectorAll('.promptlab-tab.active').length,
      }
    }
    return {
      build: snapshot('build'),
      compare: snapshot('compare'),
      random: snapshot('random'),
      stats: snapshot('stats'),
      unknown: snapshot('not-a-real-mode'),
    }
  })

  expect(result.build).toEqual({ panel: 'promptlab-mode-build', tab: 'build', activePanelCount: 1, activeTabCount: 1 })
  expect(result.compare).toEqual({ panel: 'promptlab-mode-compare', tab: 'compare', activePanelCount: 1, activeTabCount: 1 })
  expect(result.random).toEqual({ panel: 'promptlab-mode-random', tab: 'random', activePanelCount: 1, activeTabCount: 1 })
  expect(result.stats).toEqual({ panel: 'promptlab-mode-stats', tab: 'stats', activePanelCount: 1, activeTabCount: 1 })
  // Unknown mode falls back to Stats (never leaves the view in a no-panel state).
  expect(result.unknown).toEqual({ panel: 'promptlab-mode-stats', tab: 'stats', activePanelCount: 1, activeTabCount: 1 })
})

// (3) _getPickerTargetMeta — the pure target→select mapping shared by
// openImagePicker (writes the title + renders) and selectImageFromPicker
// (writes the resolved <select>.value). compare-a/compare-b map to their own
// selects; every other target (incl. 'build') maps to pl-build-source.
// prompt-lab.js:268.
test('_getPickerTargetMeta maps picker targets to their select ids', async ({ page }) => {
  await openApp(page)

  const meta = await page.evaluate(() => {
    const P = window.PromptLab
    return {
      compareA: P._getPickerTargetMeta('compare-a').selectId,
      compareB: P._getPickerTargetMeta('compare-b').selectId,
      build: P._getPickerTargetMeta('build').selectId,
      fallback: P._getPickerTargetMeta('anything-else').selectId,
    }
  })

  expect(meta.compareA).toBe('pl-compare-a')
  expect(meta.compareB).toBe('pl-compare-b')
  expect(meta.build).toBe('pl-build-source')
  // Any non-compare target (including the default Build) resolves to the build select.
  expect(meta.fallback).toBe('pl-build-source')
})

// (4) _normalizePromptTag + _mergePromptTags — the underscore/space/case fold
// dedupe that underpins affix merge, tag-set apply, and random draft insert.
// The normalize key folds spaces→'_' and lowercases; merge keeps the FIRST
// original spelling and dedupes on that key. prompt-lab.js:442, :450.
test('_normalizePromptTag folds space/underscore/case; _mergePromptTags keeps first spelling', async ({ page }) => {
  await openApp(page)

  const result = await page.evaluate(() => {
    const P = window.PromptLab
    return {
      normSpace: P._normalizePromptTag('Best Quality'),
      normUnderscore: P._normalizePromptTag('best_quality'),
      normCollapse: P._normalizePromptTag('  best   quality  '),
      merged: P._mergePromptTags(['best quality'], ['best_quality', '1girl'], ['1GIRL', 'solo']),
    }
  })

  // 'Best Quality' and 'best_quality' collapse to the same dedupe key.
  expect(result.normSpace).toBe('best_quality')
  expect(result.normUnderscore).toBe('best_quality')
  expect(result.normCollapse).toBe('best_quality')
  // First spelling ('best quality', '1girl') wins; 'best_quality' and '1GIRL' are folded out.
  expect(result.merged).toEqual(['best quality', '1girl', 'solo'])
})

test('Build prompt cleanup preserves LoRA directives across every cleanup action', async ({ page }) => {
  const categorizeRequests: string[][] = []
  const consoleIssues: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'warning' || message.type() === 'error') {
      consoleIssues.push(`${message.type()}: ${message.text()}`)
    }
  })
  await page.route('**/api/images/987654321', async (route) => {
    await route.fulfill({
      json: {
        image: {
          id: 987654321,
          prompt: '',
          negative_prompt: '',
          checkpoint: '',
          width: 512,
          height: 512,
          aesthetic_score: null,
        },
        tags: [],
      },
    })
  })
  await page.route('**/api/prompts/categorize', async (route) => {
    const tags = route.request().postDataJSON() as string[]
    categorizeRequests.push(tags)
    await route.fulfill({
      json: {
        results: tags.map((tag) => ({
          tag,
          category: tag === 'best_quality' ? 'quality' : 'unknown',
        })),
      },
    })
  })
  await openPromptLab(page)
  await page.locator('.promptlab-tab[data-mode="build"]').click()
  await expect(page.locator('#promptlab-mode-build')).toHaveClass(/active/)
  await page.evaluate(() => window.PromptLab.ensureBuildSourceOption(987654321, 'LoRA cleanup fixture'))
  await page.locator('#pl-build-source').selectOption('987654321')
  await expect(page.locator('#pl-build-editor')).toBeVisible()

  const lora = '<LORA:My_Model  Name:0.75>'
  const promptArea = page.locator('#pl-build-prompt')
  const clean = async (value: string, buttonId: string, expected: string) => {
    await promptArea.fill(value)
    const button = page.locator(buttonId)
    await button.focus()
    await expect(page.locator('.caption-autocomplete-dropdown')).toBeHidden()
    await button.click()
    await expect(promptArea).toHaveValue(expected)
  }
  const viewports = [
    { width: 1366, height: 768 },
    { width: 1920, height: 1080 },
    { width: 2560, height: 1440 },
  ]

  for (const viewport of viewports) {
    await page.setViewportSize(viewport)
    await clean(
      `portrait, ${lora}, ${lora}, sunset`,
      '#pl-build-clean-prompt',
      `portrait, ${lora}, ${lora}, sunset`,
    )
    await clean(
      `portrait_tag ${lora}, ${lora}, sunset_glow`,
      '#pl-build-space-tags',
      `portrait tag ${lora}, ${lora}, sunset glow`,
    )
    await clean(
      `sunset ${lora}, portrait ${lora}, ${lora}, ${lora}`,
      '#pl-build-reorder',
      `sunset, portrait, ${lora}, ${lora}, ${lora}, ${lora}`,
    )
    await clean(
      `best_quality, portrait ${lora}${lora}`,
      '#pl-build-drop-quality',
      `portrait, ${lora}, ${lora}`,
    )
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
  }

  expect(categorizeRequests).toEqual([
    ['sunset', 'portrait'],
    ['best_quality', 'portrait'],
    ['sunset', 'portrait'],
    ['best_quality', 'portrait'],
    ['sunset', 'portrait'],
    ['best_quality', 'portrait'],
  ])
  expect(consoleIssues).toEqual([])
})

// (5) _applyPrependAppend / _stripAffixesFromPrompt — the affix round-trip.
// apply merges prepend + core + append (deduped); strip is its inverse, pulling
// the affix tokens back out to recover the core. Smoke pins the forward path
// end-to-end through the UI; the inverse strip (used by handleAffixInput to keep
// the core stable while affixes change) is unpinned. prompt-lab.js:466, :473.
test('affix apply/strip round-trips the core prompt', async ({ page }) => {
  await openApp(page)

  const result = await page.evaluate(() => {
    const P = window.PromptLab
    const prevPrepend = P.prependTags
    const prevAppend = P.appendTags
    P.prependTags = 'masterpiece'
    P.appendTags = 'highres'
    const applied = P._applyPrependAppend('1girl, solo')
    // A core that already contains an affix token must not double it.
    const appliedDedupe = P._applyPrependAppend('masterpiece, 1girl')
    const stripped = P._stripAffixesFromPrompt(applied)
    P.prependTags = prevPrepend
    P.appendTags = prevAppend
    return { applied, appliedDedupe, stripped }
  })

  expect(result.applied).toBe('masterpiece, 1girl, solo, highres')
  expect(result.appliedDedupe).toBe('masterpiece, 1girl, highres')
  // Strip is the inverse of apply — the affixes come back out, core survives.
  expect(result.stripped).toBe('1girl, solo')
})

// (6) builder slot state — hasBuilderSelection / getSelectedTags read the slot
// map; clearAll wipes slots/weights/locked and blanks the generated output.
// getSelectedTags dedupes ACROSS slots via a Set. clearAll is the "start over"
// affordance (unpinned elsewhere). prompt-lab.js:377, :524, :1551.
test('slot readers reflect slots and clearAll wipes builder state', async ({ page }) => {
  await openApp(page)

  const seeded = await page.evaluate(() => {
    const P = window.PromptLab
    P.slots = { character: ['1girl'], style: ['1girl', 'cinematic_lighting'] }
    P.weights = { character: 80 }
    P.locked = { character: true }
    P.generatedPrompt = '1girl, cinematic_lighting'
    return {
      hasSelection: P.hasBuilderSelection(),
      selectedTags: P.getSelectedTags(),
    }
  })
  expect(seeded.hasSelection).toBe(true)
  // Cross-slot dedupe: '1girl' appears in two slots but once in the result.
  expect(seeded.selectedTags).toEqual(['1girl', 'cinematic_lighting'])

  const cleared = await page.evaluate(() => {
    const P = window.PromptLab
    P.clearAll()
    return {
      slots: P.slots,
      weights: P.weights,
      locked: P.locked,
      hasSelection: P.hasBuilderSelection(),
      generatedPrompt: P.generatedPrompt,
      outputValue: (document.getElementById('promptlab-output') as HTMLTextAreaElement | null)?.value ?? null,
    }
  })
  expect(cleared.slots).toEqual({})
  expect(cleared.weights).toEqual({})
  expect(cleared.locked).toEqual({})
  expect(cleared.hasSelection).toBe(false)
  expect(cleared.generatedPrompt).toBe('')
  expect(cleared.outputValue).toBe('')
})

// (7) ensureBuildSourceOption — the gallery/similar/modal → Build handoff seam
// (app/handoffs.js:67 calls window.PromptLab.ensureBuildSourceOption). The Build
// <select> only lists the newest-200 catalog, so an out-of-catalog image id gets
// a one-off <option> injected; otherwise `select.value = id` would silently reset
// to '' and hide the editor. Idempotent, and rejects non-positive/NaN ids.
// prompt-lab.js:2142.
test('ensureBuildSourceOption injects a one-off option for out-of-catalog ids', async ({ page }) => {
  await openApp(page)

  const result = await page.evaluate(() => {
    const P = window.PromptLab
    const select = document.getElementById('pl-build-source') as HTMLSelectElement
    const count = (value: string) =>
      Array.from(select.options).filter((option) => option.value === value).length
    const first = P.ensureBuildSourceOption(987654)
    const second = P.ensureBuildSourceOption(987654) // idempotent — no duplicate
    const invalidZero = P.ensureBuildSourceOption(0)
    const invalidNaN = P.ensureBuildSourceOption('not-a-number')
    return {
      first,
      second,
      optionCount: count('987654'),
      invalidZero,
      invalidNaN,
      hasZeroOption: count('0'),
    }
  })

  expect(result.first).toBe(true)
  expect(result.second).toBe(true)
  expect(result.optionCount).toBe(1)
  // Non-positive / non-numeric ids are rejected and add no option.
  expect(result.invalidZero).toBe(false)
  expect(result.invalidNaN).toBe(false)
  expect(result.hasZeroOption).toBe(0)
})

// (8) first-use guide card is quiet by default; Help unhides it, dismiss persists.
test('first-use guide card stays hidden until Help, then dismiss persists it', async ({ page }) => {
  await openPromptLab(page)

  const before = await page.evaluate(() => ({
    hidden: (document.getElementById('promptlab-start-card') as HTMLElement | null)?.hidden ?? null,
    flag: localStorage.getItem('promptlab-guide-seen'),
  }))
  expect(before.hidden).toBe(true)
  expect(before.flag).toBeNull()

  await page.locator('#btn-help').click()
  const guideClose = page.locator('.guide-modal-close')
  if (await guideClose.count()) {
    await guideClose.first().click({ force: true })
  }
  await expect(page.locator('#promptlab-start-card')).toBeVisible()

  await page.locator('#promptlab-start-dismiss').click()

  const after = await page.evaluate(() => ({
    hidden: (document.getElementById('promptlab-start-card') as HTMLElement | null)?.hidden ?? null,
    flag: localStorage.getItem('promptlab-guide-seen'),
  }))
  expect(after.hidden).toBe(true)
  expect(after.flag).toBe('true')
})

// (9) usePromptInGallery empty guard — with no built or typed prompt, the
// Prompt Lab → Gallery round-trip must NOT switch views or mutate filters; it
// only shows an info toast. Smoke pins the happy path (a real prompt lands in
// the gallery summary); the empty guard is unpinned. prompt-lab.js:1501.
test('usePromptInGallery with no prompt stays put and does not switch to gallery', async ({ page }) => {
  await openPromptLab(page)

  await page.evaluate(() => {
    const P = window.PromptLab
    P.generatedPrompt = ''
    P.generatedPromptCore = ''
    const output = document.getElementById('promptlab-output') as HTMLTextAreaElement | null
    if (output) output.value = ''
    P.usePromptInGallery()
  })

  // View stays on Prompt Helper; no navigation to gallery.
  await expect(page.locator('#view-promptlab.active')).toBeVisible()
  await expect(page.locator('#view-gallery.active')).toHaveCount(0)
})
