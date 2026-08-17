/**
 * SD Image Sorter - Library folder-tree navigation (gallery subset filter)
 *
 * v3.3.2 Library Navigation: renders a collapsible "Folders" tree inside the
 * gallery filter sidebar built from the distinct directories that contain
 * indexed images (GET /api/folders). Clicking a folder scopes the gallery to
 * that folder *and everything beneath it* (recursive subtree) via the
 * `folder` gallery filter, exactly the way the Collections section drives
 * `collectionId`. Re-clicking the active folder clears the scope.
 *
 * This is distinct from `folder-browser.js`, which is a raw filesystem picker
 * for the scan/import modal. This module only ever lists folders that are
 * already in the library.
 *
 * Single source of truth for the folder scope is the gallery filter state,
 * driven through App.updateFilters(f => { f.folder = path; }).
 */
(function () {
    'use strict';

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

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;',
        }[ch]));
    }

    /**
     * Normalize a stored directory path to forward slashes with no trailing
     * separator. The backend already returns forward-slash dirs, but we stay
     * defensive so the tree keys match the value we send back as `?folder=`.
     */
    function normalizePath(raw) {
        return String(raw ?? '').replace(/\\/g, '/').replace(/\/+$/, '');
    }

    const FolderTreeUI = {
        _folders: [],
        _tree: null,
        _expanded: new Set(),
        _active: null,
        _seeded: false,
        _initialized: false,

        async init() {
            if (this._initialized) {
                await this.refresh();
                return;
            }
            this._initialized = true;

            const refreshBtn = document.getElementById('btn-refresh-folders');
            if (refreshBtn) {
                refreshBtn.addEventListener('click', () => this.refresh());
            }
            const container = document.getElementById('folder-tree');
            if (container) {
                // Event delegation so we never re-bind per row on re-render.
                container.addEventListener('click', (event) => this._onClick(event));
            }
            document.addEventListener('languageChanged', () => this._relocalize());
            window.addEventListener('resize', () => this._scheduleActiveVisibility());
            await this.refresh();
        },

        async refresh() {
            try {
                const result = await appRef().API?.listLibraryFolders?.();
                const folders = Array.isArray(result?.folders) ? result.folders : [];
                this._folders = folders;
                this._tree = this._buildTree(folders);
                // Sync the highlighted/active folder from the persisted gallery
                // filter so a reload keeps the tree and the gallery in step.
                const current = normalizePath(appRef().AppState?.filters?.folder || '');
                this._active = current || null;
                if (!this._seeded) {
                    [...this._tree.children.values()].forEach((node) => this._seedDefaults(node, 0));
                    this._seeded = true;
                }
                if (this._active) this._expandAncestors(this._active);
                this._renderTree();
                this._scheduleActiveVisibility();
            } catch (error) {
                log('warn', 'Failed to load library folders', error);
            }
        },

        /** Build a nested tree (Map of children) from a flat list of dir paths. */
        _buildTree(paths) {
            const root = { name: '', path: '', children: new Map() };
            for (const raw of paths) {
                const norm = normalizePath(raw);
                if (!norm) continue;
                const segs = norm.split('/');
                let node = root;
                for (let i = 0; i < segs.length; i++) {
                    const seg = segs[i];
                    if (seg === '' && i > 0) continue; // collapse interior // (double slash)
                    const path = segs.slice(0, i + 1).join('/') || '/';
                    const key = seg || '/';
                    if (!node.children.has(key)) {
                        node.children.set(key, { name: key, path, children: new Map() });
                    }
                    node = node.children.get(key);
                }
            }
            return root;
        },

        /**
         * Seed the default expanded set ONCE: reveal the first level and keep
         * unfolding through single-child chains so a long common prefix
         * (L:/ › Pictures › Library) doesn't force the user to click through
         * empty levels. Branch points start collapsed.
         */
        _seedDefaults(node, depth) {
            const kids = [...node.children.values()];
            if (kids.length === 0) return;
            if (depth === 0 || kids.length === 1) {
                this._expanded.add(node.path);
                kids.forEach((kid) => this._seedDefaults(kid, depth + 1));
            }
        },

        /** Make sure every ancestor of `path` is expanded so it's visible. */
        _expandAncestors(path) {
            const segs = normalizePath(path).split('/');
            for (let i = 1; i < segs.length; i++) {
                const ancestor = segs.slice(0, i).join('/') || '/';
                if (ancestor) this._expanded.add(ancestor);
            }
        },

        _renderTree() {
            const container = document.getElementById('folder-tree');
            if (!container) return;
            const top = this._tree ? [...this._tree.children.values()] : [];
            if (top.length === 0) {
                container.innerHTML = `<p class="folder-tree-empty">${escapeHtml(t('folders.empty', 'No folders yet — scan a folder to populate the gallery.'))}</p>`;
                this._renderBrowsingIndicator();
                return;
            }
            container.innerHTML = top.map((node) => this._nodeHtml(node, 0)).join('');
            this._renderBrowsingIndicator();
        },

        _relocalize() {
            const container = document.getElementById('folder-tree');
            if (!container) return;
            const sidebarScroll = container.closest('.filter-sidebar-scroll');
            const treeScrollTop = container.scrollTop;
            const sidebarScrollTop = sidebarScroll?.scrollTop || 0;
            this._renderTree();
            container.scrollTop = treeScrollTop;
            if (sidebarScroll) sidebarScroll.scrollTop = sidebarScrollTop;
        },

        _scheduleActiveVisibility() {
            if (!this._active) return;
            const activePath = this._active;
            requestAnimationFrame(() => {
                if (this._active !== activePath) return;
                const container = document.getElementById('folder-tree');
                const activeRow = [...(container?.querySelectorAll('.folder-row-open') || [])]
                    .find((row) => normalizePath(row.dataset.path) === activePath);
                if (!activeRow || !container) return;

                const revealWithin = (scroller) => {
                    const rowBox = activeRow.getBoundingClientRect();
                    const scrollerBox = scroller.getBoundingClientRect();
                    const margin = 1;
                    if (rowBox.top < scrollerBox.top + margin) {
                        scroller.scrollTop -= scrollerBox.top + margin - rowBox.top;
                    } else if (rowBox.bottom > scrollerBox.bottom - margin) {
                        scroller.scrollTop += rowBox.bottom - scrollerBox.bottom + margin;
                    }
                };

                revealWithin(container);
                requestAnimationFrame(() => {
                    if (this._active !== activePath) return;
                    const sidebarScroll = container.closest('.filter-sidebar-scroll');
                    if (sidebarScroll) revealWithin(sidebarScroll);
                });
            });
        },

        _isActiveVisible() {
            if (!this._active) return false;
            const container = document.getElementById('folder-tree');
            const activeRow = [...(container?.querySelectorAll('.folder-row-open') || [])]
                .find((row) => normalizePath(row.dataset.path) === this._active);
            const sidebarScroll = container?.closest('.filter-sidebar-scroll');
            if (!activeRow || !container || !sidebarScroll) return false;

            const rowBox = activeRow.getBoundingClientRect();
            return [container, sidebarScroll].every((scroller) => {
                const scrollerBox = scroller.getBoundingClientRect();
                return rowBox.top >= scrollerBox.top && rowBox.bottom <= scrollerBox.bottom;
            });
        },

        _nodeHtml(node, depth) {
            const hasKids = node.children.size > 0;
            const isExpanded = this._expanded.has(node.path);
            const isActive = this._active != null && this._active === node.path;
            const pad = 6 + depth * 14;
            const safePath = escapeHtml(node.path);

            const toggle = hasKids
                ? `<button type="button" class="folder-row-toggle" data-action="toggle" data-path="${safePath}" `
                    + `aria-label="${escapeHtml(t('folders.toggle', 'Expand / collapse'))}" aria-expanded="${isExpanded ? 'true' : 'false'}">`
                    + `<span aria-hidden="true">${isExpanded ? '▾' : '▸'}</span></button>`
                : '<span class="folder-row-toggle folder-row-toggle-empty" aria-hidden="true"></span>';

            let html = `<div class="folder-row${isActive ? ' is-active' : ''}" `
                + `style="padding-left:${pad}px" data-path="${safePath}">`
                + toggle
                + `<button type="button" class="folder-row-open" data-action="browse" data-path="${safePath}" `
                + `aria-pressed="${isActive ? 'true' : 'false'}" title="${safePath}">`
                + '<span class="folder-row-icon" aria-hidden="true"><svg class="icon" aria-hidden="true"><use href="#i-folder"/></svg></span>'
                + `<span class="folder-row-name">${escapeHtml(node.name)}</span>`
                + '</button></div>';

            if (hasKids && isExpanded) {
                html += [...node.children.values()].map((kid) => this._nodeHtml(kid, depth + 1)).join('');
            }
            return html;
        },

        _renderBrowsingIndicator() {
            const container = document.getElementById('folder-tree');
            if (!container) return;
            document.getElementById('folder-tree-browsing')?.remove();
            if (this._active == null) return;

            const name = this._active;
            const indicator = document.createElement('div');
            indicator.id = 'folder-tree-browsing';
            // Reuse the Collections "Browsing: …" chrome, but the folder
            // variant wraps so the FULL path is always visible (no ellipsis).
            indicator.className = 'collections-browsing folder-tree-browsing';
            const label = t('folders.scoped', 'Folder: {name}').replace('{name}', name);
            indicator.innerHTML = `<span class="folder-tree-browsing-label" title="${escapeHtml(name)}">${escapeHtml(label)}</span>`
                + '<button type="button" id="folder-tree-clear" class="collections-browsing-clear" '
                + `title="${escapeHtml(t('folders.clearScope', 'Show all folders'))}" `
                + `aria-label="${escapeHtml(t('folders.clearScope', 'Show all folders'))}">`
                + '<span aria-hidden="true"><svg class="icon" aria-hidden="true"><use href="#i-close"/></svg></span></button>';
            indicator.querySelector('#folder-tree-clear')
                ?.addEventListener('click', () => this.clearBrowse());
            container.parentNode?.insertBefore(indicator, container.nextSibling);
        },

        _onClick(event) {
            const trigger = event.target.closest('[data-action]');
            if (!trigger) return;
            const action = trigger.dataset.action;
            const path = trigger.dataset.path;
            if (!path) return;

            if (action === 'toggle') {
                const container = document.getElementById('folder-tree');
                const scrollTop = container?.scrollTop || 0;
                if (this._expanded.has(path)) {
                    this._expanded.delete(path);
                } else {
                    this._expanded.add(path);
                }
                this._renderTree();
                const refreshedToggle = [...(container?.querySelectorAll('[data-action="toggle"]') || [])]
                    .find((toggle) => normalizePath(toggle.dataset.path) === normalizePath(path));
                refreshedToggle?.focus({ preventScroll: true });
                if (container) container.scrollTop = scrollTop;
            } else if (action === 'browse') {
                this.browse(path);
            }
        },

        browse(path) {
            const app = appRef();
            const target = normalizePath(path);
            // Re-clicking the active folder toggles the scope back off.
            if (this._active === target) {
                this.clearBrowse();
                return;
            }
            this._active = target;
            this._expandAncestors(target); // keep the scoped row visible even if its parent was collapsed
            app.updateFilters?.((filters) => { filters.folder = target; });
            app.updateFilterSummary?.();
            app.loadImages?.();
            this._renderTree();
            this._scheduleActiveVisibility();
        },

        clearBrowse() {
            const app = appRef();
            this._active = null;
            app.updateFilters?.((filters) => { filters.folder = null; });
            app.updateFilterSummary?.();
            app.loadImages?.();
            this._renderTree();
        },

        isScoped() {
            return this._active != null;
        },
    };

    window.FolderTreeUI = FolderTreeUI;
})();
