import fs from 'fs'
import path from 'path'
import { test, expect, type APIRequestContext, type Page } from '../fixtures/click-ledger'

const SAMPLE_IMAGE = path.resolve(__dirname, '../../../backend/favorites/ComfyUI_00208_.png')

// Deterministic multi-generator review dataset built by
// `python scripts/build_review_dataset.py`. Each fixture targets a specific
// parser path so the live Reader exercise does not collapse into a single
// ComfyUI happy-path check.
const DATASET_DIR = path.resolve(__dirname, '../../../backend/.tmp/release_review_dataset')

interface ReaderFixture {
  file: string
  label: string
  expectGenerator?: string | RegExp
  expectPromptContains?: string
  expectCheckpointContains?: string
  expectStatus?: RegExp
  skipIfMissingMetadata?: boolean
}

const FIXTURES: ReaderFixture[] = [
  {
    file: 'comfy_good.png',
    label: 'ComfyUI workflow JSON',
    expectGenerator: 'ComfyUI',
    expectPromptContains: 'masterpiece',
    expectCheckpointContains: 'v304_comfy',
  },
  {
    file: 'nai_good.png',
    label: 'NovelAI Comment JSON',
    expectGenerator: 'NovelAI',
    expectPromptContains: 'fantasy castle',
  },
  {
    file: 'webui_good.png',
    label: 'A1111 / WebUI parameters',
    expectGenerator: 'WebUI',
    expectPromptContains: 'portrait',
    expectCheckpointContains: 'v304_webui',
  },
  {
    file: 'forge_good.png',
    label: 'Forge parameters',
    expectGenerator: /Forge|WebUI/,
    expectPromptContains: 'landscape',
    expectCheckpointContains: 'v304_forge',
  },
  {
    file: 'webp_good.webp',
    label: 'Plain WebP without SD metadata',
    skipIfMissingMetadata: true,
  },
  {
    file: 'no_metadata.png',
    label: 'PNG without any SD metadata',
    skipIfMissingMetadata: true,
  },
]

async function openReaderView(page: Page) {
  await page.goto('/')
  await page.waitForLoadState('domcontentloaded')
  await page.evaluate(() => {
    const view = document.getElementById('view-reader')
    if (view) {
      document.querySelectorAll('.view').forEach((node) => {
        if (node !== view) {
          ;(node as HTMLElement).style.display = 'none'
        }
      })
      ;(view as HTMLElement).style.display = 'flex'
      view.classList.add('active')
    }
    document.getElementById('reader-tool-panel-reader')?.classList.add('active')
  })
}

function datasetAvailable(): boolean {
  return fs.existsSync(DATASET_DIR) && fs.existsSync(path.join(DATASET_DIR, 'comfy_good.png'))
}

async function parseImageViaApi(request: APIRequestContext, imagePath: string) {
  const response = await request.post('/api/parse-image', {
    multipart: {
      file: {
        name: path.basename(imagePath),
        mimeType: 'image/png',
        buffer: fs.readFileSync(imagePath),
      },
    },
  })

  expect(response.ok()).toBeTruthy()
  return response.json()
}

test.describe('Image Reader live parse', () => {
  test('file input parses real ComfyUI metadata and clipboard paste keeps the parsed content while showing the warning', async ({
    page,
  }) => {
    test.skip(!fs.existsSync(SAMPLE_IMAGE), 'Live metadata sample is missing in this workspace')

    await openReaderView(page)

    await page.setInputFiles('#reader-file-input', SAMPLE_IMAGE)

    await expect(page.locator('#reader-generator')).toHaveText('ComfyUI', { timeout: 10000 })
    await expect(page.locator('#reader-prompt-text')).toContainText('stelle', { timeout: 10000 })
    await expect(page.locator('#reader-checkpoint')).toContainText('z_image_turbo_bf16', { timeout: 10000 })

    const imageBase64 = fs.readFileSync(SAMPLE_IMAGE).toString('base64')
    await page.evaluate((b64) => {
      const binary = atob(b64)
      const bytes = new Uint8Array(binary.length)
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
      const file = new File([bytes], 'clipboard-sample.png', { type: 'image/png' })
      const dt = new DataTransfer()
      dt.items.add(file)
      const evt = new ClipboardEvent('paste', {
        clipboardData: dt,
        bubbles: true,
        cancelable: true,
      } as ClipboardEventInit)
      document.dispatchEvent(evt)
    }, imageBase64)

    await expect(page.locator('#reader-generator')).toHaveText('ComfyUI', { timeout: 10000 })
    await expect(page.locator('#reader-prompt-text')).toContainText('stelle', { timeout: 10000 })
    await expect(page.locator('#reader-checkpoint')).toContainText('z_image_turbo_bf16', { timeout: 10000 })
    await expect(page.locator('#reader-status')).toContainText(
      /prompt or model details may be incomplete|提示词或模型信息可能不完整/,
      {
        timeout: 10000,
      },
    )
  })

  test('drag-drop parses the same real ComfyUI metadata path as file upload', async ({ page }) => {
    test.skip(!fs.existsSync(SAMPLE_IMAGE), 'Live metadata sample is missing in this workspace')

    await openReaderView(page)

    const imageBase64 = fs.readFileSync(SAMPLE_IMAGE).toString('base64')
    await page.evaluate((b64) => {
      const binary = atob(b64)
      const bytes = new Uint8Array(binary.length)
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
      const file = new File([bytes], 'drag-drop-sample.png', { type: 'image/png' })
      const dt = new DataTransfer()
      dt.items.add(file)
      const dropZone = document.getElementById('reader-drop-zone')
      const evt = new DragEvent('drop', {
        dataTransfer: dt,
        bubbles: true,
        cancelable: true,
      } as DragEventInit)
      dropZone?.dispatchEvent(evt)
    }, imageBase64)

    await expect(page.locator('#reader-generator')).toHaveText('ComfyUI', { timeout: 10000 })
    await expect(page.locator('#reader-prompt-text')).toContainText('stelle', { timeout: 10000 })
    await expect(page.locator('#reader-checkpoint')).toContainText('z_image_turbo_bf16', { timeout: 10000 })
  })

  test('metadata editor saves a copy and same-path overwrite confirms before any 409 round-trip', async ({
    page,
    request,
  }) => {
    test.skip(!fs.existsSync(SAMPLE_IMAGE), 'Live metadata sample is missing in this workspace')
    test.setTimeout(120000)

    const outputPath = path.resolve(__dirname, '../../../.tmp/e2e-output/reader-e2e-edited.png')
    fs.mkdirSync(path.dirname(outputPath), { recursive: true })
    fs.rmSync(outputPath, { force: true })

    const saveStatuses: number[] = []
    const conflictResponses: string[] = []
    const consoleErrors: string[] = []
    const pageErrors: string[] = []

    page.on('response', (response) => {
      if (!response.url().includes('/api/image-metadata/save-edited')) return
      saveStatuses.push(response.status())
      if (response.status() === 409) {
        conflictResponses.push(response.url())
      }
    })
    page.on('console', (message) => {
      if (message.type() === 'error') {
        consoleErrors.push(message.text())
      }
    })
    page.on('pageerror', (error) => {
      pageErrors.push(String(error))
    })

    await openReaderView(page)
    await page.setInputFiles('#reader-file-input', SAMPLE_IMAGE)

    await expect(page.locator('#reader-generator')).toHaveText('ComfyUI', { timeout: 10000 })
    await expect(page.locator('#reader-metadata-editor')).toBeVisible({ timeout: 10000 })
    if (!(await page.locator('#reader-editor-body').isVisible().catch(() => false))) {
      await page.locator('#reader-metadata-editor .reader-section-toggle').click()
    }
    await expect(page.locator('#reader-editor-body')).toBeVisible({ timeout: 10000 })

    await page.locator('#reader-edit-prompt').fill('browser real test prompt v1')
    await page.locator('#reader-edit-negative').fill('browser real test negative v1')
    await page.locator('#reader-edit-output-path').fill(outputPath)
    await page.locator('#reader-save-metadata-as').click()

    await expect.poll(() => fs.existsSync(outputPath), { timeout: 30000 }).toBe(true)
    await expect.poll(() => saveStatuses.join(','), { timeout: 30000 }).toBe('200')

    const firstSaved = await parseImageViaApi(request, outputPath)
    // Reader still writes an A1111 parameters blob, but a remaining ComfyUI
    // graph must keep generator=comfyui. The edited prompt is executed text.
    expect(firstSaved.generator).toBe('comfyui')
    expect(firstSaved.prompt).toBe('browser real test prompt v1')
    expect(firstSaved.negative_prompt).toBe('browser real test negative v1')
    expect(String(firstSaved.checkpoint || '')).toContain('z_image_turbo_bf16')

    saveStatuses.length = 0
    conflictResponses.length = 0
    consoleErrors.length = 0
    pageErrors.length = 0

    await page.locator('#reader-edit-prompt').fill('browser real test prompt v2 overwrite')
    await page.locator('#reader-edit-negative').fill('browser real test negative v2 overwrite')
    await page.locator('#reader-save-metadata-as').click()

    const confirmModal = page.locator('#confirm-modal')
    await expect(confirmModal).toHaveClass(/visible/, { timeout: 10000 })
    await expect(page.locator('#confirm-title')).toContainText(/Replace this file\?|替换|覆盖/i)
    await expect(page.locator('#confirm-message')).toContainText(outputPath)

    await page.waitForTimeout(800)
    expect(saveStatuses).toEqual([])

    await page.locator('#btn-confirm-ok').click()
    await expect.poll(() => saveStatuses.join(','), { timeout: 30000 }).toBe('200')

    await expect.poll(async () => {
      const parsed = await parseImageViaApi(request, outputPath)
      return parsed.prompt
    }, { timeout: 30000 }).toBe('browser real test prompt v2 overwrite')

    const overwritten = await parseImageViaApi(request, outputPath)
    expect(overwritten.negative_prompt).toBe('browser real test negative v2 overwrite')
    expect(conflictResponses).toEqual([])
    expect(consoleErrors.filter((entry) => entry.includes('409') || entry.includes('Conflict'))).toEqual([])
    expect(pageErrors).toEqual([])
  })
})

test.describe('Image Reader multi-generator live coverage', () => {
  test.beforeAll(() => {
    if (!datasetAvailable()) {
      // eslint-disable-next-line no-console
      console.warn(
        `[reader-live] Release review dataset not found at ${DATASET_DIR}. ` +
          'Run `python scripts/build_review_dataset.py` to generate the 8-image fixture set.',
      )
    }
  })

  for (const fixture of FIXTURES) {
    test(`parses ${fixture.label} via real /api/parse-image`, async ({ page }) => {
      const fixturePath = path.join(DATASET_DIR, fixture.file)
      test.skip(!fs.existsSync(fixturePath), `Fixture missing: ${fixturePath}`)

      await openReaderView(page)
      await page.setInputFiles('#reader-file-input', fixturePath)

      // Wait for the real backend /api/parse-image to complete — no mocks.
      await page.waitForFunction(
        () => {
          const status = document.getElementById('reader-status')
          const result = document.getElementById('reader-result-panel')
          const statusVisible = status && status.style.display !== 'none' && (status.textContent || '').length > 0
          const resultVisible = result && result.style.display !== 'none'
          return statusVisible || resultVisible
        },
        { timeout: 15000 },
      )

      if (fixture.expectGenerator) {
        const generatorLocator = page.locator('#reader-generator')
        if (fixture.expectGenerator instanceof RegExp) {
          await expect(generatorLocator).toHaveText(fixture.expectGenerator, { timeout: 10000 })
        } else {
          await expect(generatorLocator).toHaveText(fixture.expectGenerator, { timeout: 10000 })
        }
      }
      if (fixture.expectPromptContains) {
        await expect(page.locator('#reader-prompt-text')).toContainText(fixture.expectPromptContains, {
          timeout: 10000,
        })
      }
      if (fixture.expectCheckpointContains) {
        await expect(page.locator('#reader-checkpoint')).toContainText(fixture.expectCheckpointContains, {
          timeout: 10000,
        })
      }

      // For fixtures with no real SD metadata, at least confirm the app did not crash
      // and that either a generator label, a prompt, or the status element is visible.
      if (fixture.skipIfMissingMetadata) {
        const anyVisible = await page.evaluate(() => {
          const status = document.getElementById('reader-status')
          const result = document.getElementById('reader-result-panel')
          const statusOk = !!status && status.style.display !== 'none'
          const resultOk = !!result && result.style.display !== 'none'
          return statusOk || resultOk
        })
        expect(anyVisible).toBe(true)
      }
    })
  }

  test('rejects a truncated PNG without crashing and surfaces an error to the user', async ({ page }) => {
    const truncatedPath = path.join(DATASET_DIR, 'truncated.png')
    test.skip(!fs.existsSync(truncatedPath), `Fixture missing: ${truncatedPath}`)

    await openReaderView(page)

    const backendErrors: string[] = []
    page.on('response', async (res) => {
      if (res.url().includes('/api/parse-image') && !res.ok()) {
        backendErrors.push(`${res.status()} ${res.url()}`)
      }
    })

    await page.setInputFiles('#reader-file-input', truncatedPath)

    // Either the backend returns non-2xx (surfaced via the error status class) OR the
    // parser yields a stub with no metadata — both are acceptable as long as the tab
    // does not crash. We assert one of the two.
    await page.waitForFunction(
      () => {
        const status = document.getElementById('reader-status')
        if (!status) return false
        const cls = status.className || ''
        const visible = status.style.display !== 'none'
        return visible && (cls.includes('error') || cls.includes('warning') || !!status.textContent)
      },
      { timeout: 15000 },
    )

    // Confirm the tab is still responsive — the file input still exists in the DOM.
    await expect(page.locator('#reader-file-input')).toHaveCount(1)
  })

  test('rejects a garbage file without crashing and surfaces an error to the user', async ({ page }) => {
    const garbagePath = path.join(DATASET_DIR, 'garbage.png')
    test.skip(!fs.existsSync(garbagePath), `Fixture missing: ${garbagePath}`)

    await openReaderView(page)

    await page.setInputFiles('#reader-file-input', garbagePath)

    await page.waitForFunction(
      () => {
        const status = document.getElementById('reader-status')
        if (!status) return false
        const cls = status.className || ''
        const visible = status.style.display !== 'none'
        return visible && (cls.includes('error') || cls.includes('warning') || !!status.textContent)
      },
      { timeout: 15000 },
    )

    await expect(page.locator('#reader-file-input')).toHaveCount(1)
  })
})
