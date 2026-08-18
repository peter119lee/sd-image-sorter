/**
 * Gallery banner for rows whose image files are not on disk.
 *
 * It used to offer one action, "Find Moved Files", which relinks a file that
 * *moved*. For a file that was deleted that finds nothing, and nothing else let
 * the user see or clear those rows — so the banner could only be hidden, never
 * resolved, and it came back every session. This module now also offers the
 * missing half: clearing the records, which deletes no files.
 *
 * Two rules it exists to honour:
 * - A location we cannot read right now is never offered for clearing. An
 *   unplugged external drive looks identical to a deleted folder from here, and
 *   its files may be fine. The backend decides this; the UI never guesses.
 * - The cost is stated before asking. How many of the affected images carry
 *   tags, ratings or collections is read from the summary, not assumed.
 *
 * Design:
 * - Stays hidden when there are zero missing rows.
 * - Polls /api/images/missing-summary when the gallery becomes active, with a
 *   60s cache so normal browsing does not spam the backend.
 * - "Hide for now" only hides until the next page load (sessionStorage). We
 *   deliberately do not persist a permanent dismissal — if files really are
 *   missing, the user needs to know.
 */
(function () {
    'use strict';

    var SESSION_KEY = 'sd-image-sorter:unreadable-banner-dismissed';
    var CACHE_MS = 60 * 1000;
    var MAX_RENDERED_GROUPS = 6;

    var state = {
        cachedAt: 0,
        cachedCount: null,
        summary: null,
        inFlight: null,
        detailsOpen: false,
        clearing: false
    };

    function $(selector) {
        return document.querySelector(selector);
    }

    function appT(key, fallback, params) {
        var t = window.I18n && typeof window.I18n.t === 'function'
            ? window.I18n.t(key, params)
            : null;
        if (t && t !== key) return t;
        if (typeof fallback === 'string') {
            if (params && typeof params === 'object') {
                return fallback.replace(/\{(\w+)\}/g, function (_, name) {
                    return Object.prototype.hasOwnProperty.call(params, name)
                        ? String(params[name])
                        : '{' + name + '}';
                });
            }
            return fallback;
        }
        return key;
    }

    function isDismissedThisSession() {
        try {
            return window.sessionStorage.getItem(SESSION_KEY) === '1';
        } catch (e) {
            return false;
        }
    }

    function markDismissedForSession() {
        try {
            window.sessionStorage.setItem(SESSION_KEY, '1');
        } catch (e) {
            /* noop — sessionStorage may be disabled */
        }
    }

    /** Dynamic text must claim the i18n lock or the #app observer resets it. */
    function lockedText(element, text) {
        if (!element) return element;
        element.dataset.i18nLocked = '1';
        element.textContent = text;
        return element;
    }

    function reasonLabel(reason) {
        if (reason === 'file_deleted') {
            return appT('missing.reasonFileDeleted', 'folder is there, files were deleted');
        }
        if (reason === 'folder_deleted') {
            return appT('missing.reasonFolderDeleted', 'folder no longer exists');
        }
        return appT('missing.reasonUnreachable', 'cannot read this location right now');
    }

    /** Work that clearing would really destroy, i.e. inside clearable groups only. */
    function clearableWork(summary) {
        if (typeof summary.clearable_user_work_total === 'number') {
            return summary.clearable_user_work_total;
        }
        return summary.user_work_total || 0;
    }

    /** The banner's own sentence, chosen from what the summary actually says. */
    function detailFor(summary) {
        var clearable = summary.clearable_total || 0;
        var blocked = summary.blocked_total || 0;
        if (blocked > 0 && clearable === 0) {
            return appT(
                'missing.detailAllBlocked',
                'This app cannot read that location right now — an unplugged drive would look exactly like this. Reconnect it and re-scan; nothing needs clearing.'
            );
        }
        if (blocked > 0) {
            return appT(
                'missing.detailMixed',
                '{blocked} are in a location this app cannot read right now, so those are left alone — reconnect it and re-scan. The other {clearable} point at files that are really gone.',
                { blocked: blocked, clearable: clearable }
            );
        }
        var base = appT(
            'missing.detailAllGone',
            'Their files were deleted, or the folders were removed. Clearing the records deletes nothing on disk.'
        );
        // Cost of the action offered, not of the whole library: blocked rows
        // keep their tags because they are never cleared.
        var work = clearableWork(summary);
        var cost = work > 0
            ? appT(
                'missing.costSome',
                '{count} of them carry tags, ratings or collections you added — clearing loses those.',
                { count: work }
            )
            : appT(
                'missing.costNone',
                'Nothing you made is attached to these records, so clearing them loses nothing.'
            );
        return base + ' ' + cost;
    }

    function renderGroups(summary) {
        var list = $('#gallery-missing-panel-list');
        if (!list) return;
        list.textContent = '';

        var groups = (summary.groups || []).slice(0, MAX_RENDERED_GROUPS);
        groups.forEach(function (group) {
            var item = document.createElement('li');
            item.className = 'missing-panel-item' +
                (group.clearable ? '' : ' is-blocked');

            var copy = document.createElement('div');
            copy.className = 'missing-panel-item-copy';

            var where = document.createElement('span');
            where.className = 'missing-panel-item-location';
            lockedText(where, group.location || '');
            copy.appendChild(where);

            var meta = document.createElement('span');
            meta.className = 'missing-panel-item-meta';
            var parts = [
                appT('missing.groupCount', '{count} images', { count: group.count }),
                reasonLabel(group.reason)
            ];
            if (group.user_work_total > 0) {
                parts.push(appT(
                    'missing.groupWork',
                    '{count} with your tags or ratings',
                    { count: group.user_work_total }
                ));
            }
            lockedText(meta, parts.join(' · '));
            copy.appendChild(meta);

            item.appendChild(copy);

            if (group.clearable) {
                var button = document.createElement('button');
                button.type = 'button';
                button.className = 'btn btn-ghost btn-small';
                lockedText(button, appT('missing.clearOne', 'Clear'));
                button.addEventListener('click', function () {
                    requestClear(group.location, group.count, group.user_work_total);
                });
                item.appendChild(button);
            }

            list.appendChild(item);
        });

        var extra = (summary.location_total || 0) - groups.length;
        if (extra > 0) {
            var more = document.createElement('li');
            more.className = 'missing-panel-item is-note';
            lockedText(
                more,
                appT('missing.moreLocations', 'and {count} more locations', { count: extra })
            );
            list.appendChild(more);
        }
    }

    function applySummary(summary) {
        var banner = $('#gallery-unreadable-banner');
        if (!banner) return;

        lockedText($('#gallery-unreadable-banner-title'), appT(
            'reconnect.banner.title',
            '{count} image(s) cannot be opened — their original files are missing.',
            { count: summary.total }
        ));
        lockedText($('#gallery-unreadable-banner-detail'), detailFor(summary));

        var clearAll = $('#gallery-missing-clear-all');
        var clearable = summary.clearable_total || 0;
        if (clearAll) {
            clearAll.hidden = clearable <= 0;
            clearAll.disabled = state.clearing;
            lockedText($('#gallery-missing-clear-all-label'), state.clearing
                ? appT('missing.clearing', 'Clearing…')
                : appT('missing.clearAllCount', 'Clear {count} records', { count: clearable }));
        }

        var toggle = $('#gallery-missing-toggle-details');
        if (toggle) {
            toggle.hidden = !(summary.groups && summary.groups.length);
            toggle.setAttribute('aria-expanded', state.detailsOpen ? 'true' : 'false');
            lockedText($('#gallery-missing-toggle-details-label'), state.detailsOpen
                ? appT('missing.hideDetails', 'Hide folders')
                : appT('missing.showDetails', 'Show folders'));
        }

        var panel = $('#gallery-missing-panel');
        if (panel) {
            panel.hidden = !state.detailsOpen;
            if (state.detailsOpen) renderGroups(summary);
        }
    }

    function showBanner(summary) {
        var banner = $('#gallery-unreadable-banner');
        if (!banner) return;
        if (isDismissedThisSession()) return;
        applySummary(summary);
        banner.hidden = false;
    }

    function hideBanner() {
        var banner = $('#gallery-unreadable-banner');
        if (!banner) return;
        banner.hidden = true;
        var panel = $('#gallery-missing-panel');
        if (panel) panel.hidden = true;
        state.detailsOpen = false;
    }

    function emptySummary() {
        return {
            total: 0,
            clearable_total: 0,
            blocked_total: 0,
            user_work_total: 0,
            location_total: 0,
            groups: []
        };
    }

    async function apiGet(path) {
        var api = window.App && window.App.API;
        if (api && typeof api.get === 'function') return api.get(path);
        var response = await fetch(path);
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.json();
    }

    async function apiPost(path, body) {
        var api = window.App && window.App.API;
        if (api && typeof api.post === 'function') return api.post(path, body);
        var response = await fetch(path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {})
        });
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.json();
    }

    async function fetchSummary(force) {
        var now = Date.now();
        if (!force && state.summary && (now - state.cachedAt) < CACHE_MS) {
            return state.summary;
        }
        if (state.inFlight) return state.inFlight;

        state.inFlight = (async function () {
            try {
                var data = await apiGet('/api/images/missing-summary');
                var summary = data && typeof data.total === 'number' ? data : emptySummary();
                state.summary = summary;
                state.cachedCount = summary.total;
                state.cachedAt = Date.now();
                return summary;
            } catch (e) {
                // Soft-fail: never break the gallery because the audit failed.
                return state.summary || emptySummary();
            } finally {
                state.inFlight = null;
            }
        })();
        return state.inFlight;
    }

    function requestClear(location, count, workCount) {
        if (state.clearing) return;
        var title = appT(
            'missing.confirmTitle',
            'Clear {count} gallery records?',
            { count: count }
        );
        var body = workCount > 0
            ? appT(
                'missing.confirmBodyWithWork',
                'No file on disk is deleted or moved. But {count} of these records carry tags, ratings or collections you added, and those are not recoverable by re-scanning.',
                { count: workCount }
            )
            : appT(
                'missing.confirmBody',
                'No file on disk is deleted or moved. These records point at files that are not there, and re-scanning the folder brings them back if the files return.'
            );

        var confirm = (window.App && window.App.showConfirm) || window.showConfirm;
        if (typeof confirm !== 'function') {
            performClear(location);
            return;
        }
        confirm(title, body, function () { performClear(location); });
    }

    async function performClear(location) {
        state.clearing = true;
        if (state.summary) applySummary(state.summary);
        try {
            var result = await apiPost('/api/images/missing/clear',
                location ? { location: location } : {});
            var removed = (result && result.removed) || 0;
            var status = result && result.status;
            if (window.showToast) {
                if (status === 'refused') {
                    window.showToast(appT(
                        'missing.refused',
                        'Left alone — this app cannot read that location right now. If it is a removable drive, reconnect it and re-scan.'
                    ), 'info');
                } else if (removed > 0) {
                    window.showToast(appT(
                        'missing.cleared',
                        'Cleared {count} records. No files were deleted.',
                        { count: removed }
                    ), 'success');
                } else {
                    window.showToast(appT('missing.clearedNone', 'Nothing to clear.'), 'info');
                }
            }
            if (removed > 0 && typeof window.loadImages === 'function') {
                window.loadImages();
            }
        } catch (e) {
            if (window.showToast) {
                var reason = (window.formatUserError && window.formatUserError(e))
                    || (e && e.message)
                    || '';
                window.showToast(appT(
                    'missing.clearFailed',
                    'Could not clear those records: {reason}',
                    { reason: reason }
                ), 'error');
            }
        } finally {
            state.clearing = false;
            await refresh(true);
        }
    }

    async function refresh(force) {
        var summary = await fetchSummary(force);
        if ((summary.total || 0) > 0) {
            showBanner(summary);
        } else {
            hideBanner();
        }
        // The gallery count label explains a shown/total gap with this number
        // in its tooltip, and this poll resolves after the first load.
        if (typeof window.applyGalleryCountLabel === 'function') {
            window.applyGalleryCountLabel();
        }
    }

    function bind() {
        var cta = $('#gallery-unreadable-banner-cta');
        if (cta && !cta.dataset.bound) {
            cta.dataset.bound = '1';
            cta.addEventListener('click', function () {
                var openModal = window.App && window.App.showModal
                    ? window.App.showModal
                    : window.showModal;
                if (typeof openModal === 'function') {
                    openModal('reconnect-modal');
                } else {
                    var trigger = $('#btn-reconnect-missing');
                    if (trigger) trigger.click();
                }
            });
        }

        var dismiss = $('#gallery-unreadable-banner-dismiss');
        if (dismiss && !dismiss.dataset.bound) {
            dismiss.dataset.bound = '1';
            dismiss.addEventListener('click', function () {
                markDismissedForSession();
                hideBanner();
                if (window.showToast) {
                    window.showToast(appT('reconnect.banner.dismissed', 'Banner hidden. Reopen the gallery to see it again.'), 'info');
                }
            });
        }

        var clearAll = $('#gallery-missing-clear-all');
        if (clearAll && !clearAll.dataset.bound) {
            clearAll.dataset.bound = '1';
            clearAll.addEventListener('click', function () {
                var summary = state.summary;
                if (!summary) return;
                requestClear(null, summary.clearable_total || 0, clearableWork(summary));
            });
        }

        var toggle = $('#gallery-missing-toggle-details');
        if (toggle && !toggle.dataset.bound) {
            toggle.dataset.bound = '1';
            toggle.addEventListener('click', function () {
                state.detailsOpen = !state.detailsOpen;
                if (state.summary) applySummary(state.summary);
            });
        }

        document.addEventListener('languageChanged', function () {
            if (state.summary && (state.summary.total || 0) > 0) {
                applySummary(state.summary);
            }
        });
    }

    function init() {
        if (!$('#gallery-unreadable-banner')) return;
        bind();
        // Initial check after first paint, with a small delay so we do not
        // contend with the first /api/images load.
        setTimeout(function () { refresh(false); }, 1500);
    }

    window.UnreadableBanner = {
        init: init,
        refresh: refresh,
        invalidate: function () {
            state.cachedCount = null;
            state.summary = null;
            state.cachedAt = 0;
        },
        // Last known unreadable-row count (null = not checked yet). Read by
        // the gallery image-count label to explain shown/total mismatches.
        getLastCount: function () {
            return state.cachedCount;
        }
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
