import { expect, test, type Page } from '../fixtures/click-ledger'

/**
 * The AI runtime is a shared, exclusive resource: WD14 tagging, censor
 * detection, similarity embedding, aesthetic scoring and artist identification
 * all take the same lease, and the Gallery's AI Tag job takes it from a spawned
 * child process. Two things were invisible.
 *
 * `GET /api/system/ai-jobs` has published the live lease registry since v3.3.0
 * and nothing in the UI read it, so "the app is busy with something" was a
 * guess. And when a lease could not be won the backend answered 409 with a
 * structured blocker — scope, label, elapsed seconds — that the frontend threw
 * away, leaving a bare English sentence that `errors.js` could even rewrite
 * into the wrong advice: a *busy* WD14 refusal matched the "WD14 is not ready,
 * open Model setup" pattern, which is false and sends the user to the wrong
 * screen.
 *
 * The refusal must name the blocker and then say the one thing that is true
 * about reaching it, which differs by scope: a child-process holder is
 * cancelled from the Gallery's tagging bar, while an in-server job is cancelled
 * where it was started. Inventing either for the other case is the same class
 * of mistake as offering an action the data cannot support.
 */

declare global {
  interface Window {
    App: { API: any }
    AiBusy: any
    formatUserError: (error: unknown, context?: string) => string
  }
}

const IDLE = {
  active: 0, vram_active: 0, cpu_active: 0, cpu_pool_size: 2,
  vram_estimated_mb: 0, stuck_after_seconds: 900, jobs: [],
}

function busy(jobs: Array<Record<string, unknown>>) {
  return {
    ...IDLE,
    active: jobs.length,
    vram_active: jobs.filter((j) => j.tier === 'vram').length,
    cpu_active: jobs.filter((j) => j.tier === 'cpu').length,
    jobs,
  }
}

const WD14_JOB = {
  label: 'WD14 tagging', tier: 'vram', priority: 20,
  estimated_vram_mb: 1400, elapsed_seconds: 12.4, stuck: false,
}

async function openWith(page: Page, snapshot: Record<string, unknown>): Promise<void> {
  await page.addInitScript(() => {
    localStorage.setItem('sd-image-sorter-lang', 'en')
    localStorage.setItem('aurora-entry-skip', '1')
  })
  await page.route('**/api/system/ai-jobs*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(snapshot) }))
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page.locator('#view-gallery')).toBeVisible()
  await expect
    .poll(async () => page.evaluate(() => typeof window.AiBusy?.refresh === 'function'))
    .toBe(true)
}

/** Replace the snapshot and force one poll, instead of waiting out the timer. */
async function setSnapshot(page: Page, snapshot: Record<string, unknown>): Promise<void> {
  await page.unroute('**/api/system/ai-jobs*')
  await page.route('**/api/system/ai-jobs*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(snapshot) }))
  await page.evaluate(() => window.AiBusy.refresh())
}

const badge = '#nav-ai-busy'

test('an idle runtime shows no badge at all', async ({ page }) => {
  await openWith(page, IDLE)
  await page.evaluate(() => window.AiBusy.refresh())
  await expect(page.locator(badge)).toBeHidden()
})

test('a running job names itself and how long it has been going', async ({ page }) => {
  await openWith(page, busy([WD14_JOB]))
  await expect(page.locator(badge)).toBeVisible()
  const label = await page.locator('#nav-ai-busy-label').innerText()
  expect(label, 'the label carries the job and its elapsed time').toMatch(/WD14 tagging.*12s/)

  // And it leaves again once the runtime frees up, rather than sticking.
  await setSnapshot(page, IDLE)
  await expect(page.locator(badge)).toBeHidden()
})

test('two jobs report the longest-running one and say how many there are', async ({ page }) => {
  await openWith(page, busy([
    WD14_JOB,
    { label: 'Aesthetic scoring', tier: 'cpu', priority: 30, estimated_vram_mb: null, elapsed_seconds: 3.1, stuck: false },
  ]))
  await expect(page.locator(badge)).toBeVisible()
  const label = await page.locator('#nav-ai-busy-label').innerText()
  expect(label).toMatch(/WD14 tagging/)
  expect(label, 'the second job is counted, not hidden').toMatch(/\+1|2/)

  const title = await page.locator(badge).getAttribute('title')
  expect(title, 'the tooltip lists every holder').toContain('Aesthetic scoring')
})

test('a lease the app itself calls abnormal is not reported as ordinary work', async ({ page }) => {
  await openWith(page, busy([{ ...WD14_JOB, elapsed_seconds: 1200, stuck: true }]))
  await expect(page.locator(badge)).toBeVisible()
  const label = await page.locator('#nav-ai-busy-label').innerText()
  expect(label, 'a stuck lease reads differently from a busy one').toMatch(/stuck|not responding/i)
  await expect(page.locator(badge)).toHaveClass(/is-stuck/)
})

/** Make one real API call that the backend refuses, and return the sentence the
 *  app would put in front of the user. */
async function refusalText(page: Page, body: Record<string, unknown>): Promise<string> {
  await page.route('**/api/tag/single*', (route) =>
    route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify(body) }))
  return page.evaluate(async () => {
    try {
      await window.App.API.post('/api/tag/single', { image_id: 1 })
    } catch (error) {
      return window.formatUserError(error, 'Tagging failed')
    }
    return 'THE CALL SUCCEEDED'
  })
}

const REFUSAL = {
  error: 'WD14 tagging is using the AI runtime (running 42s). Waited 30s. Try again when it finishes, or cancel it.',
  type: 'AiRuntimeBusyError',
  status_code: 409,
  reason: 'busy',
  waited_seconds: 30.0,
}

test('a child-process holder is explained as one, and points where it can be stopped', async ({ page }) => {
  await openWith(page, IDLE)
  const text = await refusalText(page, {
    ...REFUSAL,
    blocker: { scope: 'process', pid: 4242, label: 'WD14 tagging', elapsed_seconds: 42.0, stuck: false },
  })

  expect(text, 'the holder is named').toContain('WD14 tagging')
  expect(text, 'and how long it has held the runtime').toMatch(/42s/)
  expect(text, 'the Gallery tag job is where a separate process is cancelled')
    .toMatch(/Gallery/i)
  expect(text, 'a busy runtime is not a missing model').not.toMatch(/not ready|Model setup/i)
})

test('an in-server job is explained as one, and does not send the user hunting', async ({ page }) => {
  await openWith(page, IDLE)
  const text = await refusalText(page, {
    ...REFUSAL,
    error: 'Artist identification is using the AI runtime (running 8s). Waited 30s. Try again when it finishes, or cancel it.',
    blocker: { scope: 'thread', label: 'Artist identification', priority: 20, elapsed_seconds: 8.0, stuck: false },
  })

  expect(text).toContain('Artist identification')
  expect(text, 'an in-app job is not the Gallery tag process').not.toMatch(/Gallery/i)
  expect(text, 'it frees itself, which is the honest thing to say').toMatch(/finish/i)
})

test('a lock whose owner is gone asks for the one thing that helps', async ({ page }) => {
  await openWith(page, IDLE)
  const text = await refusalText(page, {
    ...REFUSAL,
    error: 'The AI runtime is locked, but the job that claimed it (WD14 tagging) is no longer running. Restart the app to clear the lock.',
    reason: 'stale_lock_holder_gone',
    blocker: { scope: 'process', pid: 4242, label: 'WD14 tagging', elapsed_seconds: 900.0, stuck: true },
  })

  expect(text, 'the only remedy is a restart').toMatch(/restart/i)
  expect(text, 'waiting for a dead holder is not advice').not.toMatch(/wait for it|try again when it finishes/i)
})

test('an unnamed holder is described without inventing a name', async ({ page }) => {
  await openWith(page, IDLE)
  const text = await refusalText(page, {
    ...REFUSAL,
    error: 'Another process is using the AI runtime. Waited 30s. Try again once it finishes.',
    blocker: null,
  })

  expect(text).toMatch(/another/i)
  expect(text, 'no job name may be conjured').not.toMatch(/WD14|Artist identification/)
})

test('hitting the wall refreshes the badge, so the two agree', async ({ page }) => {
  await openWith(page, IDLE)
  await page.evaluate(() => window.AiBusy.refresh())
  await expect(page.locator(badge)).toBeHidden()

  // The runtime became busy between the poll and the click; the refusal itself
  // is the freshest evidence there is, so it triggers a re-read.
  await page.unroute('**/api/system/ai-jobs*')
  await page.route('**/api/system/ai-jobs*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(busy([WD14_JOB])) }))
  await refusalText(page, {
    ...REFUSAL,
    blocker: { scope: 'process', pid: 4242, label: 'WD14 tagging', elapsed_seconds: 42.0, stuck: false },
  })
  await expect(page.locator(badge)).toBeVisible()
})
