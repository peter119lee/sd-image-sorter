import fsSync from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'

import { expect, test, type Page } from '../fixtures/click-ledger'

/**
 * v3.4.1 UI regression coverage (2026-06-12):
 *
 * 1. Clear Gallery button — owner directive: must be visible at a glance on
 *    the gallery page (it previously hid inside the Import modal's advanced
 *    options). It now lives at the far-right end of .gallery-header.
 * 2. Filter presets — saveFilterPreset/loadFilterPreset/renderFilterPresets
 *    existed in app.js but had NO UI entry point (#filter-presets-list was
 *    never in the DOM). The filter modal now has a presets bar.
 * 3. WASD combo counter — #combo-display was dropped from index.html in the
 *    v2.6.0 markup restructure while its JS/CSS were kept, so the combo
 *    counter incremented invisibly. Restored inside .sort-image-container.
 */

test.describe.configure({ mode: 'serial' })

const repoRoot = path.resolve(__dirname, '..', '..', '..')

function commandExists(candidate: string): boolean {
  if (candidate.includes(path.sep) || candidate.includes('/')) {
    return fsSync.existsSync(candidate)
  }

  try {
    const lookupCommand = process.platform === 'win32' ? 'where' : 'which'
    return execFileSync(lookupCommand, [candidate], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim().length > 0
  } catch {
    return false
  }
}

const backendPythonCandidates = process.platform === 'win32' ? [
  path.join(repoRoot, 'backend', 'venv', 'Scripts', 'python.exe'),
  path.join(repoRoot, 'backend', 'venv', 'bin', 'python'),
  'python',
] : [
  path.join(repoRoot, 'backend', 'venv', 'bin', 'python'),
  'python3',
  'python',
  path.join(repoRoot, 'backend', 'venv', 'Scripts', 'python.exe'),
]

const backendPython = process.env.PW_BACKEND_PYTHON
  || backendPythonCandidates.find((candidate) => commandExists(candidate))
  || backendPythonCandidates[0]
const runtimeDatabasePath = process.env.SD_IMAGE_SORTER_DB_PATH
  || path.join(repoRoot, 'data', 'images.db')

const comboRoot = path.join(repoRoot, '.tmp', 'v341-combo')
const comboOut = path.join(comboRoot, 'out')
const COMBO_SEARCH_TOKEN = 'v341_combo_token_20260612'
const COMBO_IMAGE_COUNT = 4
const CLEAR_GALLERY_VIEWPORTS = [
  { width: 1366, height: 768 },
  { width: 1920, height: 1080 },
  { width: 2560, height: 1440 },
]

function runBackendScript(script: string) {
  return execFileSync(backendPython, ['-X', 'utf8', '-c', script], {
    cwd: repoRoot,
    stdio: 'pipe',
  }).toString('utf8').trim()
}

/**
 * (Re)creates a self-contained manual-sort fixture: 4 PNGs in
 * .tmp/v341-combo/inbox plus matching DB rows searchable by the combo token.
 * The whole fixture folder is wiped and rebuilt, so the fixture is
 * deterministic across runs and retries.
 */
function resetComboFixture() {
  const script = `
import shutil
import sqlite3
from pathlib import Path
from PIL import Image

repo_root = Path(${JSON.stringify(repoRoot)})
combo_root = repo_root / ".tmp" / "v341-combo"
inbox = combo_root / "inbox"
out = combo_root / "out"
shutil.rmtree(combo_root, ignore_errors=True)
inbox.mkdir(parents=True, exist_ok=True)
out.mkdir(parents=True, exist_ok=True)

token = ${JSON.stringify(COMBO_SEARCH_TOKEN)}
filenames = [f"v341-combo-{index}.png" for index in range(1, ${COMBO_IMAGE_COUNT} + 1)]
colors = [(255, 99, 71), (99, 255, 71), (71, 99, 255), (255, 215, 71)]

db_path = Path(${JSON.stringify(runtimeDatabasePath)})
with sqlite3.connect(db_path) as conn:
    cur = conn.cursor()
    for filename, color in zip(filenames, colors):
        image_path = (inbox / filename).resolve()
        Image.new("RGB", (96, 96), color=color).save(image_path)

        cur.execute("DELETE FROM tags WHERE image_id IN (SELECT id FROM images WHERE filename = ?)", (filename,))
        cur.execute("DELETE FROM image_prompt_tokens WHERE image_id IN (SELECT id FROM images WHERE filename = ?)", (filename,))
        cur.execute("DELETE FROM images WHERE filename = ?", (filename,))
        cur.execute(
            """
            INSERT INTO images (
                path, filename, generator, prompt, negative_prompt, metadata_json,
                width, height, file_size, source_size, source_mtime_ns,
                is_readable, read_error, metadata_status, created_at
            ) VALUES (?, ?, 'unknown', ?, '', NULL, 96, 96, ?, ?, ?, 1, NULL, 'complete', CURRENT_TIMESTAMP)
            """,
            (
                str(image_path), filename, token,
                image_path.stat().st_size, image_path.stat().st_size,
                image_path.stat().st_mtime_ns,
            ),
        )
        image_id = cur.lastrowid
        cur.execute(
            "INSERT OR IGNORE INTO image_prompt_tokens (image_id, token) VALUES (?, ?)",
            (image_id, token.lower().replace('_', ' ').strip()),
        )
    conn.commit()
print("ok")
`
  runBackendScript(script)
}

function cleanupComboFixtureRows() {
  const script = `
import sqlite3
from pathlib import Path

db_path = Path(${JSON.stringify(runtimeDatabasePath)})
filenames = tuple(f"v341-combo-{index}.png" for index in range(1, ${COMBO_IMAGE_COUNT} + 1))
placeholders = ",".join("?" for _ in filenames)
with sqlite3.connect(db_path) as conn:
    cur = conn.cursor()
    cur.execute(f"DELETE FROM tags WHERE image_id IN (SELECT id FROM images WHERE filename IN ({placeholders}))", filenames)
    cur.execute(f"DELETE FROM image_prompt_tokens WHERE image_id IN (SELECT id FROM images WHERE filename IN ({placeholders}))", filenames)
    cur.execute(f"DELETE FROM images WHERE filename IN ({placeholders})", filenames)
    conn.commit()
print("ok")
`
  runBackendScript(script)
}

async function openMainPage(page: Page) {
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect.poll(async () => {
    return await page.evaluate(() => {
      return Boolean(
        window.App
          && typeof window.App.loadImages === 'function'
          && window.App.AppState?.isLoading === false
      )
    })
  }).toBe(true)
}

async function openSortingManualView(page: Page) {
  await page.locator('.nav-tabs [data-view="sorting"]').first().click({ force: true })
  await expect(page.locator('#view-sorting.active')).toBeVisible()
  await page.locator('.sorting-sub-tab[data-sorting-sub="manual"]').click({ force: true })
  await expect(page.locator('#view-manual')).toBeVisible()
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('sd-image-sorter-lang', 'en')
  })
})

test('clear gallery button should be visible on the gallery page, not buried in the scan modal', async ({ page }) => {
  await openMainPage(page)

  // Owner directive (2026-06-12): the button must be seen at a glance on the
  // gallery page — no modal, no <details> expansion required.
  const clearButton = page.locator('#btn-clear-db')
  await expect(clearButton).toHaveCount(1)
  await expect(clearButton).toBeVisible()

  // It lives in the gallery sidebar footer (still on the gallery page)...
  await expect(page.locator('.filter-sidebar-footer #btn-clear-db')).toHaveCount(1)
  await expect(page.locator('.gallery-header #btn-clear-db')).toHaveCount(0)
  // ...and is gone from the import/scan modal's danger zone.
  await expect(page.locator('#scan-modal #btn-clear-db')).toHaveCount(0)
  await expect(page.locator('#scan-modal .scan-danger-zone')).toHaveCount(0)

  // Dangerous op stays away from view toggles / sort on the thumbnail row.
  const separation = await page.evaluate(() => {
    const button = document.getElementById('btn-clear-db')
    const viewOptions = document.querySelector('.gallery-header .view-options')
    const sidebar = document.querySelector('.filter-sidebar')
    if (!button || !viewOptions || !sidebar) return null
    return {
      isInsideViewOptions: viewOptions.contains(button),
      isInSidebar: sidebar.contains(button),
    }
  })
  expect(separation).toEqual({
    isInsideViewOptions: false,
    isInSidebar: true,
  })

  // The existing handler + confirmation flow must still fire from the new
  // location (the busy-guard probes progress endpoints first, then confirms).
  await clearButton.click()
  await expect(page.locator('#confirm-modal.visible')).toBeVisible()
  await expect(page.locator('#confirm-modal')).toContainText('Clear current library')
  await page.locator('#btn-confirm-cancel').click()
  await expect(page.locator('#confirm-modal.visible')).toHaveCount(0)

  // The scan modal still opens fine without its old danger zone.
  await page.locator('#btn-scan').click()
  await expect(page.locator('#scan-modal.visible')).toBeVisible()
  await page.locator('#scan-advanced-options summary').click()
  await expect(page.locator('#scan-modal #btn-clear-db')).toHaveCount(0)
  await page.locator('#btn-cancel-scan').click()
  await expect(page.locator('#scan-modal.visible')).toHaveCount(0)
})

for (const viewport of CLEAR_GALLERY_VIEWPORTS) {
  test(`clear gallery fails closed when job state is unknown at ${viewport.width}x${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport)

    const consoleErrors: string[] = []
    const expectedProbeConsoleErrors: string[] = []
    const pageErrors: string[] = []
    const unexpectedHttpFailures: string[] = []
    let rejectScanProbe = false
    let scanProbeFailures = 0
    let clearRequests = 0

    page.on('console', (message) => {
      if (message.type() !== 'error') return
      if (rejectScanProbe && message.text() === 'Failed to load resource: the server responded with a status of 503 (Service Unavailable)') {
        expectedProbeConsoleErrors.push(message.text())
        return
      }
      consoleErrors.push(message.text())
    })
    page.on('pageerror', (error) => pageErrors.push(error.message))
    page.on('response', (response) => {
      if (response.status() < 400) return
      const url = new URL(response.url())
      if (rejectScanProbe && url.pathname === '/api/scan/progress' && response.status() === 503) return
      unexpectedHttpFailures.push(`${response.status()} ${url.pathname}`)
    })

    await page.route('**/api/scan/progress', async (route) => {
      if (!rejectScanProbe) {
        await route.continue()
        return
      }
      scanProbeFailures += 1
      await route.fulfill({
        status: 503,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'injected clear-gallery progress failure' }),
      })
    })
    await page.route('**/api/clear-gallery', async (route) => {
      clearRequests += 1
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', message: 'Gallery cleared' }),
      })
    })

    await openMainPage(page)
    rejectScanProbe = true
    await page.locator('#btn-clear-db').click()

    const errorToast = page.locator('#toast-container [role="alert"]')
      .filter({ hasText: 'Could not verify background job status' })
      .last()
    await expect(errorToast).toBeVisible()
    await expect(page.locator('#confirm-modal.visible')).toHaveCount(0)
    expect(scanProbeFailures).toBe(2)
    expect(clearRequests).toBe(0)

    const layout = await page.evaluate(() => {
      const button = document.getElementById('btn-clear-db')?.getBoundingClientRect()
      const toast = Array.from(document.querySelectorAll<HTMLElement>('#toast-container [role="alert"]'))
        .find((element) => element.textContent?.includes('Could not verify background job status'))
        ?.getBoundingClientRect()
      const toastMessage = Array.from(document.querySelectorAll<HTMLElement>('#toast-container [role="alert"]'))
        .find((element) => element.textContent?.includes('Could not verify background job status'))
        ?.querySelector<HTMLElement>('.toast-message')
        ?.getBoundingClientRect()
      return {
        horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        button: button ? { left: button.left, top: button.top, right: button.right, bottom: button.bottom } : null,
        toast: toast ? { left: toast.left, top: toast.top, right: toast.right, bottom: toast.bottom } : null,
        toastMessage: toastMessage
          ? { left: toastMessage.left, top: toastMessage.top, right: toastMessage.right, bottom: toastMessage.bottom }
          : null,
      }
    })
    expect(layout.horizontalOverflow).toBeLessThanOrEqual(0)
    expect(layout.button).not.toBeNull()
    expect(layout.button!.left).toBeGreaterThanOrEqual(0)
    expect(layout.button!.top).toBeGreaterThanOrEqual(0)
    expect(layout.button!.right).toBeLessThanOrEqual(viewport.width)
    expect(layout.button!.bottom).toBeLessThanOrEqual(viewport.height)
    expect(layout.toast).not.toBeNull()
    expect(layout.toast!.left).toBeGreaterThanOrEqual(0)
    expect(layout.toast!.top).toBeGreaterThanOrEqual(0)
    expect(layout.toast!.right).toBeLessThanOrEqual(viewport.width)
    expect(layout.toast!.bottom).toBeLessThanOrEqual(viewport.height)
    expect(layout.toastMessage).not.toBeNull()
    expect(layout.toastMessage!.left).toBeGreaterThanOrEqual(layout.toast!.left)
    expect(layout.toastMessage!.top).toBeGreaterThanOrEqual(layout.toast!.top)
    expect(layout.toastMessage!.right).toBeLessThanOrEqual(layout.toast!.right)
    expect(layout.toastMessage!.bottom).toBeLessThanOrEqual(layout.toast!.bottom)

    rejectScanProbe = false
    await page.locator('#btn-clear-db').click()
    await expect(page.locator('#confirm-modal.visible')).toBeVisible()
    await expect(page.locator('#confirm-modal')).toContainText('Clear current library')
    expect(clearRequests).toBe(0)
    await page.locator('#btn-confirm-cancel').click()
    await expect(page.locator('#confirm-modal.visible')).toHaveCount(0)

    expect(consoleErrors).toEqual([])
    expect(expectedProbeConsoleErrors).toHaveLength(2)
    expect(pageErrors).toEqual([])
    expect(unexpectedHttpFailures).toEqual([])
  })
}

test('clear gallery validates active, malformed tag, and malformed aesthetic progress', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 })

  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  const unexpectedHttpFailures: string[] = []
  let tagMode: 'idle' | 'queued' | 'running' | 'done' | 'malformed' = 'queued'
  let malformedAesthetic = false
  let tagProbeCalls = 0
  let aestheticProbeCalls = 0
  let clearRequests = 0

  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  page.on('pageerror', (error) => pageErrors.push(error.message))
  page.on('response', (response) => {
    if (response.status() < 400) return
    const url = new URL(response.url())
    unexpectedHttpFailures.push(`${response.status()} ${url.pathname}`)
  })

  await page.route('**/api/tag/progress', async (route) => {
    tagProbeCalls += 1
    const status = tagMode === 'malformed'
      ? 'mystery'
      : tagMode === 'queued'
        ? 'idle'
        : tagMode
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status,
        current: 0,
        total: 0,
        pipeline_queue: {
          total_queued: tagMode === 'queued' ? 1 : 0,
          queued: tagMode === 'queued'
            ? [{ queue_id: 'q1', kind: 'gallery', position: 1, enqueued_at: 1 }]
            : [],
          last_start_error: null,
        },
      }),
    })
  })
  await page.route('**/api/aesthetic/progress', async (route) => {
    aestheticProbeCalls += 1
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(malformedAesthetic
        ? { total: 0, completed: 0 }
        : { running: false, total: 0, completed: 0, errors: 0, error: null }),
    })
  })
  await page.route('**/api/clear-gallery', async (route) => {
    clearRequests += 1
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'ok', message: 'Gallery cleared' }),
    })
  })

  await openMainPage(page)
  await expect.poll(() => tagProbeCalls).toBeGreaterThan(0)
  await expect(page.locator('#bg-tag-progress')).toBeVisible()

  tagMode = 'idle'
  tagProbeCalls = 0
  aestheticProbeCalls = 0
  await page.locator('#btn-clear-db').click()
  await expect(page.locator('#confirm-modal.visible')).toBeVisible()
  await page.locator('#btn-confirm-cancel').click()
  await expect(page.locator('#confirm-modal.visible')).toHaveCount(0)

  tagMode = 'done'
  const queuedProbeCalls = tagProbeCalls
  await expect.poll(() => tagProbeCalls).toBeGreaterThan(queuedProbeCalls)
  await expect(page.locator('#bg-tag-progress')).toBeHidden()

  tagMode = 'running'
  tagProbeCalls = 0
  aestheticProbeCalls = 0
  await page.locator('#btn-clear-db').click()
  await expect(page.locator('#toast-container [role="alert"]')
    .filter({ hasText: 'Cannot clear gallery while scanning, tagging, or scoring is active or queued' })
    .last()).toBeVisible()
  await expect(page.locator('#confirm-modal.visible')).toHaveCount(0)
  expect(tagProbeCalls).toBe(1)
  expect(aestheticProbeCalls).toBe(1)

  tagMode = 'queued'
  tagProbeCalls = 0
  aestheticProbeCalls = 0
  await page.locator('#btn-clear-db').click()
  await expect(page.locator('#toast-container [role="alert"]')
    .filter({ hasText: 'Cannot clear gallery while scanning, tagging, or scoring is active or queued' })
    .last()).toBeVisible()
  await expect(page.locator('#confirm-modal.visible')).toHaveCount(0)
  expect(tagProbeCalls).toBe(1)
  expect(aestheticProbeCalls).toBe(1)

  tagMode = 'malformed'
  tagProbeCalls = 0
  aestheticProbeCalls = 0
  await page.locator('#btn-clear-db').click()
  await expect(page.locator('#toast-container [role="alert"]')
    .filter({ hasText: 'tag progress returned unexpected status "mystery"' })
    .last()).toBeVisible()
  await expect(page.locator('#confirm-modal.visible')).toHaveCount(0)
  expect(tagProbeCalls).toBe(2)
  expect(aestheticProbeCalls).toBe(2)

  tagMode = 'idle'
  malformedAesthetic = true
  tagProbeCalls = 0
  aestheticProbeCalls = 0
  await page.locator('#btn-clear-db').click()
  await expect(page.locator('#toast-container [role="alert"]')
    .filter({ hasText: 'aesthetic progress must include boolean running' })
    .last()).toBeVisible()
  await expect(page.locator('#confirm-modal.visible')).toHaveCount(0)
  expect(tagProbeCalls).toBe(2)
  expect(aestheticProbeCalls).toBe(2)

  malformedAesthetic = false
  await page.locator('#btn-clear-db').click()
  await expect(page.locator('#confirm-modal.visible')).toBeVisible()
  await expect(page.locator('#confirm-modal')).toContainText('Clear current library')

  tagMode = 'running'
  tagProbeCalls = 0
  aestheticProbeCalls = 0
  await page.locator('#btn-confirm-ok').click()
  await expect.poll(() => tagProbeCalls).toBe(1)
  await expect(page.locator('#toast-container [role="alert"]')
    .filter({ hasText: 'Cannot clear gallery while scanning, tagging, or scoring is active or queued' })
    .last()).toBeVisible()
  await expect(page.locator('#confirm-modal.visible')).toHaveCount(0)
  expect(clearRequests).toBe(0)

  tagMode = 'idle'
  await page.locator('#btn-clear-db').click()
  await expect(page.locator('#confirm-modal.visible')).toBeVisible()
  malformedAesthetic = true
  tagProbeCalls = 0
  aestheticProbeCalls = 0
  await page.locator('#btn-confirm-ok').click()
  await expect.poll(() => aestheticProbeCalls).toBe(2)
  await expect(page.locator('#toast-container [role="alert"]')
    .filter({ hasText: 'aesthetic progress must include boolean running' })
    .last()).toBeVisible()
  await expect(page.locator('#confirm-modal.visible')).toHaveCount(0)
  expect(clearRequests).toBe(0)

  malformedAesthetic = false
  await page.locator('#btn-clear-db').click()
  await expect(page.locator('#confirm-modal.visible')).toBeVisible()
  await page.locator('#btn-confirm-ok').click()
  await expect(page.locator('#confirm-modal.visible')).toHaveCount(0)
  await expect.poll(() => clearRequests).toBe(1)

  expect(consoleErrors).toEqual([])
  expect(pageErrors).toEqual([])
  expect(unexpectedHttpFailures).toEqual([])
})

test('filter presets should save, list, load, and delete through the filter modal', async ({ page }) => {
  await openMainPage(page)

  const presetName = `e2e preset ${Date.now()}`

  // Open the filter editor through its real entry point.
  await page.locator('#btn-open-filters').click()
  await expect(page.locator('#filter-modal.visible')).toBeVisible()

  // The presets bar is the new UI entry point for the pre-existing JS.
  await expect(page.locator('#filter-presets-bar')).toBeVisible()
  await expect(page.locator('#filter-presets-list')).toBeVisible()

  // Save a preset of the currently applied filters.
  await page.locator('#filter-preset-name').fill(presetName)
  await page.locator('#btn-save-filter-preset').click()
  await expect(page.locator('#toast-container')).toContainText('saved')
  await expect(page.locator('#filter-presets-list .preset-item')).toContainText(presetName)
  await expect(page.locator('#filter-preset-name')).toHaveValue('')

  // Load applies the preset and closes the modal.
  await page.locator(`[data-preset-action="load"][data-preset-name="${presetName}"]`).click()
  await expect(page.locator('#filter-modal.visible')).toHaveCount(0)
  await expect(page.locator('#toast-container')).toContainText('loaded')

  // The preset persists across modal reopen (localStorage-backed) and can be
  // deleted from the same list.
  await page.locator('#btn-open-filters').click()
  await expect(page.locator('#filter-modal.visible')).toBeVisible()
  await expect(page.locator('#filter-presets-list .preset-item')).toContainText(presetName)
  await page.locator(`[data-preset-action="delete"][data-preset-name="${presetName}"]`).click()
  await expect(page.locator('#filter-presets-list .preset-item')).toHaveCount(0)
  await expect(page.locator('#filter-presets-list')).toContainText('No saved presets')
})

test('WASD combo counter should become visible during sorting and reset on undo', async ({ page }) => {
  test.setTimeout(120000)
  resetComboFixture()

  await page.addInitScript((search) => {
    localStorage.setItem('manual_sort_filter_state_v1', JSON.stringify({
      generators: ['comfyui', 'nai', 'webui', 'forge', 'unknown'],
      ratings: ['general', 'sensitive', 'questionable', 'explicit'],
      tags: [],
      checkpoints: [],
      loras: [],
      prompts: [],
      artist: null,
      search,
      sortBy: 'newest',
      limit: 0,
      scope: 'library',
      minWidth: null,
      maxWidth: null,
      minHeight: null,
      maxHeight: null,
      aspectRatio: '',
      minAesthetic: null,
      maxAesthetic: null,
    }))
    localStorage.setItem('manual_sort_mode_v1', 'slot')
  }, COMBO_SEARCH_TOKEN)

  await openMainPage(page)
  await openSortingManualView(page)

  // Structural regression guard: the combo display markup is back in the DOM
  // (dropped in the v2.6.0 restructure) and starts hidden.
  const comboDisplay = page.locator('#combo-display')
  await expect(comboDisplay).toHaveCount(1)
  await expect(page.locator('.sort-image-container #combo-display .combo-number')).toHaveCount(1)
  await expect(comboDisplay).not.toHaveClass(/visible/)

  // Start a real slot-mode session moving everything to one folder.
  await page.locator('input[name="manual-sort-operation"][value="move"]').check({ force: true })
  await page.locator('.folder-path-input[data-key="d"]').fill(comboOut)
  await page.locator('#btn-start-sorting').click()
  await expect(page.locator('#confirm-modal.visible')).toBeVisible()
  await page.locator('#btn-confirm-ok').click()
  await expect(page.locator('#sort-interface')).toBeVisible()
  await expect(page.locator('#sort-progress-text')).toContainText(`0 / ${COMBO_IMAGE_COUNT}`)

  // The combo display only shows from 3 consecutive quick actions onward.
  await page.keyboard.press('D')
  await expect(page.locator('#sort-sorted-count')).toHaveText('1')
  await expect(comboDisplay).not.toHaveClass(/visible/)

  await page.keyboard.press('D')
  await expect(page.locator('#sort-sorted-count')).toHaveText('2')

  await page.keyboard.press('D')
  await expect(page.locator('#sort-sorted-count')).toHaveText('3')
  await expect(comboDisplay).toHaveClass(/visible/)
  await expect(page.locator('#combo-display .combo-number')).toHaveText('3')

  // Undo resets the combo and hides the display again.
  await page.keyboard.press('Z')
  await expect(page.locator('#sort-sorted-count')).toHaveText('2')
  await expect(comboDisplay).not.toHaveClass(/visible/)
})

test.afterAll(async () => {
  cleanupComboFixtureRows()
})
