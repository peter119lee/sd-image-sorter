/**
 * SD Image Sorter - Library roots management modal (v3.3.2 Library Navigation, Phase D)
 *
 * Lists the folders the user added as image sources (GET /api/library-roots,
 * auto-registered on scan), with per-root Rescan (POST /api/library-roots/{id}/rescan)
 * and Remove (DELETE /api/library-roots/{id}; image files stay on disk). "Add
 * Folder…" reuses the existing scan modal — scanning any folder auto-registers
 * it as a root. The modal also hosts the idle auto-refresh toggle (Phase C),
 * persisted in localStorage and driven through window.AutoRefresh.
 *
 * Mirrors collections-ui.js conventions (window.App for modal/toast/confirm/API,
 * window.I18n.t with fallbacks, event-delegated row actions).
 */
(function () {
    'use strict';

    const AUTO_REFRESH_KEY = 'library_auto_refresh_enabled';

    function appRef() {
        return window.App || {};
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

    const LibraryRootsUI = {
        _initialized: false,

        init() {
            if (this._initialized) return;
            this._initialized = true;

            document.getElementById('btn-manage-roots')?.addEventListener('click', () => this.open());
            document.getElementById('btn-library-roots-close')
                ?.addEventListener('click', () => appRef().hideModal?.('library-roots-modal'));
            document.getElementById('btn-library-roots-add')?.addEventListener('click', () => {
                // Adding a folder = scanning it; the scan auto-registers the root.
                appRef().hideModal?.('library-roots-modal');
                appRef().showModal?.('scan-modal');
            });

            const toggle = document.getElementById('library-roots-autorefresh-toggle');
            if (toggle) {
                toggle.checked = this.isAutoRefreshEnabled();
                toggle.addEventListener('change', () => {
                    try {
                        localStorage.setItem(AUTO_REFRESH_KEY, toggle.checked ? '1' : '0');
                    } catch (error) { /* private mode — session-only */ }
                    window.AutoRefresh?.setEnabled?.(toggle.checked);
                    toast(
                        toggle.checked
                            ? t('libraryRoots.autoRefreshOn', 'Idle auto-refresh on')
                            : t('libraryRoots.autoRefreshOff', 'Idle auto-refresh off'),
                        'info',
                    );
                });
            }

            document.getElementById('library-roots-list')
                ?.addEventListener('click', (event) => this._onListClick(event));
        },

        isAutoRefreshEnabled() {
            try {
                return localStorage.getItem(AUTO_REFRESH_KEY) === '1';
            } catch (error) {
                return false;
            }
        },

        async open() {
            appRef().showModal?.('library-roots-modal');
            const toggle = document.getElementById('library-roots-autorefresh-toggle');
            if (toggle) toggle.checked = this.isAutoRefreshEnabled();
            // Move keyboard focus into the dialog. The shared showModal() only
            // auto-focuses a `.modal-close` element, which this modal does not
            // have (its Close button lives in the footer), so do it explicitly.
            document.getElementById('btn-library-roots-close')?.focus();
            await this.refresh();
        },

        async refresh() {
            const list = document.getElementById('library-roots-list');
            if (!list) return;
            list.innerHTML = `<p class="library-roots-empty">${escapeHtml(t('common.loading', 'Loading…'))}</p>`;
            try {
                const data = await appRef().API?.get?.('/api/library-roots');
                const roots = Array.isArray(data?.roots) ? data.roots : [];
                this._render(roots);
            } catch (error) {
                list.innerHTML = `<p class="library-roots-empty">${escapeHtml(t('libraryRoots.loadFailed', 'Could not load library folders'))}</p>`;
            }
        },

        _render(roots) {
            const list = document.getElementById('library-roots-list');
            if (!list) return;
            if (!roots.length) {
                list.innerHTML = `<p class="library-roots-empty">${escapeHtml(t('libraryRoots.empty', 'No folders yet — use "Add Folder…" to scan one.'))}</p>`;
                return;
            }
            list.innerHTML = roots.map((root) => this._rowHtml(root)).join('');
        },

        _rowHtml(root) {
            const id = Number(root.id);
            const count = Number(root.image_count || 0);
            const isMissing = root.exists === false;
            const scanned = root.last_scanned_at
                ? t('libraryRoots.scannedAt', 'Last scanned {when}').replace('{when}', String(root.last_scanned_at).replace('T', ' '))
                : t('libraryRoots.neverScanned', 'Never scanned');
            const metaParts = [];
            if (isMissing) {
                metaParts.push(`<span class="library-root-missing"><svg class="icon" aria-hidden="true"><use href="#i-alert"/></svg> ${escapeHtml(t('libraryRoots.missing', 'Folder missing'))}</span>`);
            }
            metaParts.push(`${count} ${escapeHtml(t('libraryRoots.images', 'images'))}`);
            metaParts.push(escapeHtml(scanned));
            const rowClass = isMissing ? 'library-root-row is-missing' : 'library-root-row';
            const missingTitle = isMissing
                ? ` title="${escapeHtml(t('libraryRoots.missingHint', 'This folder no longer exists on disk. Remove it, or reconnect it by scanning the folder again.'))}"`
                : '';
            return `<div class="${rowClass}" data-id="${id}"${missingTitle}>`
                + '<div class="library-root-info">'
                + `<span class="library-root-path" title="${escapeHtml(root.path)}">${escapeHtml(root.path)}</span>`
                + `<span class="library-root-meta">${metaParts.join(' · ')}</span>`
                + '</div>'
                + '<div class="library-root-actions">'
                + `<button type="button" class="btn btn-secondary btn-small" data-action="rescan" data-id="${id}">${escapeHtml(t('libraryRoots.rescan', 'Rescan'))}</button>`
                + `<button type="button" class="btn btn-ghost btn-small danger" data-action="remove" data-id="${id}">${escapeHtml(t('libraryRoots.remove', 'Remove'))}</button>`
                + '</div></div>';
        },

        _onListClick(event) {
            const trigger = event.target.closest('[data-action]');
            if (!trigger) return;
            const id = Number(trigger.dataset.id);
            if (!Number.isFinite(id)) return;
            if (trigger.dataset.action === 'rescan') this._rescan(id);
            else if (trigger.dataset.action === 'remove') this._remove(id);
        },

        async _rescan(id) {
            try {
                const app = appRef();
                const result = await app.API?.post?.(`/api/library-roots/${id}/rescan`, {});
                if (typeof app.beginLibraryRescanScanProgress !== 'function') {
                    throw new TypeError('The library rescan progress handler is unavailable');
                }
                app.beginLibraryRescanScanProgress(result);
                toast(t('libraryRoots.rescanStarted', 'Rescan started — new files will appear shortly'), 'success');
            } catch (error) {
                const errorCode = error?.apiData?.code;
                const message = errorCode === 'manual_completion_pending'
                    ? t(
                        'libraryRoots.manualCompletionPending',
                        'The previous import finished but its completion is still pending. Reload the app to acknowledge it, then run Rescan again.'
                    )
                    : error?.apiStatus === 409
                        ? t('libraryRoots.scanBusy', 'A scan is already running')
                        : t('libraryRoots.rescanFailed', 'Could not start rescan');
                toast(message, 'error');
            }
        },

        _remove(id) {
            const run = async () => {
                try {
                    await appRef().API?.delete?.(`/api/library-roots/${id}`);
                    toast(t('libraryRoots.removed', 'Folder removed from library'), 'success');
                    await this.refresh();
                    window.FolderTreeUI?.refresh?.();
                } catch (error) {
                    toast(t('libraryRoots.removeFailed', 'Could not remove folder'), 'error');
                }
            };
            const message = t('libraryRoots.removeConfirm', 'Remove this folder from the library? The image files stay on disk — only the gallery source registration is removed.');
            if (typeof appRef().showConfirm === 'function') {
                appRef().showConfirm(t('libraryRoots.remove', 'Remove folder'), message, run);
            } else if (window.confirm(message)) {
                run();
            }
        },
    };

    window.LibraryRootsUI = LibraryRootsUI;
})();
