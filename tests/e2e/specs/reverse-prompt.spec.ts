import path from 'node:path'
import { expect, test, type Page } from '../fixtures/click-ledger'

/**
 * Reverse Prompt — drop one image, get a prompt, and know which kind you got.
 *
 * "Drop an image, get tags" is ubiquitous (A1111 ships two interrogators in
 * core), so the thing worth testing here is not the drop target. It is the
 * order of operations and the labelling:
 *
 *   * a file that recorded its own prompt shows that RECORD, and no inference
 *     runs at all;
 *   * a file that recorded nothing shows a GUESS, said as a guess, naming the
 *     method that produced it;
 *   * asking for inference over a file that DOES have a record adds a second,
 *     separately labelled box with a comparison note — the record is never
 *     replaced;
 *   * all three modes work on a file that was never indexed, and the library
 *     does not grow;
 *   * a refused lease on the shared inference runtime is explained with the
 *     wording that already shipped for it, not re-invented here;
 *   * TIPO expands Booru tag lists, so it is inert for a target documented to
 *     want natural language, and it warns about its download before spending it.
 *
 * Intake (`POST /api/parse-image`) is always LIVE — it is the half that reads
 * the record, and stubbing it would test nothing. Inference is stubbed: the
 * e2e backend has no vision-model endpoint and no real tagger weights, and the
 * no-write guarantee is pinned at its source in
 * `backend/tests/test_frontend_contract.py`.
 */

// A LOCAL intersection, not a `declare global` augmentation: `interface Window`
// merges across the whole project, and the suite already carries conflicting
// `App` declarations (`types/global.d.ts` says `App?: any`, ai-runtime-busy
// says `App: { API: any }`), so adding a third shape here only widened that
// pre-existing TS2717/TS2687 collision. guide-pins.spec.ts uses this same local
// alias for `App.switchView`; follow it.
type ReverseWindow = typeof window & {
  App: { switchView: (view: string) => void }
  ReversePrompt: { state: { sourcePath: string } }
}

// Ships in the repo and carries a real ComfyUI workflow in its PNG text chunks.
const RECORDED_PNG = path.resolve(__dirname, '../../../backend/favorites/ComfyUI_00208_.png')
// Also ships in the repo, and is a UI screenshot: decodable, and recording no
// prompt at all (verified: generator "unknown", prompt length 0). Using a real
// committed file keeps a binary fixture blob out of this spec.
const NO_METADATA_PNG = path.resolve(
  __dirname,
  '../../../docs/screenshots/caption_editor_fullscreen.png',
)

const TAGGER_RESPONSE = {
  image_path: 'stub',
  model: 'wd-swinv2-tagger-v3',
  rating: 'general',
  rating_confidences: { general: 0.94 },
  elapsed_ms: 120,
  stored: false,
  general_tags: [{ tag: '1girl', confidence: 0.98 }, { tag: 'solo', confidence: 0.95 }],
  character_tags: [],
  copyright_tags: [],
  all_tags: [{ tag: '1girl', confidence: 0.98 }, { tag: 'solo', confidence: 0.95 }],
  tags: ['1girl', 'solo'],
}

const JOB_CAPTION = 'A girl stands alone in a wheat field at golden hour.'

type Recorded = { starts: Array<Record<string, unknown>>; taggerCalls: number }

async function openReverseView(page: Page): Promise<void> {
  await page.addInitScript(() => {
    localStorage.setItem('sd-image-sorter-lang', 'en')
    localStorage.setItem('aurora-entry-skip', '1')
  })
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await page.waitForFunction(() => typeof (window as ReverseWindow).App?.switchView === 'function')
  await page.evaluate(() => (window as ReverseWindow).App.switchView('reverse'))
  await expect(page.locator('#view-reverse')).toBeVisible()
}

/** Stub every inference transport and record what the adapter actually sent. */
async function stubInference(page: Page): Promise<Recorded> {
  const seen: Recorded = { starts: [], taggerCalls: 0 }

  await page.route('**/api/tag/single', async (route) => {
    seen.taggerCalls += 1
    await route.fulfill({ json: TAGGER_RESPONSE })
  })
  await page.route('**/api/smart-tag/start', async (route) => {
    seen.starts.push(route.request().postDataJSON() as Record<string, unknown>)
    // The real endpoint answers with a live snapshot, never a finished one, so
    // the poll loop is genuinely exercised.
    await route.fulfill({ json: { job_id: 'rp-job', status: 'running', total: 1, processed: 0 } })
  })
  await page.route('**/api/smart-tag/progress*', async (route) => {
    await route.fulfill({
      json: { job_id: 'rp-job', status: 'completed', total: 1, processed: 1, succeeded: 1, caption_result_count: 1 },
    })
  })
  await page.route('**/api/smart-tag/results*', async (route) => {
    await route.fulfill({
      json: {
        job_id: 'rp-job',
        offset: 0,
        limit: 1,
        total: 1,
        has_more: false,
        results: [{ path: 'stub', caption: JOB_CAPTION, booru_text: '1girl, solo', nl_text: JOB_CAPTION }],
      },
    })
  })
  return seen
}

async function dropImage(page: Page, file: string): Promise<void> {
  await page.setInputFiles('#reverse-file-input', file)
  await expect
    .poll(async () => page.evaluate(() => (window as ReverseWindow).ReversePrompt.state.sourcePath !== ''), { timeout: 15000 })
    .toBe(true)
}

async function pickMode(page: Page, mode: string): Promise<void> {
  await page.locator(`input[name="reverse-mode"][value="${mode}"]`).check()
}

async function libraryTotal(page: Page): Promise<number> {
  const response = await page.request.get('/api/images/count')
  expect(response.ok(), 'the library count endpoint must answer').toBe(true)
  return Number((await response.json()).total)
}

const recorded = '#reverse-recorded'
const inferred = '#reverse-inferred'

test('a file that recorded its own prompt shows the record, and runs no inference', async ({ page }) => {
  await openReverseView(page)
  const seen = await stubInference(page)

  await dropImage(page, RECORDED_PNG)

  await expect(page.locator(recorded)).toBeVisible()
  const badge = await page.locator(`${recorded} .reverse-badge`).innerText()
  expect(badge, 'the badge must claim a record').toMatch(/record/i)
  const note = await page.locator(`${recorded} .reverse-result-note`).innerText()
  expect(note, 'and the sentence must say it is a record, not a guess')
    .toMatch(/record of what generated/i)
  expect(note).toMatch(/not a guess/i)
  await expect(page.locator(`${recorded} .reverse-result-text`).first()).not.toBeEmpty()

  // Nothing was inferred, and nothing was even asked to infer.
  await expect(page.locator(inferred)).toBeHidden()
  expect(seen.taggerCalls, 'reading the file must not trigger the tagger').toBe(0)
  expect(seen.starts, 'reading the file must not start a job').toHaveLength(0)

  // The run control now offers a comparison rather than a replacement.
  await expect(page.locator('#btn-reverse-run')).toBeEnabled()
  expect(await page.locator('#btn-reverse-run').innerText()).toMatch(/anyway|compare/i)
})

test('a file that recorded nothing gets a guess, labelled as a guess', async ({ page }) => {
  await openReverseView(page)
  await stubInference(page)

  await dropImage(page, NO_METADATA_PNG)
  await expect(page.locator(recorded)).toBeHidden()
  const why = await page.locator('#reverse-no-record').innerText()
  expect(why, 'the page must say there is nothing to read').toMatch(/records no prompt/i)

  await pickMode(page, 'tagger')
  await page.locator('#btn-reverse-run').click()

  await expect(page.locator(inferred)).toBeVisible()
  const badge = await page.locator(`${inferred} .reverse-badge`).innerText()
  expect(badge, 'the badge must claim a guess').toMatch(/guess/i)
  expect(badge, 'and must not claim a record').not.toMatch(/record/i)
  const note = await page.locator(`${inferred} .reverse-result-note`).innerText()
  expect(note, 'the sentence names the method that produced it').toMatch(/WD14 tagger/i)
  expect(note).toMatch(/check it before you use it/i)
  await expect(page.locator(`${inferred} .reverse-result-text`)).toContainText('1girl')
  // With no record on screen there is nothing to compare against.
  await expect(page.locator(`${inferred} .reverse-result-compare`)).toHaveCount(0)
})

test('all three modes answer for a never-indexed file, and the library does not grow', async ({ page }) => {
  await openReverseView(page)
  const seen = await stubInference(page)
  const before = await libraryTotal(page)

  await dropImage(page, NO_METADATA_PNG)

  for (const [mode, expectation] of [
    ['tagger', /1girl/],
    ['vlm', new RegExp(JOB_CAPTION.slice(0, 24))],
    ['grounded', new RegExp(JOB_CAPTION.slice(0, 24))],
  ] as Array<[string, RegExp]>) {
    await pickMode(page, mode)
    await page.locator('#btn-reverse-run').click()
    await expect(page.locator(`${inferred} .reverse-result-text`)).toContainText(expectation, { timeout: 20000 })
    await expect(page.locator(`${inferred} .reverse-badge`)).toContainText(/guess/i)
  }

  // The two job modes are the two existing switches, over a PATH and no ids —
  // which is what makes a library row impossible.
  expect(seen.starts).toHaveLength(2)
  const [vlmOnly, grounded] = seen.starts
  expect(vlmOnly.enable_wd14).toBe(false)
  expect(vlmOnly.enable_vlm).toBe(true)
  expect(grounded.enable_wd14).toBe(true)
  expect(grounded.enable_vlm).toBe(true)
  expect(grounded.vlm_grounding, 'the third mode hands the tags to the vision model').toBe(true)
  for (const payload of seen.starts) {
    expect(Array.isArray(payload.image_paths) && (payload.image_paths as string[]).length).toBe(1)
    expect(payload.image_ids ?? [], 'a dropped file has no library id to send').toEqual([])
  }

  expect(await libraryTotal(page), 'dropping a file must not index it').toBe(before)
})

test('inference over a file that has a record is offered as a comparison, never a replacement', async ({ page }) => {
  await openReverseView(page)
  await stubInference(page)

  await dropImage(page, RECORDED_PNG)
  const record = await page.locator(`${recorded} .reverse-result-text`).first().innerText()
  expect(record.trim().length, 'the recorded prompt must be non-empty to compare against')
    .toBeGreaterThan(0)

  await pickMode(page, 'tagger')
  await page.locator('#btn-reverse-run').click()
  await expect(page.locator(inferred)).toBeVisible()

  // BOTH boxes on screen, each with its own claim.
  await expect(page.locator(recorded)).toBeVisible()
  await expect(page.locator(`${recorded} .reverse-badge`)).toContainText(/record/i)
  await expect(page.locator(`${inferred} .reverse-badge`)).toContainText(/guess/i)
  expect(
    await page.locator(`${recorded} .reverse-result-text`).first().innerText(),
    'the record must be byte-identical after inference ran',
  ).toBe(record)

  const compare = await page.locator(`${inferred} .reverse-result-compare`).innerText()
  expect(compare, 'the comparison note must say which one is real').toMatch(/comparison only/i)
  expect(compare).toMatch(/the record is the true one/i)
})

test('a refused lease on the shared runtime is explained, not reported as a broken model', async ({ page }) => {
  await openReverseView(page)
  await stubInference(page)
  await dropImage(page, NO_METADATA_PNG)

  await page.unroute('**/api/tag/single')
  await page.route('**/api/tag/single', async (route) => {
    await route.fulfill({
      status: 409,
      json: {
        error: 'WD14 tagging is using the AI runtime (running 42s). Waited 30s. Try again when it finishes, or cancel it.',
        type: 'AiRuntimeBusyError',
        status_code: 409,
        reason: 'busy',
        waited_seconds: 30.0,
        blocker: { scope: 'process', pid: 4242, label: 'WD14 tagging', elapsed_seconds: 42.0, stuck: false },
      },
    })
  })

  await pickMode(page, 'tagger')
  await page.locator('#btn-reverse-run').click()

  // #reverse-status is ALREADY visible carrying "Working…" the moment the run
  // starts, so visibility does not mean the refusal has been rendered — the
  // first-ever run of this spec read "Working…" here. `is-error` is the class
  // _setStatus puts on a failed run, so it is the gate that means "the refusal
  // is on screen"; every assertion below is unchanged.
  const status = page.locator('#reverse-status')
  await expect(status).toHaveClass(/is-error/)
  await expect(status).toBeVisible()
  const text = await status.innerText()
  expect(text, 'the holder is named').toContain('WD14 tagging')
  expect(text, 'and how long it has held it').toMatch(/42s/)
  expect(text, 'a child process is stopped from the Gallery tagging bar').toMatch(/Gallery/i)
  expect(text, 'a busy runtime is not a missing model').not.toMatch(/not ready|Model setup/i)
  // Nothing was passed off as a result.
  await expect(page.locator(inferred)).toBeHidden()
})

/**
 * The likeliest VLM failure, and the one a summary line hides.
 *
 * `caption_phase.py` swallows a per-image provider error into `nl_text = ""`
 * when no caption profile is set, `_handle_caption_result` still counts the
 * image as succeeded, and the job therefore ends `completed` carrying
 * `_completion_message` — literally `"Done. {n} ok, {m} failed."`. That string
 * is a progress summary, never a reason, so it must never be what the user
 * reads under a red status. The per-image reason lives in `errors[]`; when the
 * backend recorded none, the honest sentence is that nothing came back.
 */
test('a completed job with no caption gives the reason, never its success summary', async ({ page }) => {
  await openReverseView(page)
  await stubInference(page)
  await dropImage(page, NO_METADATA_PNG)

  const emptyResult = {
    job_id: 'rp-job',
    offset: 0,
    limit: 1,
    total: 1,
    has_more: false,
    results: [{ path: 'stub', caption: '', booru_text: '', nl_text: '' }],
  }
  await page.unroute('**/api/smart-tag/results*')
  await page.route('**/api/smart-tag/results*', async (route) => {
    await route.fulfill({ json: emptyResult })
  })

  const completedWith = async (errors: Array<{ image_id: string; error: string }>) => {
    await page.unroute('**/api/smart-tag/progress*')
    await page.route('**/api/smart-tag/progress*', async (route) => {
      await route.fulfill({
        json: {
          job_id: 'rp-job',
          status: 'completed',
          total: 1,
          processed: 1,
          succeeded: 1,
          failed: 0,
          // What jobs.py:_completion_message actually puts here.
          message: 'Done. 1 ok, 0 failed.',
          errors,
          caption_result_count: 1,
        },
      })
    })
  }

  const status = page.locator('#reverse-status')
  const runAndRead = async (): Promise<string> => {
    await page.locator('#btn-reverse-run').click()
    await expect(status).toHaveClass(/is-error/)
    return status.innerText()
  }

  await pickMode(page, 'vlm')

  // 1. The backend recorded the provider's own sentence: that is the reason.
  await completedWith([{ image_id: 'stub', error: 'The vision model returned no answer content.' }])
  const withReason = await runAndRead()
  expect(withReason, 'the per-image reason is what the user needs').toContain(
    'vision model returned no answer content',
  )
  expect(withReason, 'a success summary is not a failure reason').not.toContain('Done.')
  expect(withReason).not.toMatch(/\d+ ok, \d+ failed/)
  await expect(page.locator(inferred)).toBeHidden()

  // 2. The backend recorded nothing: the honest fallback, still never the summary.
  await completedWith([])
  const withoutReason = await runAndRead()
  expect(withoutReason, 'with no recorded reason the page says what it knows').toMatch(
    /produced no prompt/i,
  )
  expect(withoutReason, 'a success summary is not a failure reason').not.toContain('Done.')
  expect(withoutReason).not.toMatch(/\d+ ok, \d+ failed/)
  await expect(page.locator(inferred)).toBeHidden()
})

/**
 * Cancel has to mean the same thing in all three modes: the run the user
 * abandoned must not land on screen, and abandoning it is not a failure.
 * The tagger mode has no job to cancel, so the request itself is aborted.
 */
test('cancelling a tagger run abandons its guess and is not painted as a failure', async ({ page }) => {
  await openReverseView(page)
  await stubInference(page)
  await dropImage(page, NO_METADATA_PNG)

  // Hold the tagger response open so the cancel lands mid-flight.
  let release: () => void = () => undefined
  const held = new Promise<void>((resolve) => { release = resolve })
  await page.unroute('**/api/tag/single')
  await page.route('**/api/tag/single', async (route) => {
    await held
    // The abort may already have torn the request down; that is the point.
    await route.fulfill({ json: TAGGER_RESPONSE }).catch(() => undefined)
  })

  await pickMode(page, 'tagger')
  await page.locator('#btn-reverse-run').click()

  const cancel = page.locator('#btn-reverse-cancel')
  await expect(cancel, 'a cancel control that is offered must do something').toBeVisible()
  await cancel.click()

  const status = page.locator('#reverse-status')
  await expect(status).toContainText(/cancel/i)
  await expect(page.locator('#btn-reverse-run')).toBeEnabled()

  // Deliberate, so not an error...
  await expect(status, 'the user asked for this; it is not a failure').not.toHaveClass(/is-error/)
  // ...and the guess the user threw away never arrives.
  release()
  await expect(page.locator(inferred)).toBeHidden()
  await page.waitForTimeout(500)
  await expect(page.locator(inferred), 'an abandoned run must not render late').toBeHidden()
})

test('TIPO is inert for a natural-language target and live for a tag target', async ({ page }) => {
  await openReverseView(page)
  await stubInference(page)
  await dropImage(page, NO_METADATA_PNG)

  await page.locator('#reverse-draft').fill('1girl, solo, wheat field')
  const button = page.locator('#btn-reverse-tipo')
  const note = page.locator('#reverse-tipo-note')

  await page.locator('#reverse-target-model').selectOption('anima')
  await expect(button, 'a Booru-tag target may expand tags').toBeEnabled()

  await page.locator('#reverse-target-model').selectOption('krea2')
  await expect(button, 'a natural-language target must not be offered tag expansion').toBeDisabled()
  expect(await note.innerText()).toMatch(/natural-language/i)
  expect(await note.innerText()).toMatch(/Booru tag/i)
})

test('TIPO warns about its download before spending it, and never offers a Prepare button', async ({ page }) => {
  await openReverseView(page)
  await stubInference(page)
  await dropImage(page, NO_METADATA_PNG)

  let suggestCalls = 0
  let prepareCalls = 0
  await page.route('**/api/models/status', async (route) => {
    await route.fulfill({ json: { status: 'ok', models: [{ id: 'tipo', installed_variants: [] }], health: {} } })
  })
  await page.route('**/api/models/prepare', async (route) => {
    prepareCalls += 1
    await route.fulfill({ json: {} })
  })
  await page.route('**/api/tags/suggest-upsample', async (route) => {
    suggestCalls += 1
    await route.fulfill({ json: { proposed_tags: [{ tag: 'wheat', category: 'general' }], model: '200m-ft' } })
  })

  await page.locator('#reverse-target-model').selectOption('anima')
  await page.locator('#reverse-draft').fill('1girl, solo, wheat field')
  await page.locator('#btn-reverse-tipo').click()

  const message = page.locator('#confirm-message')
  await expect(message).toBeVisible()
  const warning = await message.innerText()
  expect(warning, 'the size must be stated before it is spent').toMatch(/100-250 MB/)
  expect(warning, 'and where it lands').toMatch(/data folder/i)

  // Declining spends nothing.
  await page.locator('#btn-confirm-cancel').click()
  expect(suggestCalls, 'declining the download must not run TIPO').toBe(0)

  // Accepting runs it, and the picks are default-unchecked.
  await page.locator('#btn-reverse-tipo').click()
  await expect(message).toBeVisible()
  await page.locator('#btn-confirm-ok').click()
  await expect(page.locator('#reverse-tipo-results')).toContainText(/TIPO proposes 1 tag/i)
  const box = page.locator('#reverse-tipo-results input[type="checkbox"]')
  await expect(box).toHaveCount(1)
  await expect(box).not.toBeChecked()
  await expect(page.locator('#btn-reverse-tipo-apply')).toBeDisabled()

  await box.check()
  await page.locator('#btn-reverse-tipo-apply').click()
  await expect(page.locator('#reverse-draft')).toHaveValue(/wheat field, wheat$/)
  expect(prepareCalls, 'TIPO has no prepare branch; nothing may call it').toBe(0)
})
