/**
 * Function catalog — the "what can this app actually do?" listing.
 *
 * Owner FB (2026-07-07): 自由模式 and 全部工具 were the same thing; one of
 * them becomes a catalog that lists EVERY function with a one-line usage so
 * features buried in sub-tabs (隐私处理 lives inside the Reader) are finally
 * discoverable from the main page.
 *
 * Rows are built with DOM APIs (no innerHTML) and re-built on every open so
 * they always render in the current language.
 */
(function () {
    'use strict';

    function t(key, fallback) {
        const value = window.I18n && window.I18n.t ? window.I18n.t(key) : null;
        return (value && value !== key) ? value : (fallback || key);
    }

    function goView(view) {
        if (window.EntryPage && typeof window.EntryPage.hide === 'function') window.EntryPage.hide();
        const tab = document.getElementById(`nav-tab-${view}`);
        if (tab) tab.click();
    }

    /** Click a lazily-bound control, retrying a few frames while it wires up. */
    function clickWhenReady(id, attempts) {
        const node = document.getElementById(id);
        if (node) {
            node.click();
            return;
        }
        if (attempts > 0) {
            window.requestAnimationFrame(() => clickWhenReady(id, attempts - 1));
        }
    }

    function openReaderTool(tool) {
        goView('reader');
        window.requestAnimationFrame(() => clickWhenReady(`reader-tool-tab-${tool}`, 10));
    }

    function openSettingsTab(tabName) {
        const opener = document.getElementById('btn-open-model-manager');
        if (opener) opener.click();
        window.requestAnimationFrame(() => {
            if (window.SettingsTabs && typeof window.SettingsTabs.activate === 'function') {
                window.SettingsTabs.activate(tabName);
            }
        });
    }

    function enterMission(missionKey, entryTileId) {
        // Entry mission tiles already carry the full behavior (mission mode +
        // landing view + any follow-up click); proxy through them.
        const tile = document.getElementById(entryTileId);
        if (tile) {
            tile.click();
            return;
        }
        if (window.NavMissions) window.NavMissions.enter(missionKey);
    }

    // Names reuse the nav/entry keys the tabs and tiles already translate
    // with; only the usage lines are catalog-specific.
    const GROUPS = [
        {
            titleKey: 'catalog.groupCore', titleFallback: 'Core views',
            items: [
                { icon: '🖼️', nameKey: 'nav.gallery', name: 'Gallery', descKey: 'catalog.gallery', desc: 'Browse, filter, pick, and batch-process every indexed image', run: () => goView('gallery') },
                { icon: '📖', nameKey: 'nav.reader', name: 'Reader', descKey: 'catalog.reader', desc: 'Drop an image to read its prompt, model, and generation parameters', run: () => goView('reader') },
                { icon: '🗂️', nameKey: 'nav.sorting', name: 'Organize', descKey: 'catalog.sorting', desc: 'Auto-separate by filters, then hand-sort a batch with WASD keys', run: () => goView('sorting') },
                { icon: '🔳', nameKey: 'nav.censor', name: 'Censor Edit', descKey: 'catalog.censor', desc: 'Brush/pen censoring, AI detection, review conveyor, safe export', run: () => goView('censor') },
                { icon: '🔎', nameKey: 'nav.similar', name: 'Find Similar', descKey: 'catalog.similar', desc: 'Search by image and surface near-duplicates with CLIP', run: () => goView('similar') },
            ],
        },
        {
            titleKey: 'catalog.groupPipelines', titleFallback: 'Pipelines',
            items: [
                { icon: '🎯', nameKey: 'entry.missionLoraTitle', name: 'LoRA Dataset', descKey: 'catalog.lora', desc: 'Pick → tag → caption → export a kohya-ready training set', run: () => enterMission('lora', 'entry-mission-lora') },
                { icon: '📤', nameKey: 'entry.missionPixivTitle', name: 'Pixiv Set Publishing', descKey: 'catalog.pixiv', desc: 'Pick → censor → rename → export a publishable image set', run: () => enterMission('pixiv', 'entry-mission-pixiv') },
                { icon: '🧺', nameKey: 'entry.missionOrganizeTitle', name: 'Batch Organize', descKey: 'catalog.organize', desc: 'Move a mountain of images into clean folders, with undo', run: () => enterMission('organize', 'entry-mission-organize') },
            ],
        },
        {
            titleKey: 'catalog.groupTools', titleFallback: 'Tools',
            items: [
                { icon: '🛡️', nameKey: 'tools.obfuscation', name: 'Privacy Tools', descKey: 'catalog.obfuscation', desc: 'Obfuscate images for upload sites and restore them back (inside Reader)', run: () => openReaderTool('obfuscation') },
                { icon: '🧹', nameKey: 'dup.navTitle', name: 'Duplicate Cleanup', descKey: 'catalog.dup', desc: 'Scan the whole library for duplicates and clean them in one pass', run: () => { goView('gallery'); window.requestAnimationFrame(() => clickWhenReady('nav-tools-dup-cleaner', 10)); } },
                { icon: '#i-wand', nameKey: 'nav.reverse', name: 'Reverse Prompt', descKey: 'catalog.reverse', desc: 'Drop one image to recover its prompt: the record inside the file first, AI inference only when there is none', run: () => goView('reverse') },
                { icon: '🧪', nameKey: 'nav.promptlab', name: 'Prompt Helper', descKey: 'catalog.promptlab', desc: 'Build prompts from your library: weights, templates, negatives', run: () => goView('promptlab') },
                { icon: '🖌️', nameKey: 'nav.artist', name: 'Style Finder', descKey: 'catalog.artist', desc: 'Identify artists with a similar style (experimental)', run: () => goView('artist') },
                { icon: '📦', nameKey: 'entry.tileModels', name: 'Model Center', descKey: 'catalog.models', desc: 'Download and manage the AI models every feature runs on', run: () => openSettingsTab('models') },
                { icon: '⚙️', nameKey: 'settings.tabGeneral', name: 'Settings', descKey: 'catalog.settings', desc: 'Sound, entry page, UI scale, language, disk cache, updates', run: () => openSettingsTab('general') },
            ],
        },
    ];

    function buildRow(item) {
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'catalog-item';

        const icon = document.createElement('span');
        icon.className = 'catalog-icon';
        icon.setAttribute('aria-hidden', 'true');
        if (String(item.icon || '').startsWith('#i-')) {
            // Graphite entries name a sprite symbol. The legacy emoji rows below
            // still take the textContent path and are retired separately.
            const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('class', 'icon');
            svg.setAttribute('aria-hidden', 'true');
            const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
            use.setAttribute('href', item.icon);
            svg.appendChild(use);
            icon.appendChild(svg);
        } else {
            icon.textContent = item.icon;
        }

        const copy = document.createElement('span');
        copy.className = 'catalog-copy';
        const name = document.createElement('span');
        name.className = 'catalog-name';
        name.textContent = t(item.nameKey, item.name);
        const desc = document.createElement('span');
        desc.className = 'catalog-desc';
        desc.textContent = t(item.descKey, item.desc);
        copy.appendChild(name);
        copy.appendChild(desc);

        const arrow = document.createElement('span');
        arrow.className = 'catalog-arrow';
        arrow.setAttribute('aria-hidden', 'true');
        arrow.textContent = '→';

        row.appendChild(icon);
        row.appendChild(copy);
        row.appendChild(arrow);
        row.addEventListener('click', () => {
            close();
            item.run();
        });
        return row;
    }

    function render() {
        const body = document.getElementById('entry-catalog-body');
        if (!body) return;
        body.textContent = '';
        GROUPS.forEach((group) => {
            const heading = document.createElement('div');
            heading.className = 'catalog-group-title';
            heading.textContent = t(group.titleKey, group.titleFallback);
            body.appendChild(heading);
            const list = document.createElement('div');
            list.className = 'catalog-group';
            group.items.forEach((item) => list.appendChild(buildRow(item)));
            body.appendChild(list);
        });
    }

    function open() {
        const modal = document.getElementById('entry-catalog-modal');
        if (!modal) return;
        render();
        modal.classList.add('visible');
    }

    function close() {
        const modal = document.getElementById('entry-catalog-modal');
        if (modal) modal.classList.remove('visible');
    }

    function wire() {
        const modal = document.getElementById('entry-catalog-modal');
        if (!modal) return;
        const closeButton = document.getElementById('entry-catalog-close');
        if (closeButton) closeButton.addEventListener('click', close);
        const backdrop = modal.querySelector('.modal-backdrop');
        if (backdrop) backdrop.addEventListener('click', close);
        document.addEventListener('keydown', (event) => {
            if (event.key === 'Escape' && modal.classList.contains('visible')) {
                event.stopPropagation();
                close();
            }
        }, true);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', wire);
    } else {
        wire();
    }

    window.EntryCatalog = { open, close, openSettingsTab, openReaderTool };
})();
