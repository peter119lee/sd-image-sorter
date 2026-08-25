/**
 * Dataset Maker — event wiring: toolbar / caption editor / preset / output / export button bindings (_bindEvents).
 * Moved VERBATIM from dataset-maker.js L267-555.
 * Load order is pinned by the ordered async=false loader in dataset/core.js.
 */
(function () {
    'use strict';
    if (!window.DatasetMaker) return;
    const DM = window.DatasetMaker;

    const normalizeCaptionToken = (value) => String(value || '')
        .replace(/[\s_]+/g, ' ')
        .trim()
        .toLowerCase();

    const canonicalDatasetTrigger = (value) => DM._canonicalDatasetTrigger(String(value || ''));
    const datasetTriggerIssue = (value) => DM._datasetTriggerIssue(String(value || ''));

    const captionListLength = (value) => String(value || '')
        .split(/[\n,]+/)
        .map((part) => part.trim())
        .filter(Boolean)
        .length;

    const snapshotMapEntries = (map, ids) => ids.map((id) => Object.freeze({
        id,
        present: map.has(id),
        value: map.get(id),
    }));

    const captureCaptionRefreshState = (datasetMaker, ids) => {
        const imageIds = Array.from(ids || []).map(Number);
        return Object.freeze({
            captions: snapshotMapEntries(datasetMaker.captions, imageIds),
            nlCaptions: snapshotMapEntries(datasetMaker.nlCaptions, imageIds),
            meta: snapshotMapEntries(datasetMaker.meta, imageIds),
        });
    };

    const restoreMapEntries = (map, entries) => {
        for (const entry of entries) {
            if (entry.present) map.set(entry.id, entry.value);
            else map.delete(entry.id);
        }
    };

    const restoreCaptionRefreshState = (datasetMaker, snapshot) => {
        restoreMapEntries(datasetMaker.captions, snapshot.captions);
        restoreMapEntries(datasetMaker.nlCaptions, snapshot.nlCaptions);
        restoreMapEntries(datasetMaker.meta, snapshot.meta);
        datasetMaker._refreshActiveCaptionBoxes?.();
        datasetMaker._renderQueue?.();
    };

    const captionTokenEntries = (caption) => {
        const chunks = String(caption || '').trim().split(/([,\r\n]+[ \t]*)/);
        const entries = [];
        let separator = '';
        for (let index = 0; index < chunks.length; index += 1) {
            const chunk = chunks[index];
            if (index % 2 === 1) {
                separator += chunk;
                continue;
            }
            const token = chunk.trim();
            if (!token) continue;
            entries.push({ separator, token });
            separator = '';
        }
        return entries;
    };

    const preservedCaptionSeparator = (separators) => {
        const combined = separators.join('');
        const newline = combined.match(/\r\n|\r|\n/);
        if (!newline) return separators.at(-1) || ', ';
        const trailingWhitespace = combined.match(/(?:\r\n|\r|\n)([ \t]*)[^\r\n]*$/);
        return `${newline[0]}${trailingWhitespace?.[1] || ''}`;
    };

    const addCaptionToken = (caption, token) => {
        const cleanToken = String(token || '').trim();
        const original = String(caption || '').trim();
        if (!cleanToken) return original;
        const entries = captionTokenEntries(original);
        const tokenKey = normalizeCaptionToken(cleanToken);
        const matchCount = entries.filter((entry) => (
            normalizeCaptionToken(entry.token) === tokenKey
        )).length;
        if (matchCount === 0) {
            return original ? `${cleanToken}, ${original}` : cleanToken;
        }
        if (matchCount === 1 && entries.some((entry) => entry.token === cleanToken)) {
            return original;
        }

        const kept = [];
        const skippedSeparators = [];
        let found = false;
        for (const entry of entries) {
            const isMatch = normalizeCaptionToken(entry.token) === tokenKey;
            if (isMatch && found) {
                skippedSeparators.push(entry.separator);
                continue;
            }
            if (isMatch) found = true;
            const separator = kept.length === 0
                ? ''
                : preservedCaptionSeparator([...skippedSeparators, entry.separator]);
            kept.push({
                separator,
                token: isMatch ? cleanToken : entry.token,
            });
            skippedSeparators.length = 0;
        }
        return kept.map((entry) => `${entry.separator}${entry.token}`).join('');
    };

    const replaceCaptionToken = (caption, previousToken, nextToken) => {
        const original = String(caption || '').trim();
        const cleanNextToken = String(nextToken || '').trim();
        const previousKey = normalizeCaptionToken(previousToken);
        const nextKey = normalizeCaptionToken(cleanNextToken);
        if (!previousKey || previousKey === nextKey) {
            return addCaptionToken(original, cleanNextToken);
        }
        const kept = [];
        const skippedSeparators = [];
        let replaced = false;
        for (const entry of captionTokenEntries(original)) {
            const isPrevious = normalizeCaptionToken(entry.token) === previousKey;
            if (isPrevious && replaced) {
                skippedSeparators.push(entry.separator);
                continue;
            }
            const separator = kept.length === 0
                ? ''
                : preservedCaptionSeparator([...skippedSeparators, entry.separator]);
            kept.push({
                separator,
                token: isPrevious ? cleanNextToken : entry.token,
            });
            if (isPrevious) replaced = true;
            skippedSeparators.length = 0;
        }
        if (!replaced) return addCaptionToken(original, cleanNextToken);
        const rewritten = kept.map((entry) => `${entry.separator}${entry.token}`).join('');
        return addCaptionToken(rewritten, cleanNextToken);
    };

    const replaceCommonTag = (value, previousToken, nextToken) => {
        const cleanToken = String(nextToken || '').trim();
        const previousKey = normalizeCaptionToken(previousToken);
        const nextKey = normalizeCaptionToken(cleanToken);
        const parts = String(value || '')
            .split(/[\n,]+/)
            .map((part) => part.trim())
            .filter(Boolean);
        const updated = [];
        let inserted = false;
        for (const part of parts) {
            const key = normalizeCaptionToken(part);
            if (key !== previousKey && key !== nextKey) {
                updated.push(part);
                continue;
            }
            if (!inserted && cleanToken && nextKey) {
                updated.push(cleanToken);
                inserted = true;
            }
        }
        if (!inserted && cleanToken && nextKey) updated.push(cleanToken);
        return updated.join(', ');
    };

    const removeCaptionToken = (caption, token) => {
        const original = String(caption || '').trim();
        const tokenKey = normalizeCaptionToken(token);
        if (!tokenKey) return original;
        const kept = [];
        const skippedSeparators = [];
        for (const entry of captionTokenEntries(original)) {
            if (normalizeCaptionToken(entry.token) === tokenKey) {
                skippedSeparators.push(entry.separator);
                continue;
            }
            const separator = kept.length === 0
                ? ''
                : preservedCaptionSeparator([...skippedSeparators, entry.separator]);
            kept.push({ separator, token: entry.token });
            skippedSeparators.length = 0;
        }
        return kept.map((entry) => `${entry.separator}${entry.token}`).join('');
    };

    const removeCommonTag = (value, token) => {
        const tokenKey = normalizeCaptionToken(token);
        if (!tokenKey) return String(value || '').trim();
        return String(value || '')
            .split(/[\n,]+/)
            .map((part) => part.trim())
            .filter((part) => part && normalizeCaptionToken(part) !== tokenKey)
            .join(', ');
    };

    const blacklistTokenKey = (value) => String(value || '')
        .trim()
        .replace(/\s+/g, ' ')
        .toLowerCase();

    const triggerBlacklistVariants = (token) => {
        const original = String(token || '').trim();
        const spaced = original.replace(/[\s_]+/g, ' ').trim();
        const underscored = spaced.replace(/\s+/g, '_');
        const seen = new Set();
        return [original, spaced, underscored].filter((variant) => {
            const key = blacklistTokenKey(variant);
            if (!key || seen.has(key)) return false;
            seen.add(key);
            return true;
        });
    };

    const appendBlacklistTokens = (value, tokens) => {
        const original = String(value || '');
        const existingKeys = new Set(
            original
                .split(/[\n,]+/)
                .map(blacklistTokenKey)
                .filter(Boolean),
        );
        const additions = [];
        for (const token of tokens) {
            const key = blacklistTokenKey(token);
            if (!key || existingKeys.has(key)) continue;
            existingKeys.add(key);
            additions.push(token);
        }
        if (additions.length === 0) return original;
        const separator = !original || /(?:\r\n|\r|\n)$/.test(original) ? '' : '\n';
        return `${original}${separator}${additions.join('\n')}`;
    };

    // The members below are VERBATIM object-literal members lifted from
    // dataset-maker.js's original `const DM = {...}` literal. Object.assign
    // attaches them to the same window.DatasetMaker instance; call-time
    // `this` binding (DM.method() -> this === DM) is identical.
    Object.assign(DM, {

        _syncTriggerQuickfillButton() {
            const trigger = document.getElementById('dataset-trigger');
            const button = document.getElementById('btn-dataset-quickfill-trigger');
            if (!trigger || !button) return;
            button.disabled = !canonicalDatasetTrigger(trigger.value);
        },

        _bindEvents() {
            // Toolbar
            document.getElementById('btn-dataset-import-gallery')?.addEventListener('click', () => this._importFromGallery());
            document.getElementById('btn-dataset-clear')?.addEventListener('click', () => this._clearAll());

            // Tag all
            document.getElementById('btn-dataset-tag-all')?.addEventListener('click', () => this._tagAll());

            // Smart Tag button - opens the Smart Tag modal (reuses Gallery's modal)
            document.getElementById('btn-dataset-smart-tag')?.addEventListener('click', () => {
                if (typeof window.SmartTag?.open === 'function') {
                    const datasetTrigger = document.getElementById('dataset-trigger');
                    const smartTagTrigger = document.getElementById('smart-tag-trigger');
                    if (!datasetTrigger || !smartTagTrigger) {
                        throw new TypeError('Dataset Smart Tag trigger controls are unavailable.');
                    }
                    const rawTrigger = datasetTrigger.value;
                    const issue = datasetTriggerIssue(rawTrigger);
                    if (issue === 'format' || issue === 'normalized-empty') {
                        datasetTrigger.value = String(this._lastValidDatasetTrigger || '');
                        this._syncTriggerQuickfillButton();
                        const key = issue === 'format'
                            ? 'dataset.quickfillTriggerInvalid'
                            : 'dataset.quickfillTriggerInvalidEmpty';
                        const fallback = issue === 'format'
                            ? 'Trigger word must be one token of 100 characters or fewer, cannot contain commas or line breaks, and cannot contain internal whitespace.'
                            : 'Trigger word must contain characters other than spaces or underscores.';
                        this._toast(this._t(key, fallback), 'error', 6000);
                        return;
                    }
                    const trigger = canonicalDatasetTrigger(rawTrigger);
                    if (datasetTrigger.value !== trigger) {
                        datasetTrigger.value = trigger;
                        datasetTrigger.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                    smartTagTrigger.value = trigger;
                    smartTagTrigger.dispatchEvent(new Event('input', { bubbles: true }));
                    window.SmartTag.open();
                } else {
                    this._toast(this._t('dataset.smartTagUnavailable',
                        'Smart Tag feature is not available.'), 'error', 3000);
                }
            });

            // P10: Add to collection button
            document.getElementById('btn-dataset-add-to-collection')?.addEventListener('click', () => {
                if (this.imageIds.length === 0) {
                    this._toast(this._t('dataset.queueEmptyHeadline', 'No images yet'), 'warning');
                    return;
                }
                // Filter out local-source images (negative IDs) as they cannot be added to collections
                const galleryIds = this.imageIds.filter((id) => !(this.isLocalId && this.isLocalId(id)));
                if (galleryIds.length === 0) {
                    this._toast(this._t('dataset.addToCollectionOnlyGallery',
                        'Only gallery-source images can be added to collections. Scan the folder into the main library first.'),
                        'warning', 6000);
                    return;
                }
                if (typeof window.CollectionsUI?.openAddToCollectionPicker === 'function') {
                    window.CollectionsUI.openAddToCollectionPicker(galleryIds);
                } else {
                    this._toast(this._t('dataset.addToCollectionUnavailable',
                        'Collections feature is not available.'), 'error', 3000);
                }
            });

            // Quality-tags quick-fill: one click adds common LoRA quality
            // tags to the "Common tags" field. Also exposes a "use my
            // trigger word here" shortcut so users don't have to figure
            // out the filename-vs-caption distinction on their own.
            document.getElementById('btn-dataset-quickfill-quality')?.addEventListener('click', () => {
                const ta = document.getElementById('dataset-common-tags');
                if (!ta) return;
                const recommended = 'masterpiece, best_quality';
                const current = (ta.value || '').trim();
                const tokens = new Set(current.split(',').map(s => s.trim()).filter(Boolean));
                for (const tok of recommended.split(',').map(s => s.trim())) tokens.add(tok);
                ta.value = Array.from(tokens).join(', ');
                ta.dispatchEvent(new Event('input', { bubbles: true }));
                this._toast(this._t('dataset.quickfillQualityDone',
                    'Added recommended quality tags to "Common tags".'), 'success', 3000);
            });
            document.getElementById('btn-dataset-cleanup-trigger')?.addEventListener('click', async () => {
                this._flushPendingDatasetEdits?.();
                const logicalCount = typeof this._getLogicalDatasetCount === 'function'
                    ? Number(this._getLogicalDatasetCount())
                    : this.imageIds.length;
                if (!Number.isFinite(logicalCount) || logicalCount <= 0) {
                    this._toast(this._t('dataset.queueEmptyHeadline', 'No images yet'), 'warning', 4000);
                    return;
                }
                if (
                    this._activeProject
                    && (
                        typeof this._annotationHeadsReadyForProject !== 'function'
                        || !this._annotationHeadsReadyForProject(this._activeProject)
                    )
                ) {
                    this._toast(this._t('dataset.cleanupTriggerProjectUnavailable',
                        'Dataset project caption versions are unavailable. Reload the project before cleaning an old trigger.'),
                    'error', 6000);
                    return;
                }
                if (typeof window.App?.showInputModal !== 'function') {
                    throw new TypeError('Dataset old-trigger cleanup input is unavailable.');
                }
                if (typeof this._triggerQuickfillSignature !== 'function') {
                    throw new TypeError('Dataset trigger signature function is unavailable.');
                }
                const cleanupContextSignature = this._triggerQuickfillSignature(
                    document.getElementById('dataset-trigger')?.value || '',
                    document.getElementById('dataset-common-tags')?.value || '',
                );
                const requestedToken = await window.App.showInputModal(
                    this._t('dataset.cleanupTriggerTitle', 'Remove old trigger'),
                    this._t('dataset.cleanupTriggerPrompt',
                        'Enter the exact old trigger to remove from Common tags and all applicable captions.'),
                    '',
                );
                if (requestedToken === null) return;
                const currentContextSignature = this._triggerQuickfillSignature(
                    document.getElementById('dataset-trigger')?.value || '',
                    document.getElementById('dataset-common-tags')?.value || '',
                );
                if (currentContextSignature !== cleanupContextSignature) {
                    this._toast(this._t('dataset.cleanupTriggerSuperseded',
                        'Dataset changed while the cleanup dialog was open. No trigger cleanup was applied; retry on the current project.'),
                    'error', 7000);
                    return;
                }
                const staleToken = String(requestedToken).trim();
                if (!staleToken || staleToken.length > 100 || /[,\r\n]/.test(staleToken)) {
                    this._toast(this._t('dataset.cleanupTriggerInvalid',
                        'Old trigger must be 100 characters or fewer and cannot contain commas or line breaks.'),
                    'error', 6000);
                    return;
                }
                if (!normalizeCaptionToken(staleToken)) {
                    this._toast(this._t('dataset.quickfillTriggerInvalidEmpty',
                        'Trigger word must contain characters other than spaces or underscores.'),
                    'error', 6000);
                    return;
                }
                const managedTrigger = String(this._quickfilledTrigger || '').trim();
                if (
                    managedTrigger
                    && normalizeCaptionToken(staleToken) === normalizeCaptionToken(managedTrigger)
                ) {
                    this._toast(this._t('dataset.cleanupTriggerIsCurrent',
                        '"{trigger}" is the current trigger. Replace it with Add to captions instead of deleting it.',
                        { trigger: staleToken }), 'warning', 6000);
                    return;
                }
                const commonTags = document.getElementById('dataset-common-tags');
                if (!commonTags) {
                    throw new TypeError('Dataset Common tags input is unavailable.');
                }
                const blacklist = document.getElementById('dataset-blacklist');
                if (!blacklist) {
                    throw new TypeError('Dataset blacklist input is unavailable.');
                }
                if (typeof this._removeHistoricalTriggerFromCaptions !== 'function') {
                    throw new TypeError('Dataset old-trigger caption cleanup is unavailable.');
                }
                const nextCommonTags = removeCommonTag(commonTags.value, staleToken);
                const commonTagsChanged = nextCommonTags !== commonTags.value;
                const nextBlacklist = appendBlacklistTokens(
                    blacklist.value,
                    triggerBlacklistVariants(staleToken),
                );
                const blacklistChanged = nextBlacklist !== blacklist.value;
                if (captionListLength(nextBlacklist) > 1000) {
                    this._toast(this._t('dataset.cleanupTriggerBlacklistLimit',
                        'Old-trigger cleanup would exceed the blacklist limit of 1,000 entries. Remove another blacklist entry first; no captions were changed.'),
                    'error', 8000);
                    return;
                }
                let changedCaptions;
                try {
                    changedCaptions = this._removeHistoricalTriggerFromCaptions(
                        this.imageIds,
                        staleToken,
                    );
                } catch (error) {
                    const reason = error instanceof Error ? error.message : String(error);
                    this._toast(this._t('dataset.cleanupTriggerPersistenceFailed',
                        'Could not remove the old trigger because local caption persistence failed: {reason} No captions or settings were changed.',
                        { reason }), 'error', 8000);
                    return;
                }
                if (commonTagsChanged) {
                    commonTags.value = nextCommonTags;
                    commonTags.dispatchEvent(new Event('input', { bubbles: true }));
                }
                if (blacklistChanged) {
                    blacklist.value = nextBlacklist;
                    blacklist.dispatchEvent(new Event('input', { bubbles: true }));
                }
                if (!commonTagsChanged && !blacklistChanged && changedCaptions === 0) {
                    this._toast(this._t('dataset.cleanupTriggerNotFound',
                        '"{trigger}" was not found in Common tags or applicable captions.',
                        { trigger: staleToken }), 'info', 4000);
                    return;
                }
                this._markReadinessStale?.();
                this._renderReadiness?.();
                this._updateExportEnabled?.();
                this._scheduleSaveSession?.();
                this._refreshExportPreview?.();
                this._toast(this._t('dataset.cleanupTriggerDone',
                    'Removed old trigger "{trigger}" from Common tags and {count} captions, and blocked it from returning in hidden items.',
                    { trigger: staleToken, count: changedCaptions }), 'success', 5000);
            });
            document.getElementById('btn-dataset-quickfill-trigger')?.addEventListener('click', async () => {
                const button = document.getElementById('btn-dataset-quickfill-trigger');
                try {
                    this._flushPendingDatasetEdits?.();
                } catch (error) {
                    const reason = error instanceof Error ? error.message : String(error);
                    this._toast(this._t('dataset.quickfillTriggerFailed',
                        'Could not refresh every generated caption: {reason} No quickfill changes were saved.',
                        { reason }), 'error', 8000);
                    this._syncTriggerQuickfillButton();
                    return;
                }
                const triggerInput = document.getElementById('dataset-trigger');
                const rawTrigger = triggerInput?.value || '';
                const trigger = canonicalDatasetTrigger(rawTrigger);
                const triggerTimer = this._datasetFieldTimers?.get('dataset-trigger');
                if (triggerTimer) clearTimeout(triggerTimer);
                this._datasetFieldTimers?.delete('dataset-trigger');
                const issue = datasetTriggerIssue(rawTrigger);
                if (issue === 'empty') {
                    this._toast(this._t('dataset.quickfillTriggerEmpty',
                        'Type a trigger word in LoRA setup first.'), 'warning', 4000);
                    return;
                }
                if (issue === 'format') {
                    if (triggerInput) {
                        triggerInput.value = String(this._lastValidDatasetTrigger || '');
                    }
                    this._syncTriggerQuickfillButton();
                    this._toast(this._t('dataset.quickfillTriggerInvalid',
                        'Trigger word must be one token of 100 characters or fewer, cannot contain commas or line breaks, and cannot contain internal whitespace.'),
                    'error', 6000);
                    return;
                }
                if (issue === 'normalized-empty') {
                    if (triggerInput) {
                        triggerInput.value = String(this._lastValidDatasetTrigger || '');
                    }
                    this._syncTriggerQuickfillButton();
                    this._toast(this._t('dataset.quickfillTriggerInvalidEmpty',
                        'Trigger word must contain characters other than spaces or underscores.'),
                    'error', 6000);
                    return;
                }
                if (triggerInput && triggerInput.value !== trigger) {
                    triggerInput.value = trigger;
                }
                const ta = document.getElementById('dataset-common-tags');
                if (!ta || !button) return;
                for (const fieldId of ['dataset-trigger', 'dataset-common-tags']) {
                    const timer = this._datasetFieldTimers?.get(fieldId);
                    if (timer) clearTimeout(timer);
                    this._datasetFieldTimers?.delete(fieldId);
                }
                const previousCommonTags = ta.value;
                const previousManagedTrigger = String(this._quickfilledTrigger || '').trim();
                const mergedCommonTags = replaceCommonTag(
                    ta.value,
                    previousManagedTrigger,
                    trigger,
                );
                const commonTagsChanged = ta.value !== mergedCommonTags;
                if (typeof this._triggerQuickfillSignature !== 'function') {
                    throw new TypeError('Dataset trigger signature function is unavailable.');
                }
                const captionOptionsSignature = this._triggerQuickfillSignature(
                    trigger,
                    mergedCommonTags,
                );
                const captionRefreshSnapshot = captureCaptionRefreshState(this, this.imageIds);
                button.disabled = true;
                button.setAttribute('aria-busy', 'true');
                try {
                    if (typeof this._refreshAllCaptionsForTrigger !== 'function') {
                        throw new TypeError('Dataset trigger refresh function is unavailable.');
                    }
                    const refreshResult = await this._refreshAllCaptionsForTrigger(
                        trigger,
                        mergedCommonTags,
                        captionOptionsSignature,
                    );
                    if (refreshResult.status !== 'applied') {
                        const superseded = refreshResult.status === 'superseded';
                        const key = superseded
                            ? 'dataset.quickfillTriggerSuperseded'
                            : 'dataset.quickfillTriggerFailed';
                        const fallback = superseded
                            ? 'Caption settings changed while the trigger was being applied. Your newer input was kept; click Add to captions again.'
                            : 'Could not refresh every generated caption: {reason} No quickfill changes were saved.';
                        this._toast(this._t(key, fallback, {
                            reason: refreshResult.error,
                        }), 'error', 8000);
                        return;
                    }
                    if (this._triggerQuickfillSignature(trigger, mergedCommonTags) !== captionOptionsSignature) {
                        this._toast(this._t('dataset.quickfillTriggerSuperseded',
                            'Caption settings changed while the trigger was being applied. Your newer input was kept; click Add to captions again.'),
                        'error', 8000);
                        return;
                    }
                    ta.value = mergedCommonTags;
                    if (typeof this._replaceManagedTriggerInCaptions !== 'function') {
                        throw new TypeError('Dataset trigger caption writer is unavailable.');
                    }
                    const changedCaptions = this._replaceManagedTriggerInCaptions(
                        this.imageIds,
                        previousManagedTrigger,
                        trigger,
                    );
                    this._quickfilledTrigger = trigger;
                    this._lastValidDatasetTrigger = trigger;
                    this._scheduleSaveSession?.();
                    if (commonTagsChanged) {
                        ta.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                    if (!commonTagsChanged && changedCaptions === 0) {
                        this._toast(this._t('dataset.quickfillTriggerAlreadyApplied',
                            '"{trigger}" is already present in every applicable caption.',
                            { trigger }), 'info', 3000);
                        return;
                    }
                    this._toast(this._t('dataset.quickfillTriggerDone',
                        'Set "{trigger}" as the current caption trigger. It will appear exactly once in every caption .txt.',
                        { trigger }), 'success', 4000);
                } catch (error) {
                    restoreCaptionRefreshState(this, captionRefreshSnapshot);
                    if (ta.value === mergedCommonTags) ta.value = previousCommonTags;
                    const reason = error instanceof Error ? error.message : String(error);
                    this._toast(this._t('dataset.quickfillTriggerFailed',
                        'Could not refresh every generated caption: {reason} No quickfill changes were saved.',
                        { reason }), 'error', 8000);
                } finally {
                    button.removeAttribute('aria-busy');
                    this._syncTriggerQuickfillButton();
                }
            });

            // Caption editor actions
            document.getElementById('btn-dataset-prev-image')?.addEventListener('click', () => this._stepActive(-1));
            document.getElementById('btn-dataset-next-image')?.addEventListener('click', () => this._stepActive(1));
            document.getElementById('btn-dataset-revert-caption')?.addEventListener('click', () => this._revertActiveCaption());
            document.getElementById('btn-dataset-undo-caption')?.addEventListener('click', () => {
                const ta = document.getElementById('dataset-editor-textarea');
                if (!ta || this.activeId == null) return;
                const stack = this._undoStacks.get(this.activeId);
                if (!stack || stack.length === 0) return;
                const prev = stack.pop();
                ta.value = prev;
                this.captionEdits.set(this.activeId, prev);
                this._refreshQueueItem(this.activeId);
                this._renderTagPills();
            });
            document.getElementById('btn-dataset-drop-image')?.addEventListener('click', () => {
                if (this.activeId != null) this._dropImageForReview(this.activeId);
            });
            document.getElementById('btn-dataset-remove-image')?.addEventListener('click', () => this._removeActive());
            document.getElementById('btn-dataset-dedupe-tags')?.addEventListener('click', () => this._dedupeCaptionTags?.());

            // Caption textarea
            const ta = document.getElementById('dataset-editor-textarea');
            if (ta) {
                let lastSaved = null;
                ta.addEventListener('input', () => {
                    if (this.activeId == null) return;
                    this._supersedeCaptionFetch?.();
                    this._markReadinessStale?.();
                    if (this._captionInputTimer) clearTimeout(this._captionInputTimer);
                    const id = Number(this.activeId);
                    const value = ta.value;
                    this._pendingCaptionEdit = { id, value };
                    this._captionInputTimer = setTimeout(() => {
                        this._captionInputTimer = null;
                        this._pendingCaptionEdit = null;
                        const prev = this.captionEdits.has(id)
                            ? this.captionEdits.get(id)
                            : (this.captions.get(id) || '');
                        if (prev !== value && prev !== lastSaved) {
                            const stack = this._undoStacks.get(id) || [];
                            stack.push(prev);
                            if (stack.length > 20) stack.shift();
                            this._undoStacks.set(id, stack);
                        }
                        lastSaved = value;
                        this.captionEdits.set(id, value);
                        this._refreshQueueItem(id);
                        this._renderTagPills();
                    }, 200);
                });
                ta.addEventListener('keydown', (e) => {
                    if (e.ctrlKey && e.key === 'z' && !e.shiftKey) {
                        const id = this.activeId;
                        if (id == null) return;
                        const stack = this._undoStacks.get(id);
                        if (!stack || stack.length === 0) return;
                        e.preventDefault();
                        const prev = stack.pop();
                        ta.value = prev;
                        lastSaved = prev;
                        this.captionEdits.set(id, prev);
                        this._refreshQueueItem(id);
                        this._renderTagPills();
                    }
                });
            }

            // Naming preset radios
            document.querySelectorAll('input[name="dataset-naming-preset"]').forEach(radio => {
                radio.addEventListener('change', () => this._onPresetChange());
            });

            // P2 fix: Copy vs Move radios mirror to the (now hidden) select
            // that backend code reads from. The new radios are the visible
            // source of truth; the select acts as a compatibility shim.
            document.querySelectorAll('input[name="dataset-image-op-radio"]').forEach(radio => {
                radio.addEventListener('change', () => {
                    const hidden = document.getElementById('dataset-image-op');
                    if (hidden) hidden.value = radio.value;
                    this._markReadinessStale?.();
                    this._syncOutputModeUi?.();
                });
            });

            // Add-to-captions is an explicit user edit. Keep generated rows,
            // manual overrides, and saved-project drafts consistent with the
            // same trigger instead of relying on export-time template fields.
            this._applyCaptionTriggerUpdates = (updates) => {
                const previousRestoringSession = this._restoringSession;
                this._restoringSession = true;
                try {
                    for (const update of updates) {
                        if (update.channel === 'nl') this.nlEdits.set(update.id, update.value);
                        else this.captionEdits.set(update.id, update.value);
                        const id = update.id;
                        this._refreshQueueItem?.(id);
                        if (Number(this.activeId) === id) {
                            const textarea = document.getElementById(
                                update.channel === 'nl'
                                    ? 'dataset-editor-nl'
                                    : 'dataset-editor-textarea',
                            );
                            if (textarea) textarea.value = update.value;
                        }
                    }
                } finally {
                    this._restoringSession = previousRestoringSession;
                }
                if (updates.length) {
                    this._markReadinessStale?.();
                    this._renderReadiness?.();
                    this._updateExportEnabled?.();
                    this._renderTagPills?.();
                    this._renderQueue?.();
                    this._renderAnnotationLedger?.();
                    this._scheduleSaveSession?.();
                    this._refreshExportPreview?.();
                }
                return updates.length;
            };

            this._captionTriggerUpdates = (ids, transformCaption, shouldWriteCaption) => {
                const requestedIds = Array.from(ids || []).map(Number);
                const queuedIds = new Set(this.imageIds.map(Number));
                for (const id of requestedIds) {
                    if (!Number.isSafeInteger(id) || !queuedIds.has(id)) {
                        throw new RangeError(
                            `Trigger caption image_id=${id} is not in the current Dataset queue.`,
                        );
                    }
                }
                const seenIds = new Set();
                const updates = [];
                for (const id of requestedIds) {
                    if (seenIds.has(id)) continue;
                    seenIds.add(id);
                    const captionType = typeof this._captionTypeFor === 'function'
                        ? this._captionTypeFor(id)
                        : 'booru';
                    if (captionType === 'nl') {
                        if (typeof this._nlTextFor !== 'function') {
                            throw new TypeError('Dataset NL caption resolver is unavailable.');
                        }
                        const current = this._nlTextFor(id);
                        const next = transformCaption(current);
                        if (!shouldWriteCaption(id, 'nl', current, next)) continue;
                        updates.push({ id, channel: 'nl', value: next });
                        continue;
                    }
                    const current = typeof this._booruTextFor === 'function'
                        ? this._booruTextFor(id)
                        : (this.captionEdits.has(id)
                            ? (this.captionEdits.get(id) || '')
                            : (this.captions.get(id) || ''));
                    const next = transformCaption(current);
                    if (!shouldWriteCaption(id, 'booru', current, next)) continue;
                    updates.push({ id, channel: 'booru', value: next });
                }
                return updates;
            };

            this._replaceManagedTriggerInCaptions = (ids, previousTrigger, trigger) => {
                const captionOwners = new Map(
                    Array.from(ids || []).map((rawId) => [Number(rawId), String(previousTrigger || '')]),
                );
                return this._replaceManagedTriggerInCaptionsByOwner(captionOwners, trigger);
            };

            this._replaceManagedTriggerInCaptionsByOwner = (captionOwners, trigger) => {
                if (!(captionOwners instanceof Map)) {
                    throw new TypeError('Managed trigger caption owners must be a Map.');
                }
                const cleanTrigger = String(trigger || '').trim();
                if (!cleanTrigger) return 0;
                const updates = [];
                const ids = [];
                for (const [rawId, previousTrigger] of captionOwners.entries()) {
                    const id = Number(rawId);
                    if (!Number.isSafeInteger(id) || typeof previousTrigger !== 'string') {
                        throw new TypeError('Managed trigger caption owners must map integer IDs to strings.');
                    }
                    ids.push(id);
                    updates.push(...this._captionTriggerUpdates(
                        [id],
                        (caption) => replaceCaptionToken(caption, previousTrigger, cleanTrigger),
                        (imageId, channel, current, next) => (
                            next !== String(current || '').trim()
                            || (channel === 'nl'
                                ? !this.nlEdits.has(imageId)
                                : !this.captionEdits.has(imageId))
                        ),
                    ));
                }
                const localCaptionOverrides = new Map(
                    updates
                        .filter((update) => update.channel === 'booru')
                        .map((update) => [update.id, update.value]),
                );
                this._saveManagedTriggerForLocalIds?.(
                    ids,
                    cleanTrigger,
                    localCaptionOverrides,
                );
                const changed = this._applyCaptionTriggerUpdates(updates);
                return changed;
            };

            this._removeHistoricalTriggerFromCaptions = (ids, trigger) => {
                const cleanTrigger = String(trigger || '').trim();
                if (!cleanTrigger) return 0;
                const updates = this._captionTriggerUpdates(
                    ids,
                    (caption) => removeCaptionToken(caption, cleanTrigger),
                    (_id, _channel, current, next) => next !== String(current || '').trim(),
                );
                const localCaptionOverrides = new Map(
                    updates
                        .filter((update) => (
                            update.channel === 'booru'
                            && this.isLocalId(update.id)
                        ))
                        .map((update) => [update.id, update.value]),
                );
                if (localCaptionOverrides.size > 0) {
                    this._saveManagedTriggerForLocalIds?.(
                        Array.from(localCaptionOverrides.keys()),
                        String(this._quickfilledTrigger || '').trim(),
                        localCaptionOverrides,
                    );
                }
                return this._applyCaptionTriggerUpdates(updates);
            };
            this._addTriggerToCaptions = (ids, trigger) => this._replaceManagedTriggerInCaptions(
                ids,
                this._quickfilledTrigger,
                trigger,
            );
            document.querySelectorAll('input[name="dataset-output-mode"]').forEach(radio => {
                radio.addEventListener('change', () => {
                    this._markReadinessStale?.();
                    this._syncOutputModeUi?.();
                    this._updateExportEnabled();
                });
            });

            const triggerField = document.getElementById('dataset-trigger');
            const triggerCompositionPending = (id, event) => (
                id === 'dataset-trigger'
                && (event.isComposing || this._datasetTriggerComposing)
            );
            triggerField?.addEventListener('compositionstart', () => {
                this._datasetTriggerComposing = true;
                const pendingTimer = this._datasetFieldTimers?.get('dataset-trigger');
                if (pendingTimer) clearTimeout(pendingTimer);
                this._datasetFieldTimers?.delete('dataset-trigger');
                this._supersedeCaptionFetch?.();
            });
            triggerField?.addEventListener('compositionend', () => {
                this._datasetTriggerComposing = false;
                triggerField.dispatchEvent(new Event('input', { bubbles: true }));
            });

            // Trigger + custom pattern -> live preview
            for (const id of ['dataset-trigger', 'dataset-naming-pattern']) {
                document.getElementById(id)?.addEventListener('input', (event) => {
                    if (triggerCompositionPending(id, event)) return;
                    this._updateNamingPreview();
                });
            }
            // Pass-3 review fix: keep the trigger-quickfill button in sync
            // with the trigger field so it's visibly disabled when empty.
            // Without this the user would click and only learn via toast
            // that the field was empty.
            triggerField?.addEventListener('input', (event) => {
                if (triggerCompositionPending('dataset-trigger', event)) return;
                this._syncTriggerQuickfillButton();
            });
            this._syncTriggerQuickfillButton();

            const dispatchFieldEdit = (el, eventName = 'input') => {
                if (!el) return;
                el.dispatchEvent(new Event(eventName, { bubbles: true }));
            };
            document.getElementById('btn-dataset-clear-prefix')?.addEventListener('click', () => {
                const input = document.getElementById('dataset-export-prefix');
                if (!input) return;
                input.value = '';
                dispatchFieldEdit(input, 'input');
                this._refreshExportPreview?.();
            });
            document.getElementById('btn-dataset-reset-template')?.addEventListener('click', () => {
                const field = document.getElementById('dataset-template-override');
                if (!field) return;
                field.value = '{trigger}, {tags:filtered}, {append}';
                dispatchFieldEdit(field, 'input');
                this._refreshExportPreview?.();
            });
            document.getElementById('btn-dataset-refresh-zh-aid')?.addEventListener('click', () => {
                const toggle = document.getElementById('dataset-translation-show-zh');
                this._tagZhCache?.clear?.();
                if (toggle && !toggle.checked) {
                    toggle.checked = true;
                    dispatchFieldEdit(toggle, 'change');
                } else {
                    this._renderTagPills?.();
                }
            });

            // Bulk caption ops -> recompute captions on the fly (debounced)
            for (const id of [
                'dataset-trigger',
                'dataset-common-tags',
                'dataset-blacklist',
                'dataset-underscore-to-space',
                'dataset-export-content-mode',
                'dataset-export-prefix',
                'dataset-template-override',
                'dataset-replace-rules',
                'dataset-max-tags',
            ]) {
                const el = document.getElementById(id);
                if (!el) continue;
                if (!this._datasetFieldTimers) this._datasetFieldTimers = new Map();
                const evt = (el.tagName.toLowerCase() === 'input' && el.type === 'checkbox') || el.tagName.toLowerCase() === 'select'
                    ? 'change'
                    : 'input';
                el.addEventListener(evt, (event) => {
                    if (triggerCompositionPending(id, event)) return;
                    this._markReadinessStale?.();
                    this._supersedeCaptionFetch?.();
                    const pendingTimer = this._datasetFieldTimers.get(id);
                    if (pendingTimer) clearTimeout(pendingTimer);
                    const nextTimer = setTimeout(() => {
                        this._datasetFieldTimers.delete(id);
                        if (id === 'dataset-trigger' && datasetTriggerIssue(el.value)) return;
                        const refreshesCaptions = [
                            'dataset-trigger',
                            'dataset-common-tags',
                            'dataset-blacklist',
                            'dataset-underscore-to-space',
                        ].includes(id);
                        if (refreshesCaptions) {
                            this._refreshAllCaptions();
                            if (this.imageIds.length === 0) this._refreshExportPreview?.();
                        } else {
                            this._refreshExportPreview?.();
                        }
                        const templateWrap = document.getElementById('dataset-template-options');
                        if (templateWrap) templateWrap.hidden = this._exportContentMode?.() !== 'template';
                        // v3.4.4 fix #4: flash a small "✓ preview updated" cue
                        // so the user sees the debounced re-render fire instead
                        // of wondering whether the edit took effect.
                        this._flashPreviewUpdated?.();
                    }, 400);
                    this._datasetFieldTimers.set(id, nextTimer);
                });
            }

            // P1-17: trait-pruning checklist feeding the dataset blacklist.
            // Local-import items have no gallery tag rows, so only real
            // gallery ids go to the endpoint. Newline separator matches the
            // #dataset-blacklist convention (line breaks, not commas).
            window.TraitPruner?.attach({
                button: document.getElementById('btn-dataset-trait-pruner'),
                textarea: document.getElementById('dataset-blacklist'),
                separator: '\n',
                getImageIds: () => (this.imageIds || [])
                    .filter((id) => !(this.isLocalId && this.isLocalId(id)))
                    .map(Number)
                    .filter(Number.isFinite),
            });

            // Output folder validation + export-button enable
            document.getElementById('dataset-output-folder')?.addEventListener('input', () => {
                this._markReadinessStale?.();
                this._validateOutputFolder();
                this._renderReadiness?.();
                this._updateExportEnabled();
                this._syncOutputModeUi?.();
            });
            document.getElementById('btn-dataset-browse-output')?.addEventListener('click', (event) => {
                event.preventDefault();
                const input = document.getElementById('dataset-output-folder');
                if (input && typeof window.showFolderBrowser === 'function') {
                    window.showFolderBrowser(input);
                }
            });

            const readinessFields = [
                ['dataset-trigger', 'input'],
                ['dataset-naming-pattern', 'input'],
                ['dataset-overwrite', 'change'],
            ];
            for (const [id, eventName] of readinessFields) {
                document.getElementById(id)?.addEventListener(eventName, (event) => {
                    if (triggerCompositionPending(id, event)) return;
                    this._markReadinessStale?.();
                });
            }

            // Export flow
            document.getElementById('btn-dataset-readiness-check')?.addEventListener('click', () => {
                if (this._readinessView?.state === 'error' && this._readinessView?.activeJobId) {
                    this._resumeReadinessCheck?.();
                    return;
                }
                this._startReadinessCheck?.();
            });
            document.getElementById('btn-dataset-readiness-cancel')?.addEventListener('click', () => this._cancelReadinessCheck?.());
            document.getElementById('dataset-readiness-issues')?.addEventListener('click', (event) => {
                const target = event.target.closest?.('[data-readiness-image-id]');
                if (!target) return;
                this._openReadinessIssue?.(Number(target.dataset.readinessImageId));
            });
            document.getElementById('btn-dataset-export')?.addEventListener('click', () => this._showConfirmModal());
            document.getElementById('btn-dataset-confirm-cancel')?.addEventListener('click', () => this._hideConfirmModal());
            document.getElementById('btn-dataset-confirm-go')?.addEventListener('click', () => this._runExport());
            document.getElementById('btn-dataset-export-cancel')?.addEventListener('click', () => this._cancelExportJob?.());

            // Result modal
            document.getElementById('btn-dataset-result-close')?.addEventListener('click', () => this._hideResultModal());
            document.getElementById('btn-dataset-open-folder')?.addEventListener('click', () => this._openOutputFolder());
        },
    });
})();
