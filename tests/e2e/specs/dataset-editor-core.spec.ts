import { expect, test, type Page } from '../fixtures/click-ledger'

/**
 * FE-1 2b pin spec: the Dataset Maker editor-core side-effect chain.
 *
 * `_setActive`, `_buildQueueItem` and `_buildExportPayload` are (pre-refactor)
 * extended across dataset-maker-part2 / part3 / local-import /
 * confidence-pills / caption-split / separation-console via monkey-patch
 * chains. This spec pins the OBSERVABLE behavior of those chains so the
 * patch-chain dissolution (hooks/decorator registries) is provably
 * zero-behavior-change. It must pass BEFORE and AFTER the refactor.
 *
 * Notable edge cases pinned on purpose:
 *   - pending caption edits flush synchronously when switching gallery
 *     images, but NOT when re-activating the same image and NOT when
 *     switching to a local-source (negative-id) item;
 *   - the split-view refresh and caption-diff update are gallery-only
 *     side effects — the local-import branch never ran them;
 *   - the Separation Console seen-marking + token counter only attach
 *     after the console is first opened (lazy hook).
 *
 * All backend routes the chain touches are stubbed; DM state is seeded
 * in-page (pattern from sepcon-rethreshold.spec.ts).
 */

test.describe.configure({ mode: 'serial' })

const LOCAL_ID = -424242
const LOCAL_PATH = 'C:/fake/local/img_001.png'
const MANIFEST_LOCAL_ID = -424243
const MANIFEST_PATH = 'C:/fake/manifested/img_002.png'

async function seedDatasetQueue(page: Page) {
  await page.route('**/api/image-thumbnail/**', async (route) => {
    await route.fulfill({ status: 204 })
  })
  await page.route('**/api/dataset/local-thumbnail**', async (route) => {
    await route.fulfill({ status: 204 })
  })
  await page.goto('/')
  await page.waitForLoadState('networkidle')
  // _captionTypeFor is defined by dataset-maker-caption-split.js — the LAST
  // script in the ordered DM module chain — so this waits for every DM
  // patch/hook layer to be installed, not just part2.
  await page.waitForFunction(() => typeof (window as any).DatasetMaker?._captionTypeFor === 'function')
  await page.evaluate(() => (window as any).App.switchView('dataset'))
  await page.waitForFunction(() => {
    const dm = (window as any).DatasetMaker
    return dm?._trainerContractState?.status === 'ready' && dm?._pendingProjectSettings === null
  })
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm.imageIds = [701, 702, 703, 704]
    dm.meta.set(701, { filename: 'core-a.png', width: 1024, height: 1024 })
    dm.meta.set(702, { filename: 'core-b.png', width: 1024, height: 1024 })
    dm.meta.set(703, { filename: 'core-c.png', width: 1024, height: 1024 })
    dm.meta.set(704, { filename: 'core-d.png', width: 1024, height: 1024 })
    dm.captions.set(701, '1girl, smile')
    dm.captions.set(702, '1girl, frown')
    dm.captions.set(703, '1girl, frown')
    // 703 carries a manual edit (adds "hat") so diff/status pins have data.
    dm.captionEdits.set(703, '1girl, frown, hat')
    // 704 stays untagged (no caption at all).
    dm._renderQueue()
    dm._setActive(701)
  })
}

async function seedLocalItem(page: Page) {
  await page.evaluate(({ localId, localPath }) => {
    const dm = (window as any).DatasetMaker
    dm.imageIds.push(localId)
    dm.localItemPaths.set(localId, localPath)
    dm.localItemDsIds.set(localId, 'ds:feedbeef00000')
    dm.meta.set(localId, {
      source: 'local',
      abs_path: localPath,
      filename: 'img_001.png',
      width: 512,
      height: 512,
    })
    dm.captions.set(localId, 'local caption, tree')
  }, { localId: LOCAL_ID, localPath: LOCAL_PATH })
}

async function stubTriggerCaptionPreview(page: Page, filenamePrefix: string) {
  await page.route('**/api/tags/export-preview', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    const trigger = String(body.trigger || '').trim()
    const imageIds = Array.isArray(body.image_ids) ? body.image_ids.map(Number) : []
    await route.fulfill({
      json: {
        results: imageIds.map((imageId) => ({
          image_id: imageId,
          filename: `${filenamePrefix}-${imageId}.png`,
          rendered: `${trigger}, caption-${imageId}`,
        })),
      },
    })
  })
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('sd-image-sorter-lang', 'en')
  })
})

test('_setActive fills the editor, highlights the queue item, and updates the diff', async ({ page }) => {
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()

  await expect(page.locator('#dataset-editor-textarea')).toHaveValue('1girl, smile')
  await expect(page.locator('#dataset-editor-filename')).toHaveText('core-a.png')
  // Base _setActive injects the full-res button bar exactly once.
  await expect(page.locator('#dataset-editor-image-wrap .dataset-fullres-bar .dataset-fullres-btn')).toHaveCount(2)
  // 701 has no manual edit — the diff indicator stays hidden.
  await expect(page.locator('#dataset-caption-diff')).toBeHidden()
  await expect(page.locator('#dataset-queue-list .dataset-queue-item.active')).toHaveAttribute('data-image-id', '701')

  // Switching to the edited image updates textarea, filename, diff, highlight.
  await page.evaluate(() => (window as any).DatasetMaker._setActive(703))
  await expect(page.locator('#dataset-editor-textarea')).toHaveValue('1girl, frown, hat')
  await expect(page.locator('#dataset-editor-filename')).toHaveText('core-c.png')
  await expect(page.locator('#dataset-caption-diff')).toContainText('+1 tag')
  await expect(page.locator('#dataset-queue-list .dataset-queue-item.active')).toHaveAttribute('data-image-id', '703')
})

test('_setActive flushes a pending caption edit when switching, not when re-activating', async ({ page }) => {
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()

  const result = await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const ta = document.getElementById('dataset-editor-textarea') as HTMLTextAreaElement
    // Simulate typing on 701; the 200ms debounce has NOT fired yet.
    ta.value = '1girl, smile, hat'
    ta.dispatchEvent(new Event('input', { bubbles: true }))
    // Switching images must flush the pending edit synchronously.
    dm._setActive(702)
    const flushedOnSwitch = dm.captionEdits.get(701)
    const taAfterSwitch = ta.value

    // Re-activating the SAME image does not flush.
    ta.value = '1girl, frown, x'
    ta.dispatchEvent(new Event('input', { bubbles: true }))
    dm._setActive(702)
    const flushedOnSameId = dm.captionEdits.has(702)
    return { flushedOnSwitch, taAfterSwitch, flushedOnSameId, activeId: dm.activeId }
  })

  expect(result.flushedOnSwitch).toBe('1girl, smile, hat')
  expect(result.taAfterSwitch).toBe('1girl, frown')
  expect(result.flushedOnSameId).toBe(false)
  expect(result.activeId).toBe(702)
})

test('split view refreshes on gallery image change, never for local items', async ({ page }) => {
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()

  await page.locator('#btn-dataset-split-view').click()
  const cards = page.locator('#dataset-split-panel .dataset-split-card')
  await expect(cards).toHaveCount(2)
  await expect(cards.nth(0)).toHaveAttribute('data-image-id', '701')
  await expect(cards.nth(1)).toHaveAttribute('data-image-id', '702')

  await page.evaluate(() => (window as any).DatasetMaker._setActive(702))
  await expect(cards.nth(0)).toHaveAttribute('data-image-id', '702')
  await expect(cards.nth(1)).toHaveAttribute('data-image-id', '703')

  // Gallery-only side effect: switching to a local-source item leaves the
  // split panel untouched (the local branch never re-rendered it).
  await seedLocalItem(page)
  await page.evaluate((id) => (window as any).DatasetMaker._setActive(id), LOCAL_ID)
  await expect(cards.nth(0)).toHaveAttribute('data-image-id', '702')
  await expect(cards.nth(1)).toHaveAttribute('data-image-id', '703')
})

test('Separation Console hooks lazily: seen-marking + token counter after open', async ({ page }) => {
  await page.route('**/api/tags/scores/stats', async (route) => {
    await route.fulfill({
      json: { enabled: true, floor: 0.15, total_rows: 0, images_with_scores: 0, models: [], estimated_bytes: 0 },
    })
  })
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()

  // Before the console opens, _setActive marks nothing as seen.
  await page.evaluate(() => (window as any).DatasetMaker._setActive(703))
  const seenBefore = await page.evaluate(() => localStorage.getItem('sd-image-sorter-dataset-seen'))
  expect(seenBefore).toBeNull()

  await page.locator('#dataset-separation-console summary').click()
  await expect(page.locator('#sepcon-rows')).toBeVisible()

  await page.evaluate(() => (window as any).DatasetMaker._setActive(702))
  const seen = await page.evaluate(() => JSON.parse(localStorage.getItem('sd-image-sorter-dataset-seen') || '{}'))
  expect(seen['702']).toBe(true)
  // The token counter is created next to the booru box and reflects 702's
  // caption: "1girl, frown" = 2 tags, ceil(5/4)+ceil(5/4)+1 comma = 5 tokens.
  await expect(page.locator('#dataset-token-counter')).toHaveText('2 tags · ≈5 tokens')
})

test('confidence pills refresh on _setActive; hidden again for local items', async ({ page }) => {
  await page.route('**/api/images/701', async (route) => {
    await route.fulfill({
      json: {
        tags: [
          { tag: '1girl', confidence: 0.95, category: 'general' },
          { tag: 'smile', confidence: 0.42, category: 'general' },
        ],
      },
    })
  })
  await seedDatasetQueue(page)

  const pills = page.locator('#dataset-confidence-pills .dataset-confidence-pill')
  await expect(pills).toHaveCount(2)
  await expect(pills.nth(0)).toHaveClass(/conf-high/)
  await expect(pills.nth(1)).toHaveClass(/conf-low/)
  expect(await page.evaluate(() => (document.getElementById('dataset-confidence-panel') as HTMLElement).hidden)).toBe(false)

  // Local items have no DB confidence — the panel hides again.
  await seedLocalItem(page)
  await page.evaluate((id) => (window as any).DatasetMaker._setActive(id), LOCAL_ID)
  await expect.poll(() => page.evaluate(() => (document.getElementById('dataset-confidence-panel') as HTMLElement).hidden)).toBe(true)
})

test('caption-split: NL box + per-image type follow the active image', async ({ page }) => {
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()

  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm.nlCaptions.set(702, 'a girl frowning at the camera')
    dm._setActive(702)
  })
  await expect(page.locator('#dataset-editor-nl')).toBeVisible()
  await expect(page.locator('#dataset-editor-nl')).toHaveValue('a girl frowning at the camera')
  await expect(page.locator('#dataset-caption-type')).toBeVisible()
  // Auto type: an image WITH an NL sentence defaults to "both".
  await expect(page.locator('#dataset-caption-type .dataset-caption-type-btn.is-active')).toHaveAttribute('data-caption-type', 'both')
  await expect(page.locator('#dataset-editor-textarea')).toBeVisible()

  // An image without NL defaults back to booru-only: the NL box hides.
  await page.evaluate(() => (window as any).DatasetMaker._setActive(701))
  await expect(page.locator('#dataset-editor-nl')).toBeHidden()
  await expect(page.locator('#dataset-caption-type .dataset-caption-type-btn.is-active')).toHaveAttribute('data-caption-type', 'booru')
})

test('trigger quickfill can be saved before an image is queued', async ({ page }) => {
  const previewBodies: Array<Record<string, unknown>> = []
  await page.route('**/api/image-thumbnail/**', async (route) => {
    await route.fulfill({ status: 204 })
  })
  await page.route('**/api/images/701', async (route) => {
    await route.fulfill({ json: { tags: [] } })
  })
  await page.route('**/api/tags/export-preview', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    previewBodies.push(body)
    const imageIds = Array.isArray(body.image_ids) ? body.image_ids.map(Number) : []
    const trigger = String(body.trigger || '').trim()
    await route.fulfill({
      json: {
        results: imageIds.map((imageId) => ({
          image_id: imageId,
          filename: `trigger-first-${imageId}.png`,
          rendered: trigger ? `${trigger}, 1girl, smile` : '1girl, smile',
        })),
      },
    })
  })
  await page.goto('/')
  await page.waitForLoadState('networkidle')
  await page.waitForFunction(() => typeof (window as any).DatasetMaker?._captionTypeFor === 'function')
  await page.evaluate(() => (window as any).App.switchView('dataset'))
  await page.waitForFunction(() => {
    const dm = (window as any).DatasetMaker
    return dm?._trainerContractState?.status === 'ready' && dm?._pendingProjectSettings === null
  })
  await page.locator('#dataset-tab-workbench').click()

  await page.locator('#dataset-trigger').fill('  Hero_Token  ')
  await expect(page.locator('#btn-dataset-quickfill-trigger')).toBeEnabled()
  await page.locator('#btn-dataset-quickfill-trigger').click()

  await expect(page.locator('#dataset-trigger')).toHaveValue('Hero_Token')
  await expect(page.locator('#dataset-common-tags')).toHaveValue('Hero_Token')
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      imageIds: dm.imageIds,
      managedTrigger: dm._quickfilledTrigger,
      captionEdits: Array.from(dm.captionEdits.entries()),
    }
  })).toEqual({
    imageIds: [],
    managedTrigger: 'Hero_Token',
    captionEdits: [],
  })

  await expect.poll(() => page.evaluate(() => {
    const saved = JSON.parse(localStorage.getItem('sd-image-sorter-dataset-session') || 'null')
    return {
      trigger: saved?.settings?.caption_render?.trigger,
      commonTags: saved?.settings?.caption_render?.common_tags,
      managedTrigger: saved?.quickfilledTrigger,
    }
  })).toEqual({
    trigger: 'Hero_Token',
    commonTags: ['Hero_Token'],
    managedTrigger: 'Hero_Token',
  })

  await page.reload()
  await page.waitForFunction(() => typeof (window as any).DatasetMaker?._captionTypeFor === 'function')
  await page.evaluate(() => (window as any).App.switchView('dataset'))
  await page.waitForFunction(() => {
    const dm = (window as any).DatasetMaker
    return dm?._pendingProjectSettings === null && dm?._quickfilledTrigger === 'Hero_Token'
  })
  await page.locator('#dataset-tab-workbench').click()
  await expect(page.locator('#dataset-trigger')).toHaveValue('Hero_Token')
  await expect(page.locator('#dataset-common-tags')).toHaveValue('Hero_Token')

  await page.evaluate(async () => {
    await (window as any).DatasetMaker.addImageIds(
      [701],
      { showToast: false, switchView: false },
    )
  })

  await expect(page.locator('#dataset-editor-textarea')).toHaveValue('Hero_Token, 1girl, smile')
  await expect.poll(() => previewBodies.some((body) => (
    Array.isArray(body.image_ids)
    && body.image_ids.map(Number).includes(701)
    && body.trigger === 'Hero_Token'
    && Array.isArray(body.append)
    && body.append.includes('Hero_Token')
  ))).toBe(true)
})

test('clearing the queue preserves managed trigger ownership for the next replacement', async ({ page }) => {
  await stubTriggerCaptionPreview(page, 'clear-replace')
  await page.route('**/api/images/705', async (route) => {
    await route.fulfill({ json: { tags: [] } })
  })
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()

  await page.locator('#dataset-trigger').fill('Old_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect.poll(() => page.evaluate(() => (
    (window as any).DatasetMaker.captionEdits.get(701)
  ))).toBe('Old_Token, caption-701')

  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const app = (window as any).App
    const originalShowConfirm = app.showConfirm
    app.showConfirm = (_title: string, _message: string, onConfirm: () => void) => onConfirm()
    try {
      dm._clearAll()
    } finally {
      app.showConfirm = originalShowConfirm
    }
  })
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      imageIds: dm.imageIds,
      managedTrigger: dm._quickfilledTrigger,
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
    }
  })).toEqual({
    imageIds: [],
    managedTrigger: 'Old_Token',
    commonTags: 'Old_Token',
  })

  await page.locator('#dataset-trigger').fill('New_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect(page.locator('#dataset-common-tags')).toHaveValue('New_Token')
  await page.evaluate(async () => {
    await (window as any).DatasetMaker.addImageIds(
      [705],
      { showToast: false, switchView: false },
    )
  })

  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      managedTrigger: dm._quickfilledTrigger,
      effectiveCaption: dm._booruTextFor(705),
    }
  })).toEqual({
    managedTrigger: 'New_Token',
    effectiveCaption: 'New_Token, caption-705',
  })
})

test('reloaded empty-queue trigger applies to local late import without changing its baseline', async ({ page }) => {
  const exportPreviewBodies: Array<Record<string, unknown>> = []
  await page.route('**/api/image-thumbnail/**', async (route) => {
    await route.fulfill({ status: 204 })
  })
  await page.route('**/api/dataset/local-thumbnail**', async (route) => {
    await route.fulfill({ status: 204 })
  })
  await page.route('**/api/dataset/export-preview', async (route) => {
    exportPreviewBodies.push(route.request().postDataJSON() as Record<string, unknown>)
    await route.fulfill({
      json: {
        total: 1,
        returned: 1,
        items: [{
          index: 1,
          image_id: null,
          abs_path: 'C:/dataset/local-late.png',
          filename: 'local-late.png',
          output_image_name: 'local-late.png',
          output_caption_name: 'local-late.txt',
          caption: 'Hero_Token, 1girl, smile',
        }],
      },
    })
  })
  await page.goto('/')
  await page.waitForLoadState('networkidle')
  await page.waitForFunction(() => typeof (window as any).DatasetMaker?._captionTypeFor === 'function')
  await page.evaluate(() => (window as any).App.switchView('dataset'))
  await page.waitForFunction(() => {
    const dm = (window as any).DatasetMaker
    return dm?._trainerContractState?.status === 'ready' && dm?._pendingProjectSettings === null
  })
  await page.locator('#dataset-tab-workbench').click()

  await page.locator('#dataset-trigger').fill('Hero_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect.poll(() => page.evaluate(() => {
    const saved = JSON.parse(localStorage.getItem('sd-image-sorter-dataset-session') || 'null')
    return saved?.quickfilledTrigger
  })).toBe('Hero_Token')

  await page.reload()
  await page.waitForFunction(() => typeof (window as any).DatasetMaker?._captionTypeFor === 'function')
  await page.evaluate(() => (window as any).App.switchView('dataset'))
  await page.waitForFunction(() => {
    const dm = (window as any).DatasetMaker
    return dm?._pendingProjectSettings === null && dm?._quickfilledTrigger === 'Hero_Token'
  })
  await page.locator('#dataset-tab-workbench').click()
  exportPreviewBodies.length = 0

  await page.evaluate(() => {
    ;(window as any).DatasetMaker.addLocalItems([{
      ds_id: 'ds:0123456789abcdef',
      abs_path: 'C:/dataset/local-late.png',
      filename: 'local-late.png',
      width: 512,
      height: 512,
      mtime: 1,
      size: 1024,
      thumb_b64: '',
      source_kind: 'folder_path',
      sidecar_capability: 'beside_image',
      sidecar_caption: '1girl, smile',
    }], { showToast: false, switchView: false, focusImportTab: false })
  })

  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const localId = dm.imageIds[0]
    const payload = dm._buildExportPayload()
    return {
      baseline: dm.captions.get(localId),
      effectiveCaption: dm._booruTextFor(localId),
      captionEdit: dm.captionEdits.get(localId),
      exportCaption: payload.image_overrides['C:/dataset/local-late.png'],
    }
  })).toEqual({
    baseline: '1girl, smile',
    effectiveCaption: 'Hero_Token, 1girl, smile',
    captionEdit: 'Hero_Token, 1girl, smile',
    exportCaption: 'Hero_Token, 1girl, smile',
  })
  await expect.poll(() => exportPreviewBodies.some((body) => (
    (body.image_overrides as Record<string, string> | undefined)?.['C:/dataset/local-late.png']
      === 'Hero_Token, 1girl, smile'
  ))).toBe(true)
})

test('failed managed-trigger late import leaves no phantom item and retries cleanly', async ({ page }) => {
  await page.route('**/api/dataset/local-thumbnail**', async (route) => {
    await route.fulfill({ status: 204 })
  })
  await page.goto('/')
  await page.waitForLoadState('networkidle')
  await page.waitForFunction(() => typeof (window as any).DatasetMaker?._captionTypeFor === 'function')
  await page.evaluate(() => (window as any).App.switchView('dataset'))
  await page.waitForFunction(() => {
    const dm = (window as any).DatasetMaker
    return dm?._trainerContractState?.status === 'ready' && dm?._pendingProjectSettings === null
  })
  await page.locator('#dataset-tab-workbench').click()
  await page.locator('#dataset-trigger').fill('Late_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(1)

  const localItem = {
    ds_id: 'ds:7123456789abcdef',
    abs_path: 'C:/dataset/atomic-late-import.png',
    filename: 'atomic-late-import.png',
    width: 512,
    height: 512,
    mtime: 1,
    size: 1024,
    thumb_b64: '',
    source_kind: 'folder_path',
    sidecar_capability: 'beside_image',
    sidecar_caption: '1girl, smile',
  }
  const failed = await page.evaluate((item) => {
    const triggerKey = 'sd-image-sorter-dataset-local-caption-triggers'
    const originalSetItem = Storage.prototype.setItem
    Storage.prototype.setItem = function (key: string, value: string) {
      if (key === triggerKey) throw new DOMException('quota exhausted', 'QuotaExceededError')
      return originalSetItem.call(this, key, value)
    }
    const dm = (window as any).DatasetMaker
    let error = ''
    try {
      dm.addLocalItems([item], { showToast: false, switchView: false, focusImportTab: false })
    } catch (caught) {
      error = caught instanceof Error ? caught.message : String(caught)
    } finally {
      Storage.prototype.setItem = originalSetItem
    }
    const numericId = dm._dsIdToNumericId(item.ds_id)
    return {
      error,
      imageIds: [...dm.imageIds],
      pathPresent: dm.localItemPaths.has(numericId),
      dsIdPresent: dm.localItemDsIds.has(numericId),
      metaPresent: dm.meta.has(numericId),
      captionPresent: dm.captions.has(numericId),
      captionEditPresent: dm.captionEdits.has(numericId),
      nlCaptionPresent: dm.nlCaptions.has(numericId),
      nlEditPresent: dm.nlEdits.has(numericId),
      captionTypePresent: dm.captionType.has(numericId),
      undoPresent: dm._undoStacks.has(numericId),
      selected: dm._queueSelection.has(numericId),
      activeId: dm.activeId,
      lastClickedId: dm._lastClickedId,
      triggerOwnerPresent: Object.prototype.hasOwnProperty.call(
        JSON.parse(localStorage.getItem(triggerKey) || '{}'),
        item.abs_path,
      ),
    }
  }, localItem)
  expect(failed).toEqual({
    error: expect.stringContaining('quota exhausted'),
    imageIds: [],
    pathPresent: false,
    dsIdPresent: false,
    metaPresent: false,
    captionPresent: false,
    captionEditPresent: false,
    nlCaptionPresent: false,
    nlEditPresent: false,
    captionTypePresent: false,
    undoPresent: false,
    selected: false,
    activeId: null,
    lastClickedId: null,
    triggerOwnerPresent: false,
  })

  expect(await page.evaluate((item) => {
    const dm = (window as any).DatasetMaker
    const added = dm.addLocalItems(
      [item],
      { showToast: false, switchView: false, focusImportTab: false },
    )
    const localId = dm.imageIds[0]
    return {
      added,
      queueSize: dm.imageIds.length,
      effectiveCaption: dm._booruTextFor(localId),
      savedTrigger: JSON.parse(
        localStorage.getItem('sd-image-sorter-dataset-local-caption-triggers') || '{}',
      )[item.abs_path],
    }
  }, localItem)).toEqual({
    added: 1,
    queueSize: 1,
    effectiveCaption: 'Late_Token, 1girl, smile',
    savedTrigger: 'Late_Token',
  })
})

test('clear then trigger replacement removes the old managed trigger from a reimported local path', async ({ page }) => {
  await page.route('**/api/image-thumbnail/**', async (route) => {
    await route.fulfill({ status: 204 })
  })
  await page.route('**/api/dataset/local-thumbnail**', async (route) => {
    await route.fulfill({ status: 204 })
  })
  await page.route('**/api/dataset/export-preview', async (route) => {
    await route.fulfill({ json: { total: 0, returned: 0, items: [] } })
  })
  await page.goto('/')
  await page.waitForLoadState('networkidle')
  await page.waitForFunction(() => typeof (window as any).DatasetMaker?._captionTypeFor === 'function')
  await page.evaluate(() => (window as any).App.switchView('dataset'))
  await page.waitForFunction(() => {
    const dm = (window as any).DatasetMaker
    return dm?._trainerContractState?.status === 'ready' && dm?._pendingProjectSettings === null
  })
  await page.locator('#dataset-tab-workbench').click()

  const localItem = {
    ds_id: 'ds:1123456789abcdef',
    abs_path: 'C:/dataset/local-reimport.png',
    filename: 'local-reimport.png',
    width: 512,
    height: 512,
    mtime: 1,
    size: 1024,
    thumb_b64: '',
    source_kind: 'folder_path',
    sidecar_capability: 'beside_image',
    sidecar_caption: '1girl, smile',
  }
  await page.evaluate((item) => {
    ;(window as any).DatasetMaker.addLocalItems(
      [item],
      { showToast: false, switchView: false, focusImportTab: false },
    )
  }, localItem)
  await page.locator('#dataset-trigger').fill('Token_A')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect.poll(() => page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return dm._booruTextFor(dm.imageIds[0])
  })).toBe('Token_A, 1girl, smile')

  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const app = (window as any).App
    const originalShowConfirm = app.showConfirm
    app.showConfirm = (_title: string, _message: string, onConfirm: () => void) => onConfirm()
    try {
      dm._clearAll()
    } finally {
      app.showConfirm = originalShowConfirm
    }
  })
  await page.locator('#dataset-trigger').fill('Token_B')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await page.evaluate((item) => {
    ;(window as any).DatasetMaker.addLocalItems(
      [item],
      { showToast: false, switchView: false, focusImportTab: false },
    )
  }, localItem)

  expect(await page.evaluate((path) => {
    const dm = (window as any).DatasetMaker
    const localId = dm.imageIds[0]
    const saved = JSON.parse(
      localStorage.getItem('sd-image-sorter-dataset-local-captions') || '{}',
    )
    return {
      baseline: dm.captions.get(localId),
      effectiveCaption: dm._booruTextFor(localId),
      captionEdit: dm.captionEdits.get(localId),
      savedCaption: saved[path],
    }
  }, localItem.abs_path)).toEqual({
    baseline: '1girl, smile',
    effectiveCaption: 'Token_B, 1girl, smile',
    captionEdit: 'Token_B, 1girl, smile',
    savedCaption: 'Token_B, 1girl, smile',
  })
})

test('clear cancels when managed trigger ownership cannot be persisted', async ({ page }) => {
  await page.route('**/api/dataset/local-thumbnail**', async (route) => {
    await route.fulfill({ status: 204 })
  })
  await page.goto('/')
  await page.waitForLoadState('networkidle')
  await page.waitForFunction(() => typeof (window as any).DatasetMaker?._captionTypeFor === 'function')
  await page.evaluate(() => (window as any).App.switchView('dataset'))
  await page.waitForFunction(() => {
    const dm = (window as any).DatasetMaker
    return dm?._trainerContractState?.status === 'ready' && dm?._pendingProjectSettings === null
  })
  await page.locator('#dataset-tab-workbench').click()

  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm.addLocalItems([{
      ds_id: 'ds:3123456789abcdef',
      abs_path: 'C:/dataset/local-storage-failure.png',
      filename: 'local-storage-failure.png',
      width: 512,
      height: 512,
      mtime: 1,
      size: 1024,
      thumb_b64: '',
      source_kind: 'folder_path',
      sidecar_capability: 'beside_image',
      sidecar_caption: '1girl, smile',
    }], { showToast: false, switchView: false, focusImportTab: false })
  })
  await page.locator('#dataset-trigger').fill('Token_A')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect.poll(() => page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return dm._booruTextFor(dm.imageIds[0])
  })).toBe('Token_A, 1girl, smile')

  await page.evaluate(() => {
    const triggerKey = 'sd-image-sorter-dataset-local-caption-triggers'
    localStorage.removeItem(triggerKey)
    const originalSetItem = Storage.prototype.setItem
    ;(window as any).__restoreDatasetStorage = () => {
      Storage.prototype.setItem = originalSetItem
    }
    Storage.prototype.setItem = function (key: string, value: string) {
      if (key === triggerKey) throw new DOMException('quota exhausted', 'QuotaExceededError')
      return originalSetItem.call(this, key, value)
    }
    const dm = (window as any).DatasetMaker
    const app = (window as any).App
    const originalShowConfirm = app.showConfirm
    app.showConfirm = (_title: string, _message: string, onConfirm: () => void) => onConfirm()
    try {
      dm._clearAll()
    } finally {
      app.showConfirm = originalShowConfirm
    }
  })

  await expect(page.locator('#toast-container .toast.error')).toHaveCount(1)
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const localId = dm.imageIds[0]
    ;(window as any).__restoreDatasetStorage()
    delete (window as any).__restoreDatasetStorage
    return {
      queueSize: dm.imageIds.length,
      managedTrigger: dm._quickfilledTrigger,
      caption: dm._booruTextFor(localId),
    }
  })).toEqual({
    queueSize: 1,
    managedTrigger: 'Token_A',
    caption: 'Token_A, 1girl, smile',
  })
})

test('trigger quickfill rolls back Gallery captions when local owner persistence fails', async ({ page }) => {
  await page.route('**/api/dataset/local-thumbnail**', async (route) => {
    await route.fulfill({ status: 204 })
  })
  await page.route('**/api/tags/export-preview', async (route) => {
    const body = route.request().postDataJSON() as { image_ids?: number[], trigger?: string }
    const trigger = String(body.trigger || '').trim()
    await route.fulfill({
      json: {
        results: (body.image_ids || []).map((imageId) => ({
          image_id: imageId,
          filename: `gallery-${imageId}.png`,
          rendered: trigger ? `${trigger}, gallery baseline` : 'gallery baseline',
          nl_caption: '',
        })),
      },
    })
  })
  await page.goto('/')
  await page.waitForLoadState('networkidle')
  await page.waitForFunction(() => typeof (window as any).DatasetMaker?._captionTypeFor === 'function')
  await page.evaluate(() => (window as any).App.switchView('dataset'))
  await page.waitForFunction(() => {
    const dm = (window as any).DatasetMaker
    return dm?._trainerContractState?.status === 'ready' && dm?._pendingProjectSettings === null
  })
  await page.locator('#dataset-tab-workbench').click()

  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm.imageIds.push(701)
    dm.captions.set(701, 'gallery baseline')
    dm.meta.set(701, { filename: 'gallery-701.png' })
    dm.addLocalItems([{
      ds_id: 'ds:4123456789abcdef',
      abs_path: 'C:/dataset/local-mixed-storage-failure.png',
      filename: 'local-mixed-storage-failure.png',
      width: 512,
      height: 512,
      mtime: 1,
      size: 1024,
      thumb_b64: '',
      source_kind: 'folder_path',
      sidecar_capability: 'beside_image',
      sidecar_caption: '1girl, smile',
    }], { showToast: false, switchView: false, focusImportTab: false })
    const triggerKey = 'sd-image-sorter-dataset-local-caption-triggers'
    const originalSetItem = Storage.prototype.setItem
    ;(window as any).__restoreDatasetStorage = () => {
      Storage.prototype.setItem = originalSetItem
    }
    Storage.prototype.setItem = function (key: string, value: string) {
      if (key === triggerKey) throw new DOMException('quota exhausted', 'QuotaExceededError')
      return originalSetItem.call(this, key, value)
    }
  })

  await page.locator('#dataset-trigger').fill('Token_A')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect(page.locator('#toast-container .toast.error')).toHaveCount(1)

  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const localId = dm.imageIds.find((id: number) => id < 0)
    const commonTags = (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value
    ;(window as any).__restoreDatasetStorage()
    delete (window as any).__restoreDatasetStorage
    return {
      managedTrigger: dm._quickfilledTrigger,
      commonTags,
      galleryCaption: dm.captions.get(701),
      galleryEffectiveCaption: dm._booruTextFor(701),
      localCaptionEditPresent: dm.captionEdits.has(localId),
      localEffectiveCaption: dm._booruTextFor(localId),
    }
  })).toEqual({
    managedTrigger: '',
    commonTags: '',
    galleryCaption: 'gallery baseline',
    galleryEffectiveCaption: 'gallery baseline',
    localCaptionEditPresent: false,
    localEffectiveCaption: '1girl, smile',
  })

  await page.evaluate(() => {
    const captionKey = 'sd-image-sorter-dataset-local-captions'
    const originalSetItem = Storage.prototype.setItem
    let captionWriteCount = 0
    ;(window as any).__datasetCaptionWriteCount = () => captionWriteCount
    ;(window as any).__restoreDatasetStorage = () => {
      Storage.prototype.setItem = originalSetItem
    }
    Storage.prototype.setItem = function (key: string, value: string) {
      if (key === captionKey) {
        captionWriteCount += 1
        if (captionWriteCount === 2) {
          throw new DOMException('unexpected second caption write', 'QuotaExceededError')
        }
      }
      return originalSetItem.call(this, key, value)
    }
  })
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(1)

  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const localId = dm.imageIds.find((id: number) => id < 0)
    const commonTags = (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value
    const captionWriteCount = (window as any).__datasetCaptionWriteCount()
    ;(window as any).__restoreDatasetStorage()
    delete (window as any).__datasetCaptionWriteCount
    delete (window as any).__restoreDatasetStorage
    return {
      managedTrigger: dm._quickfilledTrigger,
      commonTags,
      galleryCaption: dm.captions.get(701),
      localCaptionEdit: dm.captionEdits.get(localId),
      localEffectiveCaption: dm._booruTextFor(localId),
      captionWriteCount,
    }
  })).toEqual({
    managedTrigger: 'Token_A',
    commonTags: 'Token_A',
    galleryCaption: 'Token_A, gallery baseline',
    localCaptionEdit: 'Token_A, 1girl, smile',
    localEffectiveCaption: 'Token_A, 1girl, smile',
    captionWriteCount: 1,
  })
})

test('clear cancels pending Booru and NL caption edits before a deterministic local reimport', async ({ page }) => {
  await page.route('**/api/dataset/local-thumbnail**', async (route) => {
    await route.fulfill({ status: 204 })
  })
  await page.goto('/')
  await page.waitForLoadState('networkidle')
  await page.waitForFunction(() => typeof (window as any).DatasetMaker?._captionTypeFor === 'function')
  await page.evaluate(() => (window as any).App.switchView('dataset'))
  await page.waitForFunction(() => {
    const dm = (window as any).DatasetMaker
    return dm?._trainerContractState?.status === 'ready' && dm?._pendingProjectSettings === null
  })
  await page.locator('#dataset-tab-workbench').click()

  const localItem = {
    ds_id: 'ds:2123456789abcdef',
    abs_path: 'C:/dataset/local-pending.png',
    filename: 'local-pending.png',
    width: 512,
    height: 512,
    mtime: 1,
    size: 1024,
    thumb_b64: '',
    source_kind: 'folder_path',
    sidecar_capability: 'beside_image',
    sidecar_caption: '1girl, smile',
  }
  await page.evaluate((item) => {
    const dm = (window as any).DatasetMaker
    dm.addLocalItems([item], { showToast: false, switchView: false, focusImportTab: false })
    dm.captionType.set(dm.imageIds[0], 'both')
    dm._setActive(dm.imageIds[0])
  }, localItem)
  await page.locator('#dataset-editor-textarea').fill('pending booru edit')
  await page.locator('#dataset-editor-nl').fill('pending natural-language edit')

  await page.evaluate((item) => {
    const dm = (window as any).DatasetMaker
    const app = (window as any).App
    const originalShowConfirm = app.showConfirm
    app.showConfirm = (_title: string, _message: string, onConfirm: () => void) => onConfirm()
    try {
      dm._clearAll()
    } finally {
      app.showConfirm = originalShowConfirm
    }
    dm.addLocalItems([item], { showToast: false, switchView: false, focusImportTab: false })
  }, localItem)
  await page.waitForTimeout(350)

  expect(await page.evaluate((path) => {
    const dm = (window as any).DatasetMaker
    const localId = dm.imageIds[0]
    const saved = JSON.parse(
      localStorage.getItem('sd-image-sorter-dataset-local-captions') || '{}',
    )
    return {
      baseline: dm.captions.get(localId),
      captionEditPresent: dm.captionEdits.has(localId),
      nlEditPresent: dm.nlEdits.has(localId),
      effectiveCaption: dm._booruTextFor(localId),
      savedCaptionPresent: Object.prototype.hasOwnProperty.call(saved, path),
    }
  }, localItem.abs_path)).toEqual({
    baseline: '1girl, smile',
    captionEditPresent: false,
    nlEditPresent: false,
    effectiveCaption: '1girl, smile',
    savedCaptionPresent: false,
  })
})

test('trigger quickfill rejects multi-token input without changing captions', async ({ page }) => {
  let previewCalls = 0
  await page.route('**/api/tags/export-preview', async (route) => {
    previewCalls += 1
    await route.fulfill({ json: { results: [] } })
  })
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()

  await page.locator('#dataset-trigger').fill('Bad,Trigger')
  await page.locator('#btn-dataset-quickfill-trigger').click()

  await expect(page.locator('#toast-container .toast.error')).toContainText('cannot contain commas')
  expect(previewCalls).toBe(0)
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      caption: dm.captions.get(701),
      captionEdit: dm.captionEdits.get(701),
      quickfilledTrigger: dm._quickfilledTrigger,
    }
  })).toEqual({
    commonTags: '',
    caption: '1girl, smile',
    captionEdit: undefined,
    quickfilledTrigger: '',
  })
})

test('trigger quickfill rejects internal whitespace without persisting or previewing it', async ({ page }) => {
  const previewBodies: Array<Record<string, unknown>> = []
  await page.route('**/api/tags/export-preview', async (route) => {
    previewBodies.push(route.request().postDataJSON() as Record<string, unknown>)
    await route.fulfill({ json: { results: [] } })
  })
  await page.route('**/api/dataset/export-preview', async (route) => {
    previewBodies.push(route.request().postDataJSON() as Record<string, unknown>)
    await route.fulfill({ json: { total: 0, returned: 0, items: [] } })
  })
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()

  const invalidTriggers = [
    'Bad Trigger',
    'Bad\tTrigger',
    'Bad\u00a0Trigger',
    'Bad\u3000Trigger',
  ]
  for (const invalidTrigger of invalidTriggers) {
    await page.locator('#dataset-trigger').fill(invalidTrigger)
    await page.locator('#btn-dataset-quickfill-trigger').click()
    await expect(page.locator('#toast-container .toast.error').last()).toContainText('internal whitespace')
    await expect(page.locator('#dataset-trigger')).toHaveValue('')
  }

  expect(previewBodies.some((body) => invalidTriggers.includes(String(body.trigger || '')))).toBe(false)
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const saved = JSON.parse(localStorage.getItem('sd-image-sorter-dataset-session') || 'null')
    const payload = dm._buildExportPayload()
    return {
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      caption: dm.captions.get(701),
      captionEdit: dm.captionEdits.get(701),
      quickfilledTrigger: dm._quickfilledTrigger,
      savedTrigger: saved?.settings?.caption_render?.trigger,
      payloadTrigger: payload.trigger,
    }
  })).toEqual({
    commonTags: '',
    caption: '1girl, smile',
    captionEdit: undefined,
    quickfilledTrigger: '',
    savedTrigger: '',
    payloadTrigger: '',
  })
})

test('trigger input waits for IME composition before saving or previewing', async ({ page }) => {
  const tagPreviewBodies: Array<Record<string, unknown>> = []
  const exportPreviewBodies: Array<Record<string, unknown>> = []
  await page.route('**/api/tags/export-preview', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    tagPreviewBodies.push(body)
    const trigger = String(body.trigger || '')
    const imageIds = Array.isArray(body.image_ids) ? body.image_ids.map(Number) : []
    await route.fulfill({
      json: {
        results: imageIds.map((imageId) => ({
          image_id: imageId,
          filename: `ime-${imageId}.png`,
          rendered: `${trigger}, caption-${imageId}`,
        })),
      },
    })
  })
  await page.route('**/api/dataset/export-preview', async (route) => {
    exportPreviewBodies.push(route.request().postDataJSON() as Record<string, unknown>)
    await route.fulfill({ json: { total: 0, returned: 0, items: [] } })
  })
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.waitForTimeout(500)
  tagPreviewBodies.length = 0
  exportPreviewBodies.length = 0

  await page.evaluate(() => {
    const input = document.getElementById('dataset-trigger') as HTMLInputElement
    input.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }))
    input.value = 'ni'
    input.dispatchEvent(new InputEvent('input', {
      bubbles: true,
      data: 'ni',
      inputType: 'insertCompositionText',
      isComposing: true,
    }))
  })
  await page.waitForTimeout(600)

  expect(tagPreviewBodies).toHaveLength(0)
  expect(exportPreviewBodies).toHaveLength(0)
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const saved = JSON.parse(localStorage.getItem('sd-image-sorter-dataset-session') || 'null')
    return {
      savedTrigger: saved?.settings?.caption_render?.trigger,
      caption: dm.captions.get(701),
      captionEdit: dm.captionEdits.get(701),
    }
  })).toEqual({
    savedTrigger: '',
    caption: '1girl, smile',
    captionEdit: undefined,
  })

  await page.evaluate(() => {
    const input = document.getElementById('dataset-trigger') as HTMLInputElement
    input.value = '你'
    input.dispatchEvent(new CompositionEvent('compositionend', {
      bubbles: true,
      data: '你',
    }))
  })

  await expect.poll(() => page.evaluate(() => {
    const saved = JSON.parse(localStorage.getItem('sd-image-sorter-dataset-session') || 'null')
    return saved?.settings?.caption_render?.trigger
  })).toBe('你')
  await expect.poll(() => ({
    tagTriggers: tagPreviewBodies.map((body) => body.trigger),
    exportTriggers: exportPreviewBodies.map((body) => body.trigger),
  })).toEqual({
    tagTriggers: ['你'],
    exportTriggers: ['你'],
  })
})

test('invalid trigger input preserves another pending Dataset draft edit', async ({ page }) => {
  const previewBodies: Array<Record<string, unknown>> = []
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.route('**/api/tags/export-preview', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    const imageIds = Array.isArray(body.image_ids) ? body.image_ids.map(Number) : []
    previewBodies.push(body)
    await route.fulfill({
      json: {
        results: imageIds.map((imageId) => ({
          image_id: imageId,
          filename: `pending-draft-${imageId}.png`,
          rendered: `persist_me, caption-${imageId}`,
        })),
      },
    })
  })
  await page.route('**/api/dataset/export-preview', async (route) => {
    previewBodies.push(route.request().postDataJSON() as Record<string, unknown>)
    await route.fulfill({ json: { total: 0, returned: 0, items: [] } })
  })

  await page.evaluate(() => {
    const commonTags = document.getElementById('dataset-common-tags') as HTMLTextAreaElement
    const trigger = document.getElementById('dataset-trigger') as HTMLInputElement
    commonTags.value = 'persist_me'
    commonTags.dispatchEvent(new Event('input', { bubbles: true }))
    trigger.value = '___'
    trigger.dispatchEvent(new Event('input', { bubbles: true }))
  })

  await expect(page.locator('#dataset-trigger')).toHaveValue('___')
  await expect.poll(() => page.evaluate(() => {
    const saved = JSON.parse(localStorage.getItem('sd-image-sorter-dataset-session') || 'null')
    return {
      trigger: saved?.settings?.caption_render?.trigger,
      commonTags: saved?.settings?.caption_render?.common_tags,
    }
  })).toEqual({
    trigger: '',
    commonTags: ['persist_me'],
  })
  await expect.poll(() => page.evaluate(() => (
    (window as any).DatasetMaker._datasetFieldTimers?.size || 0
  ))).toBe(0)
  expect(previewBodies).toHaveLength(0)
  expect(previewBodies.every((body) => body.trigger !== '___')).toBe(true)

  await page.evaluate(() => {
    const commonTags = document.getElementById('dataset-common-tags') as HTMLTextAreaElement
    const blacklist = document.getElementById('dataset-blacklist') as HTMLTextAreaElement
    commonTags.value = 'persist_me, second_tag'
    commonTags.dispatchEvent(new Event('input', { bubbles: true }))
    blacklist.value = 'blocked_tag'
    blacklist.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await expect.poll(() => page.evaluate(() => {
    const saved = JSON.parse(localStorage.getItem('sd-image-sorter-dataset-session') || 'null')
    return {
      trigger: saved?.settings?.caption_render?.trigger,
      commonTags: saved?.settings?.caption_render?.common_tags,
      blacklist: saved?.settings?.caption_render?.blacklist,
    }
  })).toEqual({
    trigger: '',
    commonTags: ['persist_me', 'second_tag'],
    blacklist: ['blocked_tag'],
  })
  await expect.poll(() => page.evaluate(() => (
    (window as any).DatasetMaker._datasetFieldTimers?.size || 0
  ))).toBe(0)
  expect(previewBodies).toHaveLength(0)

  await page.reload()
  await page.waitForFunction(() => typeof (window as any).DatasetMaker?._captionTypeFor === 'function')
  await page.evaluate(() => (window as any).App.switchView('dataset'))
  await page.waitForFunction(() => (window as any).DatasetMaker?._pendingProjectSettings === null)
  await page.locator('#dataset-tab-workbench').click()
  await expect(page.locator('#dataset-trigger')).toHaveValue('')
  await expect(page.locator('#dataset-common-tags')).toHaveValue('persist_me, second_tag')
  await expect(page.locator('#dataset-blacklist')).toHaveValue('blocked_tag')
})

test('trigger quickfill rejects a token that normalizes to empty', async ({ page }) => {
  let previewCalls = 0
  await page.route('**/api/tags/export-preview', async (route) => {
    previewCalls += 1
    await route.fulfill({ json: { results: [] } })
  })
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    for (const imageId of dm.imageIds) {
      dm.captionEdits.set(imageId, dm.captions.get(imageId) || '')
    }
  })
  previewCalls = 0

  await page.locator('#dataset-trigger').fill('___')
  await page.locator('#btn-dataset-quickfill-trigger').click()

  await expect(page.locator('#toast-container .toast.error')).toContainText('other than spaces or underscores')
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(0)
  expect(previewCalls).toBe(0)
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      managedTrigger: dm._quickfilledTrigger,
      captions: dm.imageIds.map((imageId: number) => dm.captionEdits.get(imageId)),
    }
  })).toEqual({
    commonTags: '',
    managedTrigger: '',
    captions: ['1girl, smile', '1girl, frown', '1girl, frown', ''],
  })

  await page.evaluate(() => (window as any).DatasetMaker._saveSession())
  expect(await page.evaluate(() => {
    const saved = JSON.parse(localStorage.getItem('sd-image-sorter-dataset-session') || 'null')
    return saved?.settings?.caption_render?.trigger
  })).toBe('')

  await page.reload()
  await page.waitForFunction(() => typeof (window as any).DatasetMaker?._captionTypeFor === 'function')
  await page.evaluate(() => (window as any).App.switchView('dataset'))
  await page.waitForFunction(() => (window as any).DatasetMaker?._pendingProjectSettings === null)
  await page.locator('#dataset-tab-workbench').click()
  await expect(page.locator('#dataset-trigger')).toHaveValue('')
})

test('Dataset Project trigger parsing matches the shared single-token contract', async ({ page }) => {
  await seedDatasetQueue(page)

  const errors = await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return [
      'x'.repeat(101),
      '___',
      'Bad,Trigger',
      'Bad\nTrigger',
      'Bad Trigger',
      'Bad\tTrigger',
      'Bad\u00a0Trigger',
      'Bad\u3000Trigger',
      'Bad\u0085Trigger',
      'Bad\ufeffTrigger',
    ].map((trigger) => {
      const settings = structuredClone(dm._defaultProjectSettings())
      settings.caption_render.trigger = trigger
      try {
        dm._parseProjectSettings(settings)
        return ''
      } catch (parseError) {
        return parseError instanceof Error ? parseError.message : String(parseError)
      }
    })
  })

  expect(errors[0]).toContain('at most 100 characters')
  expect(errors[1]).toContain('other than spaces or underscores')
  expect(errors[2]).toContain('cannot contain commas or line breaks')
  expect(errors[3]).toContain('cannot contain commas or line breaks')
  for (const error of errors.slice(4)) expect(error).toContain('internal whitespace')
})

test('Dataset trigger canonicalizes Unicode edge whitespace in project and export payloads', async ({ page }) => {
  await seedDatasetQueue(page)

  for (const rawTrigger of ['\u0085Hero_Token\u0085', '\ufeffHero_Token\ufeff']) {
    expect(await page.evaluate((raw) => {
      const dm = (window as any).DatasetMaker
      const settings = structuredClone(dm._defaultProjectSettings())
      settings.caption_render.trigger = raw
      ;(document.getElementById('dataset-trigger') as HTMLInputElement).value = raw
      const payload = dm._buildExportPayload()
      return {
        projectTrigger: dm._parseProjectSettings(settings).caption_render.trigger,
        payloadTrigger: payload.trigger,
        nestedTrigger: payload.template_options.trigger,
      }
    }, rawTrigger)).toEqual({
      projectTrigger: 'Hero_Token',
      payloadTrigger: 'Hero_Token',
      nestedTrigger: 'Hero_Token',
    })
  }
})

test('trigger quickfill freezes dynamic captions as durable export drafts', async ({ page }) => {
  const previewBodies: Array<Record<string, unknown>> = []
  await page.route('**/api/tags/export-preview', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    previewBodies.push(body)
    const trigger = String(body.trigger || '').trim()
    const blacklist = new Set((body.blacklist as string[] | undefined) || [])
    const append = (body.append as string[] | undefined) || []
    const sourceTags: Record<number, string[]> = {
      701: ['1girl', 'smile'],
      702: ['1girl', 'frown'],
      703: ['1girl', 'frown', 'hat'],
      704: [],
    }
    const ids = Array.isArray(body.image_ids) ? body.image_ids.map(Number) : []
    await route.fulfill({
      json: {
        results: ids.map((imageId) => {
          const parts = [trigger, ...(sourceTags[imageId] || []).filter((tag) => !blacklist.has(tag)), ...append]
          const seen = new Set<string>()
          const rendered = parts.filter((part) => {
            const key = part.replace(/[\s_]+/g, ' ').trim().toLowerCase()
            if (!key || seen.has(key)) return false
            seen.add(key)
            return true
          }).join(', ')
          return { image_id: imageId, filename: `core-${imageId}.png`, rendered }
        }),
      },
    })
  })
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()

  await page.locator('#dataset-trigger').fill('Hero_Token')
  await expect.poll(() => previewBodies.at(-1)?.trigger).toBe('Hero_Token')
  await expect.poll(() => page.evaluate(() => (window as any).DatasetMaker.captions.get(701))).toBe('Hero_Token, 1girl, smile')

  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm.captionEdits.set(701, '1girl, Hero Token, smile')
    const originalMarkReadinessStale = dm._markReadinessStale
    dm.__triggerReadinessCalls = 0
    dm._markReadinessStale = function () {
      dm.__triggerReadinessCalls += 1
      return originalMarkReadinessStale?.call(this)
    }
    dm._setActive(701)
  })
  await page.locator('#btn-dataset-quickfill-trigger').click()

  await expect.poll(() => page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const payload = dm._buildExportPayload()
    return {
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      override: dm.captionEdits.get(701),
      generatedOverride: dm.captionEdits.get(702),
      exportOverride: payload.image_overrides['701'],
      generatedExportOverride: payload.image_overrides['702'],
    }
  })).toEqual({
    commonTags: 'Hero_Token',
    override: '1girl, Hero_Token, smile',
    generatedOverride: 'Hero_Token, 1girl, frown',
    exportOverride: '1girl, Hero_Token, smile',
    generatedExportOverride: 'Hero_Token, 1girl, frown',
  })
  await expect.poll(() => page.evaluate(() => (window as any).DatasetMaker.__triggerReadinessCalls)).toBeGreaterThan(0)

  await page.locator('#dataset-step-cleanup > summary').click()
  await page.locator('#dataset-blacklist').fill('frown')
  await expect.poll(() => page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      generated: dm._booruTextFor(702),
      generatedOverride: dm.captionEdits.get(702),
      generatedExportOverride: dm._buildExportPayload().image_overrides['702'],
      manual: dm._booruTextFor(701),
    }
  })).toEqual({
    generated: 'Hero_Token, 1girl, frown',
    generatedOverride: 'Hero_Token, 1girl, frown',
    generatedExportOverride: 'Hero_Token, 1girl, frown',
    manual: '1girl, Hero_Token, smile',
  })

  await page.evaluate(() => (window as any).DatasetMaker._saveSession())
  await page.reload()
  await page.waitForFunction(() => typeof (window as any).DatasetMaker?._captionTypeFor === 'function')
  await page.evaluate(() => (window as any).App.switchView('dataset'))
  await page.waitForFunction(() => (window as any).DatasetMaker?._pendingProjectSettings === null)
  await expect.poll(() => page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      generated: dm._booruTextFor(702),
      generatedOverride: dm.captionEdits.get(702),
      generatedExportOverride: dm._buildExportPayload().image_overrides['702'],
    }
  })).toEqual({
    generated: 'Hero_Token, 1girl, frown',
    generatedOverride: 'Hero_Token, 1girl, frown',
    generatedExportOverride: 'Hero_Token, 1girl, frown',
  })
})

test('trigger replacement rewrites a pending equivalent token to exact spelling', async ({ page }) => {
  await stubTriggerCaptionPreview(page, 'trigger-exact-replacement')
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()

  await page.locator('#dataset-trigger').fill('Bug_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(1)

  await page.locator('#dataset-editor-textarea').fill('1girl, Hero Token, smile')
  await page.locator('#dataset-trigger').fill('Hero_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()

  await expect(page.locator('#dataset-editor-textarea')).toHaveValue(
    '1girl, Hero_Token, smile',
  )
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const payload = dm._buildExportPayload()
    return {
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      override: dm.captionEdits.get(701),
      exportOverride: payload.image_overrides['701'],
    }
  })).toEqual({
    commonTags: 'Hero_Token',
    override: '1girl, Hero_Token, smile',
    exportOverride: '1girl, Hero_Token, smile',
  })
})

test('trigger quickfill updates the visible NL-only caption and export draft', async ({ page }) => {
  await stubTriggerCaptionPreview(page, 'nl-only')
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm.nlCaptions.set(701, 'A person looks at the camera.')
    dm.captionType.set(701, 'nl')
    dm._setActive(701)
  })

  await expect(page.locator('#dataset-editor-textarea')).toBeHidden()
  await expect(page.locator('#dataset-editor-nl')).toHaveValue('A person looks at the camera.')
  await page.locator('#dataset-trigger').fill('Hero_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()

  await expect(page.locator('#dataset-editor-nl')).toHaveValue(
    'Hero_Token, A person looks at the camera.',
  )
  await page.locator('#dataset-trigger').fill('Replacement_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect(page.locator('#dataset-editor-nl')).toHaveValue(
    'Replacement_Token, A person looks at the camera.',
  )
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const payload = dm._buildExportPayload()
    return {
      booruOverridePresent: dm.captionEdits.has(701),
      nlOverride: dm.nlEdits.get(701),
      exportNlOverride: payload.image_nl_overrides['701'],
      exportType: payload.image_types['701'],
    }
  })).toEqual({
    booruOverridePresent: false,
    nlOverride: 'Replacement_Token, A person looks at the camera.',
    exportNlOverride: 'Replacement_Token, A person looks at the camera.',
    exportType: 'nl',
  })

  await page.evaluate(() => (window as any).DatasetMaker._saveSession())
  await page.reload()
  await page.waitForFunction(() => typeof (window as any).DatasetMaker?._captionTypeFor === 'function')
  await page.evaluate(() => (window as any).App.switchView('dataset'))
  await page.locator('#dataset-tab-workbench').click()
  await expect(page.locator('#dataset-editor-nl')).toHaveValue(
    'Replacement_Token, A person looks at the camera.',
  )
})

test('trigger quickfill replaces only the managed Booru token for Both captions', async ({ page }) => {
  await stubTriggerCaptionPreview(page, 'both-replacement')
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm.nlCaptions.set(701, 'A person looks at the camera.')
    dm.captionType.set(701, 'both')
    dm._setActive(701)
  })

  await page.locator('#dataset-trigger').fill('First_Both_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect.poll(() => page.evaluate(() => (
    (window as any).DatasetMaker.captionEdits.get(701)
  ))).toBe('First_Both_Token, caption-701')
  await page.locator('#dataset-trigger').fill('Second_Both_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()

  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const payload = dm._buildExportPayload()
    return {
      booru: dm._booruTextFor(701),
      nl: dm._nlTextFor(701),
      type: payload.image_types['701'],
      exportBooru: payload.image_overrides['701'],
    }
  })).toEqual({
    booru: 'Second_Both_Token, caption-701',
    nl: 'A person looks at the camera.',
    type: 'both',
    exportBooru: 'Second_Both_Token, caption-701',
  })
})

test('trigger quickfill flushes an immediate caption edit before reporting success', async ({ page }) => {
  await stubTriggerCaptionPreview(page, 'immediate-edit')
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm._setActive(701)
    ;(document.getElementById('dataset-trigger') as HTMLInputElement).value = 'Instant_Token'
    dm._syncTriggerQuickfillButton()
    const textarea = document.getElementById('dataset-editor-textarea') as HTMLTextAreaElement
    textarea.value = 'freshly edited, smile'
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    clearTimeout(dm._captionInputTimer)
    dm._captionInputTimer = setTimeout(() => {}, 5000)
    ;(document.getElementById('btn-dataset-quickfill-trigger') as HTMLButtonElement).click()
  })

  await expect(page.locator('#toast-container .toast.success')).toHaveCount(1)
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      caption: dm.captionEdits.get(701),
      pendingEdit: dm._pendingCaptionEdit,
    }
  })).toEqual({
    caption: 'Instant_Token, freshly edited, smile',
    pendingEdit: null,
  })
  expect(await page.evaluate(() => (
    (window as any).DatasetMaker._buildExportPayload().image_overrides['701']
  ))).toBe('Instant_Token, freshly edited, smile')
})

test('trigger quickfill reports a pending local edit storage failure without partial state', async ({ page }) => {
  const pageErrors: string[] = []
  page.on('pageerror', (error) => pageErrors.push(error.message))
  await page.route('**/api/dataset/local-thumbnail**', async (route) => {
    await route.fulfill({ status: 204 })
  })
  await page.goto('/')
  await page.waitForLoadState('networkidle')
  await page.waitForFunction(() => typeof (window as any).DatasetMaker?._captionTypeFor === 'function')
  await page.evaluate(() => (window as any).App.switchView('dataset'))
  await page.waitForFunction(() => {
    const dm = (window as any).DatasetMaker
    return dm?._trainerContractState?.status === 'ready' && dm?._pendingProjectSettings === null
  })
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm.addLocalItems([{
      ds_id: 'ds:8123456789abcdef',
      abs_path: 'C:/dataset/pending-storage-failure.png',
      filename: 'pending-storage-failure.png',
      width: 512,
      height: 512,
      mtime: 1,
      size: 1024,
      thumb_b64: '',
      source_kind: 'folder_path',
      sidecar_capability: 'beside_image',
      sidecar_caption: '1girl, smile',
    }], { showToast: false, switchView: false, focusImportTab: false })
    dm._setActive(dm.imageIds[0])
  })
  await page.locator('#dataset-trigger').fill('Instant_Token')
  await page.evaluate(() => {
    const captionKey = 'sd-image-sorter-dataset-local-captions'
    const originalSetItem = Storage.prototype.setItem
    ;(window as any).__restoreDatasetStorage = () => {
      Storage.prototype.setItem = originalSetItem
    }
    Storage.prototype.setItem = function (key: string, value: string) {
      if (key === captionKey) throw new DOMException('quota exhausted', 'QuotaExceededError')
      return originalSetItem.call(this, key, value)
    }
    const dm = (window as any).DatasetMaker
    const textarea = document.getElementById('dataset-editor-textarea') as HTMLTextAreaElement
    textarea.value = 'freshly edited, smile'
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    clearTimeout(dm._captionInputTimer)
    dm._captionInputTimer = null
  })
  await page.locator('#btn-dataset-quickfill-trigger').click()

  await expect(page.locator('#toast-container .toast.error')).toContainText('quota exhausted')
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(0)
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const localId = dm.imageIds[0]
    ;(window as any).__restoreDatasetStorage()
    delete (window as any).__restoreDatasetStorage
    return {
      managedTrigger: dm._quickfilledTrigger,
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      effectiveCaption: dm._booruTextFor(localId),
      captionEditPresent: dm.captionEdits.has(localId),
      pendingEdit: dm._pendingCaptionEdit,
      busy: document.getElementById('btn-dataset-quickfill-trigger')?.getAttribute('aria-busy'),
    }
  })).toEqual({
    managedTrigger: '',
    commonTags: '',
    effectiveCaption: '1girl, smile',
    captionEditPresent: false,
    pendingEdit: { id: expect.any(Number), value: 'freshly edited, smile' },
    busy: null,
  })
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(1)
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const localId = dm.imageIds[0]
    return {
      managedTrigger: dm._quickfilledTrigger,
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      effectiveCaption: dm._booruTextFor(localId),
      pendingEdit: dm._pendingCaptionEdit,
    }
  })).toEqual({
    managedTrigger: 'Instant_Token',
    commonTags: 'Instant_Token',
    effectiveCaption: 'Instant_Token, freshly edited, smile',
    pendingEdit: null,
  })
  expect(pageErrors).toEqual([])
})

test('trigger quickfill deduplicates equivalent tokens in multiline captions', async ({ page }) => {
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm.imageIds = [703]
    dm.captionEdits.set(703, 'Hero_Token\n1girl, hero token')
    dm._renderQueue()
    dm._setActive(703)
  })

  await page.locator('#dataset-trigger').fill('Hero_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()

  await expect.poll(() => page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      caption: dm.captionEdits.get(703),
      exportCaption: dm._buildExportPayload().image_overrides['703'],
    }
  })).toEqual({
    caption: 'Hero_Token\n1girl',
    exportCaption: 'Hero_Token\n1girl',
  })
})

test('trigger quickfill replaces the previously managed trigger across reloads', async ({ page }) => {
  await page.route('**/api/tags/export-preview', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    const trigger = String(body.trigger || '').trim()
    const sourceTags: Record<number, string[]> = {
      701: ['1girl', 'smile'],
      702: ['1girl', 'frown'],
      704: [],
    }
    const imageIds = Array.isArray(body.image_ids) ? body.image_ids.map(Number) : []
    await route.fulfill({
      json: {
        results: imageIds.map((imageId) => ({
          image_id: imageId,
          filename: `trigger-replacement-${imageId}.png`,
          rendered: [trigger, ...(sourceTags[imageId] || [])].filter(Boolean).join(', '),
        })),
      },
    })
  })
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.locator('#dataset-common-tags').fill('masterpiece')

  await page.locator('#dataset-trigger').fill('First_Trigger')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect.poll(() => page.evaluate(() => ({
    commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
    caption: (window as any).DatasetMaker.captionEdits.get(701),
  }))).toEqual({
    commonTags: 'masterpiece, First_Trigger',
    caption: 'First_Trigger, 1girl, smile',
  })
  await page.evaluate(() => {
    ;(window as any).DatasetMaker.captionEdits.set(
      703,
      'First_Trigger\n1girl, frown, hat',
    )
  })
  await page.locator('#dataset-trigger').fill('Second_Trigger')
  await page.locator('#btn-dataset-quickfill-trigger').click()

  await expect.poll(() => page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      captions: dm.imageIds.map((imageId: number) => dm.captionEdits.get(imageId)),
    }
  })).toEqual({
    commonTags: 'masterpiece, Second_Trigger',
    captions: [
      'Second_Trigger, 1girl, smile',
      'Second_Trigger, 1girl, frown',
      'Second_Trigger\n1girl, frown, hat',
      'Second_Trigger',
    ],
  })

  await page.evaluate(() => {
    ;(window as any).DatasetMaker._saveSession()
    const storageKey = 'sd-image-sorter-dataset-session'
    const legacyDraft = JSON.parse(localStorage.getItem(storageKey) || 'null')
    delete legacyDraft.quickfilledTrigger
    localStorage.setItem(storageKey, JSON.stringify(legacyDraft))
  })
  await page.reload()
  await page.waitForFunction(() => typeof (window as any).DatasetMaker?._captionTypeFor === 'function')
  await page.evaluate(() => (window as any).App.switchView('dataset'))
  await page.waitForFunction(() => (window as any).DatasetMaker?._pendingProjectSettings === null)
  await page.locator('#dataset-tab-workbench').click()
  await page.locator('#dataset-trigger').fill('Third_Trigger')
  await page.locator('#btn-dataset-quickfill-trigger').click()

  await expect.poll(() => page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      captions: dm.imageIds.map((imageId: number) => dm.captionEdits.get(imageId)),
    }
  })).toEqual({
    commonTags: 'masterpiece, Third_Trigger',
    captions: [
      'Third_Trigger, 1girl, smile',
      'Third_Trigger, 1girl, frown',
      'Third_Trigger\n1girl, frown, hat',
      'Third_Trigger',
    ],
  })
})

test('historical trigger cleanup removes only the confirmed stale token', async ({ page }) => {
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm._quickfilledTrigger = 'Current_Trigger'
    dm.captionEdits.set(
      701,
      'Current_Trigger\nLegacy_Trigger, 1girl, legacy trigger, smile',
    )
    dm.nlEdits.set(702, 'Legacy Trigger, A person looks at the camera.')
    dm.captionType.set(702, 'nl')
    ;(document.getElementById('dataset-trigger') as HTMLInputElement).value = 'Current_Trigger'
    ;(document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value = (
      'masterpiece, Legacy_Trigger, Current_Trigger'
    )
    ;(document.getElementById('dataset-blacklist') as HTMLTextAreaElement).value = (
      'existing_one\nexisting two'
    )
    ;(document.getElementById('dataset-underscore-to-space') as HTMLInputElement).checked = false
    dm._renderQueue()
    dm._setActive(701)
  })

  await page.locator('#btn-dataset-cleanup-trigger').click()
  await page.locator('#input-modal-field').fill('legacy trigger')
  await page.locator('#btn-input-ok').click()

  await expect(page.locator('#toast-container .toast.success')).toContainText('legacy trigger')
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      managedTrigger: dm._quickfilledTrigger,
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      blacklistText: (document.getElementById('dataset-blacklist') as HTMLTextAreaElement).value,
      blacklist: dm._buildExportPayload().blacklist,
      booru: dm.captionEdits.get(701),
      nl: dm.nlEdits.get(702),
      exportBooru: dm._buildExportPayload().image_overrides['701'],
    }
  })).toEqual({
    managedTrigger: 'Current_Trigger',
    commonTags: 'masterpiece, Current_Trigger',
    blacklistText: 'existing_one\nexisting two\nlegacy trigger\nlegacy_trigger',
    blacklist: ['existing_one', 'existing two', 'legacy trigger', 'legacy_trigger'],
    booru: 'Current_Trigger\n1girl, smile',
    nl: 'A person looks at the camera.',
    exportBooru: 'Current_Trigger\n1girl, smile',
  })
})

test('historical trigger cleanup persists local captions before clear and reimport', async ({ page }) => {
  await stubTriggerCaptionPreview(page, 'local-cleanup-reimport')
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  const localItem = {
    ds_id: 'ds:5123456789abcdef',
    abs_path: 'C:/dataset/local-cleanup-reimport.png',
    filename: 'local-cleanup-reimport.png',
    width: 512,
    height: 512,
    mtime: 1,
    size: 1024,
    thumb_b64: '',
    source_kind: 'folder_path',
    sidecar_capability: 'beside_image',
    sidecar_caption: '1girl, smile',
  }
  await page.evaluate((item) => {
    ;(window as any).DatasetMaker.addLocalItems(
      [item],
      { showToast: false, switchView: false, focusImportTab: false },
    )
  }, localItem)
  await page.locator('#dataset-trigger').fill('Current_Trigger')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect.poll(() => page.evaluate(({ dsId }) => {
    const dm = (window as any).DatasetMaker
    const localId = dm._dsIdToNumericId(dsId)
    return dm.captionEdits.get(localId)
  }, { dsId: localItem.ds_id })).toBe('Current_Trigger, 1girl, smile')
  await page.evaluate(({ dsId }) => {
    const dm = (window as any).DatasetMaker
    const localId = dm._dsIdToNumericId(dsId)
    dm.captionEdits.set(localId, 'Current_Trigger, Legacy_Trigger, 1girl, smile')
    dm._setActive(localId)
  }, { dsId: localItem.ds_id })

  await page.locator('#btn-dataset-cleanup-trigger').click()
  await page.locator('#input-modal-field').fill('Legacy_Trigger')
  await page.locator('#btn-input-ok').click()
  await expect(page.locator('#toast-container .toast.success').last()).toContainText('Legacy_Trigger')

  expect(await page.evaluate((path) => {
    const captions = JSON.parse(
      localStorage.getItem('sd-image-sorter-dataset-local-captions') || '{}',
    )
    const owners = JSON.parse(
      localStorage.getItem('sd-image-sorter-dataset-local-caption-triggers') || '{}',
    )
    return { caption: captions[path], owner: owners[path] }
  }, localItem.abs_path)).toEqual({
    caption: 'Current_Trigger, 1girl, smile',
    owner: 'Current_Trigger',
  })

  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const app = (window as any).App
    const originalShowConfirm = app.showConfirm
    app.showConfirm = (_title: string, _message: string, onConfirm: () => void) => onConfirm()
    try {
      dm._clearAll()
    } finally {
      app.showConfirm = originalShowConfirm
    }
  })
  await page.evaluate((item) => {
    ;(window as any).DatasetMaker.addLocalItems(
      [item],
      { showToast: false, switchView: false, focusImportTab: false },
    )
  }, localItem)

  expect(await page.evaluate(({ path, dsId }) => {
    const dm = (window as any).DatasetMaker
    const localId = dm._dsIdToNumericId(dsId)
    const payload = dm._buildExportPayload()
    return {
      captionEdit: dm.captionEdits.get(localId),
      exportOverride: payload.image_overrides[path],
    }
  }, { path: localItem.abs_path, dsId: localItem.ds_id })).toEqual({
    captionEdit: 'Current_Trigger, 1girl, smile',
    exportOverride: 'Current_Trigger, 1girl, smile',
  })
})

test('historical trigger cleanup preserves an empty local caption through clear and reimport', async ({ page }) => {
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  const localItem = {
    ds_id: 'ds:7123456789abcdef',
    abs_path: 'C:/dataset/local-cleanup-empty-caption.png',
    filename: 'local-cleanup-empty-caption.png',
    width: 512,
    height: 512,
    mtime: 1,
    size: 1024,
    thumb_b64: '',
    source_kind: 'folder_path',
    sidecar_capability: 'beside_image',
    sidecar_caption: 'Legacy_Trigger',
  }
  await page.evaluate((item) => {
    ;(window as any).DatasetMaker.addLocalItems(
      [item],
      { showToast: false, switchView: false, focusImportTab: false },
    )
    const dm = (window as any).DatasetMaker
    dm._quickfilledTrigger = 'Current_Trigger'
    ;(document.getElementById('dataset-trigger') as HTMLInputElement).value = 'Current_Trigger'
  }, localItem)

  await page.locator('#btn-dataset-cleanup-trigger').click()
  await page.locator('#input-modal-field').fill('Legacy_Trigger')
  await page.locator('#btn-input-ok').click()
  await expect(page.locator('#toast-container .toast.success').last()).toContainText('Legacy_Trigger')
  expect(await page.evaluate((path) => {
    const captions = JSON.parse(
      localStorage.getItem('sd-image-sorter-dataset-local-captions') || '{}',
    )
    return captions[path]
  }, localItem.abs_path)).toBe('')

  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const app = (window as any).App
    const originalShowConfirm = app.showConfirm
    app.showConfirm = (_title: string, _message: string, onConfirm: () => void) => onConfirm()
    try {
      dm._clearAll()
    } finally {
      app.showConfirm = originalShowConfirm
    }
  })
  await page.evaluate((item) => {
    ;(window as any).DatasetMaker.addLocalItems(
      [item],
      { showToast: false, switchView: false, focusImportTab: false },
    )
  }, localItem)

  expect(await page.evaluate(({ path, dsId }) => {
    const dm = (window as any).DatasetMaker
    const localId = dm._dsIdToNumericId(dsId)
    const payload = dm._buildExportPayload()
    return {
      captionEdit: dm.captionEdits.get(localId),
      exportOverride: payload.image_overrides[path],
    }
  }, { path: localItem.abs_path, dsId: localItem.ds_id })).toEqual({
    captionEdit: 'Current_Trigger',
    exportOverride: 'Current_Trigger',
  })
})

test('manual local caption clearing removes the saved path override', async ({ page }) => {
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  const localItem = {
    ds_id: 'ds:8123456789abcdef',
    abs_path: 'C:/dataset/local-manual-clear-caption.png',
    filename: 'local-manual-clear-caption.png',
    width: 512,
    height: 512,
    mtime: 1,
    size: 1024,
    thumb_b64: '',
    source_kind: 'folder_path',
    sidecar_capability: 'beside_image',
    sidecar_caption: '1girl, smile',
  }
  await page.evaluate((item) => {
    const dm = (window as any).DatasetMaker
    dm.addLocalItems(
      [item],
      { showToast: false, switchView: false, focusImportTab: false },
    )
    dm._setActive(dm._dsIdToNumericId(item.ds_id))
  }, localItem)

  const captionEditor = page.locator('#dataset-editor-textarea')
  await expect(captionEditor).toHaveValue('1girl, smile')
  await captionEditor.fill('manual caption')
  await expect.poll(() => page.evaluate((path) => {
    const captions = JSON.parse(
      localStorage.getItem('sd-image-sorter-dataset-local-captions') || '{}',
    )
    return captions[path]
  }, localItem.abs_path)).toBe('manual caption')

  await captionEditor.fill('')
  await expect.poll(() => page.evaluate((path) => {
    const captions = JSON.parse(
      localStorage.getItem('sd-image-sorter-dataset-local-captions') || '{}',
    )
    return Object.prototype.hasOwnProperty.call(captions, path)
  }, localItem.abs_path)).toBe(false)
})

test('historical trigger cleanup does not read local storage for gallery and NL captions', async ({ page }) => {
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm._quickfilledTrigger = 'Current_Trigger'
    dm.captionEdits.set(701, 'Current_Trigger, Legacy_Trigger, 1girl, smile')
    dm.captionType.set(702, 'nl')
    dm.nlEdits.set(702, 'Legacy_Trigger, A person looks at the camera.')
    ;(document.getElementById('dataset-trigger') as HTMLInputElement).value = 'Current_Trigger'
    const originalGetItem = Storage.prototype.getItem
    Storage.prototype.getItem = function (key: string) {
      if (
        key === 'sd-image-sorter-dataset-local-captions'
        || key === 'sd-image-sorter-dataset-local-caption-triggers'
      ) {
        throw new DOMException('Local caption cache is unavailable', 'InvalidStateError')
      }
      return originalGetItem.call(this, key)
    }
    ;(window as any).__restoreGalleryCleanupStorage = () => {
      Storage.prototype.getItem = originalGetItem
    }
  })

  await page.locator('#btn-dataset-cleanup-trigger').click()
  await page.locator('#input-modal-field').fill('Legacy_Trigger')
  await page.locator('#btn-input-ok').click()
  await expect(page.locator('#toast-container .toast.success').last()).toContainText('Legacy_Trigger')
  await page.evaluate(() => (window as any).__restoreGalleryCleanupStorage())

  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      booru: dm.captionEdits.get(701),
      nl: dm.nlEdits.get(702),
    }
  })).toEqual({
    booru: 'Current_Trigger, 1girl, smile',
    nl: 'A person looks at the camera.',
  })
})

test('trigger quickfill ignores an unavailable local cache without local Booru overrides', async ({ page }) => {
  await stubTriggerCaptionPreview(page, 'gallery-nl-cache-isolation')
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm.captionType.set(702, 'nl')
    dm.nlCaptions.set(702, 'A quiet scene.')
    const originalGetItem = Storage.prototype.getItem
    Storage.prototype.getItem = function (key: string) {
      if (
        key === 'sd-image-sorter-dataset-local-captions'
        || key === 'sd-image-sorter-dataset-local-caption-triggers'
      ) {
        throw new DOMException('Local caption cache is unavailable', 'InvalidStateError')
      }
      return originalGetItem.call(this, key)
    }
    ;(window as any).__restoreQuickfillStorage = () => {
      Storage.prototype.getItem = originalGetItem
    }
  })

  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const trigger = document.getElementById('dataset-trigger') as HTMLInputElement
    trigger.value = 'Cache_Independent_Token'
    dm._lastValidDatasetTrigger = trigger.value
    dm._syncTriggerQuickfillButton()
  })
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect(page.locator('#toast-container .toast.success').last())
    .toContainText('Cache_Independent_Token')
  await page.evaluate(() => (window as any).__restoreQuickfillStorage())

  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      managedTrigger: dm._quickfilledTrigger,
      booru: dm.captionEdits.get(701),
      nl: dm.nlEdits.get(702),
    }
  })).toEqual({
    managedTrigger: 'Cache_Independent_Token',
    booru: 'Cache_Independent_Token, caption-701',
    nl: 'Cache_Independent_Token, A quiet scene.',
  })
})

test('trigger quickfill ignores an unavailable local cache for an NL item with a historical Booru draft', async ({ page }) => {
  await seedDatasetQueue(page)
  await seedLocalItem(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate((localId) => {
    const dm = (window as any).DatasetMaker
    dm.imageIds = [localId]
    dm.captionEdits.set(localId, 'Historical_Booru_Draft')
    dm.captionType.set(localId, 'nl')
    dm.nlCaptions.set(localId, 'A quiet scene.')
    dm._renderQueue()
    dm._setActive(localId)
    const originalGetItem = Storage.prototype.getItem
    Storage.prototype.getItem = function (key: string) {
      if (
        key === 'sd-image-sorter-dataset-local-captions'
        || key === 'sd-image-sorter-dataset-local-caption-triggers'
      ) {
        throw new DOMException('Local caption cache is unavailable', 'InvalidStateError')
      }
      return originalGetItem.call(this, key)
    }
    ;(window as any).__restoreQuickfillStorage = () => {
      Storage.prototype.getItem = originalGetItem
    }
    const trigger = document.getElementById('dataset-trigger') as HTMLInputElement
    trigger.value = 'NL_Cache_Independent_Token'
    dm._lastValidDatasetTrigger = trigger.value
    dm._syncTriggerQuickfillButton()
  }, LOCAL_ID)

  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect(page.locator('#toast-container .toast.success').last())
    .toContainText('NL_Cache_Independent_Token')
  await page.evaluate(() => (window as any).__restoreQuickfillStorage())

  expect(await page.evaluate((localId) => {
    const dm = (window as any).DatasetMaker
    return {
      managedTrigger: dm._quickfilledTrigger,
      booru: dm.captionEdits.get(localId),
      nl: dm.nlEdits.get(localId),
    }
  }, LOCAL_ID)).toEqual({
    managedTrigger: 'NL_Cache_Independent_Token',
    booru: 'Historical_Booru_Draft',
    nl: 'NL_Cache_Independent_Token, A quiet scene.',
  })
})

test('historical trigger cleanup leaves all state unchanged when local persistence fails', async ({ page }) => {
  await stubTriggerCaptionPreview(page, 'local-cleanup-storage-failure')
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  const localItem = {
    ds_id: 'ds:6123456789abcdef',
    abs_path: 'C:/dataset/local-cleanup-storage-failure.png',
    filename: 'local-cleanup-storage-failure.png',
    width: 512,
    height: 512,
    mtime: 1,
    size: 1024,
    thumb_b64: '',
    source_kind: 'folder_path',
    sidecar_capability: 'beside_image',
    sidecar_caption: '1girl, smile',
  }
  await page.evaluate((item) => {
    ;(window as any).DatasetMaker.addLocalItems(
      [item],
      { showToast: false, switchView: false, focusImportTab: false },
    )
  }, localItem)
  await page.locator('#dataset-trigger').fill('Current_Trigger')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect.poll(() => page.evaluate(({ dsId }) => {
    const dm = (window as any).DatasetMaker
    const localId = dm._dsIdToNumericId(dsId)
    return dm.captionEdits.get(localId)
  }, { dsId: localItem.ds_id })).toBe('Current_Trigger, 1girl, smile')
  await page.evaluate(({ dsId }) => {
    const dm = (window as any).DatasetMaker
    const localId = dm._dsIdToNumericId(dsId)
    dm.captionEdits.set(localId, 'Current_Trigger, Legacy_Trigger, 1girl, smile')
    ;(document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value = (
      'Legacy_Trigger, Current_Trigger'
    )
    ;(document.getElementById('dataset-blacklist') as HTMLTextAreaElement).value = 'existing_tag'
    dm._setActive(localId)
    const originalSetItem = Storage.prototype.setItem
    Storage.prototype.setItem = function (key: string, value: string) {
      if (key === 'sd-image-sorter-dataset-local-caption-triggers') {
        throw new DOMException('Quota exceeded', 'QuotaExceededError')
      }
      return originalSetItem.call(this, key, value)
    }
    ;(window as any).__restoreCleanupStorage = () => {
      Storage.prototype.setItem = originalSetItem
    }
  }, { dsId: localItem.ds_id })

  const successToastCountBeforeCleanup = await page.locator('#toast-container .toast.success').count()
  await page.locator('#btn-dataset-cleanup-trigger').click()
  await page.locator('#input-modal-field').fill('Legacy_Trigger')
  await page.locator('#btn-input-ok').click()
  await expect(page.locator('#toast-container .toast.error').last()).toContainText('persistence failed')
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(successToastCountBeforeCleanup)
  await page.evaluate(() => (window as any).__restoreCleanupStorage())

  expect(await page.evaluate(({ path, dsId }) => {
    const dm = (window as any).DatasetMaker
    const localId = dm._dsIdToNumericId(dsId)
    const captions = JSON.parse(
      localStorage.getItem('sd-image-sorter-dataset-local-captions') || '{}',
    )
    const owners = JSON.parse(
      localStorage.getItem('sd-image-sorter-dataset-local-caption-triggers') || '{}',
    )
    return {
      captionEdit: dm.captionEdits.get(localId),
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      blacklist: (document.getElementById('dataset-blacklist') as HTMLTextAreaElement).value,
      savedCaption: captions[path],
      savedOwner: owners[path],
    }
  }, { path: localItem.abs_path, dsId: localItem.ds_id })).toEqual({
    captionEdit: 'Current_Trigger, Legacy_Trigger, 1girl, smile',
    commonTags: 'Legacy_Trigger, Current_Trigger',
    blacklist: 'existing_tag',
    savedCaption: 'Current_Trigger, Legacy_Trigger, 1girl, smile',
    savedOwner: 'Current_Trigger',
  })
})

test('historical trigger cleanup rejects a blacklist overflow atomically', async ({ page }) => {
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  const initialState = await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm._quickfilledTrigger = 'Current_Trigger'
    dm.captionEdits.set(701, 'Current_Trigger, Legacy Trigger, 1girl')
    ;(document.getElementById('dataset-trigger') as HTMLInputElement).value = 'Current_Trigger'
    ;(document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value = (
      'Legacy Trigger, Current_Trigger'
    )
    ;(document.getElementById('dataset-blacklist') as HTMLTextAreaElement).value = Array.from(
      { length: 999 },
      (_, index) => `existing-${index}`,
    ).join('\n')
    dm._renderQueue()
    dm._setActive(701)
    return {
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      blacklist: (document.getElementById('dataset-blacklist') as HTMLTextAreaElement).value,
      caption: dm.captionEdits.get(701),
    }
  })

  await page.locator('#btn-dataset-cleanup-trigger').click()
  await page.locator('#input-modal-field').fill('Legacy Trigger')
  await page.locator('#btn-input-ok').click()

  await expect(page.locator('#toast-container .toast.error')).toContainText('1,000 entries')
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(0)
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      blacklist: (document.getElementById('dataset-blacklist') as HTMLTextAreaElement).value,
      caption: dm.captionEdits.get(701),
    }
  })).toEqual(initialState)
})

test('historical trigger cleanup is superseded when the Dataset Project changes in the modal', async ({ page }) => {
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm.imageIds = [701]
    dm._activeProject = { id: 301, revision: 4 }
    dm._annotationHeadsStatus = 'ready'
    dm._annotationHeadsOwner = { project_id: 301, project_revision: 4 }
    dm._quickfilledTrigger = 'Current_Trigger'
    dm.captionEdits.set(701, 'Current_Trigger, Legacy_Trigger, 1girl')
    ;(document.getElementById('dataset-trigger') as HTMLInputElement).value = 'Current_Trigger'
    ;(document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value = (
      'Legacy_Trigger, Current_Trigger'
    )
    ;(document.getElementById('dataset-blacklist') as HTMLTextAreaElement).value = 'existing_tag'
    dm._renderQueue()
    dm._setActive(701)
  })

  await page.locator('#btn-dataset-cleanup-trigger').click()
  await expect(page.locator('#input-modal')).toBeVisible()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm._activeProject = { id: 302, revision: 1 }
    dm._annotationHeadsStatus = 'loading'
    dm._annotationHeadsOwner = { project_id: 302, project_revision: 1 }
  })
  await page.locator('#input-modal-field').fill('Legacy_Trigger')
  await page.locator('#btn-input-ok').click()

  await expect(page.locator('#toast-container .toast.error')).toContainText('Dataset changed')
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      project: dm._activeProject,
      managedTrigger: dm._quickfilledTrigger,
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      blacklist: (document.getElementById('dataset-blacklist') as HTMLTextAreaElement).value,
      booru: dm.captionEdits.get(701),
    }
  })).toEqual({
    project: { id: 302, revision: 1 },
    managedTrigger: 'Current_Trigger',
    commonTags: 'Legacy_Trigger, Current_Trigger',
    blacklist: 'existing_tag',
    booru: 'Current_Trigger, Legacy_Trigger, 1girl',
  })
})

test('historical trigger cleanup rejects the current trigger and multi-token input', async ({ page }) => {
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm._quickfilledTrigger = 'Current_Trigger'
    dm.captionEdits.set(701, 'Current_Trigger, Legacy_Trigger, 1girl, smile')
    ;(document.getElementById('dataset-trigger') as HTMLInputElement).value = 'Current_Trigger'
    ;(document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value = (
      'Legacy_Trigger, Current_Trigger'
    )
    dm._renderQueue()
    dm._setActive(701)
  })

  await page.locator('#btn-dataset-cleanup-trigger').click()
  await page.locator('#input-modal-field').fill('current trigger')
  await page.locator('#btn-input-ok').click()
  await expect(page.locator('#toast-container .toast.warning')).toContainText('current trigger')

  await page.locator('#btn-dataset-cleanup-trigger').click()
  await page.locator('#input-modal-field').fill('Legacy_Trigger, Other_Trigger')
  await page.locator('#btn-input-ok').click()
  await expect(page.locator('#toast-container .toast.error')).toContainText('cannot contain commas')

  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      managedTrigger: dm._quickfilledTrigger,
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      caption: dm.captionEdits.get(701),
    }
  })).toEqual({
    managedTrigger: 'Current_Trigger',
    commonTags: 'Legacy_Trigger, Current_Trigger',
    caption: 'Current_Trigger, Legacy_Trigger, 1girl, smile',
  })
})

test('historical trigger cleanup refuses a project before caption versions load', async ({ page }) => {
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm._activeProject = { id: 94, revision: 1 }
    dm._annotationHeadsStatus = 'loading'
    dm._annotationHeadsOwner = null
    dm.annotationHeads.clear()
    dm._quickfilledTrigger = 'Current_Trigger'
    dm.captionEdits.set(701, 'Current_Trigger, Legacy_Trigger, 1girl')
    ;(document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value = (
      'Legacy_Trigger, Current_Trigger'
    )
    dm._renderQueue()
    dm._setActive(701)
  })

  await page.locator('#btn-dataset-cleanup-trigger').click()

  await expect(page.locator('#toast-container .toast.error')).toContainText('caption versions')
  await expect(page.locator('#input-modal')).not.toHaveClass(/active/)
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      caption: dm.captionEdits.get(701),
    }
  })).toEqual({
    commonTags: 'Legacy_Trigger, Current_Trigger',
    caption: 'Current_Trigger, Legacy_Trigger, 1girl',
  })
})

test('trigger quickfill fails atomically when the caption refresh is rejected', async ({ page }) => {
  await page.route('**/api/tags/export-preview', async (route) => {
    await route.fulfill({
      status: 503,
      json: { error: 'caption refresh rejected' },
    })
  })
  await seedDatasetQueue(page)
  await seedLocalItem(page)
  await page.locator('#dataset-tab-workbench').click()

  await page.locator('#dataset-trigger').fill('Broken_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()

  await expect(page.locator('#toast-container .toast.error')).toContainText('HTTP 503')
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(0)
  await expect(page.locator('#dataset-common-tags')).toHaveValue('')
  await expect.poll(() => page.evaluate(({ localId }) => {
    const dm = (window as any).DatasetMaker
    return {
      generated: dm.captions.get(701),
      manual: dm.captionEdits.get(703),
      local: dm.captions.get(localId),
      buttonBusy: document.getElementById('btn-dataset-quickfill-trigger')
        ?.getAttribute('aria-busy'),
    }
  }, { localId: LOCAL_ID })).toEqual({
    generated: '1girl, smile',
    manual: '1girl, frown, hat',
    local: 'local caption, tree',
    buttonBusy: null,
  })
})

test('trigger quickfill rejects a partial successful caption response atomically', async ({ page }) => {
  await page.route('**/api/tags/export-preview', async (route) => {
    const body = route.request().postDataJSON() as { image_ids?: number[] }
    const imageIds = Array.isArray(body.image_ids) ? body.image_ids.map(Number) : []
    await route.fulfill({
      json: {
        results: imageIds.slice(0, -1).map((imageId) => ({
          image_id: imageId,
          filename: `partial-${imageId}.png`,
          rendered: `Broken_Token, caption-${imageId}`,
        })),
      },
    })
  })
  await seedDatasetQueue(page)
  await seedLocalItem(page)
  await page.locator('#dataset-tab-workbench').click()

  await page.locator('#dataset-trigger').fill('Broken_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()

  await expect(page.locator('#toast-container .toast.error')).toContainText('missing image_ids=[704]')
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(0)
  await expect(page.locator('#dataset-common-tags')).toHaveValue('')
  await expect.poll(() => page.evaluate(({ localId }) => {
    const dm = (window as any).DatasetMaker
    return {
      generated: dm.captions.get(701),
      manual: dm.captionEdits.get(703),
      local: dm.captions.get(localId),
    }
  }, { localId: LOCAL_ID })).toEqual({
    generated: '1girl, smile',
    manual: '1girl, frown, hat',
    local: 'local caption, tree',
  })
})

test('trigger quickfill rejects complete responses that omit the trigger', async ({ page }) => {
  await page.route('**/api/tags/export-preview', async (route) => {
    const body = route.request().postDataJSON() as { image_ids?: number[] }
    const imageIds = Array.isArray(body.image_ids) ? body.image_ids.map(Number) : []
    await route.fulfill({
      json: {
        results: imageIds.map((imageId) => ({
          image_id: imageId,
          filename: `missing-trigger-${imageId}.png`,
          rendered: `caption-${imageId}`,
        })),
      },
    })
  })
  await seedDatasetQueue(page)
  await seedLocalItem(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.locator('#dataset-step-cleanup > summary').click()

  await page.locator('#dataset-trigger').fill('Blocked_Token')
  await page.locator('#dataset-blacklist').fill('Blocked Token')
  await expect.poll(() => page.evaluate(() => (
    (window as any).DatasetMaker.captions.get(701)
  ))).toBe('caption-701')
  const before = await page.evaluate(({ localId }) => {
    const dm = (window as any).DatasetMaker
    return {
      generated: dm.captions.get(701),
      manual: dm.captionEdits.get(703),
      local: dm.captions.get(localId),
    }
  }, { localId: LOCAL_ID })

  await page.locator('#btn-dataset-quickfill-trigger').click()

  await expect(page.locator('#toast-container .toast.error')).toContainText('Blocked_Token')
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(0)
  await expect(page.locator('#dataset-common-tags')).toHaveValue('')
  await expect.poll(() => page.evaluate(({ localId }) => {
    const dm = (window as any).DatasetMaker
    return {
      generated: dm.captions.get(701),
      manual: dm.captionEdits.get(703),
      local: dm.captions.get(localId),
    }
  }, { localId: LOCAL_ID })).toEqual(before)
})

test('trigger quickfill writes Gallery and local captions when the template omits the trigger', async ({ page }) => {
  await page.route('**/api/tags/export-preview', async (route) => {
    const body = route.request().postDataJSON() as { image_ids?: number[] }
    const imageIds = Array.isArray(body.image_ids) ? body.image_ids.map(Number) : []
    await route.fulfill({
      json: {
        results: imageIds.map((imageId) => ({
          image_id: imageId,
          filename: `custom-template-${imageId}.png`,
          rendered: `caption-${imageId}`,
        })),
      },
    })
  })
  await seedDatasetQueue(page)
  await seedLocalItem(page)
  await page.evaluate(({ localId }) => {
    const dm = (window as any).DatasetMaker
    dm.imageIds = [701, localId]
    dm.captionEdits.clear()
    dm._renderQueue()
    dm._setActive(701)
  }, { localId: LOCAL_ID })
  await page.locator('#dataset-tab-workbench').click()
  await page.locator('#dataset-step-cleanup > summary').click()
  await page.locator('.dataset-caption-advanced > summary').click()

  await page.locator('#dataset-template-override').fill('{tags:filtered}')
  await page.locator('#dataset-trigger').fill('Local_Template_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()

  await expect(page.locator('#toast-container .toast.success')).toContainText('Local_Template_Token')
  await expect(page.locator('#dataset-common-tags')).toHaveValue('Local_Template_Token')
  await expect.poll(() => page.evaluate(({ localId, localPath }) => {
    const dm = (window as any).DatasetMaker
    const payload = dm._buildExportPayload()
    return {
      galleryCaption: dm.captionEdits.get(701),
      localCaption: dm.captionEdits.get(localId),
      galleryExport: payload.image_overrides['701'],
      localExport: payload.image_overrides[localPath],
    }
  }, { localId: LOCAL_ID, localPath: LOCAL_PATH })).toEqual({
    galleryCaption: 'Local_Template_Token, caption-701',
    localCaption: 'Local_Template_Token, local caption, tree',
    galleryExport: 'Local_Template_Token, caption-701',
    localExport: 'Local_Template_Token, local caption, tree',
  })
})

test('superseded trigger quickfill preserves newer caption settings', async ({ page }) => {
  let oldRequestStarted = false
  const oldRequestGate: { release?: () => void } = {}
  await page.route('**/api/tags/export-preview', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    const trigger = String(body.trigger || '').trim()
    const imageIds = Array.isArray(body.image_ids) ? body.image_ids.map(Number) : []
    if (trigger === 'Old_Token') {
      oldRequestStarted = true
      await new Promise<void>((resolve) => { oldRequestGate.release = resolve })
    }
    await route.fulfill({
      json: {
        results: imageIds.map((imageId) => ({
          image_id: imageId,
          filename: `superseded-${imageId}.png`,
          rendered: `${trigger}, caption-${imageId}`,
        })),
      },
    })
  })
  await seedDatasetQueue(page)
  await seedLocalItem(page)
  await page.locator('#dataset-tab-workbench').click()

  await page.locator('#dataset-trigger').fill('Old_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect.poll(() => oldRequestStarted).toBe(true)
  await page.locator('#dataset-trigger').fill('New_Token')
  await page.locator('#dataset-common-tags').fill('User_Tag')
  await expect.poll(() => page.evaluate(() => (
    (window as any).DatasetMaker.captions.get(701)
  ))).toBe('New_Token, caption-701')
  if (!oldRequestGate.release) throw new Error('Old quickfill request was not held')
  oldRequestGate.release()

  await expect(page.locator('#toast-container .toast.error')).toContainText('newer input was kept')
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(0)
  await expect(page.locator('#dataset-common-tags')).toHaveValue('User_Tag')
  await expect.poll(() => page.evaluate(({ localId }) => {
    const dm = (window as any).DatasetMaker
    return {
      generated: dm.captions.get(701),
      manual: dm.captionEdits.get(703),
      local: dm.captions.get(localId),
    }
  }, { localId: LOCAL_ID })).toEqual({
    generated: 'New_Token, caption-701',
    manual: '1girl, frown, hat',
    local: 'local caption, tree',
  })
})

test('editing an NL caption supersedes an in-flight trigger quickfill', async ({ page }) => {
  const requestGate: { release?: () => void } = {}
  let requestStarted = false
  await page.route('**/api/tags/export-preview', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    const trigger = String(body.trigger || '').trim()
    const imageIds = Array.isArray(body.image_ids) ? body.image_ids.map(Number) : []
    if (trigger === 'Race_NL_Token') {
      requestStarted = true
      await new Promise<void>((resolve) => { requestGate.release = resolve })
    }
    await route.fulfill({
      json: {
        results: imageIds.map((imageId) => ({
          image_id: imageId,
          filename: `nl-edit-race-${imageId}.png`,
          rendered: `${trigger}, caption-${imageId}`,
        })),
      },
    })
  })
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm.nlCaptions.set(701, 'Original sentence.')
    dm.captionType.set(701, 'nl')
    dm._setActive(701)
  })

  await page.locator('#dataset-trigger').fill('Race_NL_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect.poll(() => requestStarted).toBe(true)
  await page.locator('#dataset-editor-nl').fill('Newer user sentence.')
  if (!requestGate.release) throw new Error('Trigger quickfill request was not held')
  requestGate.release()

  await expect(page.locator('#toast-container .toast.error')).toContainText('newer input was kept')
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(0)
  await expect(page.locator('#dataset-common-tags')).toHaveValue('')
  await expect.poll(() => page.evaluate(() => (
    (window as any).DatasetMaker.nlEdits.get(701)
  ))).toBe('Newer user sentence.')
})

test('removing a queued image invalidates trigger quickfill before caption cache writes', async ({ page }) => {
  const requestGate: { release?: () => void } = {}
  let requestStarted = false
  await page.route('**/api/tags/export-preview', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    const trigger = String(body.trigger || '').trim()
    const imageIds = Array.isArray(body.image_ids) ? body.image_ids.map(Number) : []
    if (trigger === 'Removed_Token') {
      requestStarted = true
      await new Promise<void>((resolve) => { requestGate.release = resolve })
    }
    await route.fulfill({
      json: {
        results: imageIds.map((imageId) => ({
          image_id: imageId,
          filename: `removed-${imageId}.png`,
          rendered: `${trigger}, caption-${imageId}`,
        })),
      },
    })
  })
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()

  await page.locator('#dataset-trigger').fill('Removed_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect.poll(() => requestStarted).toBe(true)
  await page.evaluate(() => {
    ;(window as any).DatasetMaker._removeImageById(701, { confirm: false })
  })
  if (!requestGate.release) throw new Error('Trigger quickfill request was not held')
  requestGate.release()

  await expect(page.locator('#toast-container .toast.error')).toContainText('newer input was kept')
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(0)
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      queued: dm.imageIds.includes(701),
      staleCaptionPresent: dm.captions.has(701),
    }
  })).toEqual({ queued: false, staleCaptionPresent: false })
})

test('changing the final caption template supersedes an in-flight trigger quickfill', async ({ page }) => {
  const requestGate: { release?: () => void } = {}
  let requestStarted = false
  await page.route('**/api/tags/export-preview', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    const trigger = String(body.trigger || '').trim()
    const imageIds = Array.isArray(body.image_ids) ? body.image_ids.map(Number) : []
    if (trigger === 'Race_Token') {
      requestStarted = true
      await new Promise<void>((resolve) => { requestGate.release = resolve })
    }
    await route.fulfill({
      json: {
        results: imageIds.map((imageId) => ({
          image_id: imageId,
          filename: `template-race-${imageId}.png`,
          rendered: `${trigger}, caption-${imageId}`,
        })),
      },
    })
  })
  await seedDatasetQueue(page)
  await seedLocalItem(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.locator('#dataset-step-cleanup > summary').click()
  await page.locator('.dataset-caption-advanced > summary').click()

  await page.locator('#dataset-trigger').fill('Race_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect.poll(() => requestStarted).toBe(true)
  await page.locator('#dataset-template-override').fill('{tags:filtered}')
  if (!requestGate.release) throw new Error('Trigger quickfill request was not held')
  requestGate.release()

  await expect(page.locator('#toast-container .toast.error')).toContainText('newer input was kept')
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(0)
  await expect(page.locator('#dataset-common-tags')).toHaveValue('')
  await expect.poll(() => page.evaluate(({ localId }) => {
    const dm = (window as any).DatasetMaker
    return {
      generated: dm.captions.get(701),
      manual: dm.captionEdits.get(703),
      local: dm.captions.get(localId),
      template: (document.getElementById('dataset-template-override') as HTMLTextAreaElement).value,
    }
  }, { localId: LOCAL_ID })).toEqual({
    generated: '1girl, smile',
    manual: '1girl, frown, hat',
    local: 'local caption, tree',
    template: '{tags:filtered}',
  })
})

test('trigger quickfill freezes local and Gallery captions without changing source baselines', async ({ page }) => {
  await stubTriggerCaptionPreview(page, 'local-dynamic')
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm.imageIds.push(-705)
    dm.localItemPaths.set(-705, 'C:/dataset/local-five.png')
    dm.localItemDsIds.set(-705, 'ds:local-five')
    dm.meta.set(-705, {
      source: 'local',
      abs_path: 'C:/dataset/local-five.png',
      filename: 'local-five.png',
    })
    dm.captions.set(-705, '1girl, red_hair')
    dm._renderQueue()
    dm._setActive(-705)
  })

  await page.locator('#dataset-trigger').fill('Local_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect.poll(() => page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    const payload = dm._buildExportPayload()
    return {
      localBaseline: dm.captions.get(-705),
      localEffective: dm._booruTextFor(-705),
      localOverride: dm.captionEdits.get(-705),
      localExportOverride: payload.image_overrides['C:/dataset/local-five.png'],
      galleryOverride: dm.captionEdits.get(702),
    }
  })).toEqual({
    localBaseline: '1girl, red_hair',
    localEffective: 'Local_Token, 1girl, red_hair',
    localOverride: 'Local_Token, 1girl, red_hair',
    localExportOverride: 'Local_Token, 1girl, red_hair',
    galleryOverride: 'Local_Token, caption-702',
  })
})

test('latest trigger refresh wins when an older missing-caption fetch finishes last', async ({ page }) => {
  const oldResponseGate: { release?: () => void } = {}
  let oldRequestStarted = false
  await page.route('**/api/tags/export-preview', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    const trigger = String(body.trigger || '').trim()
    const ids = Array.isArray(body.image_ids) ? body.image_ids.map(Number) : []
    if (trigger === 'Old_Token') {
      oldRequestStarted = true
      await new Promise<void>((resolve) => { oldResponseGate.release = resolve })
    }
    await route.fulfill({
      json: {
        results: ids.map((imageId) => ({
          image_id: imageId,
          filename: `core-${imageId}.png`,
          rendered: `${trigger}, caption-${imageId}`,
        })),
      },
    })
  })
  await seedDatasetQueue(page)

  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm.captions.delete(701)
    ;(document.getElementById('dataset-trigger') as HTMLInputElement).value = 'Old_Token'
    ;(window as any).__oldTriggerRefresh = dm._fetchMissingCaptions()
  })
  await expect.poll(() => oldRequestStarted).toBe(true)
  await page.evaluate(async () => {
    ;(document.getElementById('dataset-trigger') as HTMLInputElement).value = 'New_Token'
    await (window as any).DatasetMaker._refreshAllCaptions()
  })
  if (!oldResponseGate.release) throw new Error('Old trigger response was not held')
  oldResponseGate.release()
  await page.evaluate(async () => { await (window as any).__oldTriggerRefresh })

  await expect.poll(() => page.evaluate(() => (window as any).DatasetMaker.captions.get(701)))
    .toBe('New_Token, caption-701')
})

test('opening Smart Tag from Dataset Maker uses the Dataset trigger', async ({ page }) => {
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.locator('#dataset-trigger').fill('\ufeffShared_Trigger\ufeff')
  await page.evaluate(() => {
    ;(document.getElementById('smart-tag-trigger') as HTMLInputElement).value = 'stale_modal_trigger'
  })

  await page.locator('#btn-dataset-smart-tag').click()
  await expect(page.locator('#smart-tag-trigger')).toHaveValue('Shared_Trigger')
  await expect(page.locator('#dataset-trigger')).toHaveValue('Shared_Trigger')
})

test('opening Smart Tag from Dataset Maker rejects an invalid trigger', async ({ page }) => {
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.locator('#dataset-trigger').fill('Bad\ufeffTrigger')
  await page.evaluate(() => {
    ;(document.getElementById('smart-tag-trigger') as HTMLInputElement).value = 'unchanged_trigger'
  })

  await page.locator('#btn-dataset-smart-tag').click()

  await expect(page.locator('#toast-container .toast.error')).toContainText('internal whitespace')
  await expect(page.locator('#smart-tag-modal')).not.toHaveClass(/visible/)
  await expect(page.locator('#smart-tag-trigger')).toHaveValue('unchanged_trigger')
  await expect(page.locator('#dataset-trigger')).toHaveValue('')
})

test('trigger quickfill creates a frozen draft over an active project revision', async ({ page }) => {
  await stubTriggerCaptionPreview(page, 'saved-revision')
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm._activeProject = { id: 88, revision: 1 }
    dm._annotationHeadsStatus = 'ready'
    dm._annotationHeadsOwner = { project_id: 88, project_revision: 1 }
    dm.annotationHeads = new Map([
      [701, {
        active_revision: {
          id: 1701,
          source: 'manual',
          author_class: 'user',
          provider: null,
          model: null,
          restored_from_revision_id: null,
          created_at: '2026-07-27T00:00:00Z',
          content: {
            content_version: 1,
            booru_caption: 'saved, caption',
            nl_caption: '',
            caption_type: 'booru',
          },
        },
      }],
    ])
    dm._setActive(701)
  })

  await page.locator('#dataset-trigger').fill('Project_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect.poll(() => page.evaluate(() => (
    (window as any).DatasetMaker.captionEdits.get(701)
  ))).toBe('Project_Token, saved, caption')
  await page.locator('#dataset-trigger').fill('Replacement_Project_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect.poll(() => page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return dm._buildExportPayload().annotation_selections['701']
  })).toEqual({
    kind: 'frozen_draft',
    content: {
      content_version: 1,
      booru_caption: 'Replacement_Project_Token, saved, caption',
      nl_caption: '',
      caption_type: 'booru',
    },
  })
})

test('trigger quickfill refuses a named project until annotation heads are ready', async ({ page }) => {
  let previewCalls = 0
  await page.route('**/api/tags/export-preview', async (route) => {
    previewCalls += 1
    await route.fulfill({ json: { results: [] } })
  })
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm._activeProject = { id: 91, revision: 1 }
    dm._annotationHeadsStatus = 'loading'
    dm.annotationHeads.clear()
    dm._setActive(701)
  })

  await page.locator('#dataset-trigger').fill('Loading_Project_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()

  await expect(page.locator('#toast-container .toast.error')).toContainText('caption versions')
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(0)
  expect(previewCalls).toBe(0)
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      caption: dm.captions.get(701),
      captionEdit: dm.captionEdits.get(701),
    }
  })).toEqual({
    commonTags: '',
    caption: '1girl, smile',
    captionEdit: undefined,
  })
})

test('trigger quickfill refuses a named project when annotation heads failed to load', async ({ page }) => {
  let previewCalls = 0
  await page.route('**/api/tags/export-preview', async (route) => {
    previewCalls += 1
    await route.fulfill({ json: { results: [] } })
  })
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm._activeProject = { id: 93, revision: 1 }
    dm._annotationHeadsStatus = 'error'
    dm.annotationHeads.clear()
    dm._setActive(701)
  })

  await page.locator('#dataset-trigger').fill('Failed_Project_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()

  await expect(page.locator('#toast-container .toast.error')).toContainText('caption versions')
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(0)
  expect(previewCalls).toBe(0)
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      caption: dm.captions.get(701),
      captionEdit: dm.captionEdits.get(701),
    }
  })).toEqual({
    commonTags: '',
    caption: '1girl, smile',
    captionEdit: undefined,
  })
})

test('project switch cannot quickfill from the previous project annotation heads', async ({ page }) => {
  await page.route('**/api/annotations/projects/202/training-captions/heads**', async (route) => {
    await route.fulfill({
      json: {
        project_id: 202,
        items: [],
        has_more: false,
        next_after_subject_id: null,
      },
    })
  })
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm._activeProject = { id: 201, revision: 3 }
    dm._annotationHeadsStatus = 'ready'
    dm._annotationHeadsOwner = { project_id: 201, project_revision: 3 }
    dm.annotationHeads = new Map([
      [701, {
        active_revision: {
          id: 2701,
          content_sha256: 'b'.repeat(64),
          content: {
            content_version: 1,
            booru_caption: 'project a saved caption',
            nl_caption: '',
            caption_type: 'booru',
          },
        },
      }],
    ])
    dm._fetchMissingMeta = () => new Promise<void>((resolve) => {
      ;(window as any).__releaseProjectSwitchMeta = resolve
    })
    dm._fetchMissingCaptions = async () => ({ status: 'applied', error: '' })
    const nextProject = {
      id: 202,
      name: 'Project B',
      revision: 4,
      archived_at: null,
      created_at: '2026-07-27T00:00:00Z',
      updated_at: '2026-07-27T00:00:00Z',
      settings: dm._defaultProjectSettings(),
      items: [{
        position: 0,
        item_type: 'library',
        source_image_id: 701,
        image_id: 701,
        missing: false,
      }],
      missing_image_ids: [],
    }
    ;(window as any).__projectSwitchPromise = dm._replaceQueueWithProject(nextProject)
  })
  await expect.poll(() => page.evaluate(() => (window as any).DatasetMaker._activeProject?.id))
    .toBe(202)
  await expect.poll(() => page.evaluate(() => typeof (window as any).__releaseProjectSwitchMeta))
    .toBe('function')

  await page.locator('#dataset-trigger').fill('Project_B_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()

  await expect(page.locator('#toast-container .toast.error')).toContainText('caption versions')
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(0)
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
      captionEdit: dm.captionEdits.get(701),
    }
  })).toEqual({ commonTags: '', captionEdit: undefined })

  await page.evaluate(async () => {
    ;(window as any).__releaseProjectSwitchMeta()
    await (window as any).__projectSwitchPromise
  })
})

test('failed project validation preserves the active project annotation heads', async ({ page }) => {
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm.imageIds = [701]
    dm._activeProject = { id: 211, revision: 5 }
    dm._annotationHeadsStatus = 'ready'
    dm._annotationHeadsOwner = { project_id: 211, project_revision: 5 }
    dm.annotationHeads = new Map([
      [701, {
        active_revision: {
          id: 3701,
          subject_id: 4701,
          annotation_kind: 'training_caption',
          parent_revision_id: null,
          restored_from_revision_id: null,
          content_sha256: 'c'.repeat(64),
          content: {
            content_version: 1,
            booru_caption: 'project a durable caption',
            nl_caption: '',
            caption_type: 'booru',
          },
          source: 'manual',
          provider: null,
          model: null,
          author_class: 'user',
          created_at: '2026-07-27T00:00:00Z',
        },
      }],
    ])
    dm._renderQueue()
    dm._setActive(701)
    const invalidProject = {
      id: 212,
      name: 'Invalid Project B',
      revision: 1,
      archived_at: null,
      created_at: '2026-07-27T00:00:00Z',
      updated_at: '2026-07-27T00:00:00Z',
      settings: { settings_version: 1 },
      items: [{
        position: 0,
        item_type: 'library',
        source_image_id: 701,
        image_id: 701,
        missing: false,
      }],
      missing_image_ids: [],
    }
    ;(window as any).__failedProjectSwitch = dm._replaceQueueWithProject(invalidProject)
      .then(() => ({ error: '' }))
      .catch((error: unknown) => ({ error: String(error) }))
  })

  const failedSwitch = await page.evaluate(async () => (window as any).__failedProjectSwitch)
  expect(failedSwitch.error).toContain('settings')
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      activeProject: dm._activeProject,
      status: dm._annotationHeadsStatus,
      owner: dm._annotationHeadsOwner,
      activeRevisionId: dm.annotationHeads.get(701)?.active_revision?.id,
      selection: dm._buildExportPayload().annotation_selections['701'],
    }
  })).toEqual({
    activeProject: { id: 211, revision: 5 },
    status: 'ready',
    owner: { project_id: 211, project_revision: 5 },
    activeRevisionId: 3701,
    selection: { kind: 'revision_ref', revision_id: 3701 },
  })

  await page.locator('#dataset-trigger').fill('Project_A_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect(page.locator('#toast-container .toast.success')).toContainText('Project_A_Token')
  await expect.poll(() => page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return dm._buildExportPayload().annotation_selections['701']
  })).toEqual({
    kind: 'frozen_draft',
    content: {
      content_version: 1,
      booru_caption: 'Project_A_Token, project a durable caption',
      nl_caption: '',
      caption_type: 'booru',
    },
  })
})

test('annotation head changes supersede an in-flight trigger quickfill', async ({ page }) => {
  let requestStarted = false
  const requestGate: { release?: () => void } = {}
  await page.route('**/api/tags/export-preview', async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>
    const trigger = String(body.trigger || '').trim()
    const imageIds = Array.isArray(body.image_ids) ? body.image_ids.map(Number) : []
    if (trigger === 'Head_Race_Token') {
      requestStarted = true
      await new Promise<void>((resolve) => { requestGate.release = resolve })
    }
    await route.fulfill({
      json: {
        results: imageIds.map((imageId) => ({
          image_id: imageId,
          filename: `head-race-${imageId}.png`,
          rendered: `${trigger}, generated-${imageId}`,
        })),
      },
    })
  })
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm._activeProject = { id: 92, revision: 1 }
    dm._annotationHeadsStatus = 'ready'
    dm._annotationHeadsOwner = { project_id: 92, project_revision: 1 }
    dm.annotationHeads.clear()
    dm._setActive(701)
  })

  await page.locator('#dataset-trigger').fill('Head_Race_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect.poll(() => requestStarted).toBe(true)
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm.annotationHeads.set(701, {
      active_revision: {
        id: 1901,
        content_sha256: 'a'.repeat(64),
        content: {
          content_version: 1,
          booru_caption: 'saved, caption',
          nl_caption: '',
          caption_type: 'booru',
        },
      },
    })
  })
  if (!requestGate.release) throw new Error('Trigger quickfill request was not held')
  requestGate.release()

  await expect(page.locator('#toast-container .toast.error')).toContainText('newer input was kept')
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(0)
  expect(await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return {
      generatedCaption: dm.captions.get(701),
      captionEdit: dm.captionEdits.get(701),
      commonTags: (document.getElementById('dataset-common-tags') as HTMLTextAreaElement).value,
    }
  })).toEqual({
    generatedCaption: '1girl, smile',
    captionEdit: undefined,
    commonTags: '',
  })
})

test('trigger quickfill creates a visible NL draft over an active project revision', async ({ page }) => {
  await stubTriggerCaptionPreview(page, 'saved-nl-revision')
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm._activeProject = { id: 90, revision: 1 }
    dm._annotationHeadsStatus = 'ready'
    dm._annotationHeadsOwner = { project_id: 90, project_revision: 1 }
    dm.annotationHeads = new Map([
      [701, {
        active_revision: {
          id: 1703,
          source: 'manual',
          author_class: 'user',
          provider: null,
          model: null,
          restored_from_revision_id: null,
          created_at: '2026-07-27T00:00:00Z',
          content: {
            content_version: 1,
            booru_caption: 'saved, tag',
            nl_caption: 'A saved natural-language caption.',
            caption_type: 'nl',
          },
        },
      }],
    ])
    dm._setActive(701)
  })

  await expect(page.locator('#dataset-editor-textarea')).toBeHidden()
  await page.locator('#dataset-trigger').fill('Project_NL_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect(page.locator('#dataset-editor-nl')).toHaveValue(
    'Project_NL_Token, A saved natural-language caption.',
  )
  await expect.poll(() => page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return dm._buildExportPayload().annotation_selections['701']
  })).toEqual({
    kind: 'frozen_draft',
    content: {
      content_version: 1,
      booru_caption: 'saved, tag',
      nl_caption: 'Project_NL_Token, A saved natural-language caption.',
      caption_type: 'nl',
    },
  })
})

test('trigger quickfill respects an explicitly empty active project revision', async ({ page }) => {
  await stubTriggerCaptionPreview(page, 'empty-revision')
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm._activeProject = { id: 89, revision: 1 }
    dm._annotationHeadsStatus = 'ready'
    dm._annotationHeadsOwner = { project_id: 89, project_revision: 1 }
    dm.annotationHeads = new Map([
      [701, {
        active_revision: {
          id: 1702,
          source: 'manual',
          author_class: 'user',
          provider: null,
          model: null,
          restored_from_revision_id: null,
          created_at: '2026-07-27T00:00:00Z',
          content: {
            content_version: 1,
            booru_caption: '',
            nl_caption: '',
            caption_type: 'booru',
          },
        },
      }],
    ])
    dm.captions.set(701, 'old generated')
    dm._setActive(701)
  })

  await page.locator('#dataset-trigger').fill('Empty_Revision_Token')
  await page.locator('#btn-dataset-quickfill-trigger').click()
  await expect.poll(() => page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    return dm._buildExportPayload().annotation_selections['701']
  })).toEqual({
    kind: 'frozen_draft',
    content: {
      content_version: 1,
      booru_caption: 'Empty_Revision_Token',
      nl_caption: '',
      caption_type: 'booru',
    },
  })
})

test('NL-only drafts warn before unload when durable storage is unavailable', async ({ page }) => {
  await page.addInitScript(() => {
    const originalSetItem = Storage.prototype.setItem
    Storage.prototype.setItem = function (key: string, value: string) {
      if (this === window.localStorage && key.startsWith('sd-image-sorter-dataset')) {
        throw new DOMException('Quota exceeded', 'QuotaExceededError')
      }
      return originalSetItem.call(this, key, value)
    }
  })
  await seedDatasetQueue(page)
  await page.locator('#dataset-tab-workbench').click()
  await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm.imageIds = [701]
    dm.captionEdits.clear()
    dm.nlEdits.clear()
    dm.captionType.clear()
    dm._renderQueue()
    dm._setActive(701)
  })
  await page.locator('#dataset-caption-type .dataset-caption-type-btn[data-caption-type="nl"]').click()
  await page.locator('#dataset-editor-nl').fill('A durable natural-language draft.')

  const unloadState = await page.evaluate(() => {
    const event = new Event('beforeunload', { cancelable: true })
    const dispatchResult = window.dispatchEvent(event)
    const draft = JSON.parse(sessionStorage.getItem('sd-image-sorter-dataset-session') || 'null')
    return {
      defaultPrevented: event.defaultPrevented,
      dispatchResult,
      nlEdit: draft?.nlEdits?.['701'],
      captionType: draft?.captionType?.['701'],
    }
  })
  expect(unloadState).toEqual({
    defaultPrevented: true,
    dispatchResult: false,
    nlEdit: 'A durable natural-language draft.',
    captionType: 'nl',
  })
})

test('_buildQueueItem DOM contract: statuses, badges, order, caption-type chips', async ({ page }) => {
  await seedDatasetQueue(page)

  const snap = await page.evaluate(() => {
    const dm = (window as any).DatasetMaker
    dm.captionType.set(702, 'nl')
    dm.captionType.set(703, 'both')
    const pick = (id: number, orderIndex: number) => {
      const node = dm._buildQueueItem(id, orderIndex) as HTMLElement
      return {
        className: node.className,
        imageId: node.dataset.imageId,
        queueOrder: node.dataset.queueOrder,
        role: node.getAttribute('role'),
        filename: node.querySelector('.dataset-queue-filename')?.textContent ?? null,
        idLabel: node.querySelector('.dataset-queue-id')?.textContent ?? null,
        orderBadge: node.querySelector('.dataset-queue-order')?.textContent ?? null,
        badgeClass: node.querySelector('.dataset-queue-badge')?.className ?? null,
        hasSelectToggle: !!node.querySelector('.dataset-queue-select-toggle[role="checkbox"]'),
        hasThumb: !!node.querySelector('img.dataset-queue-thumb'),
        chip: node.querySelector('.dataset-queue-captype')?.textContent ?? null,
        chipClass: node.querySelector('.dataset-queue-captype')?.className ?? null,
        chipInMeta: !!node.querySelector('.dataset-queue-meta .dataset-queue-captype'),
      }
    }
    return { a: pick(701, 0), nl: pick(702, 1), edited: pick(703, 2), untagged: pick(704, 3) }
  })

  expect(snap.a.className).toContain('dataset-queue-item')
  expect(snap.a.className).toContain('status-tagged')
  expect(snap.a.imageId).toBe('701')
  expect(snap.a.queueOrder).toBe('1')
  expect(snap.a.orderBadge).toBe('1')
  expect(snap.a.role).toBe('button')
  expect(snap.a.filename).toBe('core-a.png')
  expect(snap.a.idLabel).toBe('#701')
  expect(snap.a.hasSelectToggle).toBe(true)
  expect(snap.a.hasThumb).toBe(true)
  expect(snap.a.badgeClass).toContain('dataset-queue-badge-tagged')
  // booru-typed images get NO caption-type chip.
  expect(snap.a.chip).toBeNull()

  expect(snap.nl.chip).toBe('NL')
  expect(snap.nl.chipClass).toContain('dataset-queue-captype-nl')
  expect(snap.nl.chipInMeta).toBe(true)

  expect(snap.edited.className).toContain('status-edited')
  expect(snap.edited.badgeClass).toContain('dataset-queue-badge-edited')
  expect(snap.edited.chip).toBe('B+N')
  expect(snap.edited.chipClass).toContain('dataset-queue-captype-both')

  expect(snap.untagged.className).toContain('status-untagged')
  expect(snap.untagged.badgeClass).toContain('dataset-queue-badge-untagged')
})

test('local items: queue decoration + local _setActive branch (no flush, no diff)', async ({ page }) => {
  await seedDatasetQueue(page)
  await seedLocalItem(page)
  await page.locator('#dataset-tab-workbench').click()

  const item = await page.evaluate((id) => {
    const dm = (window as any).DatasetMaker
    const node = dm._buildQueueItem(id, 4) as HTMLElement
    return {
      className: node.className,
      idLabel: node.querySelector('.dataset-queue-id')?.textContent ?? null,
    }
  }, LOCAL_ID)
  expect(item.className).toContain('source-local')
  // A local item relabels the id line with its filename instead of the gallery
  // default `#<id>`; the source is carried by the class above, not by a glyph.
  expect(item.idLabel).toBe('img_001.png')

  // Make the caption diff visible from the edited gallery image first.
  await page.evaluate(() => (window as any).DatasetMaker._setActive(703))
  await expect(page.locator('#dataset-caption-diff')).toContainText('+1 tag')

  const res = await page.evaluate((id) => {
    const dm = (window as any).DatasetMaker
    const ta = document.getElementById('dataset-editor-textarea') as HTMLTextAreaElement
    // Pending (un-debounced) edit on 703, then switch to the LOCAL item.
    ta.value = '1girl, frown, hat, bow'
    ta.dispatchEvent(new Event('input', { bubbles: true }))
    dm._setActive(id)
    return {
      activeId: dm.activeId,
      // The local branch never flushed pending gallery edits synchronously
      // (the debounce timer still commits later on its own).
      captionEditNow: dm.captionEdits.get(703),
      taValue: ta.value,
      filename: document.getElementById('dataset-editor-filename')?.textContent ?? null,
      diffHidden: (document.getElementById('dataset-caption-diff') as HTMLElement).hidden,
    }
  }, LOCAL_ID)

  expect(res.activeId).toBe(LOCAL_ID)
  expect(res.captionEditNow).toBe('1girl, frown, hat')
  expect(res.taValue).toBe('local caption, tree')
  expect(res.filename).toBe('img_001.png')
  // Gallery-only hook: the diff indicator is untouched by a local switch.
  expect(res.diffHidden).toBe(false)
})

test('_buildExportPayload splits gallery ids, local paths, and manifest tokens', async ({ page }) => {
  await seedDatasetQueue(page)
  await seedLocalItem(page)

  const payload = await page.evaluate(({ manifestId, manifestPath, localId, localPath }) => {
    const dm = (window as any).DatasetMaker
    // A manifest-tracked local item exports via its scan token, not image_paths.
    dm.imageIds.push(manifestId)
    dm.localItemPaths.set(manifestId, manifestPath)
    dm.meta.set(manifestId, {
      source: 'local',
      abs_path: manifestPath,
      filename: 'img_002.png',
      folder_scan_token: 'tok-1',
    })
    dm.localManifestTokens.set('tok-1', {
      scan_token: 'tok-1',
      folder_path: 'C:/fake/manifested',
      total: 5,
      excludedPaths: new Set(['C:/fake/manifested/skip.png']),
    })
    dm.captionEdits.set(701, 'edited-a')
    dm.captionEdits.set(localId, 'edited-local')
    dm.captionType.set(702, 'nl')
    dm.captionType.set(localId, 'both')
    dm.nlEdits.set(702, 'nl sentence for b')
    dm.nlEdits.set(localId, 'nl sentence for local')
    return dm._buildExportPayload()
  }, { manifestId: MANIFEST_LOCAL_ID, manifestPath: MANIFEST_PATH, localId: LOCAL_ID, localPath: LOCAL_PATH })

  expect(payload.image_ids).toEqual([701, 702, 703, 704])
  expect(payload.image_paths).toEqual([LOCAL_PATH])
  expect(payload.dataset_scan_tokens).toEqual([
    { scan_token: 'tok-1', exclude_paths: ['C:/fake/manifested/skip.png'] },
  ])
  // Dual-key overrides: str(image_id) for gallery rows, abs path for local.
  expect(payload.image_overrides).toEqual({
    '701': 'edited-a',
    '703': '1girl, frown, hat',
    [LOCAL_PATH]: 'edited-local',
  })
  expect(payload.image_types).toEqual({ '702': 'nl', [LOCAL_PATH]: 'both' })
  expect(payload.image_nl_overrides).toEqual({
    '702': 'nl sentence for b',
    [LOCAL_PATH]: 'nl sentence for local',
  })
})
