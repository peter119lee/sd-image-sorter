import { expect, test } from '../fixtures/click-ledger'

/**
 * B2 cross-module contract:
 *   sidebar filter-summary chip "模型 / Checkpoints"
 *     → filter modal opens
 *     → checkpoint (base model) section is focused / scrolled into view
 *     → selecting a checkpoint and applying narrows the gallery.
 *
 * Guards against the historical per-layer green / chain-broken pattern
 * (date-filter 10835c0 lesson).
 */

test.describe.configure({ mode: 'serial' })
test.use({ viewport: { width: 1600, height: 900 } })

async function enterGalleryLibrary(page: import('@playwright/test').Page) {
  await page.goto('/')
  await page.waitForLoadState('domcontentloaded')
  // Skip aurora entry when present
  const enter = page.locator('#entry-fn-gallery')
  if (await enter.isVisible().catch(() => false)) {
    await enter.click()
  }
  await page.evaluate(() => {
    if (window.App?.switchView) window.App.switchView('gallery')
  })
  const filtersToggle = page.locator('[data-sidebar-section="filters"] .sidebar-section-toggle')
  if (await filtersToggle.getAttribute('aria-expanded') === 'false') {
    await filtersToggle.click()
  }
  await page.locator('#gallery-scope-select').selectOption('library').catch(async () => {
    await page.evaluate(() => {
      const el = document.querySelector('#gallery-scope-select') as HTMLSelectElement | null
      if (!el) return
      el.value = 'library'
      el.dispatchEvent(new Event('change', { bubbles: true }))
    })
  })
  await page.waitForTimeout(800)
}

test('sidebar checkpoints chip opens filter modal focused on base-model list', async ({ page }) => {
  await enterGalleryLibrary(page)

  const chip = page.locator('#filter-summary .summary-row[data-filter-field="checkpoints"]')
  await expect(chip).toBeVisible()
  await chip.click()

  const modal = page.locator('#filter-modal')
  await expect(modal).toBeVisible()
  await expect(modal).toHaveClass(/visible/)

  // Checkpoint list exists and its section received the focus highlight class
  // (or is at least in the visible scroll region of the modal).
  const cpList = page.locator('#modal-checkpoint-list')
  await expect(cpList).toBeVisible()

  await expect.poll(async () => {
    return page.evaluate(() => {
      const list = document.getElementById('modal-checkpoint-list')
      const section = list?.closest('.filter-section, .filter-panel')
      return Boolean(
        section?.classList.contains('filter-focus-target')
        || section?.getAttribute('data-filter-focused') === '1'
      )
    })
  }).toBe(true)

  await expect(page.locator('#modal-checkpoint-list')).toBeInViewport()
})

test('selecting a checkpoint in the modal applies and updates sidebar summary', async ({ page }) => {
  await enterGalleryLibrary(page)

  await page.locator('#filter-summary .summary-row[data-filter-field="checkpoints"]').click()
  await expect(page.locator('#filter-modal')).toBeVisible()

  // Empty e2e DB has no facet rows — inject two so "all checked == no filter"
  // does not swallow a single intentional selection (see _collectAllAwareCheckboxValues).
  const chosen = await page.evaluate(() => {
    const list = document.getElementById('modal-checkpoint-list')
    if (!list) return ''
    const boxes = [...list.querySelectorAll('input[type="checkbox"]')] as HTMLInputElement[]
    if (boxes.length >= 2) {
      boxes.forEach((b) => { b.checked = false })
      boxes[0].checked = true
      return boxes[0].value
    }
    const value = 'e2e_base_model_ckpt'
    list.innerHTML = `
      <label class="checkbox-label">
        <input type="checkbox" value="${value}" checked>
        <span class="checkbox-custom"></span>
        <span class="checkbox-text">${value}</span>
        <span class="checkbox-count">1</span>
      </label>
      <label class="checkbox-label">
        <input type="checkbox" value="e2e_other_ckpt">
        <span class="checkbox-custom"></span>
        <span class="checkbox-text">e2e_other_ckpt</span>
        <span class="checkbox-count">1</span>
      </label>`
    return value
  })
  expect(chosen).toBeTruthy()

  await page.locator('#btn-apply-modal-filters').click()
  await expect(page.locator('#filter-modal')).toBeHidden({ timeout: 10000 })

  await expect.poll(async () => {
    return page.evaluate(() => {
      // @ts-expect-error classic global
      const filters = window.App?.AppState?.filters || window.AppState?.filters
      return (filters?.checkpoints || []).join('|')
    })
  }).toContain(chosen)

  await expect(page.locator('#summary-checkpoints')).not.toHaveText(/^(None|无|—|-)$/i)
  await expect(page.locator('#filter-summary .summary-row[data-filter-field="checkpoints"]')).toHaveClass(/is-active/)
})
