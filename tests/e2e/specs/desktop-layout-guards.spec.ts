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

// ---------------------------------------------------------------------------
// A control column that scrolls internally must not swallow its primary action.
// ---------------------------------------------------------------------------

/**
 * The artist controls column is a fixed-height internal scroller at laptop
 * heights, and only its LAST section was pinned. Adding one more section above
 * the run buttons therefore pushed `#btn-identify-all` below the scroll fold
 * with no symptom other than the button being gone — the panel looked fine and
 * the destructive Clear button, being last, stayed put.
 *
 * The distinction that makes this testable: where the column is an internal
 * scroller, the page around it has almost nothing left to scroll (72px at
 * 1366x768), so a control below the column's own fold is effectively gone. At
 * taller viewports the column is not a scroller at all and the whole document
 * scrolls, so starting below the fold there is ordinary page flow, not a
 * defect. The two tests below assert exactly those two different promises.
 *
 * Geometry alone cannot clear a pinned-footer fix either: the footer can cover
 * the section above it instead. So both tests also ask the renderer whose
 * pixels are actually at the control's centre.
 */
/**
 * The buttons that start the work, and so the ones the pinned footer exists to
 * keep on screen. The maintenance controls below them may sit just past the
 * page fold at the tightest height — the page itself still has scroll room
 * there, which is the difference between "one flick away" and the internal
 * fold's "gone" — so their promise is the weaker, scroll-reachable one below.
 */
const ARTIST_RUN_ACTIONS = ['#btn-identify-all', '#btn-identify-selected'] as const
const ARTIST_COLUMN_CONTROLS = ['#btn-identify-all', '#btn-identify-selected', '#btn-clear-artist-data'] as const

/**
 * The first-use card is quiet by default (Help unhides it). Layout still
 * has to survive the tallest case, so 'shown' forces the card open without
 * the Help overlay covering the column.
 */
async function openArtistView(page: Page, guide: 'shown' | 'dismissed'): Promise<void> {
  await page.evaluate((seen) => {
    localStorage.setItem('artist-guide-seen', seen)
    const app = (window as any).App
    app.switchView('artist')
    ;(window as any).ArtistIdent?.refreshFirstUseCard?.()
    const card = document.getElementById('artist-start-card') as HTMLElement | null
    if (card) card.hidden = seen === 'true'
  }, guide === 'dismissed' ? 'true' : 'false')

  await page.waitForFunction((wantShown) => {
    const card = document.getElementById('artist-start-card') as HTMLElement | null
    return !!card && card.hidden !== wantShown
  }, guide === 'shown')
  // The stats fetch that follows the switch can still add the summary row.
  await page.waitForTimeout(400)
}

/** Internal scroll room in the control column, in px. */
function artistColumnScrollRoom(page: Page): Promise<number> {
  return page.evaluate(() => {
    const column = document.querySelector('.artist-controls')!
    return column.scrollHeight - column.clientHeight
  })
}

/**
 * Who actually paints the control's centre. A descendant (its label span) or an
 * ancestor (a disabled button sets `pointer-events: none`, so the hit lands on
 * its own section) both mean the control's own pixels are on screen. Anything
 * else is a foreign element covering it.
 */
async function coveredControls(page: Page, selectors: readonly string[]): Promise<string[]> {
  return page.evaluate((list) => list.flatMap((selector) => {
    const el = document.querySelector(selector) as HTMLElement | null
    if (!el) return [`${selector} is missing`]
    const box = el.getBoundingClientRect()
    if (box.width === 0 || box.height === 0) return [`${selector} has no box`]
    if (box.left < 0 || box.right > window.innerWidth) {
      return [`${selector} overflows horizontally (left ${Math.round(box.left)}, right ${Math.round(box.right)})`]
    }
    const owner = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)
    if (!owner) return [`${selector} centre hits nothing`]
    if (owner === el || el.contains(owner) || owner.contains(el)) return []
    const cls = String((owner as HTMLElement).className || '').trim().split(/\s+/).filter(Boolean).join('.')
    return [`${selector} centre is covered by ${owner.tagName.toLowerCase()}${owner.id ? '#' + owner.id : ''}${cls ? '.' + cls : ''}`]
  }), selectors as string[])
}

test('the artist run buttons are on screen unscrolled while the column scrolls internally', async ({ page }) => {
  await gotoApp(page)
  await page.setViewportSize({ width: 1366, height: 768 })

  // Guard the guard: if the column ever stops scrolling internally at laptop
  // height this test would pass by asserting nothing, so prove the condition.
  await openArtistView(page, 'shown')
  expect(await artistColumnScrollRoom(page),
    'the artist column is expected to scroll internally at 1366x768').toBeGreaterThan(0)

  for (const guide of ['shown', 'dismissed'] as const) {
    await openArtistView(page, guide)

    const geometry = await page.evaluate((selectors) => {
      const height = window.innerHeight
      return selectors.map((selector) => {
        const box = document.querySelector(selector)!.getBoundingClientRect()
        return {
          selector,
          offScreenBy: Math.round(Math.max(0, -box.top) + Math.max(0, box.bottom - height)),
        }
      }).filter((entry) => entry.offScreenBy > 0)
    }, ARTIST_RUN_ACTIONS as unknown as string[])

    expect(geometry, `artist run buttons off screen at 1366x768 with the first-use card ${guide}`).toEqual([])
    expect(await coveredControls(page, ARTIST_RUN_ACTIONS),
      `artist run buttons covered at 1366x768 with the first-use card ${guide}`).toEqual([])
  }
})

test('every artist control column button can be brought into view uncovered', async ({ page }) => {
  await gotoApp(page)

  for (const viewport of DESKTOP_VIEWPORTS) {
    await page.setViewportSize(viewport)
    for (const guide of ['shown', 'dismissed'] as const) {
      await openArtistView(page, guide)

      for (const selector of ARTIST_COLUMN_CONTROLS) {
        const label = `${selector} at ${viewport.width}x${viewport.height}, first-use card ${guide}`
        await page.locator(selector).scrollIntoViewIfNeeded()
        const revealed = await page.evaluate((sel) => {
          const box = document.querySelector(sel)!.getBoundingClientRect()
          return { top: Math.round(box.top), bottom: Math.round(box.bottom), height: window.innerHeight }
        }, selector)
        expect(revealed.top, `${label}: still above the viewport after scrolling`).toBeGreaterThanOrEqual(0)
        expect(revealed.bottom, `${label}: still below the fold after scrolling`)
          .toBeLessThanOrEqual(revealed.height)
        expect(await coveredControls(page, [selector]), `${label}: covered after scrolling`).toEqual([])
      }
    }
  }
})
