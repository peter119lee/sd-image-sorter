/**
 * SD Image Sorter - Collections / Favorites browsing & management UI
 *
 * v3.3.1 FEAT-COLLECTIONS: renders the "Collections" section inside the
 * gallery filter sidebar, lets the user browse a collection (or Favorites)
 * in the gallery via the collection_id filter, and create / rename / delete
 * collections. Also exposes an "Add to collection" picker reused by the
 * gallery context menu.
 *
 * Backend contract (already built & tested) is reached through the global
 * `API` object: listCollections / createCollection / renameCollection /
 * deleteCollection / setCollectionMembership / getFavoriteIds.
 *
 * "Collections 贯穿全站": the sidebar section is the single source of truth
 * for which collection the gallery is scoped to, driven through
 * App.updateFilters(f => { f.collectionId = id; }).
 */
(function () {
    'use strict';

    const FAVORITES_SLUG = 'favorites';

    function appRef() {
        return window.App || {};
    }

    function log(level, message, error) {
        const logger = appRef().Logger || window.Logger;
        if (logger && typeof logger[level] === 'function') {
            logger[level](message, error);
        }
    }

    function t(key, fallback, params) {
        const value = window.I18n?.t?.(key, params);
        return (value && value !== key) ? value : fallback;
    }

    function toast(message, type) {
        appRef().showToast?.(message, type);
    }

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        }[ch]));
    }

    const CollectionsUI = {
        _collections: [],
        _favoritesId: null,
        _activeId: null,
        _initialized: false,

        async init() {
            if (this._initialized) {
                await this.refresh();
                return;
            }
            this._initialized = true;
            const newBtn = document.getElementById('btn-new-collection');
            if (newBtn) {
                newBtn.addEventListener('click', () => this.createPrompt());
            }
            const list = document.getElementById('collections-list');
            if (list) {
                // Event delegation keeps row handlers simple and avoids
                // re-binding on every refresh.
                list.addEventListener('click', (event) => this._onListClick(event));
            }
            // Rows are JS-rendered HTML (no data-i18n), so re-render on a
            // language switch or the built-in Favorites keeps showing the
            // previous language's name (QA P3-7c).
            document.addEventListener('languageChanged', () => this._renderList());
            await this.refresh();
        },

        async refresh() {
            try {
                const result = await appRef().API?.listCollections?.();
                const collections = Array.isArray(result?.collections) ? result.collections : [];
                this._collections = collections;
                const favorites = collections.find((c) => c.slug === FAVORITES_SLUG);
                this._favoritesId = favorites ? favorites.id : null;
                const activeId = Number(appRef()?.AppState?.filters?.collectionId);
                this._activeId = Number.isFinite(activeId)
                    && collections.some((collection) => Number(collection.id) === activeId)
                    ? activeId
                    : null;
                this._renderList();
            } catch (error) {
                log('warn', 'Failed to load collections', error);
            }
        },

        notifyChanged() {
            this.refresh();
        },

        _favoritesFirst() {
            const favorites = this._collections.filter((c) => c.slug === FAVORITES_SLUG);
            const others = this._collections
                .filter((c) => c.slug !== FAVORITES_SLUG)
                .sort((a, b) => (b.id || 0) - (a.id || 0));
            return [...favorites, ...others];
        },

        _renderList() {
            const list = document.getElementById('collections-list');
            if (!list) return;

            const ordered = this._favoritesFirst();
            if (ordered.length === 0) {
                list.innerHTML = `<p class="collections-empty">${escapeHtml(t('collections.empty', 'No collections yet'))}</p>`;
                this._renderBrowsingIndicator();
                return;
            }

            list.innerHTML = ordered.map((collection) => this._rowHtml(collection)).join('');
            this._renderBrowsingIndicator();
        },

        _rowHtml(collection) {
            const isFavorites = collection.slug === FAVORITES_SLUG;
            const isActive = this._activeId != null && Number(this._activeId) === Number(collection.id);
            const name = isFavorites
                ? t('collections.favorites', 'Favorites')
                : (collection.name || '');
            const count = Number(collection.item_count || 0);
            const icon = isFavorites ? '♥' : '📁';

            const renameBtn = isFavorites ? '' : (
                `<button type="button" class="collection-row-action" data-action="rename" `
                + `data-id="${collection.id}" title="${escapeHtml(t('collections.rename', 'Rename'))}" `
                + `aria-label="${escapeHtml(t('collections.rename', 'Rename'))}">`
                + `<span aria-hidden="true"><svg class="icon" aria-hidden="true"><use href="#i-edit"/></svg></span></button>`
            );
            const deleteBtn = isFavorites ? '' : (
                `<button type="button" class="collection-row-action collection-row-action-danger" data-action="delete" `
                + `data-id="${collection.id}" title="${escapeHtml(t('collections.delete', 'Delete'))}" `
                + `aria-label="${escapeHtml(t('collections.delete', 'Delete'))}">`
                + `<span aria-hidden="true"><svg class="icon" aria-hidden="true"><use href="#i-trash"/></svg></span></button>`
            );

            return `<div class="collection-row${isActive ? ' is-active' : ''}${isFavorites ? ' is-favorites' : ''}" `
                + `data-id="${collection.id}">`
                + `<button type="button" class="collection-row-open" data-action="browse" data-id="${collection.id}" `
                + `aria-pressed="${isActive ? 'true' : 'false'}">`
                + `<span class="collection-row-icon" aria-hidden="true">${icon}</span>`
                + `<span class="collection-row-name">${escapeHtml(name)}</span>`
                + `<span class="collection-row-count">${count}</span>`
                + `</button>`
                + `<div class="collection-row-actions">${renameBtn}${deleteBtn}</div>`
                + `</div>`;
        },

        _renderBrowsingIndicator() {
            const list = document.getElementById('collections-list');
            if (!list) return;
            const existing = document.getElementById('collections-browsing');
            if (existing) existing.remove();

            if (this._activeId == null) return;
            const active = this._collections.find((c) => Number(c.id) === Number(this._activeId));
            const name = active
                ? (active.slug === FAVORITES_SLUG ? t('collections.favorites', 'Favorites') : active.name)
                : '';

            const indicator = document.createElement('div');
            indicator.id = 'collections-browsing';
            indicator.className = 'collections-browsing';
            const label = t('collections.browsing', 'Browsing: {name}', { name })
                .replace('{name}', name || '');
            indicator.innerHTML = `<span class="collections-browsing-label">${escapeHtml(label)}</span>`
                + `<button type="button" id="collections-clear-browse" class="collections-browsing-clear" `
                + `title="${escapeHtml(t('collections.clearBrowse', 'Stop browsing this collection'))}" `
                + `aria-label="${escapeHtml(t('collections.clearBrowse', 'Stop browsing this collection'))}">`
                + `<span aria-hidden="true"><svg class="icon" aria-hidden="true"><use href="#i-close"/></svg></span></button>`;
            indicator.querySelector('#collections-clear-browse')
                ?.addEventListener('click', () => this.clearBrowse());
            list.parentNode?.insertBefore(indicator, list.nextSibling);
        },

        _onListClick(event) {
            const trigger = event.target.closest('[data-action]');
            if (!trigger) return;
            const action = trigger.dataset.action;
            const id = Number(trigger.dataset.id);
            if (!Number.isFinite(id)) return;

            if (action === 'browse') {
                this.browse(id);
            } else if (action === 'rename') {
                const collection = this._collections.find((c) => Number(c.id) === id);
                this.renamePrompt(id, collection?.name || '');
            } else if (action === 'delete') {
                const collection = this._collections.find((c) => Number(c.id) === id);
                this.deleteConfirm(id, collection?.name || '');
            }
        },

        browse(id) {
            const app = appRef();
            this._activeId = id;
            app.updateFilters?.((filters) => {
                filters.collectionId = id;
                filters.scope = 'library';
            });
            app.updateFilterSummary?.();
            app.loadImages?.();
            this._renderList();
        },

        clearBrowse() {
            const app = appRef();
            this._activeId = null;
            app.updateFilters?.((filters) => {
                filters.collectionId = null;
                filters.scope = 'library';
            });
            app.updateFilterSummary?.();
            app.loadImages?.();
            this._renderList();
        },

        isFavoritesActive() {
            return this._activeId != null
                && this._favoritesId != null
                && Number(this._activeId) === Number(this._favoritesId);
        },

        isBrowsing() {
            return this._activeId != null;
        },

        async createPrompt() {
            const app = appRef();
            try {
                let name = null;
                if (typeof app.showInputModal === 'function') {
                    name = await app.showInputModal(
                        t('collections.create', 'New collection'),
                        t('collections.namePrompt', 'Enter a name for the collection:'),
                        ''
                    );
                } else {
                    name = window.prompt(t('collections.namePrompt', 'Enter a name for the collection:'), '');
                }
                name = (name || '').trim();
                if (!name) return;

                const created = await app.API?.createCollection?.(name);
                await this.refresh();
                toast(t('collections.created', 'Collection created'), 'success');
                return created;
            } catch (error) {
                log('warn', 'Failed to create collection', error);
                toast(t('collections.createFailed', 'Failed to create collection'), 'error');
            }
        },

        async renamePrompt(id, currentName) {
            const app = appRef();
            try {
                let name = null;
                if (typeof app.showInputModal === 'function') {
                    name = await app.showInputModal(
                        t('collections.rename', 'Rename collection'),
                        t('collections.namePrompt', 'Enter a name for the collection:'),
                        currentName || ''
                    );
                } else {
                    name = window.prompt(t('collections.namePrompt', 'Enter a name for the collection:'), currentName || '');
                }
                name = (name || '').trim();
                if (!name || name === currentName) return;

                await app.API?.renameCollection?.(id, name);
                await this.refresh();
                toast(t('collections.renamed', 'Collection renamed'), 'success');
            } catch (error) {
                log('warn', 'Failed to rename collection', error);
                toast(t('collections.renameFailed', 'Failed to rename collection'), 'error');
            }
        },

        deleteConfirm(id, name) {
            const app = appRef();
            const confirmMessage = t('collections.deleteConfirm', 'Delete "{name}"? Images stay on disk.')
                .replace('{name}', name || '');
            const runDelete = async () => {
                try {
                    await app.API?.deleteCollection?.(id);
                    // If we were browsing the deleted collection, return to the
                    // normal listing so the gallery isn't stuck on an empty scope.
                    if (this._activeId != null && Number(this._activeId) === Number(id)) {
                        this.clearBrowse();
                    }
                    await this.refresh();
                    toast(t('collections.deleted', 'Collection deleted'), 'success');
                } catch (error) {
                    const status = error?.apiStatus;
                    const message = String(error?.message || '');
                    // Favorites is non-deletable by design (HTTP 400). Surface a
                    // friendly note rather than a scary error.
                    if (status === 400 || /favorit/i.test(message)) {
                        toast(t('collections.deleteFavoritesBlocked', 'Favorites cannot be deleted'), 'info');
                        return;
                    }
                    log('warn', 'Failed to delete collection', error);
                    toast(t('collections.deleteFailed', 'Failed to delete collection'), 'error');
                }
            };
            if (typeof app.showConfirm === 'function') {
                app.showConfirm(t('collections.delete', 'Delete collection'), confirmMessage, runDelete);
            } else if (window.confirm(confirmMessage)) {
                runDelete();
            }
        },

        async openAddToCollectionPicker(imageIds, options = {}) {
            // A filtered-selection token ("Select all matching") stands in for
            // an explicit id list, so the backend expands it server-side.
            const selectionToken = options.selectionToken || null;
            const ids = (Array.isArray(imageIds) ? imageIds : [imageIds])
                .map((id) => Number(id))
                .filter((id) => Number.isFinite(id) && id > 0);
            if (ids.length === 0 && !selectionToken) return;

            // Make sure the cached list is fresh before showing the picker.
            if (this._collections.length === 0) {
                await this.refresh();
            }
            this._buildPickerMenu(ids, selectionToken);
        },

        _buildPickerMenu(ids, selectionToken) {
            document.querySelector('.collections-picker-menu')?.remove();

            const menu = document.createElement('div');
            menu.className = 'collections-picker-menu gallery-context-menu';
            menu.setAttribute('role', 'menu');

            const ordered = this._favoritesFirst();
            const header = document.createElement('div');
            header.className = 'collections-picker-header';
            header.textContent = t('collections.addTo', 'Add to collection');
            menu.appendChild(header);

            ordered.forEach((collection) => {
                const name = collection.slug === FAVORITES_SLUG
                    ? t('collections.favorites', 'Favorites')
                    : (collection.name || '');
                const icon = collection.slug === FAVORITES_SLUG ? '♥' : '📁';
                menu.appendChild(this._pickerItem(icon, name, () => {
                    menu.remove();
                    this._addToCollection(collection.id, name, ids, selectionToken);
                }));
            });

            const separator = document.createElement('div');
            separator.className = 'context-menu-separator';
            separator.setAttribute('role', 'separator');
            menu.appendChild(separator);

            menu.appendChild(this._pickerItem('＋', t('collections.new', 'New collection…'), async () => {
                menu.remove();
                const created = await this.createPrompt();
                if (created?.id != null) {
                    this._addToCollection(created.id, created.name || '', ids, selectionToken);
                }
            }));

            document.body.appendChild(menu);
            this._positionPickerAtPointer(menu);
            this._bindPickerDismiss(menu);
        },

        _pickerItem(icon, labelText, onClick) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'context-menu-item';
            button.setAttribute('role', 'menuitem');

            const iconEl = document.createElement('span');
            iconEl.className = 'context-menu-icon';
            iconEl.setAttribute('aria-hidden', 'true');
            iconEl.textContent = icon;

            const labelEl = document.createElement('span');
            labelEl.className = 'context-menu-label';
            labelEl.textContent = labelText;

            button.append(iconEl, labelEl);
            button.addEventListener('click', onClick);
            return button;
        },

        _positionPickerAtPointer(menu) {
            const pointer = window._lastPointerEvent || {};
            const x = Number.isFinite(pointer.clientX) ? pointer.clientX : Math.round(window.innerWidth / 2);
            const y = Number.isFinite(pointer.clientY) ? pointer.clientY : Math.round(window.innerHeight / 3);
            window.PopupPosition?.place(menu, {
                x,
                y,
                placement: 'point',
                maxHeight: Math.min(420, Math.max(120, window.innerHeight - 16)),
            });
        },

        _bindPickerDismiss(menu) {
            const close = () => {
                menu.remove();
                document.removeEventListener('click', close);
                document.removeEventListener('keydown', onKey, true);
            };
            const onKey = (event) => {
                if (event.key !== 'Escape') return;
                event.preventDefault();
                event.stopImmediatePropagation();
                close();
            };
            document.addEventListener('keydown', onKey, true);
            // Defer outside-click binding so the originating click doesn't immediately close it.
            setTimeout(() => {
                document.addEventListener('click', close);
            }, 0);
        },

        async _addToCollection(collectionId, name, ids, selectionToken) {
            const app = appRef();
            try {
                // One bulk call (single transaction server-side) instead of a
                // POST per image; a selection token covers ids the gallery
                // never materialized client-side.
                const result = await app.API?.setCollectionMembershipBulk?.(collectionId, {
                    imageIds: ids,
                    selectionToken,
                    member: true,
                });
                const added = Number(result?.added ?? ids.length) || 0;
                // Keep favorite hearts in sync if the picker targeted Favorites.
                if (this._favoritesId != null && Number(collectionId) === Number(this._favoritesId)) {
                    window.Gallery?.hydrateFavorites?.();
                }
                const message = t('collections.addedToast', 'Added {count} image(s) to {name}', {
                    count: added,
                    name,
                })
                    .replace('{count}', String(added))
                    .replace('{name}', name || '');
                toast(message, 'success');
                await this.refresh();
            } catch (error) {
                log('warn', 'Failed to add images to collection', error);
                toast(t('collections.addFailed', 'Failed to add images to collection'), 'error');
            }
        },
    };

    // Track the last pointer event so the add-to-collection picker can open
    // near the cursor even though it's triggered from a menu callback.
    document.addEventListener('pointerdown', (event) => {
        window._lastPointerEvent = { clientX: event.clientX, clientY: event.clientY };
    }, true);

    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        const menus = document.querySelectorAll('.collections-picker-menu');
        if (menus.length === 0) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        menus.forEach((menu) => menu.remove());
    }, true);

    window.CollectionsUI = CollectionsUI;
})();
