/**
 * Dataset Maker — pipeline tabs + audit engine: state/badges/filters, select/remove matches, next steps, renderResults, residue strip, audit modal.
 * Moved VERBATIM from dataset-maker-pipeline.js L1-543, L666-825,
 * L1045, L1221 (+ two documented non-verbatim additions: the
 * DM._renderAuditResults bridge and the per-module init split).
 * Load order is pinned by the ordered async=false loader in dataset/core.js.
 */
/**
 * Dataset Maker — pipeline stepper + audit panel (v3.2.2 T8 + T9 frontend).
 *
 * Two pieces of UI in one file:
 *   1. ``.dataset-stepper`` — the 5-step header. Each pill scrolls to a
 *      labelled anchor inside the Dataset Maker view.
 *   2. The LoRA-readiness audit panel (``#dataset-audit-status`` and
 *      friends). User fills the thresholds they care about (all
 *      optional), runs the audit, sees badge counts they can click
 *      to filter the queue.
 */
(function () {
    'use strict';
    if (!window.DatasetMaker) return;
    const DM = window.DatasetMaker;

    // ============== 4-tab nav ==============

    function bindTabs() {
        const tabs = document.querySelectorAll('.dataset-tabs [role="tab"]');
        const datasetMaker = document.querySelector('.dataset-maker');
        if (!datasetMaker || tabs.length === 0) return;

        for (const tab of tabs) {
            tab.addEventListener('click', () => {
                const target = tab.getAttribute('data-tab-target');
                if (!target) return;
                datasetMaker.setAttribute('data-active-tab', target);
                for (const t of tabs) {
                    t.setAttribute('aria-selected',
                        t.getAttribute('data-tab-target') === target ? 'true' : 'false');
                }
            });
        }
    }

    // ============== Audit panel ==============

    const AUDIT_STATE = {
        lastReport: null,
        activeFilter: null,    // one of "missing", "low_quality", "untagged", "small", "duplicate"
        inverted: false,       // when true, highlight items NOT matching the filter
        running: false,
    };

    function $(id) { return document.getElementById(id); }

    function setStatus(text) {
        const el = $('dataset-audit-status');
        if (el) el.textContent = text || '';
    }

    function makeBadge(flag, label, count) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'dataset-audit-badge';
        btn.dataset.flag = flag;
        const lbl = document.createElement('span');
        lbl.textContent = label;
        const countEl = document.createElement('span');
        countEl.className = 'dataset-audit-badge-count';
        countEl.textContent = String(count);
        btn.append(lbl, countEl);
        btn.addEventListener('click', () => toggleFilter(flag));
        return btn;
    }

    function updateBadgeState() {
        for (const b of document.querySelectorAll('.dataset-audit-badge')) {
            b.classList.toggle('active', b.dataset.flag === AUDIT_STATE.activeFilter);
        }
        const removeBtn = $('btn-dataset-audit-remove-flagged');
        if (removeBtn) removeBtn.hidden = !AUDIT_STATE.activeFilter;
    }

    function toggleFilter(flag) {
        AUDIT_STATE.activeFilter = (AUDIT_STATE.activeFilter === flag) ? null : flag;
        applyFilterToQueue();
        if (AUDIT_STATE.activeFilter) focusFirstAuditMatch(AUDIT_STATE.activeFilter);
        updateBadgeState();
        renderAuditNextSteps();
        // M6: keep the residue strip in sync when the user toggles a
        // filter from inside the modal.
        updateAuditResidueStrip();
    }

    function getAuditMatches(flag) {
        const flaggedIds = new Set();
        const flaggedPaths = new Set();
        if (!flag || !AUDIT_STATE.lastReport) return { flaggedIds, flaggedPaths };
        if (flag === 'duplicate') {
            for (const grp of (AUDIT_STATE.lastReport.duplicate_groups || [])) {
                for (const id of grp.image_ids || []) {
                    if (id > 0) flaggedIds.add(Number(id));
                }
                for (const p of grp.abs_paths || []) {
                    if (p) flaggedPaths.add(p);
                }
            }
        } else {
            for (const it of (AUDIT_STATE.lastReport.items || [])) {
                if (!(it.flags || []).includes(flag)) continue;
                if (it.image_id && it.image_id > 0) flaggedIds.add(Number(it.image_id));
                if (it.abs_path) flaggedPaths.add(String(it.abs_path));
            }
        }
        return { flaggedIds, flaggedPaths };
    }

    function auditSummaryCount(flag, report = AUDIT_STATE.lastReport) {
        const summary = report?.summary || {};
        if (flag === 'duplicate') return (report?.duplicate_groups || []).length;
        const key = {
            missing: 'missing_count',
            low_quality: 'low_quality_count',
            untagged: 'untagged_count',
            small: 'small_count',
        }[flag];
        return Number(key ? summary[key] : 0) || 0;
    }

    function knownAuditMatchCount(flag, report = AUDIT_STATE.lastReport) {
        if (!flag || !report) return 0;
        const matches = getAuditMatches(flag);
        return matches.flaggedIds.size + matches.flaggedPaths.size;
    }

    function auditIssueIsTruncated(flag, report = AUDIT_STATE.lastReport) {
        if (!report?.items_truncated || flag === 'duplicate') return false;
        return knownAuditMatchCount(flag, report) < auditSummaryCount(flag, report);
    }

    function auditIssueOptions(report = AUDIT_STATE.lastReport) {
        const t = (key, fb, params) => DM._t(key, fb, params);
        const summary = report?.summary || {};
        const dupes = (report?.duplicate_groups || []).length;
        return [
            {
                flag: 'missing',
                label: t('dataset.auditBadgeMissing', 'Missing / unreadable'),
                count: summary.missing_count || 0,
                removable: true,
                truncated: auditIssueIsTruncated('missing', report),
                nextCopy: t('dataset.auditNextMissing',
                    'These files are missing or unreadable. Reconnect the source files, replace them, or remove them from the dataset before export.'),
            },
            {
                flag: 'untagged',
                label: t('dataset.auditBadgeUntagged', 'Untagged'),
                count: summary.untagged_count || 0,
                removable: true,
                truncated: auditIssueIsTruncated('untagged', report),
                nextCopy: t('dataset.auditNextUntagged',
                    'These images will export blank .txt files. Go to Workbench and write captions, or select them and remove the ones you do not want in the dataset.'),
            },
            {
                flag: 'small',
                label: t('dataset.auditBadgeSmall', 'Small'),
                count: summary.small_count || 0,
                removable: true,
                truncated: auditIssueIsTruncated('small', report),
                nextCopy: t('dataset.auditNextSmall',
                    'Review these low-resolution images. Replace them with larger sources or remove them from the dataset before export.'),
            },
            {
                flag: 'low_quality',
                label: t('dataset.auditBadgeLowQuality', 'Low quality'),
                count: summary.low_quality_count || 0,
                removable: true,
                truncated: auditIssueIsTruncated('low_quality', report),
                nextCopy: t('dataset.auditNextLowQuality',
                    'Review the low-score images and remove the ones that would weaken the training set.'),
            },
            {
                flag: 'duplicate',
                label: t('dataset.auditBadgeDuplicate', 'Duplicates'),
                count: dupes,
                removable: false,
                nextCopy: t('dataset.auditNextDuplicate',
                    'Duplicate groups are selected for review. Keep the best image in each group, then manually remove the extras.'),
            },
        ].filter((it) => Number(it.count || 0) > 0);
    }

    function preferredAuditFlag() {
        const issues = auditIssueOptions();
        if (AUDIT_STATE.activeFilter && issues.some((it) => it.flag === AUDIT_STATE.activeFilter)) {
            return AUDIT_STATE.activeFilter;
        }
        return issues[0]?.flag || null;
    }

    function loadedAuditMatchIds(flag) {
        const { flaggedIds, flaggedPaths } = getAuditMatches(flag);
        const matches = [];
        for (const id of DM.imageIds || []) {
            const numericId = Number(id);
            const meta = DM.meta?.get?.(numericId) || {};
            const absPath = String(meta.abs_path || '');
            const match = (numericId > 0 && flaggedIds.has(numericId))
                || (absPath && flaggedPaths.has(absPath));
            if (match) matches.push(numericId);
        }
        return matches;
    }

    function auditMatchStats(flag) {
        const { flaggedPaths } = getAuditMatches(flag);
        const loadedIds = loadedAuditMatchIds(flag);
        const loadedPaths = new Set();
        for (const id of loadedIds) {
            const meta = DM.meta?.get?.(Number(id)) || {};
            if (meta.abs_path) loadedPaths.add(String(meta.abs_path));
        }
        const unloadedPaths = Array.from(flaggedPaths).filter((p) => p && !loadedPaths.has(String(p)));
        return {
            loadedIds,
            unloadedPaths,
            total: loadedIds.length + unloadedPaths.length,
        };
    }

    function selectAuditMatches(flag = preferredAuditFlag(), options = {}) {
        if (!flag) {
            setStatus(DM._t('dataset.auditNeedIssue', 'Choose an audit issue first.'));
            return [];
        }
        AUDIT_STATE.activeFilter = flag;
        applyFilterToQueue();
        updateBadgeState();
        const ids = loadedAuditMatchIds(flag);
        DM._queueSelection = new Set(ids);
        DM._updateMultiSelectUI?.();
        if (options.focus !== false) focusFirstAuditMatch(flag);
        renderAuditNextSteps();
        setStatus(DM._t('dataset.auditSelectedStatus',
            'Selected {count} loaded matching images. Use Workbench to edit or remove them.',
            { count: ids.length }));
        return ids;
    }

    function clearAuditResultsAfterMutation() {
        AUDIT_STATE.lastReport = null;
        AUDIT_STATE.activeFilter = null;
        const wrap = $('dataset-audit-results');
        const badges = $('dataset-audit-badges');
        if (badges) badges.innerHTML = '';
        if (wrap) wrap.hidden = true;
        const dlBtn = $('btn-dataset-audit-download');
        if (dlBtn) dlBtn.hidden = true;
        applyFilterToQueue();
        // M6: report is gone — hide the residue strip.
        updateAuditResidueStrip();
    }

    function removeAuditMatches(flag = preferredAuditFlag()) {
        if (!flag) {
            setStatus(DM._t('dataset.auditNeedIssue', 'Choose an audit issue first.'));
            return;
        }
        if (flag === 'duplicate') {
            selectAuditMatches(flag);
            setStatus(DM._t('dataset.auditDuplicateSelectOnly',
                'Duplicate groups are selected. Keep one image per group, then remove the extras manually.'));
            DM._setPipelineTab?.('workbench');
            return;
        }
        const { flaggedPaths } = getAuditMatches(flag);
        const stats = auditMatchStats(flag);
        const summaryCount = auditSummaryCount(flag);
        const wasTruncated = auditIssueIsTruncated(flag) && summaryCount > stats.total;
        if (stats.total === 0) {
            setStatus(DM._t('dataset.auditNoLoadedMatches',
                'No matching images are currently loaded in the queue. Load more previews or re-run audit.'));
            return;
        }
        const msg = DM._t('dataset.auditConfirmRemove',
            'Remove {count} matching images from this dataset? Original files will not be deleted.',
            { count: stats.total });
        if (!window.confirm(msg)) return;

        const removeIds = new Set(stats.loadedIds.map(Number));
        for (const path of flaggedPaths) {
            DM._excludeLocalPathFromManifests?.(path);
        }
        for (const id of removeIds) {
            if (DM.isLocalId?.(id)) DM._markLocalManifestExcluded?.(id);
            DM.captions?.delete?.(id);
            if (typeof DM._deleteCaptionEditForDatasetRemoval === 'function') {
                DM._deleteCaptionEditForDatasetRemoval(id);
            } else {
                DM.captionEdits?.delete?.(id);
            }
            DM._undoStacks?.delete?.(id);
            DM._queueSelection?.delete?.(id);
            // NL/type maps too — deterministic local ids resurface leaked
            // entries on re-import (2026-07 pin-sweep finding #2).
            DM.nlCaptions?.delete?.(id);
            DM.nlEdits?.delete?.(id);
            DM.captionType?.delete?.(id);
            if (DM.localItemPaths && DM.isLocalId?.(id)) {
                DM.localItemPaths.delete(id);
                DM.localItemDsIds?.delete?.(id);
            }
        }
        DM.imageIds = (DM.imageIds || []).filter((id) => !removeIds.has(Number(id)));
        if (DM.activeId != null && !DM.imageIds.includes(Number(DM.activeId))) {
            DM.activeId = DM.imageIds.length ? Number(DM.imageIds[0]) : null;
        }
        DM._queueSelection?.clear?.();
        DM._renderQueue?.();
        DM._renderImportGallery?.();
        DM._updateCount?.();
        DM._updateExportEnabled?.();
        DM._updateMultiSelectUI?.();
        if (DM.activeId != null) DM._setActive?.(DM.activeId);
        else DM._renderEmptyEditor?.();
        clearAuditResultsAfterMutation();
        if (wasTruncated) {
            setStatus(DM._t('dataset.auditRemovedPartialStatus',
                'Removed {count} returned matching images. Audit found {total}; run audit again to continue.',
                { count: stats.total, total: summaryCount }));
        } else {
            setStatus(DM._t('dataset.auditRemovedStatus',
                'Removed {count} matching images from the dataset. Run audit again to verify the remaining set.',
                { count: stats.total }));
        }
    }

    function goToAuditWorkbench(flag = preferredAuditFlag()) {
        if (flag) {
            AUDIT_STATE.activeFilter = flag;
            applyFilterToQueue();
            updateBadgeState();
            focusFirstAuditMatch(flag);
        }
        DM._setPipelineTab?.('workbench');
        renderAuditNextSteps();
    }

    function makeAuditAction(label, className, handler) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = className || 'btn btn-ghost btn-small';
        btn.textContent = label;
        btn.addEventListener('click', handler);
        return btn;
    }

    function renderAuditNextSteps() {
        const wrap = $('dataset-audit-results');
        if (!wrap || !AUDIT_STATE.lastReport) return;
        const existing = $('dataset-audit-next-steps');
        if (existing) existing.remove();
        const issues = auditIssueOptions();
        if (issues.length === 0) return;
        const flag = preferredAuditFlag();
        const issue = issues.find((it) => it.flag === flag) || issues[0];
        if (!issue) return;

        const panel = document.createElement('div');
        panel.className = 'dataset-audit-next-steps';
        panel.id = 'dataset-audit-next-steps';

        const title = document.createElement('div');
        title.className = 'dataset-audit-next-title';
        title.textContent = DM._t('dataset.auditNextTitle',
            'Next step: {label}', { label: issue.label });

        const copy = document.createElement('p');
        copy.className = 'dataset-audit-next-copy';
        copy.textContent = issue.nextCopy;

        const actions = document.createElement('div');
        actions.className = 'dataset-audit-next-actions';
        actions.append(
            makeAuditAction(DM._t('dataset.auditActionSelect', 'Select matching'), 'btn btn-ghost btn-small',
                () => selectAuditMatches(issue.flag))
        );
        if (issue.removable) {
            actions.appendChild(makeAuditAction(
                issue.truncated
                    ? DM._t('dataset.auditActionRemoveReturned', 'Remove returned matches')
                    : DM._t('dataset.auditActionRemove', 'Remove matching from dataset'),
                'btn btn-danger btn-small',
                () => removeAuditMatches(issue.flag)
            ));
        }
        panel.append(title, copy);
        if (issue.truncated) {
            const warning = document.createElement('p');
            warning.className = 'dataset-audit-next-warning';
            warning.textContent = DM._t('dataset.auditNextTruncated',
                'Audit found {count} matching images, but only {known} were returned for browser actions. Download the report or run removal in passes; this button only affects returned matches.',
                {
                    count: issue.count,
                    known: knownAuditMatchCount(issue.flag),
                });
            panel.appendChild(warning);
        }
        panel.appendChild(actions);
        wrap.appendChild(panel);
    }

    function focusFirstAuditMatch(flag) {
        const { flaggedIds, flaggedPaths } = getAuditMatches(flag);
        for (const id of DM.imageIds || []) {
            const numericId = Number(id);
            const meta = DM.meta?.get?.(numericId) || {};
            const absPath = meta.abs_path || '';
            const match = (numericId > 0 && flaggedIds.has(numericId))
                || (absPath && flaggedPaths.has(absPath));
            if (match) {
                DM._setActive?.(numericId);
                break;
            }
        }
    }

    function applyFilterToQueue() {
        const flag = AUDIT_STATE.activeFilter;
        const items = document.querySelectorAll('#dataset-queue-list .dataset-queue-item');
        if (!flag || !AUDIT_STATE.lastReport) {
            for (const it of items) it.classList.remove('audit-flagged');
            return;
        }
        const { flaggedIds, flaggedPaths } = getAuditMatches(flag);

        for (const it of items) {
            const id = Number(it.dataset.imageId || 0);
            const meta = DM.meta?.get?.(id) || {};
            const absPath = meta.abs_path || '';
            const match = (id > 0 && flaggedIds.has(id))
                || (absPath && flaggedPaths.has(absPath));
            it.classList.toggle('audit-flagged', AUDIT_STATE.inverted ? !match : !!match);
        }
    }

    function renderResults(report) {
        AUDIT_STATE.lastReport = report;
        const wrap = $('dataset-audit-results');
        const badges = $('dataset-audit-badges');
        if (!wrap || !badges) return;
        badges.innerHTML = '';

        const t = (key, fb, params) => DM._t(key, fb, params);
        const summary = report.summary || {};
        const dupes = (report.duplicate_groups || []).length;

        if ((summary.low_quality_count || 0) > 0) {
            badges.appendChild(makeBadge('low_quality', t('dataset.auditBadgeLowQuality', 'Low quality'), summary.low_quality_count));
        }
        if ((summary.missing_count || 0) > 0) {
            badges.appendChild(makeBadge('missing', t('dataset.auditBadgeMissing', 'Missing / unreadable'), summary.missing_count));
        }
        if (dupes > 0) {
            badges.appendChild(makeBadge('duplicate', t('dataset.auditBadgeDuplicate', 'Duplicates'), dupes));
        }
        if ((summary.untagged_count || 0) > 0) {
            badges.appendChild(makeBadge('untagged', t('dataset.auditBadgeUntagged', 'Untagged'), summary.untagged_count));
        }
        if ((summary.small_count || 0) > 0) {
            badges.appendChild(makeBadge('small', t('dataset.auditBadgeSmall', 'Small'), summary.small_count));
        }
        if (badges.children.length === 0) {
            const ok = document.createElement('span');
            ok.className = 'dataset-audit-status';
            ok.textContent = t('dataset.auditAllClean', 'No issues found in the active checks.');
            badges.appendChild(ok);
            AUDIT_STATE.activeFilter = null;
        } else {
            AUDIT_STATE.activeFilter = preferredAuditFlag();
            // Invert toggle: flips all filters to show the opposite set
            const invertBtn = document.createElement('button');
            invertBtn.type = 'button';
            invertBtn.className = 'btn btn-ghost btn-small dataset-audit-invert-btn';
            invertBtn.textContent = '\u21C4';
            invertBtn.title = t('dataset.auditInvertHint', 'Invert filter (show items NOT matching)');
            invertBtn.classList.toggle('active', AUDIT_STATE.inverted);
            invertBtn.addEventListener('click', () => {
                AUDIT_STATE.inverted = !AUDIT_STATE.inverted;
                invertBtn.classList.toggle('active', AUDIT_STATE.inverted);
                applyFilterToQueue();
            });
            badges.appendChild(invertBtn);
        }

        if (summary.near_duplicate_checked) {
            const phashInfo = document.createElement('span');
            phashInfo.className = summary.near_duplicate_error
                ? 'dataset-audit-phash-status is-error'
                : 'dataset-audit-phash-status';
            phashInfo.textContent = summary.near_duplicate_error
                ? t('dataset.auditPhashUnavailable', 'Near-duplicate check unavailable: {error}', { error: summary.near_duplicate_error })
                : t('dataset.auditPhashChecked', 'Near-duplicate checked {count} images.', { count: summary.near_duplicate_hashes || 0 });
            badges.appendChild(phashInfo);
        }

        // Surface the O(N^2) cap as a first-class badge instead of a
        // hidden summary field. Above PHASH_NEAR_DUPLICATE_LIMIT the
        // backend degrades to exact-hash-only duplicate detection, so
        // the user needs to see WHY duplicates may be under-reported
        // rather than trusting the count blindly.
        if (summary.near_duplicate_check_limited) {
            const limitBadge = document.createElement('span');
            limitBadge.className = 'dataset-audit-badge dataset-audit-badge-limited';
            limitBadge.title = t('dataset.auditLimitedTip',
                'Near-duplicate detection was capped at {limit} images to keep the audit responsive. Run a smaller selection for a full near-duplicate pass.',
                { limit: 5000 });
            limitBadge.textContent = t('dataset.auditLimitedBadge', 'Near-duplicate check capped');
            badges.appendChild(limitBadge);
        }

        wrap.hidden = false;
        const dlBtn = $('btn-dataset-audit-download');
        if (dlBtn) dlBtn.hidden = false;

        // "Remove all flagged" button
        let removeBtn = $('btn-dataset-audit-remove-flagged');
        if (!removeBtn) {
            removeBtn = document.createElement('button');
            removeBtn.type = 'button';
            removeBtn.id = 'btn-dataset-audit-remove-flagged';
            removeBtn.className = 'btn btn-ghost btn-small dataset-audit-remove-btn';
            removeBtn.addEventListener('click', () => removeAuditMatches());
            wrap.appendChild(removeBtn);
        }
        removeBtn.textContent = t('dataset.auditRemoveFlagged', 'Remove all flagged');
        removeBtn.hidden = !AUDIT_STATE.activeFilter;

        updateBadgeState();
        applyFilterToQueue();
        renderAuditNextSteps();
        // M6: refresh residue strip text/visibility against the fresh
        // report (modal is typically open here, so strip stays hidden
        // until the user closes the modal).
        updateAuditResidueStrip();
    }

    // H3 fix: removed legacy ``removeFlaggedFromQueue`` (previously
    // here at ~L515-543). It was dead code with zero call sites — the
    // ``#btn-dataset-audit-remove-flagged`` handler at L504 already
    // routes through ``removeAuditMatches`` (the canonical path that
    // cleans up ``localItemPaths``, ``_undoStacks``, ``_queueSelection``,
    // and calls ``_excludeLocalPathFromManifests``). Keeping the legacy
    // function around risked future code accidentally wiring to the
    // half-baked cleanup path and corrupting local-import state.


    // ============== Audit residue strip (M6) ==============
    //
    // When the audit modal closes, ``AUDIT_STATE.lastReport`` and
    // ``AUDIT_STATE.activeFilter`` may still be active — the queue
    // shows grayed-out items, but the modal's badges (the only place
    // that named the filter) are gone. Without a residue indicator
    // users see filtered items with no way to know WHAT filter is
    // applied. This strip lives next to ``#btn-dataset-import-audit``
    // and shows the active filter + a "Clear" link.

    const AUDIT_RESIDUE_FLAG_LABELS = {
        missing: 'dataset.auditBadgeMissing',
        low_quality: 'dataset.auditBadgeLowQuality',
        untagged: 'dataset.auditBadgeUntagged',
        small: 'dataset.auditBadgeSmall',
        duplicate: 'dataset.auditBadgeDuplicate',
    };

    // Fallback English strings for the audit-residue strip when a label
    // key is missing. These intentionally mirror the values of the
    // existing ``dataset.auditBadge*`` keys so the strip reads
    // identically to the audit modal badges.
    const AUDIT_RESIDUE_FLAG_FALLBACKS = {
        missing: 'Missing / unreadable',
        low_quality: 'Low quality',
        untagged: 'Untagged',
        small: 'Small',
        duplicate: 'Duplicates',
    };

    function ensureAuditResidueStyles() {
        if (document.getElementById('dataset-audit-residue-styles')) return;
        const style = document.createElement('style');
        style.id = 'dataset-audit-residue-styles';
        style.textContent = `
            .dataset-audit-residue-strip {
                display: inline-flex;
                align-items: center;
                gap: 8px;
                margin-left: 8px;
                padding: 4px 10px;
                font-size: 12px;
                color: rgba(235, 235, 235, 0.85);
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 999px;
                white-space: nowrap;
                vertical-align: middle;
            }
            .dataset-audit-residue-strip[hidden] { display: none; }
            .dataset-audit-residue-label { opacity: 0.95; }
            .dataset-audit-residue-clear {
                background: transparent;
                border: 0;
                color: rgba(var(--accent-rgb), 0.95);
                cursor: pointer;
                padding: 0;
                font: inherit;
                text-decoration: underline;
            }
            .dataset-audit-residue-clear:hover { color: #fff; }
            .dataset-audit-residue-clear:focus-visible {
                outline: 2px solid rgba(var(--accent-rgb), 0.7);
                outline-offset: 2px;
                border-radius: 4px;
            }
        `;
        document.head.appendChild(style);
    }

    function getOrCreateAuditResidueStrip() {
        const anchor = $('btn-dataset-import-audit');
        if (!anchor || !anchor.parentNode) return null;
        let strip = document.getElementById('dataset-audit-residue-strip');
        if (strip) return strip;
        ensureAuditResidueStyles();
        strip = document.createElement('span');
        strip.id = 'dataset-audit-residue-strip';
        strip.className = 'dataset-audit-residue-strip';
        strip.setAttribute('role', 'status');
        strip.hidden = true;

        const label = document.createElement('span');
        label.className = 'dataset-audit-residue-label';
        strip.appendChild(label);

        const clearBtn = document.createElement('button');
        clearBtn.type = 'button';
        clearBtn.id = 'dataset-audit-residue-clear';
        clearBtn.className = 'dataset-audit-residue-clear';
        clearBtn.textContent = DM._t?.('common.clear', 'Clear') || 'Clear';
        clearBtn.addEventListener('click', () => {
            // Clear filter only — keep ``lastReport`` so the user can
            // re-open the modal to inspect it; just drop the highlight.
            AUDIT_STATE.activeFilter = null;
            AUDIT_STATE.inverted = false;
            applyFilterToQueue();
            updateBadgeState();
            renderAuditNextSteps();
            updateAuditResidueStrip();
        });
        strip.appendChild(clearBtn);

        // Insert immediately after the trigger button so it sits in the
        // same toolbar row.
        anchor.parentNode.insertBefore(strip, anchor.nextSibling);
        return strip;
    }

    function updateAuditResidueStrip() {
        const modal = $('dataset-audit-modal');
        const modalOpen = !!(modal && !modal.hidden);
        const shouldShow = !modalOpen
            && !!AUDIT_STATE.lastReport
            && !!AUDIT_STATE.activeFilter;
        const strip = shouldShow
            ? getOrCreateAuditResidueStrip()
            : document.getElementById('dataset-audit-residue-strip');
        if (!strip) return;
        if (!shouldShow) {
            strip.hidden = true;
            return;
        }
        const flag = AUDIT_STATE.activeFilter;
        const labelKey = AUDIT_RESIDUE_FLAG_LABELS[flag];
        const labelFallback = AUDIT_RESIDUE_FLAG_FALLBACKS[flag] || flag;
        const filterName = (labelKey && DM._t?.(labelKey, labelFallback)) || labelFallback;
        const count = knownAuditMatchCount(flag) || auditSummaryCount(flag);
        const labelEl = strip.querySelector('.dataset-audit-residue-label');
        if (labelEl) {
            const text = DM._t?.('dataset.auditResidueShowing',
                'Showing {filter} · {count} items',
                { filter: filterName, count })
                || `Showing ${filterName} · ${count} items`;
            // No trailing separator: .dataset-audit-residue-strip is an
            // inline-flex row with gap:8px, so the Clear button is already
            // spaced off and a dangling "·" would just hang there.
            labelEl.textContent = text;
        }
        const clearBtn = strip.querySelector('.dataset-audit-residue-clear');
        if (clearBtn) clearBtn.textContent = DM._t?.('common.clear', 'Clear') || 'Clear';
        strip.hidden = false;
    }

    DM._showAuditModal = function () {
        const modal = $('dataset-audit-modal');
        if (modal) modal.hidden = false;
        // M6 fix: hide the residue strip while the modal is open — the
        // modal's own badges convey the same information in higher
        // fidelity.
        updateAuditResidueStrip();
    };

    DM._hideAuditModal = function () {
        const modal = $('dataset-audit-modal');
        if (modal) modal.hidden = true;
        // M6 fix: surface the active audit filter near the trigger
        // button so users still see WHAT is filtering the queue after
        // the modal goes away. Without this strip the queue shows
        // grayed-out items with no indication of why.
        updateAuditResidueStrip();
    };
    DM._applyAuditFilterToQueue = applyFilterToQueue;
    DM._auditState = AUDIT_STATE;

    // Split bridge (audit.js / audit-run.js, forced non-verbatim ADDITION):
    // runAudit lives in dataset/audit-run.js but must invoke renderResults
    // (this module's scope). Exported on DM so audit-run.js can shim a
    // local `renderResults` and keep its moved lines verbatim.
    DM._renderAuditResults = renderResults;

    // Split of dataset-maker-pipeline.js's single init() (forced
    // non-verbatim): the original init() called binder functions that now
    // live in separate module scopes (audit / audit-run / vocab /
    // defaults-pairchip / export-preview). Each module self-binds with the
    // identical readiness gate; module load order (pinned in
    // dataset/core.js) preserves the original call order bindTabs ->
    // bindAudit -> bindVocab -> bindAnimeDefaults -> bindPairChip ->
    // bindExportPreview.
    function init() {
        bindTabs();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init, { once: true });
    } else {
        init();
    }
})();
