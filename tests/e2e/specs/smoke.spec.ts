import os from 'os'
import path from 'path'
import { test, expect, type Page, type Route } from '../fixtures/click-ledger'

// Destination path used by auto-separate mocked tests. Overridable via env so
// runners on any platform can avoid writing to the author's absolute L:\ path.
const MOCK_AUTOSEP_DESTINATION =
  process.env.SD_TEST_MOVE_TARGET ?? path.join(os.tmpdir(), 'sd-image-sorter-mock-move')
const MOCK_MANUAL_SORT_DESTINATION =
  process.env.SD_TEST_MANUAL_SORT_TARGET ?? path.join(os.tmpdir(), 'sd-image-sorter-mock-manual-sort')

interface SelectionTokenRequestPayload {
  sortBy?: string
  chunkSize?: number
  excludedImageIds?: number[]
}

// AutoSep keeps its own filter state in localStorage under this key (see
// autosep.js:11 AUTOSEP_FILTER_STATE_KEY). Tests must seed this directly —
// writing to window.App.AppState.filters has no effect on AutoSep since v3.0.0.
const DEFAULT_AUTOSEP_FILTER_STATE = {
  generators: ['comfyui', 'nai', 'webui', 'forge', 'unknown'],
  ratings: ['general', 'sensitive', 'questionable', 'explicit'],
  tags: [] as string[],
  checkpoints: [] as string[],
  loras: [] as string[],
  prompts: [] as string[],
  artist: null as string | null,
  search: '',
  minWidth: null as number | null,
  maxWidth: null as number | null,
  minHeight: null as number | null,
  maxHeight: null as number | null,
  aspectRatio: '',
  minAesthetic: null as number | null,
  maxAesthetic: null as number | null,
}

async function seedAutoSepFilterState(page: Page, overrides: Partial<typeof DEFAULT_AUTOSEP_FILTER_STATE> = {}) {
  const state = { ...DEFAULT_AUTOSEP_FILTER_STATE, ...overrides }
  await page.addInitScript((payload) => {
    try {
      localStorage.setItem('autosep_filter_state_v1', JSON.stringify(payload))
    } catch (_) {
      // Ignore storage errors in the test bootstrap.
    }
  }, state)
}

async function seedAutoSepTagFilter(page: Page, tags: string[]) {
  await seedAutoSepFilterState(page, { tags })
}

const DEFAULT_MANUAL_SORT_FILTER_STATE = {
  generators: ['comfyui', 'nai', 'webui', 'forge', 'unknown'],
  ratings: ['general', 'sensitive', 'questionable', 'explicit'],
  tags: [] as string[],
  checkpoints: [] as string[],
  loras: [] as string[],
  prompts: [] as string[],
  artist: null as string | null,
  search: '',
  sortBy: 'newest',
  limit: 0,
  minWidth: null as number | null,
  maxWidth: null as number | null,
  minHeight: null as number | null,
  maxHeight: null as number | null,
  aspectRatio: '',
  minAesthetic: null as number | null,
  maxAesthetic: null as number | null,
}

async function seedManualSortFilterState(page: Page, overrides: Partial<typeof DEFAULT_MANUAL_SORT_FILTER_STATE> = {}) {
  const state = { ...DEFAULT_MANUAL_SORT_FILTER_STATE, ...overrides }
  await page.addInitScript((payload) => {
    try {
      localStorage.setItem('manual_sort_filter_state_v1', JSON.stringify(payload))
    } catch (_) {
      // Ignore storage errors in the test bootstrap.
    }
  }, state)
}

const MIXED_MASK_DATA_URL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAAjUlEQVR4nOXYsQ3AMBDDQJrw/it/VkhjOAGvVqFS0JoZyiRO4iRO4iRO4iRuv8icGAqLj5A4iZM4iZM4iZM4iZM4iZM4iZM4iZM4iZM4iZM4idt/OjBPkDiJkziJkziJkziJkziJkziJkziJkziJkziJkziJkziJkziJkziJkziJkziJkziJkzhvF7jtAUZuBIJ86O4rAAAAAElFTkSuQmCC'
const INLINE_MASK_CROP_DATA_URL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAwAAAAMCAYAAABWdVznAAAAHElEQVR4nGP8////fwYSABMpikFgVAMxYDiEEgA7PQQU42aXywAAAABJRU5ErkJggg=='

async function getGalleryScrollState(page: Page) {
  return page.evaluate(() => {
    const grid = document.getElementById('gallery-grid')
    if (!grid) return { scrollTop: 0, topVisibleId: null }

    const getScrollContainer = () => {
      let node = grid.parentElement
      while (node) {
        const style = window.getComputedStyle(node)
        const canScroll = /(auto|scroll|overlay)/.test(style.overflowY) && node.scrollHeight > node.clientHeight + 4
        if (canScroll) return node
        node = node.parentElement
      }

      return document.scrollingElement || document.documentElement
    }

    const scrollContainer = getScrollContainer()
    if (!grid || !scrollContainer) return { scrollTop: 0, topVisibleId: null }

    const scrollRect = scrollContainer.getBoundingClientRect()
    const items = Array.from(grid.querySelectorAll('.gallery-item'))

    let topVisibleItem = null
    let bestDistance = Number.POSITIVE_INFINITY
    for (const item of items) {
      const rect = item.getBoundingClientRect()
      if (rect.bottom <= scrollRect.top || rect.top >= scrollRect.bottom) continue

      const distance = Math.abs(rect.top - scrollRect.top)
      if (distance < bestDistance) {
        bestDistance = distance
        topVisibleItem = item
      }
    }

    return {
      scrollTop: scrollContainer.scrollTop,
      topVisibleId: topVisibleItem?.getAttribute('data-id') ?? null,
    }
  })
}

async function openSelectionPanelSection(page: Page, _sectionName: 'Export' | 'Remove') {
  // Aurora Phase 3 (#25a): Export/Remove actions moved from the left-panel
  // disclosures into the bottom action bar's More▾ menu. The bar (and hence
  // the menu) only exists once at least one image is selected.
  await expect(page.locator('#gallery-action-bar')).toBeVisible()
  const menu = page.locator('#gallery-action-more-menu')
  if (await menu.isHidden()) {
    await page.locator('#btn-gallery-action-more').click()
  }
  await expect(menu).toBeVisible()
}

async function getVisibleGalleryRects(page: Page, count = 4) {
  return page.evaluate((maxCount) => {
    const grid = document.getElementById('gallery-grid')
    if (!grid) return []

    return Array.from(grid.querySelectorAll('.gallery-item'))
      .slice(0, maxCount)
      .map((item) => {
        const rect = item.getBoundingClientRect()
        return {
          id: item.getAttribute('data-id'),
          top: rect.top,
          right: rect.right,
          bottom: rect.bottom,
          left: rect.left,
          width: rect.width,
          height: rect.height,
        }
      })
  }, count)
}

async function getFilterModalLayout(page: Page) {
  return page.evaluate(() => {
    const modal = document.querySelector('#filter-modal .filter-modal-shell')
    const primary = document.querySelector('#filter-modal .filter-column-primary')
    const secondary = document.querySelector('#filter-modal .filter-column-secondary')
    const actions = document.querySelector('#filter-modal .filter-modal-actions')

    if (!modal || !primary || !secondary || !actions) {
      return null
    }

    const rect = (element: Element) => {
      const box = element.getBoundingClientRect()
      return {
        top: box.top,
        right: box.right,
        bottom: box.bottom,
        left: box.left,
        width: box.width,
        height: box.height,
      }
    }

    return {
      modal: rect(modal),
      primary: rect(primary),
      secondary: rect(secondary),
      actions: rect(actions),
    }
  })
}

async function getActiveCensorCanvasSnapshot(page: Page) {
  return page.evaluate(() => {
    const noImage = document.getElementById('censor-no-image')
    const filename = document.getElementById('censor-filename')?.textContent?.trim()
    if (noImage && window.getComputedStyle(noImage).display !== 'none') {
      return null
    }
    if (!filename || filename === '-') {
      return null
    }

    const canvases = ['censor-canvas', 'censor-canvas-buffer']
      .map((id) => document.getElementById(id))
      .filter((canvas): canvas is HTMLCanvasElement => canvas instanceof HTMLCanvasElement && canvas.width > 0)

    if (canvases.length === 0) {
      return null
    }

    const activeCanvas = canvases.find((canvas) => {
      const style = window.getComputedStyle(canvas)
      return style.opacity !== '0' && style.pointerEvents !== 'none'
    }) ?? canvases[0]

    return activeCanvas.toDataURL('image/png')
  })
}

async function getActiveCensorCanvasBox(page: Page) {
  const activeCanvasId = await page.evaluate(() => {
    const canvases = ['censor-canvas', 'censor-canvas-buffer']
      .map((id) => document.getElementById(id))
      .filter((canvas): canvas is HTMLCanvasElement => canvas instanceof HTMLCanvasElement && canvas.width > 0)

    const activeCanvas = canvases.find((canvas) => {
      const style = window.getComputedStyle(canvas)
      return style.opacity !== '0' && style.pointerEvents !== 'none'
    }) ?? canvases[0]

    return activeCanvas?.id || null
  })

  if (!activeCanvasId) {
    return null
  }

  return page.locator(`#${activeCanvasId}`).boundingBox()
}

async function findFirstLoadableGalleryItemId(page: Page, maxCandidates = 48) {
  return page.evaluate(async (limit) => {
    const ids = Array.from(document.querySelectorAll('#gallery-grid .gallery-item[data-id]'))
      .map((item) => Number(item.getAttribute('data-id')))
      .filter((id) => Number.isFinite(id))
      .slice(0, limit)

    for (const id of ids) {
      try {
        const response = await fetch(`/api/image-thumbnail/${id}?size=64`, { cache: 'no-store' })
        if (response.ok) {
          return id
        }
      } catch (_) {
        // Ignore broken candidates and keep scanning.
      }
    }

    return null
  }, maxCandidates)
}

type EdgeBox = { top: number; right: number; bottom: number; left: number }

function rectsOverlap(a: EdgeBox, b: EdgeBox) {
  return Math.min(a.right, b.right) - Math.max(a.left, b.left) > 4 &&
    Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 4
}

function normalizeImageSrc(src: string | null) {
  if (!src) return src
  return src.split('?')[0]
}

async function getImageFingerprint(page: Page, src: string) {
  return page.evaluate(async (imageSrc) => {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image()
      el.onload = () => resolve(el)
      el.onerror = () => reject(new Error(`failed to load image: ${imageSrc}`))
      el.src = imageSrc
    })

    const canvas = document.createElement('canvas')
    canvas.width = image.naturalWidth
    canvas.height = image.naturalHeight
    const ctx = canvas.getContext('2d')
    if (!ctx) {
      throw new Error('missing canvas context')
    }

    ctx.drawImage(image, 0, 0)
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data
    let checksum = 0
    for (let i = 0; i < data.length; i += 8) {
      checksum = (checksum * 131 + data[i] + data[i + 1] * 3 + data[i + 2] * 7 + data[i + 3] * 11) % 1000000007
    }

    return {
      width: canvas.width,
      height: canvas.height,
      checksum,
    }
  }, src)
}

const MOCK_IMAGE_SVG = `
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
  <rect width="64" height="64" fill="#d9e2f2"/>
  <circle cx="32" cy="24" r="10" fill="#7a93b8"/>
  <rect x="14" y="40" width="36" height="10" rx="5" fill="#7a93b8"/>
</svg>
`.trim()

async function mockImageAsset(page: Page, id: number) {
  const fulfillImage = async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'image/svg+xml',
      body: MOCK_IMAGE_SVG,
    })
  }

  await page.route(`**/api/image-thumbnail/${id}**`, fulfillImage)
  await page.route(`**/api/image-file/${id}**`, fulfillImage)
}

function buildMockGalleryImage(id: number, overrides: Record<string, any> = {}): Record<string, any> {
  const filename = overrides.filename ?? `mock-${id}.png`
  return {
    id,
    filename,
    path: overrides.path ?? path.join(MOCK_AUTOSEP_DESTINATION, filename).replace(/\\/g, '/'),
    prompt: overrides.prompt ?? `mock prompt ${id}`,
    ...overrides,
  }
}

async function mockGalleryImages(page: Page, images: Array<Record<string, any>>) {
  const normalized = images.map((image) => buildMockGalleryImage(Number(image.id), image))
  await Promise.all(normalized.map((image) => mockImageAsset(page, image.id)))

  await page.route('**/api/images**', async (route) => {
    const pathname = new URL(route.request().url()).pathname
    if (pathname !== '/api/images') {
      await route.continue()
      return
    }

    await route.fulfill({
      json: {
        images: normalized,
        total: normalized.length,
        has_more: false,
        next_cursor: null,
      },
    })
  })

  return normalized
}

async function mockTaggerCatalog(page: Page) {
  await page.route('**/api/tagger/models', async (route) => {
    await route.fulfill({
      json: {
        default: 'wd-swinv2-tagger-v3',
        models: [
          {
            name: 'wd-swinv2-tagger-v3',
            recommended: true,
            description: 'Balanced default. Good if you are not sure.',
            best_for: 'Balanced default',
            default_threshold: 0.35,
            default_character_threshold: 0.85,
            speed: 4,
            memory: 2,
            runtime_safety_tier: 'stable',
          },
          {
            name: 'wd-eva02-large-tagger-v3',
            description: 'High-quality WD14 model for deeper coverage.',
            best_for: 'Best coverage',
            default_threshold: 0.35,
            default_character_threshold: 0.85,
            speed: 2,
            memory: 4,
            runtime_safety_tier: 'stable',
          },
          {
            name: 'camie-tagger-v2',
            description: 'Newer tag space with stronger artist and character coverage.',
            best_for: 'Modern tag coverage',
            default_threshold: 0.62,
            default_character_threshold: 0.78,
            speed: 3,
            memory: 3,
            runtime_safety_tier: 'stable',
          },
          {
            name: 'pixai-tagger-v0.9',
            description: 'PixAI v0.9 ONNX export with rating fallback and modern tags.',
            best_for: 'Modern general + character tags',
            default_threshold: 0.3,
            default_character_threshold: 0.85,
            speed: 3,
            memory: 3,
            runtime_safety_tier: 'stable',
          },
          {
            name: 'toriigate-0.5',
            description: 'Large multimodal captioner for natural-language descriptions of anime images.',
            best_for: 'Harder anime images',
            speed: 1,
            memory: 5,
            runtime_safety_tier: 'stable',
          },
        ],
      },
    })
  })

  await page.route('**/api/system-info', async (route) => {
    await route.fulfill({
      json: {
        system_info: {
          total_ram_gb: 64,
          available_ram_gb: 48,
          gpu_name: 'NVIDIA GeForce RTX 4090',
          gpu_vram_total_mb: 24576,
          gpu_vram_available_mb: 22000,
          onnx_providers: ['CUDAExecutionProvider', 'CPUExecutionProvider'],
          torch_cuda_available: true,
        },
        recommendation: {
          recommended_batch_size: 8,
          recommended_cpu_chunk_size: 32,
          recommended_use_gpu: true,
          recommended_session_refresh_interval: 180,
          risk_level: 'low',
          message: 'Sufficient VRAM for aggressive batched GPU inference.',
        },
        recommendations_by_model: {
          'wd-swinv2-tagger-v3': {
            gpu: {
              recommended_batch_size: 8,
              recommended_cpu_chunk_size: 16,
              recommended_use_gpu: true,
              recommended_session_refresh_interval: 180,
              risk_level: 'low',
              message: 'Balanced GPU path is ready.',
            },
          },
          'camie-tagger-v2': {
            gpu: {
              recommended_batch_size: 6,
              recommended_cpu_chunk_size: 12,
              recommended_use_gpu: true,
              recommended_session_refresh_interval: 180,
              risk_level: 'low',
              message: 'Camie GPU path is ready.',
            },
          },
          'pixai-tagger-v0.9': {
            gpu: {
              recommended_batch_size: 6,
              recommended_cpu_chunk_size: 12,
              recommended_use_gpu: true,
              recommended_session_refresh_interval: 180,
              risk_level: 'low',
              message: 'PixAI GPU path is ready.',
            },
          },
          'toriigate-0.5': {
            gpu: {
              recommended_batch_size: 1,
              recommended_cpu_chunk_size: 1,
              recommended_use_gpu: true,
              recommended_session_refresh_interval: 180,
              risk_level: 'low',
              message: 'ToriiGate PyTorch CUDA path is ready.',
            },
          },
        },
      },
    })
  })
}

interface TagCancelMockResponse {
  status: string
  message: string
  removed_queued: number | string
  owner_kind: 'gallery'
}

interface QueuedTagRouteProbe {
  cancelRequests: () => number
  inFlightProgressResponses: () => number
  progressPollsAfterCancel: () => number
  releaseInFlightProgress: () => void
  waitForInFlightProgress: () => Promise<void>
}

async function mockQueuedGalleryTagCancellation(
  page: Page,
  cancelResponse: TagCancelMockResponse,
): Promise<QueuedTagRouteProbe> {
  let started = false
  let cancelRequestCount = 0
  let cancelResponseSent = false
  let progressPollCountAfterCancel = 0
  let inFlightProgressResponseCount = 0
  let progressRequestHeld = false
  let releaseProgressRequest: (() => void) | null = null
  let reportProgressRequest: (() => void) | null = null
  const progressRequestStarted = new Promise<void>((resolve) => {
    reportProgressRequest = resolve
  })
  const progressRequestGate = new Promise<void>((resolve) => {
    releaseProgressRequest = resolve
  })

  await page.route('**/api/tag/start', async (route) => {
    started = true
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'queued',
        pipeline_queued: true,
        queue_position: 1,
      }),
    })
  })

  await page.route('**/api/tag/cancel', async (route) => {
    cancelRequestCount += 1
    cancelResponseSent = true
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(cancelResponse),
    })
  })

  await page.route('**/api/tag/progress', async (route) => {
    const wasQueuedWhenRequested = started && !cancelResponseSent
    if (wasQueuedWhenRequested && !progressRequestHeld) {
      progressRequestHeld = true
      reportProgressRequest?.()
      await progressRequestGate
    } else if (cancelResponseSent) {
      progressPollCountAfterCancel += 1
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'idle',
        processed: 0,
        total: 0,
        tagged: 0,
        errors: 0,
        message: '',
        pipeline_queue: {
          queued: wasQueuedWhenRequested ? [{ kind: 'gallery', position: 1 }] : [],
          total_queued: wasQueuedWhenRequested ? 1 : 0,
        },
      }),
    })
    if (wasQueuedWhenRequested) inFlightProgressResponseCount += 1
  })

  return {
    cancelRequests: () => cancelRequestCount,
    inFlightProgressResponses: () => inFlightProgressResponseCount,
    progressPollsAfterCancel: () => progressPollCountAfterCancel,
    releaseInFlightProgress: () => releaseProgressRequest?.(),
    waitForInFlightProgress: () => progressRequestStarted,
  }
}

async function mockArtistDiagnosticsReady(page: Page) {
  await page.route('**/api/artists/diagnostics', async (route) => {
    await route.fulfill({
      json: {
        available: true,
        message: 'Kaloscope runtime is ready.',
      },
    })
  })
}

async function openView(page: Page, view: string) {
  const desktopTab = page.locator(`.nav-tabs [data-view="${view}"]`).first()
  if (await desktopTab.count()) {
    const box = await desktopTab.boundingBox()
    if (box && box.width > 0 && box.height > 0) {
      await desktopTab.click({ force: true })
      return
    }
  }

  const toolItem = page.locator(`#nav-tools-menu [data-view="${view}"]`)
  if (await toolItem.count()) {
    const toggle = page.locator('#nav-tools-toggle')
    if (await toggle.isVisible().catch(() => false)) {
      await toggle.click({ force: true })
      await toolItem.click({ force: true })
      return
    }
  }

  const mobileToggle = page.locator('#mobile-menu-toggle')
  if (await mobileToggle.isVisible().catch(() => false)) {
    await mobileToggle.click({ force: true })
    await expect(page.locator('#mobile-nav-overlay')).toHaveClass(/visible/)
    await page.locator(`#mobile-nav-overlay .mobile-nav-item[data-view="${view}"]`).evaluate((node: HTMLButtonElement) => node.click())
    return
  }

  throw new Error(`Could not find navigation entry for ${view}`)
}

async function waitForNavigationChrome(page: Page) {
  await expect.poll(async () => {
    return await page.evaluate(() => {
      const isVisible = (element: Element | null) => {
        if (!(element instanceof HTMLElement)) return false
        const style = window.getComputedStyle(element)
        const rect = element.getBoundingClientRect()
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0
      }

      const chromeVisible = isVisible(document.querySelector('.nav-tabs [data-view="reader"]'))
        || isVisible(document.getElementById('mobile-menu-toggle'))
      return chromeVisible && document.documentElement.dataset.appReady === '1'
    })
  }, { timeout: 10000 }).toBe(true)
}

async function hasMainPageShell(page: Page) {
  return await page.evaluate(() => {
    const isVisible = (element: Element | null) => {
      if (!(element instanceof HTMLElement)) return false
      const style = window.getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0
    }

    return document.readyState !== 'loading' && (
      isVisible(document.querySelector('.nav-tabs [data-view="gallery"]'))
      || isVisible(document.getElementById('mobile-menu-toggle'))
      || isVisible(document.getElementById('view-gallery'))
    )
  }).catch(() => false)
}

async function waitForMainPageShell(page: Page, timeout = 10000) {
  try {
    await expect.poll(async () => {
      return await hasMainPageShell(page)
    }, { timeout }).toBe(true)
    return true
  } catch {
    return false
  }
}

async function openMainPage(page: Page) {
  let navigationError = null
  try {
    await page.goto('/', { waitUntil: 'commit', timeout: 5000 })
  } catch (error) {
    navigationError = error
  }

  if (navigationError && !(await waitForMainPageShell(page))) {
    throw navigationError
  }

  await waitForNavigationChrome(page)
}

async function getLiveSortBy(page: Page) {
  return await page.evaluate(() => {
    const app = (window as any).App
    if (app?.FilterStore && typeof app.FilterStore.getState === 'function') {
      return app.FilterStore.getState()?.sortBy ?? null
    }
    return app?.AppState?.filters?.sortBy ?? null
  })
}

async function openSortingSubView(page: Page, subView: 'autosep' | 'manual') {
  await openView(page, 'sorting')
  await expect(page.locator('#view-sorting.active')).toBeVisible()

  const subTab = page.locator(`.sorting-sub-tab[data-sorting-sub="${subView}"]`)
  await subTab.click({ force: true })

  if (subView === 'autosep') {
    await expect(page.locator('#view-autosep')).toBeVisible()
  } else {
    await expect(page.locator('#view-manual')).toBeVisible()
  }
}

async function openTagAdvancedOptions(page: Page) {
  const details = page.locator('#tag-advanced-options')
  const isOpen = await details.evaluate((node) => node instanceof HTMLDetailsElement && node.open)

  if (isOpen) return

  await page.locator('#tag-advanced-options > summary').click()
  await expect(details).toHaveAttribute('open', '')
}

async function setGallerySearch(page: Page, search: string) {
  await page.evaluate(async (value) => {
    const waitFor = async (predicate: () => boolean, timeout = 10000) => {
      const start = Date.now()
      while (!predicate()) {
        if (Date.now() - start > timeout) {
          throw new Error('Timed out waiting for gallery search helpers to initialize')
        }
        await new Promise((resolve) => setTimeout(resolve, 50))
      }
    }

    await waitFor(() => Boolean(window.App && typeof window.App.loadImages === 'function'))
    if (typeof window.App.updateFilters === 'function') {
      window.App.updateFilters((filters: any) => {
        filters.search = value
      })
    } else {
      window.App.AppState.filters.search = value
    }
    window.App.updateFilterSummary()
    await window.App.loadImages()
    await waitFor(() => window.App.AppState?.isLoading === false)
    await waitFor(() => Boolean(window.Gallery && typeof window.Gallery.setImages === 'function'))
    window.Gallery.setImages(window.App.AppState.images || [])
  }, search)
}

/**
 * Smoke Tests for SD Image Sorter
 *
 * These tests verify basic connectivity and critical paths.
 * Run these first to ensure the application is working.
 */

test.describe('Smoke Tests', () => {
  test('should load the main page', async ({ page }) => {
    await openMainPage(page)

    // Verify the page title
    await expect(page).toHaveTitle(/SD Image Sorter/i)

    const hasPrimaryNavigation = (
      await page.locator('.nav-tabs').isVisible().catch(() => false)
    ) || (
      await page.locator('#mobile-menu-toggle').isVisible().catch(() => false)
    )
    expect(hasPrimaryNavigation).toBeTruthy()

    await expect(page.locator('#view-gallery.active')).toBeVisible()
  })

  test('should have all navigation tabs', async ({ page }) => {
    await openMainPage(page)

    const tabs = [
      'gallery',
      'reader',
      'censor',
      'similar',
      'promptlab',
      'artist',
      'sorting',
    ]

    const availableViews = new Set<string>()
    const desktopViews = await page.locator('.nav-tabs .nav-tab').evaluateAll((nodes) =>
      nodes
        .filter((node) => {
          const box = node.getBoundingClientRect()
          return box.width > 0 && box.height > 0
        })
        .map((node) => node.getAttribute('data-view') || '')
        .filter(Boolean)
    )
    desktopViews.forEach((view) => availableViews.add(view))

    // v3.3.3: Prompt Helper + Style Finder are reachable via the "Tools ▾"
    // dropdown; collect their data-view from the menu (present even when closed).
    const toolViews = await page.locator('#nav-tools-menu [data-view]').evaluateAll((nodes) =>
      nodes.map((node) => node.getAttribute('data-view') || '').filter(Boolean)
    )
    toolViews.forEach((view) => availableViews.add(view))

    const mobileToggle = page.locator('#mobile-menu-toggle')
    if (await mobileToggle.isVisible().catch(() => false)) {
      await mobileToggle.click({ force: true })
      await expect(page.locator('#mobile-nav-overlay')).toHaveClass(/visible/)
      const mobileViews = await page.locator('#mobile-nav-overlay .mobile-nav-item').evaluateAll((nodes) =>
        nodes.map((node) => node.getAttribute('data-view') || '').filter(Boolean)
      )
      mobileViews.forEach((view) => availableViews.add(view))
      await page.keyboard.press('Escape')
    }

    for (const tab of tabs) {
      expect(availableViews.has(tab)).toBeTruthy()
    }
  })

  test('should navigate between views', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await openView(page, 'reader')
    await expect(page.locator('#view-reader.active')).toBeVisible()

    await openSortingSubView(page, 'autosep')
    await expect(page.locator('#view-autosep')).toBeVisible()

    await openSortingSubView(page, 'manual')
    await expect(page.locator('#view-manual')).toBeVisible()

    await openView(page, 'censor')
    await expect(page.locator('#view-censor.active')).toBeVisible()

    await openView(page, 'promptlab')
    await expect(page.locator('#view-promptlab.active')).toBeVisible()

    await openView(page, 'similar')
    await expect(page.locator('#view-similar.active')).toBeVisible()

    await openView(page, 'artist')
    await expect(page.locator('#view-artist.active')).toBeVisible()

    await openView(page, 'gallery')
    await expect(page.locator('#view-gallery.active')).toBeVisible()
    await expect(page.locator('#gallery-grid .gallery-item, #gallery-empty-state:visible').first()).toBeVisible()
  })

  test('Prompt Lab fixed beginning/end tags are obvious and deduplicated', async ({ page }) => {
    await page.route('**/api/prompts/categories', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          categories: {
            character: ['1girl'],
            style: ['masterpiece'],
          },
        }),
      })
    })
    await page.route('**/api/prompts/sets', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ sets: [] }) })
    })
    await page.route('**/api/prompts/exclusions', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ rules: [] }) })
    })
    await page.route('**/api/prompts/presets', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ presets: [] }) })
    })
    await page.route('**/api/prompts/generate', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ positive_prompt: '1girl, masterpiece, dress', negative_prompt: '', warnings: [] }),
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openView(page, 'promptlab')
    await page.locator('.promptlab-tab[data-mode="random"]').click()

    await expect(page.locator('.promptlab-affix-panel')).toContainText(/Auto dedupe|自动去重/i)
    await expect(page.locator('.promptlab-affix-panel')).toContainText(/duplicates|重复/i)
    await page.locator('#promptlab-prepend').fill('masterpiece, best quality')
    await page.locator('#promptlab-append').fill('highres, masterpiece')

    await page.locator('.cat-header[data-cat="character"]').click()
    await page.locator('.cat-tag', { hasText: '1girl' }).click()
    await page.locator('#btn-promptlab-generate').click()

    await expect(page.locator('#promptlab-output')).toHaveValue('masterpiece, best quality, 1girl, dress, highres')
  })

  test('auto-separate and manual sort should inherit the current gallery search on first open only', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.removeItem('sd-image-sorter-filter-state')
      localStorage.removeItem('autosep_filter_state_v1')
      localStorage.removeItem('manual_sort_filter_state_v1')
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await setGallerySearch(page, 'runtime_inherit_token_one')
    await expect.poll(() => page.evaluate(() => window.App?.AppState?.filters?.search ?? null)).toBe('runtime_inherit_token_one')

    await openSortingSubView(page, 'autosep')
    await expect
      .poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('autosep_filter_state_v1') || 'null')?.search))
      .toBe('runtime_inherit_token_one')

    await setGallerySearch(page, 'runtime_inherit_token_two')
    await expect.poll(() => page.evaluate(() => window.App?.AppState?.filters?.search ?? null)).toBe('runtime_inherit_token_two')

    await openSortingSubView(page, 'manual')
    await expect
      .poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('manual_sort_filter_state_v1') || 'null')?.search))
      .toBe('runtime_inherit_token_two')

    await openSortingSubView(page, 'autosep')
    await expect
      .poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('autosep_filter_state_v1') || 'null')?.search))
      .toBe('runtime_inherit_token_one')
  })

  test('reader workspace should switch between metadata reader and obfuscation tool', async ({ page }) => {
    await page.goto('/')
    await waitForNavigationChrome(page)

    await openView(page, 'reader')
    await expect(page.locator('#view-reader.active')).toBeVisible()
    await expect(page.locator('#reader-tool-panel-reader')).toBeVisible()
    await expect(page.locator('#reader-tool-panel-obfuscation')).toBeHidden()

    await page.locator('#reader-tool-tab-obfuscation').click()
    await expect(page.locator('#reader-tool-panel-obfuscation')).toBeVisible()
    await expect(page.locator('#obfuscate-btn-encode')).toBeVisible()
    await expect(page.locator('#obfuscate-drop-zone')).toBeVisible()

    await page.locator('#reader-tool-tab-reader').click()
    await expect(page.locator('#reader-tool-panel-reader')).toBeVisible()
  })

  test('reader should keep reading position when opening another gallery image', async ({ page }) => {
    const imageIds = [611, 612]
    let parseCount = 0
    const longPrompt = Array.from({ length: 80 }, (_, index) => `detail_tag_${index}`).join(', ')
    const generationParams = {
      seed: '123456789',
      steps: '28',
      cfg_scale: '7',
      sampler: 'DPM++ 2M',
      ...Object.fromEntries(
        Array.from({ length: 36 }, (_, index) => [`param_${index}`, `value_${index}`])
      ),
    }

    await Promise.all(imageIds.map((id) => mockImageAsset(page, id)))
    await page.route('**/api/images**', async (route) => {
      const url = new URL(route.request().url())
      const detailId = imageIds.find((id) => url.pathname === `/api/images/${id}`)
      if (detailId) {
        await route.fulfill({
          json: {
            image: {
              id: detailId,
              filename: `reader-position-${detailId}.png`,
              path: `L:/reader/reader-position-${detailId}.png`,
              generator: 'webui',
              prompt: longPrompt,
              negative_prompt: 'lowres, blurry',
              width: 1024,
              height: 1536,
              file_size: 123456,
              checkpoint: 'position-model.safetensors',
            },
            tags: [],
          },
        })
        return
      }

      if (url.pathname !== '/api/images') {
        await route.continue()
        return
      }

      await route.fulfill({
        json: {
          images: imageIds.map((id) => buildMockGalleryImage(id, {
            filename: `reader-position-${id}.png`,
            path: `L:/reader/reader-position-${id}.png`,
            generator: 'webui',
            prompt: longPrompt,
          })),
          total: imageIds.length,
          has_more: false,
          next_cursor: null,
        },
      })
    })
    await page.route('**/api/parse-image', async (route) => {
      parseCount += 1
      await route.fulfill({
        json: {
          generator: 'webui',
          prompt: longPrompt,
          negative_prompt: 'lowres, blurry',
          checkpoint: 'position-model.safetensors',
          width: 1024,
          height: 1536,
          file_size: 123456,
          metadata: { _parsed: { generation_params: generationParams } },
          source_temp_path: `/tmp/sd_image_sorter_reader_uploads/reader-position-${parseCount}.png`,
        },
      })
    })

    await openMainPage(page)
    await page.setViewportSize({ width: 1280, height: 720 })
    await page.evaluate(async (targetId) => {
      await window.App.openReaderFromImage(targetId, 'reader-position-611.png')
    }, imageIds[0])

    await expect(page.locator('#view-reader.active')).toBeVisible()
    await expect(page.locator('#reader-file-info')).toContainText('reader-position-611.png')
    await expect(page.locator('#reader-quick-facts')).toContainText('position-model')
    await expect(page.locator('#reader-quick-facts')).toContainText('123456789')
    const beforeScroll = await page.evaluate(() => {
      const candidates = [
        document.querySelector('#view-reader .reader-right'),
        document.getElementById('view-reader'),
        document.scrollingElement,
      ].filter(Boolean) as HTMLElement[]
      const scrollEl = candidates.find((element) => element.scrollHeight - element.clientHeight > 120)
      if (!scrollEl) return 0
      scrollEl.scrollTop = Math.min(520, Math.max(0, scrollEl.scrollHeight - scrollEl.clientHeight))
      ;(window as any).__readerScrollProbe = scrollEl.className || scrollEl.id || scrollEl.tagName
      return scrollEl.scrollTop
    })
    expect(beforeScroll).toBeGreaterThan(100)

    await page.evaluate(async (targetId) => {
      await window.App.openReaderFromImage(targetId, 'reader-position-612.png')
    }, imageIds[1])

    await expect(page.locator('#reader-file-info')).toContainText('reader-position-612.png')
    await expect.poll(async () => {
      return await page.evaluate(() => {
        const candidates = [
          document.querySelector('#view-reader .reader-right'),
          document.getElementById('view-reader'),
          document.scrollingElement,
        ].filter(Boolean) as HTMLElement[]
        const scrollEl = candidates.find((element) => element.scrollHeight - element.clientHeight > 120)
        return scrollEl?.scrollTop || 0
      })
    }).toBeGreaterThan(100)
  })

  test('reader library overwrite should confirm before save request and flag gallery refresh intent', async ({ page }) => {
    const imageId = 501
    const libraryPath = 'L:/datasets/library-reader-source.png'
    const tempSourcePath = '/tmp/sd_image_sorter_reader_uploads/library-reader-source.png'
    const saveAllowOverwriteFlags: boolean[] = []
    let saveConflictCount = 0

    await mockImageAsset(page, imageId)
    await page.route('**/api/images**', async (route) => {
      const url = new URL(route.request().url())
      if (url.pathname === `/api/images/${imageId}`) {
        await route.fulfill({
          json: {
            image: {
              id: imageId,
              filename: 'library-reader-source.png',
              path: libraryPath,
              generator: 'comfyui',
              prompt: 'library source prompt',
              negative_prompt: '',
              width: 1024,
              height: 1024,
              file_size: 12345,
              checkpoint: 'mock_model.safetensors',
            },
            tags: [],
          },
        })
        return
      }

      if (url.pathname !== '/api/images') {
        await route.continue()
        return
      }

      await route.fulfill({
        json: {
          images: [
            buildMockGalleryImage(imageId, {
              filename: 'library-reader-source.png',
              path: libraryPath,
              generator: 'comfyui',
              prompt: 'library source prompt',
            }),
          ],
          total: 1,
          has_more: false,
          next_cursor: null,
        },
      })
    })
    await page.route('**/api/parse-image', async (route) => {
      await route.fulfill({
        json: {
          generator: 'comfyui',
          prompt: 'parsed prompt',
          negative_prompt: 'parsed negative',
          checkpoint: 'mock_model.safetensors',
          width: 1024,
          height: 1024,
          file_size: 12345,
          metadata: { _parsed: { generation_params: {} } },
          source_temp_path: tempSourcePath,
        },
      })
    })
    await page.route('**/api/image-metadata/save-edited', async (route) => {
      const payload = route.request().postDataJSON() as { allow_overwrite?: boolean }
      const allowOverwrite = Boolean(payload?.allow_overwrite)
      saveAllowOverwriteFlags.push(allowOverwrite)
      if (!allowOverwrite) {
        saveConflictCount += 1
        await route.fulfill({
          status: 409,
          json: { detail: 'Output file already exists. Confirm overwrite before saving.' },
        })
        return
      }

      await route.fulfill({
        status: 200,
        json: {
          output_path: libraryPath,
          format: 'png',
          warnings: [],
        },
      })
    })

    await openMainPage(page)
    await expect(page.locator('#gallery-grid .gallery-item')).toHaveCount(1)

    await page.evaluate(async (targetId) => {
      await window.App.openReaderFromImage(targetId, 'library-reader-source.png')
    }, imageId)

    await expect(page.locator('#view-reader.active')).toBeVisible()
    await expect(page.locator('#reader-metadata-editor')).toBeVisible()
    if (!(await page.locator('#reader-editor-body').isVisible().catch(() => false))) {
      await page.locator('#reader-metadata-editor .reader-section-toggle').click()
    }
    await expect(page.locator('#reader-editor-body')).toBeVisible()

    await page.locator('#reader-edit-output-path').fill(libraryPath)
    await page.locator('#reader-save-metadata-as').click()

    await expect(page.locator('#confirm-modal.visible')).toBeVisible()
    expect(saveAllowOverwriteFlags).toEqual([])
    await page.locator('#btn-confirm-ok').click()

    await expect.poll(() => saveAllowOverwriteFlags.length).toBe(1)
    expect(saveAllowOverwriteFlags).toEqual([true])
    expect(saveConflictCount).toBe(0)
    await expect.poll(() => page.evaluate(() => Boolean(window.App?.AppState?.galleryNeedsRefresh))).toBe(true)
  })

  test('reader save-as-new path should not flag gallery refresh intent', async ({ page }) => {
    const imageId = 502
    const libraryPath = 'L:/datasets/library-reader-source-2.png'
    const tempSourcePath = '/tmp/sd_image_sorter_reader_uploads/library-reader-source-2.png'
    const newOutputPath = 'L:/exports/library-reader-copy.png'
    const saveAllowOverwriteFlags: boolean[] = []

    await mockImageAsset(page, imageId)
    await page.route('**/api/images**', async (route) => {
      const url = new URL(route.request().url())
      if (url.pathname === `/api/images/${imageId}`) {
        await route.fulfill({
          json: {
            image: {
              id: imageId,
              filename: 'library-reader-source-2.png',
              path: libraryPath,
              generator: 'comfyui',
              prompt: 'library source prompt',
              negative_prompt: '',
              width: 1024,
              height: 1024,
              file_size: 12345,
              checkpoint: 'mock_model.safetensors',
            },
            tags: [],
          },
        })
        return
      }

      if (url.pathname !== '/api/images') {
        await route.continue()
        return
      }

      await route.fulfill({
        json: {
          images: [
            buildMockGalleryImage(imageId, {
              filename: 'library-reader-source-2.png',
              path: libraryPath,
              generator: 'comfyui',
              prompt: 'library source prompt',
            }),
          ],
          total: 1,
          has_more: false,
          next_cursor: null,
        },
      })
    })
    await page.route('**/api/parse-image', async (route) => {
      await route.fulfill({
        json: {
          generator: 'comfyui',
          prompt: 'parsed prompt',
          negative_prompt: 'parsed negative',
          checkpoint: 'mock_model.safetensors',
          width: 1024,
          height: 1024,
          file_size: 12345,
          metadata: { _parsed: { generation_params: {} } },
          source_temp_path: tempSourcePath,
        },
      })
    })
    await page.route('**/api/image-metadata/save-edited', async (route) => {
      const payload = route.request().postDataJSON() as { allow_overwrite?: boolean }
      saveAllowOverwriteFlags.push(Boolean(payload?.allow_overwrite))
      await route.fulfill({
        status: 200,
        json: {
          output_path: newOutputPath,
          format: 'png',
          warnings: [],
        },
      })
    })

    await openMainPage(page)
    await expect(page.locator('#gallery-grid .gallery-item')).toHaveCount(1)

    await page.evaluate(async (targetId) => {
      await window.App.openReaderFromImage(targetId, 'library-reader-source-2.png')
    }, imageId)

    await expect(page.locator('#view-reader.active')).toBeVisible()
    await expect(page.locator('#reader-metadata-editor')).toBeVisible()
    if (!(await page.locator('#reader-editor-body').isVisible().catch(() => false))) {
      await page.locator('#reader-metadata-editor .reader-section-toggle').click()
    }
    await expect(page.locator('#reader-editor-body')).toBeVisible()

    await page.locator('#reader-edit-output-path').fill(newOutputPath)
    await page.locator('#reader-save-metadata-as').click()

    await expect.poll(() => saveAllowOverwriteFlags.length).toBe(1)
    expect(saveAllowOverwriteFlags).toEqual([false])
    await expect.poll(() => page.evaluate(() => Boolean(window.App?.AppState?.galleryNeedsRefresh))).toBe(false)
  })

  test('obfuscation workspace should round-trip and expose copy flow', async ({ page }) => {
    await page.addInitScript(() => {
      ;(window as any).__clipboardWrites = 0
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: {
          write: async (items: unknown[]) => {
            ;(window as any).__clipboardWrites += items.length
          },
        },
      })
      ;(window as any).ClipboardItem = class ClipboardItem {
        constructor(public items: Record<string, Blob>) {}
      }
    })

    const samplePngBuffer = Buffer.from(MIXED_MASK_DATA_URL.split(',')[1], 'base64')

    await page.goto('/')
    await waitForNavigationChrome(page)

    await openView(page, 'reader')
    await page.locator('#reader-tool-tab-obfuscation').click()
    await expect(page.locator('#reader-tool-panel-obfuscation')).toBeVisible()

    await page.locator('#obfuscate-file-input').setInputFiles({
      name: 'obfuscate-sample.png',
      mimeType: 'image/png',
      buffer: samplePngBuffer,
    })

    const queueItem = page.locator('.obfuscate-item').first()
    await expect(queueItem).toBeVisible()

    const originalFingerprint = await getImageFingerprint(page, MIXED_MASK_DATA_URL)

    await page.locator('#obfuscate-btn-encode').click()
    await expect(queueItem).toHaveClass(/done/)

    const encodedSrc = await queueItem.locator('.obfuscate-thumb.result-thumb').getAttribute('src')
    expect(encodedSrc).toBeTruthy()
    const encodedFingerprint = await getImageFingerprint(page, String(encodedSrc))
    expect(encodedFingerprint.checksum).not.toBe(originalFingerprint.checksum)

    const copyButton = queueItem.locator('.obfuscate-copy')
    const downloadButton = queueItem.locator('.obfuscate-download')
    await expect(copyButton).toBeEnabled()
    await expect(downloadButton).toBeEnabled()

    await copyButton.click()
    await expect.poll(async () => {
      return await page.evaluate(() => (window as any).__clipboardWrites || 0)
    }).toBeGreaterThan(0)

    await page.locator('#obfuscate-btn-decode').click()
    await expect(queueItem).toHaveClass(/done/)

    const decodedSrc = await queueItem.locator('.obfuscate-thumb.result-thumb').getAttribute('src')
    expect(decodedSrc).toBeTruthy()
    const decodedFingerprint = await getImageFingerprint(page, String(decodedSrc))
    expect(decodedFingerprint).toEqual(originalFingerprint)

    await page.locator('#obfuscate-compat-mode').selectOption('small_tomato')
    await expect(page.locator('#obfuscate-password')).toBeHidden()
  })

  test('obfuscation success toast should not block follow-up clicks', async ({ page }) => {
    const samplePngBuffer = Buffer.from(MIXED_MASK_DATA_URL.split(',')[1], 'base64')

    await page.goto('/')
    await waitForNavigationChrome(page)

    await openView(page, 'reader')
    await page.locator('#reader-tool-tab-obfuscation').click()
    await expect(page.locator('#reader-tool-panel-obfuscation')).toBeVisible()

    await page.locator('#obfuscate-file-input').setInputFiles({
      name: 'obfuscate-toast-check.png',
      mimeType: 'image/png',
      buffer: samplePngBuffer,
    })

    const queueItem = page.locator('.obfuscate-item').first()
    await expect(queueItem).toBeVisible()

    await page.locator('#obfuscate-btn-encode').click()
    await expect(queueItem).toHaveClass(/done/)

    const toast = page.locator('#toast-container .toast').last()
    await expect(toast).toBeVisible()
    await expect(toast).toContainText(/Protected 1\/1 images|已处理|已保护/i)

    await page.locator('#obfuscate-settings-toggle').click()
    await expect(page.locator('#obfuscate-advanced-settings')).toBeVisible()

    await page.locator('#obfuscate-btn-clear').click()
    await expect(page.locator('.obfuscate-item')).toHaveCount(0)
    await expect(page.locator('#toast-container .toast').last()).toBeVisible()
  })

  test('gallery sort reverse should support aesthetic score', async ({ page }) => {
    await page.route('**/api/aesthetic/status', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          available: true,
          message: '',
          scored_count: 6,
        }),
      })
    })

    await openMainPage(page)

    await page.locator('#gallery-sort').selectOption('aesthetic')
    await expect(page.locator('#gallery-sort')).toHaveValue('aesthetic')

    await expect.poll(async () => {
      return await getLiveSortBy(page)
    }).toBe('aesthetic')

    await page.locator('#sort-reverse-btn').click()
    await expect.poll(async () => {
      return await getLiveSortBy(page)
    }).toBe('aesthetic_asc')
    await expect(page.locator('#sort-reverse-btn')).toHaveClass(/active/)
  })

  test('should switch gallery views and open filter/library flows', async ({ page }) => {
    const pageErrors: string[] = []
    page.on('pageerror', (error) => pageErrors.push(error.message))

    await mockGalleryImages(page, [
      { id: 201, filename: 'view-flow-1.png' },
      { id: 202, filename: 'view-flow-2.png' },
      { id: 203, filename: 'view-flow-3.png' },
    ])
    await page.route('**/api/tags/library**', async (route) => {
      await route.fulfill({
        json: {
          tags: [
            { tag: 'portrait', count: 4 },
            { tag: 'dramatic_light', count: 2 },
          ],
          total: 2,
        },
      })
    })
    await page.route('**/api/prompts/library**', async (route) => {
      await route.fulfill({
        json: {
          prompts: [
            { prompt: 'cinematic portrait', count: 3 },
            { prompt: 'soft rim light', count: 2 },
          ],
          total: 2,
        },
      })
    })

    const waitForLibraryResults = async () => {
      await expect.poll(
        async () => {
          const libraryContent = page.locator('#library-content')

          if (await libraryContent.locator('.library-status-error, .library-feedback.error').count()) {
            return 'error'
          }

          if (await libraryContent.locator('.library-tag').count()) {
            return 'items'
          }

          if (await libraryContent.locator('.empty-state-text').count()) {
            return 'empty'
          }

          return 'loading'
        },
        { timeout: 15000, message: 'Expected library content or an empty-state after opening a library tab' }
      ).toMatch(/^(items|empty)$/)
    }

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const galleryGrid = page.locator('#gallery-grid')
    await expect(galleryGrid).toBeVisible()

    await page.evaluate(() => {
      const grid = document.getElementById('gallery-grid')
      if (!grid) return

      let node = grid.parentElement
      while (node) {
        const style = window.getComputedStyle(node)
        const canScroll = /(auto|scroll|overlay)/.test(style.overflowY) && node.scrollHeight > node.clientHeight + 4
        if (canScroll) {
          node.scrollTop = 900
          return
        }
        node = node.parentElement
      }

      const scrollContainer = document.scrollingElement || document.documentElement
      if (scrollContainer) {
        scrollContainer.scrollTop = 900
      }
    })

    const initialScrollState = await getGalleryScrollState(page)

    const viewModes = [
      { mode: 'large', className: /large/ },
      { mode: 'waterfall', className: /waterfall/ },
      { mode: 'grid', className: /gallery-grid/ },
    ]

    for (const { mode, className } of viewModes) {
      await page.locator(`.view-btn[data-size="${mode}"]`).click()
      await expect(galleryGrid).toHaveClass(className)

      const firstItem = page.locator('#gallery-grid .gallery-item').first()
      if (await firstItem.count()) {
        await expect(firstItem).toBeVisible()
        const box = await firstItem.boundingBox()
        const viewport = page.viewportSize()

        expect(box).not.toBeNull()
        expect(viewport).not.toBeNull()

        if (box && viewport) {
          expect(box.width).toBeGreaterThan(0)
          expect(box.width).toBeLessThan(viewport.width)
          expect(box.height).toBeGreaterThan(0)
          expect(box.height).toBeLessThan(viewport.height)
        }
      }

      const scrollState = await getGalleryScrollState(page)
      if (initialScrollState.scrollTop > 0) {
        expect(scrollState.scrollTop).toBeGreaterThan(120)
        expect(scrollState.topVisibleId).toBe(initialScrollState.topVisibleId)
      }
    }

    await page.locator('#btn-open-filters').click()
    await expect(page.locator('#filter-modal.visible')).toBeVisible()
    await expect(page.locator('#filter-modal-selection-summary')).toBeVisible()
    await expect(page.locator('#modal-generator-filters')).toBeVisible()
    await expect(page.locator('#modal-rating-filters')).toBeVisible()
    await expect(page.locator('#btn-open-library-from-filter')).toBeVisible()

    await page.locator('#btn-open-library-from-filter').click()
    await expect(page.locator('#tags-library-modal.visible')).toBeVisible()

    const libraryError = page.locator('#library-content .library-status-error, #library-content .library-feedback.error')

    await page.locator('#library-tab-prompts').click()
    await waitForLibraryResults()
    await expect(libraryError).toHaveCount(0)

    await page.locator('#library-tab-tags').click()
    await waitForLibraryResults()
    await expect(libraryError).toHaveCount(0)

    expect(pageErrors).toEqual([])
  })

  test('should keep large view selection stable without overlapping cards', async ({ page }) => {
    await mockGalleryImages(page, [
      { id: 301, filename: 'large-view-1.png' },
      { id: 302, filename: 'large-view-2.png' },
      { id: 303, filename: 'large-view-3.png' },
    ])
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('.view-btn[data-size="large"]').click()
    await expect(page.locator('#gallery-grid')).toHaveClass(/large/)
    await expect(page.locator('#gallery-grid .gallery-item')).toHaveCount(3)

    const beforeRects = await getVisibleGalleryRects(page, 3)
    expect(beforeRects.length).toBeGreaterThanOrEqual(2)

    await page.locator('#btn-toggle-select').click()
    await page.locator('#gallery-grid .gallery-item').nth(0).click()
    await page.locator('#gallery-grid .gallery-item').nth(1).click()

    const afterRects = await getVisibleGalleryRects(page, 3)
    expect(afterRects.length).toBeGreaterThanOrEqual(2)

    for (let i = 0; i < afterRects.length; i++) {
      expect(afterRects[i].width).toBeGreaterThan(0)
      expect(afterRects[i].height).toBeGreaterThan(0)

      if (beforeRects[i]) {
        expect(Math.abs(afterRects[i].width - beforeRects[i].width)).toBeLessThan(2)
        expect(Math.abs(afterRects[i].height - beforeRects[i].height)).toBeLessThan(2)
      }

      for (let j = i + 1; j < afterRects.length; j++) {
        expect(rectsOverlap(afterRects[i], afterRects[j])).toBeFalsy()
      }
    }
  })

  test('should list camie, pixai, and ToriiGate in the tagger modal', async ({ page }) => {
    await mockTaggerCatalog(page)
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-tag').click()
    await expect(page.locator('#tag-modal.visible')).toBeVisible()

    const camieOption = page.locator('#tag-model-select option[value="camie-tagger-v2"]')
    const pixaiOption = page.locator('#tag-model-select option[value="pixai-tagger-v0.9"]')
    const toriiGateOption = page.locator('#tag-model-select option[value="toriigate-0.5"]')

    await expect(camieOption).toHaveCount(1)
    await expect(pixaiOption).toHaveCount(1)
    await expect(toriiGateOption).toHaveCount(1)
    await expect(pixaiOption).toBeEnabled()
    await expect(toriiGateOption).toBeEnabled()

    await page.locator('#tag-model-select').selectOption('camie-tagger-v2')
    await expect(page.locator('#tag-model-help')).toContainText(/newer danbooru-era tag space|modern tag coverage|tagger\.desc|Q\d\/5/i)
    await expect(page.locator('#tag-threshold')).toHaveValue('0.62')
    await expect(page.locator('#tag-character-threshold')).toHaveValue('0.78')

    await page.locator('#tag-model-select').selectOption('pixai-tagger-v0.9')
    await expect(page.locator('#tag-model-help')).toContainText(/pixai v0.9 onnx export|newer tag space|rating fallback|tagger\.desc|Q\d\/5/i)
    await expect(page.locator('#tag-threshold')).toHaveValue('0.3')
    await expect(page.locator('#tag-character-threshold')).toHaveValue('0.85')

    await page.locator('#tag-model-select').selectOption('toriigate-0.5')
    await openTagAdvancedOptions(page)
    await expect(page.locator('#tag-model-help')).toContainText(/multimodal|anime/i)
    await expect(page.locator('#tag-threshold-section')).toBeHidden()
    await expect(page.locator('#tag-threshold-note')).toBeVisible()
    await expect(page.locator('#tag-threshold-note')).toContainText(/does not use WD14 thresholds|generates tags directly/i)
    await expect(page.locator('#tag-runtime-provider-chip')).toContainText(/PyTorch/i)
  })

  test('should keep canonical WD model names in the tagger modal', async ({ page }) => {
    await mockTaggerCatalog(page)
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-tag').click()
    await expect(page.locator('#tag-modal.visible')).toBeVisible()
    await openTagAdvancedOptions(page)

    const optionTexts = await page.locator('#tag-model-select option').allTextContents()

    expect(optionTexts.some((text) => text.includes('wd-eva02-large-tagger-v3'))).toBeTruthy()
    expect(optionTexts.some((text) => /Best Quality/i.test(text))).toBeFalsy()
    await expect(page.locator('#tag-model-select')).toHaveValue('wd-swinv2-tagger-v3')
    await expect.poll(async () => {
      return await page.evaluate(() => window.__taggerSystemInfoStatus || null)
    }).toBe('loaded')
    await expect(page.locator('#system-info-panel')).toBeVisible()
    await expect(page.locator('#system-info-content')).toContainText(/RAM|VRAM|GPU/i)
    await expect(page.locator('#tagger-model-panel')).toBeVisible()
    await expect(page.locator('#tag-model-badges')).toBeVisible()
    await expect(page.locator('#tag-runtime-mode-chip')).toBeVisible()
    await expect(page.locator('#tag-runtime-provider-chip')).toBeVisible()
    await expect(page.locator('#tag-runtime-chunk-chip')).toBeVisible()
    await expect(page.locator('#tag-batch-recommendation')).toContainText(/Recommended (chunk|batch) size|chunkHelp/i)
    await expect(page.locator('#tag-runtime-summary')).toContainText(/Recommended (chunk|batch)|CPU mode|adaptive GPU mode|fast path|tagger\.runtime|tagger\.chunkHelp/i)
  })

  test('should keep adaptive-throughput tagger models in adaptive runtime mode by default', async ({ page }) => {
    await page.route('**/api/system-info', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          system_info: {
            total_ram_gb: 64,
            available_ram_gb: 48,
            gpu_name: 'NVIDIA GeForce RTX 4090',
            gpu_vram_total_mb: 24576,
            gpu_vram_available_mb: 22000,
            onnx_providers: ['CUDAExecutionProvider', 'CPUExecutionProvider'],
          },
          recommendation: {
            recommended_batch_size: 12,
            recommended_cpu_chunk_size: 32,
            recommended_use_gpu: true,
            recommended_session_refresh_interval: 180,
            risk_level: 'low',
            message: 'Sufficient VRAM for aggressive batched GPU inference.',
          },
          recommendations_by_model: {
            'wd-eva02-large-tagger-v3': {
              gpu: {
                recommended_batch_size: 12,
                recommended_cpu_chunk_size: 12,
                recommended_use_gpu: true,
                recommended_session_refresh_interval: 180,
                risk_level: 'low',
                message: 'Adaptive EVA02 GPU path is ready.',
              },
            },
          },
        }),
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-tag').click()
    await expect(page.locator('#tag-modal.visible')).toBeVisible()

    await expect(page.locator('#tag-runtime-summary')).toContainText(/Recommended chunk|Adaptive GPU mode|recommended fast path|tagger\.runtime|tagger\.chunkHelp/i)

    await page.locator('#tag-model-select').selectOption('wd-eva02-large-tagger-v3')

    await expect(page.locator('#tag-use-gpu')).toBeChecked()
    await expect(page.locator('#tag-use-gpu')).toBeEnabled()
    await expect(page.locator('#tag-provider-chip')).toContainText(/CUDA|Provider unknown|providerUnknown/i)
    await expect(page.locator('#tag-runtime-provider-chip')).toContainText(/CUDA|TensorRT|Provider unknown|providerUnknown/i)
    await expect(page.locator('#tag-runtime-summary')).toContainText(/Adaptive GPU mode|recommended fast path|Recommended chunk|tagger\.runtime|tagger\.chunkHelp/i)
    await expect(page.locator('#tag-model-help')).toContainText(/adaptive runtime limits|tagger\.|Q\d\/5/i)
    await expect(page.locator('#tag-runtime-mode-chip')).toContainText(/GPU Target/i)

    await page.locator('#tag-model-select').selectOption('custom')
    await expect(page.locator('#custom-model-group')).toBeVisible()
    await expect(page.locator('#custom-tags-group')).toBeVisible()
    await expect(page.locator('#tag-runtime-summary')).toContainText(/GPU|tagger\.runtime|Custom model/i)
    await expect(page.locator('#tag-use-gpu')).toBeChecked()
    await expect(page.locator('#tag-use-gpu')).toBeEnabled()
  })

  test('should start Max Quality directly under automatic GPU safety limits', async ({ page }) => {
    let capturedPayload: Record<string, unknown> | null = null

    await page.route('**/api/system-info', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          system_info: {
            total_ram_gb: 64,
            available_ram_gb: 48,
            gpu_name: 'NVIDIA GeForce RTX 4090',
            gpu_vram_total_mb: 24576,
            gpu_vram_available_mb: 22000,
            onnx_providers: ['CUDAExecutionProvider', 'CPUExecutionProvider'],
          },
          recommendation: {
            recommended_batch_size: 12,
            recommended_cpu_chunk_size: 32,
            recommended_use_gpu: true,
            recommended_session_refresh_interval: 180,
            risk_level: 'low',
            message: 'Sufficient VRAM for aggressive batched GPU inference.',
          },
        }),
      })
    })

    await page.route('**/api/tag/start', async (route) => {
      capturedPayload = route.request().postDataJSON()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'started' }),
      })
    })

    await page.route('**/api/tag/progress', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'done',
          processed: 0,
          total: 0,
          tagged: 0,
          errors: 0,
          message: 'Tagging complete',
        }),
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-tag').click()
    await expect(page.locator('#tag-modal.visible')).toBeVisible()

    await page.locator('#tag-model-select').selectOption('wd-eva02-large-tagger-v3')
    await page.locator('#btn-start-tag').click()
    await expect(page.locator('#confirm-modal.visible')).toHaveCount(0)

    await expect.poll(() => capturedPayload, {
      message: 'Expected the Max Quality tag start payload',
    }).not.toBeNull()

    expect(capturedPayload).toMatchObject({
      model_name: 'wd-eva02-large-tagger-v3',
      use_gpu: true,
      allow_unsafe_acceleration: false,
    })
  })

  test('should start a custom GPU tagger run directly under automatic limits', async ({ page }) => {
    let capturedPayload: Record<string, unknown> | null = null

    await page.route('**/api/system-info', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          system_info: {
            total_ram_gb: 64,
            available_ram_gb: 48,
            gpu_name: 'NVIDIA GeForce RTX 4090',
            gpu_vram_total_mb: 24576,
            gpu_vram_available_mb: 22000,
            torch_cuda_available: true,
            onnx_providers: ['CUDAExecutionProvider', 'CPUExecutionProvider'],
          },
          recommendation: {
            recommended_batch_size: 12,
            recommended_cpu_chunk_size: 32,
            recommended_use_gpu: true,
            recommended_session_refresh_interval: 180,
            risk_level: 'low',
            message: 'Sufficient VRAM for aggressive batched GPU inference.',
          },
          recommendations_by_model: {
            custom: {
              gpu: {
                recommended_batch_size: 8,
                recommended_cpu_chunk_size: 8,
                recommended_use_gpu: true,
                recommended_session_refresh_interval: 180,
                risk_level: 'medium',
                message: 'Custom ONNX models stay on a conservative starting chunk until the model proves stable on this machine.',
              },
              cpu: {
                recommended_batch_size: 8,
                recommended_cpu_chunk_size: 8,
                recommended_use_gpu: false,
                recommended_session_refresh_interval: 0,
                risk_level: 'low',
                message: 'Custom ONNX models stay on a conservative starting chunk until the model proves stable on this machine.',
              },
            },
          },
        }),
      })
    })

    await page.route('**/api/tag/start', async (route) => {
      capturedPayload = route.request().postDataJSON()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'started' }),
      })
    })

    await page.route('**/api/tag/progress', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'done',
          processed: 0,
          total: 0,
          tagged: 0,
          errors: 0,
          message: 'Tagging complete',
        }),
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-tag').click()
    await expect(page.locator('#tag-modal.visible')).toBeVisible()

    await page.locator('#tag-model-select').selectOption('custom')
    await page.locator('#tag-model-path').fill('C:/models/custom-model.onnx')
    await page.locator('#tag-tags-path').fill('C:/models/selected_tags.csv')
    await expect(page.locator('#tag-model-help')).not.toContainText(/GPU Preferred|provider/i)
    await page.locator('#tag-runtime-advanced summary').click()
    await expect(page.locator('#tag-runtime-advanced')).toHaveAttribute('open', '')
    await expect(page.locator('#tag-use-gpu')).toBeChecked()
    await expect(page.locator('#tag-gpu-help')).toContainText(/Uncheck to use CPU only|GPU override is active|GPU mode is active|Automatic hardware limits|CPU mode|gpuHelpCustomCpu|gpuHelpRiskyOverride/i)
    await expect(page.locator('#tag-batch-recommendation')).toContainText('8')

    await page.locator('#btn-start-tag').click()
    await expect(page.locator('#confirm-modal.visible')).toHaveCount(0)

    await expect.poll(() => capturedPayload, {
      message: 'Expected the tag start payload after confirming risky custom GPU mode',
    }).not.toBeNull()

    expect(capturedPayload).toMatchObject({
      model_path: 'C:/models/custom-model.onnx',
      tags_path: 'C:/models/selected_tags.csv',
      use_gpu: true,
      allow_unsafe_acceleration: false,
    })
  })

  test('should keep tagger progress available in the background with stop and details', async ({ page }) => {
    let started = false
    let cancelRequested = 0
    let cancelProgressPolls = 0

    await page.route('**/api/tag/start', async (route) => {
      started = true
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'started' }),
      })
    })

    await page.route('**/api/tag/cancel', async (route) => {
      cancelRequested += 1
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'cancelling',
          message: 'Cancellation requested',
          removed_queued: 0,
        }),
      })
    })

    await page.route('**/api/tag/progress', async (route) => {
      if (!started) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            status: 'idle',
            processed: 0,
            total: 0,
            tagged: 0,
            errors: 0,
            message: '',
          }),
        })
        return
      }

      if (cancelRequested > 0) {
        cancelProgressPolls += 1
        if (cancelProgressPolls >= 2) {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              status: 'cancelled',
              processed: 3,
              total: 10,
              tagged: 3,
              errors: 0,
              message: 'Tagging cancelled',
            }),
          })
          return
        }

        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            status: 'cancelling',
            processed: 3,
            total: 10,
            tagged: 3,
            errors: 0,
            message: 'Cancelling... (3/10)',
          }),
        })
        return
      }

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'running',
          processed: 3,
          total: 10,
          tagged: 3,
          errors: 0,
          message: '3/10 (3 tagged)',
        }),
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-tag').click()
    await expect(page.locator('#tag-modal.visible')).toBeVisible()
    await page.locator('#btn-start-tag').click()

    await expect(page.locator('#tag-progress-container')).toBeVisible()
    await page.locator('#btn-cancel-tag').click()

    await expect(page.locator('#tag-modal.visible')).toHaveCount(0)
    await expect(page.locator('#bg-tag-progress')).toBeVisible()
    await expect(page.locator('#bg-tag-progress-text')).toContainText(/3\/10|Preparing/i)

    await page.locator('#bg-tag-open').click()
    await expect(page.locator('#tag-modal.visible')).toBeVisible()

    await page.locator('#btn-close-tag-modal').click()
    await expect(page.locator('#tag-modal.visible')).toHaveCount(0)
    await expect(page.locator('#bg-tag-progress')).toBeVisible()

    await page.locator('#bg-tag-cancel').click()
    await expect.poll(() => cancelRequested).toBe(1)
    await expect(page.locator('#bg-tag-progress-text')).toContainText(/Cancelling|取消/i)
    await expect(page.locator('#bg-tag-progress')).toBeHidden({ timeout: 10000 })
  })

  test('tag progress monitoring survives a temporary backend disconnect without presenting a false idle state', async ({ page }) => {
    let started = false
    let startRequests = 0
    let progressPollsAfterStart = 0
    const progressPollTimes: number[] = []
    let releaseTerminalProgress = (): void => {
      throw new Error('Terminal progress gate was not initialized')
    }
    const terminalProgressGate = new Promise<void>((resolve) => {
      releaseTerminalProgress = resolve
    })

    await page.route('**/api/tag/start', async (route) => {
      started = true
      startRequests += 1
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'queued',
          pipeline_queued: true,
          queue_position: 1,
        }),
      })
    })

    await page.route('**/api/tag/progress', async (route) => {
      if (!started) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            status: 'idle',
            processed: 0,
            total: 0,
            tagged: 0,
            errors: 0,
            message: '',
          }),
        })
        return
      }

      progressPollsAfterStart += 1
      progressPollTimes.push(performance.now())
      if (progressPollsAfterStart <= 5) {
        await route.abort('failed')
        return
      }

      await terminalProgressGate
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'done',
          completed: 6,
          processed: 6,
          total: 6,
          tagged: 6,
          errors: 0,
          message: 'Tagging complete',
        }),
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await page.evaluate(() => {
      const state = window as typeof window & {
        __tagProgressWarningCount?: number
        __taggingCompletedCount?: number
      }
      const toastContainer = document.getElementById('toast-container')
      if (!toastContainer) {
        throw new Error('Toast container is required for Tag progress monitoring')
      }
      state.__tagProgressWarningCount = 0
      state.__taggingCompletedCount = 0
      const toastObserver = new MutationObserver((records) => {
        for (const record of records) {
          for (const addedNode of record.addedNodes) {
            if (addedNode instanceof Element && addedNode.matches('.toast.warning')) {
              state.__tagProgressWarningCount = (state.__tagProgressWarningCount || 0) + 1
            }
          }
        }
      })
      toastObserver.observe(toastContainer, { childList: true })
      document.addEventListener('taggingCompleted', () => {
        state.__taggingCompletedCount = (state.__taggingCompletedCount || 0) + 1
      })
    })

    await page.locator('#btn-tag').click()
    await page.locator('#btn-start-tag').click()

    await expect.poll(() => progressPollsAfterStart, { timeout: 8000 }).toBe(4)
    expect(startRequests).toBe(1)
    await expect(page.locator('#tag-progress-container')).toBeVisible()
    await expect(page.locator('#btn-start-tag')).toBeDisabled()
    await expect(page.locator('#btn-start-tag')).toContainText(/Tagging|正在打标/i)
    await expect(page.locator('#btn-cancel-tag')).toContainText(/Run in Background|后台运行/i)
    await expect(page.locator('#tag-model-select')).toBeDisabled()
    await expect(page.locator('#toast-container .toast.warning .toast-message')).toHaveCount(1)
    await expect(page.locator('#toast-container .toast.warning .toast-message')).toContainText(/queued or running|排队或运行/i)
    const monitoringState = await page.evaluate<{
      pollingActive: boolean
      queuedWaiting: boolean
    }>(() => window.eval(`({
      pollingActive: _tagPollingActive,
      queuedWaiting: _tagQueuedWaiting,
    })`))
    expect(monitoringState).toEqual({ pollingActive: true, queuedWaiting: true })
    const modalLayout = await page.evaluate(() => {
      const modalRect = document.querySelector('#tag-modal .modal-content')?.getBoundingClientRect()
      const progressRect = document.querySelector('#tag-progress-container')?.getBoundingClientRect()
      const controlRects = [
        document.querySelector('#btn-cancel-tag')?.getBoundingClientRect(),
        document.querySelector('#btn-start-tag')?.getBoundingClientRect(),
      ].filter((rect): rect is DOMRect => Boolean(rect))
      const overlapArea = (left: DOMRect, right: DOMRect): number => (
        Math.max(0, Math.min(left.right, right.right) - Math.max(left.left, right.left))
        * Math.max(0, Math.min(left.bottom, right.bottom) - Math.max(left.top, right.top))
      )
      return {
        horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth,
        progressControlOverlap: progressRect
          ? Math.max(0, ...controlRects.map((rect) => overlapArea(progressRect, rect)))
          : null,
        progressVisibleWithinModal: Boolean(
          modalRect
          && progressRect
          && progressRect.top >= modalRect.top
          && progressRect.bottom <= modalRect.bottom,
        ),
      }
    })
    expect(modalLayout).toEqual({
      horizontalOverflow: false,
      progressControlOverlap: 0,
      progressVisibleWithinModal: true,
    })

    await page.locator('#btn-cancel-tag').click()
    await expect(page.locator('#tag-modal.visible')).toHaveCount(0)
    await expect(page.locator('#bg-tag-progress')).toBeVisible()
    await expect(page.locator('#bg-tag-progress-text')).toContainText(/queued or running|排队或运行/i)
    await page.locator('#bg-tag-open').click()
    await expect(page.locator('#tag-modal.visible')).toBeVisible()
    await expect(page.locator('#tag-progress-text')).toContainText(/queued or running|排队或运行/i)

    await expect.poll(() => progressPollsAfterStart, { timeout: 10000 }).toBe(5)
    expect(progressPollTimes[4] - progressPollTimes[3]).toBeGreaterThanOrEqual(4000)
    await expect.poll(() => progressPollsAfterStart, { timeout: 10000 }).toBe(6)
    expect(progressPollTimes[5] - progressPollTimes[4]).toBeGreaterThanOrEqual(4000)
    expect(await page.evaluate(() => (
      (window as typeof window & { __tagProgressWarningCount?: number }).__tagProgressWarningCount || 0
    ))).toBe(1)
    releaseTerminalProgress()

    await expect.poll(async () => page.evaluate(() => (
      (window as typeof window & { __taggingCompletedCount?: number }).__taggingCompletedCount || 0
    ))).toBe(1)
    await expect(page.locator('#tag-progress-container')).toBeHidden()
    await expect(page.locator('#btn-start-tag')).toBeEnabled()
    expect(startRequests).toBe(1)
    await page.waitForTimeout(1200)
    expect(progressPollsAfterStart).toBe(6)
    expect(await page.evaluate(() => (
      (window as typeof window & { __taggingCompletedCount?: number }).__taggingCompletedCount || 0
    ))).toBe(1)
    const terminalState = await page.evaluate<{
      pollingActive: boolean
      timerActive: boolean
    }>(() => window.eval(`({
      pollingActive: _tagPollingActive,
      timerActive: _tagProgressTimer !== null,
    })`))
    expect(terminalState).toEqual({ pollingActive: false, timerActive: false })
  })

  test('queued tag cancellation should stop polling and restore the Start action', async ({ page }) => {
    const probe = await mockQueuedGalleryTagCancellation(page, {
      status: 'idle',
      message: 'No tagging task is running',
      removed_queued: 1,
      owner_kind: 'gallery',
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await page.locator('#btn-tag').click()
    await expect(page.locator('#tag-modal.visible')).toBeVisible()
    await page.locator('#btn-start-tag').click()
    await expect(page.locator('#tag-progress-container')).toBeVisible()
    await page.locator('#btn-cancel-tag').click()
    await expect(page.locator('#tag-modal.visible')).toHaveCount(0)
    await expect(page.locator('#bg-tag-progress')).toBeVisible()
    await probe.waitForInFlightProgress()

    await page.locator('#bg-tag-cancel').click()

    await expect.poll(probe.cancelRequests).toBe(1)
    await expect(page.locator('#bg-tag-progress')).toBeHidden()
    await expect(
      page.locator('#toast-container .toast.info .toast-message').filter({ hasText: /cancelled|已取消/i }),
    ).toBeVisible()
    probe.releaseInFlightProgress()
    await expect.poll(probe.inFlightProgressResponses).toBe(1)
    await page.waitForTimeout(1200)
    expect(probe.progressPollsAfterCancel()).toBe(0)
    await expect(page.locator('#tag-progress-text')).not.toContainText(/Queued|排队/i)
    const terminalState = await page.evaluate<{
      pollingActive: boolean
      queuedWaiting: boolean
      timerActive: boolean
    }>(() => window.eval(`({
      pollingActive: _tagPollingActive,
      queuedWaiting: _tagQueuedWaiting,
      timerActive: _tagProgressTimer !== null,
    })`))
    expect(terminalState).toEqual({
      pollingActive: false,
      queuedWaiting: false,
      timerActive: false,
    })

    await page.locator('#btn-tag').click()
    await expect(page.locator('#tag-modal.visible')).toBeVisible()
    await expect(page.locator('#tag-progress-container')).toBeHidden()
    await expect(page.locator('#btn-start-tag')).toBeEnabled()
  })

  test('malformed queued tag cancellation data should fail explicitly', async ({ page }) => {
    const probe = await mockQueuedGalleryTagCancellation(page, {
      status: 'idle',
      message: 'No tagging task is running',
      removed_queued: '1',
      owner_kind: 'gallery',
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await page.locator('#btn-tag').click()
    await page.locator('#btn-start-tag').click()
    await expect(page.locator('#tag-progress-container')).toBeVisible()
    await page.locator('#btn-cancel-tag').click()
    await expect(page.locator('#bg-tag-progress')).toBeVisible()
    await probe.waitForInFlightProgress()

    await page.locator('#bg-tag-cancel').click()

    await expect(page.locator('#toast-container .toast.error .toast-message')).toContainText(/removed_queued|cancel response/i)
    probe.releaseInFlightProgress()
  })

  test('scan poller should survive the backend starting status window', async ({ page }) => {
    // Regression: the backend sets status='starting' synchronously when a scan
    // is requested and only flips to 'running' once the BackgroundTask runs.
    // pollScanProgress used to have no branch for 'starting', so a first poll
    // landing in that window silently killed the loop (frozen progress bar,
    // Start button stuck disabled, F5 the only recovery).
    let started = false
    let progressPolls = 0

    await page.route('**/api/scan', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      started = true
      progressPolls = 0
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'started', run_id: 991, source: 'manual' }),
      })
    })

    await page.route('**/api/scan/progress', async (route) => {
      if (!started) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'idle', run_id: 0, source: null, current: 0, total: 0, message: '' }),
        })
        return
      }

      progressPolls += 1
      if (progressPolls <= 2) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'starting', run_id: 991, source: 'manual', current: 0, total: 0, message: 'Starting...' }),
        })
        return
      }
      if (progressPolls <= 4) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'running', run_id: 991, source: 'manual', current: 5, total: 10, processed: 5, message: 'Importing images...' }),
        })
        return
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'done',
          run_id: 991,
          source: 'manual',
          current: 10,
          total: 10,
          processed: 10,
          new: 10,
          errors: 0,
          message: 'Import complete',
        }),
      })
    })

    await page.route('**/api/scan/acknowledge', async (route) => {
      if (route.request().method() !== 'POST') return route.fallback()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'acknowledged', run_id: 991, source: 'manual' }),
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-scan').click()
    await expect(page.locator('#scan-modal.visible')).toBeVisible()
    await page.locator('#scan-folder-path').fill('C:/fake/scan-poller-regression')

    const autoTag = page.locator('#scan-auto-tag')
    if (await autoTag.isChecked().catch(() => false)) {
      await page.locator('label:has(#scan-auto-tag) .checkbox-custom').click()
      await expect(autoTag).not.toBeChecked()
    }

    await page.locator('#btn-start-scan').click()

    // The poll loop must keep going through the 'starting' polls and reach the
    // mocked terminal 'done' state instead of dying after the first response.
    await expect.poll(() => progressPolls, {
      message: 'Expected the scan poll loop to survive the starting status and keep polling to done',
      timeout: 15000,
    }).toBeGreaterThanOrEqual(5)

    await expect(page.locator('#scan-modal.visible')).toHaveCount(0)
    await expect(page.locator('#btn-start-scan')).toBeEnabled()
  })

  test('should keep a custom tagger run on CPU when GPU stays off', async ({ page }) => {
    let capturedPayload: Record<string, unknown> | null = null

    await page.route('**/api/system-info', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          system_info: {
            total_ram_gb: 64,
            available_ram_gb: 48,
            gpu_name: 'NVIDIA GeForce RTX 4090',
            gpu_vram_total_mb: 24576,
            gpu_vram_available_mb: 22000,
            torch_cuda_available: true,
            onnx_providers: ['CUDAExecutionProvider', 'CPUExecutionProvider'],
          },
          recommendation: {
            recommended_batch_size: 12,
            recommended_cpu_chunk_size: 32,
            recommended_use_gpu: true,
            recommended_session_refresh_interval: 180,
            risk_level: 'low',
            message: 'Sufficient VRAM for aggressive batched GPU inference.',
          },
        }),
      })
    })

    await page.route('**/api/tag/start', async (route) => {
      capturedPayload = route.request().postDataJSON()
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'started' }),
      })
    })

    await page.route('**/api/tag/progress', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'done',
          processed: 0,
          total: 0,
          tagged: 0,
          errors: 0,
          message: 'Tagging complete',
        }),
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-tag').click()
    await expect(page.locator('#tag-modal.visible')).toBeVisible()

    await page.locator('#tag-model-select').selectOption('custom')
    await page.locator('#tag-model-path').fill('C:/models/custom-model.onnx')
    await page.locator('#tag-tags-path').fill('C:/models/selected_tags.csv')
    await expect(page.locator('#tag-use-gpu')).toBeChecked()
    await page.locator('#tag-runtime-advanced summary').click()
    await expect(page.locator('#tag-runtime-advanced')).toHaveAttribute('open', '')
    await page.locator('label:has(#tag-use-gpu) .checkbox-custom').click()
    await expect(page.locator('#tag-use-gpu')).not.toBeChecked()
    await expect(page.locator('#tag-gpu-help')).toContainText(/CPU mode|gpuHelpCustomCpu/i)

    await page.locator('#btn-start-tag').click()
    await expect(page.locator('#confirm-modal.visible')).toHaveCount(0)

    await expect.poll(() => capturedPayload, {
      message: 'Expected the tag start payload for custom CPU mode',
    }).not.toBeNull()

    expect(capturedPayload).toMatchObject({
      model_path: 'C:/models/custom-model.onnx',
      tags_path: 'C:/models/selected_tags.csv',
      use_gpu: false,
      allow_unsafe_acceleration: false,
    })
    await expect(page.locator('#tag-use-gpu')).not.toBeChecked()
  })

  test('gallery context menu should expose useful single-image actions without permanent delete', async ({ page }) => {
    await mockGalleryImages(page, [
      { id: 151, filename: 'context-menu.png', checkpoint: 'context-model.safetensors' },
    ])

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const image = page.locator('#gallery-grid .gallery-item[data-id="151"]')
    await expect(image).toBeVisible()
    await image.click({ button: 'right' })

    const menu = page.locator('.gallery-context-menu')
    await expect(menu).toBeVisible()
    await expect(menu).toContainText('Preview')
    await expect(menu).toContainText('Select Image')
    await expect(menu).toContainText('Move...')
    await expect(menu).toContainText('Copy...')
    await expect(menu).toContainText('Send to Censor')
    await expect(menu).toContainText('Find Similar')
    await expect(menu).toContainText('Prompt Helper')
    await expect(menu).toContainText('Metadata / Generation Info')
    await expect(menu).toContainText('Filter by Checkpoint')
    await expect(menu).toContainText('Open in Folder')
    await expect(menu).toContainText('Copy Path')
    await expect(menu).toContainText('Remove from Gallery')
    await expect(menu).toContainText('Move to Trash')
    await expect(menu).not.toContainText('Delete from Disk')

    await menu.getByRole('menuitem', { name: /Select Image/ }).click()
    await expect(page.locator('#selection-actions')).toBeVisible()
    await expect(image).toHaveClass(/selected/)
  })

  test('gallery preview inspector should keep scroll position when switching images', async ({ page }) => {
    const imageIds = [171, 172]
    const longPrompt = Array.from({ length: 100 }, (_, index) => `modal_tag_${index}`).join(', ')
    const imageDetail = (id: number) => ({
      id,
      filename: `modal-reader-${id}.png`,
      path: `L:/modal/modal-reader-${id}.png`,
      generator: 'webui',
      prompt: longPrompt,
      negative_prompt: 'lowres, blurry',
      width: 1024,
      height: 1536,
      file_size: 123456,
      checkpoint: 'modal-model.safetensors',
      metadata_json: {
        _parsed: {
          generation_params: {
            seed: String(1000 + id),
            steps: '30',
            cfg_scale: '7.5',
            sampler: 'Euler a',
            scheduler: 'Normal',
            denoise: '0.45',
            ...Object.fromEntries(Array.from({ length: 40 }, (_, index) => [`extra_${index}`, `value_${index}`])),
          },
        },
      },
    })

    await mockGalleryImages(page, imageIds.map((id) => imageDetail(id)))
    await page.route('**/api/images/*', async (route) => {
      const id = Number(new URL(route.request().url()).pathname.split('/').pop())
      if (!imageIds.includes(id)) {
        await route.continue()
        return
      }
      await route.fulfill({
        json: {
          image: imageDetail(id),
          tags: Array.from({ length: 70 }, (_, index) => ({ tag: `tag_${index}`, confidence: 0.9 })),
        },
      })
    })

    await openMainPage(page)
    await page.evaluate(async () => {
      await window.Gallery.openPreview(171)
    })
    await expect(page.locator('#image-modal.visible')).toBeVisible()
    await expect(page.locator('#modal-filename')).toContainText('modal-reader-171.png')
    await expect(page.locator('#modal-key-params')).toBeVisible()
    await expect(page.locator('#modal-copy-menu')).toBeVisible()
    await expect(page.locator('#modal-tools-menu')).toBeVisible()
    await expect(page.locator('.modal-handoff-row')).toBeHidden()
    await expect(page.locator('.modal-analysis-row')).toBeHidden()

    const beforeScroll = await page.evaluate(() => {
      const info = (document.querySelector('#image-modal .modal-info-scroll') as HTMLElement | null)
        || (document.querySelector('#image-modal .modal-info') as HTMLElement | null)
      if (!info) return 0
      info.scrollTop = Math.min(520, Math.max(0, info.scrollHeight - info.clientHeight))
      return info.scrollTop
    })
    expect(beforeScroll).toBeGreaterThan(100)

    await page.locator('#modal-next-image').click()
    await expect(page.locator('#modal-filename')).toContainText('modal-reader-172.png')
    await expect.poll(async () => {
      return await page.evaluate(() => {
        const info = (document.querySelector('#image-modal .modal-info-scroll') as HTMLElement | null)
          || (document.querySelector('#image-modal .modal-info') as HTMLElement | null)
        return info?.scrollTop || 0
      })
    }).toBeGreaterThan(100)
  })

  test('gallery context menu should stay visible at the right and bottom viewport edges', async ({ page }) => {
    const viewport = { width: 1366, height: 768 }
    await page.setViewportSize(viewport)
    await mockGalleryImages(page, [
      { id: 153, filename: 'context-edge.png', checkpoint: 'context-model.safetensors' },
    ])

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const image = page.locator('#gallery-grid .gallery-item[data-id="153"]')
    await expect(image).toBeVisible()
    await image.evaluate((element: HTMLElement) => {
      Object.assign(element.style, {
        position: 'fixed',
        right: '16px',
        top: '150px',
        width: '180px',
        height: '180px',
        zIndex: '20',
      })
    })
    const imageBox = await image.boundingBox()
    expect(imageBox).toBeTruthy()
    const clickX = imageBox!.x + imageBox!.width - 2
    const clickY = imageBox!.y + imageBox!.height / 2
    await image.evaluate((element, point) => {
      element.dispatchEvent(new MouseEvent('contextmenu', {
        bubbles: true,
        cancelable: true,
        button: 2,
        clientX: point.x,
        clientY: point.y,
      }))
    }, { x: clickX, y: clickY })

    const menu = page.locator('.gallery-context-menu')
    await expect(menu).toBeVisible()
    const box = await menu.boundingBox()
    expect(box).toBeTruthy()
    expect(box!.x).toBeGreaterThanOrEqual(7)
    expect(box!.y).toBeGreaterThanOrEqual(7)
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width - 7)
    expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height - 7)
    // P3-6: the 420px cap is gone — the menu may use the viewport height.
    expect(box!.height).toBeLessThanOrEqual(viewport.height - 14)
    expect(box!.x + box!.width).toBeLessThanOrEqual(imageBox!.x - 4)
    expect(clickY).toBeGreaterThanOrEqual(box!.y)
    expect(clickY).toBeLessThanOrEqual(box!.y + box!.height)
    await expect(menu).toHaveCSS('overflow-y', 'auto')
  })

  test('gallery context menu should stay near right-side images on 2k ui scale', async ({ page }) => {
    await page.setViewportSize({ width: 2560, height: 1440 })
    await mockGalleryImages(page, [
      { id: 154, filename: 'context-edge-2k.png', checkpoint: 'context-model.safetensors' },
    ])

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const image = page.locator('#gallery-grid .gallery-item[data-id="154"]')
    await expect(image).toBeVisible()
    await image.evaluate((element: HTMLElement) => {
      Object.assign(element.style, {
        position: 'fixed',
        right: '16px',
        top: '180px',
        width: '220px',
        height: '220px',
        zIndex: '20',
      })
    })
    const imageBox = await image.boundingBox()
    expect(imageBox).toBeTruthy()
    const clickX = imageBox!.x + imageBox!.width - 4
    const clickY = imageBox!.y + imageBox!.height / 2
    await page.mouse.click(clickX, clickY, { button: 'right' })

    const menu = page.locator('.gallery-context-menu')
    await expect(menu).toBeVisible()
    const box = await menu.boundingBox()
    expect(box).toBeTruthy()
    expect(box!.x).toBeGreaterThanOrEqual(7)
    expect(box!.y).toBeGreaterThanOrEqual(7)
    expect(box!.x + box!.width).toBeLessThanOrEqual(2553)
    // P3-6: no 420px cap — on a tall viewport every item is visible without
    // internal scrolling.
    expect(box!.height).toBeLessThanOrEqual(1424)
    const menuOverflow = await menu.evaluate((el) => el.scrollHeight - el.clientHeight)
    expect(menuOverflow).toBeLessThanOrEqual(2)
    expect(box!.x + box!.width).toBeLessThanOrEqual(imageBox!.x - 4)
    expect(clickY).toBeGreaterThanOrEqual(box!.y)
    expect(clickY).toBeLessThanOrEqual(box!.y + box!.height)
    await expect(menu).toHaveCSS('overflow-y', 'auto')
  })

  test('selection and right-click collection pickers should open inside the viewport', async ({ page }) => {
    await page.setViewportSize({ width: 2560, height: 1440 })
    await page.route('**/api/collections', async (route) => {
      await route.fulfill({
        json: {
          collections: [
            { id: 1, name: 'Favorites', slug: 'favorites', item_count: 0 },
            { id: 2, name: 'Training set', slug: 'training-set', item_count: 4 },
          ],
        },
      })
    })
    await mockGalleryImages(page, [
      { id: 155, filename: 'collection-edge.png', checkpoint: 'context-model.safetensors' },
    ])

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await page.evaluate(() => (window as any).App.setSelectionMode(true))

    const image = page.locator('#gallery-grid .gallery-item[data-id="155"]')
    await image.click()
    const selectionButton = page.locator('#btn-add-selected-to-collection')
    await expect(selectionButton).toBeEnabled()
    await selectionButton.click()
    await expect(page.locator('.collections-picker-menu')).toBeVisible()
    await page.keyboard.press('Escape')

    await image.evaluate((element: HTMLElement) => {
      Object.assign(element.style, {
        position: 'fixed',
        right: '12px',
        bottom: '12px',
        width: '220px',
        height: '220px',
        zIndex: '20',
      })
    })
    const imageBox = await image.boundingBox()
    expect(imageBox).toBeTruthy()
    await image.evaluate((element, point) => {
      element.dispatchEvent(new MouseEvent('contextmenu', {
        bubbles: true,
        cancelable: true,
        button: 2,
        clientX: point.x,
        clientY: point.y,
      }))
    }, {
      x: imageBox!.x + imageBox!.width - 4,
      y: imageBox!.y + imageBox!.height - 4,
    })
    await page.getByRole('menuitem', { name: /Add to collection/i }).click()

    const picker = page.locator('.collections-picker-menu')
    await expect(picker).toBeVisible()
    const pickerBox = await picker.boundingBox()
    expect(pickerBox).toBeTruthy()
    expect(pickerBox!.x).toBeGreaterThanOrEqual(7)
    expect(pickerBox!.y).toBeGreaterThanOrEqual(7)
    expect(pickerBox!.x + pickerBox!.width).toBeLessThanOrEqual(2553)
    expect(pickerBox!.y + pickerBox!.height).toBeLessThanOrEqual(1433)
  })

  test('dataset split view should render two usable caption cards', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 })
    await page.route('**/api/image-thumbnail/**', async (route) => {
      await route.fulfill({ status: 204 })
    })
    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await page.waitForFunction(() => typeof (window as any).DatasetMaker?._buildSplitCard === 'function')
    await page.evaluate(() => {
      const dm = (window as any).DatasetMaker
      dm.imageIds = [301, 302, 303]
      dm.meta.set(301, { filename: 'current.png' })
      dm.meta.set(302, { filename: 'next.png' })
      dm.meta.set(303, { filename: 'third.png' })
      dm.captions.set(301, '1girl, standing')
      dm.captions.set(302, '1girl, sitting')
      dm.nlCaptions.set(301, 'A person is standing.')
      dm.nlCaptions.set(302, 'A person is sitting.')
      ;(window as any).App.switchView('dataset')
      dm._setActive(301)
    })

    await page.locator('#dataset-tab-workbench').click()
    await expect(page.locator('#btn-dataset-split-view')).toBeVisible()
    await page.locator('#btn-dataset-split-view').click()
    const panel = page.locator('#dataset-split-panel')
    await expect(panel).toBeVisible()
    await expect(panel.locator('.dataset-split-card')).toHaveCount(2)
    await expect(panel).toContainText('current.png')
    await expect(panel).toContainText('next.png')
    const panelBox = await panel.boundingBox()
    expect(panelBox).toBeTruthy()
    expect(panelBox!.height).toBeGreaterThanOrEqual(400)

    const nextBooru = panel.locator('.dataset-split-card').nth(1).locator('.dataset-split-textarea').first()
    await nextBooru.fill('1girl, sitting, outdoors')
    await expect.poll(async () => page.evaluate(() => {
      return (window as any).DatasetMaker.captionEdits.get(302)
    })).toBe('1girl, sitting, outdoors')

    await panel.getByRole('button', { name: /Switch to this one/i }).click()
    await expect(panel).toContainText('third.png')
  })

  test('dataset workbench should keep the right action pane reachable at laptop width', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 })
    await page.route('**/api/image-thumbnail/**', async (route) => {
      await route.fulfill({ status: 204 })
    })
    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await page.waitForFunction(() => typeof (window as any).DatasetMaker?._setActive === 'function')
    await page.evaluate(() => {
      const dm = (window as any).DatasetMaker
      dm.imageIds = [401]
      dm.meta.set(401, { filename: 'reachable-actions.png', width: 1024, height: 1024 })
      dm.captions.set(401, '1girl, standing, simple_background')
      dm.nlCaptions.set(401, 'A person stands against a simple background.')
      ;(window as any).App.switchView('dataset')
      dm._setActive(401)
    })

    await page.locator('#dataset-tab-workbench').click()
    const rightPane = page.locator('#view-dataset .dataset-export-pane')
    await expect(rightPane).toBeVisible()
    await expect(page.locator('#btn-dataset-smart-tag')).toBeVisible()

    const fit = await page.evaluate(() => {
      const view = document.getElementById('view-dataset')
      const workbench = document.querySelector('#view-dataset .dataset-workbench')
      const editorControls = document.querySelector('#view-dataset .dataset-editor-controls')
      const right = document.querySelector('#view-dataset .dataset-export-pane')
      if (!view || !workbench || !editorControls || !right) return null
      const viewBox = view.getBoundingClientRect()
      const workbenchBox = workbench.getBoundingClientRect()
      const controlsBox = editorControls.getBoundingClientRect()
      const rightBox = right.getBoundingClientRect()
      return {
        viewRight: viewBox.right,
        workbenchRight: workbenchBox.right,
        controlsRight: controlsBox.right,
        rightLeft: rightBox.left,
        rightRight: rightBox.right,
        rightWidth: rightBox.width,
        controlsWidth: controlsBox.width,
      }
    })
    expect(fit).not.toBeNull()
    expect(fit!.rightRight).toBeLessThanOrEqual(fit!.viewRight + 1)
    expect(fit!.rightRight).toBeLessThanOrEqual(fit!.workbenchRight + 1)
    expect(fit!.rightLeft).toBeGreaterThan(fit!.controlsRight)
    expect(fit!.rightWidth).toBeGreaterThanOrEqual(280)
    expect(fit!.controlsWidth).toBeGreaterThanOrEqual(240)
  })

  test('dataset caption workflow keeps setup generate review and cleanup readable at 1366x768', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 })
    await page.route('**/api/image-thumbnail/**', async (route) => {
      await route.fulfill({ status: 204 })
    })
    await page.route('**/api/tags/export-preview', async (route) => {
      const body = route.request().postDataJSON() as {
        image_ids?: number[]
        trigger?: string
        append?: string[]
      }
      const imageIds = Array.isArray(body.image_ids) ? body.image_ids.map(Number) : []
      const parts = [
        String(body.trigger || '').trim(),
        '1girl',
        'standing',
        'simple_background',
        ...(Array.isArray(body.append) ? body.append : []),
      ]
      const seen = new Set<string>()
      const rendered = parts.filter((part) => {
        const key = String(part).replace(/[\s_]+/g, ' ').trim().toLowerCase()
        if (!key || seen.has(key)) return false
        seen.add(key)
        return true
      }).join(', ')
      await route.fulfill({
        json: {
          results: imageIds.map((imageId) => ({
            image_id: imageId,
            filename: 'caption-flow.png',
            rendered,
          })),
        },
      })
    })
    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await page.waitForFunction(() => typeof (window as any).DatasetMaker?._setActive === 'function')
    await page.evaluate(() => {
      const dm = (window as any).DatasetMaker
      dm.imageIds = [421]
      dm.meta.set(421, { filename: 'caption-flow.png', width: 1024, height: 1024 })
      dm.captions.set(421, '1girl, standing, simple_background')
      dm.nlCaptions.set(421, 'A person stands against a simple background.')
      ;(window as any).App.switchView('dataset')
      dm._setActive(421)
    })

    await page.locator('#dataset-tab-workbench').click()
    await expect(page.locator('#dataset-trigger')).toBeInViewport()
    await expect(page.locator('#dataset-common-tags')).toBeInViewport()
    await expect(page.locator('#btn-dataset-smart-tag')).toBeInViewport()
    await expect(page.locator('.dataset-review-card .dataset-card-title')).toBeInViewport()
    await expect(page.locator('#dataset-step-cleanup > summary')).toBeInViewport()

    const quickFillFit = await page.locator('#dataset-step-setup > .dataset-quickfill-row').evaluate((row) => {
      const buttons = Array.from(row.querySelectorAll('button'))
      const rects = buttons.map((button) => button.getBoundingClientRect())
      return {
        buttonCount: buttons.length,
        textClipped: buttons.map((button) => (
          button.scrollWidth > button.clientWidth || button.scrollHeight > button.clientHeight
        )),
        overlaps: rects.some((left, index) => rects.slice(index + 1).some((right) => (
          left.left < right.right
          && left.right > right.left
          && left.top < right.bottom
          && left.bottom > right.top
        ))),
      }
    })
    expect(quickFillFit).toEqual({
      buttonCount: 3,
      textClipped: [false, false, false],
      overlaps: false,
    })

    const rightPane = page.locator('#view-dataset .dataset-export-pane')
    const initialPaneBox = await rightPane.boundingBox()
    expect(initialPaneBox).toBeTruthy()

    await page.locator('#dataset-trigger').fill('ux_test_trigger')
    await page.locator('#btn-dataset-quickfill-trigger').click()
    await expect(page.locator('#dataset-common-tags')).toHaveValue(/ux_test_trigger/)

    await page.locator('#dataset-step-cleanup').click()
    await expect(page.locator('#dataset-step-cleanup')).toHaveAttribute('open', '')
    const cleanupBox = await page.locator('#dataset-step-cleanup').boundingBox()
    expect(cleanupBox).toBeTruthy()
    expect(cleanupBox!.height).toBeGreaterThanOrEqual(300)
    expect(cleanupBox!.height).toBeLessThanOrEqual(370)
    await expect(page.locator('.dataset-custom-dropdown[data-select-id="dataset-caption-scope"]')).toBeVisible()
    await expect(page.locator('#dataset-blacklist')).toBeVisible()

    await page.locator('#dataset-step-findreplace').scrollIntoViewIfNeeded()
    await page.locator('#dataset-step-findreplace').click()
    await expect(page.locator('#dataset-step-findreplace')).toHaveAttribute('open', '')
    await expect(page.locator('#dataset-find-input')).toBeVisible()
    await expect(page.locator('#dataset-replace-input')).toBeVisible()
    await expect(page.locator('#btn-dataset-find-replace')).toBeVisible()
  })

  test('dataset Smart Tag should submit existing-caption and VLM grounding options', async ({ page }) => {
    await mockGalleryImages(page, [{ id: 711, filename: 'smart-tag-payload.png' }])
    await mockTaggerCatalog(page)
    await page.route('**/api/vlm/settings', async (route) => {
      await route.fulfill({ json: { endpoint: 'https://example.invalid/v1', use_vertex: false } })
    })
    await page.route('**/api/smart-tag/progress**', async (route) => {
      await route.fulfill({ json: { status: 'idle' } })
    })
    let startPayload: any = null
    await page.route('**/api/smart-tag/start', async (route) => {
      startPayload = route.request().postDataJSON()
      await route.fulfill({
        json: {
          job_id: 'smart-payload-test',
          status: 'completed',
          total: 1,
          processed: 1,
          succeeded: 1,
          failed: 0,
          settings: startPayload,
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await page.evaluate(() => {
      const dm = (window as any).DatasetMaker
      dm.imageIds = [711]
      dm.meta.set(711, { filename: 'smart-tag-payload.png' })
      dm.captions.set(711, 'existing, caption')
      ;(window as any).App.switchView('dataset')
      dm._setActive(711)
    })
    await page.locator('#dataset-tab-workbench').click()
    await page.locator('#btn-dataset-smart-tag').click()
    await expect(page.locator('#smart-tag-modal.visible')).toBeVisible()
    await page.locator('#smart-tag-merge').selectOption('append')
    await page.locator('#smart-tag-skip-existing').uncheck()
    await page.locator('#smart-tag-vlm-grounding').uncheck()
    await page.locator('#btn-smart-tag-run').click()

    await expect.poll(() => startPayload).not.toBeNull()
    expect(startPayload).toMatchObject({
      image_ids: [711],
      merge_strategy: 'append',
      skip_existing: false,
      vlm_grounding: false,
      enable_wd14: true,
      enable_vlm: true,
      natural_language_mode: 'vlm',
    })
  })

  test('gallery category copy menu should copy clean purpose prompts', async ({ page }) => {
    await page.addInitScript(() => {
      ;(window as any).__clipboardText = ''
      Object.defineProperty(navigator, 'clipboard', {
        configurable: true,
        value: {
          writeText: async (text: string) => {
            ;(window as any).__clipboardText = String(text)
          },
        },
      })
    })

    const promptTags = ['masterpiece', 'best_quality', '1girl', 'blue_hair', 'school_uniform', 'standing', 'city', 'future_tag']
    const categoryMap: Record<string, string> = {
      masterpiece: 'quality',
      best_quality: 'quality',
      '1girl': 'character',
      blue_hair: 'body',
      school_uniform: 'outfit',
      standing: 'pose',
      city: 'background',
      future_tag: 'future_category',
    }

    await page.route('**/api/prompts/categorize', async (route) => {
      const tags = route.request().postDataJSON() as string[]
      await route.fulfill({
        json: {
          results: tags.map((tag) => ({ tag, category: categoryMap[tag] || 'unknown' })),
        },
      })
    })

    await mockGalleryImages(page, [
      {
        id: 152,
        filename: 'purpose-copy.png',
        prompt: promptTags.join(', '),
        checkpoint: 'context-model.safetensors',
      },
    ])

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const image = page.locator('#gallery-grid .gallery-item[data-id="152"]')
    await expect(image).toBeVisible()
    await image.click({ button: 'right' })

    const contextMenu = page.locator('.gallery-context-menu')
    await expect(contextMenu).toBeVisible()
    await contextMenu.getByRole('menuitem', { name: /Copy Tag Category/ }).click()

    const copyMenu = page.locator('.tag-category-copy-menu')
    await expect(copyMenu).toBeVisible()
    await expect(copyMenu).toContainText('Purpose prompts')
    await expect(copyMenu).toContainText('Clean training caption')
    await expect(copyMenu.locator('.tag-category-copy-item[data-group="unclassified"] .tag-category-copy-count')).toHaveText('1')
    await copyMenu.locator('[data-purpose="trainingCaption"]').click()

    await expect.poll(async () => {
      return await page.evaluate(() => (window as any).__clipboardText || '')
    }).toBe('1girl, blue_hair, school_uniform, standing, city')
  })

  test('should only enable selection actions after at least one image is selected', async ({ page }) => {
    await mockImageAsset(page, 101)
    await page.route('**/api/images**', async (route) => {
      const pathname = new URL(route.request().url()).pathname
      if (pathname !== '/api/images') {
        await route.continue()
        return
      }

      await route.fulfill({
        json: {
          images: [
            { id: 101, filename: 'selection-smoke.png', path: 'L:/selection-smoke.png', prompt: 'selection smoke' },
          ],
          total: 1,
          has_more: false,
          next_cursor: null,
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const selectionFab = page.locator('#selection-actions')
    const actionBar = page.locator('#gallery-action-bar')
    await expect(selectionFab).toBeHidden()
    await expect(actionBar).toBeHidden()

    await page.locator('#btn-toggle-select').click()
    await expect(selectionFab).toBeVisible()
    await expect(page.locator('#selection-scope-summary')).toContainText('Selected manually from Gallery')
    // Aurora Phase 3: with zero images selected the batch actions are not
    // just disabled — the whole action bar stays hidden.
    await expect(actionBar).toBeHidden()

    const firstGalleryItem = page.locator('#gallery-grid .gallery-item').first()
    await expect(firstGalleryItem).toBeVisible()
    await firstGalleryItem.click()

    await expect(selectionFab).toBeVisible()
    await expect(actionBar).toBeVisible()
    await expect(page.locator('#btn-move-selected')).toBeEnabled()
    await expect(page.locator('#btn-send-to-censor')).toBeEnabled()
    await openSelectionPanelSection(page, 'Export')
    await expect(page.locator('#btn-export-selected')).toBeVisible()
    await expect(page.locator('#btn-export-selected')).toBeEnabled()
    await expect(page.locator('#btn-copy-selected')).toBeEnabled()
    await expect(page.locator('#btn-delete-selected-files')).toBeEnabled()

    await page.locator('#btn-toggle-select').click()
    await expect(selectionFab).toBeHidden()
    await expect(actionBar).toBeHidden()
  })

  test('desktop batch and metadata windows keep primary actions reachable at 1366x768', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 })
    await mockGalleryImages(page, [
      { id: 181, filename: 'layout-1.png', width: 820, height: 800 },
      { id: 182, filename: 'layout-2.png', width: 900, height: 500 },
      { id: 183, filename: 'layout-3.png', width: 640, height: 900 },
      { id: 184, filename: 'layout-4.png', width: 1024, height: 768 },
      { id: 185, filename: 'layout-5.png', width: 768, height: 1024 },
      { id: 186, filename: 'layout-6.png', width: 800, height: 800 },
    ])

    await page.route('**/api/images/181', async (route) => {
      await route.fulfill({
        json: {
          image: buildMockGalleryImage(181, {
            filename: 'layout-1.png',
            path: 'L:/layout/layout-1.png',
            width: 820,
            height: 800,
            file_size: 8192,
            metadata_json: {
              _parsed: {
                generation_params: {
                  seed: '123',
                  steps: '28',
                  cfg_scale: '7',
                  sampler: 'Euler a',
                  model: 'layout-model.safetensors',
                },
              },
            },
          }),
          tags: [],
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-toggle-select').click()
    await page.locator('#gallery-grid .gallery-item[data-id="181"]').click()
    await page.locator('#gallery-grid .gallery-item[data-id="182"]').click()
    // (Aurora Phase 3: the export/danger disclosures left the sidebar — batch
    // actions live in the bottom action bar now, so the sidebar no longer
    // grows when actions are revealed.)

    const toggleBox = await page.locator('#btn-toggle-select').boundingBox()
    const sidebarBox = await page.locator('.filter-sidebar').boundingBox()
    expect(toggleBox).toBeTruthy()
    expect(sidebarBox).toBeTruthy()
    expect(toggleBox!.y).toBeGreaterThanOrEqual(sidebarBox!.y - 1)
    expect(toggleBox!.y + toggleBox!.height).toBeLessThanOrEqual(sidebarBox!.y + sidebarBox!.height + 1)
    await expect(page.locator('#btn-toggle-select')).toBeInViewport()

    await page.locator('#btn-toggle-select').click()
    await expect(page.locator('#selection-actions')).toBeHidden()

    await page.locator('#gallery-grid .gallery-item[data-id="181"]').click()
    await expect(page.locator('#image-modal.visible')).toBeVisible()
    await page.locator('#btn-edit-metadata').click()
    await expect(page.locator('#metadata-editor-modal.visible')).toBeVisible()

    const actionsBox = await page.locator('#metadata-editor-modal .modal-actions').boundingBox()
    expect(actionsBox).toBeTruthy()
    expect(actionsBox!.y + actionsBox!.height).toBeLessThanOrEqual(768)
    await expect(page.locator('#btn-meta-edit-cancel')).toBeVisible()
    await expect(page.locator('#btn-meta-edit-save')).toBeVisible()
  })

  test('desktop censor and artist workspaces keep window actions reachable at 1366x768', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 })
    await mockGalleryImages(page, [
      { id: 191, filename: 'desktop-censor-artist.png', width: 820, height: 800 },
    ])
    await mockArtistDiagnosticsReady(page)

    await page.route('**/api/artists/stats', async (route) => {
      await route.fulfill({
        json: {
          total_images: 1,
          identified_images: 0,
          undefined_count: 0,
          artist_counts: {},
        },
      })
    })

    await page.route('**/api/censor/models', async (route) => {
      await route.fulfill({
        json: {
          status: 'ok',
          recommended_backend: 'both',
          models: [
            {
              id: 'legacy',
              name: 'Legacy YOLO',
              available: true,
              recommended: true,
              default_model_path: 'C:/models/wenaka_yolov8s-seg.onnx',
              files: [],
            },
            { id: 'nudenet', name: 'NudeNet v3', available: true, recommended: true },
            { id: 'sam3', name: 'SAM 3', available: true, recommended: true },
          ],
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-toggle-select').click()
    await page.locator('#gallery-grid .gallery-item[data-id="191"]').click()
    await page.locator('#btn-send-to-censor').click()
    await expect(page.locator('#view-censor.active')).toBeVisible()

    await page.locator('#btn-open-detect-modal').click()
    await expect(page.locator('#detect-modal.visible')).toBeVisible()
    await page.locator('#censor-advanced-model-picker').click()
    await page.locator('#censor-show-advanced-models-row').click()
    await expect(page.locator('#censor-show-advanced-models')).toBeChecked()
    await expect(page.locator('#censor-model-path')).toBeVisible()
    await expect(page.locator('#btn-auto-detect-current-modal')).toBeInViewport()

    // Expanding the advanced picker rows can focus-scroll the modal body,
    // pushing the header out of the scroll viewport for a moment. The contract
    // under test is reachability, so bring the close button back into view
    // (no-op when it never scrolled away) before measuring geometry.
    await page.locator('#btn-close-detect-modal').evaluate((node) => node.scrollIntoView({ block: 'nearest' }))

    const detectModalBox = await page.locator('#detect-modal .modal-content').boundingBox()
    const detectCloseBox = await page.locator('#btn-close-detect-modal').boundingBox()
    const detectActionBox = await page.locator('#btn-auto-detect-current-modal').boundingBox()
    expect(detectModalBox).toBeTruthy()
    expect(detectCloseBox).toBeTruthy()
    expect(detectActionBox).toBeTruthy()
    expect(detectModalBox!.y).toBeGreaterThanOrEqual(0)
    expect(detectModalBox!.y + detectModalBox!.height).toBeLessThanOrEqual(768)
    expect(detectCloseBox!.y).toBeGreaterThanOrEqual(detectModalBox!.y)
    expect(detectActionBox!.y + detectActionBox!.height).toBeLessThanOrEqual(768)
    await page.locator('#btn-close-detect-modal').click()

    await page.locator('#btn-save-all-processed').scrollIntoViewIfNeeded()
    await page.locator('#btn-save-all-processed').click()
    await expect(page.locator('#save-options-modal.visible')).toBeVisible()
    await expect(page.locator('#btn-cancel-save-options')).toBeInViewport()
    await expect(page.locator('#btn-confirm-save-options')).toBeInViewport()
    const saveModalBox = await page.locator('#save-options-modal .modal-content').boundingBox()
    expect(saveModalBox).toBeTruthy()
    expect(saveModalBox!.y).toBeGreaterThanOrEqual(0)
    expect(saveModalBox!.y + saveModalBox!.height).toBeLessThanOrEqual(768)
    await page.locator('#btn-cancel-save-options').click()

    await openView(page, 'artist')
    await expect(page.locator('#view-artist.active')).toBeVisible()
    const startCard = page.locator('#artist-start-card')
    if (await startCard.isVisible().catch(() => false)) {
      await page.locator('#artist-start-dismiss').click()
    }

    await expect(page.locator('#btn-identify-all')).toBeVisible()
    await expect(page.locator('#btn-refresh-artist-stats')).toBeVisible()
    await expect(page.locator('#btn-clear-artist-data')).toBeVisible()
    await expect(page.locator('#btn-identify-all')).toBeInViewport()
    await expect(page.locator('#btn-clear-artist-data')).toBeInViewport()

    const artistControlsBox = await page.locator('.artist-controls').boundingBox()
    const clearArtistBox = await page.locator('#btn-clear-artist-data').boundingBox()
    expect(artistControlsBox).toBeTruthy()
    expect(clearArtistBox).toBeTruthy()
    expect(artistControlsBox!.y + artistControlsBox!.height).toBeLessThanOrEqual(768)
    expect(clearArtistBox!.y + clearArtistBox!.height).toBeLessThanOrEqual(768)
  })

  test('desktop prompt helper keeps random output actions reachable at 1366x768', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 })
    await page.addInitScript(() => {
      localStorage.setItem('promptlab-guide-seen', 'true')
    })

    await page.route('**/api/prompts/categories', async (route) => {
      await route.fulfill({
        json: {
          categories: {
            character: ['1girl', '1boy'],
            outfit: ['school_uniform', 'maid_outfit'],
            pose: ['standing', 'sitting'],
            expression: ['smile', 'serious'],
            angle: ['full_body', 'close-up'],
            background: ['city', 'beach'],
            style: ['cinematic_lighting', 'watercolor'],
            artist: ['artist:mock'],
            quality: ['masterpiece', 'best_quality'],
            meta: ['solo'],
          },
        },
      })
    })
    await page.route('**/api/prompts/sets', async (route) => {
      await route.fulfill({ json: { sets: [] } })
    })
    await page.route('**/api/prompts/exclusions', async (route) => {
      await route.fulfill({ json: { rules: [] } })
    })
    await page.route('**/api/prompts/presets', async (route) => {
      await route.fulfill({ json: { presets: [] } })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openView(page, 'promptlab')
    await expect(page.locator('#view-promptlab.active')).toBeVisible()
    await page.locator('.promptlab-tab[data-mode="random"]').click()

    await expect(page.locator('#view-promptlab .promptlab-random-layout')).toBeInViewport()
    await expect(page.locator('#view-promptlab .promptlab-output-panel')).toBeInViewport()
    await expect(page.locator('#btn-promptlab-generate')).toBeInViewport()
    await expect(page.locator('#btn-promptlab-copy')).toBeInViewport()
    await expect(page.locator('#btn-promptlab-validate')).toBeInViewport()
    await expect(page.locator('#btn-promptlab-save-preset')).toBeInViewport()
    await expect(page.locator('#promptlab-output')).toBeInViewport()
  })

  test('desktop long setup modals keep close buttons reachable after scrolling', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 })
    await mockGalleryImages(page, [{ id: 712, filename: 'smart-tag-scroll.png' }])
    await mockTaggerCatalog(page)
    await page.route('**/api/vlm/settings', async (route) => {
      await route.fulfill({ json: { endpoint: 'https://example.invalid/v1', use_vertex: false } })
    })
    await page.route('**/api/smart-tag/progress**', async (route) => {
      await route.fulfill({ json: { status: 'idle' } })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-open-model-manager').click()
    await expect(page.locator('#model-manager-modal.visible')).toBeVisible()
    await page.locator('#model-manager-modal .modal-content').hover()
    await page.mouse.wheel(0, 2400)
    await expect(page.locator('#model-manager-close')).toBeInViewport()
    await expect.poll(async () =>
      page.locator('#model-manager-close').evaluate((element) => getComputedStyle(element).position)
    ).toBe('sticky')
    await page.locator('#model-manager-close').click()
    await expect(page.locator('#model-manager-modal.visible')).toHaveCount(0)

    await page.locator('#btn-toggle-select').click()
    await page.locator('#gallery-grid .gallery-item[data-id="712"]').click()
    await page.locator('#btn-tag').click()
    await expect(page.locator('#tag-modal.visible')).toBeVisible()
    await page.locator('#btn-tag-modal-smart-tag-go').click()
    await expect(page.locator('#smart-tag-modal.visible')).toBeVisible()
    await page.locator('#smart-tag-modal .modal-content').hover()
    await page.mouse.wheel(0, 900)
    await expect(page.locator('#btn-smart-tag-close')).toBeInViewport()
    await expect.poll(async () =>
      page.locator('#btn-smart-tag-close').evaluate((element) => getComputedStyle(element).position)
    ).toBe('sticky')
    await page.locator('#btn-smart-tag-close').click()
    await expect(page.locator('#smart-tag-modal.visible')).toHaveCount(0)
  })

  test('selection scope summary should distinguish manual selection from loaded range selection', async ({ page }) => {
    await Promise.all([101, 102, 103].map((id) => mockImageAsset(page, id)))
    await page.route('**/api/images**', async (route) => {
      const pathname = new URL(route.request().url()).pathname
      if (pathname !== '/api/images') {
        await route.continue()
        return
      }

      await route.fulfill({
        json: {
          images: [
            { id: 101, filename: 'scope-1.png', path: 'L:/scope-1.png', prompt: 'scope one' },
            { id: 102, filename: 'scope-2.png', path: 'L:/scope-2.png', prompt: 'scope two' },
            { id: 103, filename: 'scope-3.png', path: 'L:/scope-3.png', prompt: 'scope three' },
          ],
          total: 3,
          has_more: false,
          next_cursor: null,
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const galleryItems = page.locator('#gallery-grid .gallery-item')
    const scopeSummary = page.locator('#selection-scope-summary')

    await page.locator('#btn-toggle-select').click()
    await expect(galleryItems).toHaveCount(3)
    await expect(scopeSummary).toContainText('Selected manually from Gallery')

    await galleryItems.nth(0).click()
    await page.keyboard.down('Shift')
    await galleryItems.nth(2).click()
    await page.keyboard.up('Shift')
    await expect(scopeSummary).toContainText('loaded gallery items')

    await expect(page.locator('#btn-select-visible')).toHaveCount(0)
  })

  test('filtered selection should keep token scope and survive same-filter reloads', async ({ page }) => {
    const loadedImages = [
      buildMockGalleryImage(11, { filename: 'filtered-1.png', prompt: 'filtered one' }),
      buildMockGalleryImage(22, { filename: 'filtered-2.png', prompt: 'filtered two' }),
    ]
    let selectionTokenRequests = 0
    const selectionTokenPayloads: SelectionTokenRequestPayload[] = []
    const selectionChunkOffsets: number[] = []
    const exportDataPayloads: any[] = []
    let legacySelectionIdsRequests = 0

    await Promise.all(loadedImages.map((image) => mockImageAsset(page, image.id)))

    await page.route('**/api/images**', async (route) => {
      const pathname = new URL(route.request().url()).pathname
      if (pathname !== '/api/images') {
        await route.continue()
        return
      }

      await route.fulfill({
        json: {
          images: loadedImages,
          total: 4,
          has_more: false,
          next_cursor: null,
        },
      })
    })

    await page.route('**/api/images/selection-token', async (route) => {
      selectionTokenRequests += 1
      expect(route.request().method()).toBe('POST')
      const payload = route.request().postDataJSON() as SelectionTokenRequestPayload
      selectionTokenPayloads.push(payload)
      expect(payload).toMatchObject({
        sortBy: 'newest',
        chunkSize: 2000,
      })
      const excludedIds = Array.isArray(payload.excludedImageIds) ? payload.excludedImageIds : []
      await route.fulfill({
        json: {
          selection_token: excludedIds.length > 0
            ? 'filtered-selection-token-minus-22'
            : 'filtered-selection-token',
          total_estimate: excludedIds.length > 0 ? 3 : 4,
          exact_total: true,
          chunk_size: 2,
        },
      })
    })

    await page.route('**/api/images/selection-chunk**', async (route) => {
      expect(route.request().method()).toBe('GET')
      const url = new URL(route.request().url())
      expect(url.searchParams.get('selection_token')).toBe('filtered-selection-token')
      const offset = Number(url.searchParams.get('offset') || '0')
      const limit = Number(url.searchParams.get('limit') || '0')
      selectionChunkOffsets.push(offset)
      const allIds = [11, 22, 33, 44]
      const imageIds = allIds.slice(offset, offset + limit)
      await route.fulfill({
        json: {
          image_ids: imageIds,
          offset,
          limit,
          next_offset: offset + imageIds.length < allIds.length ? offset + imageIds.length : null,
          has_more: offset + imageIds.length < allIds.length,
        },
      })
    })

    await page.route('**/api/images/selection-ids', async (route) => {
      legacySelectionIdsRequests += 1
      await route.fulfill({
        json: {
          image_ids: [11, 22, 33, 44],
          total: 4,
        },
      })
    })

    await page.route('**/api/images/export-data', async (route) => {
      const payload = route.request().postDataJSON()
      exportDataPayloads.push(payload)
      await route.fulfill({
        json: {
          images: [
            { id: 11, prompt: 'filtered one', tags: ['filtered'] },
            { id: 33, prompt: 'filtered three', tags: ['filtered'] },
          ],
          missing_ids: [],
          count: 2,
          total: 3,
          offset: 0,
          limit: 2000,
          next_offset: 2,
          has_more: true,
          source: 'selection_token',
          exact_total: true,
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-toggle-select').click()
    await page.locator('#btn-select-all').click()

    await expect.poll(() => selectionTokenRequests).toBe(1)
    await expect.poll(() => selectionChunkOffsets).toEqual([])
    expect(legacySelectionIdsRequests).toBe(0)
    await expect.poll(() => page.evaluate(() => window.App.AppState.selectionToken)).toBe('filtered-selection-token')
    await expect.poll(() => page.evaluate(() => window.App.AppState.selectionTotal)).toBe(4)
    await expect.poll(() => page.evaluate(() => window.App.AppState.selectedIds.size)).toBe(0)
    await expect(page.locator('#selection-count')).toContainText('4 items selected')
    await expect(page.locator('#selection-scope-summary')).toContainText('all current filter matches')

    await page.evaluate(() => window.App.loadImages())
    await expect.poll(() => page.evaluate(() => window.App.AppState.selectionToken)).toBe('filtered-selection-token')
    await expect.poll(() => page.evaluate(() => window.App.AppState.selectionTotal)).toBe(4)
    await expect.poll(() => page.evaluate(() => window.App.AppState.selectedIds.size)).toBe(0)
    await expect(page.locator('#selection-scope-summary')).toContainText('all current filter matches')

    const excludedCard = page.locator('#gallery-grid .gallery-item[data-id="22"]')
    await excludedCard.click()
    await expect.poll(() => selectionTokenRequests).toBe(2)
    expect(selectionTokenPayloads[1].excludedImageIds).toEqual([22])
    await expect(excludedCard).toHaveAttribute('aria-selected', 'false')
    await expect(page.locator('#selection-count')).toContainText('3 items selected')
    await expect.poll(() => page.evaluate(() => window.App.AppState.selectionToken))
      .toBe('filtered-selection-token-minus-22')

    await openSelectionPanelSection(page, 'Export')
    await page.locator('#btn-export-selected').click()
    await expect(page.locator('#export-modal.visible')).toBeVisible()
    await expect(page.locator('#export-text')).toHaveValue(/filtered one/)
    await expect(page.locator('#export-text')).toHaveValue(/filtered three/)
    await expect(page.locator('#export-text')).not.toHaveValue(/filtered two/)
    await expect(page.locator('#export-text')).toHaveValue(/Preview only shows the first 2 of 3 selected images/)
    await expect.poll(() => exportDataPayloads.length).toBe(1)
    expect(exportDataPayloads[0]).toMatchObject({
      selection_token: 'filtered-selection-token-minus-22',
      offset: 0,
      limit: 2000,
    })
    expect(exportDataPayloads[0].image_ids).toBeUndefined()
    await page.locator('#btn-close-export').click()

    await page.evaluate(() => {
      window.App.updateFilters((filters: any) => {
        filters.search = 'changed-filter'
      })
      return window.App.loadImages()
    })
    await expect.poll(() => page.evaluate(() => window.App.AppState.selectedIds.size)).toBe(0)
    await expect(page.locator('#selection-scope-summary')).toContainText('Selected manually from Gallery')
  })

  test('filtered selection token refresh should be fail-closed and last-write-wins', async ({ page }) => {
    const loadedImages = [
      buildMockGalleryImage(11, { filename: 'rapid-filtered-1.png' }),
      buildMockGalleryImage(22, { filename: 'rapid-filtered-2.png' }),
    ]
    await mockGalleryImages(page, loadedImages)

    let tokenRequestCount = 0
    let massTagFallbackTokenRequests = 0
    let vlmBatchRequests = 0
    let staleResponseFulfilled = false
    let releaseStaleResponse!: () => void
    const staleResponseGate = new Promise<void>((resolve) => {
      releaseStaleResponse = resolve
    })

    await page.route('**/api/images/selection-token', async (route) => {
      const payload = route.request().postDataJSON()
      if (payload.chunkSize === 5000) {
        massTagFallbackTokenRequests += 1
        await route.fulfill({
          json: {
            selection_token: 'unexpected-mass-tag-filter-token',
            total_estimate: 2,
            exact_total: true,
            chunk_size: 5000,
          },
        })
        return
      }
      tokenRequestCount += 1
      if (tokenRequestCount === 1) {
        await route.fulfill({
          json: {
            selection_token: 'rapid-original-token',
            total_estimate: 2,
            exact_total: true,
            chunk_size: 2000,
          },
        })
        return
      }
      if (tokenRequestCount === 2) {
        expect(payload.excludedImageIds).toEqual([11])
        await staleResponseGate
        await route.fulfill({
          json: {
            selection_token: 'rapid-stale-minus-11-token',
            total_estimate: 1,
            exact_total: true,
            chunk_size: 2000,
          },
        })
        staleResponseFulfilled = true
        return
      }

      expect(payload.excludedImageIds).toBeUndefined()
      await route.fulfill({
        json: {
          selection_token: 'rapid-latest-all-token',
          total_estimate: 2,
          exact_total: true,
          chunk_size: 2000,
        },
      })
    })

    await page.route('**/api/vlm/caption-batch', async (route) => {
      vlmBatchRequests += 1
      await route.fulfill({ json: { status: 'started' } })
    })
    await page.route('**/api/vlm/caption-batch/progress', async (route) => {
      await route.fulfill({ json: { running: false, total: 0, completed: 0, failed: 0 } })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await page.locator('#btn-toggle-select').click()
    await page.locator('#btn-select-all').click()

    const firstCard = page.locator('#gallery-grid .gallery-item[data-id="11"]')
    await firstCard.click()
    await expect.poll(() => tokenRequestCount).toBe(2)
    await expect(firstCard).toHaveAttribute('aria-selected', 'false')
    await expect(page.locator('#selection-scope-summary')).toContainText('Updating filtered selection')

    const pendingActionIds = [
      'btn-select-all',
      'btn-invert-selection-filtered',
      'btn-move-selected',
      'btn-copy-selected',
      'btn-export-selected',
      'btn-batch-export-tags',
      'btn-send-to-censor',
      'btn-send-selection-to-dataset-maker',
      'btn-add-selected-to-collection',
      'btn-publish-selected',
      'btn-remove-selected-gallery',
      'btn-delete-selected-files',
    ]
    const enabledPendingActionIds = await page.evaluate((actionIds) => actionIds.filter((actionId) => {
      const button = document.getElementById(actionId) as HTMLButtonElement | null
      return button && !button.disabled
    }), pendingActionIds)
    expect(enabledPendingActionIds).toEqual([])

    const pendingConsumerState = await page.evaluate(async () => {
      const w = window as any
      return {
        actionToken: w.AppFilterAccess?.getActiveSelectionToken?.() ?? null,
        actionIds: w.AppFilterAccess?.getSelectedImageIds?.() ?? [],
        datasetIds: await w.DatasetMaker?._resolveGallerySelectionIds?.(),
        artistIds: w.ArtistIdent?._getExplicitGallerySelectionIds?.(),
      }
    })
    expect(pendingConsumerState).toEqual({
      actionToken: null,
      actionIds: [],
      datasetIds: [],
      artistIds: [],
    })

    const pendingToolState = await page.evaluate(async () => {
      const w = window as any
      const vlm = w.VLMCaption
      const massTag = w.MassTagEditor
      await vlm.startBatchCaption()
      vlm.stopPolling()
      vlm.isRunning = false
      vlm._startInFlight = false
      await massTag.openModal()
      const massTagScope = massTag.getScopeValue()
      const massTagPayload = await massTag.resolveScopePayload()
      const state = {
        vlmStatus: document.getElementById('vlm-batch-status')?.textContent || '',
        massTagStatus: document.getElementById('mass-tag-status')?.textContent || '',
        massTagScope,
        massTagPayload,
      }
      massTag.closeModal()
      return state
    })
    expect(vlmBatchRequests).toBe(0)
    expect(massTagFallbackTokenRequests).toBe(0)
    expect(pendingToolState.vlmStatus).toContain('Updating filtered selection')
    expect(pendingToolState.massTagStatus).toContain('Updating filtered selection')
    expect(pendingToolState.massTagScope).toBe('selection')
    expect(pendingToolState.massTagPayload).toBeNull()

    await firstCard.click({ button: 'right' })
    const reincludeMenuItem = page.getByRole('menuitem', { name: 'Select Image', exact: true })
    await expect(reincludeMenuItem).toBeVisible()
    await reincludeMenuItem.click()
    await expect.poll(() => tokenRequestCount).toBe(3)
    await expect.poll(() => page.evaluate(() => window.App.AppState.selectionToken))
      .toBe('rapid-latest-all-token')
    await expect(firstCard).toHaveAttribute('aria-selected', 'true')
    await expect(page.locator('#selection-count')).toContainText('2 items selected')

    releaseStaleResponse()
    await expect.poll(() => staleResponseFulfilled).toBe(true)
    await page.waitForLoadState('networkidle')
    await expect.poll(() => page.evaluate(() => window.App.AppState.selectionToken))
      .toBe('rapid-latest-all-token')
    await expect(firstCard).toHaveAttribute('aria-selected', 'true')
    await expect(page.locator('#selection-count')).toContainText('2 items selected')
    await expect(page.locator('#btn-delete-selected-files')).toBeEnabled()
  })

  test('filtered selection token refresh failure restores confirmed scope and explicit selection still works', async ({ page }) => {
    const loadedImages = [
      buildMockGalleryImage(11, { filename: 'failed-filtered-1.png' }),
      buildMockGalleryImage(22, { filename: 'failed-filtered-2.png' }),
    ]
    await mockGalleryImages(page, loadedImages)

    let tokenRequestCount = 0
    await page.route('**/api/images/selection-token', async (route) => {
      tokenRequestCount += 1
      if (tokenRequestCount === 1) {
        await route.fulfill({
          json: {
            selection_token: 'failure-confirmed-token',
            total_estimate: 2,
            exact_total: true,
            chunk_size: 2000,
          },
        })
        return
      }
      expect(route.request().postDataJSON().excludedImageIds).toEqual([11])
      await route.fulfill({
        status: 503,
        json: { detail: 'selection token service unavailable for exclusion refresh' },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await page.locator('#btn-toggle-select').click()
    await page.locator('#btn-select-all').click()

    const firstCard = page.locator('#gallery-grid .gallery-item[data-id="11"]')
    await firstCard.click()
    await expect.poll(() => tokenRequestCount).toBe(2)
    await expect(page.locator('#toast-container')).toContainText('previous selection was restored')
    await expect(page.locator('#toast-container')).toContainText('selection token service unavailable for exclusion refresh')
    await expect.poll(() => page.evaluate(() => window.App.AppState.selectionToken))
      .toBe('failure-confirmed-token')
    await expect.poll(() => page.evaluate(() => window.App.AppState.selectedIds.size)).toBe(0)
    await expect(firstCard).toHaveAttribute('aria-selected', 'true')
    await expect(page.locator('#selection-count')).toContainText('2 items selected')

    await page.locator('#btn-clear-selection').click()
    await firstCard.click()
    await expect(firstCard).toHaveAttribute('aria-selected', 'true')
    await expect(page.locator('#selection-count')).toContainText('1 item')
    await expect.poll(() => page.evaluate(() => Array.from(window.App.AppState.selectedIds)))
      .toEqual([11])
    expect(tokenRequestCount).toBe(2)
  })

  test('filtered selection should keep an all-excluded token action-safe and allow re-including a card', async ({ page }) => {
    const loadedImages = [
      buildMockGalleryImage(11, { filename: 'zero-filtered-1.png' }),
      buildMockGalleryImage(22, { filename: 'zero-filtered-2.png' }),
    ]
    await mockGalleryImages(page, loadedImages)

    const tokenPayloads: SelectionTokenRequestPayload[] = []
    let vlmBatchRequests = 0
    await page.route('**/api/images/selection-token', async (route) => {
      const payload = route.request().postDataJSON() as SelectionTokenRequestPayload
      tokenPayloads.push(payload)
      const excludedIds = Array.isArray(payload.excludedImageIds)
        ? Array.from(new Set(payload.excludedImageIds.map(Number))).filter((id) => id === 11 || id === 22)
        : []
      await route.fulfill({
        json: {
          selection_token: `zero-selection-token-${excludedIds.join('-') || 'all'}`,
          total_estimate: 2 - excludedIds.length,
          exact_total: true,
          chunk_size: 2000,
        },
      })
    })
    await page.route('**/api/vlm/caption-batch', async (route) => {
      vlmBatchRequests += 1
      await route.fulfill({ json: { status: 'started' } })
    })
    await page.route('**/api/vlm/caption-batch/progress', async (route) => {
      await route.fulfill({ json: { running: false, total: 0, completed: 0, failed: 0 } })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await page.locator('#btn-toggle-select').click()
    await page.locator('#btn-select-all').click()

    const firstCard = page.locator('#gallery-grid .gallery-item[data-id="11"]')
    const secondCard = page.locator('#gallery-grid .gallery-item[data-id="22"]')
    await firstCard.click()
    await expect.poll(() => tokenPayloads.length).toBe(2)
    await secondCard.click()
    await expect.poll(() => tokenPayloads.length).toBe(3)
    await expect.poll(() => page.evaluate(() => ({
      total: window.App.AppState.selectionTotal,
      pending: window.App.isFilteredSelectionTokenRefreshPending(),
    }))).toEqual({ total: 0, pending: false })

    const zeroSelectionState = await page.evaluate(async () => {
      const w = window as any
      const vlm = w.VLMCaption
      const massTag = w.MassTagEditor
      await vlm.startBatchCaption()
      vlm.stopPolling()
      vlm.isRunning = false
      vlm._startInFlight = false
      await massTag.openModal()
      const massTagScope = massTag.getScopeValue()
      massTag.closeModal()
      return {
        count: w.App.getSelectedGalleryCount(),
        total: w.App.AppState.selectionTotal,
        exclusions: Array.from(w.App.AppState.selectedIds),
        actionToken: w.AppFilterAccess.getActiveSelectionToken(),
        actionIds: w.AppFilterAccess.getSelectedImageIds(),
        massTagScope,
        vlmStatus: document.getElementById('vlm-batch-status')?.textContent || '',
      }
    })
    expect(zeroSelectionState).toEqual({
      count: 0,
      total: 0,
      exclusions: [11, 22],
      actionToken: null,
      actionIds: [],
      massTagScope: 'selection',
      vlmStatus: 'No images to caption. Select images or use current view.',
    })
    expect(vlmBatchRequests).toBe(0)
    await expect(firstCard).toHaveAttribute('aria-selected', 'false')
    await expect(secondCard).toHaveAttribute('aria-selected', 'false')
    await expect(page.locator('#btn-delete-selected-files')).toBeDisabled()
    await expect(page.locator('#btn-export-selected')).toBeDisabled()

    await firstCard.click()
    await expect.poll(() => tokenPayloads.length).toBe(4)
    await expect.poll(() => page.evaluate(() => window.App.AppState.selectionTotal)).toBe(1)
    await expect(firstCard).toHaveAttribute('aria-selected', 'true')
    await expect(secondCard).toHaveAttribute('aria-selected', 'false')
    await expect(page.locator('#selection-count')).toContainText('1 item')
    await expect.poll(() => page.evaluate(() => (window as any).AppFilterAccess.getActiveSelectionToken()))
      .toBe('zero-selection-token-22')
  })

  test('all-excluded filtered selection should invert back to the full explicit selection', async ({ page }) => {
    const loadedImages = [
      buildMockGalleryImage(11, { filename: 'invert-zero-filtered-1.png' }),
      buildMockGalleryImage(22, { filename: 'invert-zero-filtered-2.png' }),
    ]
    await mockGalleryImages(page, loadedImages)

    const exclusionPayloads: number[][] = []
    await page.route('**/api/images/selection-token', async (route) => {
      const payload = route.request().postDataJSON() as SelectionTokenRequestPayload
      const excludedIds = Array.isArray(payload.excludedImageIds)
        ? Array.from(new Set(payload.excludedImageIds.map(Number))).filter((id) => id === 11 || id === 22)
        : []
      exclusionPayloads.push(excludedIds)
      await route.fulfill({
        json: {
          selection_token: `invert-zero-token-${excludedIds.join('-') || 'all'}`,
          total_estimate: 2 - excludedIds.length,
          exact_total: true,
          chunk_size: 2000,
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await page.locator('#btn-toggle-select').click()
    await page.locator('#btn-select-all').click()

    const firstCard = page.locator('#gallery-grid .gallery-item[data-id="11"]')
    const secondCard = page.locator('#gallery-grid .gallery-item[data-id="22"]')
    await firstCard.click()
    await expect.poll(() => exclusionPayloads.length).toBe(2)
    await secondCard.click()
    await expect.poll(() => page.evaluate(() => ({
      total: window.App.AppState.selectionTotal,
      pending: window.App.isFilteredSelectionTokenRefreshPending(),
    }))).toEqual({ total: 0, pending: false })

    await page.locator('#btn-invert-selection-filtered').click()
    await expect.poll(() => exclusionPayloads.length).toBe(4)
    expect(exclusionPayloads[3]).toEqual([11, 22])
    await expect.poll(() => page.evaluate(() => ({
      scope: window.App.AppState.selectionScope,
      token: window.App.AppState.selectionToken,
      ids: Array.from(window.App.AppState.selectedIds),
      count: window.App.getSelectedGalleryCount(),
    }))).toEqual({ scope: 'filtered', token: null, ids: [11, 22], count: 2 })
    await expect(firstCard).toHaveAttribute('aria-selected', 'true')
    await expect(secondCard).toHaveAttribute('aria-selected', 'true')
  })

  test('filtered selection should require confirmation for very large result sets', async ({ page }) => {
    const loadedImages = [
      buildMockGalleryImage(11, { filename: 'large-filtered-1.png' }),
      buildMockGalleryImage(22, { filename: 'large-filtered-2.png' }),
    ]
    await Promise.all(loadedImages.map((image) => mockImageAsset(page, image.id)))

    await page.route('**/api/images**', async (route) => {
      const pathname = new URL(route.request().url()).pathname
      if (pathname !== '/api/images') {
        await route.continue()
        return
      }
      await route.fulfill({
        json: {
          images: loadedImages,
          total: 12000,
          has_more: false,
          next_cursor: null,
        },
      })
    })

    let selectionChunkRequests = 0

    await page.route('**/api/images/selection-token', async (route) => {
      await route.fulfill({
        json: {
          selection_token: 'large-filtered-selection-token',
          total_estimate: 12000,
          exact_total: true,
          chunk_size: 2000,
        },
      })
    })

    await page.route('**/api/images/selection-chunk**', async (route) => {
      selectionChunkRequests += 1
      await route.fulfill({
        json: {
          image_ids: [11, 22, 33, 44],
          offset: 0,
          limit: 2000,
          next_offset: null,
          has_more: false,
        },
      })
    })

    await page.route('**/api/images/selection-ids', async (route) => {
      await route.fulfill({
        json: {
          image_ids: [11, 22, 33, 44],
          total: 12000,
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.evaluate(() => {
      ;(window as Window & { __confirmCalls?: string[] }).__confirmCalls = []
      const originalConfirm = window.confirm
      window.confirm = (message?: string) => {
        ;(window as Window & { __confirmCalls?: string[] }).__confirmCalls?.push(String(message || ''))
        return false
      }
      ;(window as Window & { __restoreConfirm?: () => void }).__restoreConfirm = () => {
        window.confirm = originalConfirm
      }
    })

    await page.locator('#btn-toggle-select').click()
    await page.locator('#btn-select-all').click()

    await expect.poll(() => page.evaluate(() => window.App.AppState.selectedIds.size)).toBe(0)
    expect(selectionChunkRequests).toBe(0)
    await expect.poll(async () => {
      return await page.evaluate(() => (window as Window & { __confirmCalls?: string[] }).__confirmCalls || [])
    }).toContainEqual(expect.stringContaining('12000'))

    await page.evaluate(() => {
      ;(window as Window & { __restoreConfirm?: () => void }).__restoreConfirm?.()
    })
  })

  test('gallery batch actions should separate remove-from-gallery from move-to-trash', async ({ page }) => {
    await mockGalleryImages(page, [
      { id: 301, filename: 'remove-vs-delete.png' },
    ])

    const removePayloads: any[] = []
    const deletePayloads: any[] = []

    // v3.3.2 Phase-1 (commit 8360d17): both bulk actions run as background jobs
    // now — the UI POSTs to `/start` then polls `/progress` until a terminal
    // status. Mock both halves; capture the start payloads for the assertions.
    await page.route('**/api/images/remove-selected/start', async (route) => {
      removePayloads.push(route.request().postDataJSON())
      await route.fulfill({ json: { status: 'starting' } })
    })

    await page.route('**/api/images/remove-selected/progress', async (route) => {
      await route.fulfill({
        json: {
          status: 'done',
          removed: 1,
          missing_ids: [],
          permanent_delete: false,
        },
      })
    })

    await page.route('**/api/images/delete-selected/start', async (route) => {
      deletePayloads.push(route.request().postDataJSON())
      await route.fulfill({ json: { status: 'starting' } })
    })

    await page.route('**/api/images/delete-selected/progress', async (route) => {
      await route.fulfill({
        json: {
          status: 'done',
          deleted: 1,
          failed: [],
          permanent_delete: true,
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-toggle-select').click()
    // Aurora Phase 3: with nothing selected the action bar (and its More menu
    // housing the danger actions) does not exist at all.
    await expect(page.locator('#gallery-action-bar')).toBeHidden()

    await page.locator('#gallery-grid .gallery-item[data-id="301"]').click()
    await openSelectionPanelSection(page, 'Remove')
    await expect(page.locator('#btn-remove-selected-gallery')).toBeVisible()
    await expect(page.locator('#btn-delete-selected-files')).toBeVisible()
    await expect(page.locator('#btn-remove-selected-gallery')).toBeEnabled()
    await expect(page.locator('#btn-delete-selected-files')).toBeEnabled()
    // The danger pair sits behind the menu's divider, separated from the
    // everyday actions (same separation intent as the old Remove disclosure).
    await expect(page.locator('#gallery-action-more-menu .gallery-action-more-divider')).toHaveCount(1)
    expect(await page.evaluate(() => {
      const menu = document.getElementById('gallery-action-more-menu')
      if (!menu) return null
      const children = Array.from(menu.children)
      const dividerIndex = children.findIndex((el) => el.classList.contains('gallery-action-more-divider'))
      const removeIndex = children.findIndex((el) => el.id === 'btn-remove-selected-gallery')
      const deleteIndex = children.findIndex((el) => el.id === 'btn-delete-selected-files')
      return dividerIndex >= 0 && removeIndex > dividerIndex && deleteIndex > dividerIndex
    })).toBe(true)
    await page.locator('#btn-remove-selected-gallery').click()
    await expect(page.locator('#confirm-modal.visible')).toBeVisible()
    await expect(page.locator('#confirm-message')).toContainText('Files stay on disk')
    await page.locator('#btn-confirm-ok').click()

    await expect.poll(() => removePayloads.length).toBe(1)
    expect(removePayloads[0]).toMatchObject({ image_ids: [301] })
    expect(deletePayloads).toHaveLength(0)

    await page.locator('#gallery-grid .gallery-item[data-id="301"]').click()
    await page.keyboard.press('Delete')
    await expect(page.locator('#confirm-modal.visible')).toBeVisible()
    await expect(page.locator('#confirm-message')).toContainText('Files stay on disk')
    await page.locator('#btn-confirm-ok').click()

    await expect.poll(() => removePayloads.length).toBe(2)
    expect(removePayloads[1]).toMatchObject({ image_ids: [301] })
    expect(deletePayloads).toHaveLength(0)

    await page.locator('#gallery-grid .gallery-item[data-id="301"]').click()
    await openSelectionPanelSection(page, 'Remove')
    await expect(page.locator('#btn-delete-selected-files')).toBeEnabled()
    await page.locator('#btn-delete-selected-files').click()
    await expect(page.locator('#confirm-modal.visible')).toBeVisible()
    await expect(page.locator('#confirm-message')).toContainText('Recycle Bin')
    await page.locator('#btn-confirm-ok').click()

    await expect.poll(() => deletePayloads.length).toBe(1)
    expect(deletePayloads[0]).toMatchObject({
      image_ids: [301],
      confirm_delete_files: true,
    })
  })

  test('gallery should recover from stale large-library loads when switching tabs', async ({ page }) => {
    const largeImages = Array.from({ length: 5000 }, (_, index) =>
      buildMockGalleryImage(index + 1, {
        filename: `large-${index + 1}.png`,
        generator: index % 2 === 0 ? 'webui' : 'forge',
      })
    )

    await Promise.all(largeImages.slice(0, 24).map((image) => mockImageAsset(page, image.id)))

    let imageRequests = 0
    // No-op init: the real resolver is assigned inside the route handler, and
    // TS control flow cannot see closure assignments (a null init narrows the
    // later call site to `never`).
    let releaseFirstRequest: () => void = () => {}
    let firstRequestStarted: (() => void) | null = null
    const firstRequestSeen = new Promise<void>((resolve) => {
      firstRequestStarted = resolve
    })

    await page.route('**/api/images**', async (route) => {
      const pathname = new URL(route.request().url()).pathname
      if (pathname !== '/api/images') {
        await route.continue()
        return
      }

      imageRequests += 1
      const payload = {
        images: largeImages.slice(0, 24),
        total: largeImages.length,
        has_more: true,
        next_cursor: 'cursor-24',
      }

      if (imageRequests === 1) {
        firstRequestStarted?.()
        await new Promise<void>((resolve) => {
          releaseFirstRequest = resolve
        })
        await route.fulfill({ json: payload }).catch(() => undefined)
        return
      }

      await route.fulfill({ json: payload })
    })

    await page.route('**/api/stats', async (route) => {
      await route.fulfill({
        json: {
          total_images: largeImages.length,
          generators: [
            { generator: 'webui', count: 0 },
            { generator: 'forge', count: 0 },
          ],
          top_tags: [],
          checkpoints: [],
          loras: [],
          metadata_pending: 0,
          metadata_status: { complete: 0, pending: 0 },
          scan_status: 'running',
          scan_step: 'indexing',
          scan_library_ready: false,
        },
      })
    })

    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await waitForNavigationChrome(page)
    await firstRequestSeen

    await openView(page, 'reader')
    await openView(page, 'gallery')

    await expect.poll(() => imageRequests, { timeout: 5000 }).toBeGreaterThanOrEqual(2)
    releaseFirstRequest?.()

    await expect.poll(() => page.evaluate(() => window.App.AppState.isLoading), { timeout: 5000 }).toBe(false)
    await expect(page.locator('#gallery-grid .gallery-item[data-id="1"]')).toBeVisible()
    await expect(page.locator('#metadata-status-chip')).toContainText('Scanning library')
    await expect(page.locator('#count-webui')).toHaveText('…')
  })

  test('gallery selected move and copy actions should call move API with explicit operation', async ({ page }) => {
    await mockGalleryImages(page, [
      { id: 401, filename: 'move-selected-1.png' },
      { id: 402, filename: 'move-selected-2.png' },
    ])

    const movePayloads: any[] = []
    // v3.3.0 USR-1: moves now run through the background job endpoint
    // (/api/move/start) with progress polling instead of the synchronous
    // /api/move. Returning status:'done' inline lets the frontend skip the
    // poll loop, so the test stays a single round-trip.
    await page.route('**/api/move/start', async (route) => {
      const payload = route.request().postDataJSON()
      movePayloads.push(payload)
      await route.fulfill({
        json: {
          status: 'done',
          results: payload.image_ids.map((id: number) => ({
            id,
            new_path: `${payload.destination_folder}/${id}.png`,
            operation: payload.operation || 'move',
            success: true,
          })),
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-toggle-select').click()
    await page.locator('#gallery-grid .gallery-item[data-id="401"]').click()
    await page.locator('#gallery-grid .gallery-item[data-id="402"]').click()
    await expect(page.locator('#btn-move-selected')).toBeEnabled()
    await page.locator('#btn-move-selected').click()
    await expect(page.locator('#input-modal.visible')).toBeVisible()
    await page.locator('#input-modal-field').fill('C:/sorted/move')
    await page.locator('#btn-input-ok').click()
    await expect(page.locator('#confirm-modal.visible')).toBeVisible()
    await page.locator('#btn-confirm-ok').click()

    await expect.poll(() => movePayloads.length).toBe(1)
    expect(movePayloads[0]).toMatchObject({
      image_ids: [401, 402],
      destination_folder: 'C:/sorted/move',
      operation: 'move',
    })

    await expect(page.locator('#gallery-grid .gallery-item[data-id="401"]')).toBeVisible()
    await page.locator('#gallery-grid .gallery-item[data-id="401"]').click()
    await page.locator('#gallery-grid .gallery-item[data-id="402"]').click()
    // Copy moved into the action bar's More menu (Aurora Phase 3).
    await openSelectionPanelSection(page, 'Export')
    await expect(page.locator('#btn-copy-selected')).toBeEnabled()
    await page.locator('#btn-copy-selected').click()
    await expect(page.locator('#input-modal.visible')).toBeVisible()
    await page.locator('#input-modal-field').fill('C:/sorted/copy')
    await page.locator('#btn-input-ok').click()
    await expect(page.locator('#confirm-modal.visible')).toBeVisible()
    await page.locator('#btn-confirm-ok').click()

    await expect.poll(() => movePayloads.length).toBe(2)
    expect(movePayloads[1]).toMatchObject({
      image_ids: [401, 402],
      destination_folder: 'C:/sorted/copy',
      operation: 'copy',
    })
  })


  test('export modal should fetch selected prompt/tag data once and reuse it when toggling views', async ({ page }) => {
    const selectedImages = [
      { id: 11, filename: 'export-1.png', path: 'L:/export-1.png', prompt: 'prompt one' },
      { id: 22, filename: 'export-2.png', path: 'L:/export-2.png', prompt: 'prompt two' },
      { id: 33, filename: 'export-3.png', path: 'L:/export-3.png', prompt: 'prompt three' },
    ]
    const detailRequests: string[] = []
    let selectionDataRequests = 0

    await Promise.all(selectedImages.map((image) => mockImageAsset(page, image.id)))

    await page.route('**/api/images**', async (route) => {
      await route.fulfill({
        json: {
          images: selectedImages,
          total: selectedImages.length,
          has_more: false,
          next_cursor: null,
        },
      })
    })

    await page.route('**/api/images/export-data', async (route) => {
      selectionDataRequests += 1
      expect(route.request().method()).toBe('POST')
      expect(route.request().postDataJSON()).toMatchObject({
        image_ids: [11, 22, 33],
      })
      await route.fulfill({
        json: {
          images: [
            { id: 11, prompt: 'prompt one', tags: ['beta', 'alpha'] },
            { id: 22, prompt: 'prompt two', tags: ['gamma'] },
            { id: 33, prompt: 'prompt three', tags: ['alpha'] },
          ],
          missing_ids: [],
        },
      })
    })

    page.on('request', (request) => {
      const pathname = new URL(request.url()).pathname
      if (/^\/api\/images\/\d+$/.test(pathname)) {
        detailRequests.push(pathname)
      }
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-toggle-select').click()
    await page.locator('#gallery-grid .gallery-item[data-id="11"]').click()
    await page.locator('#gallery-grid .gallery-item[data-id="22"]').click()
    await page.locator('#gallery-grid .gallery-item[data-id="33"]').click()
    await openSelectionPanelSection(page, 'Export')
    await expect(page.locator('#btn-export-selected')).toContainText('Combined Export')
    await page.locator('#btn-export-selected').click()

    await expect(page.locator('#export-modal.visible')).toBeVisible()
    await expect(page.locator('#export-title')).toContainText('Prompt text')
    await expect(page.locator('#export-format')).toHaveValue('prompt')
    await expect(page.locator('#export-format option[data-i18n="export.groupAdvanced"]')).toHaveText('Advanced formats')
    await expect(page.locator('#export-format option[value="prompt"]')).toHaveText('Prompt text')
    await expect(page.locator('#export-format-description')).toContainText('One .txt')
    await expect(page.locator('#btn-download-export')).toBeVisible()
    await expect(page.locator('#export-text')).toHaveValue('prompt one\n\nprompt two\n\nprompt three')
    await expect.poll(() => selectionDataRequests).toBe(1)
    expect(detailRequests).toHaveLength(0)

    await page.locator('#export-format').selectOption('tags')
    await expect(page.locator('#export-format-description')).toContainText('merged unique Tags')
    await expect(page.locator('#export-text')).toHaveValue('alpha, beta, gamma')
    await expect.poll(() => selectionDataRequests).toBe(1)
    expect(detailRequests).toHaveLength(0)

    await page.locator('#export-format').selectOption('jsonl')
    await expect(page.locator('#export-text')).toHaveValue(/"prompt":"prompt one"/)
    await expect.poll(() => selectionDataRequests).toBe(1)
    expect(detailRequests).toHaveLength(0)
  })

  test('export modal should cap large previews and request preview subset only', async ({ page }) => {
    const requestedPayloadSizes: number[] = []

    await page.route('**/api/images/export-data', async (route) => {
      const payload = route.request().postDataJSON() as { image_ids?: number[] }
      requestedPayloadSizes.push(Array.isArray(payload?.image_ids) ? payload.image_ids.length : 0)
      await route.fulfill({
        json: {
          images: [
            { id: 1, prompt: 'prompt one', tags: ['alpha'] },
            { id: 2, prompt: 'prompt two', tags: ['beta'] },
            { id: 3, prompt: 'prompt three', tags: ['gamma'] },
          ],
          missing_ids: [],
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.evaluate(() => {
      const ids = Array.from({ length: 2501 }, (_, index) => index + 1)
      window.App.setSelectionState({
        selectionMode: true,
        selectedIds: new Set(ids),
        scope: 'filtered',
        filterKey: 'preview-cap-test',
      })
    })

    await page.evaluate(async () => {
      await window.App.showExportModal()
    })
    await expect(page.locator('#export-modal.visible')).toBeVisible()
    await expect(page.locator('#export-count')).toContainText('This export includes only 2501 selected images')
    await expect(page.locator('#export-modal')).toContainText('Only the images selected in Gallery are included here')
    await expect(page.locator('#btn-download-export')).toBeVisible()
    await expect(page.locator('#export-text')).toHaveValue(/Preview only shows the first 2000 of 2501 selected images/)
    await expect.poll(() => requestedPayloadSizes.length).toBe(1)
    expect(requestedPayloadSizes[0]).toBe(2000)

    await page.locator('#export-format').selectOption('csv')
    await expect(page.locator('#export-text')).toHaveValue(/id,filename,generator,prompt/)
    await expect.poll(() => requestedPayloadSizes.length).toBe(1)
  })

  test('batch sidecar export should send content mode and overwrite policy', async ({ page }) => {
    const selectedImages = [
      { id: 71, filename: 'sidecar-1.png', path: 'L:/sidecar-1.png', prompt: 'prompt one', tags: ['alpha'] },
      { id: 72, filename: 'sidecar-2.png', path: 'L:/sidecar-2.png', prompt: 'prompt two', tags: ['beta'] },
    ]
    let exportPayload: any = null

    await Promise.all(selectedImages.map((image) => mockImageAsset(page, image.id)))

    await page.route('**/api/images**', async (route) => {
      await route.fulfill({
        json: {
          images: selectedImages,
          total: selectedImages.length,
          has_more: false,
          next_cursor: null,
        },
      })
    })

    // v3.3.2 Phase-1 (commit 8360d17): batch sidecar export is a background job —
    // POST `/start` then poll `/progress`. The terminal payload embeds the export
    // result under `result`. Capture the start payload for the assertion.
    await page.route('**/api/tags/export-batch/start', async (route) => {
      exportPayload = route.request().postDataJSON()
      await route.fulfill({ json: { status: 'starting' } })
    })

    await page.route('**/api/tags/export-batch/progress', async (route) => {
      await route.fulfill({
        json: {
          status: 'done',
          result: {
            status: 'ok',
            exported: 2,
            skipped: 0,
            total: 2,
            error_count: 0,
          },
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-toggle-select').click()
    await page.locator('#gallery-grid .gallery-item[data-id="71"]').click()
    await page.locator('#gallery-grid .gallery-item[data-id="72"]').click()
    await openSelectionPanelSection(page, 'Export')
    await expect(page.locator('#btn-batch-export-tags')).toContainText('Training captions')
    await page.locator('#btn-batch-export-tags').click()

    await expect(page.locator('#batch-export-modal.visible')).toBeVisible()
    await expect(page.locator('#batch-export-count')).toContainText('This batch export includes only 2 selected images')
    await expect(page.locator('#batch-export-modal')).toContainText('This only writes files for the images selected in Gallery')
    await expect(page.locator('#batch-export-content-mode')).toBeVisible()
    await expect(page.locator('#batch-export-content-mode option[value="caption_merged"]')).toHaveText('LoRA caption file')
    await expect(page.locator('#batch-export-content-mode option[data-i18n="batchExport.groupAdvanced"]')).toHaveText('Advanced formats')
    await expect(page.locator('#batch-export-content-description')).toContainText('LoRA training')
    await expect(page.locator('#batch-export-overwrite')).toBeVisible()

    await page.locator('input[name="batch-export-output-mode"][value="folder"]').check({ force: true })
    await expect(page.locator('#batch-export-folder')).toBeVisible()
    await page.locator('#batch-export-folder').fill('C:/exports/sidecars')
    await page.locator('#batch-export-content-mode').selectOption('a1111')
    await page.locator('#batch-export-overwrite').selectOption('skip')
    await page.locator('#btn-start-batch-export').click()

    await expect.poll(() => exportPayload).toMatchObject({
      image_ids: [71, 72],
      output_folder: 'C:/exports/sidecars',
      content_mode: 'a1111',
      overwrite_policy: 'skip',
    })
  })

  test('should preview auto-separate matches for an active gallery filter', async ({ page }) => {
    // Keep this smoke self-contained so public-repo runners do not need a
    // pre-scanned local library on disk.
    await seedAutoSepFilterState(page, { generators: ['comfyui', 'nai', 'webui', 'forge'] })
    const previewImages = [
      buildMockGalleryImage(901, { filename: 'autosep-comfy.png', generator: 'comfyui' }),
      buildMockGalleryImage(902, { filename: 'autosep-nai.png', generator: 'nai' }),
      buildMockGalleryImage(903, { filename: 'autosep-ignore.png', generator: 'unknown' }),
    ]
    await Promise.all(previewImages.map((image) => mockImageAsset(page, image.id)))
    await page.route('**/api/images?**', async (route) => {
      const url = new URL(route.request().url())
      if (url.pathname !== '/api/images') {
        await route.continue()
        return
      }

      const generatorFilter = (url.searchParams.get('generators') || '')
        .split(',')
        .map((value) => value.trim())
        .filter(Boolean)

      const filtered = generatorFilter.length
        ? previewImages.filter((image) => generatorFilter.includes(String(image.generator || 'unknown')))
        : previewImages

      await route.fulfill({
        json: {
          images: filtered,
          total: filtered.length,
          has_more: false,
          next_cursor: null,
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await openSortingSubView(page, 'autosep')

    await page.locator('#btn-preview-autosep').click()

    await expect.poll(async () => {
      const value = await page.locator('#autosep-preview .stat-number').textContent()
      return Number(value || '0')
    }, { timeout: 10000 }).toBe(2)

    await expect(page.locator('#autosep-preview-list .autosep-preview-item').first()).toBeVisible()
  })

  test('auto-separate should report partial move failures without lying about moved count', async ({ page }) => {
    await mockImageAsset(page, 1)
    await seedAutoSepTagFilter(page, ['partial_match'])

    await page.route('**/api/images?**', async (route) => {
      await route.fulfill({
        json: {
          images: [
            { id: 1, filename: 'partial-match-1.png', path: 'L:/Antigravitiy code/sd-image-sorter/test-data/partial-match-1.png' },
            { id: 2, filename: 'partial-match-2.png', path: 'L:/Antigravitiy code/sd-image-sorter/test-data/partial-match-2.png' },
          ],
          total: 2,
          has_more: false,
        },
      })
    })

    await page.route('**/api/batch-move', async (route) => {
      await route.fulfill({
        json: {
          status: 'started',
          total: 2,
          count: 2,
        },
      })
    })

    await page.route('**/api/batch-move/progress', async (route) => {
      await route.fulfill({
        json: {
          status: 'done',
          current: 2,
          total: 2,
          moved: 1,
          errors: 1,
          message: 'Completed! Moved 1 images. 1 errors.',
          recent_errors: [{
            image_id: 2,
            filename: 'partial-match-2.png',
            error: 'Permission denied while writing the destination file',
          }],
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await openSortingSubView(page, 'autosep')

    const actionPanel = page.locator('#autosep-action-mode-panel')
    await expect(actionPanel).toBeVisible()
    await actionPanel.locator('input[data-autosep-operation-mode][value="move"]').check({ force: true })
    await expect(actionPanel.locator('input[data-autosep-operation-mode][value="move"]')).toBeChecked()
    await expect(page.locator('#btn-execute-autosep')).toContainText('Move Images')

    await page.locator('#btn-preview-autosep').click()
    await expect(page.locator('#autosep-preview .stat-number')).toHaveText('2')

    await page.locator('#autosep-destination').fill(MOCK_AUTOSEP_DESTINATION)
    await page.locator('#btn-execute-autosep').click()
    await expect(page.locator('#confirm-modal.visible')).toBeVisible()
    await page.locator('#btn-confirm-ok').click()

    const warningToast = page.locator('.toast.warning').last()
    await expect(warningToast).toContainText('Moved 1 images')
    await expect(warningToast).toContainText('1 failed')
    const errorPanel = page.locator('#autosep-move-errors')
    await expect(errorPanel).toBeVisible()
    await expect(errorPanel).toContainText('partial-match-2.png')
    await expect(errorPanel).toContainText('Permission denied while writing the destination file')
    await expect(errorPanel).not.toContainText('[object Object]')
    await expect(page.locator('#autosep-preview .stat-number')).toHaveText('0')
  })

  test('auto-separate progress should reject missing or malformed error details', async ({ page }) => {
    await openMainPage(page)
    await openSortingSubView(page, 'autosep')

    const failures = await page.evaluate(() => {
      const progressUpdates = [
        { current: 1, total: 1, moved: 0, errors: 1 },
        { current: 1, total: 1, moved: 0, errors: 1, recent_errors: { filename: 'bad.png', error: 'failed' } },
        { current: 1, total: 1, moved: 0, errors: 1, recent_errors: [] },
        { current: 1, total: 1, moved: 0, errors: 1, recent_errors: [{ filename: '', error: 'failed' }] },
        { current: 1, total: 1, moved: 0, errors: 1, recent_errors: [{ filename: 'bad.png', error: ' ' }] },
      ]

      ;(window as any).showAutosepMoveProgress(1)
      return progressUpdates.map((progress) => {
        try {
          ;(window as any).updateAutosepMoveProgress(progress, 1)
          return null
        } catch (error) {
          return {
            name: error instanceof Error ? error.name : typeof error,
            message: error instanceof Error ? error.message : String(error),
          }
        }
      })
    })

    expect(failures).toEqual([
      {
        name: 'TypeError',
        message: 'Auto-Separate progress reports errors but recent_errors is not an array',
      },
      {
        name: 'TypeError',
        message: 'Auto-Separate progress reports errors but recent_errors is not an array',
      },
      {
        name: 'TypeError',
        message: 'Auto-Separate progress reports errors but recent_errors is empty',
      },
      {
        name: 'TypeError',
        message: 'Auto-Separate error detail requires non-empty filename and error fields',
      },
      {
        name: 'TypeError',
        message: 'Auto-Separate error detail requires non-empty filename and error fields',
      },
    ])
  })

  test('auto-separate progress should not rewrite unchanged alert details', async ({ page }) => {
    await openMainPage(page)
    await openSortingSubView(page, 'autosep')

    const result = await page.evaluate(async () => {
      ;(window as any).showAutosepMoveProgress(2)
      const alert = document.querySelector('#autosep-move-errors[role="alert"]')
      if (!(alert instanceof HTMLElement)) {
        throw new Error('Auto-Separate error alert was not rendered')
      }

      let mutations = 0
      let mutationsAfterFirstRender = 0
      let initialErrorNode: Element | null = null
      const observer = new MutationObserver((records) => {
        mutations += records.length
      })
      observer.observe(alert, { childList: true, subtree: true, characterData: true })

      const progressResponses = [
        {
          status: 'running', current: 1, total: 2, moved: 0, errors: 1,
          recent_errors: [{ image_id: 1, filename: 'bad.png', error: 'Permission denied' }],
        },
        {
          status: 'running', current: 1, total: 2, moved: 0, errors: 1,
          recent_errors: [{ image_id: 99, filename: 'bad.png', error: 'Permission denied' }],
        },
        {
          status: 'done', current: 2, total: 2, moved: 1, errors: 1,
          recent_errors: [{ image_id: 1, filename: 'bad.png', error: 'Permission denied' }],
        },
      ]
      let responseIndex = 0
      ;(window as any).App.API.get = async () => {
        if (responseIndex === 1) {
          mutationsAfterFirstRender = mutations
          initialErrorNode = alert.firstElementChild
        }
        const response = progressResponses[Math.min(responseIndex, progressResponses.length - 1)]
        responseIndex += 1
        return response
      }

      await (window as any).pollAutosepMoveProgress(2, '')
      await new Promise((resolve) => setTimeout(resolve, 0))
      const unchangedMutationCount = mutations - mutationsAfterFirstRender
      const preservedNode = alert.firstElementChild === initialErrorNode

      mutations = 0
      ;(window as any).updateAutosepMoveProgress({
        status: 'done', current: 2, total: 2, moved: 1, errors: 1,
        recent_errors: [{ image_id: 1, filename: 'bad.png', error: 'Disk full' }],
      }, 2)
      await new Promise((resolve) => setTimeout(resolve, 0))
      const changedMutationCount = mutations
      const changedText = alert.textContent
      observer.disconnect()

      return { unchangedMutationCount, preservedNode, changedMutationCount, changedText }
    })

    expect(result.unchangedMutationCount).toBe(0)
    expect(result.preservedNode).toBe(true)
    expect(result.changedMutationCount).toBeGreaterThan(0)
    expect(result.changedText).toContain('bad.png: Disk full')
  })

  test('auto-separate visible copy mode should send copy operation', async ({ page }) => {
    await mockImageAsset(page, 1)
    await seedAutoSepTagFilter(page, ['copy_match'])

    let batchMovePayload: any = null

    await page.route('**/api/images?**', async (route) => {
      await route.fulfill({
        json: {
          images: [
            { id: 1, filename: 'copy-match.png', path: 'L:/Antigravitiy code/sd-image-sorter/test-data/copy-match.png' },
          ],
          total: 1,
          has_more: false,
          next_cursor: null,
        },
      })
    })

    await page.route('**/api/batch-move', async (route) => {
      batchMovePayload = route.request().postDataJSON()
      await route.fulfill({ json: { status: 'started', total: 1, count: 1 } })
    })

    await page.route('**/api/batch-move/progress', async (route) => {
      await route.fulfill({
        json: {
          status: 'done',
          current: 1,
          total: 1,
          moved: 1,
          errors: 0,
          operation: 'copy',
          message: 'Completed! Copied 1 images.',
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await openSortingSubView(page, 'autosep')
    await page.locator('#autosep-action-mode-panel input[data-autosep-operation-mode][value="copy"]').check({ force: true })
    await expect(page.locator('#btn-execute-autosep')).toContainText('Copy Images')

    await page.locator('#btn-preview-autosep').click()
    await expect(page.locator('#autosep-preview .stat-number')).toHaveText('1')
    await page.locator('#autosep-destination').fill(MOCK_AUTOSEP_DESTINATION)
    await page.locator('#btn-execute-autosep').click()
    await expect(page.locator('#confirm-modal.visible')).toBeVisible()
    await page.locator('#btn-confirm-ok').click()

    await expect.poll(() => batchMovePayload?.operation).toBe('copy')
  })

  test('auto-separate should pass normalized artist filters through preview and execution', async ({ page }) => {
    await mockImageAsset(page, 1)
    await seedAutoSepFilterState(page, { artist: '  Mock Artist  ' })

    let previewArtist: string | null = null
    let batchMovePayload: any = null

    await page.route('**/api/images**', async (route) => {
      const url = new URL(route.request().url())
      if (url.pathname !== '/api/images') {
        await route.continue()
        return
      }
      previewArtist = url.searchParams.get('artist')
      await route.fulfill({
        json: {
          images: [
            buildMockGalleryImage(1, {
              filename: 'artist-match.png',
              artist: 'Mock Artist',
            }),
          ],
          total: 1,
          has_more: false,
          next_cursor: null,
        },
      })
    })

    await page.route('**/api/batch-move', async (route) => {
      batchMovePayload = route.request().postDataJSON()
      await route.fulfill({
        json: {
          status: 'started',
          total: 1,
          count: 1,
        },
      })
    })

    await page.route('**/api/batch-move/progress', async (route) => {
      await route.fulfill({
        json: {
          status: 'done',
          current: 1,
          total: 1,
          moved: 1,
          errors: 0,
          message: 'Completed! Moved 1 images.',
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await openSortingSubView(page, 'autosep')
    await page.locator('#btn-preview-autosep').click()
    await expect.poll(() => previewArtist).toBe('Mock Artist')
    await expect.poll(async () => {
      const value = await page.locator('#autosep-preview .stat-number').textContent()
      return Number(value || '0')
    }, { timeout: 10000 }).toBe(1)

    await page.locator('#autosep-destination').fill(MOCK_AUTOSEP_DESTINATION)
    await page.locator('#btn-execute-autosep').click()
    await expect(page.locator('#confirm-modal.visible')).toBeVisible()
    await page.locator('#btn-confirm-ok').click()

    await expect.poll(() => batchMovePayload?.artist).toBe('Mock Artist')
  })

  test('auto-separate should surface start errors instead of polling a non-existent batch job', async ({ page }) => {
    await seedAutoSepTagFilter(page, ['too_many'])

    // Preview renders one DOM button per image (no cap — matches production
    // behavior of not arbitrarily limiting results). 6000 DOM nodes freezes
    // Chromium in Playwright. We use a realistic preview size and rely on the
    // backend's 400 response to assert the "too many images" error path — the
    // frontend preview count is independent of the backend limit message.
    const MOCK_PREVIEW_COUNT = 100
    await page.route('**/api/image-thumbnail/**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'image/svg+xml', body: MOCK_IMAGE_SVG })
    })
    await page.route('**/api/image-file/**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'image/svg+xml', body: MOCK_IMAGE_SVG })
    })

    const tooManyImages = Array.from({ length: MOCK_PREVIEW_COUNT }, (_, i) => ({
      id: i + 1,
      filename: `too-many-${i}.png`,
      path: `L:/Antigravitiy code/sd-image-sorter/test-data/too-many-${i}.png`,
    }))

    await page.route('**/api/images?**', async (route) => {
      await route.fulfill({
        json: {
          images: tooManyImages,
          total: tooManyImages.length,
          has_more: false,
        },
      })
    })

    await page.route('**/api/batch-move', async (route) => {
      await route.fulfill({
        status: 400,
        json: {
          detail: 'Found 6000 images matching filters. Maximum allowed is 5000.',
        },
      })
    })

    let progressCalls = 0
    await page.route('**/api/batch-move/progress', async (route) => {
      progressCalls += 1
      await route.fulfill({
        json: {
          status: 'idle',
          current: 0,
          total: 0,
          moved: 0,
          errors: 0,
          message: '',
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await openSortingSubView(page, 'autosep')

    await page.locator('#btn-preview-autosep').click()
    await expect(page.locator('#autosep-preview .stat-number')).toHaveText(String(MOCK_PREVIEW_COUNT))

    await page.locator('#autosep-destination').fill(MOCK_AUTOSEP_DESTINATION)
    await page.locator('#btn-execute-autosep').click()
    await expect(page.locator('#confirm-modal.visible')).toBeVisible()
    await page.locator('#btn-confirm-ok').click()

    const errorToast = page.locator('.toast.error').last()
    await expect(errorToast).toContainText('Maximum allowed is 5000')
    expect(progressCalls).toBeLessThanOrEqual(1)
    await expect(page.locator('#autosep-preview .stat-number')).toHaveText(String(MOCK_PREVIEW_COUNT))
  })

  test('auto-separate preview should cap rendered DOM and load overflow progressively', async ({ page }) => {
    const MOCK_PREVIEW_COUNT = 600
    const previewImages = Array.from({ length: MOCK_PREVIEW_COUNT }, (_, i) => ({
      id: i + 1,
      filename: `bulk-preview-${i + 1}.png`,
      path: `L:/Antigravitiy code/sd-image-sorter/test-data/bulk-preview-${i + 1}.png`,
    }))
    const requestedCursors: Array<string | null> = []
    const encodeCursor = (imageId: number) => `opaque:${imageId}`
    const decodeCursor = (cursor: string | null) => {
      if (!cursor) return 0
      const lastSeenId = Number(cursor.replace('opaque:', ''))
      return Number.isFinite(lastSeenId)
        ? previewImages.findIndex((image) => image.id === lastSeenId) + 1
        : -1
    }

    await page.route('**/api/image-thumbnail/**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'image/svg+xml', body: MOCK_IMAGE_SVG })
    })
    await page.route('**/api/image-file/**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'image/svg+xml', body: MOCK_IMAGE_SVG })
    })

    await page.route('**/api/images**', async (route) => {
      const url = new URL(route.request().url())
      const limit = Number(url.searchParams.get('limit') || '0') || 0
      const cursor = url.searchParams.get('cursor')
      requestedCursors.push(cursor)
      const startIndex = decodeCursor(cursor)
      expect(startIndex).toBeGreaterThanOrEqual(0)
      const effectiveLimit = Math.max(1, limit || 50)
      const rows = previewImages.slice(startIndex, startIndex + effectiveLimit)
      const nextRow = previewImages[startIndex + effectiveLimit] || null

      await route.fulfill({
        json: {
          images: rows,
          total: previewImages.length,
          has_more: Boolean(nextRow),
          next_cursor: nextRow ? encodeCursor(rows.at(-1)?.id || 0) : null,
        },
      })
    })

    await seedAutoSepFilterState(page, { tags: ['bulk_preview'] })
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await openSortingSubView(page, 'autosep')
    await page.locator('#btn-preview-autosep').click()

    await expect(page.locator('#autosep-preview .stat-number')).toHaveText(String(MOCK_PREVIEW_COUNT))
    await expect(page.locator('#autosep-preview-more')).toContainText('+')

    const previewNodeCount = await page.locator('#autosep-preview-list .autosep-preview-item').count()
    expect(previewNodeCount).toBeLessThan(40)

    await page.locator('#autosep-preview-more').click()
    await expect(page.locator('#autosep-overflow-modal.visible')).toBeVisible()
    await expect(page.locator('#autosep-overflow-load-more')).toBeVisible()

    const overflowNodeCount = await page.locator('#autosep-overflow-list .autosep-preview-item').count()
    expect(overflowNodeCount).toBeLessThanOrEqual(200)

    await page.locator('#autosep-overflow-load-more').click()
    await expect
      .poll(() => requestedCursors.filter((value): value is string => Boolean(value)))
      .toContain('opaque:200')
  })

  test('sending selected images to censor should preserve the user selection order', async ({ page }) => {
    const orderedImages = [
      { id: 11, filename: 'ordered-1.png', path: 'L:/ordered-1.png' },
      { id: 22, filename: 'ordered-2.png', path: 'L:/ordered-2.png' },
      { id: 33, filename: 'ordered-3.png', path: 'L:/ordered-3.png' },
    ]
    const detailRequests: string[] = []
    let selectionDataRequests = 0

    await Promise.all(orderedImages.map((image) => mockImageAsset(page, image.id)))

    await page.route('**/api/images**', async (route) => {
      await route.fulfill({
        json: {
          images: orderedImages,
          total: orderedImages.length,
          has_more: false,
          next_cursor: null,
        },
      })
    })

    await page.route('**/api/images/export-data', async (route) => {
      selectionDataRequests += 1
      expect(route.request().method()).toBe('POST')
      expect(route.request().postDataJSON()).toMatchObject({
        image_ids: [11, 22, 33],
      })
      await new Promise((resolve) => setTimeout(resolve, 50))
      await route.fulfill({
        json: {
          images: orderedImages.map((image) => ({
            id: image.id,
            prompt: '',
            tags: [],
          })),
          missing_ids: [],
        },
      })
    })

    page.on('request', (request) => {
      const pathname = new URL(request.url()).pathname
      if (/^\/api\/images\/\d+$/.test(pathname)) {
        detailRequests.push(pathname)
      }
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-toggle-select').click()
    await page.locator('#gallery-grid .gallery-item[data-id="11"]').click()
    await page.locator('#gallery-grid .gallery-item[data-id="22"]').click()
    await page.locator('#gallery-grid .gallery-item[data-id="33"]').click()
    await page.locator('#btn-send-to-censor').click()

    await expect(page.locator('#view-censor.active')).toBeVisible()
    await expect(page.locator('#censor-queue-list .queue-thumb-v2')).toHaveCount(3)
    await expect.poll(() => selectionDataRequests).toBe(1)
    expect(detailRequests).toHaveLength(0)

    await expect.poll(async () => {
      return await page.locator('#censor-queue-list .queue-thumb-v2').evaluateAll((nodes) =>
        nodes.map((node) => Number(node.getAttribute('data-id')))
      )
    }).toEqual([11, 22, 33])
  })

  test('filtered selection sent to censor should keep a token-backed queue window', async ({ page }) => {
    const visibleImages = [
      buildMockGalleryImage(11, { filename: 'censor-filter-window-1.png' }),
      buildMockGalleryImage(22, { filename: 'censor-filter-window-2.png' }),
    ]
    const tokenImages = [
      ...visibleImages,
      buildMockGalleryImage(33, { filename: 'censor-filter-token-3.png' }),
      buildMockGalleryImage(44, { filename: 'censor-filter-token-4.png' }),
    ]
    let selectionChunkRequests = 0
    const exportDataPayloads: any[] = []
    const detectImageIds: number[] = []

    await page.route('**/api/image-thumbnail/**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'image/svg+xml', body: MOCK_IMAGE_SVG })
    })
    await page.route('**/api/image-file/**', async (route) => {
      await route.fulfill({ status: 200, contentType: 'image/svg+xml', body: MOCK_IMAGE_SVG })
    })

    await page.route('**/api/images**', async (route) => {
      const pathname = new URL(route.request().url()).pathname
      if (pathname !== '/api/images') {
        await route.continue()
        return
      }

      await route.fulfill({
        json: {
          images: visibleImages,
          total: tokenImages.length,
          has_more: false,
          next_cursor: null,
        },
      })
    })

    await page.route('**/api/images/selection-token', async (route) => {
      await route.fulfill({
        json: {
          selection_token: 'filtered-censor-token',
          total_estimate: tokenImages.length,
          exact_total: true,
          chunk_size: 2000,
        },
      })
    })

    await page.route('**/api/images/selection-chunk**', async (route) => {
      selectionChunkRequests += 1
      await route.fulfill({
        json: {
          image_ids: tokenImages.map((image) => image.id),
          offset: 0,
          limit: 2000,
          next_offset: null,
          has_more: false,
        },
      })
    })

    await page.route('**/api/images/export-data', async (route) => {
      const payload = route.request().postDataJSON()
      exportDataPayloads.push(payload)
      const ids = Array.isArray(payload.image_ids)
        ? payload.image_ids
        : tokenImages.map((image) => image.id)
      await route.fulfill({
        json: {
          images: tokenImages
            .filter((image) => ids.includes(image.id))
            .map((image) => ({ ...image, tags: [] })),
          missing_ids: [],
          count: ids.length,
          total: tokenImages.length,
          offset: payload.offset || 0,
          limit: payload.limit || ids.length,
          next_offset: null,
          has_more: false,
          source: payload.selection_token ? 'selection_token' : 'image_ids',
          exact_total: true,
        },
      })
    })

    await page.route('**/api/censor/models', async (route) => {
      await route.fulfill({
        json: {
          recommended_backend: 'nudenet',
          models: [
            { id: 'legacy', name: 'YOLO', available: false, files: [] },
            { id: 'nudenet', name: 'NudeNet', available: true },
          ],
        },
      })
    })

    await page.route('**/api/censor/detect', async (route) => {
      const payload = route.request().postDataJSON()
      detectImageIds.push(Number(payload.image_id))
      await route.fulfill({
        json: {
          detections: [],
          image_width: 64,
          image_height: 64,
          warnings: [],
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-toggle-select').click()
    await page.locator('#btn-select-all').click()
    await page.locator('#btn-send-to-censor').click()

    await expect(page.locator('#view-censor.active')).toBeVisible()
    await expect(page.locator('#censor-queue-list .queue-thumb-v2')).toHaveCount(2)
    expect(selectionChunkRequests).toBe(0)

    const queueState = await page.evaluate(() => {
      const state = (window as any).__CENSOR_STATE__
      return {
        token: state?.tokenQueueSource?.selectionToken,
        total: state?.tokenQueueSource?.total,
        loadedCount: state?.tokenQueueSource?.loadedCount,
        queueLength: state?.queue?.length,
      }
    })
    expect(queueState).toMatchObject({
      token: 'filtered-censor-token',
      total: tokenImages.length,
      loadedCount: visibleImages.length,
      queueLength: visibleImages.length,
    })
    expect(exportDataPayloads[0]).toMatchObject({ image_ids: [11, 22] })
    expect(exportDataPayloads[0].selection_token).toBeUndefined()

    await page.locator('#btn-auto-detect-all-sidebar').click()
    await expect.poll(() => detectImageIds).toEqual([11, 22, 33, 44])
    expect(exportDataPayloads.some((payload) => payload.selection_token === 'filtered-censor-token')).toBe(true)
    expect(selectionChunkRequests).toBe(0)
  })

  test('should populate prompt lab tag set selector from API data', async ({ page }) => {
    await page.route('**/api/prompts/categories', async (route) => {
      await route.fulfill({
        json: {
          categories: {
            character: ['1girl'],
            outfit: ['dress'],
          },
        },
      })
    })

    await page.route('**/api/prompts/sets', async (route) => {
      await route.fulfill({
        json: {
          sets: [
            {
              id: 101,
              name: 'School Uniform Set',
              category: 'outfit',
              description: 'Mocked preset',
              members: [{ tag: 'school_uniform', category: 'outfit', weight: 1, required: true }],
              tags: [{ tag: 'school_uniform', weight: 1, required: true }],
            },
          ],
        },
      })
    })

    await page.route('**/api/prompts/exclusions', async (route) => {
      await route.fulfill({ json: { rules: [] } })
    })

    await page.route('**/api/prompts/presets', async (route) => {
      await route.fulfill({ json: { presets: [] } })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openView(page, 'promptlab')
    await expect(page.locator('#view-promptlab.active')).toBeVisible()

    await expect.poll(
      async () => {
        const optionTexts = await page.locator('#promptlab-set-select option').allTextContents()
        return optionTexts.some((text) => text.includes('School Uniform Set'))
      },
      { timeout: 10000 }
    ).toBeTruthy()
  })

  test('prompt lab should preserve runtime empty states instead of reverting to loading placeholders', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('promptlab-guide-seen', 'true')
    })

    await page.route('**/api/prompts/categories', async (route) => {
      await route.fulfill({ json: { categories: {} } })
    })

    await page.route('**/api/prompts/sets', async (route) => {
      await route.fulfill({ json: { sets: [] } })
    })

    await page.route('**/api/prompts/exclusions', async (route) => {
      await route.fulfill({ json: { rules: [] } })
    })

    await page.route('**/api/prompts/presets', async (route) => {
      await route.fulfill({ json: { presets: [] } })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openView(page, 'promptlab')
    await expect(page.locator('#view-promptlab.active')).toBeVisible()

    const categoriesEmpty = page.locator('#promptlab-categories .empty-state')
    const slotsEmpty = page.locator('#promptlab-slots .empty-state')
    const presetsEmpty = page.locator('#promptlab-presets .preset-empty')

    await expect(categoriesEmpty).toContainText('No categories loaded')
    await expect(slotsEmpty).toContainText('Load categories first')
    await expect(presetsEmpty).toContainText('Save your current configuration as a preset')

    await page.waitForTimeout(700)

    await expect(categoriesEmpty).toContainText('No categories loaded')
    await expect(categoriesEmpty).not.toContainText('Loading categories')
    await expect(slotsEmpty).toContainText('Load categories first')
    await expect(slotsEmpty).not.toContainText('Loading slots')
    await expect(presetsEmpty).toContainText('Save your current configuration as a preset')
  })

  test('prompt lab should send selected slots to generation API and render the returned prompt', async ({ page }) => {
    let receivedConfig: any = null

    await page.addInitScript(() => {
      localStorage.setItem('promptlab-guide-seen', 'true')
    })

    await page.route('**/api/prompts/categories', async (route) => {
      await route.fulfill({
        json: {
          categories: {
            style: ['cinematic_lighting'],
            pose: ['standing'],
          },
        },
      })
    })

    await page.route('**/api/prompts/sets', async (route) => {
      await route.fulfill({ json: { sets: [] } })
    })

    await page.route('**/api/prompts/exclusions', async (route) => {
      await route.fulfill({ json: { rules: [] } })
    })

    await page.route('**/api/prompts/presets', async (route) => {
      await route.fulfill({ json: { presets: [] } })
    })

    await page.route('**/api/prompts/generate', async (route) => {
      receivedConfig = route.request().postDataJSON()
      const selectedTags = Object.values(receivedConfig?.categories || {})
        .flatMap((category: any) => category.tags || [])

      await route.fulfill({
        json: {
          positive_prompt: selectedTags.join(', '),
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openView(page, 'promptlab')
    await expect(page.locator('#view-promptlab.active')).toBeVisible()
    // Switch to Random mode tab (default is Stats in the redesigned Prompt Lab)
    await page.locator('.promptlab-tab[data-mode="random"]').click()

    await page.locator('#promptlab-categories .cat-header').filter({ hasText: /style/i }).click()
    await page.locator('#promptlab-categories .cat-tag').filter({ hasText: 'cinematic_lighting' }).click()
    await page.locator('#promptlab-categories .cat-header').filter({ hasText: /pose/i }).click()
    await page.locator('#promptlab-categories .cat-tag').filter({ hasText: 'standing' }).click()
    await page.locator('#btn-promptlab-generate').click()

    await expect.poll(
      () => receivedConfig?.categories?.style?.tags?.[0] ?? null,
      { timeout: 10000 }
    ).toBe('cinematic_lighting')
    expect(receivedConfig?.categories?.pose?.tags).toEqual(['standing'])
    expect(receivedConfig?.quality_preset).toBe('none')
    expect(receivedConfig?.count_tag).toBe('')
    expect(receivedConfig?.include_negative).toBe(false)
    await expect(page.locator('#promptlab-output')).toHaveValue('cinematic_lighting, standing')
  })

  test('prompt lab build should turn image tags into clean category prompts', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('promptlab-guide-seen', 'true')
    })

    const recipeTags = ['masterpiece', 'best_quality', '1girl', 'blue_hair', 'school_uniform', 'standing', 'city', 'future_tag']
    const categoryMap: Record<string, string> = {
      masterpiece: 'quality',
      best_quality: 'quality',
      '1girl': 'character',
      blue_hair: 'body',
      school_uniform: 'outfit',
      standing: 'pose',
      city: 'background',
      future_tag: 'future_category',
    }

    await page.route('**/api/prompts/categories', async (route) => {
      await route.fulfill({
        json: {
          categories: {
            character: ['1girl'],
            body: ['blue_hair'],
            outfit: ['school_uniform'],
            pose: ['standing'],
            background: ['city'],
            quality: ['masterpiece', 'best_quality'],
          },
        },
      })
    })
    await page.route('**/api/prompts/sets', async (route) => {
      await route.fulfill({ json: { sets: [] } })
    })
    await page.route('**/api/prompts/exclusions', async (route) => {
      await route.fulfill({ json: { rules: [] } })
    })
    await page.route('**/api/prompts/presets', async (route) => {
      await route.fulfill({ json: { presets: [] } })
    })
    await page.route('**/api/prompts/categorize', async (route) => {
      const tags = route.request().postDataJSON() as string[]
      await route.fulfill({
        json: {
          results: tags.map((tag) => ({ tag, category: categoryMap[tag] || 'unknown' })),
        },
      })
    })
    await page.route('**/api/images/501', async (route) => {
      await route.fulfill({
        json: {
          image: {
            id: 501,
            filename: 'recipe-source.png',
            prompt: recipeTags.join(', '),
            negative_prompt: '',
            width: 1024,
            height: 1536,
            generator: 'comfyui',
          },
          tags: recipeTags.map((tag) => ({ tag, confidence: 0.9 })),
        },
      })
    })
    await page.route('**/api/images?*', async (route) => {
      await route.fulfill({
        json: {
          images: [
            {
              id: 501,
              filename: 'recipe-source.png',
              prompt: recipeTags.join(', '),
              width: 1024,
              height: 1536,
              generator: 'comfyui',
            },
          ],
          total: 1,
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openView(page, 'promptlab')
    await expect(page.locator('#view-promptlab.active')).toBeVisible()
    await page.locator('.promptlab-tab[data-mode="build"]').click()

    await expect.poll(async () => {
      return await page.locator('#pl-build-source option').allTextContents()
    }).toContain('recipe-source.png')

    await page.locator('#pl-build-source').selectOption('501')
    await expect(page.locator('#pl-build-category-workbench')).toBeVisible()
    await expect(page.locator('#pl-build-category-workbench')).toContainText('Image Prompt Recipe')
    await expect(page.locator('#pl-build-category-workbench')).toContainText('school_uniform')
    await expect(page.locator('#pl-build-category-workbench')).toContainText('future_tag')

    await page.locator('#pl-build-use-checked').click()
    await expect(page.locator('#pl-build-prompt')).toHaveValue('1girl, blue_hair, school_uniform, standing, city')

    await page.locator('#pl-build-prompt').fill('masterpiece, 1girl, blue_hair, school_uniform, standing, city, best_quality, 1girl')
    await page.locator('#pl-build-drop-quality').click()
    await expect(page.locator('#pl-build-prompt')).toHaveValue('1girl, blue_hair, school_uniform, standing, city')
  })

  test('prompt lab should clear stale generated prompt when loading a preset', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('promptlab-guide-seen', 'true')
    })

    await page.route('**/api/prompts/categories', async (route) => {
      await route.fulfill({
        json: {
          categories: {
            style: ['cinematic_lighting'],
            pose: ['standing'],
            background: ['city_night'],
          },
        },
      })
    })

    await page.route('**/api/prompts/sets', async (route) => {
      await route.fulfill({ json: { sets: [] } })
    })

    await page.route('**/api/prompts/exclusions', async (route) => {
      await route.fulfill({ json: { rules: [] } })
    })

    await page.route('**/api/prompts/presets', async (route) => {
      await route.fulfill({
        json: {
          presets: [
            {
              id: 1,
              name: 'Night Preset',
              config: {
                slots: {
                  background: ['city_night'],
                },
                weights: {},
                locked: {},
              },
            },
          ],
        },
      })
    })

    await page.route('**/api/prompts/generate', async (route) => {
      const payload = route.request().postDataJSON()
      const selectedTags = Object.values(payload?.categories || {})
        .flatMap((category: any) => category.tags || [])

      await route.fulfill({
        json: {
          positive_prompt: selectedTags.join(', '),
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openView(page, 'promptlab')
    await expect(page.locator('#view-promptlab.active')).toBeVisible()
    // Switch to Random mode tab (default is Stats in the redesigned Prompt Lab)
    await page.locator('.promptlab-tab[data-mode="random"]').click()

    await page.locator('#promptlab-categories .cat-header').filter({ hasText: /style/i }).click()
    await page.locator('#promptlab-categories .cat-tag').filter({ hasText: 'cinematic_lighting' }).click()
    await page.locator('#promptlab-categories .cat-header').filter({ hasText: /pose/i }).click()
    await page.locator('#promptlab-categories .cat-tag').filter({ hasText: 'standing' }).click()
    await page.locator('#btn-promptlab-generate').click()

    await expect(page.locator('#promptlab-output')).toHaveValue('cinematic_lighting, standing')
    await expect(page.locator('#btn-promptlab-use-gallery')).toBeEnabled()
    await page.locator('#btn-promptlab-use-gallery').click()
    await expect(page.locator('#view-gallery.active')).toBeVisible()
    await expect(page.locator('#summary-prompt')).toContainText('cinematic_lighting, standing')

    await openView(page, 'promptlab')
    await expect(page.locator('#view-promptlab.active')).toBeVisible()
    await page.locator('.promptlab-tab[data-mode="random"]').click()

    await page.locator('.btn-preset-load[data-id="1"]').click()
    await expect(page.locator('#promptlab-output')).toHaveValue('')
    await expect(page.locator('#btn-promptlab-use-gallery')).toBeDisabled()

    await page.locator('#btn-promptlab-generate').click()
    await expect(page.locator('#promptlab-output')).toHaveValue('city_night')

    await page.locator('#btn-promptlab-use-gallery').click()
    await expect(page.locator('#view-gallery.active')).toBeVisible()
    await expect(page.locator('#summary-prompt')).toContainText('city_night')
  })

  test('prompt lab validate should respect violations returned by the backend contract', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('promptlab-guide-seen', 'true')
    })

    await page.route('**/api/prompts/categories', async (route) => {
      await route.fulfill({
        json: {
          categories: {
            outfit: ['school_uniform'],
          },
        },
      })
    })

    await page.route('**/api/prompts/sets', async (route) => {
      await route.fulfill({ json: { sets: [] } })
    })

    await page.route('**/api/prompts/exclusions', async (route) => {
      await route.fulfill({ json: { rules: [] } })
    })

    await page.route('**/api/prompts/presets', async (route) => {
      await route.fulfill({ json: { presets: [] } })
    })

    await page.route('**/api/prompts/validate', async (route) => {
      await route.fulfill({
        json: {
          valid: false,
          violations: [
            {
              rule: 'No Uniform Clash',
              conflicting_tags: ['school_uniform'],
            },
          ],
          suggestions: [],
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openView(page, 'promptlab')
    await expect(page.locator('#view-promptlab.active')).toBeVisible()
    await page.locator('.promptlab-tab[data-mode="random"]').click()

    await page.locator('#promptlab-categories .cat-header').filter({ hasText: /outfit/i }).click()
    await page.locator('#promptlab-categories .cat-tag').filter({ hasText: 'school_uniform' }).click()
    await page.locator('#btn-promptlab-validate').click()

    await expect(page.locator('#toast-container .toast.error .toast-message').last()).toContainText('Found 1 conflict(s)')
  })

  test('artist filter should sync into gallery summary and clear cleanly', async ({ page }) => {
    await page.route('**/api/artists/stats', async (route) => {
      await route.fulfill({
        json: {
          total_images: 10,
          identified_images: 4,
          undefined_count: 1,
          artist_counts: {
            mock_artist: 3,
          },
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openView(page, 'artist')
    await expect(page.locator('#view-artist.active')).toBeVisible()

    const artistGuideClose = page.locator('#artist-first-use-guide [data-guide-close]')
    if (await artistGuideClose.count()) {
      await artistGuideClose.click()
    }

    const artistCard = page.locator('#artist-results-grid .artist-card').first()
    await expect(artistCard).toBeVisible()
    await artistCard.click()

    await page.locator('#btn-filter-by-artist').click()
    await expect(page.locator('#view-gallery.active')).toBeVisible()
    const filtersToggle = page.locator('[data-sidebar-section="filters"] .sidebar-section-toggle')
    if (await filtersToggle.getAttribute('aria-expanded') === 'false') {
      await filtersToggle.click()
    }
    await expect(page.locator('#artist-filter-row')).toBeVisible()
    await expect(page.locator('#summary-artist')).toContainText('Mock Artist')

    await page.locator('#btn-clear-artist').click()
    await expect(page.locator('#artist-filter-row')).toBeHidden()
  })

  test('artist identification should send the selected model source and local path', async ({ page }) => {
    let identifyPayload: any = null

    await page.addInitScript(() => {
      localStorage.setItem('artist-guide-seen', 'true')
    })
    await mockGalleryImages(page, [
      { id: 401, filename: 'artist-selected.png' },
    ])
    await mockArtistDiagnosticsReady(page)

    await page.route('**/api/artists/stats', async (route) => {
      await route.fulfill({
        json: {
          total_images: 10,
          identified_images: 0,
          undefined_count: 0,
          artist_counts: {},
        },
      })
    })

    await page.route('**/api/artists/identify-batch', async (route) => {
      identifyPayload = route.request().postDataJSON()
      await route.fulfill({
        json: {
          message: 'Batch identification started',
          total: identifyPayload?.image_ids?.length || 0,
        },
      })
    })

    await page.route('**/api/artists/batch-progress', async (route) => {
      await route.fulfill({
        json: {
          running: false,
          total: 1,
          processed: 1,
          errors: 0,
          results: [],
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-toggle-select').click()
    await page.locator('#gallery-grid .gallery-item').first().click()

    await openView(page, 'artist')
    await expect(page.locator('#view-artist.active')).toBeVisible()

    await page.selectOption('#artist-model-source', 'local')
    await page.locator('#artist-model-path').fill('C:/models/artist.onnx')
    await page.locator('#btn-identify-selected').click()

    await expect.poll(
      () => identifyPayload?.model_source ?? null,
      { timeout: 10000 }
    ).toBe('local')
    expect(identifyPayload?.model_path).toBe('C:/models/artist.onnx')
    expect(Array.isArray(identifyPayload?.image_ids)).toBeTruthy()
    expect(identifyPayload?.image_ids?.length).toBe(1)
  })

  test('artist identify selected should stay disabled until there is a gallery selection', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('artist-guide-seen', 'true')
    })
    await mockGalleryImages(page, [
      { id: 402, filename: 'artist-toggle-selection.png' },
    ])
    await mockArtistDiagnosticsReady(page)

    await page.route('**/api/artists/stats', async (route) => {
      await route.fulfill({
        json: {
          total_images: 10,
          identified_images: 0,
          undefined_count: 0,
          artist_counts: {},
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await openView(page, 'artist')
    await expect(page.locator('#view-artist.active')).toBeVisible()
    await expect(page.locator('#btn-identify-selected')).toBeDisabled()

    await openView(page, 'gallery')
    await page.locator('#btn-toggle-select').click()
    await page.locator('#gallery-grid .gallery-item').first().click()

    await openView(page, 'artist')
    await expect(page.locator('#btn-identify-selected')).toBeEnabled()
  })

  test('artist start card should dismiss and persist after acknowledgement', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.removeItem('artist-guide-seen')
    })
    await mockArtistDiagnosticsReady(page)

    await page.route('**/api/artists/stats', async (route) => {
      await route.fulfill({
        json: {
          total_images: 10,
          identified_images: 0,
          undefined_count: 0,
          artist_counts: {},
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openView(page, 'artist')
    await expect(page.locator('#artist-start-card')).toBeHidden()

    await page.locator('#btn-help').click()
    await expect(page.locator('#artist-start-card')).toBeVisible()
    const guideClose = page.locator('.guide-modal-close')
    if (await guideClose.count()) {
      await guideClose.first().click({ force: true })
    }

    await page.locator('#artist-start-dismiss').click()
    await expect(page.locator('#artist-start-card')).toBeHidden()
    await expect.poll(() => page.evaluate(() => localStorage.getItem('artist-guide-seen'))).toBe('true')

    await page.evaluate(() => {
      localStorage.removeItem('artist-guide-seen')
      window.ArtistIdent?.showFirstUseGuide?.()
    })

    await expect(page.locator('#artist-start-card')).toBeHidden()
  })

  test('manual sort discard should require confirmation before deleting the saved session', async ({ page }) => {
    let deleteSessionCalls = 0

    await mockImageAsset(page, 1)

    await page.route('**/api/sort/current', async (route) => {
      await route.fulfill({
        json: {
          image: { id: 1, filename: 'resume.png' },
          remaining: 4,
          done: false,
        },
      })
    })

    await page.route('**/api/sort/session', async (route) => {
      if (route.request().method() === 'DELETE') {
        deleteSessionCalls += 1
        await route.fulfill({ json: { status: 'ok' } })
        return
      }

      await route.fulfill({ json: { status: 'ok' } })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openSortingSubView(page, 'manual')
    await expect(page.locator('#sort-resume-banner')).toBeVisible()

    await page.locator('#btn-discard-session').click()
    await expect(page.locator('#confirm-modal.visible')).toBeVisible()
    expect(deleteSessionCalls).toBe(0)

    await page.locator('#btn-confirm-cancel').click()
    await expect(page.locator('#confirm-modal.visible')).toHaveCount(0)
    expect(deleteSessionCalls).toBe(0)
    await expect(page.locator('#sort-resume-banner')).toBeVisible()

    await page.locator('#btn-discard-session').click()
    await page.locator('#btn-confirm-ok').click()
    await expect.poll(() => deleteSessionCalls).toBe(1)
    await expect(page.locator('#sort-resume-banner')).toBeHidden()
  })

  test('manual sort resume banner should show saved session mode and folders context', async ({ page }) => {
    await page.setViewportSize({ width: 1366, height: 768 })
    await mockImageAsset(page, 1)

    await page.route('**/api/sort/current', async (route) => {
      await route.fulfill({
        json: {
          image: { id: 1, filename: 'resume.png' },
          remaining: 12,
          done: false,
          operation_mode: 'copy',
          folders: {
            a: 'C:/sorted/keep',
            d: 'C:/sorted/best',
          },
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openSortingSubView(page, 'manual')

    const banner = page.locator('#sort-resume-banner')
    await expect(banner).toBeVisible()
    await expect(banner).toContainText('12 images remaining')
    await expect(banner).toContainText('Saved session action mode: Copy and keep originals')
    await expect(banner).toContainText('A: C:/sorted/keep')
    await expect(banner).toContainText('D: C:/sorted/best')
    await expect(banner).toContainText('Setup preferences here may differ from the active saved session.')

    const layout = await page.evaluate(() => {
      const actions = document.getElementById('sort-setup-actions')
      const count = document.getElementById('sort-scope-count')
      const resume = document.getElementById('sort-resume-banner')
      const columns = document.querySelector('.sort-setup-columns')
      if (!actions || !count || !resume || !columns) {
        throw new Error('Missing Manual Sort resume layout element')
      }

      const actionRect = actions.getBoundingClientRect()
      const countRect = count.getBoundingClientRect()
      const resumeRect = resume.getBoundingClientRect()
      const columnsRect = columns.getBoundingClientRect()
      const isContained = (inner: DOMRect, outer: DOMRect): boolean => (
        inner.top >= outer.top &&
        inner.left >= outer.left &&
        inner.bottom <= outer.bottom &&
        inner.right <= outer.right
      )
      const overlapsColumns = (
        actionRect.left < columnsRect.right &&
        actionRect.right > columnsRect.left &&
        actionRect.top < columnsRect.bottom &&
        actionRect.bottom > columnsRect.top
      )

      return {
        actionFullyVisible: (
          actionRect.top >= 0 &&
          actionRect.left >= 0 &&
          actionRect.bottom <= window.innerHeight &&
          actionRect.right <= window.innerWidth
        ),
        countContained: isContained(countRect, actionRect),
        resumeContained: isContained(resumeRect, actionRect),
        resumeFullyVisible: (
          resumeRect.top >= 0 &&
          resumeRect.left >= 0 &&
          resumeRect.bottom <= window.innerHeight &&
          resumeRect.right <= window.innerWidth
        ),
        hasHorizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        overlapsColumns,
      }
    })

    expect(layout).toEqual({
      actionFullyVisible: true,
      countContained: true,
      resumeContained: true,
      resumeFullyVisible: true,
      hasHorizontalOverflow: false,
      overlapsColumns: false,
    })
  })

  test('manual sort start should resume unfinished session instead of starting over', async ({ page }) => {
    await seedManualSortFilterState(page)
    await mockImageAsset(page, 701)
    await page.addInitScript((setupFolder) => {
      localStorage.setItem('sort-folder-a', setupFolder)
      localStorage.setItem('manual_sort_operation_mode_v1', 'copy')
    }, MOCK_MANUAL_SORT_DESTINATION)

    let startRequests = 0
    let setFolderRequests = 0

    await page.route('**/api/sort/current', async (route) => {
      await route.fulfill({
        json: {
          image: { id: 701, filename: 'unfinished-session.png' },
          tags: [],
          done: false,
          index: 1,
          total: 3,
          remaining: 2,
          sorted_count: 1,
          skipped_count: 0,
          undo_available: true,
          redo_available: false,
          image_ids: [700, 701, 702],
          folders: { a: 'C:/sorted/keep' },
          operation_mode: 'move',
        },
      })
    })

    await page.route('**/api/sort/set-folders', async (route) => {
      setFolderRequests += 1
      await route.fulfill({ json: { status: 'ok' } })
    })

    await page.route('**/api/sort/start**', async (route) => {
      startRequests += 1
      await route.fulfill({ status: 500, json: { detail: 'Start should not be called while resume is available' } })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openSortingSubView(page, 'manual')

    await expect(page.locator('#sort-resume-banner')).toBeVisible()
    await expect(page.locator('.folder-path-input[data-key="a"]')).toHaveValue(MOCK_MANUAL_SORT_DESTINATION)
    await expect(page.locator('input[name="manual-sort-operation"][value="copy"]')).toBeChecked()
    await page.locator('.folder-path-input[data-key="a"]').fill(MOCK_MANUAL_SORT_DESTINATION)

    await page.locator('#btn-start-sorting').click()
    await expect(page.locator('#confirm-modal.visible')).toBeVisible()
    await expect(page.locator('#confirm-title')).toContainText('Resume saved Manual Sort session?')
    await expect(page.locator('#confirm-message')).toContainText('discard the saved session first')
    await page.locator('#btn-confirm-cancel').click()
    await expect(page.locator('#sort-resume-banner')).toBeVisible()
    expect(startRequests).toBe(0)
    expect(setFolderRequests).toBe(0)

    await page.locator('#btn-start-sorting').click()
    await expect(page.locator('#confirm-title')).toContainText('Resume saved Manual Sort session?')
    await page.locator('#btn-confirm-ok').click()

    await expect(page.locator('#sort-interface')).toBeVisible()
    await expect(page.locator('#sort-progress-text')).toHaveText('1 / 3')
    await expect(page.locator('.folder-path-input[data-key="a"]')).toHaveValue('C:/sorted/keep')
    await expect(page.locator('input[name="manual-sort-operation"][value="move"]')).toBeChecked()
    expect(await page.evaluate(() => {
      const state = (window as typeof window & {
        ManualSortState?: { folders: Record<string, string>, operationMode: string }
      }).ManualSortState
      if (!state) throw new Error('Manual Sort state is unavailable after resume')
      return { folders: state.folders, operationMode: state.operationMode }
    })).toEqual({
      folders: { a: 'C:/sorted/keep' },
      operationMode: 'move',
    })
    await expect.poll(() => startRequests).toBe(0)
    expect(setFolderRequests).toBe(0)
  })


  test('manual sort resume should restore counts and support redo after undoing a saved action', async ({ page }) => {
    let resumeRequested = false

    await mockImageAsset(page, 1)
    await mockImageAsset(page, 2)
    await mockImageAsset(page, 3)

    await page.route('**/api/sort/current', async (route) => {
      if (!resumeRequested) {
        await route.fulfill({
          json: {
            image: { id: 1, filename: 'resume.png' },
            remaining: 2,
            done: false,
          },
        })
        return
      }

      await route.fulfill({
        json: {
          image: { id: 1, filename: 'resume.png' },
          tags: [],
          index: 1,
          total: 3,
          remaining: 2,
          sorted_count: 0,
          skipped_count: 1,
          undo_available: true,
          redo_available: false,
          image_ids: [1, 2, 3],
          folders: {
            a: 'C:/sorted/keep',
          },
        },
      })
    })

    await page.route('**/api/sort/action?action=undo', async (route) => {
      await route.fulfill({
        json: {
          status: 'undone',
          undone_action: 'skip',
          folder_key: null,
          image: { id: 1, filename: 'resume.png' },
          tags: [],
          index: 1,
          total: 3,
          remaining: 2,
          sorted_count: 0,
          skipped_count: 0,
        },
      })
    })

    await page.route('**/api/sort/action?action=redo', async (route) => {
      await route.fulfill({
        json: {
          status: 'redone',
          redone_action: 'skip',
          image: { id: 2, filename: 'after-redo.png' },
          tags: [],
          index: 2,
          total: 3,
          remaining: 1,
          sorted_count: 0,
          skipped_count: 1,
          undo_available: true,
          redo_available: false,
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openSortingSubView(page, 'manual')
    await expect(page.locator('#sort-resume-banner')).toBeVisible()

    resumeRequested = true
    await page.locator('#btn-resume-sorting').click()
    await expect(page.locator('#sort-interface')).toBeVisible()
    await expect(page.locator('#sort-skipped-count')).toHaveText('1')
    await expect(page.locator('#folder-name-a')).toContainText('keep')
    await expect(page.locator('#preview-scroll .preview-thumb')).toHaveCount(3)

    await page.keyboard.press('Control+Z')
    await expect(page.locator('#sort-skipped-count')).toHaveText('0')

    await page.keyboard.press('y')
    await expect(page.locator('#sort-skipped-count')).toHaveText('1')
  })

  test('manual sort resume should stay on setup if the saved session cannot be loaded', async ({ page }) => {
    let resumeRequested = false

    await mockImageAsset(page, 1)

    await page.route('**/api/sort/current', async (route) => {
      if (!resumeRequested) {
        await route.fulfill({
          json: {
            image: { id: 1, filename: 'resume.png' },
            remaining: 4,
            done: false,
          },
        })
        return
      }

      await route.fulfill({
        status: 500,
        json: {
          detail: 'Session could not be restored',
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openSortingSubView(page, 'manual')
    await expect(page.locator('#sort-resume-banner')).toBeVisible()

    resumeRequested = true
    await page.locator('#btn-resume-sorting').click()

    await expect(page.locator('#sort-setup')).toBeVisible()
    await expect(page.locator('#sort-interface')).not.toBeVisible()
    await expect(page.locator('#sort-resume-banner')).toBeVisible()
  })

  test('should support manual sort skip, undo, and redo without desyncing the current image', async ({ page }) => {
    let currentImageId = 501
    let currentSessionCalls = 0

    await seedManualSortFilterState(page)
    await mockGalleryImages(page, [
      { id: 501, filename: 'manual-sort-1.png' },
      { id: 502, filename: 'manual-sort-2.png' },
    ])
    await page.route('**/api/sort/set-folders', async (route) => {
      await route.fulfill({ json: { status: 'ok' } })
    })
    await page.route('**/api/sort/start**', async (route) => {
      await route.fulfill({
        json: {
          status: 'ok',
          total_images: 2,
        },
      })
    })
    await page.route('**/api/sort/current', async (route) => {
      currentSessionCalls += 1
      if (currentSessionCalls <= 2) {
        await route.fulfill({ json: { done: true, image: null } })
        return
      }

      await route.fulfill({
        json: {
          image: { id: currentImageId, filename: `manual-sort-${currentImageId - 500}.png` },
          tags: [],
          index: currentImageId === 501 ? 1 : 2,
          total: 2,
          remaining: currentImageId === 501 ? 1 : 0,
          sorted_count: 0,
          skipped_count: currentImageId === 501 ? 0 : 1,
          undo_available: currentImageId !== 501,
          redo_available: false,
          image_ids: [501, 502],
          folders: {
            a: MOCK_MANUAL_SORT_DESTINATION,
          },
          operation_mode: 'move',
        },
      })
    })
    await page.route('**/api/sort/action?action=skip', async (route) => {
      currentImageId = 502
      await route.fulfill({
        json: {
          status: 'ok',
          image: { id: 502, filename: 'manual-sort-2.png' },
          tags: [],
          index: 2,
          total: 2,
          remaining: 0,
          sorted_count: 0,
          skipped_count: 1,
          undo_available: true,
          redo_available: false,
          image_ids: [501, 502],
        },
      })
    })
    await page.route('**/api/sort/action?action=undo', async (route) => {
      currentImageId = 501
      await route.fulfill({
        json: {
          status: 'undone',
          undone_action: 'skip',
          image: { id: 501, filename: 'manual-sort-1.png' },
          tags: [],
          index: 1,
          total: 2,
          remaining: 1,
          sorted_count: 0,
          skipped_count: 0,
          undo_available: false,
          redo_available: true,
          image_ids: [501, 502],
        },
      })
    })
    await page.route('**/api/sort/action?action=redo', async (route) => {
      currentImageId = 502
      await route.fulfill({
        json: {
          status: 'redone',
          redone_action: 'skip',
          image: { id: 502, filename: 'manual-sort-2.png' },
          tags: [],
          index: 2,
          total: 2,
          remaining: 0,
          sorted_count: 0,
          skipped_count: 1,
          undo_available: true,
          redo_available: false,
          image_ids: [501, 502],
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await openSortingSubView(page, 'manual')

    await page.locator('.folder-path-input[data-key="a"]').fill(MOCK_MANUAL_SORT_DESTINATION)
    await page.locator('#btn-start-sorting').click()
    await expect(page.locator('#confirm-modal.visible')).toBeVisible()
    await page.locator('#btn-confirm-ok').click()
    await expect(page.locator('#sort-interface')).toBeVisible()

    const currentImage = page.locator('#current-image')
    await expect(currentImage).toBeVisible()
    await expect.poll(() => currentImage.getAttribute('src'), { timeout: 10000 }).not.toBe('')
    const initialSrc = normalizeImageSrc(await currentImage.getAttribute('src'))
    expect(initialSrc).not.toBeNull()

    await page.keyboard.press('Space')
    await expect.poll(async () => normalizeImageSrc(await currentImage.getAttribute('src')), { timeout: 10000 }).not.toBe(initialSrc)
    const skippedSrc = normalizeImageSrc(await currentImage.getAttribute('src'))
    expect(skippedSrc).not.toBeNull()

    await page.keyboard.press('Control+Z')
    await expect.poll(async () => normalizeImageSrc(await currentImage.getAttribute('src')), { timeout: 10000 }).toBe(initialSrc)

    await page.keyboard.press('y')
    await expect.poll(async () => normalizeImageSrc(await currentImage.getAttribute('src')), { timeout: 10000 }).toBe(skippedSrc)

    await page.locator('#btn-exit-sorting').click()
  })

  test('similar search should support drag and drop uploads', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('similar-guide-seen', 'true')
    })

    await mockImageAsset(page, 7)
    await page.route('**/api/similarity/model-status', async (route) => {
      await route.fulfill({
        json: {
          available: true,
          runtime_loaded: true,
          message: 'CLIP model is loaded and ready.',
        },
      })
    })
    await page.route('**/api/similarity/stats', async (route) => {
      await route.fulfill({
        json: {
          total_images: 5,
          embedded_images: 5,
          embedded_count: 5,
          pending: 0,
          pending_count: 0,
        },
      })
    })
    await page.route('**/api/similarity/progress', async (route) => {
      await route.fulfill({
        json: {
          running: false,
          total: 0,
          processed: 0,
          errors: 0,
        },
      })
    })

    await page.route('**/api/similarity/search-upload**', async (route) => {
      await route.fulfill({
        json: {
          results: [
            { id: 7, filename: 'drop-match.png', similarity: 0.987 },
          ],
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openView(page, 'similar')
    await expect(page.locator('#view-similar.active')).toBeVisible()

    const dataTransfer = await page.evaluateHandle(() => {
      const transfer = new DataTransfer()
      const file = new File([new Uint8Array([1, 2, 3, 4])], 'query.png', { type: 'image/png' })
      transfer.items.add(file)
      return transfer
    })

    await page.locator('#similar-upload-dropzone').dispatchEvent('drop', { dataTransfer })

    await expect(page.locator('#similar-results .similar-result').first()).toBeVisible()
    await expect(page.locator('#similar-results .similar-name').first()).toContainText('drop-match.png')
  })

  test('similar embedding should resume running progress and report processed plus errors correctly', async ({ page }) => {
    let progressCalls = 0
    let releaseTerminalProgress: () => void = () => undefined
    let signalTerminalProgressStarted: () => void = () => undefined
    const terminalProgressGate = new Promise<void>((resolve) => {
      releaseTerminalProgress = resolve
    })
    const terminalProgressStarted = new Promise<void>((resolve) => {
      signalTerminalProgressStarted = resolve
    })

    await page.addInitScript(() => {
      localStorage.setItem('similar-guide-seen', 'true')
    })
    await page.route('**/api/similarity/model-status', async (route) => {
      await route.fulfill({
        json: {
          available: true,
          runtime_loaded: true,
          message: 'CLIP model is loaded and ready.',
        },
      })
    })

    await page.route('**/api/similarity/stats', async (route) => {
      await route.fulfill({
        json: {
          total_images: 10,
          embedded_images: 4,
          embedded_count: 4,
          pending: 6,
          pending_count: 6,
        },
      })
    })

    await page.route('**/api/similarity/progress', async (route) => {
      progressCalls += 1

      if (progressCalls === 1) {
        await route.fulfill({
          json: {
            running: true,
            total: 5,
            processed: 3,
            errors: 1,
          },
        })
        return
      }

      signalTerminalProgressStarted()
      await terminalProgressGate
      await route.fulfill({
        json: {
          running: false,
          total: 5,
          processed: 4,
          errors: 1,
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openView(page, 'similar')
    await expect(page.locator('#view-similar.active')).toBeVisible()
    await terminalProgressStarted

    await expect(page.locator('#btn-similar-embed')).toBeDisabled()
    await expect(page.locator('#btn-similar-search')).toBeDisabled()
    await expect(page.locator('#btn-similar-upload')).toBeDisabled()
    await expect(page.locator('#btn-similar-duplicates')).toBeDisabled()
    releaseTerminalProgress()
    await expect.poll(() => progressCalls, { timeout: 10000 }).toBeGreaterThanOrEqual(2)
    await expect(page.locator('#btn-similar-embed')).toBeEnabled()
    await expect(page.locator('#btn-similar-search')).toBeEnabled()
    await expect(page.locator('#btn-similar-upload')).toBeEnabled()
    await expect(page.locator('#btn-similar-duplicates')).toBeEnabled()
    await expect(page.locator('#similar-embed-text')).toContainText('5/5')
    await expect(page.locator('#similar-embed-text')).toContainText('4 embedded')
  })

  test('similar duplicate search should explain when there are not enough embedded images', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('similar-guide-seen', 'true')
    })
    await page.route('**/api/similarity/model-status', async (route) => {
      await route.fulfill({
        json: {
          available: true,
          runtime_loaded: true,
          message: 'CLIP model is loaded and ready.',
        },
      })
    })

    await page.route('**/api/similarity/stats', async (route) => {
      await route.fulfill({
        json: {
          total_images: 1,
          embedded_images: 1,
          embedded_count: 1,
          pending: 0,
          pending_count: 0,
        },
      })
    })

    await page.route('**/api/similarity/progress', async (route) => {
      await route.fulfill({
        json: {
          running: false,
          total: 0,
          processed: 0,
          errors: 0,
        },
      })
    })

    await page.route('**/api/similarity/duplicates**', async (route) => {
      await route.fulfill({
        json: {
          duplicates: [],
          count: 0,
          threshold: 0.95,
          reason: 'insufficient_embeddings',
          embedded_count: 1,
          minimum_required: 2,
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openView(page, 'similar')
    await expect(page.locator('#view-similar.active')).toBeVisible()

    await page.locator('.similar-tab[data-target="panel-similar-duplicates"]').click()
    await expect(page.locator('#btn-similar-duplicates')).toBeDisabled()
    await expect(page.locator('#similar-duplicates .empty-state')).toContainText(/waiting for more indexed images|至少需要/i)
  })

  test('manual sort should pass normalized artist filters to session start and preview loading', async ({ page }) => {
    let startArtist: string | null = null
    let previewArtist: string | null = null
    let currentSessionCalls = 0

    await seedManualSortFilterState(page, { artist: '  Mock Artist  ' })
    await mockImageAsset(page, 601)

    await page.route('**/api/images**', async (route) => {
      const url = new URL(route.request().url())
      if (url.pathname !== '/api/images') {
        await route.continue()
        return
      }
      previewArtist = url.searchParams.get('artist')
      await route.fulfill({
        json: {
          images: [
            buildMockGalleryImage(601, {
              filename: 'manual-artist.png',
              artist: 'Mock Artist',
            }),
          ],
          total: 1,
          has_more: false,
          next_cursor: null,
        },
      })
    })
    await page.route('**/api/sort/set-folders', async (route) => {
      await route.fulfill({ json: { status: 'ok' } })
    })
    await page.route('**/api/sort/start**', async (route) => {
      const url = new URL(route.request().url())
      const payload = route.request().postDataJSON() as { artist?: string | null }
      startArtist = payload.artist ?? null
      expect(url.searchParams.get('artist')).toBeNull()
      await route.fulfill({
        json: {
          status: 'ok',
          total_images: 1,
        },
      })
    })
    await page.route('**/api/sort/current', async (route) => {
      currentSessionCalls += 1
      if (currentSessionCalls <= 2) {
        await route.fulfill({ json: { done: true, image: null } })
        return
      }

      await route.fulfill({
        json: {
          image: { id: 601, filename: 'manual-artist.png' },
          tags: [],
          index: 1,
          total: 1,
          remaining: 0,
          sorted_count: 0,
          skipped_count: 0,
          undo_available: false,
          redo_available: false,
          image_ids: [601],
          folders: {
            a: MOCK_MANUAL_SORT_DESTINATION,
          },
          operation_mode: 'move',
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await openSortingSubView(page, 'manual')
    await page.locator('.folder-path-input[data-key="a"]').fill(MOCK_MANUAL_SORT_DESTINATION)
    await page.locator('#btn-start-sorting').click()
    await expect(page.locator('#confirm-modal.visible')).toBeVisible()
    await page.locator('#btn-confirm-ok').click()

    await expect(page.locator('#sort-interface')).toBeVisible()
    expect(startArtist).toBe('Mock Artist')
    expect(previewArtist).toBe('Mock Artist')
  })

  test('should undo a censor brush stroke back to the previous canvas state', async ({ page }) => {
    await mockImageAsset(page, 11)
    await page.route('**/api/images**', async (route) => {
      await route.fulfill({
        json: {
          images: [
            { id: 11, filename: 'censor-undo.png', path: 'L:/censor-undo.png', prompt: 'undo smoke' },
          ],
          total: 1,
          has_more: false,
          next_cursor: null,
        },
      })
    })
    await page.route('**/api/images/export-data', async (route) => {
      await route.fulfill({
        json: {
          images: [{ id: 11, prompt: 'undo smoke', tags: [] }],
          missing_ids: [],
        },
      })
    })

    await page.goto('/')
    await openView(page, 'censor')
    await page.evaluate(() => {
      if (typeof (window as Window & { initCensorEdit?: any }).initCensorEdit === 'function') {
        ;(window as Window & { initCensorEdit?: any }).initCensorEdit()
      }
    })
    await expect.poll(() => page.evaluate(() => {
      return typeof (window as Window & { App?: any }).App?.addToCensorQueue === 'function'
    }), { timeout: 10000 }).toBeTruthy()
    await page.evaluate(async () => {
      await (window as Window & { App?: any }).App.addToCensorQueue([11])
    })

    await expect(page.locator('#view-censor.active')).toBeVisible()
    await expect(page.locator('#canvas-wrapper')).toBeVisible()
    await expect.poll(() => page.evaluate(() => {
      return (window as Window & { __CENSOR_STATE__?: any }).__CENSOR_STATE__?.activeId ?? null
    }), { timeout: 10000 }).toBe(11)

    const penTool = page.locator('[data-tool="pen"]').first()
    if (await penTool.count()) {
      await penTool.click()
    }
    const brushSize = page.locator('#tool-size')
    if (await brushSize.count()) {
      await brushSize.fill('90')
    }

    await expect.poll(() => getActiveCensorCanvasSnapshot(page), { timeout: 10000 }).not.toBeNull()
    const initialSnapshot = await getActiveCensorCanvasSnapshot(page)
    expect(initialSnapshot).not.toBeNull()

    const canvasBox = await getActiveCensorCanvasBox(page)
    expect(canvasBox).not.toBeNull()
    if (!canvasBox) return

    const startX = canvasBox.x + canvasBox.width * 0.45
    const startY = canvasBox.y + canvasBox.height * 0.45
    const endX = canvasBox.x + canvasBox.width * 0.62
    const endY = canvasBox.y + canvasBox.height * 0.62

    await page.mouse.move(startX, startY)
    await page.mouse.down()
    await page.mouse.move(endX, endY, { steps: 8 })
    await page.mouse.up()

    await expect.poll(() => page.evaluate(() => {
      return Number((window as Window & { __CENSOR_STATE__?: any }).__CENSOR_STATE__?.undoStack?.length || 0)
    }), { timeout: 10000 }).toBeGreaterThan(1)
    await expect.poll(() => getActiveCensorCanvasSnapshot(page), { timeout: 5000 }).not.toBe(initialSnapshot)
    const editedSnapshot = await getActiveCensorCanvasSnapshot(page)
    expect(editedSnapshot).not.toBeNull()

    await page.keyboard.press('Control+Z')
    await expect.poll(async () => {
      const snapshot = await getActiveCensorCanvasSnapshot(page)
      return snapshot === initialSnapshot || snapshot !== editedSnapshot
    }, { timeout: 10000 }).toBeTruthy()
  })

  test('low-memory censor mode should load safely and block diff mode', async ({ page }) => {
    await page.addInitScript(() => {
      ;(window as Window & { __SD_SORTER_TEST_FLAGS__?: Record<string, number> }).__SD_SORTER_TEST_FLAGS__ = {
        censorLowMemoryPixelThreshold: 1,
        censorShowChangesPixelThreshold: 1,
      }
    })
    await mockImageAsset(page, 11)
    await page.route('**/api/images**', async (route) => {
      await route.fulfill({
        json: {
          images: [
            { id: 11, filename: 'censor-low-memory.png', path: 'L:/censor-low-memory.png', prompt: 'low memory smoke' },
          ],
          total: 1,
          has_more: false,
          next_cursor: null,
        },
      })
    })
    await page.route('**/api/images/export-data', async (route) => {
      await route.fulfill({
        json: {
          images: [{ id: 11, prompt: 'low memory smoke', tags: [] }],
          missing_ids: [],
        },
      })
    })

    await page.goto('/')
    await openView(page, 'censor')
    await page.evaluate(() => {
      if (typeof (window as Window & { initCensorEdit?: any }).initCensorEdit === 'function') {
        ;(window as Window & { initCensorEdit?: any }).initCensorEdit()
      }
    })
    await expect.poll(() => page.evaluate(() => {
      return typeof (window as Window & { App?: any }).App?.addToCensorQueue === 'function'
    }), { timeout: 10000 }).toBeTruthy()
    await page.evaluate(async () => {
      await (window as Window & { App?: any }).App.addToCensorQueue([11])
    })

    await expect(page.locator('#view-censor.active')).toBeVisible()
    await expect(page.locator('#canvas-wrapper')).toBeVisible()

    await expect.poll(() => page.evaluate(() => {
      return (window as Window & { __CENSOR_STATE__?: any }).__CENSOR_STATE__?.activeId ?? null
    }), { timeout: 10000 }).toBe(11)
    await expect.poll(() => page.evaluate(() => {
      return Boolean((window as Window & { __CENSOR_STATE__?: any }).__CENSOR_STATE__?.lowMemoryMode)
    }), { timeout: 10000 }).toBeTruthy()

    await expect.poll(() => getActiveCensorCanvasSnapshot(page), { timeout: 10000 }).not.toBeNull()
    const initialSnapshot = await getActiveCensorCanvasSnapshot(page)
    expect(initialSnapshot).not.toBeNull()

    await page.locator('#btn-show-changes').click()
    await expect(page.locator('#toast-container')).toContainText(/disabled for large images|大图已禁用 Diff 对比/i)
  })

  test('large-image proxy mode should save censor edits through save-operations instead of save-data', async ({ page }) => {
    let saveOperationsPayload: any = null
    let saveDataCalls = 0

    await mockImageAsset(page, 11)
    await page.route('**/api/images**', async (route) => {
      await route.fulfill({
        json: {
          images: [
            {
              id: 11,
              filename: 'censor-proxy-save.png',
              path: 'L:/censor-proxy-save.png',
              prompt: 'proxy save smoke',
              width: 5000,
              height: 5000,
            },
          ],
          total: 1,
          has_more: false,
          next_cursor: null,
        },
      })
    })
    await page.route('**/api/images/export-data', async (route) => {
      await route.fulfill({
        json: {
          images: [{ id: 11, prompt: 'proxy save smoke', tags: [], width: 5000, height: 5000 }],
          missing_ids: [],
        },
      })
    })
    await page.route('**/api/censor/save-operations', async (route) => {
      saveOperationsPayload = route.request().postDataJSON()
      await route.fulfill({
        json: {
          status: 'ok',
          output_path: 'L:/mock-output/censor-proxy-save.png',
          filename: 'censor-proxy-save.png',
          warnings: [],
        },
      })
    })
    await page.route('**/api/censor/save-data', async (route) => {
      saveDataCalls += 1
      await route.fulfill({
        json: {
          status: 'ok',
          output_path: 'L:/mock-output/censor-proxy-save.png',
          filename: 'censor-proxy-save.png',
          warnings: [],
        },
      })
    })

    await page.goto('/')
    await openView(page, 'censor')
    await page.evaluate(() => {
      if (typeof (window as Window & { initCensorEdit?: any }).initCensorEdit === 'function') {
        ;(window as Window & { initCensorEdit?: any }).initCensorEdit()
      }
    })
    await expect.poll(() => page.evaluate(() => {
      return typeof (window as Window & { App?: any }).App?.addToCensorQueue === 'function'
    }), { timeout: 10000 }).toBeTruthy()
    await page.evaluate(async () => {
      await (window as Window & { App?: any }).App.addToCensorQueue([11])
    })

    await expect(page.locator('#view-censor.active')).toBeVisible()
    await expect(page.locator('#canvas-wrapper')).toBeVisible()
    await expect.poll(() => page.evaluate(() => {
      return (window as Window & { __CENSOR_STATE__?: any }).__CENSOR_STATE__?.activeId ?? null
    }), { timeout: 10000 }).toBe(11)
    await expect.poll(() => page.evaluate(() => {
      return Boolean((window as Window & { __CENSOR_STATE__?: any }).__CENSOR_STATE__?.proxyEditMode)
    }), { timeout: 10000 }).toBeTruthy()

    const penTool = page.locator('[data-tool="pen"]').first()
    if (await penTool.count()) {
      await penTool.click()
    }
    const brushSize = page.locator('#tool-size')
    if (await brushSize.count()) {
      await brushSize.fill('120')
    }

    const canvasBox = await getActiveCensorCanvasBox(page)
    expect(canvasBox).not.toBeNull()
    if (!canvasBox) return

    const startX = canvasBox.x + canvasBox.width * 0.35
    const startY = canvasBox.y + canvasBox.height * 0.35
    const endX = canvasBox.x + canvasBox.width * 0.65
    const endY = canvasBox.y + canvasBox.height * 0.65

    await page.mouse.move(startX, startY)
    await page.mouse.down()
    await page.mouse.move(endX, endY, { steps: 8 })
    await page.mouse.up()

    await expect.poll(() => page.evaluate(() => {
      const state = (window as Window & { __CENSOR_STATE__?: any }).__CENSOR_STATE__
      return Number(state?.queue?.find((item: any) => item.id === 11)?.editOperations?.length || 0)
    }), { timeout: 10000 }).toBeGreaterThan(0)

    await page.locator('#btn-save-all-processed').click()
    await expect(page.locator('#save-options-modal.visible')).toBeVisible()
    await page.locator('#save-output-folder').fill('L:/mock-output')
    await page.locator('#btn-confirm-save-options').click()

    await expect.poll(() => saveOperationsPayload, { timeout: 10000 }).not.toBeNull()
    expect(saveDataCalls).toBe(0)
    expect(saveOperationsPayload.original_image_id).toBe(11)
    expect(Array.isArray(saveOperationsPayload.operations)).toBeTruthy()
    expect(saveOperationsPayload.operations.length).toBeGreaterThan(0)
    expect(saveOperationsPayload.operations[0]?.kind).toBe('stroke')
    expect(saveOperationsPayload.operations[0]?.tool).toBe('pen')
    expect(Array.isArray(saveOperationsPayload.operations[0]?.points)).toBeTruthy()
    expect(saveOperationsPayload.operations[0]?.points.length).toBeGreaterThan(0)
  })

  test('large-image SAM3 mask refs should preview and save without inline mask data', async ({ page }) => {
    let saveOperationsPayload: any = null
    let saveDataCalls = 0

    await mockImageAsset(page, 11)
    await page.route('**/api/images**', async (route) => {
      await route.fulfill({
        json: {
          images: [
            {
              id: 11,
              filename: 'censor-sam3-mask-ref.png',
              path: 'L:/censor-sam3-mask-ref.png',
              prompt: 'sam3 mask ref smoke',
              width: 5000,
              height: 5000,
            },
          ],
          total: 1,
          has_more: false,
          next_cursor: null,
        },
      })
    })
    await page.route('**/api/images/export-data', async (route) => {
      await route.fulfill({
        json: {
          images: [{ id: 11, prompt: 'sam3 mask ref smoke', tags: [], width: 5000, height: 5000 }],
          missing_ids: [],
        },
      })
    })
    await page.route('**/api/censor/segment-text', async (route) => {
      await route.fulfill({
        json: {
          status: 'ok',
          mask: null,
          mask_ref: 'sam3-mask-11',
          mask_bounds: [1000, 1200, 2200, 2800],
          image_width: 5000,
          image_height: 5000,
          text_prompt: 'face',
        },
      })
    })
    await page.route('**/api/censor/mask-cache/sam3-mask-11**', async (route) => {
      await route.fulfill({
        contentType: 'image/png',
        body: Buffer.from(MIXED_MASK_DATA_URL.split(',')[1], 'base64'),
      })
    })
    await page.route('**/api/censor/save-operations', async (route) => {
      saveOperationsPayload = route.request().postDataJSON()
      await route.fulfill({
        json: {
          status: 'ok',
          output_path: 'L:/mock-output/censor-sam3-mask-ref.png',
          filename: 'censor-sam3-mask-ref.png',
          warnings: [],
        },
      })
    })
    await page.route('**/api/censor/save-data', async (route) => {
      saveDataCalls += 1
      await route.fulfill({
        json: {
          status: 'ok',
          output_path: 'L:/mock-output/censor-sam3-mask-ref.png',
          filename: 'censor-sam3-mask-ref.png',
          warnings: [],
        },
      })
    })

    await page.goto('/')
    await openView(page, 'censor')
    await page.evaluate(() => {
      if (typeof (window as Window & { initCensorEdit?: any }).initCensorEdit === 'function') {
        ;(window as Window & { initCensorEdit?: any }).initCensorEdit()
      }
    })
    await expect.poll(() => page.evaluate(() => {
      return typeof (window as Window & { App?: any }).App?.addToCensorQueue === 'function'
    }), { timeout: 10000 }).toBeTruthy()
    await page.evaluate(async () => {
      await (window as Window & { App?: any }).App.addToCensorQueue([11])
    })

    await expect.poll(() => page.evaluate(() => {
      const state = (window as Window & { __CENSOR_STATE__?: any }).__CENSOR_STATE__
      return state?.activeId ?? null
    }), { timeout: 10000 }).toBe(11)
    await expect.poll(() => page.evaluate(() => {
      return Boolean((window as Window & { __CENSOR_STATE__?: any }).__CENSOR_STATE__?.lowMemoryMode)
    }), { timeout: 10000 }).toBeTruthy()

    const initialSnapshot = await getActiveCensorCanvasSnapshot(page)
    expect(initialSnapshot).not.toBeNull()

    await page.evaluate(async () => {
      const result = await (window as Window & { App?: any }).App.API.post('/api/censor/segment-text', {
        image_id: 11,
        text_prompt: 'face',
      })
      await (window as Window & { applyRasterMaskToActiveCanvas?: any }).applyRasterMaskToActiveCanvas(result)
    })

    await expect.poll(() => page.evaluate(() => {
      const state = (window as Window & { __CENSOR_STATE__?: any }).__CENSOR_STATE__
      const operation = state?.queue?.find((item: any) => item.id === 11)?.editOperations?.[0]
      return operation?.mask_ref || null
    }), { timeout: 10000 }).toBe('sam3-mask-11')
    await expect.poll(() => getActiveCensorCanvasSnapshot(page), { timeout: 10000 }).not.toBe(initialSnapshot)

    await page.locator('#btn-save-all-processed').click()
    await expect(page.locator('#save-options-modal.visible')).toBeVisible()
    await page.locator('#save-output-folder').fill('L:/mock-output')
    await page.locator('#btn-confirm-save-options').click()

    await expect.poll(() => saveOperationsPayload, { timeout: 10000 }).not.toBeNull()
    expect(saveDataCalls).toBe(0)
    expect(saveOperationsPayload.original_image_id).toBe(11)
    expect(saveOperationsPayload.operations[0]?.kind).toBe('mask_effect')
    expect(saveOperationsPayload.operations[0]?.mask_ref).toBe('sam3-mask-11')
    expect(Array.isArray(saveOperationsPayload.operations[0]?.mask_bounds)).toBeTruthy()
    expect(saveOperationsPayload.operations[0]?.mask_data ?? null).toBeNull()
  })

  test('small-image inline SAM3 masks should keep full-size preview and save through save-data', async ({ page }) => {
    let saveDataPayload: any = null
    let saveOperationsCalls = 0

    await mockImageAsset(page, 11)
    await page.route('**/api/images**', async (route) => {
      await route.fulfill({
        json: {
          images: [
            {
              id: 11,
              filename: 'censor-sam3-inline-small.png',
              path: 'L:/censor-sam3-inline-small.png',
              prompt: 'sam3 inline small smoke',
              width: 64,
              height: 64,
            },
          ],
          total: 1,
          has_more: false,
          next_cursor: null,
        },
      })
    })
    await page.route('**/api/images/export-data', async (route) => {
      await route.fulfill({
        json: {
          images: [{ id: 11, prompt: 'sam3 inline small smoke', tags: [], width: 64, height: 64 }],
          missing_ids: [],
        },
      })
    })
    await page.route('**/api/censor/segment-text', async (route) => {
      await route.fulfill({
        json: {
          status: 'ok',
          mask: INLINE_MASK_CROP_DATA_URL,
          mask_ref: null,
          mask_bounds: [48, 48, 60, 60],
          image_width: 64,
          image_height: 64,
          text_prompt: 'corner',
        },
      })
    })
    await page.route('**/api/censor/save-data', async (route) => {
      saveDataPayload = route.request().postDataJSON()
      await route.fulfill({
        json: {
          status: 'ok',
          output_path: 'L:/mock-output/censor-sam3-inline-small.png',
          filename: 'censor-sam3-inline-small.png',
          warnings: [],
        },
      })
    })
    await page.route('**/api/censor/save-operations', async (route) => {
      saveOperationsCalls += 1
      await route.fulfill({
        json: {
          status: 'ok',
          output_path: 'L:/mock-output/censor-sam3-inline-small.png',
          filename: 'censor-sam3-inline-small.png',
          warnings: [],
        },
      })
    })

    await page.goto('/')
    await openView(page, 'censor')
    await page.evaluate(() => {
      if (typeof (window as Window & { initCensorEdit?: any }).initCensorEdit === 'function') {
        ;(window as Window & { initCensorEdit?: any }).initCensorEdit()
      }
    })
    await expect.poll(() => page.evaluate(() => {
      return typeof (window as Window & { App?: any }).App?.addToCensorQueue === 'function'
    }), { timeout: 10000 }).toBeTruthy()
    await page.evaluate(async () => {
      await (window as Window & { App?: any }).App.addToCensorQueue([11])
    })

    await expect.poll(() => page.evaluate(() => {
      const state = (window as Window & { __CENSOR_STATE__?: any }).__CENSOR_STATE__
      return state?.activeId ?? null
    }), { timeout: 10000 }).toBe(11)
    await expect.poll(() => page.evaluate(() => {
      return Boolean((window as Window & { __CENSOR_STATE__?: any }).__CENSOR_STATE__?.lowMemoryMode)
    }), { timeout: 10000 }).toBeFalsy()
    await page.selectOption('#censor-style', 'black_bar')
    await expect.poll(() => getActiveCensorCanvasSnapshot(page), { timeout: 10000 }).not.toBeNull()
    const initialSnapshot = await getActiveCensorCanvasSnapshot(page)
    expect(initialSnapshot).not.toBeNull()

    await page.evaluate(async () => {
      const result = await (window as Window & { App?: any }).App.API.post('/api/censor/segment-text', {
        image_id: 11,
        text_prompt: 'corner',
      })
      await (window as Window & { applyRasterMaskToActiveCanvas?: any }).applyRasterMaskToActiveCanvas(result)
    })

    await expect.poll(() => getActiveCensorCanvasSnapshot(page), { timeout: 10000 }).not.toBe(initialSnapshot)

    await page.locator('#btn-save-all-processed').click()
    await expect(page.locator('#save-options-modal.visible')).toBeVisible()
    await page.locator('#save-output-folder').fill('L:/mock-output')
    await page.locator('#btn-confirm-save-options').click()

    await expect.poll(() => saveDataPayload, { timeout: 10000 }).not.toBeNull()
    expect(saveOperationsCalls).toBe(0)
    expect(saveDataPayload.original_image_id).toBe(11)
    expect(typeof saveDataPayload.image_data).toBe('string')
    expect(saveDataPayload.image_data.startsWith('data:image/png;base64,')).toBeTruthy()
  })

  test('censor detect modal should explain simple and pro model capabilities', async ({ page }) => {
    await page.route('**/api/censor/models', async (route) => {
      await route.fulfill({
        json: {
          status: 'ok',
          recommended_backend: 'both',
          models: [
            {
              id: 'legacy',
              name: 'Legacy YOLO',
              available: true,
              recommended: true,
              default_model_path: 'C:/models/wenaka_yolov8s-seg.onnx',
              simple_user_advice: 'Keep mode on Both and leave the model path blank.',
              files: [
                {
                  name: 'wenaka_yolov8s-seg.onnx',
                  path: 'C:/models/wenaka_yolov8s-seg.onnx',
                  size_mb: 45.7,
                  profile: 'privacy-censor',
                  profile_label: 'Privacy-part detector',
                  recommended_for_censor: true,
                  message: 'Specialized for privacy-part detection and censor workflows.',
                  capabilities: {
                    input_mode_label: 'Fixed privacy-part labels',
                    output_mode_label: 'Fast box-first censoring',
                    class_scope_label: '5 built-in privacy classes',
                    supports_text_prompt: false,
                    plain_english: 'Best for normal users who want quick privacy-part auto-detection.',
                  },
                },
                {
                  name: 'yolo26s-seg.onnx',
                  path: 'C:/models/yolo26s-seg.onnx',
                  size_mb: 40.0,
                  profile: 'general-object',
                  profile_label: 'General object segmentation',
                  recommended_for_censor: false,
                  message: 'General segmentation test model.',
                  capabilities: {
                    input_mode_label: 'Fixed built-in object classes',
                    output_mode_label: 'General object segmentation tests',
                    class_scope_label: '80 built-in object classes',
                    supports_text_prompt: false,
                    plain_english: 'Useful for advanced compatibility checks, not free-text prompting.',
                  },
                },
              ],
              privacy_model_count: 1,
              general_model_count: 1,
            },
            {
              id: 'nudenet',
              name: 'NudeNet v3',
              available: true,
              recommended: true,
              message: 'NudeNet model ready.',
              capabilities: {
                input_mode_label: 'No manual prompt input',
                output_mode_label: 'Detection boxes',
                class_scope_label: 'Built-in NSFW body-part classes',
                supports_text_prompt: false,
                plain_english: 'Good default for NSFW region detection.',
              },
            },
            {
              id: 'sam3',
              name: 'SAM 3',
              available: true,
              recommended: true,
              message: 'SAM3 checkpoint and runtime dependencies are ready.',
              capabilities: {
                input_mode_label: 'Text prompt or box prompt',
                output_mode_label: 'Pixel-accurate masks',
                class_scope_label: 'Prompt-guided segmentation',
                supports_text_prompt: true,
                plain_english: 'This is the precise tool for pro users.',
              },
            },
          ],
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openView(page, 'censor')
    await expect(page.locator('#view-censor.active')).toBeVisible()

    // censor-simple-guide is hidden in the redesigned UI (info moved to settings popup)
    await page.locator('#btn-open-detect-modal').click()
    await expect(page.locator('#detect-modal.visible')).toBeVisible()
    await expect(page.locator('#censor-model-type-help')).toContainText('YOLO alone uses the local file below')
    await expect(page.locator('#censor-model-type-status')).toContainText('Use this file: wenaka_yolov8s-seg.onnx')

    // Model details and advanced picker are in collapsed <details> sections
    // Open them to test their content
    const modelDetailsSection = page.locator('#detect-modal details').first()
    await modelDetailsSection.click()
    await expect(page.locator('#censor-capability-panel')).toContainText('Built-in NSFW body-part classes')
    await expect(page.locator('#censor-capability-panel')).toContainText('Prompt-guided segmentation')

    // SAM3 text prompt is in the pro segmentation details section
    const proSection = page.locator('#censor-pro-segmentation-group')
    await proSection.click()
    await expect(page.locator('#censor-text-prompt')).toBeEnabled()

    await page.selectOption('#censor-model-type', 'legacy')
    // Advanced model picker is in a collapsed details section — open it first
    const advancedPickerSection = page.locator('#detect-modal details').nth(1)
    await advancedPickerSection.click()
    const advancedModelsToggle = page.locator('label.checkbox-label', {
      has: page.locator('#censor-show-advanced-models'),
    })
    await advancedModelsToggle.scrollIntoViewIfNeeded()
    await advancedModelsToggle.click()
    await expect(page.locator('#censor-show-advanced-models')).toBeChecked()
    let advancedLegacyModelPath = ''
    await expect.poll(async () => {
      const optionValues = await page.locator('#censor-model-file option').evaluateAll((options) =>
        options
          .map((option) => ({
            text: option.textContent || '',
            value: (option as HTMLOptionElement).value || '',
          }))
          .filter((option) => /yolo26s-seg\.onnx/i.test(option.text) || /yolo26s-seg\.onnx/i.test(option.value))
          .map((option) => option.value)
      )
      advancedLegacyModelPath = optionValues[0] || ''
      return advancedLegacyModelPath
    }, { timeout: 10000 }).not.toBe('')
    await page.selectOption('#censor-model-file', advancedLegacyModelPath)
    await expect(page.locator('#censor-model-type-status')).toContainText('Use this file: yolo26s-seg.onnx')
    await expect(page.locator('#censor-simple-guide')).toContainText('general fixed-class segmentation model')
    await expect(page.locator('.target-region-check').first()).toBeEnabled()
    await expect(page.locator('#censor-target-region-help')).toContainText(/switch back to the recommended privacy detector|Wenaka \/ NudeNet families|自动切回推荐的隐私检测路线/i)
  })

  test('quick auto censor should auto-restore the privacy detector when a general legacy model is selected', async ({ page }) => {
    let detectPayload: any = null

    await mockGalleryImages(page, [
      { id: 601, filename: 'quick-auto-censor.png' },
    ])

    await page.route('**/api/censor/models', async (route) => {
      await route.fulfill({
        json: {
          status: 'ok',
          recommended_backend: 'both',
          models: [
            {
              id: 'legacy',
              name: 'Legacy YOLO',
              available: true,
              recommended: true,
              default_model_path: 'C:/models/wenaka_yolov8s-seg.onnx',
              simple_user_advice: 'Keep mode on Both and leave the model path blank.',
              files: [
                {
                  name: 'wenaka_yolov8s-seg.onnx',
                  path: 'C:/models/wenaka_yolov8s-seg.onnx',
                  size_mb: 45.7,
                  profile: 'privacy-censor',
                  profile_label: 'Privacy-part detector',
                  recommended_for_censor: true,
                  message: 'Specialized for privacy-part detection and censor workflows.',
                  capabilities: {
                    input_mode_label: 'Fixed privacy-part labels',
                    output_mode_label: 'Fast box-first censoring',
                    class_scope_label: '5 built-in privacy classes',
                    supports_text_prompt: false,
                    plain_english: 'Best for normal users who want quick privacy-part auto-detection.',
                  },
                },
                {
                  name: 'yolo26s-seg.onnx',
                  path: 'C:/models/yolo26s-seg.onnx',
                  size_mb: 40.0,
                  profile: 'general-object',
                  profile_label: 'General object segmentation',
                  recommended_for_censor: false,
                  message: 'General segmentation test model.',
                  capabilities: {
                    input_mode_label: 'Fixed built-in object classes',
                    output_mode_label: 'General object segmentation tests',
                    class_scope_label: '80 built-in object classes',
                    supports_text_prompt: false,
                    plain_english: 'Useful for advanced compatibility checks, not free-text prompting.',
                  },
                },
              ],
              privacy_model_count: 1,
              general_model_count: 1,
            },
            {
              id: 'nudenet',
              name: 'NudeNet v3',
              available: true,
              recommended: true,
              message: 'NudeNet model ready.',
              capabilities: {
                input_mode_label: 'No manual prompt input',
                output_mode_label: 'Detection boxes',
                class_scope_label: 'Built-in NSFW body-part classes',
                supports_text_prompt: false,
                plain_english: 'Good default for NSFW region detection.',
              },
            },
            {
              id: 'sam3',
              name: 'SAM 3',
              available: true,
              recommended: true,
              message: 'SAM3 checkpoint and runtime dependencies are ready.',
              capabilities: {
                input_mode_label: 'Text prompt or box prompt',
                output_mode_label: 'Pixel-accurate masks',
                class_scope_label: 'Prompt-guided segmentation',
                supports_text_prompt: true,
                plain_english: 'This is the precise tool for pro users.',
              },
            },
          ],
        },
      })
    })

    await page.route('**/api/censor/detect', async (route) => {
      detectPayload = JSON.parse(route.request().postData() || '{}')
      await route.fulfill({
        json: {
          status: 'ok',
          image_id: detectPayload.image_id,
          model_type: detectPayload.model_type,
          detections: [
            {
              box: [8, 8, 28, 28],
              class: 'breasts',
              confidence: 0.92,
            },
          ],
          combined_mask: null,
          geometry_mode: 'box',
          warnings: [],
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-toggle-select').click()
    await page.locator('#gallery-grid .gallery-item').first().click()
    await expect(page.locator('#btn-send-to-censor')).toBeEnabled()
    await page.locator('#btn-send-to-censor').click()

    await expect(page.locator('#view-censor.active')).toBeVisible()
    await expect(page.locator('#censor-queue-list .queue-thumb-v2')).toHaveCount(1, { timeout: 15000 })

    await page.locator('#btn-open-detect-modal').click()
    await expect(page.locator('#detect-modal.visible')).toBeVisible()

    await page.selectOption('#censor-model-type', 'legacy')
    // Open the advanced model picker details section first
    const advPickerDetails = page.locator('#detect-modal details').nth(1)
    await advPickerDetails.click()
    const advancedModelsToggle = page.locator('label.checkbox-label', {
      has: page.locator('#censor-show-advanced-models'),
    })
    await advancedModelsToggle.scrollIntoViewIfNeeded()
    await advancedModelsToggle.click()
    await expect(page.locator('#censor-show-advanced-models')).toBeChecked()

    let advancedLegacyModelPath = ''
    await expect.poll(async () => {
      const optionValues = await page.locator('#censor-model-file option').evaluateAll((options) =>
        options
          .map((option) => ({
            text: option.textContent || '',
            value: (option as HTMLOptionElement).value || '',
          }))
          .filter((option) => /yolo26s-seg\.onnx/i.test(option.text) || /yolo26s-seg\.onnx/i.test(option.value))
          .map((option) => option.value)
      )
      advancedLegacyModelPath = optionValues[0] || ''
      return advancedLegacyModelPath
    }, { timeout: 10000 }).not.toBe('')

    await page.selectOption('#censor-model-file', advancedLegacyModelPath)
    await expect(page.locator('.target-region-check').first()).toBeEnabled()

    await page.locator('#btn-auto-detect-current-modal').click()

    await expect.poll(() => detectPayload, { timeout: 10000 }).not.toBeNull()
    expect(detectPayload.model_type).toBe('both')
    expect(detectPayload.model_path).toBe('C:/models/wenaka_yolov8s-seg.onnx')
    expect(detectPayload.target_classes).toEqual(['breasts', 'pussy', 'dick', 'penis', 'anus', 'buttocks'])
    await expect(page.locator('#toast-container')).toContainText(/switch(ed)? back to both mode|自动切回.*两者一起/i)
  })

  test('quick auto censor mixed geometry should affect only matched regions instead of the whole image', async ({ page }) => {
    let detectPayload: any = null
    const fulfillImage = async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'image/svg+xml',
        body: MOCK_IMAGE_SVG,
      })
    }

    await page.route('**/api/image-thumbnail/*', fulfillImage)
    await page.route('**/api/image-file/*', fulfillImage)
    await page.route('**/api/images/export-data', async (route) => {
      await route.fulfill({
        json: {
          images: [
            { id: 11, filename: 'censor-mixed-geometry.png', prompt: 'mixed geometry smoke', tags: [] },
          ],
          missing_ids: [],
        },
      })
    })

    await page.route('**/api/censor/models', async (route) => {
      await route.fulfill({
        json: {
          status: 'ok',
          recommended_backend: 'both',
          models: [
            {
              id: 'legacy',
              name: 'Legacy YOLO',
              available: true,
              recommended: true,
              default_model_path: 'C:/models/wenaka_yolov8s-seg.onnx',
              files: [
                {
                  name: 'wenaka_yolov8s-seg.onnx',
                  path: 'C:/models/wenaka_yolov8s-seg.onnx',
                  profile: 'privacy-censor',
                  profile_label: 'Privacy-part detector',
                  recommended_for_censor: true,
                  capabilities: {
                    supports_text_prompt: false,
                  },
                },
              ],
              privacy_model_count: 1,
              general_model_count: 0,
            },
            {
              id: 'nudenet',
              name: 'NudeNet v3',
              available: true,
              recommended: true,
              capabilities: {
                supports_text_prompt: false,
              },
            },
            {
              id: 'sam3',
              name: 'SAM 3',
              available: false,
              recommended: false,
              capabilities: {
                supports_text_prompt: true,
              },
            },
          ],
        },
      })
    })

    await page.route('**/api/censor/detect', async (route) => {
      detectPayload = JSON.parse(route.request().postData() || '{}')
      await route.fulfill({
        json: {
          status: 'ok',
          image_id: detectPayload.image_id,
          model_type: detectPayload.model_type,
          detections: [
            {
              box: [8, 8, 28, 28],
              polygon: [[8, 8], [28, 8], [28, 28], [8, 28]],
              class: 'breasts',
              confidence: 0.95,
            },
            {
              box: [40, 40, 56, 56],
              class: 'anus',
              confidence: 0.88,
            },
          ],
          combined_mask: MIXED_MASK_DATA_URL,
          geometry_mode: 'mixed',
          warnings: [],
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await openView(page, 'censor')
    await page.evaluate(() => {
      if (typeof (window as Window & { initCensorEdit?: any }).initCensorEdit === 'function') {
        ;(window as Window & { initCensorEdit?: any }).initCensorEdit()
      }
    })
    await expect.poll(() => page.evaluate(() => {
      return typeof (window as Window & { App?: any }).App?.addToCensorQueue === 'function'
    }), { timeout: 10000 }).toBeTruthy()
    await page.evaluate(async () => {
      await (window as Window & { App?: any }).App.addToCensorQueue([11])
    })

    await expect(page.locator('#view-censor.active')).toBeVisible()
    await expect(page.locator('#censor-queue-list .queue-thumb-v2')).toHaveCount(1, { timeout: 15000 })
    await page.selectOption('#censor-style', 'black_bar')

    const readCensorPixels = async () => page.evaluate(async () => {
      const activeItem = (window as any).__CENSOR_STATE__?.queue?.[0]
      const src = activeItem?.currentDataUrl || activeItem?.originalUrl
      if (!src) return null

      const img = await new Promise<HTMLImageElement>((resolve, reject) => {
        const image = new Image()
        image.onload = () => resolve(image)
        image.onerror = () => reject(new Error('Failed to load censor preview image'))
        image.src = src
      })

      const canvas = document.createElement('canvas')
      canvas.width = img.naturalWidth || img.width
      canvas.height = img.naturalHeight || img.height
      const ctx = canvas.getContext('2d')
      if (!ctx) return null
      ctx.drawImage(img, 0, 0)
      const sample = (x: number, y: number) => Array.from(ctx.getImageData(x, y, 1, 1).data)
      return {
        unaffected: sample(2, 2),
        maskRegion: sample(12, 12),
        boxRegion: sample(48, 48),
      }
    })

    const before = await readCensorPixels()

    await page.locator('#btn-open-detect-modal').click()
    await expect(page.locator('#detect-modal.visible')).toBeVisible()
    await page.locator('#btn-auto-detect-current-modal').click()
    await expect.poll(() => detectPayload, { timeout: 10000 }).not.toBeNull()
    await expect(page.locator('#toast-container')).toContainText(/mixed auto-censor|混合自动打码|auto-censor mask|基于框的自动打码/i)

    await expect.poll(async () => {
      const queuePayload = await page.evaluate(() => {
        const queue = (window as any).__CENSOR_STATE__?.queue || null
        return queue && queue[0] ? Boolean(queue[0].currentDataUrl) : null
      })
      return queuePayload
    }, { timeout: 10000 }).toBeTruthy()

    const after = await readCensorPixels()

    expect(before).not.toBeNull()
    expect(after).not.toBeNull()
    expect(after!.unaffected).toEqual(before!.unaffected)
    expect(after!.maskRegion).not.toEqual(before!.maskRegion)
    expect(after!.boxRegion).not.toEqual(before!.boxRegion)
  })

  test('should keep the filter modal readable across supported desktop widths', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('networkidle')

    await page.locator('#btn-open-filters').click()
    await expect(page.locator('#filter-modal.visible')).toBeVisible()

    const viewports = [
      { width: 1366, height: 768 },
      { width: 1920, height: 1080 },
      { width: 2560, height: 1440 },
    ]

    for (const viewport of viewports) {
      await page.setViewportSize({ width: viewport.width, height: viewport.height })
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))))

      const layout = await getFilterModalLayout(page)
      const gridMetrics = await page.evaluate(() => {
        const grid = document.querySelector('#filter-modal .filter-modal-grid')
        if (!grid) return null

        return {
          clientHeight: grid.clientHeight,
          scrollHeight: grid.scrollHeight,
        }
      })
      expect(layout).not.toBeNull()
      if (!layout) continue

      expect(gridMetrics).not.toBeNull()
      if (gridMetrics) {
        expect(gridMetrics.clientHeight).toBeGreaterThan(0)
        expect(gridMetrics.scrollHeight).toBeGreaterThanOrEqual(gridMetrics.clientHeight)
      }

      expect(layout.modal.left).toBeGreaterThanOrEqual(0)
      expect(layout.modal.right).toBeLessThanOrEqual(viewport.width)
      expect(layout.modal.top).toBeGreaterThanOrEqual(0)
      expect(layout.modal.bottom).toBeLessThanOrEqual(viewport.height)
      expect(layout.primary.right).toBeLessThanOrEqual(layout.secondary.left + 8)
      expect(layout.actions.left).toBeGreaterThanOrEqual(layout.modal.left)
      expect(layout.actions.right).toBeLessThanOrEqual(layout.modal.right)
      expect(layout.actions.bottom).toBeLessThanOrEqual(viewport.height)
      await expect(page.locator('#btn-apply-modal-filters')).toBeVisible()
      await expect(page.locator('#btn-reset-filters')).toBeVisible()
    }
  })

  test('top navigation and generator rail should stay reachable in English and Chinese', async ({ page }) => {
    await page.route('**/api/stats', async (route) => {
      await route.fulfill({
        json: {
          total_images: 50008,
          app_version: '3.4.3',
          generators: [
            { generator: 'nai', count: 419 },
            { generator: 'comfyui', count: 864 },
            { generator: 'forge', count: 482 },
            { generator: 'webui', count: 380 },
            { generator: 'unknown', count: 47838 },
            { generator: 'fooocus', count: 10 },
            { generator: 'invokeai', count: 6 },
            { generator: 'gemini', count: 5 },
          ],
          checkpoints: [],
          loras: [],
          top_tags: [],
        },
      })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')

    const cases = [
      { width: 1366, height: 900, lang: 'en' },
      { width: 1366, height: 900, lang: 'zh-CN' },
      { width: 1920, height: 1080, lang: 'en' },
      { width: 1920, height: 1080, lang: 'zh-CN' },
    ]

    for (const item of cases) {
      await page.setViewportSize({ width: item.width, height: item.height })
      await page.evaluate((lang) => {
        ;(window as Window & { I18n?: { setLang?: (lang: string) => void } }).I18n?.setLang?.(lang)
      }, item.lang)
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))))

      const fit = await page.evaluate(() => {
        const nav = document.querySelector('.nav-bar')
        const tabs = document.querySelector('.nav-tabs')
        const generator = document.getElementById('generator-tabs')
        const scroller = document.getElementById('generator-tabs-scroll')
        const allTab = document.querySelector('.gen-tab[data-gen="all"]')
        const more = document.getElementById('nav-tools-toggle')
        const prompt = document.getElementById('nav-tab-promptlab')
        const artist = document.getElementById('nav-tab-artist')
        if (!nav || !tabs || !generator || !scroller || !allTab || !more || !prompt || !artist) return null
        const rect = (element: Element) => {
          const box = element.getBoundingClientRect()
          return { left: box.left, right: box.right, top: box.top, bottom: box.bottom, width: box.width, height: box.height }
        }
        const navRect = rect(nav)
        const visibleNavItems = Array.from(tabs.querySelectorAll<HTMLElement>('.nav-tab, .nav-tools-toggle'))
          .filter((element) => {
            const box = element.getBoundingClientRect()
            const style = window.getComputedStyle(element)
            return box.width > 0 && box.height > 0 && style.display !== 'none' && style.visibility !== 'hidden'
          })
          .map((element) => ({ id: element.id, box: rect(element) }))
        return {
          viewport: window.innerWidth,
          classes: nav.className,
          nav: navRect,
          items: visibleNavItems,
          generator: rect(generator),
          scroller: rect(scroller),
          allTab: rect(allTab),
          scrollWidth: scroller.scrollWidth,
          clientWidth: scroller.clientWidth,
          moreVisible: rect(more).width > 0,
          promptVisible: rect(prompt).width > 0,
          artistVisible: rect(artist).width > 0,
        }
      })

      expect(fit).not.toBeNull()
      if (!fit) continue

      for (const itemRect of fit.items) {
        expect(itemRect.box.left).toBeGreaterThanOrEqual(fit.nav.left - 1)
        expect(itemRect.box.right).toBeLessThanOrEqual(fit.nav.right + 1)
      }
      expect(fit.generator.left).toBeGreaterThanOrEqual(0)
      expect(fit.generator.right).toBeLessThanOrEqual(fit.viewport + 1)
      expect(fit.allTab.left).toBeGreaterThanOrEqual(fit.generator.left - 1)
      expect(fit.allTab.right).toBeLessThanOrEqual(fit.generator.right + 1)
      expect(fit.clientWidth).toBeGreaterThan(0)
      expect(fit.scrollWidth).toBeGreaterThanOrEqual(fit.clientWidth)

      // More ▾ is always visible (Dup Cleaner / tucked tools live there).
      // Prompt Helper and Style Finder are tucked by default.
      expect(fit.moreVisible).toBeTruthy()
      expect(fit.promptVisible).toBeFalsy()
      expect(fit.artistVisible).toBeFalsy()

      await page.evaluate(() => {
        const scroller = document.getElementById('generator-tabs-scroll')
        if (scroller) scroller.scrollLeft = scroller.scrollWidth
      })
      await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(resolve)))
      const othersFit = await page.evaluate(() => {
        const scroller = document.getElementById('generator-tabs-scroll')
        const others = document.querySelector('.gen-tab[data-gen="others"]')
        if (!scroller || !others) return null
        const s = scroller.getBoundingClientRect()
        const o = others.getBoundingClientRect()
        return { scrollerLeft: s.left, scrollerRight: s.right, othersLeft: o.left, othersRight: o.right }
      })
      expect(othersFit).not.toBeNull()
      if (othersFit) {
        expect(othersFit.othersLeft).toBeGreaterThanOrEqual(othersFit.scrollerLeft - 1)
        expect(othersFit.othersRight).toBeLessThanOrEqual(othersFit.scrollerRight + 1)
      }
    }
  })

  test('AI Auto Tagging modal should open Smart Tag directly', async ({ page }) => {
    await mockGalleryImages(page, [{ id: 701, filename: 'smart-tag-entry.png' }])
    await mockTaggerCatalog(page)
    await page.route('**/api/vlm/settings', async (route) => {
      await route.fulfill({ json: { endpoint: 'https://example.invalid/v1', use_vertex: false } })
    })
    await page.route('**/api/smart-tag/progress**', async (route) => {
      await route.fulfill({ json: { status: 'idle' } })
    })

    await page.goto('/')
    await page.waitForLoadState('networkidle')
    await page.locator('#btn-toggle-select').click()
    await page.locator('#gallery-grid .gallery-item[data-id="701"]').click()
    await expect(page.locator('#selection-actions')).toBeVisible()
    await page.locator('#btn-tag').click()
    await expect(page.locator('#tag-modal.visible')).toBeVisible()
    await expect(page.locator('#btn-tag-modal-smart-tag-go')).toBeVisible()

    await page.locator('#btn-tag-modal-smart-tag-go').click()
    await expect(page.locator('#tag-modal.visible')).toHaveCount(0)
    await expect(page.locator('#smart-tag-modal.visible')).toBeVisible()
    await expect(page.locator('#smart-tag-image-count')).toHaveText('1')
  })

  test('API health check - images endpoint', async ({ request }) => {
    const response = await request.get('/api/images?limit=1')
    expect(response.ok()).toBeTruthy()

    const data = await response.json()
    expect(data).toHaveProperty('images')
    expect(Array.isArray(data.images)).toBeTruthy()
  })

  test('API health check - stats endpoint', async ({ request }) => {
    const response = await request.get('/api/stats')
    expect(response.ok()).toBeTruthy()

    const data = await response.json()
    expect(data).toHaveProperty('total_images')
  })

  test('API health check - generators endpoint', async ({ request }) => {
    const response = await request.get('/api/generators')
    expect(response.ok()).toBeTruthy()

    const data = await response.json()
    expect(data).toHaveProperty('generators')
    expect(Array.isArray(data.generators)).toBeTruthy()
  })

  test('API health check - tags endpoint', async ({ request }) => {
    const response = await request.get('/api/tags?limit=10')
    expect(response.ok()).toBeTruthy()

    const data = await response.json()
    expect(data).toHaveProperty('tags')
    expect(Array.isArray(data.tags)).toBeTruthy()
  })

  test('should handle invalid API routes gracefully', async ({ request }) => {
    const response = await request.get('/api/nonexistent-endpoint')
    expect(response.status()).toBe(404)
  })

  test('should have OpenAPI documentation available', async ({ request }) => {
    const response = await request.get('/docs')
    expect(response.ok()).toBeTruthy()
  })
})

test.describe('Error Handling', () => {
  test('should return validation error for an empty scan path', async ({ request }) => {
    const response = await request.post('/api/scan', {
      data: { folder_path: '', recursive: true },
    })

    expect(response.status()).toBe(400)

    const data = await response.json()
    expect(data).toMatchObject({
      error: 'Path cannot be empty',
      type: 'HTTPException',
    })
  })

  test('should handle 404 for missing image', async ({ request }) => {
    const response = await request.get('/api/images/999999999')
    expect(response.status()).toBe(404)
  })

  test('should handle rate limiting gracefully', async ({ request }) => {
    // Make many rapid requests
    const promises = []
    for (let i = 0; i < 10; i++) {
      promises.push(request.get('/api/images?limit=1'))
    }

    const responses = await Promise.all(promises)

    // All should succeed (within rate limit)
    const successCount = responses.filter((r) => r.ok()).length
    expect(successCount).toBe(10)
  })
})
