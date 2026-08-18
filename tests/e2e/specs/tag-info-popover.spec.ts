import { expect, test, type Page } from '../fixtures/click-ledger'

/**
 * The tag knowledge popover.
 *
 * `GET /api/tags/info` has always answered with everything the app knows about
 * one tag — canonical spelling, Danbooru popularity, aliases, Chinese display,
 * implication edges both ways, and the live library count — and the only way to
 * reach it was the Separation Console's per-row menu. The autocomplete, which is
 * where tags are actually typed, never asked.
 *
 * Two honesty rules govern what it may say:
 *
 * 1. These are vocabulary and library facts. A Danbooru post count says how
 *    often booru users tagged something; it says nothing about what any model
 *    was trained on, and the popover must not let the reader infer that it does.
 * 2. For a project whose target model wants natural-language captions, Booru
 *    tag conventions do not apply, and the popover says so instead of quietly
 *    presenting tag lore as advice.
 */

declare global {
  interface Window {
    CaptionAutocomplete: any
  }
}

const INFO = {
  tag: 'hatsune_miku',
  canonical: 'hatsune_miku',
  found_in_vocab: true,
  category: 'character',
  danbooru_count: 1_284_000,
  aliases: ['miku', 'miku_hatsune'],
  zh: '初音未来',
  implies: ['vocaloid'],
  implied_by: ['hatsune_miku_(append)'],
  library_count: 340,
}

async function openApp(page: Page): Promise<void> {
  await page.addInitScript(() => localStorage.setItem('sd-image-sorter-lang', 'en'))
  await page.goto('/', { waitUntil: 'domcontentloaded' })
  await expect(page.locator('#view-gallery')).toBeVisible()
  await expect
    .poll(async () => page.evaluate(() => typeof window.CaptionAutocomplete?.showTagInfo === 'function'))
    .toBe(true)
}

async function routeInfo(page: Page, info: Record<string, unknown>): Promise<void> {
  await page.route('**/api/tags/info*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(info) }))
}

/** Open the popover for one tag against an attached surface, without needing a
 *  view opened or a project loaded. */
async function showFor(page: Page, elementId: string, tag: string): Promise<void> {
  await page.evaluate(([id, name]) => {
    const el = document.getElementById(id as string)
    window.CaptionAutocomplete.showTagInfo(el, name as string)
  }, [elementId, tag])
  await expect(page.locator('.cap-ac-info')).toBeVisible()
  await expect(page.locator('.cap-ac-info')).not.toContainText('Loading')
}

async function popoverText(page: Page): Promise<string> {
  return (await page.locator('.cap-ac-info').innerText()).replace(/\s+/g, ' ').trim()
}

test('the popover reports what the app knows about a tag', async ({ page }) => {
  await routeInfo(page, INFO)
  await openApp(page)
  await showFor(page, 'mass-tag-add-tags', 'hatsune_miku')

  const text = await popoverText(page)
  expect(text, 'canonical spelling').toContain('hatsune_miku')
  expect(text, 'Chinese display').toContain('初音未来')
  expect(text, 'category').toContain('character')
  // Abbreviated, not a raw seven-digit number.
  expect(text, 'danbooru popularity').toMatch(/1\.3M|1,284,000/)
  expect(text, 'the live library count').toContain('340')
  expect(text, 'aliases').toContain('miku_hatsune')
  expect(text, 'implication edges').toContain('vocaloid')
  expect(text, 'and the reverse edge').toContain('hatsune_miku_(append)')
})

test('the popover never presents its counts as model knowledge', async ({ page }) => {
  await routeInfo(page, INFO)
  await openApp(page)
  await showFor(page, 'mass-tag-add-tags', 'hatsune_miku')

  const text = await popoverText(page)
  expect(text, 'it must say whose facts these are')
    .toMatch(/do(es)? not say what any model was trained on/i)
  for (const claim of [
    /the model (knows|understands|recognises|recognizes)/i,
    /works well/i,
    /better results/i,
    /recommended tag/i,
  ]) {
    expect(text, `must not imply model capability: ${claim}`).not.toMatch(claim)
  }
})

test('typing an alias says which tag it resolves to', async ({ page }) => {
  await routeInfo(page, { ...INFO, tag: 'miku', canonical: 'hatsune_miku' })
  await openApp(page)
  await showFor(page, 'mass-tag-add-tags', 'miku')

  expect(await popoverText(page)).toMatch(/alias of .*hatsune_miku/i)
})

test('a tag outside the bundled vocabulary is described, not dismissed', async ({ page }) => {
  await routeInfo(page, {
    tag: 'my_own_trigger_word', canonical: 'my_own_trigger_word', found_in_vocab: false,
    category: 'unknown', danbooru_count: 0, aliases: [], zh: null,
    implies: [], implied_by: [], library_count: 12,
  })
  await openApp(page)
  await showFor(page, 'mass-tag-add-tags', 'my_own_trigger_word')

  const text = await popoverText(page)
  expect(text).toContain('my_own_trigger_word')
  expect(text, 'the vocabulary miss is a fact, not a verdict').toMatch(/not in the bundled/i)
  expect(text, 'the library count still applies').toContain('12')
})

/** The Dataset Maker view is not open in these tests, so drive the setting the
 *  way the app itself reads it: the live value of the select. */
async function setTargetModel(page: Page, value: string): Promise<void> {
  await page.evaluate((next) => {
    const select = document.getElementById('dataset-target-model') as HTMLSelectElement
    select.value = next as string
    select.dispatchEvent(new Event('change', { bubbles: true }))
  }, value)
}

test('a natural-language target says Booru conventions do not apply there', async ({ page }) => {
  await routeInfo(page, INFO)
  await openApp(page)

  // krea2 is the natural-language-first target in the backend's dialect map.
  await setTargetModel(page, 'krea2')
  await showFor(page, 'dataset-editor-textarea', 'hatsune_miku')
  expect(await popoverText(page), 'the note must appear for a natural-language target')
    .toMatch(/natural-language/i)

  // anima is the tag-first target: the same popover carries no such note.
  await page.locator('.cap-ac-info-close').click()
  await setTargetModel(page, 'anima')
  await showFor(page, 'dataset-editor-textarea', 'hatsune_miku')
  expect(await popoverText(page), 'a tag-first target needs no dialect note')
    .not.toMatch(/natural-language/i)

  // sdxl and flux are un-opinionated in the backend's dialect map, and a note
  // for them would be a claim no first-party source supports.
  await page.locator('.cap-ac-info-close').click()
  await setTargetModel(page, 'flux')
  await showFor(page, 'dataset-editor-textarea', 'hatsune_miku')
  expect(await popoverText(page), 'an un-opinionated target gets no dialect claim')
    .not.toMatch(/natural-language/i)
})

test('the dialect note is scoped to dataset caption surfaces', async ({ page }) => {
  await routeInfo(page, INFO)
  await openApp(page)
  await setTargetModel(page, 'krea2')

  // The image-detail tag editor writes library tags, not this project's
  // captions; borrowing the project's target model there would be a claim
  // about text the setting does not govern.
  await showFor(page, 'modal-tags-add-input', 'hatsune_miku')
  expect(await popoverText(page)).not.toMatch(/natural-language/i)
})

test('the keyboard path opens the popover from the highlighted suggestion', async ({ page }) => {
  await routeInfo(page, INFO)
  await page.route('**/api/tags/suggest*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        suggestions: [
          { tag: 'hatsune_miku', count: 1_284_000, source: 'vocab', category: 'character', zh: '初音未来' },
        ],
      }),
    }))
  await openApp(page)
  await page.evaluate(() => window.App.switchView('promptlab'))
  await page.locator('.promptlab-tab[data-mode="build"]').click()
  await page.evaluate(() => {
    const editor = document.getElementById('pl-build-editor')
    if (editor) (editor as HTMLElement).style.display = ''
  })

  const input = page.locator('#pl-build-prompt')
  await input.click()
  await input.pressSequentially('hatsune_mi', { delay: 10 })
  const dropdown = page.locator('.caption-autocomplete-dropdown')
  await expect(dropdown).toBeVisible({ timeout: 5_000 })

  await input.press('ArrowRight')
  await expect(page.locator('.cap-ac-info')).toBeVisible()
  expect(await popoverText(page)).toContain('hatsune_miku')

  // Layered dismissal: the first Escape takes the popover, the second the
  // dropdown. Collapsing both at once would lose the suggestion list after a
  // glance at the details.
  await input.press('Escape')
  await expect(page.locator('.cap-ac-info')).toBeHidden()
  await expect(dropdown).toBeVisible()
  await input.press('Escape')
  await expect(dropdown).toBeHidden()

  // Nothing was committed by looking.
  await expect(input).toHaveValue('hatsune_mi')
})

test('opening the details from the mouse does not commit the suggestion', async ({ page }) => {
  await routeInfo(page, INFO)
  await page.route('**/api/tags/suggest*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        suggestions: [
          { tag: 'hatsune_miku', count: 1_284_000, source: 'vocab', category: 'character', zh: '初音未来' },
        ],
      }),
    }))
  await openApp(page)
  await page.evaluate(() => window.App.switchView('promptlab'))
  await page.locator('.promptlab-tab[data-mode="build"]').click()
  await page.evaluate(() => {
    const editor = document.getElementById('pl-build-editor')
    if (editor) (editor as HTMLElement).style.display = ''
  })

  const input = page.locator('#pl-build-prompt')
  await input.click()
  await input.pressSequentially('hatsune_mi', { delay: 10 })
  await expect(page.locator('.caption-autocomplete-dropdown')).toBeVisible({ timeout: 5_000 })

  await page.locator('.caption-autocomplete-item .cap-ac-info-btn').first().click()
  await expect(page.locator('.cap-ac-info')).toBeVisible()
  await expect(input).toHaveValue('hatsune_mi')
})
