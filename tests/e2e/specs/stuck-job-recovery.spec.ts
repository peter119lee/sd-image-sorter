import path from 'node:path'
import fsSync from 'node:fs'
import { execFileSync } from 'node:child_process'

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
 *
 * That last claim used to be checked against causes written by hand in this
 * file, which is why it kept passing while the product shipped
 * "manual-autosep-copy-fail.png: An unexpected error occurred. Please try
 * again." for a file that cannot be decoded. Hand-written causes only ever
 * prove that the strings someone thought of survive. The causes below are
 * produced by the backend normalizer itself, so the guard fails when either
 * side drifts: if the backend stops normalizing, the cause arrives carrying an
 * absolute path and the panel drops it; if the panel stops formatting, the
 * mapped cause arrives raw.
 *
 * The last test is the other half of the same boundary. `formatUserError`
 * discards drive-qualified paths on purpose, across every error surface in the
 * app, so "the cause was swallowed" must never be answered by loosening that
 * filter. A cause that still carries an absolute path has to stay swallowed.
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

const repoRoot = path.resolve(__dirname, '..', '..', '..')

function resolveBackendPython(): string {
  const candidates = process.platform === 'win32'
    ? [
        path.join(repoRoot, 'backend', 'venv', 'Scripts', 'python.exe'),
        path.join(repoRoot, 'backend', 'venv', 'bin', 'python'),
      ]
    : [
        path.join(repoRoot, 'backend', 'venv', 'bin', 'python'),
        path.join(repoRoot, 'backend', 'venv', 'Scripts', 'python.exe'),
      ]
  return process.env.PW_BACKEND_PYTHON
    || candidates.find((candidate) => fsSync.existsSync(candidate))
    || (process.platform === 'win32' ? 'python' : 'python3')
}

/**
 * The exact causes the backend reports for real per-file failures, taken from
 * the backend rather than invented here — including the corrupt file it decodes
 * for real, which is the failure this panel was losing.
 */
function backendReportedCauses(): string[] {
  const backendDir = path.join(repoRoot, 'backend')
  const script = `
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ${JSON.stringify(backendDir)})

from PIL import Image

from exceptions import FileOperationError
from metadata_parser._runtime import verify_image_readable
from services.sorting.move import _describe_file_operation_cause, describe_readability_failure

causes = []

with tempfile.TemporaryDirectory() as workspace:
    broken = Path(workspace) / "IMG_0042.png"
    Image.new("RGB", (16, 16), color="green").save(broken)
    broken.write_bytes(b"truncated image data")
    readable, decoder_answer = verify_image_readable(str(broken))
    if readable:
        raise SystemExit("the decoder accepted a truncated file; this fixture proves nothing")
    causes.append(describe_readability_failure(decoder_answer))

denied = FileOperationError(
    "Permission denied: [WinError 5] Access is denied:\\n"
    r"'L:\\library\\IMG_0043.png' -> 'L:\\keepers\\IMG_0043.png'",
    path=r"L:\\library\\IMG_0043.png",
    operation="move",
)
causes.append(_describe_file_operation_cause(denied))

locked = FileOperationError(
    "Failed to move file: [WinError 32] The process cannot access the file "
    "because it is being used by another process",
    path=r"L:\\library\\IMG_0044.png",
    operation="move",
)
causes.append(_describe_file_operation_cause(locked))

print(json.dumps(causes))
`
  const output = execFileSync(resolveBackendPython(), ['-X', 'utf8', '-c', script], {
    cwd: repoRoot,
    stdio: 'pipe',
  }).toString('utf8').trim()

  const causes: unknown = JSON.parse(output)
  if (!Array.isArray(causes) || causes.length === 0) {
    throw new TypeError(`Backend reported no causes to check: ${output}`)
  }
  for (const cause of causes) {
    if (typeof cause !== 'string' || cause.trim().length === 0) {
      throw new TypeError(`Backend reported an unusable cause: ${JSON.stringify(cause)}`)
    }
  }
  return causes as string[]
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
  const backendCauses = backendReportedCauses()
  await gotoWithControllableClock(page)
  await openAutoSeparate(page)
  await page.evaluate((causes) => {
    const w = window as RecoveryWin
    const recentErrors = [
      ...causes.map((error, index) => ({ filename: `IMG_01${index}.png`, error })),
      // A cause the shared map owns, so a raw render is distinguishable from a
      // formatted one.
      { filename: 'IMG_0199.png', error: 'Failed to move image: ENOSPC no space left on device' },
    ]
    w.showAutosepMoveProgress(recentErrors.length)
    ;(w as any).updateAutosepMoveProgress({
      status: 'running',
      current: recentErrors.length,
      total: recentErrors.length,
      moved: 0,
      errors: recentErrors.length,
      operation: 'move',
      recent_errors: recentErrors,
    }, recentErrors.length)
  }, backendCauses)

  const panel = page.locator('#autosep-move-errors')
  await expect(panel).toBeVisible()
  const text = (await panel.textContent()) || ''

  // Every cause the backend reports arrives whole: not shortened, not replaced.
  for (const cause of backendCauses) {
    expect(text).toContain(cause)
  }
  expect(text).not.toContain('An unexpected error occurred')
  expect(text).not.toContain('发生了未预期的错误')

  // ENOSPC proves the panel really is going through formatUserError now: raw
  // rendering would have printed the errno string instead of this sentence.
  expect(text).toContain('Not enough disk space')

  await page.evaluate(() => (window as RecoveryWin).hideAutosepMoveProgress())
})

test('a cause that still carries an absolute path stays swallowed', async ({ page }) => {
  await gotoWithControllableClock(page)
  await openAutoSeparate(page)
  await page.evaluate(() => {
    const w = window as RecoveryWin
    w.showAutosepMoveProgress(1)
    ;(w as any).updateAutosepMoveProgress({
      status: 'running',
      current: 1,
      total: 1,
      moved: 0,
      errors: 1,
      operation: 'move',
      recent_errors: [{
        filename: 'IMG_0200.png',
        error: "cannot identify image file 'L:\\private-library\\subject\\IMG_0200.png'",
      }],
    }, 1)
  })

  const panel = page.locator('#autosep-move-errors')
  await expect(panel).toBeVisible()
  const text = (await panel.textContent()) || ''

  // The filter that discards drive-qualified paths guards every error surface
  // in the app, so it has to keep firing here. Making a swallowed cause visible
  // is the backend's job — normalize it before reporting it — never this
  // filter's job to stop looking.
  expect(text).not.toContain('L:\\private-library')
  expect(text).not.toContain('private-library')
  // The panel still names the file it is talking about.
  expect(text).toContain('IMG_0200.png')

  await page.evaluate(() => (window as RecoveryWin).hideAutosepMoveProgress())
})
