import { expect, test, type Page } from '../fixtures/click-ledger'

/**
 * Prompt Lab's empty panels must say what is true and offer an action only
 * where one exists.
 *
 * The old single string — "Checkpoint patterns will appear here after you
 * import more prompt metadata" — was advice that could never succeed for a
 * library whose images were not generated locally: there is no metadata left to
 * import, because the images never carried any. The backend now answers WHY
 * each panel is empty (`*_empty_reason`) and, separately, whether scanning can
 * help (`checkpoint_empty_action`), and those two answers are deliberately not
 * the same question. `no_checkpoint_metadata` arrives with the scan offer when
 * no indexed image came from a generator at all, and with a NULL action when
 * generator output *is* indexed and simply recorded no model name — there,
 * scanning is a long operation that cannot change the answer, so nothing may be
 * offered.
 *
 * These tests drive each reason through the real renderer and assert the fact
 * appears, the offer appears only with a non-null action, and the misleading
 * sentence is gone.
 */

declare global {
  interface Window {
    PromptLab?: any
    initPromptLab?: any
    App: any
  }
}

const OLD_MISLEADING_ADVICE = 'import more prompt metadata'

const CHECKPOINT_PANELS = {
  topCheckpoints: '#pl-top-checkpoints',
  leaders: '#pl-best-checkpoints',
  recipes: '#pl-recipe-suggestions',
} as const

/** A stats payload with every list empty, so all three panels render a reason. */
function emptyStats(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    total_images: 120,
    usable_images: 118,
    tagged_images: 118,
    scored_images: 0,
    top_tags: [],
    top_tags_total: 0,
    top_tags_denominator: 118,
    top_tags_denominator_basis: 'usable_images',
    top_checkpoints: [],
    top_checkpoints_total: 0,
    checkpoint_score_leaders: [],
    checkpoint_score_leaders_total: 0,
    checkpoint_recipes: [],
    checkpoint_recipes_total: 0,
    high_aesthetic_tags: [],
    high_aesthetic_tags_total: 0,
    low_aesthetic_tags: [],
    top_scored_images: [],
    prompt_length: { avg: 210, max: 640, min: 12, sample: 118, scope: 'usable_images' },
    caption_length: {
      avg: 0, max: 0, min: 0, sample: 0,
      scope: 'usable_images_with_sidecar_caption', available: true,
    },
    checkpoint_coverage: {
      total_images: 120,
      usable_images: 118,
      images_with_checkpoint: 0,
      images_with_checkpoint_any: 0,
      scored_usable_images: 0,
      sd_attributed_images: 0,
      min_scored_images_per_checkpoint: 3,
    },
    checkpoint_empty_action: null,
    top_checkpoints_empty_reason: null,
    checkpoint_score_leaders_empty_reason: null,
    checkpoint_recipes_empty_reason: null,
    ...overrides,
  }
}

async function openPromptLabWith(page: Page, stats: Record<string, unknown>): Promise<void> {
  await page.route('**/api/prompts/stats*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(stats) }))
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect
    .poll(async () => page.evaluate(() => Boolean(window.App?.AppState) && window.App.AppState.isLoading === false))
    .toBe(true)
  await page.evaluate(() => window.App.switchView('promptlab'))
  await expect(page.locator('#view-promptlab.active')).toBeVisible()
  await expect.poll(async () => page.evaluate(() => window.PromptLab?.isReady === true)).toBe(true)
  await page.evaluate(() => window.PromptLab.loadStats())
}

async function panelText(page: Page, selector: string): Promise<string> {
  return (await page.locator(selector).innerText()).replace(/\s+/g, ' ').trim()
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('sd-image-sorter-lang', 'en'))
})

test('no checkpoint metadata states the fact, and offers the scan only when scanning can help', async ({ page }) => {
  // No indexed image came from a generator: the user's own generations are
  // simply somewhere this library has never looked, so the offer is real.
  await openPromptLabWith(page, emptyStats({
    top_checkpoints_empty_reason: 'no_checkpoint_metadata',
    checkpoint_score_leaders_empty_reason: 'no_checkpoint_metadata',
    checkpoint_recipes_empty_reason: 'no_checkpoint_metadata',
    checkpoint_empty_action: 'scan_generated_images_folder',
  }))

  for (const [name, selector] of Object.entries(CHECKPOINT_PANELS)) {
    const text = await panelText(page, selector)
    expect(text, `${name} must state the fact`).toContain('records which checkpoint made it')
    expect(text, `${name} must offer the scan`).toContain('folder of your own generations')
    expect(text, `${name} must not repeat the old advice`).not.toContain(OLD_MISLEADING_ADVICE)
  }
})

test('the same reason with a null action states the fact and offers nothing', async ({ page }) => {
  // Generator output IS indexed and recorded no model name. Scanning again
  // cannot produce one, so an offer here would cost a long operation for
  // nothing.
  await openPromptLabWith(page, emptyStats({
    top_checkpoints_empty_reason: 'no_checkpoint_metadata',
    checkpoint_score_leaders_empty_reason: 'no_checkpoint_metadata',
    checkpoint_recipes_empty_reason: 'no_checkpoint_metadata',
    checkpoint_empty_action: null,
    checkpoint_coverage: { ...(emptyStats().checkpoint_coverage as object), sd_attributed_images: 118 },
  }))

  for (const [name, selector] of Object.entries(CHECKPOINT_PANELS)) {
    const text = await panelText(page, selector)
    expect(text, `${name} must state the fact`).toContain('records which checkpoint made it')
    expect(text, `${name} must not invent an action`).not.toContain('folder of your own generations')
    expect(text, `${name} must not repeat the old advice`).not.toContain(OLD_MISLEADING_ADVICE)
  }
})

test('checkpoints recorded only on deleted files say so, with no offer', async ({ page }) => {
  await openPromptLabWith(page, emptyStats({
    top_checkpoints_empty_reason: 'checkpoint_metadata_only_on_missing_files',
    checkpoint_score_leaders_empty_reason: 'checkpoint_metadata_only_on_missing_files',
    checkpoint_recipes_empty_reason: 'checkpoint_metadata_only_on_missing_files',
    checkpoint_empty_action: null,
    checkpoint_coverage: {
      ...(emptyStats().checkpoint_coverage as object),
      images_with_checkpoint_any: 7,
      sd_attributed_images: 7,
    },
  }))

  const text = await panelText(page, CHECKPOINT_PANELS.topCheckpoints)
  expect(text).toContain('missing from disk')
  expect(text, 'the count the backend measured must reach the reader').toContain('7')
  expect(text).not.toContain('folder of your own generations')
})

test('an unscored library says scoring is what ranking needs, without an offer', async ({ page }) => {
  await openPromptLabWith(page, emptyStats({
    top_checkpoints: [{ name: 'illustrious.safetensors', count: 40 }],
    top_checkpoints_total: 1,
    top_checkpoints_empty_reason: null,
    checkpoint_score_leaders_empty_reason: 'no_scored_images',
    checkpoint_recipes_empty_reason: 'no_scored_images',
    checkpoint_empty_action: null,
    checkpoint_coverage: {
      ...(emptyStats().checkpoint_coverage as object),
      images_with_checkpoint: 40,
      images_with_checkpoint_any: 40,
      sd_attributed_images: 118,
    },
  }))

  const text = await panelText(page, CHECKPOINT_PANELS.leaders)
  expect(text).toContain('aesthetic score')
  expect(text).not.toContain('folder of your own generations')
  // The panel that CAN fill is untouched.
  expect(await panelText(page, CHECKPOINT_PANELS.topCheckpoints)).toContain('illustrious')
})

test('too few scores per checkpoint reports the threshold and the total', async ({ page }) => {
  await openPromptLabWith(page, emptyStats({
    scored_images: 4,
    top_checkpoints: [{ name: 'noobai.safetensors', count: 40 }],
    top_checkpoints_total: 1,
    top_checkpoints_empty_reason: null,
    checkpoint_score_leaders_empty_reason: 'not_enough_scored_images_per_checkpoint',
    checkpoint_recipes_empty_reason: 'not_enough_scored_images_per_checkpoint',
    checkpoint_empty_action: null,
    checkpoint_coverage: {
      ...(emptyStats().checkpoint_coverage as object),
      images_with_checkpoint: 40,
      images_with_checkpoint_any: 40,
      scored_usable_images: 4,
      sd_attributed_images: 118,
      min_scored_images_per_checkpoint: 3,
    },
  }))

  const text = await panelText(page, CHECKPOINT_PANELS.leaders)
  expect(text, 'the threshold must be named').toContain('3')
  expect(text, 'and what the user actually has').toContain('4')
  expect(text).not.toContain('folder of your own generations')
})

test('a reason this build cannot explain says only that, and carries no offer', async ({ page }) => {
  // Forward compatibility: a newer backend reason must degrade to "no data yet"
  // rather than borrowing the scan offer, which would claim the scan addresses
  // something this build never established.
  await openPromptLabWith(page, emptyStats({
    top_checkpoints_empty_reason: 'some_reason_from_a_newer_backend',
    checkpoint_empty_action: 'scan_generated_images_folder',
  }))

  const text = await panelText(page, CHECKPOINT_PANELS.topCheckpoints)
  expect(text).toContain('No checkpoint data to show yet')
  expect(text).not.toContain('folder of your own generations')
})

test('an empty caption statistic reads as a rescan away, not as a dead end', async ({ page }) => {
  await openPromptLabWith(page, emptyStats())

  const card = page.locator('#pl-avg-caption-len').locator('xpath=ancestor::*[contains(@class,"promptlab-stat-card")][1]')
  const text = (await card.innerText()).replace(/\s+/g, ' ').trim()
  expect(text, 'a rescan is a real remedy and must be named').toContain('rescan')
  expect(text, 'and it must not read as an absence with nothing to do')
    .not.toMatch(/no sidecar captions exist/i)
  // 0 as a headline average would read as a measurement, not as "none yet".
  expect(await page.locator('#pl-avg-caption-len').innerText()).not.toBe('0')
})

test('a recorded caption average reports its own sample size', async ({ page }) => {
  await openPromptLabWith(page, emptyStats({
    caption_length: {
      avg: 184, max: 420, min: 21, sample: 96,
      scope: 'usable_images_with_sidecar_caption', available: true,
    },
  }))

  await expect(page.locator('#pl-avg-caption-len')).toHaveText('184')
  const card = page.locator('#pl-avg-caption-len').locator('xpath=ancestor::*[contains(@class,"promptlab-stat-card")][1]')
  expect((await card.innerText()).replace(/\s+/g, ' ')).toContain('96')
})
