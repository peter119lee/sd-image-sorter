/**
 * Long-lived multi-library workspaces (DESIGN.md §product-narrative).
 *
 * - One current library id (localStorage + X-SD-Library-Id on API calls)
 * - Entry home: switch / create / rename / delete (main cannot delete)
 * - Clear gallery clears **current** library only (backend scoped)
 */
(function () {
    'use strict';

    const STORAGE_KEY = 'sd-library-workspace-v1';
    const HEADER = 'X-SD-Library-Id';
    const DEFAULT_ID = 'main';

    let _cache = null; // { libraries, currentId }

    function _t(key, fallback, params) {
        if (typeof window.appT === 'function') return window.appT(key, fallback, params);
        const val = window.I18n?.t?.(key, params);
        if (val && val !== key) return val;
        let text = fallback || key;
        if (params && typeof params === 'object') {
            for (const [k, v] of Object.entries(params)) {
                text = String(text).split(`{${k}}`).join(String(v));
            }
        }
        return text;
    }

    function _readLocal() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            if (!raw) return { currentId: DEFAULT_ID };
            const parsed = JSON.parse(raw);
            return {
                currentId: (parsed && parsed.currentId) || DEFAULT_ID,
            };
        } catch (_e) {
            return { currentId: DEFAULT_ID };
        }
    }

    function _writeLocal(currentId) {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify({
                v: 2,
                currentId: currentId || DEFAULT_ID,
            }));
        } catch (_e) { /* ignore */ }
    }

    function getCurrentLibraryId() {
        if (_cache && _cache.currentId) return _cache.currentId;
        return _readLocal().currentId || DEFAULT_ID;
    }

    function libraryHeaders(extra) {
        const headers = {};
        // Accept Headers instance or plain object from bare fetch call sites.
        if (extra && typeof Headers !== 'undefined' && extra instanceof Headers) {
            extra.forEach((value, key) => { headers[key] = value; });
        } else if (extra && typeof extra === 'object') {
            Object.assign(headers, extra);
        }
        headers[HEADER] = getCurrentLibraryId();
        return headers;
    }

    /** Fetch with X-SD-Library-Id for image-scoped / library-scoped APIs. */
    function apiFetch(url, options) {
        const opts = Object.assign({}, options || {});
        opts.headers = libraryHeaders(opts.headers || {});
        // Use native fetch to avoid re-entrancy once the global guard is installed.
        const native = window.__sdNativeFetch || window.fetch;
        return native.call(window, url, opts);
    }

    /**
     * Patch window.fetch so every /api/* call carries X-SD-Library-Id.
     * Closes residual bare-fetch leaks without editing every call site.
     */
    function installFetchGuard() {
        if (window.__sdLibraryFetchPatched) return;
        if (typeof window.fetch !== 'function') return;
        window.__sdLibraryFetchPatched = true;
        const original = window.fetch.bind(window);
        window.__sdNativeFetch = original;
        window.fetch = function sdLibraryFetch(input, init) {
            try {
                let url = '';
                if (typeof input === 'string') url = input;
                else if (input && typeof input.url === 'string') url = input.url;
                const isApi = typeof url === 'string' && url.indexOf('/api/') !== -1;
                if (!isApi) return original(input, init);

                if (typeof Request !== 'undefined' && input instanceof Request) {
                    const merged = libraryHeaders(input.headers);
                    if (init && init.headers) {
                        Object.assign(merged, libraryHeaders(init.headers));
                    }
                    const nextInit = Object.assign({}, init || {}, { headers: merged });
                    return original(new Request(input, nextInit));
                }
                const next = Object.assign({}, init || {});
                next.headers = libraryHeaders(next.headers || {});
                return original(input, next);
            } catch (_e) {
                return original(input, init);
            }
        };
    }

    function getCurrentLibrary() {
        const id = getCurrentLibraryId();
        const list = (_cache && _cache.libraries) || [];
        const found = list.find((lib) => lib.id === id);
        if (found) {
            return {
                id: found.id,
                name: found.name,
                is_default: Boolean(found.is_default),
                image_count: Number(found.image_count || 0),
            };
        }
        return {
            id: id || DEFAULT_ID,
            name: _t('library.defaultName', 'Main library'),
            is_default: id === DEFAULT_ID,
            image_count: 0,
        };
    }

    async function refreshFromServer() {
        try {
            const res = await fetch('/api/libraries', {
                headers: libraryHeaders({ Accept: 'application/json' }),
            });
            if (!res.ok) throw new Error('libraries_list_failed');
            const data = await res.json();
            const libraries = Array.isArray(data.libraries) ? data.libraries : [];
            let currentId = _readLocal().currentId || DEFAULT_ID;
            if (!libraries.some((lib) => lib.id === currentId)) {
                currentId = data.current_id || DEFAULT_ID;
                if (!libraries.some((lib) => lib.id === currentId)) {
                    currentId = (libraries[0] && libraries[0].id) || DEFAULT_ID;
                }
            }
            _cache = { libraries, currentId };
            _writeLocal(currentId);
            refreshEntryHome();
            return _cache;
        } catch (_e) {
            // Offline / pre-migration backend: keep local main-only view.
            _cache = {
                currentId: _readLocal().currentId || DEFAULT_ID,
                libraries: [{
                    id: DEFAULT_ID,
                    name: _t('library.defaultName', 'Main library'),
                    is_default: true,
                    image_count: 0,
                }],
            };
            refreshEntryHome();
            return _cache;
        }
    }

    async function setCurrentLibraryId(id, { reloadGallery } = { reloadGallery: true }) {
        const next = String(id || DEFAULT_ID);
        _writeLocal(next);
        if (_cache) _cache.currentId = next;
        refreshEntryHome();
        try {
            window.dispatchEvent(new CustomEvent('library-workspace-changed', {
                detail: getCurrentLibrary(),
            }));
        } catch (_e) { /* ignore */ }

        // Force long-term library query scope (not process session).
        applyLibraryDefaultScope();

        if (reloadGallery && typeof window.loadImages === 'function') {
            try { await window.loadImages(false, { coalesce: true }); } catch (_e) { /* ignore */ }
        }
        if (typeof window.loadStats === 'function') {
            try { window.loadStats(); } catch (_e) { /* ignore */ }
        }
        return getCurrentLibrary();
    }

    function applyLibraryDefaultScope() {
        try {
            if (typeof window.updateAppFilters === 'function') {
                window.updateAppFilters((filters) => {
                    filters.scope = 'library';
                });
            } else if (window.AppState && window.AppState.filters) {
                window.AppState.filters.scope = 'library';
            }
        } catch (_e) { /* boot order */ }
    }

    function clearConfirmCopy() {
        const current = getCurrentLibrary();
        const count = Number(current.image_count || 0);
        const countText = count > 0
            ? _t('gallery.clearCountPart', '{count} indexed images', { count: String(count) })
            : _t('gallery.clearCountUnknown', 'all indexed images');
        return {
            title: _t('gallery.clearTitle', 'Clear current library'),
            message: _t(
                'gallery.clearMessageNamedCount',
                'Clear {countText} from library “{name}”? Other libraries are not affected. Files on disk are not deleted.',
                { name: current.name, countText, count: String(count) },
            ),
            success: _t(
                'gallery.clearSuccessNamed',
                'Library “{name}” cleared',
                { name: current.name },
            ),
        };
    }

    function refreshNavChip() {
        const chip = document.getElementById('nav-library-chip');
        const label = document.getElementById('nav-library-chip-label');
        if (!chip || !label) return;
        const current = getCurrentLibrary();
        const count = Number(current.image_count || 0);
        label.textContent = count > 0
            ? `${current.name} · ${count}`
            : current.name;
        chip.hidden = false;
        chip.title = _t(
            'nav.libraryChipTitle',
            'Current library: {name}. Click to manage libraries.',
            { name: current.name },
        );
        chip.setAttribute(
            'aria-label',
            _t('entry.currentLibraryAria', 'Current library: {name}', { name: current.name }),
        );
    }

    function refreshEntryHome() {
        const current = getCurrentLibrary();
        const nameEl = document.getElementById('entry-library-name');
        if (nameEl) nameEl.textContent = current.name;
        refreshNavChip();

        const switcher = document.getElementById('entry-library-switcher');
        if (switcher) {
            switcher.setAttribute(
                'aria-label',
                _t('entry.currentLibraryAria', 'Current library: {name}', { name: current.name }),
            );
        }

        const countEl = document.getElementById('entry-count-gallery');
        if (countEl && current.image_count != null && document.getElementById('entry-page')
            && !document.getElementById('entry-page').hidden) {
            // Prefer server summary when present; otherwise show library count.
            if (!countEl.textContent || countEl.dataset.fromLibrary === '1') {
                countEl.textContent = String(current.image_count);
                countEl.dataset.fromLibrary = '1';
            }
        }

        const menu = document.getElementById('entry-library-menu');
        if (menu && !menu.hidden) renderMenu(menu);
    }

    function renderMenu(menu) {
        const current = getCurrentLibrary();
        const libs = (_cache && _cache.libraries) || [current];
        menu.innerHTML = '';

        libs.forEach((lib) => {
            const row = document.createElement('div');
            row.className = 'entry-library-menu-row';

            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'entry-library-menu-item' + (lib.id === current.id ? ' is-current' : '');
            btn.setAttribute('role', 'option');
            btn.setAttribute('aria-selected', String(lib.id === current.id));
            btn.dataset.libraryId = lib.id;
            const count = Number(lib.image_count || 0);
            btn.textContent = count > 0 ? `${lib.name} (${count})` : lib.name;
            btn.addEventListener('click', async () => {
                closeMenu();
                await setCurrentLibraryId(lib.id);
            });
            row.appendChild(btn);

            const rename = document.createElement('button');
            rename.type = 'button';
            rename.className = 'entry-library-menu-rename';
            rename.title = _t('library.renameTitle', 'Rename library');
            rename.setAttribute('aria-label', _t('library.renameTitle', 'Rename library'));
            rename.innerHTML = "<svg class=\"icon\" aria-hidden=\"true\"><use href=\"#i-edit\"/></svg>";
            rename.addEventListener('click', async (e) => {
                e.preventDefault();
                e.stopPropagation();
                await renameLibraryInteractive(lib.id, lib.name);
            });
            row.appendChild(rename);

            if (!lib.is_default && lib.id !== DEFAULT_ID) {
                const del = document.createElement('button');
                del.type = 'button';
                del.className = 'entry-library-menu-delete';
                del.title = _t('library.deleteTitle', 'Delete library');
                del.setAttribute('aria-label', _t('library.deleteTitle', 'Delete library'));
                del.textContent = '⌫';
                del.addEventListener('click', async (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    await deleteLibrary(lib.id, lib.name);
                });
                row.appendChild(del);
            }

            menu.appendChild(row);
        });

        const createBtn = document.createElement('button');
        createBtn.type = 'button';
        createBtn.className = 'entry-library-menu-create';
        createBtn.textContent = _t('library.create', 'New library…');
        createBtn.addEventListener('click', async () => {
            await createLibraryInteractive();
        });
        menu.appendChild(createBtn);

        const exportBtn = document.createElement('button');
        exportBtn.type = 'button';
        exportBtn.className = 'entry-library-menu-create';
        exportBtn.textContent = _t(
            'library.exportCurrent',
            'Export current library index…',
        );
        exportBtn.addEventListener('click', async () => {
            closeMenu();
            await exportLibraryIndex(current.id, current.name);
        });
        menu.appendChild(exportBtn);

        // Move gallery selection into a chosen library (when selection exists).
        const selectedIds = _selectedImageIds();
        if (selectedIds.length > 0) {
            const moveLabel = document.createElement('p');
            moveLabel.className = 'entry-library-menu-note';
            moveLabel.textContent = _t(
                'library.moveSelectionHint',
                'Move {count} selected image(s) into…',
                { count: String(selectedIds.length) },
            );
            menu.appendChild(moveLabel);
            libs.forEach((lib) => {
                if (lib.id === current.id) return;
                const moveBtn = document.createElement('button');
                moveBtn.type = 'button';
                moveBtn.className = 'entry-library-menu-create';
                moveBtn.textContent = _t(
                    'library.moveSelectionTo',
                    '→ {name}',
                    { name: lib.name },
                );
                moveBtn.addEventListener('click', async () => {
                    closeMenu();
                    await moveImagesToLibrary(selectedIds, lib.id, lib.name);
                });
                menu.appendChild(moveBtn);
            });
        }

        const note = document.createElement('p');
        note.className = 'entry-library-menu-note';
        note.textContent = _t(
            'entry.multiLibraryNote',
            'Clearing the gallery only empties the current library. Other libraries stay intact.',
        );
        menu.appendChild(note);
    }

    function _selectedImageIds() {
        try {
            if (typeof window.AppFilterAccess?.getSelectedImageIds === 'function') {
                const ids = window.AppFilterAccess.getSelectedImageIds();
                if (Array.isArray(ids)) return ids.map(Number).filter((n) => n > 0);
            }
            const sel = window.AppState?.selectedIds;
            if (sel instanceof Set) return Array.from(sel).map(Number).filter((n) => n > 0);
            if (Array.isArray(sel)) return sel.map(Number).filter((n) => n > 0);
        } catch (_e) { /* ignore */ }
        return [];
    }

    async function moveImagesToLibrary(imageIds, targetLibraryId, targetName) {
        const ids = (imageIds || []).map(Number).filter((n) => n > 0);
        if (!ids.length || !targetLibraryId) return null;
        try {
            const res = await apiFetch('/api/libraries/move-images', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Accept: 'application/json',
                },
                body: JSON.stringify({
                    image_ids: ids,
                    target_library_id: targetLibraryId,
                }),
            });
            if (!res.ok) throw new Error('move_failed');
            const data = await res.json();
            await refreshFromServer();
            if (typeof window.loadImages === 'function') {
                try { await window.loadImages(false, { coalesce: true }); } catch (_e) { /* ignore */ }
            }
            if (typeof window.showToast === 'function') {
                window.showToast(
                    _t(
                        'library.movedToast',
                        'Moved {count} image(s) to “{name}”',
                        {
                            count: String(data.moved || 0),
                            name: targetName || targetLibraryId,
                        },
                    ),
                    'success',
                );
            }
            return data;
        } catch (_e) {
            if (typeof window.showToast === 'function') {
                window.showToast(_t('library.moveFailed', 'Could not move images'), 'error');
            }
            return null;
        }
    }

    async function claimPaths(paths, targetLibraryId) {
        const list = Array.isArray(paths) ? paths.filter(Boolean) : [];
        if (!list.length) return null;
        try {
            const res = await apiFetch('/api/libraries/claim-paths', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Accept: 'application/json',
                },
                body: JSON.stringify({
                    paths: list.slice(0, 500),
                    target_library_id: targetLibraryId || getCurrentLibraryId(),
                }),
            });
            if (!res.ok) throw new Error('claim_failed');
            const data = await res.json();
            await refreshFromServer();
            if (typeof window.loadImages === 'function') {
                try { await window.loadImages(false, { coalesce: true }); } catch (_e) { /* ignore */ }
            }
            if (typeof window.showToast === 'function') {
                window.showToast(
                    _t(
                        'library.claimedToast',
                        'Claimed {count} image(s) into this library',
                        { count: String(data.moved || 0) },
                    ),
                    'success',
                );
            }
            return data;
        } catch (_e) {
            if (typeof window.showToast === 'function') {
                window.showToast(_t('library.claimFailed', 'Could not claim images'), 'error');
            }
            return null;
        }
    }

    async function exportLibraryIndex(libraryId, libraryName) {
        const id = libraryId || getCurrentLibraryId();
        try {
            const res = await apiFetch(
                `/api/libraries/${encodeURIComponent(id)}/export?download=1`,
                { headers: { Accept: 'application/json' } },
            );
            if (!res.ok) throw new Error('export_failed');
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            const safe = String(libraryName || id).replace(/[^\w\-]+/g, '_').slice(0, 40);
            a.href = url;
            a.download = `library-export-${safe || 'library'}.json`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 2000);
            if (typeof window.showToast === 'function') {
                window.showToast(
                    _t('library.exportToast', 'Library index downloaded'),
                    'success',
                );
            }
        } catch (_e) {
            if (typeof window.showToast === 'function') {
                window.showToast(_t('library.exportFailed', 'Could not export library'), 'error');
            }
        }
    }

    async function createLibraryInteractive() {
        const suggested = _t('library.newNamePlaceholder', 'New library');
        let name = suggested;
        if (typeof window.showInputModal === 'function') {
            // optional if exists
        }
        name = window.prompt(
            _t('library.createPrompt', 'Name for the new library:'),
            suggested,
        );
        if (name == null) return;
        name = String(name).trim();
        if (!name) return;
        try {
            const res = await fetch('/api/libraries', {
                method: 'POST',
                headers: libraryHeaders({
                    'Content-Type': 'application/json',
                    Accept: 'application/json',
                }),
                body: JSON.stringify({ name }),
            });
            if (!res.ok) throw new Error('create_failed');
            const data = await res.json();
            await refreshFromServer();
            if (data.library && data.library.id) {
                await setCurrentLibraryId(data.library.id);
            }
            closeMenu();
            if (typeof window.showToast === 'function') {
                window.showToast(
                    _t('library.createdToast', 'Library “{name}” created', { name: data.library.name }),
                    'success',
                );
            }
        } catch (_e) {
            if (typeof window.showToast === 'function') {
                window.showToast(_t('library.createFailed', 'Could not create library'), 'error');
            }
        }
    }

    async function renameLibraryInteractive(id, currentName) {
        const suggested = currentName || _t('library.newNamePlaceholder', 'New library');
        const nameRaw = window.prompt(
            _t('library.renamePrompt', 'Rename library:'),
            suggested,
        );
        if (nameRaw == null) return;
        const name = String(nameRaw).trim();
        if (!name || name === currentName) return;
        try {
            const res = await apiFetch(`/api/libraries/${encodeURIComponent(id)}`, {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    Accept: 'application/json',
                },
                body: JSON.stringify({ name }),
            });
            if (!res.ok) throw new Error('rename_failed');
            await refreshFromServer();
            if (typeof window.showToast === 'function') {
                window.showToast(
                    _t('library.renamedToast', 'Library renamed to “{name}”', { name }),
                    'success',
                );
            }
        } catch (_e) {
            if (typeof window.showToast === 'function') {
                window.showToast(_t('library.renameFailed', 'Could not rename library'), 'error');
            }
        }
    }

    async function deleteLibrary(id, name) {
        if (id === DEFAULT_ID) {
            if (typeof window.showToast === 'function') {
                window.showToast(
                    _t('library.cannotDeleteMain', 'The main library cannot be deleted. Clear it instead.'),
                    'error',
                );
            }
            return;
        }
        const ok = window.confirm(
            _t(
                'library.deleteConfirm',
                'Delete library “{name}”? Indexed images in this library are removed. Files on disk are kept. Other libraries are not affected.',
                { name: name || id },
            ),
        );
        if (!ok) return;
        try {
            const res = await fetch(`/api/libraries/${encodeURIComponent(id)}`, {
                method: 'DELETE',
                headers: libraryHeaders({ Accept: 'application/json' }),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                if (err?.detail?.code === 'default_library_protected') {
                    throw new Error('protected');
                }
                throw new Error('delete_failed');
            }
            if (getCurrentLibraryId() === id) {
                await setCurrentLibraryId(DEFAULT_ID, { reloadGallery: true });
            }
            await refreshFromServer();
            closeMenu();
            if (typeof window.showToast === 'function') {
                window.showToast(
                    _t('library.deletedToast', 'Library “{name}” deleted', { name: name || id }),
                    'success',
                );
            }
        } catch (e) {
            if (typeof window.showToast === 'function') {
                window.showToast(
                    e && e.message === 'protected'
                        ? _t('library.cannotDeleteMain', 'The main library cannot be deleted. Clear it instead.')
                        : _t('library.deleteFailed', 'Could not delete library'),
                    'error',
                );
            }
        }
    }

    function openMenu() {
        const menu = document.getElementById('entry-library-menu');
        const switcher = document.getElementById('entry-library-switcher');
        if (!menu || !switcher) return;
        refreshFromServer().then(() => {
            renderMenu(menu);
            menu.hidden = false;
            switcher.setAttribute('aria-expanded', 'true');
        });
    }

    function closeMenu() {
        const menu = document.getElementById('entry-library-menu');
        const switcher = document.getElementById('entry-library-switcher');
        if (menu) menu.hidden = true;
        if (switcher) switcher.setAttribute('aria-expanded', 'false');
    }

    function toggleMenu() {
        const menu = document.getElementById('entry-library-menu');
        if (!menu || menu.hidden) openMenu();
        else closeMenu();
    }

    function wireEntry() {
        document.getElementById('entry-library-switcher')?.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            toggleMenu();
        });
        document.getElementById('entry-library-manage')?.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            openMenu();
        });
        document.getElementById('nav-library-chip')?.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            // Prefer entry home management; if entry hidden, still open menu if present.
            const entry = document.getElementById('entry-page');
            if (entry && entry.hidden && typeof window.EntryPage?.show === 'function') {
                try { window.EntryPage.show(); } catch (_e) { /* ignore */ }
            }
            openMenu();
        });
        document.addEventListener('click', (e) => {
            const home = document.getElementById('entry-library-home');
            const chip = document.getElementById('nav-library-chip');
            if (home && home.contains(e.target)) return;
            if (chip && chip.contains(e.target)) return;
            const menu = document.getElementById('entry-library-menu');
            if (menu && menu.contains(e.target)) return;
            closeMenu();
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') closeMenu();
        });
        window.addEventListener('library-workspace-changed', () => {
            refreshNavChip();
        });
    }

    function init() {
        installFetchGuard();
        wireEntry();
        applyLibraryDefaultScope();
        refreshFromServer();
        document.addEventListener('i18n-applied', () => refreshEntryHome());
    }

    // Install as early as this classic script loads (before DOMContentLoaded),
    // so subsequent module fetches still get the library header.
    installFetchGuard();

    window.LibraryWorkspace = {
        HEADER,
        getCurrentLibraryId,
        getCurrentLibrary,
        libraryHeaders,
        apiFetch,
        setCurrentLibraryId,
        refreshFromServer,
        refreshEntryHome,
        refreshNavChip,
        clearConfirmCopy,
        applyLibraryDefaultScope,
        createLibraryInteractive,
        renameLibraryInteractive,
        deleteLibrary,
        moveImagesToLibrary,
        claimPaths,
        exportLibraryIndex,
        _STORAGE_KEY: STORAGE_KEY,
        _DEFAULT_ID: DEFAULT_ID,
    };
    window.libraryFetchHeaders = libraryHeaders;
    window.apiFetch = apiFetch;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
