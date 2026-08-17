import { expect, test, type Page } from '../fixtures/click-ledger'

/**
 * Characterization pins for frontend/js/app/scan-diagnostics.js (1,023 lines) —
 * "step 0" of a later verbatim decomposition (the file regrew past the 800-line
 * budget when the QA scan fixes landed in 09affbc "make scan completion
 * identity-safe" + 75ed160 "make folder scans junction-safe"). This mirrors the
 * shipped app-core / gallery / image-reader / smart-tag / vlm-caption pins:
 * pin the OBSERVABLE behavior of the load-bearing seams so a future split is
 * provably zero-behavior-change — the suite MUST pass before AND after.
 *
 * ASSEMBLY SHAPE (verified): scan-diagnostics.js is a classic
 * (non-module, no 'use strict') script sharing ONE global lexical environment
 * with app.js and the other app/ parts (index.html loads it BEFORE app.js in
 * original line order). Consequence:
 *   - its top-level `function` declarations become window.* properties, so the
 *     helpers NOT on the facade (readScanIdentity, requireSupersedingScanProgress,
 *     _formatScanDiagnosticsPayload, copyScanLogPath, resumeScanProgress, …) are
 *     reachable from page.evaluate as bare window globals;
 *   - its top-level `const`s (SCAN_SOURCE_MANUAL, SCAN_TERMINAL_STATUSES, …) live
 *     in the global LEXICAL environment and are invisible on window;
 *   - exactly five members are re-published on the sealed window.App facade
 *     (updateScanDiagnosticsCard, copyScanDiagnostics, openScanLogFile,
 *     beginAutoRefreshScanProgress, beginLibraryRescanScanProgress) because
 *     OTHER classic modules (auto-refresh.js, library-roots-ui.js) read them
 *     through window.App. The split must preserve BOTH surfaces.
 *
 * OUT OF SCOPE (deliberately deferred): the folder-start / 400-path-missing
 * flow (startScan + mapScanPathError) lives in the SIBLING scan-flow.js, not in
 * this file — it belongs to a future scan-flow pins spec.
 *
 * The isolated e2e DB starts empty (idle scan state on boot is legitimate); the
 * suite storageState seeds aurora-entry-skip=1 so we land straight in the app.
 */

test.describe.configure({ mode: 'serial' })

type ScanWin = typeof window & {
  App: Record<string, unknown> & {
    updateScanDiagnosticsCard: (progress: unknown) => void
    copyScanDiagnostics: () => Promise<void>
    openScanLogFile: () => Promise<void>
  }
  readScanIdentity: (payload: unknown) => { runId: number, source: string } | null
  scanIdentitiesMatch: (left: unknown, right: unknown) => boolean
  isCanonicalIdleScanProgress: (payload: unknown) => boolean
  requireScanIdentity: (payload: unknown, context: string) => { runId: number, source: string }
  scanIdentityKey: (identity: unknown) => string
  requireSupersedingScanProgress: (error: unknown, requested: unknown) => unknown
  _formatScanDiagnosticsPayload: (payload: unknown) => string
  copyScanLogPath: () => Promise<void>
  resumeScanProgress: () => Promise<void>
  rememberScanDiagnosticsInteraction: () => void
  buildScanAttentionMessage: (progress: unknown, options?: unknown) => string
  appT: (key: string, fallback: string, vars?: Record<string, string>) => string
  __clip: string | null
}

async function gotoApp(page: Page): Promise<void> {
  await page.goto('/')
  await page.waitForLoadState('domcontentloaded')
  // app.js runs at end of <body>; wait until the sealed facade is published
  // with the scan-diagnostics entry point.
  await page.waitForFunction(() => typeof (window as unknown as ScanWin).App?.updateScanDiagnosticsCard === 'function')
}

test.beforeEach(async ({ page }) => {
  await gotoApp(page)
})

// Replace navigator.clipboard.writeText so copyTextToClipboard resolves
// deterministically and we can capture the exact bytes it would copy. The
// product calls navigator.clipboard.writeText (a live browser-API read, NOT a
// lexical binding), so overriding the property here is observed at call time.
async function installClipboardCapture(page: Page): Promise<void> {
  await page.evaluate(() => {
    const w = window as unknown as ScanWin
    w.__clip = null
    Object.defineProperty(window.navigator, 'clipboard', {
      configurable: true,
      value: {
        writeText: (text: string) => {
          w.__clip = String(text)
          return Promise.resolve()
        },
      },
    })
  })
}

// ---------------------------------------------------------------------------
// 1. Assembly surface — facade entries + shared-scope window globals + the
//    lexical consts that must stay invisible. This is the contract the split
//    must reproduce byte-for-byte.
// ---------------------------------------------------------------------------

test('scan-diagnostics publishes 5 facade entries, exposes its helpers as shared-scope window globals, and keeps its consts lexical', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const w = window as unknown as ScanWin
    const App = w.App as Record<string, unknown>
    // Re-published on the SEALED facade — auto-refresh.js / library-roots-ui.js
    // read these through window.App.
    const facadeFns = [
      'updateScanDiagnosticsCard', 'copyScanDiagnostics', 'openScanLogFile',
      'beginAutoRefreshScanProgress', 'beginLibraryRescanScanProgress',
    ]
    // Top-level function declarations reachable as bare window globals (called
    // that way by boot-listeners-shell.js / filter-summary.js / scan-flow.js, or
    // pinned directly below).
    const windowFns = [
      'rememberScanDiagnosticsInteraction', 'rememberScanLogPath',
      '_formatScanDiagnosticsPayload', 'copyScanDiagnostics', 'copyScanLogPath',
      'openScanLogFile', 'updateScanDiagnosticsCard', 'readScanIdentity',
      'scanIdentitiesMatch', 'isCanonicalIdleScanProgress', 'requireScanIdentity',
      'requireSupersedingScanProgress', 'scanIdentityKey', 'attachScanProgressForState',
      'beginManualScanProgress', 'beginAutoRefreshScanProgress',
      'beginLibraryRescanScanProgress', 'resumeScanProgress',
    ]
    // Top-level `const`s: global LEXICAL bindings, never window properties.
    const lexicalConsts = ['SCAN_SOURCE_MANUAL', 'SCAN_TERMINAL_STATUSES', 'LIBRARY_BACKGROUND_SCAN_SOURCES']
    const win = window as unknown as Record<string, unknown>
    return {
      sealed: Object.isSealed(App),
      missingFacadeFns: facadeFns.filter((k) => typeof App[k] !== 'function'),
      missingWindowFns: windowFns.filter((k) => typeof win[k] !== 'function'),
      leakedConsts: lexicalConsts.filter((k) => win[k] !== undefined),
    }
  })

  expect(probe.sealed).toBe(true)
  expect(probe.missingFacadeFns).toEqual([])
  expect(probe.missingWindowFns).toEqual([])
  expect(probe.leakedConsts).toEqual([])
})

// ---------------------------------------------------------------------------
// 2. readScanIdentity — the strict {runId, source} gate. run_id must be a
//    positive safe integer; source must be manual OR a known library source.
// ---------------------------------------------------------------------------

test('readScanIdentity returns a frozen identity only for a positive-safe-int run_id + known source', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const w = window as unknown as ScanWin
    const manual = w.readScanIdentity({ run_id: 5, source: 'manual', status: 'running' })
    return {
      manual,
      manualFrozen: manual ? Object.isFrozen(manual) : null,
      autoRefresh: w.readScanIdentity({ run_id: 7, source: 'library_auto_refresh' }),
      rescan: w.readScanIdentity({ run_id: 9, source: 'library_rescan' }),
      zeroRunId: w.readScanIdentity({ run_id: 0, source: 'manual' }),
      negativeRunId: w.readScanIdentity({ run_id: -3, source: 'manual' }),
      floatRunId: w.readScanIdentity({ run_id: 1.5, source: 'manual' }),
      stringRunId: w.readScanIdentity({ run_id: '3', source: 'manual' }),
      unsafeRunId: w.readScanIdentity({ run_id: Number.MAX_SAFE_INTEGER + 1, source: 'manual' }),
      unknownSource: w.readScanIdentity({ run_id: 4, source: 'library_delete' }),
      nullSource: w.readScanIdentity({ run_id: 4, source: null }),
      nullPayload: w.readScanIdentity(null),
    }
  })

  expect(probe.manual).toEqual({ runId: 5, source: 'manual' })
  expect(probe.manualFrozen).toBe(true)
  expect(probe.autoRefresh).toEqual({ runId: 7, source: 'library_auto_refresh' })
  expect(probe.rescan).toEqual({ runId: 9, source: 'library_rescan' })
  expect(probe.zeroRunId).toBeNull()
  expect(probe.negativeRunId).toBeNull()
  expect(probe.floatRunId).toBeNull()
  expect(probe.stringRunId).toBeNull()
  expect(probe.unsafeRunId).toBeNull()
  expect(probe.unknownSource).toBeNull()
  expect(probe.nullSource).toBeNull()
  expect(probe.nullPayload).toBeNull()
})

// ---------------------------------------------------------------------------
// 3. scanIdentitiesMatch + isCanonicalIdleScanProgress + scanIdentityKey — the
//    equality / canonical-idle / poll-key primitives the poller map keys on.
// ---------------------------------------------------------------------------

test('scanIdentitiesMatch, isCanonicalIdleScanProgress and scanIdentityKey pin the identity primitives', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const w = window as unknown as ScanWin
    const a = { runId: 5, source: 'manual' }
    return {
      matchSame: w.scanIdentitiesMatch(a, { runId: 5, source: 'manual' }),
      matchDiffRun: w.scanIdentitiesMatch(a, { runId: 6, source: 'manual' }),
      matchDiffSource: w.scanIdentitiesMatch(a, { runId: 5, source: 'library_rescan' }),
      matchNullLeft: w.scanIdentitiesMatch(null, { runId: 5, source: 'manual' }),
      matchNullRight: w.scanIdentitiesMatch(a, null),
      // isCanonicalIdleScanProgress is exact: status 'idle' AND run_id 0 AND source null.
      idleCanonical: w.isCanonicalIdleScanProgress({ status: 'idle', run_id: 0, source: null }),
      idleWithSource: w.isCanonicalIdleScanProgress({ status: 'idle', run_id: 0, source: 'manual' }),
      idleWithRun: w.isCanonicalIdleScanProgress({ status: 'idle', run_id: 5, source: null }),
      nonIdleStatus: w.isCanonicalIdleScanProgress({ status: 'running', run_id: 0, source: null }),
      idleNull: w.isCanonicalIdleScanProgress(null),
      key: w.scanIdentityKey({ runId: 42, source: 'library_auto_refresh' }),
    }
  })

  expect(probe.matchSame).toBe(true)
  expect(probe.matchDiffRun).toBe(false)
  expect(probe.matchDiffSource).toBe(false)
  expect(probe.matchNullLeft).toBe(false)
  expect(probe.matchNullRight).toBe(false)
  expect(probe.idleCanonical).toBe(true)
  expect(probe.idleWithSource).toBe(false)
  expect(probe.idleWithRun).toBe(false)
  expect(probe.nonIdleStatus).toBe(false)
  expect(probe.idleNull).toBe(false)
  expect(probe.key).toBe('42:library_auto_refresh')
})

// ---------------------------------------------------------------------------
// 4. requireScanIdentity — returns the identity, or throws a TypeError that
//    names the context + the offending status/run_id/source.
// ---------------------------------------------------------------------------

test('requireScanIdentity returns the identity for valid payloads and throws a descriptive TypeError otherwise', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const w = window as unknown as ScanWin
    const ok = w.requireScanIdentity({ run_id: 11, source: 'manual', status: 'done' }, 'Ctx')
    let threw = false
    let name = ''
    let message = ''
    try {
      w.requireScanIdentity({ run_id: 0, source: null, status: 'idle' }, 'Manual scan acknowledgement')
    } catch (error) {
      threw = true
      name = (error as Error).name
      message = (error as Error).message
    }
    return { ok, threw, name, message }
  })

  expect(probe.ok).toEqual({ runId: 11, source: 'manual' })
  expect(probe.threw).toBe(true)
  expect(probe.name).toBe('TypeError')
  expect(probe.message).toContain('Manual scan acknowledgement')
  expect(probe.message).toContain('invalid scan identity')
  expect(probe.message).toContain('run_id=0')
})

// ---------------------------------------------------------------------------
// 5. requireSupersedingScanProgress — the 409 scan_identity_mismatch resolver.
//    Canonical idle => null (reset). A DIFFERENT valid identity with a supported
//    status => the frozen superseding progress. A repeated identity, or an
//    unsupported status, => a TypeError.
// ---------------------------------------------------------------------------

test('requireSupersedingScanProgress resolves canonical-idle, a superseding identity, and rejects repeats + bad statuses', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const w = window as unknown as ScanWin
    const requested = { runId: 5, source: 'manual' }
    const conflict = (current: unknown) => ({ apiData: { current } })

    const idleResult = w.requireSupersedingScanProgress(
      conflict({ status: 'idle', run_id: 0, source: null }),
      requested,
    )

    const supersede = w.requireSupersedingScanProgress(
      conflict({ status: 'running', run_id: 99, source: 'manual' }),
      requested,
    )

    let repeatThrew = false
    try {
      w.requireSupersedingScanProgress(
        conflict({ status: 'running', run_id: 5, source: 'manual' }),
        requested,
      )
    } catch { repeatThrew = true }

    let badStatusThrew = false
    try {
      w.requireSupersedingScanProgress(
        conflict({ status: 'paused', run_id: 99, source: 'manual' }),
        requested,
      )
    } catch { badStatusThrew = true }

    return {
      idleResult,
      supersede,
      supersedeFrozen: supersede ? Object.isFrozen(supersede) : null,
      repeatThrew,
      badStatusThrew,
    }
  })

  expect(probe.idleResult).toBeNull()
  expect(probe.supersede).toEqual({ run_id: 99, source: 'manual', status: 'running' })
  expect(probe.supersedeFrozen).toBe(true)
  expect(probe.repeatThrew).toBe(true)
  expect(probe.badStatusThrew).toBe(true)
})

// ---------------------------------------------------------------------------
// 6. _formatScanDiagnosticsPayload — the copy-to-clipboard text builder.
//    Redacted path preferred, yes/no + on/off booleans, log-line fallback.
// ---------------------------------------------------------------------------

test('_formatScanDiagnosticsPayload renders the labelled diagnostics block with redaction + fallbacks', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const w = window as unknown as ScanWin
    const full = w._formatScanDiagnosticsPayload({
      app_version: '9.9.9',
      log_file_path: '/home/u/app.log',
      log_file_path_redacted: '<HOME>/app.log',
      log_file_exists: true,
      access_log_enabled: false,
      log_level: 'INFO',
      recent_log_text: 'line1\nline2',
    })
    const empty = w._formatScanDiagnosticsPayload({})
    // path present but no redacted variant => the literal placeholder <PATH>.
    const rawOnly = w._formatScanDiagnosticsPayload({ log_file_path: '/x/y.log' })
    return { full, empty, rawOnly }
  })

  expect(probe.full).toBe([
    'SD Image Sorter scan diagnostics',
    'App version: 9.9.9',
    'Log file: <HOME>/app.log',
    'Log exists: yes',
    'Access log: off',
    'Log level: INFO',
    '',
    'Recent backend log:',
    'line1\nline2',
  ].join('\n'))

  expect(probe.empty).toContain('App version: unknown')
  expect(probe.empty).toContain('Log file: unavailable')
  expect(probe.empty).toContain('Log exists: no')
  expect(probe.empty).toContain('Access log: off')
  expect(probe.empty).toContain('Log level: unknown')
  expect(probe.empty).toContain('(no log lines available)')
  expect(probe.rawOnly).toContain('Log file: <PATH>')
})

// ---------------------------------------------------------------------------
// 7. updateScanDiagnosticsCard — hidden unless attention is required or the
//    post-interaction hold window is active on a running/cancelling scan.
// ---------------------------------------------------------------------------

test('updateScanDiagnosticsCard hides the card for null, terminal, and running-without-hold progress', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const w = window as unknown as ScanWin
    const card = document.getElementById('scan-diagnostics-card') as HTMLElement
    const snapshot = () => ({
      display: card.style.display,
      isVisible: card.classList.contains('is-visible'),
    })
    w.App.updateScanDiagnosticsCard(null)
    const afterNull = snapshot()
    w.App.updateScanDiagnosticsCard({ status: 'done', run_id: 5, source: 'manual' })
    const afterDone = snapshot()
    // Fresh page: the hold-until timestamp is 0, so a running scan with no
    // attention flag stays hidden.
    w.App.updateScanDiagnosticsCard({ status: 'running', run_id: 5, source: 'manual' })
    const afterRunningNoHold = snapshot()
    return { afterNull, afterDone, afterRunningNoHold }
  })

  expect(probe.afterNull).toEqual({ display: 'none', isVisible: false })
  expect(probe.afterDone).toEqual({ display: 'none', isVisible: false })
  expect(probe.afterRunningNoHold).toEqual({ display: 'none', isVisible: false })
})

// ---------------------------------------------------------------------------
// 8. updateScanDiagnosticsCard — attention_required render: card shown, meta
//    fields populated, completed uses metadata_processed/metadata_total, and
//    the message equals buildScanAttentionMessage(progress).
// ---------------------------------------------------------------------------

test('updateScanDiagnosticsCard shows the attention card with the attention message and metadata completed counts', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const w = window as unknown as ScanWin
    const progress = {
      status: 'running',
      run_id: 5,
      source: 'manual',
      attention_required: true,
      step: 'metadata',
      current_item: 'foo.png',
      metadata_pending: 3,
      metadata_processed: 10,
      metadata_total: 20,
    }
    w.App.updateScanDiagnosticsCard(progress)
    const card = document.getElementById('scan-diagnostics-card') as HTMLElement
    const text = (id: string) => document.getElementById(id)?.textContent ?? null
    const stop = document.getElementById('btn-stop-scan-from-diagnostics') as HTMLButtonElement
    return {
      display: card.style.display,
      isVisible: card.classList.contains('is-visible'),
      message: text('scan-diagnostics-message'),
      expectedMessage: w.buildScanAttentionMessage(progress),
      step: text('scan-diagnostics-step'),
      current: text('scan-diagnostics-current'),
      pending: text('scan-diagnostics-pending'),
      completed: text('scan-diagnostics-completed'),
      stopDisabled: stop.disabled,
      stopText: stop.textContent,
    }
  })

  expect(probe.display).toBe('flex')
  expect(probe.isVisible).toBe(true)
  expect(probe.message).toBe(probe.expectedMessage)
  expect(probe.step).toBe('metadata')
  expect(probe.current).toBe('foo.png')
  expect(probe.pending).toBe('3')
  expect(probe.completed).toBe('10/20')
  expect(probe.stopDisabled).toBe(false)
})

// ---------------------------------------------------------------------------
// 9. updateScanDiagnosticsCard — completed-count fallback (no metadata_total)
//    and the cancelling stop-button state (disabled + "Stopping...").
// ---------------------------------------------------------------------------

test('updateScanDiagnosticsCard falls back to processed/total counts and disables the stop button while cancelling', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const w = window as unknown as ScanWin
    const text = (id: string) => document.getElementById(id)?.textContent ?? null

    // No metadata_total => `${processed || current || 0}/${total || '?'}`.
    w.App.updateScanDiagnosticsCard({
      status: 'running', run_id: 5, source: 'manual', attention_required: true,
      processed: 5, total: 0,
    })
    const totalUnknown = text('scan-diagnostics-completed')

    w.App.updateScanDiagnosticsCard({
      status: 'running', run_id: 5, source: 'manual', attention_required: true,
      processed: 0, current: 7, total: 12,
    })
    const processedFromCurrent = text('scan-diagnostics-completed')

    // Cancelling: stop button disabled and relabelled.
    w.App.updateScanDiagnosticsCard({
      status: 'cancelling', run_id: 5, source: 'manual', attention_required: true,
    })
    const stop = document.getElementById('btn-stop-scan-from-diagnostics') as HTMLButtonElement
    return {
      totalUnknown,
      processedFromCurrent,
      cancelDisabled: stop.disabled,
      cancelText: stop.textContent,
    }
  })

  expect(probe.totalUnknown).toBe('5/?')
  expect(probe.processedFromCurrent).toBe('7/12')
  expect(probe.cancelDisabled).toBe(true)
  expect(probe.cancelText).toBe('Stopping...')
})

// ---------------------------------------------------------------------------
// 10. updateScanDiagnosticsCard — the post-interaction hold window keeps the
//     card visible on a running scan even without attention_required, showing
//     the "recently active" message instead of the attention message.
// ---------------------------------------------------------------------------

test('updateScanDiagnosticsCard keeps the card visible during the interaction hold with the recently-active message', async ({ page }) => {
  const probe = await page.evaluate(() => {
    const w = window as unknown as ScanWin
    // Arm the 10s hold window, then render a running scan with NO attention flag.
    w.rememberScanDiagnosticsInteraction()
    w.App.updateScanDiagnosticsCard({ status: 'running', run_id: 5, source: 'manual', metadata_pending: 0 })
    const card = document.getElementById('scan-diagnostics-card') as HTMLElement
    return {
      display: card.style.display,
      isVisible: card.classList.contains('is-visible'),
      message: document.getElementById('scan-diagnostics-message')?.textContent ?? null,
      expected: w.appT('scan.diagnosticsRecentlyActive', 'Progress resumed. Keeping diagnostics visible briefly in case you still need them.'),
    }
  })

  expect(probe.display).toBe('flex')
  expect(probe.isVisible).toBe(true)
  expect(probe.message).toBe(probe.expected)
})

// ---------------------------------------------------------------------------
// 11. copyScanDiagnostics — fetches /api/support/diagnostics?lines=200, copies
//     the formatted block, and shows a success toast.
// ---------------------------------------------------------------------------

test('copyScanDiagnostics copies the formatted diagnostics block and toasts success', async ({ page }) => {
  let capturedLines: string | null = null
  await page.route(/\/api\/support\/diagnostics/, async (route) => {
    capturedLines = new URL(route.request().url()).searchParams.get('lines')
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        app_version: '3.5.0',
        log_file_path: '/home/u/app.log',
        log_file_path_redacted: '<HOME>/app.log',
        log_file_exists: true,
        access_log_enabled: true,
        log_level: 'DEBUG',
        recent_log_text: 'boot ok\nscan started',
      }),
    })
  })
  await installClipboardCapture(page)

  const result = await page.evaluate(async () => {
    const w = window as unknown as ScanWin
    document.getElementById('toast-container')?.replaceChildren()
    await w.App.copyScanDiagnostics()
    return {
      clip: w.__clip,
      expected: w._formatScanDiagnosticsPayload({
        app_version: '3.5.0',
        log_file_path: '/home/u/app.log',
        log_file_path_redacted: '<HOME>/app.log',
        log_file_exists: true,
        access_log_enabled: true,
        log_level: 'DEBUG',
        recent_log_text: 'boot ok\nscan started',
      }),
    }
  })

  expect(capturedLines).toBe('200')
  expect(result.clip).toBe(result.expected)
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(1)
})

// ---------------------------------------------------------------------------
// 12. openScanLogFile — POST /api/support/open-log. opened:true => success
//     toast + redacted path shown; opened:false => a warning toast (path still
//     shown). The path element always renders the REDACTED path.
// ---------------------------------------------------------------------------

test('openScanLogFile shows the redacted path and toasts success when opened, warning when not', async ({ page }) => {
  let openResponse = { opened: true, path: '/home/u/app.log', path_redacted: '<HOME>/app.log' }
  await page.route('**/api/support/open-log', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(openResponse),
    })
  })

  await page.evaluate(async () => {
    const w = window as unknown as ScanWin
    document.getElementById('toast-container')?.replaceChildren()
    await w.App.openScanLogFile()
  })
  const pathEl = page.locator('#scan-diagnostics-path')
  await expect(pathEl).toHaveText('<HOME>/app.log')
  await expect(pathEl).toHaveAttribute('title', '<HOME>/app.log')
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(1)

  // opened:false => warning toast; path is still updated.
  openResponse = { opened: false, path: '/home/u/app.log', path_redacted: '<HOME>/app.log' } as typeof openResponse
  await page.evaluate(async () => {
    const w = window as unknown as ScanWin
    document.getElementById('toast-container')?.replaceChildren()
    await w.App.openScanLogFile()
  })
  await expect(pathEl).toHaveText('<HOME>/app.log')
  await expect(page.locator('#toast-container .toast.warning')).toHaveCount(1)
})

// ---------------------------------------------------------------------------
// 13. copyScanLogPath — with no cached path, fetches diagnostics(1), renders
//     the REDACTED path in the UI, but copies the RAW (unredacted) path. This
//     redacted-display / raw-copy split is a privacy-relevant invariant.
// ---------------------------------------------------------------------------

test('copyScanLogPath renders the redacted path but copies the raw path', async ({ page }) => {
  let capturedLines: string | null = null
  await page.route(/\/api\/support\/diagnostics/, async (route) => {
    capturedLines = new URL(route.request().url()).searchParams.get('lines')
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        log_file_path: '/home/u/secret/app.log',
        log_file_path_redacted: '<HOME>/app.log',
      }),
    })
  })
  await installClipboardCapture(page)

  const clip = await page.evaluate(async () => {
    const w = window as unknown as ScanWin
    document.getElementById('toast-container')?.replaceChildren()
    const pathEl = document.getElementById('scan-diagnostics-path') as HTMLElement
    pathEl.textContent = ''
    await w.copyScanLogPath()
    return w.__clip
  })

  expect(capturedLines).toBe('1')
  expect(clip).toBe('/home/u/secret/app.log')
  await expect(page.locator('#scan-diagnostics-path')).toHaveText('<HOME>/app.log')
  await expect(page.locator('#toast-container .toast.success')).toHaveCount(1)
})

// ---------------------------------------------------------------------------
// 14. resumeScanProgress — an idle scan is a clean no-op: it hides the floating
//     bg progress bar, attaches no poller, and never posts an acknowledgement.
// ---------------------------------------------------------------------------

test('resumeScanProgress short-circuits on idle: hides bg progress, posts no acknowledgement', async ({ page }) => {
  await page.route('**/api/scan/progress', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'idle', run_id: 0, source: null }),
    })
  })

  const acknowledgeCalls: string[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname === '/api/scan/acknowledge') {
      acknowledgeCalls.push(request.method())
    }
  })

  const result = await page.evaluate(async () => {
    const w = window as unknown as ScanWin
    document.getElementById('toast-container')?.replaceChildren()
    const bar = document.getElementById('bg-scan-progress') as HTMLElement
    bar.style.display = 'flex'
    await w.resumeScanProgress()
    return { barDisplay: bar.style.display }
  })

  expect(result.barDisplay).toBe('none')
  expect(acknowledgeCalls).toEqual([])
  await expect(page.locator('#toast-container .toast.error')).toHaveCount(0)
})
