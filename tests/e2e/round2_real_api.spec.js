// Round-2 verification. Live VLM calls need AIHUBMIX_API_KEY in the
// environment; never commit a provider key.
const { chromium } = require('playwright');

const BASE = process.env.BASE_URL || 'http://127.0.0.1:8488';
const AIHUBMIX_KEY = String(process.env.AIHUBMIX_API_KEY || '').trim();
const AIHUBMIX_NO_V1 = process.env.AIHUBMIX_ENDPOINT || 'https://aihubmix.com';
const VLM_MODEL = process.env.AIHUBMIX_MODEL || 'gemini-3.1-flash-lite';

async function main() {
    const browser = await chromium.launch({ headless: true });
    const ctx = await browser.newContext({ viewport: { width: 1366, height: 800 } });
    const page = await ctx.newPage();

    const consoleErrors = [];
    page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + e.message));
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push('console.error: ' + m.text()); });

    await page.goto(BASE);
    await page.waitForLoadState('networkidle').catch(() => {});
    await page.waitForTimeout(800);

    // --- (7) refresh button in navbar next to ❓ ---
    const navBtnPos = await page.evaluate(() => {
        const btn = document.getElementById('btn-refresh-ui');
        const help = document.getElementById('btn-help');
        if (!btn || !help) return null;
        const r1 = btn.getBoundingClientRect();
        const r2 = help.getBoundingClientRect();
        return {
            visible: btn.offsetParent !== null,
            adjacent: Math.abs(r1.left - r2.right) < 80,
            inNavActions: !!btn.closest('.nav-actions'),
        };
    });
    if (!navBtnPos || !navBtnPos.visible) throw new Error('btn-refresh-ui not visible in navbar');
    if (!navBtnPos.inNavActions) throw new Error('btn-refresh-ui not in nav-actions row');
    if (!navBtnPos.adjacent) throw new Error('btn-refresh-ui not adjacent to btn-help');
    console.log('[ok] (7) refresh button visible in navbar adjacent to ❓');

    // --- (8) refresh button NOT in guide modal ---
    await page.click('#btn-help');
    await page.waitForSelector('.guide-overlay.visible', { timeout: 5000 });
    const inGuide = await page.evaluate(() => Boolean(document.querySelector('.guide-modal-refresh-i18n')));
    if (inGuide) throw new Error('refresh button must NOT be inside Help modal anymore');
    console.log('[ok] (8) refresh button removed from Help modal');
    await page.click('.guide-modal-close');
    await page.waitForTimeout(200);

    // --- (1+2) live VLM save + Fetch Models. Optional: needs AIHUBMIX_API_KEY ---
    if (!AIHUBMIX_KEY) {
        console.log('[skip] (1+2) live VLM checks need AIHUBMIX_API_KEY');
    } else {
        const saveResp = await page.request.post(BASE + '/api/vlm/settings', {
            data: {
                provider: 'openai_compat',
                endpoint: AIHUBMIX_NO_V1,
                api_key: AIHUBMIX_KEY,
                model: VLM_MODEL,
                // Clear leftover state from prior runs that would otherwise nuke
                // network connectivity (bogus proxy / 1s timeout).
                http_proxy: '',
                https_proxy: '',
                socks_proxy: '',
                timeout_seconds: 60.0,
                concurrent_requests: 2,
                max_retries: 3,
                retry_delay_seconds: 2.0,
            },
        });
        if (!saveResp.ok()) throw new Error('settings save failed: HTTP ' + saveResp.status());

        const fetchResp = await page.request.post(BASE + '/api/vlm/models', { data: {} });
        const fetchData = await fetchResp.json();
        console.log('[info] /api/vlm/models →', JSON.stringify(fetchData).slice(0, 200));
        if (!fetchResp.ok()) throw new Error('models fetch HTTP ' + fetchResp.status());
        const models = Array.isArray(fetchData?.models) ? fetchData.models : [];
        if (models.length === 0) {
            throw new Error('Fetch Available Models returned 0 results — endpoint normalization or auth broken');
        }
        console.log('[ok] (1+2) endpoint normalized + Fetch Models returned', models.length, 'entries');
    }

    // --- (3) NL source choices exist ---
    await page.click('#btn-tag');
    await page.waitForSelector('#tag-modal.visible', { timeout: 5000 });
    await page.waitForTimeout(800);
    await page.click('#tag-modal .tagger-tab[data-tagger-tab="nl"]');
    await page.waitForTimeout(300);
    const sourceChoices = await page.evaluate(() => {
        const values = Array.from(document.querySelectorAll('#tag-model-choice-list .tagger-model-choice'))
            .filter((el) => el.offsetParent !== null)
            .map((el) => el.dataset.modelValue);
        return {
            hasHiddenStateInputs: document.querySelectorAll('input[name="tagger-nl-source"]').length === 2,
            values,
        };
    });
    if (!sourceChoices.hasHiddenStateInputs) throw new Error('NL source state inputs missing');
    if (!sourceChoices.values.includes('vlm') || !sourceChoices.values.some((v) => String(v).includes('toriigate'))) {
        throw new Error('NL source cards missing VLM or ToriiGate: ' + JSON.stringify(sourceChoices.values));
    }
    const duplicateNlButtonsHidden = await page.evaluate(() => {
        const localActionsHidden = Array.from(document.querySelectorAll('#tag-modal .tagger-local-action'))
            .every((el) => el.offsetParent === null);
        const utilityStripHidden = !document.querySelector('#tag-modal .tagger-utility-strip')?.offsetParent;
        return { localActionsHidden, utilityStripHidden };
    });
    if (!duplicateNlButtonsHidden.localActionsHidden) throw new Error('Import/Export tag buttons should hide on NL tab');
    if (!duplicateNlButtonsHidden.utilityStripHidden) throw new Error('Duplicate VLM utility strip should not be visible in NL tab');
    console.log('[ok] (3) NL source cards (ToriiGate / VLM API) present');

    // --- (4) VLM API card hides ToriiGate + sets dropdown=vlm ---
    await page.click('#tag-model-choice-list .tagger-model-choice[data-model-value="vlm"]');
    await page.waitForTimeout(300);
    const afterVlm = await page.evaluate(() => {
        const torii = document.getElementById('tagger-nl-toriigate-card');
        const dropdown = document.getElementById('tag-model-select');
        const banner = document.getElementById('vlm-mode-banner');
        return {
            toriiHidden: !torii || torii.offsetParent === null,
            dropdownValue: dropdown ? dropdown.value : null,
            bannerVisible: !!(banner && banner.offsetParent !== null),
        };
    });
    if (!afterVlm.toriiHidden) throw new Error('ToriiGate card should hide when VLM API selected');
    if (afterVlm.dropdownValue !== 'vlm') throw new Error('dropdown should=vlm, got ' + afterVlm.dropdownValue);
    const compactVlm = await page.evaluate(() => {
        const workflow = document.getElementById('tagger-nl-workflow-card');
        const settings = document.getElementById('btn-vlm-banner-settings');
        const utility = document.querySelector('#tag-modal .tagger-utility-strip');
        return {
            workflowHidden: !workflow || workflow.offsetParent === null,
            settingsVisible: !!(settings && settings.offsetParent !== null),
            utilityHidden: !utility || utility.offsetParent === null,
        };
    });
    if (afterVlm.bannerVisible) throw new Error('Legacy VLM banner should stay hidden in compact NL flow');
    if (!compactVlm.workflowHidden || !compactVlm.settingsVisible || !compactVlm.utilityHidden) {
        throw new Error('compact VLM flow invalid: ' + JSON.stringify(compactVlm));
    }
    console.log('[ok] (4) VLM API card hides ToriiGate, sets dropdown=vlm, shows compact workflow');

    // --- (5) Start Tagging in VLM mode does NOT hit /api/tags/start ---
    let tagsStartCalls = 0;
    let vlmBatchCalls = 0;
    page.on('request', (req) => {
        const u = req.url();
        if (u.includes('/api/tags/start') && req.method() === 'POST') tagsStartCalls++;
        if (u.includes('/api/vlm/caption-batch') && req.method() === 'POST') vlmBatchCalls++;
    });
    await page.click('#btn-start-tag');
    await page.waitForTimeout(1500);
    if (tagsStartCalls > 0) {
        throw new Error('Start Tagging while VLM hit /api/tags/start ' + tagsStartCalls + ' times — intercept broken');
    }
    console.log('[ok] (5) Start Tagging in VLM mode bypasses /api/tags/start');
    console.log('[info]    /api/vlm/caption-batch fired:', vlmBatchCalls, 'time(s)');

    // toggle back to ToriiGate
    await page.click('#tag-model-choice-list .tagger-model-choice[data-model-value="toriigate-0.5"]');
    await page.waitForTimeout(300);
    const afterTorii = await page.evaluate(() => {
        const torii = document.getElementById('tagger-nl-toriigate-card');
        const dropdown = document.getElementById('tag-model-select');
        return {
            toriiVisible: !!(torii && torii.offsetParent !== null),
            dropdownValue: dropdown ? dropdown.value : null,
        };
    });
    if (!afterTorii.toriiVisible) throw new Error('ToriiGate card should reappear');
    if (!String(afterTorii.dropdownValue || '').includes('toriigate')) {
        throw new Error('dropdown should be toriigate-*, got ' + afterTorii.dropdownValue);
    }
    console.log('[ok] toggle back to ToriiGate restores card + dropdown');

    // --- (6) Aesthetic tab fetches /api/aesthetic/status ---
    let aestheticStatusCalls = 0;
    page.on('request', (req) => {
        if (req.url().includes('/api/aesthetic/status')) aestheticStatusCalls++;
    });
    await page.click('#tag-modal .tagger-tab[data-tagger-tab="aesthetic"]');
    await page.waitForTimeout(1500);
    if (aestheticStatusCalls === 0) {
        throw new Error('Aesthetic tab should call /api/aesthetic/status, did not');
    }
    console.log('[ok] (6) Aesthetic tab calls /api/aesthetic/status (' + aestheticStatusCalls + ' calls)');

    const aestheticState = await page.evaluate(() => {
        const startBtn = document.getElementById('btn-tagger-aesthetic-start');
        const setupBtn = document.getElementById('btn-tagger-aesthetic-setup');
        const titleEl = document.getElementById('tagger-aesthetic-status-title');
        return {
            startDisabled: startBtn ? startBtn.disabled : null,
            setupVisible: setupBtn ? !!setupBtn.offsetParent : null,
            titleText: titleEl ? titleEl.textContent.trim().slice(0, 100) : null,
        };
    });
    console.log('[info] aesthetic state:', JSON.stringify(aestheticState));
    if (aestheticState.startDisabled === false && aestheticState.setupVisible === true) {
        throw new Error('Aesthetic state inconsistent: start enabled AND setup CTA visible');
    }
    if (aestheticState.startDisabled === true && aestheticState.setupVisible === false) {
        throw new Error('Aesthetic state inconsistent: start disabled AND setup CTA hidden — no path forward');
    }
    console.log('[ok] (6) Aesthetic state consistent with backend status');

    if (consoleErrors.length) {
        console.log('[warn] console errors:');
        for (const e of consoleErrors) console.log('  -', e);
    }

    await browser.close();
    console.log('--- ALL ROUND-2 CHECKS PASSED ---');
}

main().catch((err) => {
    console.error('ROUND-2 TEST FAILED:', err.message);
    process.exit(1);
});
