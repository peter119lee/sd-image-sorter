import { expect, test, type Page } from '../fixtures/click-ledger'

/**
 * Recovery paths for jobs that get stranded in `cancelling`.
 *
 * `cancel` publishes `cancelling` and leaves the terminal write to the worker.
 * A worker that dies first strands the status there forever: the poller never
 * reaches a terminal state, the progress panel never collapses, and every later
 * run of that feature is refused with HTTP 409 until the app is restarted.
 * Four endpoints exist to clear exactly that state (`/api/move/reset`,
 * `/api/batch-move/reset`, `/api/images/delete-selected/reset`,
 * `/api/images/remove-selected/reset`) and until this slice nothing in the UI
 * called any of them.
 *
 * The contract each button has to honour:
 *   - it is NOT offered while the cancel is still plausibly in flight (a reset
 *     raced against a live worker is what the 409 exists to prevent);
 *   - a 200 leaves the feature usable again — panel collapsed, user told they
 *     can start over;
 *   - a 409 is NOT a failure. It means the refusal was correct, so it reads as
 *     "still running", not as an error.
 *
 * Also pinned here: Auto-Separate's `recent_errors` panel goes through the
 * shared `formatUserError` mapping like every other error surface, and a real
 * normalized backend cause still arrives intact through that filter rather than
 * being replaced by its canned fallback sentence.
 */

test.describe.configure({ mode: 'serial' })

type RecoveryWin = typeof window & {
  App: any
  _switchSortingSub: (sub: string) => void
  showAutosepMoveProgress: (total: number) => void
  pollAutosepMoveProgress: (total: number, destination: string) => Promise<void>
  hideAutosepMoveProgress: () => void
  pollMoveProgressUntilDone: () => Promise<unknown>
  undoLastAction: () => Promise<void>
  __clockOffsetMs: number
}

/**
 * Boot the app with a controllable `Date.now`. The stall detector measures how
 * long a job has been sitting in `cancelling`; driving that with a fake clock
 * keeps the test honest about the real threshold instead of shrinking it.
 */
async function gotoWithControllableClock(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const realNow = Date.now.bind(Date)
    ;(window as any).__clockOffsetMs = 0
    Date.now = () => realNow() + ((window as any).__clockOffsetMs || 0)
  })
  await page.goto('/')
  await page.waitForLoadState('domcontentloaded')
  await page.waitForFunction(() => {
    const w = window as RecoveryWin
    return document.documentElement.dataset.appReady === '1'
      && typeof w.App?.API?.post === 'function'
      && typeof w.showAutosepMoveProgress === 'function'
      && typeof w.pollAutosepMoveProgress === 'function'
      && typeof w.pollMoveProgressUntilDone === 'function'
      && typeof w._switchSortingSub === 'function'
  })
}

/** The Auto-Separate panel lives inside the Sorting view; open it for real. */
async function openAutoSeparate(page: Page): Promise<void> {
  await page.evaluate(() => {
    const w = window as RecoveryWin
    w.App.switchView('sorting')
    w._switchSortingSub('autosep')
  })
  await expect(page.locator('#view-autosep')).toBeVisible()
}

async function advanceClock(page: Page, ms: number): Promise<void> {
  await page.evaluate((delta) => {
    ;(window as RecoveryWin).__clockOffsetMs += delta
  }, ms)
}

/** Text of every toast currently on screen, paired with its severity class. */
async function readToasts(page: Page): Promise<Array<{ text: string, kind: string }>> {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll('#toast-container .toast')).map((el) => ({
      text: el.querySelector('.toast-message')?.textContent || '',
      kind: Array.from(el.classList).filter((c) => c !== 'toast').join(' '),
    })),
  )
}

async function clearToasts(page: Page): Promise<void> {
  await page.evaluate(() => {
    document.querySelectorAll('#toast-container .toast').forEach((el) => el.remove())
  })
}

const CANCELLING_BATCH_MOVE = {
  status: 'cancelling',
  step: 'cancelling',
  current: 4,
  total: 10,
  moved: 4,
  errors: 0,
  operation: 'move',
  message: 'Cancelling...',
}

const CANCELLING_MOVE = {
  status: 'cancelling',
  step: 'cancelling',
  current: 2,
  total: 6,
  operation: 'move',
  message: 'Cancelling...',
}

// ---------------------------------------------------------------------------
// Auto-Separate — the most common dead end, because its own Cancel button is
// what produces the `cancelling` state.
// ---------------------------------------------------------------------------

test('Auto-Separate offers no reset while the cancel is still plausibly in flight', async ({ page }) => {
  await gotoWithControllableClock(page)
  await page.route('**/api/batch-move/progress', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CANCELLING_BATCH_MOVE) }))
  await openAutoSeparate(page)

  await page.evaluate(() => {
    const w = window as RecoveryWin
    w.showAutosepMoveProgress(10)
    void w.pollAutosepMoveProgress(10, '')
  })

  const reset = page.locator('#btn-reset-autosep-move')
  await expect(page.locator('#autosep-move-progress')).toBeVisible()
  // Several real polls go by; the button must still be absent.
  await page.waitForTimeout(1200)
  await expect(reset).toBeHidden()

  await page.evaluate(() => (window as RecoveryWin).hideAutosepMoveProgress())
})

test('a batch move stalled in cancelling offers a reset, and a 200 leaves Auto-Separate usable again', async ({ page }) => {
  await gotoWithControllableClock(page)
  let resetCalls = 0
  await page.route('**/api/batch-move/progress', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CANCELLING_BATCH_MOVE) }))
  await page.route('**/api/batch-move/reset', (route) => {
    resetCalls += 1
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'reset', message: 'Batch move progress reset to idle' }),
    })
  })
  await openAutoSeparate(page)

  await page.evaluate(() => {
    const w = window as RecoveryWin
    w.showAutosepMoveProgress(10)
    void w.pollAutosepMoveProgress(10, '')
  })
  await expect(page.locator('#autosep-move-progress')).toBeVisible()

  // The watcher stamps the clock on the first `cancelling` poll, so let one
  // land before jumping past the stall window.
  await page.waitForTimeout(700)
  await advanceClock(page, 30000)
  const reset = page.locator('#btn-reset-autosep-move')
  await expect(reset).toBeVisible()
  await expect(reset).toHaveAccessibleName(/reset|重置|清除/i)

  await clearToasts(page)
  await reset.click()

  // The panel collapses back to idle: the feature is usable again without a restart.
  await expect(page.locator('#autosep-move-progress')).not.toHaveClass(/visible/)
  expect(resetCalls).toBe(1)

  const toasts = await readToasts(page)
  expect(toasts.length).toBeGreaterThan(0)
  expect(toasts.some((t) => /again/i.test(t.text) || /重新/.test(t.text))).toBe(true)
  expect(toasts.every((t) => !t.kind.includes('error'))).toBe(true)
})

test('a 409 from the batch-move reset reports the job as still running, not as a failure', async ({ page }) => {
  await gotoWithControllableClock(page)
  await page.route('**/api/batch-move/progress', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CANCELLING_BATCH_MOVE) }))
  await page.route('**/api/batch-move/reset', (route) =>
    route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Cannot reset batch move while it is still running' }),
    }))
  await openAutoSeparate(page)

  await page.evaluate(() => {
    const w = window as RecoveryWin
    w.showAutosepMoveProgress(10)
    void w.pollAutosepMoveProgress(10, '')
  })
  await expect(page.locator('#autosep-move-progress')).toBeVisible()
  await page.waitForTimeout(700)
  await advanceClock(page, 30000)
  const reset = page.locator('#btn-reset-autosep-move')
  await expect(reset).toBeVisible()

  await clearToasts(page)
  await reset.click()

  const toasts = await page.waitForFunction(() => {
    const nodes = Array.from(document.querySelectorAll('#toast-container .toast'))
    if (!nodes.length) return null
    return nodes.map((el) => ({
      text: el.querySelector('.toast-message')?.textContent || '',
      kind: Array.from(el.classList).filter((c) => c !== 'toast').join(' '),
    }))
  }).then((handle) => handle.jsonValue() as Promise<Array<{ text: string, kind: string }>>)

  // A refused reset is the endpoint working correctly, so it must not read as a failure.
  expect(toasts.every((t) => !t.kind.includes('error'))).toBe(true)
  expect(toasts.some((t) => /still running|仍在运行|还在运行/i.test(t.text))).toBe(true)
  // The job really is running, so the panel stays up.
  await expect(page.locator('#autosep-move-progress')).toBeVisible()

  await page.evaluate(() => (window as RecoveryWin).hideAutosepMoveProgress())
})

// ---------------------------------------------------------------------------
// Gallery move — same contract on the floating background progress bar.
// ---------------------------------------------------------------------------

test('the gallery move bar reveals its reset only after a stall and clears on 200', async ({ page }) => {
  await gotoWithControllableClock(page)
  let resetCalls = 0
  await page.route('**/api/move/progress', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(CANCELLING_MOVE) }))
  await page.route('**/api/move/reset', (route) => {
    resetCalls += 1
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'reset', message: 'Move progress reset to idle' }),
    })
  })

  await page.evaluate(() => {
    void (window as RecoveryWin).pollMoveProgressUntilDone()
  })
  await expect(page.locator('#bg-move-progress')).toBeVisible()

  const reset = page.locator('#bg-move-reset')
  await page.waitForTimeout(900)
  await expect(reset).toBeHidden()

  await advanceClock(page, 30000)
  await expect(reset).toBeVisible()

  await clearToasts(page)
  await reset.click()

  await expect(page.locator('#bg-move-progress')).toBeHidden()
  expect(resetCalls).toBe(1)
  const toasts = await readToasts(page)
  expect(toasts.every((t) => !t.kind.includes('error'))).toBe(true)
})

test('the gallery delete and remove bars carry the same reset control', async ({ page }) => {
  await gotoWithControllableClock(page)
  // Both bars are static markup, so the control's presence is checkable without
  // driving a real delete: the defect this guards against is the button being
  // dropped from one bar while its sibling keeps it.
  for (const [bar, button] of [
    ['#bg-delete-progress', '#bg-delete-reset'],
    ['#bg-remove-progress', '#bg-remove-reset'],
  ]) {
    await expect(page.locator(`${bar} ${button}`)).toHaveCount(1)
    await expect(page.locator(button)).toBeHidden()
  }
})

// ---------------------------------------------------------------------------
// Manual Sort undo — the backend's reason has to reach the user.
// ---------------------------------------------------------------------------

test('a refused undo shows the backend reason instead of a bare "Failed to undo"', async ({ page }) => {
  await gotoWithControllableClock(page)
  await page.route('**/api/sort/action**', (route) =>
    route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({
        detail: "Could not undo last action: another file named '00042.png' is already in 'library'. Move or rename it, then undo again.",
      }),
    }))

  await clearToasts(page)
  await page.evaluate(() => (window as RecoveryWin).undoLastAction())

  const toasts = await readToasts(page)
  const joined = toasts.map((t) => t.text).join(' | ')
  expect(joined).toContain("another file named '00042.png' is already in 'library'")
  expect(joined).toContain('Move or rename it, then undo again.')
})

// ---------------------------------------------------------------------------
// Auto-Separate error panel — shared localization, without swallowing the cause.
// ---------------------------------------------------------------------------

test('Auto-Separate per-file errors go through the shared formatter and keep a real normalized cause', async ({ page }) => {
  await gotoWithControllableClock(page)
  await openAutoSeparate(page)
  await page.evaluate(() => {
    const w = window as RecoveryWin
    w.showAutosepMoveProgress(2)
    ;(w as any).updateAutosepMoveProgress({
      status: 'running',
      current: 2,
      total: 2,
      moved: 0,
      errors: 2,
      operation: 'move',
      recent_errors: [
        // Exactly the shape services/sorting/batch_move.py now emits: single
        // line, basename only, length-capped.
        { filename: 'IMG_0042.png', error: "Failed to move image: [Errno 13] Permission denied: 'IMG_0042.png'" },
        // A cause the shared map owns, so a raw render is distinguishable from
        // a formatted one.
        { filename: 'IMG_0043.png', error: 'Failed to move image: ENOSPC no space left on device' },
      ],
    }, 2)
  })

  const panel = page.locator('#autosep-move-errors')
  await expect(panel).toBeVisible()
  const text = (await panel.textContent()) || ''

  // The normalized cause survives the formatter's jargon/length filter intact.
  expect(text).toContain('IMG_0042.png')
  expect(text).toContain('Permission denied')
  expect(text).not.toContain('An unexpected error occurred')

  // ENOSPC proves the panel really is going through formatUserError now: raw
  // rendering would have printed the errno string instead of this sentence.
  expect(text).toContain('Not enough disk space')

  await page.evaluate(() => (window as RecoveryWin).hideAutosepMoveProgress())
})
