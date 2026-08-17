import fs from 'node:fs'
import path from 'node:path'
import { expect, test, type Page } from '../fixtures/click-ledger'

/**
 * Privacy Tools must not silently eat the user's generation data — client side.
 *
 * The queue runs window.ObfuscateEngine by default, so the backend fix in
 * 8982d08 was invisible to the people who actually use the feature: the browser
 * engine gated metadata on big_tomato and read PNG chunks only, so a JPEG or
 * WebP source, and all of small_tomato, still lost the prompt.
 *
 * Both directions emit a PNG and a PNG can always carry tEXt, so these tests
 * assert what the user cares about: after protect -> restore through the client
 * engine, the prompt is still there and identical, for every source container
 * and both compat modes. The backend twin lives in
 * backend/tests/test_obfuscation_client_engine_parity.py and pins the same
 * fixtures to the same harvested chunks, so the two paths cannot drift apart.
 */

const FIXTURE_DIR = path.resolve(__dirname, '..', 'fixtures', 'obfuscation')

// Kept character-for-character in sync with the fixture generator and with
// backend/tests/test_obfuscation_metadata_roundtrip.py.
const A1111_PARAMETERS = [
  'a girl standing in the rain, masterpiece, best quality',
  'Negative prompt: lowres, bad anatomy',
  'Steps: 28, Sampler: DPM++ 2M Karras, CFG scale: 7, Seed: 123456789, Size: 512x768, Model: someModel',
].join('\n')

const SOURCES = {
  png: { file: 'sd-metadata-source.png', mimeType: 'image/png' },
  jpeg: { file: 'sd-metadata-source.jpg', mimeType: 'image/jpeg' },
  webp: { file: 'sd-metadata-source.webp', mimeType: 'image/webp' },
} as const

type SourceKind = keyof typeof SOURCES

const COMPAT_MODES = ['big_tomato', 'small_tomato'] as const

function readFixture(name: string): string {
  return fs.readFileSync(path.join(FIXTURE_DIR, name)).toString('base64')
}

interface RoundTripCase {
  base64: string
  mimeType: string
  compatMode: string
  password: string
  preserveMetadata: boolean
  legacyPngInfo: boolean
}

interface RoundTripResult {
  error?: string
  protectedChunks: [string, string][]
  restoredChunks: [string, string][]
  sourcePixels: string
  restoredPixels: string
  protectedSize: { width: number, height: number }
  restoredSize: { width: number, height: number }
}

/**
 * Drive one protect -> restore cycle entirely inside the page, through the same
 * public ObfuscateEngine entry points the queue uses.
 */
async function roundTripInBrowser(page: Page, testCase: RoundTripCase): Promise<RoundTripResult> {
  return await page.evaluate(async (input: RoundTripCase) => {
    const engine = (window as any).ObfuscateEngine
    const internals = engine.__internals

    const toBlob = (base64: string, mimeType: string) => {
      const raw = atob(base64)
      const bytes = new Uint8Array(raw.length)
      for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i)
      return new Blob([bytes], { type: mimeType })
    }

    const readChunks = async (blob: Blob): Promise<[string, string][]> =>
      internals.extractPngTextChunksFromBytes(new Uint8Array(await blob.arrayBuffer()))

    // Base64 of the decoded RGBA bytes, so pixel identity is comparable in Node.
    const pixelDigest = async (blob: Blob): Promise<string> => {
      const url = URL.createObjectURL(blob)
      try {
        const image = await new Promise<HTMLImageElement>((resolve, reject) => {
          const element = new Image()
          element.onload = () => resolve(element)
          element.onerror = () => reject(new Error('failed to decode image'))
          element.src = url
        })
        const canvas = document.createElement('canvas')
        canvas.width = image.naturalWidth
        canvas.height = image.naturalHeight
        const context = canvas.getContext('2d')!
        context.drawImage(image, 0, 0)
        const data = context.getImageData(0, 0, canvas.width, canvas.height).data
        let binary = ''
        for (let i = 0; i < data.length; i++) binary += String.fromCharCode(data[i])
        return btoa(binary)
      } finally {
        URL.revokeObjectURL(url)
      }
    }

    const options = {
      compatMode: input.compatMode,
      preserveMetadata: input.preserveMetadata,
      legacyPngInfo: input.legacyPngInfo,
    }

    try {
      const sourceBlob = toBlob(input.base64, input.mimeType)
      const sourcePixels = await pixelDigest(sourceBlob)

      const protectedResult = await engine.encode(sourceBlob, input.password, options)
      const restoredResult = await engine.decode(protectedResult.blob, input.password, options)

      return {
        protectedChunks: await readChunks(protectedResult.blob),
        restoredChunks: await readChunks(restoredResult.blob),
        sourcePixels,
        restoredPixels: await pixelDigest(restoredResult.blob),
        protectedSize: { width: protectedResult.width, height: protectedResult.height },
        restoredSize: { width: restoredResult.width, height: restoredResult.height },
      }
    } catch (error) {
      return {
        error: String((error as Error)?.message || error),
        protectedChunks: [],
        restoredChunks: [],
        sourcePixels: '',
        restoredPixels: '',
        protectedSize: { width: 0, height: 0 },
        restoredSize: { width: 0, height: 0 },
      }
    }
  }, testCase)
}

function chunkValue(chunks: [string, string][], key: string): string | undefined {
  return chunks.find(([chunkKey]) => chunkKey === key)?.[1]
}

test.describe('Privacy Tools client engine — the prompt survives protect/restore', () => {
  for (const sourceKind of Object.keys(SOURCES) as SourceKind[]) {
    for (const compatMode of COMPAT_MODES) {
      // big_tomato honours the password digits; small_tomato pins step=1 and no
      // padding, so a password there must make no difference either way.
      const password = compatMode === 'big_tomato' ? '0512' : ''

      test(`${sourceKind} source in ${compatMode} keeps the generation parameters`, async ({ page }) => {
        await page.goto('/')
        await expect(page.locator('#view-gallery')).toBeVisible()

        const result = await roundTripInBrowser(page, {
          base64: readFixture(SOURCES[sourceKind].file),
          mimeType: SOURCES[sourceKind].mimeType,
          compatMode,
          password,
          preserveMetadata: true,
          legacyPngInfo: false,
        })

        expect(result.error).toBeUndefined()

        // The user-visible outcome: the prompt comes back byte-identical.
        expect(chunkValue(result.restoredChunks, 'parameters')).toBe(A1111_PARAMETERS)

        // The protected copy is the one that gets shared, so the carried value
        // must be the encrypted form, never the readable prompt.
        const carried = chunkValue(result.protectedChunks, 'parameters')
        expect(carried).toBeTruthy()
        expect(carried).not.toBe(A1111_PARAMETERS)
        expect(carried).not.toContain('a girl standing in the rain')

        // Pixels still round trip exactly, against the source as the browser
        // decoded it (JPEG/WebP are lossy, so that is the only honest baseline).
        expect(result.restoredPixels).toBe(result.sourcePixels)
        expect(result.restoredSize).toEqual({ width: 24, height: 24 })
      })
    }
  }

  test('the legacy PNG Info algorithm round trips in small_tomato too', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('#view-gallery')).toBeVisible()

    // The backend honours legacy_pnginfo in both modes after 8982d08, so the
    // client must too — otherwise a file protected through the queue could not
    // be restored through the file-path endpoint with the same settings.
    const result = await roundTripInBrowser(page, {
      base64: readFixture(SOURCES.jpeg.file),
      mimeType: SOURCES.jpeg.mimeType,
      compatMode: 'small_tomato',
      password: '',
      preserveMetadata: true,
      legacyPngInfo: true,
    })

    expect(result.error).toBeUndefined()
    expect(chunkValue(result.restoredChunks, 'parameters')).toBe(A1111_PARAMETERS)
    expect(chunkValue(result.protectedChunks, 'parameters')).not.toBe(A1111_PARAMETERS)
  })

  test('opting out still strips everything, in both compat modes', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('#view-gallery')).toBeVisible()

    for (const compatMode of COMPAT_MODES) {
      const result = await roundTripInBrowser(page, {
        base64: readFixture(SOURCES.png.file),
        mimeType: SOURCES.png.mimeType,
        compatMode,
        password: '',
        preserveMetadata: false,
        legacyPngInfo: false,
      })

      expect(result.error).toBeUndefined()
      expect(result.protectedChunks, `${compatMode} carried chunks despite the opt-out`).toEqual([])
      expect(result.restoredChunks).toEqual([])
      expect(result.restoredPixels).toBe(result.sourcePixels)
    }
  })

  test('a source with no generation data carries nothing and still round trips', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('#view-gallery')).toBeVisible()

    const result = await roundTripInBrowser(page, {
      base64: readFixture('no-metadata-source.png'),
      mimeType: 'image/png',
      compatMode: 'small_tomato',
      password: '',
      preserveMetadata: true,
      legacyPngInfo: false,
    })

    expect(result.error).toBeUndefined()
    expect(result.protectedChunks).toEqual([])
    expect(result.restoredPixels).toBe(result.sourcePixels)
  })

  test('harvested chunks match the backend byte-for-byte for every container', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('#view-gallery')).toBeVisible()

    // The values on the right are what backend/obfuscation.py's
    // extract_source_text_chunks_from_bytes returns for the very same fixture
    // files; the backend twin test asserts that side.
    const harvested = await page.evaluate(async (inputs: { name: string, base64: string }[]) => {
      const internals = (window as any).ObfuscateEngine.__internals
      const output: Record<string, [string, string][]> = {}
      for (const input of inputs) {
        const raw = atob(input.base64)
        const bytes = new Uint8Array(raw.length)
        for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i)
        output[input.name] = internals.extractSourceTextChunksFromBytes(bytes)
      }
      return output
    }, [
      ...(Object.keys(SOURCES) as SourceKind[]).map((kind) => ({
        name: kind,
        base64: readFixture(SOURCES[kind].file),
      })),
      { name: 'bare', base64: readFixture('no-metadata-source.png') },
    ])

    expect(harvested).toEqual({
      png: [['parameters', A1111_PARAMETERS]],
      jpeg: [['parameters', A1111_PARAMETERS]],
      webp: [['parameters', A1111_PARAMETERS]],
      bare: [],
    })
  })
})

test.describe('Privacy Tools UI — the small-tomato download limitation is stated up front', () => {
  async function openObfuscationPanel(page: Page): Promise<void> {
    await page.goto('/')
    await expect(page.locator('#view-gallery')).toBeVisible()
    await page.waitForFunction(() => (window as any).ImageObfuscator?._eventsBound === true)
    await page.locator('#nav-tab-reader').click()
    await page.locator('#reader-tool-tab-obfuscation').click()
    await expect(page.locator('#reader-tool-panel-obfuscation')).toHaveClass(/active/)
  }

  test('choosing Simple mode reveals the caveat without disabling Keep metadata', async ({ page }) => {
    await openObfuscationPanel(page)

    const metadata = page.locator('#obfuscate-preserve-metadata')
    const legacy = page.locator('#obfuscate-use-legacy-pnginfo')
    const note = page.locator('#obfuscate-metadata-download-note')

    // Standard mode: the caveat does not apply, so it stays out of the way.
    await page.locator('#obfuscate-settings-toggle').click()
    await expect(page.locator('#obfuscate-advanced-settings')).toBeVisible()
    await expect(metadata).toBeEnabled()
    await expect(metadata).toBeChecked()
    await expect(note).toBeHidden()

    await page.locator('#obfuscate-compat-mode').selectOption('small_tomato')

    // The checkbox now delivers in Simple mode, so it must not be taken away…
    await expect(metadata).toBeEnabled()
    await expect(metadata).toBeChecked()
    await expect(legacy).toBeEnabled()
    // …but the user is told, before downloading anything, that the Simple-mode
    // download is a re-encoded .jpg and cannot carry PNG Info.
    await expect(note).toBeVisible()
    await expect(note).toContainText('.jpg')
    await expect(note).toContainText('JPEG')

    await page.locator('#obfuscate-compat-mode').selectOption('big_tomato')
    await expect(note).toBeHidden()
  })

  test('the mode help keeps describing the selected mode', async ({ page }) => {
    await openObfuscationPanel(page)
    await page.locator('#obfuscate-settings-toggle').click()

    const help = page.locator('#obfuscate-compat-help')
    const standardHelp = (await help.textContent())?.trim()
    expect(standardHelp).toBeTruthy()

    await page.locator('#obfuscate-compat-mode').selectOption('small_tomato')
    await expect(help).not.toHaveText(standardHelp!)

    // ui-refresh's MutationObserver re-applies the markup's static data-i18n key
    // on any DOM change inside #app, which used to revert this line to the
    // Standard-mode description a moment after the user picked Simple mode.
    await page.evaluate(() => document.getElementById('obfuscate-queue')?.appendChild(document.createElement('span')))
    await page.waitForTimeout(600)
    await expect(help).not.toHaveText(standardHelp!)
  })

  test('the caveat is on screen even when Advanced settings was never opened', async ({ page }) => {
    await openObfuscationPanel(page)
    await expect(page.locator('#obfuscate-advanced-settings')).toBeHidden()

    await page.locator('#obfuscate-compat-mode').selectOption('small_tomato')

    await expect(page.locator('#obfuscate-advanced-settings')).toBeVisible()
    await expect(page.locator('#obfuscate-metadata-download-note')).toBeVisible()
  })

  for (const viewport of [
    { width: 1366, height: 768 },
    { width: 1920, height: 1080 },
    { width: 2560, height: 1440 },
  ]) {
    test(`the caveat fits ${viewport.width}x${viewport.height} without console errors or overflow`, async ({ page }) => {
      const consoleErrors: string[] = []
      page.on('console', (message) => {
        if (message.type() === 'error') consoleErrors.push(message.text())
      })
      page.on('pageerror', (error) => consoleErrors.push(String(error.message)))

      await page.setViewportSize(viewport)
      await openObfuscationPanel(page)
      await page.locator('#obfuscate-compat-mode').selectOption('small_tomato')

      const note = page.locator('#obfuscate-metadata-download-note')
      await expect(note).toBeVisible()

      const layout = await page.evaluate(() => {
        const noteEl = document.getElementById('obfuscate-metadata-download-note')!
        const rowEl = document.getElementById('obfuscate-metadata-row')!
        const legacyEl = document.getElementById('obfuscate-legacy-row')!
        const noteBox = noteEl.getBoundingClientRect()
        return {
          documentOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          clipped: noteEl.scrollHeight > noteEl.clientHeight + 1,
          insideViewport: noteBox.right <= window.innerWidth + 1 && noteBox.left >= -1,
          // The note has to sit between the checkbox it qualifies and the next row.
          overlapsRow: noteBox.top < rowEl.getBoundingClientRect().bottom - 1,
          overlapsLegacy: noteBox.bottom > legacyEl.getBoundingClientRect().top + 1,
        }
      })

      expect(layout.documentOverflow).toBeLessThanOrEqual(0)
      expect(layout.clipped).toBe(false)
      expect(layout.insideViewport).toBe(true)
      expect(layout.overlapsRow).toBe(false)
      expect(layout.overlapsLegacy).toBe(false)
      expect(consoleErrors).toEqual([])
    })
  }
})
