import { expect, test, type Page } from '../fixtures/click-ledger'

/**
 * Characterization pins for the image-reader.js god-file (1,749 lines) — "step 0"
 * of a later verbatim decomposition (mirrors the shipped gallery.js -> gallery/*.js,
 * app.js -> app/*.js, censor, dataset, autosep, manual-sort splits).
 *
 * image-reader.js publishes ONE global — `window.ImageReader` — built as a single
 * object LITERAL inside an IIFE (`(function(){ const ImageReader = { ...~1730 lines... };
 * window.ImageReader = ImageReader; ImageReader.init(); })()`). There is NO
 * closure-private state shared across methods (every method uses `this._x` + the
 * `window.*` globals), so — unlike queue-solitaire.js's true-IIFE exemption — this is
 * fully splittable by reassembling the object incrementally (`Object.assign(
 * window.ImageReader, {...})`), exactly like gallery.js. The object is NOT sealed.
 *
 * Cross-module consumers the split must keep working:
 *   - app/handoffs.js calls `window.ImageReader.openLibraryImage(id, filename)` — the
 *     ONLY external runtime entry point besides the DOMContentLoaded `init()`.
 *   - backend/tests/test_frontend_contract.py reads the file text and asserts the
 *     literals `_copyPromptCategory` + `_renderReaderCategoryTags` are present.
 *
 * Scope note — pins here deliberately AVOID what the two neighboring reader specs
 * already cover so this stays additive:
 *   - real multi-generator /api/parse-image + metadata-editor save/overwrite 409 flow
 *     -> reader-live.spec.ts
 *   - clipboard paste UI (button arm, Ctrl+V pipeline, missing-metadata warning text)
 *     -> reader-paste.spec.ts
 * This spec pins the currently-UNPINNED units: the (unsealed) public surface shape,
 * the reader<->obfuscation workspace-tool tab contract, the parse->DOM render mapping,
 * the prompt-format cycle, the pure metadata extractors (_getLoras / _getAllHashes /
 * _getGenParams / _cleanModelName), the clipboard-missing decision, the metadata-editor
 * population + save-path helpers, openLibraryImage (the handoffs.js seam), and _clear.
 *
 * No DB seeding: every case drives the reader in-page via a route-mocked
 * /api/parse-image + direct `window.ImageReader.*` calls (avoids the
 * `.tmp/e2e-data-<port>` cross-run pollution pitfall). It MUST pass before AND after
 * the refactor.
 */

test.describe.configure({ mode: 'serial' })

// Minimal valid 1x1 PNG, reused for the mocked file/blob payloads.
const TINY_PNG_BASE64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAAWgmWQ0AAAAASUVORK5CYII='

/** Reveal #view-reader and wait for ImageReader.init() to have wired its listeners. */
async function openReaderView(page: Page): Promise<void> {
  await page.goto('/')
  await page.waitForLoadState('domcontentloaded')
  // image-reader.js is a classic script that self-inits on DOMContentLoaded; wait for
  // its public entry AND the _eventsBound flag it sets at the end of init() so tab/
  // button clicks below are guaranteed bound.
  await page.waitForFunction(() => {
    const IR = (window as unknown as { ImageReader?: Record<string, unknown> }).ImageReader
    return !!IR && typeof IR.openLibraryImage === 'function' && IR._eventsBound === true
  })
  await page.evaluate(() => {
    const view = document.getElementById('view-reader')
    if (view) {
      document.querySelectorAll('.view').forEach((node) => {
        if (node !== view) (node as HTMLElement).style.display = 'none'
      })
      ;(view as HTMLElement).style.display = 'flex'
      view.classList.add('active')
    }
    document.getElementById('reader-tool-panel-reader')?.classList.add('active')
  })
}

/** Route /api/parse-image to a fixed result and drive one parse via the file input. */
async function loadMockParse(page: Page, result: Record<string, unknown>): Promise<void> {
  await page.route('**/api/parse-image', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(result) })
  })
  await page.setInputFiles('#reader-file-input', {
    name: 'pin-sample.png',
    mimeType: 'image/png',
    buffer: Buffer.from(TINY_PNG_BASE64, 'base64'),
  })
  await expect(page.locator('#reader-result-panel')).toBeVisible({ timeout: 10000 })
}

test.beforeEach(async ({ page }) => {
  await openReaderView(page)
})

// ---------------------------------------------------------------------------
// 1. Public surface — the (unsealed) window.ImageReader other modules depend on.
// ---------------------------------------------------------------------------

test('window.ImageReader is an unsealed object exposing the load-bearing public surface', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const IR = (window as any).ImageReader
    // Public entry points + the internal methods the verbatim split must keep callable
    // on the reassembled object (init() self-boots; openLibraryImage is the handoffs.js
    // seam; the rest are reached only via `this.` but a bad cut would drop them).
    const requiredFns = [
      'init', 'openLibraryImage',
      '_handleFile', '_renderResult', '_renderPromptSection', '_renderCharacterPrompts', '_buildPromptView',
      '_getGenParams', '_getLoras', '_getAllHashes', '_getModelAssets', '_cleanModelName',
      '_toggleFormat', '_copy', '_copyPromptCategory', '_clear', '_switchWorkspaceTool',
      '_renderReaderCategoryTags', '_populateMetadataEditor', '_saveEditedMetadata',
      '_renderQuickFacts', '_renderModelAssetsSection', '_renderReaderColorDistribution',
      '_handlePaste', '_getClipboardWarning', '_clipboardMetadataMissing',
      '_getSuggestedOutputFilename', '_buildSuggestedOutputPath', '_pathsReferToSameFile',
    ]
    const requiredProps = [
      '_currentResult', '_currentSourcePath', '_currentLibraryImageId', '_promptFormat',
      '_histogramMode', '_collapsedState', '_eventsBound', '_currentReaderTags',
    ]
    return {
      isObject: IR !== null && typeof IR === 'object',
      sealed: Object.isSealed(IR),
      identity: (window as any).ImageReader === IR,
      missingFns: requiredFns.filter((k) => typeof IR[k] !== 'function'),
      missingProps: requiredProps.filter((k) => !(k in IR)),
      promptFormat: IR._promptFormat,
      histogramMode: IR._histogramMode,
      collapsedKeys: Object.keys(IR._collapsedState).sort(),
      eventsBound: IR._eventsBound,
    }
  })

  expect(probe.isObject).toBe(true)
  // Deliberately NOT sealed: the split reassembles it with Object.assign.
  expect(probe.sealed).toBe(false)
  expect(probe.identity).toBe(true)
  expect(probe.missingFns).toEqual([])
  expect(probe.missingProps).toEqual([])
  // Documented default state.
  expect(probe.promptFormat).toBe('original')
  expect(probe.histogramMode).toBe('rgb')
  expect(probe.eventsBound).toBe(true)
  expect(probe.collapsedKeys).toEqual([
    'categoryTags', 'characters', 'editor', 'hashes', 'loras', 'modelAssets', 'negative', 'params', 'prompt',
  ])
})

// ---------------------------------------------------------------------------
// 2. Workspace tool tabs — reader <-> obfuscation share #view-reader.
// ---------------------------------------------------------------------------

test('the reader/obfuscation tool tabs toggle panels, aria-selected, and the title i18n key', async ({ page }) => {
  const readerPanel = page.locator('#reader-tool-panel-reader')
  const obfPanel = page.locator('#reader-tool-panel-obfuscation')
  const title = page.locator('#view-reader .reader-tools-title')

  // Switch to the 隐私处理 (obfuscation) tool.
  await page.locator('#reader-tool-tab-obfuscation').click()
  await expect(obfPanel).toHaveClass(/active/)
  await expect(readerPanel).not.toHaveClass(/active/)
  await expect(readerPanel).toBeHidden()
  await expect(page.locator('#reader-tool-tab-obfuscation')).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('#reader-tool-tab-reader')).toHaveAttribute('aria-selected', 'false')
  // Title element follows the active tool by swapping its data-i18n key.
  await expect(title).toHaveAttribute('data-i18n', 'reader.workspaceTitleObfuscation')

  // Switch back to the metadata reader.
  await page.locator('#reader-tool-tab-reader').click()
  await expect(readerPanel).toHaveClass(/active/)
  await expect(obfPanel).not.toHaveClass(/active/)
  await expect(obfPanel).toBeHidden()
  await expect(title).toHaveAttribute('data-i18n', 'reader.workspaceTitle')
})

// ---------------------------------------------------------------------------
// 3. _handleFile + _renderResult — parse result -> DOM (badge, facts, checkpoint).
// ---------------------------------------------------------------------------

test('a parsed result renders the generator badge, checkpoint clean-name, quick facts, and swaps to the loaded layout', async ({ page }) => {
  await loadMockParse(page, {
    generator: 'webui',
    prompt: 'a scenic portrait, masterpiece',
    negative_prompt: 'blurry',
    checkpoint: 'models\\sd\\coolMix_v3.safetensors',
    width: 768,
    height: 512,
    file_size: 2048,
    loras: [],
    metadata: {
      _parsed: {
        generation_params: { seed: 12345, steps: 28, cfg_scale: 7, sampler: 'Euler a', model_hash: 'abc123' },
      },
    },
  })

  // Generator badge: text via App.formatGeneratorLabel, class carries gen-<lower>.
  await expect(page.locator('#reader-generator')).toHaveText('WebUI')
  await expect(page.locator('#reader-generator')).toHaveClass(/gen-webui/)
  // Prompt + checkpoint (path + extension stripped to the clean model name).
  await expect(page.locator('#reader-prompt-text')).toContainText('scenic portrait')
  await expect(page.locator('#reader-checkpoint')).toHaveText('coolMix_v3')
  await expect(page.locator('#reader-checkpoint')).toHaveAttribute('title', 'models\\sd\\coolMix_v3.safetensors')

  // Quick facts: checkpoint + size + seed + steps + cfg + sampler are all present -> 6 chips.
  const facts = page.locator('#reader-quick-facts .reader-quick-fact')
  await expect(facts).toHaveCount(6)
  await expect(page.locator('#reader-quick-facts')).toContainText('12345')

  // Empty-state was dropped; the container now carries the image layout flag.
  await expect(page.locator('#reader-drop-zone')).toBeHidden()
  await expect(page.locator('.reader-container')).toHaveClass(/reader-has-image/)
})

test('NovelAI character slots render in the Reader instead of disappearing after parse', async ({ page }) => {
  await loadMockParse(page, {
    generator: 'nai',
    prompt: '3::blender (medium)::, masterpiece',
    negative_prompt: 'lowres',
    checkpoint: 'NovelAI Diffusion V5 0ADF9AB7',
    width: 832,
    height: 1216,
    loras: [],
    metadata: {
      _parsed: {
        generation_params: { steps: 28, sampler: 'k_euler' },
        character_prompts: [
          { index: 0, prompt: 'girl, remielle, huge breasts', negative_prompt: 'lowres', center: { x: 0.5, y: 0.5 } },
          { index: 1, prompt: 'girl, sitting, casual clothes', center: { x: 0.2, y: 0.8 } },
        ],
      },
    },
  })

  const section = page.locator('#reader-characters-section')
  await expect(section).toBeVisible()
  await expect(page.locator('#reader-characters-list .character-card')).toHaveCount(2)
  await expect(page.locator('#reader-characters-list')).toContainText('remielle')
  await expect(page.locator('#reader-characters-list')).toContainText('casual clothes')
  await expect(page.locator('#reader-prompt-text')).toContainText('3::blender (medium)::')
  await expect(page.locator('#reader-checkpoint')).toHaveText('NovelAI Diffusion V5 0ADF9AB7')
})

// ---------------------------------------------------------------------------
// 4. _toggleFormat — cycles original -> sd -> nai -> original.
// ---------------------------------------------------------------------------

test('the format toggle button cycles _promptFormat original -> sd -> nai -> original', async ({ page }) => {
  // The toggle button lives in the result panel, which is hidden until a parse
  // lands — load one (reset makes the starting format 'original').
  await loadMockParse(page, {
    generator: 'webui',
    prompt: 'format cycle prompt',
    negative_prompt: '',
    checkpoint: '',
    width: 512,
    height: 512,
    loras: [],
    metadata: { _parsed: { generation_params: {} } },
  })
  const readFormat = () => page.evaluate(() => (window as any).ImageReader._promptFormat)
  const button = page.locator('#reader-toggle-format')

  expect(await readFormat()).toBe('original')
  await button.click()
  expect(await readFormat()).toBe('sd')
  await button.click()
  expect(await readFormat()).toBe('nai')
  await button.click()
  expect(await readFormat()).toBe('original')
  // The button label is kept in sync (non-empty, driven by _updateFormatButton).
  await expect(button).not.toHaveText('')
})

// ---------------------------------------------------------------------------
// 5. _getLoras — direct field wins, else <lora:...> prompt fallback, else empty.
// ---------------------------------------------------------------------------

test('_getLoras prefers the loras field, falls back to <lora:name:weight> prompt tags, and returns [] otherwise', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const IR = (window as any).ImageReader
    return {
      directField: IR._getLoras({ loras: ['alpha', 'beta'], prompt: '<lora:ignored:1>' }),
      jsonStringField: IR._getLoras({ loras: '["gamma"]', prompt: '' }),
      promptFallback: IR._getLoras({ loras: [], prompt: 'cat <lora:styleA:0.8>, dog <lora:styleB:1.2>' }),
      empty: IR._getLoras({ loras: [], prompt: 'no loras here' }),
    }
  })

  expect(probe.directField).toEqual(['alpha', 'beta'])
  expect(probe.jsonStringField).toEqual(['gamma'])
  // The regex captures the name segment only (weight dropped).
  expect(probe.promptFallback).toEqual(['styleA', 'styleB'])
  expect(probe.empty).toEqual([])
})

test('raw LoRA prompt directives stay in the LoRA section and out of category chips', async ({ page }) => {
  const categorizeRequests: string[][] = []
  await page.route('**/api/prompts/categorize', async (route) => {
    const tags = route.request().postDataJSON() as string[]
    categorizeRequests.push(tags)
    await route.fulfill({
      json: {
        results: tags.map((tag) => ({
          tag,
          category: tag.includes('<lora:') || tag === 'standing' ? 'pose' : 'background',
        })),
      },
    })
  })

  await loadMockParse(page, {
    generator: 'webui',
    prompt: 'standing, <lora:very_long_style:0.85>, sunset',
    negative_prompt: '',
    checkpoint: '',
    width: 512,
    height: 768,
    loras: [],
    metadata: { _parsed: { generation_params: {} } },
  })

  await expect.poll(() => categorizeRequests.length).toBeGreaterThan(0)
  expect(categorizeRequests).toEqual([['standing', 'sunset']])
  await expect(page.locator('#reader-loras')).toContainText('very_long_style')
  await expect(page.locator('#reader-category-tags')).not.toContainText('<lora:')
  await expect(page.locator('.reader-category-group[data-group="pose"] .reader-category-chip')).toHaveText('standing')
  expect(await page.evaluate(() => (window as any).TagCategoryCopy.parsePromptTags(
    'portrait <lora:inline_style:0.7>, <camera:low>, <LORA:upper_case_style:1>',
  ))).toEqual(['portrait <lora:inline_style:0.7>', '<camera:low>', '<LORA:upper_case_style:1>'])

  await page.evaluate(async () => {
    const copy = (window as any).TagCategoryCopy
    const promptTags = copy.parsePromptTags(
      'portrait <lora:inline_style:0.7>, <camera:low>, <LORA:upper_case_style:1>',
    )
    await copy.classifyTags(copy.getPromptCategoryTags(promptTags))
    const directTags = await copy.getTagsFromSource({
      tags: ['<lora:database_label:1>'],
      prompt: '<lora:prompt_style:1>',
    })
    await copy.classifyTags(directTags)
  })
  expect(categorizeRequests).toEqual([
    ['standing', 'sunset'],
    ['portrait', '<camera:low>'],
    ['<lora:database_label:1>'],
  ])
})

// ---------------------------------------------------------------------------
// 6. _getAllHashes — model_hash + lora_hashes/ti_hashes -> keyed map.
// ---------------------------------------------------------------------------

test('_getAllHashes aggregates the model hash and parses the WebUI lora/ti hash strings', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const IR = (window as any).ImageReader
    return IR._getAllHashes({
      metadata: {
        _parsed: {
          generation_params: {
            model_hash: 'deadbeef',
            lora_hashes: 'styleA: 1111aaaa, styleB: 2222bbbb',
            ti_hashes: 'embed1: 3333cccc',
          },
        },
      },
    })
  })

  expect(probe).toEqual({
    model: 'deadbeef',
    'lora:styleA': '1111aaaa',
    'lora:styleB': '2222bbbb',
    'ti:embed1': '3333cccc',
  })
})

// ---------------------------------------------------------------------------
// 7. _getGenParams — string vs object metadata, _parsed nesting, empty fallback.
// ---------------------------------------------------------------------------

test('_getGenParams unwraps _parsed.generation_params from string or object metadata and falls back to {}', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const IR = (window as any).ImageReader
    return {
      fromObjectParsed: IR._getGenParams({ metadata: { _parsed: { generation_params: { steps: 20 } } } }),
      fromString: IR._getGenParams({ metadata: JSON.stringify({ _parsed: { generation_params: { cfg_scale: 6 } } }) }),
      fromTopLevel: IR._getGenParams({ metadata: { generation_params: { seed: 7 } } }),
      missingMetadata: IR._getGenParams({}),
      badJson: IR._getGenParams({ metadata: '{not json' }),
    }
  })

  expect(probe.fromObjectParsed).toEqual({ steps: 20 })
  expect(probe.fromString).toEqual({ cfg_scale: 6 })
  expect(probe.fromTopLevel).toEqual({ seed: 7 })
  expect(probe.missingMetadata).toEqual({})
  expect(probe.badJson).toEqual({})
})

// ---------------------------------------------------------------------------
// 8. _cleanModelName — strip directory prefix + known model extensions.
// ---------------------------------------------------------------------------

test('_cleanModelName strips path prefixes and model file extensions', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const IR = (window as any).ImageReader
    return {
      backslash: IR._cleanModelName('Anima\\anime\\coolMix.safetensors'),
      forwardslash: IR._cleanModelName('a/b/c/model.ckpt'),
      plain: IR._cleanModelName('justAName'),
      empty: IR._cleanModelName(''),
    }
  })

  expect(probe.backslash).toBe('coolMix')
  expect(probe.forwardslash).toBe('model')
  expect(probe.plain).toBe('justAName')
  expect(probe.empty).toBe('')
})

// ---------------------------------------------------------------------------
// 9. _clipboardMetadataMissing — clipboard-only, empty-metadata-only gate.
// ---------------------------------------------------------------------------

test('_clipboardMetadataMissing is true only for clipboard sources whose parsed result carries no SD metadata', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const IR = (window as any).ImageReader
    const emptyResult = { generator: 'unknown', prompt: '', checkpoint: '', metadata: { _parsed: { generation_params: {} } } }
    const richResult = { generator: 'webui', prompt: 'a cat', checkpoint: '', metadata: { _parsed: { generation_params: {} } } }
    return {
      clipboardEmpty: IR._clipboardMetadataMissing(emptyResult, 'clipboard-shortcut'),
      clipboardButtonEmpty: IR._clipboardMetadataMissing(emptyResult, 'clipboard-button'),
      clipboardRich: IR._clipboardMetadataMissing(richResult, 'clipboard-shortcut'),
      fileEmpty: IR._clipboardMetadataMissing(emptyResult, 'file'),
      // The warning helper mirrors the same gate: empty string for non-clipboard.
      warningForFile: IR._getClipboardWarning(emptyResult, 'file'),
      warningForClipboardEmpty: IR._getClipboardWarning(emptyResult, 'clipboard-shortcut'),
    }
  })

  expect(probe.clipboardEmpty).toBe(true)
  expect(probe.clipboardButtonEmpty).toBe(true)
  // Real prompt present -> not "missing" even from clipboard.
  expect(probe.clipboardRich).toBe(false)
  // File drops never trigger the clipboard-loss warning.
  expect(probe.fileEmpty).toBe(false)
  expect(probe.warningForFile).toBe('')
  expect(typeof probe.warningForClipboardEmpty).toBe('string')
  expect(probe.warningForClipboardEmpty.length).toBeGreaterThan(0)
})

// ---------------------------------------------------------------------------
// 10. _populateMetadataEditor — result -> editor inputs + default format.
// ---------------------------------------------------------------------------

test('parsing populates the metadata editor inputs from the parsed result', async ({ page }) => {
  await loadMockParse(page, {
    generator: 'webui',
    prompt: 'editor prompt text',
    negative_prompt: 'editor negative text',
    checkpoint: 'coolMix.safetensors',
    width: 640,
    height: 960,
    loras: ['styleA'],
    metadata: {
      _parsed: {
        generation_params: { seed: 999, steps: 30, cfg_scale: 5.5, sampler: 'DPM++ 2M' },
      },
    },
  })

  await expect(page.locator('#reader-metadata-editor')).not.toHaveAttribute('hidden', /.*/)
  await expect(page.locator('#reader-edit-prompt')).toHaveValue('editor prompt text')
  await expect(page.locator('#reader-edit-negative')).toHaveValue('editor negative text')
  await expect(page.locator('#reader-edit-seed')).toHaveValue('999')
  await expect(page.locator('#reader-edit-model')).toHaveValue('coolMix.safetensors')
  await expect(page.locator('#reader-edit-sampler')).toHaveValue('DPM++ 2M')
  await expect(page.locator('#reader-edit-steps')).toHaveValue('30')
  await expect(page.locator('#reader-edit-cfg')).toHaveValue('5.5')
  await expect(page.locator('#reader-edit-size')).toHaveValue('640x960')
  await expect(page.locator('#reader-edit-loras')).toHaveValue('styleA')
})

// ---------------------------------------------------------------------------
// 11. Save-path helpers — filename suggestion, ext replace, join, same-file check.
// ---------------------------------------------------------------------------

test('the save-as path helpers derive filenames, swap extensions, join OS separators, and compare paths', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const IR = (window as any).ImageReader
    // Set a stable, non-temp source so the suggestion derives from it (not localStorage).
    IR._currentSourcePath = 'C:/imgs/pic.png'
    IR._currentOriginalSourcePath = ''
    IR._currentImage = null
    return {
      suggestPng: IR._getSuggestedOutputFilename('png'),
      suggestJpg: IR._getSuggestedOutputFilename('jpg'),
      replaceExt: IR._replacePathExtension('C:/imgs/pic.png', 'jpg'),
      joinBackslash: IR._joinPath('C:\\imgs', 'pic.png'),
      joinForward: IR._joinPath('C:/imgs', 'pic.png'),
      defaultFormat: IR._getDefaultEditorFormat(),
      sameFileCaseInsensitive: IR._pathsReferToSameFile('C:/imgs/PIC.png', 'c:\\imgs\\pic.png'),
      differentFile: IR._pathsReferToSameFile('C:/imgs/pic.png', 'C:/imgs/other.png'),
    }
  })

  expect(probe.suggestPng).toBe('pic.edited.png')
  expect(probe.suggestJpg).toBe('pic.edited.jpg')
  expect(probe.replaceExt).toBe('C:/imgs/pic.jpg')
  expect(probe.joinBackslash).toBe('C:\\imgs\\pic.png')
  expect(probe.joinForward).toBe('C:/imgs/pic.png')
  // .png source -> png stays the default editor format.
  expect(probe.defaultFormat).toBe('png')
  // Windows drive paths compare case-insensitively across separators.
  expect(probe.sameFileCaseInsensitive).toBe(true)
  expect(probe.differentFile).toBe(false)
})

// ---------------------------------------------------------------------------
// 12. openLibraryImage — the app/handoffs.js seam: fetch file+detail, then render.
// ---------------------------------------------------------------------------

test('openLibraryImage fetches the file + detail, switches to the reader tool, and records the library id', async ({ page }) => {
  const fileRequests: string[] = []
  page.on('request', (req) => {
    if (req.url().includes('/api/image-file/')) fileRequests.push(req.url())
  })

  await page.route('**/api/image-file/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'image/png', body: Buffer.from(TINY_PNG_BASE64, 'base64') })
  })
  await page.route('**/api/images/321', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ image: { id: 321, path: 'C:/library/hero.png' }, tags: ['tag_alpha', 'tag_beta'] }),
    })
  })
  await page.route('**/api/parse-image', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        generator: 'comfyui',
        prompt: 'library hero prompt',
        negative_prompt: '',
        checkpoint: '',
        width: 512,
        height: 512,
        loras: [],
        metadata: { _parsed: { generation_params: {} } },
      }),
    })
  })

  // Invalid ids short-circuit to false WITHOUT any /api/image-file fetch.
  const invalid = await page.evaluate(async () => {
    const IR = (window as any).ImageReader
    return { zero: await IR.openLibraryImage(0), nan: await IR.openLibraryImage('not-a-number') }
  })
  expect(invalid.zero).toBe(false)
  expect(invalid.nan).toBe(false)
  expect(fileRequests).toEqual([])

  // Valid id resolves true and renders the library image in the reader tool.
  const ok = await page.evaluate(async () => (window as any).ImageReader.openLibraryImage(321, 'hero.png'))
  expect(ok).toBe(true)

  await expect(page.locator('#reader-generator')).toHaveText('ComfyUI', { timeout: 10000 })
  await expect(page.locator('#reader-prompt-text')).toContainText('library hero prompt')
  await expect(page.locator('#reader-tool-panel-reader')).toHaveClass(/active/)

  const state = await page.evaluate(() => {
    const IR = (window as any).ImageReader
    return { libraryId: IR._currentLibraryImageId, tags: IR._currentReaderTags }
  })
  expect(state.libraryId).toBe(321)
  expect(state.tags).toEqual(['tag_alpha', 'tag_beta'])
  expect(fileRequests.length).toBe(1)
})

// ---------------------------------------------------------------------------
// 13. _clear — resets state + returns the tab to its empty layout.
// ---------------------------------------------------------------------------

test('the clear button resets reader state and restores the empty drop-zone layout', async ({ page }) => {
  await loadMockParse(page, {
    generator: 'webui',
    prompt: 'to be cleared',
    negative_prompt: '',
    checkpoint: 'coolMix.safetensors',
    width: 512,
    height: 512,
    loras: [],
    metadata: { _parsed: { generation_params: { seed: 1 } } },
  })
  // Sanity: something was loaded.
  await expect(page.locator('.reader-container')).toHaveClass(/reader-has-image/)

  await page.locator('#reader-clear').click()

  const state = await page.evaluate(() => {
    const IR = (window as any).ImageReader
    return {
      result: IR._currentResult,
      sourcePath: IR._currentSourcePath,
      libraryId: IR._currentLibraryImageId,
      sourceKind: IR._currentSourceKind,
    }
  })
  expect(state.result).toBeNull()
  expect(state.sourcePath).toBe('')
  expect(state.libraryId).toBeNull()
  expect(state.sourceKind).toBe('file')

  // Back to the empty layout: result panel hidden, drop zone shown, flag removed.
  await expect(page.locator('#reader-result-panel')).toBeHidden()
  await expect(page.locator('#reader-drop-zone')).toBeVisible()
  await expect(page.locator('.reader-container')).not.toHaveClass(/reader-has-image/)
  await expect(page.locator('#reader-edit-prompt')).toHaveValue('')
})
