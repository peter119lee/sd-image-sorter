/**
 * smart-tag/run.js — smart-tag.js decomposition.
 * Extracted VERBATIM from frontend/js/smart-tag.js pre-split lines
 * 897-1098: applyPathCaptions (hosts the `/api/smart-tag/results`
 * literal pinned by backend/tests/test_frontend_contract.py),
 * onJobFinished (toasts + Dataset Maker caption reseed/refresh),
 * runSmartTag (empty-source guard, pick-one-mode guard,
 * destructive-replace confirm, pipeline-queued branch) and
 * cancelSmartTag (404-already-finished swallow). Classic script;
 * family renames applied.
 */
'use strict';
    function replaceSmartTagChannel(_existing, incoming, _separator) {
        return incoming;
    }

    function appendSmartTagChannel(existing, incoming, separator) {
        if (!existing || existing === incoming) return incoming;
        return `${existing}${separator}${incoming}`;
    }

    function currentSmartTagChannel(pendingEdits, edits, originals, id) {
        const value = pendingEdits.has(id)
            ? pendingEdits.get(id)
            : edits.has(id)
                ? edits.get(id)
                : originals?.get?.(id);
        return String(value || '').trim();
    }

    function nextSmartTagChannel(pendingEdits, edits, originals, id, incoming, separator, mergeChannel) {
        const normalized = String(incoming || '').trim();
        if (!normalized) return null;
        const existing = currentSmartTagChannel(pendingEdits, edits, originals, id);
        const next = mergeChannel(existing, normalized, separator);
        return next === existing ? null : next;
    }

    async function applyPathCaptions(jobId, mergeStrategy) {
        const dm = window.DatasetMaker;
        if (!jobId || !dm?.localItemPaths || !dm?.captionEdits || !dm?.nlEdits) return 0;
        const mergeChannel = mergeStrategy === 'replace'
            ? replaceSmartTagChannel
            : mergeStrategy === 'append'
                ? appendSmartTagChannel
                : null;
        if (!mergeChannel) {
            throw new TypeError(
                `Unsupported Smart Tag merge strategy "${mergeStrategy}". Expected "replace" or "append".`
            );
        }
        const idByPath = new Map();
        for (const [id, path] of dm.localItemPaths.entries()) {
            idByPath.set(String(path), Number(id));
        }
        let offset = 0;
        const appliedIds = new Set();
        const pendingBooruEdits = new Map();
        const pendingNlEdits = new Map();
        while (true) {
            const page = await getJson(`/api/smart-tag/results?job_id=${encodeURIComponent(jobId)}&offset=${offset}&limit=1000`);
            if (!Array.isArray(page.results)) {
                throw new TypeError(`Smart Tag results for job "${jobId}" must contain a results array.`);
            }
            for (let index = 0; index < page.results.length; index += 1) {
                const item = page.results[index];
                if (
                    !item
                    || typeof item.path !== 'string'
                    || typeof item.booru_text !== 'string'
                    || typeof item.nl_text !== 'string'
                ) {
                    throw new TypeError(
                        `Smart Tag result ${offset + index} for job "${jobId}" must contain path, booru_text, and nl_text strings.`
                    );
                }
                const id = idByPath.get(item.path);
                if (id === undefined) continue;
                // `caption` is the legacy combined field. Writing it into the
                // Booru editor would duplicate NL prose when both modes run.
                const nextBooru = nextSmartTagChannel(
                    pendingBooruEdits,
                    dm.captionEdits,
                    dm.captions,
                    id,
                    item.booru_text,
                    ', ',
                    mergeChannel
                );
                const nextNl = nextSmartTagChannel(
                    pendingNlEdits,
                    dm.nlEdits,
                    dm.nlCaptions,
                    id,
                    item.nl_text,
                    ' ',
                    mergeChannel
                );
                if (nextBooru !== null) pendingBooruEdits.set(id, nextBooru);
                if (nextNl !== null) pendingNlEdits.set(id, nextNl);
                if (nextBooru !== null || nextNl !== null) appliedIds.add(id);
            }
            offset += page.results.length;
            if (!page.has_more || !page.results.length) break;
        }
        if (appliedIds.size > 0) {
            for (const [id, value] of pendingBooruEdits.entries()) {
                dm.captionEdits.set(id, value);
            }
            for (const [id, value] of pendingNlEdits.entries()) {
                dm.nlEdits.set(id, value);
            }
            dm._scheduleSaveSession?.();
            dm._renderQueue?.();
            if (dm.activeId != null) dm._setActive?.(dm.activeId);
            dm._refreshVocab?.();
            dm._refreshExportPreview?.();
        }
        return appliedIds.size;
    }

    async function onJobFinished(snap) {
        const status = snap.status || 'completed';
        const ok = (snap.succeeded || 0);
        const fail = (snap.failed || 0);
        const total = snap.total || 0;
        showProgress(false);
        let captionApplyFailed = false;
        if (['completed', 'warning', 'cancelled'].includes(status) && (snap.caption_result_count || 0) > 0) {
            try {
                await applyPathCaptions(snap.job_id, snap.settings?.merge_strategy || 'replace');
            } catch (err) {
                captionApplyFailed = true;
                const errorMessage = `Smart Tag captions were generated but could not be applied: ${err.message || err}`;
                if (typeof window.showToast === 'function') {
                    window.showToast(errorMessage, 'error');
                } else {
                    (window.Logger?.error || console.error)('[smart-tag]', errorMessage);
                }
            }
        }

        // Reuse the existing toast helper if available; fall back to alert.
        const noiseStripped = snap.noise_stripped_count || 0;
        const noiseSuffix = noiseStripped > 0 ? ` · ${noiseStripped} noise tags removed` : '';
        const firstError = Array.isArray(snap.errors)
            ? snap.errors.find((entry) => entry && entry.error)
            : null;
        const errorSource = String(firstError?.image_id || '').trim();
        const errorLabel = errorSource
            ? (/^-?\d+$/.test(errorSource) ? `Image #${errorSource}` : `Image ${errorSource}`)
            : 'Image';
        const errorSuffix = firstError
            ? ` · ${errorLabel}: ${String(firstError.error)}`
            : '';
        const errorText = firstError ? String(firstError.error) : '';
        const failedErrorSuffix = firstError
            ? String(snap.message || '').includes(errorText)
                ? ` · ${errorLabel}`
                : errorSuffix
            : '';
        const isWarning = status === 'warning' || (status === 'completed' && fail > 0);
        const message = status === 'cancelled'
            ? `Smart Tag cancelled at ${ok + fail}/${total}${noiseSuffix}`
            : status === 'failed'
                ? `Smart Tag failed: ${snap.message || 'unknown error'}${failedErrorSuffix}`
                : isWarning
                    ? `Smart Tag finished with warnings: ${ok} ok, ${fail} failed${noiseSuffix}${errorSuffix}.`
                    : `Smart Tag finished: ${ok} ok, ${fail} failed${noiseSuffix}.`;
        const toastType = status === 'failed'
            ? 'error'
            : status === 'cancelled'
                ? 'info'
                : isWarning
                    ? 'warning'
                    : 'success';

        if (!captionApplyFailed && typeof window.showToast === 'function') {
            window.showToast(message, toastType);
        } else if (!captionApplyFailed) {
            (window.Logger?.info || console.log)('[smart-tag]', message);
        }

        // Surface the new captions in Dataset Maker so they show up in the
        // editor + queue without requiring a re-import (Bug: gallery-source
        // images previously needed a manual re-import from the gallery before
        // Smart Tag results appeared here).
        const dm = window.DatasetMaker;
        if (dm) {
            try {
                // When the run produced natural-language captions, seed them
                // from the DB ai_caption into the editor — the booru-tags
                // template the editor renders omits {nl_caption}, so VLM/API
                // captions were only visible in the gallery before.
                const usedVlm = snap.settings?.enable_vlm === true
                    && (snap.settings?.natural_language_mode || 'vlm') !== 'off';
                if (usedVlm && typeof dm._seedAiCaptions === 'function' && Array.isArray(dm.imageIds)) {
                    const galleryIds = dm.imageIds.filter((id) => !(dm.isLocalId?.(id)));
                    if (galleryIds.length) await dm._seedAiCaptions(galleryIds);
                }
            } catch (_e) { /* non-fatal: gallery still shows ai_caption */ }
            // Re-render queue tiles + export preview + active editor from the
            // refreshed caption state (this is what a re-import used to force).
            if (typeof dm._refreshAllCaptions === 'function') {
                try { await dm._refreshAllCaptions(); } catch (_e) { /* ignore */ }
            }
        }
        activeJobId = null;
    }

    async function runSmartTag() {
        const form = readForm();
        if (
            !form.image_ids.length
            && !form.image_paths.length
            && !form.selection_token
            && !form.dataset_scan_token
        ) {
            const noImagesMsg = smartTagT('smartTag.noImages', 'No images in Dataset Maker. Add images first.');
            if (typeof window.showToast === 'function') {
                window.showToast(noImagesMsg, 'warning');
            } else {
                alert(noImagesMsg);
            }
            return;
        }
        if ((form.image_ids.length + form.image_paths.length) > LARGE_EXPLICIT_SOURCE_LIMIT
            && !form.selection_token
            && !form.dataset_scan_token) {
            console.warn(
                '[smart-tag] large explicit source list; selection_token or dataset_scan_token would be more efficient.',
                form.image_ids.length + form.image_paths.length
            );
        }
        if (!form.enable_wd14 && !form.enable_vlm) {
            if (typeof window.showToast === 'function') {
                window.showToast(smartTagT('smartTag.pickOneMode', 'Pick booru tags, natural-language captioning, or both.'), 'warning');
            }
            return;
        }

        // Destructive-replace guard: replace mode overwrites existing
        // captions and DB images don't get a backup, so confirm before
        // we commit a large overwrite the user can't undo.
        if (form.merge_strategy === 'replace') {
            const explicitTotal = (form.image_ids?.length || 0) + (form.image_paths?.length || 0);
            const tokenTotal = Number(form.selection_token ? (getDatasetSources().selectionTotal || 0) : 0)
                + Number(form.dataset_scan_token ? (getDatasetSources().datasetScanTotal || 0) : 0);
            const total = explicitTotal + tokenTotal;
            if (total > 100) {
                const proceed = window.confirm(
                    `This will overwrite existing captions on ${total} images. Continue?`
                );
                if (!proceed) return;
            }
        }

        if (typeof window.ensureFeatureModel === 'function') {
            const specs = [];
            if (form.enable_wd14) {
                const names = form.taggers
                    ? form.taggers.map((item) => item.model)
                    : (form.tagger_model ? [form.tagger_model] : ['wd-swinv2-tagger-v3']);
                for (const name of names) {
                    if (typeof window.prepareSpecForTagger === 'function') {
                        specs.push(window.prepareSpecForTagger(name));
                    }
                }
            }
            if (form.enable_vlm && form.natural_language_mode === 'florence2') {
                specs.push({
                    modelId: 'florence2',
                    label: 'Florence-2 Base',
                    sizeHint: '~465 MB',
                    confirmBytes: 0,
                });
            }
            if (form.enable_vlm && form.natural_language_mode === 'toriigate') {
                specs.push({
                    modelId: 'toriigate',
                    label: 'ToriiGate 0.5',
                    sizeHint: '~9.6 GB',
                    confirmBytes: 9.6 * 1024 * 1024 * 1024,
                });
            }
            const seen = new Set();
            for (const spec of specs) {
                const key = `${spec.modelId}:${spec.variant || ''}`;
                if (seen.has(key)) continue;
                seen.add(key);
                const ensured = await window.ensureFeatureModel(spec.modelId, spec);
                if (!ensured.ok) return;
            }
        }

        showProgress(true);
        setProgressUI({ percent: 0, text: 'Starting...', preview: '' });
        try {
            const snap = await postJson('/api/smart-tag/start', form);
            if (snap && snap.pipeline_queued === true) {
                // v3.4.1 AI job queue: another AI job is running, so this
                // run was queued (200) instead of rejected with 409. The
                // poll loop renders the queued state until the dispatcher
                // starts the job.
                pipelineQueuedSince = Date.now();
                activeJobId = null;
                if (typeof window.showToast === 'function') {
                    window.showToast(snap.duplicate
                        ? smartTagT('aiQueue.duplicateToast', 'An identical job is already queued')
                        : smartTagT('aiQueue.queuedToast', 'Queued — starts automatically after the current AI job finishes'), 'info');
                }
                setProgressUI({
                    percent: 0,
                    text: smartTagT('aiQueue.queuedProgress', 'Queued #{position} — waiting for the current AI job to finish')
                        .replace('{position}', String(snap.queue_position || 1)),
                    preview: '',
                });
                startProgressPolling();
                return;
            }
            pipelineQueuedSince = 0;
            activeJobId = snap.job_id || null;
            renderSnapshot(snap);
            startProgressPolling();
        } catch (err) {
            showProgress(false);
            const msg = err.message || String(err);
            if (typeof window.showToast === 'function') {
                window.showToast(`Smart Tag failed to start: ${msg}`, 'error');
            } else {
                alert(`Smart Tag failed to start: ${msg}`);
            }
        }
    }

    async function cancelSmartTag() {
        const cancelBtn = smartTag$('#btn-smart-tag-cancel-job');
        try {
            await postJson('/api/smart-tag/cancel', null);
            // Immediately reflect intent in the UI so the user gets
            // feedback instead of watching the progress bar keep moving
            // for ~1s until the worker checks the cancel flag.
            if (cancelBtn) cancelBtn.disabled = true;
            setProgressUI({ text: smartTagT('smartTag.cancellingKept', 'Cancelling — already-tagged results will be kept') });
            if (typeof window.showToast === 'function') {
                window.showToast(smartTagT('smartTag.cancelRequested', 'Smart Tag cancellation requested'), 'info');
            }
        } catch (err) {
            // 404 means the job already finished between the user
            // clicking Stop and the request landing — surface that
            // explicitly instead of swallowing it silently.
            if (err && err.status === 404) {
                if (typeof window.showToast === 'function') {
                    window.showToast(smartTagT('smartTag.jobAlreadyFinished', 'Job already finished'), 'info');
                }
            }
        }
    }

