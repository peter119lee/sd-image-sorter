import { expect, test, type Page } from '../fixtures/click-ledger'

/**
 * Two whole-app layout guards, both for defect classes this project has now hit
 * more than once.
 *
 * 1. `[hidden]` that does not hide. The UA rule is `[hidden] { display: none }`
 *    at specificity 0-0-1, so any class or id rule that sets `display` beats it
 *    and `el.hidden = true` becomes a silent no-op. `#btn-metadata-reparse` and
 *    `.similar-embed-row` were each found this way, months apart, with the same
 *    symptom: a control the code believes it hid is still on screen.
 *
 * 2. A card that clips its own text instead of scrolling. `overflow` other than
 *    `visible` zeroes a grid item's automatic minimum size, so a height-squeezed
 *    grid row collapses below its content and `overflow: hidden` then cuts the
 *    card's own lines off — invisibly, with no scrollbar to hint at it.
 *
 * Desktop widths only (project rule): 1366x768, 1920x1080, 2560x1440.
 */

test.describe.configure({ mode: 'serial' })

const DESKTOP_VIEWPORTS = [
  { width: 1366, height: 768 },
  { width: 1920, height: 1080 },
  { width: 2560, height: 1440 },
] as const

async function gotoApp(page: Page): Promise<void> {
  await page.goto('/')
  await page.waitForLoadState('domcontentloaded')
  await page.waitForFunction(() => document.documentElement.dataset.appReady === '1')
}

/**
 * Every element carrying the `hidden` attribute, whose computed display is not
 * `none` — i.e. the attribute is being overridden by a CSS `display` rule.
 */
async function leakingHiddenElements(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll('[hidden]'))
      .filter((el) => getComputedStyle(el).display !== 'none')
      .map((el) => {
        const cls = typeof el.className === 'string' && el.className.trim()
          ? '.' + el.className.trim().split(/\s+/).join('.')
          : ''
        return `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}${cls} -> display:${getComputedStyle(el).display}`
      }),
  )
}

test('an element marked hidden is actually hidden, at every desktop width', async ({ page }) => {
  await gotoApp(page)
  for (const viewport of DESKTOP_VIEWPORTS) {
    await page.setViewportSize(viewport)
    // Give any width-conditional stylesheet a frame to apply.
    await page.waitForTimeout(120)
    const leaks = await leakingHiddenElements(page)
    expect(leaks, `[hidden] overridden by a CSS display rule at ${viewport.width}x${viewport.height}`).toEqual([])
  }
})

test('every view keeps its hidden elements hidden after the user navigates to it', async ({ page }) => {
  await gotoApp(page)
  await page.setViewportSize({ width: 1920, height: 1080 })
  const views = ['gallery', 'sorting', 'censor', 'dataset', 'similar', 'reader', 'promptlab', 'artist']
  for (const view of views) {
    await page.evaluate((name) => (window as any).App.switchView(name), view)
    await page.waitForTimeout(120)
    const leaks = await leakingHiddenElements(page)
    expect(leaks, `[hidden] overridden after switching to the ${view} view`).toEqual([])
  }
})

// ---------------------------------------------------------------------------
// Artist cards must never clip their own content.
// ---------------------------------------------------------------------------

const ARTIST_COUNTS: Record<string, number> = {
  sakimichan: 412, wlop: 388, as109: 301, ilya_kuvshinov: 254, kantoku: 233,
  rella: 190, mika_pikazo: 175, redjuice: 140, huke: 121, yoneyama_mai: 98,
}
const ARTIST_STATS = Object.fromEntries(
  Object.keys(ARTIST_COUNTS).map((name, i) => [name, {
    avg_confidence: 0.874 - i * 0.01,
    max_confidence: 0.991 - i * 0.005,
    count: ARTIST_COUNTS[name],
  }]),
)

test('artist cards show their confidence line in full at every desktop width', async ({ page }) => {
  await page.route('**/api/artists/stats', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        total_images: 6842,
        identified_images: 1204,
        confident_count: 1204,
        low_confidence_count: 0,
        undefined_count: 5320,
        artist_counts: ARTIST_COUNTS,
        artist_stats: ARTIST_STATS,
        low_confidence_artist_counts: {},
        confident_threshold: 0.2,
        vocabulary_size: 39261,
      }),
    }))
  await gotoApp(page)

  for (const viewport of DESKTOP_VIEWPORTS) {
    await page.setViewportSize(viewport)
    await page.evaluate(() => (window as any).App.switchView('artist'))
    await page.evaluate(() => (window as any).ArtistIdent?.loadStats?.())
    await page.waitForFunction(() => document.querySelectorAll('#artist-results-grid .artist-card').length > 0)
    await page.waitForTimeout(200)

    const clipped = await page.evaluate(() =>
      Array.from(document.querySelectorAll('#artist-results-grid .artist-card'))
        .map((card) => {
          const summary = card.querySelector('.artist-confidence-summary')
          if (!summary) return null
          const cardBox = card.getBoundingClientRect()
          const summaryBox = summary.getBoundingClientRect()
          const style = getComputedStyle(card)
          const contentBottom = cardBox.bottom - parseFloat(style.paddingBottom) - parseFloat(style.borderBottomWidth)
          return {
            name: card.querySelector('.artist-name')?.textContent || '',
            hiddenOverflowPx: +(card.scrollHeight - card.clientHeight).toFixed(1),
            summaryBelowContentBoxPx: +(summaryBox.bottom - contentBottom).toFixed(1),
          }
        })
        .filter((entry): entry is { name: string, hiddenOverflowPx: number, summaryBelowContentBoxPx: number } =>
          entry !== null && (entry.hiddenOverflowPx > 1 || entry.summaryBelowContentBoxPx > 1)))

    expect(clipped, `artist cards clipped at ${viewport.width}x${viewport.height}`).toEqual([])

    // The grid is the point of the view; a sliver is not a usable panel.
    const gridHeight = await page.evaluate(() =>
      document.getElementById('artist-results-grid')!.getBoundingClientRect().height)
    expect(gridHeight, `artist results grid height at ${viewport.width}x${viewport.height}`).toBeGreaterThan(120)

    // A card measuring itself as un-clipped proves nothing if the *row track*
    // it sits in collapsed: the card then keeps its full height and simply
    // paints over its neighbour, hiding the lower card's confidence line
    // behind the upper card's background. Ask the renderer who actually owns
    // each card's pixels.
    for (const mode of ['grid', 'list'] as const) {
      await page.evaluate((listMode) => {
        document.getElementById('artist-results-grid')!.classList.toggle('list-mode', listMode === 'list')
      }, mode)
      await page.waitForTimeout(120)

      const covered = await page.evaluate(() => {
        const grid = document.getElementById('artist-results-grid')!
        const gridBox = grid.getBoundingClientRect()
        return Array.from(grid.querySelectorAll('.artist-card'))
          .map((card) => {
            const box = card.getBoundingClientRect()
            const x = box.x + box.width / 2
            const y = box.y + box.height / 2
            // Only judge points the user can actually see: inside the viewport
            // and inside the grid's own scroll viewport.
            const visible = x > gridBox.left && x < gridBox.right
              && y > gridBox.top && y < gridBox.bottom
              && x > 0 && y > 0 && x < window.innerWidth && y < window.innerHeight
            if (!visible) return null
            const owner = document.elementFromPoint(x, y)?.closest('.artist-card')
            if (owner === card) return null
            return `${(card as HTMLElement).dataset.artist} is painted over by ${
              (owner as HTMLElement | null)?.dataset?.artist ?? 'a non-card element'}`
          })
          .filter((entry): entry is string => entry !== null)
      })

      expect(covered, `artist cards overlapping in ${mode} mode at ${viewport.width}x${viewport.height}`).toEqual([])
    }
    await page.evaluate(() => {
      document.getElementById('artist-results-grid')!.classList.remove('list-mode')
    })
  }
})
